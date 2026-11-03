import os
import requests
from datetime import datetime

# Konfiguracja ze zmiennych środowiskowych
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORTS_ENDPOINTS = [
    {"key": "soccer_uefa_champs_league", "name": "⚽ Liga Mistrzów"},
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "baseball_mlb", "name": "⚾ Baseball (MLB)"},
    {"key": "volleyball_cev", "name": "🏐 Siatkówka"}
]

def fetch_odds_for_sport(sport_key):
    if not ODDS_API_KEY:
        print("BŁĄD: Brak klucza ODDS_API_KEY w zmiennych środowiskowych!")
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
        print(f"API Odds [{sport_key}] - Status HTTP: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Pobrano wydarzeń: {len(data)}")
            return data
        else:
            print(f"Błąd API: {response.text}")
    except Exception as e:
        print(f"Wyjątek przy pobieraniu danych dla {sport_key}: {e}")
    return []

def generate_pro_batch():
    now = datetime.now().strftime("%d.%m.%Y")
    
    lines = [
        "🔥 **@PROFIT VIP – DZIENNY PAKIET EKSPERCKI** 🚀",
        f"📅 Data: {now} | Status: PRODUKCJA",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    
    for sport in SPORTS_ENDPOINTS:
        print(f"Skanowanie dyscypliny: {sport['name']}...")
        events = fetch_odds_for_sport(sport["key"])
        
        for event in events[:2]:
            home = event.get("home_team")
            away = event.get("away_team")
            bookmakers = event.get("bookmakers", [])
            
            if not bookmakers:
                continue
                
            markets = bookmakers[0].get("markets", [])
            for market in markets:
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        price = outcome.get("price", 0.0)
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            
                            bet_type = "Single (Value)" if price >= 2.05 else "Bezpieczny / AKO"
                            
                            lines.append(f"🔹 **Typ {picks_count} [{sport['name']}]**")
                            lines.append(f"🏟 Mecz: {home} vs {away}")
                            lines.append(f"🎯 Typ: **{team_name}** ({bet_type})")
                            lines.append(f"💰 Kurs: **{price:.2f}**\n")
                            
                            print(f"Znaleziono typ: {home} vs {away} | Kurs: {price}")
                            
                            if picks_count >= 5:
                                break
                    if picks_count >= 5:
                        break
            if picks_count >= 5:
                break
        if picks_count >= 5:
            break

    if picks_count == 0:
        print("Brak typów w przedziale value z API – używam komunikatu zastępczego.")
        lines.append("📌 **Analiza rynkowa w toku. Trwa agregacja spotkań.**")
        lines.append("🎯 Szykujemy potężny zestaw value!\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💎 *Zarządzanie bankrollem to klucz do sukcesu. Jedziemy z bukiem!*")
    
    final_message = "\n".join(lines)
    print("--- WYGENEROWANA WIADOMOŚĆ ---")
    print(final_message)
    print("-------------------------------")
    return final_message

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("BŁĄD: Brak TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID w ENV!")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(url, json=payload)
    print(f"Telegram API Status: {response.status_code}")
    print(f"Telegram API Response: {response.text}")

if __name__ == "__main__":
    print("Start skryptu bota...")
    report = generate_pro_batch()
    send_to_telegram(report)
    print("Skrypt zakończył działanie.")
