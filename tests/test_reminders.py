"""Quick reminders: typed in plain words, rung at the exact time, snoozed or finished, never busy."""

from datetime import datetime, timedelta

import pytest

from app.schemas.event import SG
from app.services.date_resolution import Clarification
from app.services.jobs import Notifier
from app.services.quick_reminders import parse_reminder
from tests.conftest import onboard, tap

# The reference clock is Sun 4 Oct 2026, noon.


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=SG)


@pytest.mark.parametrize(
    "text,body,when",
    [
        ("remind me at 5pm to buy milk", "Buy milk", at(4, 17)),
        ("remind me to buy milk at 5pm", "Buy milk", at(4, 17)),
        ("Remind me tomorrow 9am to submit the form", "Submit the form", at(5, 9)),
        ("remind me on fri at 3pm to pay hall fees", "Pay hall fees", at(9, 15)),
        ("remind me in 30 minutes to call mum", "Call mum", at(4, 12, 30)),
        ("remind me in an hour to stretch", "Stretch", at(4, 13)),
        ("remind me at 9am to eat", "Eat", at(5, 9)),  # 9am has passed today: tomorrow
        ("remind me tomorrow to buy 2 apples", "Buy 2 apples", at(5, 9)),
        ("remind me tonight to study", "Study", at(4, 20)),
        ("remind me at 17:30 about the CCA meeting", "The CCA meeting", at(4, 17, 30)),
    ],
)
def test_reading_a_reminder(reference, text, body, when):
    draft = parse_reminder(text, reference)
    assert (draft.text, draft.remind_at) == (body, when)


@pytest.mark.parametrize(
    "text,asks",
    [
        ("remind me to buy milk", "When should I remind you"),
        ("remind me at 5 to buy milk", "AM or PM"),
        ("remind me at 11am today to eat", "already passed"),
        ("remind me at 5pm", "What should I remind you about"),
    ],
)
def test_unclear_reminders_ask(reference, text, asks):
    with pytest.raises(Clarification, match=asks):
        parse_reminder(text, reference)


async def test_typed_reminder_is_saved_and_listed_but_not_busy(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    card = (await service.message(1, 2, "remind me at 5pm to buy milk", reference))[0]
    assert card.text == "⏰ Got it. I'll remind you today at 5pm: Buy milk"
    assert len(repo.reminders) == 1 and not repo.events and not repo.deadlines

    plans = (await service.command(1, "plans"))[0]
    assert any(
        "Reminder: Buy milk" in b.text for row in plans.keyboard.inline_keyboard for b in row
    )
    opened = (await service.callback(1, tap(plans, "4 Oct · Reminder: Buy milk")))[0]
    assert "Buy milk" in opened.text and "Rings today at 5pm" in opened.text

    # It does not make 5pm busy.
    free = (await service.command(1, "free", "today 5-6pm"))[0]
    assert "clash" not in free.text.lower() or "No clash" in free.text


async def test_remind_command_and_unclear_input(service_factory, reference):
    service, repo = service_factory()
    assert "What should I remind you about" in (await service.command(1, "remind"))[0].text
    assert "Got it" in (await service.command(1, "remind", "me at 6pm to call mum"))[0].text
    assert "Got it" in (await service.command(1, "remind", "at 7pm to eat"))[0].text
    assert "When should I remind you" in (await service.command(1, "remind", "buy milk"))[0].text
    assert len(repo.reminders) == 2


async def test_it_rings_once_at_the_time_even_with_reminders_off(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    await service.command(1, "reminders", "off")
    await service.message(1, 2, "remind me in 10 minutes to call mum", reference)
    sent = []

    async def send(telegram_id, reply):
        sent.append(reply)

    now = [reference]
    service.clock = lambda: now[0]
    await Notifier(service, send).run()
    assert sent == []  # not yet

    now[0] = reference + timedelta(minutes=10)
    await Notifier(service, send).run()
    await Notifier(service, send).run()  # a second tick does not repeat it
    assert [r.text for r in sent] == ["⏰ Call mum"]
    assert [b.text for row in sent[0].keyboard.inline_keyboard for b in row] == [
        "✅ Done",
        "🗑 Remove",
        "Snooze 10 min",
        "1 hr",
        "Tomorrow",
    ]


async def test_snooze_rings_again_and_done_stops_it(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    card = (await service.message(1, 2, "remind me in 5 minutes to call mum", reference))[0]
    sent, now = [], [reference + timedelta(minutes=5)]
    service.clock = lambda: now[0]

    async def send(telegram_id, reply):
        sent.append(reply)

    await Notifier(service, send).run()
    assert len(sent) == 1
    snoozed = (await service.callback(1, tap(sent[0], "Snooze 10 min")))[0]
    assert snoozed.edit and "Snoozed until today at 12:15pm" in snoozed.text
    await Notifier(service, send).run()
    assert len(sent) == 1  # not ringing again before the new time

    now[0] += timedelta(minutes=10)
    await Notifier(service, send).run()
    assert len(sent) == 2
    done = (await service.callback(1, tap(sent[1], "✅ Done")))[0]
    assert done.text == "✅ Done: Call mum"
    now[0] += timedelta(hours=1)
    await Notifier(service, send).run()
    assert len(sent) == 2
    assert tap(card, "🗑 Remove")  # the confirmation card carries the same buttons


async def test_remove_and_tomorrow_snooze(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    card = (await service.message(1, 2, "remind me at 5pm to buy milk", reference))[0]
    later = (await service.callback(1, tap(card, "Tomorrow")))[0]
    assert "Snoozed until tomorrow at 9am" in later.text
    gone = (await service.callback(1, tap(later, "🗑 Remove")))[0]
    assert gone.text == "🗑 Removed: Buy milk" and not repo.reminders
    again = (await service.callback(1, tap(card, "✅ Done")))[0]
    assert "already gone" in again.text
