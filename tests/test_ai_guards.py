"""Deterministic guards around the LLM, each from a failure seen in the 11 Oct baseline eval."""

from datetime import datetime

import pytest

from app.schemas.event import SG
from app.services.date_resolution import (
    Clarification,
    in_source,
    restore_meridiem,
    validate_extraction,
)
from app.services.event_extractor import settle_kind
from tests.conftest import extraction

MONDAY = datetime(2026, 10, 12, 10, tzinfo=SG)


@pytest.mark.parametrize(
    "value,source",
    [
        ("tomorrow", "Mid-autumn gathering tmrw 8-10pm"),
        ("tomorrow", "dinner tmr can?"),
        ("Saturday", "Badminton this Sat 9am"),
        ("Tuesday", "Swim training Tue 7-8am"),
        ("Thursday", "Rehearsal Thurs 7pm"),
        ("7:30pm", "Bubble tea run tmr 730pm-830pm"),
        ("14:00", "Time: 1400 – 1500"),
    ],
)
def test_harmless_rewording_is_accepted(value, source):
    assert in_source(value, source)


@pytest.mark.parametrize(
    "value,source",
    [
        ("14 Oct 2026", "CS3244 consultation Wed 14 Oct 3pm"),  # invented year
        ("Friday", "Dinner Saturday 7pm"),
        ("Wed", "Wedding dinner Saturday 7pm"),
        ("8pm", "Dinner 7-9pm"),
    ],
)
def test_added_or_wrong_information_is_rejected(value, source):
    assert not in_source(value, source)


@pytest.mark.parametrize(
    "raw,source,expected",
    [
        ("6:30", "Dinner Sunday 6:30pm–8:30pm at Jewel", "6:30pm"),
        ("730", "Bubble tea 730pm-830pm", "730pm"),
        ("7:30", "jb101 makan jio 730pm", "7:30pm"),
        ("3", "Coffee chat Wed 3pm", "3pm"),
        ("7", "CCA dinner Friday 7–9pm", "7"),  # shared suffix, left to the range rule
        ("19:00", "Jam 19:00 - 22:00", "19:00"),
        ("7pm", "Dinner 7pm", "7pm"),
    ],
)
def test_dropped_am_pm_is_restored_from_the_message(raw, source, expected):
    assert restore_meridiem(raw, source) == expected


def test_evening_is_never_turned_into_morning():
    draft = validate_extraction(
        extraction(date_text="Sunday", start_time_text="6:30", end_time_text="8:30pm"),
        "Dinner with the gang Sunday 6:30pm–8:30pm at Jewel",
        MONDAY,
    )
    assert f"{draft.start_at:%H:%M}" == "18:30"


def test_answer_to_a_question_stays_part_of_the_new_event():
    assert settle_kind(extraction(kind="update"), clarifying=True).kind == "new"
    assert settle_kind(extraction(kind="unknown"), clarifying=True).kind == "new"
    assert settle_kind(extraction(kind="update"), clarifying=False).kind == "update"
    hello = extraction(kind="unknown")  # model invented Friday 7: not in the message
    assert settle_kind(hello, clarifying=False, text="hello").kind == "unknown"


def test_unclear_invitation_is_asked_about_not_dropped():
    unclear = extraction(kind="unknown", date_text="Friday or Saturday", ambiguity="two dates")
    text = "Dinner Friday or Saturday 7-9pm"
    assert settle_kind(unclear, clarifying=False, text=text).kind == "new"
    chatter = extraction(kind="unknown", date_text=None, start_time_text=None, ambiguity="?")
    assert settle_kind(chatter, clarifying=False, text=text).kind == "unknown"
    with pytest.raises(Clarification, match="two dates"):
        validate_extraction(settle_kind(unclear, False, text), text, MONDAY)


async def test_bot_completes_a_clarification_the_model_calls_an_update(service_factory, reference):
    service, _ = service_factory(
        extraction(title="Coffee chat", date_text="Wed", start_time_text="3pm", end_time_text=None),
        extraction(
            kind="update",
            title="Coffee chat",
            date_text="Wed",
            start_time_text="3pm",
            end_time_text="4pm",
        ),
    )
    first = await service.message(1, 1, "Coffee chat Wed 3pm at Starbucks", reference)
    assert "end" in first[0].text
    card = await service.message(1, 2, "till 4pm", reference)
    assert card[0].text.startswith("✅") and "Coffee chat" in card[0].text


# ---- second eval round (prompt v2) ----


@pytest.mark.parametrize(
    "value,source",
    [
        ("2pm", "Project meeting tomorrow 2-4pm at COM1 SR3"),
        ("8:00pm", "Movie night tonight 8–10:30pm at GV Vivo"),
        ("1pm", "Cohort photo Monday 19 Oct 1-2pm"),
        ("6:30pm", "Talk on Wednesday, 6.30 to 8pm"),
    ],
)
def test_start_that_borrowed_the_ranges_pm_is_accepted(value, source):
    assert in_source(value, source)


@pytest.mark.parametrize(
    "value,source",
    [
        ("2am", "Meeting 2-4pm"),  # wrong meridiem
        ("3pm", "Meeting 2-4pm"),  # wrong hour
        ("2pm", "Meeting at 2, ends 4pm"),  # not a range
    ],
)
def test_start_that_changes_the_meaning_is_rejected(value, source):
    assert not in_source(value, source)


def test_shared_pm_start_resolves_to_the_afternoon():
    draft = validate_extraction(
        extraction(date_text="tomorrow", start_time_text="2pm", end_time_text="4pm"),
        "Project meeting tomorrow 2-4pm at COM1 SR3",
        MONDAY,
    )
    assert (f"{draft.start_at:%H:%M}", f"{draft.end_at:%H:%M}") == ("14:00", "16:00")


def test_whole_range_in_the_start_field_is_split():
    draft = validate_extraction(
        extraction(date_text="this Thursday", start_time_text="4–6pm", end_time_text="6pm"),
        "our CS3244 group meeting is this Thursday 4–6pm at COM3 MR6",
        MONDAY,
    )
    assert (f"{draft.start_at:%H:%M}", f"{draft.end_at:%H:%M}") == ("16:00", "18:00")


@pytest.mark.parametrize(
    "text,count",
    [
        ("1) Bake sale this Wed 11am-2pm\n2) Inter-hall games Sat 17 Oct 2-6pm", 2),
        ("CCA dinner Friday 7–9pm at Vivo", 1),
        ("Time: 1400 – 1500", 1),
        ("Jam session Saturday 19:00 - 22:00", 1),
        ("Hackathon 2026-10-17 9am–6pm", 1),  # the date's hyphens are not a time range
        ("Workshop 16/10 9am-12pm", 1),
        ("Night cycling Sat 11pm to Sun 2am", 0),
        ("Room 3-5 on level 2", 0),  # numbers without am/pm or a clock are not times
    ],
)
def test_time_ranges(text, count):
    from app.services.date_resolution import time_ranges

    assert len(time_ranges(text)) == count


def test_two_events_in_one_message_are_not_saved_as_one():
    from app.services.date_resolution import single_event_guard

    with pytest.raises(Clarification, match="more than one event"):
        single_event_guard("1) Bake sale this Wed 11am-2pm\n2) Inter-hall games Sat 17 Oct 2-6pm")
    single_event_guard("CCA dinner Friday 7–9pm at Vivo")  # no error


def test_an_answer_resolves_the_old_ambiguity():
    from app.services.event_extractor import settle_ambiguity

    stuck = extraction(date_text="Saturday", ambiguity="Fri or Sat")
    assert settle_ambiguity(stuck, "Saturday").ambiguity is None
    # An answer that doesn't supply a date or time leaves the question open.
    assert settle_ambiguity(stuck, "ok").ambiguity == "Fri or Sat"


async def test_bot_asks_for_one_event_and_keeps_no_draft(service_factory, reference):
    service, repo = service_factory(extraction(title="Inter-hall games", date_text="Sat 17 Oct"))
    text = "Week 10:\n1) Bake sale this Wed 11am-2pm\n2) Inter-hall games Sat 17 Oct 2-6pm"
    reply = await service.message(1, 1, text, reference)
    assert "more than one event" in reply[0].text
    assert not repo.events and not (await repo.ensure_user(1)).pending


async def test_bot_accepts_saturday_as_the_answer_to_fri_or_sat(service_factory, reference):
    service, _ = service_factory(
        extraction(date_text="Friday or Saturday", ambiguity="Fri or Sat"),
        extraction(date_text="Saturday", ambiguity="Fri or Sat"),  # model repeats the flag
    )
    first = await service.message(1, 1, "Dinner Friday or Saturday 7–9pm at Vivo", reference)
    assert "Fri or Sat" in first[0].text
    card = await service.message(1, 2, "Saturday", reference)
    assert card[0].text.startswith("✅")


# ---- third eval round (prompt v3) ----


def test_dot_time_swapped_for_a_colon_keeps_its_pm():
    draft = validate_extraction(
        extraction(date_text="Wednesday", start_time_text="6:30", end_time_text="8pm"),
        "Talk on Wednesday, 6.30 to 8pm, LT27",
        MONDAY,
    )
    assert f"{draft.start_at:%H:%M}" == "18:30"


def test_colon_time_written_by_the_sender_stays_24_hour():
    draft = validate_extraction(
        extraction(date_text="Saturday", start_time_text="07:00", end_time_text="9pm"),
        "Run Saturday 07:00–9pm",
        MONDAY,
    )
    assert f"{draft.start_at:%H:%M}" == "07:00"


def test_a_reply_that_is_a_date_becomes_the_date():
    from app.services.event_extractor import settle_ambiguity

    kept = extraction(date_text="Friday or Saturday", ambiguity="date")
    fixed = settle_ambiguity(kept, "Saturday")
    assert (fixed.date_text, fixed.ambiguity) == ("Saturday", None)
    # A reply that isn't a date doesn't touch the date.
    assert settle_ambiguity(kept, "7pm").date_text == "Friday or Saturday"
