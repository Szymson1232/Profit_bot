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
logger = logging.getLogger("PITOS_VIP_BOT")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "e88171c0d8a0108a310ab634acdfe4ef").strip()
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Warsaw")
TZ = ZoneInfo(TZ_NAME)

# Na start odpala bieżącą sesję, żeby od razu wygenerować paczkę pod dzisiejszy dzień
START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")

SESSION_STATE_FILE = os.getenv("SESSION_STATE_FILE", "session_used_events.json").strip()

# Bankroll / jednostki VIP: 1j = 25 PLN, drabinka 1j / 2j / 3j / 4j
UNIT_PLN = int(os.getenv("UNIT_PLN", "25"))
MIN_UNITS = int(os.getenv("MIN_UNITS", "1"))
MAX_UNITS = int(os.getenv("MAX_UNITS", "4"))

# Limity kuponów na sesję: min 3, max 10 (poniżej min 3 -> PASS)
MIN_COUPONS = int(os.getenv("MIN_COUPONS_PER_RUN", "3"))
MAX_COUPONS = int(os.getenv("MAX_COUPONS_PER_RUN", "10"))

# Zasady kursowe pod skuteczność i regularny zysk (bez podatku)
SINGLE_MIN_ODDS = float(os.getenv("SINGLE_MIN_ODDS", "1.70"))
SINGLE_MAX_ODDS = float(os.getenv("SINGLE_MAX_ODDS", "2.95"))
AKO3_MIN_ODDS = float(os.getenv("AKO3_MIN_ODDS", "2.00"))
AKO_MAX_ODDS = float(os.getenv("AKO_MAX_ODDS", "5.50"))
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.22"))
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "3.10"))

# Filtry jakości i bezpieczeństwa (ochrona przed fałszywym edge i dużą wariancją)
MIN_BOOKMAKERS = int(os.getenv("MIN_BOOKMAKERS", "3"))
MIN_EDGE_RATIO = float(os.getenv("MIN_EDGE_RATIO", "1.04"))
MAX_EDGE_RATIO = float(os.getenv("MAX_EDGE_RATIO", "1.22"))
STRONG_EDGE_RATIO = float(os.getenv("STRONG_EDGE_RATIO", "1.08"))
PEREŁKA_EDGE_RATIO = float(os.getenv("PERELKA_EDGE_RATIO", "1.12"))
MIN_SINGLE_QUALITY = float(os.getenv("MIN_SINGLE_QUALITY", "115.0"))
MIN_COUPON_QUALITY = float(os.getenv("MIN_COUPON_QUALITY", "114.0"))
MAX_PER_SPORT_HOUR = int(os.getenv("MAX_PER_SPORT_HOUR", "2"))

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
    return "day" if now_local.hour < 20 else "night"


def get_session_key(session: str, now_local: datetime) -> str:
    return f"{now_local.date().isoformat()}_{session}"


def load_session_used_events(session_key: str) -> Set[str]:
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

    logger.info("Aktywne ligi/turnieje VIP: %d", len(matched))
    return matched


def fetch_events_for_sport(sport_key: str) -> List[Dict[str, Any]]:
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


def evaluate_pick_value(best_odds: float, all_odds: List[float]) -> Tuple[float, float, float]:
    """
    Liczy jakość i edge względem konsensusu innych bukmacherów z premią za większe prawdopodobieństwo.
    Ucinamy nadmiernie rozstrzelone kursy (podejrzane linie / różne zasady rynków).
    """
    avg_odds = mean(all_odds) if all_odds else best_odds
    if len(all_odds) >= 2:
        peers = sorted(all_odds)[:-1]
        ref_odds = median(peers) if peers else avg_odds
    else:
        ref_odds = avg_odds

    edge_ratio = (best_odds / ref_odds) if ref_odds > 0 else 1.0
    implied_prob = 1.0 / best_odds if best_odds > 0 else 0.0
    # Premia za wyższe implied_prob, żeby promować skuteczność, a nie ślepe polowanie na długie kursy
    quality_score = round((edge_ratio * 92.0) + (implied_prob * 48.0), 2)
    return quality_score, round(edge_ratio, 3), round(avg_odds, 2)


def extract_vip_picks(
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
            if len(entries) < MIN_BOOKMAKERS:
                continue

            best_price, best_bm = max(entries, key=lambda x: x[0])
            all_prices = [p for p, _ in entries]
            quality_score, edge_ratio, avg_odds = evaluate_pick_value(best_price, all_prices)

            if not (MIN_EDGE_RATIO <= edge_ratio <= MAX_EDGE_RATIO):
                continue

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
                    "avg_odds": avg_odds,
                    "bookmaker": best_bm,
                    "commence_local": commence_local,
                    "quality_score": quality_score,
                    "edge_ratio": edge_ratio,
                }
            )

    return picks


def choose_units_vip(
    coupon_type: str, total_odds: float, avg_quality: float, max_edge: float, avg_edge: float
) -> int:
    """
    Konserwatywny dobór jednostek (1j..4j, 1j = 25 PLN) pod skuteczność i regularny zysk:
    - wysokie kursy (>= 2.80) schodzą do 1j, żeby nie topić bankrolla na wariancji,
    - 3j / 4j tylko dla wyraźnych przewag przy rozsądnym kursie i wysokiej jakości.
    """
    if total_odds >= 2.80:
        return 1

    units = 1
    if coupon_type == "SINGLE":
        if total_odds <= 2.25 and max_edge >= PEREŁKA_EDGE_RATIO and avg_quality >= 125.0:
            units = 4
        elif total_odds <= 2.45 and max_edge >= STRONG_EDGE_RATIO and avg_quality >= 120.0:
            units = 3
        elif avg_quality >= MIN_SINGLE_QUALITY:
            units = 2
    elif coupon_type == "AKO2":
        if total_odds <= 2.65 and avg_edge >= STRONG_EDGE_RATIO and avg_quality >= 121.0:
            units = 3
        elif avg_quality >= MIN_COUPON_QUALITY:
            units = 2
        else:
            units = 1
    elif coupon_type == "AKO3":
        if total_odds <= 3.10 and avg_edge >= STRONG_EDGE_RATIO and avg_quality >= 119.0:
            units = 2
        else:
            units = 1

    return max(MIN_UNITS, min(MAX_UNITS, units))


def _respects_sport_hour_cap(legs: List[Dict[str, Any]], max_per_slot: int = MAX_PER_SPORT_HOUR) -> bool:
    counts: Dict[Tuple[str, str], int] = {}
    for leg in legs:
        slot_key = (leg["sport_key"], leg["commence_local"].strftime("%Y-%m-%d %H"))
        counts[slot_key] = counts.get(slot_key, 0) + 1
        if counts[slot_key] > max_per_slot:
            return False
    return True


def build_vip_coupons(
    picks: List[Dict[str, Any]],
    min_coupons: int = MIN_COUPONS,
    max_coupons: int = MAX_COUPONS,
    already_used_in_session: Set[str] | None = None,
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """
    Buduje paczkę VIP (SINGLE 1.70–2.95, AKO2, AKO3 2.00+) z unikalnych meczów.
    Jeśli sensownych kuponów jest mniej niż min_coupons (domyślnie 3), zwraca PASS.
    """
    blocked = set(already_used_in_session or set())
    valid_picks = [
        p
        for p in picks
        if p["event_id"] not in blocked and (MIN_EDGE_RATIO <= p["edge_ratio"] <= MAX_EDGE_RATIO)
    ]

    best_per_event: Dict[str, Dict[str, Any]] = {}
    for p in sorted(valid_picks, key=lambda x: x["quality_score"], reverse=True):
        eid = p["event_id"]
        if eid not in best_per_event:
            best_per_event[eid] = p

    ranked_picks = sorted(best_per_event.values(), key=lambda x: x["quality_score"], reverse=True)
    candidates: List[Dict[str, Any]] = []

    # 1. Single w przedziale 1.70 - 2.95
    for p in ranked_picks:
        if SINGLE_MIN_ODDS <= p["odds"] <= SINGLE_MAX_ODDS and p["quality_score"] >= MIN_SINGLE_QUALITY:
            units = choose_units_vip("SINGLE", p["odds"], p["quality_score"], p["edge_ratio"], p["edge_ratio"])
            stake_pln = units * UNIT_PLN
            candidates.append(
                {
                    "type": "SINGLE",
                    "label": "SINGIEL",
                    "legs": [p],
                    "odds": p["odds"],
                    "units": units,
                    "stake_pln": stake_pln,
                    "potential_return": round(stake_pln * p["odds"], 2),
                    "quality": p["quality_score"],
                    "max_edge": p["edge_ratio"],
                    "event_ids": {p["event_id"]},
                }
            )

    # 2. AKO2
    if len(ranked_picks) >= 2:
        for leg_a, leg_b in combinations(ranked_picks, 2):
            if not _respects_sport_hour_cap([leg_a, leg_b]):
                continue
            ako = round(leg_a["odds"] * leg_b["odds"], 2)
            if not (1.55 <= ako <= AKO_MAX_ODDS):
                continue
            avg_q = round(mean([leg_a["quality_score"], leg_b["quality_score"]]), 1)
            avg_edge = round(mean([leg_a["edge_ratio"], leg_b["edge_ratio"]]), 3)
            max_edge = max(leg_a["edge_ratio"], leg_b["edge_ratio"])
            if avg_q < MIN_COUPON_QUALITY:
                continue
            units = choose_units_vip("AKO2", ako, avg_q, max_edge, avg_edge)
            stake_pln = units * UNIT_PLN
            candidates.append(
                {
                    "type": "AKO2",
                    "label": "AKO2",
                    "legs": [leg_a, leg_b],
                    "odds": ako,
                    "units": units,
                    "stake_pln": stake_pln,
                    "potential_return": round(stake_pln * ako, 2),
                    "quality": avg_q,
                    "max_edge": max_edge,
                    "event_ids": {leg_a["event_id"], leg_b["event_id"]},
                }
            )

    # 3. AKO3 (łączny kurs >= 2.00)
    if len(ranked_picks) >= 3:
        for leg_a, leg_b, leg_c in combinations(ranked_picks, 3):
            if not _respects_sport_hour_cap([leg_a, leg_b, leg_c]):
                continue
            ako = round(leg_a["odds"] * leg_b["odds"] * leg_c["odds"], 2)
            if not (AKO3_MIN_ODDS <= ako <= AKO_MAX_ODDS):
                continue
            avg_q = round(mean([leg_a["quality_score"], leg_b["quality_score"], leg_c["quality_score"]]), 1)
            avg_edge = round(mean([leg_a["edge_ratio"], leg_b["edge_ratio"], leg_c["edge_ratio"]]), 3)
            max_edge = max(leg_a["edge_ratio"], leg_b["edge_ratio"], leg_c["edge_ratio"])
            if avg_q < MIN_COUPON_QUALITY:
                continue
            units = choose_units_vip("AKO3", ako, avg_q, max_edge, avg_edge)
            stake_pln = units * UNIT_PLN
            candidates.append(
                {
                    "type": "AKO3",
                    "label": "AKO3",
                    "legs": [leg_a, leg_b, leg_c],
                    "odds": ako,
                    "units": units,
                    "stake_pln": stake_pln,
                    "potential_return": round(stake_pln * ako, 2),
                    "quality": avg_q,
                    "max_edge": max_edge,
                    "event_ids": {leg_a["event_id"], leg_b["event_id"], leg_c["event_id"]},
                }
            )

    candidates.sort(key=lambda c: (c["quality"], -c["odds"], c["max_edge"]), reverse=True)

    selected_coupons: List[Dict[str, Any]] = []
    used_in_run: Set[str] = set()
    slot_used_count: Dict[Tuple[str, str], int] = {}

    for cand in candidates:
        if len(selected_coupons) >= max_coupons:
            break
        if cand["event_ids"] & blocked or cand["event_ids"] & used_in_run:
            continue

        temp_counts = dict(slot_used_count)
        slot_ok = True
        for leg in cand["legs"]:
            s_key = (leg["sport_key"], leg["commence_local"].strftime("%Y-%m-%d %H"))
            temp_counts[s_key] = temp_counts.get(s_key, 0) + 1
            if temp_counts[s_key] > MAX_PER_SPORT_HOUR:
                slot_ok = False
                break
        if not slot_ok:
            continue

        selected_coupons.append(
            {
                "type": cand["type"],
                "label": cand["label"],
                "legs": cand["legs"],
                "odds": cand["odds"],
                "units": cand["units"],
                "stake_pln": cand["stake_pln"],
                "potential_return": cand["potential_return"],
                "quality": cand["quality"],
                "max_edge": cand["max_edge"],
            }
        )
        used_in_run.update(cand["event_ids"])
        slot_used_count = temp_counts

    if len(selected_coupons) < min_coupons:
        logger.info(
            "Znaleziono tylko %d jakościowych kuponów (wymagane min. %d) -> PASS.",
            len(selected_coupons),
            min_coupons,
        )
        return [], set()

    return selected_coupons, used_in_run


def format_vip_telegram_message(
    session: str, window_start: datetime, window_end: datetime, coupons: List[Dict[str, Any]]
) -> str:
    session_label = "SESJA DZIEŃ" if session == "day" else "SESJA NOC"

    header = (
        f"💎🔥 *VIP SELEKCJA — {session_label}*\n"
        f"🕒 Okno: `{window_start.strftime('%d.%m %H:%M')}` – `{window_end.strftime('%d.%m %H:%M')}` ({TZ_NAME})\n"
        f"🎯 Single `{SINGLE_MIN_ODDS:.2f}–{SINGLE_MAX_ODDS:.2f}` | AKO2 | AKO3 `{AKO3_MIN_ODDS:.2f}+` | `1j = {UNIT_PLN} PLN` (`1j–4j`)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not coupons:
        return (
            header
            + f"🛑 *PASS* — brak minimum `{MIN_COUPONS}` pewnych, jakościowych kuponów z czystym value w tym oknie. "
            f"Chronimy bankroll."
        )

    total_units = sum(c["units"] for c in coupons)
    total_stake = sum(c["stake_pln"] for c in coupons)
    blocks = [header]

    for idx, c in enumerate(coupons, start=1):
        lines = [
            f"*VIP #{idx} ({c['label']})* | Kurs: *{c['odds']:.2f}* | Stawka: *{c['units']}j ({c['stake_pln']} PLN)* "
            f"| Wygrana: *{c['potential_return']:.2f} PLN* | Q: `{c['quality']}` | Edge: `x{c['max_edge']:.2f}`"
        ]
        for leg in c["legs"]:
            godz = leg["commence_local"].strftime("%H:%M")
            lines.append(
                f"• [{leg['group_label']}] *{leg['sport_title']}* — `{godz}`\n"
                f"  `{leg['home']} vs {leg['away']}`\n"
                f"  Typ: *{leg['selection']}* @ *{leg['odds']:.2f}* (śr. `{leg['avg_odds']:.2f}`, edge `x{leg['edge_ratio']:.2f}`, {leg['bookmaker']})"
            )
        blocks.append("\n".join(lines))

    blocks.append(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Kuponów: *{len(coupons)}* (min. `{MIN_COUPONS}` / max. `{MAX_COUPONS}`, bez powtórek)\n"
        f"💰 Łączna ekspozycja: *{total_units}j ({total_stake} PLN)*"
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
    logger.info("Wysłano paczkę VIP na Telegram (chat_id=%s).", TELEGRAM_CHAT_ID)


def run_session(session: str) -> None:
    now_local = datetime.now(TZ)
    session_key = get_session_key(session, now_local)
    used_in_session = load_session_used_events(session_key)

    window_start, window_end = get_session_window(session, now_local)
    logger.info(
        "Start sesji VIP [%s] (%s): %s -> %s | Już użytych meczów w sesji: %d",
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

        picks = extract_vip_picks(
            events=events,
            sport_key=s_key,
            sport_title=s_title,
            group_label=group_label,
            window_start=window_start,
            window_end=window_end,
            blocked_event_ids=used_in_session,
        )
        all_picks.extend(picks)

    logger.info("Zebrano %d kandydatów VIP w sesji [%s].", len(all_picks), session)
    coupons, newly_used = build_vip_coupons(
        all_picks,
        min_coupons=MIN_COUPONS,
        max_coupons=MAX_COUPONS,
        already_used_in_session=used_in_session,
    )
    message = format_vip_telegram_message(session, window_start, window_end, coupons)
    send_telegram(message)

    if newly_used:
        updated_used = used_in_session | newly_used
        save_session_used_events(session_key, updated_used)
        logger.info("Zapisano %d użytych meczów dla sesji %s.", len(updated_used), session_key)


def main() -> None:
    now_init = datetime.now(TZ)
    current_session = resolve_current_session(now_init)

    if START_NOW:
        logger.info("START_NOW=true — od razu odpalam sesję [%s]...", current_session)
        try:
            run_session(current_session)
        except Exception as exc:
            logger.exception("Błąd przy wysyłce startowej: %s", exc)
    else:
        logger.info("Start w trybie harmonogramu. Planowo: 08:00 (dzień) i 20:00 (noc).")

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
