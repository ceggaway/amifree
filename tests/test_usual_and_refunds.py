"""Usual weekly free times, and refunds of Clubs Pro payments."""

from datetime import timedelta

import pytest
from telegram import Update

from app.services.date_resolution import Clarification
from app.services.usual_times import covers, describe, parse_usual
from tests.conftest import extraction
from tests.test_group_events import CHAT, make_bot
from tests.test_group_gaps import paid, pre_checkout, states
from tests.test_group_reports import run
from tests.test_polls import with_schedule


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tue evenings", "Tue 18:00–22:00"),
        ("tue, thu evenings; sat all day", "Tue 18:00–22:00 · Thu 18:00–22:00 · Sat 08:00–22:00"),
        ("tuesdays and thursdays 2-4pm", "Tue 14:00–16:00 · Thu 14:00–16:00"),
        ("weekends mornings", "Sat 08:00–12:00 · Sun 08:00–12:00"),
        ("sat 11-1pm", "Sat 11:00–13:00"),
        ("wed after 6pm", "Wed 18:00–22:00"),
        ("fri before noon", "Fri 08:00–12:00"),
        ("mon 7pm onwards", "Mon 19:00–22:00"),
        ("tue 8am-9am, tue 8:30am-10am", "Tue 08:00–10:00"),  # overlapping windows merge
    ],
)
def test_usual_phrases(text, expected):
    assert describe(parse_usual(text)) == expected


def test_weekday_ranges_and_groups():
    assert [d for d, _, _ in parse_usual("mon-fri 6-9pm")] == [0, 1, 2, 3, 4]
    assert len(parse_usual("weekdays after 6pm")) == 5
    assert len(parse_usual("every day all day")) == 7
    assert [d for d, _, _ in parse_usual("fri-mon all day")] == [0, 4, 5, 6]


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("evenings", "Which days"),
        ("tue", "When on Tuesday"),
        ("tue evenings, thu", "When on Thursday"),
        ("hello", "couldn't find"),
        ("   ", "Tell me"),
        ("sat 3am-5am", "When on Saturday"),  # outside the 08:00 to 22:00 grid
    ],
)
def test_unclear_usual_times_ask_again(text, fragment):
    with pytest.raises(Clarification, match=fragment):
        parse_usual(text)


def test_covers_needs_the_whole_cell():
    windows = [(1, 18 * 60, 22 * 60)]
    assert covers(windows, 1, 18 * 60, 18 * 60 + 30)
    assert covers(windows, 1, 21 * 60 + 30, 22 * 60)
    assert not covers(windows, 1, 17 * 60 + 30, 18 * 60)
    assert not covers(windows, 2, 18 * 60, 18 * 60 + 30)


async def test_usual_command_sets_shows_and_clears(service_factory):
    service, repo = service_factory()
    assert "Examples:" in (await service.command(1, "usual"))[0].text
    saved = await service.command(1, "usual", "tue evenings, sat all day")
    assert "Tue 18:00–22:00 · Sat 08:00–22:00" in saved[0].text
    assert "Tue 18:00–22:00" in (await service.command(1, "usual"))[0].text
    # A bad one keeps what was there.
    assert "Which days" in (await service.command(1, "usual", "evenings"))[0].text
    assert len(await repo.get_usual((await repo.ensure_user(1)).id)) == 2
    assert "Cleared" in (await service.command(1, "usual", "clear"))[0].text
    assert await repo.get_usual((await repo.ensure_user(1)).id) == []


async def poll_for_monday(service, repo):
    await with_schedule(repo, 1, 2, 3)
    await service.polls.create(1, CHAT, 30, "sync 1h tomorrow")  # Monday 5 Oct
    poll = next(iter(repo.polls.values()))
    for telegram_id in (2, 3):
        await repo.poll_join(poll.id, (await repo.ensure_user(telegram_id)).id)
    return poll


async def test_usual_times_prefill_the_grid_and_count_as_known(service_factory):
    service, repo = service_factory()
    poll = await poll_for_monday(service, repo)
    stranger = await repo.ensure_user(7)  # no schedule at all
    repo.members.add((CHAT, stranger.id))
    assert (await service.polls.my_grid(stranger.id, poll.id))["known"] is False
    await service.command(7, "usual", "mon 6-9pm")
    grid = await service.polls.my_grid(stranger.id, poll.id)
    cells = states(grid)
    assert grid["known"] is True and grid["from_usual"] is True and grid["submitted"] is False
    assert cells["18:00"] == cells["20:30"] == "free"
    assert cells["17:30"] == cells["21:00"] == "off" and cells["08:00"] == "off"
    # They now count towards the heatmap, free only inside their window.
    await repo.poll_join(poll.id, stranger.id)
    found = await service.polls.heatmap((await repo.ensure_user(1)).id, poll.id)
    assert found["known"] == 4
    free = {c["start_at"][11:16]: c["free"] for d in found["days"] for c in d["cells"]}
    assert free["19:00"] == 4 and free["10:00"] == 3
    # A submitted grid replaces the usual times for that poll.
    await service.polls.save_grid(stranger.id, poll.id, ["2026-10-05T10:00:00+08:00"])
    after = {
        c["start_at"][11:16]: c["free"]
        for d in (await service.polls.heatmap((await repo.ensure_user(1)).id, poll.id))["days"]
        for c in d["cells"]
    }
    assert after["10:00"] == 4 and after["19:00"] == 3


async def test_usual_times_on_a_busy_cell_stay_busy(service_factory):
    service, repo = service_factory()
    poll = await poll_for_monday(service, repo)
    await service.command(2, "block", "busy mon 7pm-8pm")
    await service.callback(2, f"confirm:{(await repo.ensure_user(2)).pending['nonce']}")
    await service.command(2, "usual", "mon evenings")
    grid = await service.polls.my_grid((await repo.ensure_user(2)).id, poll.id)
    cells = states(grid)
    assert cells["19:00"] == cells["19:30"] == "busy"
    assert cells["18:00"] == "free" and cells["20:00"] == "free" and cells["12:00"] == "off"


# ---- refunds ----


def pro_service(service_factory, **settings):
    """A service plus its bot; the refund hook is set after the bot, which installs its own."""
    service, repo = service_factory(extraction(), export_requires_pro=True, **settings)
    bot, transport = make_bot(service)
    returned = []

    async def refund(telegram_id, charge_id):
        returned.append((telegram_id, charge_id))

    service.refund_star_payment = refund
    return service, repo, bot, transport, returned


async def buy(bot, charge="charge-1", update_id=9701):
    await bot.process_update(Update.de_json(pre_checkout(f"pro:{CHAT}"), bot.bot))
    await bot.process_update(
        Update.de_json(paid(f"pro:{CHAT}", charge, update_id=update_id), bot.bot)
    )


async def test_refund_returns_the_stars_and_ends_the_pro_time(service_factory):
    service, repo, bot, transport, returned = pro_service(service_factory)
    async with bot:
        await run(bot, "/pro", 90)
        assert "Refundable within 3 days" in transport.invoices[-1]["description"]
        await buy(bot)
        await buy(bot, "charge-2", 9702)
    assert await repo.group_tier(CHAT, service.clock()) == "PRO"
    first_until = repo.tiers[CHAT][1]
    reply = (await service.command(1, "refund"))[0].text
    assert "Refunded 250" in reply and returned == [(1, "charge-2")]
    # The newest purchase is the one removed: 30 days come off.
    assert repo.tiers[CHAT][1] == first_until - timedelta(days=30)
    again = (await service.command(1, "refund"))[0].text
    assert "Refunded 250" in again and returned[-1] == (1, "charge-1")
    assert await repo.group_tier(CHAT, service.clock()) == "FREE"
    nothing = (await service.command(1, "refund"))[0].text
    assert "can't find a Clubs Pro payment" in nothing and len(returned) == 2


async def test_refund_window_and_failures(service_factory):
    service, repo, bot, transport, returned = pro_service(service_factory)
    async with bot:
        await buy(bot)
    # Too late.
    repo.paid_at["charge-1"] = service.clock() - timedelta(days=10)
    assert "can't find" in (await service.command(1, "refund"))[0].text and not returned
    repo.paid_at["charge-1"] = service.clock()

    # Telegram says no: nothing changes, so the buyer can try again.
    async def failing(telegram_id, charge_id):
        raise ConnectionError("telegram down")

    service.refund_star_payment = failing
    assert "didn't accept the refund" in (await service.command(1, "refund"))[0].text
    assert "charge-1" not in repo.refunded and await repo.group_tier(CHAT, service.clock()) == "PRO"

    # Refunded earlier on Telegram's side: treated as done, and the Pro time still ends.
    async def already(telegram_id, charge_id):
        raise RuntimeError("Bad Request: CHARGE_ALREADY_REFUNDED")

    service.refund_star_payment = already
    assert "Refunded 250" in (await service.command(1, "refund"))[0].text
    assert await repo.group_tier(CHAT, service.clock()) == "FREE"


async def test_a_payment_that_cannot_be_matched_is_refunded_at_once(service_factory):
    service, repo, bot, transport, returned = pro_service(service_factory)
    async with bot:
        await bot.process_update(Update.de_json(paid("pro:garbage", "charge-x"), bot.bot))
    assert returned == [(1, "charge-x")]
    assert "refunding it" in transport.sent[-1]["text"]
    assert "charge-x" not in repo.payments and CHAT not in repo.tiers


async def test_refund_needs_the_hook_and_is_private_only(service_factory):
    service, repo = service_factory(extraction(), export_requires_pro=True)
    bot, transport = make_bot(service)
    service.refund_star_payment = None
    async with bot:
        await buy(bot)
    assert "didn't accept the refund" in (await service.command(1, "refund"))[0].text
    async with bot:
        await run(bot, "/refund", 91)
    assert "private chat" in transport.sent[-1]["text"]
