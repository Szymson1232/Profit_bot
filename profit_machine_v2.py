import os
import time
import sqlite3
import requests
from datetime import datetime, timedelta, timezone
from itertools import combinations

# --- MASZYNA PITiS v3 (ULTIMATE ANTI-DUPLICATE & 2H MODE) ---
API_KEY = os.getenv("ODDS_API_KEY", "d578396f7740845e63b7cc3f648b7486")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802")

MIN_ODDS_SINGLE = 1.05
MAX_ODDS_SINGLE = 1.75
MIN_AKO_TOTAL = 1.90
MAX_AKO_TOTAL = 3.20
MAX_AKO_EVENTS = 3
MAX_AKO_PER_CYCLE = 3

def verify_environment():
    if not API_KEY:
        print("[CRITICAL ERROR] Brak klucza API!")
        return False
    masked = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
    print(f"[SYSTEM PITOS v3] Maszyna Ultimate Anti-Duplicate Aktywna. API Key: {masked}")
    return True

def init_db():
    conn = sqlite3.connect("monsters_bets_v3.db")
    cursor = conn.cursor()
    # Tabela trzymająca historię pojedynczych meczów przez 48h
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_matches_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_name TEXT UNIQUE,
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
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[TELEGRAM] Kupon wysłany pomyślnie.")
        else:
            print(f"[TELEGRAM ERROR {response.status_code}] {response.text}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

def check_api_quota_and_warn(response_headers):
    remaining = response_headers.get('x-requests-remaining')
    used = response_headers.get('x-requests-used')
    
    if remaining is not None:
        remaining_int = int(remaining)
        print(f"[API QUOTA] Pozostało zapytań: {remaining_int} (Zużyto: {used})")
        if remaining_int <= 50:
            warn_msg = (
                f"🚨 <b>OSTRZEŻENIE: LIMIT API NA WYKOŃCZENIE!</b> 🚨\n\n"
                f"Pozostało Ci zaledwie <b>{remaining_int}</b> zapytań do The Odds API."
            )
            send_telegram_message(warn_msg)

def get_all_available_sports():
    url = "https://api.the-odds-api.com/v4/sports/"
    params = {"apiKey": API_KEY}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            check_api_quota_and_warn(response.headers)
            sports = response.json()
            return [s['key'] for s in sports if s.get('active', True)]
        return []
    except Exception as e:
        print(f"[EXCEPTION sports]: {e}")
        return []

def fetch_odds_for_sport(sport):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu,us",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception:
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
    if avg_odds <= 1.25 and len(combo) == 3:
        return "3j (75 zł)", "💎💎💎 Maszyna Pitosu / Najwyższa pewność"
    elif avg_odds <= 1.35:
        return "2j (50 zł)", "⭐⭐⭐ Wysoka pewność"
    else:
        return "1j (25 zł)", "⭐⭐ Agresywne Value"

def extract_favorites(events, sport_name):
    favorites = []
    seen_matches = set()
    now = datetime.now(timezone.utc)
    
    min_allowed_date = now - timedelta(minutes=30)
    max_allowed_date = now + timedelta(hours=24)
    
    for event in events:
        commence_time_str = event.get('commence_time', '')
        event_time = parse_iso_time(commence_time_str)
        
        if event_time:
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            if event_time < min_allowed_date or event_time > max_allowed_date:
                continue
            
        home_team = event.get('home_team', '')
        away_team = event.get('away_team', '')
        match_key = f"{home_team} vs {away_team}"
        
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
                                'match': match_key,
                                'selection': name,
                                'odds': price,
                                'sport': sport_name,
                                'time': commence_time_str
                            })
                    break
            break
    return favorites

def run_analysis_cycle(reason="Cykl Ultimate Anti-Dup"):
    print(f"\n[PITOS v3 CYCLE] Start skanowania ({reason}): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    conn = sqlite3.connect("monsters_bets_v3.db")
    cursor = conn.cursor()
    
    # Czyszczenie historii starszej niż 48 godzin, żeby baza nie puchła w nieskończoność
    two_days_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("DELETE FROM sent_matches_history WHERE timestamp < ?", (two_days_ago,))
    conn.commit()
    
    # Pobieramy zestawienie WSZYSTKICH meczów, które były wysłane w ciągu ostatnich 48h
    cursor.execute("SELECT match_name FROM sent_matches_history")
    already_sent_matches = {row[0] for row in cursor.fetchall()}
    print(f"[DB] Liczba zablokowanych meczów z ostatnich 48h: {len(already_sent_matches)}")

    all_sports = get_all_available_sports()
    print(f"[API] Znaleziono aktywnych dyscyplin: {len(all_sports)}")
    
    all_favorites = []
    for sport in all_sports[:25]:
        events = fetch_odds_for_sport(sport)
        favs = extract_favorites(events, sport)
        
        # TWARDY FILTR: Odrzucamy w ogóle mecze, które już kiedyś poszły na kanał w ciągu ostatnich 48h!
        fresh_favs = [f for f in favs if f['match'] not in already_sent_matches]
        all_favorites.extend(fresh_favs)
        time.sleep(0.3)
        
    print(f"[ANALIZA v3] Pobrano świeżych, nietkniętych walorów: {len(all_favorites)}")
    
    candidates_to_send = []
    
    for combo in combinations(all_favorites, MAX_AKO_EVENTS):
        matches = [item['match'] for item in combo]
        
        if len(set(matches)) != len(matches):
            continue
            
        total_odds = 1.0
        for item in combo:
            total_odds *= item['odds']
            
        if MIN_AKO_TOTAL <= total_odds <= MAX_AKO_TOTAL:
            candidates_to_send.append({
                'combo': combo,
                'total_odds': total_odds,
                'matches': matches
            })
                
    candidates_to_send.sort(key=lambda x: x['total_odds'], reverse=True)
    
    generated_akos = 0
    cycle_used_matches = set()
    
    for cand in candidates_to_send:
        if generated_akos >= MAX_AKO_PER_CYCLE:
            break
            
        # Sprawdzenie unikalności wewnątrz tego samego cyklu
        if any(m in cycle_used_matches for m in cand['matches']):
            continue
            
        # Podwójne upewnienie się, że żaden mecz z tej kombinacji nie był wcześniej wysłany
        if any(m in already_sent_matches for m in cand['matches']):
            continue
            
        # Zapisz każdy mecz z tego kuponu do bazy jako wysłany
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for m in cand['matches']:
            cursor.execute("INSERT OR IGNORE INTO sent_matches_history (match_name, timestamp) VALUES (?, ?)", (m, now_str))
            cycle_used_matches.add(m)
            already_sent_matches.add(m)
        conn.commit()
            
        stake, confidence = calculate_stake_and_confidence(cand['combo'], cand['total_odds'])
        
        msg = f"<b>💸 CASH MACHINE AKO3 (2H CLEAN) 💸</b>\n\n"
        for idx, item in enumerate(cand['combo'], 1):
            msg += f"{idx}. <b>{item['match']}</b>\n   Typ: <i>{item['selection']}</i> (Kurs: <b>{item['odds']}</b>)\n"
        
        msg += f"\n📊 <b>Kurs Łączny: {cand['total_odds']:.2f}</b>"
        msg += f"\n🎯 <b>Status:</b> {confidence}"
        msg += f"\n💰 <b>Stawka: {stake}</b>"
        msg += f"\n💎 <i>Betclic (BEZ PODATKU) | Let's Make That Cash</i>"
        
        send_telegram_message(msg)
        generated_akos += 1
            
    conn.close()
    print(f"[PITOS v3 CYCLE] Wypuszczono na kanał unikalnych kuponów: {generated_akos}.")

if __name__ == "__main__":
    if verify_environment():
        init_db()
        print("[SYSTEM PITOS v3] Start w trybie Ultimate Anti-Duplicate...")
        
        try:
            run_analysis_cycle(reason="Start Ultimate")
        except Exception as e:
            print(f"[ERROR START] {e}")
            
        last_run_timestamp = time.time()
        TWO_HOURS_IN_SECONDS = 2 * 60 * 60
        
        while True:
            try:
                current_time = time.time()
                if current_time - last_run_timestamp >= TWO_HOURS_IN_SECONDS:
                    run_analysis_cycle(reason="Cykl automatyczny 2h")
                    last_run_timestamp = current_time
            except Exception as e:
                print(f"[CRITICAL] Błąd pętli głównej: {e}")
                
            time.sleep(60)
