"""/plans: the next days written out, seven date-and-name buttons per page, Later plans after."""

from datetime import timedelta

from app.schemas.event import EventDraft, Status
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


async def test_agenda_labels_plans_deadlines_and_blocks(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    await onboard(service, reference)
    await add_dinner(service, reference)
    await service.command(1, "deadline", "CS3244 report fri 11:59pm")
    block = (await service.message(1, 5, "work every wed 6-10pm", reference))[0]
    await service.callback(1, tap(block, "Save block"))
    reply = (await service.command(1, "plans"))[0]
    assert reply.text == (
        "<b>Your plans · next 7 days</b>\n\n"
        "<b>Wed 7 Oct</b>\n• 6–10pm · 🚫 Busy: Work\n\n"
        "<b>Fri 9 Oct</b>\n• 7–9pm · CCA dinner · 📍 Vivo\n• 11:59pm · 📝 Due: CS3244 report\n\n"
        "Tap a plan below to edit or remove it."
    )
    labels = [row[0].text for row in reply.keyboard.inline_keyboard]
    assert labels == ["7 Oct · Busy: Work", "9 Oct · CCA dinner", "9 Oct · Due: CS3244 report"]
    assert all(len(row) == 1 for row in reply.keyboard.inline_keyboard)


async def test_tapping_a_label_opens_the_item_and_back_returns(service_factory, reference):
    service, repo = service_factory(extraction(), due())
    await onboard(service, reference)
    await add_dinner(service, reference)
    await service.command(1, "deadline", "CS3244 report fri 11:59pm")
    reply = (await service.command(1, "plans"))[0]
    dl_card = (await service.callback(1, tap(reply, "9 Oct · Due: CS3244 report")))[0]
    assert "CS3244 report" in dl_card.text and dl_card.edit
    assert [b.text for row in dl_card.keyboard.inline_keyboard for b in row] == [
        "✅ Done",
        "🗑 Remove",
        "← Back to plans",
    ]
    back = (await service.callback(1, tap(dl_card, "← Back to plans")))[0]
    assert back.text == reply.text and back.edit
    plan_card = (await service.callback(1, tap(back, "9 Oct · CCA dinner")))[0]
    assert "<b>CCA dinner</b>" in plan_card.text and plan_card.edit
    labels = [b.text for row in plan_card.keyboard.inline_keyboard for b in row]
    assert "Edit details" in labels and "Remove from plans" in labels
    assert labels[-1] == "← Back to plans"


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


async def test_agenda_pages_seven_at_a_time(service_factory, reference):
    service, repo = service_factory()
    await onboard(service, reference)
    for n in range(13):
        await service.message(
            1, 10 + n, f"remind me at {n % 9 + 1}pm tomorrow to task {n}", reference
        )
    first = (await service.command(1, "plans"))[0]
    rows = first.keyboard.inline_keyboard
    assert len(rows) == 8 and all(len(row) == 1 for row in rows)
    assert rows[-1][0].text == "Next →"
    assert "Page 1 of 2" in first.text and not first.edit
    second = (await service.callback(1, tap(first, "Next →")))[0]
    assert second.edit and "Page 2 of 2" in second.text
    assert len(second.keyboard.inline_keyboard) == 7
    assert second.keyboard.inline_keyboard[-1][0].text == "← Previous"
    back = (await service.callback(1, tap(second, "← Previous")))[0]
    assert back.edit and back.text == first.text
    assert (await service.callback(1, "plans:99"))[0].text.endswith("<i>Page 2 of 2</i>")


async def test_cards_do_not_repeat_first_plan_tips(service_factory, reference):
    service, _ = service_factory(
        extraction(),
        extraction(title="Study group", start_time_text="2", end_time_text="4pm", location=None),
    )
    await onboard(service, reference)
    first = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    added = (await service.callback(1, tap(first, "Add to plans")))[0]
    assert "Next, try /free tomorrow" not in added.text

    second = (await service.message(1, 3, "Study group Friday 2–4pm", reference))[0]
    added = (await service.callback(1, tap(second, "Add to plans")))[0]
    assert "Next, try /free tomorrow" not in added.text


async def populate_plans(service_factory, reference, count):
    service, repo = service_factory()
    user = await repo.ensure_user(1)
    for index in range(count):
        start = reference + timedelta(days=index + 1)
        event = await repo.create_preview(
            user.id,
            index + 1,
            EventDraft(
                title=f"Plan {index + 1}",
                start_at=start,
                end_at=start + timedelta(hours=2),
                location="Clementi Mall",
            ),
        )
        await repo.rsvp(user.id, event, Status.GOING)
    return service, repo


async def test_plan_pages_and_back_restore_same_page(service_factory, reference):
    service, _ = await populate_plans(service_factory, reference, 16)
    first = (await service.command(1, "plans"))[0]
    # Plans 1-6 fall in the next 7 days (Sun 4 to Sat 10 Oct); 7-16 wait behind Later plans.
    assert "Page" not in first.text and "+ 10 more after Sat 10 Oct" in first.text
    assert [b.text for b in first.keyboard.inline_keyboard[-1]] == ["Later plans →"]
    later = (await service.callback(1, tap(first, "Later plans →")))[0]
    assert later.text.startswith("<b>Later plans</b>") and "Page 1 of 2" in later.text
    second = (await service.callback(1, tap(later, "Next →")))[0]
    assert "Page 2 of 2" in second.text and second.edit
    assert [b.text for b in later.keyboard.inline_keyboard[-1]] == ["← Previous", "Next →"]
    assert (await service.callback(1, tap(later, "← Previous")))[0].text == first.text
    label = second.keyboard.inline_keyboard[0][0].text
    assert label == "18 Oct · Plan 14"
    card = (await service.callback(1, tap(second, label)))[0]
    assert "<b>Plan 14</b>" in card.text and "📍 Clementi Mall" in card.text
    assert card.edit and card.parse_mode == "HTML"
    back = (await service.callback(1, tap(card, "← Back to plans")))[0]
    assert back.edit and back.text == second.text
    assert back.keyboard == second.keyboard
    for row in card.keyboard.inline_keyboard:
        for button in row:
            if button.callback_data:
                assert len(button.callback_data.encode()) <= 64


async def test_back_after_removing_last_item_clamps_page(service_factory, reference):
    service, _ = await populate_plans(service_factory, reference, 7)
    second = (await service.callback(1, "plans:1"))[0]
    assert second.text.startswith("<b>Later plans</b>")
    card = (await service.callback(1, second.keyboard.inline_keyboard[0][0].callback_data))[0]
    removed = (await service.callback(1, tap(card, "Remove from plans")))[0]
    assert "Not in your plans" in removed.text
    back = (await service.callback(1, tap(removed, "← Back to plans")))[0]
    assert back.text.startswith("<b>Your plans · next 7 days</b>") and back.edit
    assert "more after" not in back.text
    assert len(back.keyboard.inline_keyboard) == 6


async def test_list_page_survives_edits_and_service_restart(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 9)
    second = (await service.callback(1, "plans:1"))[0]
    card = (await service.callback(1, second.keyboard.inline_keyboard[0][0].callback_data))[0]
    edit = (await service.callback(1, tap(card, "Edit details")))[0]
    await service.callback(1, tap(edit, "Date & time"))
    assert (await repo.ensure_user(1)).pending["plans_page"] == 1
    restarted, _ = service_factory()
    restarted.repo = repo
    preview = (await restarted.message(1, 30, "7-9pm", reference))[0]
    assert "After:" in preview.text
    saved = (await restarted.callback(1, tap(preview, "Save changes")))[-1]
    assert "7PM–9PM SGT" in saved.text
    back = (await restarted.callback(1, tap(saved, "← Back to plans")))[0]
    assert back.edit and back.text.startswith("<b>Later plans</b>")


async def test_long_titles_are_shortened_only_in_list(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 1)
    event = next(iter(repo.events.values()))
    event.title = "A very long workshop title with <tags> & more information for everyone"
    agenda = (await service.command(1, "plans"))[0]
    button = agenda.keyboard.inline_keyboard[0][0]
    assert button.text == "5 Oct · A very long workshop title with…"
    assert event.title not in agenda.text
    card = (await service.callback(1, button.callback_data))[0]
    assert "&lt;tags&gt; &amp; more information for everyone</b>" in card.text


async def test_workshop_button_does_not_end_in_partial_word(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 1)
    event = next(iter(repo.events.values()))
    event.title = "Halloween Workshop: Amigurumi Ghost"
    agenda = (await service.command(1, "plans"))[0]
    button = agenda.keyboard.inline_keyboard[0][0]
    assert button.text == "5 Oct · Halloween Workshop: Amigurumi…"
    card = (await service.callback(1, button.callback_data))[0]
    assert "<b>Halloween Workshop: Amigurumi Ghost</b>" in card.text


async def test_plans_sort_by_event_date_and_time_not_creation(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 3)
    events = list(repo.events.values())
    starts = [
        reference + timedelta(days=3, hours=7),
        reference + timedelta(days=1, hours=8),
        reference + timedelta(days=1),
    ]
    for event, start in zip(events, starts, strict=True):
        event.start_at, event.end_at = start, start + timedelta(hours=1)
    agenda = (await service.command(1, "plans"))[0]
    assert [row[0].text for row in agenda.keyboard.inline_keyboard] == [
        "5 Oct · Plan 3",
        "5 Oct · Plan 2",
        "7 Oct · Plan 1",
    ]


async def test_list_buttons_keep_ownership_checks(service_factory, reference):
    service, _ = await populate_plans(service_factory, reference, 1)
    agenda = (await service.command(1, "plans"))[0]
    response = (await service.callback(2, agenda.keyboard.inline_keyboard[0][0].callback_data))[0]
    assert "isn't in your plans" in response.text
    assert "Clementi Mall" not in response.text
    for data in ("fp:no:pc:any", "fp:-1:pc:any", "fp:1:fp:0:pc:any"):
        assert "isn't valid" in (await service.callback(1, data))[0].text


async def test_start_opens_the_mini_app_from_a_button(service_factory, reference):
    service, _ = service_factory(mini_app_url="https://example.test/app/")
    welcome = (await service.command(1, "start"))[0]
    [app] = [b for row in welcome.keyboard.inline_keyboard for b in row if b.web_app]
    assert app.text == "📱 Open AmIFree" and app.web_app.url == "https://example.test/app/"
    await onboard(service, reference)
    menu = (await service.command(1, "start"))[0]
    assert menu.keyboard.inline_keyboard[-1][0].web_app.url == "https://example.test/app/"
    # Without a deployed Mini App there is no button.
    plain, _ = service_factory()
    assert not any(
        b.web_app
        for row in (await plain.command(1, "start"))[0].keyboard.inline_keyboard
        for b in row
    )


async def test_app_command_and_plans_open_the_mini_app(service_factory, reference):
    url = "https://example.test/app/"
    service, _ = service_factory(extraction(), mini_app_url=url)
    opened = (await service.command(1, "app"))[0]
    assert opened.keyboard.inline_keyboard[0][0].web_app.url == url
    await onboard(service, reference)
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    plans = (await service.command(1, "plans"))[0]
    last = plans.keyboard.inline_keyboard[-1][0]
    assert last.text == "📱 Open AmIFree" and last.web_app.url == url
    plain, _ = service_factory()
    assert "isn't available" in (await plain.command(1, "app"))[0].text


async def test_plans_list_marks_kinds_days_and_odd_plans(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 3)
    first, second, third = repo.events.values()
    # Under way since yesterday: listed under today, with its own end date.
    first.start_at, first.end_at = reference - timedelta(days=1), reference + timedelta(days=1)
    first.title, first.location = "Event", None
    second.status = Status.MAYBE
    third.title = "Hackathon <briefing> for the whole computing cohort"
    third.location = "COM3 Seminar Room 1 & Lounge"
    await service.message(1, 9, "remind me at 9pm tomorrow to call mum", reference)
    text = (await service.command(1, "plans"))[0].text
    assert "<b>Today · Sun 4 Oct</b>\n• 12pm – 5 Oct 12pm · Event" in text
    assert "<b>Tomorrow · Mon 5 Oct</b>\n• 9pm · ⏰ Call mum" in text
    assert "<b>Tue 6 Oct</b>\n• 12–2pm · Plan 2 · 📍 Clementi Mall (maybe)" in text
    assert "• 12–2pm · Hackathon &lt;briefing&gt; for the… · 📍 COM3 Seminar Room 1…" in text


async def test_settings_choose_how_many_days_plans_shows(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 10)
    await onboard(service, reference)
    panel = (await service.command(1, "settings"))[0]
    assert "📋 /plans shows: next 7 days" in panel.text
    days = [b.text for b in panel.keyboard.inline_keyboard[-1]]
    assert days == ["3 days", "✓ 7 days", "14 days", "30 days"]
    chosen = (await service.callback(1, tap(panel, "14 days")))[0]
    assert chosen.edit and "next 14 days" in chosen.text
    assert (await repo.ensure_user(1)).plans_days == 14
    plans = (await service.command(1, "plans"))[0]
    assert plans.text.startswith("<b>Your plans · next 14 days</b>")
    assert "more after" not in plans.text and "Page 1 of 2" in plans.text
    for data in ("set:plansdays:5", "set:plansdays:x", "set:plansdays:007"):
        assert "isn't valid" in (await service.callback(1, data))[0].text
    assert (await repo.ensure_user(1)).plans_days == 14


async def test_nothing_soon_points_to_later_plans(service_factory, reference):
    service, repo = await populate_plans(service_factory, reference, 1)
    event = next(iter(repo.events.values()))
    event.start_at += timedelta(days=20)
    event.end_at += timedelta(days=20)
    plans = (await service.command(1, "plans"))[0]
    assert plans.text == (
        "<b>Your plans · next 7 days</b>\n\n"
        "Nothing in the next 7 days. Your next plan is on <b>Sun 25 Oct</b>."
    )
    assert [b.text for row in plans.keyboard.inline_keyboard for b in row] == ["Later plans →"]
    later = (await service.callback(1, tap(plans, "Later plans →")))[0]
    assert "25 Oct · Plan 1" in [row[0].text for row in later.keyboard.inline_keyboard]
