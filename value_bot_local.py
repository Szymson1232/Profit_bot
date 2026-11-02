import os
import time
import requests

# Konfiguracja zmiennych środowiskowych z Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORTS = [
    "soccer_italy_serie_a",
    "soccer_epl",
    "basketball_nba",
    "tennis"
]

REGION = "eu"

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Brak skonfigurowanego tokena Telegrama lub CHAT_ID w zmiennych środowiskowych!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        res_data = response.json()
        if not res_data.get("ok"):
            print(f"❌ Błąd wysyłania powiadomienia: {res_data}")
        else:
            print("✅ Wiadomość VIP Value Bet została wysłana na Telegram!")
    except Exception as e:
        print(f"❌ Wyjątek podczas wysyłania wiadomości na Telegram: {e}")

def check_value_bets():
    print("🔍 Skanuję rynki i wszechstronne sporty w poszukiwaniu value picków...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY w zmiennych środowiskowych!")
        return

    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
        
        try:
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                print(f"⚠️ Ostrzeżenie dla sportu {sport}: API zwróciło status {response.status_code}")
                continue

            matches = response.json()
            print(f"Pobrano {len(matches)} nadchodzących meczów dla ligi/sportu: {sport}")

            for match in matches:
                home_team = match['home_team']
                away_team = match['away_team']
                
                # Tutaj bot wysyła powiadomienie (możesz później dodać własny warunek na value)
                message = f"💎 *VALUE PICK / KURS*\n⚽ {home_team} vs {away_team}\n🎯 Sport: {sport}"
                send_telegram_message(message)
                
                # Krótki sleep, żeby nie spamić API Telegrama zbyt szybkimi żądaniami
                time.sleep(1)

        except Exception as e:
            print(f"❌ Wyjątek podczas pobierania danych dla {sport}: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam zautomatyzowanego bota na Railway...")
    while True:
        check_value_bets()
        time.sleep(7200)
