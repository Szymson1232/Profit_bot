import os
import sys
import time
import requests

# ==========================================
# 1. BEZPIECZNA KONFIGURACJA I WALIDACJA
# ==========================================
ODDS_API_KEY = os.getenv("2dd63be4b08f78956ef0d1b936b05882", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo", "").strip()
TELEGRAM_CHAT_ID = os.getenv("7221307802", "").strip()

missing_vars = []
if not ODDS_API_KEY:
    missing_vars.append("ODDS_API_KEY")
if not TELEGRAM_BOT_TOKEN:
    missing_vars.append("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_CHAT_ID:
    missing_vars.append("TELEGRAM_CHAT_ID")

if missing_vars:
    print("=" * 60)
    print(f"[FATAL ERROR] Brak wymaganych zmiennych: {', '.join(missing_vars)}")
    print("DIAGNOSTYKA ŚRODOWISKA:")
    print(f" -> ODDS_API_KEY obecny: {bool(ODDS_API_KEY)} (długość: {len(ODDS_API_KEY)})")
    print(f" -> TELEGRAM_BOT_TOKEN obecny: {bool(TELEGRAM_BOT_TOKEN)} (długość: {len(TELEGRAM_BOT_TOKEN)})")
    print(f" -> TELEGRAM_CHAT_ID obecny: {bool(TELEGRAM_CHAT_ID)} (długość: {len(TELEGRAM_CHAT_ID)})")
    print("Upewnij się, że zmienne są dodane w panelu hostingu i wykonano Clear Build Cache.")
    print("=" * 60)
    sys.exit(1)

print("[INFO] Konfiguracja załadowana pomyślnie. Maszyna do value betów rusza...")


# ==========================================
# 2. MODUŁ TELEGRAMA
# ==========================================
def send_telegram_alert(message: str):
    """Wysyła sformatowane powiadomienie na czat Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[BLAD TELEGRAM] Kod {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[WYJATEK TELEGRAM] Nie udało się wysłać wiadomości: {e}")


# ==========================================
# 3. MODUŁ ODDS API I ANALIZY VALUE
# ==========================================
def fetch_odds(sport_key: str = "soccer_epl"):
    """Pobiera kursy bukmacherskie z The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "api_key": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsformat": "decimal"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"[BLAD API] Kod {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"[WYJATEK API] Błąd połączenia z Odds API: {e}")
        return []


def calculate_value_bets(events, min_value_pct: float = 3.0):
    """
    Przeszukuje wydarzenia, szukając odchyłek (value) 
    względem uśrednionego prawdopodobieństwa rynkowego.
    """
    value_bets = []

    for event in events:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        bookmakers = event.get("bookmakers", [])

        if not bookmakers:
            continue

        # Zbieramy wszystkie kursy dla wyliczenia średniej rynkowej (fair odds)
        market_odds_home = []
        market_odds_away = []
        market_odds_draw = []

        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        name = outcome.get("name")
                        price = outcome.get("price")
                        if name == home_team:
                            market_odds_home.append(price)
                        elif name == away_team:
                            market_odds_away.append(price)
                        else:
                            market_odds_draw.append(price)

        if not market_odds_home or not market_odds_away:
            continue

        # Średnie kursy rynkowe
        avg_home = sum(market_odds_home) / len(market_odds_home)
        avg_away = sum(market_odds_away) / len(market_odds_away)

        # Analiza konkretnych bukmacherów pod kątem value
        for bm in bookmakers:
            bm_title = bm.get("title")
            for market in bm.get("markets", []):
                if market.get("key") == "h2h":
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name")
                        price = outcome.get("price")

                        # Obliczamy implikowane prawdopodobieństwo bukmachera vs rynek
                        # Używamy średniej rynkowej jako benchmarku true probability
                        if name == home_team and avg_home > 0:
                            implied_prob = 1 / price
                            true_prob = 1 / avg_home
                            value = (true_prob * price) - 1
                            if value >= (min_value_pct / 100.0):
                                value_bets.append({
                                    "match": f"{home_team} vs {away_team}",
                                    "bookmaker": bm_title,
                                    "selection": name,
                                    "price": price,
                                    "edge": round(value * 100, 2)
                                })

                        elif name == away_team and avg_away > 0:
                            implied_prob = 1 / price
                            true_prob = 1 / avg_away
                            value = (true_prob * price) - 1
                            if value >= (min_value_pct / 100.0):
                                value_bets.append({
                                    "match": f"{home_team} vs {away_team}",
                                    "bookmaker": bm_title,
                                    "selection": name,
                                    "price": price,
                                    "edge": round(value * 100, 2)
                                })

    return value_bets


# ==========================================
# 4. GŁÓWNA PĘTLA URUCHOMIONIOWA
# ==========================================
def main():
    print("[INFO] Uruchomiono skanowanie rynku bukmacherskiego...")
    send_telegram_alert("🚀 *Maszyna Value Betów uruchomiona!* Skanuję rynki...")

    # Przykładowe dyscypliny do monitorowania (możesz rozszerzyć)
    sports = ["soccer_epl", "basketball_nba", "icehockey_nhl"]

    while True:
        for sport in sports:
            print(f"[SKAN] Sprawdzam dyscyplinę: {sport}")
            events = fetch_odds(sport)
            if events:
                value_bets = calculate_value_bets(events, min_value_pct=4.0)
                for bet in value_bets:
                    msg = (
                        f"🎯 *ZNALEZIONO VALUE BET!*\n\n"
                        f"⚽ Mecz: `{bet['match']}`\n"
                        f"🏢 Bukmacher: *{bet['bookmaker']}*\n"
                        f"📌 Typ: `{bet['selection']}`\n"
                        f"💰 Kurs: *{bet['price']}*\n"
                        f"📈 Szacowane Value (Edge): `+{bet['edge']}%`"
                    )
                    send_telegram_alert(msg)
                    print(f"[ZNALEZIONO] {bet['match']} | {bet['bookmaker']} | Kurs: {bet['price']}")

        # Czas oczekiwania przed kolejnym skanowaniem (np. 30 minut, żeby nie przepalić limitów API)
        print("[INFO] Cykl zakończony. Czekam na kolejne skanowanie...")
        time.sleep(1800)


if __name__ == "__main__":
    main()
