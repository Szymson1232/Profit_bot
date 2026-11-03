import random
import requests
from datetime import datetime

# --- WKLEJ TUTAJ NOWY TOKEN Z BOTFATHERRAEL ---
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"
ODDS_API_KEY = "99e1b40f31bdf20559cb664785d538b8"

VIP_SPORTS_ENDPOINTS = [
    {"key": "basketball_nba", "name": "🏀 Koszykówka (NBA)"},
    {"key": "icehockey_nhl", "name": "🏒 Hokej (NHL)"},
    {"key": "basketball_euroleague", "name": "🏀 Euroleague"},
    {"key": "esports_counter_strike", "name": "🎮 CS2 (E-sport Value)"},
    {"key": "soccer_usa_mls", "name": "⚽ MLS / Ameryka Północna"}
]

def verify_telegram_token():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        res = requests.get(url)
        print(f"Test Tokena Telegrama - Status: {res.status_code}")
        print(f"Odpowiedź Telegrama: {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"Błąd testu tokena: {e}")
        return False

def fetch_odds_for_sport(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us,uk",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            print(f"Pobrano wydarzeń dla {sport_key}: {len(data)}")
            return data
    except Exception as e:
        print(f"Błąd dla {sport_key}: {e}")
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
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            used_match_ids.add(match_id)
                            
                            stake_units = "2j" if price < 2.05 else "1j"
                            stake_pln = 50 if price < 2.05 else 25
                            tier_label = "🎯 PEWNY KIERUNEK (CORE)" if price < 2.05 else "🔥 VALUE HUNT (HIGH ODDS)"
                            
                            lines.append(f"🔹 **Selekcja #{picks_count} [{sport['name']}]**")
                            lines.append(f"🏟 Mecz: {home} vs {away}")
                            lines.append(f"💡 Typ: **{team_name}**")
                            lines.append(f"📊 Status: {tier_label}")
                            lines.append(f"💰 Kurs: **{price:.2f}** | Stawka: **{stake_units} ({stake_pln} zł)**\n")
                            break 
                    if match_id in used_match_ids:
                        break
            if match_id in used_match_ids:
                continue
                
    if picks_count == 0:
        lines.append("📌 **Skanowanie giełd i nisz w toku. Trwa agregacja nocnych spotkań.**\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💎 *Zarządzanie kapitałem: 1j = 25 zł, 2j = 50 zł. Gramy chłodną matematikę.*")
    return "\n".join(lines)

def send_to_telegram(text):
    if not verify_telegram_token():
        print("KRYTYCZNY BŁĄD: Token Telegrama jest niepoprawny! Wygeneruj nowy u @BotFather.")
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
