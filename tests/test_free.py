"""/free and partial-overlap wording."""

from datetime import date, datetime, timedelta

import pytest

from app.bot.messages import clash_detail, clock, duration, private_verdict, time_range
from app.models.timetable import BusyBlock
from app.schemas.event import SG, EventDraft
from app.services.availability import check_availability
from app.services.date_resolution import Clarification
from app.services.free_time import free_gaps, parse_free, weekend
from tests.conftest import extraction, tap

MONDAY = datetime(2026, 10, 12, 10, tzinfo=SG)


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=SG)


def slot(a, b, title="Dinner"):
    return EventDraft(title=title, start_at=a, end_at=b)


# ---------- partial overlaps ----------

DINNER = slot(at(16, 19), at(16, 21))


@pytest.mark.parametrize(
    "busy,expected",
    [
        (slot(at(16, 18), at(16, 22), "CS2040 Lab"), "Clashes with CS2040 Lab (the whole time)"),
        (slot(at(16, 19), at(16, 21), "CS2040 Lab"), "Clashes with CS2040 Lab (the whole time)"),
        (
            slot(at(16, 18), at(16, 19, 30), "CS2040 Lab"),
            "Clashes with CS2040 Lab: it ends at 7:30pm, so you'd miss the first 30 min",
        ),
        (
            slot(at(16, 20), at(16, 22), "Badminton"),
            "Clashes with Badminton: you'd need to leave at 8pm (the last 1 hr)",
        ),
        (slot(at(16, 19, 30), at(16, 20), "Call"), "Clashes with Call from 7:30–8pm"),
    ],
)
def test_clash_detail(busy, expected):
    assert clash_detail(DINNER, busy) == expected


def test_time_words():
    assert clock(at(16, 19)) == "7pm" and clock(at(16, 19, 30)) == "7:30pm"
    assert time_range(at(16, 19), at(16, 21)) == "7–9pm"
    assert time_range(at(16, 11), at(16, 13)) == "11am–1pm"
    assert duration(30) == "30 min" and duration(60) == "1 hr" and duration(75) == "1 hr 15 min"


def test_partial_overlap_is_still_a_clash():
    lab = BusyBlock(title="CS2040 Lab", start_at=at(16, 18), end_at=at(16, 19, 15))
    result = check_availability(DINNER, [lab], [])
    assert result.verdict == "CLASH"
    popup = private_verdict(result)
    assert "you'd miss the first 15 min" in popup and len(popup) <= 200


async def test_card_says_how_much_of_the_plan_is_lost(service_factory, reference):
    service, _ = service_factory(
        extraction(title="CS2040 Lab", start_time_text="6pm", end_time_text="8pm"),
        extraction(title="CCA dinner", start_time_text="7", end_time_text="9pm"),
    )
    lab = (await service.message(1, 1, "CS2040 Lab Friday 6pm-8pm", reference))[0]
    await service.callback(1, tap(lab, "Add to plans"))
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm", reference))[0]
    assert card.text.startswith(
        "⚠️ Clashes with CS2040 Lab: it ends at 8pm, so you'd miss the first 1 hr"
    )
    assert "Add anyway" in [b.text for row in card.keyboard.inline_keyboard for b in row]


# ---------- reading /free ----------


@pytest.mark.parametrize(
    "text,start,end,assumed",
    [
        ("fri 7-9pm", at(16, 19), at(16, 21), False),
        ("fri 7pm", at(16, 19), at(16, 21), True),
        ("sat 2-5pm", at(17, 14), at(17, 17), False),
        ("7.30pm", at(12, 19, 30), at(12, 21, 30), True),
        ("Tue 13 Oct 1400-1600", at(13, 14), at(13, 16), False),
        ("noon to 2pm sun", at(18, 12), at(18, 14), False),
    ],
)
def test_time_queries(text, start, end, assumed):
    q = parse_free(text, MONDAY)
    assert (q.slot.start_at, q.slot.end_at, q.assumed_end) == (start, end, assumed)


@pytest.mark.parametrize(
    "text,days",
    [
        ("", (date(2026, 10, 12),)),
        ("tomorrow", (date(2026, 10, 13),)),
        ("tmr", (date(2026, 10, 13),)),
        ("17th", (date(2026, 10, 17),)),
        ("this weekend", (date(2026, 10, 17), date(2026, 10, 18))),
    ],
)
def test_day_queries(text, days):
    assert parse_free(text, MONDAY).days == days


@pytest.mark.parametrize("text", ["friday 7", "fri at 7", "sat 2-5"])
def test_unclear_am_pm_is_asked(text):
    with pytest.raises(Clarification, match="AM or PM"):
        parse_free(text, MONDAY)


def test_weekend_from_each_day():
    assert weekend(date(2026, 10, 12)) == (date(2026, 10, 17), date(2026, 10, 18))  # Monday
    assert weekend(date(2026, 10, 17)) == (date(2026, 10, 17), date(2026, 10, 18))  # Saturday
    assert weekend(date(2026, 10, 18)) == (date(2026, 10, 18),)  # Sunday


# ---------- free gaps ----------


def test_free_gaps_around_busy_times():
    busy = [
        (at(13, 7), at(13, 9)),  # starts before 8am
        (at(13, 12), at(13, 14)),
        (at(13, 13), at(13, 15)),  # overlaps the previous one
        (at(13, 15, 15), at(13, 18)),  # leaves a 15-minute gap: too short to list
        (at(13, 22, 45), at(13, 23, 30)),  # runs past 11pm
    ]
    gaps = free_gaps(date(2026, 10, 13), busy, MONDAY)
    assert gaps == [(at(13, 9), at(13, 12)), (at(13, 18), at(13, 22, 45))]


def test_today_starts_from_the_next_quarter_hour():
    now = at(12, 15, 47)
    assert free_gaps(date(2026, 10, 12), [], now) == [(at(12, 16), at(12, 23))]


# ---------- /free in the bot ----------


async def test_free_with_no_time_offers_buttons(service_factory, reference):
    service, _ = service_factory()
    reply = (await service.command(1, "free"))[0]
    labels = [b.text for row in reply.keyboard.inline_keyboard for b in row]
    assert labels == ["Today", "Tomorrow", "This weekend"]
    today = (await service.callback(1, tap(reply, "Today")))[0]
    assert (
        today.edit and today.text == "🟢 Sun 4 Oct: nothing booked, free for the rest of the day."
    )


async def test_free_lists_gaps_around_plans_and_calendar(service_factory, reference):
    service, repo = service_factory(extraction())  # CCA dinner Fri 9 Oct 7–9pm
    card = (await service.message(1, 1, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    user = await repo.ensure_user(1)
    calendar, _ = await repo.add_calendar(user.id, "sealed", "hash", "CCA")
    lunch = (datetime(2026, 10, 9, 12, tzinfo=SG), datetime(2026, 10, 9, 14, tzinfo=SG))
    await repo.replace_calendar(calendar.id, [lunch], reference)
    reply = (await service.command(1, "free", "fri"))[0]
    assert reply.text == (
        "📅 Fri 9 Oct\nBusy:\n• 12–2pm CCA calendar\n• 7–9pm CCA dinner (Going)\n"
        "Free: 8am–12pm, 2–7pm, 9–11pm"
    )


async def test_free_checks_a_time(service_factory, reference):
    service, _ = service_factory(extraction())
    card = (await service.message(1, 1, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    clash = (await service.command(1, "free", "fri 8-10pm"))[0]
    assert clash.text.startswith(
        "⚠️ Clashes with CCA dinner: it ends at 9pm, so you'd miss the first 1 hr"
    )
    assert "Fri 9 Oct · 8–10pm" in clash.text
    free = (await service.command(1, "free", "sat 2-5pm"))[0]
    assert free.text.startswith("✅ No clashes found · Sat 10 Oct · 2–5pm")
    guessed = (await service.command(1, "free", "sat 2pm"))[0]
    assert "No end time given, so I checked 2 hours" in guessed.text


async def test_free_mentions_saved_plans(service_factory, reference):
    # The card no longer offers Save; a Saved plan still exists (e.g. a group plan) and the
    # engine lists it as a possible overlap, so we set the status directly.
    service, repo = service_factory(extraction())
    await service.message(1, 1, "CCA dinner Friday 7–9pm at Vivo", reference)
    ev = next(iter(repo.events.values()))
    await repo.save(ev.user_id, ev)
    reply = (await service.command(1, "free", "fri 7-9pm"))[0]
    assert (
        reply.text.startswith("✅ No clashes found") and "You also saved CCA dinner" in reply.text
    )


async def test_free_explains_what_it_needs(service_factory, reference):
    service, _ = service_factory()
    reply = (await service.command(1, "free", "fri 7"))[0]
    assert "AM or PM" in reply.text and "/free fri 7-9pm" in reply.text
    past = (await service.command(1, "free", "2026-10-01"))[0]
    assert "already passed" in past.text


def test_week_range_sanity():
    assert MONDAY + timedelta(days=4) == at(16, 10)


def test_dates_with_hyphens_are_not_time_ranges():
    q = parse_free("2026-10-16 7-9pm", MONDAY)
    assert (q.slot.start_at, q.slot.end_at) == (at(16, 19), at(16, 21))
    assert parse_free("2026-10-16", MONDAY).days == (date(2026, 10, 16),)


async def test_free_lists_saved_plans_without_making_you_busy(service_factory, reference):
    service, repo = service_factory(extraction())  # CCA dinner Fri 9 Oct 7-9pm
    await service.message(1, 1, "CCA dinner Friday 7–9pm at Vivo", reference)
    ev = next(iter(repo.events.values()))
    await repo.save(ev.user_id, ev)  # a Saved plan; the card no longer offers Save
    reply = (await service.command(1, "free", "fri"))[0]
    assert reply.text == (
        "📅 Fri 9 Oct\nNothing confirmed:\n• 7–9pm CCA dinner (Saved, not decided)\nFree: 8am–11pm"
    )
    # Going makes it definite: the slot is busy and no longer free.
    await service.callback(1, tap((await service.command(1, "events"))[0], "Add to plans"))
    reply = (await service.command(1, "free", "fri"))[0]
    assert reply.text == ("📅 Fri 9 Oct\nBusy:\n• 7–9pm CCA dinner (Going)\nFree: 8am–7pm, 9–11pm")
    assert "Checked" not in reply.text


async def test_free_all_day_and_fully_booked_days(service_factory, reference):
    service, _ = service_factory(
        extraction(
            title="Hackathon", date_text="Friday", start_time_text="08:00", end_time_text="23:00"
        )
    )
    assert (await service.command(1, "free", "thu"))[0].text == (
        "🟢 Thu 8 Oct: nothing booked, free all day."
    )
    card = (await service.message(1, 1, "Hackathon Friday 08:00 23:00", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    reply = (await service.command(1, "free", "fri"))[0]
    assert reply.text == (
        "📅 Fri 9 Oct\nBusy:\n• 8am–11pm Hackathon (Going)\n🔴 No free time between 8am and 11pm."
    )
