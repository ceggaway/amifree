"""Deadlines can be edited: name, due date and time, and notes, picked then typed."""

from datetime import datetime

import pytest

from app.schemas.event import SG
from app.services.date_resolution import Clarification
from app.services.deadlines import changed_due
from tests.conftest import tap
from tests.test_deadlines import REPORT, at, due, reminders_at


async def edit(service, repo, reference, field):
    card = (await service.message(1, 1, REPORT, reference))[0]
    (menu,) = await service.callback(1, tap(card, "✏️ Edit"))
    assert menu.text.startswith("What do you want to change?\n\n📌 CS3244 report")
    (question,) = await service.callback(1, tap(menu, field))
    return question


async def test_rename_a_deadline(service_factory, reference):
    service, repo = service_factory(due())
    question = await edit(service, repo, reference, "📌 Name")
    assert question.text.startswith("Send the new name.")
    (card,) = await service.message(1, 2, "CS2103T tP report", reference)
    assert card.text.startswith("✏️ Renamed (was CS3244 report).\n\n📌 CS2103T tP report")
    assert tap(card, "✏️ Edit")
    (deadline,) = repo.deadlines.values()
    assert deadline.title == "CS2103T tP report" and not repo.users[1].pending


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("sat 5pm", at(10, 17)),  # day and time
        ("tmr", at(5, 23, 59)),  # a day keeps the time
        ("6pm", at(9, 18)),  # a time keeps the day
        ("due 20 oct 2359", at(20, 23, 59)),
        ("Thursday by noon", at(8, 12)),
    ],
)
async def test_change_the_due_time(service_factory, reference, typed, expected):
    service, repo = service_factory(due())
    await edit(service, repo, reference, "🕒 Due date & time")
    (card,) = await service.message(1, 2, typed, reference)
    assert card.text.startswith("✏️ Due time changed (was 📅 09/10/2026 (Friday), 11:59pm")
    (deadline,) = repo.deadlines.values()
    assert deadline.due_at == expected


async def test_an_unclear_due_time_asks_again_and_keeps_the_edit_open(service_factory, reference):
    service, repo = service_factory(due())
    await edit(service, repo, reference, "🕒 Due date & time")
    (reply,) = await service.message(1, 2, "sat 5", reference)
    assert "AM or PM" in reply.text and "/cancel" in reply.text
    (reply,) = await service.message(1, 3, "today 9am", reference)
    assert "already passed" in reply.text
    (card,) = await service.message(1, 4, "sat 5pm", reference)
    assert next(iter(repo.deadlines.values())).due_at == at(10, 17)


async def test_notes_can_be_added_and_removed(service_factory, reference):
    service, repo = service_factory(due())
    await edit(service, repo, reference, "📝 Notes")
    (card,) = await service.message(1, 2, "Submit on Canvas, PDF only", reference)
    assert card.text.startswith("✏️ Notes updated.") and "Submit on Canvas, PDF only" in card.text
    (menu,) = await service.callback(1, tap(card, "✏️ Edit"))
    await service.callback(1, tap(menu, "📝 Notes"))
    (card,) = await service.message(1, 3, "none", reference)
    assert card.text.startswith("✏️ Notes removed.") and "Canvas" not in card.text


async def test_keep_unchanged_or_a_new_message_leaves_it_as_it_was(service_factory, reference):
    service, repo = service_factory(due(), due(title="Pay hall fees", date_text="Wed"))
    card = (await service.message(1, 1, REPORT, reference))[0]
    (menu,) = await service.callback(1, tap(card, "✏️ Edit"))
    assert (await service.callback(1, tap(menu, "Keep unchanged")))[0].text == "Kept unchanged."
    # With the menu open, a message is read as a new one, not as an edit.
    await service.callback(1, tap(card, "✏️ Edit"))
    (new,) = await service.message(1, 2, "Pay hall fees due Wed 11:59pm", reference)
    assert new.text.startswith("📌 Pay hall fees")
    assert {d.title for d in repo.deadlines.values()} == {"CS3244 report", "Pay hall fees"}
    # An old menu's buttons expire.
    assert "expired" in (await service.callback(1, tap(menu, "📌 Name")))[0].text


async def test_reminders_follow_the_new_due_time(service_factory, reference):
    service, repo = service_factory(due())
    await edit(service, repo, reference, "🕒 Due date & time")
    (card,) = await service.message(1, 2, "tmr 5pm", reference)
    assert "I'll remind you tomorrow at 9am, and again at 2pm, 3 hours before." in card.text
    for d in repo.deadlines.values():
        d.created_at = reference
    (today,) = await reminders_at(service, repo, at(5, 9, 5))
    assert "• CS3244 report · 5pm" in today.text
    (alert,) = await reminders_at(service, repo, at(5, 14, 5))
    assert alert.text.startswith("⏰ Due at 5pm")
    assert await reminders_at(service, repo, at(9, 9, 5)) == []  # nothing on the old day


@pytest.mark.parametrize("typed", ["", "please", "next week"])
def test_changed_due_needs_a_day_or_a_time(typed):
    now = datetime(2026, 10, 4, 12, tzinfo=SG)
    with pytest.raises(Clarification):
        changed_due(typed, datetime(2026, 10, 9, 23, 59, tzinfo=SG), now)


def test_midnight_means_the_end_of_that_day():
    now = datetime(2026, 10, 4, 12, tzinfo=SG)
    assert changed_due("fri midnight", datetime(2026, 10, 9, 17, tzinfo=SG), now) == at(9, 23, 59)
