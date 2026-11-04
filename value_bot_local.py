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

# --- TWARDZIZNA PRODUKCYJNA (BEZ BŁĘDÓW ŚRODOWISKOWYCH) ---
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
        best_row = max((row for row in rows if outcome in row["prices"]), key=lambda
