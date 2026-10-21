from datetime import datetime

import pytest

from app.schemas.event import SG, Status
from tests.conftest import extraction, tap

NOTES = """Meeting notes

20 October
Completed allocation of tasks
Check if video due 4 November
Aim to finish allocated tasks by 27 October
28 October 10pm meeting to confirm research and organise research data"""


@pytest.mark.parametrize("default_minutes", [0, 120])
async def test_meeting_in_notes_uses_its_own_date_and_handles_missing_end(
    service_factory, default_minutes
):
    # Model behaviour is covered by the live eval cases. Here, verify that the rest of
    # the bot accepts a meeting surrounded by other dates and keeps end times honest.
    service, repo = service_factory(
        extraction(
            title="Research meeting",
            date_text="28 October",
            start_time_text="10pm",
            end_time_text=None,
            location=None,
        ),
        default_event_duration_minutes=default_minutes,
    )
    reference = datetime(2026, 10, 20, 22, 52, tzinfo=SG)
    reply = (await service.message(1, 1, NOTES, reference))[0]
    if not default_minutes:
        assert "What time does this event end?" in reply.text
        assert not repo.events
        reply = (await service.message(1, 2, "11pm", reference))[0]
    event = next(iter(repo.events.values()))
    assert event.start_at == datetime(2026, 10, 28, 22, tzinfo=SG)
    assert event.end_at == (
        datetime(2026, 10, 29, 0, tzinfo=SG)
        if default_minutes
        else datetime(2026, 10, 28, 23, tzinfo=SG)
    )
    assert event.assumed_duration == bool(default_minutes)
    assert ("End time estimated." in reply.text) == bool(default_minutes)
    assert not repo.deadlines
    assert event.status == Status.PREVIEW
    await service.callback(1, tap(reply, "Add to plans"))
    assert event.status == Status.GOING


async def test_unknown_message_suggests_forwarding_the_relevant_line(service_factory, reference):
    service, repo = service_factory(
        extraction(kind="unknown", date_text=None, start_time_text=None, end_time_text=None)
    )
    reply = (await service.message(1, 1, "Meeting notes: tasks allocated", reference))[0]
    assert "try sending just the relevant line" in reply.text
    assert "start/end" not in reply.text
    assert not repo.events
