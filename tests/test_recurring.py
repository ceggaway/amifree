"""Weekly events ("Saturdays, 9:30am-11:30am"): one date, or every week."""

import pytest

from app.services.date_resolution import weekly_weekday
from tests.conftest import extraction, tap

BHID_POST = """Ready to make an impact while building meaningful connections?

Project BHID is looking for regular volunteers to bring joy and companionship to our residents! 💜

Join our weekly Saturday sessions for fun activities like arts and crafts, exercise and meaningful conversations.

📍 6 Bishan St. 13, S579798
⏰ Saturdays, 9:30am-11:30am

Scan the QR code or visit https://au.cglink.me/2sg/s2382 to sign up by 13 October 2026."""

BHID = extraction(
    title="Project BHID volunteering",
    date_text="Saturdays",
    start_time_text="9:30am",
    end_time_text="11:30am",
    location="6 Bishan St. 13",
)


@pytest.mark.parametrize(
    "text,day",
    [
        ("Saturdays, 9:30am-11:30am", 5),
        ("Yoga every Tuesday 7-8pm", 1),
        ("each wed 6pm", 2),
        ("Thursdays at the hall", 3),
        ("Tuesdays and Thursdays 7pm", None),  # two days: ask instead of guessing
        ("CCA dinner Friday 7-9pm", None),  # one Friday, not every Friday
        ("Sunday brunch", None),
    ],
)
def test_weekly_weekday(text, day):
    assert weekly_weekday(text) == day


async def test_weekly_post_asks_one_or_every_week(service_factory, reference):
    service, _ = service_factory(BHID)
    reply = (await service.message(1, 1, BHID_POST, reference))[0]
    assert reply.text.startswith("🔁 Project BHID volunteering")
    assert "every week: Saturdays, 9:30–11:30am" in reply.text
    labels = [b.text for row in reply.keyboard.inline_keyboard for b in row]
    assert labels == ["Just the next one (Sat 10 Oct)", "Every Saturday", "Neither"]


async def test_just_the_next_one_makes_a_normal_card(service_factory, reference):
    service, repo = service_factory(BHID)
    offer = (await service.message(1, 1, BHID_POST, reference))[0]
    card = (await service.callback(1, tap(offer, "Just the next one (Sat 10 Oct)")))[0]
    assert card.edit and "Sat, 10 Oct 2026 · 09:30 → 10 Oct 11:30" in card.text
    assert "Add to plans" in [b.text for row in card.keyboard.inline_keyboard for b in row]
    assert not (await repo.ensure_user(1)).pending


async def test_every_week_becomes_a_weekly_busy_block(service_factory, reference):
    service, repo = service_factory(
        BHID,
        extraction(
            title="Brunch", date_text="Saturday", start_time_text="10am", end_time_text="12pm"
        ),
    )
    offer = (await service.message(1, 1, BHID_POST, reference))[0]
    confirm = (await service.callback(1, tap(offer, "Every Saturday")))[0]
    assert (
        confirm.edit
        and "Block this time?" in confirm.text
        and "Project BHID volunteering" in confirm.text
    )
    saved = (await service.callback(1, tap(confirm, "Save block")))[0]
    assert "Blocked" in saved.text
    brunch = (await service.message(1, 2, "Brunch Saturday 10am-12pm", reference))[0]
    assert brunch.text.startswith("⚠️ Clashes with Project BHID volunteering")


async def test_neither_and_expired_choices(service_factory, reference):
    service, repo = service_factory(BHID)
    offer = (await service.message(1, 1, BHID_POST, reference))[0]
    assert "Kept unchanged" in (await service.callback(1, tap(offer, "Neither")))[0].text
    assert not (await repo.ensure_user(1)).pending
    assert "expired" in (await service.callback(1, tap(offer, "Every Saturday")))[0].text


async def test_a_weekly_post_without_an_end_time_asks_for_it(service_factory, reference):
    no_end = BHID.model_copy(update={"end_time_text": None})
    service, _ = service_factory(no_end)
    reply = (await service.message(1, 1, "Volunteering Saturdays from 9:30am", reference))[0]
    assert reply.text.startswith("What time does this event end?")
