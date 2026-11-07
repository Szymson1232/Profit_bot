import os
import time
import sqlite3
import requests
from datetime import datetime

# --- PRAWIDŁOWE POBIERANIE ZMIENNYCH Z KLUCZAMI ZE SCREENÓW I KOMPLETNYM TOKENEM ---
API_KEY = os.getenv("ODDS_API_KEY", "d97af8a243c0f210867f0e7f905a9014")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802")

# Sztywne godziny skanowania: 08:00 (dla meczów na dzień) oraz 20:00 (dla meczów na nockę)
SCHEDULED_HOURS = ["08:00", "20:00"]

# Pełna lista dyscyplin i lig sportowych (piłka nożna, koszykówka, hokej)
SPORTS_TO_CHECK = [
    "soccer_epl",                  # Premier League (Anglia)
    "soccer_spain_la_liga",        # La Liga (Hiszpania)
    "soccer_germany_bundesliga",    # Bundesliga (Niemcy)
    "soccer_italy_serie_a",        # Serie A (Włochy)
    "soccer_france_ligue_one",     # Ligue 1 (Francja)
    "basketball_nba",              # NBA (USA / Nocka)
    "basketball_euroleague",       # Euroliga (Europa)
    "icehockey_nhl"                # NHL (USA / Nocka)
]

def verify_environment():
    """Weryfikuje poprawność załadowania kluczy ze środowiska uruchomieniowego."""
    if not API_KEY:
        print("[CRITICAL ERROR] Zmienna ODDS_API_KEY nie została odnaleziona!")
        return False
    
    masked_key = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
    print(f"[SYSTEM OK] Załadowano ODDS_API_KEY: {masked_key} (Długość: {len(API_KEY)})")
    print(f"[SYSTEM OK] Skanowanie zaplanowane na godziny: {', '.join(SCHEDULED_HOURS)}")
    print(f"[SYSTEM OK] Aktywne ligi ({len(SPORTS_TO_CHECK)}): {', '.join(SPORTS_TO_CHECK)}")
    return True

def init_db():
    """Tworzy strukturę bazy danych SQLite dla weryfikacji duplikatów zdarzeń."""
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
    """Pobiera dane rynkowe z REST API z obsługą kodów błędów i przekroczeń limitów."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 401:
            print(f"[API ERROR 401] Błąd autoryzacji dla {sport}. Odpowiedź: {response.text}")
            return []
        elif response.status_code != 200:
            print(f"[API ERROR {response.status_code}] Nieoczekiwany status dla {sport}: {response.text}")
            return []
        
        return response.json()
    except Exception as error:
        print(f"[EXCEPTION] Wyjątek podczas komunikacji sieciowej dla {sport}: {error}")
        return []

def run_analysis_cycle(reason="Zaplanowany cykl"):
    """Wykonuje pojedynczą iterację skanowania rynku bukmacherskiego dla wszystkich lig."""
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[CYKL] Uruchomienie analizy ({reason}): {current_time}")
    total_candidates = 0
    
    for sport in SPORTS_TO_CHECK:
        print(f"[ANALIZA] Pobieranie kursów dla: {sport}")
        odds_data = fetch_odds_for_sport(sport)
        
        if odds_data:
            print(f"[ANALIZA] Pomyślnie pobrano {len(odds_data)} zdarzeń dla {sport}.")
        else:
            print(f"[ANALIZA] Brak danych lub niepowodzenie zapytania dla {sport}.")
            
        time.sleep(1.5)  # Ochronny odstęp czasu między zapytaniami HTTP
        
    print(f"[ANALIZA] Znaleziono łącznie {total_candidates} kandydatów SOLO i 0 nóg AKO.")
    print(f"[CYKL] Zakończony pomyślnie. Następne skanowanie zgodnie z harmonogramem (08:00 / 20:00).")

if __name__ == "__main__":
    if verify_environment():
        init_db()
        
        # 1. Wykonaj pierwsze skanowanie natychmiast po starcie kontenera
        run_analysis_cycle(reason="Inicjalizacja po starcie")
        
        last_run_slot = None
        
        # 2. Główna pętla oczekująca na zaplanowane godziny (08:00 oraz 20:00)
        while True:
            try:
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")
                
                if current_time_str in SCHEDULED_HOURS and last_run_slot != current_time_str:
                    run_analysis_cycle(reason=f"Harmonogram {current_time_str}")
                    last_run_slot = current_time_str
                
                if current_time_str not in SCHEDULED_HOURS:
                    last_run_slot = None
                    
            except Exception as global_error:
                print(f"[CRITICAL ERROR] Wyjątek w pętli głównej skryptu: {global_error}")
            
            time.sleep(30)  # Sprawdzaj czas co 30 sekund
    else:
        print("[FATAL ERROR] Przerwanie pracy skryptu z powodu braku wymaganych zmiennych środowiskowych.")
