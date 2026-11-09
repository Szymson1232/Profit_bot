#!/usr/bin/env python3
# PITOS v4 — Multi-sport selector with strict H2H filtering, 422 avoidance & dynamic stakes.
# Required Railway Variables / Hardcoded Credentials Active:
#   ODDS_API_KEY
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# Recommended Railway Volume:
#   Mount path: /data
#   SQLITE_PATH: /data/pitos_v4.db

import os
import time
import math
import sqlite3
import logging
import hashlib
import html
from datetime import datetime, timedelta, timezone
from itertools import combinations

import requests

# ------------------------- CONFIGURATION -------------------------

API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/pitos_v4.db")
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
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("pitos-v4")

session = requests.Session()


# ------------------------- DATABASE -------------------------

def db_connect():
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


class _DBSession:
    """Explicit SQLite context manager."""

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

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sent_events_status "
            "ON sent_events(status)"
        )


def event_already_used(event_id):
    with db_session() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_events WHERE event_id = ? LIMIT 1",
            (event_id,)
        ).fetchone()

    return row is not None


def coupon_signature_exists(signature):
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
        log.info(
            "The Odds API quota: remaining=%s used=%s",
            remaining,
            used
        )

    if response.status_code != 200:
        log.error(
            "Odds API error %s: %s",
            response.status_code,
            response.text[:1000]
        )
        response.raise_for_status()

    return response.json()


def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
        )

    url = f"{TELEGRAM_BASE}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    response = session.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"Telegram send failed/uncertain: HTTP {response.status_code}, "
            f"{str(data)[:500]}"
        )

    return data["result"].get("message_id")


def verify_environment():
    missing = []

    for key, value in (
        ("ODDS_API_KEY", API_KEY),
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
    ):
        if not value:
            missing.append(key)

    if missing:
        log.error(
            "Missing Railway Variables: %s",
            ", ".join(missing)
        )
        return False

    if AKO_LEGS < 2:
        log.error("AKO_LEGS must be at least 2")
        return False

    if MIN_SINGLE_ODDS <= 1 or MAX_SINGLE_ODDS < MIN_SINGLE_ODDS:
        log.error("Invalid single odds limits")
        return False

    if MIN_AKO
