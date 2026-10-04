import asyncio
import hmac
import logging
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from openai import AsyncOpenAI
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions
from telegram import BotCommand, Update
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.config import Settings
from app.db.supabase import SupabaseRepository
from app.services.event_extractor import EventExtractor
from app.services.nusmods import NUSModsImporter
from app.services.scheduling import SchedulingService

log = logging.getLogger(__name__)


def configure_logging(level):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # HTTP library logs may contain the Telegram bot token in the URL.
    for name in ("httpx", "httpcore", "telegram", "openai", "postgrest", "supabase"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def create_app(
    settings: Settings | None = None, *, service=None, telegram_application=None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging(settings.log_level)
        app.state.ready = False
        app.state.service = service
        app.state.telegram = telegram_application
        app.state.webhook_lock = asyncio.Lock()
        if service is not None:
            app.state.ready = True
            yield
            return
        if settings.bot_mode == "disabled":
            log.info("Bot disabled; health endpoint only")
            yield
            return
        settings.require_credentials()
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(httpx.AsyncClient(timeout=20))
            ai = await stack.enter_async_context(
                AsyncOpenAI(
                    api_key=settings.openai_api_key.get_secret_value(), timeout=25, max_retries=1
                )
            )
            supa_http = await stack.enter_async_context(httpx.AsyncClient(timeout=20))
            client = await acreate_client(
                settings.supabase_url,
                settings.supabase_service_role_key.get_secret_value(),
                options=AsyncClientOptions(httpx_client=supa_http),
            )
            repo = SupabaseRepository(client, settings.pending_ttl_hours)
            try:
                await repo.ping()
                await repo.cleanup(settings.extraction_retention_days)
            except Exception:
                raise RuntimeError(
                    "Supabase startup check failed. Apply app/db/schema.sql and check your connection credentials."
                ) from None
            app.state.service = SchedulingService(
                repo,
                EventExtractor(ai, settings.openai_model),
                NUSModsImporter(http, settings.nusmods_academic_year),
                settings,
            )
            bot_app = (
                Application.builder()
                .token(settings.telegram_bot_token.get_secret_value())
                .concurrent_updates(False)
                .build()
            )
            register_handlers(bot_app, app.state.service)
            app.state.telegram = bot_app
            await stack.enter_async_context(bot_app)
            await bot_app.bot.set_my_commands(
                [
                    BotCommand("start", "Set up or check readiness"),
                    BotCommand("help", "How AmIFree works"),
                    BotCommand("events", "Recent events"),
                    BotCommand("cancel", "Clear pending action"),
                    BotCommand("delete", "Delete my data"),
                ]
            )
            await bot_app.start()
            stack.push_async_callback(bot_app.stop)
            if settings.bot_mode == "polling":
                await bot_app.bot.delete_webhook(drop_pending_updates=False)
                await bot_app.updater.start_polling(
                    allowed_updates=["message", "callback_query"], drop_pending_updates=False
                )
                stack.push_async_callback(bot_app.updater.stop)
            else:
                await bot_app.bot.set_webhook(
                    url=settings.app_base_url.rstrip("/") + "/telegram/webhook",
                    secret_token=settings.telegram_webhook_secret.get_secret_value(),
                    allowed_updates=["message", "callback_query"],
                )

            async def retention_loop():
                while True:
                    await asyncio.sleep(3600)
                    try:
                        await repo.cleanup(settings.extraction_retention_days)
                    except Exception as exc:
                        log.warning("retention_cleanup_failed error_type=%s", type(exc).__name__)

            task = asyncio.create_task(retention_loop())

            async def stop_retention():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            stack.push_async_callback(stop_retention)
            app.state.ready = True
            log.info("AmIFree ready mode=%s", settings.bot_mode)
            try:
                yield
            finally:
                app.state.ready = False

    app = FastAPI(
        title="AmIFree", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "amifree", "bot_mode": settings.bot_mode}

    @app.get("/readyz")
    async def ready():
        if not app.state.ready or not app.state.service:
            raise HTTPException(503, "Bot is not configured or ready")
        try:
            await app.state.service.repo.ping()
        except Exception:
            raise HTTPException(503, "Database is unavailable") from None
        return {"status": "ready", "database": "connected"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request):
        if settings.bot_mode != "webhook":
            raise HTTPException(404, "Webhook mode is disabled")
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(
            provided.encode(), settings.telegram_webhook_secret.get_secret_value().encode()
        ):
            raise HTTPException(403, "Invalid webhook secret")
        if not app.state.ready or not app.state.telegram:
            raise HTTPException(503, "Bot unavailable")
        body = await request.body()
        if len(body) > 262144:
            raise HTTPException(413, "Update too large")
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("update_id"), int):
                raise ValueError("Invalid update")
            update = Update.de_json(payload, app.state.telegram.bot)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise HTTPException(400, "Invalid Telegram update") from None
        async with app.state.webhook_lock:
            await app.state.telegram.process_update(update)
        return {"ok": True}

    return app


app = create_app()
