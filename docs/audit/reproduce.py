import asyncio
from datetime import datetime, timedelta

import httpx
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.config import Settings
from app.db.supabase import SupabaseRepository
from app.main import create_app
from app.schemas.event import SG, EventDraft, Status
from app.services.date_resolution import validate_extraction
from app.services.scheduling import SchedulingService
from tests.conftest import extraction
from tests.fakes import MemoryRepository, QueueExtractor
from tests.sql_transport import SQLGateway
from tests.telegram_transport import TelegramTransport, message_payload


async def main():
    ref = datetime(2026, 10, 4, 12, tzinfo=SG)
    result = validate_extraction(extraction(start_time_text="07:00"), "Friday 07:00 to 9pm", ref)
    assert result.start_at.hour == 7
    print("PASS: explicit 07:00 stays 07:00")
    evening = validate_extraction(extraction(start_time_text="19:00"), "Friday 19:00 to 9pm", ref)
    assert evening.start_at.hour == 19 and evening.end_at.hour == 21
    print("PASS: valid 19:00 to 9pm accepted")

    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        user = await repo.ensure_user(1)
        start = datetime.now(SG) + timedelta(days=7)
        draft = EventDraft(
            title="Future booking", start_at=start, end_at=start + timedelta(hours=1)
        )
        event = await repo.create_preview(user.id, 1, draft)
        await repo.rsvp(user.id, event, Status.GOING)
        await db.query(
            "update events set created_at=now()-interval '31 days' where id=$1", [event.id]
        )
        recent = await repo.recent_events(user.id)
        _, busy = await repo.busy(user.id, draft)
        assert len(recent) == 1 and len(busy) == 1
        print(
            "PASS: future GOING event created 31 days ago remains discoverable and blocks availability"
        )

    class OutageRepository(MemoryRepository):
        async def ensure_user(self, telegram_id):
            raise ConnectionError("simulated database outage")

    class OutageTransport(TelegramTransport):
        async def do_request(self, url, method, request_data=None, **kwargs):
            if url.endswith("/sendMessage"):
                raise TimeoutError("simulated reply outage")
            return await super().do_request(url, method, request_data, **kwargs)

    service = SchedulingService(
        OutageRepository(), QueueExtractor(), None, Settings(_env_file=None, bot_mode="disabled")
    )
    transport = OutageTransport()
    bot = Application.builder().token("123456:test").request(transport).updater(None).build()
    register_handlers(bot, service)
    app = create_app(
        Settings(
            _env_file=None,
            bot_mode="webhook",
            app_base_url="https://example.test",
            telegram_webhook_secret="test_secret_123456",
        ),
        service=service,
        telegram_application=bot,
    )
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test"
        ) as http,
    ):
        response = await http.post(
            "/telegram/webhook",
            json=message_payload("/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"},
        )
        assert response.status_code == 503 and not transport.sent
        print("PASS: failed webhook processing returns HTTP 503 for retry")


asyncio.run(main())
