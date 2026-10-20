"""Personal deadlines: saved from a forward or /deadline, listed, never busy, reminded twice."""

from datetime import datetime, timedelta

import pytest

from app.schemas.event import SG, DeadlineDraft
from app.services.date_resolution import Clarification
from app.services.deadlines import validate_deadline
from app.services.jobs import Notifier
from tests.conftest import extraction, tap

REPORT = "CS3244 report due Fri 11:59pm"  # reference is Sun 4 Oct, noon


def due(**changes):
    fields = dict(
        kind="deadline",
        title="CS3244 report",
        date_text="Fri",
        start_time_text="11:59pm",
        end_time_text=None,
        location=None,
    )
    return extraction(**(fields | changes))


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=SG)


# ---------- reading the due time ----------


@pytest.mark.parametrize(
    "text,time_text,expected",
    [
        ("report due Fri 11:59pm", "11:59pm", at(9, 23, 59)),
        ("report due Fri 2359", "2359", at(9, 23, 59)),
        ("report due Fri", None, at(9, 23, 59)),  # no time: by the end of the day
        ("report due Fri midnight", "midnight", at(9, 23, 59)),
        ("report due Fri 5pm", "5pm", at(9, 17)),
    ],
)
def test_due_time(reference, text, time_text, expected):
    draft = validate_deadline(due(start_time_text=time_text), text, reference)
    assert draft.due_at == expected and draft.title == "CS3244 report"


def test_missing_or_past_dates_are_asked(reference):
    with pytest.raises(Clarification, match="What's the due date"):
        validate_deadline(due(date_text=None), "report due soon", reference)
    with pytest.raises(Clarification, match="already passed"):
        validate_deadline(due(date_text="2026-10-01"), "report due 2026-10-01 11:59pm", reference)
    with pytest.raises(Clarification):  # a date the message never said
        validate_deadline(due(date_text="Sat"), REPORT, reference)


# ---------- saving ----------


async def test_forwarded_deadline_is_saved(service_factory, reference):
    service, repo = service_factory(due(), due())
    card = (await service.message(1, 1, REPORT, reference))[0]
    assert card.text.startswith("📌 CS3244 report\nDue Fri 9 Oct, 11:59pm (in 5 days)")
    assert "remind you the day before" in card.text
    assert tap(card, "✅ Done") and tap(card, "🗑 Remove")
    assert len(repo.deadlines) == 1 and not repo.events
    await service.message(1, 1, REPORT, reference)  # the same message again
    assert len(repo.deadlines) == 1


async def test_missing_date_is_asked_then_answered(service_factory, reference):
    service, repo = service_factory(due(date_text=None, start_time_text=None))
    ask = (await service.message(1, 1, "CS3244 report due soon", reference))[0]
    assert ask.text.startswith("What's the due date?")
    card = (await service.message(1, 2, "20 oct", reference))[0]
    assert "Due Tue 20 Oct, 11:59pm" in card.text
    assert len(service.extractor.calls) == 1 and len(repo.deadlines) == 1


async def test_deadline_command_overrides_the_ai(service_factory, reference):
    service, repo = service_factory(
        due(kind="new"), due(kind="new", title="Pay hall fees", start_time_text=None)
    )
    card = (await service.command(1, "deadline", REPORT))[0]
    assert card.text.startswith("📌 CS3244 report")
    # With no text, /deadline shows the list (and leaves nothing pending).
    listed = (await service.command(1, "deadline"))[0]
    assert "1. CS3244 report · Due Fri 9 Oct, 11:59pm (in 5 days)" in listed.text
    assert (await repo.ensure_user(1)).pending is None
    card = (await service.command(1, "deadline", "Pay hall fees by Fri"))[0]
    assert card.text.startswith("📌 Pay hall fees")
    assert len(repo.deadlines) == 2 and not repo.events


async def test_an_event_draft_stays_an_event(service_factory, reference):
    service, repo = service_factory(extraction(date_text=None), extraction(kind="deadline"))
    await service.message(1, 1, "CCA dinner 7-9pm at Vivo", reference)
    card = (await service.message(1, 2, "it's on Friday", reference))[0]
    assert "CCA dinner" in card.text and not repo.deadlines


# ---------- listing, done, remove ----------


async def test_list_done_and_remove(service_factory, reference):
    service, repo = service_factory(
        due(), due(title="Pay hall fees", date_text="Wed", start_time_text=None)
    )
    await service.message(1, 1, REPORT, reference)
    await service.message(1, 2, "Pay hall fees by Wed", reference)
    listed = await service.command(1, "deadline")
    assert len(listed) == 1
    assert "1. Pay hall fees" in listed[0].text and "2. CS3244 report" in listed[0].text
    assert listed == await service.command(1, "deadlines")  # hidden alias
    fees = (await service.callback(1, tap(listed[0], "1")))[0]
    report = (await service.callback(1, tap(listed[0], "2")))[0]
    done = (await service.callback(1, tap(fees, "✅ Done")))[0]
    assert done.edit and done.text.startswith("✅ Done: Pay hall fees")
    gone = (await service.callback(1, tap(report, "🗑 Remove")))[0]
    assert gone.text == "🗑 Removed: CS3244 report"
    assert "No upcoming deadlines" in (await service.command(1, "deadlines"))[0].text


async def test_a_deadline_never_makes_you_busy(service_factory, reference):
    service, _ = service_factory(due(), extraction(start_time_text="11pm", end_time_text="11:59pm"))
    await service.message(1, 1, REPORT, reference)
    free = (await service.command(1, "free", "fri"))[0]
    assert free.text == "🟢 Fri 9 Oct: nothing booked, free all day.\n📌 Due 11:59pm: CS3244 report"
    card = (await service.message(1, 2, "Supper Friday 11pm-11:59pm", reference))[0]
    assert "No clashes found" in card.text


# ---------- reminders ----------


async def reminders_at(service, repo, now):
    service.clock = lambda: now
    sent = []

    async def send(telegram_id, reply):
        sent.append(reply)

    await Notifier(service, send).run()
    return sent


async def test_reminded_the_day_before_and_that_morning(service_factory, reference):
    service, repo = service_factory(due())
    await service.message(1, 1, REPORT, reference)
    for d in repo.deadlines.values():
        d.created_at = reference
    assert await reminders_at(service, repo, at(8, 8, 59)) == []
    day = await reminders_at(service, repo, at(8, 9, 5))
    assert [r.text.split("\n")[0] for r in day] == [
        "📌 Due tomorrow (Fri 9 Oct, 11:59pm): CS3244 report"
    ]
    assert await reminders_at(service, repo, at(8, 18)) == []  # once only
    morning = await reminders_at(service, repo, at(9, 9))
    assert morning[0].text.startswith("📌 Due today at 11:59pm: CS3244 report")
    assert tap(morning[0], "✅ Done")


async def test_no_reminder_once_done_or_with_reminders_off(service_factory, reference):
    service, repo = service_factory(due(), due(title="Pay hall fees", start_time_text=None))
    card = (await service.message(1, 1, REPORT, reference))[0]
    for d in repo.deadlines.values():
        d.created_at = reference
    await service.callback(1, tap(card, "✅ Done"))
    assert await reminders_at(service, repo, at(8, 10)) == []
    await service.message(1, 2, "Pay hall fees by Fri", reference)
    for d in repo.deadlines.values():
        d.created_at = reference
    await service.command(1, "reminders", "off")
    assert await reminders_at(service, repo, at(8, 10)) == []


async def test_no_reminder_for_a_deadline_added_after_its_moment(service_factory, reference):
    service, repo = service_factory(due(date_text="Mon", start_time_text="5pm"))
    service.clock = lambda: at(5, 10)
    await service.message(1, 1, "CS3244 report due Mon 5pm", at(5, 10))  # Mon morning
    for d in repo.deadlines.values():
        d.created_at = at(5, 10)
    # The morning reminder (9am) was before it was added; nothing is sent today.
    assert await reminders_at(service, repo, at(5, 11)) == []


async def test_nightly_summary_mentions_tomorrows_deadlines(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    card = (await service.message(1, 1, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    await service.message(1, 2, REPORT, reference)
    hour = service.settings.nightly_hour
    sent = await reminders_at(service, repo, at(8, hour) + timedelta(minutes=5))
    nightly = [r for r in sent if r.text.startswith("Tomorrow")]
    assert "📌 Due: CS3244 report (11:59pm)" in nightly[0].text


async def test_list_shows_overdue_first_and_days_left(service_factory, reference):
    service, repo = service_factory(due(), due(title="Pay hall fees", date_text="Wed"))
    await service.command(1, "deadline", REPORT)
    await service.command(1, "deadline", "Pay hall fees due Wed 11:59pm")
    late = next(d for d in repo.deadlines.values() if d.title == "Pay hall fees")
    late.due_at = at(2, 23, 59)  # two days before the reference (Sun 4 Oct)

    listed = await service.command(1, "deadline")
    assert len(listed) == 1
    assert listed[0].text == (
        "Deadlines — tap a number to open it.\n\n⚠️ Overdue\n"
        "1. Pay hall fees · Due Fri 2 Oct, 11:59pm (2 days overdue)\n\nComing up\n"
        "2. CS3244 report · Due Fri 9 Oct, 11:59pm (in 5 days)"
    )
    # Overdue items still have Done / Remove, and Done clears them.
    card = (await service.callback(1, tap(listed[0], "1")))[0]
    assert tap(card, "🗑 Remove")
    await service.callback(1, tap(card, "✅ Done"))
    again = await service.command(1, "deadline")
    assert len(again) == 1 and "1. CS3244 report" in again[0].text
    assert "Overdue" not in again[0].text and "Pay hall fees" not in again[0].text


async def test_bare_deadline_with_none_saved_explains_how_to_add(service_factory, reference):
    service, _ = service_factory()
    reply = (await service.command(1, "deadline"))[0]
    assert (
        reply.text.startswith("No upcoming deadlines") and "/deadline CS3244 report" in reply.text
    )


async def test_deadline_pages_stay_in_deadlines_and_edit_in_both_directions(
    service_factory, reference
):
    service, repo = service_factory()
    user = await repo.ensure_user(1)
    for n in range(23):
        await repo.create_deadline(
            user.id,
            None,
            DeadlineDraft(title=f"Report {n}", due_at=reference + timedelta(days=n - 10)),
        )
    (first,) = await service.command(1, "deadline")
    assert "Page 1 of 3" in first.text and "⚠️ Overdue" in first.text
    assert "Coming up" not in first.text and not first.edit
    assert [b.text for row in first.keyboard.inline_keyboard for b in row] == [
        *map(str, range(1, 11)),
        "Next",
    ]
    (second,) = await service.callback(1, tap(first, "Next"))
    assert second.edit and "Page 2 of 3" in second.text and "Coming up" in second.text
    assert "Overdue" not in second.text and "1. Report 10" in second.text
    (third,) = await service.callback(1, tap(second, "Next"))
    assert third.edit and "Page 3 of 3" in third.text
    (card,) = await service.callback(1, tap(third, "3"))
    assert card.text.startswith("📌 Report 22")
    assert tap(card, "✅ Done") and tap(card, "🗑 Remove")
    (back,) = await service.callback(1, tap(second, "Previous"))
    assert back.edit and back.text == first.text
    assert (await service.callback(1, "deadline:99"))[0].text == third.text


@pytest.mark.parametrize("page", ["-1", "bad", "1:2", "９", "123456789"])
async def test_invalid_deadline_pages(service_factory, page):
    service, _ = service_factory()
    (reply,) = await service.callback(1, f"deadline:{page}")
    assert reply.text == "That page isn't valid. Use /deadline."


async def test_deadline_buttons_handle_completed_removed_and_other_users_items(
    service_factory, reference
):
    service, _ = service_factory(due())
    (card,) = await service.command(1, "deadline", REPORT)
    (listed,) = await service.command(1, "deadline")
    open_button = tap(listed, "1")
    # A copied callback cannot reveal another user's deadline.
    assert "/deadline" in (await service.callback(2, open_button))[0].text
    assert (await service.command(2, "deadline"))[0].text.startswith("No upcoming deadlines")
    await service.callback(1, tap(card, "✅ Done"))
    assert "already done" in (await service.callback(1, open_button))[0].text
    await service.callback(1, tap(card, "🗑 Remove"))
    assert "/deadline" in (await service.callback(1, open_button))[0].text
    assert "/deadline" in (await service.callback(1, tap(card, "🗑 Remove")))[0].text
    assert "/deadline" in (await service.callback(1, "dl:done:invalid"))[0].text
    assert "/deadline" in (await service.callback(1, "dc:invalid"))[0].text


async def test_deadline_cards_and_reminders_point_to_current_commands(service_factory, reference):
    service, repo = service_factory(due())
    (enabled,) = await service.command(1, "deadline", REPORT)
    (panel,) = await service.command(1, "settings")
    await service.callback(1, tap(panel, "🔔 Turn off reminders"))
    (listed,) = await service.command(1, "deadline")
    (disabled,) = await service.callback(1, tap(listed, "1"))
    assert "Reminders are off" in disabled.text
    (panel,) = await service.command(1, "settings")
    await service.callback(1, tap(panel, "🔔 Turn on reminders"))
    for deadline in repo.deadlines.values():
        deadline.created_at = reference
    (reminder,) = await reminders_at(service, repo, at(8, 9))
    for reply in (enabled, disabled, reminder):
        assert "/settings" in reply.text and "/deadline lists them all" in reply.text
        assert "/reminders" not in reply.text and "/deadlines" not in reply.text
