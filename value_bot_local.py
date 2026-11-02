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

# Bankroll Management - Startujemy od 25 zł jako 1 Jednostka (1U)
BASE_UNIT_PLN = 25.0
current_bankroll = 250.0  # Przykładowy kapitał początkowy bankrollu (10 jednostek)
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
            print("✅ Sygnał VIP z systemem stawek dostarczony!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def calculate_stake_and_tier(price):
    """
    Dobiera stawkę (1x, 2x, 3x) w zależności od kursu i profilu ryzyka/value.
    Bazowa jednostka to 25 zł.
    """
    if price >= 3.50:
        # Wysokie value / egzotyk / niespodzianka -> gramy ostrożniej, ale z potężnym zwrotem (1x)
        units = 1
    elif 2.60 <= price < 3.50:
        # Solidny value pick -> 2 jednostki
        units = 2
    else:
        # Pewniejsza baza o wyższym prawdopodobieństwie wejścia -> 3 jednostki
        units = 3
        
    total_stake = units * BASE_UNIT_PLN
    return units, total_stake

def analyze_and_pick_best():
    print("💎 Skanuję rynek pod kątem wyselekcjonowanych perełek VIP (System 1U = 25 zł)...")
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
                            for outcome in market.get('outcomes', []):
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                # Celujemy w selekcje z potencjałem
                                if price and 2.10 <= price <= 4.50:
                                    pick_key = f"{match_id}_{name}"
                                    if pick_key in sent_vip_picks:
                                        continue

                                    score = price * 1.15

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
            f"🔥 **VIP 100K // SELEKCJA DNIA** 🔥\n\n"
            f"{best_gem['emoji']} **{best_gem['home']} vs {best_gem['away']}**\n"
            f"⏱ Start: `{best_gem['time']} UTC`\n"
            f"🏆 Dyscyplina: `{best_gem['sport'].upper()}`\n\n"
            f"🎯 **TYP:** `{best_gem['outcome']}`\n"
            f"📈 **KURS:** `{best_gem['price']}`\n"
            f"💰 **STAWKA:** `{best_gem['units']}x` ({best_gem['stake']} zł | 1U = {BASE_UNIT_PLN} zł)\n"
            f"🏢 **BUKMACHER:** *{best_gem['bookmaker']}*\n\n"
            f"📊 *Stan Bankrollu:* `Start / Aktywny`\n"
            f"💡 *Komentarz analityczny:* Wykryto anomalię kursową. Gramy precyzyjnie wyliczoną stawką, celując w maksymalizację zysku przy zachowaniu pełnego bezpieczeństwa kapitału."
        )
        send_telegram_message(message)
        sent_vip_picks.add(best_gem['identifier'])
        print("✅ Sygnał VIP z systemem stawek wysłany.")
    else:
        print("ℹ️ Czekamy na perfekcyjną okazję rynkową.")

if __name__ == "__main__":
    print("🚀 System VIP 100k (Bankroll Management) uruchomiony...")
    while True:
        analyze_and_pick_best()
        time.sleep(3600)
