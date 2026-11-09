#!/usr/bin/env python3
# PITOS v5 — Clean, robust multi-sport betting selector built from scratch.

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
MAX_SPORTS = int(os.getenv("MAX_SPORTS_PER_CYCLE", "25"))
BOOKMAKER = os.getenv("BOOKMAKER_NAME", "Betclic").strip().lower()

MIN_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.05"))
MAX_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "1.75"))
MIN_AKO = float(os.getenv("MIN_AKO_TOTAL", "2.00"))
MAX_AKO = float(os.getenv("MAX_AKO_TOTAL", "3.20"))
AKO_LEGS = int(os.getenv("AKO_LEGS", "3"))
MAX_COUPONS = int(os.getenv("MAX_AKOS_PER_CYCLE", "3"))

MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.58"))
LOOKAHEAD = int(os.getenv("LOOKAHEAD_HOURS", "24"))

SPORT_ALLOWLIST = {
    s.strip() for s in os.getenv("SPORT_ALLOWLIST", "soccer,basketball,icehockey,tennis,eports,americanfootball,baseball").split(",")
    if s.strip()
}

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("pitos-v5")
session = requests.Session()

# Pamięć podręczna RAM
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
            
    log.info("DB initialized. Loaded %d events and %d signatures into RAM.", len(MEM_EVENTS), len(MEM_SIGNATURES))


# ================= API & TELEGRAM =================
def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("Missing ODDS_API_KEY")
    p = dict(params or {})
    p["apiKey"] = API_KEY
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    
    rem = res.headers.get("x-requests-remaining")
    if rem:
        log.info("API Quota remaining: %s", rem)
        
    if res.status_code != 200:
        log.error("API Error %s: %s", res.status_code, res.text[:500])
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
        raise RuntimeError(f"Telegram failed: {res.status_code}, {str(data)[:300]}")
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
        key = s.get("key", "")
        if any(b in key for b in ["winner", "futures", "outrights", "championship"]):
            continue
        if not SPORT_ALLOWLIST or any(a in key for a in SPORT_ALLOWLIST):
            active.append(key)
    return active[:MAX_SPORTS]

def extract_candidates(sport_key):
    try:
        events = api_get(f"/sports/{sport_key}/odds/", {"regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
    except Exception as e:
        log.warning("Failed to fetch odds for %s: %s", sport_key, e)
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

        # Szukamy Betclica
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

        # Prawdopodobieństwa
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
    log.info("===== PITOS v5 scan started =====")
    sports = get_sports()
    log.info("Active sports to scan: %d", len(sports))

    all_candidates = []
    for sp in sports:
        all_candidates.extend(extract_candidates(sp))
        time.sleep(0.2)

    # Unikalność po event_id
    unique_map = {c["event_id"]: c for c in all_candidates}
    candidates = list(unique_map.values())
    log.info("Total unique eligible candidates: %d", len(candidates))

    if not candidates:
        log.info("No candidates found in this cycle.")
        return

    # Sortowanie i losowanie puli dla świeżości
    candidates.sort(key=lambda x: (x["prob"], -x["odds"]), reverse=True)
    random.shuffle(candidates)

    valid_coupons = []
    for combo in combinations(candidates, AKO_LEGS):
        e_ids = [c["event_id"] for c in combo]
        if len(set(e_ids)) != AKO_LEGS:
            continue
        if any(eid in MEM_EVENTS for eid in e_ids):
            continue

        total_odds = math.prod(c["odds"] for c in combo)
        if MIN_AKO <= total_odds <= MAX_AKO:
            sig_source = "|".join(sorted(f"{c['event_id']}:{c['selection']}" for c in combo))
            sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()

            if sig in MEM_SIGNATURES:
                continue

            valid_coupons.append({
                "combo": combo,
                "total_odds": total_odds,
                "signature": sig,
                "event_ids": e_ids
            })

    random.shuffle(valid_coupons)
    log.info("Valid unique AKO combinations found: %d", len(valid_coupons))

    sent = 0
    used_in_cycle = set()

    for coupon in valid_coupons:
        if sent >= MAX_COUPONS:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue
        if any(eid in MEM_EVENTS for eid in coupon["event_ids"]):
            continue
        if coupon["signature"] in MEM_SIGNATURES:
            continue

        tot = coupon["total_odds"]
        stake_u = 3 if tot <= 2.30 else (2 if tot <= 2.75 else 1)
        stake_pln = stake_u * 25

        lines = [f"<b>🥷 PITOS v5 | AKO{AKO_LEGS} #{sent+1}</b>", ""]
        for idx, item in enumerate(coupon["combo"], 1):
            ev_esc = html.escape(item["event"])
            sel_esc = html.escape(item["selection"])
            sp_esc = html.escape(item["sport"])
            kick = item["commence"].strftime("%d.%m %H:%M UTC")
            lines.append(f"{idx}. <b>{ev_esc}</b>\n   Typ: <i>{sel_esc}</i> | Kurs: <b>{item['odds']:.2f}</b>\n   {sp_esc} | {kick}")

        lines.extend(["", f"📊 <b>Kurs łączny: {tot:.2f}</b>", f"💰 <b>Zalecana stawka: {stake_u}j ({stake_pln} zł)</b>"])
        msg = "\n".join(lines)
        c_id = coupon["signature"][:24]

        # Zapis do bazy
        try:
            with get_db() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO coupons (coupon_id, signature, message, status, created_at) VALUES (?, ?, ?, 'sent', ?)",
                    (c_id, coupon["signature"], msg, now_str)
                )
                for item in coupon["combo"]:
                    conn.execute(
                        "INSERT INTO sent_events (event_id, sport_key, event_name, coupon_id, status, created_at) VALUES (?, ?, ?, ?, 'sent', ?)",
                        (item["event_id"], item["sport"], item["event"], c_id, now_str)
                    )

            telegram_send(msg)
            MEM_SIGNATURES.add(coupon["signature"])
            for eid in coupon["event_ids"]:
                MEM_EVENTS.add(eid)
            used_in_cycle.update(coupon["event_ids"])
            sent += 1
            log.info("Published coupon %s successfully.", c_id)
        except Exception as e:
            log.exception("Failed to save/send coupon %s: %s", c_id, e)

    log.info("===== PITOS v5 scan end; sent %d coupons =====", sent)


# ================= MAIN =================
def main():
    if not API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing critical environment variables!")
        sys.exit(1)

    init_db()
    log.info("PITOS v5 started. Interval: %ds", SCAN_INTERVAL)

    first = True
    while True:
        start = time.monotonic()
        try:
            run_cycle()
            first = False
        except Exception:
            log.exception("Cycle crashed unexpectedly.")

        elapsed = time.monotonic() - start
        sleep_time = max(30, SCAN_INTERVAL - int(elapsed))
        log.info("Sleeping for %d seconds...", sleep_time)
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
