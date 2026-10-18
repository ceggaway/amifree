"""Translate the app's PostgREST requests into real SQL for local integration tests.

This implements only the tested request subset, not a replacement PostgREST server.
"""

import asyncio
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
RPC_PARAMS = {
    "amifree_ensure_user": ["p_telegram_id"],
    "amifree_import_timetable": [
        "p_user_id",
        "p_blocks",
        "p_year",
        "p_semester",
        "p_coverage_start",
        "p_coverage_end",
    ],
    "amifree_create_preview": ["p_user_id", "p_message_id", "p_draft"],
    "amifree_rsvp": ["p_user_id", "p_event_id", "p_status", "p_version"],
    "amifree_update_event": ["p_user_id", "p_event_id", "p_draft", "p_version", "p_cancel"],
    "amifree_cleanup": ["p_retention_days"],
    "amifree_replace_calendar_blocks": ["p_user_id", "p_blocks", "p_synced_at"],
    "amifree_replace_calendar": ["p_calendar_id", "p_blocks", "p_synced_at"],
    "amifree_save": ["p_user_id", "p_event_id", "p_version"],
    "amifree_create_group_event": ["p_user_id", "p_chat_id", "p_source_message_id", "p_draft"],
    "amifree_touch_member": ["p_chat_id", "p_user_id"],
    "amifree_group_rsvp": ["p_user_id", "p_group_event_id", "p_status"],
    "amifree_group_counts": ["p_group_event_id"],
    "amifree_update_group_event": [
        "p_user_id",
        "p_group_event_id",
        "p_draft",
        "p_version",
        "p_cancel",
    ],
    "amifree_enqueue_notification": ["p_user_id", "p_kind", "p_key", "p_payload"],
    "amifree_claim_notifications": ["p_limit"],
    "amifree_finish_notification": ["p_id", "p_ok", "p_permanent"],
    "amifree_create_poll": [
        "p_user_id",
        "p_chat_id",
        "p_source_message_id",
        "p_title",
        "p_duration",
        "p_window_start",
        "p_window_end",
    ],
    "amifree_poll_join": ["p_poll_id", "p_user_id"],
    "amifree_poll_open": ["p_poll_id", "p_user_id", "p_slots"],
    "amifree_poll_close": ["p_poll_id", "p_user_id"],
    "amifree_poll_vote": ["p_poll_id", "p_user_id", "p_votes"],
    "amifree_poll_tally": ["p_poll_id"],
    "amifree_replace_usual": ["p_user_id", "p_windows"],
    "amifree_claim_update": ["p_update_id"],
    "amifree_finish_update": ["p_update_id"],
    "amifree_release_update": ["p_update_id"],
}
VOID_RPCS = {
    "amifree_replace_usual",
    "amifree_cleanup",
    "amifree_replace_calendar_blocks",
    "amifree_replace_calendar",
    "amifree_import_timetable",
    "amifree_touch_member",
    "amifree_finish_notification",
    "amifree_finish_update",
    "amifree_release_update",
}
SCALAR_RPCS = {
    "amifree_enqueue_notification",
    "amifree_poll_join",
    "amifree_poll_vote",
    "amifree_claim_update",
}
TABLES = {
    "users",
    "calendar_busy_blocks",
    "personal_calendars",
    "events",
    "timetable_blocks",
    "event_extractions",
    "user_actions",
    "manual_busy_blocks",
    "group_events",
    "group_members",
    "event_changes",
    "notification_logs",
    "polls",
    "poll_participants",
    "poll_votes",
    "schema_migrations",
    "processed_updates",
    "event_series",
    "attendance",
    "attendance_checks",
    "group_plans",
    "group_settings",
    "event_suggestions",
    "poll_availability",
    "pro_payments",
    "weekly_availability",
    "deadlines",
}
COLUMNS = {
    "version",
    "name",
    "update_id",
    "claimed_at",
    "id",
    "title",
    "due_at",
    "notes",
    "done",
    "source_message_id",
    "calendar_feed_token",
    "calendar_feed_seen_at",
    "personal_calendar_url",
    "personal_calendar_synced_at",
    "personal_calendar_error",
    "personal_calendar_checked_at",
    "calendar_id",
    "sealed_url",
    "url_hash",
    "label",
    "synced_at",
    "checked_at",
    "error",
    "user_id",
    "status",
    "start_at",
    "end_at",
    "created_at",
    "updated_at",
    "pending",
    "pending_expires_at",
    "event_id",
    "action_type",
    "metadata",
    "raw_text",
    "extracted_json",
    "validation_state",
    "kind",
    "weekday",
    "start_time",
    "end_time",
    "start_date",
    "until_date",
    "group_event_id",
    "chat_id",
    "message_id",
    "reminders_enabled",
    "dm_blocked",
    "poll_id",
    "window_end",
    "deadline_at",
    "display_name",
    "username",
    "series_id",
    "occurrence_date",
    "edited_alone",
    "ended_at",
    "answer",
    "present",
    "marked_by",
    "tier",
    "expires_at",
    "detection_enabled",
    "enabled_by_telegram_id",
    "enabled_at",
    "author_telegram_id",
    "draft",
    "weekdays",
    "first_date",
    "creator_user_id",
    "location",
    "assumed_duration",
    "free",
    "charge_id",
    "stars",
    "days",
    "paid_at",
    "marked_at",
    "telegram_id",
    "refunded_at",
    "start_min",
    "end_min",
}


class SQLGateway:
    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            "node",
            str(ROOT / "scripts/sql-tests/gateway.mjs"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # A full 500-row PostgREST page exceeds asyncio's default 64 KiB.
            limit=8 * 1024 * 1024,
        )
        self.lock = asyncio.Lock()
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=30)
        assert line and json.loads(line).get("ready"), "SQL test gateway failed to start"
        return self

    async def __aexit__(self, *args):
        self.process.stdin.close()
        await asyncio.wait_for(self.process.wait(), timeout=10)

    async def query(self, sql, values=None):
        values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in (values or [])]
        async with self.lock:
            self.process.stdin.write((json.dumps({"sql": sql, "values": values}) + "\n").encode())
            await self.process.stdin.drain()
            result = json.loads(await asyncio.wait_for(self.process.stdout.readline(), timeout=10))
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["rows"]

    async def handle(self, request):
        assert request.headers["apikey"] == "local-schema-test-key"
        endpoint = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else {}
        if "/rpc/" in request.url.path:
            params = RPC_PARAMS[endpoint]
            placeholders = ",".join(f"${i + 1}" for i in range(len(params)))
            rows = await self.query(
                f"select * from {endpoint}({placeholders})", [body[p] for p in params]
            )
            if endpoint in SCALAR_RPCS:
                return httpx.Response(200, json=rows[0][endpoint])
            return httpx.Response(200, json=None if endpoint in VOID_RPCS else rows)
        assert endpoint in TABLES
        values, conditions = [], []
        for key, value in request.url.params.multi_items():
            if key in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            if key == "or":
                # e.g. (checked_at.is.null,checked_at.lt.2026-10-13T00:00:00Z)
                parts = []
                for term in value.strip("()").split(","):
                    column, op, operand = term.split(".", 2)
                    assert column in COLUMNS
                    if op == "is":
                        assert operand == "null"
                        parts.append(f"{column} is null")
                    else:
                        operator = {"lt": "<", "gt": ">", "gte": ">=", "lte": "<="}[op]
                        values.append(operand)
                        parts.append(f"{column}{operator}${len(values)}")
                conditions.append("(" + " or ".join(parts) + ")")
                continue
            assert key in COLUMNS, key
            op, operand = value.split(".", 1)
            if op == "in":
                items = [item.strip('"') for item in operand.strip("()").split(",")]
                marks = []
                for item in items:
                    values.append(item)
                    marks.append(f"${len(values)}")
                conditions.append(f"{key} in ({','.join(marks)})")
                continue
            if op == "is":
                assert operand == "null"
                conditions.append(f"{key} is null")
                continue
            operator = {"eq": "=", "neq": "<>", "lt": "<", "gt": ">", "gte": ">=", "lte": "<="}[op]
            values.append({"true": True, "false": False}.get(operand, operand))
            conditions.append(f"{key}{operator}${len(values)}")
        where = " where " + " and ".join(conditions) if conditions else ""
        if request.method == "GET":
            select = request.url.params.get("select", "*")
            assert select == "*" or all(c in COLUMNS for c in select.split(","))
            sql = f"select {select} from {endpoint}{where}"
            order = request.url.params.get("order")
            if order:
                terms = []
                for term in order.split(","):
                    column, direction, *nulls = term.split(".")
                    assert column in COLUMNS and direction in ("asc", "desc")
                    assert nulls in ([], ["nullsfirst"], ["nullslast"])
                    suffix = {"nullsfirst": " nulls first", "nullslast": " nulls last"}
                    terms.append(f"{column} {direction}{suffix[nulls[0]] if nulls else ''}")
                sql += " order by " + ", ".join(terms)
            for key in ("limit", "offset"):
                if key in request.url.params:
                    sql += f" {key} {int(request.url.params[key])}"
        elif request.method == "PATCH":
            assignments = []
            for key, value in body.items():
                assert key in COLUMNS
                values.append(value)
                assignments.append(f"{key}=${len(values)}")
            sql = f"update {endpoint} set {','.join(assignments)}{where} returning *"
        elif request.method == "POST":
            assert all(key in COLUMNS for key in body)
            # PostgreSQL array columns take {1,3}, not the JSON the client library sends.
            values = [
                "{" + ",".join(map(str, v)) + "}" if k == "weekdays" else v for k, v in body.items()
            ]
            sql = f"insert into {endpoint}({','.join(body)}) values({','.join(f'${i + 1}' for i in range(len(values)))})"
            conflict = request.url.params.get("on_conflict")
            if conflict:
                # PostgREST upsert: Prefer: resolution=merge-duplicates
                assert all(c in COLUMNS for c in conflict.split(","))
                updates = [k for k in body if k not in conflict.split(",")]
                sql += f" on conflict({conflict}) do update set " + ",".join(
                    f"{k}=excluded.{k}" for k in updates
                )
            sql += " returning *"
        elif request.method == "DELETE":
            sql = f"delete from {endpoint}{where} returning *"
        else:
            raise AssertionError("Unsupported test request")
        return httpx.Response(200, json=await self.query(sql, values))
