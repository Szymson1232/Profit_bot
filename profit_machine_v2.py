#!/usr/bin/env python3
# PITOS v7.5 — Multi-sport AKO2 selector, consensus probability, bet ledger.

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
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 23\r\n\r\nPITOS v7.5 Multi-AKO2"
                conn.sendall(response)
            except Exception:
                pass
            finally:
                conn.close()
    except Exception as e:
        print(f"-> Bind error: {e}", flush=True)


threading.Thread(target=instant_bind, daemon=True).start()

# ================= CONFIG =================
API_KEY = os.getenv("ODDS_API_KEY", "d0fc5228dffea0fe88d6dc2c4c9e10df").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v75_multi.db")

# Domyślnie szerszy zestaw płynnych sportów; można nadpisać np. "basketball" lub "basketball,icehockey,soccer,tennis"
RAW_SPORTS = os.getenv("SPORT_KEYWORDS", "basketball,icehockey,soccer")
TARGET_SPORTS_KEYWORDS = [k.strip().lower() for k in RAW_SPORTS.split(",") if k.strip()]

# Parametry pod AKO2 i promocję Betclic (każda selekcja min. 1.20)
MIN_SINGLE_ODDS = float(os.getenv("MIN_SINGLE_ODDS", "1.35"))
MAX_SINGLE_ODDS = float(os.getenv("MAX_SINGLE_ODDS", "1.95"))
MIN_TOTAL_ODDS = float(os.getenv("MIN_TOTAL_ODDS", "1.95"))
MAX_TOTAL_ODDS = float(os.getenv("MAX_TOTAL_ODDS", "3.10"))

MAX_COUPONS_PER_SESSION = int(os.getenv("MAX_COUPONS_PER_SESSION", "3"))
UNIT_PLN = 25  # 1j = 25 zł, 2j = 50 zł, 3j = 75 zł
MIN_PROB = float(os.getenv("MIN_MARKET_PROBABILITY", "0.42"))
LOOKAHEAD_HOURS = int(os.getenv("LOOKAHEAD_HOURS", "16"))

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
                total_odds REAL,
                units INTEGER,
                stake_pln INTEGER,
                est_prob REAL,
                result TEXT,
                pnl_pln REAL,
                created_at TEXT
            )
        """)
    print("-> PITOS v7.5 DB ready.", flush=True)


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


def compute_consensus_selections(bookmakers):
    """Liczy średni kurs i znormalizowane prawdopodobieństwo po wszystkich dostępnych bukmacherach."""
    agg_odds = {}
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
            if name not in agg_odds:
                agg_odds[name] = {"odds_sum": 0.0, "prob_sum": 0.0, "n": 0}
            agg_odds[name]["odds_sum"] += pr
            agg_odds[name]["prob_sum"] += fair_p
            agg_odds[name]["n"] += 1

    if book_count == 0 or not agg_odds:
        return []

    results = []
    for name, st in agg_odds.items():
        if st["n"] > 0:
            avg_odds = st["odds_sum"] / st["n"]
            avg_prob = st["prob_sum"] / st["n"]
            results.append((name, avg_odds, avg_prob, st["n"]))
    return results


def get_stake(total_odds, min_prob_in_combo, combo_prob):
    # Wyłącznie 1j (25 zł), 2j (50 zł) lub 3j (75 zł)
    if min_prob_in_combo >= 0.58 and combo_prob >= 0.34 and total_odds <= 2.40:
        return 3, 3 * UNIT_PLN  # 75 zł
    elif min_prob_in_combo >= 0.48 and combo_prob >= 0.24 and total_odds <= 2.85:
        return 2, 2 * UNIT_PLN  # 50 zł
    else:
        return 1, 1 * UNIT_PLN  # 25 zł


def run_session(session_type):
    print(f"-> Starting v7.5 AKO2 scan ({TARGET_SPORTS_KEYWORDS}) for [{session_type}]...", flush=True)
    sports_data = api_get("/sports/")
    if not sports_data:
        print("-> Failed to fetch sports list or invalid API key.", flush=True)
        return

    sports = []
    for s in sports_data:
        key = s.get("key", "").lower()
        if not s.get("active", True) or "outrights" in key or "winner" in key:
            continue
        if any(kw in key for kw in TARGET_SPORTS_KEYWORDS):
            sports.append(key)

    print(f"-> Active sports leagues ({len(sports)}): {sports}", flush=True)

    candidates = []
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=LOOKAHEAD_HOURS)

    for sp in sports:
        events = api_get(
            f"/sports/{sp}/odds/",
            {"regions": "eu,us,uk", "markets": "h2h", "oddsFormat": "decimal"},
        )
        if not events:
            continue

        for ev in events:
            ev_id = ev.get("id")
            if not ev_id:
                continue
            commence = parse_time(ev.get("commence_time"))
            if not commence or commence < now or commence > window_end:
                continue

            home, away = ev.get("home_team", "Unknown"), ev.get("away_team", "Unknown")
            bookmakers = ev.get("bookmakers", [])
            if not bookmakers:
                continue

            consensus = compute_consensus_selections(bookmakers)
            if not consensus:
                continue

            for name, avg_odds, avg_prob, bk_n in consensus:
                if MIN_SINGLE_ODDS <= avg_odds <= MAX_SINGLE_ODDS and avg_prob >= MIN_PROB:
                    candidates.append({
                        "event_id": ev_id,
                        "event": f"{home} vs {away}",
                        "selection": name,
                        "odds": avg_odds,
                        "prob": avg_prob,
                        "books": bk_n,
                        "sport": sp,
                        "commence": commence,
                    })

    best_per_event = {}
    for c in candidates:
        eid = c["event_id"]
        if eid not in best_per_event or c["prob"] > best_per_event[eid]["prob"]:
            best_per_event[eid] = c

    ranked_candidates = sorted(
        best_per_event.values(),
        key=lambda x: (x["prob"], x["books"], -x["odds"]),
        reverse=True,
    )
    print(f"-> Ranked candidates: {len(ranked_candidates)}", flush=True)

    if len(ranked_candidates) < 2:
        telegram_send(
            f"<b>🎯 PITOS v7.5 AKO2 | {session_type}</b>\n"
            f"📉 Za mało meczów spełniających filtry. Pass."
        )
        return

    valid_coupons = []

    for combo in combinations(ranked_candidates, 2):
        e_ids = [c["event_id"] for c in combo]
        if len(set(e_ids)) != 2:
            continue
        if any(c["odds"] < 1.20 for c in combo):
            continue
        total_odds = math.prod(c["odds"] for c in combo)
        if MIN_TOTAL_ODDS <= total_odds <= MAX_TOTAL_ODDS:
            sig_source = "|".join(sorted(f"{i['event_id']}:{i['selection']}" for i in combo))
            sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
            if sig in MEM_SIGNATURES:
                continue
            min_p_in_combo = min(i["prob"] for i in combo)
            combo_prob = math.prod(i["prob"] for i in combo)
            ev_score = combo_prob * total_odds
            valid_coupons.append({
                "combo": combo,
                "total_odds": total_odds,
                "signature": sig,
                "event_ids": e_ids,
                "min_prob": min_p_in_combo,
                "combo_prob": combo_prob,
                "score": (combo_prob, ev_score, min_p_in_combo),
            })

    valid_coupons.sort(key=lambda x: x["score"], reverse=True)

    sent = 0
    used_in_cycle = set()

    for coupon in valid_coupons:
        if sent >= MAX_COUPONS_PER_SESSION:
            break
        if any(eid in used_in_cycle for eid in coupon["event_ids"]):
            continue

        tot = coupon["total_odds"]
        min_p = coupon["min_prob"]
        c_prob = coupon["combo_prob"]
        units, stake_pln = get_stake(tot, min_p, c_prob)

        lines = [
            f"<b>🎯 PITOS v7.5 AKO2 ({session_type}) #{sent + 1}</b>",
            f"<i>Szac. trafność AKO2: {c_prob * 100:.1f}% | Sprawdź ofertę Betclic i składy.</i>",
            "",
        ]
        for idx, item in enumerate(coupon["combo"], 1):
            lines.append(
                f"{idx}. <b>{html.escape(item['event'])}</b>\n"
                f"   Typ: <i>{html.escape(item['selection'])}</i> | Śr. kurs: <b>{item['odds']:.2f}</b> ({item['prob']*100:.0f}%, {item['books']} buk.)\n"
                f"   {html.escape(item['sport'])} | {item['commence'].strftime('%d.%m %H:%M UTC')}"
            )

        lines.extend([
            "",
            f"📊 <b>Kurs łączny: {tot:.2f}</b>",
            f"💰 <b>Stawka: {units}j ({stake_pln} zł)</b>",
        ])
        msg = "\n".join(lines)
        c_id = coupon["signature"][:24]

        try:
            with get_db() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO coupons VALUES (?, ?, ?, 'sent', ?)",
                    (c_id, coupon["signature"], msg, now_str),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO bets_history VALUES (?, ?, ?, ?, ?, ?, 'pending', 0.0, ?)",
                    (c_id, session_type, round(tot, 2), units, stake_pln, round(c_prob, 4), now_str),
                )
                for item in coupon["combo"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO sent_events VALUES (?, ?, ?, ?, 'sent', ?)",
                        (item["event_id"], item["sport"], item["event"], c_id, now_str),
                    )

            telegram_send(msg)
            MEM_SIGNATURES.add(coupon["signature"])
            used_in_cycle.update(coupon["event_ids"])
            sent += 1
        except Exception as e:
            print(f"-> Send error: {e}", flush=True)

    if sent == 0:
        telegram_send(
            f"<b>🎯 PITOS v7.5 AKO2 | {session_type}</b>\n"
            f"📉 Brak kuponów AKO2 spełniających filtry. Pass."
        )


def scheduler_loop():
    print("-> Harmonogram v7.5 uruchomiony.", flush=True)
    while True:
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        minute = now_utc.minute

        if hour == 5 and minute == 30:
            run_session("DZIEŃ")
            time.sleep(70)
        elif hour == 21 and minute == 0:
            run_session("NOC")
            time.sleep(70)

        time.sleep(30)


def main():
    print("-> PITOS v7.5 starting with new API key...", flush=True)
    init_db()
    if API_KEY:
        run_session("START v7.5")
    else:
        print("-> Brak klucza API.", flush=True)

    threading.Thread(target=scheduler_loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
