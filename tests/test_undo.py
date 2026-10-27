"""Remove is one tap, and Undo puts it back: plans, deadlines, reminders and busy blocks."""

from datetime import timedelta

from app.schemas.event import Status
from tests.conftest import extraction, onboard, tap


async def test_removing_a_plan_can_be_added_back(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    added = (await service.callback(1, tap(card, "Add to plans")))[0]
    removed = (await service.callback(1, tap(added, "Remove from plans")))[0]
    assert "Not in your plans" in removed.text and removed.edit
    assert [b.text for row in removed.keyboard.inline_keyboard for b in row] == [
        "Add to plans",
        "Edit details",
    ]
    back = (await service.callback(1, tap(removed, "Add to plans")))[0]
    assert "Added to /plans" in back.text
    assert next(iter(repo.events.values())).status == Status.GOING
    assert [b.text for row in back.keyboard.inline_keyboard for b in row][:2] == [
        "Edit details",
        "Remove from plans",
    ]


async def test_skipping_a_preview_has_no_undo_button(service_factory, reference):
    service, _ = service_factory(extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    skipped = (await service.callback(1, tap(card, "Skip")))[0]
    assert "Not in your plans" in skipped.text
    assert "↩️ Undo" not in [b.text for row in skipped.keyboard.inline_keyboard for b in row]


async def test_removed_deadline_comes_back(service_factory, reference):
    service, repo = service_factory(
        extraction(
            title="CS3244 report",
            date_text="Fri",
            start_time_text="11:59pm",
            end_time_text=None,
            location=None,
            kind="deadline",
        )
    )
    await service.command(1, "deadline", "CS3244 report due Fri 11:59pm")
    listed = (await service.command(1, "deadline"))[0]
    card = (await service.callback(1, tap(listed, "1")))[0]
    gone = (await service.callback(1, tap(card, "🗑 Remove")))[0]
    assert gone.text == "🗑 Removed: CS3244 report" and not repo.deadlines
    back = (await service.callback(1, tap(gone, "↩️ Undo")))[0]
    assert back.text.startswith("↩️ Restored.\n📌 CS3244 report") and len(repo.deadlines) == 1
    assert [b.text for row in back.keyboard.inline_keyboard for b in row] == ["✅ Done", "🗑 Remove"]


async def test_removed_reminder_comes_back(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    card = (await service.message(1, 2, "remind me at 5pm to buy milk", reference))[0]
    gone = (await service.callback(1, tap(card, "🗑 Remove")))[0]
    assert not repo.reminders
    back = (await service.callback(1, tap(gone, "↩️ Undo")))[0]
    assert back.text == "↩️ Restored. I'll remind you today at 5pm: Buy milk"
    assert len(repo.reminders) == 1


async def test_removed_busy_block_comes_back(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    block = (await service.message(1, 5, "work every wed 6-10pm", reference))[0]
    await service.callback(1, tap(block, "Save block"))
    (rule,) = repo.rules[next(iter(repo.users.values())).id]
    gone = (await service.callback(1, f"unblock:{rule.id}"))[0]
    assert gone.text == "Busy block removed." and not any(repo.rules.values())
    back = (await service.callback(1, tap(gone, "↩️ Undo")))[0]
    assert back.text.startswith("↩️ Restored.") and any(repo.rules.values())


async def test_undo_expires_and_works_once(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    card = (await service.message(1, 2, "remind me at 5pm to buy milk", reference))[0]
    gone = (await service.callback(1, tap(card, "🗑 Remove")))[0]
    await service.callback(1, tap(gone, "↩️ Undo"))
    assert "too late" in (await service.callback(1, tap(gone, "↩️ Undo")))[0].text  # only once

    again = (await service.message(1, 3, "remind me at 6pm to call mum", reference))[0]
    gone = (await service.callback(1, tap(again, "🗑 Remove")))[0]
    service.clock = lambda: reference + timedelta(hours=1)
    assert "too late" in (await service.callback(1, tap(gone, "↩️ Undo")))[0].text
