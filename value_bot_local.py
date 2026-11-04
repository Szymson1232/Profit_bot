#!/usr/bin/env python3
"""Value scanner for The Odds API + Telegram. Runs as a Railway Worker."""
from __future__ import annotations

import itertools
import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API = "https://api.telegram.org"

ODDS_API_KEY = "99e1b40f31bdf20559cb664785d538b8"  # <--- Pamiętaj o wklejeniu swojego aktualnego klucza API z kredytami!
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"

VIP_HEADER_GIF = "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExM3ptZ25rdmZtd2VpcXQ1bjF2bm1ocDZpcWw2a3U4ZnY3NHRkZHBhayZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/Lp8kVSwaSuwX9oVusW/giphy.gif"

SCAN_INTERVAL_SECONDS = 7200   # Co 2 godziny
ODDS_REGIONS = "eu,us"
MAX_SPORTS_PER_SCAN = 12       
MAX_PICKS = 3                  
MAX_DAYS_AHEAD = 1             # Tylko dzisiejsze i jutrzejsze mecze
MIN_BOOKMAKERS = 2
MIN_EDGE_PCT = 0.8
MIN_EV_PCT = 0.8
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
                log.info("Wczytano stan z pliku: %s zapisanych selekcji.", len(data.get("sent", {})))
                return data
    except Exception as exc:
        log.warning("Nie udało się odczytać stanu (%s); startuję z pustym.", exc)
    return {"sent": {}}


def save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:
        log.error("Błąd zapisu stanu do pliku: %s", exc)


def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = dict(params or {})
    query["apiKey"] = ODDS_API_KEY
    response = SESSION.get(f"{API_BASE}{path}", params=query, timeout=REQUEST_TIMEOUT)
    if response.status_code == 429:
        log.warning("Przekroczono limit zapytań API (429). Czekam 10 sekund...")
        time.sleep(10)
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
    priority_keys = ["basketball_nba", "icehockey_nhl", "soccer_usa_mls", "soccer_germany_bundesliga", "esports_counter_strike"]
    sorted_sports = sorted(active, key=lambda x: 0 if x.get("key") in priority_keys else 1)
    return sorted_sports[:MAX_SPORTS_PER_SCAN]


def fetch_events(sport_key: str) -> list[dict[str, Any]]:
    data = api_get(f"/sports/{sport_key}/odds/", {
        "regions": ODDS_REGIONS,
        "markets": "h2h",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    return data if isinstance(data, list) else []


def get_sport_emoji(sport_name: str) -> str:
    s = sport_name.lower()
    if "basketball" in s or "nba" in s or "euroleague" in s:
        return "🏀"
    if "ice hockey" in s or "nhl" in s:
        return "🏒"
    if "soccer" in s or "mls" in s or "league" in s or "bundesliga" in s:
        return "⚽"
    if "american" in s or "nfl" in s or "ncaaf" in s:
        return "🏈"
    if "esports" in s or "counter-strike" in s:
        return "🎮"
    if "tennis" in s:
        return "🎾"
    return "🎯"


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

    now = now_utc()
    max_allowed_time = now + timedelta(days=MAX_DAYS_AHEAD)
    if start < now or start > max_allowed_time:
        return None

    rows = bookmaker_market_prices(event)
    if len(rows) < MIN_BOOKMAKERS:
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
        
        if not (1.60 <= best_odds <= 3.50):
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
            "bookmakers_count": len(rows),
        })
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p["ev_pct"], p["edge_pct"]))


def selection_key(pick: dict[str, Any]) -> str:
    # Unikalny klucz oparty na ID wydarzenia oraz typie, żeby uniknąć duplikatów
    return f"{pick['event_id']}|{pick['outcome']}".lower()


def filter_new_picks(picks: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    sent = state.setdefault("sent", {})
    cutoff = now_utc().timestamp() - (48 * 3600)  # Pamięć 48h
    # Czyszczenie przestarzałych wpisów
    state["sent"] = {k: v for k, v in sent.items() if isinstance(v, (int, float)) and v >= cutoff}
    
    # Odrzucamy to, co już zostało wysłane
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
    matchup = f"{pick['home']} vs {pick['away']}"
    sport_name = str(pick['sport'])
    emoji = get_sport_emoji(sport_name)
    stake_units = "2j" if pick['odds'] < 2.05 else "1j"
    stake_pln = 50 if pick['odds'] < 2.05 else 25
    return (
        f"<b>{number}. {emoji} [{escape(sport_name)}]</b>\n"
        f"🏟 <code>{escape(matchup)}</code>\n"
        f"💡 Typ: <b>{escape(pick['outcome'])}</b> @ <code>{pick['odds']:.2f}</code>\n"
        f"💸 Stawka: <b>{stake_units} ({stake_pln} zł)</b> | Buk: <i>{escape(pick['bookmaker'])}</i>\n"
        f"📊 Model EV: <code>{pick['ev_pct']:+.2f}%</code> | ⏱ Start: {fmt_time(pick['commence_time'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )


def make_akos(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    akos2 = []
    for combo in itertools.combinations(picks, 2):
        if len({p["event_id"] for p in combo}) != 2:
            continue
        total_odds = combo[0]["odds"] * combo[1]["odds"]
        joint_prob = combo[0]["fair_probability"] * combo[1]["fair_probability"]
        if 2.0 < total_odds < 6.0:
            akos2.append({"picks": combo, "odds": total_odds, "ev_pct": (joint_prob * total_odds - 1) * 100})
    akos2.sort(key=lambda x: (x["ev_pct"], x["odds"]), reverse=True)
    return akos2[:1]


def send_telegram_broadcast(message: str) -> None:
    gif_url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendAnimation"
    try:
        SESSION.post(gif_url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "animation": VIP_HEADER_GIF,
            "caption": "👑 <b>PROFIT | ELITE SYNDICATE DIGEST</b> 🚀",
            "parse_mode": "HTML"
        }, timeout=15)
    except Exception as e:
        log.warning("Nie udało się wysłać GIF-a: %s", e)

    msg_url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = SESSION.post(msg_url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:300]}")


def build_message(picks: list[dict[str, Any]], scanned_sports: int) -> str:
    local_now = now_utc().astimezone(ZoneInfo(BOT_TIMEZONE))
    lines = [
        f"📅 <b>Raport rynkowy (Co 2h):</b> {local_now.strftime('%d.%m.%Y | %H:%M')} (Warszawa)",
        f"🔍 Wyselekcjonowane mecze (Dziś / Jutra): <b>{len(picks)} top typy</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    lines.extend(fmt_pick(p, i + 1) for i, p in enumerate(picks))

    ako2 = make_akos(picks)
    if ako2:
        lines += ["", "🔥 <b>TOP AKO NA DZIŚ</b>"]
        for combo in ako2:
            labels = [f"{p['home']} vs {p['away']}: {p['outcome']} @{p['odds']:.2f}" for p in combo["picks"]]
            lines.append(f"• <b>AKO2:</b> " + " + ".join(escape(x) for x in labels) + f" | Kurs: <b>{combo['odds']:.2f}</b>")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💎 <b>Zarządzanie bankrollem:</b>",
        "• <code>1j = 25 zł</code> | <code>2j = 50 zł</code>",
        "<i>Profit Syndicate – gramy bieżącą ofertę co 2 godziny z matematyczną przewagą.</i>",
    ]
    return "\n".join(lines)


def scan_once(state: dict[str, Any]) -> None:
    sports = get_active_sports()
    log.info("Skanuję aktywne ligi (%s) z filtrem na najbliższe 24-48h...", len(sports))
    evaluated: list[dict[str, Any]] = []
    
    for sport in sports:
        try:
            events = fetch_events(sport["key"])
            for event in events:
                pick = evaluate_event(event, sport)
                if pick:
                    evaluated.append(pick)
            time.sleep(1.0)
        except Exception as exc:
            log.warning("Pominięto ligę %s: %s", sport.get("key"), exc)

    best_by_event: dict[str, dict[str, Any]] = {}
    for pick in evaluated:
        event_id = pick["event_id"]
        if event_id not in best_by_event or pick["ev_pct"] > best_by_event[event_id]["ev_pct"]:
            best_by_event[event_id] = pick
            
    new_picks = filter_new_picks(list(best_by_event.values()), state)

    if not new_picks:
        log.info("Brak nowych unikalnych selekcji na dziś/jutro w tej turze.")
        save_state(state)
        return

    message = build_message(new_picks, len(sports))
    send_telegram_broadcast(message)
    
    # Natychmiast zapisujemy stan do pliku, żeby zablokować duplikaty
    stamp = now_utc().timestamp()
    for pick in new_picks:
        state.setdefault("sent", {})[selection_key(pick)] = stamp
    save_state(state)
    
    log.info("Wysłano bieżący pakiet VIP na Telegram (%s nowych typów).", len(new_picks))


def main() -> None:
    log.info("Start silnika Profit VIP (Tryb bieżący: co 2 godziny, ochrona przed duplikatami).")
    state = load_state()
    
    try:
        scan_once(state)
    except Exception:
        log.exception("Błąd wstępnego skanu")

    while True:
        started = time.monotonic()
        try:
            scan_once(state)
        except Exception:
            log.exception("Błąd cyklu skanowania")
        elapsed = time.monotonic() - started
        time.sleep(max(60, SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
