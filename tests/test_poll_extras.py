"""/whosfree: reply-by deadline, automatic suggestion, the heatmap data."""

import json
from datetime import datetime, timedelta

from telegram import Update

from app.schemas.event import SG
from app.services.polls import heatmap_cells, parse_deadline
from tests.test_group_events import alerts, buttons, group_tap, make_bot, seed_poll_card
from tests.test_polls import last_card, run_poll, with_schedule


def test_reply_by_is_split_off_the_request(reference):
    rest, deadline = parse_deadline("project meeting 2h next week reply by Sun", reference)
    assert rest == "project meeting 2h next week"
    assert deadline == datetime(2026, 10, 4, 23, 59, tzinfo=SG)
    rest, deadline = parse_deadline("sync by friday", reference)
    assert rest == "sync" and deadline == datetime(2026, 10, 9, 23, 59, tzinfo=SG)
    rest, deadline = parse_deadline("dinner by 12 oct", reference)
    assert rest == "dinner" and deadline.date().isoformat() == "2026-10-12"
    # No deadline given, or one that is not understood: two days from now.
    for text in ("project meeting", "lunch by the lake"):
        rest, deadline = parse_deadline(text, reference)
        assert rest == text and deadline == reference + timedelta(hours=48)


def test_heatmap_counts_people_free_per_half_hour(reference):
    start, end = datetime(2026, 10, 5, tzinfo=SG), datetime(2026, 10, 6, tzinfo=SG)
    busy = {
        "a": [(datetime(2026, 10, 5, 9, tzinfo=SG), datetime(2026, 10, 5, 10, tzinfo=SG))],
        "b": [(datetime(2026, 10, 5, 9, 30, tzinfo=SG), datetime(2026, 10, 5, 11, tzinfo=SG))],
        "c": [],
    }
    (day,) = heatmap_cells(start, end, busy, reference)
    free = {c["start_at"][11:16]: c["free"] for c in day["cells"]}
    assert (free["08:30"], free["09:00"], free["09:30"], free["10:00"], free["11:00"]) == (
        3,
        2,
        1,
        2,
        3,
    )
    assert len(day["cells"]) == 28  # 08:00 to 22:00
    # Cells already in the past are left out; people are never named.
    later = heatmap_cells(start, end, busy, datetime(2026, 10, 5, 20, tzinfo=SG))
    assert later[0]["cells"][0]["start_at"][11:16] == "20:00"
    assert all(set(c) == {"start_at", "free"} for d in later for c in d["cells"])


async def test_poll_shows_deadline_and_suggests_itself_when_it_passes(service_factory, reference):
    service, repo = service_factory()
    await with_schedule(repo, 1, 2, 3)
    bot, transport = make_bot(service)
    async with bot:
        await seed_poll_card(bot, "sync 1h next week reply by Mon", 30, 1)
        card = transport.sent[-1]
        assert "Reply by Mon 5 Oct, 11:59pm" in card["text"]
        taps = buttons(card["reply_markup"])
        for n, member in enumerate((2, 3)):
            await bot.process_update(
                Update.de_json(
                    group_tap(taps["✋ Count me in"], member, update_id=8300 + n), bot.bot
                )
            )
        poll = next(iter(repo.polls.values()))
        assert poll.deadline_at == datetime(2026, 10, 5, 23, 59, tzinfo=SG)
        # Before the deadline nothing happens by itself.
        service.clock = lambda: reference + timedelta(hours=3)
        assert "suggested" not in await service.notifier.run()
        assert repo.polls[poll.id].status == "COLLECTING"
        service.clock = lambda: poll.deadline_at + timedelta(minutes=1)
        result = await service.notifier.run()
        assert result["suggested"] == 1
        assert repo.polls[poll.id].status == "OPEN" and repo.polls[poll.id].slots
        # The card in the group is redrawn with the slots, and not suggested twice.
        assert "Results are in from 3 schedules" in last_card(transport)["text"]
        # The results are posted in the group for the organiser to choose from.
        report = [m for m in transport.sent if m.get("text", "").startswith("Results for")][-1]
        assert int(report["chat_id"]) != 1
        assert "Results for sync (1 hour)" in report["text"]
        assert "suggested" not in await service.notifier.run()


async def test_deadline_does_nothing_without_enough_schedules(service_factory):
    service, repo = service_factory()
    bot, transport, card, taps, poll = await run_poll(service, repo, joiners=(2, 3))
    # Nobody has a schedule AmIFree can read, so a suggestion would be guesswork.
    service.clock = lambda: poll.deadline_at + timedelta(hours=1)
    result = await service.notifier.run()
    assert "suggested" not in result
    assert repo.polls[poll.id].status == "COLLECTING"
    # The card says why no slots appeared, instead of still promising them at the reply time.
    text = last_card(transport)["text"]
    assert "The reply time has passed" in text and "taps Suggest" in text
    edits = len([c for c in transport.calls if c[0] == "editMessageText"])
    await service.notifier.run()  # nothing changed since: not redrawn again
    assert len([c for c in transport.calls if c[0] == "editMessageText"]) == edits


async def test_heatmap_is_for_participants_with_enough_schedules(service_factory):
    service, repo = service_factory()
    await with_schedule(repo, 1, 2, 3, 4)
    bot, transport, card, taps, poll = await run_poll(service, repo, joiners=(2, 3))
    organiser = (await repo.ensure_user(1)).id
    outsider = (await repo.ensure_user(4)).id
    # A stranger to the poll learns nothing, not even that it exists.
    assert await service.polls.heatmap(outsider, poll.id) is None
    found = await service.polls.heatmap(organiser, poll.id)
    assert found["ready"] is True and found["known"] == 3 and found["max_free"] == 3
    assert found["days"] and found["days"][0]["cells"][0]["free"] == 3
    assert "user" not in str(found).lower()
    # Two schedules are too few to hide who is busy.
    for telegram_id in (1, 2, 3):
        repo.users[telegram_id].coverage_start = None
    thin = await service.polls.heatmap(organiser, poll.id)
    assert thin["ready"] is False and thin["days"] == [] and thin["known"] == 0


async def test_poll_card_links_to_the_mini_app_when_configured(service_factory):
    service, repo = service_factory(bot_username="AmIFreeBot", mini_app_short_name="free")
    bot, transport = make_bot(service)
    async with bot:
        await seed_poll_card(bot, "sync tomorrow", 30, 1)
    markup = transport.sent[-1]["reply_markup"]
    markup = json.loads(markup) if isinstance(markup, str) else markup
    urls = [b["url"] for row in markup["inline_keyboard"] for b in row if b.get("url")]
    poll = next(iter(repo.polls.values()))
    # One way into the app per card; the organiser's Suggest and Nudge live there.
    assert urls == [f"https://t.me/AmIFreeBot/free?startapp=poll_{poll.id}"]
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert labels == ["🗓 Fill in my availabilities"]
    assert alerts(transport) == []
