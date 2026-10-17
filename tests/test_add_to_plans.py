"""The individual card: Add to plans / Skip / Remove. Group cards keep Going / Maybe / Can't."""

from app.schemas.event import Status
from tests.conftest import extraction, onboard, tap

TEXT = "CCA dinner Friday 7–9pm at Vivo"


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row]


async def test_preview_offers_add_and_skip(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    assert labels(card) == ["Add to plans", "Skip", "Edit"]
    # Skip takes it out of plans without blocking time; the card then offers to add it back.
    skipped = (await service.callback(1, tap(card, "Skip")))[0]
    assert "Skipped" in skipped.text and "Not in your plans" in skipped.text
    assert labels(skipped) == ["Add to plans", "Edit"]
    assert next(iter(repo.events.values())).status == Status.DECLINED


async def test_add_then_remove(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    added = (await service.callback(1, tap(card, "Add to plans")))[0]
    assert "Added to your plans" in added.text
    assert next(iter(repo.events.values())).status == Status.GOING
    assert "Edit" in labels(added) and "Remove" in labels(added)
    removed = (await service.callback(1, tap(added, "Remove")))[0]
    assert "Removed from your plans" in removed.text
    assert next(iter(repo.events.values())).status == Status.DECLINED


async def test_a_clash_shows_add_anyway(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(title="Supper"))
    await onboard(service, reference)
    first = (await service.message(1, 2, TEXT, reference))[0]
    await service.callback(1, tap(first, "Add to plans"))
    second = (await service.message(1, 3, "Supper Friday 7–9pm at Vivo", reference))[0]
    assert "Add anyway" in labels(second) and "Add to plans" not in labels(second)
