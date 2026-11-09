#!/usr/bin/env python3
# PITOS v4 — Betclic-only selector with persistent event deduplication.
# Required Railway Variables:
#   ODDS_API_KEY
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# Recommended Railway Volume:
#   Mount path: /data
#   SQLITE_PATH: /data/pitos_v4.db
#
# IMPORTANT:
# This bot ranks selections using Betclic's market-implied probabilities.
# It does NOT independently predict outcomes and cannot guarantee profit.

import os
import time
import math
import sqlite3
import logging
import hashlib
import html
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from itertools import combinations

import requests

# ------------------------- CONFIGURATION -------------------------

API_KEY = os.getenv("3daceb3cf3247c5445f38d2392c47cc9", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo", "").strip()
TELEGRAM_CHAT_ID = os.getenv("-1004306783026", "").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/pitos_v4.db")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "7200"))
MAX_SPORTS_PER_CYCLE = int(os.getenv("MAX_SPORTS_PER_CYCLE", "25"))

# Odds are taken ONLY from a bookmaker whose title contains "Betclic".
BOOKMAKER_NAME = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.05"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "1.75"))
MIN_AKO_TOTAL = float(os.getenv("MIN_AKO_TOTAL", "2.00"))
MAX_AKO_TOTAL = float(os.getenv("MAX_AKO_TOTAL", "3.20"))
AKO_LEGS = int(os.getenv("AKO_LEGS", "3"))
MAX_AKOS_PER_CYCLE = int(os.getenv("MAX_AKOS_PER_CYCLE", "1"))

# Only events starting between now and this many hours ahead are considered.
LOOKAHEAD_HOURS = int(os.getenv("LOOKAHEAD_HOURS", "24"))
# Ignore events that started more than this many minutes ago.
PAST_GRACE_MINUTES = int(os.getenv("PAST_GRACE_MINUTES", "0"))

# Conservative market filter. This is a normalized implied probability
# derived from all Betclic outcomes in the h2h market, not a model prediction.
MIN_MARKET_PROBABILITY = float(os.getenv("MIN_MARKET_PROBABILITY", "0.62"))

# Optional allow-list. Empty means all active sports returned by the API.
# Example: SPORT_ALLOWLIST=soccer_epl,basketball_nba
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
    # Ensure the configured parent directory exists (e.g. Railway /data volume).
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    conn = db_connect()
    try:
        yield conn
        conn.commit()
