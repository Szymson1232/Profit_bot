import os
import time
import sqlite3
import requests
from datetime import datetime
from itertools import combinations

# --- KONFIGURACJA MASZYNY ZYSKU ---
API_KEY = os.getenv("ODDS_API_KEY", "d97af8a243c0f210867f0e7f905a9014")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802")

# Sztywne godziny skanowania: rano (08:00) i wieczorem (20:00)
SCHEDULED_HOURS = ["08:00", "20:00"]

# Pełna baza dyscyplin pod maszynkę zysku
SPORTS_TO_CHECK = [
    "soccer_epl",                  
    "soccer_spain_la_liga",        
    "soccer_germany_bundesliga",    
    "soccer_italy_serie_a",        
    "soccer_france_ligue_one",     
    "basketball_nba",              
    "basketball_euroleague",       
    "icehockey_nhl",               
    "tennis_atp_aus_open",         
    "baseball_mlb"                 
]

# Parametry strategii AKO
MIN_ODDS_SINGLE = 1.12
MAX_ODDS_SINGLE = 1.48
MIN_AKO_TOTAL = 2.00    # Dolna granica pod Betclic Bez Podatku
MAX_AKO_TOTAL = 2.50    # Górna granica bezpieczeństwa
MAX_AKO_EVENTS = 3      # Maksymalnie 3 zdarzenia na taśmie

def verify_environment():
    if not API_KEY:
        print("[CRITICAL ERROR] Brak klucza API!")
        return False
    masked = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
    print(f"[SYSTEM OK] Maszyna wystartowała. API Key: {masked}")
    return True

def init_db():
    conn = sqlite3.connect("monsters_bets.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_akos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ako_signature TEXT UNIQUE,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

def fetch_odds_for_sport(sport):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            return response.json()
        print(f"[API ERROR {response.status_code}] Sport {sport}")
        return []
    except Exception as e:
        print(f"[EXCEPTION] {sport}: {e}")
        return []

def extract_favorites(events, sport_name):
    favorites = []
    seen_matches = set()
    
    for event in events:
        home_team = event.get('home_team')
        away_team = event.get('away_team')
        match_key = f"{home_team}_{away_team}"
        
        if match_key in seen_matches:
            continue
        seen_matches.add(match_key)
        
        commence_time = event.get('commence_time', '')
        
        for bookmaker in event.get('bookmakers', []):
            for market in bookmaker.get('markets', []):
                if market.get('key') == 'h2h':
                    outcomes = market.get('outcomes', [])
                    for outcome in outcomes:
                        name = outcome.get('name')
                        price = outcome.get('price')
                        
                        if MIN_ODDS_SINGLE <= price <= MAX_ODDS_SINGLE:
                            favorites.append({
                                'match': f"{home_team} vs {away_team}",
                                'selection': name,
                                'odds': price,
                                'sport': sport_name,
                                'time': commence_time
                            })
                    break
            break
    return favorites

def run_analysis_cycle(reason="Harmonogram"):
    print(f"\n[CYKL] Skanowanie rynku ({reason}): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    all_favorites = []
    
    for sport in SPORTS_TO_CHECK:
        events = fetch_odds_for_sport(sport)
        favs = extract_favorites(events, sport)
        all_favorites.extend(favs)
        time.sleep(1)
        
    print(f"[ANALIZA] Wyłapano {len(all_favorites)} stabilnych faworytów.")
    
    generated_akos = 0
    conn = sqlite3.connect("monsters_bets.db")
    cursor = conn.cursor()
    
    # Zbiór śledzący mecze użyte w obecnym cyklu – ŻADEN mecz nie powtórzy się na taśmach!
    used_matches_in_cycle = set()
    
    # Sortujemy faworytów stabilnie, aby deterministycznie dobierać najlepsze kombinacje
    for r in range(2, MAX_AKO_EVENTS + 1):
        for combo in combinations(all_favorites, r):
            matches = [item['match'] for item in combo]
            
            # 1. Sprawdzenie czy w samej kombinacji nie ma duplikatu meczu
            if len(set(matches)) != len(matches):
                continue
                
            # 2. Twardy filtr: czy którykolwiek mecz z tej kombinacji został już użyty w tym cyklu?
            if any(match in used_matches_in_cycle for match in matches):
                continue
                
            total_odds = 1.0
            for item in combo:
                total_odds *= item['odds']
                
            if MIN_AKO_TOTAL <= total_odds <= MAX_AKO_TOTAL:
                signature = "_".join(sorted([f"{i['match']}:{i['selection']}" for i in combo]))
                
                # 3. Sprawdzenie w bazie SQLite czy kupon nie był wysłany wcześniej
                cursor.execute("SELECT id FROM sent_akos WHERE ako_signature = ?", (signature,))
                if cursor.fetchone() is None:
                    # Zapisujemy do bazy
                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cursor.execute("INSERT INTO sent_akos (ako_signature, timestamp) VALUES (?, ?)", (signature, now_str))
                    conn.commit()
                    
                    # Oznaczamy mecze jako wykorzystane w tym cyklu, żeby żaden inny kupon ich nie zduplikował
                    for m in matches:
                        used_matches_in_cycle.add(m)
                    
                    msg = f"<b>🎯 MASZYNA ZYSKU: MASTER AKO ({r} zdarzenia) 🎯</b>\n\n"
                    for idx, item in enumerate(combo, 1):
                        msg += f"{idx}. <b>{item['match']}</b>\n   Typ: <i>{item['selection']}</i> (Kurs: <b>{item['odds']}</b>)\n"
                    msg += f"\n📊 <b>Kurs Łączny: {total_odds:.2f}</b>\n💎 <i>Cel: Betclic (BEZ PODATKU)</i>"
                    
                    send_telegram_message(msg)
                    generated_akos += 1
                    
    conn.close()
    print(f"[AKO] Wypuszczono na kanał: {generated_akos} w pełni unikalnych kuponów (bez powtórek meczów).")

if __name__ == "__main__":
    if verify_environment():
        init_db()
        print("[SYSTEM] Bot oczekuje na zaplanowane okna czasowe (08:00 i 20:00)...")
        
        last_run_date_slot = None
        
        while True:
            try:
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")
                current_date_str = now.strftime("%Y-%m-%d")
                current_slot_id = f"{current_date_str}_{current_time_str}"
                
                if current_time_str in SCHEDULED_HOURS and last_run_date_slot != current_slot_id:
                    run_analysis_cycle(reason=f"Slot {current_time_str}")
                    last_run_date_slot = current_slot_id
                
                if current_time_str not in SCHEDULED_HOURS:
                    pass
                    
            except Exception as e:
                print(f"[CRITICAL] Błąd pętli: {e}")
                
            time.sleep(30)
