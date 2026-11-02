import os
import time
import requests

# Konfiguracja zmiennych środowiskowych z Railway
TELEGRAM_TOKEN = os.getenv("9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
CHAT_ID = os.getenv("7221307802")
ODDS_API_KEY = os.getenv("78de4134e569cdc3a5a251cec2ac9563")

# Lista różnych sportów do skanowania w poszukiwaniu value picków
SPORTS = [
    "soccer_italy_serie_a",
    "soccer_epl",
    "basketball_nba",
    "tennis_atp"
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
            
            # Obsługa błędów API (np. 502, 422 itp.) bez wywalania bota
            if response.status_code != 200:
                print(f"⚠️ Ostrzeżenie dla sportu {sport}: API zwróciło status {response.status_code} - {response.text}")
                continue  # Przechodzimy do kolejnego sportu zamiast crashować skrypt

            matches = response.json()
            print(f"Pobrano {len(matches)} nadchodzących meczów dla ligi/sportu: {sport}")

            for match in matches:
                home_team = match['home_team']
                away_team = match['away_team']
                
                # Tutaj wchodzi Twoja logika obliczania value i bukmacherów
                # Przykład detekcji value picka (przykładowy warunek/placeholder pod rozbudowę):
                # if detected_value:
                #     message = f"💎 *VIP VALUE PICK*\n⚽ {home_team} vs {away_team}\n🎯 Sport: {sport}"
                #     send_telegram_message(message)

        except Exception as e:
            print(f"❌ Wyjątek podczas pobierania danych dla {sport}: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam zautomatyzowanego bota na Railway...")
    while True:
        check_value_bets()
        # Skanowanie rynku co 2 godziny (7200 sekund), żeby nie przepalić limitu zapytań API
        time.sleep(7200)
