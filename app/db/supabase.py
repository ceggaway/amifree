"""All I/O is async and server-side; every event operation requires its owner."""

from datetime import UTC, datetime, time, timedelta

from supabase import AsyncClient

from app.models.timetable import BlockRule, BusyBlock
from app.models.user import User
from app.schemas.event import SG, Event, EventDraft, Status
from app.services.nusmods import ImportResult
from app.services.quick_blocks import expand_blocks


class SupabaseRepository:
    def __init__(self, client: AsyncClient, pending_ttl_hours: int = 24):
        self.client = client
        self.pending_ttl_hours = pending_ttl_hours

    async def ping(self):
        # Check all required tables are present, not merely that the host responds.
        for table in (
            "users",
            "timetable_blocks",
            "events",
            "event_extractions",
            "user_actions",
            "manual_busy_blocks",
        ):
            await self.client.table(table).select("id").limit(1).execute()

    async def ensure_user(self, telegram_id: int) -> User:
        result = await self.client.rpc(
            "amifree_ensure_user", {"p_telegram_id": telegram_id}
        ).execute()
        row = result.data[0]
        expires = row.get("pending_expires_at")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(UTC):
            await self.set_pending(row["id"], None)
            row["pending"] = None
        return User.model_validate(row)

    async def set_pending(self, user_id: str, pending: dict | None):
        expires = (
            (datetime.now(UTC) + timedelta(hours=self.pending_ttl_hours)).isoformat()
            if pending
            else None
        )
        await (
            self.client.table("users")
            .update(
                {
                    "pending": pending,
                    "pending_expires_at": expires,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", user_id)
            .execute()
        )

    async def import_timetable(self, user_id: str, result: ImportResult):
        await self.client.rpc(
            "amifree_import_timetable",
            {
                "p_user_id": user_id,
                "p_blocks": [b.model_dump(mode="json") for b in result.blocks],
                "p_year": result.academic_year,
                "p_semester": result.semester,
                "p_coverage_start": result.coverage_start.isoformat(),
                "p_coverage_end": result.coverage_end.isoformat(),
            },
        ).execute()

    async def busy(self, user_id: str, draft: EventDraft) -> tuple[list[BusyBlock], list[Event]]:
        # Events are fetched for the whole Singapore day so the engine can count a busy day.
        day = datetime.combine(draft.start_at.astimezone(SG).date(), time.min, SG)
        window_start = min(draft.start_at, day)
        window_end = max(draft.end_at, day + timedelta(days=1))

        # Filter BEFORE pagination: unrelated old rows cannot hide a future clash.
        async def fetch(table, extra=False):
            rows = []
            offset = 0
            while True:
                q = (
                    self.client.table(table)
                    .select("*")
                    .eq("user_id", user_id)
                    .lt("start_at", (window_end if extra else draft.end_at).isoformat())
                    .gt("end_at", (window_start if extra else draft.start_at).isoformat())
                    .order("id")
                    .range(offset, offset + 499)
                )
                if extra:
                    q = q.in_("status", ["GOING", "SAVED"])
                batch = (await q.execute()).data
                rows.extend(batch)
                if len(batch) < 500:
                    return rows
                offset += 500

        blocks = await fetch("timetable_blocks")
        events = await fetch("events", True)
        manual = expand_blocks(await self.list_blocks(user_id), draft.start_at, draft.end_at)
        return [BusyBlock.model_validate(b) for b in blocks] + manual, [
            Event.model_validate(e) for e in events
        ]

    async def create_block(self, user_id: str, rule: BlockRule) -> BlockRule:
        row = rule.model_dump(mode="json", exclude={"id", "user_id"}) | {"user_id": user_id}
        result = await self.client.table("manual_busy_blocks").insert(row).execute()
        return BlockRule.model_validate(result.data[0])

    async def list_blocks(self, user_id: str) -> list[BlockRule]:
        result = (
            await self.client.table("manual_busy_blocks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at")
            .limit(500)
            .execute()
        )
        return [BlockRule.model_validate(row) for row in result.data]

    async def delete_block(self, user_id: str, block_id: str) -> bool:
        result = (
            await self.client.table("manual_busy_blocks")
            .delete()
            .eq("user_id", user_id)
            .eq("id", block_id)
            .execute()
        )
        return bool(result.data)

    async def create_preview(self, user_id: str, message_id: int, draft: EventDraft) -> Event:
        result = await self.client.rpc(
            "amifree_create_preview",
            {
                "p_user_id": user_id,
                "p_message_id": message_id,
                "p_draft": draft.model_dump(mode="json"),
            },
        ).execute()
        return Event.model_validate(result.data[0])

    async def get_event(self, user_id: str, event_id: str) -> Event | None:
        result = (
            await self.client.table("events")
            .select("*")
            .eq("user_id", user_id)
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        return Event.model_validate(result.data[0]) if result.data else None

    async def recent_events(self, user_id: str) -> list[Event]:
        now = datetime.now(UTC)
        # Upcoming events must remain discoverable regardless of when they were saved.
        # Fetch each set in pages to avoid the hosted API's response row limit.
        rows = {}
        for column, cutoff in (("end_at", now), ("created_at", now - timedelta(days=30))):
            offset = 0
            while True:
                batch = (
                    await self.client.table("events")
                    .select("*")
                    .eq("user_id", user_id)
                    .neq("status", "CANCELLED")
                    .gte(column, cutoff.isoformat())
                    .order("id")
                    .range(offset, offset + 499)
                    .execute()
                ).data
                rows.update((row["id"], row) for row in batch)
                if len(batch) < 500:
                    break
                offset += 500
        events = [Event.model_validate(row) for row in rows.values()]
        return sorted(events, key=lambda e: (e.end_at < now, e.start_at, e.id))

    async def rsvp(self, user_id: str, event: Event, status: Status) -> Event | None:
        result = await self.client.rpc(
            "amifree_rsvp",
            {
                "p_user_id": user_id,
                "p_event_id": event.id,
                "p_status": status.value,
                "p_version": event.version,
            },
        ).execute()
        return Event.model_validate(result.data[0]) if result.data else None

    async def save(self, user_id: str, event: Event) -> Event | None:
        result = await self.client.rpc(
            "amifree_save",
            {"p_user_id": user_id, "p_event_id": event.id, "p_version": event.version},
        ).execute()
        return Event.model_validate(result.data[0]) if result.data else None

    async def update_event(
        self, user_id: str, event: Event, draft: EventDraft | None, cancel=False
    ) -> Event | None:
        result = await self.client.rpc(
            "amifree_update_event",
            {
                "p_user_id": user_id,
                "p_event_id": event.id,
                "p_draft": draft.model_dump(mode="json") if draft else {},
                "p_version": event.version,
                "p_cancel": cancel,
            },
        ).execute()
        return Event.model_validate(result.data[0]) if result.data else None

    async def action(
        self, user_id: str, kind: str, event_id: str | None = None, metadata: dict | None = None
    ):
        await (
            self.client.table("user_actions")
            .insert(
                {
                    "user_id": user_id,
                    "event_id": event_id,
                    "action_type": kind,
                    "metadata": metadata or {},
                }
            )
            .execute()
        )

    async def extraction(self, user_id: str, text: str, data: dict, state: str):
        await (
            self.client.table("event_extractions")
            .insert(
                {
                    "user_id": user_id,
                    "raw_text": text[:8000],
                    "extracted_json": data,
                    "validation_state": state,
                }
            )
            .execute()
        )

    async def delete_user(self, user_id: str):
        await self.client.table("users").delete().eq("id", user_id).execute()

    async def cleanup(self, days: int):
        await self.client.rpc("amifree_cleanup", {"p_retention_days": days}).execute()
