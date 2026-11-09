#!/usr/bin/env python3
# PITOS v5.19 — Fixed odds range bug & clean multi-sport night engine.

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
import http.server
import socketserver
import threading

os.environ["PYTHONUNBUFFERED"] = "1"

# ================= RAILWAY INSTANT HEALTH CHECK =================
PORT = int(os.getenv("PORT", "8080"))

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PITOS v5.19 Active")
    def log_message(self, format, *args):
        pass

def run_http_server():
    try:
        with socketserver.TCPServer(("", PORT), HealthHandler) as httpd:
            print(f"-> Instant health server active on port {PORT}", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"-> Health server error: {e}", flush=True)

t_http = threading.Thread(target=run_http_server, daemon=True)
t_http.start()


# ================= CONFIGURATION =================
API_KEY = os.getenv("ODDS_API_KEY", "3daceb3cf3247c5445f38d2392c47cc9").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v5.db")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "3600"))
BOOKMAKER = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.20"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "2.80"))  # NAPRAWIONE: POPRZEDNIO BYŁ TU BUG Z MIN
MIN_TOTAL_ODDS = float(os.getenv("MIN_TOTAL_ODDS", "1.90"))
MAX_TOTAL_ODDS = float(os.getenv("MAX_TOTAL_ODDS", "4.00"))

MAX_COUPONS = int(os.getenv("MAX_AKOS_PER_CYCLE", "3"))
MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.35"))
LOOKAHEAD = int(os.getenv("LOOKAHEAD_HOURS", "12"))

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("pitos-v5.19")
session = requests.Session()

MEM_EVENTS = set()
MEM_TEAMS = set()
MEM_SIGNATURES = set()


# ================= DATABASE & PURGE =================
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
            print(f"-> Purged old database file {SQLITE_PATH} for a fresh start.", flush=True)
        except Exception as e:
            print(f"-> Failed to purge old DB: {e}", flush=True)

    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_events (
                event_id TEXT PRIMARY KEY,
                sport_key TEXT NOT NULL,
                event_name TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
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
            
    print("-> PITOS v5.19 DB freshly initialized. Memory cleared.", flush=True)


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
        banned_keywords = [
            "winner", "futures", "outrights", "championship", 
            "nba", "basketball_nba", "americanfootball", "gridiron",
            "icehockey", "nhl"
        ]
        if any(b in key for b in banned_keywords):
            continue
        active.append(key)
    return active

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

        home = ev.get("home_team", "Unknown")
        away = ev.get("away_team", "Unknown")

        if home.strip().lower() in MEM_TEAMS or away.strip().lower() in MEM_TEAMS:
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

        ev_name = f"{home} vs {away}"

        for name, pr, raw_p in probs:
            norm_p = raw_p / p_sum
            if MIN_SINGLE_ODDS <= pr <= MAX_SINGLE_ODDS and norm_p >= MIN_PROB:
                candidates.append({
                    "event_id": ev_id,
                    "event": ev_name,
                    "home_team": home,
                    "away_team": away,
                    "selection": name,
                    "odds": pr,
                    "prob": norm_p,
                    "sport": sport_key,
                    "commence": commence
                })
    return candidates


def run_cycle():
    print("-> PITOS v5.19 fresh scan started (Corrected odds range)...", flush=True)
    sports = get_sports()
    print(f"-> Active clean sports pooled: {len(sports)}", flush=True)

    all_candidates = []
    for sp in sports:
        all_candidates.extend(extract_candidates(sp))
        time.sleep(0.02)

    unique_map = {c["event_id"]: c for c in all_candidates}
    candidates = list(unique_map.values())
    print(f"-> Total unique fresh candidates: {len(candidates)}", flush=True)

    if not candidates:
        print("-> No new fresh candidates found in window.", flush=True)
        return

    candidates.sort(key=lambda x: (x["prob"], -x["odds"]), reverse=True)
    random.shuffle(candidates)

    valid_coupons = []

    for c in candidates:
        if c["odds"] >= 2.20:
            sig_source = f"{c['event_id']}:{c['selection']}"
            sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
            if sig not in MEM_SIGNATURES:
                valid_coupons.append({
                    "combo": [c],
                    "total_odds": c["odds"],
                    "signature": sig,
                    "event_ids": [c["event_id"]],
                    "type": "SINGIEL NOCNY"
                })

    for r in [2, 3]:
        for combo in combinations(candidates, r):
            e_ids = [c["event_id"] for c in combo]
            if len(set(e_ids)) != r:
                continue
            if any(eid in MEM_EVENTS for eid in e_ids):
                continue

            total_odds = math.prod(c["odds"] for c in combo)
            if MIN_TOTAL_ODDS <= total_odds <= MAX_TOTAL_ODDS:
                sig_source = "|".join(sorted(f"{c['event_id']}:{c['selection']}" for c in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()

                if sig in MEM_SIGNATURES:
                    continue

                valid_coupons.append({
                    "combo": combo,
                    "total_odds": total_odds,
                    "signature": sig,
                    "event_ids": e_ids,
                    "type": f"AKO{r} NOCNY"
                })

    random.shuffle(valid_coupons)
    print(f"-> Valid clean coupons found: {len(valid_coupons)}", flush=True)

    sent = 0
    used_in_cycle = set()

    for coupon in valid_coupons:
        if sent >= MAX_COUPONS:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue
        if any(eid in MEM_EVENTS for eid in coupon["event_ids"]):
            continue

        tot = coupon["total_odds"]
        c_type = coupon["type"]
        stake_u = 3 if tot <= 2.50 else (2 if tot <= 3.20 else 1)
        stake_pln = stake_u * 25

        lines = [f"<b>🥷 PITOS v5.19 | {c_type} #{sent+1}</b>", ""]
        for idx, item in enumerate(coupon["combo"], 1):
            ev_esc = html.escape(item["event"])
            sel_esc = html.escape(item["selection"])
            sp_esc = html.escape(item["sport"])
            kick = item["commence"].strftime("%d.%m %H:%M UTC")
            lines.append(f"{idx}. <b>{ev_esc}</b>\n   Typ: <i>{sel_esc}</i> | Kurs: <b>{item['odds']:.2f}</b>\n   {sp_esc} | {kick}")

        lines.extend(["", f"📊 <b>Kurs łączny: {tot:.2f}</b>", f"💰 <b>Zalecana stawka: {stake_u}j ({stake_pln} zł)</b>"])
        msg = "\n".join(lines)
        c_id = coupon["signature"][:24]

        try:
            with get_db() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO coupons (coupon_id, signature, message, status, created_at) VALUES (?, ?, ?, 'sent', ?)",
                    (c_id, coupon["signature"], msg, now_str)
                )
                for item in coupon["combo"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO sent_events (event_id, sport_key, event_name, home_team, away_team, coupon_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'sent', ?)",
                        (item["event_id"], item["sport"], item["event"], item["home_team"], item["away_team"], c_id, now_str)
                    )

            telegram_send(msg)
            MEM_SIGNATURES.add(coupon["signature"])
            for eid in coupon["event_ids"]:
                MEM_EVENTS.add(eid)
            for item in coupon["combo"]:
                if item["home_team"]: MEM_TEAMS.add(item["home_team"].strip().lower())
                if item["away_team"]: MEM_TEAMS.add(item["away_team"].strip().lower())

            used_in_cycle.update(coupon["event_ids"])
            sent += 1
            print(f"-> Published {c_type} coupon {c_id} successfully!", flush=True)
        except Exception as e:
            print(f"-> Failed to save/send coupon {c_id}: {e}", flush=True)

    print(f"-> Scan cycle finished. Sent {sent} coupons.", flush=True)


def background_worker():
    init_db()
    print("-> Background worker started. Triggering initial clean scan...", flush=True)
    try:
        run_cycle()
    except Exception as e:
        print(f"-> Initial scan crashed: {e}", flush=True)

    while True:
        start = time.monotonic()
        try:
            run_cycle()
        except Exception as e:
            print(f"-> Cycle crashed with error: {e}", flush=True)

        elapsed = time.monotonic() - start
        sleep_time = max(30, SCAN_INTERVAL - int(elapsed))
        print(f"-> Sleeping for {sleep_time} seconds...", flush=True)
        time.sleep(sleep_time)


# ================= MAIN =================
def main():
    print("-> PITOS v5.19 starting up...", flush=True)
    if not API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("-> CRITICAL: Missing environment variables!", flush=True)
        sys.exit(1)

    t_worker = threading.Thread(target=background_worker, daemon=True)
    t_worker.start()

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
