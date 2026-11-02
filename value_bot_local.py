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

# Pamięć wysłanych typów, żeby nie wysłać tego samego meczu dwa razy
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
            print("✅ Wysłano pewny typ na Telegram!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def check_value_bets():
    print("🔍 Skanuję rynek w poszukiwaniu najlepszego typu pod Betclic...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY!")
        return

    # Zmienna do znalezienia absolutnie najlepszego value picka w tej turze
    best_pick = None
    highest_value_score = 0

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
                
                for bookmaker in bookmakers:
                    # Szukamy konkretnie oferty pod polski rynek / europejskich bukmacherów (np. Betclic jeśli dostępny w API lub uniwersalny europejski)
                    bm_title = bookmaker.get('title', '')
                    
                    for market in bookmaker.get('markets', []):
                        if market.get('key') == 'h2h':
                            for outcome in market.get('outcomes', []):
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                # Szukamy kursów mieszczących się w rozsądnym przedziale value (np. 2.00 - 3.50)
                                if price and 2.00 <= price <= 3.50:
                                    pick_identifier = f"{match_id}_{name}"
                                    if pick_identifier in sent_picks:
                                        continue

                                    # Algorytm oceny "wartości" (im wyższy kurs w tym przedziale, tym ciekawszy value bet)
                                    value_score = price 

                                    if value_score > highest_value_score:
                                        highest_value_score = value_score
                                        best_pick = {
                                            "emoji": emoji,
                                            "home": home_team,
                                            "away": away_team,
                                            "outcome": name,
                                            "price": price,
                                            "bookmaker": bm_title,
                                            "sport": sport,
                                            "identifier": pick_identifier
                                        }

        except Exception as e:
            print(f"❌ Wyjątek dla {sport}: {e}")

    # Jeśli znaleźliśmy jeden konkretny, najlepszy typ - wysyłamy go i kończymy temat na tę sesję
    if best_pick:
        message = (
            f"🎯 *PEWNY VALUE PICK (BETCLIC)*\n"
            f"{best_pick['emoji']} *{best_pick['home']} vs {best_pick['away']}*\n"
            f"📌 Typ: *{best_pick['outcome']}*\n"
            f"📈 Kurs: `{best_pick['price']}`\n"
            f"🏢 Bukmacher: *{best_pick['bookmaker']}*\n"
            f"🏆 Sport: `{best_pick['sport']}`"
        )
        send_telegram_message(message)
        sent_picks.add(best_pick['identifier'])
        print("✅ Wysłano wyselekcjonowany pojedynczy typ.")
    else:
        print("ℹ️ Brak typów spełniających surowe kryteria w tym rozdaniu.")

if __name__ == "__main__":
    print("🚀 Uruchamiam precyzyjnego bota...")
    while True:
        check_value_bets()
        # Sprawdzamy rynek co 2 godziny, żeby nie generować śmietnika
        time.sleep(7200)
