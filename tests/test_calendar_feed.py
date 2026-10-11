"""Subscribed calendar feed: subscribe once, plans appear and update by themselves."""

import logging
import re
from datetime import timedelta

import httpx
import pytest

from app.config import Settings
from app.main import RedactFeedTokens, create_app
from app.schemas.event import Status
from tests.conftest import extraction, tap

TEXT = "CCA dinner Friday 7–9pm at Vivo"
BASE = "https://amifree.example"


@pytest.fixture
def online(service_factory):
    def make(*items):
        service, repo = service_factory(*items, app_base_url=BASE)
        return service, repo

    return make


def link_in(reply):
    return re.search(rf"{BASE}/cal/([A-Za-z0-9_-]+)\.ics", reply.text)


async def going(service, reference, telegram_id=1, message_id=2, text=TEXT):
    card = (await service.message(telegram_id, message_id, text, reference))[0]
    return (await service.callback(telegram_id, tap(card, "Going")))[0]


async def test_calendar_command_gives_a_private_link_and_iphone_steps(online):
    service, repo = online()
    reply = (await service.command(1, "calendar"))[0]
    token = link_in(reply)[1]
    assert len(token) >= 32
    assert "Add Subscribed Calendar" in reply.text and "Not connected yet" in reply.text
    assert [b.text for b in reply.keyboard.inline_keyboard[0]] == ["New link", "Turn off"]
    # Asking again shows the same link rather than breaking an existing subscription.
    assert link_in((await service.command(1, "calendar"))[0])[1] == token


async def test_without_a_public_address_it_explains_instead(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "calendar"))[0]
    assert "isn't set up yet" in reply.text and "cal/" not in reply.text


async def test_feed_has_only_your_going_and_saved_plans(online, reference):
    service, _ = online(
        extraction(),
        extraction(title="Hall supper", start_time_text="10pm", end_time_text="11pm"),
        extraction(title="Badminton", date_text="Saturday"),
        extraction(title="Someone else's plan"),
    )
    token = link_in((await service.command(1, "calendar"))[0])[1]
    await going(service, reference)
    supper = (await service.message(1, 3, "Hall supper Friday 10pm-11pm", reference))[0]
    await service.callback(1, tap(supper, "Save"))
    badminton = (await service.message(1, 4, "Badminton Saturday 7-9pm", reference))[0]
    await service.callback(1, tap(badminton, "Maybe"))
    await going(service, reference, telegram_id=2, message_id=5)
    feed = (await service.calendar_feed(token)).decode()
    assert feed.count("BEGIN:VEVENT") == 2
    assert "SUMMARY:CCA dinner" in feed and "SUMMARY:Hall supper" in feed
    assert "Badminton" not in feed and "Someone else" not in feed
    assert "X-WR-CALNAME:AmIFree" in feed


async def test_dropping_a_plan_removes_it_from_the_feed(online, reference):
    service, _ = online(extraction())
    token = link_in((await service.command(1, "calendar"))[0])[1]
    card = await going(service, reference)
    assert "CCA dinner" in (await service.calendar_feed(token)).decode()
    await service.callback(1, tap(card, "Can't make it"))
    assert "BEGIN:VEVENT" not in (await service.calendar_feed(token)).decode()


async def test_first_real_fetch_switches_cards_to_synced(online, reference):
    service, repo = online(
        extraction(),
        extraction(title="Hall supper", start_time_text="10pm", end_time_text="11pm"),
    )
    before = await going(service, reference)
    assert "Tip: /calendar" in before.text
    assert any(
        b.text == "🍎 Apple Calendar" for row in before.keyboard.inline_keyboard for b in row
    )
    token = link_in((await service.command(1, "calendar"))[0])[1]
    # Telegram's link preview fetches the URL too: that is not the iPhone subscribing.
    await service.calendar_feed(token, "TelegramBot (like TwitterBot)")
    assert (await repo.ensure_user(1)).calendar_feed_seen_at is None
    await service.calendar_feed(token, "iOS/18.0 (22A3354) dataaccessd/1.0")
    assert (await repo.ensure_user(1)).calendar_feed_seen_at is not None
    assert "✅ Connected" in (await service.command(1, "calendar"))[0].text
    after = await going(service, reference, message_id=3, text="Hall supper Friday 10pm-11pm")
    assert "Syncing to your calendar automatically" in after.text and "Tip:" not in after.text
    labels = [b.text for row in after.keyboard.inline_keyboard for b in row]
    assert "🍎 Apple Calendar" not in labels and "📅 Google Calendar" not in labels


async def test_seen_is_not_rewritten_on_every_fetch(online, reference):
    service, repo = online()
    token = link_in((await service.command(1, "calendar"))[0])[1]
    await service.calendar_feed(token)
    first = (await repo.ensure_user(1)).calendar_feed_seen_at
    await service.calendar_feed(token)
    assert (await repo.ensure_user(1)).calendar_feed_seen_at == first
    service.clock = lambda: reference + timedelta(minutes=11)
    await service.calendar_feed(token)
    assert (await repo.ensure_user(1)).calendar_feed_seen_at > first


async def test_new_link_and_turn_off_kill_the_old_link(online):
    service, _ = online()
    first = (await service.command(1, "calendar"))[0]
    old = link_in(first)[1]
    renewed = (await service.callback(1, tap(first, "New link")))[0]
    new = link_in(renewed)[1]
    assert new != old and "old one has stopped working" in renewed.text
    assert await service.calendar_feed(old) is None
    assert await service.calendar_feed(new) is not None
    off = (await service.callback(1, tap(renewed, "Turn off")))[0]
    assert "no longer works" in off.text
    assert await service.calendar_feed(new) is None


async def test_deleting_my_data_kills_the_link(online):
    service, repo = online()
    token = link_in((await service.command(1, "calendar"))[0])[1]
    await repo.delete_user((await repo.ensure_user(1)).id)
    assert await service.calendar_feed(token) is None


async def test_feed_over_http(online, reference):
    service, _ = online(extraction())
    token = link_in((await service.command(1, "calendar"))[0])[1]
    await going(service, reference)
    app = create_app(Settings(_env_file=None, bot_mode="disabled"), service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        ok = await client.get(f"/cal/{token}.ics")
        assert ok.status_code == 200
        assert ok.headers["content-type"].startswith("text/calendar")
        assert "SUMMARY:CCA dinner" in ok.text
        assert (await client.get("/cal/" + "x" * 43 + ".ics")).status_code == 404  # unknown
        assert (await client.get("/cal/short.ics")).status_code == 404  # malformed


def test_feed_tokens_never_reach_the_access_log():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/cal/abcdefghijklmnopqrstuvwxyz0123456789ABCD.ics", "1.1", 200),
        None,
    )
    RedactFeedTokens().filter(record)
    assert "abcdefghij" not in record.getMessage() and "/cal/[redacted]" in record.getMessage()


async def test_change_alert_says_synced_instead_of_buttons(online, reference):
    service, repo = online(extraction())
    token = link_in((await service.command(1, "calendar"))[0])[1]
    await service.calendar_feed(token)
    card = await going(service, reference)
    event = next(iter(repo.events.values()))
    assert event.status == Status.GOING and "Syncing" in card.text
