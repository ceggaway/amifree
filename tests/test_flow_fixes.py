"""Fixes from the October bug report: edits, /free days, end-time answers, blocks."""

from tests.conftest import extraction, onboard, tap

DINNER = "CCA dinner Friday 7–9pm at Vivo"


async def saved_dinner(service, reference):
    card = (await service.message(1, 1, DINNER, reference))[0]
    return (await service.callback(1, tap(card, "Save")))[0]


# ---------- bug 1: changing the venue ----------


async def test_venue_button_sets_the_venue_as_typed(service_factory, reference):
    service, repo = service_factory(extraction())
    card = await saved_dinner(service, reference)
    ask = (await service.callback(1, tap(card, "Edit")))[0]
    assert ask.text.startswith("What do you want to change?")
    prompt = (await service.callback(1, tap(ask, "📍 Venue")))[0]
    assert prompt.text.startswith("Send the new venue.")
    preview = (await service.message(1, 5, "clb lvl 3", reference))[0]
    before, after = preview.text.split("After:")
    assert "Location: Vivo" in before and "Location: clb lvl 3" in after
    await service.callback(1, tap(preview, "Confirm update"))
    assert [e.location for e in repo.events.values()] == ["clb lvl 3"]
    assert len(service.extractor.calls) == 1  # only the original message used the AI


async def test_title_button(service_factory, reference):
    service, repo = service_factory(extraction())
    card = await saved_dinner(service, reference)
    ask = (await service.callback(1, tap(card, "Edit")))[0]
    await service.callback(1, tap(ask, "✏️ Title"))
    preview = (await service.message(1, 5, "Hall dinner", reference))[0]
    assert "After:\nHall dinner" in preview.text


async def test_a_change_that_changes_nothing_asks_again(service_factory, reference):
    same = extraction(date_text="2026-10-09", start_time_text="19:00", end_time_text="21:00")
    service, _ = service_factory(extraction(), same)
    card = await saved_dinner(service, reference)
    await service.callback(1, tap(card, "Edit"))
    reply = (await service.message(1, 5, "clb lvl 3", reference))[0]
    assert reply.text.startswith("I couldn't tell what to change")
    assert tap(reply, "📍 Venue")
    assert "Before:" not in reply.text


# ---------- bug 2: /free for any day ----------


async def test_free_accepts_a_typed_day(service_factory, reference):
    service, _ = service_factory()
    await service.command(1, "free")
    reply = (await service.message(1, 5, "15 oct", reference))[0]
    assert reply.text == "🟢 Thu 15 Oct: nothing booked, free all day."
    # Answered: the next message is an ordinary one again.
    assert (await service.repo.ensure_user(1)).pending is None


async def test_free_typed_time(service_factory, reference):
    service, _ = service_factory()
    await service.command(1, "free")
    reply = (await service.message(1, 5, "sat 2-5pm", reference))[0]
    assert reply.text.startswith("✅ No clashes found · Sat 10 Oct · 2–5pm")


# ---------- bug 3: answering "what time does it end?" ----------


async def test_end_time_answers_in_any_format(service_factory, reference):
    for answer in ("8PM", "2000", "20:00", "ends at 8pm"):
        service, _ = service_factory(extraction(start_time_text="7pm", end_time_text=None))
        await onboard(service, reference)
        ask = (await service.message(1, 2, "CCA dinner Friday 7pm at Vivo", reference))[0]
        assert ask.text.startswith("What time does this event end? For example 9pm or 21:00.")
        card = (await service.message(1, 3, answer, reference))[0]
        assert "19:00 → 09 Oct 20:00 SGT" in card.text, answer
        assert len(service.extractor.calls) == 1, answer


# ---------- bugs 5 and 6: blocks ----------


async def test_block_with_one_time_asks_for_the_range(service_factory, reference):
    service, repo = service_factory()
    ask = (await service.message(1, 5, "busy 14 oct 18:30", reference))[0]
    assert ask.text.startswith("What time is this block?")
    preview = (await service.message(1, 6, "18:30-19:00", reference))[0]
    assert preview.text.startswith("Block this time?")
    assert "Wed 14 Oct" in preview.text or "14 Oct" in preview.text
    assert "SGT" in preview.text and "Singapore" not in preview.text
    done = (await service.callback(1, tap(preview, "Save block")))[0]
    assert done.text.startswith("Blocked:")
    assert len(repo.rules[(await repo.ensure_user(1)).id]) == 1


async def test_block_command_waits_for_the_time(service_factory, reference):
    service, _ = service_factory()
    await service.command(1, "block")
    preview = (await service.message(1, 5, "sat 2-6pm", reference))[0]
    assert preview.text.startswith("Block this time?")


async def test_block_over_a_plan_shows_the_clash_now(service_factory, reference):
    service, _ = service_factory(extraction())
    card = (await service.message(1, 1, DINNER, reference))[0]
    await service.callback(1, tap(card, "Going"))
    preview = (await service.message(1, 5, "busy fri 8-10pm", reference))[0]
    assert "⚠️ This clashes with your plans:\n• CCA dinner (Going, Fri 9 Oct 7–9pm)" in preview.text
    done = (await service.callback(1, tap(preview, "Save block")))[0]
    assert "CCA dinner (Going" in done.text


async def test_weekly_block_clash(service_factory, reference):
    service, _ = service_factory(extraction())
    card = (await service.message(1, 1, DINNER, reference))[0]
    await service.callback(1, tap(card, "Save"))
    preview = (await service.message(1, 5, "work every fri 6-8pm", reference))[0]
    assert "CCA dinner (Saved, Fri 9 Oct 7–9pm)" in preview.text


# ---------- a weekday after the date ----------

POSTER = (
    "HI ALL!! Here are some details for our 2nd society meeting!!!\n\nDetails:\n"
    "Date: 22nd October, Thursday\nTime: 6-9PM\nVenue: S16 03-06\n\n"
    "We will be having some sharings, more bonding games, and not to forget DINNER"
)


async def test_weekday_after_the_date(service_factory, reference):
    service, _ = service_factory(
        extraction(
            title="2nd society meeting",
            date_text="22nd October, Thursday",
            start_time_text="6",
            end_time_text="9PM",
            location="S16 03-06",
        )
    )
    await onboard(service, reference)
    card = (await service.message(1, 2, POSTER, reference))[0]
    assert "Thu, 22 Oct 2026 · 18:00 → 22 Oct 21:00 SGT" in card.text


async def test_date_answer_needs_no_ai(service_factory, reference):
    service, _ = service_factory(extraction(date_text=None))
    await onboard(service, reference)
    ask = (await service.message(1, 2, "CCA dinner 7-9pm at Vivo", reference))[0]
    assert "date" in ask.text
    card = (await service.message(1, 3, "2026-10-22", reference))[0]
    assert "Thu, 22 Oct 2026" in card.text
    assert len(service.extractor.calls) == 1
