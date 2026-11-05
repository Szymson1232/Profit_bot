import os
import sys
import time
import requests

# ==========================================
# KONFIGURACJA NADHENRYKA (VIP ULTRA + STAWKI)
# ==========================================
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "2dd63be4b08f78956ef0d1b936b05882").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802").strip()

# Parametry algorytmu Value
MIN_EDGE_PERCENT = 3.0          
MAX_ODDS = 5.00                 
MIN_ODDS = 1.30                 
SCAN_INTERVAL_SECONDS = 600     
MAX_ALERTS_PER_HOUR = 6         

# Wartość jednostki w PLN
UNIT_VALUE_PLN = 25.0

# Pamięć podręczna duplikatów
sent_match_cache = set()
recent_alerts_timestamps = []

# WSZECHSTRONNY ROSTER SPORTÓW I LIG
SPORTS_TO_SCAN = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_poland_ekstraklasa", "soccer_turkey_super_lig", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga", "soccer_efl_champ", "basketball_nba", "basketball_euroleague",
    "basketball_ncaab", "basketball_spain_acb", "icehockey_nhl", "icehockey_sweden_hockey_league",
    "icehockey_finland_liiga", "icehockey_khl", "tennis_atp_aus_open", "tennis_wta_aus_open",
    "baseball_mlb", "americanfootball_nfl", "volleyball_cev_champions_league",
    "esports_league_of_legends", "darts_world_championship"
]

if not ODDS_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[FATAL ERROR] Brak wymaganych kluczy konfiguracyjnych!")
    sys.exit(1)

print("[INFO] NADHENRYK z systemem stawków PLN wystartował...")


def send_telegram_alert(message: str):
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
        print(f"[WYJATEK TELEGRAM] {e}")


def fetch_market_odds(sport_key: str):
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
            return response.json()
        elif response.status_code == 429:
            time.sleep(3600)
            return []
        else:
            return []
    except Exception as e:
        print(f"[WYJATEK API dla {sport_key}]: {e}")
        return []


def calculate_true_probabilities(bookmakers):
    all
