import os
import time
import requests

# Konfiguracja środowiskowa
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORT = "soccer_italy_serie_a"  # Przykładowy sport / liga
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
            print(f"❌ Błąd wysyłania: {res_data}")
        else:
            print("✅ Wiadomość VIP została wysłana na Telegram!")
    except Exception as e:
        print(f"❌ Wyjątek podczas wysyłania wiadomości na Telegram: {e}")

def check_value_bets():
    print("🔍 Skanuję rynek w poszukiwaniu meczów i kursów...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY w zmiennych środowiskowych!")
        return

    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Błąd pobierania danych z API: {response.status_code} - {response.text}")
            return  # Bezpieczny powrót, bot nie crashuje, spróbuje w kolejnym cyklu

        matches = response.json()
        print(f"Pobrano {len(matches)} nadchodzących meczów.")

        for match in matches:
            home_team = match['home_team']
            away_team = match['away_team']
            print(f"Znaleziono mecz: {home_team} vs {away_team}")
            
            # Przykładowe powiadomienie testowe VIP (możesz dostosować format)
            # message = f"💎 *VIP VALUE BET*\n⚽ {home_team} vs {away_team}"
            # send_telegram_message(message)
            # break # odkomentuj, jeśli chcesz wysłać tylko pierwszy mecz do testów

    except Exception as e:
        print(f"❌ Wyjątek podczas połączenia z API: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam bota na Railway...")
    while True:
        check_value_bets()
        # Czekaj np. 2 godziny (7200 sekund) przed kolejnym skanowaniem
        time.sleep(7200)
