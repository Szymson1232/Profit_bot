import os
import requests
from datetime import datetime

# Konfiguracja ze zmiennych środowiskowych (GitHub Secrets / Railway Env)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")  # Twój klucz do The Odds API

# Obsługiwane sporty w naszym potworze typerskim
SPORTS_ENDPOINTS = [
    {"key": "soccer_uefa_champs_league", "name": "⚽ Liga Mistrzów"},
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "baseball_mlb", "name": "⚾ Baseball (MLB)"},
    {"key": "volleyball_cev", "name": "🏐 Siatkówka"}
]

def fetch_odds_for_sport(sport_key):
    """Pobiera kursy z The Odds API dla danej dyscypliny."""
    if not ODDS_API_KEY:
        return []
    
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Błąd pobierania danych dla {sport_key}: {e}")
    return []

def generate_pro_batch():
    """Analizuje rynki i buduje profesjonalny, dzienny pakiet typów."""
    now = datetime.now().strftime("%d.%m.%Y")
    
    message = f"🔥 **@PROFIT VIP – DZIENNY PAKIET EKSPERCKI** 🚀\n"
    message += f"📅 Data: {now} | Status: PRODUKCJA\n"
    message += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    picks_count = 0
    
    # Przeszukujemy rynki w poszukiwaniu wartościowych kursów (celujemy w value 1.70 - 3.40)
    for sport in SPORTS_ENDPOINTS:
        events = fetch_odds_for_sport(sport["key"])
        
        for event in events[:2]:  # Bierzemy po 1-2 najciekawsze mecze z dyscypliny
            home = event.get("home_team")
            away = event.get("away_team")
            bookmakers = event.get("bookmakers", [])
            
            if not bookmakers:
                continue
                
            # Wyciągamy kursy z pierwszego lepszego bukmachera w zestawieniu
            markets = bookmakers[0].get("markets", [])
            for market in markets:
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        price = outcome.get("price", 0.0)
                        # Szukamy value w naszym przedziale (singli lub mocnych kursów)
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            
                            bet_type = "Single (Value)" if price >= 2.05 else "Bezpieczny / AKO"
                            
                            message += f"🔹 **Typ {picks_count} [{sport['name']}]**\n"
                            message += f"🏟 Mecz: {home} vs {away}\n"
                            message += f"🎯 Typ: **{team_name}** ({bet_type})\n"
                            message += f"💰 Kurs: **{price:.2f}**\n\n"
                            
                            if picks_count >= 5:  # Zatrzymujemy się na 5-6 kuponach dziennie
                                break
                    if picks_count >= 5:
                        break
            if picks_count >= 5:
                break
        if picks_count >= 5:
                            break

    # Fallback, gdyby API nie zwróciło danych (np. brak spotkań w danej chwili)
    if picks_count == 0:
        message += f"📌 **Analiza rynkowa w toku. Trwa agregacja wieczornych spotkań z Ligi Mistrzów i NBA.**\n"
        message += f"🎯 Szykujemy potężny zestaw value na nadchodzącą noc!\n\n"

    message += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    message += f"💎 *Zarządzanie bankrollem to klucz do sukcesu. Jedziemy z bukiem!*"
    
    return message

def send_to_telegram(text):
    """Wysyła gotowy raport na kanał Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Brak tokenów Telegrama w ENV!")
        print(text)
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

if __name__ == "__main__":
    report = generate_pro_batch()
    send_to_telegram(report)
