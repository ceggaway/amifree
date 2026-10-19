"""/stats, /export (Clubs Pro) and optional event detection."""

import csv
import io
from datetime import timedelta

from telegram import Update

from app.services.jobs import Notifier
from tests.conftest import extraction
from tests.test_group_events import (
    CHAT,
    alerts,
    buttons,
    group_message,
    group_tap,
    make_bot,
    post_card,
)
from tests.test_group_features import press
from tests.test_notifications import Outbox


async def finished_event(service, repo, going=(2, 3, 4), came=(2, 3)):
    """An event with RSVPs and check-ins that is over, with named members."""
    bot, transport = make_bot(service)
    service.notifier = Notifier(service, Outbox())
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        for n, member in enumerate(going):
            await press(bot, taps["Going"], member, 9000 + n, first_name=f"Member {member}")
        await press(bot, taps["Can't"], 8, 9020, first_name='=HYPERLINK("x")')
        start = repo.group_events[event.id].start_at
        service.clock = lambda: start - timedelta(minutes=5)
        for n, member in enumerate(came):
            await press(bot, taps["I'm here"], member, 9030 + n, first_name=f"Member {member}")
        service.clock = lambda: start + timedelta(hours=4)
    return bot, transport, event


def command(text, message_id, telegram_id=1):
    return Update.de_json(group_message(text, message_id, telegram_id), None)


async def run(bot, text, message_id, telegram_id=1):
    await bot.process_update(Update.de_json(group_message(text, message_id, telegram_id), bot.bot))


async def test_stats_are_private_and_free(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo)
    async with bot:
        await run(bot, "/stats", 70)
    assert any("sent you your turnout stats privately" in p["text"] for p in transport.sent)
    private = [p for p in transport.sent if int(p["chat_id"]) == 1][-1]["text"]
    assert "Sessions held: 1" in private and "Going 3 · came 2 · turnout 67%" in private
    assert "By event:" in private  # free for everyone unless EXPORT_REQUIRES_PRO is set
    # Someone else's events are not in your numbers.
    async with bot:
        await run(bot, "/stats", 71, telegram_id=2)
    assert "No finished events of yours" in transport.sent[-1]["text"]


async def test_export_is_pro_only_when_switched_on(service_factory):
    service, repo = service_factory(extraction(), export_requires_pro=True)
    bot, transport, event = await finished_event(service, repo)
    async with bot:
        await run(bot, "/export", 72)
        assert "part of Clubs Pro" in transport.sent[-1]["text"] and not transport.documents
        await repo.set_group_tier(CHAT, "PRO")
        await run(bot, "/export", 73)
    assert "sent you the attendance file privately" in transport.sent[-1]["text"]
    assert len(transport.documents) == 1
    document = transport.documents[0]
    assert int(document["chat_id"]) == 1  # to the organiser, not the group
    assert "turnout 67%" in document["caption"] and "1 held" in document["caption"]
    assert document["filename"].startswith("attendance-")
    content = document["content"].decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    by_name = {r["name"]: r for r in rows}
    assert by_name["Member 2"]["rsvp"] == "Going" and by_name["Member 2"]["attended"] == "Yes"
    assert by_name["Member 4"]["rsvp"] == "Going" and by_name["Member 4"]["attended"] == "No"
    # A name that would run as a formula in a spreadsheet is neutralised.
    assert '\'=HYPERLINK("x")' in by_name and by_name['\'=HYPERLINK("x")']["rsvp"] == "Can't"
    assert set(rows[0]) == {
        "event",
        "date",
        "time",
        "name",
        "username",
        "rsvp",
        "still_coming",
        "attended",
    }


async def test_export_is_free_by_default(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo)
    async with bot:
        await run(bot, "/export", 77)
    assert len(transport.documents) == 1
    assert "turnout 67%" in transport.documents[0]["caption"]


async def test_pro_chats_can_be_listed_in_settings(service_factory):
    service, repo = service_factory(
        extraction(), pro_chat_ids=f"123, {CHAT}", export_requires_pro=True
    )
    bot, transport, event = await finished_event(service, repo)
    async with bot:
        await run(bot, "/export 30", 74)
    assert len(transport.documents) == 1
    # Per-event breakdown is part of stats for Pro chats.
    async with bot:
        await run(bot, "/stats", 75)
    text = [p for p in transport.sent if int(p["chat_id"]) == 1][-1]["text"]
    assert "By event:" in text and "CCA dinner" in text


async def test_report_needs_a_private_chat_open(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo)
    transport.blocked.add(1)
    async with bot:
        await run(bot, "/stats", 76)
    assert "I can't message you yet" in transport.sent[-1]["text"]


async def test_deleting_my_data_removes_my_name_and_attendance(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo)
    user = await repo.ensure_user(2)
    assert repo.names and any(k[1] == user.id for k in repo.attended)
    await repo.delete_user(user.id)
    assert not any(k[1] == user.id for k in repo.names)
    assert not any(k[1] == user.id for k in repo.attended)


PLAN = extraction(
    title="Badminton",
    date_text="Saturday",
    start_time_text="4pm",
    end_time_text="6pm",
    location="UTSRC",
)


async def test_detection_is_off_until_an_admin_turns_it_on(service_factory):
    service, repo = service_factory(PLAN)
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC anyone?", 80, 2)
        assert service.extractor.calls == [] and not transport.sent  # nothing read, nothing said
        await run(bot, "/detect", 81)
        assert "Event detection is off" in transport.sent[-1]["text"]
        await run(bot, "/detect on", 82, 2)  # not an admin
        assert "Only a group admin" in transport.sent[-1]["text"]
        assert not repo.detection.get(CHAT)
        transport.admins.add(1)
        await run(bot, "/detect on", 83, 1)
    notice = transport.sent[-1]["text"]
    assert repo.detection[CHAT] is True
    assert "not stored" in notice and "/detect off" in notice and "admin" in notice


async def test_detected_plan_becomes_a_card_only_on_a_tap(service_factory):
    service, repo = service_factory(PLAN)
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        # Chatter that has no day and time is never sent to the extractor.
        await run(bot, "lol did anyone see the match last night", 90, 2)
        await run(bot, "see you at 7:30 then", 91, 2)
        assert service.extractor.calls == [] and not transport.sent
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC anyone keen?", 92, 2)
        suggestion = transport.sent[-1]
        assert "Looks like a plan" in suggestion["text"] and "Badminton" in suggestion["text"]
        taps = buttons(suggestion["reply_markup"])
        assert set(taps) == {"Make event card", "Ignore"}
        # Nothing is saved until someone taps, and a retried update adds no second suggestion.
        assert not repo.group_events and len(repo.suggestions) == 1
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC anyone keen?", 92, 2)
        assert len(repo.suggestions) == 1
        await bot.process_update(
            Update.de_json(group_tap(taps["Make event card"], 2, update_id=9100), bot.bot)
        )
    event = next(iter(repo.group_events.values()))
    assert event.title == "Badminton" and event.message_id == 101
    assert event.creator_user_id == (await repo.ensure_user(2)).id
    assert event.source_message_id == 92 and not repo.suggestions
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "📅 Badminton" in edits[-1]["text"] and "Going 0" in edits[-1]["text"]
    # The suggestion message is now the card, so RSVPs edit it.
    card_taps = buttons(edits[-1]["reply_markup"])
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(card_taps["Going"], 4, update_id=9101), bot.bot)
        )
    assert "Going 1" in [p for n, p in transport.calls if n == "editMessageText"][-1]["text"]


async def test_ignored_and_stale_suggestions_disappear(service_factory):
    service, repo = service_factory(PLAN, PLAN)
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 95, 2)
        taps = buttons(transport.sent[-1]["reply_markup"])
        await bot.process_update(
            Update.de_json(group_tap(taps["Ignore"], 2, update_id=9200), bot.bot)
        )
        assert [c for c in transport.calls if c[0] == "deleteMessage"]
        assert not repo.suggestions and not repo.group_events
        # A second plan nobody answers is removed by the clock.
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 96, 2)
        assert len(repo.suggestions) == 1
        deletions = len([c for c in transport.calls if c[0] == "deleteMessage"])
    # expiry is measured against the repository's creation time (real clock in the fake)
    stale = next(iter(repo.suggestions.values()))
    stale.created_at -= timedelta(hours=1)
    from datetime import datetime

    assert await service.detection.sweep(datetime.now(stale.created_at.tzinfo)) == 1
    assert not repo.suggestions
    assert len([c for c in transport.calls if c[0] == "deleteMessage"]) == deletions + 1
    # A tap on a button whose suggestion has gone is refused politely.
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Make event card"], 3, update_id=9201), bot.bot)
        )
    assert "expired" in alerts(transport)[-1]["text"]


async def test_detection_is_rate_limited_and_ignores_bad_extractions(service_factory):
    service, repo = service_factory(PLAN, extraction(kind="unknown"), detection_per_hour=2)
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC", 100, 2)  # suggestion
        await run(bot, "Quiz night Friday 8pm at the pub", 101, 2)  # extractor says unknown
        await run(bot, "Cinema Sunday 3pm at Vivo", 102, 2)  # over the hourly limit: not read
    assert len(service.extractor.calls) == 2
    assert len(transport.sent) == 1 and len(repo.suggestions) == 1
