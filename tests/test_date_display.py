"""Displayed dates use the Singapore day, including across midnight and New Year."""

from datetime import UTC, date, datetime

import pytest

from app.bot.messages import details, personal_when
from app.date_display import display_date
from app.schemas.event import SG, EventDraft


@pytest.mark.parametrize(
    "value,expected",
    [
        (date(2026, 11, 5), "📅 05/11/2026 (Thursday)"),
        (date(2028, 2, 29), "📅 29/02/2028 (Tuesday)"),
        (datetime(2026, 11, 4, 18, tzinfo=UTC), "📅 05/11/2026 (Thursday)"),
        (datetime(2026, 12, 31, 17, tzinfo=UTC), "📅 01/01/2027 (Friday)"),
    ],
)
def test_local_date(value, expected):
    assert display_date(value) == expected


def test_overnight_dates_match_in_personal_and_group_details():
    start = datetime(2026, 12, 31, 23, tzinfo=SG)
    end = datetime(2027, 1, 1, 1, tzinfo=SG)
    event = EventDraft(title="Countdown", start_at=start, end_at=end)
    for text in (personal_when(start, end), details(event)):
        assert "📅 31/12/2026 (Thursday)" in text
        assert "📅 01/01/2027 (Friday)" in text
        assert text.count("📅") == 2
