import os
import sys
import time
import requests

# ==========================================
# KOREAŃSKI MÓZG OPERACYJNY - KONFIGURACJA VIP
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "a830dfc856760eac60f65fa2c61421d4").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

# Parametry bezwzględnego value
MIN_EDGE_PERCENT = 2.8          # Agresywniejszy próg wyłapujący błędy buków
MAX_ODDS = 5.50                 # Łapiemy też soczyste kursy w nocy (Ameryka/Meksyk)
MIN_ODDS = 1.25                 # Odrzucamy mikroskopijną drobniznę
SCAN_INTERVAL_SECONDS = 300     # Skan całego globu co 5 minut
MAX_ALERTS_PER_HOUR = 6         # Maksymalnie 6 wyselekcjonowanych perełek na godzinę

# Bankroll Management (1j = 25 PLN)
UNIT_VALUE_PLN = 25.0

# Pamięć cache przeciwko duplikatom
sent_match_cache = set()
recent_alerts_timestamps = []

# WSZECHSTRONNY GLOBALNY ROSTER (Noc, Dzień, Cały Świat - Bez Ograniczeń)
GLOBAL_SPORTS_TO_SCAN = [
    # --- PIŁKA NOŻNA (Global & Egzotyka) ---
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_poland_ekstraklasa", "soccer_turkey_super_lig", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga", "soccer_efl_champ", "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    
    # --- KOSZYKÓWKA (USA & Świat) ---
    "basketball_nba", "basketball_euroleague", "basketball_ncaab", "basketball_spain_acb",
    "basketball_nbl", "basketball_turkey_super_lig",
    
    # --- HOKEJ NA LODZIE ---
    "icehockey_nhl", "icehockey_sweden_hockey_league", "icehockey_finland_liiga", "icehockey_khl",
    
    # --- TENIS (Turnieje Świata, Ameryka Południowa/Północna) ---
    "tennis_atp_aus_open", "tennis_wta_aus_open", "tennis_atp_challenger", "tennis_wta_challenger",
    
    # --- BASEBALL, NFL, FUTBOL AMERYKAŃSKI AKADEMICKI ---
    "baseball_mlb", "baseball_npb", "americanfootball_nfl", "americanfootball_ncaaf",
    
    # --- POZOSTAŁE DYSCYPLINY (Siatkówka, Esport, Dart) ---
    "volleyball_cev_champions_league", "esports_league_of_legends", "esports_dota2", "darts_world_championship"
]

if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych w środowisku!")
    sys.exit(1)

print("[INFO] KOREAN OPERATIONAL BRAIN: Diamentowy potwór bukmacherski wystartował...")


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
        "regions": "eu,us,uk,au",    # Globalne pokrycie rynków (w tym buki amerykańskie na nocne hity)
        "markets": "h2h",
        "oddsformat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            return data
        elif response.status_code == 429:
            print(f"[API] Ostrzeżenie 429 - Limit wyczerpany. Odpoczywamy godzinę...")
            time.sleep(3600)
            return []
        else:
            return []
    except Exception as e:
        return []


def calculate_true_probabilities(bookmakers):
    """Zdejmuje marżę bukmacherską metodą konsensusu rynkowego."""
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


def calculate_stake(edge_percent):
    """Precyzyjny system zarządzania stawką w zależności od wielkości błędu bukmachera."""
    if edge_percent >= 9.0:
        units = 3
    elif edge_percent >= 5.0:
        units = 2
    else:
        units = 1
    
    amount_pln = units * UNIT_VALUE_PLN
    return units, amount_pln


def analyze_events_for_value(events):
    valuable_bets = []

    for event in events:
        home_team = event.get("home_team", "Gospodarz")
        away_team = event.get("away_team", "Gość")
        commence_time = event.get("commence_time", "")
        bookmakers = event.get("bookmakers", [])

        if not bookmakers:
            continue

        true_probs = calculate_true_probabilities(bookmakers)
        if not true_probs:
            continue

        match_name = f"{home_team} vs {away_team}"
        best_bet_for_match = None

        for bm in bookmakers:
            bm_title = bm.get("title")
            for market in bm.get("markets", []):
                if market.get("key") == "h2h":
                    for outcome in market.get("outcomes", []):
                        selection = outcome.get("name")
                        price = outcome.get("price")

                        if not price or not selection:
                            continue
                        if not (MIN_ODDS <= price <= MAX_ODDS):
                            continue

                        fair_prob = true_probs.get(selection, 0)
                        if fair_prob <= 0:
                            continue

                        edge = (fair_prob * price) - 1.0
                        edge_percent = edge * 100.0

                        if edge_percent >= MIN_EDGE_PERCENT:
                            units, amount_pln = calculate_stake(edge_percent)
                            candidate = {
                                "match": match_name,
                                "time": commence_time.replace("T", " ")[:16] if commence_time else "Live",
                                "bookmaker": bm_title,
                                "selection": selection,
                                "price": price,
                                "fair_odds": round(1.0 / fair_prob, 2),
                                "edge": round(edge_percent, 2),
                                "units": units,
                                "amount_pln": int(amount_pln)
                            }
                            if best_bet_for_match is None or candidate["edge"] > best_bet_for_match["edge"]:
                                best_bet_for_match = candidate

        if best_bet_for_match:
            valuable_bets.append(best_bet_for_match)

    return valuable_bets


def can_send_alert():
    global recent_alerts_timestamps
    now = time.time()
    recent_alerts_timestamps = [t for t in recent_alerts_timestamps if now - t < 3600]
    return len(recent_alerts_timestamps) < MAX_ALERTS_PER_HOUR


def main():
    global sent_match_cache, recent_alerts_timestamps

    startup_msg = (
        "💎 <b>KOREAN OPERATIONAL BRAIN: DIAMOND VIP</b> 💎\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 Skanowanie globalne: <b>24/7 (Nocne USA + Dzień Europa/Świat)</b>\n"
        "⚡ Blokada duplikatów: <b>AKTYWNA (1 mecz = 1 typ)</b>\n"
        "💵 Stawki: <b>1j (25 zł) / 2j (50 zł) / 3j (75 zł)</b>\n"
        "🚀 <i>Bezwzględny potwór gotowy na zarabianie pitosu!</i>"
    )
    send_telegram_alert(startup_msg)

    while True:
        try:
            all_found_bets = []
            print("\n[CYKL] Koreański mózg operacyjny analizuje globalne rynki w tle...")

            for sport in GLOBAL_SPORTS_TO_SCAN:
                events = fetch_market_odds(sport)
                if events:
                    value_bets = analyze_events_for_value(events)
                    for bet in value_bets:
                        if bet["match"] not in sent_match_cache:
                            all_found_bets.append(bet)
                time.sleep(1)

            # Sortujemy od największego błędu bukmachera (najwyższy Edge)
            all_found_bets.sort(key=lambda x: x["edge"], reverse=True)

            for bet in all_found_bets:
                if bet["match"] in sent_match_cache:
                    continue

                if can_send_alert():
                    unit_icons = "🟢" * bet['units'] + "⚪" * (3 - bet['units'])

                    alert_msg = (
                        f"🔥 <b>VIP VALUE / SUREBET PICK</b> 🔥\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 <b>Wydarzenie:</b> {bet['match']}\n"
                        f"⏰ <b>Start:</b> {bet['time']} UTC\n"
                        f"🏢 <b>Bukmacher:</b> <code>{bet['bookmaker']}</code>\n"
                        f"🎯 <b>Typ:</b> <b>{bet['selection']}</b>\n"
                        f"💰 <b>Kurs rynkowy:</b> <code>{bet['price']}</code>\n"
                        f"📊 <b>Uczciwy kurs (Fair):</b> <code>{bet['fair_odds']}</code>\n"
                        f"📈 <b>Przewaga (Edge):</b> 🟢 <b>+{bet['edge']}%</b>\n"
                        f"💵 <b>Zalecana stawka:</b> <b>{bet['units']}j ({bet['amount_pln']} PLN)</b> {unit_icons}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    send_telegram_alert(alert_msg)
                    
                    sent_match_cache.add(bet["match"])
                    recent_alerts_timestamps.append(time.time())
                    
                    print(f"[WYSŁANO VIP] {bet['match']} | Buk: {bet['bookmaker']} | Edge: +{bet['edge']}% | Stawka: {bet['units']}j")
                    time.sleep(2)
                else:
                    break

            print(f"[CYKL] Zakończony pomyślnie. Czekam {SCAN_INTERVAL_SECONDS // 60} minut do kolejnego skanu...")
            time.sleep(SCAN_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            print(f"[BŁĄD KRYTYCZNY W PĘTLI GŁÓWNEJ]: {e}")
            time.sleep(30)


if __name__ == "__main__":
