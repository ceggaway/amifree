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


async def test_detection_cannot_be_enabled_by_members_or_admins(service_factory):
    service, repo = service_factory(PLAN)
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "Badminton Saturday 4pm-6pm at UTSRC anyone?", 80, 2)
        assert not service.extractor.calls and not transport.sent
        for index, (command, member) in enumerate(
            (("/detect", 2), ("/detect on", 2), ("/detect on", 1), ("/detect off", 1))
        ):
            transport.admins.add(1)
            await run(bot, command, 81 + index, member)
            assert "no longer supported" in transport.sent[-1]["text"]
    assert not repo.detection.get(CHAT) and not repo.suggestions


async def test_previously_enabled_groups_never_process_ordinary_messages(service_factory):
    service, repo = service_factory(PLAN)
    repo.detection[CHAT] = True
    bot, transport = make_bot(service)
    async with bot:
        for index, text in enumerate(
            (
                "Badminton Saturday 4pm-6pm at UTSRC anyone?",
                "Quiz night Friday 8pm at the pub",
                "Cinema Sunday 3pm at Vivo",
            )
        ):
            await run(bot, text, 90 + index, 2)
    assert not service.extractor.calls and not transport.sent
    assert not repo.suggestions and not repo.group_events
    assert await service.detection.enabled(CHAT) is False


async def test_retired_suggestion_buttons_cannot_create_cards(service_factory, reference):
    from app.schemas.event import EventDraft

    service, repo = service_factory()
    draft = EventDraft(
        title="Old suggestion",
        start_at=reference + timedelta(days=1),
        end_at=reference + timedelta(days=1, hours=1),
    )
    suggestion = await repo.create_suggestion(CHAT, 95, draft, 2)
    bot, transport = make_bot(service)
    async with bot:
        for index, action in enumerate(("ok", "no")):
            await bot.process_update(
                Update.de_json(
                    group_tap(f"d:{action}:{suggestion.id}", 2, update_id=9200 + index), bot.bot
                )
            )
            assert "no longer supported" in alerts(transport)[-1]["text"]
    assert not repo.group_events and not service.extractor.calls
    assert [c for c in transport.calls if c[0] == "deleteMessage"]


async def test_old_expired_suggestions_are_still_cleaned_up(service_factory, reference):
    from datetime import datetime

    from app.schemas.event import EventDraft

    service, repo = service_factory()
    draft = EventDraft(
        title="Old suggestion",
        start_at=reference + timedelta(days=1),
        end_at=reference + timedelta(days=1, hours=1),
    )
    suggestion = await repo.create_suggestion(CHAT, 96, draft, 2)
    await repo.set_suggestion_message(suggestion.id, 101)
    stale = repo.suggestions[suggestion.id]
    stale.created_at -= timedelta(hours=1)
    bot, transport = make_bot(service)
    async with bot:
        assert await service.detection.sweep(datetime.now(stale.created_at.tzinfo)) == 1
    assert not repo.suggestions and not repo.group_events
    assert [c for c in transport.calls if c[0] == "deleteMessage"]
