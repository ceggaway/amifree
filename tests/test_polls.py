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
                Update.de_json(group_tap(taps["Count me in"], member, update_id=8000 + n), bot.bot)
            )
    return bot, transport, card, taps, next(iter(repo.polls.values()))


def last_card(transport):
    return [p for name, p in transport.calls if name == "editMessageText"][-1]


async def test_slots_need_three_opt_ins_and_the_organiser(service_factory):
    service, repo = service_factory()
    bot, transport, card, taps, poll = await run_poll(service, repo, joiners=(2,))
    assert "Who's free? project meeting (2h)" in card["text"] and "Joined: 1" in card["text"]
    assert poll.message_id == 101
    async with bot:
        # A second tap by the same member does not count twice.
        await bot.process_update(Update.de_json(group_tap(taps["Count me in"], 2), bot.bot))
        assert "Joined: 2" in last_card(transport)["text"]
        suggest = taps["Suggest slots (organiser)"]
        await bot.process_update(Update.de_json(group_tap(suggest, 2, update_id=8100), bot.bot))
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        await bot.process_update(Update.de_json(group_tap(suggest, 1, update_id=8101), bot.bot))
        assert "At least 3 are needed" in alerts(transport)[-1]["text"]
        # Votes are refused until slots exist.
        await bot.process_update(
            Update.de_json(group_tap(f"p:v:{poll.id}:0F", 2, update_id=8102), bot.bot)
        )
        assert "hasn't suggested slots" in alerts(transport)[-1]["text"]
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
    brunch = (await service.message(3, 6, "Brunch Monday 14:00 16:00", reference))[0]
    await service.callback(3, brunch.keyboard.inline_keyboard[2][0].callback_data)
    bot, transport, _, taps, poll = await run_poll(service, repo)
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Suggest slots (organiser)"], 1, update_id=8200), bot.bot)
        )
        opened = last_card(transport)
        # Everyone is free from 14:00, so the morning never appears.
        assert "1. Mon 5 Oct 14:00–16:00 · 3 of 3 free" in opened["text"]
        assert "2. Mon 5 Oct 16:00–18:00 · 3 of 3 free" in opened["text"]
        # The card shows counts only: no names, no titles of anyone's plans.
        assert "Lab" not in opened["text"] and "Brunch" not in opened["text"]
        taps = buttons(opened["reply_markup"])
        assert all(len(data.encode()) <= 64 for data in taps.values())
        await bot.process_update(
            Update.de_json(group_tap(taps["Fill mine"], 3, update_id=8201), bot.bot)
        )
        filled = alerts(transport)[-1]
        assert filled["text"].startswith("Filled from your schedule: 1 Maybe · 2 Free · 3 Free")
        assert filled["show_alert"] and len(filled["text"]) <= 200
        await bot.process_update(
            Update.de_json(group_tap(taps["Fill mine"], 2, update_id=8202), bot.bot)
        )
        # Someone who never opted in can still answer by hand, and change an answer.
        await bot.process_update(
            Update.de_json(group_tap(taps["1 ❌"], 9, update_id=8203), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(taps["1 🤔"], 3, update_id=8204), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(taps["1 ✅"], 3, update_id=8205), bot.bot)
        )
        assert "✅ 2 · 🤔 0 · ❌ 1" in last_card(transport)["text"]
        assert len(repo.poll_people[poll.id]) == 3
        # Only the organiser can settle it; picking posts a normal event card.
        await bot.process_update(
            Update.de_json(group_tap(taps["Pick 2"], 2, update_id=8206), bot.bot)
        )
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        await bot.process_update(
            Update.de_json(group_tap(taps["Pick 2"], 1, update_id=8207), bot.bot)
        )
        assert "This poll is closed" in last_card(transport)["text"]
        event_card = transport.sent[-1]
        assert "project meeting" in event_card["text"] and "16:00" in event_card["text"]
        assert "Going 0" in event_card["text"]
        going = buttons(event_card["reply_markup"])["Going"]
        await bot.process_update(Update.de_json(group_tap(going, 2, update_id=8208), bot.bot))
        # A closed poll no longer accepts answers, and a repeated pick makes no second card.
        await bot.process_update(
            Update.de_json(group_tap(taps["1 ✅"], 2, update_id=8209), bot.bot)
        )
        assert alerts(transport)[-1]["text"] == "This poll is closed."
    assert len(repo.group_events) == 1
    event = next(iter(repo.group_events.values()))
    assert event.start_at == datetime(2026, 10, 5, 16, tzinfo=SG) and event.message_id
    assert any("project meeting" in r.text for r in await service.command(2, "plans"))


async def test_poll_is_bound_to_its_chat_and_needs_a_topic(service_factory):
    service, repo = service_factory()
    bot, transport, _, taps, poll = await run_poll(service, repo, joiners=())
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Count me in"], 2, chat=CHAT - 1), bot.bot)
        )
        assert alerts(transport)[-1]["text"] == "This poll is no longer available."
        await bot.process_update(Update.de_json(group_message("/whosfree", 31, 1), bot.bot))
        assert "What are you scheduling?" in transport.sent[-1]["text"]
        # A retried command does not post a second poll.
        update = group_message("/whosfree project meeting 2h tomorrow", 30, 1)
        await bot.process_update(Update.de_json(update, bot.bot))
    assert len(repo.polls) == 1 and len(repo.poll_people[poll.id]) == 1
    assert "works in group chats" in (await service.command(1, "whosfree"))[0].text
