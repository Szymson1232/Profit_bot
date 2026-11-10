#!/usr/bin/env python3
# PITOS v5.46 — Night session target scan with 50 PLN (2j) stake configuration.

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
import random
from datetime import datetime, timedelta, timezone
from itertools import combinations
import requests

os.environ["PYTHONUNBUFFERED"] = "1"
PORT = int(os.getenv("PORT", "8080"))

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
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 18\r\n\r\nPITOS v5.46 Active"
                conn.sendall(response)
            except Exception:
                pass
            finally:
                conn.close()
    except Exception as e:
        print(f"-> Bind error: {e}", flush=True)

threading.Thread(target=instant_bind, daemon=True).start()

# ================= CONFIG =================
API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v5.db")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "3600"))

MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.10"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "3.50"))
MIN_TOTAL_ODDS = float(os.getenv("MIN_TOTAL_ODDS", "1.75"))
MAX_TOTAL_ODDS = float(os.getenv("MAX_TOTAL_ODDS", "4.50"))

MAX_COUPONS = 2  # Dokładnie 2 mocne picki na nockę
STAKE_PLN = 50   # Zgodnie z życzeniem: 50 zł
STAKE_UNITS = "2j"

MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.01"))
PAST_WINDOW_HOURS = 2
FUTURE_WINDOW_HOURS = 24

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
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
    if os.path.exists(SQLITE_PATH):
        try:
            os.remove(SQLITE_PATH)
            print("-> Purged old DB.", flush=True)
        except Exception:
            pass
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
    print("-> PITOS v5.46 DB ready.", flush=True)

def api_get(path, params=None):
    p = dict(params or {})
    p["apiKey"] = API_KEY
    time.sleep(0.3)
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    
    if res.status_code == 401:
        print(f"-> CRITICAL: API Key out of credits or invalid!", flush=True)
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
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

def run_cycle():
    print("-> Starting night target scan cycle (v5.46)...", flush=True)
    sports_data = api_get("/sports/")
    if not sports_data:
        print("-> Failed to fetch sports list.", flush=True)
        return

    sports = [
        s["key"].lower() for s in sports_data 
        if s.get("active", True) and "outrights" not in s["key"].lower() and "winner" not in s["key"].lower()
    ]
    print(f"-> Active sports pooled for night scan: {len(sports)}", flush=True)

    candidates = []
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=PAST_WINDOW_HOURS)
    window_end = now + timedelta(hours=FUTURE_WINDOW_HOURS)

    for sp in sports:
        events = api_get(f"/sports/{sp}/odds/", {"regions": "eu,us,uk", "markets": "h2h", "oddsFormat": "decimal"})
        if not events:
            continue

        for ev in events:
            ev_id = ev.get("id")
            if not ev_id:
                continue
            commence = parse_time(ev.get("commence_time"))
            if not commence or commence < window_start or commence > window_end:
                continue

            home, away = ev.get("home_team", "Unknown"), ev.get("away_team", "Unknown")
            bookies = ev.get("bookmakers", [])
            if not bookies:
                continue

            bk = bookies[0]
            h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue

            prices = []
            for o in h2h.get("outcomes", []):
                try:
                    pr = float(o.get("price"))
                    if math.isfinite(pr) and pr > 1.0:
                        prices.append((o.get("name", ""), pr))
                except Exception:
                    continue

            if len(prices) < 2:
                continue

            p_sum = sum(1.0 / pr for _, pr in prices)
            if p_sum <= 0:
                continue

            for name, pr in prices:
                norm_p = (1.0 / pr) / p_sum
                if MIN_SINGLE_ODDS <= pr <= MAX_SINGLE_ODDS and norm_p >= MIN_PROB:
                    candidates.append({
                        "event_id": ev_id, "event": f"{home} vs {away}", "selection": name,
                        "odds": pr, "prob": norm_p, "sport": sp, "commence": commence
                    })

    unique_map = {c["event_id"]: c for c in candidates}
    candidates = list(unique_map.values())
    print(f"-> Total night candidates found: {len(candidates)}", flush=True)

    if not candidates:
        print("-> No candidates found in window.", flush=True)
        return

    candidates.sort(key=lambda x: (x["prob"], -x["odds"]), reverse=True)
    valid_coupons = []

    for r in [2, 3]:
        for combo in combinations(candidates, r):
            e_ids = [c["event_id"] for c in combo]
            if len(set(e_ids)) != r:
                continue
            total_odds = math.prod(c["odds"] for c in combo)
            if MIN_TOTAL_ODDS <= total_odds <= MAX_TOTAL_ODDS:
                sig_source = "|".join(sorted(f"{i['event_id']}:{i['selection']}" for i in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
                if sig in MEM_SIGNATURES:
                    continue
                valid_coupons.append({
                    "combo": combo, "total_odds": total_odds, "signature": sig,
                    "event_ids": e_ids, "type": f"AKO{r} NIGHT ELITE"
                })

    random.shuffle(valid_coupons)
    sent = 0
    used_in_cycle = set()

    for coupon in valid_coupons:
        if sent >= MAX_COUPONS:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue

        tot = coupon["total_odds"]
        lines = [f"<b>🥷 PITOS v5.46 | {coupon['type']} #{sent+1}</b>", ""]
        for idx, item in enumerate(coupon["combo"], 1):
            lines.append(f"{idx}. <b>{html.escape(item['event'])}</b>\n   Typ: <i>{html.escape(item['selection'])}</i> | Kurs: <b>{item['odds']:.2f}</b>\n   {html.escape(item['sport'])} | {item['commence'].strftime('%d.%m %H:%M UTC')}")

        lines.extend(["", f"📊 <b>Kurs łączny: {tot:.2f}</b>", f"💰 <b>Zalecana stawka: {STAKE_UNITS} ({STAKE_PLN} zł)</b>"])
        msg = "\n".join(lines)
        c_id = coupon["signature"][:24]

        try:
            with get_db() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute("INSERT OR IGNORE INTO coupons VALUES (?, ?, ?, 'sent', ?)", (c_id, coupon["signature"], msg, now_str))
                for item in coupon["combo"]:
                    conn.execute("INSERT OR IGNORE INTO sent_events VALUES (?, ?, ?, ?, 'sent', ?)", (item["event_id"], item["sport"], item["event"], c_id, now_str))
            telegram_send(msg)
            MEM_SIGNATURES.add(coupon["signature"])
            used_in_cycle.update(coupon["event_ids"])
            sent += 1
            print(f"-> Sent coupon {c_id}", flush=True)
        except Exception as e:
            print(f"-> Send error: {e}", flush=True)

    print(f"-> Cycle done. Sent: {sent}", flush=True)

def main():
    print("-> PITOS v5.46 starting...", flush=True)
    init_db()
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"-> Cycle error: {e}", flush=True)
        print(f"-> Sleeping for {SCAN_INTERVAL} seconds...", flush=True)
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
