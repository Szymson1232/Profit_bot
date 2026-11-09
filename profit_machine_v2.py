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

    h2h = next((m for m in bookmaker.get("markets", []) if m.get("key") == "h2h"), None)
    if not h2h:
        return None

    outcomes = h2h.get("outcomes", [])
    valid_prices = []
    for outcome in outcomes:
        try:
            price = float(outcome.get("price"))
            if math.isfinite(price) and price > 1.0:
                valid_prices.append((outcome.get("name", ""), price))
        except (TypeError, ValueError):
            continue

    if len(valid_prices) < 2:
        return None

    raw_probs = [(name, price, 1.0 / price) for name, price in valid_prices]
    probability_sum = sum(item[2] for item in raw_probs)
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

    return max(choices, key=lambda item: item["market_probability"])


# ------------------------- COUPON SELECTION -------------------------

def build_candidates(candidates):
    ranked = sorted(
        candidates,
        key=lambda item: (item["market_probability"], -item["odds"]),
        reverse=True,
    )
    
    random.shuffle(ranked)

    valid = []
    for combo in combinations(ranked, AKO_LEGS):
        event_ids = [item["event_id"] for item in combo]
        if len(set(event_ids)) != AKO_LEGS:
            continue

        if any(event_already_used(eid) for eid in event_ids):
            continue

        total_odds = math.prod(item["odds"] for item in combo)
        if MIN_AKO_TOTAL <= total_odds <= MAX_AKO_TOTAL:
            signature_source = "|".join(sorted(f"{item['event_id']}:{item['selection']}" for item in combo))
            signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()

            if coupon_signature_exists(signature):
                continue

            valid.append({
                "combo": combo,
                "total_odds": total_odds,
                "signature": signature,
                "event_ids": event_ids,
            })

    random.shuffle(valid)
    return valid


def format_coupon(coupon, number):
    total_odds = coupon["total_odds"]

    if total_odds <= 2.30:
        stake_units = 3
        stake_pln = 75
    elif total_odds <= 2.75:
        stake_units = 2
        stake_pln = 50
    else:
        stake_units = 1
        stake_pln = 25

    lines = [
        f"<b>🥷 PITOS v4 | AKO{AKO_LEGS} #{number}</b>",
        ""
    ]

    for index, item in enumerate(coupon["combo"], 1):
        event = html.escape(item["event"])
        selection = html.escape(item["selection"])
        sport = html.escape(item["sport"])
        kickoff = item["commence"].strftime("%d.%m %H:%M UTC")

        lines.append(
            f"{index}. <b>{event}</b>\n"
            f"   Typ: <i>{selection}</i> | "
            f"Kurs: <b>{item['odds']:.2f}</b>\n"
            f"   {sport} | {kickoff}"
        )

    lines.extend([
        "",
        f"📊 <b>Kurs łączny: {total_odds:.2f}</b>",
        f"💰 <b>Zalecana stawka: {stake_units}j ({stake_pln} zł)</b>",
    ])

    return "\n".join(lines)


def reserve_coupon(coupon, message):
    now = datetime.now(timezone.utc).isoformat()
    coupon_id = coupon["signature"][:24]

    try:
        with db_session() as conn:
            if coupon_signature_exists(coupon["signature"]):
                return None

            for eid in coupon["event_ids"]:
                if event_already_used(eid):
                    return None

            conn.execute(
                "INSERT INTO coupons (coupon_id, signature, message, status, created_at) VALUES (?, ?, ?, 'reserved', ?)",
                (coupon_id, coupon["signature"], message, now),
            )

            for item in coupon["combo"]:
                conn.execute(
                    "INSERT INTO sent_events (event_id, sport_key, event_name, commence_time, coupon_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'reserved', ?)",
                    (item["event_id"], item["sport"], item["event"], item["commence"].isoformat(), coupon_id, now),
                )

        MEMORY_SENT_SIGNATURES.add(coupon["signature"])
        for eid in coupon["event_ids"]:
            MEMORY_SENT_EVENTS.add(eid)

        return coupon_id

    except sqlite3.IntegrityError:
        return None


def mark_coupon_sent(coupon_id):
    now = datetime.now(timezone.utc).isoformat()
    with db_session() as conn:
        conn.execute("UPDATE coupons SET status='sent', sent_at=? WHERE coupon_id=?", (now, coupon_id))
        conn.execute("UPDATE sent_events SET status='sent', sent_at=? WHERE coupon_id=?", (now, coupon_id))


def record_scan(sports_count, events_count, candidates_count, sent_count, note=""):
    with db_session() as conn:
        conn.execute(
            "INSERT INTO scan_log (scanned_at, sports_count, events_count, candidates_count, coupons_sent, note) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), sports_count, events_count, candidates_count, sent_count, note[:500]),
        )


# ------------------------- MAIN SCAN CYCLE -------------------------

def run_cycle(reason="scheduled"):
    log.info("===== PITOS v4 scan start: %s =====", reason)

    sports = get_active_sports()
    candidates = []
    events_seen = 0
    bookmaker_seen = False

    for sport in sports:
        sport_key = sport.get("key")
        try:
            events = fetch_sport_events(sport_key)
            events_seen += len(events)

            for event in events:
                if find_betclic_bookmaker(event):
                    bookmaker_seen = True

                candidate = extract_candidate(event, sport_key)
                if candidate:
                    candidates.append(candidate)

            time.sleep(0.25)
        except Exception as exc:
            log.exception("Error scanning %s: %s", sport_key, exc)

    unique = {}
    for candidate in candidates:
        unique.setdefault(candidate["event_id"], candidate)
    candidates = list(unique.values())

    log.info("Events scanned=%d | unique eligible candidates=%d | Betclic found=%s", events_seen, len(candidates), bookmaker_seen)

    if not bookmaker_seen:
        record_scan(len(sports), events_seen, len(candidates), 0, "Betclic not found")
        return

    possible_coupons = build_candidates(candidates)
    log.info("Valid AKO combinations: %d", len(possible_coupons))

    used_this_cycle = set()
    sent_count = 0

    for coupon in possible_coupons:
        if sent_count >= MAX_AKOS_PER_CYCLE:
            break

        if any(event_id in used_this_cycle for event_id in coupon["event_ids"]):
            continue

        if any(event_already_used(event_id) for event_id in coupon["event_ids"]):
            continue

        if coupon_signature_exists(coupon["signature"]):
            continue

        message = format_coupon(coupon, sent_count + 1)
        coupon_id = reserve_coupon(coupon, message)

        if coupon_id is None:
            continue

        try:
            telegram_send(message)
            mark_coupon_sent(coupon_id)
            used_this_cycle.update(coupon["event_ids"])
            sent_count += 1
            log.info("Published AKO coupon %s", coupon_id)
        except Exception as exc:
            log.exception("Telegram delivery failed for %s: %s", coupon_id, exc)

    record_scan(len(sports), events_seen, len(candidates), sent_count, "OK" if sent_count else "No coupon published")
    log.info("===== PITOS v4 scan end; coupons sent=%d =====", sent_count)


# ------------------------- MAIN -------------------------

def main():
    if not verify_environment():
        sys.exit(1)

    init_db()
    log.info("PITOS v4 started. Path: %s", SQLITE_PATH)

    first_cycle = True
    while True:
        started = time.monotonic()
        try:
            run_cycle("startup" if first_cycle else "scheduled")
            first_cycle = False
        except Exception:
            log.exception("Cycle crashed.")

        elapsed = time.monotonic() - started
        sleep_for = max(30, SCAN_INTERVAL_SECONDS - int(elapsed))
        log.info("Next scan in approx %d seconds.", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
