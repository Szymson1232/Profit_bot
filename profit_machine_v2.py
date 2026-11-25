import os
import json
import time as pytime
import logging
from datetime import datetime, timedelta, time
from itertools import combinations
from statistics import mean, median
from typing import List, Dict, Any, Tuple, Set
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("FREAK_VIP_2067")

# Nowe API + dane Telegrama
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "64ea0f27d1bf2d4c4eeabf5e6b836be6").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Od razu po starcie zapodaje paczkę, a potem codziennie o 06:00 czasu polskiego
START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")
DAILY_DROP_HOUR = int(os.getenv("DAILY_DROP_HOUR", "6"))
DAILY_DROP_MINUTE = int(os.getenv("DAILY_DROP_MINUTE", "0"))

STATE_FILE = os.getenv("VIP_STATE_FILE", "vip_2067_used_events.json").strip()

# Bankroll / jednostki: 1j = 25 PLN, drabinka 1j / 2j / 3j / 4j
UNIT_PLN = int(os.getenv("UNIT_PLN", "25"))
MIN_UNITS = int(os.getenv("MIN_UNITS", "1"))
MAX_UNITS = int(os.getenv("MAX_UNITS", "4"))

# Cel paczki na dobę: minimum 4 kupony na dobę (bez sztywnego sufitu w dół, max np. 10)
MIN_DAILY_COUPONS = int(os.getenv("MIN_DAILY_COUPONS", "4"))
MAX_DAILY_COUPONS = int(os.getenv("MAX_DAILY_COUPONS", "10"))

# Zasady kursowe: SOLO od 1.50+, oraz AKO2 / AKO3 z jakościowych nóg
SOLO_MIN_ODDS = float(os.getenv("SOLO_MIN_ODDS", "1.50"))
SOLO_MAX_ODDS = float(os.getenv("SOLO_MAX_ODDS", "4.20"))
PARLAY_LEG_MIN_ODDS = float(os.getenv("PARLAY_LEG_MIN_ODDS", "1.18"))
PARLAY_LEG_MAX_ODDS = float(os.getenv("PARLAY_LEG_MAX_ODDS", "2.60"))
AKO2_MIN_ODDS = float(os.getenv("AKO2_MIN_ODDS", "1.65"))
AKO3_MIN_ODDS = float(os.getenv("AKO3_MIN_ODDS", "2.00"))
MAX_COUPON_ODDS = float(os.getenv("MAX_COUPON_ODDS", "8.50"))

# Detektor anomalii / błędu buka / "szpiega rynkowego"
MIN_EDGE_RATIO = float(os.getenv("MIN_EDGE_RATIO", "1.03"))
STRONG_EDGE_RATIO = float(os.getenv("STRONG_EDGE_RATIO", "1.08"))
SNIPER_EDGE_RATIO = float(os.getenv("SNIPER_EDGE_RATIO", "1.14"))
MAX_SUSPICIOUS_SPREAD = float(os.getenv("MAX_SUSPICIOUS_SPREAD", "1.45"))

# Wykluczamy wyłącznie sporty wyraźnie niepasujące pod klasyczną ofertę (np. szachy/wyścigi konne jeśli nieużywane),
# a resztę otwieramy szeroko pod dostępność na Betclic
EXCLUDED_SPORT_PREFIXES = (
    "horse_racing",
    "greyhound_racing",
    "politics",
    "cricket",
)

BASE_URL = "https://api.the-odds-api.com/v4"


def is_allowed_sport(sport: Dict[str, Any]) -> bool:
    key = (sport.get("key") or "").lower()
    if not key or sport.get("active") is False:
        return False
    if any(key.startswith(prefix) for prefix in EXCLUDED_SPORT_PREFIXES):
        return False
    return True


def prettify_sport_name(sport: Dict[str, Any]) -> str:
    title = (sport.get("title") or "").strip()
    group = (sport.get("group") or "").strip()
    if title and group and group.lower() not in title.lower():
        return f"{group} — {title}"
    return title or key_fallback(sport)


def key_fallback(sport: Dict[str, Any]) -> str:
    return (sport.get("key") or "Sport").replace("_", " ").title()


def load_used_events_for_day(day_str: str) -> Set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("day") == day_str and isinstance(data.get("used_event_ids"), list):
            return set(str(x) for x in data["used_event_ids"])
    except Exception as exc:
        logger.warning("Nie udało się wczytać stanu dobowego: %s", exc)
    return set()


def save_used_events_for_day(day_str: str, used_ids: Set[str]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "day": day_str,
                    "used_event_ids": sorted(used_ids),
                    "updated_at": datetime.now(TZ).isoformat(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        logger.warning("Nie udało się zapisać stanu dobowego: %s", exc)


def get_rolling_24h_window(now_local: datetime) -> Tuple[datetime, datetime]:
    """Najbliższa doba (24h) od momentu uruchomienia."""
    return now_local - timedelta(minutes=15), now_local + timedelta(hours=24)


def fetch_active_sports() -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    resp = requests.get(url, params=params, timeout=25)
    resp.raise_for_status()
    sports = resp.json()
    filtered = [s for s in sports if is_allowed_sport(s)]
    logger.info("Załadowano %d aktywnych rozgrywek do skanowania VIP.", len(filtered))
    return filtered


def fetch_sport_odds(sport_key: str) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk,us,au",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        resp = requests.get(url, params=params, timeout=25)
        if resp.status
