import os
import time
import sqlite3
import requests
from datetime import datetime, timedelta, timezone
from itertools import combinations

# --- KONFIGURACJA MASZYNY DO ZARABIANIA PITOSU ---
API_KEY = os.getenv("ODDS_API_KEY", "d578396f7740845e63b7cc3f648b7486")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7221307802")

# Zdrowa, działająca baza dyscyplin (bez błędów 404)
SPORTS_TO_CHECK = [
    "soccer_epl",                  
    "soccer_spain_la_liga",        
    "soccer_germany_bundesliga",    
    "soccer_italy_serie_a",        
    "soccer_france_ligue_one",     
    "basketball_nba",              
    "basketball_euroleague",       
    "icehockey_nhl"                
]

# Parametry AKO3 pod Betclic Bez Podatku
MIN_ODDS_SINGLE = 1.08
MAX_ODDS_SINGLE = 1.60
MIN_AKO_TOTAL = 1.95
MAX_AKO_TOTAL = 2.70
MAX_AKO_EVENTS = 3

MAX_AKO_PER_CYCLE = 10

def verify_environment():
    if not API_KEY:
        print("[CRITICAL ERROR] Brak klucza API!")
        return False
    masked = f"{API_KEY[:4]}...{API_KEY[-4:]}" if len(API_KEY) > 8 else "***"
    print(f"[SYSTEM PITOS OK] Maszyna AKO3 (Clean Active Mode). API Key: {masked}")
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
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[TELEGRAM] Kupon wysłany pomyślnie.")
        else:
            print(f"[TELEGRAM ERROR {response.status_code}] {response.text}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

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
    if avg_odds <= 1.25 and len(combo) == 3:
        return "3j (75 zł)", "💎💎💎 Maszyna Pitosu / Najwyższa pewność"
    elif avg_odds <= 1.35:
        return "2j (50 zł)", "⭐⭐⭐ Wysoka pewność"
    else:
        return "1j
