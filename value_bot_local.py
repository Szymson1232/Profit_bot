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

SPORT_EMOJIS = {
    "soccer_italy_serie_a": "⚽",
    "soccer_epl": "⚽",
    "basketball_nba": "🏀",
    "tennis": "🎾"
}

# Globalna pamięć unikalnych typów w tej sesji
sent_picks = set()

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
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
        if res_data.get("ok"):
            print("✅ Wysłano selekcjonowany Value Pick na Telegram!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def check_value_bets():
    print("🔍 Skanuję rynki i selekcjonuję najwyższą wartość...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY!")
        return

    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
        
        try:
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                continue

            matches = response.json()
            emoji = SPORT_EMOJIS.get(sport, "🎯")

            for match in matches:
                match_id = match.get('id')
                home_team = match.get('home_team')
                away_team = match.get('away_team')
                bookmakers = match.get('bookmakers', [])
                
                if not bookmakers:
                    continue

                # Słownik do wyciągnięcia najlepszego kursu dla każdego typu w meczu
                best_outcomes = {}

                for bookmaker in bookmakers:
                    bm_title = bookmaker.get('title')
                    for market in bookmaker.get('markets', []):
                        if market.get('key') == 'h2h':
                            for outcome in market.get('outcomes', []):
                                name = outcome.get('name')
                                price = outcome.get('price')
                                if price:
                                    if name not in best_outcomes or price > best_outcomes[name]["price"]:
                                        best_outcomes[name] = {
                                            "price": price,
                                            "bookmaker": bm_title
                                        }

                # Selekcja i wysyłka tylko wtedy, gdy spełnia kryteria value
                for outcome_name, data in best_outcomes.items():
                    price = data["price"]
                    bm_title = data["bookmaker"]

                    # Unikalny klucz: ID meczu + nazwa typu (zapobiega jakimkolwiek powtórzeniom)
                    pick_identifier = f"{match_id}_{outcome_name}"

                    if price >= 2.10 and pick_identifier not in sent_picks:
                        message = (
                            f"💎 *VALUE PICK*\n"
                            f"{emoji} *{home_team} vs {away_team}*\n"
                            f"🎯 Typ: *{outcome_name}*\n"
                            f"📈 Najlepszy Kurs: `{price}`\n"
                            f"🏢 Bukmacher: *{bm_title}*\n"
                            f"🏆 Sport: `{sport}`"
                        )
                        
                        send_telegram_message(message)
                        sent_picks.add(pick_identifier)
                        time.sleep(1.5)

        except Exception as e:
            print(f"❌ Wyjątek dla {sport}: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam zoptymalizowanego bota...")
    while True:
        check_value_bets()
        # Skanowanie rynku co 2 godziny
        time.sleep(7200)
