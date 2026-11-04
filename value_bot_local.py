#!/usr/bin/env python3
from __future__ import annotations

import os
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape

API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API = "https://api.telegram.org"

ODDS_API_KEY = "99e1b40f31bdf20559cb664785d538b8"
TELEGRAM_BOT_TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"
TELEGRAM_CHAT_ID = "7221307802"

SESSION = requests.Session()

def send_telegram(message: str) -> None:
    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = SESSION.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=20)
    print(f"Telegram status: {response.status_code}, response: {response.text}")

def test_run():
    print("Start testowego zrzutu nocnego...")
    # Pobieramy hokej NHL na szybko jako test
    url = f"{API_BASE}/sports/icehockey_nhl/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }
    res = SESSION.get(url, params=params, timeout=20)
    if res.status_code != 200:
        send_telegram(f"<b>TEST BŁĘDU API:</b> HTTP {res.status_code}")
        return

    data = res.json()
    if not isinstance(data, list) or len(data) == 0:
        send_telegram("<b>TEST:</b> Brak wydarzeń NHL w API.")
        return

    lines = [
        "<b>🚨 TESTOWY ZRZUT NOCNY (DIAGNOSTYKA) 🚨</b>",
        f"Pobrano meczów NHL: {len(data)}",
        ""
    ]

    # Weźmy pierwsze 3 nadchodzące mecze i pokażmy ich kursy
    count = 0
    for event in data[:3]:
        home = event.get("home_team", "Dom")
        away = event.get("away_team", "Gość")
        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue
        markets = bookmakers[0].get("markets", [])
        outcomes = []
        for m in markets:
            if m.get("key") == "h2h":
                outcomes = m.get("outcomes", [])
                break
        
        lines.append(f"<b>{escape(home)} vs {escape(away)}</b>")
        for o in outcomes:
            name = o.get("name", "")
            price = o.get("price", 0)
            lines.append(f"• Typ: {escape(name)} | Kurs: <b>{price}</b>")
        lines.append("")
        count += 1

    lines.append("<i>Maszynka działa poprawnie, API odpowiada, łącze z Telegramem stabilne.</i>")
    
    final_msg = "\n".join(lines)
    send_telegram(final_msg)
    print("Wysłano testowy komunikat na Telegram!")

if __name__ == "__main__":
    test_run()
