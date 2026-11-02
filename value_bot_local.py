import requests
import time

# --- KONFIGURACJA (WPISZ SWOJE DANE) ---
TELEGRAM_TOKEN = "9258087483:AAFaDk---UhFbJLKc6hx0ZywwI5Qt2-8qiM"
CHAT_ID = "7221307802"

# Darmowy klucz z the-odds-api.com
ODDS_API_KEY = "78de4134e569cdc3a5a251cec2ac9563"
SPORT = "soccer_italy_serie_a"  # Wybieramy np. Serie A pod naszą dzisiejszą Atalantę!
REGION = "eu"

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        print("✅ Alert wysłany na Telegram pomyślnie!")
    else:
        print(f"❌ Błąd wysyłania: {response.text}")

def check_value_bets():
    print("🔍 Skanuję rynek w poszukiwaniu meczów i kursów...")
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
    
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Błąd pobierania danych z API: {response.status_code} - {response.text}")
        return

    matches = response.json()
    print(f"Pobrano {len(matches)} nadchodzących meczów.")

    for match in matches:
        home_team = match['home_team']
        away_team = match['away_team']
        
        # Pobieramy kursy od pierwszego dostępnego bukmachera w API
        bookmakers = match.get('bookmakers', [])
        if not bookmakers:
            continue
            
        bookie = bookmakers[0]
        bookie_name = bookie['title']
        market = bookie['markets'][0]
        outcomes = {out['name']: out['price'] for out in market['outcomes']}
        
        home_odds = outcomes.get(home_team)
        away_odds = outcomes.get(away_team)
        
        # PROSTA LOGIKA TESTOWA: 
        # Szukamy meczu, gdzie kurs na gospodarza jest warty uwagi (np. > 1.40)
        if home_odds and home_odds > 1.40:
            message = (
                f"🔥 **TESTOWy VALUE ALERT** 🔥\n\n"
                f"⚽ **Mecz:** {home_team} vs {away_team}\n"
                f"🎯 **Sprawdzany typ:** 1 (Gospodarz)\n"
                f"📊 **Aktualny kurs ({bookie_name}):** `{home_odds}`\n"
                f"💰 **Zalecana stawka:** 50 PLN (Flat)\n\n"
                f"🤖 *Bot testowy uruchomiony lokalnie.*"
            )
            
            # Wypisujemy w konsoli i wysyłamy na Telegram
            print(f"Znaleziono mecz: {home_team} vs {away_team} (Kurs: {home_odds})")
            send_telegram_message(message)
            break  # Wysyłamy jeden testowy i przerywamy pętlę na ten moment

if __name__ == "__main__":
    # Uruchomienie jednorazowe do testu lokalnego
    check_value_bets()