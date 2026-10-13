from datetime import datetime

import httpx
from telegram import Update

from app.config import Settings
from app.main import create_app
from app.schemas.event import SG, Status
from app.services.change_alerts import describe_change
from app.services.jobs import Notifier, Undeliverable
from tests.conftest import extraction, tap
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
        self.sent, self.errors, self.replies = [], [], []

    async def __call__(self, telegram_id, reply):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((telegram_id, reply.text))
        self.replies.append((telegram_id, reply))


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
    assert outbox.sent == [
        (
            2,
            "CCA dinner (Fri 9 Oct 7pm–9pm) was cancelled by the organiser."
            "\n\nIf you added it to your calendar, delete it there too.",
        )
    ]
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


def at(service, hour, day=4):
    service.clock = lambda: datetime(2026, 10, day, hour, tzinfo=SG)


async def two_plans_tomorrow(service_factory, reference, **settings):
    lunch = extraction(
        title="Lunch", date_text="Monday", start_time_text="12:00", end_time_text="13:00"
    )
    dinner = extraction(
        title="Dinner", date_text="Monday", start_time_text="19:00", end_time_text="21:00"
    )
    maybe = extraction(
        title="Movie",
        date_text="Monday",
        start_time_text="22:00",
        end_time_text="23:00",
        location=None,
    )
    service, repo = service_factory(dinner, lunch, maybe, **settings)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    for n, (text, row, col) in enumerate(
        (
            ("Dinner Monday 19:00 21:00", 0, 0),
            ("Lunch Monday 12:00 13:00", 0, 0),
            ("Movie Monday 22:00 23:00", 2, 0),
        )
    ):
        card = (await service.message(1, n + 2, text, reference))[0]
        await service.callback(1, card.keyboard.inline_keyboard[row][col].callback_data)
    return service, repo, outbox


async def test_two_plans_tomorrow_make_one_nightly_message(service_factory, reference):
    service, repo, outbox = await two_plans_tomorrow(service_factory, reference)
    # Someone with nothing tomorrow gets nothing.
    await repo.ensure_user(2)
    at(service, 19)
    assert await service.notifier.run() == {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    at(service, 20)
    assert await service.notifier.run() == {"queued": 1, "sent": 1, "failed": 0, "skipped": 0}
    ((telegram_id, text),) = outbox.sent
    assert telegram_id == 1
    assert text.startswith(
        "Tomorrow, Mon 5 Oct:\n• 12:00–13:00 Lunch @ Vivo\n• 19:00–21:00 Dinner @ Vivo"
    )
    assert "Still undecided: Movie" in text
    # Repeating the job the same night, or just after midnight, sends nothing more.
    at(service, 23)
    assert (await service.notifier.run())["sent"] == 0
    at(service, 0, day=5)
    assert await service.notifier.run() == {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    assert len(outbox.sent) == 1


async def test_nightly_retries_after_a_transient_failure(service_factory, reference):
    service, repo, outbox = await two_plans_tomorrow(service_factory, reference)
    at(service, 21)
    outbox.errors = [ConnectionError("telegram down")]
    assert (await service.notifier.run())["failed"] == 1 and outbox.sent == []
    assert (await service.notifier.run())["sent"] == 1
    assert (await service.notifier.run())["sent"] == 0 and len(outbox.sent) == 1


async def test_reminder_opt_out_and_cancelled_plans(service_factory, reference):
    service, repo, outbox = await two_plans_tomorrow(service_factory, reference, nightly_hour=18)
    assert "Reminders are on" in (await service.command(1, "reminders"))[0].text
    assert "are off" in (await service.command(1, "reminders", "off"))[0].text
    at(service, 18)
    assert (await service.notifier.run())["skipped"] == 1 and outbox.sent == []
    # A second user's only plan is declined after the summary was queued.
    service.extractor.items.append(
        extraction(title="Run", date_text="Monday", start_time_text="07:00", end_time_text="08:00")
    )
    card = (await service.message(2, 9, "Run Monday 07:00 08:00", reference))[0]
    going = await service.callback(2, card.keyboard.inline_keyboard[0][0].callback_data)
    from app.services.reminders import enqueue_nightly

    assert await enqueue_nightly(repo, service.clock(), 18) == 1
    await service.callback(2, tap(going[0], "Can't make it"))
    assert (await service.notifier.run())["skipped"] == 1 and outbox.sent == []


async def test_jobs_endpoint_requires_its_secret(service_factory, reference):
    service, repo, outbox = await two_plans_tomorrow(service_factory, reference)
    at(service, 20)
    disabled = create_app(Settings(_env_file=None, bot_mode="disabled"), service=service)
    app = create_app(
        Settings(_env_file=None, bot_mode="disabled", jobs_secret="job-secret-123"), service=service
    )
    for target, headers, expected in (
        (disabled, {"X-Jobs-Secret": "job-secret-123"}, 404),
        (app, {}, 403),
        (app, {"X-Jobs-Secret": "wrong"}, 403),
        (app, {"X-Jobs-Secret": "job-secret-123"}, 200),
    ):
        async with (
            target.router.lifespan_context(target),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(target), base_url="http://test"
            ) as client,
        ):
            response = await client.post("/internal/jobs/run", headers=headers)
            assert response.status_code == expected
    assert response.json() == {"queued": 1, "sent": 1, "failed": 0, "skipped": 0}
    assert len(outbox.sent) == 1


async def test_change_alert_offers_to_update_the_calendar(service_factory):
    service, repo = service_factory(extraction(), MOVED)
    _, _, outbox, _, event = await card_with_members(service, repo, {2: "Going", 3: "Maybe"})
    await organiser_moves_to_8pm(service, event)
    by_user = dict(outbox.replies)
    going = by_user[2]
    google, apple = going.keyboard.inline_keyboard[-1]
    assert google.url and "20261009T120000Z" in google.url  # the new 8pm start, in UTC
    assert apple.callback_data.startswith("ics:")
    assert "replaces the old entry" in going.text
    # Maybe isn't a commitment, so there's nothing to update in a calendar.
    assert all(
        b.text != "📅 Google Calendar" for row in by_user[3].keyboard.inline_keyboard for b in row
    )


async def test_backlog_is_measured_and_a_stuck_outbox_is_logged(service_factory, caplog):
    service, repo = service_factory()
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    user = await repo.ensure_user(424242)
    await repo.enqueue_notification(user.id, "NUDGE", "a", {})
    stats = await service.notifier.stats()
    assert stats["pending"] == 1 and stats["failed"] == 0
    assert stats["oldest_undelivered_seconds"] is not None
    # Nothing is wrong yet: no warning for a fresh message.
    with caplog.at_level("WARNING", logger="app.services.jobs"):
        await service.notifier.report_backlog()
    assert "notification_backlog" not in caplog.text
    # A message stuck for a long time is reported, with counts and no user data.
    repo.notifications[0]["created_at"] = repo.notifications[0]["created_at"].replace(year=2020)
    with caplog.at_level("WARNING", logger="app.services.jobs"):
        await service.notifier.report_backlog()
    assert "notification_backlog pending=1 sending=0 failed=0" in caplog.text
    assert str(user.id) not in caplog.text and str(user.telegram_user_id) not in caplog.text
    # A permanent failure is counted too.
    repo.notifications[0]["status"] = "failed"
    assert (await service.notifier.stats())["failed"] == 1


async def test_job_stats_endpoint_needs_the_secret_and_returns_counts_only(service_factory):
    service, repo = service_factory()
    service.notifier = Notifier(service, Outbox())
    user = await repo.ensure_user(1)
    await repo.enqueue_notification(user.id, "NUDGE", "a", {"text": "private"})
    app = create_app(
        Settings(_env_file=None, bot_mode="disabled", jobs_secret="job-secret-123"), service=service
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        assert (await client.get("/internal/jobs/stats")).status_code == 403
        ok = await client.get("/internal/jobs/stats", headers={"X-Jobs-Secret": "job-secret-123"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["pending"] == 1 and set(body) == {
        "pending",
        "sending",
        "failed",
        "oldest_undelivered_seconds",
    }
