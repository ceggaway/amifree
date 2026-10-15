"""Recurring group events: "Training every Tue/Thu until week 13"."""

from datetime import date, timedelta

from telegram import Update

from app.schemas.event import Status
from app.services.jobs import Notifier
from app.services.recurrence import parse_recurrence, session_dates, week_end
from tests.conftest import extraction
from tests.test_group_events import alerts, buttons, group_message, group_tap, make_bot
from tests.test_notifications import Outbox

TRAINING = "Training every Tue/Thu 7-9pm at UTSRC until week 13"
SERIES = extraction(
    title="Training",
    date_text="Tue",
    start_time_text="7",
    end_time_text="9pm",
    location="UTSRC",
)


def moved(day: str, start="20:00", end="22:00"):
    return extraction(
        date_text=day, start_time_text=start, end_time_text=end, end_date_text=day, title="Training"
    )


async def post_series(service, repo, text=TRAINING, message_id=50):
    bot, transport = make_bot(service)
    service.posted_cards = []

    async def post(chat_id, card):
        service.posted_cards.append((chat_id, card))
        return 700 + len(service.posted_cards)

    service.post_group_card = post
    service.notifier = Notifier(service, Outbox())
    async with bot:
        await bot.process_update(
            Update.de_json(group_message(f"/event {text}", message_id, 1), bot.bot)
        )
    return bot, transport


def sessions(repo):
    found = [g for g in repo.group_events.values() if g.series_id]
    return sorted(found, key=lambda g: g.occurrence_date)


def test_weekday_lists_and_end_clauses(reference):
    cases = {
        "Training every Tue/Thu 7pm until week 13": ([1, 3], date(2026, 11, 15)),
        "Tuesdays and Thursdays 7pm for 8 weeks": ([1, 3], date(2026, 11, 29)),
        "every mon, wed & fri until 30 Nov": ([0, 2, 4], date(2026, 11, 30)),
        "yoga every sunday until 2026-12-01": ([6], date(2026, 12, 1)),
        "Saturdays 9:30am-11:30am": ([5], None),
    }
    for text, (days, until) in cases.items():
        found = parse_recurrence(text, reference)
        assert (found.weekdays, found.until) == (days, until), text
    assert parse_recurrence("CCA dinner Friday 7pm", reference) is None
    assert parse_recurrence("Tue/Thu 7pm", reference) is None  # alternatives, not a repeat


def test_teaching_weeks_skip_the_recess_week(reference):
    assert week_end(1, reference) == date(2026, 8, 16)
    assert week_end(6, reference) == date(2026, 9, 20)
    assert week_end(7, reference) == date(2026, 10, 4)  # week 7 follows recess
    assert week_end(13, reference) == date(2026, 11, 15)


def test_session_dates_are_bounded(reference):
    import pytest

    from app.services.date_resolution import Clarification

    days = session_dates([1, 3], date(2026, 10, 6), date(2026, 10, 15))
    assert days == [date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 13), date(2026, 10, 15)]
    with pytest.raises(Clarification):
        session_dates([1], date(2026, 10, 6), date(2026, 10, 1))
    with pytest.raises(Clarification):
        session_dates([1], date(2026, 10, 6), date(2026, 10, 6) + timedelta(weeks=40))


async def test_series_makes_a_card_per_session_and_posts_them_ahead(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    found = sessions(repo)
    assert len(found) == 12
    assert [g.occurrence_date.isoformat() for g in found[:3]] == [
        "2026-10-06",
        "2026-10-08",
        "2026-10-13",
    ]
    assert found[-1].occurrence_date == date(2026, 11, 12)
    assert all(g.start_at.hour == 19 and g.end_at.hour == 21 for g in found)
    assert all(g.location == "UTSRC" and g.title == "Training" for g in found)
    # Only the first card is in the group; the rest wait.
    assert found[0].message_id == 101 and all(g.message_id is None for g in found[1:])
    card = transport.sent[-1]
    assert "🔁 12 sessions, every Tue/Thu until Thu 12 Nov" in card["text"]
    assert "Going to all" in buttons(card["reply_markup"])
    series = next(iter(repo.series.values()))
    assert series.weekdays == [1, 3] and series.until_date == date(2026, 11, 12)
    # A retried delivery of the same command makes nothing new.
    async with bot:
        await bot.process_update(
            Update.de_json(group_message(f"/event {TRAINING}", 50, 1), bot.bot)
        )
    assert len(sessions(repo)) == 12 and len(repo.series) == 1

    # Not chased while unposted. The tick posts a session once it is within four days.
    service.clock = lambda: reference + timedelta(days=1)  # Mon 5 Oct, 12:00
    first = await service.notifier.run()
    assert first.get("posted") == 1  # Thu 8 Oct 19:00 is under four days away
    assert len(service.posted_cards) == 1 and "Thu, 08 Oct" in service.posted_cards[0][1].text
    assert sessions(repo)[1].message_id == 701
    assert "posted" not in await service.notifier.run()


async def test_going_to_all_covers_every_future_session(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    taps = buttons(transport.sent[-1]["reply_markup"])
    async with bot:
        await bot.process_update(Update.de_json(group_tap(taps["Going to all"], 2), bot.bot))
    assert alerts(transport)[-1]["text"].startswith("You're Going to 12 sessions")
    user = await repo.ensure_user(2)
    mine = [e for e in repo.events.values() if e.user_id == user.id and e.group_event_id]
    assert len(mine) == 12 and {e.status for e in mine} == {Status.GOING}


async def test_one_session_changed_alone_then_all_later_ones(service_factory, reference):
    service, repo = service_factory(
        SERIES, moved("2026-10-08"), moved("2026-10-15", "18:00", "20:00"), moved("2026-10-08")
    )
    bot, transport = await post_series(service, repo)
    found = sessions(repo)
    taps = buttons(transport.sent[-1]["reply_markup"])
    async with bot:
        await bot.process_update(Update.de_json(group_tap(taps["Going"], 2), bot.bot))

    async def change(event, message_id, text="20:00–22:00"):
        await service.command(1, "start", f"edit_{event.id}")
        preview = (await service.message(1, message_id, text, reference))[0]
        return await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)

    # Thursday the 8th moves to 8pm on its own.
    done = await change(found[1], 91)
    assert done[0].text.startswith("Event updated")
    assert done[1].keyboard.inline_keyboard[0][0].text == "Apply to all future sessions"
    assert repo.group_events[found[1].id].edited_alone is True
    assert repo.group_events[found[0].id].start_at.hour == 19
    # The 15th gets its own change too (6pm).
    await change(found[3], 92, "18:00–20:00")
    assert repo.group_events[found[3].id].start_at.hour == 18

    # Apply the 8 Oct change to everything after it, except sessions changed on their own.
    answer = await service.callback(1, f"ser:all:{found[1].id}")
    assert "Updated 9 later session(s)" in answer[0].text
    later = sessions(repo)
    assert [g.start_at.hour for g in later[:4]] == [19, 20, 20, 18]
    assert all(g.start_at.hour == 20 and g.end_at.hour == 22 for g in later[4:])
    assert [g.start_at.date() for g in later] == [
        date(2026, 10, 6),
        date(2026, 10, 8),
        date(2026, 10, 13),
        date(2026, 10, 15),
    ] + [g.start_at.date() for g in later[4:]]
    # Series-wide changes are not "on their own": a later bulk change still reaches them.
    assert not any(g.edited_alone for g in later[2:3] + later[4:])
    # Member 2 said Going on the first card only: nothing to tell them about 8 Oct yet.
    series = next(iter(repo.series.values()))
    assert (series.start_time.hour, series.end_time.hour) == (20, 22)
    # Only the organiser may do this.
    assert "Only the organiser" in (await service.callback(2, f"ser:all:{found[1].id}"))[0].text


async def test_cancelling_one_session_can_stop_the_rest(service_factory, reference):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo)
    found = sessions(repo)
    await service.command(1, "start", f"cancel_{found[2].id}")
    state = (await repo.ensure_user(1)).pending
    done = await service.callback(1, f"confirm:{state['nonce']}")
    assert "cancelled for everyone" in done[0].text
    stop = done[1].keyboard.inline_keyboard[0][0]
    assert stop.text == "Cancel all future sessions"
    result = await service.callback(1, stop.callback_data)
    assert "9 later session(s) cancelled" in result[0].text
    statuses = [g.status for g in sessions(repo)]
    assert statuses[:2] == ["ACTIVE", "ACTIVE"] and set(statuses[2:]) == {"CANCELLED"}
    assert next(iter(repo.series.values())).status == "STOPPED"


async def test_series_needs_an_end_and_a_workable_time(service_factory):
    service, repo = service_factory(SERIES)
    bot, transport = await post_series(service, repo, text="Training every Tue/Thu 7-9pm at UTSRC")
    reply = transport.sent[-1]["text"]
    assert "How long does it run?" in reply and "until week 13" in reply
    assert not repo.series and not repo.group_events
