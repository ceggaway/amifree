"""The group side of the Mini App: opened from a group or a card, checked against Telegram."""

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from telegram import Update

from app.config import Settings
from app.main import create_app
from app.schemas.event import EventDraft, Status
from app.services.app_links import chat_code, parse_chat_code
from app.services.group_hub import request_message_id
from tests.conftest import extraction
from tests.test_api import bearer
from tests.test_group_events import CHAT, group_message, make_bot
from tests.test_miniapp_auth import TOKEN, init_data

APP = {"bot_username": "AmIFreeTestBot", "mini_app_short_name": "app"}
CODE = chat_code(CHAT)


@pytest.fixture
async def hub(service_factory, reference):
    service, repo = service_factory(**APP)
    service.clock = lambda: reference
    members = {1, 2, 3}  # Telegram says these people are in the group
    posted = []

    async def check(chat_id, telegram_id):
        return chat_id == CHAT and telegram_id in members

    async def post(chat_id, card):
        posted.append((chat_id, card))
        return 700 + len(posted)

    service.check_chat_member = check
    service.post_group_card = post
    settings = Settings(_env_file=None, bot_mode="disabled", telegram_bot_token=TOKEN)
    app = create_app(settings, service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        yield client, service, repo, posted, members


def markup(message):
    found = message["reply_markup"]
    return json.loads(found) if isinstance(found, str) else found


def event_form(reference, **changes):
    start = reference + timedelta(days=2)
    return {
        "request_id": str(uuid.uuid4()),
        "title": "Dinner",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=2)).isoformat(),
        "location": "Vivo",
    } | changes


def test_chat_codes_and_request_ids_round_trip():
    assert chat_code(-1001234567890) == "n1001234567890"
    assert parse_chat_code("n1001234567890") == -1001234567890
    assert parse_chat_code("p42") == 42
    for bad in ("", "x1", "n", "n12a", "-100"):
        assert parse_chat_code(bad) is None
    request = str(uuid.uuid4())
    assert request_message_id(request) == request_message_id(request) < 0


async def test_only_people_telegram_lists_in_the_group_see_it(hub):
    client, service, repo, posted, members = hub
    assert (await client.get(f"/api/groups/{CODE}", headers=bearer(9))).status_code == 404
    assert (await client.get("/api/groups/nonsense", headers=bearer(1))).status_code == 404
    # Someone who never tapped anything in the group is in, and is counted as a member now.
    seen = await client.get(f"/api/groups/{CODE}", headers=bearer(2))
    assert seen.status_code == 200 and seen.json()["events"] == []
    assert (await repo.ensure_user(2)).id in await repo.group_member_ids(CHAT)


async def test_sign_in_name_shows_in_whos_going(hub, reference):
    client, service, repo, posted, members = hub
    import time

    signed = init_data(telegram_id=2, auth_date=int(time.time()))
    token = (await client.post("/api/auth/telegram", json={"init_data": signed})).json()["token"]
    created = await client.post(
        f"/api/groups/{CODE}/events", headers=bearer(1), json=event_form(reference)
    )
    event_id = created.json()["id"]
    auth = {"Authorization": f"Bearer {token}"}
    going = await client.post(
        f"/api/group-events/{event_id}/rsvp", headers=auth, json={"status": "GOING"}
    )
    assert going.json()["going"] == ["Student"]


async def test_create_an_event_from_the_app_posts_one_card(hub, reference):
    client, service, repo, posted, members = hub
    form = event_form(reference)
    first = await client.post(f"/api/groups/{CODE}/events", headers=bearer(1), json=form)
    assert first.status_code == 200
    again = await client.post(f"/api/groups/{CODE}/events", headers=bearer(1), json=form)
    assert again.json() == first.json() and len(posted) == 1  # a retried submit: one card
    chat_id, card = posted[0]
    assert chat_id == CHAT and "Dinner" in card.text
    # The card opens this event in the app.
    urls = [b.url for row in card.keyboard.inline_keyboard for b in row if b.url]
    assert urls == [f"https://t.me/AmIFreeTestBot/app?startapp=ev_{first.json()['id']}"]
    event = repo.group_events[first.json()["id"]]
    assert event.message_id == 701 and event.creator_user_id == (await repo.ensure_user(1)).id
    overview = (await client.get(f"/api/groups/{CODE}", headers=bearer(2))).json()
    assert [e["title"] for e in overview["events"]] == ["Dinner"]
    assert overview["events"][0]["organiser"] is False
    # Outsiders can't post into the group, and the past is refused.
    outsider = await client.post(f"/api/groups/{CODE}/events", headers=bearer(9), json=form)
    assert outsider.status_code == 404
    past = event_form(reference, start_at=(reference - timedelta(hours=3)).isoformat())
    past["end_at"] = (reference - timedelta(hours=1)).isoformat()
    refused = await client.post(f"/api/groups/{CODE}/events", headers=bearer(1), json=past)
    assert refused.status_code == 409 and "passed" in refused.json()["detail"]["message"]


async def test_a_card_the_bot_cannot_post_is_taken_back(hub, reference):
    from telegram.error import Forbidden

    client, service, repo, posted, members = hub

    async def refused(chat_id, card):
        raise Forbidden("bot was kicked")

    service.post_group_card = refused
    answer = await client.post(
        f"/api/groups/{CODE}/events", headers=bearer(1), json=event_form(reference)
    )
    assert answer.status_code == 409 and "Add AmIFree back" in answer.json()["detail"]["message"]
    assert all(g.status == "CANCELLED" for g in repo.group_events.values())


async def test_a_repeating_event_from_the_app(hub, reference):
    client, service, repo, posted, members = hub
    form = event_form(
        reference, weekdays=[1, 3], until=str((reference + timedelta(days=20)).date())
    )
    created = await client.post(f"/api/groups/{CODE}/events", headers=bearer(1), json=form)
    assert created.status_code == 200
    sessions = [g for g in repo.group_events.values() if g.series_id]
    assert len(sessions) == 6 and {g.start_at.weekday() for g in sessions} == {1, 3}
    assert len(posted) == 1 and "🔁 6 sessions" in posted[0][1].text
    # The overview shows a series once: its next session.
    overview = (await client.get(f"/api/groups/{CODE}", headers=bearer(1))).json()
    assert len(overview["events"]) == 1 and overview["events"][0]["recurring"]
    no_end = event_form(reference, weekdays=[1])
    missing = await client.post(f"/api/groups/{CODE}/events", headers=bearer(1), json=no_end)
    assert missing.status_code == 409 and "last date" in missing.json()["detail"]["message"]


async def test_answering_and_checking_in_from_the_app(hub, reference):
    client, service, repo, posted, members = hub
    event_id = (
        await client.post(
            f"/api/groups/{CODE}/events", headers=bearer(1), json=event_form(reference)
        )
    ).json()["id"]
    url = f"/api/group-events/{event_id}"
    # A clash must be acknowledged, as with "Going anyway" on the card.
    member = await repo.ensure_user(2)
    event = repo.group_events[event_id]
    clash = EventDraft(title="Tutorial", start_at=event.start_at, end_at=event.end_at)
    tutorial = await repo.create_preview(member.id, 5, clash)
    await repo.rsvp(member.id, tutorial, Status.GOING)
    blocked = await client.post(f"{url}/rsvp", headers=bearer(2), json={"status": "GOING"})
    assert blocked.status_code == 409, blocked.json()
    assert blocked.json()["detail"]["reason"] == "clash"
    assert blocked.json()["detail"]["availability"]["conflicts"]
    going = await client.post(
        f"{url}/rsvp", headers=bearer(2), json={"status": "GOING", "force": True}
    )
    assert going.status_code == 200 and going.json()["my_status"] == "GOING"
    assert going.json()["counts"]["GOING"] == 1 and len(going.json()["going"]) == 1
    assert repo.linked(member.id, event_id).status == Status.GOING
    # Check-in opens 30 minutes before the start.
    early = await client.post(f"{url}/check-in", headers=bearer(2))
    assert early.status_code == 409 and "30 minutes" in early.json()["detail"]["message"]
    service.clock = lambda: event.start_at - timedelta(minutes=10)
    here = await client.post(f"{url}/check-in", headers=bearer(2))
    assert here.status_code == 200 and here.json()["checked_in"] is True
    # A member never sees the organiser's tools or anyone's ids.
    assert "manage" not in here.json() and here.json()["organiser"] is False
    # Someone Telegram doesn't list in the group cannot open the card, even with its id.
    assert (await client.get(url, headers=bearer(9))).status_code == 404


async def test_organiser_tools_in_the_app(hub, reference):
    client, service, repo, posted, members = hub
    event_id = (
        await client.post(
            f"/api/groups/{CODE}/events", headers=bearer(1), json=event_form(reference)
        )
    ).json()["id"]
    url = f"/api/group-events/{event_id}"
    await client.post(f"{url}/rsvp", headers=bearer(2), json={"status": "GOING", "force": True})
    await client.post(f"{url}/rsvp", headers=bearer(3), json={"status": "MAYBE"})
    seen = (await client.get(url, headers=bearer(1))).json()
    assert seen["organiser"] is True
    people = seen["manage"]["people"]
    assert [p["answer"] for p in people] == ["GOING", "MAYBE"]
    # Members are refused the organiser's actions.
    version = seen["version"]
    for method, path, body in (
        ("PATCH", url, {"version": version, "title": "Mine"}),
        ("POST", f"{url}/cancel", {"version": version}),
        ("POST", f"{url}/nudge", None),
        ("PUT", f"{url}/attendance", {"user_id": people[0]["id"]}),
    ):
        refused = await client.request(method, path, headers=bearer(2), json=body)
        assert refused.status_code == 409, path
        assert "Only the organiser" in refused.json()["detail"]["message"]
    # Edit: one change, and a stale version is refused.
    edited = await client.patch(
        url, headers=bearer(1), json={"version": version, "location": "Jewel"}
    )
    assert edited.status_code == 200 and edited.json()["event"]["location"] == "Jewel"
    stale = await client.patch(url, headers=bearer(1), json={"version": version, "title": "X"})
    assert stale.status_code == 409 and stale.json()["detail"]["reason"] == "stale"
    # Mark who came: one person, then everyone who said Going.
    service.clock = lambda: repo.group_events[event_id].end_at + timedelta(minutes=5)
    marked = await client.put(
        f"{url}/attendance", headers=bearer(1), json={"user_id": people[1]["id"]}
    )
    assert marked.json()["manage"]["came"] == 1
    everyone = await client.put(f"{url}/attendance", headers=bearer(1), json={})
    assert everyone.json()["manage"]["came"] == 2
    stranger = await client.put(
        f"{url}/attendance", headers=bearer(1), json={"user_id": str(uuid.uuid4())}
    )
    assert stranger.status_code == 409
    # Cancel for everyone.
    service.clock = lambda: reference
    latest = (await client.get(url, headers=bearer(1))).json()["version"]
    cancelled = await client.post(f"{url}/cancel", headers=bearer(1), json={"version": latest})
    assert cancelled.json()["event"]["status"] == "CANCELLED"


async def test_find_a_time_from_the_app(hub, reference):
    client, service, repo, posted, members = hub
    first = reference.date() + timedelta(days=1)
    form = {
        "request_id": str(uuid.uuid4()),
        "title": "Project meeting",
        "minutes": 120,
        "first": str(first),
        "last": str(first + timedelta(days=6)),
        "reply_by": str(first + timedelta(days=1)),
    }
    created = await client.post(f"/api/groups/{CODE}/polls", headers=bearer(1), json=form)
    assert created.status_code == 200
    poll = repo.polls[created.json()["id"]]
    assert poll.message_id == 701 and "Who's free? Project meeting (2h)" in posted[0][1].text
    assert poll.deadline_at.date() == first + timedelta(days=1)
    # As with /whosfree, asking for the poll counts the organiser in, and nobody else.
    assert await repo.poll_participants(poll.id) == [(await repo.ensure_user(1)).id]
    overview = (await client.get(f"/api/groups/{CODE}", headers=bearer(2))).json()
    assert overview["polls"][0]["title"] == "Project meeting"
    too_long = form | {"request_id": str(uuid.uuid4()), "last": str(first + timedelta(days=20))}
    refused = await client.post(f"/api/groups/{CODE}/polls", headers=bearer(1), json=too_long)
    assert refused.status_code == 409


async def test_group_commands_open_the_app(service_factory):
    service, repo = service_factory(extraction(), **APP)
    bot, transport = make_bot(service)
    async with bot:
        for n, command in enumerate(("/event", "/whosfree", "/app", "/help")):
            await bot.process_update(Update.de_json(group_message(command, 70 + n, 1), bot.bot))
        sent = transport.sent[-4:]
        urls = [[b["url"] for row in markup(m)["inline_keyboard"] for b in row] for m in sent]
        assert urls[0] == [f"https://t.me/AmIFreeTestBot/app?startapp=new_{CODE}"]
        assert urls[1] == [f"https://t.me/AmIFreeTestBot/app?startapp=find_{CODE}"]
        assert (
            urls[2]
            == urls[3]
            == [
                f"https://t.me/AmIFreeTestBot/app?startapp={page}_{CODE}"
                for page in ("new", "find", "g")
            ]
        )
        assert "AmIFree in this group" in sent[3]["text"]
        # A card typed in full still posts straight away, with 📱 Open in place of Manage.
        await bot.process_update(
            Update.de_json(group_message("/event Dinner Fri 7-9pm at Vivo", 80, 1), bot.bot)
        )
        card = transport.sent[-1]
        labels = [b["text"] for row in markup(card)["inline_keyboard"] for b in row]
        assert "📱 Open" in labels and "⚙️ Manage" not in labels


async def test_without_the_app_set_up_commands_work_as_before(service_factory):
    service, repo = service_factory()
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(Update.de_json(group_message("/event", 90, 1), bot.bot))
        assert "Reply /event to a message" in transport.sent[-1]["text"]
        assert "reply_markup" not in transport.sent[-1]
