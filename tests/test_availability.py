from datetime import datetime, timedelta

import pytest

from app.models.timetable import BusyBlock
from app.schemas.event import SG, Event, EventDraft, Status
from app.services.availability import check_availability

START = datetime(2026, 10, 9, 19, tzinfo=SG)
END = START + timedelta(hours=2)


def draft(start=START, end=END):
    return EventDraft(title="Dinner", start_at=start, end_at=end)


def check(candidate, blocks=(), events=(), **kwargs):
    return check_availability(
        candidate,
        blocks,
        events,
        coverage_start=START - timedelta(days=1),
        coverage_end=END + timedelta(days=1),
        **kwargs,
    )


@pytest.mark.parametrize(
    "offset,length,expected",
    [
        (0, 2, "CLASH"),
        (1, 1, "CLASH"),
        (-1, 4, "CLASH"),
        (2, 1, "FREE"),
        (-1, 1, "FREE"),
        (3, 1, "FREE"),
    ],
)
def test_overlap_boundaries(offset, length, expected):
    start = START + timedelta(hours=offset)
    block = BusyBlock(title="Class", start_at=start, end_at=start + timedelta(hours=length))
    assert check(draft(), [block]).verdict == expected


@pytest.mark.parametrize("status", list(Status))
def test_only_going_blocks(status):
    event = Event(**draft().model_dump(), id="e", user_id="u", status=status)
    assert check(draft(), events=[event]).verdict == ("CLASH" if status == Status.GOING else "FREE")
    assert check(draft(), events=[event], exclude_event_id="e").verdict == "FREE"


def test_unknown_outside_coverage():
    assert check_availability(draft(), [], []).verdict == "UNKNOWN"
    assert check_availability(draft(), [BusyBlock(**draft().model_dump())], []).verdict == "CLASH"


def test_timezone_normalisation():
    block = BusyBlock(
        title="UTC exam", start_at="2026-10-09T11:00:00Z", end_at="2026-10-09T13:00:00Z"
    )
    assert check(draft(), [block]).verdict == "CLASH"


def test_invalid_interval_and_naive_datetime():
    with pytest.raises(ValueError):
        draft(END, START)
    with pytest.raises(ValueError):
        draft(START.replace(tzinfo=None), END)
