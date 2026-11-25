import os
import json
import time as pytime
import logging
from datetime import datetime, timedelta
from itertools import combinations
from statistics import mean, median
from typing import List, Dict, Any, Tuple, Set, Optional
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("PROFIT_MACHINE_V2")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "64ea0f27d1bf2d4c4eeabf5e6b836be6").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")
DAILY_DROP_HOUR = int(os.getenv("DAILY_DROP_HOUR", "6"))
DAILY_DROP_MINUTE = int(os.getenv("DAILY_DROP_MINUTE", "0"))

STATE_FILE = os.getenv("VIP_STATE_FILE", "vip_2067_used_events.json").strip()

UNIT_PLN = int(os.getenv("UNIT_PLN", "25"))
MIN_UNITS = int(os.getenv("MIN_UNITS", "1"))
MAX_UNITS = int(os.getenv("MAX_UNITS", "4"))

MIN_DAILY_COUPONS = int(os.getenv("MIN_DAILY_COUPONS", "4"))
MAX_DAILY_COUPONS = int(os.getenv("MAX_DAILY_COUPONS", "10"))

SOLO_MIN_ODDS = float(os.getenv("SOLO_MIN_ODDS", "1.50"))
SOLO_MAX_ODDS = float(os.getenv("SOLO_MAX_ODDS", "4.20"))
PARLAY_LEG_MIN_ODDS = float(os.getenv("PARLAY_LEG_MIN_ODDS", "1.18"))
PARLAY_LEG_MAX_ODDS = float(os.getenv("PARLAY_LEG_MAX_ODDS", "2.60"))
AKO2_MIN_ODDS = float(os.getenv("AKO2_MIN_ODDS", "1.65"))
AKO3_MIN_ODDS = float(os.getenv("AKO3_MIN_ODDS", "2.00"))
MAX_COUPON_ODDS = float(os.getenv("MAX_COUPON_ODDS", "8.50"))

MIN_EDGE_RATIO = float(os.getenv("MIN_EDGE_RATIO", "1.03"))
STRONG_EDGE_RATIO = float(os.getenv("STRONG_EDGE_RATIO", "1.08"))
SNIPER_EDGE_RATIO = float(os.getenv("SNIPER_EDGE_RATIO", "1.14"))
MAX_SUSPICIOUS_SPREAD = float(os.getenv("MAX_SUSPICIOUS_SPREAD", "1.45"))

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


def key_fallback(sport: Dict[str, Any]) -> str:
    return (sport.get("key") or "Sport").replace("_", " ").title()


def prettify_sport_name(sport: Dict[str, Any]) -> str:
    title = (sport.get("title") or "").strip()
    group = (sport.get("group") or "").strip()
    if title and group and group.lower() not in title.lower():
        return f"{group} — {title}"
    return title or key_fallback(sport)


def load_used_events_for_day(day_str: str) -> Set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("day") == day_str and isinstance(data.get("used_event_ids"), list):
            return {str(x) for x in data["used_event_ids"] if x}
    except Exception as exc:
        logger.warning("Nie udało się wczytać stanu dobowego: %s", exc)
    return set()


def save_used_events_for_day(day_str: str, used_ids: Set[str]) -> None:
    try:
        state_dir = os.path.dirname(STATE_FILE)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
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
        os.replace(tmp_path, STATE_FILE)
    except Exception as exc:
        logger.warning("Nie udało się zapisać stanu dobowego: %s", exc)


def get_rolling_24h_window(now_local: datetime) -> Tuple[datetime, datetime]:
    return now_local - timedelta(minutes=15), now_local + timedelta(hours=24)


def fetch_active_sports() -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    try:
        resp = requests.get(url, params=params, timeout=25)
        if resp.status_code != 200:
            logger.warning("Błąd pobierania listy sportów (status=%s)", resp.status_code)
            return []
        sports = resp.json()
        if not isinstance(sports, list):
            return []
        filtered = [s for s in sports if isinstance(s, dict) and is_allowed_sport(s)]
        logger.info("Załadowano %d aktywnych rozgrywek do skanowania VIP.", len(filtered))
        return filtered
    except Exception as exc:
        logger.warning("Wyjątek podczas pobierania listy sportów: %s", exc)
        return []


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
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            logger.warning("Błąd pobierania kursów dla %s (status=%s)", sport_key, resp.status_code)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Błąd pobierania kursów dla %s: %s", sport_key, exc)
        return []


def analyze_anomaly_and_value(best_odds: float, all_odds: List[float]) -> Dict[str, Any]:
    avg_odds = mean(all_odds) if all_odds else best_odds
    if len(all_odds) >= 3:
        peers = sorted(all_odds)[:-1]
        ref_odds = median(peers)
    elif len(all_odds) == 2:
        ref_odds = sorted(all_odds)[0]
    else:
        ref_odds = best_odds

    edge_ratio = (best_odds / ref_odds) if ref_odds > 0 else 1.0
    max_to_min = (max(all_odds) / min(all_odds)) if len(all_odds) >= 2 and min(all_odds) > 0 else 1.0
    implied_prob = (1.0 / best_odds) if best_odds > 0 else 0.0

    spread_penalty = 0.0
    if max_to_min > MAX_SUSPICIOUS_SPREAD:
        spread_penalty = (max_to_min - MAX_SUSPICIOUS_SPREAD) * 28.0

    anomaly_bonus = 0.0
    signal_tag = "VALUE"
    if edge_ratio >= SNIPER_EDGE_RATIO and max_to_min <= MAX_SUSPICIOUS_SPREAD:
        anomaly_bonus = 10.0
        signal_tag = "BŁĄD BUKA / ANOMALIA"
    elif edge_ratio >= STRONG_EDGE_RATIO and max_to_min <= MAX_SUSPICIOUS_SPREAD:
        anomaly_bonus = 5.5
        signal_tag = "MOCNY EDGE"

    score = round((edge_ratio * 96.0) + (implied_prob * 38.0) + anomaly_bonus - spread_penalty, 2)

    return {
        "score": score,
        "edge_ratio": round(edge_ratio, 3),
        "avg_odds": round(avg_odds, 2),
        "max_to_min": round(max_to_min, 2),
        "bookmakers_count": len(all_odds),
        "signal_tag": signal_tag,
    }


def parse_commence_time(commence_raw: Any) -> Optional[datetime]:
    if not isinstance(commence_raw, str) or not commence_raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(commence_raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def extract_picks_from_events(
    events: List[Dict[str, Any]],
    sport_key: str,
    sport_label: str,
    window_start: datetime,
    window_end: datetime,
    blocked_ids: Set[str],
    relaxed: bool = False,
) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    min_edge = 1.01 if relaxed else MIN_EDGE_RATIO

    for ev in events:
        if not isinstance(ev, dict):
            continue

        ev_id = str(ev.get("id") or "").strip()
        if not ev_id or ev_id in blocked_ids:
            continue

        commence_local = parse_commence_time(ev.get("commence_time"))
        if commence_local is None:
            continue

        if not (window_start <= commence_local <= window_end):
            continue

        home = ev.get("home_team")
        away = ev.get("away_team")
        if not isinstance(home, str) or not isinstance(away, str) or not home.strip() or not away.strip():
            continue

        home = home.strip()
        away = away.strip()
        odds_by_side: Dict[str, List[Tuple[float, str]]] = {home: [], away: []}

        bookmakers = ev.get("bookmakers")
        if not isinstance(bookmakers, list):
            continue

        for bm in bookmakers:
            if not isinstance(bm, dict):
                continue
            bm_title = str(bm.get("title") or "Bookmaker")
            markets = bm.get("markets")
            if not isinstance(markets, list):
                continue
            for mkt in markets:
                if not isinstance(mkt, dict) or mkt.get("key") != "h2h":
                    continue
                outcomes = mkt.get("outcomes")
                if not isinstance(outcomes, list):
                    continue
                for out in outcomes:
                    if not isinstance(out, dict):
                        continue
                    name = out.get("name")
                    price = out.get("price")
                    if name in odds_by_side and isinstance(price, (int, float)) and not isinstance(price, bool):
                        price_f = float(price)
                        if 1.15 <= price_f <= 6.0:
                            odds_by_side[name].append((price_f, bm_title))

        for selection, entries in odds_by_side.items():
            if not entries:
                continue
            best_price, best_bm = max(entries, key=lambda x: x[0])
            all_prices = [p for p, _ in entries]
            analysis = analyze_anomaly_and_value(best_price, all_prices)

            if analysis["edge_ratio"] < min_edge:
                continue

            picks.append(
                {
                    "event_id": ev_id,
                    "sport_key": sport_key,
                    "sport_label": sport_label,
                    "home": home,
                    "away": away,
                    "selection": selection,
                    "odds": round(best_price, 2),
                    "avg_odds": analysis["avg_odds"],
                    "edge_ratio": analysis["edge_ratio"],
                    "max_to_min": analysis["max_to_min"],
                    "bookmakers_count": analysis["bookmakers_count"],
                    "signal_tag": analysis["signal_tag"],
                    "bookmaker": best_bm,
                    "commence_local": commence_local,
                    "score": analysis["score"],
                }
            )

    return picks


def assign_units_2067(
    coupon_type: str, odds: float, score: float, max_edge: float, max_spread: float
) -> int:
    if max_spread > MAX_SUSPICIOUS_SPREAD:
        return 1

    units = 1
    if coupon_type == "SOLO":
        if odds <= 2.20 and max_edge >= STRONG_EDGE_RATIO and score >= 122.0:
            units = 4
        elif odds <= 2.65 and max_edge >= STRONG_EDGE_RATIO and score >= 117.0:
            units = 3
        elif score >= 113.0:
            units = 2
        else:
            units = 1
    elif coupon_type == "AKO2":
        if odds <= 2.50 and max_edge >= STRONG_EDGE_RATIO and score >= 120.0:
            units = 3
        elif score >= 114.0:
            units = 2
        else:
            units = 1
    elif coupon_type == "AKO3":
        if odds <= 3.10 and max_edge >= STRONG_EDGE_RATIO and score >= 118.0:
            units = 2
        else:
            units = 1

    return max(MIN_UNITS, min(MAX_UNITS, units))


def compose_daily_vip_pack(
    picks: List[Dict[str, Any]],
    min_coupons: int = MIN_DAILY_COUPONS,
    max_coupons: int = MAX_DAILY_COUPONS,
    blocked_ids: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    already_used = set(blocked_ids or set())

    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(picks, key=lambda x: x["score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event and eid not in already_used:
            best_per_event[eid] = p

    ranked = sorted(best_per_event.values(), key=lambda x: x["score"],
