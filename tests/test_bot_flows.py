from datetime import timedelta

import pytest

from app.models.timetable import BusyBlock
from app.schemas.event import Status
from app.services.scheduling import SchedulingService
from tests.conftest import extraction, onboard

TEXT = "CCA dinner Friday 7–9pm at Vivo"


async def test_complete_mvp_and_user_isolation(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(), extraction())
    await onboard(service, reference)
    first = (await service.message(1, 2, TEXT, reference))[0]
    assert "You're free" in first.text
    button = first.keyboard.inline_keyboard[0][0]
    committed = await service.callback(1, button.callback_data)
    assert "Added" in committed[0].text
    second = (await service.message(1, 3, TEXT, reference))[0]
    assert "Clash detected" in second.text
    assert second.keyboard.inline_keyboard[0][0].text == "Going anyway"
    await onboard(service, reference, 2)
    third = (await service.message(2, 2, TEXT, reference))[0]
    assert "You're free" in third.text
    unauthorised = await service.callback(2, button.callback_data)
    assert "isn't available to you" in unauthorised[0].text
    assert len(repo.events) == 3


async def test_timetable_clash(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    user = await repo.ensure_user(1)
    from app.services.date_resolution import validate_extraction

    draft = validate_extraction(extraction(), TEXT, reference)
    repo.blocks[user.id] = [
        BusyBlock(
            title="CS3216 Lecture",
            start_at=draft.start_at + timedelta(hours=1),
            end_at=draft.end_at,
        )
    ]
    reply = (await service.message(1, 2, TEXT, reference))[0]
    assert "Clash" in reply.text and "CS3216 Lecture" in reply.text


async def test_clarification_survives_service_restart(service_factory, reference):
    service, repo = service_factory(
        extraction(start_time_text=None, end_time_text=None), extraction()
    )
    await onboard(service, reference)
    reply = (await service.message(1, 2, "CCA dinner Friday at Vivo", reference))[0]
    assert "start" in reply.text
    assert not repo.events
    restarted = SchedulingService(repo, service.extractor, service.importer, service.settings)
    card = (await restarted.message(1, 3, "7–9pm", reference + timedelta(days=10)))[0]
    assert "09 Oct 2026" in card.text
    assert "You're free" in card.text
    assert service.extractor.calls[-1][1] == reference
    assert service.extractor.calls[-1][2] is not None


async def test_repeat_callback_and_duplicate_message(service_factory, reference):
    service, repo = service_factory(extraction(), extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    action = card.keyboard.inline_keyboard[0][0].callback_data
    await service.callback(1, action)
    assert "outdated" in (await service.callback(1, action))[0].text
    await service.message(1, 2, TEXT, reference)
    assert len(repo.events) == 1
    assert len([a for a in repo.actions if a[1] == "going"]) == 1


@pytest.mark.parametrize("row,col,status", [(0, 1, Status.MAYBE), (1, 0, Status.DECLINED)])
async def test_non_going_does_not_block(service_factory, reference, row, col, status):
    service, repo = service_factory(extraction(), extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    await service.callback(1, card.keyboard.inline_keyboard[row][col].callback_data)
    assert next(iter(repo.events.values())).status == status
    assert "You're free" in (await service.message(1, 3, TEXT, reference))[0].text


async def test_availability_changes_before_going(service_factory, reference):
    service, repo = service_factory(extraction(), extraction())
    await onboard(service, reference)
    first = (await service.message(1, 2, TEXT, reference))[0]
    second = (await service.message(1, 3, TEXT, reference))[0]
    await service.callback(1, second.keyboard.inline_keyboard[0][0].callback_data)
    result = await service.callback(1, first.keyboard.inline_keyboard[0][0].callback_data)
    assert "availability changed" in result[0].text
    assert list(repo.events.values())[0].status == Status.PREVIEW
    await service.callback(1, result[1].keyboard.inline_keyboard[0][0].callback_data)
    assert all(e.status == Status.GOING for e in repo.events.values())


async def test_update_requires_confirmation(service_factory, reference):
    moved = extraction(
        kind="update",
        title="CCA dinner",
        match_title="dinner",
        date_text=None,
        start_time_text="8pm",
        end_time_text=None,
        location=None,
    )
    service, repo = service_factory(extraction(), moved)
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    await service.callback(1, card.keyboard.inline_keyboard[0][0].callback_data)
    preview = (await service.message(1, 3, "CCA dinner moved to 8pm", reference))[0]
    e = next(iter(repo.events.values()))
    assert e.start_at.hour == 19
    assert "Before:" in preview.text and "After:" in preview.text
    await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert e.start_at.hour == 20 and e.end_at.hour == 22
    assert e.status == Status.GOING
    assert len(repo.events) == 1


async def test_ambiguous_update_asks_for_selection(service_factory, reference):
    moved = extraction(
        kind="update",
        match_title="dinner",
        date_text=None,
        start_time_text="8pm",
        end_time_text=None,
    )
    service, repo = service_factory(extraction(), extraction(), moved)
    await onboard(service, reference)
    await service.message(1, 2, TEXT, reference)
    await service.message(1, 3, TEXT, reference)
    reply = (await service.message(1, 4, "Dinner moved to 8pm", reference))[0]
    assert "Which event" in reply.text and len(reply.keyboard.inline_keyboard) == 2
    assert all(e.start_at.hour == 19 for e in repo.events.values())
    chosen = (await service.callback(1, reply.keyboard.inline_keyboard[0][0].callback_data))[0]
    assert "Review" in chosen.text


async def test_cancel_event_and_delete_data(service_factory, reference):
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
    await service.message(1, 2, TEXT, reference)
    reply = (await service.message(1, 3, "CCA dinner cancelled", reference))[0]
    assert next(iter(repo.events.values())).status == Status.PREVIEW
    await service.callback(1, reply.keyboard.inline_keyboard[0][0].callback_data)
    assert next(iter(repo.events.values())).status == Status.CANCELLED
    delete = (await service.command(1, "delete"))[0]
    assert len(repo.users) == 1
    await service.callback(1, delete.keyboard.inline_keyboard[0][0].callback_data)
    assert not repo.users and not repo.events and not repo.actions and not repo.blocks


async def test_edit_keeps_commitment_until_confirmed(service_factory, reference):
    corrected = extraction(
        date_text="2026-10-09",
        start_time_text="20:00",
        end_time_text="22:00",
        end_date_text="2026-10-09",
    )
    service, repo = service_factory(extraction(), corrected)
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    confirmed = await service.callback(1, card.keyboard.inline_keyboard[0][0].callback_data)
    await service.callback(1, confirmed[1].keyboard.inline_keyboard[1][1].callback_data)
    preview = (await service.message(1, 3, "20:00–22:00", reference))[0]
    e = next(iter(repo.events.values()))
    assert e.status == Status.GOING and e.start_at.hour == 19
    await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert e.start_at.hour == 20


async def test_clear_draft_and_unknown_message(service_factory, reference):
    service, repo = service_factory(extraction(date_text=None), extraction(kind="unknown"))
    await onboard(service, reference)
    await service.message(1, 2, "CCA dinner 7–9pm at Vivo", reference)
    assert (await repo.ensure_user(1)).pending
    await service.command(1, "cancel")
    assert not (await repo.ensure_user(1)).pending
    assert "couldn't find an event" in (await service.message(1, 3, "hello", reference))[0].text


async def test_not_onboarded_blocks_extraction(service_factory, reference):
    service, repo = service_factory()
    assert "first" in (await service.message(1, 2, TEXT, reference))[0].text
    assert not service.extractor.calls
