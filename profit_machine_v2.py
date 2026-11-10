#!/usr/bin/env python3
# PITOS v5.38 — Full night expansion including NCAA and all available sports.

import os
import sys
import socket
import threading

PORT = int(os.getenv("PORT", "8080"))

# ================= NATYCHMIASTOWY BIND PORTU DLA RAILWAY =================
def instant_bind():
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", PORT))
        server.listen(5)
        print(f"-> Railway health port {PORT} bound instantly.", flush=True)
        while True:
            conn, _ = server.accept()
            try:
                conn.recv(512)
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 18\r\n\r\nPITOS v5.38 Active"
                conn.sendall(response)
            except Exception:
                pass
            finally:
                conn.close()
    except Exception as e:
        print(f"-> Bind error: {e}", flush=True)

threading.Thread(target=instant_bind, daemon=True).start()

# ================= RESZTA PAKIETÓW I LOGIKA =================
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

API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v5.db")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "3600"))
MAX_SPORTS = int(os.getenv("MAX_SPORTS_PER_CYCLE", "75"))  # ZWIĘKSZONA PULA NA WSZYSTKIE LIGI I NCAA
BOOKMAKER = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.15"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "2.70"))
MIN_TOTAL_ODDS = float(os.getenv("MIN_TOTAL_ODDS", "1.90"))
MAX_TOTAL_ODDS = float(os.getenv("MAX_TOTAL_ODDS", "4.00"))

MAX_COUPONS = int(os.getenv("MAX_AKOS_PER_CYCLE", "3"))
MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.10"))  # SZERSZE OTWARCIE NA NOCNE VALUE
LOOKAHEAD = int(os.getenv("LOOKAHEAD_HOURS", "18"))

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("pitos-v5.38")
session = requests.Session()

MEM_EVENTS = set()
MEM_SIGNATURES = set()

def get_db():
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    if os.path.exists(SQLITE_PATH):
        try:
            os.remove(SQLITE_PATH)
            print(f"-> Purged old database file {SQLITE_PATH}.", flush=True)
        except Exception:
            pass

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
    print("-> PITOS v5.38 DB initialized (Full NCAA & night expansion).", flush=True)

def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("Missing ODDS_API_KEY")
    p = dict(params or {})
    p["apiKey"] = API_KEY
    time.sleep(0.3)
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code == 429:
        time.sleep(10)
        res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code != 200:
        res.raise_for_status()
    return res.json()

def telegram_send(msg):
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
        raise RuntimeError("Telegram failed")

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
        if "outrights" in key or "futures" in key:
            continue
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
        if not ev_id:
            continue
        commence = parse_time(ev.get("commence_time"))
        if not commence or commence < now or commence > now + timedelta(hours=LOOKAHEAD):
            continue

        home = ev.get("home_team", "Unknown")
        away = ev.get("away_team", "Unknown")

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

        ev_name = f"{home} vs {away}"
        for name, pr, raw_p in probs:
            norm_p = raw_p / p_sum
            if MIN_SINGLE_ODDS <= pr <= MAX_SINGLE_ODDS and norm_p >= MIN_PROB:
                candidates.append({
                    "event_id": ev_id,
                    "event": ev_name,
                    "selection": name,
                    "odds": pr,
                    "prob": norm_p,
                    "sport": sport_key,
                    "commence": commence
                })
    return candidates

def run_cycle():
    print("-> PITOS v5.38 full expansion scan started...", flush=True)
    sports = get_sports()
    print(f"-> Active sports pooled (including NCAA): {len(sports)}", flush=True)

    all_candidates = []
    for sp in sports:
        all_candidates.extend(extract_candidates(sp))

    unique_map = {c["event_id"]: c for c in all_candidates}
    candidates = list(unique_map.values())
    print(f"-> Total unique candidates found: {len(candidates)}", flush=True)

    if not candidates:
        print("-> No candidates found.", flush=True)
        return

    candidates.sort(key=lambda x: (x["prob"], -x["odds"]), reverse=True)
    random.shuffle(candidates)

    valid_coupons = []

    for r in [2, 3]:
        for combo in combinations(candidates, r):
            e_ids = [c["event_id"] for c in combo]
            if len(set(e_ids)) != r:
                continue
            total_odds = math.prod(c["odds"] for c in combo)
            if MIN_TOTAL_ODDS <= total_odds <= MAX_TOTAL_ODDS:
                sig_source = "|".join(sorted(f"{item['event_id']}:{item['selection']}" for item in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
                if sig in MEM_SIGNATURES:
                    continue
                valid_coupons.append({
                    "combo": combo,
                    "total_odds": total_odds,
                    "signature": sig,
                    "event_ids": e_ids,
                    "type": f"AKO{r} NCAA/NIGHT"
                })

    random.shuffle(valid_coupons)
    print(f"-> Valid AKO coupons generated: {len(valid_coupons)}", flush=True)

    sent = 0
    used_in_cycle = set()
    for coupon in valid_coupons:
        if sent >= MAX_COUPONS:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue

        tot = coupon["total_odds"]
        c_type = coupon["type"]
        stake_u = 3
        stake_pln = 75

        lines = [f"<b>🥷 PITOS v5.38 | {c_type} #{sent+1}</b>", ""]
        for idx, item in enumerate(coupon["combo"], 1):
            ev_esc = html.escape(item["event"])
            sel_esc = html.escape(item["selection"])
            sp_esc = html.escape(item["sport"])
            kick = item["commence"].strftime("%d.%m %H:%M UTC")
            lines.append(f"{idx}. <b>{ev_esc}</b>\n   Typ: <i>{sel_esc}</i> | Kurs: <b>{item['odds']:.2f}</b>\n   {sp_esc} | {kick
