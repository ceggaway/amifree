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
}
TABLES = {"users", "events", "timetable_blocks", "event_extractions", "user_actions"}
COLUMNS = {
    "id",
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
}


class SQLGateway:
    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            "node",
            str(ROOT / "scripts/sql-tests/gateway.mjs"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
            return httpx.Response(
                200,
                json=None if endpoint in ("amifree_cleanup", "amifree_import_timetable") else rows,
            )
        assert endpoint in TABLES
        values, conditions = [], []
        for key, value in request.url.params.multi_items():
            if key in ("select", "order", "limit", "offset"):
                continue
            assert key in COLUMNS
            op, operand = value.split(".", 1)
            operator = {"eq": "=", "neq": "<>", "lt": "<", "gt": ">", "gte": ">="}[op]
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
