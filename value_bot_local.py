import os
import time
import sqlite3
import requests
from datetime import datetime

# --- KONFIGURACJA ---
API_KEY = os.getenv("d97af8a243c0f210867f0e7f905a9014")
TELEGRAM_BOT_TOKEN = os.getenv("9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("7221307802")

# Interwał ustawiony na 2 godziny (2 * 60 * 60 = 7200 sekund)
SCAN_INTERVAL_SECONDS = 2 * 60 * 60

# Wybierz tylko najpotrzebniejsze sporty, aby oszczędzać darmowy limit API (np. 500 req/mc)
SPORTS_TO_CHECK = [
    "basketball_euroleague",
    "icehockey_nhl"
]

def check_api_key():
    if not API_KEY:
        print("[DEBUG] BŁĄD: Zmienna ODDS_API_KEY jest pusta lub nie istnieje!")
    else:
        masked = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
        print(f"[DEBUG] Załadowano ODDS_API_KEY: {masked} (długość: {len(API_KEY)})")

def init_db():
    conn = sqlite3.connect("monsters_bets.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT,
            match_name TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

def fetch_odds_for_sport(sport):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 401:
            print(f"[API ERROR] Sport {sport} zwrócił kod 401 (Błąd limitu/klucza): {response.text}")
            return []
        elif response.status_code != 200:
            print(f"[API ERROR] Sport {sport} zwrócił kod {response.status_code}: {response.text}")
            return []
        
        return response.json()
    except Exception as e:
        print(f"[API ERROR] Wyjątek podczas pobierania {sport}: {e}")
        return []

def run_analysis_cycle():
    print(f"\n[CYKL] Rozpoczęcie skanowania: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    total_candidates = 0
    
    for sport in SPORTS_TO_CHECK:
        print(f"[ANALIZA] Sprawdzam sport: {sport}")
        data = fetch_odds_for_sport(sport)
        
        # Tutaj Twoja logika przetwarzania kursów i szukania value...
        # Przykładowo: len(data) eventów pobranych poprawnie
        
        time.sleep(1) # Krótka przerwa między zapytaniami do API
        
    print(f"[ANALIZA] Znaleziono łącznie {total_candidates} kandydatów SOLO i 0 nóg AKO.")
    print(f"[CYKL] Zakończony pomyślnie. Czekam 2 godziny[span_2](start_span)[span_2](end_span).")

if __name__ == "__main__":
    check_api_key()
    init_db()
    
    # Główna pętla z oszczędnym interwałem
    while True:
        try:
            run_analysis_cycle()
        except Exception as e:
            print(f"[CRITICAL] Błąd w pętli głównej: {e}")
            
        time.sleep(SCAN_INTERVAL_SECONDS)
