"""First-run setup: welcome, step-by-step link guides, and what happens after a link."""

from app.bot import onboarding
from tests.conftest import extraction, tap
from tests.test_personal_calendar import GOOGLE, FakeReader


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row] if reply.keyboard else []


async def test_new_user_gets_the_welcome(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "start"))[0]
    assert reply.text == onboarding.WELCOME
    assert labels(reply) == ["📚 NUSMods link?", "📅 Calendar link?", "Skip"]


async def test_every_guide_is_reachable_and_comes_back(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    picker = (await service.callback(1, tap(welcome, "📅 Calendar link?")))[0]
    assert picker.edit and labels(picker) == [
        "🍎 iPhone / iCloud",
        "📅 Google",
        "📧 Outlook",
        "« Back",
    ]
    expected = {
        "🍎 iPhone / iCloud": ["Public Calendar", "Share Link", "webcal://"],
        "📅 Google": ["calendar.google.com", "Integrate calendar", "Secret address in iCal format"],
        "📧 Outlook": ["Publish a calendar", "Can view when I'm busy"],
    }
    for button, phrases in expected.items():
        guide = (await service.callback(1, tap(picker, button)))[0]
        assert guide.edit and all(p in guide.text for p in phrases), button
        back = (await service.callback(1, tap(guide, "« Other calendars")))[0]
        assert back.text == onboarding.PICK_CALENDAR
    home = (await service.callback(1, tap(picker, "« Back")))[0]
    assert home.text == onboarding.WELCOME


async def test_nusmods_guide_links_to_nusmods(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    guide = (await service.callback(1, tap(welcome, "📚 NUSMods link?")))[0]
    assert "Share/Sync" in guide.text and "every class type" in guide.text
    open_button = guide.keyboard.inline_keyboard[0][0]
    assert open_button.url == onboarding.NUSMODS_URL


async def test_skip(service_factory):
    service, _ = service_factory()
    welcome = (await service.command(1, "start"))[0]
    skipped = (await service.callback(1, tap(welcome, "Skip")))[0]
    assert skipped.edit and skipped.text.startswith("✅ All set") and "/calendar" in skipped.text


async def test_after_a_calendar_link_start_says_all_set(service_factory, reference):
    service, _ = service_factory()
    service.calendar_reader = FakeReader()
    await service.message(1, 1, GOOGLE, reference)
    again = (await service.command(1, "start"))[0]
    assert (
        again.text.startswith("You're all set ✅")
        and "your calendar (Google Calendar)" in again.text
    )


async def test_after_nusmods_start_says_all_set(service_factory, reference):
    from tests.conftest import onboard

    service, _ = service_factory()
    await onboard(service, reference)
    again = (await service.command(1, "start"))[0]
    assert (
        again.text.startswith("You're all set ✅")
        and "NUSMods (2026-2027, semester 1)" in again.text
    )


async def test_a_random_link_is_explained(service_factory, reference):
    service, _ = service_factory(extraction())
    reply = (await service.message(1, 1, "https://www.youtube.com/watch?v=abc", reference))[0]
    assert reply.text == onboarding.NOT_A_SCHEDULE_LINK
    assert service.extractor.calls == []  # no LLM call for a bare link


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
