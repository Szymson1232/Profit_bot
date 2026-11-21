import os
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
logger = logging.getLogger("PITOS_SOBOTA")

# Dane z wcześniejszych rozmów (możesz nadpisać zmiennymi środowiskowymi)
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "133d53ed2236d1e09849deabd16155e6").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Poluzowane widełki pod sobotnią ofertę i budowanie paczki AKO2
AKO_MIN = float(os.getenv("AKO_MIN", "1.85"))
AKO_MAX = float(os.getenv("AKO_MAX", "3.60"))
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.12"))
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "3.80"))
MAX_COUPONS = int(os.getenv("MAX_COUPONS_PER_RUN", "5"))

# Stawki w PLN pod Betclic (bez podatku)
STAKE_LOW = int(os.getenv("STAKE_LOW", "25"))
STAKE_MID = int(os.getenv("STAKE_MID", "50"))
STAKE_HIGH = int(os.getenv("STAKE_HIGH", "75"))

TARGET_GROUPS = {
    "basketball": "Koszykówka",
    "icehockey": "Hokej",
    "tennis": "Tenis",
}

# Twarda blokada NBA, NHL i pokrewnych rozgrywek w sesji dziennej
BLOCKED_KEYWORDS = (
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


def is_forbidden_day_league(sport_key: str, sport_title: str, commence_local: datetime) -> bool:
    """Blokuje NBA/NHL/itp. oraz nocne okienka północnoamerykańskie w sesji dziennej."""
    text = f"{sport_key} {sport_title}".lower()
    if any(bad in text for bad in BLOCKED_KEYWORDS):
        return True
    if any(tag in text for tag in ("usa", "north america", "canada")) and commence_local.hour >= 16:
        return True
    return False


def fetch_all_target_sports() -> List[Dict[str, Any]]:
    """Pobiera wszystkie aktywne rozgrywki dla koszykówki, hokeja i tenisa (bez NBA i NHL)."""
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
        if any(bad in f"{key} {title}" for bad in BLOCKED_KEYWORDS):
            continue
        matched.append(s)

    logger.info("Aktywne ligi/turnieje (bez NBA/NHL): %d", len(matched))
    return matched


def get_day_session_window(now_local: datetime) -> Tuple[datetime, datetime]:
    """Okno sesji dziennej: od teraz do 21:30 czasu lokalnego."""
    today = now_local.date()
    start = now_local
    end = datetime.combine(today, time(21, 30), tzinfo=TZ)
    if end <= start:
        end = min(
            now_local + timedelta(hours=3),
            datetime.combine(today, time(22, 30), tzinfo=TZ),
        )
    return start, end


def fetch_events_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    """Pobiera mecze i kursy h2h dla wskazanej ligi/turnieju."""
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
        logger.warning("Błąd dla %s: %s", sport_key, exc)
        return []


def compute_quality_score(best_odds: float, all_odds: List[float]) -> float:
    """
    Liczy wynik jakości picku:
    - premiuje wyższy kurs niż średnia rynkowa (przewaga nad konsensusem buków),
    - premiuje rozsądne prawdopodobieństwo bazowe (żeby nie grać samych loterii po 3.70).
    """
    avg_odds = mean(all_odds) if all_odds else best_odds
    edge_ratio = (best_odds / avg_odds) if avg_odds > 0 else 1.0
    implied_prob = 1.0 / best_odds if best_odds > 0 else 0.0
    # Balans między przewagą kursową a realną szansą wejścia
    score = (edge_ratio * 100.0) + (implied_prob * 35.0)
    return round(score, 2)


def extract_quality_picks(
    events: List[Dict[str, Any]],
    sport_key: str,
    sport_title: str,
    group_label: str,
    window_start: datetime,
    window_end: datetime,
) -> List[Dict[str, Any]]:
    """Zbiera quality picki z okna dziennego (bez NBA/NHL)."""
    picks: List[Dict[str, Any]] = []

    for ev in events:
        commence_raw = ev.get("commence_time")
        if not commence_raw:
            continue

        commence_local = datetime.fromisoformat(commence_raw.replace("Z", "+00:00")).astimezone(TZ)
        if not (window_start <= commence_local <= window_end):
            continue

        if is_forbidden_day_league(sport_key, sport_title, commence_local):
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
            q_score = compute_quality_score(best_price, all_prices)

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
                    "quality_score": q_score,
                }
            )

    return picks


def choose_stake_pln(ako: float, avg_quality: float) -> int:
    """Dobiera stawkę 25 / 50 / 75 PLN pod kurs AKO2 i jakość par."""
    if ako <= 2.35 and avg_quality >= 115.0:
        return STAKE_HIGH  # 75 PLN
    if ako <= 2.95:
        return STAKE_MID   # 50 PLN
    return STAKE_LOW       # 25 PLN


def build_ako2_coupons(picks: List[Dict[str, Any]], max_coupons: int = MAX_COUPONS) -> List[Dict[str, Any]]:
    """
    Buduje kupony AKO2 z najlepszych quality picków:
    1) najpierw unikalne mecze,
    2) potem dociąga kolejne pary z puli, żeby nie ucinać paczki na 1 kuponie.
    """
    # Bierzemy najwyższy quality_score dla każdego meczu
    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(picks, key=lambda x: x["quality_score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event:
            best_per_event[eid] = p

    ranked_picks = sorted(best_per_event.values(), key=lambda x: x["quality_score"], reverse=True)
    coupons: List[Dict[str, Any]] = []
    used_events = set()
    seen_pairs = set()

    # Etap 1: osobne mecze dla każdego kuponu
    for leg_a, leg_b in combinations(ranked_picks, 2):
        if len(coupons) >= max_coupons:
            return coupons
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

    # Etap 2: dociąganie brakujących kuponów z pozostałych par
    for leg_a, leg_b in combinations(ranked_picks, 2):
        if len(coupons) >= max_coupons:
            break
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


def format_telegram_message(window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]]) -> str:
    """Formatuje sobotnie kupony AKO2 na Telegram."""
    header = (
        f"🔥🏀🏒🎾 *SOBOTNIA PACZKA AKO2 (BEZ NBA/NHL)*\n"
        f"🕒 Okno dzienne: `{window_start.strftime('%H:%M')}` – `{window_end.strftime('%H:%M')}` ({TZ_NAME})\n"
        f"🎯 AKO: `{AKO_MIN:.2f} – {AKO_MAX:.2f}` | Stawki: `{STAKE_LOW} / {STAKE_MID} / {STAKE_HIGH} PLN` (Betclic)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return (
            header
            + f"⚠️ Brak par AKO2 w widełkach `{AKO_MIN:.2f}–{AKO_MAX:.2f}` w bieżącym oknie dziennym."
        )

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
    """Wysyła wiadomość na Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    logger.info("Wysłano paczkę kuponów AKO2 na Telegram (chat_id=%s).", TELEGRAM_CHAT_ID)


def run_saturday_day_session() -> None:
    """Główny przebieg sobotniej sesji dziennej: koszykówka, hokej, tenis (bez NBA/NHL)."""
    now_local = datetime.now(TZ)
    window_start, window_end = get_day_session_window(now_local)
    logger.info("Start sesji dziennej: %s -> %s", window_start, window_end)

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

    logger.info("Zebrano %d quality picków w oknie dziennym.", len(all_picks))
    coupons = build_ako2_coupons(all_picks, max_coupons=MAX_COUPONS)
    message = format_telegram_message(window_start, window_end, coupons)
    send_telegram(message)


if __name__ == "__main__":
    run_saturday_day_session()
