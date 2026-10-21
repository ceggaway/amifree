from datetime import datetime, timedelta

import pytest

from app.schemas.event import SG, Status
from app.services.date_resolution import Clarification, resolve_date
from app.services.nusmods import academic_year_for
from tests.conftest import extraction, onboard, tap


async def test_edit_tomorrow_uses_correction_message_time(service_factory, reference):
    service, repo = service_factory(
        extraction(), extraction(date_text="tomorrow", start_time_text="8pm", end_time_text="10pm")
    )
    await onboard(service, reference)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    menu = (await service.callback(1, tap(card, "Edit details")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    reply_time = reference + timedelta(days=1)
    preview = (await service.message(1, 3, "Move to tomorrow 8pm–10pm", reply_time))[0]
    assert "6 Oct 2026 (Tuesday)" in preview.text
    assert next(iter(repo.events.values())).start_at.day == 9
    assert service.extractor.calls[-1][1] == reply_time
    await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert next(iter(repo.events.values())).start_at.day == 6


async def test_confirmation_rechecks_new_conflicts(service_factory, reference):
    service, repo = service_factory(
        extraction(),
        extraction(title="Movie", start_time_text="9pm", end_time_text="11pm"),
        extraction(
            kind="update",
            date_text=None,
            start_time_text="8pm",
            end_time_text=None,
            match_title="CCA dinner",
        ),
    )
    await onboard(service, reference)
    first = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, first.keyboard.inline_keyboard[0][0].callback_data)
    second = (await service.message(1, 3, "Movie Friday 9pm–11pm at Vivo", reference))[0]
    preview = (await service.message(1, 4, "CCA dinner moved to 8pm", reference))[0]
    await service.callback(1, second.keyboard.inline_keyboard[0][0].callback_data)
    old_button = preview.keyboard.inline_keyboard[0][0].callback_data
    refreshed = await service.callback(1, old_button)
    assert "availability changed" in refreshed[0].text
    assert "Movie" in refreshed[1].text
    assert list(repo.events.values())[0].start_at.hour == 19
    new_button = refreshed[1].keyboard.inline_keyboard[0][0].callback_data
    assert new_button != old_button
    assert "expired" in (await service.callback(1, old_button))[0].text
    result = await service.callback(1, new_button)
    assert "Event updated" in result[0].text
    assert list(repo.events.values())[0].start_at.hour == 20


async def test_existing_disclosed_clash_does_not_loop(service_factory, reference):
    service, repo = service_factory(
        extraction(),
        extraction(
            kind="update",
            date_text=None,
            start_time_text="8pm",
            end_time_text=None,
            match_title="dinner",
        ),
    )
    await onboard(service, reference)
    first = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    from app.models.timetable import BusyBlock

    event = next(iter(repo.events.values()))
    repo.blocks[event.user_id] = [
        BusyBlock(title="Exam", start_at=event.start_at, end_at=event.end_at)
    ]
    preview = (await service.message(1, 3, "Dinner moved to 8pm", reference))[0]
    assert "Exam" in preview.text
    result = await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert result[0].text == "Event updated."
    assert first.keyboard is not None


@pytest.mark.parametrize("raw", ["2026-02-30", "2026-13-10", "31/04/2026", "29 Feb 2026"])
def test_invalid_dates_become_clarifications(raw, reference):
    with pytest.raises(Clarification, match="valid event date"):
        resolve_date(raw, reference)


async def test_invalid_date_keeps_pending_and_can_be_corrected(service_factory, reference):
    service, repo = service_factory(
        extraction(date_text="2026-02-30"), extraction(date_text="2026-10-09")
    )
    await onboard(service, reference)
    question = (await service.message(1, 2, "CCA dinner 2026-02-30 7–9pm at Vivo", reference))[0]
    assert "valid event date" in question.text
    assert not repo.events and (await repo.ensure_user(1)).pending is not None
    card = (await service.message(1, 3, "2026-10-09", reference))[0]
    assert "No clashes found" in card.text
    assert len(repo.events) == 1
    assert (await repo.ensure_user(1)).pending is None


async def test_expired_confirmation_never_modifies_event(service_factory, reference):
    service, repo = service_factory(
        extraction(),
        extraction(
            kind="cancel",
            date_text=None,
            start_time_text=None,
            end_time_text=None,
            match_title="dinner",
        ),
    )
    await onboard(service, reference)
    await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference)
    preview = (await service.message(1, 3, "Dinner cancelled", reference))[0]
    user = await repo.ensure_user(1)
    await repo.set_pending(user.id, None)  # Cleanup expires persisted pending state.
    result = await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert "expired" in result[0].text
    assert next(iter(repo.events.values())).status == Status.PREVIEW


@pytest.mark.parametrize(
    "reference,year",
    [
        (datetime(2026, 1, 1, tzinfo=SG), "2025-2026"),
        (datetime(2026, 7, 31, 23, 59, tzinfo=SG), "2025-2026"),
        (datetime(2026, 8, 1, tzinfo=SG), "2026-2027"),
        (datetime(2027, 8, 1, tzinfo=SG), "2027-2028"),
    ],
)
def test_shared_academic_year_rule(reference, year):
    assert academic_year_for(reference) == year
