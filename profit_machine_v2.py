#!/usr/bin/env python3
# PITOS v4 — Multi-sport selector with randomized candidate shuffling & anti-repetition guards.
# Required Railway Variables / Hardcoded Credentials Active:
#   ODDS_API_KEY
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID

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

# ------------------------- CONFIGURATION -------------------------

API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v4.db")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "7200"))
MAX_SPORTS_PER_CYCLE = int(os.getenv("MAX_SPORTS_PER_CYCLE", "25"))

BOOKMAKER_NAME = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.05"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "1.75"))
MIN_AKO_TOTAL = float(os.getenv("MIN_AKO_TOTAL", "2.00"))
MAX_AKO_TOTAL = float(os.getenv("MAX_AKO_TOTAL", "3.20"))
AKO_LEGS = int(os.getenv("AKO_LEGS", "3"))
MAX_AKOS_PER_CYCLE = int(os.getenv("MAX_AKOS_PER_CYCLE", "1"))

LOOKAHEAD_HOURS = int(os.getenv("LOOKAHEAD_HOURS", "24"))
PAST_GRACE_MINUTES = int(os.getenv("PAST_GRACE_MINUTES", "0"))

MIN_MARKET_PROBABILITY = float(os.getenv("MIN_MARKET_PROBABILITY", "0.62"))

SPORT_ALLOWLIST = {
    item.strip() for item in os.getenv("SPORT_ALLOWLIST", "soccer,basketball,icehockey,tennis,eports,americanfootball,baseball").split(",")
    if item.strip()
}

REQUEST_TIMEOUT = 20
API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("pitos-v4")

session = requests.Session()

MEMORY_SENT_EVENTS = set()
MEMORY_SENT_SIGNATURES = set()


# ------------------------- DATABASE -------------------------

def db_connect():
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


class _DBSession:
    def __enter__(self):
        self.conn = db_connect()
        return self.conn

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


def db_session():
    return _DBSession()


def init_db():
    with db_session() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_events (
                event_id TEXT PRIMARY KEY,
                sport_key TEXT NOT NULL,
                event_name TEXT NOT NULL,
                commence_time TEXT,
                coupon_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                coupon_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                sports_count INTEGER NOT NULL,
                events_count INTEGER NOT NULL,
                candidates_count INTEGER NOT NULL,
                coupons_sent INTEGER NOT NULL,
                note TEXT
            )
        """)

        cursor = conn.cursor()
        cursor.execute("SELECT event_id FROM sent_events")
        for row in cursor.fetchall():
            MEMORY_SENT_EVENTS.add(row[0])

        cursor.execute("SELECT signature FROM coupons")
        for row in cursor.fetchall():
            MEMORY_SENT_SIGNATURES.add(row[0])

        log.info("SQLite DB initialized. Events in RAM: %d | Signatures in RAM: %d", 
                 len(MEMORY_SENT_EVENTS), len(MEMORY_SENT_SIGNATURES))


def event_already_used(event_id):
    if event_id in MEMORY_SENT_EVENTS:
        return True
    with db_session() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_events WHERE event_id = ? LIMIT 1",
            (event_id,)
        ).fetchone()
    return row is not None


def coupon_signature_exists(signature):
    if signature in MEMORY_SENT_SIGNATURES:
        return True
    with db_session() as conn:
        row = conn.execute(
            "SELECT 1 FROM coupons WHERE signature = ? LIMIT 1",
            (signature,)
        ).fetchone()
    return row is not None


# ------------------------- API / TELEGRAM -------------------------

def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("Missing ODDS_API_KEY")

    query = dict(params or {})
    query["apiKey"] = API_KEY

    response = session.get(
        f"{API_BASE}{path}",
        params=query,
        timeout=REQUEST_TIMEOUT
    )

    remaining = response.headers.get("x-requests-remaining")
    used = response.headers.get("x-requests-used")

    if remaining is not None:
        log.info("The Odds API quota: remaining=%s used=%s", remaining, used)

    if response.status_code != 200:
        log.error("Odds API error %s: %s", response.status_code, response.text[:1000])
        response.raise_for_status()

    return response.json()


def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"{TELEGRAM_BASE}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code != 200 or not data.get("ok"):
        raise RuntimeError(f"Telegram send failed: HTTP {response.status_code}, {str(data)[:500]}")

    return data["result"].get("message_id")


def verify_environment():
    if not API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing critical environment variables!")
        return False
    return True


# ------------------------- DATA NORMALIZATION -------------------------

def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def get_active_sports():
    sports = api_get("/sports/", {})
    active = []
    for sport in sports:
        if not sport.get("active", True):
            continue
        key = sport.get("key", "")
        if any(bad in key for bad in ["winner", "futures", "outrights", "championship"]):
            continue
        matched = any(allowed in key for allowed in SPORT_ALLOWLIST)
        if SPORT_ALLOWLIST and matched:
            active.append(sport)

    if not active:
        active = [
            s for s in sports 
            if s.get("active", True) and not any(b in s.get("key", "") for b in ["winner", "futures", "outrights", "championship"])
        ]

    return active[:MAX_SPORTS_PER_CYCLE]


def fetch_sport_events(sport_key):
    return api_get(
        f"/sports/{sport_key}/odds/",
        {"regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
    )


def find_betclic_bookmaker(event):
    for bookmaker in event.get("bookmakers", []):
        title = (bookmaker.get("title") or "").strip().lower()
        key = (bookmaker.get("key") or "").strip().lower()
        if BOOKMAKER_NAME in title or BOOKMAKER_NAME in key:
            return bookmaker
    return None


def extract_candidate(event, sport_key):
