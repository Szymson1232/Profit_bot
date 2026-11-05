import os
import sys
import time
import requests

# ==========================================
# KONFIGURACJA NADHENRYKA (VIP ULTRA)
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "2dd63be4b08f78956ef0d1b936b05882").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

# Parametry algorytmu Value
MIN_EDGE_PERCENT = 3.0          # Minimalna przewaga (edge) w %
MAX_ODDS = 5.00                 # Maksymalny kurs (łapiemy też soczyste niespodzianki)
MIN_ODDS = 1.30                 # Minimalny kurs (odsiewamy drobnice)
SCAN_INTERVAL_SECONDS = 1200    # Przerwa między pełnymi cyklami (20 minut)

# WSZECHSTRONNY ROSTER SPORTÓW I LIG (NADHENRYK SPECTRUM)
SPORTS_TO_SCAN = [
    # --- PIŁKA NOŻNA (Global) ---
    "soccer_epl",                       # Anglia - Premier League
    "soccer_spain_la_liga",             # Hiszpania - La Liga
    "soccer_italy_serie_a",             # Włochy - Serie A
    "soccer_germany_bundesliga",        # Niemcy - Bundesliga
    "soccer_france_ligue_one",          # Francja - Ligue 1
    "soccer_uefa_champs_league",        # Liga Mistrzów UEFA
    "soccer_uefa_europa_league",        # Liga Europy UEFA
    "soccer_poland_ekstraklasa",        # Polska - Ekstraklasa
    "soccer_turkey_super_lig",          # Turcja - Super Lig
    "soccer_netherlands_eredivisie",    # Holandia - Eredivisie
    "soccer_portugal_primeira_liga",    # Portugalia - Primeira Liga
    "soccer_efl_champ",                 # Anglia - Championship
    
    # --- KOSZYKÓWKA (Global) ---
    "basketball_nba",                   # USA - NBA
    "basketball_euroleague",            # Europa - Euroliga
    "basketball_ncaab",                 # USA - NCAA (uniwersytety)
    "basketball_spain_acb",             # Hiszpania - ACB
    
    # --- HOKEJ NA LODZIE (Global) ---
    "icehockey_nhl",                    # USA/Kanada - NHL
    "icehockey_sweden_hockey_league",   # Szwecja - SHL
    "icehockey_finland_liiga",          # Finlandia - Liiga
    "icehockey_khl",                    # KHL / Międzynarodowe
    
    # --- POZOSTAŁE DYSCYPLINY (Pełne Spektrum) ---
    "tennis_atp_aus_open",              # Tenis ATP (ogólne/turnieje główne)
    "tennis_wta_aus_open",              # Tenis WTA
    "baseball_mlb",                     # Baseball - MLB
    "americanfootball_nfl",             # Futbol Amerykański - NFL
    "volleyball_cev_champions_league",  # Siatkówka - Liga Mistrzów
    "esports_league_of_legends",        # Esport - LoL (LEC/LPL/LCK)
    "darts_world_championship"          # Dart - PDC / Turnieje
]

# Walidacja startowa
if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych!")
    sys.exit(1)

print("[INFO] NADHENRYK został uruchomiony i skanuje całe spektrum rynków...")


# ==========================================
# MODUŁ KOMUNIKACJI TELEGRAM
# ==========================================
def send_telegram_alert(message: str):
    """Wysyła sformatowany alert VIP w standardzie HTML na Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[BŁĄD TELEGRAM] Kod {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[WYJATEK TELEGRAM] Nie udało się dostarczyć powiadomienia: {e}")


# ==========================================
# MODUŁ POBIERANIA DANYCH Z ODDS API
# ==========================================
def fetch_market_odds(sport_key: str):
    """Pobiera aktualne kursy dla dowolnego sportu z The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "api_key": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h",
        "oddsformat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            remaining = response.headers.get("x-requests-remaining", "N/A")
            print(f"[API] [{sport_key}] OK. Pozostałe zapytania: {remaining}")
            return response.json()
        elif response.status_code == 429:
            print(f"[OSTRZEŻENIE API] Limit zapytań wyczerpany (429). Odpoczywamy...")
            time.sleep(3600)
            return []
        else:
            # Nie każda liga musi być aktywnie rozgrywana w danym dniu (kod 404/422 to norma
