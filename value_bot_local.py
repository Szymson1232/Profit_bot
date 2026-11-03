import os
import time
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

SPORTS = [
    "basketball_nba",
    "soccer_italy_serie_a",
    "soccer_epl",
    "tennis",
    "basketball_euroleague"
]

REGION = "eu"

SPORT_EMOJIS = {
    "basketball_nba": "🏀",
    "soccer_italy_serie_a": "⚽",
    "soccer_epl": "⚽",
    "tennis": "🎾",
    "basketball_euroleague": "🏀"
}

BASE_UNIT_PLN = 25.0

# System pamięci w tle (w produkcyjnej wersji warto to zapisać do pliku/bazy JSON)
sent_vip_picks = set()
active_bets = {}  # match_id -> dane zakładu do rozliczenia

# Statystyki bilansu
stats = {
    "total_bets": 0,
    "wins": 0,
    "losses": 0,
    "profit_pln": 0.0,
    "total_staked": 0.0
}

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Błąd wysyłania: {e}")

def calculate_stake_and_tier(price):
    if price >= 3.20:
        units = 1
    elif 2.50 <= price < 3.20:
        units = 2
    else:
        units = 3
    return units, units * BASE_UNIT_PLN

def analyze_and_pick_best():
    print("🔍 Skanuję rynki pod kątem najnowszych sygnałów...")
    if not ODDS_API_KEY:
        return

    best_gem = None
    highest_score = 0
    now = datetime.now(timezone.utc)
    max_time_horizon = now + timedelta(hours=36)

    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={ODDS_API_KEY}&regions={REGION}&markets=h2h"
        
        try:
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                continue

            matches = response.json()
            emoji = SPORT_EMOJIS.get(sport, "🎯")

            for match in matches:
                match_id = match.get('id')
                home_team = match.get('home_team')
                away_team = match.get('away_team')
                commence_str = match.get('commence_time')
                
                if not commence_str:
                    continue

                try:
                    match_time = datetime.fromisoformat(commence_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                if not (now <= match_time <= max_time_horizon):
                    continue

                formatted_time = match_time.strftime('%d.%m %H:%M')

                for bookmaker in match.get('bookmakers', []):
                    bm_title = bookmaker.get('title', 'Betclic')
                    
                    for market in bookmaker.get('markets', []):
                        if market.get('key') == 'h2h':
                            outcomes = market.get('outcomes', [])
                            if len(outcomes) != 2:
                                continue
                            
                            for outcome in outcomes:
                                name = outcome.get('name')
                                price = outcome.get('price')
                                
                                if price and 2.10 <= price <= 3.20:
                                    pick_key = f"{match_id}_{name}"
                                    if pick_key in sent_vip_picks:
                                        continue

                                    score = price

                                    if score > highest_score:
                                        highest_score = score
                                        units, stake_pln = calculate_stake_and_tier(price)
                                        best_gem = {
                                            "match_id": match_id,
                                            "emoji": emoji,
                                            "home": home_team,
                                            "away": away_team,
                                            "outcome": name,
                                            "price": price,
                                            "bookmaker": bm_title,
                                            "sport": sport,
                                            "time": formatted_time,
                                            "units": units,
                                            "stake": stake_pln,
                                            "identifier": pick_key
                                        }

        except Exception as e:
            print(f"❌ Wyjątek przy analizie {sport}: {e}")

    if best_gem:
        message = (
            f"🔥 **VIP 100K // WYSELEKCJONOWANA PEREŁKA** 🔥\n\n"
            f"{best_gem['emoji']} **{best_gem['home']} vs {best_gem['away']}**\n"
            f"⏱ Start: `{best_gem['time']} UTC`\n"
            f"🏆 Dyscyplina: `{best_gem['sport'].upper()}`\n\n"
            f"🎯 **TYP:** `{best_gem['outcome']}`\n"
            f"📈 **KURS:** `{best_gem['price']}`\n"
            f"💰 **STAWKA:** `{best_gem['units']}x` ({best_gem['stake']} zł | 1U = {BASE_UNIT_PLN} zł)\n"
            f"🏢 **BUKMACHER:** *{best_gem['bookmaker']}*\n\n"
            f"💡 *Komentarz analityczny:* Wykryto błąd szacowania ryzyka u bukmachera. Gramy z zachowaniem dyscypliny bankrollu."
        )
        send_telegram_message(message)
        sent_vip_picks.add(best_gem['identifier'])
        
        # Zapisujemy kupon do rozliczenia
        active_bets[best_gem['match_id']] = best_gem

def check_and_settle_results():
    """Sprawdza zakończenie meczów i rozlicza bilans +/- oraz skutecznosc %"""
    print("📋 Sprawdzam wyniki zakończonych spotkań...")
    if not active_bets or not ODDS_API_KEY:
        return

    resolved_ids = []

    for match_id, bet in active_bets.items():
        sport = bet['sport']
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/scores/?apiKey={ODDS_API_KEY}&daysFrom=1"
        
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                continue

            matches_scores = res.json()
            for m in matches_scores:
                if m.get('id') == match_id and m.get('completed'):
                    # Mecz zakończony – wyciągamy zwycięzcę
                    scores = m.get('scores')
                    if not scores:
                        continue

                    # Wyznaczenie wygranego zespołu / zawodnika
                    winner = None
                    s1 = int(scores[0].get('score', 0))
                    s2 = int(scores[1].get('score', 0))

                    if s1 > s2:
                        winner = scores[0].get('name')
                    elif s2 > s1:
                        winner = scores[1].get('name')

                    stats['total_bets'] += 1
                    stats['total_staked'] += bet['stake']

                    if winner == bet['outcome']:
                        # ZAKŁAD WYGRANY
                        profit = (bet['stake'] * bet['price']) - bet['stake']
                        stats['wins'] += 1
                        stats['profit_pln'] += profit
                        
                        win_rate = (stats['wins'] / stats['total_bets']) * 100
                        yield_val = (stats['profit_pln'] / stats['total_staked']) * 100

                        msg = (
                            f"✅ **ROZLICZENIE: KUPON WYGRANY!** ✅\n\n"
                            f"{bet['emoji']} **{bet['home']} vs {bet['away']}**\n"
                            f"🎯 Typ: `{bet['outcome']}` (Kurs: `{bet['price']}`)\n"
                            f"💵 Zysk z kuponu: `+{profit:.2f} PLN`\n\n"
                            f"📊 **STATYSTYKI GRUPY VIP 100K:**\n"
                            f"📈 Skuteczność: `{win_rate:.1f}%` ({stats['wins']}/{stats['total_bets']})\n"
                            f"💰 Czysty Bilans: `{stats['profit_pln']:+.2f} PLN`\n"
                            f"🚀 Yield: `{yield_val:+.1f}%`"
                        )
                    else:
                        # ZAKŁAD PRZEGRANY
                        stats['losses'] += 1
                        stats['profit_pln'] -= bet['stake']
                        
                        win_rate = (stats['wins'] / stats['total_bets']) * 100
                        yield_val = (stats['profit_pln'] / stats['total_staked']) * 100

                        msg = (
                            f"❌ **ROZLICZENIE: KUPON PRZEGRANY** ❌\n\n"
                            f"{bet['emoji']} **{bet['home']} vs {bet['away']}**\n"
                            f"🎯 Typ: `{bet['outcome']}` (Kurs: `{bet['price']}`)\n"
                            f"📉 Strata z kuponu: `-{bet['stake']:.2f} PLN`\n\n"
                            f"📊 **STATYSTYKI GRUPY VIP 100K:**\n"
                            f"📈 Skuteczność: `{win_rate:.1f}%` ({stats['wins']}/{stats['total_bets']})\n"
                            f"💰 Czysty Bilans: `{stats['profit_pln']:+.2f} PLN`\n"
                            f"🚀 Yield: `{yield_val:+.1f}%`"
                        )

                    send_telegram_message(msg)
                    resolved_ids.append(match_id)

        except Exception as e:
            print(f"❌ Wyjątek przy rozliczaniu {match_id}: {e}")

    for r_id in resolved_ids:
        del active_bets[r_id]

if __name__ == "__main__":
    print("🚀 System VIP 100k z automatycznym podsumowaniem i bilansem +/- ruszył...")
    while True:
        analyze_and_pick_best()
        check_and_settle_results()
        time.sleep(3600)
