"""Regression tests for the tester feedback list (six items, plus the block follow-up)."""

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
    return await service.callback(1, tap(card, "Going"))


# 1. Changing only the venue ------------------------------------------------------------


async def test_a_bare_venue_reply_changes_the_location(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(**UNCHANGED))
    confirmed = await going_dinner(service, reference)
    await service.callback(1, tap(confirmed[0], "Edit"))
    preview = (await service.message(1, 3, "clb lvl 3", reference))[0]
    assert "Location: Vivo" in preview.text.split("After:")[0]
    assert "Location: clb lvl 3" in preview.text.split("After:")[1]
    await service.callback(1, tap(preview, "Confirm update"))
    assert next(iter(repo.events.values())).location == "clb lvl 3"


async def test_a_time_like_reply_is_not_taken_as_a_venue(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(**UNCHANGED))
    confirmed = await going_dinner(service, reference)
    await service.callback(1, tap(confirmed[0], "Edit"))
    preview = (await service.message(1, 3, "8pm", reference))[0]
    assert "Location: Vivo" in preview.text.split("After:")[1]


# 2. Typing a date for /free ------------------------------------------------------------


async def test_free_accepts_a_typed_date_after_the_prompt(service_factory, reference):
    service, repo = service_factory()
    prompt = (await service.command(1, "free"))[0]
    assert "type a date" in prompt.text
    reply = (await service.message(1, 5, "15 oct", reference))[0]
    assert reply.text == "🟢 Free all day on Thu 15 Oct."
    assert not (await repo.ensure_user(1)).pending
    # Other spellings work too.
    for typed, expected in (("oct 15", "Thu 15 Oct"), ("Fri", "Fri 9 Oct")):
        await service.command(1, "free")
        assert expected in (await service.message(1, 6, typed, reference))[0].text


async def test_free_accepts_a_week_and_a_time(service_factory, reference):
    service, _ = service_factory()
    await service.command(1, "free")
    week = (await service.message(1, 5, "next week", reference))[0].text
    assert week.count("Free all day") == 7 and "Mon 5 Oct" in week and "Sun 11 Oct" in week
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


async def test_an_unclear_date_is_asked_again_with_examples(service_factory, reference):
    service, repo = service_factory()
    await service.command(1, "free")
    reply = (await service.message(1, 5, "someday", reference))[0]
    assert "For example: /free fri 7-9pm" in reply.text
    assert (await repo.ensure_user(1)).pending["stage"] == "free"


# 3. End time answered in 24-hour (or am/pm) time -----------------------------------------


async def test_a_bare_time_answers_the_end_time_question(service_factory, reference):
    # The extractor would lose "8PM"; the answer must not depend on it.
    service, repo = service_factory(extraction(end_time_text=None), extraction(kind="unknown"))
    await onboard(service, reference)
    ask = (await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference))[0]
    assert ask.text.startswith(
        "What time does this event end? Please type in 24-hour time (eg. 07:00, 18:00)."
    )
    card = (await service.message(1, 3, "2000", reference))[0]
    assert "No clashes found" in card.text
    event = next(iter(repo.events.values()))
    assert (event.start_at.hour, event.end_at.hour) == (19, 20)
    assert len(service.extractor.calls) == 1  # the answer never reached the LLM


async def test_am_pm_answers_work_too_and_an_unclear_one_is_asked_again(service_factory, reference):
    service, repo = service_factory(extraction(end_time_text=None))
    await onboard(service, reference)
    await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference)
    unclear = (await service.message(1, 3, "9", reference))[0]
    assert "AM or PM" in unclear.text and (await repo.ensure_user(1)).pending
    card = (await service.message(1, 4, "9PM", reference))[0]
    assert "No clashes found" in card.text
    assert next(iter(repo.events.values())).end_at.hour == 21


# 4. /free output -----------------------------------------------------------------------


async def test_free_day_message_has_no_checked_footer(service_factory, reference):
    service, _ = service_factory(extraction())
    assert (await service.command(1, "free", "tomorrow"))[0].text == "🟢 Free all day on Mon 5 Oct."
    await going_dinner(service, reference)
    text = (await service.command(1, "free", "fri"))[0].text
    assert "Checked" not in text
    assert text.startswith("🔴 Busy on Fri 9 Oct:\n• 7pm – 9pm CCA dinner")


# 5 and 6. Blocks that sit on top of plans ----------------------------------------------


async def test_a_block_over_a_plan_warns_before_and_after_saving(service_factory, reference):
    service, repo = service_factory(extraction())
    await going_dinner(service, reference)
    preview = (await service.message(1, 3, "busy fri 6pm-8pm", reference))[0]
    assert preview.text.startswith("Block this time?")
    assert "SGT" in preview.text and "Singapore" not in preview.text
    assert "⚠️ This overlaps your plans:\n• CCA dinner (Fri 9 Oct, 7–9pm, Going)" in preview.text
    saved = (await service.callback(1, tap(preview, "Save block")))[0]
    assert saved.text.startswith("Blocked. ⚠️ It clashes with your plans:")
    assert "CCA dinner" in saved.text
    event = next(iter(repo.events.values()))
    assert event.status == Status.GOING


async def test_a_block_with_no_overlap_stays_quiet(service_factory, reference):
    service, _ = service_factory(extraction())
    await going_dinner(service, reference)
    preview = (await service.message(1, 3, "busy sat 2-6pm", reference))[0]
    assert "overlaps" not in preview.text
    saved = (await service.callback(1, tap(preview, "Save block")))[0]
    assert saved.text.startswith("Blocked. Plans that overlap it will show a clash.")


async def test_a_block_time_can_be_sent_in_a_second_message(service_factory, reference):
    service, _ = service_factory(extraction())
    await going_dinner(service, reference)
    ask = (await service.message(1, 3, "busy 9 oct 18:30", reference))[0]
    assert ask.text.startswith("What time is this block?")
    preview = (await service.message(1, 4, "18:30-19:30", reference))[0]
    assert preview.text.startswith("Block this time?")
    assert "18:30 → 19:30 SGT" in preview.text
    assert "CCA dinner" in preview.text  # the 7pm dinner starts inside the block
