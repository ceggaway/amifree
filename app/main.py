import asyncio
import hmac
import logging
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions
from telegram import BotCommand, MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import Application

from app.api.routes import build_router
from app.bot.delivery import Delivery, webhook_delivery
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
        app.state.deliveries = OrderedDict()
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
                    "Supabase startup check failed. Apply app/db/schema.sql, then app/db/migrations/*.sql in order, and check your connection credentials."
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
                    BotCommand("start", "Getting started"),
                    BotCommand("help", "How AmIFree works"),
                    BotCommand("plans", "Upcoming saved plans"),
                    BotCommand("block", "Mark yourself busy: busy sat 2-6pm"),
                    BotCommand("blocks", "Your busy blocks"),
                    BotCommand("reminders", "Night-before summary on or off"),
                    BotCommand("events", "Recent cards, including previews"),
                    BotCommand("cancel", "Clear pending action"),
                    BotCommand("deletemydata", "Delete everything about me"),
                ]
            )
            if settings.mini_app_url:
                await bot_app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        "Open AmIFree", WebAppInfo(url=settings.mini_app_url)
                    )
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

            async def retention():
                await repo.cleanup(settings.extraction_retention_days)

            async def periodic(seconds, job, name):
                while True:
                    await asyncio.sleep(seconds)
                    try:
                        await job()
                    except Exception as exc:
                        log.warning("%s_failed error_type=%s", name, type(exc).__name__)

            # The in-process tick is best effort: a sleeping or restarting instance misses
            # runs, so production should also call POST /internal/jobs/run from a scheduler.
            tasks = [
                asyncio.create_task(periodic(3600, retention, "retention_cleanup")),
                asyncio.create_task(periodic(60, app.state.service.notifier.run, "jobs")),
            ]

            async def stop_tasks():
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            stack.push_async_callback(stop_tasks)
            app.state.ready = True
            log.info("AmIFree ready mode=%s", settings.bot_mode)
            try:
                yield
            finally:
                app.state.ready = False

    app = FastAPI(
        title="AmIFree", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    app.include_router(build_router(settings))
    if settings.mini_app_dev_origin:
        # Bearer tokens, not cookies: no credentials mode and exactly one allowed origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.mini_app_dev_origin.rstrip("/")],
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith(("/api/", "/internal/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    # The built Mini App, when present, is served from this same origin.
    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if (dist / "index.html").is_file():
        app.mount("/app", StaticFiles(directory=dist, html=True), name="miniapp")

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

    @app.post("/internal/jobs/run")
    async def run_jobs(request: Request):
        secret = settings.jobs_secret.get_secret_value()
        if not secret:
            raise HTTPException(404, "Jobs endpoint is disabled")
        provided = request.headers.get("X-Jobs-Secret", "")
        if not hmac.compare_digest(provided.encode(), secret.encode()):
            raise HTTPException(403, "Invalid jobs secret")
        notifier = getattr(app.state.service, "notifier", None)
        if not app.state.ready or not notifier:
            raise HTTPException(503, "Bot unavailable")
        return await notifier.run()

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
            # Retain reply progress so a delivery retry does not repeat a successful
            # business operation (including /delete or a confirmed edit).
            deliveries = app.state.deliveries
            delivery = deliveries.get(update.update_id)
            if delivery is None:
                delivery = Delivery()
                token = webhook_delivery.set(delivery)
                try:
                    await app.state.telegram.process_update(update)
                finally:
                    webhook_delivery.reset(token)
            else:
                delivery.failed = False
                try:
                    await delivery.flush(update)
                except Exception:
                    delivery.failed = True
            if delivery.replies is not None or not delivery.failed:
                deliveries[update.update_id] = delivery
                deliveries.move_to_end(update.update_id)
                while len(deliveries) > 1000:
                    deliveries.popitem(last=False)
            if delivery.failed:
                raise HTTPException(503, "Update processing or reply delivery failed")
        return {"ok": True}

    return app


app = create_app()
