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

# Nowy klucz API (możesz też nadpisać zmienną środowiskową ODDS_API_KEY)
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "a2b9167ee71a696f4c0ebb1943613714").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Teraz NIE wysyła nic od razu na starcie — czeka na harmonogram (08:00 dzień, 20:00 noc)
START_NOW = os.getenv("START_NOW", "false").strip().lower() in ("1", "true", "yes", "on")

# Widełki pod AKO2 oraz AKO3 — twarde minimum 1.20 na każde zdarzenie
AKO_MIN = float(os.getenv("AKO_MIN", "1.85"))
AKO_MAX = float(os.getenv("AKO_MAX", "6.50"))
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

BASE_URL = "https://api.the-odds-api.com/v4"


def get_session_window(session: str, now_local: datetime) -> Tuple[datetime, datetime]:
    """
    - 'day' (planowo 08:00): od startu do 19:59 tego samego dnia (pełna oferta w tym ligi amerykańskie).
    - 'night' (planowo 20:00): od startu do 08:30 kolejnego dnia.
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


def fetch_all_target_sports() -> List[Dict[str, Any]]:
    """Pobiera wszystkie aktywne rozgrywki dla koszykówki, hokeja i tenisa (w tym ligi amerykańskie)."""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    sports = resp.json()

    matched: List[Dict[str, Any]] = []
    for s in sports:
        group = (s.get("group") or "").lower()
        key = (s.get("key") or "").lower()
        if any(grp in group or key.startswith(grp) for grp in TARGET_GROUPS):
            matched.append(s)

    logger.info("Aktywne ligi/turnieje (kosz / hokej / tenis): %d", len(matched))
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
            if best_price < MIN_LEG_ODDS:
                continue
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


def choose_stake_pln(ako: float, avg_quality: float, legs_count: int) -> int:
    """Dobiera stawkę 25 / 50 / 75 PLN pod kurs AKO, jakość i liczbę zdarzeń (AKO2 / AKO3)."""
    if legs_count == 2 and ako <= 2.40 and avg_quality >= 115.0:
        return STAKE_HIGH
    if ako <= 3.20:
        return STAKE_MID
    return STAKE_LOW


def build_ako_coupons(picks: List[Dict[str, Any]], max_coupons: int = MAX_COUPONS) -> List[Dict[str, Any]]:
    """
    Buduje kupony AKO2 oraz AKO3 z najlepszych quality picków:
    - najpierw unikalne mecze (AKO2 i AKO3),
    - potem dociąganie brakujących kuponów z pozostałych kombinacji.
    """
    valid_picks = [p for p in picks if p["odds"] >= MIN_LEG_ODDS]

    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(valid_picks, key=lambda x: x["quality_score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event:
            best_per_event[eid] = p

    ranked_picks = sorted(best_per_event.values(), key=lambda x: x["quality_score"], reverse=True)
    coupons: List[Dict[str, Any]] = []
    used_events = set()
    seen_combos = set()

    for legs_count in (2, 3):
        if len(ranked_picks) < legs_count:
            continue
        for combo in combinations(ranked_picks, legs_count):
            if len(coupons) >= max_coupons:
                return coupons
            if any(leg["odds"] < MIN_LEG_ODDS for leg in combo):
                continue
            if any(leg["event_id"] in used_events for leg in combo):
                continue

            ako = 1.0
            for leg in combo:
                ako *= leg["odds"]
            ako = round(ako, 2)

            if AKO_MIN <= ako <= AKO_MAX:
                combo_key = tuple(sorted(f"{leg['event_id']}:{leg['selection']}" for leg in combo))
                seen_combos.add(combo_key)
                avg_q = mean(leg["quality_score"] for leg in combo)
                stake_pln = choose_stake_pln(ako, avg_q, legs_count)
                coupons.append(
                    {
                        "legs": list(combo),
                        "legs_count": legs_count,
                        "ako": ako,
                        "stake_pln": stake_pln,
                        "potential_return": round(stake_pln * ako, 2),
                        "quality": round(avg_q, 1),
                    }
                )
                for leg in combo:
                    used_events.add(leg["event_id"])

    for legs_count in (2, 3):
        if len(ranked_picks) < legs_count:
            continue
        for combo in combinations(ranked_picks, legs_count):
            if len(coupons) >= max_coupons:
                break
            if any(leg["odds"] < MIN_LEG_ODDS for leg in combo):
                continue
            event_ids = [leg["event_id"] for leg in combo]
            if len(set(event_ids)) != legs_count:
                continue

            combo_key = tuple(sorted(f"{leg['event_id']}:{leg['selection']}" for leg in combo))
            if combo_key in seen_combos:
                continue

            ako = 1.0
            for leg in combo:
                ako *= leg["odds"]
            ako = round(ako, 2)

            if AKO_MIN <= ako <= AKO_MAX:
                seen_combos.add(combo_key)
                avg_q = mean(leg["quality_score"] for leg in combo)
                stake_pln = choose_stake_pln(ako, avg_q, legs_count)
                coupons.append(
                    {
                        "legs": list(combo),
                        "legs_count": legs_count,
                        "ako": ako,
                        "stake_pln": stake_pln,
                        "potential_return": round(stake_pln * ako, 2),
                        "quality": round(avg_q, 1),
                    }
                )

    return coupons


def format_telegram_message(
    session: str, window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]]
) -> str:
    label_map = {
        "day": "SESJA DZIEŃ (08:00)",
        "night": "SESJA NOC (20:00)",
    }
    title_label = label_map.get(session, session.upper())

    header = (
        f"🔥🏀🏒🎾 *PACZKA AKO2 / AKO3 — {title_label}*\n"
        f"🕒 Okno: `{window_start.strftime('%d.%m %H:%M')}` – `{window_end.strftime('%d.%m %H:%M')}` ({TZ_NAME})\n"
        f"🎯 AKO: `{AKO_MIN:.2f} – {AKO_MAX:.2f}` | Min. kurs: `{MIN_LEG_ODDS:.2f}` | Stawki: `{STAKE_LOW} / {STAKE_MID} / {STAKE_HIGH} PLN`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return header + f"⚠️ Brak kuponów AKO2/AKO3 w widełkach `{AKO_MIN:.2f}–{AKO_MAX:.2f}` (min. `{MIN_LEG_ODDS:.2f}`/zdarzenie) w tym oknie."

    total_stake = sum(c["stake_pln"] for c in coupons)
    blocks = [header]

    for idx, c in enumerate(coupons, start=1):
        ako_tag = f"AKO{c['legs_count']}"
        lines = [
            f"*KUPON #{idx} ({ako_tag})* | Kurs: *{c['ako']:.2f}* | Stawka: *{c['stake_pln']} PLN* "
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
    logger.info("Wysłano paczkę AKO2/AKO3 na Telegram (chat_id=%s).", TELEGRAM_CHAT_ID)


def run_session(session: str) -> None:
    now_local = datetime.now(TZ)
    window_start, window_end = get_session_window(session, now_local)
    logger.info("Start sesji [%s]: %s -> %s", session, window_start, window_end)

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

        picks = extract_quality_picks(
            events=events,
            sport_key=s_key,
            sport_title=s_title,
            group_label=group_label,
            window_start=window_start,
            window_end=window_end,
        )
        all_picks.extend(picks)

    logger.info("Zebrano %d quality picków w sesji [%s].", len(all_picks), session)
    coupons = build_ako_coupons(all_picks, max_coupons=MAX_COUPONS)
    message = format_telegram_message(session, window_start, window_end, coupons)
    send_telegram(message)


def main() -> None:
    if START_NOW:
        logger.info("START_NOW=true — wysyłka od razu...")
        try:
            run_session("day")
        except Exception as exc:
            logger.exception("Błąd przy wysyłce startowej: %s", exc)
    else:
        logger.info("Start w trybie oczekiwania — teraz nic nie wysyłamy. Planowo: 08:00 (dzień) i 20:00 (noc).")

    schedule_map = {
        (8, 0): "day",
        (20, 0): "night",
    }
    fired_today = set()
    last_date = None

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
                run_session(session_to_run)
            except Exception as exc:
                logger.exception("Błąd podczas sesji %s (%s): %s", session_to_run, slot, exc)

        pytime.sleep(20)


if __name__ == "__main__":
    main()
