from datetime import timedelta

from telegram import Update

from app.services.chase import non_responders
from app.services.jobs import Notifier
from tests.conftest import extraction
from tests.test_group_events import (
    alerts,
    buttons,
    group_tap,
    make_bot,
    post_card,
    seed_poll_card,
)
from tests.test_notifications import Outbox


async def card_with_lurkers(service, repo, lurkers=(3, 4)):
    """Member 2 answers; the lurkers only check availability, so they are known but silent."""
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        await bot.process_update(Update.de_json(group_tap(taps["Going"], 2), bot.bot))
        for n, member in enumerate(lurkers):
            await bot.process_update(
                Update.de_json(group_tap(taps["Am I free?"], member, update_id=9000 + n), bot.bot)
            )
    return bot, transport, outbox, taps, event


async def test_organiser_nudge_reaches_only_silent_members_once(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card_with_lurkers(service, repo)
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Nudge"], 2, update_id=9100), bot.bot)
        )
        assert "Only the organiser" in alerts(transport)[-1]["text"] and outbox.sent == []
        await bot.process_update(
            Update.de_json(group_tap(taps["Nudge"], 1, update_id=9101), bot.bot)
        )
        assert alerts(transport)[-1]["text"].startswith("Nudging 2 members privately")
        # Not the organiser, and not the member who already answered.
        assert sorted(telegram_id for telegram_id, _ in outbox.sent) == [3, 4]
        text = outbox.sent[0][1]
        assert "waiting for your answer" in text and "CCA dinner" in text
        assert "✅ No clashes found" in text and "/reminders off" in text
        await bot.process_update(
            Update.de_json(group_tap(taps["Nudge"], 1, update_id=9102), bot.bot)
        )
        assert "Nobody left to nudge" in alerts(transport)[-1]["text"] and len(outbox.sent) == 2
        # The nudge can be answered privately, and the shared headcount follows.
        answer = await service.callback(3, f"g:go:{event.id}")
        assert answer[0].text.startswith("You're Going.")
        edits = [p for name, p in transport.calls if name == "editMessageText"]
        assert "Going 2" in edits[-1]["text"]
    # Someone never seen in that group cannot use the same button data.
    assert "isn't available to you" in (await service.callback(9, f"g:go:{event.id}"))[0].text
    assert "isn't available to you" in (await service.callback(3, f"g:nudge:{event.id}"))[0].text
    assert len([e for e in repo.events.values() if e.group_event_id]) == 2


async def test_nudges_respect_opt_out_and_late_answers(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card_with_lurkers(service, repo, lurkers=(3, 4, 5))
    await service.command(3, "reminders", "off")
    # Member 4 answers after the nudge is queued but before it is sent.
    # The opted-out member is never attempted, so only two sends fail on the first pass.
    outbox.errors = [TimeoutError(), TimeoutError()]
    async with bot:
        await bot.process_update(
            Update.de_json(group_tap(taps["Nudge"], 1, update_id=9200), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(taps["Can't"], 4, update_id=9201), bot.bot)
        )
    result = await service.notifier.deliver()
    assert result["sent"] == 1 and [telegram_id for telegram_id, _ in outbox.sent] == [5]


async def test_automatic_chase_after_a_day(service_factory, reference):
    service, repo = service_factory(extraction())
    bot, transport, outbox, taps, event = await card_with_lurkers(service, repo)
    stored = repo.group_events[event.id]
    # A card posted a moment ago is left alone.
    stored.created_at = reference
    service.clock = lambda: reference + timedelta(hours=1)
    assert (await service.notifier.run())["queued"] == 0
    service.clock = lambda: reference + timedelta(hours=25)
    assert await service.notifier.run() == {"queued": 2, "sent": 2, "failed": 0, "skipped": 0}
    assert await service.notifier.run() == {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    # Once the event has started there is nothing left to chase.
    stored.created_at = reference + timedelta(days=5)
    repo.notifications.clear()
    service.clock = lambda: reference + timedelta(days=7)
    assert (await service.notifier.run())["queued"] == 0


async def test_poll_nudges_members_who_have_not_filled_in(service_factory, reference):
    service, repo = service_factory()
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        await seed_poll_card(bot, "sync tomorrow", 30, 1)
        taps = buttons(transport.sent[-1]["reply_markup"])
        await bot.process_update(
            Update.de_json(group_tap(taps["✋ Count me in"], 2, update_id=9300), bot.bot)
        )
        poll = next(iter(repo.polls.values()))
        # Member 3 is seen in the group but hasn't filled in their times.
        await bot.process_update(
            Update.de_json(group_tap(f"p:nudge:{poll.id}", 3, update_id=9312), bot.bot)
        )
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        await bot.process_update(
            Update.de_json(group_tap(f"p:nudge:{poll.id}", 1, update_id=9313), bot.bot)
        )
    assert alerts(transport)[-1]["text"].startswith("Nudging 1 member privately")
    assert len(outbox.sent) == 1 and outbox.sent[0][0] == 3
    assert (
        "choosing a time for sync" in outbox.sent[0][1]
        and "Fill in your availabilities" in outbox.sent[0][1]
    )
    # Once results are out, nobody else has anything to do.
    repo.polls[poll.id].status = "OPEN"
    assert await non_responders(repo, repo.polls[poll.id]) == []


async def test_several_open_polls_arrive_as_one_dm(service_factory, reference):
    service, repo = service_factory()
    service.settings.bot_username = "amifree_bot"
    service.settings.mini_app_short_name = "app"
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        await seed_poll_card(bot, "sync tomorrow", 30, 1)
        await seed_poll_card(bot, "lunch friday", 31, 1)
        await seed_poll_card(bot, "movie night", 32, 1)
        # Member 3 is seen in the group (taps a card) but fills nothing in.
        first = next(iter(repo.polls.values()))
        await bot.process_update(
            Update.de_json(group_tap(f"p:nudge:{first.id}", 3, update_id=9400), bot.bot)
        )
    member = next(u for u in repo.users.values() if u.telegram_user_id == 3)
    polls = list(repo.polls.values())
    for poll in polls:
        await repo.enqueue_notification(
            member.id, "NUDGE", f"poll:{poll.id}:mid", {"type": "poll", "id": poll.id}
        )
    # One poll was filled in while the nudge was queued: it is left out of the DM.
    await repo.poll_join(polls[2].id, member.id)
    counts = await service.notifier.deliver()
    assert counts == {"sent": 2, "failed": 0, "skipped": 1}
    assert len(outbox.sent) == 1 and outbox.sent[0][0] == 3
    _, reply = outbox.replies[0]
    assert "2 polls are waiting" in reply.text and "movie" not in reply.text
    urls = [row[0].url for row in reply.keyboard.inline_keyboard]
    assert urls == [f"https://t.me/amifree_bot/app?startapp=mine_{p.id}" for p in polls[:2]]
