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
}
VOID_RPCS = {
    "amifree_cleanup",
    "amifree_replace_calendar_blocks",
    "amifree_import_timetable",
    "amifree_touch_member",
    "amifree_finish_notification",
}
SCALAR_RPCS = {"amifree_enqueue_notification", "amifree_poll_join", "amifree_poll_vote"}
TABLES = {
    "users",
    "calendar_busy_blocks",
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
}
COLUMNS = {
    "id",
    "calendar_feed_token",
    "calendar_feed_seen_at",
    "personal_calendar_url",
    "personal_calendar_synced_at",
    "personal_calendar_error",
    "personal_calendar_checked_at",
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
    "title",
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
            if key in ("select", "order", "limit", "offset"):
                continue
            assert key in COLUMNS
            op, operand = value.split(".", 1)
            if op == "in":
                items = [item.strip('"') for item in operand.strip("()").split(",")]
                marks = []
                for item in items:
                    values.append(item)
                    marks.append(f"${len(values)}")
                conditions.append(f"{key} in ({','.join(marks)})")
                continue
            operator = {"eq": "=", "neq": "<>", "lt": "<", "gt": ">", "gte": ">=", "lte": "<="}[op]
            values.append(operand)
            conditions.append(f"{key}{operator}${len(values)}")
        where = " where " + " and ".join(conditions) if conditions else ""
        if request.method == "GET":
            select = request.url.params.get("select", "*")
            assert select in ("id", "*")
            sql = f"select {select} from {endpoint}{where}"
            order = request.url.params.get("order")
            if order:
                column, direction = order.split(".")
                assert column in COLUMNS and direction in ("asc", "desc")
                sql += f" order by {column} {direction}"
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
            values = list(body.values())
            sql = f"insert into {endpoint}({','.join(body)}) values({','.join(f'${i + 1}' for i in range(len(values)))}) returning *"
        elif request.method == "DELETE":
            sql = f"delete from {endpoint}{where} returning *"
        else:
            raise AssertionError("Unsupported test request")
        return httpx.Response(200, json=await self.query(sql, values))
