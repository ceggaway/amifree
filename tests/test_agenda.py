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
    assert "Reminders are" in (await service.callback(1, tap(menu, "⚙️ Settings")))[0].text
    assert "No upcoming deadlines" in (await service.callback(1, tap(menu, "📌 Deadlines")))[0].text
    assert await service.callback(1, tap(menu, "🕒 Am I free?"))
