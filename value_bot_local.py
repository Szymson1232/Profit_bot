import random
import requests
from datetime import datetime

# --- PRODUKCJA VIP ---
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"
ODDS_API_KEY = "99e1b40f31bdf20559cb664785d538b8"

VIP_SPORTS_ENDPOINTS = [
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "basketball_euroleague", "name": "🏀 Euroleague"},
    {"key": "esports_counter_strike", "name": "🎮 CS2 (E-sport Value)"},
    {"key": "soccer_usa_mls", "name": "⚽ MLS / Ameryka Północna"}
]

def fetch_odds_for_sport(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us,uk",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            print(f"Pobrano wydarzeń dla {sport_key}: {len(data)}")
            return data
    except Exception as e:
        print(f"Błąd dla {sport_key}: {e}")
    return []

def generate_pro_batch():
    now_str = datetime.now().strftime("%d.%m.%Y")
    lines = [
        "👑 **@PROFIT VIP SYNDICATE – NOCNY PAKIET ELITE** 🚀",
        f"📅 Data: {now_str} | Status: PRODUKCJA LIVE",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    used_match_ids = set()
    used_teams = set()
    
    shuffled_sports = list(VIP_SPORTS_ENDPOINTS)
    random.shuffle(shuffled_sports)
    
    for sport in shuffled_sports:
        if picks_count >= 6:
            break
        events = fetch_odds_for_sport(sport["key"])
        for event in events:
            if picks_count >= 6:
                break
            match_id = event.get("id")
            if match_id in used_match_ids:
                continue
                
            home = event.get("home_team")
            away = event.get("away_team")
            
            if not home or not away:
                continue
            
            match_key = f"{home.lower()}_vs_{away.lower()}"
            if home in used_teams or away in used_teams or match_key in used_match_ids:
                continue
                
            bookmakers = event.get("bookmakers", [])
            if not bookmakers:
                continue
                
            markets = bookmakers[0].get("markets", [])
            for market in markets:
                if market.get("key") == "h2h":
