import time

import httpx
import pytest

from app.api.auth import issue_token, session_key
from app.config import Settings
from app.main import create_app
from app.schemas.event import Status
from tests.conftest import extraction
from tests.test_miniapp_auth import TOKEN, init_data

TEXT = "CCA dinner Friday 7–9pm at Vivo"
WEEK = {"start": "2026-10-05T00:00:00+08:00", "end": "2026-10-12T00:00:00+08:00"}


def bearer(telegram_id, ttl=3600):
    return {"Authorization": "Bearer " + issue_token(telegram_id, session_key(TOKEN), ttl)}


@pytest.fixture
async def api(service_factory):
    service, repo = service_factory(extraction(), extraction(title="Hall supper"))
    settings = Settings(_env_file=None, bot_mode="disabled", telegram_bot_token=TOKEN)
    app = create_app(settings, service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        yield client, service, repo


async def saved_plan(service, reference, telegram_id=1):
    # The Mini App still has a Saved state; the card just no longer offers the button.
    await service.message(telegram_id, 2, TEXT, reference)
    user = await service.repo.ensure_user(telegram_id)
    ev = next(e for e in service.repo.events.values() if e.user_id == user.id)
    await service.repo.save(user.id, ev)


async def test_sign_in_requires_a_valid_telegram_signature(api):
    client, service, repo = api
    fresh = init_data(telegram_id=7, auth_date=int(time.time()))
    for body, expected in (
        ({"init_data": fresh.replace("7", "8", 1)}, 401),
        ({"init_data": init_data(telegram_id=7, auth_date=int(time.time()) - 7200)}, 401),
        ({"init_data": init_data(telegram_id=7, auth_date=int(time.time()), token="1:other")}, 401),
        ({"init_data": fresh, "user_id": 1}, 422),  # a posted user ID is not accepted at all
        ({}, 422),
    ):
        assert (await client.post("/api/auth/telegram", json=body)).status_code == expected
    assert not repo.users
    response = await client.post("/api/auth/telegram", json={"init_data": fresh})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    token = response.json()["token"]
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["telegram_user_id"] == 7 and me.json()["nusmods"]["connected"] is False
    assert me.json()["outside_calendars_connected"] is False and me.json()["calendars"] == []


async def test_every_route_rejects_missing_forged_and_expired_sessions(api):
    client, service, repo = api
    routes = [
        ("GET", "/api/me"),
        ("GET", "/api/plans"),
        ("GET", "/api/calendar?start=2026-10-05T00:00:00%2B08:00&end=2026-10-06T00:00:00%2B08:00"),
        (
            "GET",
            "/api/availability?start=2026-10-05T10:00:00%2B08:00&end=2026-10-05T11:00:00%2B08:00",
        ),
        ("GET", "/api/blocks"),
        ("POST", "/api/blocks"),
        ("DELETE", "/api/blocks/5f0c1c0e-0000-4000-8000-000000000000"),
        ("PATCH", "/api/plans/5f0c1c0e-0000-4000-8000-000000000000"),
        ("GET", "/api/group-events/5f0c1c0e-0000-4000-8000-000000000000"),
        ("GET", "/api/polls/5f0c1c0e-0000-4000-8000-000000000000/heatmap"),
    ]
    forged = {"Authorization": "Bearer " + issue_token(1, session_key("1:other-bot"), 3600)}
    for headers in (
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Basic x"},
        forged,
        bearer(1, -5),
    ):
        for method, path in routes:
            response = await client.request(
                method, path, headers=headers, json={"text": "busy sat 2-6pm"}
            )
            assert response.status_code == 401, (method, path, headers)
    assert not repo.users


async def test_api_and_bot_share_plans_and_isolate_users(api, reference):
    client, service, repo = api
    await saved_plan(service, reference)
    mine = (await client.get("/api/plans", headers=bearer(1), params=WEEK)).json()["plans"]
    assert [p["title"] for p in mine] == ["CCA dinner"] and mine[0]["status"] == "SAVED"
    assert mine[0]["availability"]["verdict"] == "UNKNOWN" and not mine[0]["shared"]
    assert (await client.get("/api/plans", headers=bearer(2), params=WEEK)).json() == {"plans": []}
    plan = mine[0]
    url = f"/api/plans/{plan['id']}"
    # Another user's valid session cannot read or change it, and learns nothing about it.
    other = await client.patch(
        url, headers=bearer(2), json={"version": plan["version"], "status": "GOING"}
    )
    assert other.status_code == 404
    assert (
        await client.patch("/api/plans/not-a-uuid", headers=bearer(1), json={"version": 1})
    ).status_code == 404
    stale = await client.patch(url, headers=bearer(1), json={"version": 99, "status": "GOING"})
    assert stale.status_code == 409 and stale.json()["detail"]["reason"] == "stale"
    going = await client.patch(
        url, headers=bearer(1), json={"version": plan["version"], "status": "GOING"}
    )
    assert going.status_code == 200 and going.json()["status"] == "GOING"
    # The bot sees the Mini App's change.
    assert "Status: In your plans" in (await service.command(1, "plans"))[0].text
    edited = await client.patch(
        url,
        headers=bearer(1),
        json={
            "version": going.json()["version"],
            "start_at": "2026-10-09T20:00:00+08:00",
            "end_at": "2026-10-09T22:00:00+08:00",
            "location": "Clementi",
        },
    )
    assert edited.status_code == 200 and edited.json()["location"] == "Clementi"
    event = next(iter(repo.events.values()))
    assert (
        event.start_at.hour == 20 and event.status == Status.GOING and event.title == "CCA dinner"
    )
    bad = await client.patch(
        url,
        headers=bearer(1),
        json={"version": event.version, "end_at": "2026-10-09T19:00:00+08:00"},
    )
    assert bad.status_code == 422
    assert (
        await client.patch(url, headers=bearer(1), json={"version": event.version, "user_id": "x"})
    ).status_code == 422
    # A second plan at the same time needs an explicit "going anyway".
    await service.message(1, 3, "Hall supper Friday 7–9pm", reference)
    supper = next(e for e in repo.events.values() if e.title == "Hall supper")
    supper_url = f"/api/plans/{supper.id}"
    moved = await client.patch(
        supper_url,
        headers=bearer(1),
        json={
            "version": supper.version,
            "start_at": "2026-10-09T21:00:00+08:00",
            "end_at": "2026-10-09T23:00:00+08:00",
        },
    )
    clash = await client.patch(
        supper_url, headers=bearer(1), json={"version": moved.json()["version"], "status": "GOING"}
    )
    assert clash.status_code == 409 and clash.json()["detail"]["reason"] == "clash"
    assert clash.json()["detail"]["availability"]["conflicts"][0]["title"] == "CCA dinner"
    forced = await client.patch(
        supper_url,
        headers=bearer(1),
        json={"version": moved.json()["version"], "status": "GOING", "force": True},
    )
    assert forced.status_code == 200 and forced.json()["availability"]["verdict"] == "CLASH"
    cancelled = await client.patch(
        url, headers=bearer(1), json={"version": event.version, "cancel": True}
    )
    assert cancelled.json()["status"] == "CANCELLED"
    assert (
        await client.patch(
            url, headers=bearer(1), json={"version": cancelled.json()["version"], "status": "GOING"}
        )
    ).status_code == 409


async def test_blocks_calendar_and_availability(api, reference):
    client, service, repo = api
    headers = bearer(1)
    unclear = await client.post("/api/blocks", headers=headers, json={"text": "busy sat"})
    assert unclear.status_code == 422 and "What time" in unclear.json()["detail"]
    preview = await client.post(
        "/api/blocks", headers=headers, json={"text": "work every Wed 6-10pm"}
    )
    assert (
        preview.json()["saved"] is False
        and "Every Wednesday" in preview.json()["block"]["description"]
    )
    assert (await client.get("/api/blocks", headers=headers)).json() == {"blocks": []}
    saved = await client.post(
        "/api/blocks", headers=headers, json={"text": "work every Wed 6-10pm", "confirm": True}
    )
    block_id = saved.json()["block"]["id"]
    assert len((await client.get("/api/blocks", headers=headers)).json()["blocks"]) == 1
    assert (await client.get("/api/blocks", headers=bearer(2))).json() == {"blocks": []}
    # The bot's /blocks sees the Mini App's block, and both use the same clash engine.
    assert "Every Wednesday" in (await service.command(1, "blocks"))[0].text
    check = {"start": "2026-10-07T19:00:00+08:00", "end": "2026-10-07T20:00:00+08:00"}
    busy = (await client.get("/api/availability", headers=headers, params=check)).json()
    assert busy["verdict"] == "CLASH" and busy["conflicts"][0]["source"] == "MANUAL"
    free = (await client.get("/api/availability", headers=bearer(2), params=check)).json()
    assert free["conflicts"] == []
    await saved_plan(service, reference)
    calendar = (await client.get("/api/calendar", headers=headers, params=WEEK)).json()
    assert [b["title"] for b in calendar["blocks"]] == ["Work"]
    assert [p["title"] for p in calendar["plans"]] == ["CCA dinner"]
    for params in (
        {"start": "2026-10-05T00:00:00", "end": "2026-10-06T00:00:00"},  # no timezone
        {"start": WEEK["end"], "end": WEEK["start"]},
        {"start": "2026-10-05T00:00:00+08:00", "end": "2027-10-05T00:00:00+08:00"},
    ):
        assert (
            await client.get("/api/calendar", headers=headers, params=params)
        ).status_code == 422
    assert (await client.delete(f"/api/blocks/{block_id}", headers=bearer(2))).status_code == 404
    assert (await client.delete(f"/api/blocks/{block_id}", headers=headers)).status_code == 204
    assert (await client.delete(f"/api/blocks/{block_id}", headers=headers)).status_code == 404


async def test_shared_plans_and_group_event_access(api, reference):
    client, service, repo = api
    organiser = await repo.ensure_user(1)
    await service.message(1, 2, TEXT, reference)
    event = next(iter(repo.events.values()))
    card = await repo.create_group_event(organiser.id, -500, 10, event)
    member = await repo.ensure_user(2)
    await repo.group_rsvp(member.id, card, Status.GOING)
    url = f"/api/group-events/{card.id}"
    seen = (await client.get(url, headers=bearer(2))).json()
    assert (
        seen["counts"] == {"GOING": 1, "MAYBE": 0, "DECLINED": 0} and seen["my_status"] == "GOING"
    )
    assert "creator_user_id" not in seen and "chat_id" not in seen
    # Knowing the ID is not enough: only people seen in that group may open it.
    assert (await client.get(url, headers=bearer(3))).status_code == 404
    linked = repo.linked(member.id, card.id)
    plans = (await client.get("/api/plans", headers=bearer(2), params=WEEK)).json()["plans"]
    assert plans[0]["shared"] and plans[0]["group_event_id"] == card.id
    plan_url = f"/api/plans/{linked.id}"
    edit = await client.patch(
        plan_url, headers=bearer(2), json={"version": linked.version, "title": "Mine"}
    )
    assert edit.status_code == 403
    maybe = await client.patch(
        plan_url, headers=bearer(2), json={"version": linked.version, "status": "MAYBE"}
    )
    assert maybe.status_code == 200 and await repo.group_counts(card.id) == {"MAYBE": 1}


async def test_api_unavailable_without_a_bot_and_cors_is_opt_in(service_factory):
    service, _ = service_factory()
    no_token = create_app(Settings(_env_file=None, bot_mode="disabled"), service=service)
    dev = create_app(
        Settings(
            _env_file=None,
            bot_mode="disabled",
            telegram_bot_token=TOKEN,
            mini_app_dev_origin="https://dev.example",
        ),
        service=service,
    )
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    async with (
        no_token.router.lifespan_context(no_token),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(no_token), base_url="http://test"
        ) as client,
    ):
        assert (await client.get("/api/me", headers=bearer(1))).status_code == 503
        response = await client.options(
            "/api/me", headers=preflight | {"Origin": "https://dev.example"}
        )
        assert "access-control-allow-origin" not in response.headers
        assert (await client.get("/healthz")).headers["x-content-type-options"] == "nosniff"
    async with (
        dev.router.lifespan_context(dev),
        httpx.AsyncClient(transport=httpx.ASGITransport(dev), base_url="http://test") as client,
    ):
        allowed = await client.options(
            "/api/me", headers=preflight | {"Origin": "https://dev.example"}
        )
        assert allowed.headers["access-control-allow-origin"] == "https://dev.example"
        denied = await client.options(
            "/api/me", headers=preflight | {"Origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in denied.headers
    for bad in (
        {"mini_app_url": "http://insecure.example"},
        {"mini_app_dev_origin": "https://x/path"},
        {"session_secret": "short"},
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, bot_mode="disabled", **bad)


async def test_me_reports_connected_calendars_truthfully(api, reference):
    client, service, repo = api
    user = await repo.ensure_user(7)
    headers = bearer(7)
    me = (await client.get("/api/me", headers=headers)).json()
    assert me["outside_calendars_connected"] is False and me["calendars"] == []
    good, _ = await repo.add_calendar(user.id, "sealed-1", "hash-1", "CCA")
    bad, _ = await repo.add_calendar(user.id, "sealed-2", "hash-2", "Events")
    # A link we have not read yet is not "connected".
    me = (await client.get("/api/me", headers=headers)).json()
    assert me["outside_calendars_connected"] is False
    assert {c["status"] for c in me["calendars"]} == {"PENDING"}
    await repo.replace_calendar(good.id, [], reference)
    me = (await client.get("/api/me", headers=headers)).json()
    assert me["outside_calendars_connected"] is True
    assert {c["label"]: c["status"] for c in me["calendars"]} == {
        "CCA": "CONNECTED",
        "Events": "PENDING",
    }
    # A revoked link is reported as an error, and never exposes the stored (sealed) URL.
    repo.calendars[bad.id].error = "revoked"
    me = (await client.get("/api/me", headers=headers)).json()
    assert {c["label"]: c["status"] for c in me["calendars"]}["Events"] == "ERROR"
    assert "sealed" not in str(me) and "revoked" not in str(me)
    # Someone else's calendars never show up.
    assert (await client.get("/api/me", headers=bearer(8))).json()["calendars"] == []


async def test_sign_in_is_rate_limited_per_caller(api):
    client, service, repo = api
    forged = {"init_data": "auth_date=1&hash=0"}
    codes = [
        (
            await client.post(
                "/api/auth/telegram", json=forged, headers={"x-forwarded-for": "9.9.9.9"}
            )
        ).status_code
        for _ in range(121)
    ]
    assert codes[:120] == [401] * 120 and codes[120] == 429
    # Another caller is unaffected.
    other = await client.post(
        "/api/auth/telegram", json=forged, headers={"x-forwarded-for": "8.8.8.8, 10.0.0.1"}
    )
    assert other.status_code == 401
    # The proxy hop is shared by everyone, so it is never the key: this caller is still limited.
    same = await client.post(
        "/api/auth/telegram", json=forged, headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
    )
    assert same.status_code == 429


async def test_poll_heatmap_is_for_participants_and_shows_counts_only(api, reference):
    client, service, repo = api
    from datetime import datetime

    from app.schemas.event import SG

    for telegram_id in (1, 2, 3, 4):
        await repo.ensure_user(telegram_id)
        repo.users[telegram_id].coverage_start = datetime(2026, 8, 1, tzinfo=SG)
    await service.polls.create(1, -500, 10, "sync 1h tomorrow")
    poll = next(iter(repo.polls.values()))
    path = f"/api/polls/{poll.id}/heatmap"
    # Signed in, but not in the poll: indistinguishable from a poll that doesn't exist.
    assert (await client.get(path, headers=bearer(4))).status_code == 404
    assert (await client.get(path)).status_code == 401
    assert (await client.get("/api/polls/not-an-id/heatmap", headers=bearer(1))).status_code == 404
    # One person's schedule is too few to show without exposing it.
    thin = (await client.get(path, headers=bearer(1))).json()
    assert thin["ready"] is False and thin["days"] == [] and thin["joined"] == 1
    for telegram_id in (2, 3):
        await repo.poll_join(poll.id, (await repo.ensure_user(telegram_id)).id)
    found = (await client.get(path, headers=bearer(2))).json()
    assert found["ready"] is True and found["max_free"] == 3 and found["days"]
    assert set(found["days"][0]["cells"][0]) == {"start_at", "free"}
    assert found["title"] == "sync" and found["duration_minutes"] == 60


async def test_heatmap_refreshes_are_rate_limited(api):
    client, service, repo = api
    await service.polls.create(1, -500, 11, "sync 1h tomorrow")
    poll = next(iter(repo.polls.values()))
    codes = [
        (await client.get(f"/api/polls/{poll.id}/heatmap", headers=bearer(1))).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [200] * 20 and codes[20] == 429
