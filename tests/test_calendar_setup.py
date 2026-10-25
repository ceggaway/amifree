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


async def test_first_plan_asks_once_then_again_at_three_then_never(service_factory, reference):
    service, _ = service_factory(*(plan(n) for n in range(1, 6)))
    await onboard(service, reference)
    first = await keep(service, reference, 1)
    assert len(first) == 2 and first[1].text == onboarding.CALENDAR_PROMPT
    assert labels(first[1]) == ["Set up (1 min)", "Not now"]
    assert len(await keep(service, reference, 2)) == 1
    third = await keep(service, reference, 3)
    assert len(third) == 2 and third[1].text == onboarding.CALENDAR_PROMPT
    assert len(await keep(service, reference, 4)) == 1
    assert len(await keep(service, reference, 5)) == 1


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
    assert "Step 1 of 3" in step1.text and "Open Google Calendar ↗" in labels(step1)
    step2 = (await service.callback(1, tap(step1, "✅ Done, I've shared it")))[0]
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
    [(telegram_id, reply)] = sent.messages
    assert telegram_id == 1 and "Part 1 done" in reply.text and "Part 2 of 2" in reply.text
    assert "⏭ Skip this part" in labels(reply)
    assert onboarding.FEED_REFRESH_TIP in reply.text
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
    text = sent.messages[0][1].text
    assert text.startswith(onboarding.APPLE_ALL_SET) and "Every 5 minutes" in text


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
