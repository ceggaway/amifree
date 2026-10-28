"""Regressions from the October 2026 audit of the group features."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from telegram import Update

from app.schemas.event import SG, Suggestion
from app.services.polls import PollService, parse_request
from tests.conftest import extraction
from tests.test_group_events import CHAT, alerts, group_tap, make_bot
from tests.test_group_reports import PLAN
from tests.test_group_series import SERIES, post_series, sessions

WEDNESDAY = datetime(2026, 10, 7, 12, tzinfo=SG)


def test_this_weekend_is_not_read_as_this_week():
    title, _, start, end = parse_request("dinner this weekend", WEDNESDAY)
    assert title == "dinner"
    assert (start, end) == (datetime(2026, 10, 10, tzinfo=SG), datetime(2026, 10, 12, tzinfo=SG))
    # A window word inside another word is not a window.
    assert parse_request("todays agenda", WEDNESDAY)[0] == "todays agenda"


async def open_poll(service, repo, slots):
    organiser = await repo.ensure_user(1)
    poll = await repo.create_poll(
        organiser.id,
        CHAT,
        40,
        "sync",
        60,
        datetime(2026, 10, 5, tzinfo=SG),
        datetime(2026, 10, 6, tzinfo=SG),
    )
    await repo.set_poll_message(poll.id, 140)
    return await repo.poll_open(
        poll.id,
        organiser.id,
        [
            {"start_at": s.isoformat(), "end_at": (s + timedelta(hours=1)).isoformat(), "free": 3}
            for s in slots
        ],
    )


async def test_old_fill_button_records_no_vote(service_factory):
    """Cards from before the organiser chose privately still carry Fill mine: it does nothing."""
    service, repo = service_factory()
    morning = datetime(2026, 10, 5, 10, tzinfo=SG)
    poll = await open_poll(service, repo, [morning])
    member = await repo.ensure_user(2)
    await repo.poll_join(poll.id, member.id)
    result = await PollService(service).callback(2, CHAT, f"p:fill:{poll.id}")
    assert "organiser is choosing" in result.alert
    assert not repo.poll_votes


async def test_stopping_a_series_leaves_sessions_already_held(service_factory, reference):
    service, repo = service_factory(SERIES)
    await post_series(service, repo)
    found = sessions(repo)
    # Three sessions have happened; the organiser cancels the first and stops the rest.
    service.clock = lambda: found[2].end_at + timedelta(minutes=1)
    organiser = await repo.ensure_user(1)
    await repo.update_group_event(organiser.id, found[0], None, True)
    answer = await service.callback(1, f"ser:stop:{found[0].id}")
    assert f"{len(found) - 3} later session(s) cancelled" in answer[0].text
    statuses = [g.status for g in sessions(repo)]
    assert statuses[:3] == ["CANCELLED", "ACTIVE", "ACTIVE"]
    assert set(statuses[3:]) == {"CANCELLED"}


class RateLimited:
    """Wraps the fake transport: the first poll card edit is refused with "retry after"."""

    def __init__(self, transport):
        self.transport, self.refused = transport, False
        original = transport.do_request

        async def do_request(url, method, request_data=None, **kwargs):
            if url.endswith("/editMessageText") and not self.refused:
                self.refused = True
                transport.calls.append(("editMessageText", {}))
                body = {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 1",
                    "parameters": {"retry_after": 1},  # 0 is not read as a retry
                }
                return 429, json.dumps(body).encode()
            return await original(url, method, request_data, **kwargs)

        transport.do_request = do_request


@pytest.mark.filterwarnings("ignore::telegram.warnings.PTBDeprecationWarning")
async def test_rate_limited_poll_card_edit_is_retried(service_factory):
    service, repo = service_factory()
    bot, transport = make_bot(service)
    RateLimited(transport)
    poll = await open_poll(service, repo, [datetime(2026, 10, 5, 10, tzinfo=SG)])
    async with bot:
        await service.poll_changed(poll.id)
        await asyncio.sleep(1.7)
    edits = [name for name, _ in transport.calls if name == "editMessageText"]
    assert len(edits) == 2  # refused once, then redrawn instead of lost


async def test_retired_suggestions_cannot_be_accepted_by_authors_members_or_admins(service_factory):
    service, repo = service_factory(PLAN)
    bot, transport = make_bot(service)
    async with bot:
        for index, member in enumerate((2, 3, 5)):
            transport.admins.add(5)
            await bot.process_update(
                Update.de_json(
                    group_tap("d:ok:old-suggestion", member, update_id=9300 + index), bot.bot
                )
            )
            assert "no longer supported" in alerts(transport)[-1]["text"]
    assert not repo.group_events and not service.extractor.calls


async def test_suggestions_from_before_the_migration_are_also_retired(service_factory):
    service, repo = service_factory()
    old = Suggestion(id="x", chat_id=CHAT, source_message_id=1, draft={})
    repo.suggestions[old.id] = old
    result = await service.detection.callback(3, CHAT, "d:ok:x")
    assert "no longer supported" in result.alert and result.delete_message
    assert not repo.group_events


async def test_manage_opens_a_private_menu_for_the_organiser_only(service_factory):
    from tests.test_group_events import post_card

    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        card, taps, event = await post_card(bot, transport, repo)
        # A member is refused in private; nothing opens for them.
        await bot.process_update(Update.de_json(group_tap(taps["⚙️ Manage"], 2), bot.bot))
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        # The organiser is sent to the private chat with a start parameter.
        await bot.process_update(
            Update.de_json(group_tap(taps["⚙️ Manage"], 1, update_id=5001), bot.bot)
        )
        assert alerts(transport)[-1]["url"].endswith(f"?start=manage_{event.id}")

    panel = (await service.command(1, "start", f"manage_{event.id}"))[0]
    labels = [b.text for row in panel.keyboard.inline_keyboard for b in row]
    assert labels == [
        "✏️ Edit",
        "❌ Cancel event",
        "📣 Nudge members",
        "📊 Who's confirmed",
        "📋 Mark who came",
    ]
    assert all(
        len(b.callback_data.encode()) <= 64 for row in panel.keyboard.inline_keyboard for b in row
    )
    data = {b.text: b.callback_data for row in panel.keyboard.inline_keyboard for b in row}
    assert "Going 0" in (await service.callback(1, data["📊 Who's confirmed"]))[0].text
    assert "Nudging 1 member" in (await service.callback(1, data["📣 Nudge members"]))[0].text
    asked = (await service.callback(1, data["✏️ Edit"]))[0]
    assert asked.text.startswith("What do you want to change?")
    # Someone else cannot open or use the menu, even with the button data.
    assert "Only the organiser" in (await service.command(2, "start", f"manage_{event.id}"))[0].text
    assert "Only the organiser" in (await service.callback(2, data["❌ Cancel event"]))[0].text


async def test_help_works_inside_a_group(service_factory):
    from tests.test_group_events import group_message

    service, repo = service_factory()
    bot, transport = make_bot(service)
    async with bot:
        for n, command in enumerate(("/help", "/start")):
            await bot.process_update(Update.de_json(group_message(command, 60 + n, 1), bot.bot))
            text = transport.sent[-1]["text"]
            assert "How to use AmIFree in this group" in text
            assert "/app" in text and "/attendance" in text
            # Retired commands are never advertised.
            assert "/event" not in text and "/whosfree" not in text
