"""Weekly events ("Saturdays, 9:30am-11:30am"): one date, or every week."""

import pytest

from app.services.date_resolution import weekly_weekday, weekly_weekdays
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
    assert card.edit and "10 Oct 2026 (Saturday) · 9:30AM–11:30AM" in card.text
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
    assert brunch.text.startswith("⚠️ Overlaps with Project BHID volunteering")


async def test_neither_and_expired_choices(service_factory, reference):
    service, repo = service_factory(BHID)
    offer = (await service.message(1, 1, BHID_POST, reference))[0]
    assert "not added" in (await service.callback(1, tap(offer, "Neither")))[0].text
    assert not (await repo.ensure_user(1)).pending
    assert "expired" in (await service.callback(1, tap(offer, "Every Saturday")))[0].text


@pytest.mark.parametrize(
    "text,days",
    [
        ("Badminton Tuesdays and Thursdays 7-9pm", [1, 3]),
        ("Training every Tue/Thu 7-9pm", [1, 3]),
        ("Gym every Mon, Wed & Fri 6pm", [0, 2, 4]),
        ("every sat and sun 9am", [5, 6]),
        ("Yoga every Tuesday 7-8pm", [1]),
        ("meet every month to plan", []),  # "month" is not Monday
        ("CCA dinner Friday 7-9pm", []),
        # One plural day makes the days listed with it weekly too.
        ("Badminton wednesday and Thursdays 7-9pm", [2, 3]),
        ("Badminton Wednesdayand Thursdays 7-9pm", [2, 3]),
        ("Sat and Sundays 9am", [5, 6]),
        ("Training Tuesdays 7pm and Thursday dinner", [1]),
        ("Dinner Wednesday and Thursday 7pm", []),  # two dates, not weekly
    ],
)
def test_weekly_weekdays(text, days):
    assert weekly_weekdays(text) == days


BADMINTON = extraction(
    title="Badminton",
    date_text=None,
    start_time_text="7",
    end_time_text="9pm",
    location="USC",
    ambiguity="recurring",
)
TWICE = "Badminton Tuesdays and Thursdays 7-9pm at USC"


async def test_two_weekdays_ask_one_or_every_week(service_factory, reference):
    service, _ = service_factory(BADMINTON)
    reply = (await service.message(1, 1, TWICE, reference))[0]
    assert reply.text.startswith("🔁 Badminton")
    assert "every week: Tue & Thu, 7–9pm" in reply.text
    labels = [b.text for row in reply.keyboard.inline_keyboard for b in row]
    # Sunday 4 Oct: Tuesday 6 Oct comes first.
    assert labels == ["Just the next one (Tue 6 Oct)", "Every Tue & Thu", "Neither"]


async def test_weekly_hours_listed_in_other_times_still_get_the_offer(service_factory, reference):
    from app.schemas.event import OtherTime

    # The model put "7-9pm" in other_times and left the main fields empty.
    listed = BADMINTON.model_copy(
        update={
            "start_time_text": None,
            "end_time_text": None,
            "relation": "one",
            "other_times": [
                OtherTime(
                    title=None,
                    date_text=None,
                    start_time_text="7",
                    end_time_text="9pm",
                    end_date_text=None,
                    location="UTSRC",
                )
            ],
        }
    )
    service, _ = service_factory(listed)
    text = "Training every Tue/Thu 7-9pm at UTSRC"
    reply = (await service.message(1, 1, text, reference))[0]
    assert "every week: Tue & Thu, 7–9pm" in reply.text


async def test_every_tue_and_thu_saves_one_block_per_day(service_factory, reference):
    service, repo = service_factory(BADMINTON)
    offer = (await service.message(1, 1, TWICE, reference))[0]
    confirm = (await service.callback(1, tap(offer, "Every Tue & Thu")))[0]
    assert confirm.text.startswith("Block these times?")
    assert "Every Tuesday 19:00 → 21:00" in confirm.text
    assert "Every Thursday 19:00 → 21:00" in confirm.text
    saved = (await service.callback(1, tap(confirm, "Save blocks")))[0]
    assert saved.text.startswith("Blocked:")
    blocks = await repo.list_blocks((await repo.ensure_user(1)).id)
    assert sorted((b.weekday, f"{b.start_date:%a %-d %b}") for b in blocks) == [
        (1, "Tue 6 Oct"),
        (3, "Thu 8 Oct"),
    ]


async def test_a_preview_saved_before_several_days_still_saves(service_factory, reference):
    service, repo = service_factory(BHID)
    offer = (await service.message(1, 1, BHID_POST, reference))[0]
    user = await repo.ensure_user(1)
    old = dict(user.pending)
    old["weekday"] = old.pop("weekdays")[0]  # what an offer looked like before this change
    await repo.set_pending(user.id, old)
    confirm = (await service.callback(1, tap(offer, "Every Saturday")))[0]
    user = await repo.ensure_user(1)
    old = dict(user.pending)
    old["rule"] = old.pop("rules")[0]
    await repo.set_pending(user.id, old)
    saved = (await service.callback(1, tap(confirm, "Save block")))[0]
    assert saved.text.startswith("Blocked:")
    assert len(await repo.list_blocks(user.id)) == 1


async def test_a_weekly_post_without_an_end_time_asks_for_it(service_factory, reference):
    no_end = BHID.model_copy(update={"end_time_text": None})
    service, _ = service_factory(no_end)
    reply = (await service.message(1, 1, "Volunteering Saturdays from 9:30am", reference))[0]
    assert reply.text.startswith("What time does this event end?")


async def test_a_new_message_closes_the_every_week_question(service_factory, reference):
    service, repo = service_factory(BADMINTON, BADMINTON)
    first = (await service.message(1, 1, TWICE, reference))[0]
    again = (await service.message(1, 2, TWICE, reference))[0]
    assert again.text.startswith("🔁 Badminton")
    assert (await repo.ensure_user(1)).pending["message_id"] == 2
    assert "expired" in (await service.callback(1, tap(first, "Every Tue & Thu")))[0].text
