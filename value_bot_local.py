import os
import random
import requests
from datetime import datetime, timezone

# Konfiguracja tokenów VIP
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Pula dyscyplin z naciskiem na nocne rynki (NBA, NHL, Ameryka, e-sport)
VIP_SPORTS_ENDPOINTS = [
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "basketball_euroleague", "name": "🏀 Euroleague"},
    {"key": "esports_counter_strike", "name": "🎮 CS2 (E-sport Value)"},
    {"key": "soccer_usa_mls", "name": "⚽ MLS / Ameryka Północna"}
]

def fetch_odds_for_sport(sport_key):
    if not ODDS_API_KEY:
        print("BŁĄD: Brak klucza ODDS_API_KEY w zmiennych środowiskowych!")
        return []
    
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us,uk",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(url, params=params)
        print(f"API Odds [{sport_key}] - Status HTTP: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Pobrano wydarzeń dla {sport_key}: {len(data)}")
            return data
        else:
            print(f"Błąd API dla {sport_key}: {response.text}")
    except Exception as e:
        print(f"Wyjątek przy pobieraniu danych dla {sport_key}: {e}")
    return []

def generate_pro_batch():
    now_str = datetime.now().strftime("%d.%m.%Y")
    current_utc = datetime.now(timezone.utc)
    
    lines = [
        "👑 **@PROFIT VIP SYNDICATE – NOCNY PAKIET ELITE** 🚀",
        f"📅 Data: {now_str} | Status: SKANOWANIE NOCNYCH RYNKÓW",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    used_match_ids = set()
    
    shuffled_sports = list(VIP_SPORTS_ENDPOINTS)
    random.shuffle(shuffled_sports)
    
    for sport in shuffled_sports:
        if picks_count >= 6:
            break
            
        print(f"Skanowanie rynków: {sport['name']}...")
        events = fetch_odds_for_sport(sport["key"])
        
        for event in events:
            if picks_count >= 6:
                break
                
            match_id = event.get("id")
            if match_id in used_match_ids:
                continue
