from datetime import datetime

import pytest

from app.bot.messages import details, event_card
from app.schemas.event import SG, Event, Status
from app.services.availability import Availability
from tests.conftest import extraction, tap


def plan(**changes):
    return Event(
        **(
            dict(
                id="event",
                user_id="user",
                title="Dinner with friends",
                start_at=datetime(2026, 10, 23, 19, tzinfo=SG),
                end_at=datetime(2026, 10, 23, 21, tzinfo=SG),
                location="Clementi Mall",
            )
            | changes
        )
    )


def rows(card):
    return [[button.text for button in row] for row in card.keyboard.inline_keyboard]


def test_approved_preview():
    card = event_card(plan(), Availability((), personal=True))
    assert card.text == (
        "✅ No clashes found\n\n<b>Dinner with friends</b>\n"
        "📅 23 Oct 2026 (Friday) · 7PM–9PM SGT\n📍 Clementi Mall"
    )
    assert card.parse_mode == "HTML"
    assert rows(card) == [["Add to plans"], ["Edit details", "Skip"]]


@pytest.mark.parametrize(
    "status", [Status.PREVIEW, Status.GOING, Status.DECLINED, Status.CANCELLED]
)
def test_personal_details_omit_price_and_other_notes(status):
    from app.bot.messages import personal_details

    event = plan(status=status, notes="Price: $2 (For Participants)\nBring your own materials.")
    card = event_card(event, Availability(()))
    assert "<b>Dinner with friends</b>" in card.text
    assert "23 Oct 2026 (Friday) · 7PM–9PM SGT" in card.text
    assert "📍 Clementi Mall" in card.text
    for text in (card.text, personal_details(event), personal_details(event, compact=True)):
        assert "Price:" not in text and "Bring your own" not in text
    group_card = event_card(event.model_copy(update={"group_event_id": "shared"}), Availability(()))
    assert "Price: $2" in group_card.text


@pytest.mark.parametrize(
    "timetable,personal,note",
    [
        (False, False, "Connect your timetable and personal calendar for a fuller clash check."),
        (False, True, "Connect your NUSMods calendar to check for clashes with your classes."),
        (True, False, "Connect your personal calendar to check for clashes with your other plans."),
        (True, True, None),
    ],
)
def test_calendar_notes_follow_connection_state(timetable, personal, note):
    availability = Availability(
        (), covered=False, timetable_connected=timetable, personal_connected=personal
    )
    for status in (Status.PREVIEW, Status.GOING, Status.DECLINED):
        card = event_card(plan(status=status), availability)
        assert "Calendar coverage is incomplete" not in card.text
        assert "📌" not in card.text
        if note and status != Status.GOING:
            assert f"<i>{note}</i>" in card.text
            assert card.text.count("Connect your") == 1
        else:
            assert "<i>Connect your" not in card.text


async def test_service_distinguishes_connection_from_date_coverage(service_factory, reference):
    from tests.conftest import onboard

    service, repo = service_factory()
    await onboard(service, reference)
    user = await repo.ensure_user(1)
    # An event beyond semester coverage still has a connected school timetable.
    event = plan(
        start_at=datetime(2027, 2, 1, 19, tzinfo=SG),
        end_at=datetime(2027, 2, 1, 21, tzinfo=SG),
    )
    availability = await service.availability(user, event)
    assert availability.timetable_connected and not availability.personal_connected
    assert not availability.covered
    card = event_card(event, availability)
    assert "Connect your personal calendar" in card.text
    assert "Connect your NUSMods calendar" not in card.text


def test_minutes_overnight_and_no_venue():
    event = plan(
        start_at=datetime(2026, 10, 23, 19, 30, tzinfo=SG),
        end_at=datetime(2026, 10, 23, 21, 30, tzinfo=SG),
        location=None,
    )
    assert "7:30PM–9:30PM SGT" in event_card(event, Availability(())).text
    overnight = event.model_copy(update={"end_at": datetime(2026, 10, 24, 1, tzinfo=SG)})
    text = event_card(overnight, Availability(())).text
    assert "23 Oct 2026 (Friday) · 7:30PM – 24 Oct 2026 (Saturday) · 1AM SGT" in text
    assert "📍" not in text


def test_clashes_appear_once_and_survive_adding():
    first = plan(title="CS3216 meeting", end_at=datetime(2026, 10, 23, 20, tzinfo=SG))
    second = plan(title="Project discussion", start_at=datetime(2026, 10, 23, 20, tzinfo=SG))
    single = event_card(plan(), Availability((first,)))
    assert single.text.startswith("⚠️ Overlaps with CS3216 meeting · 7PM–8PM SGT\n\n")
    assert rows(single) == [["Add anyway"], ["Edit details", "Skip"]]
    for status in (Status.PREVIEW, Status.GOING, Status.DECLINED):
        card = event_card(plan(status=status), Availability((first, second)), synced=True)
        assert card.text.startswith(
            "⚠️ Overlaps with 2 plans\n"
            "• CS3216 meeting · 7PM–8PM SGT\n"
            "• Project discussion · 8PM–9PM SGT\n\n"
        )
        assert card.text.count("CS3216 meeting") == 1
        assert "Conflicts:" not in card.text and "miss the first" not in card.text
        if status == Status.GOING:
            assert "☑️ <b>Added to your plans.</b>" in card.text
            assert rows(card) == [["Edit details", "Remove from plans"]]
        elif status == Status.DECLINED:
            assert "❌ <b>Not in your plans.</b>" in card.text
            assert rows(card) == [["Add anyway", "Edit details"]]


def test_safe_html_and_short_notices():
    event = plan(
        title='<Dinner & "friends">', location="<Mall>", notes="A & B <3", assumed_duration=True
    )
    conflict = plan(title="<Meeting> & work")
    card = event_card(event, Availability((conflict,), covered=False, busy_day=True))
    assert "<b>&lt;Dinner &amp; &quot;friends&quot;&gt;</b>" in card.text
    assert "📍 &lt;Mall&gt;" in card.text
    assert "A &amp; B &lt;3" not in card.text
    assert "&lt;Meeting&gt; &amp; work" in card.text
    assert "End time estimated." in card.text
    assert "Connect your timetable and personal calendar for a fuller clash check." in card.text
    assert "Busy day" not in card.text and "Checked:" not in card.text


def test_calendar_controls_only_for_added_unsynced_plans():
    for status in (Status.PREVIEW, Status.DECLINED, Status.GOING):
        for synced in (False, True):
            card = event_card(plan(status=status), Availability(()), synced=synced)
            buttons = [label for row in rows(card) for label in row]
            assert ("📅 Google Calendar" in buttons) == (status == Status.GOING and not synced)
            assert ("Syncing to your calendar." in card.text) == (status == Status.GOING and synced)


@pytest.mark.parametrize("personal_connected", [False, True])
@pytest.mark.parametrize("synced", [False, True])
def test_added_calendar_explanation_matches_export_buttons(personal_connected, synced):
    card = event_card(
        plan(status=Status.GOING),
        Availability(
            (), covered=False, timetable_connected=False, personal_connected=personal_connected
        ),
        synced=synced,
    )
    assert "☑️ <b>Added to your plans.</b>" in card.text
    assert "<i>Connect your" not in card.text
    buttons = [label for row in rows(card) for label in row]
    exports = not personal_connected and not synced
    assert ("📅 Google Calendar" in buttons) == exports
    assert ("🍎 Apple Calendar" in buttons) == exports
    assert ("Want this event in your personal calendar?" in card.text) == exports
    assert ("connect calendar syncing through /calendar" in card.text) == exports
    # An imported calendar is not evidence of automatic outbound syncing.
    assert ("Syncing to your calendar." in card.text) == synced


async def test_connected_personal_calendar_hides_exports_after_add(service_factory, reference):
    from tests.test_personal_calendar import GOOGLE, FakeReader

    service, _ = service_factory(extraction())
    service.calendar_reader = FakeReader()
    await service.message(1, 1, GOOGLE, reference)
    preview = (await service.message(1, 2, "Dinner Friday 7-9pm at Vivo", reference))[0]
    added = (await service.callback(1, tap(preview, "Add to plans")))[0]
    assert rows(added) == [["Edit details", "Remove from plans"]]
    assert "☑️ <b>Added to your plans.</b>" in added.text
    assert "Want this event in your personal calendar?" not in added.text
    assert "connect calendar syncing" not in added.text


def test_cancelled_and_group_cards():
    cancelled = event_card(plan(status=Status.CANCELLED), Availability(()))
    assert cancelled.text.startswith("Cancelled\n\n<b>Dinner with friends</b>")
    assert cancelled.keyboard is None and cancelled.parse_mode == "HTML"
    group = plan(group_event_id="shared", status=Status.GOING)
    card = event_card(group, Availability(()))
    assert card.text.startswith("✅ No clashes found\n\n" + details(group))
    assert "Status: Going" in card.text and card.parse_mode is None
    assert rows(card)[0] == ["Can't make it"]


@pytest.mark.parametrize("clash", [False, True])
async def test_actions_and_edits_keep_html(service_factory, reference, clash):
    service, repo = service_factory(extraction(), extraction(title="Other plan"))
    if clash:
        first = (await service.message(1, 1, "Dinner Friday 7-9pm", reference))[0]
        await service.callback(1, tap(first, "Add to plans"))
    card = (await service.message(1, 2, "Dinner Friday 7-9pm", reference))[0]
    action = "Add anyway" if clash else "Add to plans"
    added = (await service.callback(1, tap(card, action)))[0]
    assert added.edit and added.parse_mode == "HTML"
    assert added.text.count("Added to your plans.") == 1
    assert added.text.startswith("⚠️ Overlaps with" if clash else "✅ No clashes found")
    removed = (await service.callback(1, tap(added, "Remove from plans")))[0]
    assert removed.parse_mode == "HTML" and rows(removed) == [[action, "Edit details"]]
    again = (await service.callback(1, tap(removed, action)))[0]
    edit = (await service.callback(1, tap(again, "Edit details")))[0]
    await service.callback(1, tap(edit, "Event name"))
    changed = (await service.message(1, 3, "Dinner <&> friends", reference))[0]
    assert changed.parse_mode == "HTML" and "<b>Dinner &lt;&amp;&gt; friends</b>" in changed.text
    assert changed.text.startswith("⚠️ Overlaps with" if clash else "✅ No clashes found")
    assert repo.events


async def test_details_match_in_plans_edit_and_change_confirmation(service_factory, reference):
    from app.bot.messages import personal_details

    service, repo = service_factory(
        extraction(), extraction(kind="update", start_time_text="8pm", end_time_text="10pm")
    )
    preview = (await service.message(1, 1, "Dinner Friday 7-9pm at Vivo", reference))[0]
    added = (await service.callback(1, tap(preview, "Add to plans")))[0]
    event = next(iter(repo.events.values()))
    expected = personal_details(event)
    agenda = (await service.command(1, "plans"))[0]
    assert agenda.parse_mode == "HTML"
    assert agenda.keyboard.inline_keyboard[0][0].text == "9 Oct · CCA dinner"
    opened = (await service.callback(1, tap(agenda, "9 Oct · CCA dinner")))[0]
    assert expected in opened.text and opened.parse_mode == "HTML"
    edit = (await service.callback(1, tap(added, "Edit details")))[0]
    assert expected in edit.text and edit.parse_mode == "HTML"
    await service.callback(1, tap(edit, "Date & time"))
    change = (await service.message(1, 2, "Move to 8-10pm", reference))[0]
    assert change.parse_mode == "HTML"
    assert expected in change.text.split("After:")[0]
    assert "8PM–10PM SGT" in change.text.split("After:")[1]
    updated = (await service.callback(1, tap(change, "Save changes")))[-1]
    assert updated.parse_mode == "HTML" and "8PM–10PM SGT" in updated.text


async def test_cancellation_confirmation_escapes_and_formats_details(service_factory, reference):
    service, repo = service_factory(extraction(title="Dinner <&> friends"))
    await service.message(1, 1, "Dinner Friday 7-9pm at Vivo", reference)
    user = await repo.ensure_user(1)
    event = next(iter(repo.events.values()))
    confirmation = (await service.begin_change(user, event, cancel=True))[0]
    assert confirmation.parse_mode == "HTML"
    assert "<b>Dinner &lt;&amp;&gt; friends</b>" in confirmation.text
    assert "9 Oct 2026 (Friday) · 7PM–9PM SGT" in confirmation.text
    cancelled = (await service.callback(1, tap(confirmation, "Confirm cancellation")))[-1]
    assert cancelled.parse_mode == "HTML" and cancelled.keyboard is None
    assert cancelled.text.startswith("Cancelled\n\n<b>Dinner &lt;&amp;&gt; friends</b>")


def test_personal_summary_reuses_details_and_keeps_group_only_summary():
    from app.bot.messages import personal_details
    from app.services.reminders import summary

    personal = plan(status=Status.GOING, title="Dinner <&> friends")
    group = plan(status=Status.GOING, title="Group <&> meeting", group_event_id="shared")
    text = summary(personal.start_at, [personal, group])
    assert personal_details(personal, compact=True) in text
    assert "Group &lt;&amp;&gt; meeting" in text
    group_only = summary(group.start_at, [group])
    assert "• 19:00–21:00 Group <&> meeting @ Clementi Mall" in group_only


async def test_direct_notifications_preserve_html():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.bot.handlers import attach_notifier, send_private
    from app.bot.messages import Reply

    bot = SimpleNamespace(send_message=AsyncMock())
    reply = Reply("<b>Dinner</b>", parse_mode="HTML")
    await send_private(bot, 1, reply)
    bot.send_message.assert_awaited_with(1, reply.text, reply_markup=None, parse_mode="HTML")

    service = SimpleNamespace(repo=None)
    notifier = attach_notifier(SimpleNamespace(bot=bot), service)
    await notifier.send(1, reply)
    assert bot.send_message.await_count == 2
    bot.send_message.assert_awaited_with(1, reply.text, reply_markup=None, parse_mode="HTML")


@pytest.mark.parametrize(
    "changes,answer,day,start,end",
    [
        (
            dict(date_text=None, start_time_text="8pm", end_time_text="10pm"),
            "Change the time to 8-10pm",
            9,
            20,
            22,
        ),
        (
            dict(date_text="24 Oct 2026", start_time_text=None, end_time_text=None),
            "Move it to 24 Oct 2026",
            24,
            19,
            21,
        ),
    ],
)
async def test_edit_menu_and_partial_date_time_changes(
    service_factory, reference, changes, answer, day, start, end
):
    service, repo = service_factory(extraction(), extraction(kind="update", **changes))
    card = (await service.message(1, 1, "Dinner Friday 7-9pm at Vivo", reference))[0]
    menu = (await service.callback(1, tap(card, "Edit details")))[0]
    assert rows(menu) == [["Event name", "Date & time", "Venue"], ["Keep unchanged"]]
    prompt = (await service.callback(1, tap(menu, "Date & time")))[0]
    assert prompt.text == (
        "Send the new date, time, or both. Anything you don’t mention stays unchanged."
        "\n\nI’ll show you a preview before saving."
    )
    preview = (await service.message(1, 2, answer, reference))[0]
    event = next(iter(repo.events.values()))
    assert event.start_at.day == 9 and event.start_at.hour == 19
    assert "Before:" in preview.text and "After:" in preview.text
    await service.callback(1, tap(preview, "Save changes"))
    event = next(iter(repo.events.values()))
    assert (event.start_at.day, event.start_at.hour, event.end_at.hour) == (day, start, end)


@pytest.mark.parametrize("answer", ["7-9pm", "7–9pm", "7pm to 9pm", "19:00-21:00"])
async def test_simple_time_edit_does_not_depend_on_ai(service_factory, reference, answer):
    service, repo = service_factory(extraction(start_time_text="6pm", end_time_text="9pm"))
    card = (await service.message(1, 1, "Workshop Friday 6-9pm at Vivo", reference))[0]
    menu = (await service.callback(1, tap(card, "Edit details")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    preview = (await service.message(1, 2, answer, reference))[0]
    assert preview.parse_mode == "HTML"
    assert "6PM–9PM SGT" in preview.text.split("After:")[0]
    assert "7PM–9PM SGT" in preview.text.split("After:")[1]
    event = next(iter(repo.events.values()))
    assert event.start_at.hour == 18
    await service.callback(1, tap(preview, "Save changes"))
    assert (event.start_at.day, event.start_at.hour, event.end_at.hour) == (9, 19, 21)
    assert len(service.extractor.calls) == 1


async def change_preview(service_factory, reference):
    service, repo = service_factory(extraction(start_time_text="6pm", end_time_text="9pm"))
    card = (await service.message(1, 1, "Dinner Friday 6-9pm at Vivo", reference))[0]
    menu = (await service.callback(1, tap(card, "Edit details")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    preview = (await service.message(1, 2, "7-9pm", reference))[0]
    assert rows(preview) == [["Save changes", "Revise", "Discard changes"]]
    assert "<u>Before:</u>" in preview.text and "<u>After:</u>" in preview.text
    assert preview.parse_mode == "HTML"
    return service, repo, preview


async def test_revise_keeps_proposed_values_without_saving(service_factory, reference):
    service, repo, preview = await change_preview(service_factory, reference)
    event = next(iter(repo.events.values()))
    original_version = event.version
    old_save = tap(preview, "Save changes")
    menu = (await service.callback(1, tap(preview, "Revise")))[0]
    assert "7PM–9PM SGT" in menu.text
    assert event.start_at.hour == 18 and event.version == original_version
    assert "expired" in (await service.callback(1, old_save))[0].text
    await service.callback(1, tap(menu, "Event name"))
    updated = (await service.message(1, 3, "Dinner <&> friends", reference))[0]
    assert "<b>Dinner &lt;&amp;&gt; friends</b>" in updated.text.split("After:")[1]
    assert "7PM–9PM SGT" in updated.text.split("After:")[1]
    assert event.start_at.hour == 18 and event.title == "CCA dinner"
    await service.callback(1, tap(updated, "Save changes"))
    assert event.start_at.hour == 19 and event.title == "Dinner <&> friends"
    assert event.version == original_version + 1
    assert (await repo.ensure_user(1)).pending is None


async def test_discard_returns_original_card_and_invalidates_save(service_factory, reference):
    service, repo, preview = await change_preview(service_factory, reference)
    event = next(iter(repo.events.values()))
    original_version = event.version
    discarded = (await service.callback(1, tap(preview, "Discard changes")))[0]
    assert discarded.edit and discarded.parse_mode == "HTML"
    assert "6PM–9PM SGT" in discarded.text
    assert "Changes discarded." in discarded.text
    assert event.start_at.hour == 18 and event.version == original_version
    assert (await repo.ensure_user(1)).pending is None
    assert "expired" in (await service.callback(1, tap(preview, "Save changes")))[0].text
    assert event.start_at.hour == 18


async def test_revise_then_discard_does_not_apply_any_draft(service_factory, reference):
    service, repo, preview = await change_preview(service_factory, reference)
    menu = (await service.callback(1, tap(preview, "Revise")))[0]
    await service.callback(1, tap(menu, "Date & time"))
    second = (await service.message(1, 3, "8-10pm", reference))[0]
    await service.callback(1, tap(second, "Discard changes"))
    event = next(iter(repo.events.values()))
    assert (event.start_at.hour, event.end_at.hour) == (18, 21)


@pytest.mark.parametrize("action", ["Revise", "Discard changes"])
async def test_preview_actions_reject_stale_event_versions(service_factory, reference, action):
    service, repo, preview = await change_preview(service_factory, reference)
    event = next(iter(repo.events.values()))
    await repo.rsvp(event.user_id, event, Status.GOING)
    response = (await service.callback(1, tap(preview, action)))[0]
    assert "That event changed" in response.text
    assert event.start_at.hour == 18
    assert (await repo.ensure_user(1)).pending is None


@pytest.mark.parametrize("revision", [False, True])
async def test_edit_menu_requires_field_selection(service_factory, reference, revision):
    service, repo, preview = await change_preview(service_factory, reference)
    if revision:
        menu = (await service.callback(1, tap(preview, "Revise")))[0]
    else:
        await service.command(1, "cancel")
        event = next(iter(repo.events.values()))
        menu = (await service.begin_change(await repo.ensure_user(1), event))[0]
    assert menu.text.endswith("Choose a detail below to edit.")
    assert "type the change" not in menu.text
    calls = len(service.extractor.calls)
    response = (await service.message(1, 3, "8-10pm", reference))[0]
    assert response.text == "Choose a detail below to edit."
    assert rows(response) == [["Event name", "Date & time", "Venue"], ["Keep unchanged"]]
    assert len(service.extractor.calls) == calls
    assert next(iter(repo.events.values())).start_at.hour == 18
    await service.callback(1, tap(response, "Date & time"))
    updated = (await service.message(1, 4, "8-10pm", reference))[0]
    assert "8PM–10PM SGT" in updated.text.split("After:")[1]
    assert rows(updated) == [["Save changes", "Revise", "Discard changes"]]
    assert next(iter(repo.events.values())).start_at.hour == 18
