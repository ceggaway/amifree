"""Preview the real onboarding screens using a separate Telegram test bot.

Run .venv/bin/python scripts/preview_onboarding.py after setting
TELEGRAM_PREVIEW_BOT_TOKEN in .env. No database or AI service is used.
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot import onboarding  # noqa: E402


async def start(update, context):
    await update.effective_message.reply_text(
        onboarding.WELCOME, reply_markup=onboarding.welcome_keyboard(), parse_mode="HTML"
    )


async def callback(update, context):
    query = update.callback_query
    await query.answer()
    name = (query.data or "").removeprefix("setup:")
    screen = onboarding.screen(name)
    if screen is None:
        return
    text, keyboard = screen
    if query.message.text == text:
        return
    await query.edit_message_text(
        text, reply_markup=keyboard, parse_mode="HTML" if name == "home" else None
    )


async def other_message(update, context):
    await update.effective_message.reply_text(
        "This test bot previews onboarding only. Send /start to see the welcome message. "
        "Calendar connections and saved plans are not enabled in this preview."
    )


async def check_test_bot(application):
    webhook = await application.bot.get_webhook_info()
    if webhook.url:
        raise RuntimeError("This bot has a webhook. Use a separate test bot for the preview.")
    print(f"Preview ready: https://t.me/{application.bot.username} — send /start", flush=True)


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.environ.get("TELEGRAM_PREVIEW_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Add TELEGRAM_PREVIEW_BOT_TOKEN to .env first. Use a separate test bot.")
    # HTTP logs can contain the bot token in request URLs.
    logging.basicConfig(level=logging.CRITICAL)
    application = Application.builder().token(token).post_init(check_test_bot).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback, pattern="^setup:"))
    application.add_handler(MessageHandler(filters.ALL, other_message))
    application.run_polling()


if __name__ == "__main__":
    main()
