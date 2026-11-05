import os
import sys
import time
import math
import requests
from datetime import datetime

# ==========================================
# KONFIGURACJA WBUDOWANA (PROJEKT VIP)
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "2dd63be4b08f78956ef0d1b936b05882").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

# Parametry algorytmu Value
MIN_EDGE_PERCENT = 3.5          # Minimalna wartość value (edge) w % do wysłania alertu
MAX_ODDS = 4.50                 # Maksymalny kurs (odrzucamy szalone niespodzianki)
MIN_ODDS = 1.35                 # Minimalny kurs (odrzucamy oczywiste pewniaki z mikroskopijnym value)
SCAN_INTERVAL_SECONDS = 1800    # Przerwa między skanami (30 minut, żeby oszczędzać limity API)

# Sporty do monitorowania (Piłka nożna, Koszykówka, Hokej, Tenis)
SPORTS_TO_SCAN = [
    "soccer_epl",               # Premier League
    "soccer_spain_la_liga",     # La Liga
    "soccer_italy_serie_a",     # Serie A
    "soccer_germany_bundesliga",# Bundesliga
    "basketball_nba",           # NBA
    "icehockey_nhl"             # NHL
]

# Walidacja startowa
if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych!")
    sys.exit(1)

print("[INFO] Maszyna VIP do szukania Value Betów została zainicjalizowana pomyślnie.")


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
    """Pobiera aktualne kursy i rynki dla danej dyscypliny z The Odds API."""
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
            remaining_requests = response.headers.get("x-requests-remaining", "N/A")
            print(f"[API] [{sport_key}] Pobrano pomyślnie. Pozostałe zapytania w miesiącu: {remaining_requests}")
            return response.json()
        elif response.status_code == 429:
            print(f"[OSTRZEŻENIE API] Limit zapytań wyczerpany (429). Czekam dłużej...")
            time.sleep(3600)
            return []
        else:
            print(f"[BŁĄD API] Kod {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"[WYJATEK API] Błąd sieciowy podczas pobierania kursów: {e}")
        return []


# ==========================================
# ALGORYTMT MATEMATYCZNY (TRUE PRO
