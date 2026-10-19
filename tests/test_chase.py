from datetime import timedelta

from telegram import Update

from app.services.jobs import Notifier
from tests.conftest import extraction
from tests.test_group_events import alerts, buttons, group_message, group_tap, make_bot, post_card
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
            Update.de_json(group_tap(taps["Maybe"], 4, update_id=9201), bot.bot)
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


async def test_poll_nudges_non_voters(service_factory, reference):
    service, repo = service_factory()
    for telegram_id in (1, 2, 3):  # a timetable, so their empty week counts as known
        await repo.ensure_user(telegram_id)
        repo.users[telegram_id].coverage_start = reference - timedelta(days=60)
    bot, transport = make_bot(service)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    async with bot:
        await bot.process_update(
            Update.de_json(group_message("/whosfree sync tomorrow", 30, 1), bot.bot)
        )
        taps = buttons(transport.sent[-1]["reply_markup"])
        for n, member in enumerate((2, 3)):
            await bot.process_update(
                Update.de_json(
                    group_tap(taps["✋ Count me in"], member, update_id=9300 + n), bot.bot
                )
            )
        suggest = taps["Suggest slots (organiser)"]
        await bot.process_update(Update.de_json(group_tap(suggest, 1, update_id=9310), bot.bot))
        poll = next(iter(repo.polls.values()))
        await bot.process_update(
            Update.de_json(group_tap(f"p:fill:{poll.id}", 2, update_id=9311), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(f"p:nudge:{poll.id}", 2, update_id=9312), bot.bot)
        )
        assert "Only the organiser" in alerts(transport)[-1]["text"]
        await bot.process_update(
            Update.de_json(group_tap(f"p:nudge:{poll.id}", 1, update_id=9313), bot.bot)
        )
    assert alerts(transport)[-1]["text"].startswith("Nudging 1 member privately")
    assert len(outbox.sent) == 1 and outbox.sent[0][0] == 3
    assert (
        "choosing a time for sync" in outbox.sent[0][1]
        and "times you can make" in outbox.sent[0][1]
    )
    assert not repo.events
