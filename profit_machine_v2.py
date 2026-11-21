import os
import logging
from datetime import datetime, timedelta, time
from itertools import combinations
from typing import List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("PITOS_AKO2")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Parametry pod AKO2 i Betclic (25 / 50 / 75 PLN)
AKO_MIN = float(os.getenv("AKO_MIN", "1.95"))
AKO_MAX = float(os.getenv("AKO_MAX", "3.40"))
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.25"))
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "2.60"))
MAX_COUPONS = int(os.getenv("MAX_COUPONS_PER_RUN", "3"))

STAKE_LOW = int(os.getenv("STAKE_LOW", "25"))
STAKE_MID = int(os.getenv("STAKE_MID", "50"))
STAKE_HIGH = int(os.getenv("STAKE_HIGH", "75"))

TARGET_GROUPS = {
    "basketball": "Koszykówka",
    "icehockey": "Hokej",
    "tennis": "Tenis",
}

BASE_URL = "https://api.the-odds-api.com/v4"


def fetch_all_target_sports() -> List[Dict[str, Any]]:
    """Pobiera wszystkie aktywne rozgrywki dla koszykówki, hokeja i tenisa (pełna oferta)."""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    sports = resp.json()

    matched = []
    for s in sports:
        group = (s.get("group") or "").lower()
        key = (s.get("key") or "").lower()
        if any(grp in group or key.startswith(grp) for grp in TARGET_GROUPS):
            matched.append(s)

    logger.info("Znaleziono %d aktywnych rozgrywek (kosz / hokej / tenis).", len(matched))
    return matched


def get_day_session_window(now_local: datetime) -> Tuple[datetime, datetime]:
    """
    Okno sesji dziennej: od teraz do 19:00 lokalnego czasu (lub bezpiecznego limitu wieczornego),
    żeby łapać mecze dzienne, a nie nocne NHL/NBA z Ameryki Północnej.
    """
    today = now_local.date()
    start = now_local
    end = datetime.combine(today, time(19, 0), tzinfo=TZ)

    if end <= start:
        end = min(
            now_local + timedelta(hours=2, minutes=30),
            datetime.combine(today, time(20, 30), tzinfo=TZ),
        )
    return start, end


def is_night_na_event(sport_key: str, sport_title: str, commence_local: datetime) -> bool:
    """Blokuje typowo nocne rozgrywki z Ameryki Północnej w sesji dziennej."""
    combined = f"{sport_key} {sport_title}".lower()
    na_markers = ("nhl", "ahl", "nba", "wnba", "ncaab", "ncaaf")
    if any(m in combined for m in na_markers) and commence_local.hour >= 17:
        return True
    return False


def fetch_events_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    """Pobiera mecze i kursy h2h dla wybranej ligi/turnieju."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Błąd pobierania kursów dla %s: %s", sport_key, exc)
        return []


def extract_day_picks(
    events: List[Dict[str, Any]],
    sport_key: str,
    sport_title: str,
    group_label: str,
    window_start: datetime,
    window_end: datetime,
) -> List[Dict[str, Any]]:
    """Wyciąga typy 1/2 z okna dziennego w zadanych widełkach kursowych."""
    picks: List[Dict[str, Any]] = []

    for ev in events:
        commence_raw = ev.get("commence_time")
        if not commence_raw:
            continue

        commence_local = datetime.fromisoformat(commence_raw.replace("Z", "+00:00")).astimezone(TZ)
        if not (window_start <= commence_local <= window_end):
            continue

        if is_night_na_event(sport_key, sport_title, commence_local):
            continue

        home = ev.get("home_team")
        away = ev.get("away_team")
        if not home or not away:
            continue

        best_odds: Dict[str, Tuple[float, str]] = {}
        for bm in ev.get("bookmakers", []):
            bm_title = bm.get("title", "Bookmaker")
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for out in mkt.get("outcomes", []):
                    name = out.get("name")
                    price = out.get("price")
                    if name not in (home, away) or not isinstance(price, (int, float)):
                        continue
                    price = float(price)
                    if MIN_LEG_ODDS <= price <= MAX_LEG_ODDS:
                        if name not in best_odds or price > best_odds[name][0]:
                            best_odds[name] = (price, bm_title)

        for selection, (price, bm_title) in best_odds.items():
            picks.append(
                {
                    "event_id": ev.get("id"),
                    "sport_key": sport_key,
                    "sport_title": sport_title,
                    "group_label": group_label,
                    "home": home,
                    "away": away,
                    "selection": selection,
                    "odds": round(price, 2),
                    "bookmaker": bm_title,
                    "commence_local": commence_local,
                }
            )

    return picks


def choose_stake_pln(ako: float) -> int:
    """Dobiera stawkę 25 / 50 / 75 PLN pod kurs AKO2 (1.95 - 3.40)."""
    if ako <= 2.35:
        return STAKE_HIGH  # 75 PLN — niższe AKO, wyższa pewność
    if ako <= 2.85:
        return STAKE_MID   # 50 PLN — środek stawki
    return STAKE_LOW       # 25 PLN — wyższe AKO w widełkach


def build_ako2_coupons(picks: List[Dict[str, Any]], max_coupons: int = MAX_COUPONS) -> List[Dict[str, Any]]:
    """Buduje kupony AKO2 z różnych meczów w widełkach AKO_MIN..AKO_MAX (1.95 - 3.40)."""
    by_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(picks, key=lambda x: x["odds"], reverse=True):
        eid = p["event_id"]
        if eid not in by_event:
            by_event[eid] = p

    unique_picks = list(by_event.values())
    coupons: List[Dict[str, Any]] = []
    used_events = set()

    for leg_a, leg_b in combinations(unique_picks, 2):
        if len(coupons) >= max_coupons:
            break
        if leg_a["event_id"] in used_events or leg_b["event_id"] in used_events:
            continue

        ako = round(leg_a["odds"] * leg_b["odds"], 2)
        if AKO_MIN <= ako <= AKO_MAX:
            stake_pln = choose_stake_pln(ako)
            potential_return = round(stake_pln * ako, 2)  # Betclic bez podatku (x1.0)
            coupons.append(
                {
                    "legs": [leg_a, leg_b],
                    "ako": ako,
                    "stake_pln": stake_pln,
                    "potential_return": potential_return,
                }
            )
            used_events.add(leg_a["event_id"])
            used_events.add(leg_b["event_id"])

    return coupons


def format_telegram_message(window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]]) -> str:
    """Formatuje kupony AKO2 na Telegram z godziną, ligą, typem i stawką w PLN."""
    header = (
        f"🏀🏒🎾 *KUPONY AKO2 — SESJA DZIENNA*\n"
        f"🕒 Okno: `{window_start.strftime('%H:%M')}` – `{window_end.strftime('%H:%M')}` ({TZ_NAME})\n"
        f"🎯 Zakres AKO: `{AKO_MIN:.2f} – {AKO_MAX:.2f}` | Stawki: `{STAKE_LOW} / {STAKE_MID} / {STAKE_HIGH} PLN` (Betclic)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return (
            header
            + f"⚠️ Brak par AKO2 w widełkach `{AKO_MIN:.2f}–{AKO_MAX:.2f}` dla aktualnego okna dziennego."
        )

    blocks = [header]
    for idx, c in enumerate(coupons, start=1):
        lines = [
            f"*KUPON #{idx} (AKO2)* | Kurs: *{c['ako']:.2f}* | Stawka: *{c['stake_pln']} PLN* "
            f"(Do wyjęcia: *{c['potential_return']:.2f} PLN*)"
        ]
        for leg in c["legs"]:
            godz = leg["commence_local"].strftime("%H:%M")
            lines.append(
                f"• [{leg['group_label']}] *{leg['sport_title']}* — `{godz}`\n"
                f"  `{leg['home']} vs {leg['away']}`\n"
                f"  Typ: *{leg['selection']}* @ *{leg['odds']:.2f}* ({leg['bookmaker']})"
            )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def send_telegram(text: str) -> None:
    """Wysyła gotową paczkę kuponów na Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — drukuję wiadomość:\n%s", text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    logger.info("Wysłano kupony AKO2 na Telegram.")


def run_day_now() -> None:
    """Natychmiastowy przegląd pełnej oferty dziennej (kosz/hokej/tenis) i wysyłka kuponów AKO2."""
    if not ODDS_API_KEY:
        raise RuntimeError("Brak zmiennej środowiskowej ODDS_API_KEY!")

    now_local = datetime.now(TZ)
    window_start, window_end = get_day_session_window(now_local)
    logger.info("Sesja DZIENNA: %s -> %s", window_start, window_end)

    sports = fetch_all_target_sports()
    all_picks: List[Dict[str, Any]] = []

    for s in sports:
        s_key = s.get("key", "")
        s_title = s.get("title", s_key)
        grp_raw = (s.get("group") or "").lower()

        group_label = "Sport"
        for k, label in TARGET_GROUPS.items():
            if k in grp_raw or s_key.lower().startswith(k):
                group_label = label
                break

        events = fetch_events_for_sport(s_key)
        if not events:
            continue

        picks = extract_day_picks(
            events=events,
            sport_key=s_key,
            sport_title=s_title,
            group_label=group_label,
            window_start=window_start,
            window_end=window_end,
        )
        all_picks.extend(picks)

    logger.info("Zebrano %d typów w sesji dziennej.", len(all_picks))
    coupons = build_ako2_coupons(all_picks, max_coupons=MAX_COUPONS)
    message = format_telegram_message(window_start, window_end, coupons)
    send_telegram(message)


if __name__ == "__main__":
    run_day_now()
