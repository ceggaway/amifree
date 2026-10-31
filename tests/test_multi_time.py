"""Two or more times in one message: one picker, "and" adds events, "or" chooses a time."""

from app.schemas.event import OtherTime, Status
from app.services.multi_time import (
    fold_answer,
    is_multi,
    item_message_id,
    read_times,
    settle_times,
    split_times,
)
from tests.conftest import extraction, tap

TRAINING = "Training Tue 7-9pm and Thu 6-8pm at UTown"
DINNER = "Dinner Fri 7pm or Sat 6pm, whichever works"


def other(**fields):
    blank = dict(
        title=None,
        date_text=None,
        start_time_text=None,
        end_time_text=None,
        end_date_text=None,
        location=None,
    )
    return OtherTime(**(blank | fields))


def trainings():
    return extraction(
        title="Training",
        date_text="Tue",
        start_time_text="7",
        end_time_text="9pm",
        location="UTown",
        relation="separate",
        other_times=[other(date_text="Thu", start_time_text="6", end_time_text="8pm")],
    )


def dinners():
    return extraction(
        title="Dinner",
        date_text="Fri",
        start_time_text="7pm",
        end_time_text=None,
        location=None,
        relation="alternatives",
        other_times=[other(date_text="Sat", start_time_text="6pm")],
    )


# ---- splitting ----


def test_a_null_field_is_the_time_before_it():
    first, second = split_times(trainings())
    assert (second.title, second.location, second.date_text) == ("Training", "UTown", "Thu")
    assert (second.start_time_text, second.end_time_text) == ("6", "8pm")
    assert first.other_times is None and first.relation is None


def test_a_new_day_without_hours_keeps_the_hours():
    both = extraction(
        title="Hackathon",
        date_text="Sat",
        start_time_text="9am",
        end_time_text="6pm",
        relation="separate",
        other_times=[other(date_text="Sun")],
    )
    _, sunday = split_times(both)
    assert (sunday.date_text, sunday.start_time_text, sunday.end_time_text) == (
        "Sun",
        "9am",
        "6pm",
    )


def test_a_new_start_without_an_end_does_not_borrow_the_old_end():
    # "Dinner Fri 7-9pm or Sat 6pm": Saturday's end is estimated, not 9pm.
    first = dinners().model_copy(update={"end_time_text": "9pm"})
    _, saturday = split_times(first)
    assert (saturday.start_time_text, saturday.end_time_text) == ("6pm", None)


def test_a_new_time_without_a_date_keeps_the_date(reference):
    lunch = extraction(
        title="Workshop",
        date_text="Thu 15 Oct",
        start_time_text="10am",
        end_time_text="12pm",
        relation="separate",
        other_times=[other(title="Lunch", start_time_text="12", end_time_text="1pm")],
    )
    times = read_times(lunch, "Workshop Thu 15 Oct 10am-12pm, lunch 12-1pm", reference)
    assert [d.title for d in times.drafts] == ["Workshop", "Lunch"]
    assert times.drafts[1].start_at.date() == times.drafts[0].start_at.date()
    assert f"{times.drafts[1].start_at:%H:%M}" == "12:00"


def test_first_day_borrows_the_shared_month(reference):
    dinner = extraction(
        title="Hall dinner",
        date_text="24 Oct",
        start_time_text="7pm",
        end_time_text="10pm",
        end_date_text="25 Oct",
        relation="alternatives",
        other_times=[other(date_text="25 Oct", start_time_text="7pm", end_time_text="10pm")],
    )
    times = read_times(dinner, "Hall dinner 24 or 25 Oct, 7pm–10pm", reference)
    assert [f"{d.start_at:%d %H:%M}" for d in times.drafts] == ["24 19:00", "25 19:00"]
    assert not times.unread


def test_a_range_start_never_takes_another_lines_am_pm(reference):
    weekly = extraction(
        title="Training",
        date_text="Wed",
        start_time_text="7",
        end_time_text="9pm",
        relation="separate",
        other_times=[other(date_text="Sat", start_time_text="9", end_time_text="11am")],
    )
    text = "This week's trainings:\nMon - no training\nWed 7-9pm @ MPSH 5\nSat 9-11am @ field"
    times = read_times(weekly, text, reference)
    assert [f"{d.start_at:%a %H:%M}-{d.end_at:%H:%M}" for d in times.drafts] == [
        "Wed 19:00-21:00",
        "Sat 09:00-11:00",
    ]


def test_choose_your_slot_is_a_choice():
    slots = extraction(
        title="Photoshoot",
        date_text="20 Oct",
        start_time_text="2",
        end_time_text="3pm",
        relation="one",
        other_times=[other(date_text="21 Oct", start_time_text="2", end_time_text="3pm")],
    )
    text = "Choose ur slot for the photoshoot: Tue 20 Oct 2-3pm / Wed 21 Oct 2-3pm"
    assert settle_times(slots, text).relation == "alternatives"
    assert settle_times(slots, "Photoshoot Tue 20 Oct 2-3pm, Wed 21 Oct 2-3pm").relation == (
        "separate"
    )


def test_at_most_five_times():
    many = trainings().model_copy(update={"other_times": [other(date_text="Thu")] * 9})
    assert len(split_times(many)) == 5


def test_a_time_not_in_the_message_is_never_offered(reference):
    made_up = trainings().model_copy(
        update={"other_times": [other(date_text="2026-10-08", start_time_text="6")]}
    )
    times = read_times(made_up, TRAINING, reference)
    assert len(times.drafts) == 1 and times.unread == ["Training"]


def test_the_first_time_repeated_is_one_time(reference):
    repeated = trainings().model_copy(
        update={"other_times": [other(date_text="Tue", start_time_text="7", end_time_text="9pm")]}
    )
    assert len(read_times(repeated, TRAINING, reference).drafts) == 1


def test_only_fresh_new_events_with_other_times_are_split():
    assert is_multi(trainings(), TRAINING)
    assert not is_multi(trainings().model_copy(update={"relation": "one"}), TRAINING)
    assert not is_multi(trainings().model_copy(update={"other_times": []}), TRAINING)
    assert not is_multi(trainings().model_copy(update={"kind": "update"}), TRAINING)
    assert not is_multi(trainings().model_copy(update={"ambiguity": "am or pm"}), TRAINING)
    # Weekly wording goes to the recurring flows, never to the picker.
    assert not is_multi(trainings(), "Training Tuesdays and Thursdays 7-9pm")
    assert not is_multi(trainings(), "Training every Tue 7-9pm and Thu 6-8pm")


def test_each_extra_time_gets_its_own_message_id():
    ids = {item_message_id(41, n) for n in range(5)}
    assert len(ids) == 5 and item_message_id(41, 0) == 41
    assert not ids & {item_message_id(42, n) for n in range(5)}


def test_old_drafts_without_the_new_fields_still_load():
    from app.schemas.event import Extraction

    saved = extraction().model_dump(mode="json")
    del saved["relation"], saved["other_times"]
    assert Extraction.model_validate(saved).other_times is None


# ---- "and": separate events ----


async def test_separate_events_get_one_picker(service_factory, reference):
    service, repo = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    assert picker.text.startswith("I found 2 events in that message:")
    assert "1. <b>Training</b>" in picker.text and "2. <b>Training</b>" in picker.text
    assert "6 Oct 2026 (Tuesday) · 7PM–9PM SGT" in picker.text
    assert "8 Oct 2026 (Thursday) · 6PM–8PM SGT" in picker.text
    assert picker.text.count("✅ No clashes found") == 2
    labels = [b.text for row in picker.keyboard.inline_keyboard for b in row]
    assert labels == ["Add both", "Add 1 · Tue 6 Oct", "Add 2 · Thu 8 Oct", "Neither"]
    assert len(repo.events) == 2
    assert {e.status for e in repo.events.values()} == {Status.PREVIEW}


async def test_the_picker_says_where_plans_go_without_a_calendar(service_factory, reference):
    service, _ = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    assert "➕ What you add goes to /plans. Want it in Google or Apple Calendar too?" in (
        picker.text
    )
    replies = await service.callback(1, tap(picker, "Add both"))
    assert "☑️ <b>All added to /plans.</b>\n📅 Only in AmIFree for now." in replies[0].text


async def test_the_picker_names_the_calendar_plans_sync_to(service_factory, reference):
    service, repo = service_factory(trainings())
    user = await repo.ensure_user(1)
    await repo.set_user_flags(user.id, google_connected=True)
    [picker] = await service.message(1, 7, TRAINING, reference)
    assert "➕ What you add goes to /plans and syncs to your Google Calendar." in picker.text
    assert "Want it in Google" not in picker.text
    replies = await service.callback(1, tap(picker, "Add both"))
    assert "📅 Syncing to your Google Calendar too (/calendar to change)." in replies[0].text
    assert replies[0].keyboard is None  # plans already go to a calendar: nothing to set up


async def test_pickers_share_the_three_calendar_asks_then_stop(service_factory, reference):
    from app.bot import onboarding

    service, _ = service_factory(*(trainings() for _ in range(4)))
    replies = []
    for n in range(4):
        [picker] = await service.message(1, 10 + n, TRAINING, reference)
        replies.append(picker)
        [done] = await service.callback(1, tap(picker, "Add both"))
        replies.append(done)
    first, second, third, fourth = replies[1], replies[3], replies[5], replies[7]
    # Plans 2, 4 and 6 reach the asks at 1, 3 and 6 plans: buttons each time.
    for done in (first, second, third):
        assert tap(done, "🍎 Apple Calendar") == "setup:apple"
    assert onboarding.LAST_CALENDAR_ASK not in second.text
    assert onboarding.LAST_CALENDAR_ASK in third.text  # says it's the last time
    # After three asks: no buttons, no "connect a calendar", just where the plans went.
    assert fourth.keyboard is None
    assert fourth.text.endswith("☑️ <b>All added to /plans.</b>")
    assert "➕ What you add goes to /plans.\n" in replies[6].text + "\n"
    assert "Want it in Google" not in replies[6].text


async def test_a_card_ask_counts_for_the_picker_too(service_factory, reference):
    lunch = extraction(title="Lunch", date_text="Monday", start_time_text="12", end_time_text="1pm")
    service, _ = service_factory(lunch, trainings())
    card = (await service.message(1, 6, "Lunch Monday 12-1pm", reference))[0]
    asked = await service.callback(1, tap(card, "Add to plans"))
    assert len(asked) == 2  # the card's own "Set up (1 min)" prompt: ask 1
    [picker] = await service.message(1, 7, TRAINING, reference)
    [first] = await service.callback(1, tap(picker, "Add 1 · Tue 6 Oct"))
    [done] = await service.callback(1, tap(first, "Done"))
    # 2 plans, and the next ask waits for 3: no buttons, just where to connect later.
    assert done.keyboard is None
    assert "📅 Only in AmIFree for now. Connect a calendar any time in /calendar." in done.text


async def test_done_after_adding_some_offers_the_calendar_buttons(service_factory, reference):
    service, repo = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    [first] = await service.callback(1, tap(picker, "Add 1 · Tue 6 Oct"))
    replies = await service.callback(1, tap(first, "Done"))
    assert len(replies) == 1
    assert "☑️ <b>Added to /plans.</b>\n📅 Only in AmIFree for now." in replies[0].text
    assert tap(replies[0], "🍎 Apple Calendar") == "setup:apple"
    assert tap(replies[0], "📅 Google Calendar") == "setup:google"
    assert (await repo.ensure_user(1)).pending is None


async def test_an_untitled_extraction_is_named_from_the_message(service_factory, reference):
    # Seen live: the model left title null, so both lines said "Event".
    service, _ = service_factory(trainings().model_copy(update={"title": None}))
    [picker] = await service.message(1, 7, TRAINING, reference)
    assert "1. <b>Training</b>" in picker.text and "2. <b>Training</b>" in picker.text
    assert "<b>Event</b>" not in picker.text


async def test_add_one_then_the_other(service_factory, reference):
    service, repo = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    [first] = await service.callback(1, tap(picker, "Add 1 · Tue 6 Oct"))
    assert first.edit and "☑️ Added to /plans." in first.text
    labels = [b.text for row in first.keyboard.inline_keyboard for b in row]
    assert labels == ["Add 2 · Thu 8 Oct", "Done"]
    statuses = sorted(e.status for e in repo.events.values())
    assert statuses == sorted([Status.GOING, Status.PREVIEW])
    replies = await service.callback(1, tap(first, "Add 2 · Thu 8 Oct"))
    assert "All added to /plans." in replies[0].text
    # No calendar connected: setup is one tap away, in the picker, not in a second message.
    assert len(replies) == 1
    assert [b.text for row in replies[0].keyboard.inline_keyboard for b in row] == [
        "🍎 Apple Calendar",
        "📅 Google Calendar",
    ]
    assert {e.status for e in repo.events.values()} == {Status.GOING}
    assert (await repo.ensure_user(1)).pending is None


async def test_a_clash_shows_on_its_line(service_factory, reference):
    lab = extraction(title="Lab", date_text="Thu", start_time_text="6", end_time_text="7pm")
    service, _ = service_factory(lab, trainings())
    [card] = await service.message(1, 6, "Lab Thu 6-7pm", reference)
    await service.callback(1, tap(card, "Add to plans"))
    [picker] = await service.message(1, 7, TRAINING, reference)
    tuesday, thursday = picker.text.split("\n\n")[1:3]
    assert "✅ No clashes found" in tuesday
    assert "⚠️ Overlaps with Lab" in thursday


async def test_add_both(service_factory, reference):
    service, repo = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    replies = await service.callback(1, tap(picker, "Add both"))
    assert "All added to /plans." in replies[0].text
    assert {e.status for e in repo.events.values()} == {Status.GOING}


async def test_neither_adds_nothing(service_factory, reference):
    service, repo = service_factory(trainings())
    [picker] = await service.message(1, 7, TRAINING, reference)
    [reply] = await service.callback(1, tap(picker, "Neither"))
    assert reply.text == "Okay, nothing added." and reply.edit
    assert {e.status for e in repo.events.values()} == {Status.PREVIEW}


async def test_a_new_message_closes_the_picker(service_factory, reference):
    service, _ = service_factory(trainings(), extraction())
    [picker] = await service.message(1, 7, TRAINING, reference)
    card = await service.message(1, 8, "CCA dinner Friday 7–9pm at Vivo", reference)
    assert "CCA dinner" in card[0].text
    [expired] = await service.callback(1, tap(picker, "Add both"))
    assert "expired" in expired.text


async def test_a_time_that_cannot_be_read_is_named(service_factory, reference):
    lunch = trainings().model_copy(
        update={
            "other_times": [other(title="Lunch", date_text="next blue moon", start_time_text="7")]
        }
    )
    service, repo = service_factory(lunch)
    [picker] = await service.message(1, 7, TRAINING, reference)
    assert picker.text.startswith("I found 2 events")
    assert "I couldn't read the date or time of Lunch. Send it on its own" in picker.text
    assert len(repo.events) == 1


async def test_no_readable_time_asks_for_one_event(service_factory, reference):
    lost = trainings().model_copy(update={"date_text": "someday"})
    lost = lost.model_copy(update={"other_times": [other(date_text="anotherday")]})
    service, repo = service_factory(lost)
    [reply] = await service.message(1, 7, TRAINING, reference)
    assert "one event at a time" in reply.text and not repo.events


async def test_the_model_repeating_one_time_gets_the_usual_card(service_factory, reference):
    repeated = trainings().model_copy(
        update={"other_times": [other(date_text="Tue", start_time_text="7", end_time_text="9pm")]}
    )
    service, _ = service_factory(repeated)
    [card] = await service.message(1, 7, TRAINING, reference)
    assert card.text.startswith("✅") and "I found" not in card.text


async def test_two_ranges_the_model_missed_are_still_refused(service_factory, reference):
    service, repo = service_factory(extraction(title="Training", date_text="Tue"))
    [reply] = await service.message(1, 7, TRAINING, reference)
    assert "more than one event" in reply.text and not repo.events


# ---- "or": choose a time ----


async def test_alternatives_choose_one_time(service_factory, reference):
    service, repo = service_factory(dinners(), default_event_duration_minutes=120)
    [picker] = await service.message(1, 7, DINNER, reference)
    assert picker.text.startswith("🤔 <b>Dinner</b> has 2 possible times.")
    assert "1. 📅 9 Oct 2026 (Friday) · 7PM–9PM SGT" in picker.text
    assert "2. 📅 10 Oct 2026 (Saturday) · 6PM–8PM SGT" in picker.text
    assert "End time estimated." in picker.text
    labels = [b.text for row in picker.keyboard.inline_keyboard for b in row]
    assert labels == ["Fri 9 Oct, 7PM", "Sat 10 Oct, 6PM", "Not decided yet"]
    [card] = await service.callback(1, tap(picker, "Sat 10 Oct, 6PM"))
    assert card.edit and "10 Oct 2026 (Saturday)" in card.text
    assert tap(card, "Add to plans")
    # Nothing is added until they tap Add to plans on the chosen time's card.
    assert {e.status for e in repo.events.values()} == {Status.PREVIEW}


async def test_not_decided_yet(service_factory, reference):
    service, _ = service_factory(dinners(), default_event_duration_minutes=120)
    [picker] = await service.message(1, 7, DINNER, reference)
    [reply] = await service.callback(1, tap(picker, "Not decided yet"))
    assert reply.text.startswith("Okay, nothing added.")
    assert (await service.repo.ensure_user(1)).pending is None


async def test_a_forged_choice_is_refused(service_factory, reference):
    service, _ = service_factory(dinners(), default_event_duration_minutes=120)
    [picker] = await service.message(1, 7, DINNER, reference)
    nonce = tap(picker, "Not decided yet").split(":")[1]
    for choice in ("9", "all", "-1", "x"):
        [reply] = await service.callback(1, f"mt:{nonce}:{choice}")
        assert "isn't valid" in reply.text


# ---- groups: still one card per /event ----


async def test_a_group_event_with_several_times_is_not_one_card(service_factory, reference):
    service, repo = service_factory(trainings(), dinners())
    separate = await service.groups.create(1, -100, 50, TRAINING, reference)
    assert (
        "more than one time" in separate.post.text and "one /event per plan" in separate.post.text
    )
    choices = await service.groups.create(1, -100, 51, DINNER, reference)
    assert "/whosfree" in choices.post.text
    assert not repo.group_events


# ---- found by the live eval ----


def test_another_session_day_is_not_the_first_one_s_end_date(reference):
    # The model put "Sun" in end_date_text, which made Saturday run into Sunday.
    hackathon = extraction(
        title="Hackathon",
        date_text="Sat",
        start_time_text="9",
        end_time_text="6pm",
        end_date_text="Sun",
        relation="separate",
        other_times=[other(date_text="Sun", start_time_text="9", end_time_text="6pm")],
    )
    times = read_times(hackathon, "Hackathon Sat & Sun 9am-6pm at NUS Enterprise", reference)
    assert len(times.drafts) == 2
    assert all(d.start_at.date() == d.end_at.date() for d in times.drafts)


def test_an_answer_in_other_times_fills_the_draft():
    answer = extraction(
        date_text="Wed",
        end_time_text=None,
        relation="one",
        other_times=[other(end_time_text="4pm", date_text="Sat")],
    )
    folded = fold_answer(answer)
    # Empty fields are filled; fields the draft already has are kept.
    assert (folded.end_time_text, folded.date_text) == ("4pm", "Wed")
    assert folded.other_times is None and folded.relation is None


def test_an_end_without_a_start_keeps_the_start_before_it(reference):
    # "Dinner Friday or Saturday 7-9pm": the model gave Saturday only "9pm".
    both = extraction(
        title="Dinner",
        date_text="Friday",
        start_time_text="7",
        end_time_text="9pm",
        relation="alternatives",
        other_times=[other(date_text="Saturday", end_time_text="9pm")],
    )
    times = read_times(both, "Dinner Friday or Saturday 7-9pm at Vivo", reference)
    assert [f"{d.start_at:%a %H:%M}-{d.end_at:%H:%M}" for d in times.drafts] == [
        "Fri 19:00-21:00",
        "Sat 19:00-21:00",
    ]


def test_a_due_date_in_notes_never_borrows_the_meeting_time(reference):
    notes = (
        "Meeting notes\n20 October\nCheck if video due 4 November\n"
        "Aim to finish allocated tasks by 27 October\n28 October 10pm meeting to confirm research"
    )
    listed = extraction(
        title="Meeting",
        date_text="28 October",
        start_time_text="10pm",
        end_time_text=None,
        relation="separate",
        other_times=[other(date_text="4 November"), other(date_text="27 October")],
    )
    assert len(split_times(listed, notes)) == 1
    assert len(read_times(listed, notes, reference, 60).drafts) == 1


def test_a_dropped_end_is_put_back_from_the_message(reference):
    text = "Consult slots: Mon 19 Oct 2-3pm, Tue 20 Oct 3-4pm or Wed 21 Oct 10-11am, pick one"
    slots = extraction(
        title="Consult",
        date_text="19 Oct",
        start_time_text="2",
        end_time_text=None,
        relation="alternatives",
        other_times=[
            other(date_text="20 Oct", start_time_text="3", end_time_text="4pm"),
            other(date_text="21 Oct", start_time_text="10", end_time_text="11am"),
        ],
    )
    times = read_times(slots, text, reference)
    assert [f"{d.start_at:%d %H:%M}-{d.end_at:%H:%M}" for d in times.drafts] == [
        "19 14:00-15:00",
        "20 15:00-16:00",
        "21 10:00-11:00",
    ]


def test_listed_times_labelled_one_are_still_listed():
    from app.services.multi_time import settle_times

    one = trainings().model_copy(update={"relation": "one"})
    assert settle_times(one, TRAINING).relation == "separate"
    assert settle_times(one, "makan tmr 12-1pm or thurs 6-7pm").relation == "alternatives"
    assert settle_times(one, "Consult Mon 2pm, Tue 3pm, pick one").relation == "alternatives"
    # An ambiguity that only describes the listed times is not a question.
    for word in ("alternatives", "one"):
        noted = one.model_copy(update={"ambiguity": word})
        assert settle_times(noted, DINNER).ambiguity is None
    real = one.model_copy(update={"ambiguity": "7 could be am or pm"})
    assert settle_times(real, DINNER).ambiguity == "7 could be am or pm"
    # Nothing listed: nothing changes.
    assert settle_times(extraction(), "CCA dinner Fri 7-9pm") == extraction()


def test_till_4pm_read_as_a_start_is_the_end():
    answer = extraction(
        start_time_text="3pm",
        end_time_text=None,
        other_times=[other(date_text="Wed", start_time_text="4")],
    )
    folded = fold_answer(answer)
    assert (folded.start_time_text, folded.end_time_text) == ("3pm", "4")


async def test_till_4pm_answers_the_end_time_question(service_factory, reference):
    first = extraction(
        title="Coffee chat", date_text="Wed", start_time_text="3pm", end_time_text=None
    )
    answer = first.model_copy(update={"other_times": [other(end_time_text="4pm")]})
    service, _ = service_factory(first, answer)
    [question] = await service.message(1, 7, "Coffee chat Wed 3pm at Starbucks", reference)
    assert "end" in question.text
    [card] = await service.message(1, 8, "till 4pm", reference)
    assert card.text.startswith("✅") and "3PM–4PM" in card.text


GREASE = "Hall Production 'Grease'\nBlocking: Tue 13 Oct 7-10pm\nFull run: Thu 15 Oct 7-10pm"


def parts(first: str, second: str):
    return extraction(
        title=first,
        date_text="Tue 13 Oct",
        start_time_text="7",
        end_time_text="10pm",
        relation="separate",
        other_times=[
            other(title=second, date_text="Thu 15 Oct", start_time_text="7", end_time_text="10pm")
        ],
    )


def test_labelled_parts_all_carry_the_event_name(reference):
    times = read_times(parts("Grease blocking", "Full run"), GREASE, reference)
    assert [d.title for d in times.drafts] == ["Grease blocking", "Grease full run"]
    named = read_times(parts("Grease blocking", "Grease full run"), GREASE, reference)
    assert named.drafts[1].title == "Grease full run"


def test_unlabelled_or_unrelated_titles_are_left_alone(reference):
    # "Dinner with John" ends with a word of the message, but nothing is a "Label:" line.
    source = "Dinner with John Tue 13 Oct 7-10pm, lunch with Mary Thu 15 Oct 7-10pm"
    times = read_times(parts("Dinner with John", "Lunch with Mary"), source, reference)
    assert [d.title for d in times.drafts] == ["Dinner with John", "Lunch with Mary"]
    plain = read_times(parts("Blocking", "Full run"), GREASE, reference)
    assert [d.title for d in plain.drafts] == ["Blocking", "Full run"]
