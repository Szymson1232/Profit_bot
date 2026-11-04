import logging
import sqlite3
import telegram
from telegram import Bot

# Konfiguracja logowania
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Konfiguracja kanału i bota
TOKEN = "9258087483:AAG8GBtZ4DSm2f-xtxGCvdNvhBxm1OrYbGo"


CHANNEL_ID = "@Profit"  # lub odpowiedni kanał / czat Monster's Bets
bot = Bot(token=TOKEN)


def connect_db():
  # Połączenie z bazą danych, w której trzymaliśmy kursy i statystyki
  conn = sqlite3.connect("monsters_bets.db")
  return conn


def fetch_value_slips():
  conn = connect_db()
  cursor = conn.cursor()

  # Zapytanie wyciągające wyselekcjonowane value (głównie tenis i zróżnicowane rynki z tamtego okresu)
  cursor.execute("""
        SELECT sport, match_name, pick, odds, edge, bookmaker 
        FROM calculated_value_bets 
        WHERE sent_to_telegram = 0 AND edge >= 3.5
        ORDER BY edge DESC
    """)

  rows = cursor.fetchall()
  conn.close()
  return rows


def format_and_send_bet(bet):
  sport, match_name, pick, odds, edge, bookmaker = bet

  # Ikona dyscypliny (w tym klasyczna ikonka tenisowa pod tamte kupony)
  sport_icon = "🎾" if sport.lower() == "tenis" else "🎯"

  message = (
      f"{sport_icon} **MONSTER'S BETS / VALUE ALERT**\n\n"
      f"🏆 **Dyscyplina:** {sport.upper()}\n"
      f"⚔️ **Mecz:** {match_name}\n"
      f"🔥 **Typ:** `{pick}`\n"
      f"📊 **Kurs:** `{odds:.2f}`\n"
      f"📈 **Value Edge:** `+{edge}%`\n"
      f"🏢 **Bukmacher:** {bookmaker}\n\n"
      f"⚡ *Gramy z głową, budżet pod kontrolą!*"
  )

  try:
    bot.send_message(
        chat_id=CHANNEL_ID, text=message, parse_mode=telegram.ParseMode.MARKDOWN
    )
    logging.info(f"Wysłano kupon na Telegram: {match_name} ({pick})")
  except Exception as e:
    logging.error(f"Błąd wysyłania wiadomości: {e}")


def mark_as_sent(match_name):
  conn = connect_db()
  cursor = conn.cursor()
  cursor.execute(
      "UPDATE calculated_value_bets SET sent_to_telegram = 1 WHERE match_name"
      " = ?",
      (match_name,),
  )
  conn.commit()
  conn.close()


def main():
  logging.info("Uruchamianie skanera bota (Monster's Bets / Fiddle's Bets)...")
  slips = fetch_value_slips()

  if not slips:
    logging.info("Brak nowych typów spełniających kryteria value.")
    return

  for slip in slips:
    format_and_send_bet(slip)
    mark_as_sent(slip[1])  # match_name


if __name__ == "__main__":
  main()
