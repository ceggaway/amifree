from datetime import timedelta

import httpx
import pytest
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.bot.messages import Reply
from app.config import Settings
from app.main import create_app
from app.schemas.event import EventDraft
from app.services.date_resolution import validate_extraction
from tests.conftest import extraction, onboard
from tests.telegram_transport import TelegramTransport, message_payload


@pytest.mark.parametrize("start,hour", [("07:00", 7), ("19:00", 19), ("00:30", 0), ("7", 19)])
def test_mixed_time_format_preserves_explicit_hours(reference, start, hour):
    draft = validate_extraction(
        extraction(start_time_text=start), f"Friday {start} to 9pm", reference
    )
    assert draft.start_at.hour == hour
    assert draft.end_at.hour == 21


async def test_all_event_pages_are_reachable_and_user_scoped(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    user = await repo.ensure_user(1)
    for index in range(21):
        await repo.create_preview(
            user.id,
            index + 2,
            EventDraft(
                title=f"Booking {index}", start_at=reference, end_at=reference + timedelta(hours=1)
            ),
        )
    first = await service.command(1, "events")
    assert len(first) == 11 and "page 1 of 3" in first[-1].text
    second = await service.callback(1, first[-1].keyboard.inline_keyboard[0][-1].callback_data)
    assert "page 2 of 3" in second[-1].text
    third = await service.callback(1, second[-1].keyboard.inline_keyboard[0][-1].callback_data)
    assert len(third) == 2 and "Booking 20" in third[0].text
    assert "No upcoming" in (await service.callback(2, "events:2"))[0].text
    assert "isn't valid" in (await service.callback(1, "events:-1"))[0].text


@pytest.mark.parametrize("fail_reply", [False, True])
async def test_webhook_failure_recovery_and_duplicate_delivery(service_factory, fail_reply):
    service, repo = service_factory()
    calls = []

    async def command(uid, name, args=""):
        calls.append(name)
        await repo.ensure_user(uid)
        return [Reply("First response"), Reply("Second response")]

    service.command = command

    class Transport(TelegramTransport):
        offline = fail_reply

        async def do_request(self, url, method, request_data=None, **kwargs):
            if url.endswith("/sendMessage") and self.offline and len(self.sent) == 1:
                raise TimeoutError("simulated reply outage")
            return await super().do_request(url, method, request_data, **kwargs)

    transport = Transport()
    repo.available = fail_reply  # either fail the database or the second reply
    bot = Application.builder().token("123456:test").request(transport).updater(None).build()
    register_handlers(bot, service)
    app = create_app(
        Settings(
            _env_file=None,
            bot_mode="webhook",
            app_base_url="https://test.example",
            telegram_webhook_secret="test_secret_123456",
        ),
        service=service,
        telegram_application=bot,
    )
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="https://test.example",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"},
        ) as client,
    ):
        payload = message_payload("/start")
        assert (await client.post("/telegram/webhook", json=payload)).status_code == 503
        repo.available = True
        transport.offline = False
        assert (await client.post("/telegram/webhook", json=payload)).status_code == 200
        assert (await client.post("/telegram/webhook", json=payload)).status_code == 200
        assert [r["text"] for r in transport.sent] == ["First response", "Second response"]
        assert len(calls) == (1 if fail_reply else 2)
        # However it got there (a retried send, or a rerun), the update ends up finished.
        assert repo.updates[payload["update_id"]] == "done"
