#!/usr/bin/env python3
"""Value scanner for The Odds API + Telegram. Runs as a Railway Worker."""
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

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API = "https://api.telegram.org"

# --- TWARDZIZNA PRODUKCYJNA ---
ODDS_API_KEY = "99e1b40f31bdf20559cb664785d538b8"
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"

SCAN_INTERVAL_SECONDS = 900
ODDS_REGIONS = "eu"
MAX_SPORTS_PER_SCAN = 10
MAX_PICKS = 6
MIN_BOOKMAKERS = 4
MIN_EDGE_PCT = 1.0
MIN_EV_PCT = 1.0
MIN_MINUTES_TO_START = 20
MAX_ODDS_AGE_MINUTES = 90
DEDUP_HOURS = 48
BOT_TIMEZONE = "Europe/Warsaw"
STATE_FILE = Path("state.json")
REQUEST_TIMEOUT = 20

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("value-vip")
SESSION = requests.Session()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_state() -> dict[str, Any]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log.warning("Nie udało się odczytać stanu (%s); startuję z pustym.", exc)
    return {"sent": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = dict(params or {})
    query["apiKey"] = ODDS_API_KEY
    response = SESSION.get(f"{API_BASE}{path}", params=query, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"The Odds API HTTP {response.status_code}: {detail}")
    return response.json()


def get_active_sports() -> list[dict[str, Any]]:
    sports = api_get("/sports/")
    if not isinstance(sports, list):
        return []
    active = [s for s in sports if s.get("active") and s.get("key")]
    return active[:MAX_SPORTS_PER_SCAN]


def fetch_events(sport_key: str) -> list[dict[str, Any]]:
    data = api_get(f"/sports/{sport_key}/odds/", {
        "regions": ODDS_REGIONS,
        "markets": "h2h",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    return data if isinstance(data, list) else []


def bookmaker_market_prices(event: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bookmaker in event.get("bookmakers", []) or []:
        for market in bookmaker.get("markets", []) or []:
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes", []) or []
            prices = {
                str(o.get("name")): float(o.get("price", 0))
                for o in outcomes
                if o.get("name") and isinstance(o.get("price"), (int, float)) and float(o["price"]) > 1.01
            }
            if len(prices) < 2:
                continue
            inv = {name: 1.0 / price for name, price in prices.items()}
            total = sum(inv.values())
            if total <= 0:
                continue
            rows.append({
                "bookmaker": bookmaker.get("title") or bookmaker.get("key") or "Bookmaker",
                "last_update": market.get("last_update") or bookmaker.get("last_update"),
                "prices": prices,
                "probabilities": {name: value / total for name, value in inv.items()},
            })
            break
    return rows


def evaluate_event(event: dict[str, Any], sport: dict[str, Any]) -> dict[str, Any] | None:
    start = parse_dt(event.get("commence_time"))
    if not start:
        return None
    minutes_left = (start - now_utc()).total_seconds() / 60
    if minutes_left < MIN_MINUTES_TO_START:
        return None

    rows = bookmaker_market_prices(event)
    if len(rows) < MIN_BOOKMAKERS:
        return None

    newest_updates = [parse_dt(row.get("last_update")) for row in rows]
    valid_updates = [dt for dt in newest_updates if dt]
    if valid_updates:
        age_minutes = (now_utc() - max(valid_updates)).total_seconds() / 60
        if age_minutes > MAX_ODDS_AGE_MINUTES:
            return None

    names = set.intersection(*(set(row["prices"]) for row in rows))
    if len(names) < 2:
        return None

    candidates = []
    for outcome in names:
        best_row = max((row for row in rows if outcome in row["prices"]), key=lambda r: r["prices"][outcome])
        probabilities = [row["probabilities"][outcome] for row in rows if outcome in row["probabilities"]]
        if len(probabilities) < MIN_BOOKMAKERS:
            continue
        fair_probability = statistics.median(probabilities)
        best_odds = best_row["prices"][outcome]
        implied_probability = 1.0 / best_odds
        edge_pct = (fair_probability - implied_probability) * 100
        ev_pct = (fair_probability * best_odds - 1.0) * 100
        if edge_pct < MIN_EDGE_PCT or ev_pct < MIN_EV_PCT:
            continue
        candidates.append({
            "sport": sport.get("title") or sport.get("key"),
            "sport_key": sport.get("key"),
            "event_id": event.get("id"),
            "home": event.get("home_team", ""),
            "away": event.get("away_team", ""),
            "outcome": outcome,
            "odds": best_odds,
            "bookmaker": best_row["bookmaker"],
            "fair_probability": fair_probability,
            "edge_pct": edge_pct,
            "ev_pct": ev_pct,
            "commence_time": start.isoformat(),
            "minutes_left": minutes_left,
            "bookmakers_count": len(rows),
        })
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p["ev_pct"], p["edge_pct"]))


def selection_key(pick: dict[str, Any]) -> str:
    return f"{pick['event_id']}|{pick['outcome']}".lower()


def filter_new_picks(picks: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    sent = state.setdefault("sent", {})
    cutoff = now_utc().timestamp() - DEDUP_HOURS * 3600
    state["sent"] = {k: v for k, v in sent.items() if isinstance(v, (int, float)) and v >= cutoff}
    new = [p for p in picks if selection_key(p) not in state["sent"]]
    new.sort(key=lambda p: (p["ev_pct"], p["edge_pct"]), reverse=True)
    return new[:MAX_PICKS]


def fmt_time(value: str) -> str:
    dt = parse_dt(value)
    if not dt:
        return "godzina nieznana"
    try:
        return dt.astimezone(ZoneInfo(BOT_TIMEZONE)).strftime("%d.%m, %H:%M")
    except Exception:
        return dt.astimezone(timezone.utc).strftime("%d.%m, %H:%M UTC")


def fmt_pick(pick: dict[str, Any], number: int) -> str:
    matchup = f"{pick['home']} – {pick['away']}"
    return (
        f"<b>{number}. {escape(matchup)}</b>\n"
        f"Typ: <b>{escape(pick['outcome'])}</b> | Kurs: <b>{pick['odds']:.2f}</b>\n"
        f"Bukmacher: {escape(pick['bookmaker'])} | Liga: {escape(str(pick['sport']))}\n"
        f"Szac. fair: {pick['fair_probability'] * 100:.1f}% | Edge: {pick['edge_pct']:+.2f}% | EV: {pick['ev_pct']:+.2f}%\n"
        f"Start: {fmt_time(pick['commence_time'])} | Rynek: {pick['bookmakers_count']} bukmacherów"
    )


def make_akos(picks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    akos2, akos3 = [], []
    for size, target, threshold in ((2, akos2, 2.0), (3, akos3, 3.0)):
        for combo in itertools.combinations(picks, size):
            if len({p["event_id"] for p in combo}) != size:
                continue
