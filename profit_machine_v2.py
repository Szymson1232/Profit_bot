#!/usr/bin/env python3
# PITOS v8.1 Shark Edition — Basketball, Ice Hockey, Tennis | AKO3 / AKO2 + gated RISK SINGLE
# Min 3 / Max 10 coupons per session (or clean PASS) | 08:00 & 20:00 PL | Stakes 25 / 50 / 75 / 100 PLN.

import os
import sys
import socket
import threading
import time
import math
import sqlite3
import logging
import hashlib
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from itertools import combinations
import requests

os.environ["PYTHONUNBUFFERED"] = "1"
PORT = int(os.getenv("PORT", "8080"))
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


# ================= NATYCHMIASTOWY BIND PORTU =================
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
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 22\r\n\r\nPITOS v8.1 Shark Active"
                conn.sendall(response)
            except Exception:
                pass
            finally:
                conn.close()
    except Exception as e:
        print(f"-> Bind error: {e}", flush=True)


threading.Thread(target=instant_bind, daemon=True).start()

# ================= CONFIG =================
API_KEY = os.getenv("ODDS_API_KEY", "133d53ed2236d1e09849deabd16155e6").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v81_shark.db")

# 3 główne dziedziny: koszykówka, hokej, tenis
TARGET_SPORTS_KEYWORDS = ["basketball", "icehockey", "tennis"]

# Każda selekcja pod AKO bez podatku na Betclic min. 1.20; tu bezpieczniej od 1.22
MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.22"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "2.25"))

# Zakresy kursów łącznych dla AKO2 i AKO3
MIN_AKO2_ODDS = float(os.getenv("MIN_AKO2_ODDS", "1.80"))
MAX_AKO2_ODDS = float(os.getenv("MAX_AKO2_ODDS", "3.50"))
MIN_AKO3_ODDS = float(os.getenv("MIN_AKO3_ODDS", "2.35"))
MAX_AKO3_ODDS = float(os.getenv("MAX_AKO3_ODDS", "6.20"))

# Osobne progi dla pojedynczego kuponu RISK
RISK_SINGLE_MIN_ODDS = float(os.getenv("RISK_SINGLE_MIN_ODDS", "1.60"))
RISK_SINGLE_MAX_ODDS = float(os.getenv("RISK_SINGLE_MAX_ODDS", "2.40"))
RISK_SINGLE_MIN_PROB = float(os.getenv("RISK_SINGLE_MIN_PROB", "0.48"))
RISK_SINGLE_MIN_EDGE = float(os.getenv("RISK_SINGLE_MIN_EDGE", "0.045"))

# Wolumen: minimum 3, maksimum 10 kuponów na sesję
MIN_COUPONS_PER_SESSION = int(os.getenv("MIN_COUPONS_PER_SESSION", "3"))
MAX_COUPONS_PER_SESSION = int(os.getenv("MAX_COUPONS_PER_SESSION", "10"))
MAX_RISK_SINGLES_PER_SESSION = int(os.getenv("MAX_RISK_SINGLES_PER_SESSION", "1"))

UNIT_PLN = 25  # Stawki: 1j = 25 zł, 2j = 50 zł, 3j = 75 zł, 4j = 100 zł
MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.38"))
MIN_EDGE = float(os.getenv("MIN_MARKET_EDGE", "0.005"))
LOOKAHEAD_HOURS = int(os.getenv("LOOKAHEAD_HOURS", "24"))

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
session = requests.Session()
MEM_SIGNATURES = set()


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
                sport_key TEXT,
                event_name TEXT,
                coupon_id TEXT,
                status TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                coupon_id TEXT PRIMARY KEY,
                signature TEXT UNIQUE,
                message TEXT,
                status TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets_history (
                coupon_id TEXT PRIMARY KEY,
                session_type TEXT,
                coupon_kind TEXT,
                total_odds REAL,
                units INTEGER,
                stake_pln INTEGER,
                est_prob REAL,
                est_edge REAL,
                result TEXT,
                pnl_pln REAL,
                created_at TEXT
            )
        """)
    print("-> PITOS v8.1 Shark DB ready.", flush=True)


def api_get(path, params=None):
    if not API_KEY:
        print("-> Brak ustawionego klucza ODDS_API_KEY.", flush=True)
        return None
    p = dict(params or {})
    p["apiKey"] = API_KEY
    time.sleep(0.3)
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code == 401:
        print("-> CRITICAL: API Key out of credits or invalid!", flush=True)
    elif res.status_code == 422:
        return None
    elif res.status_code == 429:
        time.sleep(10)
        res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code != 200:
        return None
    return res.json()


def telegram_send(msg):
    url = f"{TELEGRAM_BASE}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    res = session.post(url, json=payload, timeout=20)
    if res.status_code != 200 or not res.json().get("ok"):
        raise RuntimeError("Telegram failed")


def parse_time(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def compute_consensus_with_edge(bookmakers):
    """Liczy uczciwe prawdopodobieństwo konsensusu oraz najlepszy dostępny kurs i przewagę (edge)."""
    agg = {}
    book_count = 0

    for bk in bookmakers:
        h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
        if not h2h:
            continue
        outcomes = h2h.get("outcomes", [])
        parsed = []
        for o in outcomes:
            name = (o.get("name") or "").strip()
            try:
                pr = float(o.get("price"))
            except Exception:
                continue
            if name and math.isfinite(pr) and pr > 1.0:
                parsed.append((name, pr))

        if len(parsed) < 2:
            continue

        overround = sum(1.0 / pr for _, pr in parsed)
        if overround <= 0:
            continue

        book_count += 1
        for name, pr in parsed:
            fair_p = (1.0 / pr) / overround
            if name not in agg:
                agg[name] = {"prob_sum": 0.0, "best_odds": pr, "n": 0}
            agg[name]["prob_sum"] += fair_p
            if pr > agg[name]["best_odds"]:
                agg[name]["best_odds"] = pr
            agg[name]["n"] += 1

    if book_count == 0 or not agg:
        return []

    results = []
    for name, st in agg.items():
        if st["n"] >= 1:
            avg_prob = st["prob_sum"] / st["n"]
            best_odds = st["best_odds"]
            edge = (best_odds * avg_prob) - 1.0
            results.append((name, best_odds, avg_prob, edge, st["n"]))
    return results


def get_ako_stake(legs_count, total_odds, min_prob_in_combo, combo_prob, combo_edge):
    """Stawki wyłącznie 25 / 50 / 75 / 100 zł (1j..4j)."""
    if legs_count == 2 and min_prob_in_combo >= 0.55 and combo_prob >= 0.30 and combo_edge >= 0.045 and total_odds <= 2.50:
        return 4, 4 * UNIT_PLN  # 100 zł
    elif legs_count == 3 and min_prob_in_combo >= 0.48 and combo_prob >= 0.13 and combo_edge >= 0.05 and total_odds <= 4.00:
        return 3, 3 * UNIT_PLN  # 75 zł
    elif combo_prob >= 0.18 and combo_edge >=
