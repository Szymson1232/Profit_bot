def generate_pro_batch():
    now = datetime.now().strftime("%d.%m.%Y")
    
    # Budujemy jedną solidną listę tekstową
    lines = [
        "🔥 **@PROFIT VIP – DZIENNY PAKIET EKSPERCKI** 🚀",
        f"📅 Data: {now} | Status: PRODUKCJA",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    picks_count = 0
    
    for sport in SPORTS_ENDPOINTS:
        events = fetch_odds_for_sport(sport["key"])
        
        for event in events[:2]:
            home = event.get("home_team")
            away = event.get("away_team")
            bookmakers = event.get("bookmakers", [])
            
            if not bookmakers:
                continue
                
            markets = bookmakers[0].get("markets", [])
            for market in markets:
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        price = outcome.get("price", 0.0)
                        if 1.70 <= price <= 3.40:
                            team_name = outcome.get("name")
                            picks_count += 1
                            
                            bet_type = "Single (Value)" if price >= 2.05 else "Bezpieczny / AKO"
                            
                            lines.append(f"🔹 **Typ {picks_count} [{sport['name']}]**")
                            lines.append(f"🏟 Mecz: {home} vs {away}")
                            lines.append(f"🎯 Typ: **{team_name}** ({bet_type})")
                            lines.append(f"💰 Kurs: **{price:.2f}**\n")
                            
                            if picks_count >= 5:
                                break
                    if picks_count >= 5:
                        break
            if picks_count >= 5:
                break
        if picks_count >= 5:
            break

    if picks_count == 0:
        lines.append("📌 **Analiza rynkowa w toku. Trwa agregacja spotkań.**")
        lines.append("🎯 Szykujemy potężny zestaw value!\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💎 *Zarządzanie bankrollem to klucz do sukcesu. Jedziemy z bukiem!*")
    
    # Łączymy wszystko w jeden string oddzielony nowymi liniami
    return "\n".join(lines)

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Brak tokenów Telegrama w ENV!")
        print(text)
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(url, json=payload)
    print(f"Status Telegram API: {response.status_code}")
    print(f"Odpowiedź Telegrama: {response.text}")
