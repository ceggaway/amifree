"""Regressions from the October 2026 audit of the group features."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from telegram import Update

from app.schemas.event import SG, Suggestion
from app.services.detection import DetectionService
from app.services.polls import STEP, PollService, parse_request
from tests.test_group_events import CHAT, alerts, buttons, group_tap, make_bot
from tests.test_group_reports import PLAN, run
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


async def test_fill_mine_respects_the_members_own_grid(service_factory):
    service, repo = service_factory()
    morning, evening = datetime(2026, 10, 5, 10, tzinfo=SG), datetime(2026, 10, 5, 19, tzinfo=SG)
    poll = await open_poll(service, repo, [morning, evening])
    member = await repo.ensure_user(2)
    # No timetable or calendar: only "Set my times", ticking the morning hour.
    await repo.poll_join(poll.id, member.id)
    await repo.set_availability(
        poll.id, member.id, [morning.isoformat(), (morning + STEP).isoformat()]
    )
    result = await PollService(service).callback(2, CHAT, f"p:fill:{poll.id}")
    assert "1 Free · 2 Busy" in result.alert
    assert repo.poll_votes[(poll.id, member.id, 0)] == "FREE"
    assert repo.poll_votes[(poll.id, member.id, 1)] == "BUSY"


async def test_fill_mine_respects_usual_times(service_factory):
    service, repo = service_factory()
    morning, evening = datetime(2026, 10, 5, 10, tzinfo=SG), datetime(2026, 10, 5, 19, tzinfo=SG)
    poll = await open_poll(service, repo, [morning, evening])
    member = await repo.ensure_user(2)
    await repo.poll_join(poll.id, member.id)
    await repo.replace_usual(member.id, [(0, 18 * 60, 22 * 60)])  # only free Monday evenings
    result = await PollService(service).callback(2, CHAT, f"p:fill:{poll.id}")
    assert "1 Busy · 2 Free" in result.alert


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


async def test_only_the_author_or_an_admin_decides_a_suggestion(service_factory):
    service, repo = service_factory(PLAN)
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 97, 2)
        taps = buttons(transport.sent[-1]["reply_markup"])
        assert next(iter(repo.suggestions.values())).author_telegram_id == 2
        # Another member can neither take it over nor remove it.
        for n, label in enumerate(("Make event card", "Ignore")):
            await bot.process_update(
                Update.de_json(group_tap(taps[label], 3, update_id=9300 + n), bot.bot)
            )
            assert "Only the person who wrote that message" in alerts(transport)[-1]["text"]
        assert len(repo.suggestions) == 1 and not repo.group_events
        assert not [c for c in transport.calls if c[0] == "deleteMessage"]
        # A group admin can.
        transport.admins.add(5)
        await bot.process_update(
            Update.de_json(group_tap(taps["Ignore"], 5, update_id=9310), bot.bot)
        )
    assert not repo.suggestions
    assert [c for c in transport.calls if c[0] == "deleteMessage"]


async def test_suggestions_from_before_the_migration_stay_open(service_factory):
    old = Suggestion(id="x", chat_id=CHAT, source_message_id=1, draft={})
    assert await DetectionService.may_decide(old, 3, None)
