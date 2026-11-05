import os
import sys
import time
import requests

# ==========================================
# KONFIGURACJA NADHENRYKA (VIP ULTRA)
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "2dd63be4b08f78956ef0d1b936b05882").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

# Parametry algorytmu Value
MIN_EDGE_PERCENT = 3.0          # Minimalna przewaga (edge) w %
MAX_ODDS = 5.00                 # Maksymalny kurs
MIN_ODDS = 1.30                 # Minimalny kurs
SCAN_INTERVAL_SECONDS = 600     # Skanujemy co 10 minut rynki, ale wysyłamy z limitem!
MAX_ALERTS_PER_HOUR = 6         # Limit: max 6 propozycji na godzinę

# Pamięć podręczna wysłanych typów, żeby nie spamować tym samym meczem
sent_bets_cache = set()
recent_alerts_timestamps = []

# WSZECHSTRONNY ROSTER SPORTÓW I LIG
SPORTS_TO_SCAN = [
    # --- PIŁKA NOŻNA ---
    "soccer_epl",                       
    "soccer_spain_la_liga",             
    "soccer_italy_serie_a",             
    "soccer_germany_bundesliga",        
    "soccer_france_ligue_one",          
    "soccer_uefa_champs_league",        
    "soccer_uefa_europa_league",        
    "soccer_poland_ekstraklasa",        
    "soccer_turkey_super_lig",          
    "soccer_netherlands_eredivisie",    
    "soccer_portugal_primeira_liga",    
    "soccer_efl_champ",                 
    
    # --- KOSZYKÓWKA ---
    "basketball_nba",                   
    "basketball_euroleague",            
    "basketball_ncaab",                 
    "basketball_spain_acb",             
    
    # --- HOKEJ NA LODZIE ---
    "icehockey_nhl",                    
    "icehockey_sweden_hockey_league",   
    "icehockey_finland_liiga",          
    "icehockey_khl",                    
    
    # --- POZOSTAŁE ---
    "tennis_atp_aus_open",              
    "tennis_wta_aus_open",              
    "baseball_mlb",                     
    "americanfootball_nfl",             
    "volleyball_cev_champions_league",  
    "esports_league_of_legends",        
    "darts_world_championship"          
]

# Walidacja startowa
if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych!")
    sys.exit(1)

print("[INFO] NADHENRYK z systemem anty-spamowym i limitem godzinowym wystartował...")


# ==========================================
# MODUŁ KOMUNIKACJI TELEGRAM
# ==========================================
def send_telegram_alert(message: str):
    """Wysyła sformatowany alert VIP w standardzie HTML na Telegram."""
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
        print(f"[WYJATEK TELEGRAM] Nie udało się dostarczyć powiadomienia: {e}")


# ==========================================
# MODUŁ POBIERANIA DANYCH Z ODDS API
# ==========================================
def fetch_market_odds(sport_key: str):
    """Pobiera aktualne kursy dla dowolnego sportu z The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "api_key": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h",
        "oddsformat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            remaining = response.headers.get("x-requests-remaining", "N/A")
            print(f"[API] [{sport_key}] OK. Pozostałe zapytania: {remaining}")
            return response.json()
        elif response.status_code == 429:
            print(f"[OSTRZEŻENIE API] Limit zapytań wyczerpany (429). Odpoczywamy...")
            time.sleep(3600)
            return []
        else:
            return []
    except Exception as e:
        print(f"[WYJATEK API] Błąd sieciowy dla {sport_key}: {e}")
        return []


# ==========================================
# ALGORYTM MATEMATYCZNY (FAIR CONSENSUS)
# ==========================================
def calculate_true_probabilities(bookmakers):
    """Usuwa marżę bukmacherską i wyznacza obiektywne prawdopodobieństwo rynkowe."""
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


def analyze_events_for_value(events):
    """Przeszukuje wydarzenia w poszukiwaniu odchyłek kursowych."""
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
                            bet_key = f"{home_team}_{away_team}_{selection}"
                            valuable_bets.append({
                                "unique_key": bet_key,
                                "match": f"{home_team} vs {away_team}",
                                "time": commence_time.replace("T", " ")[:16] if commence_time else "Live/Niedługo",
                                "bookmaker": bm_title,
                                "selection": selection,
                                "price": price,
                                "fair_odds": round(1.0 / fair_prob, 2),
                                "edge": round(edge_percent, 2)
                            })

    return valuable_bets


def can_send_alert():
    """Sprawdza, czy nie przekroczyliśmy limitu 6 propozycji na ostatnią godzinę."""
    global recent_alerts_timestamps
    now = time.time()
    # Usuwamy wpisy starsze niż 1 godzina (3600 sekund)
    recent_alerts_timestamps = [t for t in recent_alerts_timestamps if now - t < 3600]
    
    if len(recent_alerts_timestamps) < MAX_ALERTS_PER_HOUR:
        return True
    return False


# ==========================================
# GŁÓWNA PĘTLA OPERACYJNA NADHENRYKA
# ==========================================
def main():
    global sent_bets_cache, recent_alerts_timestamps
    
    startup_msg = (
        "🧬 <b>NADHENRYK VIP - INTELIGENTNY SKANER</b> 🧬\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Skaner anty-spamowy aktywny!\n"
        "🎯 Limit: <b>Max 6 typów na godzinę</b>\n"
        "📈 Minimalny Edge: <b>&gt;= 3.0%</b>\n"
        "💰 <i>Wybieramy tylko absolutne hity rynkowe!</i>"
    )
    send_telegram_alert(startup_msg)
    print("[INFO] NADHENRYK wystartował z kontrolą częstotliwości.")

    while True:
        try:
            all_found_bets = []

            # 1. Zbieramy wszystkie value bety ze wszystkich sportów w tym cyklu
            for sport in SPORTS_TO_SCAN:
                events = fetch_market_odds(sport)
                if events:
                    value_bets = analyze_events_for_value(events)
                    for bet in value_bets:
                        # Sprawdzamy czy ten konkretny zakład nie był już wysyłany wcześniej
                        if bet["unique_key"] not in sent_bets_cache:
                            all_found_bets.append(bet)
                time.sleep(2)

            # 2. Sortujemy znalezione bety po najwyższym Edge (najlepsze okazje na górze)
            all_found_bets.sort(key=lambda x: x["edge"], reverse=True)

            # 3. Wysyłamy z zachowaniem limitu godzinowego
            for bet in all_found_bets:
                if can_send_alert():
                    alert_msg = (
                        f"⚡ <b>NADHENRYK: VIP VALUE BET</b> ⚡\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 <b>Wydarzenie:</b> {bet['match']}\n"
                        f"⏰ <b>Start:</b> {bet['time']} UTC\n"
                        f"🏢 <b>Bukmacher:</b> <code>{bet['bookmaker']}</code>\n"
                        f"🎯 <b>Typ:</b> <b>{bet['selection']}</b>\n"
                        f"💰 <b>Kurs rynkowy:</b> <code>{bet['price']}</code>\n"
                        f"📊 <b>Uczciwy kurs (Fair):</b> <code>{bet['fair_odds']}</code>\n"
                        f"📈 <b>Przewaga (Edge):</b> 🟢 <b>+{bet['edge']}%</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    send_telegram_alert(alert_msg)
                    
                    # Zapisujemy do pamięci podręcznej, żeby nigdy więcej nie wysłać tego samego
                    sent_bets_cache.add(bet["unique_key"])
                    recent_alerts_timestamps.append(time.time())
                    
                    print(f"[WYSŁANO VIP] {bet['match']} | Buk: {bet['bookmaker']} | Edge: +{bet['edge']}%")
                    time.sleep(3)
                else:
                    print("[INFO] Osiągnięto limit 6 typów na godzinę. Czekamy z kolejnymi...")
                    break

            print(f"\n[INFO] Cykl zakończony. Czekam 10 minut do kolejnego sprawdzenia rynku...")
            time.sleep(600)

        except KeyboardInterrupt:
            print("\n[STOP] Nadhenryk zatrzymany ręcznie.")
            sys.exit(0)
        except Exception as e:
            print(f"[BŁĄD KRYTYCZNY NADHENRYKA]: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
