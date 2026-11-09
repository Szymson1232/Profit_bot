#!/usr/bin/env python3
# PITOS v5.16 — Open-night engine (all sports scanned, zero allowlist restrictions, strict 10/11 night window).

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
        self.wfile.write(b"PITOS v5.16 Active")
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
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "2.80"))
MIN_TOTAL_ODDS = float(os.getenv("MIN_TOTAL_ODDS", "1.90"))
MAX_TOTAL_ODDS = float(os.getenv("MAX_TOTAL_ODDS", "4.00"))

MAX_COUPONS = int(os.getenv("MAX_AKOS_PER_CYCLE", "3"))
MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.35"))
LOOKAHEAD = int(os.getenv("LOOKAHEAD_HOURS", "12")) # Ścisła nocka 10/11

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("pitos-v5.16")
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
            
    print(f"-> PITOS v5.16 DB initialized. Loaded {len(MEM_EVENTS)} events and {len(MEM_SIGNATURES)} signatures.", flush=True)


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
    """Pobiera WSZYSTKIE dostępne dyscypliny, odrzucając tylko NBA i rynki długoterminowe"""
    sports = api_get("/sports/")
    active = []
    for s in sports:
        if not s.get("active", True):
            continue
        key = s.get("key", "").lower()
        # Wykluczamy wyłącznie NBA oraz outrights/futures
        if any(b in key for b in ["winner", "futures", "outrights", "championship", "nba", "basketball_nba"]):
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
        if not ev_id:
            continue

        commence = parse_time(ev.get("commence_time"))
        # Ścisłe okno nocne 10/11
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
    print("-> PITOS v5.16 open-night scan started...", flush=True)
    sports = get_sports()
    print(f"-> Total active sports pooled (no allowlist limits): {len(sports)}", flush=True)

    all_candidates = []
    for sp in sports:
        all_candidates.extend(extract_candidates(sp))
        time.sleep(0.02)

    unique_map = {c["event_id"]: c for c in all_candidates}
    candidates = list(unique_map.values())
    print(f"-> Total unique candidates for tonight: {len(candidates)}", flush=True)

    if not candidates:
        print("-> No candidates found in tonight's open window.", flush=True)
        return

    candidates.sort(key=lambda x: (x["prob"], -x["odds"]), reverse=True)
    random.shuffle(candidates)

    valid_coupons = []

    for c in candidates:
        if c["odds"] >= 2.20:
            sig_source = f"{c['event_id']}:{c['selection']}"
            sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
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

            total_odds = math.prod(c["odds"] for c in combo)
            if MIN_TOTAL_ODDS <= total_odds <= MAX_TOTAL_ODDS:
                sig_source = "|".join(sorted(f"{c['event_id']}:{c['selection']}" for c in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()

                valid_coupons.append({
                    "combo": combo,
                    "total_odds": total_odds,
                    "signature": sig,
                    "event_ids": e_ids,
                    "type": f"AKO{r} NOCNY"
                })

    random.shuffle(valid_coupons)
    print(f"-> Valid open-night coupons found: {len(valid_coupons)}", flush=True)

    sent = 0
    used_in_cycle = set()

    for coupon in valid_coupons:
        if sent >= MAX_COUPONS:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue

        tot = coupon["total_odds"]
        c_type = coupon["type"]
        stake_u = 3 if tot <= 2.50 else (2 if tot <= 3.20 else 1)
        stake_pln = stake_u * 25

        lines = [f"<b>🥷 PITOS v5.16 | {c_type} #{sent+1}</b>", ""]
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
                        "INSERT OR IGNORE INTO sent_events (event_id, sport_key, event_name, coupon_id, status, created_at) VALUES (?, ?, ?, ?, 'sent', ?)",
                        (item["event_id"], item["sport"], item["event"], c_id, now_str)
                    )

            telegram_send(msg)
            used_in_cycle.update(coupon["event_ids"])
            sent += 1
            print(f"-> Published {c_type} coupon {c_id} successfully!", flush=True)
        except Exception as e:
            print(f"-> Failed to save/send coupon {c_id}: {e}", flush=True)

    print(f"-> Scan cycle finished. Sent {sent} coupons.", flush=True)


def background_worker():
    init_db()
    print("-> Background worker started. Triggering initial scan...", flush=True)
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
    print("-> PITOS v5.16 starting up...", flush=True)
    if not API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("-> CRITICAL: Missing environment variables!", flush=True)
        sys.exit(1)

    t_worker = threading.Thread(target=background_worker, daemon=True)
    t_worker.start()

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
