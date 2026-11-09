#!/usr/bin/env python3
# PITOS v5.1 — Clean, responsive multi-sport betting selector.

import os
import sys
import time
import math
import sqlite3
import logging
import hashlib
import html
import random
from datetime import datetime, timedelta, timezone
from itertools import combinations
import requests

os.environ["PYTHONUNBUFFERED"] = "1"

# ================= CONFIGURATION =================
API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v5.db")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "7200"))
MAX_SPORTS = int(os.getenv("MAX_SPORTS_PER_CYCLE", "30"))
BOOKMAKER = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.05"))
MAX_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "1.80"))
MIN_AKO = float(os.getenv("MIN_AKO_TOTAL", "2.00"))
MAX_AKO = float(os.getenv("MAX_AKO_TOTAL", "3.40"))
AKO_LEGS = int(os.getenv("AKO_LEGS", "3"))
MAX_COUPONS = int(os.getenv("MAX_AKOS_PER_CYCLE", "3"))

MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.55"))
LOOKAHEAD = int(os.getenv("LOOKAHEAD_HOURS", "24"))

SPORT_ALLOWLIST = {
    s.strip() for s in os.getenv("SPORT_ALLOWLIST", "soccer,icehockey,tennis,eports,americanfootball,baseball,gridiron,cricket,rugby").split(",")
    if s.strip()
}

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("pitos-v5.1")
session = requests.Session()

MEM_EVENTS = set()
MEM_SIGNATURES = set()


# ================= DATABASE =================
def get_db():
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_events (
                event_id TEXT PRIMARY KEY,
                sport_key TEXT NOT NULL,
                event_name TEXT NOT NULL,
                coupon_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                coupon_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        cur = conn.cursor()
        cur.execute("SELECT event_id FROM sent_events")
        for row in cur.fetchall():
            MEM_EVENTS.add(row[0])
            
        cur.execute("SELECT signature FROM coupons")
        for row in cur.fetchall():
            MEM_SIGNATURES.add(row[0])
            
    print(f"-> PITOS v5.1 DB initialized. Loaded {len(MEM_EVENTS)} events and {len(MEM_SIGNATURES)} signatures.", flush=True)


# ================= API & TELEGRAM =================
def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("Missing ODDS_API_KEY")
    p = dict(params or {})
    p["apiKey"] = API_KEY
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    
    if res.status_code != 200:
        log.error("API Error %s: %s", res.status_code, res.text[:300])
        res.raise_for_status()
    return res.json()

def telegram_send(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing Telegram credentials")
    url = f"{TELEGRAM_BASE}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = session.post(url, json=payload, timeout=20)
    data = res.json() if res.status_code == 200 else {}
    if res.status_code != 200 or not data.get("ok"):
        raise RuntimeError(f"Telegram failed: {res.status_code}")
    return data["result"]["message_id"]


# ================= CORE LOGIC =================
def parse_time(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def get_sports():
    sports = api_get("/sports/")
    active = []
    for s in sports:
        if not s.get("active", True):
            continue
        key = s.get("key", "").lower()
        if any(b in key for b in ["winner", "futures", "outrights", "championship", "basketball", "nba"]):
            continue
        if not SPORT_ALLOWLIST or any(a in key for a in SPORT_ALLOWLIST) or "ncaa" in key or "college" in key:
            active.append(key)
    return active[:MAX_SPORTS]

def extract_candidates(sport_key):
    try:
        events = api_get(f"/sports/{sport_key}/odds/", {"regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
    except Exception:
        return []

    candidates = []
    now = datetime.now(timezone.utc)

    for ev in events:
        ev_id = ev.get("id")
        if not ev_id or ev_id in MEM_EVENTS:
            continue

        commence = parse_time(ev.get("commence_time"))
        if not commence or commence < now or commence > now + timedelta(hours=LOOKAHEAD):
            continue

        bk = None
        for b in ev.get("bookmakers", []):
            title = (b.get("title") or "").strip().lower()
            key = (b.get("key") or "").strip().lower()
            if BOOKMAKER in title or BOOKMAKER in key:
                bk = b
                break
        if not bk:
            continue

        h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
        if not h2h:
            continue

        outcomes = h2h.get("outcomes", [])
        prices = []
        for o in outcomes:
            try:
                pr = float(o.get("price"))
                if math.isfinite(pr) and pr > 1.0:
                    prices.append((o.get("name", ""), pr))
            except Exception:
                continue

        if len(prices) < 2:
            continue

        probs = [(name, pr, 1.0 / pr) for name, pr in prices]
        p_sum = sum(p[2] for p in probs)
        if p_sum <= 0:
            continue

        home = ev.get("home_team", "Unknown")
        away = ev.get("away_team", "Unknown")
        ev_name = f"{home} vs {away}"

        for name, pr, raw_p in probs:
            norm_p = raw_p / p_sum
            if MIN_ODDS <= pr <= MAX_ODDS and norm_p >= MIN_PROB:
                candidates.
