import os
import time
import requests

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

# Słownik emotek pod konkretne sporty
SPORT_EMOJIS = {
    "soccer_italy_serie_a": "⚽",
    "soccer_epl": "⚽",
    "basketball_nba": "🏀",
    "tennis": "🎾"
}

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Brak skonfigurowanego tokena Telegrama lub CHAT_ID!")
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
            print("✅ Wiadomość Value Bet wysłana na Telegram!")
    except Exception as e:
        print(f"❌ Wyjątek podczas wysyłania: {e}")

def check_value_bets():
    print("🔍 Skanuję rynki...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY!")
        return

    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
        
        try:
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                print(f"⚠️ Status {response.status_code} dla {sport}")
                continue

            matches = response.json()
            emoji = SPORT_EMOJIS.get(sport, "🎯")

            for match in matches:
                home_team = match.get('home_team')
                away_team = match.get('away_team')
                bookmakers = match.get('bookmakers', [])

                # Szukamy pierwszego lepszego bukmachera z ofertą H2H, żeby wyciągnąć kursy i typy
                for bookmaker in bookmakers:
                    bm_title = bookmaker.get('title', 'Bukmacher')
                    markets = bookmaker.get('markets', [])
                    
                    for market in markets:
                        if market.get('key') == 'h2h':
                            outcomes = market.get('outcomes', [])
                            
                            # Budujemy czytelną wiadomość z opcjami kursowymi na dany mecz
                            outcomes_text = ""
                            for outcome in outcomes:
                                name = outcome.get('name')
                                price = outcome.get('price')
                                outcomes_text += f"▪️ *{name}*: `{price}`\n"

                            # Przykładowy próg value (możesz dostosować warunek)
                            message = (
                                f"💎 *VALUE PICK*\n"
                                f"{emoji} *{home_team} vs {away_team}*\n"
                                f"🏆 Liga/Sport: `{sport}`\n"
                                f"🏢 Bukmacher: *{bm_title}*\n\n"
                                f"📊 *Kursy (1X2 / H2H):*\n"
                                f"{outcomes_text}"
                            )
                            
                            send_telegram_message(message)
                            time.sleep(1)
                            break # Wysyłamy dla meczu od pierwszego dostępnego buka i idziemy dalej
                
                # Ogranicznik, żeby nie zalać Telegrama tysiącem wiadomości na start
                time.sleep(2)

        except Exception as e:
            print(f"❌ Wyjątek dla {sport}: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam bota...")
    while True:
        check_value_bets()
        time.sleep(7200)
