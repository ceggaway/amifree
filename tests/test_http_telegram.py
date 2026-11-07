import json

import httpx
import pytest
from telegram import Update
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.config import Settings
from app.main import create_app
from tests.conftest import extraction, onboard
from tests.telegram_transport import TelegramTransport, callback_payload, message_payload


async def test_webhook_to_telegram_complete_flow(service_factory, reference):
    service, repo = service_factory(extraction(), extraction())
    transport = TelegramTransport()
    bot = (
        Application.builder()
        .token("123456:fake-test-token")
        .request(transport)
        .updater(None)
        .build()
    )
    register_handlers(bot, service)
    settings = Settings(
        _env_file=None,
        bot_mode="webhook",
        app_base_url="https://example.test",
        telegram_webhook_secret="test_secret_123456",
    )
    app = create_app(settings, service=service, telegram_application=bot)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"}
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test", headers=headers
        ) as client,
    ):
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 200
        await client.post("/telegram/webhook", json=message_payload("/start"))
        assert "Try an example" in transport.sent[-1]["text"]
        await client.post(
            "/telegram/webhook",
            json=message_payload("https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", 2),
        )
        assert "✅ <b>Timetable connected</b>" in transport.sent[-1]["text"]
        assert transport.sent[-1]["parse_mode"] == "HTML"
        # Original forward timestamp, not the time the forward is received.
        await client.post(
            "/telegram/webhook",
            json=message_payload(
                "CCA dinner Friday 7–9pm at Vivo", 3, forward_timestamp=int(reference.timestamp())
            ),
        )
        first = transport.sent[-1]
        assert "No clashes found" in first["text"]
        markup = first["reply_markup"]
        if isinstance(markup, str):
            markup = json.loads(markup)
        callback = markup["inline_keyboard"][0][0]["callback_data"]
        assert len(callback.encode()) <= 64
        await client.post("/telegram/webhook", json=callback_payload(callback))
        # Going updates the tapped card in place rather than sending a new message.
        edits = [p for name, p in transport.calls if name == "editMessageText"]
        assert any("Added to /plans" in e["text"] for e in edits)
        assert edits[-1]["parse_mode"] == "HTML"
        assert "<b>CCA dinner</b>" in edits[-1]["text"]
        assert int(edits[-1]["message_id"]) == 100
        await client.post(
            "/telegram/webhook",
            json=message_payload(
                "CCA dinner Friday 7–9pm at Vivo", 4, forward_timestamp=int(reference.timestamp())
            ),
        )
        assert "Overlaps with" in transport.sent[-1]["text"]
        assert len(repo.events) == 2
        assert service.extractor.calls[0][1] == reference
        assert any(name == "answerCallbackQuery" for name, _ in transport.calls)


async def test_webhook_secret_and_payload_validation(service_factory):
    service, _ = service_factory()
    bot = (
        Application.builder()
        .token("123456:test")
        .request(TelegramTransport())
        .updater(None)
        .build()
    )
    register_handlers(bot, service)
    settings = Settings(
        _env_file=None,
        bot_mode="webhook",
        app_base_url="https://example.test",
        telegram_webhook_secret="test_secret_123456",
    )
    app = create_app(settings, service=service, telegram_application=bot)
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test"
        ) as client,
    ):
        assert (
            await client.post("/telegram/webhook", json=message_payload("/start"))
        ).status_code == 403
        headers = {"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"}
        for payload in [{}, [], {"update_id": "bad"}]:
            assert (
                await client.post("/telegram/webhook", json=payload, headers=headers)
            ).status_code == 400
        assert (
            await client.post(
                "/telegram/webhook", json={"update_id": 1, "message": []}, headers=headers
            )
        ).status_code == 400
        assert (
            await client.post(
                "/telegram/webhook",
                json=message_payload("/start"),
                headers={"X-Telegram-Bot-Api-Secret-Token": b"\xff"},
            )
        ).status_code == 403
        assert (
            await client.post("/telegram/webhook", content="not json", headers=headers)
        ).status_code == 400
        assert (
            await client.post("/telegram/webhook", content="x" * 262145, headers=headers)
        ).status_code == 413


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("secret-value"),
        ValueError("invalid JSON private message"),
        ConnectionError("secret password"),
    ],
)
async def test_upstream_failure_returns_friendly_message(service_factory, reference, error, caplog):
    service, _ = service_factory(error)
    await onboard(service, reference)
    transport = TelegramTransport()
    bot = Application.builder().token("123456:test").request(transport).updater(None).build()
    register_handlers(bot, service)
    async with bot:
        await bot.process_update(
            Update.de_json(message_payload("CCA dinner Friday 7–9pm at Vivo", 2), bot.bot)
        )
    assert "couldn't process" in transport.sent[-1]["text"]
    assert "secret-value" not in caplog.text and "secret password" not in caplog.text
    assert "invalid JSON private message" not in caplog.text


async def test_group_callbacks_do_not_reveal_private_data(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    transport = TelegramTransport()
    bot = Application.builder().token("123456:test").request(transport).updater(None).build()
    register_handlers(bot, service)
    async with bot:
        await bot.process_update(
            Update.de_json(
                callback_payload(card.keyboard.inline_keyboard[0][0].callback_data, group=True),
                bot.bot,
            )
        )
        await bot.process_update(Update.de_json(message_payload("/events", group=True), bot.bot))
    assert len(transport.sent) == 1
    assert "private chat" in transport.sent[0]["text"]
    assert "Vivo" not in transport.sent[0]["text"]
    assert next(iter(repo.events.values())).status.value == "PREVIEW"


async def test_health_only_mode_and_database_outage(service_factory):
    app = create_app(Settings(_env_file=None, bot_mode="disabled"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 503
        assert (await client.post("/telegram/webhook")).status_code == 404
    service, repo = service_factory()
    app = create_app(Settings(_env_file=None, bot_mode="disabled"), service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        repo.available = False
        assert (await client.get("/readyz")).status_code == 503


async def test_missing_credentials_prevent_bot_start():
    app = create_app(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="Configure .env"):
        async with app.router.lifespan_context(app):
            pass


SCALE_POST = """AI can generate 100 ideas in seconds. But can it tell you which one is worth pursuing?

Join Associate Professor Chai Kah Hin, Dean of NUS SCALE, for a session on creative problem-solving in the age of Generative AI.

📅 Wednesday, 21 October 2026
🕖 7pm (Registration and refreshments from 6.30pm)
📍 JustCo Marina Square
6 Raffles Blvd, #03-308, Singapore 039594

Register here"""


async def test_a_poster_with_a_caption_is_read_like_any_plan(service_factory, reference):
    """Regression: a forwarded channel post (photo + caption) got "photos aren't supported"."""
    from tests.telegram_transport import photo_payload

    seminar = extraction(
        title="Creative problem-solving seminar",
        date_text="Wednesday, 21 October 2026",
        start_time_text="7pm",
        end_time_text=None,
        location="JustCo Marina Square",
    )
    service, _ = service_factory(seminar)
    transport = TelegramTransport()
    bot = (
        Application.builder()
        .token("123456:fake-test-token")
        .request(transport)
        .updater(None)
        .build()
    )
    register_handlers(bot, service)
    async with bot:
        payload = photo_payload(SCALE_POST, 7, forward_timestamp=int(reference.timestamp()))
        await bot.process_update(Update.de_json(payload, bot.bot))
        # The caption reached the extractor, and the bot answered about the event itself.
        assert service.extractor.calls[0][0] == SCALE_POST
        reply = transport.sent[-1]["text"]
        assert "photo" not in reply.lower() and "end" in reply.lower()  # asks when it ends
        await bot.process_update(Update.de_json(photo_payload(None, 8), bot.bot))
        assert "I can't read pictures right now" in transport.sent[-1]["text"]


async def webhook_app(service):
    transport = TelegramTransport()
    bot = (
        Application.builder()
        .token("123456:fake-test-token")
        .request(transport)
        .updater(None)
        .build()
    )
    register_handlers(bot, service)
    settings = Settings(
        _env_file=None,
        bot_mode="webhook",
        app_base_url="https://example.test",
        telegram_webhook_secret="test_secret_123456",
    )
    return create_app(settings, service=service, telegram_application=bot), bot, transport


HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"}
DINNER = "CCA dinner Friday 7–9pm at Vivo"


async def test_a_retried_update_is_not_repeated_even_after_a_restart(service_factory, reference):
    service, repo = service_factory(extraction())  # a second extraction would run out of items
    await onboard(service, reference)
    app, bot, transport = await webhook_app(service)
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test", headers=HEADERS
        ) as client,
    ):
        payload = message_payload(DINNER, 40, forward_timestamp=int(reference.timestamp()))
        assert (await client.post("/telegram/webhook", json=payload)).status_code == 200
        sent, previews = len(transport.sent), len(repo.events)
        assert repo.updates[40] == "done"
        # The process restarted: its in-memory record of recent updates is gone.
        app.state.deliveries.clear()
        assert (await client.post("/telegram/webhook", json=payload)).status_code == 200
        assert (len(transport.sent), len(repo.events)) == (sent, previews)
        assert len(service.extractor.calls) == 1


async def test_a_failure_before_anything_commits_is_retried(service_factory, reference):
    service, repo = service_factory(TimeoutError("upstream"), extraction())
    await onboard(service, reference)
    app, bot, transport = await webhook_app(service)
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test", headers=HEADERS
        ) as client,
    ):
        payload = message_payload(DINNER, 41, forward_timestamp=int(reference.timestamp()))
        first = await client.post("/telegram/webhook", json=payload)
        assert first.status_code == 503 and 41 not in repo.updates  # claim released
        app.state.deliveries.clear()
        second = await client.post("/telegram/webhook", json=payload)
        assert second.status_code == 200 and repo.updates[41] == "done"
        assert len(service.extractor.calls) == 2


async def test_an_update_stuck_in_processing_is_retaken_once_stale(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    app, bot, _ = await webhook_app(service)
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test", headers=HEADERS
        ) as client,
    ):
        repo.updates[42] = "processing"  # a worker that crashed mid-update
        payload = message_payload(DINNER, 42, forward_timestamp=int(reference.timestamp()))
        await client.post("/telegram/webhook", json=payload)
        assert service.extractor.calls == []  # still fresh: not retaken
        repo.stale_updates.add(42)
        await client.post("/telegram/webhook", json=payload)
        assert len(service.extractor.calls) == 1 and repo.updates[42] == "done"
