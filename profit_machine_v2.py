#!/usr/bin/env python3
# PITOS v4 — Betclic-only selector with strict historical event deduplication & dynamic stakes.
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
    item.strip() for item in os.getenv("SPORT_ALLOWLIST", "").split(",")
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
    """Checks across ALL statuses (reserved or sent) to strictly prevent event reuse."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_events WHERE event_id = ? LIMIT 1",
            (event_id,)
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

    if MIN_AKO_TOTAL <= 1 or MAX_AKO_TOTAL < MIN_AKO_TOTAL:
        log.error("Invalid AKO total odds limits")
        return False

    return True


# ------------------------- DATA NORMALIZATION -------------------------

def parse_time(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return None


def get_active_sports():
    sports = api_get("/sports/", {})

    active = [
        sport for sport in sports
        if sport.get("active", True)
    ]

    if SPORT_ALLOWLIST:
        active = [
            sport for sport in active
            if sport.get("key") in SPORT_ALLOWLIST
        ]

    return active[:MAX_SPORTS_PER_CYCLE]


def fetch_sport_events(sport_key):
    return api_get(
        f"/sports/{sport_key}/odds/",
        {
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        },
    )


def find_betclic_bookmaker(event):
    for bookmaker in event.get("bookmakers", []):
        title = (
            bookmaker.get("title") or ""
        ).strip().lower()

        key = (
            bookmaker.get("key") or ""
        ).strip().lower()

        if BOOKMAKER_NAME in title or BOOKMAKER_NAME in key:
            return bookmaker

    return None


def extract_candidate(event, sport_key):
    event_id = event.get("id")

    if not event_id or event_already_used(event_id):
        return None

    commence = parse_time(event.get("commence_time"))

    if commence is None:
        return None

    now = datetime.now(timezone.utc)

    if commence < now - timedelta(minutes=PAST_GRACE_MINUTES):
        return None

    if commence > now + timedelta(hours=LOOKAHEAD_HOURS):
        return None

    bookmaker = find_betclic_bookmaker(event)

    if bookmaker is None:
        return None

    h2h = next(
        (
            market
            for market in bookmaker.get("markets", [])
            if market.get("key") == "h2h"
        ),
        None,
    )

    if not h2h:
        return None

    outcomes = h2h.get("outcomes", [])
    valid_prices = []

    for outcome in outcomes:
        try:
            price = float(outcome.get("price"))

            if math.isfinite(price) and price > 1.0:
                valid_prices.append(
                    (outcome.get("name", ""), price)
                )

        except (TypeError, ValueError):
            continue

    if len(valid_prices) < 2:
        return None

    raw_probs = [
        (name, price, 1.0 / price)
        for name, price in valid_prices
    ]

    probability_sum = sum(
        item[2] for item in raw_probs
    )

    if probability_sum <= 0:
        return None

    home = event.get("home_team", "Unknown")
    away = event.get("away_team", "Unknown")
    event_name = f"{home} vs {away}"

    choices = []

    for selection, price, raw_prob in raw_probs:
        normalized_prob = raw_prob / probability_sum

        if MIN_SINGLE_ODDS <= price <= MAX_SINGLE_ODDS:
            if normalized_prob >= MIN_MARKET_PROBABILITY:
                choices.append({
                    "event_id": event_id,
                    "event": event_name,
                    "selection": selection,
                    "odds": price,
                    "market_probability": normalized_prob,
                    "sport": sport_key,
                    "commence": commence,
                })

    if not choices:
        return None

    return max(
        choices,
        key=lambda item: item["market_probability"]
    )


# ------------------------- COUPON SELECTION -------------------------

def build_candidates(candidates):
    ranked = sorted(
        candidates,
        key=lambda item: (
            item["market_probability"],
            -item["odds"]
        ),
        reverse=True,
    )

    valid = []

    for combo in combinations(ranked, AKO_LEGS):
        event_ids = [
            item["event_id"] for item in combo
        ]

        if len(set(event_ids)) != AKO_LEGS:
            continue

        # Strict multi-layer check against historical database
        if any(event_already_used(eid) for eid in event_ids):
            continue

        total_odds = math.prod(
            item["odds"] for item in combo
        )

        if MIN_AKO_TOTAL <= total_odds <= MAX_AKO_TOTAL:
            signature_source = "|".join(
                sorted(
                    f"{item['event_id']}:{item['selection']}"
                    for item in combo
                )
            )

            signature = hashlib.sha256(
                signature_source.encode("utf-8")
            ).hexdigest()

            valid.append({
                "combo": combo,
                "total_odds": total_odds,
                "signature
