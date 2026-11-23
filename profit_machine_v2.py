import os
import json
import time as pytime
import logging
from datetime import datetime, timedelta, time
from itertools import combinations
from statistics import mean
from typing import List, Dict, Any, Tuple, Set
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("PITOS_BOT")

# Klucz API (możesz też nadpisać zmienną środowiskową ODDS_API_KEY)
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "a2b9167ee71a696f4c0ebb1943613714").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Od razu puszcza paczkę testową na start, a potem pilnuje 08:00 (dzień) i 20:00 (noc)
START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")

# Plik przechowujący event_id już użytych meczów w aktualnej sesji
SESSION_STATE_FILE = os.getenv("SESSION_STATE_FILE", "session_used_events.json").strip()

# Widełki pod AKO2 oraz AKO3 — twarde minimum 1.20 na każde zdarzenie, maksymalnie 6 kuponów na paczkę
AKO_MIN = float(os.getenv("AKO_MIN", "1.85"))
AKO_MAX = float(os.getenv("AKO_MAX", "6.50"))
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.20"))
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "3.80"))
MAX_COUPONS = int(os.getenv("MAX_COUPONS_PER_RUN", "6"))

STAKE_LOW = int(os.getenv("STAKE_LOW", "25"))
STAKE_MID = int(os.getenv("STAKE_MID", "50"))
STAKE_HIGH = int(os.getenv("STAKE_HIGH", "75"))

# Docelowe dyscypliny: koszykówka, hokej, tenis, futbol amerykański i esport
TARGET_GROUPS = {
    "basketball": "Koszykówka",
    "icehockey": "Hokej",
    "tennis": "Tenis",
    "americanfootball": "Futbol amerykański",
    "esports": "Esport",
}

ESPORT_KEYWORDS = (
    "esport",
    "counter-strike",
    "cs2",
    "cs:go",
    "league of legends",
    "lol",
    "dota",
    "valorant",
    "overwatch",
    "call of duty",
    "rainbow six",
    "rocket league",
)

BASE_URL = "https://api.the-odds-api.com/v4"


def detect_group_label(group_raw: str, sport_key: str, sport_title: str) -> str | None:
    """Dopasowuje dyscyplinę do docelowej puli: kosz, hokej, tenis, futbol amerykański, esport."""
    grp = (group_raw or "").lower()
    key = (sport_key or "").lower()
    title = (sport_title or "").lower()
    combined = f"{grp} {key} {title}"

    for k, label in TARGET_GROUPS.items():
        if k in grp or key.startswith(k):
            return label

    if "american" in combined or "nfl" in combined or "ncaaf" in combined:
        return "Futbol amerykański"

    if any(kw in combined for kw in ESPORT_KEYWORDS):
        return "Esport"

    return None


def resolve_current_session(now_local: datetime) -> str:
    """Określa bieżącą sesję na podstawie godziny lokalnej: do 19:59 -> 'day', od 20:00 -> 'night'."""
    if now_local.hour < 20:
        return "day"
    return "night"


def get_session_key(session: str, now_local: datetime) -> str:
    """Unikalny klucz sesji dla danego dnia i typu sesji (day / night)."""
    return f"{now_local.date().isoformat()}_{session}"


def load_session_used_events(session_key: str) -> Set[str]:
    """Wczytuje event_id meczów już użytych w tej samej sesji."""
    if not os.path.exists(SESSION_STATE_FILE):
        return set()
    try:
        with open(SESSION_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("session_key") == session_key and isinstance(data.get("used_event_ids"), list):
            return set(str(x) for x in data["used_event_ids"])
    except Exception as exc:
        logger.warning("Nie udało się wczytać stanu sesji: %s", exc)
    return set()


def save_session_used_events(session_key: str, used_event_ids: Set[str]) -> None:
    """Zapisuje event_id meczów użytych w bieżącej sesji."""
    try:
        with open(SESSION_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "session_key": session_key,
                    "used_event_ids": sorted(used_event_ids),
                    "updated_at": datetime.now(TZ).isoformat(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        logger.warning("Nie udało się zapisać stanu sesji: %s", exc)


def get_session_window(session: str, now_local: datetime) -> Tuple[datetime, datetime]:
    """
    - 'day': od teraz do 21:30 tego samego dnia.
    - 'night': od teraz do 08:30 kolejnego dnia.
    """
    today = now_local.date()

    if session == "day":
        start = now_local
        end = datetime.combine(today, time(21, 30), tzinfo=TZ)
        if end <= start:
            end = start + timedelta(hours=3)
        return start, end

    start = now_local
    tomorrow = today + timedelta(days=1)
    end = datetime.combine(tomorrow, time(8, 30), tzinfo=TZ)
    return start, end


def fetch_all_target_sports() -> List[Tuple[Dict[str, Any], str]]:
    """Pobiera aktywne rozgrywki dla koszykówki, hokeja, tenisa, futbolu amerykańskiego i esportu."""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    sports = resp.json()

    matched: List[Tuple[Dict[str, Any], str]] = []
    for s in sports:
        group_raw = s.get("group") or ""
        key = s.get("key") or ""
        title = s.get("title") or key
        label = detect_group_label(group_raw, key, title)
        if label:
            matched.append((s, label))

    logger.info("Aktywne ligi/turnieje (kosz/hokej/tenis/NFL/esport): %d", len(matched))
    return matched


def fetch_events_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    """Pobiera mecze i kursy h2h dla wskazanej ligi/turnieju z szerokiego zestawu regionów."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk,us,au",
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
    blocked_event_ids: Set[str],
) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []

    for ev in events:
        ev_id = str(ev.get("id") or "")
        if not ev_id or ev_id in blocked_event_ids:
            continue

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
                    "event_id": ev_id,
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
    if legs_count == 2 and ako <= 2.40 and avg_quality >= 115.0:
        return STAKE_HIGH
    if legs_count == 2 and ako <= 3.10:
        return STAKE_MID
    if legs_count == 3 and ako <= 3.60 and avg_quality >= 115.0:
        return STAKE_MID
    return STAKE_LOW


def build_ako_coupons(
    picks: List[Dict[str, Any]],
    max_coupons: int = MAX_COUPONS,
    already_used_in_session: Set[str] | None = None,
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """
    Buduje maksymalnie max_coupons (domyślnie 6) kuponów AKO2 oraz AKO3:
    - ZERO powtórek meczów w paczce oraz ZERO meczów już użytych w tej sesji.
    """
    blocked = set(already_used_in_session or set())
    valid_picks = [p for p in picks if p["odds"] >= MIN_LEG_ODDS and p["event_id"] not in blocked]

    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(valid_picks, key=lambda x: x["quality_score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event:
            best_per_event[eid] = p

    ranked_picks = sorted(best_per_event.values(), key=lambda x: x["quality_score"], reverse=True)
    candidates: List[Dict[str, Any]] = []

    for legs_count in (2, 3):
        if len(ranked_picks) < legs_count:
            continue
        for combo in combinations(ranked_picks, legs_count):
            if any(leg["odds"] < MIN_LEG_ODDS for leg in combo):
                continue

            ako = 1.0
            for leg in combo:
                ako *= leg["odds"]
            ako = round(ako, 2)

            if AKO_MIN <= ako <= AKO_MAX:
                avg_q = round(mean(leg["quality_score"] for leg in combo), 1)
                stake_pln = choose_stake_pln(ako, avg_q, legs_count)
                candidates.append(
                    {
                        "legs": list(combo),
                        "legs_count": legs_count,
                        "ako": ako,
                        "stake_pln": stake_pln,
                        "potential_return": round(stake_pln * ako, 2),
                        "quality": avg_q,
                        "event_ids": {leg["event_id"] for leg in combo},
                    }
                )

    candidates.sort(key=lambda c: c["quality"], reverse=True)

    selected_coupons: List[Dict[str, Any]] = []
    used_in_run: Set[str] = set()

    for cand in candidates:
        if len(selected_coupons) >= max_coupons:
            break
        if cand["event_ids"] & blocked or cand["event_ids"] & used_in_run:
            continue

        selected_coupons.append(
            {
                "legs": cand["legs"],
                "legs_count": cand["legs_count"],
                "ako": cand["ako"],
                "stake_pln": cand["stake_pln"],
                "potential_return": cand["potential_return"],
                "quality": cand["quality"],
            }
        )
        used_in_run.update(cand["event_ids"])

    return selected_coupons, used_in_run


def format_telegram_message(
    session: str, window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]], is_test_run: bool = False
) -> str:
    label_map = {
        "day": "SESJA DZIEŃ",
        "night": "SESJA NOC",
    }
    title_label = label_map.get(session, session.upper())
    if is_test_run:
        title_label = f"{title_label} — TEST (NOWA PULA)"

    header = (
        f"🔥🏀🏒🎾🏈🎮 *PACZKA AKO2 / AKO3 — {title_label}*\n"
        f"🕒 Okno: `{window_start.strftime('%d.%m %H:%M')}` – `{window_end.strftime('%d.%m %H:%M')}` ({TZ_NAME})\n"
        f"🎯 AKO: `{AKO_MIN:.2f} – {AKO_MAX:.2f}` | Min. kurs: `{MIN_LEG_ODDS:.2f}` | Max kuponów: `{MAX_COUPONS}` (bez powtórek)\n"
        f"💰 Stawki: `{STAKE_LOW} / {STAKE_MID} / {STAKE_HIGH} PLN`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return (
            header
            + f"⚠️ Brak nowych, nieużytych jeszcze w tej sesji kuponów AKO2/AKO3 w widełkach `{AKO_MIN:.2f}–{AKO_MAX:.2f}`."
        )

    ako2_cnt = sum(1 for c in coupons if c["legs_count"] == 2)
    ako3_cnt = sum(1 for c in coupons if c["legs_count"] == 3)
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

    blocks.append(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Skład: *{ako2_cnt}x AKO2* + *{ako3_cnt}x AKO3* | Łącznie kuponów: *{len(coupons)}* (unikalne mecze)\n"
        f"💰 Łączna ekspozycja paczki: *{total_stake} PLN*"
    )
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


def run_session(session: str, is_test_run: bool = False) -> None:
    now_local = datetime.now(TZ)
    session_key = get_session_key(session, now_local)
    used_in_session = load_session_used_events(session_key)

    window_start, window_end = get_session_window(session, now_local)
    logger.info(
        "Start sesji [%s] (%s): %s -> %s | Już użytych meczów w sesji: %d",
        session,
        session_key,
        window_start,
        window_end,
        len(used_in_session),
    )

    sports = fetch_all_target_sports()
    all_picks: List[Dict[str, Any]] = []

    for s, group_label in sports:
        s_key = s.get("key", "")
        s_title = s.get("title", s_key)

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
            blocked_event_ids=used_in_session,
        )
        all_picks.extend(picks)

    logger.info("Zebrano %d nowych quality picków w sesji [%s].", len(all_picks), session)
    coupons, newly_used = build_ako_coupons(
        all_picks,
        max_coupons=MAX_COUPONS,
        already_used_in_session=used_in_session,
    )
    message = format_telegram_message(session, window_start, window_end, coupons, is_test_run=is_test_run)
    send_telegram(message)

    if newly_used:
        updated_used = used_in_session | newly_used
        save_session_used_events(session_key, updated_used)
        logger.info("Zapisano %d użytych meczów dla sesji %s.", len(updated_used), session_key)


def main() -> None:
    now_init = datetime.now(TZ)
    current_session = resolve_current_session(now_init)

    if START_NOW:
        logger.info("START_NOW=true — wysyłka testowa od razu dla bieżącej sesji [%s]...", current_session)
        try:
            run_session(current_session, is_test_run=True)
        except Exception as exc:
            logger.exception("Błąd przy wysyłce testowej: %s", exc)
    else:
        logger.info("Start w trybie oczekiwania. Planowo: 08:00 (dzień) i 20:00 (noc).")

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
                run_session(session_to_run, is_test_run=False)
            except Exception as exc:
                logger.exception("Błąd podczas sesji %s (%s): %s", session_to_run, slot, exc)

        pytime.sleep(20)


if __name__ == "__main__":
    main()
