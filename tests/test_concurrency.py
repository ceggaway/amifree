"""Different people are served at the same time; one person's messages stay in order."""

import asyncio

import httpx
from telegram import Update
from telegram.ext import Application

from app.bot.concurrency import KeyedLocks, PerUserProcessor, update_keys
from app.bot.handlers import register_handlers
from app.config import Settings
from app.main import create_app, telegram_builder
from tests.conftest import extraction, onboard
from tests.telegram_transport import TelegramTransport, message_payload

HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test_secret_123456"}
DINNER = "CCA dinner Friday 7–9pm at Vivo"


async def test_keyed_locks_serialise_one_key_and_not_others():
    locks, order = KeyedLocks(), []
    release = asyncio.Event()

    async def job(name, keys, wait=False):
        async with locks.hold(keys):
            order.append(f"{name} in")
            if wait:
                await release.wait()
            order.append(f"{name} out")

    first = asyncio.create_task(job("a1", ["a"], wait=True))
    await asyncio.sleep(0)
    second = asyncio.create_task(job("a2", ["a"]))
    other = asyncio.create_task(job("b1", ["b"]))
    await asyncio.sleep(0.01)
    # b ran to completion while a1 held its key; a2 is still waiting behind a1.
    assert order == ["a1 in", "b1 in", "b1 out"]
    release.set()
    await asyncio.gather(first, second, other)
    assert order[3:] == ["a1 out", "a2 in", "a2 out"]
    assert len(locks) == 0  # nothing is kept once nobody needs it


async def test_shared_keys_are_taken_in_one_order_so_nothing_deadlocks():
    locks = KeyedLocks()

    async def job(keys):
        async with locks.hold(keys):
            await asyncio.sleep(0)

    await asyncio.wait_for(
        asyncio.gather(*[job(["x", "y"] if n % 2 else ["y", "x"]) for n in range(20)]), 2
    )
    assert len(locks) == 0


async def test_a_waiter_that_is_cancelled_leaves_no_lock_behind():
    locks = KeyedLocks()
    holding = asyncio.Event()

    async def holder():
        async with locks.hold(["k"]):
            holding.set()
            await asyncio.sleep(0.05)

    async def waiter():
        async with locks.hold(["k"]):
            raise AssertionError("never reached")

    first = asyncio.create_task(holder())
    await holding.wait()
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert len(locks) == 0
    async with locks.hold(["k"]):  # and the key is usable again
        pass


def test_update_keys_cover_the_sender_and_a_group_chat():
    bot_update = Update.de_json(message_payload("hi", 1, telegram_id=7), None)
    assert update_keys(bot_update) == [("user", 7)]  # private chat id is the user id
    group = Update.de_json(message_payload("/event x", 1, telegram_id=7, group=True), None)
    assert update_keys(group) == [("user", 7), ("chat", -1)]
    assert update_keys(object())[0][0] == "update"


def test_the_bot_is_built_to_process_users_concurrently():
    builder = telegram_builder().token("123456:fake-test-token")
    application = builder.updater(None).build()
    assert isinstance(application.update_processor, PerUserProcessor)
    assert application.update_processor.max_concurrent_updates > 1


async def test_polling_processor_orders_one_user_and_overlaps_two():
    processor, order = PerUserProcessor(), []
    gate = asyncio.Event()

    async def handle(name, wait=False):
        order.append(f"{name} start")
        if wait:
            await gate.wait()
        order.append(f"{name} end")

    def update(user, n):
        return Update.de_json(message_payload("x", n, telegram_id=user), None)

    tasks = [
        asyncio.create_task(processor.process_update(update(1, 1), handle("u1-first", wait=True))),
        asyncio.create_task(processor.process_update(update(1, 2), handle("u1-second"))),
        asyncio.create_task(processor.process_update(update(2, 3), handle("u2"))),
    ]
    await asyncio.sleep(0.01)
    assert order == ["u1-first start", "u2 start", "u2 end"]
    gate.set()
    await asyncio.gather(*tasks)
    assert order[3:] == ["u1-first end", "u1-second start", "u1-second end"]


class SlowExtractor:
    """User 1's extraction hangs until released; everyone else's returns at once."""

    def __init__(self):
        self.release, self.started, self.calls = asyncio.Event(), asyncio.Event(), []

    async def extract(self, text, reference, previous=None):
        self.calls.append(text)
        if text.startswith("SLOW"):
            self.started.set()
            await self.release.wait()
        return extraction()


async def test_a_slow_request_does_not_hold_up_another_user(service_factory, reference):
    service, repo = service_factory()
    service.extractor = SlowExtractor()
    await onboard(service, reference, 1)
    await onboard(service, reference, 2)
    transport = TelegramTransport()
    bot = (
        Application.builder()
        .token("123456:fake-test-token")
        .request(transport)
        .updater(None)
        .build()
    )
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
    stamp = int(reference.timestamp())
    async with (
        bot,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://example.test", headers=HEADERS
        ) as client,
    ):
        slow = asyncio.create_task(
            client.post(
                "/telegram/webhook",
                json=message_payload("SLOW " + DINNER, 50, 1, forward_timestamp=stamp),
            )
        )
        await asyncio.wait_for(service.extractor.started.wait(), 2)
        # User 1's own next message waits its turn...
        queued = asyncio.create_task(
            client.post(
                "/telegram/webhook", json=message_payload(DINNER, 51, 1, forward_timestamp=stamp)
            )
        )
        # ...while user 2 is answered straight away.
        fast = await asyncio.wait_for(
            client.post(
                "/telegram/webhook", json=message_payload(DINNER, 52, 2, forward_timestamp=stamp)
            ),
            2,
        )
        assert fast.status_code == 200
        assert [int(m["chat_id"]) for m in transport.sent] == [2]
        assert service.extractor.calls == ["SLOW " + DINNER, DINNER]  # user 1's second not yet
        assert not queued.done()
        service.extractor.release.set()
        assert (await slow).status_code == 200 and (await queued).status_code == 200
    assert [int(m["chat_id"]) for m in transport.sent] == [2, 1, 1]
    assert len(app.state.update_locks) == 0
