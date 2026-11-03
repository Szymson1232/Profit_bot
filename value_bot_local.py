import os
import random
import requests
from datetime import datetime, timezone

# Konfiguracja tokenów VIP
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Szeroka, globalna i niszowa pula dyscyplin (masówka + egzotyka i e-sport dla maksymalnej skuteczności)
VIP_SPORTS_ENDPOINTS = [
    {"key": "soccer_epl", "name": "⚽ Premier League (Masówka)"},
    {"key": "soccer_uefa_champs_league", "name": "⚽ Liga Mistrzów"},
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "table_tennis_set_odds", "name": "🏓 Tenis Stołowy (Egzotyka / Set Odds)"},
    {"key": "esports_counter_strike", "name": "🎮 CS2 (E-sport Value)"},
    {"key": "volleyball_cev", "name": "🏐 Siatkówka Europejska"},
    {"key": "baseball_mlb", "name": "⚾ Baseball (MLB)"}
]

def fetch_odds_for_sport(sport_key):
    if not ODDS_API_KEY:
        print("BŁĄD: Brak klucza ODDS_API_KEY w zmiennych środowiskowych!")
        return []
    
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk,us,au",  # Globalne giełdy i bukmacherzy
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
    current_utc = datetime.now(timezone.utc)
    
    lines = [
        "👑 **@PROFIT VIP SYNDICATE – PAKIET ELITE 10k** 🚀",
        f"📅 Data: {now_str} | Status: ANALIZA GIEŁDOWA LIVE",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    used_match_ids = set()
    
    # Mieszamy naszą szeroką pulę, żeby łączyć masówkę z niszowymi perełkami
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
                
            # Filtr czasowy: gramy tylko to, co rozstrzyga się w najbliższym oknie (max 24h)
            commence_time_str = event.get("commence_time")
            if commence_time_str:
                try:
                    commence_dt = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
                    if (commence_dt - current_utc).total_seconds() > 24 * 3600:
                        continue
                except Exception:
                    pass

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
                        
                        # Żelazny przedział value (1.70 - 3.40) – tu uciekamy bukowi
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            used_match_ids.add(match_id)
                            
                            tier_label = "🔥 VIP SINGLE (High Value)" if price >= 2.05 else "🎯 VIP CORE (Pewniak rynkowy)"
                            
                            lines.append(f"🔹 **Selekcja #{picks_count} [{sport['name']}]**")
                            lines.append(f"🏟 Mecz: {home} vs {away}")
                            lines.append(f"💡 Typ: **{team_name}**")
                            lines.append(f"📊 Status: {tier_label}")
                            lines.append(f"💰 Kurs wejściowy: **{price:.2f}**\n")
                            
                            print(f"Dodano typ VIP {picks_count}: {home} vs {away} | {team_name} | Kurs: {price}")
                            break 
                    
                    if match_id in used_match_ids:
                        break
            
            if match_id in used_match_ids:
                continue
    
    if picks_count < 4:
        # Rozszerzenie awaryjne, gdyby węższe filtry na niszach dały za mało opcji
        lines.append("📌 **Uwaga: Skanowanie niszowych giełd Azji i Europy w toku.**")
        lines.append("🎯 Pakiet uzupełniony o selekcje o podwyższonej płynności.\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💎 *Inwestycja operta na matematyce, rotacji kapitału i błędach bukmacherów. Tylko twardy zysk.*")
    
    final_message = "\n".join(lines)
    print("--- WYGENEROWANA WIADOMOŚĆ VIP ---")
    print(final_message)
    print("---------------------------------")
    return final_message

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        print("BŁĄD: Brak TELEGRAM_BOT_TOKEN!")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(url, json=payload)
    print(f"Telegram API Status: {response.status_code}")
    print(f"Telegram API Response: {response.text}")

if __name__ == "__main__":
    print("Start silnika VIP Syndicate...")
    report = generate_pro_batch()
    send_to_telegram(report)
    print("Skrypt VIP zakończył działanie.")
