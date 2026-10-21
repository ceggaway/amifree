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
    # Three numbered steps: connect a calendar, add NUSMods, forward a plan. Setup is
    # optional, and the four everyday buttons are already here.
    assert "1️⃣ <b>Connect your calendar</b>" in reply.text
    assert "2️⃣ <b>Add your NUSMods</b>" in reply.text
    assert "3️⃣ <b>Forward me a plan</b>" in reply.text
    assert "skip 1 and 2" in reply.text
    assert labels(reply) == [
        "📅 Connect my calendar",
        "📚 Add my NUSMods",
        "📋 My plans",
        "🕒 Am I free?",
        "📌 Deadlines",
        "⚙️ Settings",
    ]


async def test_every_guide_is_reachable_and_comes_back(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    picker = (await service.callback(1, tap(welcome, "📅 Connect my calendar")))[0]
    assert picker.edit and labels(picker) == [
        "🍎 iPhone / iCloud",
        "📅 Google",
        "📧 Outlook",
        "« Back",
    ]
    expected = {
        "🍎 iPhone / iCloud": ["Public Calendar", "Share Link", "webcal://"],
        "📅 Google": ["calendar.google.com", "Integrate calendar", "Secret address in iCal format"],
        "📧 Outlook": ["coming soon"],
    }
    for button, phrases in expected.items():
        guide = (await service.callback(1, tap(picker, button)))[0]
        assert guide.edit and all(p in guide.text for p in phrases), button
        back = (await service.callback(1, tap(guide, "« Other calendars")))[0]
        assert back.text == onboarding.PICK_CALENDAR
    home = (await service.callback(1, tap(picker, "« Back")))[0]
    assert home.text == onboarding.WELCOME
    assert home.parse_mode == "HTML"
    assert picker.parse_mode is None


async def test_picker_explains_phone_setup_and_outlook_availability(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    picker = (await service.callback(1, tap(welcome, "📅 Connect my calendar")))[0]
    assert "iPhone works right from your phone" in picker.text
    assert "Google works in the Google Calendar app or a computer browser" in picker.text
    assert "Outlook is coming soon" in picker.text


async def test_every_guide_ends_with_what_to_do_next(service_factory):
    service, _ = service_factory()
    for screen in ("ios", "google", "nusmods"):
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
        "nusmods": ("Open NUSMods", "<code>https://nusmods.com/timetable</code>"),
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
    welcome = (await service.command(1, "start"))[0]
    guide = (await service.callback(1, tap(welcome, "📚 Add my NUSMods")))[0]
    assert "Share/Sync" in guide.text and "every class type" in guide.text
    open_button = guide.keyboard.inline_keyboard[0][0]
    assert open_button.url == onboarding.NUSMODS_URL


async def test_old_skip_buttons_still_work(service_factory):
    """Welcome messages sent before Skip was removed still have the button."""
    service, _ = service_factory()
    skipped = (await service.callback(1, "setup:skip"))[0]
    assert skipped.edit and skipped.text.startswith("✅ All set") and "/calendar" in skipped.text


async def test_welcome_menu_buttons_work_before_any_setup(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    assert (await service.callback(1, tap(welcome, "📋 My plans")))[0].text.startswith(
        "Nothing coming up"
    )
    assert "Reminders: on" in (await service.callback(1, tap(welcome, "⚙️ Settings")))[0].text


async def test_nusmods_guide_points_to_its_own_calendar_export(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    guide = (await service.callback(1, tap(welcome, "📚 Add my NUSMods")))[0]
    export = (await service.callback(1, tap(guide, "Classes in my own calendar?")))[0]
    assert export.edit and "Export" in export.text and "not your classes" in export.text
    back = (await service.callback(1, tap(export, "« NUSMods link")))[0]
    assert back.text.startswith("📚 <b>Your NUSMods link</b>")


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
    assert "📤 Plans in my calendar app" in labels(status)
    guide = (await service.callback(1, tap(status, "📚 Add or update NUSMods")))[0]
    assert guide.edit and guide.parse_mode == "HTML"
    assert "Share/Sync" in guide.text and "Paste the link here" in guide.text
    assert guide.keyboard.inline_keyboard[0][0].url == onboarding.NUSMODS_URL
    added = (
        await service.message(
            1, 2, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
        )
    )[0]
    assert "Schedule added!" in added.text
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
    assert labels(reply) == ["🕒 Am I free?"]
    assert "forward me any plan" in reply.text


async def test_an_empty_calendar_offers_the_other_guides(service_factory, reference):
    service, _ = service_factory()
    service.calendar_reader = FakeReader()  # connects fine, but has no events
    reply = (await service.message(1, 1, GOOGLE, reference))[0]
    assert "don't see any events" in reply.text
    assert labels(reply) == ["🍎 iPhone / iCloud", "📅 Google", "📧 Outlook", "📚 NUSMods"]


async def test_group_card_deep_links_still_work(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "start", "edit_00000000-0000-0000-0000-000000000000"))[0]
    assert reply.text != onboarding.WELCOME


async def test_unknown_setup_button(service_factory):
    service, _ = service_factory()
    assert "isn't valid" in (await service.callback(1, "setup:nope"))[0].text


def test_messages_after_a_link_say_what_to_do_next():
    icloud = onboarding.calendar_added("iPhone calendar", 4)
    assert "your iPhone calendar" in icloud and "forward me any plan" in icloud
    assert "your CCA calendar" in onboarding.calendar_added("CCA", 2)
    assert "clash checks" not in icloud and "/whosfree" not in icloud  # no jargon on day one
    empty = onboarding.calendar_added("Google Calendar", 0)
    assert "don't see any events" in empty and "/start" in empty
    nusmods = onboarding.nusmods_added("2026-2027", 1)
    assert nusmods.startswith("✅ Schedule added!") and "Coverage" not in nusmods


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
