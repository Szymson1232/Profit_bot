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


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL_SECONDS = env_int("SCAN_INTERVAL_SECONDS", 900, 60)
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "eu").strip() or "eu"
MAX_SPORTS_PER_SCAN = env_int("MAX_SPORTS_PER_SCAN", 10, 1)
MAX_PICKS = min(6, env_int("MAX_PICKS", 6, 1))
MIN_BOOKMAKERS = env_int("MIN_BOOKMAKERS", 4, 2)
MIN_EDGE_PCT = env_float("MIN_EDGE_PCT", 1.0)
MIN_EV_PCT = env_float("MIN_EV_PCT", 1.0)
MIN_MINUTES_TO_START = env_int("MIN_MINUTES_TO_START", 20, 0)
MAX_ODDS_AGE_MINUTES = env_int("MAX_ODDS_AGE_MINUTES", 90, 1)
DEDUP_HOURS = env_int("DEDUP_HOURS", 48, 1)
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Warsaw")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
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
        # Do not log request URL: it contains the API key.
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"The Odds API HTTP {response.status_code}: {detail}")
    return response.json()


def get_active_sports() -> list[dict[str, Any]]:
    sports = api_get("/sports/")
    if not isinstance(sports, list):
        return []
    active = [s for s in sports if s.get("active") and s.get("key")]
    # Prefer leagues with a recognizable title; API order is retained for stability.
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
    """Return valid h2h prices grouped by bookmaker, with bookmaker no-vig probabilities."""
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
    # One best selection per event, avoiding correlated duplicates from the same match.
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
    """Build illustrative AKO combinations from distinct events; EV assumes independence."""
    akos2, akos3 = [], []
    for size, target, threshold in ((2, akos2, 2.0), (3, akos3, 3.0)):
        for combo in itertools.combinations(picks, size):
            if len({p["event_id"] for p in combo}) != size:
                continue
            total_odds = 1.0
            joint_probability = 1.0
            for p in combo:
                total_odds *= p["odds"]
                joint_probability *= p["fair_probability"]
            if total_odds > threshold:
                target.append({"picks": combo, "odds": total_odds, "ev_pct": (joint_probability * total_odds - 1) * 100})
        target.sort(key=lambda x: (x["ev_pct"], x["odds"]), reverse=True)
    return akos2[:2], akos3[:2]


def send_telegram(message: str) -> None:
    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = SESSION.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:300]}")
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def build_message(picks: list[dict[str, Any]], scanned_sports: int) -> str:
    local_now = now_utc().astimezone(ZoneInfo(BOT_TIMEZONE))
    lines = [
        "<b>VALUE SCANNER | VIP</b>",
        f"{local_now.strftime('%d.%m.%Y %H:%M')} | Przeskanowane ligi: {scanned_sports}",
        f"Znalezione nowe selekcje: <b>{len(picks)}/{MAX_PICKS}</b>",
        "",
    ]
    lines.extend(fmt_pick(p, i + 1) for i, p in enumerate(picks))

    solos_mid = [p for p in picks if 1.80 <= p["odds"] <= 2.00]
    solos_high = [p for p in picks if p["odds"] > 2.00]
    ako2, ako3 = make_akos(picks)
    lines += ["", "<b>SOLO 1.80–2.00</b>"]
    lines += [f"• {escape(p['home'])} – {escape(p['away'])}: {escape(p['outcome'])} @ {p['odds']:.2f}" for p in solos_mid] or ["Brak kwalifikujących się typów."]
    lines += ["", "<b>SOLO POWYŻEJ 2.00</b>"]
    lines += [f"• {escape(p['home'])} – {escape(p['away'])}: {escape(p['outcome'])} @ {p['odds']:.2f}" for p in solos_high] or ["Brak kwalifikujących się typów."]

    for title, combos in (("AKO2 | kurs łączny &gt; 2.00", ako2), ("AKO3 | kurs łączny &gt; 3.00", ako3)):
        lines += ["", f"<b>{title}</b>"]
        if not combos:
            lines.append("Brak kombinacji spełniającej próg.")
        for combo in combos:
            labels = [f"{p['home']}–{p['away']}: {p['outcome']} @{p['odds']:.2f}" for p in combo["picks"]]
            lines.append("• " + " + ".join(escape(x) for x in labels) + f" | Łączny kurs: <b>{combo['odds']:.2f}</b> | modelowe EV AKO: {combo['ev_pct']:+.2f}%")

    lines += [
        "",
        "<i>Metodologia: estymacja prawdopodobieństwa z mediany no-vig rynku; to skaner rynku, nie niezależny model predykcyjny. EV AKO zakłada niezależność zdarzeń. Kursy mogą się zmienić — sprawdź je przed zagraniem. Brak gwarancji zysku.</i>",
    ]
    return "\n".join(lines)


def scan_once(state: dict[str, Any]) -> None:
    sports = get_active_sports()
    log.info("Aktywne ligi dostępne w API: %s; skanuję maksymalnie %s", len(sports), MAX_SPORTS_PER_SCAN)
    evaluated: list[dict[str, Any]] = []
    for sport in sports:
        try:
            events = fetch_events(sport["key"])
            log.info("%s: %s wydarzeń", sport["key"], len(events))
            for event in events:
                pick = evaluate_event(event, sport)
                if pick:
                    evaluated.append(pick)
        except Exception as exc:
            log.warning("Pominięto ligę %s: %s", sport.get("key"), exc)

    # Keep only one candidate per event and sort by estimated EV.
    best_by_event: dict[str, dict[str, Any]] = {}
    for pick in evaluated:
        event_id = pick["event_id"]
        if event_id not in best_by_event or pick["ev_pct"] > best_by_event[event_id]["ev_pct"]:
            best_by_event[event_id] = pick
    new_picks = filter_new_picks(list(best_by_event.values()), state)

    if not new_picks:
        log.info("Brak nowych selekcji spełniających filtry. Nic nie publikuję.")
        save_state(state)
        return

    message = build_message(new_picks, len(sports))
    send_telegram(message)
    stamp = now_utc().timestamp()
    for pick in new_picks:
        state.setdefault("sent", {})[selection_key(pick)] = stamp
    save_state(state)
    log.info("Wysłano %s nowych selekcji na Telegram.", len(new_picks))


def validate_config() -> None:
    missing = [name for name, value in (
        ("ODDS_API_KEY", ODDS_API_KEY),
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
    ) if not value]
    if missing:
        raise SystemExit("Brak wymaganych zmiennych środowiskowych: " + ", ".join(missing))
    try:
        ZoneInfo(BOT_TIMEZONE)
    except Exception:
        raise SystemExit(f"Nieprawidłowa strefa czasowa BOT_TIMEZONE={BOT_TIMEZONE!r}")


def main() -> None:
    validate_config()
    log.info("Start bota VIP. Interwał=%ss, regiony=%s, max lig=%s, max picków=%s", SCAN_INTERVAL_SECONDS, ODDS_REGIONS, MAX_SPORTS_PER_SCAN, MAX_PICKS)
    state = load_state()
    while True:
        started = time.monotonic()
        try:
            scan_once(state)
        except Exception:
            log.exception("Błąd cyklu skanowania")
        elapsed = time.monotonic() - started
        time.sleep(max(5, SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
