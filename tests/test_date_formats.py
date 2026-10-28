"""Everyday Singapore date/time formats that the extractor passes through verbatim."""

from datetime import date, datetime, time

import pytest

from app.schemas.event import SG
from app.services.date_resolution import (
    Clarification,
    resolve_date,
    resolve_time,
    validate_extraction,
)
from tests.conftest import extraction

MONDAY = datetime(2026, 10, 12, 10, tzinfo=SG)


@pytest.mark.parametrize(
    "raw,meridiem,expected",
    [
        ("7.30pm", None, time(19, 30)),
        ("11.30pm", None, time(23, 30)),
        ("10.30am", None, time(10, 30)),
        ("7.00pm", None, time(19, 0)),
        ("7 p.m.", None, time(19, 0)),
        ("1930", None, time(19, 30)),
        ("0800", None, time(8, 0)),
        ("1900hrs", None, time(19, 0)),
        ("1400 hrs", None, time(14, 0)),
        ("730pm", None, time(19, 30)),
        ("noon", None, time(12, 0)),
        ("midnight", None, time(0, 0)),
        ("6.30", "pm", time(18, 30)),
        ("730", "pm", time(19, 30)),
        ("19:00", None, time(19, 0)),
    ],
)
def test_time_formats(raw, meridiem, expected):
    assert resolve_time(raw, meridiem) == expected


@pytest.mark.parametrize("raw", ["7", "7.30", "730", "7ish", "evening"])
def test_still_asks_when_am_pm_is_unknown(raw):
    with pytest.raises(Clarification):
        resolve_time(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("tmr", date(2026, 10, 13)),
        ("tmrw", date(2026, 10, 13)),
        ("tdy", date(2026, 10, 12)),
        ("17th", date(2026, 10, 17)),
        ("the 17th", date(2026, 10, 17)),
        ("on the 5th", date(2026, 11, 5)),
        ("Tue 13 Oct", date(2026, 10, 13)),
        ("Saturday, 17 October", date(2026, 10, 17)),
        ("Thursday 15th Oct", date(2026, 10, 15)),
        ("Friday 16 October 2026", date(2026, 10, 16)),
        ("15/10 (Thu)", date(2026, 10, 15)),
        ("Thurs", date(2026, 10, 15)),
    ],
)
def test_date_formats(raw, expected):
    assert resolve_date(raw, MONDAY) == expected


def test_weekday_that_disagrees_with_the_date_asks():
    with pytest.raises(Clarification, match="is a Tuesday"):
        resolve_date("Wed 13 Oct", MONDAY)


@pytest.mark.parametrize(
    "text,fields,start,end",
    [
        (
            "Rehearsal Thu 1930-2130",
            dict(date_text="Thu", start_time_text="1930", end_time_text="2130"),
            "19:30",
            "21:30",
        ),
        (
            "Talk Wednesday, 6.30 to 8pm",
            dict(date_text="Wednesday", start_time_text="6.30", end_time_text="8pm"),
            "18:30",
            "20:00",
        ),
        (
            "Bubble tea tmr 730pm-830pm",
            dict(date_text="tmr", start_time_text="730pm", end_time_text="830pm"),
            "19:30",
            "20:30",
        ),
        (
            "Workshop Wednesday 7:30–9:00 PM",
            dict(date_text="Wednesday", start_time_text="7:30", end_time_text="9:00 PM"),
            "19:30",
            "21:00",
        ),
        (
            "Seminar Wednesday 11:30-1pm",
            dict(date_text="Wednesday", start_time_text="11:30", end_time_text="1pm"),
            "11:30",
            "13:00",
        ),
        (
            "Dinner Wednesday 19:30-9pm",
            dict(date_text="Wednesday", start_time_text="19:30", end_time_text="9pm"),
            "19:30",
            "21:00",
        ),
        # Speech-to-text writes "p.m." with dots; the model often copies it as "pm".
        *[
            (
                "Dinner tomorrow, 7 to 9 p.m.",
                dict(date_text="tomorrow", start_time_text=a, end_time_text=b),
                "19:00",
                "21:00",
            )
            for a, b in [("7pm", "9pm"), ("7 p.m.", "9 p.m."), ("7", "9 p.m."), ("7", "9 PM")]
        ],
        (
            "Lunch Thursday noon to 1.30pm",
            dict(date_text="Thursday", start_time_text="noon", end_time_text="1.30pm"),
            "12:00",
            "13:30",
        ),
    ],
)
def test_whole_messages(text, fields, start, end):
    draft = validate_extraction(extraction(**fields), text, MONDAY)
    assert f"{draft.start_at:%H:%M}" == start and f"{draft.end_at:%H:%M}" == end


def test_ending_at_midnight_rolls_to_the_next_day():
    draft = validate_extraction(
        extraction(date_text="Fri", start_time_text="10pm", end_time_text="12am"),
        "Supper Fri 10pm-12am",
        MONDAY,
    )
    assert draft.end_at == datetime(2026, 10, 17, 0, 0, tzinfo=SG)


def test_other_overnight_ranges_still_need_an_end_date():
    with pytest.raises(Clarification, match="overnight"):
        validate_extraction(
            extraction(date_text="Fri", start_time_text="11pm", end_time_text="2am"),
            "Games Fri 11pm-2am",
            MONDAY,
        )


def test_model_rewriting_noon_as_12_still_verifies():
    source = "Lunch Thursday noon to 1.30pm at Frontier"
    draft = validate_extraction(
        extraction(date_text="Thursday", start_time_text="12", end_time_text="1.30pm"),
        source,
        MONDAY,
    )
    assert draft.start_at == datetime(2026, 10, 15, 12, 0, tzinfo=SG)
    assert draft.end_at == datetime(2026, 10, 15, 13, 30, tzinfo=SG)


@pytest.mark.parametrize("source", ["Call with mentor Friday 8-9pm UK time", "Webinar Fri 9pm EST"])
def test_other_timezone_asks_instead_of_saving(source):
    with pytest.raises(Clarification):
        validate_extraction(
            extraction(date_text="Friday", start_time_text="8", end_time_text="9pm"),
            source,
            MONDAY,
        )
