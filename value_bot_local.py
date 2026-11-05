
#!/usr/bin/env python3
# PROFIT BOT — VALUE SCANNER
# Railway Worker

from __future__ import annotations

import itertools
import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

# ==========================================
# KONFIGURACJA — DANE POBIERANE Z RAILWAY
# ==========================================

ODDS_API_KEY = os.getenv("2dd63be4b08f78956ef0d1b936b05882", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo", "").strip()
TELEGRAM_CHAT_ID = os.getenv("7221307802", "").strip()

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "900"))
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "eu")
MAX_SPORTS_PER_SCAN = int(os.getenv("MAX_SPORTS_PER_SCAN", "10"))
MAX_PICKS = min(6, int(os.getenv("MAX_PICKS", "6")))
MIN_BOOKMAKERS = int(os.getenv("MIN_BOOKMAKERS", "4"))
MIN_EDGE_PCT = float(os.getenv("MIN_EDGE_PCT", "1.0"))
MIN_EV_PCT = float(os.getenv("MIN_EV_PCT", "1.0"))
MIN_MINUTES_TO_START = int(os.getenv("MIN_MINUTES_TO_START", "20"))
MAX_ODDS_AGE_MINUTES = int(os.getenv("MAX_ODDS_AGE_MINUTES", "90"))
DEDUP_HOURS = int(os.getenv("DEDUP_HOURS", "48"))

BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Warsaw")
STATE_FILE = Path("state.json")

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("profit-bot")
session = requests.Session()


# ==========================================
# PODSTAWOWE FUNKCJE
# ==========================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def load_state():
    try:
        if STATE_FILE.exists():
            data = json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log.warning("Błąd odczytu state.json: %s", exc)

    return {"sent": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    tmp.replace(STATE_FILE)


# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(message):

    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    response = session.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram HTTP {response.status_code}: "
            f"{response.text[:250]}"
        )

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


# ==========================================
# ODDS API
# ==========================================

def api_get(path, params=None):

    query = dict(params or {})
    query["apiKey"] = ODDS_API_KEY

    response = session.get(
        f"{API_BASE}{path}",
        params=query,
        timeout=TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Odds API HTTP {response.status_code}: "
            f"{response.text[:250]}"
        )

    return response.json()


def get_active_sports():

    sports = api_get("/sports/")

    if not isinstance(sports, list):
        return []

    active = [
        sport for sport in sports
        if sport.get("active") and sport.get("key")
    ]

    return active[:MAX_SPORTS_PER_SCAN]


def fetch_events(sport_key):

    data = api_get(
        f"/sports/{sport_key}/odds/",
        {
            "regions": ODDS_REGIONS,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso"
        }
    )

    return data if isinstance(data, list) else []


# ==========================================
# ANALIZA KURSÓW
# ==========================================

def bookmaker_rows(event):

    rows = []

    for bookmaker in event.get("bookmakers", []) or []:

        for market in bookmaker.get("markets", []) or []:

            if market.get("key") != "h2h":
                continue

            prices = {
                str(outcome["name"]): float(outcome["price"])
                for outcome in market.get("outcomes", []) or []
                if outcome.get("name")
                and isinstance(outcome.get("price"), (int, float))
                and float(outcome["price"]) > 1.01
            }

            if len(prices) < 2:
                continue

            inverse = {
                name: 1 / price
                for name, price in prices.items()
            }

            total = sum(inverse.values())

            if total <= 0:
                continue

            rows.append({
                "bookmaker": (
                    bookmaker.get("title")
                    or bookmaker.get("key")
                    or "Bookmaker"
                ),
                "last_update": (
                    market.get("last_update")
                    or bookmaker.get("last_update")
                ),
                "prices": prices,
                "probabilities": {
                    name: value / total
                    for name, value in inverse.items()
                }
            })

            break

    return rows


def evaluate_event(event, sport):

    start = parse_dt(event.get("commence_time"))

    if not start:
        return None

    minutes_left = (
        start - now_utc()
    ).total_seconds() / 60

    if minutes_left < MIN_MINUTES_TO_START:
        return None

    rows = bookmaker_rows(event)

    if len(rows) < MIN_BOOKMAKERS:
        return None

    updates = [
        parse_dt(row.get("last_update"))
        for row in rows
    ]

    updates = [dt for dt in updates if dt]

    if updates:
        age = (
            now_utc() - max(updates)
        ).total_seconds() / 60

        if age > MAX_ODDS_AGE_MINUTES:
            return None

    common_names = set.intersection(
        *(set(row["prices"]) for row in rows)
    )

    candidates = []

    for outcome in common_names:

        probabilities = [
            row["probabilities"][outcome]
            for row in rows
            if outcome in row["probabilities"]
        ]

        if len(probabilities) < MIN_BOOKMAKERS:
            continue

        best_row = max(
            (
                row for row in rows
                if outcome in row["prices"]
            ),
            key=lambda row: row["prices"][outcome]
        )

        best_odds = best_row["prices"][outcome]

        fair_probability = statistics.median(
            probabilities
        )

        edge = (
            fair_probability - 1 / best_odds
        ) * 100

        ev = (
            fair_probability * best_odds - 1
        ) * 100

        if edge < MIN_EDGE_PCT or ev < MIN_EV_PCT:
            continue

        candidates.append({
            "sport": sport.get("title") or sport.get("key"),
            "event_id": event.get("id"),
            "home": event.get("home_team", ""),
            "away": event.get("away_team", ""),
            "outcome": outcome,
            "odds": best_odds,
            "bookmaker": best_row["bookmaker"],
            "fair_probability": fair_probability,
            "edge_pct": edge,
            "ev_pct": ev,
            "commence_time": start.isoformat(),
            "bookmakers_count": len(rows)
        })

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda pick: pick["ev_pct"]
    )


# ==========================================
# FORMATOWANIE I AKO
# ==========================================

def selection_key(pick):
    return f"{pick['event_id']}|{pick['outcome']}".lower()


def fmt_time(value):

    dt = parse_dt(value)

    if not dt:
        return "Nieznana"

    return dt.astimezone(
        ZoneInfo(BOT_TIMEZONE)
    ).strftime("%d.%m.%Y %H:%M")


def make_akos(picks):

    ako2 = []
    ako3 = []

    for size, target, threshold in (
        (2, ako2, 2.0),
        (3, ako3, 3.0)
    ):

        for combo in itertools.combinations(picks, size):

            if len({
                pick["event_id"] for pick in combo
            }) != size:
                continue

            odds = 1.0
            probability = 1.0

            for pick in combo:
                odds *= pick["odds"]
                probability *= pick["fair_probability"]

            if odds > threshold:

                target.append({
                    "picks": combo,
                    "odds": odds,
                    "ev_pct": (
                        probability * odds - 1
                    ) * 100
                })

        target.sort(
            key=lambda item: item["ev_pct"],
            reverse=True
        )

    return ako2[:2], ako3[:2]


def build_message(picks, scanned_sports):

    local_now = now_utc().astimezone(
        ZoneInfo(BOT_TIMEZONE)
    )

    lines = [
        "<b>PROFIT-BOT | VALUE SCANNER</b>",
        f"{local_now.strftime('%d.%m.%Y %H:%M')}",
        f"Przeskanowane ligi: {scanned_sports}",
        f"Nowe selekcje: <b>{len(picks)}/{MAX_PICKS}</b>",
        ""
    ]

    for number, pick in enumerate(picks, 1):

        matchup = escape(
            f"{pick['home']} – {pick['away']}"
        )

        lines.extend([
            f"<b>{number}. {matchup}</b>",
            f"Typ: <b>{escape(pick['outcome'])}</b>",
            f"Kurs: <b>{pick['odds']:.2f}</b>",
            f"Bukmacher: {escape(pick['bookmaker'])}",
            f"Liga: {escape(str(pick['sport']))}",
            (
                f"Fair: {pick['fair_probability'] * 100:.1f}%"
                f" | Edge: {pick['edge_pct']:+.2f}%"
                f" | EV: {pick['ev_pct']:+.2f}%"
            ),
            f"Start: {fmt_time(pick['commence_time'])}",
            f"Bukmacherów: {pick['bookmakers_count']}",
            ""
        ])

    ako2, ako3 = make_akos(picks)

    for title, combos in (
        ("AKO2", ako2),
        ("AKO3", ako3)
    ):

        lines.append(f"<b>{title}</b>")

        if not combos:
            lines.append(
                "Brak kombinacji spełniającej próg."
            )

        for combo in combos:

            legs = [
                (
                    f"{p['home']}–{p['away']}: "
                    f"{p['outcome']} @{p['odds']:.2f}"
                )
                for p in combo["picks"]
            ]

            lines.append(
                "• " + escape(" + ".join(legs))
                + f" | Kurs: <b>{combo['odds']:.2f}</b>"
                + f" | Modelowe EV: {combo['ev_pct']:+.2f}%"
            )

        lines.append("")

    lines.append(
        "<i>To skaner rynku, nie niezależny model predykcyjny. "
        "EV AKO zakłada niezależność zdarzeń. Kursy mogą się zmienić. "
        "Brak gwarancji zysku.</i>"
    )

    return "\n".join(lines)


# ==========================================
# SKANOWANIE
# ==========================================

def scan_once(state):

    sports = get_active_sports()

    log.info(
        "Aktywne ligi: %s. Skanuję maksymalnie %s.",
        len(sports),
        MAX_SPORTS_PER_SCAN
    )

    best_by_event = {}

    for sport in sports:

        try:
            events = fetch_events(sport["key"])

            log.info(
                "%s: %s wydarzeń",
                sport["key"],
                len(events)
            )

            for event in events:

                pick = evaluate_event(event, sport)

                if not pick:
                    continue

                event_id = pick["event_id"]

                if (
                    event_id not in best_by_event
                    or pick["ev_pct"]
                    > best_by_event[event_id]["ev_pct"]
                ):
                    best_by_event[event_id] = pick

        except Exception as exc:

            log.warning(
                "Pominięto ligę %s: %s",
                sport.get("key"),
                exc
            )

    sent = state.setdefault("sent", {})

    cutoff = (
        now_utc().timestamp()
        - DEDUP_HOURS * 3600
    )

    state["sent"] = {
        key: stamp
        for key, stamp in sent.items()
        if isinstance(stamp, (int, float))
        and stamp >= cutoff
    }

    new_picks = [
        pick
        for pick in best_by_event.values()
        if selection_key(pick) not in state["sent"]
    ]

    new_picks.sort(
        key=lambda pick: (
            pick["ev_pct"],
            pick["edge_pct"]
        ),
        reverse=True
    )

    new_picks = new_picks[:MAX_PICKS]

    if not new_picks:

        log.info(
            "Brak nowych selekcji spełniających filtry."
        )

        save_state(state)
        return

    message = build_message(
        new_picks,
        len(sports)
    )

    send_telegram(message)

    stamp = now_utc().timestamp()

    for pick in new_picks:
        state["sent"][selection_key(pick)] = stamp

    save_state(state)

    log.info(
        "Wysłano %s selekcji na Telegram.",
        len(new_picks)
    )


# ==========================================
# WALIDACJA I START
# ==========================================

def validate_config():

    missing = [
        name
        for name, value in (
            ("ODDS_API_KEY", ODDS_API_KEY),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
        )
        if not value
    ]

    if missing:

        raise SystemExit(
            "Brak wymaganych zmiennych środowiskowych: "
            + ", ".join(missing)
        )

    try:
        ZoneInfo(BOT_TIMEZONE)
    except Exception:
        raise SystemExit(
            f"Nieprawidłowa strefa czasowa: {BOT_TIMEZONE}"
        )


def main():

    validate_config()

    # WIADOMOŚĆ TESTOWA — NATYCHMIAST PO STARCIE
    send_telegram(
        "<b>PROFIT-BOT URUCHOMIONY! ✅</b>\n\n"
        "Zmienne środowiskowe działają.\n"
        "Rozpoczynam skanowanie rynku."
    )

    log.info(
        "Bot wystartował. Interwał: %s sekund.",
        SCAN_INTERVAL_SECONDS
    )

    state = load_state()

    while True:

        started = time.monotonic()

        try:
            scan_once(state)

        except Exception:
            log.exception(
                "Błąd cyklu skanowania."
            )

        elapsed = time.monotonic() - started

        time.sleep(
            max(5, SCAN_INTERVAL_SECONDS - elapsed)
        )


if __name__ == "__main__":
    main()
