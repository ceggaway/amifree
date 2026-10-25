"""Group features: names, check-in, ended cards, "still coming?", chase stages."""

from datetime import timedelta

from telegram import Update

from app.schemas.event import Status
from app.services.chase import current_stage, stages
from app.services.jobs import Notifier
from tests.conftest import extraction
from tests.test_group_events import alerts, group_tap, make_bot, post_card
from tests.test_notifications import Outbox


def tap_as(data, telegram_id, update_id, first_name="Member", username=None):
    payload = group_tap(data, telegram_id, update_id=update_id)
    sender = payload["callback_query"]["from"]
    sender["first_name"] = first_name
    if username:
        sender["username"] = username
    return payload


async def press(bot, data, telegram_id, update_id, **who):
    await bot.process_update(Update.de_json(tap_as(data, telegram_id, update_id, **who), bot.bot))


async def card(service, repo):
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
    return bot, transport, outbox, taps, event


async def test_whos_going_lists_only_going_names(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, _, taps, event = await card(service, repo)
    async with bot:
        await press(bot, taps["Going"], 2, 8001, first_name="Wei Ling", username="weiling")
        await press(bot, taps["Going"], 3, 8002, first_name="Jun")
        await press(bot, taps["Can't"], 4, 8003, first_name="Priya")
        await press(bot, taps["Can't"], 5, 8004, first_name="Daniel")
        await press(bot, taps["Who's going?"], 6, 8005)
    text = alerts(transport)[-1]["text"]
    assert text == "Going (2): Jun, Wei Ling"
    assert "Priya" not in text and "Daniel" not in text
    assert len(text) <= 200


async def test_whos_going_stays_inside_the_alert_limit(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, _, taps, event = await card(service, repo)
    async with bot:
        for n in range(30):
            await press(bot, taps["Going"], 100 + n, 8100 + n, first_name=f"Member number {n:02d}")
        await press(bot, taps["Who's going?"], 2, 8200)
    text = alerts(transport)[-1]["text"]
    assert text.startswith("Going (30): ") and "more" in text and len(text) <= 200


async def test_check_in_only_while_the_event_is_on(service_factory, reference):
    service, repo = service_factory(extraction())
    bot, transport, _, taps, event = await card(service, repo)
    start = repo.group_events[event.id].start_at
    async with bot:
        service.clock = lambda: start - timedelta(hours=2)
        await press(bot, taps["I'm here"], 2, 8301)
        assert "opens 30 minutes before" in alerts(transport)[-1]["text"]
        service.clock = lambda: start - timedelta(minutes=10)
        await press(bot, taps["I'm here"], 2, 8302)
        assert alerts(transport)[-1]["text"].startswith("Checked in")
        service.clock = lambda: start + timedelta(hours=5)
        await press(bot, taps["I'm here"], 3, 8303)
        assert alerts(transport)[-1]["text"] == "This event has ended."
    user = await repo.ensure_user(2)
    assert await repo.attendees(event.id) == {user.id}


async def test_finished_cards_are_redrawn_once_with_who_went(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    start = repo.group_events[event.id].start_at
    async with bot:
        for n, member in enumerate((2, 3, 4)):
            await press(bot, taps["Going"], member, 8400 + n)
        service.clock = lambda: start - timedelta(minutes=5)
        await press(bot, taps["I'm here"], 2, 8410)
        await press(bot, taps["I'm here"], 3, 8411)
        service.clock = lambda: start + timedelta(hours=3)
        edits_before = len([c for c in transport.calls if c[0] == "editMessageText"])
        assert (await service.notifier.run()).get("ended") == 1
        assert (await service.notifier.run()).get("ended") is None  # only once
        edits = [p for name, p in transport.calls if name == "editMessageText"]
        assert len(edits) == edits_before + 1
        assert "· ended" in edits[-1]["text"] and "2 went" in edits[-1]["text"]
        assert "reply_markup" not in edits[-1] or not edits[-1]["reply_markup"]
        # Old buttons give an honest answer instead of changing an event that is over.
        await press(bot, taps["Going"], 9, 8420)
        assert alerts(transport)[-1]["text"] == "This event has ended."
    assert len([e for e in repo.events.values() if e.group_event_id == event.id]) == 3


async def test_ended_card_without_check_ins_says_who_planned_to_go(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, _, taps, event = await card(service, repo)
    start = repo.group_events[event.id].start_at
    async with bot:
        await press(bot, taps["Going"], 2, 8500)
        await press(bot, taps["Going"], 3, 8501)
        service.clock = lambda: start + timedelta(days=1)
        await service.notifier.run()
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "2 said Going" in edits[-1]["text"] and "went" not in edits[-1]["text"]


async def test_still_coming_check_and_organiser_summary(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    start = repo.group_events[event.id].start_at
    async with bot:
        for n, (member, label) in enumerate(
            {2: "Going", 3: "Going", 4: "Going", 5: "Going", 6: "Can't", 7: "Can't"}.items()
        ):
            await press(bot, taps[label], member, 8600 + n)
        await press(bot, taps["Status"], 2, 8610)
        assert alerts(transport)[-1]["text"] == "Only the organiser can see this."
        await press(bot, taps["Status"], 1, 8611)
        assert "goes out the day before" in alerts(transport)[-1]["text"]
        # Not yet within a day of the start: nobody is asked.
        service.clock = lambda: start - timedelta(days=3)
        assert (await service.notifier.run())["queued"] == 0
        service.clock = lambda: start - timedelta(hours=26)
        result = await service.notifier.run()
        assert result["queued"] == 4 and result["sent"] == 4
        assert sorted(t for t, _ in outbox.sent) == [2, 3, 4, 5]  # Going only
        assert "Still coming to CCA dinner" in outbox.sent[0][1]
        assert (await service.notifier.run())["queued"] == 0  # asked once

    labels = [b.text for row in outbox.replies[0][1].keyboard.inline_keyboard for b in row]
    assert labels == ["Still coming", "Can't anymore"]
    data = {
        b.text: b.callback_data
        for row in outbox.replies[0][1].keyboard.inline_keyboard
        for b in row
    }
    yes = await service.callback(2, data["Still coming"])
    assert yes[0].text.startswith("✅ See you there") and yes[0].edit
    # "Not sure" on a message sent before it was removed asks for a clear answer again.
    legacy = await service.callback(3, data["Still coming"].replace("c:y:", "c:u:"))
    assert legacy[0].text.startswith("Are you still coming")
    assert [b.text for row in legacy[0].keyboard.inline_keyboard for b in row] == labels
    drop = await service.callback(4, data["Can't anymore"])
    assert "marked Can't make it" in drop[0].text
    async with bot:
        await press(bot, taps["Status"], 1, 8620)
    assert alerts(transport)[-1]["text"] == (
        "Going 3 · Can't 3\n1 confirmed · 1 dropped · 2 no reply"
    )
    assert (await service.callback(9, data["Still coming"]))[0].text.startswith("That check isn't")
    linked = {e.user_id: e.status for e in repo.events.values() if e.group_event_id == event.id}
    four = (await repo.ensure_user(4)).id
    assert linked[four] == Status.DECLINED
    # The shared card follows the drop.
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "Going 3 · Can't 3" in edits[-1]["text"]


async def test_still_coming_skips_people_who_changed_their_mind(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    start = repo.group_events[event.id].start_at
    async with bot:
        await press(bot, taps["Going"], 2, 8700)
        await press(bot, taps["Going"], 3, 8701)
        service.clock = lambda: start - timedelta(hours=26)
        queued = await service.groups.enqueue_confirmations(service.clock())
        assert queued == 2
        await press(bot, taps["Can't"], 3, 8702)  # changed their mind before it was sent
    result = await service.notifier.deliver()
    assert result["sent"] == 1 and [t for t, _ in outbox.sent] == [2]
    # After the event has started a late tap does nothing.
    service.clock = lambda: start + timedelta(hours=3)
    assert (await service.callback(2, f"c:y:{event.id}"))[0].text == "This event has ended."


async def test_chase_has_two_stages_and_each_person_is_nudged_once_per_stage(
    service_factory, reference
):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    stored = repo.group_events[event.id]
    stored.created_at = reference
    async with bot:
        await press(bot, taps["Going"], 2, 8800)
        await press(bot, taps["Am I free?"], 3, 8801)
        await press(bot, taps["Am I free?"], 4, 8802)
    start = stored.start_at
    assert [name for name, _ in stages(stored)] == ["mid", "final"]
    service.clock = lambda: reference + timedelta(hours=1)
    assert current_stage(stored, service.clock()) is None
    assert (await service.notifier.run())["queued"] == 0
    service.clock = lambda: reference + timedelta(hours=25)
    assert (await service.notifier.run())["queued"] == 2
    assert (await service.notifier.run())["queued"] == 0
    service.clock = lambda: start - timedelta(hours=2)
    assert current_stage(stored, service.clock()) == "final"
    assert (await service.notifier.run())["queued"] == 2
    assert (await service.notifier.run())["queued"] == 0
    assert len(outbox.sent) == 4
    # Whoever answered in between is left alone at the final stage.
    service.clock = lambda: reference + timedelta(days=2)
    assert (await service.notifier.run())["queued"] == 0


async def test_a_short_window_never_nudges_twice_at_once(service_factory, reference):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card(service, repo)
    stored = repo.group_events[event.id]
    stored.created_at = stored.start_at - timedelta(hours=5)
    async with bot:
        await press(bot, taps["Am I free?"], 3, 8900)
    service.clock = lambda: stored.start_at - timedelta(hours=2)
    first = await service.notifier.run()
    assert first["queued"] == 1 and (await service.notifier.run())["queued"] == 0
