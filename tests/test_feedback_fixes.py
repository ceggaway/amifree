"""Regression tests for the tester feedback list (six items, plus the block follow-up)."""

import pytest

from app.schemas.event import Status
from tests.conftest import extraction, onboard, tap

TEXT = "CCA dinner Friday 7–9pm at Vivo"
# What the extractor hands back for an edit reply that changed nothing (Fri 9 Oct, 19:00-21:00).
UNCHANGED = dict(
    date_text="2026-10-09",
    start_time_text="19:00",
    end_time_text="21:00",
    end_date_text="2026-10-09",
    location="Vivo",
)


async def going_dinner(service, reference):
    await onboard(service, reference)
    card = (await service.message(1, 2, TEXT, reference))[0]
    return await service.callback(1, tap(card, "Add to plans"))


# 1. Changing only the venue ------------------------------------------------------------


async def test_a_bare_venue_reply_changes_the_location(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(**UNCHANGED))
    confirmed = await going_dinner(service, reference)
    menu = (await service.callback(1, tap(confirmed[0], "Edit details")))[0]
    await service.callback(1, tap(menu, "Venue"))
    preview = (await service.message(1, 3, "clb lvl 3", reference))[0]
    assert "📍 clb lvl 3" in preview.text
    assert preview.parse_mode == "HTML"
    assert next(iter(repo.events.values())).location == "clb lvl 3"


async def test_a_time_like_reply_is_not_taken_as_a_venue(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(**UNCHANGED))
    confirmed = await going_dinner(service, reference)
    menu = (await service.callback(1, tap(confirmed[0], "Edit details")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    reply = (await service.message(1, 3, "8pm", reference))[0]
    # Nothing changed and it is not a place: ask again, never save "8pm" as the venue.
    assert "couldn't tell what to change" in reply.text and "Location: 8pm" not in reply.text


# 2. Typing a date for /free ------------------------------------------------------------


async def test_free_accepts_a_typed_date_after_the_prompt(service_factory, reference):
    service, repo = service_factory()
    prompt = (await service.command(1, "free"))[0]
    assert "type one" in prompt.text
    reply = (await service.message(1, 5, "15 oct", reference))[0]
    assert reply.text == "🟢 Thu 15 Oct: nothing booked, free all day."
    assert not (await repo.ensure_user(1)).pending
    # Other spellings work too.
    for typed, expected in (("oct 15", "Thu 15 Oct"), ("Fri", "Fri 9 Oct")):
        await service.command(1, "free")
        assert expected in (await service.message(1, 6, typed, reference))[0].text


async def test_free_accepts_a_week_and_a_time(service_factory, reference):
    service, _ = service_factory()
    await service.command(1, "free")
    week = (await service.message(1, 5, "next week", reference))[0].text
    assert week.count("free all day") == 7 and "Mon 5 Oct" in week and "Sun 11 Oct" in week
    await service.command(1, "free")
    slot = (await service.message(1, 6, "fri 7-9pm", reference))[0].text
    assert slot.startswith("✅ No clashes found")


async def test_a_reply_that_is_not_a_date_still_works_as_a_message(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    await service.command(1, "free")
    # A forwarded invitation is not an answer to "when?": it becomes an event card.
    card = (
        await service.message(1, 7, "CCA dinner Friday 7–9pm at Vivo\nBring a friend", reference)
    )[0]
    assert "No clashes found" in card.text and len(repo.events) == 1
    assert not (await repo.ensure_user(1)).pending
    # A busy block typed while the question is open is a block.
    await service.command(1, "free")
    block = (await service.message(1, 8, "busy sat 2-6pm", reference))[0]
    assert block.text.startswith("Block this time?")


async def test_an_unclear_answer_gets_the_examples_and_never_blocks_later_messages(
    service_factory, reference
):
    service, repo = service_factory(extraction(kind="unknown"), extraction())
    await onboard(service, reference)
    await service.command(1, "free")
    reply = (await service.message(1, 5, "someday", reference))[0]
    assert "couldn't find an event" in reply.text
    # The question is closed: the next message is an ordinary one.
    assert not (await repo.ensure_user(1)).pending
    card = (await service.message(1, 6, TEXT, reference))[0]
    assert "No clashes found" in card.text


async def test_a_short_plan_sent_after_free_is_a_plan_not_a_date_error(service_factory, reference):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    await service.command(1, "free")
    card = (await service.message(1, 5, "Dinner fri 7-9pm", reference))[0]
    assert "No clashes found" in card.text and len(repo.events) == 1


# 3. End time answered in 24-hour (or am/pm) time -----------------------------------------


async def test_a_bare_time_answers_the_end_time_question(service_factory, reference):
    # The extractor would lose "8PM"; the answer must not depend on it.
    service, repo = service_factory(extraction(end_time_text=None), extraction(kind="unknown"))
    await onboard(service, reference)
    ask = (await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference))[0]
    assert ask.text.startswith("What time does this event end?")
    card = (await service.message(1, 3, "2000", reference))[0]
    assert "No clashes found" in card.text
    event = next(iter(repo.events.values()))
    assert (event.start_at.hour, event.end_at.hour) == (19, 20)
    assert len(service.extractor.calls) == 1  # the answer never reached the LLM


async def test_am_pm_answers_work_too_and_an_unclear_one_is_asked_again(service_factory, reference):
    # The second extraction is what the model makes of an ambiguous "5".
    service, repo = service_factory(
        extraction(end_time_text=None),
        extraction(end_time_text="5"),
        extraction(end_time_text="9PM"),
    )
    await onboard(service, reference)
    await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference)
    # "5" could not follow a 7pm start either way, so it is asked about rather than guessed.
    unclear = (await service.message(1, 3, "5", reference))[0]
    assert "AM or PM" in unclear.text and (await repo.ensure_user(1)).pending
    card = (await service.message(1, 4, "9PM", reference))[0]
    assert "No clashes found" in card.text
    assert next(iter(repo.events.values())).end_at.hour == 21


@pytest.mark.parametrize(
    ("answer", "hours"),
    [
        ("pm", (18, 20)),
        ("PM", (18, 20)),
        ("p.m.", (18, 20)),
        ("evening", (18, 20)),
        ("am", (6, 8)),
        ("6pm", (18, 20)),
        ("6-8pm", (18, 20)),
    ],
)
async def test_a_bare_am_or_pm_answers_the_am_pm_question(
    service_factory, reference, answer, hours
):
    # "today 6-8 cca practice": the model can only copy the bare 6 and 8 again, so a reply
    # of just "pm" used to be sent back to it and the same question came back for ever.
    service, repo = service_factory(
        extraction(title="CCA practice", date_text="today", start_time_text="6", end_time_text="8")
    )
    await onboard(service, reference)
    asked = (await service.message(1, 2, "today 6-8 cca practice", reference))[0]
    assert "AM or PM" in asked.text
    card = (await service.message(1, 3, answer, reference))[0]
    assert "AM or PM" not in card.text
    event = next(iter(repo.events.values()))
    assert (event.start_at.hour, event.end_at.hour) == hours
    assert len(service.extractor.calls) == 1  # the answer never reached the LLM


# 4. /free output -----------------------------------------------------------------------


async def test_free_day_message_has_no_checked_footer(service_factory, reference):
    service, _ = service_factory(extraction())
    assert (await service.command(1, "free", "tomorrow"))[0].text == (
        "🟢 Mon 5 Oct: nothing booked, free all day."
    )
    await going_dinner(service, reference)
    text = (await service.command(1, "free", "fri"))[0].text
    assert "Checked" not in text
    assert text.startswith("📅 Fri 9 Oct\nBusy:\n• 7–9pm CCA dinner (Going)")


# 5 and 6. Blocks that sit on top of plans ----------------------------------------------


async def test_a_block_over_a_plan_warns_before_and_after_saving(service_factory, reference):
    service, repo = service_factory(extraction())
    await going_dinner(service, reference)
    preview = (await service.message(1, 3, "busy fri 6pm-8pm", reference))[0]
    assert preview.text.startswith("Block this time?")
    assert "SGT" in preview.text and "Singapore" not in preview.text
    assert "⚠️ This clashes with your plans:\n• CCA dinner (Going, Fri 9 Oct 7–9pm)" in preview.text
    saved = (await service.callback(1, tap(preview, "Save block")))[0]
    assert saved.text.startswith("Blocked:") and "CCA dinner (Going" in saved.text
    event = next(iter(repo.events.values()))
    assert event.status == Status.GOING


async def test_a_block_with_no_overlap_stays_quiet(service_factory, reference):
    service, _ = service_factory(extraction())
    await going_dinner(service, reference)
    preview = (await service.message(1, 3, "busy sat 2-6pm", reference))[0]
    assert "clashes" not in preview.text
    saved = (await service.callback(1, tap(preview, "Save block")))[0]
    assert saved.text.startswith("Blocked:") and "clashes" not in saved.text


async def test_a_block_time_can_be_sent_in_a_second_message(service_factory, reference):
    service, _ = service_factory(extraction())
    await going_dinner(service, reference)
    ask = (await service.message(1, 3, "busy 9 oct 18:30", reference))[0]
    assert ask.text.startswith("What time is this block?")
    preview = (await service.message(1, 4, "18:30-19:30", reference))[0]
    assert preview.text.startswith("Block this time?")
    assert "18:30 → 19:30 SGT" in preview.text
    assert "CCA dinner" in preview.text  # the 7pm dinner starts inside the block


async def test_a_single_end_time_completes_a_half_typed_block(service_factory, reference):
    service, _ = service_factory()
    await service.message(1, 3, "busy 9 oct 18:30", reference)
    preview = (await service.message(1, 4, "19:30", reference))[0]
    assert preview.text.startswith("Block this time?") and "18:30 → 19:30 SGT" in preview.text


async def test_an_unrelated_message_is_not_glued_onto_a_half_typed_block(
    service_factory, reference
):
    service, repo = service_factory(extraction())
    await onboard(service, reference)
    ask = (await service.message(1, 3, "busy sat", reference))[0]
    assert ask.text.startswith("What time is this block?")
    card = (await service.message(1, 4, TEXT, reference))[0]
    # A plan with its own time is a plan: no block titled after it, and the question is closed.
    assert "No clashes found" in card.text and "Block this time?" not in card.text
    assert not await repo.list_blocks((await repo.ensure_user(1)).id)
    assert not (await repo.ensure_user(1)).pending


async def test_a_bare_end_hour_is_read_as_the_first_time_after_the_start(
    service_factory, reference
):
    service, repo = service_factory(extraction(start_time_text="7pm", end_time_text=None))
    await onboard(service, reference)
    await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference)
    card = (await service.message(1, 3, "9", reference))[0]
    assert "No clashes found" in card.text
    event = next(iter(repo.events.values()))
    assert (event.start_at.hour, event.end_at.hour) == (19, 21)


async def test_small_talk_is_not_saved_as_a_venue(service_factory, reference):
    service, _ = service_factory(extraction(), extraction(**UNCHANGED))
    confirmed = await going_dinner(service, reference)
    menu = (await service.callback(1, tap(confirmed[0], "Edit details")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    preview = (await service.message(1, 3, "ok thanks", reference))[0]
    assert "Location: ok" not in preview.text
