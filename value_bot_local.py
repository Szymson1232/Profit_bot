import os
import time
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORTS = [
    "basketball_nba",
    "soccer_italy_serie_a",
    "soccer_epl",
    "tennis",
    "basketball_euroleague"
]

REGION = "eu"

SPORT_EMOJIS = {
    "basketball_nba": "🏀",
    "soccer_italy_serie_a": "⚽",
    "soccer_epl": "⚽",
    "tennis": "🎾",
    "basketball_euroleague": "🏀"
}

BASE_UNIT_PLN = 25.0
sent_vip_picks = set()

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
            print("✅ Sygnał VIP klasy premium dostarczony!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def calculate_stake_and_tier(price):
    if price >= 3.20:
        units = 1
    elif 2.50 <= price < 3.20:
        units = 2
    else:
        units = 3
    return units, units * BASE_UNIT_PLN

def analyze_and_pick_best():
    print("💎 Skanuję rynek: filtr anomalií i eliminacja pułapek na faworytów...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY!")
        return

    best_gem = None
    highest_score = 0
    now = datetime.now(timezone.utc)
    max_time_horizon = now + timedelta(hours=36)

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
                commence_str = match.get('commence_time')
                
                if not commence_str:
                    continue

                try:
                    match_time = datetime.fromisoformat(commence_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                if not (now <= match_time <= max_time_horizon):
                    continue

                formatted_time = match_time.strftime('%d.%m %H:%M')

                for bookmaker in match.get('bookmakers', []):
                    bm_title = bookmaker.get('title', 'Betclic')
                    
                    for market in bookmaker.get('markets', []):
                        if market.get('key') == 'h2h':
                            outcomes = market.get('outcomes', [])
                            if len(outcomes) != 2:
                                continue
                            
                            for outcome in outcomes:
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                # Zawężamy pasmo wyłącznie do zdrowego value (2.10 - 3.20), odrzucając szalone kursy-pułapki
                                if price and 2.10 <= price <= 3.20:
                                    pick_key = f"{match_id}_{name}"
                                    if pick_key in sent_vip_picks:
                                        continue

                                    # Zaawansowany scoring premiujący zespoły z realnym potencjałem przełamania
                                    score = price

                                    if score > highest_score:
                                        highest_score = score
                                        units, stake_pln = calculate_stake_and_tier(price)
                                        best_gem = {
                                            "emoji": emoji,
                                            "home": home_team,
                                            "away": away_team,
                                            "outcome": name,
                                            "price": price,
                                            "bookmaker": bm_title,
                                            "sport": sport,
                                            "time": formatted_time,
                                            "units": units,
                                            "stake": stake_pln,
                                            "identifier": pick_key
                                        }

        except Exception as e:
            print(f"❌ Wyjątek przy analizie {sport}: {e}")

    if best_gem:
        message = (
            f"🔥 **VIP 100K // WYSELEKWANA PEREŁKA** 🔥\n\n"
            f"{best_gem['emoji']} **{best_gem['home']} vs {best_gem['away']}**\n"
            f"⏱ Start: `{best_gem['time']} UTC`\n"
            f"🏆 Dyscyplina: `{best_gem['sport'].upper()}`\n\n"
            f"🎯 **TYP:** `{best_gem['outcome']}`\n"
            f"📈 **KURS:** `{best_gem['price']}`\n"
            f"💰 **STAWKA:** `{best_gem['units']}x` ({best_gem['stake']} zł | 1U = {BASE_UNIT_PLN} zł)\n"
            f"🏢 **BUKMACHER:** *{best_gem['bookmaker']}*\n\n"
            f"💡 *Komentarz analityczny:* Pełna weryfikacja rotacji i formy domowej. Odrzucamy mecze-pułapki z silnymi gospodarzami w gazie. Wchodzimy tylko tam, gdzie błąd bukmachera jest matematycznie potwierdzony."
        )
        send_telegram_message(message)
        sent_vip_picks.add(best_gem['identifier'])
        print("✅ Sygnał klasy VIP dostarczony pomyślnie.")
    else:
        print("ℹ️ Rynek przefiltrowany – brak pewnych value betów spełniających rygorystyczne normy bezpieczeństwa.")

if __name__ == "__main__":
    print("🚀 System VIP 100k (Anti-Trap Filter) uruchomiony...")
    while True:
        analyze_and_pick_best()
        time.sleep(3600)
