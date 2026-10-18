"""/plans: one message grouping plans, deadlines and busy time, with a button per item."""

from tests.conftest import extraction, onboard, tap

DINNER = "CCA dinner Friday 7–9pm at Vivo"


def due():
    return extraction(
        title="CS3244 report",
        date_text="Fri",
        start_time_text="11:59pm",
        end_time_text=None,
        location=None,
    )


async def add_dinner(service, reference):
    card = (await service.message(1, 2, DINNER, reference))[0]
    await service.callback(1, tap(card, "Add to plans"))


async def test_empty_agenda(service_factory, reference):
    service, _ = service_factory()
    reply = (await service.command(1, "plans"))[0]
    assert reply.text.startswith("Nothing coming up")


async def test_agenda_groups_plans_deadlines_and_blocks(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    await onboard(service, reference)
    await add_dinner(service, reference)  # Fri 9 Oct 7–9pm
    await service.command(1, "deadline", "CS3244 report fri 11:59pm")  # Fri 9 Oct
    block = (await service.message(1, 5, "work every wed 6-10pm", reference))[0]
    await service.callback(1, tap(block, "Save block"))  # weekly, next Wed 8 Oct

    reply = (await service.command(1, "plans"))[0]
    text = reply.text
    assert text.startswith("Coming up — tap a number to open it.")
    # Three items, numbered, soonest first (Wed block, then Fri dinner + deadline).
    assert "1. " in text and "2. " in text and "3. " in text
    assert (
        "CCA dinner" in text
        and "CS3244 report" in text
        and "work · busy (weekly)".lower() in text.lower()
    )
    assert "This week" in text  # all within 7 days of Sun 4 Oct
    labels = [b.text for row in reply.keyboard.inline_keyboard for b in row]
    assert labels == ["1", "2", "3"]


async def test_tapping_a_number_opens_the_item(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    await onboard(service, reference)
    await add_dinner(service, reference)
    await service.command(1, "deadline", "CS3244 report fri 11:59pm")
    reply = (await service.command(1, "plans"))[0]

    # The deadline button opens its card with Done / Remove.
    def cb_for(title_hint):
        lines = reply.text.split("\n")
        n = next(line.split(".")[0] for line in lines if title_hint in line)
        return next(
            b.callback_data for row in reply.keyboard.inline_keyboard for b in row if b.text == n
        )

    dl_card = (await service.callback(1, cb_for("CS3244")))[0]
    assert "CS3244 report" in dl_card.text
    assert [b.text for row in dl_card.keyboard.inline_keyboard for b in row] == [
        "✅ Done",
        "🗑 Remove",
    ]

    plan_card = (await service.callback(1, cb_for("CCA dinner")))[0]
    assert "CCA dinner" in plan_card.text
    labels = [b.text for row in plan_card.keyboard.inline_keyboard for b in row]
    assert "Edit" in labels and "Remove" in labels


async def test_declined_and_past_plans_are_not_listed(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    await onboard(service, reference)
    card = (await service.message(1, 2, DINNER, reference))[0]
    await service.callback(1, tap(card, "Skip"))  # declined
    assert "Nothing coming up" in (await service.command(1, "plans"))[0].text


async def test_start_menu_buttons_run_the_matching_command(service_factory, reference):
    service, _ = service_factory()
    await onboard(service, reference)
    menu = (await service.command(1, "start"))[0]
    labels = [b.text for row in menu.keyboard.inline_keyboard for b in row]
    assert labels == ["📋 My plans", "🕒 Am I free?", "📌 Deadlines", "⚙️ Settings"]
    assert (await service.callback(1, tap(menu, "📋 My plans")))[0].text.startswith(
        "Nothing coming up"
    )
    panel = (await service.callback(1, tap(menu, "⚙️ Settings")))[0]
    assert "Reminders: on" in panel.text
    flipped = (await service.callback(1, tap(panel, "🔔 Turn off reminders")))[0]
    assert "Reminders: off" in flipped.text and flipped.edit
    assert (
        "Reminders: on" in (await service.callback(1, tap(flipped, "🔔 Turn on reminders")))[0].text
    )
    assert "No upcoming deadlines" in (await service.callback(1, tap(menu, "📌 Deadlines")))[0].text
    assert await service.callback(1, tap(menu, "🕒 Am I free?"))


async def test_agenda_pages_ten_at_a_time(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    for n in range(13):
        await service.message(
            1, 10 + n, f"remind me at {n % 9 + 1}pm tomorrow to task {n}", reference
        )
    first = (await service.command(1, "plans"))[0]
    labels = [b.text for row in first.keyboard.inline_keyboard for b in row]
    assert labels == [str(n) for n in range(1, 11)] + ["Next"]
    assert "Page 1 of 2" in first.text and not first.edit
    second = (await service.callback(1, tap(first, "Next")))[0]
    assert second.edit and "Page 2 of 2" in second.text
    assert [b.text for row in second.keyboard.inline_keyboard for b in row] == [
        "1",
        "2",
        "3",
        "Previous",
    ]
    assert (await service.callback(1, "plans:99"))[0].text.startswith("Coming up")  # clamped


async def test_first_plan_gets_one_tip(service_factory, reference):
    service, _ = service_factory(
        extraction(),
        extraction(title="Study group", start_time_text="2", end_time_text="4pm", location=None),
    )
    await onboard(service, reference)
    first = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    added = (await service.callback(1, tap(first, "Add to plans")))[0]
    assert "Next, try /free tomorrow" in added.text
    second = (await service.message(1, 3, "Study group Friday 2–4pm", reference))[0]
    added = (await service.callback(1, tap(second, "Add to plans")))[0]
    assert "Next, try /free tomorrow" not in added.text
