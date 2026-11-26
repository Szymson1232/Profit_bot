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
logger = logging.getLogger("PROFIT_MACHINE_V2")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "64ea0f27d1bf2d4c4eeabf5e6b836be6").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004306783026").strip()

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Warsaw"))
START_NOW = os.getenv("START_NOW", "true").strip().lower() in ("1", "true", "yes", "on")

MORNING_DROP_HOUR = int(os.getenv("MORNING_DROP_HOUR", "6"))
MORNING_DROP_MINUTE = int(os.getenv("MORNING_DROP_MINUTE", "0"))
EVENING_DROP_HOUR = int(os.getenv("EVENING_DROP_HOUR", "20"))
EVENING_DROP_MINUTE = int(os.getenv("EVENING_DROP_MINUTE", "0"))

STATE_FILE = os.getenv("VIP_STATE_FILE", "vip_2067_used_events.json").strip()

UNIT_PLN = int(os.getenv("UNIT_PLN", "25"))
MIN_MORNING_COUPONS = int(os.getenv("MIN_MORNING_COUPONS", "4"))
MAX_MORNING_COUPONS = int(os.getenv("MAX_MORNING_COUPONS", "10"))
MAX_EVENING_COUPONS = int(os.getenv("MAX_EVENING_COUPONS", "4"))

SOLO_MIN_ODDS = float(os.getenv("SOLO_MIN_ODDS", "1.50"))
SOLO_MAX_ODDS = float(os.getenv("SOLO_MAX_ODDS", "4.20"))
PARLAY_LEG_MIN_ODDS = float(os.getenv("PARLAY_LEG_MIN_ODDS", "1.18"))
PARLAY_LEG_MAX_ODDS = float(os.getenv("PARLAY_LEG_MAX_ODDS", "2.60"))
AKO2_MIN_ODDS = float(os.getenv("AKO2_MIN_ODDS", "1.65"))
AKO3_MIN_ODDS = float(os.getenv("AKO3_MIN_ODDS", "2.00"))
MAX_COUPON_ODDS = float(os.getenv("MAX_COUPON_ODDS", "8.50"))

MIN_EDGE_RATIO = float(os.getenv("MIN_EDGE_RATIO", "1.03"))
EVENING_MIN_EDGE_RATIO = float(os.getenv("EVENING_MIN_EDGE_RATIO", "1.07"))
STRONG_EDGE_RATIO = float(os.getenv("STRONG_EDGE_RATIO", "1.08"))
SNIPER_EDGE_RATIO = float(os.getenv("SNIPER_EDGE_RATIO", "1.14"))
MAX_SUSPICIOUS_SPREAD = float(os.getenv("MAX_SUSPICIOUS_SPREAD", "1.45"))

EXCLUDED_PREFIXES = ("horse_racing", "greyhound_racing", "politics", "cricket")
BASE_URL = "https://api.the-odds-api.com/v4"


def is_allowed_sport(sport: Dict[str, Any]) -> bool:
    key = (sport.get("key") or "").lower()
    if not key or sport.get("active") is False:
        return False
    return not any(key.startswith(p) for p in EXCLUDED_PREFIXES)


def prettify_sport(sport: Dict[str, Any]) -> str:
    title = (sport.get("title") or "").strip()
    group = (sport.get("group") or "").strip()
    if title and group and group.lower() not in title.lower():
        return f"{group} — {title}"
    return title or (sport.get("key") or "Sport").replace("_", " ").title()


def load_used_events(day_str: str) -> Set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("day") == day_str and isinstance(data.get("used_event_ids"), list):
            return {str(x) for x in data["used_event_ids"] if x}
    except Exception as exc:
        logger.warning("Błąd odczytu stanu: %s", exc)
    return set()


def save_used_events(day_str: str, used_ids: Set[str]) -> None:
    try:
        d = os.path.dirname(STATE_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": day_str, "used_event_ids": sorted(used_ids)}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        logger.warning("Błąd zapisu stanu: %s", exc)


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
            return []
        data = r.json()
        return [s for s in data if isinstance(s, dict) and is_allowed_sport(s)] if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Błąd listy sportów: %s", exc)
        return []


def fetch_odds(sport_key: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={"apiKey": ODDS_API_KEY, "regions": "eu,uk,us,au", "markets": "h2h", "oddsFormat": "decimal"},
            timeout=25,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Błąd kursów %s: %s", sport_key, exc)
        return []


def analyze_pick(best_odds: float, all_odds: List[float]) -> Dict[str, Any]:
    avg_odds = mean(all_odds) if all_odds else best_odds
    if len(all_odds) >= 3:
        ref = median(sorted(all_odds)[:-1])
    elif len(all_odds) == 2:
        ref = sorted(all_odds)[0]
    else:
        ref = best_odds

    edge = (best_odds / ref) if ref > 0 else 1.0
    spread = (max(all_odds) / min(all_odds)) if len(all_odds) >= 2 and min(all_odds) > 0 else 1.0
    prob = (1.0 / best_odds) if best_odds > 0 else 0.0
    penalty = max(0.0, spread - MAX_SUSPICIOUS_SPREAD) * 28.0

    tag, bonus = "VALUE", 0.0
    if edge >= SNIPER_EDGE_RATIO and spread <= MAX_SUSPICIOUS_SPREAD:
        tag, bonus = "BŁĄD BUKA / ANOMALIA", 10.0
    elif edge >= STRONG_EDGE_RATIO and spread <= MAX_SUSPICIOUS_SPREAD:
        tag, bonus = "MOCNY EDGE", 5.5

    score = round((edge * 96.0) + (prob * 38.0) + bonus - penalty, 2)
    return {"score": score, "edge": round(edge, 3), "avg": round(avg_odds, 2), "spread": round(spread, 2), "tag": tag}


def extract_picks(
    events: List[Dict[str, Any]],
    sport_key: str,
    sport_label: str,
    start_dt: datetime,
    end_dt: datetime,
    blocked: Set[str],
    min_edge_req: float,
) -> List[Dict[str, Any]]:
    out_picks: List[Dict[str, Any]] = []

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
                        if 1.15 <= p <= 6.0:
                            sides[name].append((p, bm_name))

        for sel, entries in sides.items():
            if not entries:
                continue
            best_p, best_bm = max(entries, key=lambda x: x[0])
            info = analyze_pick(best_p, [x[0] for x in entries])
            if info["edge"] >= min_edge_req:
                out_picks.append({
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
                    "tag": info["tag"],
                    "bookmaker": best_bm,
                    "commence": dt,
                    "score": info["score"],
                })
    return out_picks


def calc_units(ctype: str, odds: float, score: float, max_edge: float, max_spread: float) -> int:
    if max_spread > MAX_SUSPICIOUS_SPREAD:
        return 1
    if ctype == "SOLO":
        if odds <= 2.20 and max_edge >= STRONG_EDGE_RATIO and score >= 122.0:
            return 4
        if odds <= 2.65 and max_edge >= STRONG_EDGE_RATIO and score >= 117.0:
            return 3
        return 2 if score >= 113.0 else 1
    if ctype == "AKO2":
        if odds <= 2.50 and max_edge >= STRONG_EDGE_RATIO and score >= 120.0:
            return 3
        return 2 if score >= 114.0 else 1
    if odds <= 3.10 and max_edge >= STRONG_EDGE_RATIO and score >= 118.0:
        return 2
    return 1


def make_coupon(ctype: str, label: str, legs: List[Dict[str, Any]], odds: float, score: float, max_e: float, max_sp: float) -> Dict[str, Any]:
    units = calc_units(ctype, odds, score, max_e, max_sp)
    stake = units * UNIT_PLN
    return {
        "type": ctype,
        "label": label,
        "legs": legs,
        "odds": odds,
        "units": units,
        "stake_pln": stake,
        "potential_return": round(stake * odds, 2),
        "score": score,
        "max_edge": max_e,
        "event_ids": {x["event_id"] for x in legs},
    }


def compose_pack(
    picks: List[Dict[str, Any]],
    blocked: Set[str],
    min_coupons: int,
    max_coupons: int,
    allow_fallback_fill: bool,
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    best_by_ev: Dict[str, Dict[str, Any]] = {}
    for p in sorted(picks, key=lambda x: x["score"], reverse=True):
        eid = p["event_id"]
        if eid not in blocked and eid not in best_by_ev:
            best_by_ev[eid] = p

    ranked = sorted(best_by_ev.values(), key=lambda x: x["score"], reverse=True)
    cands: List[Dict[str, Any]] = []

    for p in ranked:
        if SOLO_MIN_ODDS <= p["odds"] <= SOLO_MAX_ODDS:
            cands.append(make_coupon("SOLO", "SOLO", [p], p["odds"], p["score"], p["edge"], p["spread"]))

    pool = [p for p in ranked if PARLAY_LEG_MIN_ODDS <= p["odds"] <= PARLAY_LEG_MAX_ODDS]
    for a, b in combinations(pool[:24], 2):
        ako = round(a["odds"] * b["odds"], 2)
        if AKO2_MIN_ODDS <= ako <= MAX_COUPON_ODDS:
            cands.append(make_coupon("AKO2", "AKO2", [a, b], ako, round(mean([a["score"], b["score"]]), 1), max(a["edge"], b["edge"]), max(a["spread"], b["spread"])))

    for a, b, c in combinations(pool[:20], 3):
        ako = round(a["odds"] * b["odds"] * c["odds"], 2)
        if AKO3_MIN_ODDS <= ako <= MAX_COUPON_ODDS:
            cands.append(make_coupon("AKO3", "AKO3", [a, b, c], ako, round(mean([a["score"], b["score"], c["score"]]), 1), max(a["edge"], b["edge"], c["edge"]), max(a["spread"], b["spread"], c["spread"])))

    cands.sort(key=lambda x: (x["score"], x["max_edge"]), reverse=True)
    selected: List[Dict[str, Any]] = []
    used: Set[str] = set()

    for c in cands:
        if len(selected) >= max_coupons:
            break
        if not (c["event_ids"] & blocked) and not (c["event_ids"] & used):
            selected.append(c)
            used |= c["event_ids"]

    if allow_fallback_fill and len(selected) < min_coupons:
        for p in ranked:
            if len(selected) >= min_coupons:
                break
            if p["event_id"] not in blocked and p["event_id"] not in used:
                selected.append(make_coupon("SOLO", "SOLO", [p], p["odds"], p["score"], p["edge"], p["spread"]))
                used.add(p["event_id"])

    if len(selected) < min_coupons:
        return [], set()

    return selected, used


def clean_txt(val: Any) -> str:
    return "".join(ch for ch in str(val or "") if ch not in "*_`[")


def format_msg(start_dt: datetime, end_dt: datetime, coupons: List[Dict[str, Any]], mode: str) -> str:
    mode_titles = {
        "instant": "NATYCHMIASTOWY ZRZUT VIP",
        "morning": "PORANNY ZRZUT VIP (06:00 PL)",
        "evening": "WIECZORNY SKAN VIP (20:00 PL)",
    }
    tag = mode_titles.get(mode, "ZRZUT VIP")
    head = (
        f"⚡💎 *{tag} — EDYCJA 2067*\n"
        f"🕒 Okno: `{start_dt.strftime('%d.%m %H:%M')}` – `{end_dt.strftime('%d.%m %H:%M')}`\n"
        f"🎯 SOLO `{SOLO_MIN_ODDS:.2f}+` | AKO2 | AKO3 `{AKO3_MIN_ODDS:.2f}+` | `1j = {UNIT_PLN} PLN` (`1j–4j`)\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    if not coupons:
        return f"{head}\n🛑 *PASS* — brak nowych okazji wartych gry w tym oknie."

    solo_n = sum(1 for c in coupons if c["type"] == "SOLO")
    ako_n = len(coupons) - solo_n
    parts = [head]
    for i, c in enumerate(coupons, 1):
        lines = [
            f"*VIP #{i} [{clean_txt(c['label'])}]* | Kurs: *{c['odds']:.2f}* | Stawka: *{c['units']}j ({c['stake_pln']} PLN)* "
            f"| Wygrana: *{c['potential_return']:.2f} PLN* | Edge: `x{c['max_edge']:.2f}`"
        ]
        for leg in c["legs"]:
            lines.append(
                f"• *{clean_txt(leg['sport_label'])}* — `{leg['commence'].strftime('%d.%m %H:%M')}` [{clean_txt(leg['tag'])}]\n"
                f"  `{clean_txt(leg['home'])} vs {clean_txt(leg['away'])}`\n"
                f"  Typ: *{clean_txt(leg['selection'])}* @ *{leg['odds']:.2f}* (śr. `{leg['avg_odds']:.2f}`, {clean_txt(leg['bookmaker'])})"
            )
        parts.append("\n".join(lines))

    parts.append(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *{len(coupons)}* kuponów (*{solo_n}x SOLO* + *{ako_n}x AKO*) | 💰 Suma: *{sum(c['units'] for c in coupons)}j ({sum(c['stake_pln'] for c in coupons)} PLN)*"
    )
    return "\n\n".join(parts)


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for idx in range(0, len(text), 3900):
        chunk = text[idx : idx + 3900]
        try:
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=25)
            if r.status_code != 200:
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": True}, timeout=25)
        except Exception as exc:
            logger.warning("Błąd Telegram: %s", exc)


def run_pack(mode: str = "morning", skip_empty_tg: bool = False) -> None:
    now = datetime.now(TZ)
    day_str = now.date().isoformat()
    blocked = load_used_events(day_str)
    start_dt, end_dt = now - timedelta(minutes=15), now + timedelta(hours=24)

    is_evening = mode == "evening"
    edge_req = EVENING_MIN_EDGE_RATIO if is_evening else MIN_EDGE_RATIO
    min_c = 1 if is_evening else MIN_MORNING_COUPONS
    max_c = MAX_EVENING_COUPONS if is_evening else MAX_MORNING_COUPONS

    sports = fetch_sports()
    all_picks: List[Dict[str, Any]] = []
    for sp in sports:
        key = str(sp.get("key") or "").strip()
        if key:
            all_picks.extend(extract_picks(fetch_odds(key), key, prettify_sport(sp), start_dt, end_dt, blocked, edge_req))

    if not is_evening and len(all_picks) < min_c * 2:
        seen = {(p["event_id"], p["selection"]) for p in all_picks}
        for sp in sports:
            key = str(sp.get("key") or "").strip()
            if key:
                for p in extract_picks(fetch_odds(key), key, prettify_sport(sp), start_dt, end_dt, blocked, 1.01):
                    k = (p["event_id"], p["selection"])
                    if k not in seen:
                        seen.add(k)
                        all_picks.append(p)

    coupons, used = compose_pack(all_picks, blocked, min_c, max_c, allow_fallback_fill=not is_evening)
    if coupons or not skip_empty_tg:
        send_tg(format_msg(start_dt, end_dt, coupons, mode))
    if used:
        save_used_events(day_str, blocked | used)


def main() -> None:
    if START_NOW:
        try:
            current_mode = "evening" if datetime.now(TZ).hour >= EVENING_DROP_HOUR else "instant"
            run_pack(mode=current_mode, skip_empty_tg=False)
        except Exception as exc:
            logger.exception("Błąd startu: %s", exc)

    schedule_slots = {
        (MORNING_DROP_HOUR, MORNING_DROP_MINUTE): "morning",
        (EVENING_DROP_HOUR, EVENING_DROP_MINUTE): "evening",
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
            if slot in schedule_slots and slot not in fired:
                fired.add(slot)
                run_pack(mode=schedule_slots[slot], skip_empty_tg=False)
        except Exception as exc:
            logger.exception("Błąd pętli: %s", exc)
        pytime.sleep(20)


if __name__ == "__main__":
    main()
