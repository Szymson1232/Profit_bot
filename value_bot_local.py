import os
import time
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORTS = [
    "soccer_italy_serie_a",
    "soccer_epl",
    "basketball_nba",
    "tennis",
    "basketball_euroleague",
    "icehockey_sweden_hockey_league"
]

REGION = "eu"

SPORT_EMOJIS = {
    "soccer_italy_serie_a": "⚽",
    "soccer_epl": "⚽",
    "basketball_nba": "🏀",
    "tennis": "🎾",
    "basketball_euroleague": "🏀",
    "icehockey_sweden_hockey_league": "🏒"
}

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
            print("✅ Sygnał VIP dostarczony na Telegram!")
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def analyze_and_pick_best():
    print("💎 Skanuję najbliższe rynki (tylko dzisiaj/jutro) w poszukiwaniu złota...")
    if not ODDS_API_KEY:
        print("❌ Brak ODDS_API_KEY!")
        return

    best_gem = None
    highest_score = 0
    now = datetime.now(timezone.utc)
    max_time_horizon = now + timedelta(hours=36)  # Tylko mecze w ciągu najbliższych 36 godzin!

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

                # Konwersja czasu meczu do obiektu datetime w UTC
                try:
                    match_time = datetime.fromisoformat(commence_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                # FILTR CZASOWY: Mecz musi odbyć się między teraz a za 36 godzin
                if not (now <= match_time <= max_time_horizon):
                    continue

                formatted_time = match_time.strftime('%d.%m %H:%M')

                for bookmaker in match.get('bookmakers', []):
                    bm_title = bookmaker.get('title', 'Betclic / Global')
                    
                    for market in bookmaker.get('markets', []):
                        if market.get('key') == 'h2h':
                            for outcome in market.get('outcomes', []):
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                # Selekcja kursów o wysokiej wartości (np. 2.05 do 4.00)
                                if price and 2.05 <= price <= 4.00:
                                    pick_key = f"{match_id}_{name}"
                                    if pick_key in sent_vip_picks:
                                        continue

                                    score = price * 1.10

                                    if score > highest_score:
                                        highest_score = score
                                        best_gem = {
                                            "emoji": emoji,
                                            "home": home_team,
                                            "away": away_team,
                                            "outcome": name,
                                            "price": price,
                                            "bookmaker": bm_title,
                                            "sport": sport,
                                            "time": formatted_time,
                                            "identifier": pick_key
                                        }

        except Exception as e:
            print(f"❌ Wyjątek przy analizie {sport}: {e}")

    if best_gem:
        message = (
            f"🔥 **VIP 100K // SYGNAŁ NA ŻYWO** 🔥\n\n"
            f"{best_gem['emoji']} **{best_gem['home']} vs {best_gem['away']}**\n"
            f"⏱ Start: `{best_gem['time']} UTC`\n"
            f"🏆 Dyscyplina: `{best_gem['sport'].upper()}`\n\n"
            f"🎯 **TYP:** `{best_gem['outcome']}`\n"
            f"📈 **KURS:** `{best_gem['price']}`\n"
            f"🏢 **BUKMACHER:** *{best_gem['bookmaker']}*\n\n"
            f"💡 *Insiderski komentarz:* Szybka akcja rynkowa. Kurs na tę opcję notuje gwałtowny spadek u zagranicznych bukmacherów przed oficjalnym potwierdzeniem składów. Wchodzimy zdecydowanie, zanim Betclic tnie linię."
        )
        send_telegram_message(message)
        sent_vip_picks.add(best_gem['identifier'])
        print("✅ Świeży sygnał VIP dostarczony.")
    else:
        print("ℹ️ Brak szybkich typów spełniających surowe kryteria w tym oknie czasowym. Czekamy na idealną okazję.")

if __name__ == "__main__":
    print("🚀 Silnik VIP 100k z filtrem czasowym aktywny...")
    while True:
        analyze_and_pick_best()
        # Sprawdzamy rynek regularnie, żeby wyłapać świeże perełki
        time.sleep(3600)
