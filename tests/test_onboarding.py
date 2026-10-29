"""First-run setup: welcome, step-by-step link guides, and what happens after a link."""

import pytest

from app.bot import onboarding
from tests.conftest import extraction, onboard, tap
from tests.test_personal_calendar import FRIDAY_DINNER, GOOGLE, FakeReader


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row] if reply.keyboard else []


async def test_new_user_gets_the_welcome(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "start"))[0]
    assert reply.text == onboarding.WELCOME
    assert reply.parse_mode == "HTML"
    assert "<b>1. Connect your schedule</b>" in reply.text
    assert "<b>2. Send me a plan</b>" in reply.text
    assert "<b>3. Save with a tap</b>" in reply.text
    assert "calendar syncing enabled" in reply.text
    assert "/help" in reply.text
    assert labels(reply) == ["📅 Connect personal calendar", "Skip calendar setup"]
    assert [len(row) for row in reply.keyboard.inline_keyboard] == [2]


async def test_every_guide_is_reachable_and_comes_back(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    picker = (await service.callback(1, tap(welcome, "📅 Connect personal calendar")))[0]
    assert picker.edit and labels(picker) == [
        "📅 Google Calendar",
        "🍎 Apple / iCloud",
        "🤔 I'm not sure",
        "📚 NUSMods timetable",
        "📧 Outlook (soon)",
        "« Back",
    ]
    expected = {
        # No public address in tests, so Apple goes straight to its busy-times part.
        "🍎 Apple / iCloud": ["Part 2 of 2", "Step 1 of 4", "calendar icon"],
        "📅 Google Calendar": ["calendar.google.com", "Secret address in iCal format"],
        "📧 Outlook (soon)": ["coming soon"],
        "📚 NUSMods timetable": ["Connect your NUSMods timetable", "Share/Sync"],
    }
    for button, phrases in expected.items():
        guide = (await service.callback(1, tap(picker, button)))[0]
        assert guide.edit and all(p in guide.text for p in phrases), button
        if button == "🍎 Apple / iCloud":
            continue  # its way out is Skip this part, tested on its own
        back = (await service.callback(1, tap(guide, "« Other calendars")))[0]
        assert back.text == onboarding.PICK_CALENDAR
    home = (await service.callback(1, tap(picker, "« Back")))[0]
    assert home.text == onboarding.WELCOME
    assert home.parse_mode == "HTML"
    assert picker.parse_mode is None


async def test_picker_asks_where_events_live_and_helps_the_unsure(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    picker = (await service.callback(1, tap(welcome, "📅 Connect personal calendar")))[0]
    assert "Where do you usually add your events?" in picker.text
    assert "Outlook is coming soon" in picker.text
    unsure = (await service.callback(1, tap(picker, "🤔 I'm not sure")))[0]
    assert unsure.edit and unsure.text == onboarding.NOT_SURE
    # Not only iPhone users decide here.
    assert "Android" in unsure.text and "iPhone" in unsure.text and "Computer" in unsure.text
    assert labels(unsure) == ["📅 Google Calendar", "🍎 Apple / iCloud", "« Back"]
    back = (await service.callback(1, tap(unsure, "« Back")))[0]
    assert back.text == onboarding.PICK_CALENDAR


async def test_every_guide_ends_with_what_to_do_next(service_factory):
    service, _ = service_factory()
    for screen in ("ios", "google"):
        guide = (await service.callback(1, f"setup:{screen}"))[0]
        assert "about 1 minute" in guide.text, screen
        assert "Paste the link here" in guide.text, screen
        assert "Setup is optional" in guide.text, screen


async def test_guides_warn_about_the_common_wrong_turns(service_factory):
    service, _ = service_factory()
    ios = (await service.callback(1, "setup:ios"))[0].text
    assert "only works for iCloud calendars" in ios and "Google or Outlook" in ios
    google = (await service.callback(1, "setup:google"))[0].text
    assert "Not the Public address" in google and "school or work account" in google
    outlook = (await service.callback(1, "setup:outlook"))[0].text
    assert "coming soon" in outlook.lower()


async def test_guides_say_where_to_do_it_and_how_to_spot_the_right_link(service_factory):
    service, _ = service_factory()
    expected = {
        "ios": ("Do this on your iPhone", "<code>webcal://</code>"),
        "google": ("Do this on a computer", "ends in <code>.ics</code>"),
    }
    for screen, phrases in expected.items():
        guide = (await service.callback(1, f"setup:{screen}"))[0]
        assert guide.parse_mode == "HTML", screen  # so the on-screen labels are bold
        assert "You'll know it's right" in guide.text, screen
        assert all(p in guide.text for p in phrases), screen


async def test_iphone_guide_has_a_mac_version_and_back(service_factory):
    service, _ = service_factory()
    iphone = (await service.callback(1, "setup:ios"))[0]
    assert "💻 On a Mac instead?" in labels(iphone)
    mac = (await service.callback(1, tap(iphone, "💻 On a Mac instead?")))[0]
    assert mac.edit and mac.parse_mode == "HTML"
    assert all(p in mac.text for p in ("Do this on your Mac", "Public Calendar", "webcal://"))
    assert labels(mac) == [
        "📱 On an iPhone instead?",
        "📅 Mine's Google",
        "« Other calendars",
    ]
    back = (await service.callback(1, tap(mac, "📱 On an iPhone instead?")))[0]
    assert "Do this on your iPhone" in back.text


async def test_iphone_and_mac_guides_send_their_pictures(service_factory):
    service, _ = service_factory()
    ios = await service.callback(1, "setup:ios")
    assert len(ios) == 2 and ios[1].text == onboarding.PHOTOS_CAPTION and len(ios[1].photos) == 4
    mac = await service.callback(1, "setup:mac")
    assert len(mac) == 2 and len(mac[1].photos) == 2
    # Only these two guides have pictures here; Google's are sent by its own flow.
    assert len(await service.callback(1, "setup:outlook")) == 1
    assert all(photo.startswith(b"\x89PNG") for photo in ios[1].photos + mac[1].photos)


async def test_picture_guides_announce_and_replace_each_other(service_factory):
    service, _ = service_factory()
    ios = await service.callback(1, "setup:ios")
    assert "Picture guide below" in ios[0].text
    assert ios[0].clear_album_group == "setup"
    assert ios[1].album_group == "setup" and len(ios[1].photos) == 4
    # switching to the Mac guide clears the previous album, then shows its own
    mac = await service.callback(1, "setup:mac")
    assert mac[0].clear_album_group == "setup"
    assert mac[1].album_group == "setup" and len(mac[1].photos) == 2
    # a guide with no pictures still clears a stuck album
    outlook = await service.callback(1, "setup:outlook")
    assert outlook[0].clear_album_group == "setup" and len(outlook) == 1


async def test_nusmods_guide_links_to_nusmods(service_factory):
    service, _ = service_factory()
    question = (await service.callback(1, "setup:classes"))[0]
    guide = (await service.callback(1, tap(question, "📚 Add NUSMods")))[0]
    assert "Share/Sync" in guide.text and "lectures, tutorials and labs" in guide.text
    open_button = guide.keyboard.inline_keyboard[0][0]
    assert open_button.url == onboarding.NUSMODS_URL


async def test_old_skip_buttons_still_work(service_factory):
    """Skip explains the limitation and how to connect later."""
    service, _ = service_factory()
    skipped = (await service.callback(1, "setup:skip"))[0]
    assert skipped.edit and "plans you’ve saved with me" in skipped.text
    assert "won’t be included" in skipped.text and "/calendar" in skipped.text


async def test_skip_button_and_existing_menu_callbacks_work(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    skipped = (await service.callback(1, tap(welcome, "Skip calendar setup")))[0]
    assert skipped.text == onboarding.SKIPPED and skipped.edit
    assert (await service.callback(1, "menu:plans"))[0].text.startswith("Nothing coming up")
    assert "Reminders: on" in (await service.callback(1, "menu:settings"))[0].text


async def test_nusmods_guide_returns_to_classes_question(service_factory):
    service, _ = service_factory()
    question = (await service.callback(1, "setup:classes"))[0]
    guide = (await service.callback(1, tap(question, "📚 Add NUSMods")))[0]
    assert labels(guide) == ["Open NUSMods ↗", "‹ Back"]
    assert "select the correct semester" in guide.text
    assert "Paste the link into this chat and send it" in guide.text
    assert "classes and exams" in guide.text
    back = (await service.callback(1, tap(guide, "‹ Back")))[0]
    assert back.text == onboarding.CLASSES_QUESTION and back.edit
    assert labels(back) == ["📚 Add NUSMods", "Continue"]


@pytest.mark.parametrize("add_nusmods", [False, True])
async def test_calendar_first_flow_keeps_connection_through_classes_step(
    service_factory, reference, add_nusmods
):
    service, repo = service_factory()
    service.calendar_reader = FakeReader(busy=[FRIDAY_DINNER])
    connected = (await service.message(1, 1, GOOGLE, reference))[0]
    assert labels(connected) == ["📚 Add NUSMods", "Continue"]
    if add_nusmods:
        guide = (await service.callback(1, tap(connected, "📚 Add NUSMods")))[0]
        back = (await service.callback(1, tap(guide, "‹ Back")))[0]
        await service.callback(1, tap(back, "📚 Add NUSMods"))
        prompt = (
            await service.message(
                1, 2, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
            )
        )[0]
        assert "Timetable connected" in prompt.text and "personal calendar events" in prompt.text
    else:
        prompt = (await service.callback(1, tap(connected, "Continue")))[0]
        assert prompt.edit and "You can add NUSMods later" in prompt.text
    assert not labels(prompt) and prompt.parse_mode == "HTML"
    assert "<b>Add to plans</b>" in prompt.text and "<b>Skip</b>" in prompt.text
    user = await repo.ensure_user(1)
    assert len(await repo.list_calendars(user.id)) == 1


async def test_timetable_already_connected_does_not_repeat_classes_setup(
    service_factory, reference
):
    service, _ = service_factory()
    await onboard(service, reference)
    service.calendar_reader = FakeReader(busy=[FRIDAY_DINNER])
    reply = (await service.message(1, 2, GOOGLE, reference))[0]
    assert not labels(reply) and "Are your classes included" not in reply.text
    assert "<b>Add to plans</b>" in reply.text


@pytest.mark.parametrize(
    "calendar_name,displayed_name",
    [
        ("Google Calendar", "Google Calendar"),
        ("CCA <study> & friends", "CCA &lt;study&gt; &amp; friends"),
    ],
)
async def test_after_a_calendar_link_start_says_all_set(
    service_factory, reference, calendar_name, displayed_name
):
    service, _ = service_factory()
    service.calendar_reader = FakeReader(labels={GOOGLE: calendar_name})
    await service.message(1, 1, GOOGLE, reference)
    again = (await service.command(1, "start"))[0]
    assert (
        again.text.startswith("You're all set ✅")
        and f"your calendar ({displayed_name})" in again.text
    )
    assert again.parse_mode == "HTML"


async def test_after_nusmods_start_says_all_set(service_factory, reference):
    service, _ = service_factory()
    await onboard(service, reference)
    again = (await service.command(1, "start"))[0]
    assert (
        again.text.startswith("You're all set ✅")
        and "NUSMods (2026-2027, semester 1)" in again.text
    )


@pytest.mark.parametrize("with_personal_calendar", [False, True])
@pytest.mark.parametrize("with_nusmods", [False, True])
async def test_calendar_offers_nusmods_setup_and_updates_after_onboarding(
    service_factory, reference, with_personal_calendar, with_nusmods
):
    service, repo = service_factory()
    if with_personal_calendar:
        service.calendar_reader = FakeReader()
        await service.message(1, 1, GOOGLE, reference)
    if with_nusmods:
        await onboard(service, reference)
    status = (await service.command(1, "calendar"))[0]
    assert "Share/Sync link" in status.text
    if with_nusmods:
        assert "NUSMods: 2026-2027, semester 1" in status.text
        assert "update your timetable" in status.text
    else:
        assert "Add your classes" in status.text
    assert "📤 Plans in my calendar app" not in labels(status)
    guide = (await service.callback(1, tap(status, "📚 Add or update NUSMods")))[0]
    assert guide.edit and guide.parse_mode == "HTML"
    assert "Share/Sync" in guide.text and "Paste the link into this chat" in guide.text
    assert guide.keyboard.inline_keyboard[0][0].url == onboarding.NUSMODS_URL
    added = (
        await service.message(
            1, 2, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
        )
    )[0]
    assert "<b>Timetable connected</b>" in added.text
    assert not labels(added) and added.parse_mode == "HTML"
    status = (await service.command(1, "calendar"))[0]
    assert "NUSMods: 2026-2027, semester 1" in status.text
    user = await repo.ensure_user(1)
    assert len(await repo.list_calendars(user.id)) == int(with_personal_calendar)


async def test_a_random_link_is_explained(service_factory, reference):
    service, _ = service_factory(extraction())
    reply = (await service.message(1, 1, "https://www.youtube.com/watch?v=abc", reference))[0]
    assert reply.text == onboarding.NOT_A_SCHEDULE_LINK
    assert service.extractor.calls == []  # no LLM call for a bare link
    # One tap to the right guide, instead of "send /start and look again".
    assert labels(reply) == ["🍎 iPhone / iCloud", "📅 Google", "📧 Outlook", "📚 NUSMods"]
    guide = (await service.callback(1, tap(reply, "🍎 iPhone / iCloud")))[0]
    assert guide.edit and "Share Link" in guide.text


async def test_first_calendar_offers_a_one_tap_try(service_factory, reference):
    service, _ = service_factory()
    service.calendar_reader = FakeReader(busy=[FRIDAY_DINNER])
    reply = (await service.message(1, 1, GOOGLE, reference))[0]
    assert labels(reply) == ["📚 Add NUSMods", "Continue"]
    prompt = (await service.callback(1, tap(reply, "Continue")))[0]
    assert prompt.edit and prompt.text == onboarding.SEND_PLAN
    assert "Are your classes included" in reply.text


async def test_an_empty_calendar_offers_the_other_guides(service_factory, reference):
    service, _ = service_factory()
    service.calendar_reader = FakeReader()  # connects fine, but has no events
    reply = (await service.message(1, 1, GOOGLE, reference))[0]
    assert "don’t see any events" in reply.text
    assert labels(reply) == ["📚 Add NUSMods", "Continue"]


async def test_group_card_deep_links_still_work(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "start", "edit_00000000-0000-0000-0000-000000000000"))[0]
    assert reply.text != onboarding.WELCOME


async def test_unknown_setup_button(service_factory):
    service, _ = service_factory()
    assert "isn't valid" in (await service.callback(1, "setup:nope"))[0].text


def test_messages_after_a_link_say_what_to_do_next():
    icloud = onboarding.calendar_added("iPhone calendar", 4)
    assert "Personal calendar connected" in icloud
    assert "Are your classes included" in icloud
    assert "automatically sync" not in icloud
    synced = onboarding.calendar_added("Google Calendar", 2, sync_enabled=True)
    assert "automatically sync" in synced
    empty = onboarding.calendar_added("Google Calendar", 0)
    assert "don’t see any events" in empty and "/calendar" in empty
    nusmods = onboarding.nusmods_added("2026-2027", 1)
    assert "<b>Timetable connected</b>" in nusmods and "Coverage" not in nusmods
    assert "personal calendar events" in nusmods


async def test_welcome_formatting_is_sent_to_telegram():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.bot.messages import Reply, send_reply

    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(edit_message_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, callback_query=query)
    keyboard = onboarding.welcome_keyboard()
    for edit in (False, True):
        await send_reply(update, Reply(onboarding.WELCOME, keyboard, edit=edit, parse_mode="HTML"))
        sender = query.edit_message_text if edit else message.reply_text
        sender.assert_awaited_once_with(
            onboarding.WELCOME, reply_markup=keyboard, parse_mode="HTML"
        )


@pytest.mark.parametrize("screen", ["google", "googlelink"])
@pytest.mark.parametrize("delivery", ["edit", "new", "fallback"])
async def test_google_read_only_guide_formats_without_a_link_preview(
    service_factory, screen, delivery
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from telegram import LinkPreviewOptions
    from telegram.error import BadRequest

    from app.bot.messages import send_reply

    service, _ = service_factory()
    reply = (await service.callback(1, f"setup:{screen}"))[0]
    assert "Google Calendar · read only" in reply.text
    assert "without adding or changing events" in reply.text
    assert reply.parse_mode == "HTML"
    assert reply.disable_link_preview
    reply.edit = delivery != "new"
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(edit_message_text=AsyncMock())
    if delivery == "fallback":
        query.edit_message_text.side_effect = BadRequest("Message can't be edited")
    update = SimpleNamespace(effective_message=message, callback_query=query)
    await send_reply(update, reply)
    sender = query.edit_message_text if delivery == "edit" else message.reply_text
    sender.assert_awaited_once_with(
        reply.text,
        reply_markup=reply.keyboard,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
