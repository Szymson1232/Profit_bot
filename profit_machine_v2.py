#!/usr/bin/env python3
# PITOS v8.3 Shark Edition — Basketball, Ice Hockey, Tennis | AKO3 / AKO2 + gated RISK SINGLE
# Sessions DZIEŃ 1 (08:00 PL) & NOC 1 (20:00 PL) | Strict no-repeat per match | Auto-settle + session PnL summary.

import os
import re
import sys
import socket
import threading
import time
import math
import sqlite3
import logging
import hashlib
import html
import json
import unicodedata
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
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 22\r\n\r\nPITOS v8.3 Shark Active"
                conn.sendall(response)
            except Exception:
                pass
            finally:
                conn.close()
    except Exception as e:
        print(f"-> Bind error: {e}", flush=True)


threading.Thread(target=instant_bind, daemon=True).start()

# ================= CONFIG =================
API_KEY = os.getenv("ODDS_API_KEY", "9028150dab46c5196701bbb6222529e2").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

SQLITE_PATH = os.getenv("SQLITE_PATH", "pitos_v83_shark.db")

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
FUTURE_BUFFER_MINUTES = int(os.getenv("FUTURE_BUFFER_MINUTES", "5"))
SETTLE_INTERVAL_SECONDS = int(os.getenv("SETTLE_INTERVAL_SECONDS", "900"))

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
session = requests.Session()
API_401_NOTIFIED = False


def get_db():
    parent = os.path.dirname(os.path.abspath(SQLITE_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        # Każde spotkanie może być użyte TYLKO RAZ (po event_id oraz po znormalizowanym match_key)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS used_events (
                event_id TEXT PRIMARY KEY,
                match_key TEXT,
                sport_key TEXT,
                event_name TEXT,
                session_label TEXT,
                coupon_id TEXT,
                created_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_used_match_key ON used_events(match_key)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions_log (
                session_label TEXT PRIMARY KEY,
                period_kind TEXT,
                session_num INTEGER,
                summary_sent INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets_history (
                coupon_id TEXT PRIMARY KEY,
                session_label TEXT,
                coupon_idx INTEGER,
                coupon_kind TEXT,
                legs_json TEXT,
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
    print("-> PITOS v8.3 Shark DB ready.", flush=True)


def normalize_team_name(name):
    txt = unicodedata.normalize("NFKD", (name or "").strip().lower())
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-z0-9]+", " ", txt).strip()
    return txt


def build_match_key(sport_key, home_team, away_team, commence_dt):
    p1 = normalize_team_name(home_team)
    p2 = normalize_team_name(away_team)
    pair = "|".join(sorted([p1, p2]))
    day_pl = commence_dt.astimezone(WARSAW_TZ).strftime("%Y-%m-%d")
    sport_group = sport_key.split("_")[0].lower() if sport_key else "sport"
    return f"{sport_group}:{pair}:{day_pl}"


def get_used_event_and_match_keys():
    with get_db() as conn:
        ev_rows = conn.execute("SELECT event_id FROM used_events").fetchall()
        mk_rows = conn.execute("SELECT match_key FROM used_events WHERE match_key IS NOT NULL AND match_key != ''").fetchall()
    used_events = {r[0] for r in ev_rows if r and r[0]}
    used_matches = {r[0] for r in mk_rows if r and r[0]}
    return used_events, used_matches


def next_session_label(period_kind):
    """Zwraca etykietę np. DZIEŃ 1, DZIEŃ 2, NOC 1, NOC 2 (nigdy DZIEŃ 0 / SESJA 0)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(session_num), 0) FROM sessions_log WHERE period_kind = ?",
            (period_kind,),
        ).fetchone()
        next_num = (int(row[0]) if row else 0) + 1
        label = f"{period_kind} {next_num}"
        conn.execute(
            "INSERT OR IGNORE INTO sessions_log VALUES (?, ?, ?, 0, ?)",
            (label, period_kind, next_num, datetime.now(timezone.utc).isoformat()),
        )
        return label


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


def api_get(path, params=None):
    global API_401_NOTIFIED
    if not API_KEY:
        print("-> Brak ustawionego klucza ODDS_API_KEY.", flush=True)
        return None
    p = dict(params or {})
    p["apiKey"] = API_KEY
    time.sleep(0.3)
    res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code == 401:
        print("-> CRITICAL: API Key out of credits or invalid (HTTP 401)!", flush=True)
        if not API_401_NOTIFIED:
            API_401_NOTIFIED = True
            try:
                telegram_send("<b>Alert API</b> — klucz The Odds API wyczerpał limit lub jest nieaktywny (HTTP 401).")
            except Exception:
                pass
        return None
    elif res.status_code == 422:
        return None
    elif res.status_code == 429:
        time.sleep(10)
        res = session.get(f"{API_BASE}{path}", params=p, timeout=20)
    if res.status_code != 200:
        return None
    return res.json()


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


def balance_sports_candidates(candidates_list, max_total=30):
    """Zapewnia udział koszykówki, hokeja i tenisa bez całkowitego zdominowania przez jeden sport."""
    buckets = {}
    for c in candidates_list:
        grp = c["sport"].split("_")[0].lower() if c.get("sport") else "other"
        buckets.setdefault(grp, []).append(c)

    for grp in buckets:
        buckets[grp].sort(key=lambda x: (x["edge"], x["prob"], x["books"]), reverse=True)

    ordered = []
    group_names = sorted(buckets.keys())
    idx = 0
    while len(ordered) < max_total:
        added_in_round = False
        for grp in group_names:
            if idx < len(buckets[grp]):
                ordered.append(buckets[grp][idx])
                added_in_round = True
                if len(ordered) >= max_total:
                    break
        if not added_in_round:
            break
        idx += 1
    return ordered


def run_session(period_kind):
    session_label = next_session_label(period_kind)
    now_pl = datetime.now(WARSAW_TZ)
    print(f"-> PITOS v8.3 scan [{session_label}] at {now_pl.strftime('%Y-%m-%d %H:%M %Z')}...", flush=True)

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

    used_events_ever, used_matches_ever = get_used_event_and_match_keys()
    candidates = []
    now_utc = datetime.now(timezone.utc)
    earliest_start = now_utc + timedelta(minutes=FUTURE_BUFFER_MINUTES)
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
            if not ev_id or ev_id in used_events_ever:
                continue

            commence = parse_time(ev.get("commence_time"))
            # Sztywne odcięcie przeszłości i meczów już rozpoczętych
            if not commence or commence <= earliest_start or commence > window_end:
                continue

            home, away = ev.get("home_team", "Unknown"), ev.get("away_team", "Unknown")
            m_key = build_match_key(sp, home, away, commence)
            if m_key in used_matches_ever:
                continue

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
                        "match_key": m_key,
                        "event": f"{home} vs {away}",
                        "home_team": home,
                        "away_team": away,
                        "selection": name,
                        "odds": odds,
                        "prob": prob,
                        "edge": edge,
                        "books": bk_n,
                        "sport": sp,
                        "commence": commence,
                    })

    best_per_match = {}
    for c in candidates:
        mk = c["match_key"]
        score_c = c["prob"] * (1.0 + max(0.0, c["edge"]))
        if mk not in best_per_match:
            best_per_match[mk] = c
        else:
            prev = best_per_match[mk]
            score_prev = prev["prob"] * (1.0 + max(0.0, prev["edge"]))
            if score_c > score_prev:
                best_per_match[mk] = c

    ranked_candidates = balance_sports_candidates(list(best_per_match.values()), max_total=30)
    print(f"-> Balanced fresh candidates (no repeats): {len(ranked_candidates)}", flush=True)

    ako_coupons = []
    for r in [2, 3]:
        if len(ranked_candidates) < r:
            continue
        min_tot = MIN_AKO2_ODDS if r == 2 else MIN_AKO3_ODDS
        max_tot = MAX_AKO2_ODDS if r == 2 else MAX_AKO3_ODDS

        for combo in combinations(ranked_candidates, r):
            e_ids = [c["event_id"] for c in combo]
            m_keys = [c["match_key"] for c in combo]
            if len(set(e_ids)) != r or len(set(m_keys)) != r:
                continue
            if any(eid in used_events_ever for eid in e_ids) or any(mk in used_matches_ever for mk in m_keys):
                continue
            if any(c["odds"] < 1.20 for c in combo):
                continue
            total_odds = math.prod(c["odds"] for c in combo)
            if min_tot <= total_odds <= max_tot:
                sig_source = f"AKO{r}|" + "|".join(sorted(f"{i['match_key']}:{i['selection']}" for i in combo))
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
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
                    "match_keys": m_keys,
                    "min_prob": min_p_in_combo,
                    "combo_prob": combo_prob,
                    "combo_edge": combo_edge,
                    "units": units,
                    "stake_pln": stake_pln,
                    "score": (combo_edge, combo_prob, -r),
                })

    ako_coupons.sort(key=lambda x: x["score"], reverse=True)

    selected_coupons = []
    used_events_run = set(used_events_ever)
    used_matches_run = set(used_matches_ever)
    risk_used = 0

    for c in ako_coupons:
        if len(selected_coupons) >= MAX_COUPONS_PER_SESSION:
            break
        if any(eid in used_events_run for eid in c["event_ids"]) or any(mk in used_matches_run for mk in c["match_keys"]):
            continue
        selected_coupons.append(c)
        used_events_run.update(c["event_ids"])
        used_matches_run.update(c["match_keys"])

    if len(selected_coupons) < MAX_COUPONS_PER_SESSION and risk_used < MAX_RISK_SINGLES_PER_SESSION:
        for cand in ranked_candidates:
            if cand["event_id"] in used_events_run or cand["match_key"] in used_matches_run:
                continue
            if (
                RISK_SINGLE_MIN_ODDS <= cand["odds"] <= RISK_SINGLE_MAX_ODDS
                and cand["prob"] >= RISK_SINGLE_MIN_PROB
                and cand["edge"] >= RISK_SINGLE_MIN_EDGE
            ):
                sig_source = f"RISK_SINGLE|{cand['match_key']}:{cand['selection']}"
                sig = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()
                units, stake_pln = get_risk_single_stake(cand["odds"], cand["prob"], cand["edge"])
                selected_coupons.append({
                    "legs_count": 1,
                    "kind": "RISK_SINGLE",
                    "combo": [cand],
                    "total_odds": cand["odds"],
                    "signature": sig,
                    "event_ids": [cand["event_id"]],
                    "match_keys": [cand["match_key"]],
                    "min_prob": cand["prob"],
                    "combo_prob": cand["prob"],
                    "combo_edge": cand["edge"],
                    "units": units,
                    "stake_pln": stake_pln,
                    "score": (cand["edge"], cand["prob"], 1),
                })
                used_events_run.add(cand["event_id"])
                used_matches_run.add(cand["match_key"])
                risk_used += 1
                break

    # Minimum 3 kupony w sesji, w przeciwnym razie PASS
    if len(selected_coupons) < MIN_COUPONS_PER_SESSION:
        print(f"-> [{session_label}] Found only {len(selected_coupons)} coupons (min {MIN_COUPONS_PER_SESSION}). Sending PASS.", flush=True)
        with get_db() as conn:
            conn.execute("UPDATE sessions_log SET summary_sent = 1 WHERE session_label = ?", (session_label,))
        telegram_send(f"<b>{html.escape(session_label)}</b> — PASS (brak min. 3 czystych układów).")
        return

    for idx_c, coupon in enumerate(selected_coupons[:MAX_COUPONS_PER_SESSION], 1):
        tot = coupon["total_odds"]
        c_prob = coupon["combo_prob"]
        c_edge = coupon["combo_edge"]
        units = coupon["units"]
        stake_pln = coupon["stake_pln"]
        tag = "RISK SINGLE" if coupon["kind"] == "RISK_SINGLE" else coupon["kind"]
        c_id = f"{session_label}_{idx_c}_{coupon['signature'][:12]}"

        legs_payload = []
        lines = [f"<b>{html.escape(session_label)} | #{idx_c} | {html.escape(tag)}</b>", ""]
        for leg_i, item in enumerate(coupon["combo"], 1):
            start_pl = item["commence"].astimezone(WARSAW_TZ).strftime("%d.%m %H:%M")
            legs_payload.append({
                "event_id": item["event_id"],
                "match_key": item["match_key"],
                "sport": item["sport"],
                "event": item["event"],
                "home_team": item["home_team"],
                "away_team": item["away_team"],
                "selection": item["selection"],
                "odds": round(item["odds"], 2),
            })
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

        try:
            with get_db() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO bets_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0.0, ?)",
                    (
                        c_id,
                        session_label,
                        idx_c,
                        coupon["kind"],
                        json.dumps(legs_payload, ensure_ascii=False),
                        round(tot, 2),
                        units,
                        stake_pln,
                        round(c_prob, 4),
                        round(c_edge, 4),
                        now_str,
                    ),
                )
                for item in coupon["combo"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO used_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (item["event_id"], item["match_key"], item["sport"], item["event"], session_label, c_id, now_str),
                    )

            telegram_send(msg)
        except Exception as e:
            print(f"-> Send error: {e}", flush=True)


def determine_winner_from_score(ev_obj):
    """Zwraca nazwę zwycięzcy, jeśli mecz jest oznaczony jako completed i ma dwa różne wyniki."""
    if not ev_obj.get("completed"):
        return None
    scores = ev_obj.get("scores")
    if not isinstance(scores, list) or len(scores) < 2:
        return None
    parsed = []
    for sc in scores:
        name = (sc.get("name") or "").strip()
        val_str = sc.get("score")
        if not name or val_str is None:
            return None
        try:
            pts = float(val_str)
        except Exception:
            return None
        parsed.append((name, pts))
    if len(parsed) < 2 or parsed[0][1] == parsed[1][1]:
        return None
    return max(parsed, key=lambda x: x[1])[0]


def settle_pending_bets_and_report():
    """Sprawdza zakończone mecze, rozlicza kupony i wysyła podsumowanie sesji po jej pełnym rozliczeniu."""
    with get_db() as conn:
        pending_rows = conn.execute(
            "SELECT coupon_id, session_label, coupon_idx, coupon_kind, legs_json, total_odds, stake_pln "
            "FROM bets_history WHERE result = 'pending'"
        ).fetchall()

    if not pending_rows:
        return

    needed_sports = set()
    parsed_bets = []
    for r in pending_rows:
        c_id, s_label, c_idx, c_kind, legs_json, tot_odds, stake_pln = r
        try:
            legs = json.loads(legs_json)
        except Exception:
            continue
        for lg in legs:
            if lg.get("sport"):
                needed_sports.add(lg["sport"])
        parsed_bets.append({
            "coupon_id": c_id,
            "session_label": s_label,
            "coupon_idx": c_idx,
            "coupon_kind": c_kind,
            "legs": legs,
            "total_odds": float(tot_odds),
            "stake_pln": int(stake_pln),
        })

    winners_by_event = {}
    for sp in needed_sports:
        scores_data = api_get(f"/sports/{sp}/scores/", {"daysFrom": "2"})
        if not isinstance(scores_data, list):
            continue
        for ev_obj in scores_data:
            ev_id = ev_obj.get("id")
            if not ev_id:
                continue
            win_name = determine_winner_from_score(ev_obj)
            if win_name:
                winners_by_event[ev_id] = win_name

    for b in parsed_bets:
        legs = b["legs"]
        if not legs:
            continue
        all_known = True
        all_won = True
        for lg in legs:
            eid = lg.get("event_id")
            sel = (lg.get("selection") or "").strip()
            if eid not in winners_by_event:
                all_known = False
                break
            if winners_by_event[eid].lower() != sel.lower():
                all_won = False

        if not all_known:
            continue

        stake = b["stake_pln"]
        tot_odds = b["total_odds"]
        if all_won:
            res_tag = "won"
            pnl = round((stake * tot_odds) - stake, 2)
        else:
            res_tag = "lost"
            pnl = round(-float(stake), 2)

        with get_db() as conn:
            conn.execute(
                "UPDATE bets_history SET result = ?, pnl_pln = ? WHERE coupon_id = ?",
                (res_tag, pnl, b["coupon_id"]),
            )

    with get_db() as conn:
        cand_sessions = conn.execute(
            "SELECT session_label FROM sessions_log WHERE summary_sent = 0"
        ).fetchall()

    for (s_label,) in cand_sessions:
        with get_db() as conn:
            rem = conn.execute(
                "SELECT COUNT(*) FROM bets_history WHERE session_label = ? AND result = 'pending'",
                (s_label,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT coupon_idx, coupon_kind, total_odds, stake_pln, result, pnl_pln "
                "FROM bets_history WHERE session_label = ? ORDER BY coupon_idx ASC",
                (s_label,),
            ).fetchall()

        if not rows or rem > 0:
            continue

        total_pnl = sum(float(x[5]) for x in rows)
        lines = [f"<b>Podsumowanie sesji: {html.escape(s_label)}</b>", ""]
        for c_idx, c_kind, tot_odds, stake_pln, res_tag, pnl in rows:
            icon = "✅" if res_tag == "won" else "❌"
            sign = "+" if float(pnl) >= 0 else ""
            lines.append(
                f"{icon} #{c_idx} ({html.escape(c_kind)}) @{tot_odds:.2f} | {stake_pln} zł -> <b>{sign}{float(pnl):.0f} zł</b>"
            )

        tot_sign = "+" if total_pnl >= 0 else ""
        lines.extend([
            "",
            f"Bilans sesji ({html.escape(s_label)}): <b>{tot_sign}{total_pnl:.0f} zł</b>",
        ])

        try:
            telegram_send("\n".join(lines))
            with get_db() as conn:
                conn.execute(
                    "UPDATE sessions_log SET summary_sent = 1 WHERE session_label = ?",
                    (s_label,),
                )
        except Exception as e:
            print(f"-> Session summary error [{s_label}]: {e}", flush=True)


def scheduler_loop():
    print("-> Harmonogram v8.3 uruchomiony (08:00 DZIEŃ PL / 20:00 NOC PL + auto-rozliczanie).", flush=True)
    last_run_slot = None
    last_settle_ts = 0.0

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

        now_ts = time.time()
        if now_ts - last_settle_ts >= SETTLE_INTERVAL_SECONDS:
            last_settle_ts = now_ts
            try:
                settle_pending_bets_and_report()
            except Exception as e:
                print(f"-> Settle loop error: {e}", flush=True)

        time.sleep(20)


def main():
    print("-> PITOS v8.3 Shark Edition starting...", flush=True)
    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
