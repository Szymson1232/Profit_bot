import os
import time
import sqlite3
import requests
from datetime import datetime, timedelta
from itertools import combinations

# --- KONFIGURACJA MASZYNY ZYSKU ---
API_KEY = os.getenv("ODDS_API_KEY", "d97af8a243c0f210867f0e7f905a9014")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802")

# Ścisłe godziny VIP (06:25 oraz 18:25 czasu polskiego) - ŻADNYCH INNYCH
SCHEDULED_HOURS = ["06:25", "18:25"]

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

# Parametry żelaznej strategii VIP AKO
MIN_ODDS_SINGLE = 1.10
MAX_ODDS_SINGLE = 1.52
MIN_AKO_TOTAL = 2.00    # Dolna granica pod Betclic Bez Podatku
MAX_AKO_TOTAL = 2.55    # Górna granica bezpieczeństwa
MAX_AKO_EVENTS = 3      # Maksymalnie 3 zdarzenia na taśmie

MAX_AKO_PER_CYCLE = 5

def verify_environment():
    if not API_KEY:
        print("[CRITICAL ERROR] Brak klucza API!")
        return False
    masked = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
    print(f"[SYSTEM VIP OK] Chiński analityczny mózg wystartował. API Key: {masked}")
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

def parse_iso_time(time_str):
    try:
        if time_str.endswith('Z'):
            time_str = time_str[:-1]
        return datetime.fromisoformat(time_str)
    except Exception:
        return None

def calculate_stake_and_confidence(combo, total_odds):
    avg_odds = sum(item['odds'] for item in combo) / len(combo)
    if avg_odds <= 1.25 and len(combo) == 2:
        return "3j (75 zł)", "💎💎💎 Masywne Value / Najwyższa pewność"
    elif avg_odds <= 1.35:
        return "2j (50 zł)", "⭐⭐⭐ Wysoka pewność"
    else:
        return "1j (25 zł)", "⭐⭐ Standardowy pewniak"

def extract_favorites(events, sport_name):
    favorites = []
    seen_matches = set()
    now = datetime.now()
    max_allowed_date = now + timedelta(hours=48)
    
    for event in events:
        commence_time_str = event.get('commence_time', '')
        event_time = parse_iso_time(commence_time_str)
        
        if event_time and (event_time < now or event_time > max_allowed_date):
            continue
            
        home_team = event.get('home_team')
        away_team = event.get('away_team')
        match_key = f"{home_team}_{away_team}"
        
        if match_key in seen_matches:
            continue
        seen_matches.add(match_key)
        
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
                                'time': commence_time_str
                            })
                    break
            break
    return favorites

def run_analysis_cycle(reason="Harmonogram VIP"):
    print(f"\n[CYKL VIP] Skanowanie rynku ({reason}): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    all_favorites = []
    
    for sport in SPORTS_TO_CHECK:
        events = fetch_odds_for_sport(sport)
        favs = extract_favorites(events, sport)
        all_favorites.extend(favs)
        time.sleep(1)
        
    print(f"[ANALIZA] Wyłapano {len(all_favorites)} stabilnych faworytów.")
    
    candidates_to_send = []
    
    for r in range(2, MAX_AKO_EVENTS + 1):
        for combo in combinations(all_favorites, r):
            matches = [item['match'] for item in combo]
            if len(set(matches)) != len(matches):
                continue
                
            total_odds = 1.0
            for item in combo:
                total_odds *= item['odds']
                
            if MIN_AKO_TOTAL <= total_odds <= MAX_AKO_TOTAL:
                signature = "_".join(sorted([f"{i['match']}:{i['selection']}" for i in combo]))
                candidates_to_send.append({
                    'signature': signature,
                    'combo': combo,
                    'total_odds': total_odds,
                    'r': r,
                    'matches': matches
                })
                
    candidates_to_send.sort(key=lambda x: x['total_odds'], reverse=True)
    
    generated_akos = 0
    conn = sqlite3.connect("monsters_bets.db")
    cursor = conn.cursor()
    used_matches_in_cycle = set()
    
    for cand in candidates_to_send:
        if generated_akos >= MAX_AKO_PER_CYCLE:
            break
            
        if any(m in used_matches_in_cycle for m in cand['matches']):
            continue
            
        cursor.execute("SELECT id FROM sent_akos WHERE ako_signature = ?", (cand['signature'],))
        if cursor.fetchone() is None:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("INSERT INTO sent_akos (ako_signature, timestamp) VALUES (?, ?)", (cand['signature'], now_str))
            conn.commit()
            
            for m in cand['matches']:
                used_matches_in_cycle.add(m)
                
            stake, confidence = calculate_stake_and_confidence(cand['combo'], cand['total_odds'])
            
            msg = f"<b>🔥 VIP MASTER AKO ({cand['r']} ZDARZENIA) 🔥</b>\n\n"
            for idx, item in enumerate(cand['combo'], 1):
                msg += f"{idx}. <b>{item['match']}</b>\n   Typ: <i>{item['selection']}</i> (Kurs: <b>{item['odds']}</b>)\n"
            
            msg += f"\n📊 <b>Kurs Łączny: {cand['total_odds']:.2f}</b>"
            msg += f"\n🎯 <b>Analiza Pewności:</b> {confidence}"
            msg += f"\n💰 <b>Zalecana Stawka: {stake}</b>"
            msg += f"\n💎 <i>Betclic (BEZ PODATKU) | System Monster's Bets</i>"
            
            send_telegram_message(msg)
            generated_akos += 1
            
    conn.close()
    print(f"[AKO VIP] Wypuszczono na kanał: {generated_akos} unikalnych kuponów VIP.")

if __name__ == "__main__":
    if verify_environment():
        init_db()
        print("[SYSTEM VIP] Bot gotowy. Harmonogram: 06:25 oraz 18:25 (dokładnie 2 razy na dobę)...")
        
        last_run_date_slot = None
        
        while True:
            try:
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")
                current_date_str = now.strftime("%Y-%m-%d")
                current_slot_id = f"{current_date_str}_{current_time_str}"
                
                # Uruchamia się TYLKO wtedy, gdy bieżący czas pasuje do harmonogramu 
                # ORAZ ten konkretny slot czasowy nie był jeszcze w tym dniu uruchamiany
                if current_time_str in SCHEDULED_HOURS and last_run_date_slot != current_slot_id:
                    run_analysis_cycle(reason=f"Slot VIP {current_time_str}")
                    last_run_date_slot = current_slot_id
                
            except Exception as e:
                print(f"[CRITICAL] Błąd pętli głównej: {e}")
                
            time.sleep(15) # Sprawdzanie zegara co 15 sekund dla pełnej precyzji minutowej
