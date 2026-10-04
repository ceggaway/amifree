"""Read-only external connectivity check; never prints credentials or event content."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from openai import AsyncOpenAI
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions
from telegram import Bot

from app.config import Settings
from app.db.supabase import SupabaseRepository
from app.main import configure_logging
from app.schemas.event import SG
from app.services.nusmods import API, academic_year_for


async def main():
    settings = Settings()
    configure_logging("WARNING")
    checks = []
    try:
        settings.require_credentials()
    except RuntimeError as exc:
        print(str(exc))
        return 1
    async with httpx.AsyncClient(timeout=20) as http:
        supa = await acreate_client(
            settings.supabase_url,
            settings.supabase_service_role_key.get_secret_value(),
            options=AsyncClientOptions(httpx_client=http),
        )

        async def db():
            await SupabaseRepository(supa).ping()

        async def telegram():
            async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
                await bot.get_me()

        async def openai():
            async with AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(), timeout=20
            ) as client:
                await client.models.retrieve(settings.openai_model)

        async def nusmods():
            year = settings.nusmods_academic_year or academic_year_for(datetime.now(SG))
            r = await http.get(f"{API}/{year}/moduleList.json")
            r.raise_for_status()

        for name, check in [
            ("Supabase tables", db),
            ("Telegram bot identity", telegram),
            ("OpenAI model access (no generation)", openai),
            ("NUSMods public API", nusmods),
        ]:
            try:
                await check()
                print(f"PASS: {name}")
                checks.append(True)
            except Exception as exc:
                print(f"FAIL: {name} ({type(exc).__name__}); check .env, schema and network")
                checks.append(False)
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
