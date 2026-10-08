from telegram import Update

from app.schemas.event import Status
from app.services.change_alerts import describe_change
from app.services.jobs import Notifier, Undeliverable
from tests.conftest import extraction
from tests.test_group_events import CHAT, alerts, group_tap, make_bot, post_card

MOVED = extraction(
    date_text="2026-10-09",
    start_time_text="20:00",
    end_time_text="22:00",
    end_date_text="2026-10-09",
)


class Outbox:
    """Captures DMs; can fail on demand like a flaky or blocked Telegram chat."""

    def __init__(self):
        self.sent, self.errors = [], []

    async def __call__(self, telegram_id, reply):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((telegram_id, reply.text))


async def card_with_members(service, repo, answers):
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        for n, (member, label) in enumerate(answers.items()):
            await bot.process_update(
                Update.de_json(group_tap(taps[label], member, update_id=7000 + n), bot.bot)
            )
    return bot, transport, outbox, taps, event


async def organiser_moves_to_8pm(service, event):
    await service.command(1, "start", f"edit_{event.id}")
    reference = service.clock()
    preview = (await service.message(1, 90, "20:00–22:00", reference))[0]
    assert "Before:" in preview.text and "After:" in preview.text
    return await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)


def test_change_wording():
    before = {
        "title": "Dinner",
        "start_at": "2026-10-09T19:00:00+08:00",
        "end_at": "2026-10-09T21:00:00+08:00",
        "location": "Vivo",
        "notes": None,
    }
    later = before | {"start_at": "2026-10-09T12:00:00+00:00", "end_at": "2026-10-09T14:00:00Z"}
    assert describe_change(before, later) == (
        "Dinner changed: Fri 9 Oct 7pm–9pm → 8pm–10pm; venue unchanged"
    )
    moved = before | {
        "start_at": "2026-10-10T19:30:00+08:00",
        "end_at": "2026-10-10T21:00:00+08:00",
    }
    assert "Fri 9 Oct 7pm–9pm → Sat 10 Oct 7:30pm–9pm" in describe_change(before, moved)
    venue = describe_change(before, before | {"location": "Clementi", "title": "Supper"})
    assert venue == "Supper changed: renamed from Dinner; time unchanged; venue Vivo → Clementi"
    assert describe_change(before, before, True) == (
        "Dinner (Fri 9 Oct 7pm–9pm) was cancelled by the organiser."
    )


async def test_edit_once_alerts_savers_only_and_once(service_factory, reference):
    service, repo = service_factory(extraction(), MOVED)
    answers = {2: "Going", 3: "Save", 4: "Can't", 6: "Maybe"}
    bot, transport, outbox, taps, event = await card_with_members(service, repo, answers)
    async with bot:
        # Only the organiser is sent to their private chat to edit.
        await bot.process_update(
            Update.de_json(group_tap(taps["Edit"], 2, update_id=7100), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(taps["Edit"], 1, update_id=7101), bot.bot)
        )
        refused, allowed = alerts(transport)[-2:]
        assert "Only the organiser" in refused["text"]
        assert allowed["url"] == f"https://t.me/AmIFreeTestBot?start=edit_{event.id}"
        assert (
            "Only the organiser" in (await service.command(2, "start", f"edit_{event.id}"))[0].text
        )
        done = await organiser_moves_to_8pm(service, event)
    assert "Event updated" in done[0].text
    # Going, Saved and Maybe are told; the decliner, the organiser and outsiders are not.
    told = sorted(telegram_id for telegram_id, _ in outbox.sent)
    assert told == [2, 3, 6]
    text = outbox.sent[0][1]
    assert text.startswith("CCA dinner changed: Fri 9 Oct 7pm–9pm → 8pm–10pm; venue unchanged")
    assert "✅ No clashes found" in text
    # Every member's own plan moved, and the shared card was re-rendered.
    assert all(e.start_at.hour == 20 for e in repo.events.values() if e.group_event_id)
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "20:00" in edits[-1]["text"] and int(edits[-1]["chat_id"]) == CHAT
    # Running the worker again sends nothing more.
    assert await service.notifier.deliver() == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(outbox.sent) == 3 and all(n["status"] == "sent" for n in repo.notifications)


async def test_alert_carries_a_fresh_clash_check(service_factory, reference):
    lecture = extraction(title="Night class", start_time_text="20:00", end_time_text="22:00")
    service, repo = service_factory(lecture, extraction(), MOVED)
    mine = (await service.message(2, 5, "Night class Friday 20:00 22:00", reference))[0]
    await service.callback(2, mine.keyboard.inline_keyboard[0][0].callback_data)
    _, _, outbox, _, event = await card_with_members(service, repo, {2: "Maybe"})
    await organiser_moves_to_8pm(service, event)
    assert outbox.sent[0][0] == 2
    assert "⚠️ Clashes with Night class" in outbox.sent[0][1]


async def test_transient_failure_retries_and_blocked_user_is_skipped(service_factory):
    service, repo = service_factory(extraction(), MOVED)
    _, _, outbox, _, event = await card_with_members(service, repo, {2: "Going", 3: "Going"})
    outbox.errors = [TimeoutError("telegram down"), Undeliverable()]
    await organiser_moves_to_8pm(service, event)
    # Nothing is marked sent before Telegram confirms.
    assert outbox.sent == []
    statuses = sorted(n["status"] for n in repo.notifications)
    assert statuses == ["failed", "pending"]
    blocked = await repo.get_user(
        next(n for n in repo.notifications if n["status"] == "failed")["user_id"]
    )
    assert blocked.dm_blocked
    assert await service.notifier.deliver() == {"sent": 1, "failed": 0, "skipped": 0}
    assert await service.notifier.deliver() == {"sent": 0, "failed": 0, "skipped": 0}
    assert [telegram_id for telegram_id, _ in outbox.sent] == [2]
    # Opening the bot again clears the block.
    await service.command(blocked.telegram_user_id, "start")
    assert not (await repo.get_user(blocked.id)).dm_blocked


async def test_organiser_cancels_for_everyone(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card_with_members(service, repo, {2: "Going"})
    async with bot:
        await bot.process_update(Update.de_json(group_tap(taps["Cancel event"], 1), bot.bot))
        assert alerts(transport)[-1]["url"].endswith(f"start=cancel_{event.id}")
        ask = (await service.command(1, "start", f"cancel_{event.id}"))[0]
        assert "Cancel this event for everyone?" in ask.text
        done = await service.callback(1, ask.keyboard.inline_keyboard[0][0].callback_data)
        await bot.process_update(
            Update.de_json(group_tap(taps["Going"], 3, update_id=7200), bot.bot)
        )
    assert "cancelled for everyone" in done[0].text
    assert outbox.sent == [(2, "CCA dinner (Fri 9 Oct 7pm–9pm) was cancelled by the organiser.")]
    linked = [e for e in repo.events.values() if e.group_event_id]
    assert [e.status for e in linked] == [Status.CANCELLED]
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert edits[-1]["text"].startswith("❌ Cancelled")
    assert alerts(transport)[-1]["text"] == "This event was cancelled."
    assert "already cancelled" in (await service.command(1, "start", f"edit_{event.id}"))[0].text


async def test_stale_organiser_edit_is_rejected(service_factory, reference):
    service, repo = service_factory(extraction(), MOVED, MOVED)
    _, _, outbox, _, event = await card_with_members(service, repo, {2: "Going"})
    await service.command(1, "start", f"edit_{event.id}")
    preview = (await service.message(1, 90, "20:00–22:00", reference))[0]
    # The card changes underneath the pending preview.
    current = await repo.get_group_event(event.id)
    await repo.update_group_event(current.creator_user_id, current, current, False)
    stale = await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert "That event changed" in stale[0].text
    assert (await repo.get_group_event(event.id)).start_at.hour == 19


async def test_personal_edits_do_not_alert_anyone(service_factory, reference):
    service, repo = service_factory(extraction(), MOVED)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, card.keyboard.inline_keyboard[1][1].callback_data)
    preview = (await service.message(1, 3, "20:00–22:00", reference))[0]
    await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert repo.notifications == []
