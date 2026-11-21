import os
import time as pytime
import logging
from datetime import datetime, timedelta, time
from itertools import combinations
from statistics import mean
from typing import List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("PITOS_BOT")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "133d53ed2236d1e09849deabd16155e6").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Wyjątkowo na start wysyła paczkę na nockę (bo 20:00 już minęła), potem działa według harmonogramu 08:00 / 20:00
START_NIGHT_NOW = os.getenv("START_NIGHT_NOW", "true").strip().lower() in ("1", "true", "yes", "on")

# Widełki pod AKO2 i Betclic (25 / 50 / 75 PLN) — minimum 1.20 na nogę pod grę bez podatku
AKO_MIN = float(os.getenv("AKO_MIN", "1.85"))
AKO_MAX = float(os.getenv("AKO_MAX", "3.60"))
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.20"))
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "3.80"))
MAX_COUPONS = int(os.getenv("MAX_COUPONS_PER_RUN", "5"))

STAKE_LOW = int(os.getenv("STAKE_LOW", "25"))
STAKE_MID = int(os.getenv("STAKE_MID", "50"))
STAKE_HIGH = int(os.getenv("STAKE_HIGH", "75"))

TARGET_GROUPS = {
    "basketball": "Koszykówka",
    "icehockey": "Hokej",
    "tennis": "Tenis",
}

BLOCKED_DAY_KEYWORDS = (
    "nba",
    "wnba",
    "nhl",
    "ahl",
    "ncaab",
    "ncaaf",
    "g_league",
    "gleague",
)

BASE_URL = "https://api.the-odds-api.com/v4"


def get_session_window(session: str, now_local: datetime) -> Tuple[datetime, datetime]:
    """
    - 'day' (odpalane o 08:00): od startu do 19:59 tego samego dnia (mecze dzienne).
    - 'night' (odpalane o 20:00 lub wyjątkowo na start): od teraz do 08:30 kolejnego dnia (nocka).
    """
    today = now_local.date()

    if session == "day":
        start = now_local
        end = datetime.combine(today, time(19, 59), tzinfo=TZ)
        if end <= start:
            end = start + timedelta(hours=4)
        return start, end

    start = now_local
    tomorrow = today + timedelta(days=1)
    end = datetime.combine(tomorrow, time(8, 30), tzinfo=TZ)
    return start, end


def is_filtered_out(session: str, sport_key: str, sport_title: str, commence_local: datetime) -> bool:
    """W sesji dziennej (08:00) blokuje NBA/NHL oraz późne okienka USA; w nocnej (20:00) puszcza."""
    if session != "day":
        return False
    text = f"{sport_key} {sport_title}".lower()
    if any(bad in text for bad in BLOCKED_DAY_KEYWORDS):
        return True
    if any(tag in text for tag in ("usa", "north america", "canada")) and commence_local.hour >= 16:
        return True
    return False


def fetch_all_target_sports(session: str) -> List[Dict[str, Any]]:
    """Pobiera wszystkie aktywne rozgrywki dla koszykówki, hokeja i tenisa."""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    sports = resp.json()

    matched: List[Dict[str, Any]] = []
    for s in sports:
        group = (s.get("group") or "").lower()
        key = (s.get("key") or "").lower()
        title = (s.get("title") or "").lower()

        if not any(grp in group or key.startswith(grp) for grp in TARGET_GROUPS):
            continue
        if session == "day" and any(bad in f"{key} {title}" for bad in BLOCKED_DAY_KEYWORDS):
            continue
        matched.append(s)

    logger.info("Aktywne ligi/turnieje (%s): %d", session, len(matched))
    return matched


def fetch_events_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    """Pobiera mecze i kursy h2h dla wskazanej ligi/turnieju."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk,us",
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
        logger.warning("Błąd dla %s: %s", sport_key, exc)
        return []


def compute_quality_score(best_odds: float, all_odds: List[float]) -> float:
    avg_odds = mean(all_odds) if all_odds else best_odds
    edge_ratio = (best_odds / avg_odds) if avg_odds > 0 else 1.0
    implied_prob = 1.0 / best_odds if best_odds > 0 else 0.0
    return round((edge_ratio * 100.0) + (implied_prob * 35.0), 2)


def extract_quality_picks(
    events: List[Dict[str, Any]],
    session: str,
    sport_key: str,
    sport_title: str,
    group_label: str,
    window_start: datetime,
    window_end: datetime,
) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []

    for ev in events:
        commence_raw = ev.get("commence_time")
        if not commence_raw:
            continue

        commence_local = datetime.fromisoformat(commence_raw.replace("Z", "+00:00")).astimezone(TZ)
        if not (window_start <= commence_local <= window_end):
            continue

        if is_filtered_out(session, sport_key, sport_title, commence_local):
            continue

        home = ev.get("home_team")
        away = ev.get("away_team")
        if not home or not away:
            continue

        odds_by_side: Dict[str, List[Tuple[float, str]]] = {home: [], away: []}

        for bm in ev.get("bookmakers", []):
            bm_title = bm.get("title", "Bookmaker")
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for out in mkt.get("outcomes", []):
                    name = out.get("name")
                    price = out.get("price")
                    if name in odds_by_side and isinstance(price, (int, float)):
                        price_f = float(price)
                        if MIN_LEG_ODDS <= price_f <= MAX_LEG_ODDS:
                            odds_by_side[name].append((price_f, bm_title))

        for selection, entries in odds_by_side.items():
            if not entries:
                continue
            best_price, best_bm = max(entries, key=lambda x: x[0])
            all_prices = [p for p, _ in entries]
            picks.append(
                {
                    "event_id": ev.get("id"),
                    "sport_key": sport_key,
                    "sport_title": sport_title,
                    "group_label": group_label,
                    "home": home,
                    "away": away,
                    "selection": selection,
                    "odds": round(best_price, 2),
                    "avg_odds": round(mean(all_prices), 2),
                    "bookmaker": best_bm,
                    "commence_local": commence_local,
                    "quality_score": compute_quality_score(best_price, all_prices),
                }
            )

    return picks


def choose_stake_pln(ako: float, avg_quality: float) -> int:
    if ako <= 2.35 and avg_quality >= 115.0:
        return STAKE_HIGH
    if ako <= 2.95:
        return STAKE_MID
    return STAKE_LOW


def build_ako2_coupons(picks: List[Dict[str, Any]], max_coupons: int = MAX_COUPONS) -> List[Dict[str, Any]]:
    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(picks, key=lambda x: x["quality_score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event:
            best_per_event[eid] = p

    ranked_picks = sorted(best_per_event.values(), key=lambda x: x["quality_score"], reverse=True)
    coupons: List[Dict[str, Any]] = []
    used_events = set()
    seen_pairs = set()

    for leg_a, leg_b in combinations(ranked_picks, 2):
        if len(coupons) >= max_coupons:
            return coupons
        if leg_a["odds"] < MIN_LEG_ODDS or leg_b["odds"] < MIN_LEG_ODDS:
            continue
        if leg_a["event_id"] in used_events or leg_b["event_id"] in used_events:
            continue

        ako = round(leg_a["odds"] * leg_b["odds"], 2)
        if AKO_MIN <= ako <= AKO_MAX:
            pair_key = tuple(
                sorted([f"{leg_a['event_id']}:{leg_a['selection']}", f"{leg_b['event_id']}:{leg_b['selection']}"])
            )
            seen_pairs.add(pair_key)
            avg_q = (leg_a["quality_score"] + leg_b["quality_score"]) / 2.0
            stake_pln = choose_stake_pln(ako, avg_q)
            coupons.append(
                {
                    "legs": [leg_a, leg_b],
                    "ako": ako,
                    "stake_pln": stake_pln,
                    "potential_return": round(stake_pln * ako, 2),
                    "quality": round(avg_q, 1),
                }
            )
            used_events.add(leg_a["event_id"])
            used_events.add(leg_b["event_id"])

    for leg_a, leg_b in combinations(ranked_picks, 2):
        if len(coupons) >= max_coupons:
            break
        if leg_a["odds"] < MIN_LEG_ODDS or leg_b["odds"] < MIN_LEG_ODDS:
            continue
        if leg_a["event_id"] == leg_b["event_id"]:
            continue

        pair_key = tuple(
            sorted([f"{leg_a['event_id']}:{leg_a['selection']}", f"{leg_b['event_id']}:{leg_b['selection']}"])
        )
        if pair_key in seen_pairs:
            continue

        ako = round(leg_a["odds"] * leg_b["odds"], 2)
        if AKO_MIN <= ako <= AKO_MAX:
            seen_pairs.add(pair_key)
            avg_q = (leg_a["quality_score"] + leg_b["quality_score"]) / 2.0
            stake_pln = choose_stake_pln(ako, avg_q)
            coupons.append(
                {
                    "legs": [leg_a, leg_b],
                    "ako": ako,
                    "stake_pln": stake_pln,
                    "potential_return": round(stake_pln * ako, 2),
                    "quality": round(avg_q, 1),
                }
            )

    return coupons


def format_telegram_message(
    session: str, window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]], is_startup_catchup: bool = False
) -> str:
    label_map = {
        "day": "MECZĘ DZIENNE (08:00)",
        "night": "NOCKA (20:00)",
    }
    title_label = label_map.get(session, session.upper())
    if is_startup_catchup and session == "night":
        title_label = "NOCKA — WYJĄTKOWY ZRZUT NA START"

    header = (
        f"🔥🏀🏒🎾 *PACZKA AKO2 — {title_label}*\n"
        f"🕒 Okno: `{window_start.strftime('%d.%m %H:%M')}` – `{window_end.strftime('%d.%m %H:%M')}` ({TZ_NAME})\n"
        f"🎯 AKO: `{AKO_MIN:.2f} – {AKO_MAX:.2f}` | Min. kurs nogi: `{MIN_LEG_ODDS:.2f}` | Stawki: `{STAKE_LOW} / {STAKE_MID} / {STAKE_HIGH} PLN` (Betclic)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return header + f"⚠️ Brak par AKO2 w widełkach `{AKO_MIN:.2f}–{AKO_MAX:.2f}` (min. `{MIN_LEG_ODDS:.2f}`/noga) w tym oknie."

    total_stake = sum(c["stake_pln"] for c in coupons)
    blocks = [header]

    for idx, c in enumerate(coupons, start=1):
        lines = [
            f"*KUPON #{idx} (AKO2)* | Kurs: *{c['ako']:.2f}* | Stawka: *{c['stake_pln']} PLN* "
            f"| Wygrana: *{c['potential_return']:.2f} PLN* | Q: `{c['quality']}`"
        ]
        for leg in c["legs"]:
            godz = leg["commence_local"].strftime("%H:%M")
            lines.append(
                f"• [{leg['group_label']}] *{leg['sport_title']}* — `{godz}`\n"
                f"  `{leg['home']} vs {leg['away']}`\n"
                f"  Typ: *{leg['selection']}* @ *{leg['odds']:.2f}* (śr. rynku `{leg['avg_odds']:.2f}`, {leg['bookmaker']})"
            )
        blocks.append("\n".join(lines))

    blocks.append(f"━━━━━━━━━━━━━━━━━━━━\n💰 Łączna ekspozycja paczki: *{total_stake} PLN*")
    return "\n\n".join(blocks)


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    logger.info("Wysłano paczkę AKO2 na Telegram (chat_id=%s).", TELEGRAM_CHAT_ID)


def run_session(session: str, is_startup_catchup: bool = False) -> None:
    now_local = datetime.now(TZ)
    window_start, window_end = get_session_window(session, now_local)
    logger.info("Start sesji [%s]: %s -> %s", session, window_start, window_end)

    sports = fetch_all_target_sports(session)
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

        picks = extract_quality_picks(
            events=events,
            session=session,
            sport_key=s_key,
            sport_title=s_title,
            group_label=group_label,
            window_start=window_start,
            window_end=window_end,
        )
        all_picks.extend(picks)

    logger.info("Zebrano %d quality picków w sesji [%s].", len(all_picks), session)
    coupons = build_ako2_coupons(all_picks, max_coupons=MAX_COUPONS)
    message = format_telegram_message(session, window_start, window_end, coupons, is_startup_catchup=is_startup_catchup)
    send_telegram(message)


def main() -> None:
    if START_NIGHT_NOW:
        logger.info("Wyjątkowy zrzut paczki na nockę na start...")
        try:
            run_session("night", is_startup_catchup=True)
        except Exception as exc:
            logger.exception("Błąd przy zrzucie startowym na nockę: %s", exc)

    schedule_map = {
        (8, 0): "day",
        (20, 0): "night",
    }
    fired_today = set()
    last_date = None

    logger.info("Harmonogram aktywny: 08:00 (mecze dzienne) oraz 20:00 (nocka) w strefie %s.", TZ_NAME)
    while True:
        now_local = datetime.now(TZ)
        if now_local.date() != last_date:
            fired_today.clear()
            last_date = now_local.date()

        slot = (now_local.hour, now_local.minute)
        if slot in schedule_map and slot not in fired_today:
            fired_today.add(slot)
            session_to_run = schedule_map[slot]
            try:
                run_session(session_to_run, is_startup_catchup=False)
            except Exception as exc:
                logger.exception("Błąd podczas sesji %s (%s): %s", session_to_run, slot, exc)

        pytime.sleep(20)


if __name__ == "__main__":
    main()
