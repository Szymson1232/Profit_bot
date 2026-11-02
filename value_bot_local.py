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

# Pamięć wysłanych konkretnych typów, żeby nie duplikować
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
            print("✅ Wysłano value pick na Telegram!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def check_value_bets():
    print("🔍 Skanuję rynki w poszukiwaniu konkretnych value picków...")
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
                home_team = match.get('home_team')
                away_team = match.get('away_team')
                
                bookmakers = match.get('bookmakers', [])
                if not bookmakers:
                    continue

                for bookmaker in bookmakers:
                    bm_title = bookmaker.get('title', 'Bukmacher')
                    markets = bookmaker.get('markets', [])
                    
                    for market in markets:
                        if market.get('key') == 'h2h':
                            for outcome in market.get('outcomes', []):
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                # Warunek value dla konkretnego typu (kurs >= 2.00)
                                if price and price >= 2.00:
                                    pick_key = f"{sport}_{home_team}_{away_team}_{name}_{bm_title}"
                                    if pick_key in sent_picks:
                                        continue

                                    message = (
                                        f"💎 *VALUE PICK*\n"
                                        f"{emoji} *{home_team} vs {away_team}*\n"
                                        f"🎯 Typ: *{name}*\n"
                                        f"📈 Kurs: `{price}`\n"
                                        f"🏢 Bukmacher: *{bm_title}*\n"
                                        f"🏆 Sport: `{sport}`"
                                    )
                                    
                                    send_telegram_message(message)
                                    sent_picks.add(pick_key)
                                    time.sleep(1)

        except Exception as e:
            print(f"❌ Wyjątek dla {sport}: {e}")

if __name__ == "__main__":
    print("🚀 Uruchamiam bota...")
    while True:
        check_value_bets()
        time.sleep(7200)
