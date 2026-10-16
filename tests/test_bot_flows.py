from datetime import timedelta

import pytest

from app.models.timetable import BusyBlock
from app.schemas.event import Status
from app.services.scheduling import SchedulingService
from tests.conftest import extraction, onboard, tap

TEXT = "CCA dinner Friday 7–9pm at Vivo"


async def test_complete_mvp_and_user_isolation(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(), extraction())
    await onboard(service, reference)
    first = (await service.message(1, 2, TEXT, reference))[0]
    assert "No clashes found" in first.text
    button = first.keyboard.inline_keyboard[0][0]
    committed = await service.callback(1, button.callback_data)
    assert "Added" in committed[0].text
    second = (await service.message(1, 3, TEXT, reference))[0]
    assert "Clashes with" in second.text
    assert second.keyboard.inline_keyboard[0][0].text == "Going anyway"
    await onboard(service, reference, 2)
    third = (await service.message(2, 2, TEXT, reference))[0]
    assert "No clashes found" in third.text
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
    assert "No clashes found" in card.text
    assert len(service.extractor.calls) == 1  # "7–9pm" answers the question without the AI


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
    assert "No clashes found" in (await service.message(1, 3, TEXT, reference))[0].text


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
    await service.callback(1, tap(confirmed[0], "Edit"))
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


async def test_new_user_saves_without_nusmods(service_factory, reference):
    service, repo = service_factory(extraction())
    assert (
        "paste your NUSMods or personal calendar link"
        in (await service.command(1, "start"))[0].text
    )
    card = (await service.message(1, 2, TEXT, reference))[0]
    assert card.text.startswith("✅ No clashes found")
    assert "No NUSMods timetable covers this date" in card.text
    assert "No upcoming plans" in (await service.command(1, "plans"))[0].text
    save = card.keyboard.inline_keyboard[2][0]
    assert save.text == "Save"
    saved = await service.callback(1, save.callback_data)
    assert len(saved) == 1 and saved[0].edit  # the tapped card is updated in place
    assert "Saved (not decided)" in saved[0].text and "Find it any time" in saved[0].text
    event = next(iter(repo.events.values()))
    assert event.status == Status.SAVED
    assert "CCA dinner" in (await service.command(1, "plans"))[0].text
    # Going turns the saved plan into a commitment that blocks later checks.
    await service.callback(1, tap(saved[0], "Going"))
    assert event.status == Status.GOING


async def test_overlapping_saved_plans_ask_which_to_attend(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(title="Hall supper"))
    first = (await service.message(1, 2, TEXT, reference))[0]
    await service.callback(1, first.keyboard.inline_keyboard[2][0].callback_data)
    second = (await service.message(1, 3, "Hall supper Friday 7–9pm", reference))[0]
    # A saved plan is undecided, so it is reported without being a clash.
    assert second.text.startswith("✅ No clashes found")
    assert "Possible overlap with CCA dinner. Which will you attend?" in second.text
    choices = [row[0] for row in second.keyboard.inline_keyboard[3:]]
    assert [b.text for b in choices] == ["Attend this one", "Attend CCA dinner"]
    await service.callback(1, choices[1].callback_data)
    dinner, supper = repo.events.values()
    assert dinner.status == Status.GOING
    assert supper.status == Status.PREVIEW and len(repo.events) == 2


async def test_busy_day_warning_is_not_a_clash(service_factory, reference):
    plans = [
        extraction(title=f"Plan {n}", start_time_text=f"{hour}:00", end_time_text=f"{hour + 1}:00")
        for n, hour in enumerate((9, 11, 13, 15))
    ]
    service, repo = service_factory(*plans)
    for n, hour in enumerate((9, 11, 13)):
        card = (await service.message(1, n + 2, f"Plan Friday {hour}:00 {hour + 1}:00", reference))[
            0
        ]
        await service.callback(1, card.keyboard.inline_keyboard[0][0].callback_data)
    fourth = (await service.message(1, 9, "Plan Friday 15:00 16:00", reference))[0]
    assert fourth.text.startswith("ℹ️ Busy day")
    assert fourth.keyboard.inline_keyboard[0][0].text == "Going"


async def test_plans_excludes_previews_and_declined(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(title="Other"))
    await service.message(1, 2, TEXT, reference)
    declined = (await service.message(1, 3, "Other Friday 7–9pm", reference))[0]
    await service.callback(1, declined.keyboard.inline_keyboard[1][0].callback_data)
    assert "No upcoming plans" in (await service.command(1, "plans"))[0].text
    assert len(await service.command(1, "events")) == 2


async def test_card_buttons_follow_the_plans_status(service_factory, reference):
    """Regression: a Going card offered Going again, and Save, which downgraded it."""
    service, repo = service_factory(extraction())

    def labels(reply):
        return [b.text for row in reply.keyboard.inline_keyboard for b in row]

    card = (await service.message(1, 2, TEXT, reference))[0]
    assert labels(card) == ["Going", "Maybe", "Can't make it", "Edit", "Save"]
    saved = await service.callback(1, tap(card, "Save"))
    cal = ["📅 Google Calendar", "🍎 Apple Calendar"]
    assert labels(saved[0]) == ["Going", "Maybe", "Can't make it", "Edit", *cal]
    going = await service.callback(1, tap(saved[0], "Going"))
    assert labels(going[0]) == ["Maybe", "Can't make it", "Edit", *cal]
    assert next(iter(repo.events.values())).status == Status.GOING
    maybe = await service.callback(1, tap(going[0], "Maybe"))
    assert labels(maybe[0]) == ["Going", "Can't make it", "Edit"]  # no calendar: not committed
