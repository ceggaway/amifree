"""Calendar setup: asked after the first plan, one question, then Google or Apple step by step."""

from datetime import timedelta

import httpx

from app.bot import onboarding
from app.config import Settings
from app.main import create_app
from app.services.google_calendar import Access
from tests.conftest import extraction, onboard, tap
from tests.test_google_calendar import FakeGoogle
from tests.test_personal_calendar import ICLOUD_CCA, FakeReader

BASE = "https://amifree.example"


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row] if reply.keyboard else []


def plan(n):
    return extraction(title=f"Plan {n}", date_text=f"{10 + n} Oct")


async def keep(service, reference, n):
    """Forward plan n and tap Add to plans. Returns every reply to the tap."""
    card = (await service.message(1, 10 + n, f"Plan {n} on {10 + n} Oct 7-9pm", reference))[0]
    return await service.callback(1, tap(card, "Add to plans"))


class Sent:
    def __init__(self):
        self.messages = []

    async def __call__(self, telegram_id, reply):
        self.messages.append((telegram_id, reply))


# ---- the prompt ----


async def test_asks_at_the_first_third_and_sixth_plan_then_never(service_factory, reference):
    service, _ = service_factory(*(plan(n) for n in range(1, 9)))
    await onboard(service, reference)
    first = await keep(service, reference, 1)
    assert len(first) == 2 and first[1].text == onboarding.CALENDAR_PROMPT
    assert labels(first[1]) == ["Set up (1 min)", "Not now"]
    assert len(await keep(service, reference, 2)) == 1
    third = await keep(service, reference, 3)
    assert len(third) == 2 and third[1].text == onboarding.CALENDAR_PROMPT
    assert len(await keep(service, reference, 4)) == 1
    assert len(await keep(service, reference, 5)) == 1
    sixth = await keep(service, reference, 6)
    # The last ask says it's the last, and that clashes are still checked without one.
    assert sixth[1].text == onboarding.CALENDAR_PROMPT + "\n\n" + onboarding.LAST_CALENDAR_ASK
    assert "still check new plans for clashes" in sixth[1].text
    assert len(await keep(service, reference, 7)) == 1
    assert len(await keep(service, reference, 8)) == 1


async def test_prompt_buttons(service_factory, reference):
    service, _ = service_factory(plan(1))
    await onboard(service, reference)
    prompt = (await keep(service, reference, 1))[1]
    setup = (await service.callback(1, tap(prompt, "Set up (1 min)")))[0]
    assert setup.edit and setup.text == onboarding.PICK_CALENDAR
    later = (await service.callback(1, tap(prompt, "Not now")))[0]
    assert later.edit and "/calendar" in later.text and "🍎" in later.text


async def test_no_prompt_once_plans_already_go_to_a_calendar(service_factory, reference):
    service, repo = service_factory(plan(1))
    await onboard(service, reference)
    await repo.set_user_flags(repo.users[1].id, google_connected=True)
    assert len(await keep(service, reference, 1)) == 1


# ---- Google: share, email, code ----


async def test_google_is_three_steps(service_factory, reference):
    service, repo = service_factory()
    service.google = FakeGoogle()
    await onboard(service, reference)
    step1 = (await service.callback(1, "setup:google"))[0]
    assert "Step 1 of 3" in step1.text and "Next ›" in labels(step1)
    last = (await service.callback(1, "gs:phone:5"))[0]
    step2 = (await service.callback(1, tap(last, "✅ Done, I've shared it")))[0]
    assert "Step 2 of 3" in step2.text and not step2.edit  # the steps stay readable above
    assert repo.users[1].pending == {"stage": "gcal_email"}
    again = (await service.message(1, 5, "me@gmail", reference))[0]
    assert again.text == onboarding.GOOGLE_EMAIL_AGAIN
    step3 = (await service.message(1, 6, "me@gmail.com", reference))[0]
    assert "Step 3 of 3" in step3.text and "6-digit" in step3.text
    done = (await service.message(1, 7, service.google.code, reference))[0]
    assert "Google Calendar connected" in done.text and repo.users[1].google_connected


async def test_an_ordinary_message_closes_the_email_question(service_factory, reference):
    service, repo = service_factory(extraction())
    service.google = FakeGoogle()
    await onboard(service, reference)
    await service.callback(1, "gcal:done")
    card = (await service.message(1, 5, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    assert "CCA dinner" in card.text
    assert (repo.users[1].pending or {}).get("stage") != "gcal_email"


async def test_blocked_sharing_offers_the_link_instead(service_factory, reference):
    service, _ = service_factory()
    service.google = FakeGoogle(Access.NONE)
    await onboard(service, reference)
    reply = (await service.message(1, 5, "me@school.edu", reference))[0]
    assert "school and work accounts" in reply.text
    link = (await service.callback(1, tap(reply, "Use a link instead")))[0]
    assert "Secret address in iCal format" in link.text


# ---- Apple: subscribe, then the busy-times link ----


async def test_apple_part_one_is_one_tap_to_subscribe(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    await onboard(service, reference)
    part1 = (await service.callback(1, "setup:apple"))[0]
    assert "Part 1 of 2" in part1.text and part1.parse_mode == "HTML"
    # The refresh setting is part of part 1, before moving on.
    assert "Fetch New Data" in part1.text and "Every 5 minutes" in part1.text
    token = repo.users[1].calendar_feed_token
    add = part1.keyboard.inline_keyboard[0][0]
    assert add.text == "➕ Add to Calendar ↗" and add.url == f"{BASE}/cal/{token}/add"
    assert {"Didn't work? Show the manual steps", "⏭ Skip to part 2"} <= set(labels(part1))
    page = await service.subscribe_page(token)
    assert f'href="webcal://amifree.example/cal/{token}.ics"' in page
    assert await service.subscribe_page("x" * 43) is None


async def test_first_feed_fetch_moves_on_to_part_two(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    service.send_private = sent = Sent()
    await onboard(service, reference)
    await service.callback(1, "setup:apple")
    token = repo.users[1].calendar_feed_token
    await service.calendar_feed(token, "TelegramBot (like TwitterBot)")  # a preview isn't it
    assert sent.messages == []
    await service.calendar_feed(token, "iOS/18.0 (22A) dataaccessd/1.0")
    [(telegram_id, step)] = sent.messages
    assert telegram_id == 1 and step.text.startswith(onboarding.APPLE_PART1_DONE)
    assert "Part 2 of 2" in step.text and "Step 1 of 4" in step.text and step.photo
    assert "⏭ Skip this part" in labels(step)
    service.clock = lambda: reference + timedelta(hours=1)
    await service.calendar_feed(token, "iOS/18.0 (22A) dataaccessd/1.0")
    assert len(sent.messages) == 1  # only the first fetch announces
    # Coming back to Apple setup now starts at part 2.
    again = (await service.callback(1, "setup:apple"))[0]
    assert "Part 1 done" in again.text and "Part 2 of 2" in again.text


async def test_feed_after_a_link_says_all_set(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    service.calendar_reader = FakeReader((), None, {ICLOUD_CCA: "CCA"})
    service.send_private = sent = Sent()
    await onboard(service, reference)
    await service.message(1, 5, ICLOUD_CCA, reference)
    await service.callback(1, "setup:apple")
    await service.calendar_feed(repo.users[1].calendar_feed_token, "iOS/18.0")
    assert sent.messages[0][1].text == onboarding.APPLE_ALL_SET


async def test_half_done_apple_setup_shows_in_calendar(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    await onboard(service, reference)
    await service.callback(1, "setup:apple")
    await service.calendar_feed(repo.users[1].calendar_feed_token, "iOS/18.0")
    status = (await service.command(1, "calendar"))[0]
    assert "✓ adding plans · ✗ not checking busy times" in status.text
    part2 = (await service.callback(1, tap(status, "🍎 Finish Apple setup")))[0]
    assert "Part 2 of 2" in part2.text
    skipped = (await service.callback(1, tap(part2, "⏭ Skip this part")))[0]
    assert skipped.text == onboarding.APPLE_SKIPPED


async def test_apple_without_a_public_address_goes_to_busy_times(service_factory, reference):
    service, _ = service_factory()
    await onboard(service, reference)
    reply = (await service.callback(1, "setup:apple"))[0]
    assert "isn't available yet" in reply.text and "Part 2 of 2" in reply.text


async def test_subscribe_page_route(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    await onboard(service, reference)
    await service.callback(1, "setup:apple")
    token = repo.users[1].calendar_feed_token
    app = create_app(Settings(_env_file=None, bot_mode="disabled"), service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        ok = await client.get(f"/cal/{token}/add")
        assert ok.status_code == 200 and "webcal://" in ok.text
        assert ok.headers["cache-control"] == "no-store"
        assert (await client.get("/cal/" + "x" * 43 + "/add")).status_code == 404
    # Opening the page is not the calendar subscribing.
    assert repo.users[1].calendar_feed_seen_at is None


async def test_apple_part_two_is_one_step_and_picture_at_a_time(service_factory, reference):
    service, _ = service_factory()
    await onboard(service, reference)
    pictures = set()
    step = (await service.callback(1, "setup:apple2"))[0]
    for n in range(1, 5):
        assert f"Step {n} of 4" in step.text and step.edit and step.photo
        assert len(step.text) <= 1024  # Telegram's caption limit
        pictures.add(step.photo)
        assert ("‹ Back" in labels(step)) == (n > 1)
        assert ("📅 Mine's Google" in labels(step)) == (n == 2)  # where Gmail is on screen
        assert "⏭ Skip this part" in labels(step)
        if n < 4:
            step = (await service.callback(1, tap(step, "Next ›")))[0]
    assert "Paste the link here" in step.text and "Next ›" not in labels(step)
    assert len(pictures) == 4  # each step shows its own screen
    back = (await service.callback(1, tap(step, "‹ Back")))[0]
    assert "Step 3 of 4" in back.text and "Public Calendar" in back.text
    mac = (await service.callback(1, "setup:apple2mac"))[0]
    assert "On a Mac · Step 1 of 2" in mac.text and "📱 On an iPhone instead?" in labels(mac)
    last = (await service.callback(1, tap(mac, "Next ›")))[0]
    assert "Step 2 of 2" in last.text and last.photo != mac.photo
    for bad in ("a2:ios:5", "a2:ios:0", "a2:pc:1", "a2:ios"):
        assert "isn't valid" in (await service.callback(1, bad))[0].text


async def test_a_step_swaps_its_picture_in_place():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.bot.messages import Reply, send_reply

    text, photo, keyboard = onboarding.apple_step("ios", 2)
    on_picture = SimpleNamespace(photo=[object()], delete=AsyncMock())
    query = SimpleNamespace(
        message=on_picture, edit_message_media=AsyncMock(), edit_message_text=AsyncMock()
    )
    message = SimpleNamespace(reply_photo=AsyncMock(), reply_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_message=message)
    await send_reply(update, Reply(text, keyboard, edit=True, photo=photo, parse_mode="HTML"))
    query.edit_message_media.assert_awaited_once()
    message.reply_photo.assert_not_awaited()
    # Leaving the steps (Skip) replaces the picture with a text message.
    await send_reply(update, Reply(onboarding.APPLE_SKIPPED, edit=True))
    on_picture.delete.assert_awaited_once()
    message.reply_text.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()


async def test_deleting_data_says_how_to_unlink_each_calendar(service_factory, reference):
    from tests.test_google_calendar import BOT, connect

    service, repo = service_factory(app_base_url=BASE)
    service.google = FakeGoogle()
    service.calendar_reader = FakeReader((), None, {ICLOUD_CCA: "CCA"})
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference)
    await service.message(1, 30, ICLOUD_CCA, reference)
    await service.callback(1, "setup:apple")
    asked = (await service.command(1, "deletemydata"))[0]
    done = (await service.callback(1, tap(asked, "Delete my data")))[0]
    assert "has been deleted" in done.text and done.parse_mode == "HTML"
    # Deleting here can't undo anything in the user's own apps, so each one is spelled out.
    assert "Google Calendar you shared with me" in done.text and f"<code>{BOT}</code>" in done.text
    assert "iCloud calendar you made public" in done.text and "Public Calendar" in done.text
    assert "Calendar Accounts → <b>AmIFree</b> → Delete Account" in done.text
    assert "Google secret link" not in done.text  # only what they connected
    [button] = [b for row in done.keyboard.inline_keyboard for b in row]
    # Straight to that calendar's settings page: its id (the email) in base64.
    assert button.url == onboarding.GOOGLE_SETTINGS + "/calendar/bWVAZ21haWwuY29t"


async def test_removing_one_link_only_explains_that_one(service_factory, reference):
    from tests.test_personal_calendar import GOOGLE

    service, _ = service_factory()
    service.calendar_reader = FakeReader((), None, {})
    await onboard(service, reference)
    await service.message(1, 30, GOOGLE, reference)
    status = (await service.command(1, "calendar"))[0]
    remove = next(
        b.text for row in status.keyboard.inline_keyboard for b in row if "Remove" in b.text
    )
    replies = await service.callback(1, tap(status, remove))
    assert len(replies) == 2 and "Google secret link" in replies[1].text
    assert "iCloud" not in replies[1].text and "AmIFree calendar" not in replies[1].text
    assert replies[1].keyboard.inline_keyboard[0][0].url == onboarding.GOOGLE_SETTINGS


def test_cards_name_the_calendar_plans_sync_to():
    from datetime import datetime

    from app.models.user import User
    from app.services.scheduling import synced

    user = User(id="u", telegram_user_id=1)
    assert synced(user) == ""
    user.google_connected = True
    assert synced(user) == "your Google Calendar"
    user.calendar_feed_token, user.calendar_feed_seen_at = "t", datetime(2026, 10, 1)
    assert synced(user) == "your Google Calendar and AmIFree calendar"
    user.google_connected = False
    assert synced(user) == "your AmIFree calendar"


async def test_half_done_apple_has_one_apple_button(service_factory, reference):
    service, repo = service_factory(app_base_url=BASE)
    await onboard(service, reference)
    await service.callback(1, "setup:apple")
    await service.calendar_feed(repo.users[1].calendar_feed_token, "iOS/18.0")
    status = (await service.command(1, "calendar"))[0]
    apple = [label for label in labels(status) if "🍎" in label]
    assert apple == ["🍎 Finish Apple setup", "🍎 AmIFree calendar settings"]
