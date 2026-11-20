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
    if (
        legs_count == 2
        and min_prob_in_combo >= 0.55
        and combo_prob >= 0.30
        and combo_edge >= 0.045
        and total_odds <= 2.50
    ):
        return 4, 4 * UNIT_PLN  # 100 zł
    elif (
        legs_count == 3
        and min_prob_in_combo >= 0.48
        and combo_prob >= 0.13
        and combo_edge >= 0.05
        and total_odds <= 4.00
    ):
        return 3, 3 * UNIT_PLN  # 75 zł
    elif combo_prob >= 0.18 and combo_edge >= 0.02:
        return 2, 2 * UNIT_PLN  # 50 zł
    else:
        return 1, 1 * UNIT_PLN  # 25 zł


def get_risk_single_stake(odds, prob, edge):
    """Stawka dla kuponu RISK single: 25 zł (1j) lub 50 zł (2j)."""
    if prob >= 0.56 and edge >= 0.09 and odds <= 2.00:
        return 2, 2 * UNIT_PLN  # 50 zł
    return 1, 1 * UNIT_PLN  # 25 zł


def market_pulse_line(prob, edge, odds):
    """Krótki, konkretny komentarz analityczny do kuponu."""
    if edge >= 0.07 and prob >= 0.52:
        return f"Radar: wyraźny rozjazd z konsensusem (+{edge*100:.1f}%), podbicie pod próg zaufania."
    if odds >= 2.80:
        return f"Radar: wyższa dźwignia kursowa ({odds:.2f}) — pilnuj stawki i weryfikuj absencje."
    return f"Radar: stabilny rozkład w koszu/hokeju/tenisie (szansa modelu {prob*100:.1f}%)."


def run_session(session_type):
    now_pl = datetime.now(WARSAW_TZ)
    print(f"-> PITOS v8.1 scan [{session_type}] at {now_pl.strftime('%Y-%m-%d %H:%M %Z')}...", flush=True)

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

    print(f"-> Active sports ({len(sports)}): {sports}", flush=True)

    candidates = []
    now_utc = datetime.now(timezone.utc)
    window_end = now_utc + timedelta(hours=LOOKAHEAD_HOURS)

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
            if not commence or commence < now_utc or commence > window_end:
                continue

            home, away = ev.get("home_team", "Unknown"), ev.get("away_team", "Unknown")
            bookmakers = ev.get("bookmakers", [])
            if not bookmakers:
                continue

            consensus = compute_consensus_with_edge(bookmakers)
            if not consensus:
                continue

            for name, odds, prob, edge, bk_n in consensus:
                if (
                    MIN_SINGLE_ODDS <= odds <= MAX_SINGLE_ODDS
                    and prob >= MIN_PROB
                    and edge >= MIN_EDGE
                ):
                    candidates.append({
                        "event_id": ev_id,
                        "event": f"{home} vs {away}",
                        "selection": name,
                        "odds": odds,
                        "prob": prob,
                        "edge": edge,
                        "books": bk_n,
                        "sport": sp,
                        "commence": commence,
                    })

    best_per_event = {}
    for c in candidates:
        eid = c["event_id"]
        score_c = c["prob"] * (1.0 + max(0.0, c["edge"]))
        if eid not in best_per_event:
            best_per_event[eid] = c
        else:
            prev = best_per_event[eid]
            score_prev = prev["prob"] * (1.0 + max(0.0, prev["edge"]))
            if score_c > score_prev:
                best_per_event[eid] = c

    ranked_candidates = sorted(
        best_per_event.values(),
        key=lambda x: (x["edge"], x["prob"], x["books"]),
        reverse=True,
    )
    print(f"-> Qualified candidates: {len(ranked_candidates)}", flush=True)

    ako_coupons = []
    for r in [2, 3]:
        if len(ranked_candidates) < r:
            continue
        min_tot = MIN_AKO2_ODDS if r == 2 else MIN_AKO3_ODDS
        max_tot = MAX_AKO2_ODDS if r == 2 else MAX_AKO3_ODDS

        for combo in combinations(ranked_candidates, r):
            e_ids = [c["event_id"] for c in combo]
            if len(set(e_ids)) != r:
                continue
            if any(c["odds"] < 1.20 for c in combo):
                continue
            total_odds = math.prod(c["odds"] for c in combo)
            if min_tot <= total_odds <= max_tot:
                sig_source = f"AKO{r}|" + "|".join(sorted(f"{i['event_id']}:{i['selection']}" for i in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
                if sig in MEM_SIGNATURES:
                    continue
                min_p_in_combo = min(i["prob"] for i in combo)
                combo_prob = math.prod(i["prob"] for i in combo)
                combo_edge = (total_odds * combo_prob) - 1.0
                if combo_edge < MIN_EDGE:
                    continue
                units, stake_pln = get_ako_stake(r, total_odds, min_p_in_combo, combo_prob, combo_edge)
                ako_coupons.append({
                    "legs_count": r,
                    "kind": f"AKO{r}",
                    "combo": combo,
                    "total_odds": total_odds,
                    "signature": sig,
                    "event_ids": e_ids,
                    "min_prob": min_p_in_combo,
                    "combo_prob": combo_prob,
                    "combo_edge": combo_edge,
                    "units": units,
                    "stake_pln": stake_pln,
                    "score": (combo_edge, combo_prob, -r),
                })

    ako_coupons.sort(key=lambda x: x["score"], reverse=True)

    # Składamy paczkę z zachowaniem limitu 3..10 kuponów i maks. 1 RISK single
    selected_coupons = []
    used_events = set()
    risk_used = 0

    for c in ako_coupons:
        if len(selected_coupons) >= MAX_COUPONS_PER_SESSION:
            break
        if any(eid in used_events for eid in c["event_ids"]):
            continue
        selected_coupons.append(c)
        used_events.update(c["event_ids"])

    # Jeśli w sesji jest miejsce (poniżej 10 kuponów) i mamy mocny sygnał na pojedynczy typ RISK
    if len(selected_coupons) < MAX_COUPONS_PER_SESSION and risk_used < MAX_RISK_SINGLES_PER_SESSION:
        for cand in ranked_candidates:
            if cand["event_id"] in used_events:
                continue
            if (
                RISK_SINGLE_MIN_ODDS <= cand["odds"] <= RISK_SINGLE_MAX_ODDS
                and cand["prob"] >= RISK_SINGLE_MIN_PROB
                and cand["edge"] >= RISK_SINGLE_MIN_EDGE
            ):
                sig_source = f"RISK_SINGLE|{cand['event_id']}:{cand['selection']}"
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
                if sig in MEM_SIGNATURES:
                    continue
                units, stake_pln = get_risk_single_stake(cand["odds"], cand["prob"], cand["edge"])
                selected_coupons.append({
                    "legs_count": 1,
                    "kind": "RISK_SINGLE",
                    "combo": [cand],
                    "total_odds": cand["odds"],
                    "signature": sig,
                    "event_ids": [cand["event_id"]],
                    "min_prob": cand["prob"],
                    "combo_prob": cand["prob"],
                    "combo_edge": cand["edge"],
                    "units": units,
                    "stake_pln": stake_pln,
                    "score": (cand["edge"], cand["prob"], 1),
                })
                used_events.add(cand["event_id"])
                risk_used += 1
                break

    # Twarda reguła: minimum 3 kupony w sesji, w przeciwnym razie PASS
    if len(selected_coupons) < MIN_COUPONS_PER_SESSION:
        print(f"-> Found only {len(selected_coupons)} coupons (min {MIN_COUPONS_PER_SESSION}). Sending PASS.", flush=True)
        telegram_send("PASS — brak wystarczającej liczby solidnych układów w koszu, hokeju i tenisie.")
        return

    for idx_c, coupon in enumerate(selected_coupons[:MAX_COUPONS_PER_SESSION], 1):
        tot = coupon["total_odds"]
        c_prob = coupon["combo_prob"]
        c_edge = coupon["combo_edge"]
        units = coupon["units"]
        stake_pln = coupon["stake_pln"]
        tag = "RISK SINGLE" if coupon["kind"] == "RISK_SINGLE" else coupon["kind"]

        lines = [f"<b>#{idx_c} | {html.escape(tag)}</b>", ""]
        for leg_i, item in enumerate(coupon["combo"], 1):
            start_pl = item["commence"].astimezone(WARSAW_TZ).strftime("%d.%m %H:%M")
            lines.append(
                f"{leg_i}. <b>{html.escape(item['event'])}</b> ({html.escape(item['sport'])}, {start_pl})\n"
                f"Typ: <b>{html.escape(item['selection'])}</b> @ <b>{item['odds']:.2f}</b> "
                f"[szansa {item['prob']*100:.0f}%, edge +{item['edge']*100:.1f}%]"
            )

        lines.extend([
            "",
            f"Kurs łączny: <b>{tot:.2f}</b> | Stawka: <b>{units}j ({stake_pln} zł)</b>",
            f"<i>{html.escape(market_pulse_line(c_prob, c_edge, tot))}</i>",
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
                    "INSERT OR IGNORE INTO bets_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0.0, ?)",
                    (c_id, session_type, coupon["kind"], round(tot, 2), units, stake_pln, round(c_prob, 4), round(c_edge, 4), now_str),
                )
                for item in coupon["combo"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO sent_events VALUES (?, ?, ?, ?, 'sent', ?)",
                        (item["event_id"], item["sport"], item["event"], c_id, now_str),
                    )

            telegram_send(msg)
            MEM_SIGNATURES.add(coupon["signature"])
        except Exception as e:
            print(f"-> Send error: {e}", flush=True)


def scheduler_loop():
    print("-> Harmonogram v8.1 uruchomiony (08:00 DZIEŃ PL / 20:00 NOC PL).", flush=True)
    last_run_slot = None
    while True:
        now_pl = datetime.now(WARSAW_TZ)
        slot_key = now_pl.strftime("%Y-%m-%d %H:%M")

        if now_pl.hour == 8 and now_pl.minute == 0 and last_run_slot != slot_key:
            last_run_slot = slot_key
            run_session("DZIEŃ")
            time.sleep(35)
        elif now_pl.hour == 20 and now_pl.minute == 0 and last_run_slot != slot_key:
            last_run_slot = slot_key
            run_session("NOC")
            time.sleep(35)

        time.sleep(20)


def main():
    print("-> PITOS v8.1 Shark Edition starting...", flush=True)
    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
