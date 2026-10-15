"""Regressions from the group-features audit."""

import asyncio
from datetime import timedelta

import pytest
from telegram import Update
from telegram.error import RetryAfter

from app.bot.handlers import CardRefresher, addressed_to_me
from app.schemas.event import Status
from app.services.jobs import Notifier
from app.services.recurrence import parse_recurrence
from tests.conftest import extraction
from tests.test_group_events import CHAT, alerts, buttons, group_message, group_tap, make_bot
from tests.test_group_features import card, press
from tests.test_group_reports import PLAN, finished_event, run
from tests.test_group_series import SERIES, post_series, sessions
from tests.test_notifications import Outbox


async def test_detection_failure_never_answers_chatter(service_factory):
    service, repo = service_factory(PLAN)

    async def broken(chat_id):
        raise ConnectionError("database blip")

    repo.detection_enabled = broken
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 80, 2)
    assert transport.sent == [] and alerts(transport) == []


async def test_commands_for_another_bot_are_ignored(service_factory):
    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "/stats@SomeOtherBot", 81)
        await run(bot, "/export@someotherbot", 82)
        assert transport.sent == []
        await run(bot, "/stats@AmIFreeTestBot", 83)
    assert "No finished events" in transport.sent[-1]["text"]
    assert addressed_to_me("/event", "Bot") and addressed_to_me("/event@bot x", "Bot")
    assert not addressed_to_me("/event@other", "Bot")


async def test_anonymous_admin_can_switch_detection(service_factory):
    service, repo = service_factory()
    bot, transport = make_bot(service)
    payload = group_message("/detect on", 84, 1087968824)
    payload["message"]["sender_chat"] = {"id": CHAT, "type": "supergroup", "title": "CCA"}
    payload["message"]["from"]["is_bot"] = True
    async with bot:
        await bot.process_update(Update.de_json(payload, bot.bot))
    assert repo.detection[CHAT] is True


async def test_bulk_series_changes_send_one_message_per_member(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    taps = buttons(transport.sent[-1]["reply_markup"])
    async with bot:
        await bot.process_update(Update.de_json(group_tap(taps["Going to all"], 2), bot.bot))
    first = sessions(repo)[0]
    await service.command(1, "start", f"cancel_{first.id}")
    state = (await repo.ensure_user(1)).pending
    await service.callback(1, f"confirm:{state['nonce']}")
    sent_before = len(outbox.sent)
    await service.callback(1, f"ser:stop:{first.id}")
    stops = outbox.sent[sent_before:]
    assert len(stops) == 1 and stops[0][0] == 2
    assert "all sessions after Tue 6 Oct are cancelled" in stops[0][1]
    assert not [n for n in repo.notifications if n["status"] == "pending"]


async def test_going_to_all_keeps_a_deliberate_cant(service_factory):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    found = sessions(repo)
    await repo.group_rsvp((await repo.ensure_user(2)).id, found[3], Status.DECLINED)
    taps = buttons(transport.sent[-1]["reply_markup"])
    async with bot:
        await bot.process_update(Update.de_json(group_tap(taps["Going to all"], 2), bot.bot))
    assert "You're Going to 11 sessions. Kept your Can't for 1." in alerts(transport)[-1]["text"]
    mine = await repo.linked_event((await repo.ensure_user(2)).id, found[3].id)
    assert mine.status == Status.DECLINED


async def test_series_from_an_old_forward_starts_in_the_future(service_factory, reference):
    service, repo = service_factory(SERIES)
    service.clock = lambda: reference + timedelta(days=10)  # Wed 14 Oct
    bot, transport = await post_series(service, repo)  # message dated Sun 4 Oct
    assert sessions(repo)[0].start_at > service.clock()


def test_until_a_time_is_not_an_end_date(reference):
    found = parse_recurrence("every tue 7pm till 10 tonight", reference)
    assert found.weekdays == [1] and found.until is None


async def test_posted_sessions_are_chased_from_when_they_appear(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    later = sessions(repo)[4]  # Tue 20 Oct, card not posted yet
    await repo.ensure_user(3)
    repo.members.add((CHAT, (await repo.ensure_user(3)).id))
    service.clock = lambda: later.start_at - timedelta(days=3)
    assert (await service.notifier.run()).get("posted") == 1  # Tue 20 Oct
    posted = repo.group_events[later.id]
    assert posted.created_at == service.clock()
    # A day after posting, members who have not answered are chased.
    service.clock = lambda: later.start_at - timedelta(days=1, hours=-1)
    keys = [n["dedupe_key"] for n in repo.notifications if n["kind"] == "NUDGE"]
    assert not keys
    await service.notifier.run()
    keys = [n["dedupe_key"] for n in repo.notifications if n["kind"] == "NUDGE"]
    assert f"event:{later.id}:mid" in keys


async def test_overlapping_job_runs_post_a_session_once(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    slow = service.post_group_card

    async def post(chat_id, card):
        await asyncio.sleep(0.01)
        return await slow(chat_id, card)

    service.post_group_card = post
    service.clock = lambda: reference + timedelta(days=1)
    await asyncio.gather(service.notifier.run(), service.notifier.run())
    assert len(service.posted_cards) == 1


async def test_default_reply_by_leaves_time_to_meet(service_factory, reference):
    service, repo = service_factory()
    await service.polls.create(1, CHAT, 30, "sync 1h tomorrow")
    poll = next(iter(repo.polls.values()))
    # The window ends at midnight tomorrow: halfway through what is left, not two days out.
    assert poll.deadline_at <= reference + (poll.window_end - reference) / 2
    await service.polls.create(1, CHAT, 31, "sync 1h tomorrow reply by friday")
    chosen = [p for p in repo.polls.values() if p.source_message_id == 31][0]
    assert chosen.deadline_at <= chosen.window_end - timedelta(hours=1)


async def test_stale_poll_is_not_retried_every_tick(service_factory):
    service, repo = service_factory()
    await service.polls.create(1, CHAT, 30, "sync 1h next week")
    poll = next(iter(repo.polls.values()))
    poll.message_id = 5
    for telegram_id in (2, 3):
        await repo.poll_join(poll.id, (await repo.ensure_user(telegram_id)).id)
    calls = []
    original = service.polls.busy_spans

    async def counted(p, person):
        calls.append(person)
        return await original(p, person)

    service.polls.busy_spans = counted
    now = poll.deadline_at + timedelta(minutes=1)
    await service.polls.sweep_deadlines(now)
    await service.polls.sweep_deadlines(now)
    assert len(calls) == 3  # one attempt until somebody else joins
    await repo.poll_join(poll.id, (await repo.ensure_user(4)).id)
    await service.polls.sweep_deadlines(now)
    assert len(calls) == 7


async def test_dropped_then_back_is_not_counted_twice(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    async with bot:
        await press(bot, taps["Going"], 2, 9500)
    user = await repo.ensure_user(2)
    await repo.set_attendance_check(event.id, user.id, "DROPPED")
    summary = await service.groups.attendance_summary(repo.group_events[event.id])
    assert summary["dropped"] == 0 and summary["no_reply"] == 1


async def test_export_with_a_huge_number_does_not_crash(service_factory):
    service, repo = service_factory(extraction(), pro_chat_ids=str(CHAT))
    bot, transport, event = await finished_event(service, repo)
    async with bot:
        await run(bot, "/export " + "9" * 5000, 85)
    assert len(transport.documents) == 1


# Building RetryAfter in a test reads its deprecated seconds field; the app code avoids it.
@pytest.mark.filterwarnings("ignore::telegram.warnings.PTBDeprecationWarning")
async def test_rate_limited_card_edit_is_retried_not_reported(service_factory):
    edits = []

    async def edit(key):
        edits.append(key)
        if len(edits) == 1:
            raise RetryAfter(0)

    refresher = CardRefresher(0, edit)
    await refresher.refresh("card")
    assert edits == ["card"]
    await asyncio.sleep(0.7)
    assert edits == ["card", "card"]


async def test_suggestions_do_not_keep_quoted_notes(service_factory):
    service, repo = service_factory(
        PLAN.model_copy(update={"notes": "my private text from the message"})
    )
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 86, 2)
    stored = next(iter(repo.suggestions.values()))
    assert stored.draft["notes"] is None
    assert "private text" not in transport.sent[-1]["text"]


async def test_removed_bot_stops_trying_to_post_sessions(service_factory, reference):
    from telegram.error import Forbidden

    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    attempts = []

    async def refused(chat_id, card):
        attempts.append(chat_id)
        raise Forbidden("bot was kicked from the group chat")

    service.post_group_card = refused
    service.clock = lambda: reference + timedelta(days=1)
    await service.notifier.run()
    await service.notifier.run()
    assert len(attempts) == 1
    assert sessions(repo)[1].message_id == -1
    # Never chased, asked about, or redrawn.
    service.clock = lambda: sessions(repo)[1].start_at - timedelta(hours=10)
    result = await service.notifier.run()
    assert result["queued"] == 0
