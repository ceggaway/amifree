import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions

from app.db.supabase import SupabaseRepository
from app.schemas.event import EventDraft, Status


async def test_sdk_queries_are_scoped_and_rpcs_match_schema(reference):
    uid, eid = str(uuid4()), str(uuid4())
    draft = EventDraft(title="Dinner", start_at=reference, end_at=reference + timedelta(hours=1))
    event = draft.model_dump(mode="json") | {
        "id": eid,
        "user_id": uid,
        "source_message_id": 2,
        "status": "PREVIEW",
        "version": 1,
    }
    calls = []

    def handler(request):
        calls.append(request)
        path = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else {}
        assert request.headers["apikey"] == "test-service-role-key"
        if path == "amifree_ensure_user":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": uid,
                        "telegram_user_id": 1,
                        "onboarding_status": "READY",
                        "coverage_start": reference.isoformat(),
                        "coverage_end": draft.end_at.isoformat(),
                        "pending": {},
                        "pending_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    }
                ],
            )
        if path in ("amifree_create_preview", "amifree_rsvp", "amifree_update_event"):
            assert body["p_user_id"] == uid
            return httpx.Response(200, json=[event])
        if path == "schema_migrations":
            return httpx.Response(200, json=[{"version": 16}])
        if path == "events":
            if request.url.params.get("select") == "id":
                assert request.url.params.get("limit") == "1"
                return httpx.Response(200, json=[])
            assert request.url.params["user_id"] == f"eq.{uid}"
            return httpx.Response(200, json=[event])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = await acreate_client(
            "https://example.supabase.co",
            "test-service-role-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        user = await repo.ensure_user(1)
        assert user.pending is None
        assert any(r.method == "PATCH" and r.url.params["id"] == f"eq.{uid}" for r in calls)
        await repo.ping()
        blocks, events = await repo.busy(uid, draft)
        assert events[0].id == eid
        queries = [
            r
            for r in calls
            if r.url.path.endswith(("/events", "/timetable_blocks")) and "start_at" in r.url.params
        ]
        assert len(queries) == 2
        assert all(r.url.params["user_id"] == f"eq.{uid}" for r in queries)
        assert all(
            r.url.params["start_at"].startswith("lt.") and r.url.params["end_at"].startswith("gt.")
            for r in queries
        )
        preview = await repo.create_preview(uid, 2, draft)
        await repo.get_event(uid, eid)
        await repo.recent_events(uid)
        await repo.rsvp(uid, preview, Status.GOING)
        await repo.update_event(uid, preview, draft)
        await repo.delete_user(uid)
        assert any(r.method == "DELETE" and r.url.params["id"] == f"eq.{uid}" for r in calls)


async def test_ping_names_missing_migration_objects_and_fails():
    import logging

    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if path in ("personal_calendars", "amifree_replace_calendar"):
            return httpx.Response(404, json={"message": "not found", "code": "42P01"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = await acreate_client(
            "https://example.supabase.co",
            "test-service-role-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("app.db.supabase")
        logger.addHandler(Capture())
        try:
            await repo.ping()
        except RuntimeError as exc:
            assert "out of date" in str(exc)
        else:
            raise AssertionError("ping accepted a database without migration 007")
    assert any("personal_calendars" in m and "amifree_replace_calendar" in m for m in records)


async def test_ping_rejects_a_database_behind_the_required_schema_version():
    def handler(request):
        if request.url.path.rsplit("/", 1)[-1] == "schema_migrations":
            return httpx.Response(200, json=[{"version": 7}])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = await acreate_client(
            "https://example.supabase.co",
            "test-service-role-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        try:
            await SupabaseRepository(client).ping()
        except RuntimeError as exc:
            assert "out of date" in str(exc)
        else:
            raise AssertionError("ping accepted schema version 7")
