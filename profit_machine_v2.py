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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BETCLIC_AKO_ENGINE")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "42cd1b4b93652a3a439314841a3ced4b").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Warsaw"))
START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")

DAY_DROP_HOUR = int(os.getenv("DAY_DROP_HOUR", "7"))
DAY_DROP_MINUTE = int(os.getenv("DAY_DROP_MINUTE", "0"))
NIGHT_DROP_HOUR = int(os.getenv("NIGHT_DROP_HOUR", "20"))
NIGHT_DROP_MINUTE = int(os.getenv("NIGHT_DROP_MINUTE", "0"))

DAY_WINDOW_HOURS = int(os.getenv("DAY_WINDOW_HOURS", "16"))
NIGHT_WINDOW_HOURS = int(os.getenv("NIGHT_WINDOW_HOURS", "14"))

STATE_FILE = os.getenv("SESSION_STATE_FILE", "betclic_session_events.json").strip()

UNIT_PLN = int(os.getenv("UNIT_PLN", "25"))
MIN_COUPONS_PER_SESSION = int(os.getenv("MIN_COUPONS_PER_SESSION", "4"))
MAX_COUPONS_PER_SESSION = int(os.getenv("MAX_COUPONS_PER_SESSION", "10"))

# Każde zdarzenie (noga / singiel) musi mieć min. 1.30
LEG_MIN_ODDS = float(os.getenv("LEG_MIN_ODDS", "1.30"))
LEG_MAX_ODDS = float(os.getenv("LEG_MAX_ODDS", "2.15"))

AKO2_MIN_ODDS = float(os.getenv("AKO2_MIN_ODDS", "1.69"))
AKO2_MAX_ODDS = float(os.getenv("AKO2_MAX_ODDS", "4.50"))
AKO3_MIN_ODDS = float(os.getenv("AKO3_MIN_ODDS", "2.19"))
AKO3_MAX_ODDS = float(os.getenv("AKO3_MAX_ODDS", "7.50"))

SINGLE_MIN_ODDS = float(os.getenv("SINGLE_MIN_ODDS", "1.65"))
SINGLE_MAX_ODDS = float(os.getenv("SINGLE_MAX_ODDS", "2.85"))
SINGLE_MIN_EDGE = float(os.getenv("SINGLE_MIN_EDGE", "1.08"))
SINGLE_MIN_HIT_SCORE = float(os.getenv("SINGLE_MIN_HIT_SCORE", "66.0"))

MAX_SUSPICIOUS_SPREAD = float(os.getenv("MAX_SUSPICIOUS_SPREAD", "1.40"))
BASE_URL = "https://api.the-odds-api.com/v4"


def is_allowed_sport(sport: Dict[str, Any]) -> bool:
    key = str(sport.get("key") or "").strip().lower()
    if not key or sport.get("active") is False:
        return False
    return not key.startswith(("horse_racing", "greyhound_racing"))


def prettify_sport(sport: Dict[str, Any]) -> str:
    title = str(sport.get("title") or "").strip()
    group = str(sport.get("group") or "").strip()
    if title and group and group.lower() not in title.lower():
        return f"{group} — {title}"
    return title or str(sport.get("key") or "Sport").replace("_", " ").title()


def session_key_for(mode: str, now_dt: datetime) -> str:
    return f"{now_dt.date().isoformat()}_{mode}"


def load_used_events(sess_key: str) -> Set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("session") == sess_key and isinstance(data.get("used_ids"), list):
            return {str(x) for x in data["used_ids"] if x}
    except Exception as exc:
        logger.warning("Błąd odczytu stanu sesji: %s", exc)
    return set()


def save_used_events(sess_key: str, used_ids: Set[str]) -> None:
    try:
        d = os.path.dirname(STATE_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"session": sess_key, "used_ids": sorted(used_ids)}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        logger.warning("Błąd zapisu stanu sesji: %s", exc)


def parse_dt(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def fetch_sports() -> List[Dict[str, Any]]:
    try:
        r = requests.get(f"{BASE_URL}/sports", params={"apiKey": ODDS_API_KEY, "all": "false"}, timeout=25)
        if r.status_code != 200:
            logger.warning("Błąd pobierania listy sportów: status=%s", r.status_code)
            return []
        data = r.json()
        return [s for s in data if isinstance(s, dict) and is_allowed_sport(s)] if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Wyjątek listy sportów: %s", exc)
        return []


def fetch_odds(sport_key: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "eu,uk,us,au",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=25,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Błąd kursów %s: %s", sport_key, exc)
        return []


def score_leg_quality(best_odds: float, all_odds: List[float]) -> Dict[str, Any]:
    avg_odds = mean(all_odds) if all_odds else best_odds
    if len(all_odds) >= 3:
        ref = median(sorted(all_odds)[:-1])
    elif len(all_odds) == 2:
        ref = sorted(all_odds)[0]
    else:
        ref = best_odds

    edge = (best_odds / ref) if ref > 0 else 1.0
    spread = (max(all_odds) / min(all_odds)) if len(all_odds) >= 2 and min(all_odds) > 0 else 1.0
    implied_prob = (1.0 / best_odds) if best_odds > 0 else 0.0

    hit_score = round((implied_prob * 100.0) + (min(edge, 1.25) * 18.0) - max(0.0, spread - 1.15) * 20.0, 2)
    return {
        "hit_score": hit_score,
        "edge": round(edge, 3),
        "avg": round(avg_odds, 2),
        "spread": round(spread, 2),
        "bookmakers_count": len(all_odds),
    }


def extract_picks(
    events: List[Dict[str, Any]],
    sport_key: str,
    sport_label: str,
    start_dt: datetime,
    end_dt: datetime,
    blocked: Set[str],
    strict: bool = True,
) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    min_odds = LEG_MIN_ODDS
    max_odds = LEG_MAX_ODDS if strict else SINGLE_MAX_ODDS

    for ev in events:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or "").strip()
        if not eid or eid in blocked:
            continue
        dt = parse_dt(ev.get("commence_time"))
        if dt is None or not (start_dt <= dt <= end_dt):
            continue
        home = str(ev.get("home_team") or "").strip()
        away = str(ev.get("away_team") or "").strip()
        if not home or not away:
            continue

        sides: Dict[str, List[Tuple[float, str]]] = {home: [], away: []}
        for bm in ev.get("bookmakers") or []:
            if not isinstance(bm, dict):
                continue
            bm_name = str(bm.get("title") or "Bookmaker")
            for mkt in bm.get("markets") or []:
                if not isinstance(mkt, dict) or mkt.get("key") != "h2h":
                    continue
                for o in mkt.get("outcomes") or []:
                    if not isinstance(o, dict):
                        continue
                    name = o.get("name")
                    price = o.get("price")
                    if name in sides and isinstance(price, (int, float)) and not isinstance(price, bool):
                        p = float(price)
                        if min_odds <= p <= max_odds:
                            sides[name].append((p, bm_name))

        for sel, entries in sides.items():
            if not entries:
                continue
            if strict and len(entries) < 2:
                continue
            best_p, best_bm = max(entries, key=lambda x: x[0])
            if best_p < LEG_MIN_ODDS:
                continue
            info = score_leg_quality(best_p, [x[0] for x in entries])
            if strict and info["spread"] > MAX_SUSPICIOUS_SPREAD:
                continue
            picks.append({
                "event_id": eid,
                "sport_key": sport_key,
                "sport_label": sport_label,
                "home": home,
                "away": away,
                "selection": sel,
                "odds": round(best_p, 2),
                "avg_odds": info["avg"],
                "edge": info["edge"],
                "spread": info["spread"],
                "bookmakers_count": info["bookmakers_count"],
                "bookmaker": best_bm,
                "commence": dt,
                "hit_score": info["hit_score"],
            })

    return picks


def pick_units(ctype: str, odds: float, hit_score: float, max_edge: float, max_spread: float) -> int:
    if max_spread > MAX_SUSPICIOUS_SPREAD:
        return 1
    if ctype == "AKO2":
        if odds <= 2.40 and hit_score >= 66.0:
            return 4
        if odds <= 3.10 and hit_score >= 60.0:
            return 3
        return 2 if hit_score >= 54.0 else 1
    if ctype == "AKO3":
        if odds <= 3.40 and hit_score >= 62.0:
            return 3
        return 2 if hit_score >= 55.0 else 1
    if max_edge >= 1.12 and hit_score >= 70.0 and odds <= 2.30:
        return 3
    if max_edge >= SINGLE_MIN_EDGE and hit_score >= SINGLE_MIN_HIT_SCORE:
        return 2
    return 1


def build_coupon(
    ctype: str, label: str, legs: List[Dict[str, Any]], odds: float, hit_score: float, max_edge: float, max_spread: float
) -> Dict[str, Any]:
    units = pick_units(ctype, odds, hit_score, max_edge, max_spread)
    stake = units * UNIT_PLN
    return {
        "type": ctype,
        "label": label,
        "legs": legs,
        "odds": odds,
        "units": units,
        "stake_pln": stake,
        "potential_return": round(stake * odds, 2),
        "hit_score": hit_score,
        "max_edge": max_edge,
        "event_ids": {x["event_id"] for x in legs},
    }


def compose_session_coupons(picks: List[Dict[str, Any]], blocked: Set[str]) -> Tuple[List[Dict[str, Any]], Set[str]]:
    eligible_picks = [p for p in picks if p["odds"] >= LEG_MIN_ODDS]

    best_by_ev: Dict[str, Dict[str, Any]] = {}
    for p in sorted(eligible_picks, key=lambda x: x["hit_score"], reverse=True):
        eid = p["event_id"]
        if eid not in blocked and eid not in best_by_ev:
            best_by_ev[eid] = p

    ranked = sorted(best_by_ev.values(), key=lambda x: x["hit_score"], reverse=True)
    ako_candidates: List[Dict[str, Any]] = []
    single_candidates: List[Dict[str, Any]] = []

    ako_pool = [p for p in ranked if LEG_MIN_ODDS <= p["odds"] <= LEG_MAX_ODDS]
    for a, b in combinations(ako_pool[:26], 2):
        ako = round(a["odds"] * b["odds"], 2)
        if AKO2_MIN_ODDS <= ako <= AKO2_MAX_ODDS:
            ako_candidates.append(
                build_coupon(
                    "AKO2",
                    "AKO2",
                    [a, b],
                    ako,
                    round(mean([a["hit_score"], b["hit_score"]]), 1),
                    max(a["edge"], b["edge"]),
                    max(a["spread"], b["spread"]),
                )
            )

    for a, b, c in combinations(ako_pool[:22], 3):
        ako = round(a["odds"] * b["odds"] * c["odds"], 2)
        if AKO3_MIN_ODDS <= ako <= AKO3_MAX_ODDS:
            ako_candidates.append(
                build_coupon(
                    "AKO3",
                    "AKO3",
                    [a, b, c],
                    ako,
                    round(mean([a["hit_score"], b["hit_score"], c["hit_score"]]), 1),
                    max(a["edge"], b["edge"], c["edge"]),
                    max(a["spread"], b["spread"], c["spread"]),
                )
            )

    for p in ranked:
        if SINGLE_MIN_ODDS <= p["odds"] <= SINGLE_MAX_ODDS:
            if p["edge"] >= SINGLE_MIN_EDGE and p["hit_score"] >= SINGLE_MIN_HIT_SCORE:
                single_candidates.append(
                    build_coupon("SINGLE", "SINGIEL (VALUE)", [p], p["odds"], p["hit_score"], p["edge"], p["spread"])
                )

    ako_candidates.sort(key=lambda x: (x["hit_score"], -x["odds"]), reverse=True)
    single_candidates.sort(key=lambda x: (x["max_edge"], x["hit_score"]), reverse=True)

    selected: List[Dict[str, Any]] = []
    used: Set[str] = set()

    for c in ako_candidates:
        if len(selected) >= MAX_COUPONS_PER_SESSION:
            break
        if not (c["event_ids"] & blocked) and not (c["event_ids"] & used):
            selected.append(c)
            used |= c["event_ids"]

    added_singles = 0
    for s in single_candidates:
        if len(selected) >= MAX_COUPONS_PER_SESSION or added_singles >= 2:
            break
        if not (s["event_ids"] & blocked) and not (s["event_ids"] & used):
            selected.append(s)
            used |= s["event_ids"]
            added_singles += 1

    if len(selected) < MIN_COUPONS_PER_SESSION:
        remaining = [p for p in ranked if p["event_id"] not in blocked and p["event_id"] not in used and LEG_MIN_ODDS <= p["odds"] <= 2.30]
        for a, b in combinations(remaining[:20], 2):
            if len(selected) >= MIN_COUPONS_PER_SESSION:
                break
            ako = round(a["odds"] * b["odds"], 2)
            ev_pair = {a["event_id"], b["event_id"]}
            if ako >= AKO2_MIN_ODDS and not (ev_pair & used):
                selected.append(
                    build_coupon(
                        "AKO2",
                        "AKO2",
                        [a, b],
                        ako,
                        round(mean([a["hit_score"], b["hit_score"]]), 1),
                        max(a["edge"], b["edge"]),
                        max(a["spread"], b["spread"]),
                    )
                )
                used |= ev_pair

    if len(selected) < MIN_COUPONS_PER_SESSION:
        return [], set()

    return selected[:MAX_COUPONS_PER_SESSION], used


def clean_txt(val: Any) -> str:
    return "".join(ch for ch in str(val or "") if ch not in "*_`[")


def format_session_msg(mode: str, start_dt: datetime, end_dt: datetime, coupons: List[Dict[str, Any]], test_run: bool = False) -> str:
    base_title = "SESJA DZIEŃ (07:00 PL)" if mode == "day" else "SESJA NOC (20:00 PL)"
    title = f"TEST — {base_title}" if test_run else base_title
    head = (
        f"🔥💎 *{title} — BETCLIC AKO & VALUE*\n"
        f"🕒 Okno: `{start_dt.strftime('%d.%m %H:%M')}` – `{end_dt.strftime('%d.%m %H:%M')}`\n"
        f"🎯 Każde zdarzenie `{LEG_MIN_ODDS:.2f}+` | Głównie *AKO2 / AKO3* | `1j = {UNIT_PLN} PLN` (`1j–4j`)\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    if not coupons:
        return f"{head}\n🛑 *PASS* — za mało jakościowych pozycji `{LEG_MIN_ODDS:.2f}+` pod próg min. `{MIN_COUPONS_PER_SESSION}` kuponów."

    ako_cnt = sum(1 for c in coupons if c["type"] in ("AKO2", "AKO3"))
    sng_cnt = len(coupons) - ako_cnt
    parts = [head]

    for idx, c in enumerate(coupons, start=1):
        lines = [
            f"*KUPON #{idx} [{clean_txt(c['label'])}]* | Kurs: *{c['odds']:.2f}* | "
            f"Stawka: *{c['units']}j ({c['stake_pln']} PLN)* | Wygrana: *{c['potential_return']:.2f} PLN*"
        ]
        for leg in c["legs"]:
            lines.append(
                f"• *{clean_txt(leg['sport_label'])}* — `{leg['commence'].strftime('%d.%m %H:%M')}`\n"
                f"  `{clean_txt(leg['home'])} vs {clean_txt(leg['away'])}`\n"
                f"  Typ: *{clean_txt(leg['selection'])}* @ *{leg['odds']:.2f}* (śr. `{leg['avg_odds']:.2f}`, {clean_txt(leg['bookmaker'])})"
            )
        parts.append("\n".join(lines))

    total_u = sum(c["units"] for c in coupons)
    total_pln = sum(c["stake_pln"] for c in coupons)
    parts.append(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Kuponów: *{len(coupons)}* (*{ako_cnt}x AKO* + *{sng_cnt}x SINGLE*) | "
        f"💰 Suma: *{total_u}j ({total_pln} PLN)*"
    )
    return "\n\n".join(parts)


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for idx in range(0, len(text), 3900):
        chunk = text[idx : idx + 3900]
        try:
            r = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown", "disable_web_page_preview": True},
                timeout=25,
            )
            if r.status_code != 200:
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": True}, timeout=25)
        except Exception as exc:
            logger.warning("Błąd wysyłki na Telegram: %s", exc)


def run_session(mode: str, test_run: bool = False) -> None:
    now = datetime.now(TZ)
    sess_key = session_key_for(mode, now)
    blocked = load_used_events(sess_key)

    hours_ahead = DAY_WINDOW_HOURS if mode == "day" else NIGHT_WINDOW_HOURS
    start_dt = now - timedelta(minutes=15)
    end_dt = now + timedelta(hours=hours_ahead)

    logger.info("Start sesji=%s test=%s okno=%s..%s min_leg=%.2f", mode, test_run, start_dt.strftime("%d.%m %H:%M"), end_dt.strftime("%d.%m %H:%M"), LEG_MIN_ODDS)
    sports = fetch_sports()
    all_picks: List[Dict[str, Any]] = []

    for sp in sports:
        key = str(sp.get("key") or "").strip()
        if key:
            all_picks.extend(extract_picks(fetch_odds(key), key, prettify_sport(sp), start_dt, end_dt, blocked, strict=True))

    if len(all_picks) < MIN_COUPONS_PER_SESSION * 2:
        seen = {(p["event_id"], p["selection"]) for p in all_picks}
        for sp in sports:
            key = str(sp.get("key") or "").strip()
            if key:
                for p in extract_picks(fetch_odds(key), key, prettify_sport(sp), start_dt, end_dt, blocked, strict=False):
                    pair = (p["event_id"], p["selection"])
                    if pair not in seen:
                        seen.add(pair)
                        all_picks.append(p)

    coupons, used = compose_session_coupons(all_picks, blocked)
    send_tg(format_session_msg(mode, start_dt, end_dt, coupons, test_run=test_run))
    if used:
        save_used_events(sess_key, blocked | used)


def resolve_mode(now_dt: datetime) -> str:
    return "day" if DAY_DROP_HOUR <= now_dt.hour < NIGHT_DROP_HOUR else "night"


def main() -> None:
    logger.info("Start silnika BETCLIC AKO ENGINE (%s)", TZ)
    if START_NOW:
        try:
            run_session(resolve_mode(datetime.now(TZ)), test_run=True)
        except Exception as exc:
            logger.exception("Błąd przy skanie testowym na start: %s", exc)

    schedule_map = {
        (DAY_DROP_HOUR, DAY_DROP_MINUTE): "day",
        (NIGHT_DROP_HOUR, NIGHT_DROP_MINUTE): "night",
    }
    fired: Set[Tuple[int, int]] = set()
    last_day = None

    while True:
        try:
            now = datetime.now(TZ)
            if now.date() != last_day:
                fired.clear()
                last_day = now.date()
            slot = (now.hour, now.minute)
            if slot in schedule_map and slot not in fired:
                fired.add(slot)
                run_session(schedule_map[slot], test_run=False)
        except Exception as exc:
            logger.exception("Błąd w pętli harmonogramu: %s", exc)
        pytime.sleep(20)


if __name__ == "__main__":
    main()
