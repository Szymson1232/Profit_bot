import os
import sys
import time
from datetime import datetime, timezone
import requests

# ==========================================
# KOREAŃSKI MÓZG OPERACYJNY - DEBUG & FIX VIP
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "b8e9dd7127d7afee70c5eeb8a8a730a0").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

SCAN_INTERVAL_SECONDS = 300
MAX_ALERTS_PER_HOUR = 4         # Lekko podbijamy limit, żeby nadronić stratę

SOLO_MIN_ODDS = 1.50            # Odrobinę szersze widmo kursowe
SOLO_MAX_ODDS = 2.60
SOLO_MIN_EDGE = 0.5             # Agresywne łapanie każdego value

AKO_LEG_MIN_ODDS = 1.25
AKO_LEG_MAX_ODDS = 1.65

UNIT_VALUE_PLN = 25.0
MAX_HOURS_AHEAD = 96            # Zwiększamy okno do 4 dni, żeby mieć pewność że nic nie umknie

sent_match_cache = set()
recent_alerts_timestamps = []

GLOBAL_SPORTS_TO_SCAN = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_poland_ekstraklasa", "basketball_nba", "basketball_euroleague", "icehockey_nhl"
]

if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych!")
    sys.exit(1)

print("[INFO] KOREAN OPERATIONAL BRAIN: Tryb DEBUG aktywny. Startujemy...")


def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[BŁĄD TELEGRAM] Kod {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[WYJATEK TELEGRAM]: {e}")


def fetch_market_odds(sport_key: str):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "api_key": ODDS_API_KEY,
        "regions": "eu,us,uk,au",
        "markets": "h2h",
        "oddsformat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            print(f"[API DEBUG] Sport {sport_key}: pobrano {len(data)} wydarzeń.")
            return data
        elif response.status_code == 429:
            print(f"[API] Ostrzeżenie 429 - Limit wyczerpany. Czekamy...")
            time.sleep(3600)
            return []
        else:
            print(f"[API ERROR] Sport {sport_key} zwrócił kod {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"[API EXCEPTION] {sport_key}: {e}")
        return []


def calculate_true_probabilities(bookmakers):
    all_implied_probs = []
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") == "h2h":
                outcomes = market.get("outcomes", [])
                bm_probs = {}
                sum_prob = 0
                for out in outcomes:
                    price = out.get("price")
                    if price and price > 1.0:
                        p = 1.0 / price
                        bm_probs[out.get("name")] = p
                        sum_prob += p
                if sum_prob > 1.0:
                    for name in bm_probs:
                        bm_probs[name] /= sum_prob
                    all_implied_probs.append(bm_probs)

    if not all_implied_probs:
        return {}

    market_consensus = {}
    keys = all_implied_probs[0].keys()
    for key in keys:
        values = [bp.get(key, 0) for bp in all_implied_probs if key in bp]
        if values:
            market_consensus[key] = sum(values) / len(values)

    return market_consensus


def calculate_smart_stake(edge_percent, bet_type="solo"):
    if bet_type == "ako":
        units = 2
    else:
        if edge_percent >= 3.0:
            units = 3
        elif edge_percent >= 1.5:
            units = 2
        else:
            units = 1
            
    amount_pln = units * UNIT_VALUE_PLN
    return units, amount_pln


def analyze_events(events):
    solos = []
    ako_candidates = []
    now_dt = datetime.now(timezone.utc)

    for event in events:
        home_team = event.get("home_team", "Gospodarz")
        away_team = event.get("away_team", "Gość")
        commence_time = event.get("commence_time", "")
        bookmakers = event.get("bookmakers", [])

        if not bookmakers or not commence_time:
            continue

        try:
            clean_time = commence_time.replace("Z", "+00:00")
            event_dt = datetime.fromisoformat(clean_time)
            hours_diff = (event_dt - now_dt).total_seconds() / 3600
            # Jeśli mecz jest w przeszłości (zbyt duży bufor) lub dalej niż 96h, pomijamy
            if hours_diff < -2 or hours_diff > MAX_HOURS_AHEAD:
                continue
        except Exception as e:
            continue

        true_probs = calculate_true_probabilities(bookmakers)
        if not true_probs:
            continue

        match_name = f"{home_team} vs {away_team}"

        for bm in bookmakers:
            bm_title = bm.get("title")
            for market in bm.get("markets", []):
                if market.get("key") == "h2h":
                    for outcome in market.get("outcomes", []):
                        selection = outcome.get("name")
                        price = outcome.get("price")

                        if not price or not selection:
                            continue

                        fair_prob = true_probs.get(selection, 0)
                        if fair_prob <= 0:
                            continue

                        edge = ((fair_prob * price) - 1.0) * 100.0
                        unique_id = f"{match_name}_{selection}"
                        match_id = match_name

                        if SOLO_MIN_ODDS <= price <= SOLO_MAX_ODDS and edge >= SOLO_MIN_EDGE:
                            solos.append({
                                "id": unique_id,
                                "match_id": match_id,
                                "match": match_name,
                                "time": commence_time.replace("T", " ")[:16],
                                "bookmaker": bm_title,
                                "selection": selection,
                                "price": price,
                                "edge": round(edge, 2)
                            })

                        if AKO_LEG_MIN_ODDS <= price <= AKO_LEG_MAX_ODDS and fair_prob >= 0.55:
                            ako_candidates.append({
                                "id": unique_id,
                                "match_id": match_id,
                                "match": match_name,
                                "bookmaker": bm_title,
                                "selection": selection,
                                "price": price
                            })

    return solos, ako_candidates


def can_send_alert():
    global recent_alerts_timestamps
    now = time.time()
    recent_alerts_timestamps = [t for t in recent_alerts_timestamps if now - t < 3600]
    return len(recent_alerts_timestamps) < MAX_ALERTS_PER_HOUR


def main():
    global sent_match_cache, recent_alerts_timestamps
    print("[INFO] Mózg operacyjny ruszył w trybie naprawczym...")

    while True:
        try:
            print("\n[CYKL] Skanowanie rynków i zbieranie danych...")
            all_solos = []
            all_ako_legs = []

            for sport in GLOBAL_SPORTS_TO_SCAN:
                events = fetch_market_odds(sport)
                if events:
                    solos, ako_legs = analyze_events(events)
                    all_solos.extend(solos)
                    all_ako_legs.extend(ako_legs)
                time.sleep(0.5)

            print(f"[ANALIZA] Znaleziono łącznie {len(all_solos)} kandydatów SOLO i {len(all_ako_legs)} nóg AKO.")
            sent_matches_this_cycle = set()

            # Wysyłka SOLO
            all_solos.sort(key=lambda x: x["edge"], reverse=True)
            for bet in all_solos:
                if bet["id"] in sent_match_cache or bet["match_id"] in sent_matches_this_cycle:
                    continue

                if can_send_alert():
                    units, amount_pln = calculate_smart_stake(bet["edge"], "solo")
                    unit_icons = "🟢" * units + "⚪" * (3 - units)

                    alert_msg = (
                        f"🔥 <b>SOLO VIP PICK (Recover)</b> 🔥\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 <b>Mecz:</b> {bet['match']}\n"
                        f"⏰ <b>Start:</b> {bet['time']} UTC\n"
                        f"🏢 <b>Bukmacher:</b> <code>{bet['bookmaker']}</code>\n"
                        f"🎯 <b>Typ:</b> <b>{bet['selection']}</b>\n"
                        f"💰 <b>Kurs:</b> <code>{bet['price']}</code>\n"
                        f"📈 <b>Przewaga:</b> +{bet['edge']}%\n"
                        f"💵 <b>Stawka:</b> <b>{units}j ({amount_pln} PLN)</b> {unit_icons}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    send_telegram_alert(alert_msg)
                    sent_match_cache.add(bet["id"])
                    sent_matches_this_cycle.add(bet["match_id"])
                    recent_alerts_timestamps.append(time.time())
                    print(f"[WYSŁANO SOLO] {bet['match']} | Stawka: {units}j")
                    time.sleep(2)
                    break

            # Wysyłka AKO
            if len(all_ako_legs) >= 2 and can_send_alert():
                unique_ako = []
                seen_ako_matches = set()
                for leg in all_ako_legs:
                    if leg["id"] not in sent_match_cache and leg["match_id"] not in sent_matches_this_cycle and leg["match"] not in seen_ako_matches:
                        unique_ako.append(leg)
                        seen_ako_matches.add(leg["match"])
                    if len(unique_ako) == 2:
                        break

                if len(unique_ako) == 2:
                    total_odds = round(unique_ako[0]["price"] * unique_ako[1]["price"], 2)
                    ako_key = f"AKO_{unique_ako[0]['id']}_{unique_ako[1]['id']}"

                    if ako_key not in sent_match_cache and 1.75 <= total_odds <= 3.30:
                        units, amount_pln = calculate_smart_stake(0, "ako")
                        unit_icons = "🟢" * units + "⚪" * (3 - units)

                        alert_msg = (
                            f"⚡ <b>AKO / TAŚMA VIP (Recover)</b> ⚡\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"1️⃣ {unique_ako[0]['match']} 👉 <b>{unique_ako[0]['selection']}</b> ({unique_ako[0]['price']})\n"
                            f"2️⃣ {unique_ako[1]['match']} 👉 <b>{unique_ako[1]['selection']}</b> ({unique_ako[1]['price']})\n"
                            f"💰 <b>Łączny kurs AKO:</b> <code>{total_odds}</code>\n"
                            f"💵 <b>Zalecana stawka:</b> <b>{units}j ({amount_pln} PLN)</b> {unit_icons}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━"
                        )
                        send_telegram_alert(alert_msg)
                        sent_match_cache.add(ako_key)
                        sent_match_cache.add(unique_ako[0]["id"])
                        sent_match_cache.add(unique_ako[1]["id"])
                        sent_matches_this_cycle.add(unique_ako[0]["match_id"])
                        sent_matches_this_cycle.add(unique_ako[1]["match_id"])
                        recent_alerts_timestamps.append(time.time())
                        print(f"[WYSŁANO AKO] Kurs łączny: {total_odds} | Stawka: {units}j")

            print(f"[CYKL] Zakończony pomyślnie. Czekam {SCAN_INTERVAL_SECONDS // 60} minut...")
            time.sleep(SCAN_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            print(f"[BŁĄD KRYTYCZNY]: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
