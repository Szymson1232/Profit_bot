import os
import random
import requests
from datetime import datetime

# Stałe konfiguracyjne - Twardo wkodowane dla pewności działania
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"
ODDS_API_KEY = os.getenv("99e1b40f31bdf20559cb664785d538b8")
# Pula selektywnych rynków nocnych i globalnych
VIP_SPORTS_ENDPOINTS = [
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "basketball_euroleague", "name": "🏀 Euroleague"},
    {"key": "esports_counter_strike", "name": "🎮 CS2 (E-sport Value)"},
    {"key": "soccer_usa_mls", "name": "⚽ MLS / Ameryka Północna"}
]

def fetch_odds_for_sport(sport_key):
    if not ODDS_API_KEY:
        print("BŁĄD: Brak klucza ODDS_API_KEY w zmiennych środowiskowych Railway!")
        return []
    
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us,uk",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(url, params=params)
        print(f"API Odds [{sport_key}] - Status HTTP: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Pobrano wydarzeń dla {sport_key}: {len(data)}")
            return data
        else:
            print(f"Błąd API dla {sport_key}: {response.text}")
    except Exception as e:
        print(f"Wyjątek przy pobieraniu danych dla {sport_key}: {e}")
    return []

def generate_pro_batch():
    now_str = datetime.now().strftime("%d.%m.%Y")
    
    lines = [
        "👑 **@PROFIT VIP SYNDICATE – NOCNY PAKIET ELITE** 🚀",
        f"📅 Data: {now_str} | Status: PRODUKCJA LIVE",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    used_match_ids = set()
    
    shuffled_sports = list(VIP_SPORTS_ENDPOINTS)
    random.shuffle(shuffled_sports)
    
    for sport in shuffled_sports:
        if picks_count >= 6:
            break
            
        print(f"Skanowanie rynków: {sport['name']}...")
        events = fetch_odds_for_sport(sport["key"])
        
        for event in events:
            if picks_count >= 6:
                break
                
            match_id = event.get("id")
            if match_id in used_match_ids:
                continue
                
            home = event.get("home_team")
            away = event.get("away_team")
            bookmakers = event.get("bookmakers", [])
            
            if not bookmakers:
                continue
                
            markets = bookmakers[0].get("markets", [])
            for market in markets:
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    random.shuffle(outcomes)
                    
                    for outcome in outcomes:
                        price = outcome.get("price", 0.0)
                        
                        # Żelazny przedział value
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            used_match_ids.add(match_id)
                            
                            # System stawek: 2j (50 zł) dla mocnych kursów, 1j (25 zł) dla value
                            if price < 2.05:
                                stake_units = "2j"
                                stake_pln = 50
                                tier_label = "🎯 PEWNY KIERUNEK (CORE)"
                            else:
                                stake_units = "1j"
                                stake_pln = 25
                                tier_label = "🔥 VALUE HUNT (HIGH ODDS)"
                            
                            lines.append(f"🔹 **Selekcja #{picks_count} [{sport['name']}]**")
                            lines.append(f"🏟 Mecz: {home} vs {away}")
                            lines.append(f"💡 Typ: **{team_name}**")
                            lines.append(f"📊 Status: {tier_label}")
                            lines.append(f"💰 Kurs: **{price:.2f}** | Stawka: **{stake_units} ({stake_pln} zł)**\n")
                            
                            print(f"Dodano typ VIP {picks_count}: {home} vs {away} | {team_name} | Kurs: {price} | Stawka: {stake_units}")
                            break 
                    
                    if match_id in used_match_ids:
                        break
            
            if match_id in used_match_ids:
                continue
    
    if picks_count == 0:
        print("Brak typów w przedziale value – dodaję komunikat awaryjny.")
        lines.append("📌 **Skanowanie giełd i nisz w toku. Trwa agregacja nocnych spotkań.**")
        lines.append("🎯 Kolejna fala typów już w drodze!\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💎 *Zarządzanie kapitałem: 1j = 25 zł, 2j = 50 zł. Gramy chłodną matematikę.*")
    
    final_message = "\n".join(lines)
    print("--- WYGENEROWANA WIADOMOŚĆ VIP ---")
    print(final_message)
    print("---------------------------------")
    return final_message

def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        print(f"Telegram API Status: {response.status_code}")
        print(f"Telegram API Response: {response.text}")
        if response.status_code != 200:
            print("WPROWADŹ KOREKTĘ: Upewnij się, że bot jest administratorem na kanale Telegrama!")
    except Exception as e:
        print(f"Wyjątek podczas wysyłania do Telegrama: {e}")

if __name__ == "__main__":
    print("Start silnika VIP Syndicate (Final Deploy)...")
    report = generate_pro_batch()
    send_to_telegram(report)
    print("Skrypt VIP zakończył działanie.")
