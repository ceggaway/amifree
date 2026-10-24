import json
from datetime import datetime, timedelta

from telegram import Update

from app.schemas.event import SG
from app.services.polls import parse_request, suggest_slots
from tests.conftest import extraction
from tests.test_group_events import CHAT, alerts, buttons, group_message, group_tap, make_bot

# Reference is Sunday 4 Oct 2026, 12:00 Singapore.


def test_request_parsing(reference):
    title, minutes, start, end = parse_request("project meeting 2h next week", reference)
    assert (title, minutes) == ("project meeting", 120)
    assert start == datetime(2026, 10, 5, tzinfo=SG) and end == datetime(2026, 10, 12, tzinfo=SG)
    title, minutes, start, end = parse_request("dinner", reference)
    assert (title, minutes) == ("dinner", 60)
    assert start == datetime(2026, 10, 5, tzinfo=SG) and end - start == timedelta(days=7)
    title, minutes, start, end = parse_request("90 min sync tomorrow", reference)
    assert (title, minutes, end - start) == ("sync", 90, timedelta(days=1))
    assert parse_request("1.5h", reference)[:2] == ("Meetup", 90)


def test_slots_prefer_most_people_free_then_earliest(reference):
    start, end = datetime(2026, 10, 5, tzinfo=SG), datetime(2026, 10, 7, tzinfo=SG)

    def span(day, a, b):
        return (datetime(2026, 10, day, a, tzinfo=SG), datetime(2026, 10, day, b, tzinfo=SG))

    busy = {
        "a": [span(5, 8, 18)],
        "b": [span(5, 8, 12), span(6, 8, 10)],
        "c": [],
    }
    slots = suggest_slots(start, end, 120, busy, reference)
    assert [(s["start_at"][5:16], s["free"]) for s in slots] == [
        ("10-05T18:00", 3),
        ("10-05T20:00", 3),
        ("10-06T10:00", 3),
    ]
    # Slots never overlap, stay inside 08:00–22:00, and never start in the past.
    late = suggest_slots(start, end, 60, {"a": []}, datetime(2026, 10, 6, 20, 15, tzinfo=SG))
    assert [s["start_at"][5:16] for s in late] == ["10-06T20:30"]
    assert suggest_slots(start, end, 60, {"a": []}, end) == []


async def with_schedule(repo, *telegram_ids):
    """Give members a NUSMods timetable, so an empty week reads as free rather than unknown."""
    for telegram_id in telegram_ids:
        await repo.ensure_user(telegram_id)
        repo.users[telegram_id].coverage_start = datetime(2026, 8, 1, tzinfo=SG)


async def run_poll(service, repo, joiners=(2, 3)):
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(
            Update.de_json(group_message("/whosfree project meeting 2h tomorrow", 30, 1), bot.bot)
        )
        card = transport.sent[-1]
        taps = buttons(card["reply_markup"])
        for n, member in enumerate(joiners):
            await bot.process_update(
                Update.de_json(
                    group_tap(taps["✋ Count me in"], member, update_id=8000 + n), bot.bot
                )
            )
    return bot, transport, card, taps, next(iter(repo.polls.values()))


def private_to(transport, telegram_id):
    return [m for m in transport.sent if int(m["chat_id"]) == telegram_id]


def last_card(transport):
    return [p for name, p in transport.calls if name == "editMessageText"][-1]


async def test_slots_need_three_opt_ins_and_the_organiser(service_factory):
    service, repo = service_factory()
    bot, transport, card, taps, poll = await run_poll(service, repo, joiners=(2,))
    assert "Who's free? project meeting (2h)" in card["text"] and "Joined: 1" in card["text"]
    assert poll.message_id == 101
    async with bot:
        # A second tap by the same member does not count twice.
        await bot.process_update(Update.de_json(group_tap(taps["✋ Count me in"], 2), bot.bot))
        assert "Joined: 2" in last_card(transport)["text"]
        suggest = taps["Suggest slots (organiser)"]
        await bot.process_update(Update.de_json(group_tap(suggest, 2, update_id=8100), bot.bot))
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        await bot.process_update(Update.de_json(group_tap(suggest, 1, update_id=8101), bot.bot))
        assert "At least 3 are needed" in alerts(transport)[-1]["text"]
        # Old vote buttons do nothing until results are out.
        await bot.process_update(
            Update.de_json(group_tap(f"p:v:{poll.id}:0F", 2, update_id=8102), bot.bot)
        )
        assert "Results aren't out yet" in alerts(transport)[-1]["text"]
    assert repo.polls[poll.id].status == "COLLECTING" and repo.polls[poll.id].slots == []


async def test_self_filling_poll_end_to_end(service_factory, reference):
    morning = extraction(
        title="Lab", date_text="Monday", start_time_text="08:00", end_time_text="14:00"
    )
    saved = extraction(
        title="Brunch", date_text="Monday", start_time_text="14:00", end_time_text="16:00"
    )
    service, repo = service_factory(morning, saved)
    # Member 2 is committed all morning; member 3 has an undecided plan in the afternoon.
    lab = (await service.message(2, 5, "Lab Monday 08:00 14:00", reference))[0]
    await service.callback(2, lab.keyboard.inline_keyboard[0][0].callback_data)
    await service.message(3, 6, "Brunch Monday 14:00 16:00", reference)
    # An undecided (Saved) plan doesn't block a poll slot; the card no longer offers Save.
    brunch = next(e for e in repo.events.values() if e.title == "Brunch")
    await repo.save(brunch.user_id, brunch)
    await with_schedule(repo, 1, 2, 3)
    bot, transport, _, taps, poll = await run_poll(service, repo)
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Suggest slots (organiser)"], 1, update_id=8200), bot.bot)
        )
        opened = last_card(transport)
        assert "Results are in from 3 schedules" in opened["text"]
        assert "organiser is choosing the time" in opened["text"]
        # Nothing for members to tap on the group card any more: no voting.
        assert "reply_markup" not in opened or not buttons(opened["reply_markup"])
        # The organiser gets the results privately, with one button per time.
        report = private_to(transport, 1)[-1]
        assert "📊 Results for project meeting (2h)" in report["text"]
        assert "3 people filled in their availabilities" in report["text"]
        # Everyone is free from 14:00, so the morning never appears.
        assert "1. Mon 5 Oct, 14:00–16:00 · 3 of 3 free" in report["text"]
        assert "2. Mon 5 Oct, 16:00–18:00 · 3 of 3 free" in report["text"]
        # Counts only: no names, no titles of anyone's plans.
        assert "Lab" not in report["text"] and "Brunch" not in report["text"]
        choose = buttons(report["reply_markup"])
        assert list(choose) == ["Choose 1", "Choose 2", "Choose 3"]
        assert all(len(data.encode()) <= 64 for data in choose.values())
        # Old vote buttons now just say the organiser is choosing, and record nothing.
        await bot.process_update(
            Update.de_json(group_tap(f"p:t:{poll.id}:0", 2, update_id=8201), bot.bot)
        )
        assert "organiser is choosing" in alerts(transport)[-1]["text"]
        assert not repo.poll_votes
    # Only the organiser can choose from the report.
    assert "isn't available" in (await service.callback(2, choose["Choose 2"]))[0].text
    sent_before = len(transport.sent)
    async with bot:
        done = (await service.callback(1, choose["Choose 2"]))[0]
    assert done.edit and "is set for Mon 5 Oct, 16:00–18:00" in done.text
    # Without the Mini App, the usual event card is posted in the group.
    event_card = transport.sent[sent_before]
    assert int(event_card["chat_id"]) == CHAT
    assert "project meeting" in event_card["text"] and "Going 0" in event_card["text"]
    assert repo.polls[poll.id].status == "CLOSED"
    # A second tap changes nothing.
    async with bot:
        again = (await service.callback(1, choose["Choose 1"]))[0]
    assert "already chosen" in again.text
    assert len(repo.group_events) == 1
    event = next(iter(repo.group_events.values()))
    assert event.start_at == datetime(2026, 10, 5, 16, tzinfo=SG) and event.message_id


async def test_choosing_announces_the_time_with_calendar_buttons(service_factory):
    service, repo = service_factory(
        bot_username="AmIFreeBot",
        mini_app_short_name="free",
        app_base_url="https://amifree.example",
    )
    await with_schedule(repo, 1, 2, 3)
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(
            Update.de_json(group_message("/whosfree project meeting 2h tomorrow", 30, 1), bot.bot)
        )
    poll = next(iter(repo.polls.values()))
    for member in (1, 2, 3):
        await repo.poll_join(poll.id, (await repo.ensure_user(member)).id)
    async with bot:
        organiser = await repo.ensure_user(1)
        await service.hub.poll_suggest(organiser, repo.polls[poll.id])
        report = private_to(transport, 1)[-1]
        markup = report["reply_markup"]
        markup = json.loads(markup) if isinstance(markup, str) else markup
        row = markup["inline_keyboard"]
        assert [b["text"] for b in row[0]] == ["Choose 1", "Choose 2", "Choose 3"]
        assert row[1][0]["text"] == "📱 See all times"
        sent_before = len(transport.sent)
        await service.callback(1, row[0][0]["callback_data"])
    announced = transport.sent[sent_before]
    assert int(announced["chat_id"]) == CHAT
    assert "📣 project meeting is confirmed!" in announced["text"]
    assert "🗓 Mon 5 Oct 2026 · 08:00–10:00" in announced["text"]
    markup = announced["reply_markup"]
    markup = json.loads(markup) if isinstance(markup, str) else markup
    urls = {b["text"]: b["url"] for row in markup["inline_keyboard"] for b in row}
    event = next(iter(repo.group_events.values()))
    assert urls["📅 Google Calendar"].startswith("https://calendar.google.com/")
    assert urls["🍎 Apple Calendar"] == f"https://amifree.example/ics/event/{event.id}.ics"
    assert urls["✋ Going or Can't"].endswith(f"startapp=ev_{event.id}")
    ics = await service.group_event_ics(event.id)
    assert b"SUMMARY:project meeting" in ics
    assert await service.group_event_ics("not-an-id") is None


async def test_poll_is_bound_to_its_chat_and_needs_a_topic(service_factory):
    service, repo = service_factory()
    bot, transport, _, taps, poll = await run_poll(service, repo, joiners=())
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["✋ Count me in"], 2, chat=CHAT - 1), bot.bot)
        )
        assert alerts(transport)[-1]["text"] == "This poll is no longer available."
        await bot.process_update(Update.de_json(group_message("/whosfree", 31, 1), bot.bot))
        assert "What are you scheduling?" in transport.sent[-1]["text"]
        # A retried command does not post a second poll.
        update = group_message("/whosfree project meeting 2h tomorrow", 30, 1)
        await bot.process_update(Update.de_json(update, bot.bot))
    assert len(repo.polls) == 1 and len(repo.poll_people[poll.id]) == 1
    assert "works in group chats" in (await service.command(1, "whosfree"))[0].text


async def test_members_with_no_schedule_are_never_counted_as_free(service_factory):
    service, repo = service_factory()
    await with_schedule(repo, 1, 2, 3)
    bot, transport, _, taps, poll = await run_poll(service, repo, joiners=(2, 3, 4))
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Suggest slots (organiser)"], 1, update_id=8300), bot.bot)
        )
    text = private_to(transport, 1)[-1]["text"]
    # Member 4 joined but gave AmIFree nothing to check: said so, and not counted.
    assert "of 3 free" in text and "of 4" not in text
    assert "1 member had no schedule to check" in text
    assert repo.polls[poll.id].slots[0]["of"] == 3 and repo.polls[poll.id].slots[0]["unknown"] == 1


async def test_slots_need_three_members_with_a_schedule(service_factory):
    service, repo = service_factory()
    await with_schedule(repo, 1, 2)  # joiner 3 has no timetable, calendar or blocks
    bot, transport, _, taps, poll = await run_poll(service, repo, joiners=(2, 3))
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Suggest slots (organiser)"], 1, update_id=8400), bot.bot)
        )
        assert "only 2 have a schedule" in alerts(transport)[-1]["text"]
    assert repo.polls[poll.id].status == "COLLECTING" and repo.polls[poll.id].slots == []


async def test_old_fill_buttons_no_longer_vote(service_factory):
    service, repo = service_factory()
    await with_schedule(repo, 1, 2, 3)
    bot, transport, _, taps, poll = await run_poll(service, repo, joiners=(2, 3, 4))
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Suggest slots (organiser)"], 1, update_id=8500), bot.bot)
        )
        fill = f"p:fill:{poll.id}"  # on cards posted before the organiser chose privately
        await bot.process_update(Update.de_json(group_tap(fill, 4, update_id=8501), bot.bot))
        assert "organiser is choosing" in alerts(transport)[-1]["text"]
    assert not repo.poll_votes
