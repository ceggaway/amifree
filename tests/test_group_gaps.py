"""Members' own availability grid, organiser attendance, Clubs Pro payments, alerts."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
from telegram import Update

from app.api.auth import issue_token, session_key
from app.config import Settings
from app.main import create_app
from app.schemas.event import SG
from app.services.jobs import Notifier
from tests.conftest import extraction
from tests.test_api import TOKEN
from tests.test_group_events import CHAT, buttons, make_bot
from tests.test_group_reports import finished_event, run
from tests.test_notifications import Outbox
from tests.test_polls import with_schedule


async def make_poll(service, repo, members=(1, 2, 3)):
    await with_schedule(repo, *members)
    await service.polls.create(1, CHAT, 30, "sync 1h tomorrow")
    poll = next(iter(repo.polls.values()))
    for telegram_id in members[1:]:
        await repo.poll_join(poll.id, (await repo.ensure_user(telegram_id)).id)
    return poll


def states(grid):
    return {c["start_at"][11:16]: c["state"] for d in grid["days"] for c in d["cells"]}


async def test_grid_prefills_from_the_schedule_and_never_says_why(service_factory):
    service, repo = service_factory()
    poll = await make_poll(service, repo)
    await service.command(2, "block", "busy mon 9am-10am")
    await service.callback(
        2,
        (await repo.ensure_user(2)).pending["nonce"]
        and f"confirm:{(await repo.ensure_user(2)).pending['nonce']}",
    )
    me = (await repo.ensure_user(2)).id
    grid = await service.polls.my_grid(me, poll.id)
    cells = states(grid)
    assert cells["09:00"] == cells["09:30"] == "busy"
    assert cells["08:00"] == cells["10:00"] == "free"
    assert grid["known"] is True and grid["submitted"] is False
    # Only a state per cell: no title, no reason.
    assert set(grid["days"][0]["cells"][0]) == {"start_at", "state"}
    assert "block" not in json.dumps(grid).lower()


async def test_unknown_schedule_starts_empty_not_free(service_factory):
    service, repo = service_factory()
    poll = await make_poll(service, repo, members=(1, 2, 3))
    stranger = (await repo.ensure_user(7)).id
    repo.members.add((CHAT, stranger))
    grid = await service.polls.my_grid(stranger, poll.id)
    assert grid["known"] is False and set(states(grid).values()) == {"off"}


async def test_saved_cells_drive_the_heatmap_and_suggestions(service_factory):
    service, repo = service_factory()
    poll = await make_poll(service, repo, members=(1, 2, 3, 4))
    outsider = (await repo.ensure_user(9)).id
    assert await service.polls.my_grid(outsider, poll.id) is None
    stranger = (await repo.ensure_user(4)).id  # in the poll, but no schedule at all
    repo.users[4].coverage_start = None
    thin = await service.polls.heatmap((await repo.ensure_user(1)).id, poll.id)
    assert thin["known"] == 3
    grid = await service.polls.my_grid(stranger, poll.id)
    first = next(c["start_at"] for d in grid["days"] for c in d["cells"])
    result = await service.polls.save_grid(
        stranger, poll.id, [first, first, "2026-10-05T03:00:00+08:00"]
    )
    assert result == {"saved": 1, "dropped": 0}  # a time outside the grid is ignored
    after = await service.polls.heatmap((await repo.ensure_user(1)).id, poll.id)
    assert after["known"] == 4 and after["max_free"] == 4
    cells = {c["start_at"]: c["free"] for d in after["days"] for c in d["cells"]}
    assert cells[first] == 4 and sorted(set(cells.values())) == [3, 4]
    # Ticking a cell the schedule blocks is dropped, not trusted.
    await service.command(2, "block", "busy mon 9am-10am")
    me = await repo.ensure_user(2)
    await service.callback(2, f"confirm:{(await repo.ensure_user(2)).pending['nonce']}")
    blocked = next(
        c["start_at"]
        for d in (await service.polls.my_grid(me.id, poll.id))["days"]
        for c in d["cells"]
        if c["state"] == "busy"
    )
    saved = await service.polls.save_grid(me.id, poll.id, [blocked, first])
    assert saved == {"saved": 1, "dropped": 1}
    with pytest.raises(ValueError):
        await service.polls.save_grid(me.id, poll.id, ["not a time"])


async def test_closed_polls_refuse_new_answers(service_factory):
    service, repo = service_factory()
    poll = await make_poll(service, repo)
    repo.polls[poll.id].status = "CLOSED"
    me = (await repo.ensure_user(2)).id
    with pytest.raises(ValueError, match="closed"):
        await service.polls.save_grid(me, poll.id, [])


@pytest.fixture
async def api(service_factory):
    service, repo = service_factory()
    app = create_app(
        Settings(_env_file=None, bot_mode="disabled", telegram_bot_token=TOKEN), service=service
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        yield client, service, repo


def bearer(telegram_id):
    return {"Authorization": "Bearer " + issue_token(telegram_id, session_key(TOKEN), 3600)}


async def test_availability_api_round_trip(api):
    client, service, repo = api
    poll = await make_poll(service, repo)
    path = f"/api/polls/{poll.id}/availability"
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=bearer(8))).status_code == 404
    grid = (await client.get(path, headers=bearer(2))).json()
    cells = [c["start_at"] for d in grid["days"] for c in d["cells"]]
    assert grid["known"] and len(cells) == 28
    saved = await client.put(path, json={"free": cells[:4]}, headers=bearer(2))
    assert saved.json() == {"saved": 4, "dropped": 0}
    again = (await client.get(path, headers=bearer(2))).json()
    assert again["submitted"] is True
    assert [c["state"] for d in again["days"] for c in d["cells"]][:5] == ["free"] * 4 + ["off"]
    assert (
        await client.put(path, json={"free": ["nope-nope-nope"]}, headers=bearer(2))
    ).status_code == 422
    assert (
        await client.put(path, json={"free": [], "extra": 1}, headers=bearer(2))
    ).status_code == 422
    assert (
        await client.put(path, json={"free": ["x" * 20] * 801}, headers=bearer(2))
    ).status_code == 422
    repo.polls[poll.id].status = "CLOSED"
    assert (await client.put(path, json={"free": []}, headers=bearer(2))).status_code == 409


# ---- the organiser marks who came ----


async def organiser_panel(service, repo, bot, transport):
    async with bot:
        await run(bot, "/attendance", 90)
    sent = [p for p in transport.sent if int(p["chat_id"]) == 1][-1]
    return sent, buttons(sent["reply_markup"])


async def test_organiser_ticks_who_came(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo, going=(2, 3, 4), came=(2,))
    sent, taps = await organiser_panel(service, repo, bot, transport)
    assert "Tap a name to mark who came" in sent["text"] and "Came: 1" in sent["text"]
    assert taps["✅ Member 2"].startswith("at:") and "⬜ Member 3" in taps
    assert all(len(d.encode()) <= 64 for d in taps.values())
    # Tap Member 3 on, Member 2 off.
    toggled = await service.callback(1, taps["⬜ Member 3"])
    assert toggled[0].edit and "Came: 2" in toggled[0].text
    labels = [b.text for r in toggled[0].keyboard.inline_keyboard for b in r]
    assert "✅ Member 3" in labels and "✅ Member 2" in labels
    gone = await service.callback(1, taps["✅ Member 2"])
    assert "Came: 1" in gone[0].text
    event_id = next(iter(repo.group_events))
    present = await repo.attendees(event_id)
    assert present == {(await repo.ensure_user(3)).id}
    # Everyone who said Going came.
    keys = [b.callback_data for r in gone[0].keyboard.inline_keyboard for b in r]
    all_going = await service.callback(1, next(k for k in keys if k.startswith("aa:")))
    assert "Came: 3" in all_going[0].text
    # The ended card now says how many went.
    start = repo.group_events[event_id].start_at
    service.clock = lambda: start + timedelta(hours=5)
    await service.groups.sweep_ended(service.clock())
    edits = [p for n, p in transport.calls if n == "editMessageText"]
    assert "3 went" in edits[-1]["text"]


async def test_only_the_organiser_can_mark_and_stale_panels_refresh(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo, going=(2, 3), came=())
    sent, taps = await organiser_panel(service, repo, bot, transport)
    tap = next(iter(taps.values()))
    assert (await service.callback(2, tap))[0].text == "Only the organiser can mark attendance."
    # Someone new checks in after the panel was drawn: the old buttons redraw, not mis-toggle.
    event_id = next(iter(repo.group_events))
    await repo.check_in(event_id, (await repo.ensure_user(50)).id, (await repo.ensure_user(50)).id)
    fresh = await service.callback(1, tap)
    assert fresh[0].edit and await repo.attendees(event_id) == {(await repo.ensure_user(50)).id}
    assert (await service.callback(1, "at:zz:0:abcd"))[0].text.startswith("Only the organiser")


async def test_attendance_panel_pages_large_groups(service_factory):
    service, repo = service_factory(extraction())
    bot, transport, event = await finished_event(service, repo, going=tuple(range(10, 40)), came=())
    sent, taps = await organiser_panel(service, repo, bot, transport)
    assert "More ›" in taps and "‹ Back" not in taps
    page = await service.callback(1, taps["More ›"])
    labels = [b.text for r in page[0].keyboard.inline_keyboard for b in r]
    assert "‹ Back" in labels and "2/2" in labels
    assert not any(label.startswith(("✅ Member 10", "⬜ Member 10")) for label in labels)


async def test_attendance_needs_an_event_that_has_started(service_factory):
    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "/attendance", 91)
    assert "no event in this group that started" in transport.sent[-1]["text"]


# ---- Clubs Pro ----


async def test_pro_is_free_for_now(service_factory):
    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "/pro", 92)
    assert "free for now" in transport.sent[-1]["text"] and not transport.invoices


def pre_checkout(payload, amount=250, currency="XTR", update_id=9700):
    return {
        "update_id": update_id,
        "pre_checkout_query": {
            "id": "pc1",
            "from": {"id": 1, "is_bot": False, "first_name": "Org"},
            "currency": currency,
            "total_amount": amount,
            "invoice_payload": payload,
        },
    }


def paid(payload, charge, amount=250, update_id=9701):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1791100800,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Org"},
            "successful_payment": {
                "currency": "XTR",
                "total_amount": amount,
                "invoice_payload": payload,
                "telegram_payment_charge_id": charge,
                "provider_payment_charge_id": "",
            },
        },
    }


async def test_buying_pro_with_stars(service_factory):
    service, repo = service_factory(extraction(), export_requires_pro=True, pro_price_stars=250)
    bot, transport = make_bot(service)
    async with bot:
        await run(bot, "/pro", 93)
        assert "invoice privately" in transport.sent[-1]["text"]
        invoice = transport.invoices[-1]
        assert int(invoice["chat_id"]) == 1 and invoice["currency"] == "XTR"
        assert invoice["payload"] == f"pro:{CHAT}" and "250" in json.dumps(invoice["prices"])
        # Telegram asks before charging: a wrong price or payload is refused.
        for bad in (pre_checkout("pro:x"), pre_checkout(f"pro:{CHAT}", amount=1)):
            await bot.process_update(Update.de_json(bad, bot.bot))
            assert transport.calls[-1][0] == "answerPreCheckoutQuery"
            assert transport.calls[-1][1]["ok"] is False
        await bot.process_update(Update.de_json(pre_checkout(f"pro:{CHAT}"), bot.bot))
        assert transport.calls[-1][1]["ok"] is True
        assert await repo.group_tier(CHAT, service.clock()) == "FREE"
        await bot.process_update(Update.de_json(paid(f"pro:{CHAT}", "charge-1"), bot.bot))
        assert "Clubs Pro is active" in transport.sent[-1]["text"]
        until = repo.tiers[CHAT][1]
        assert await repo.group_tier(CHAT, service.clock()) == "PRO"
        # The same payment arriving again changes nothing.
        await bot.process_update(
            Update.de_json(paid(f"pro:{CHAT}", "charge-1", update_id=9702), bot.bot)
        )
        assert "already applied" in transport.sent[-1]["text"] and repo.tiers[CHAT][1] == until
        # A second purchase adds to the time that is left.
        await bot.process_update(
            Update.de_json(paid(f"pro:{CHAT}", "charge-2", update_id=9703), bot.bot)
        )
        assert repo.tiers[CHAT][1] == until + timedelta(days=30)
        await run(bot, "/pro", 94)
        assert (
            "already active" in transport.sent[-1]["text"]
            or "is active" in transport.sent[-1]["text"]
        )


async def test_a_payment_interrupted_midway_still_gives_pro(service_factory):
    service, repo = service_factory(export_requires_pro=True, pro_price_stars=250)
    record = repo.record_payment

    async def database_blip(*args):
        raise ConnectionError("database blip")

    repo.record_payment = database_blip
    with pytest.raises(ConnectionError):
        await service.pro.complete(1, f"pro:{CHAT}", "charge-9", "XTR", 250)
    # Telegram retries the payment update: the buyer must end up with Pro, not "already applied".
    repo.record_payment = record
    text, refund = await service.pro.complete(1, f"pro:{CHAT}", "charge-9", "XTR", 250)
    assert "Clubs Pro is active" in text and not refund
    assert await repo.group_tier(CHAT, service.clock()) == "PRO"
    assert await repo.payment_recorded("charge-9")


# ---- monitoring ----


async def test_failing_background_steps_alert_the_operator_once(service_factory):
    service, repo = service_factory(alert_telegram_id=777, alert_cooldown_minutes=60)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)

    async def broken(now):
        raise ConnectionError("database blip")

    service.groups.sweep_ended = broken
    for _ in range(2):
        await service.notifier.run()
    assert outbox.sent == []  # two failures in a row are not yet an alert
    await service.notifier.run()
    assert len(outbox.sent) == 1 and outbox.sent[0][0] == 777
    assert "'ended' has failed 3 times" in outbox.sent[0][1]
    for _ in range(3):
        await service.notifier.run()
    assert len(outbox.sent) == 1  # once per cooldown
    assert (await service.notifier.stats())["step_failures"] == {"ended": 6}
    service.clock = lambda: (
        service.clock.__wrapped__() if False else datetime(2026, 10, 4, 14, tzinfo=SG)
    )
    await service.notifier.run()
    assert len(outbox.sent) == 2
    # A step that recovers stops counting.
    service.groups.sweep_ended = type(service.groups).sweep_ended.__get__(service.groups)
    await service.notifier.run()
    assert (await service.notifier.stats())["step_failures"] == {}


async def test_no_alert_target_means_no_alert(service_factory):
    service, repo = service_factory()
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)

    async def broken(now):
        raise ConnectionError("x")

    service.groups.sweep_ended = broken
    for _ in range(4):
        await service.notifier.run()
    assert outbox.sent == []
