"""Run the full bot with isolated .env.test credentials, never the live database."""

import asyncio
import sys
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from telegram import Bot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import configure_logging, create_app  # noqa: E402


async def check_bot(settings):
    async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
        me = await bot.get_me()
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError("This bot is connected to a hosted server. Use the new test bot.")
        print(f"Test bot: https://t.me/{me.username} — send /start once startup finishes.")


def main():
    configure_logging("WARNING")
    test = dotenv_values(ROOT / ".env.test")
    live = dotenv_values(ROOT / ".env")
    required = ("TELEGRAM_BOT_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENAI_API_KEY")
    missing = [key for key in required if not test.get(key)]
    if missing:
        raise SystemExit("Test setup still needs: " + ", ".join(missing))
    for key in ("TELEGRAM_BOT_TOKEN", "SUPABASE_URL"):
        if test[key].strip().rstrip("/") == (live.get(key) or "").strip().rstrip("/"):
            raise SystemExit(f"Test {key} matches the live setup. Use a separate test bot/project.")
    settings = Settings(
        _env_file=None,
        **{key.lower(): value for key, value in test.items() if value is not None},
    )
    if settings.bot_mode != "polling":
        raise SystemExit("The local test bot must use BOT_MODE=polling.")
    settings.require_credentials()
    asyncio.run(check_bot(settings))
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
