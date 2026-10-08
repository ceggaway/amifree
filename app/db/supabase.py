"""All I/O is async and server-side; every event operation requires its owner."""

from datetime import UTC, datetime, time, timedelta

from supabase import AsyncClient

from app.models.timetable import BlockRule, BusyBlock
from app.models.user import User
from app.schemas.event import SG, Event, EventDraft, GroupEvent, Poll, Status
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
            "group_events",
            "notification_logs",
            "polls",
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

    async def events_between(
        self,
        start: datetime,
        end: datetime,
        statuses: list[Status],
        user_id: str | None = None,
    ) -> list[Event]:
        """Events starting in [start, end). Without a user this spans everyone (jobs only)."""
        rows, offset = [], 0
        while True:
            q = (
                self.client.table("events")
                .select("*")
                .gte("start_at", start.isoformat())
                .lt("start_at", end.isoformat())
                .in_("status", [s.value for s in statuses])
                .order("id")
                .range(offset, offset + 499)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            batch = (await q.execute()).data
            rows.extend(batch)
            if len(batch) < 500:
                return [Event.model_validate(row) for row in rows]
            offset += 500

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

    async def create_group_event(
        self, user_id: str, chat_id: int, message_id: int, draft: EventDraft
    ) -> GroupEvent:
        result = await self.client.rpc(
            "amifree_create_group_event",
            {
                "p_user_id": user_id,
                "p_chat_id": chat_id,
                "p_source_message_id": message_id,
                "p_draft": draft.model_dump(mode="json"),
            },
        ).execute()
        return GroupEvent.model_validate(result.data[0])

    async def set_group_message(self, group_event_id: str, message_id: int):
        await (
            self.client.table("group_events")
            .update({"message_id": message_id})
            .eq("id", group_event_id)
            .execute()
        )

    async def get_group_event(self, group_event_id: str) -> GroupEvent | None:
        result = (
            await self.client.table("group_events")
            .select("*")
            .eq("id", group_event_id)
            .limit(1)
            .execute()
        )
        return GroupEvent.model_validate(result.data[0]) if result.data else None

    async def touch_member(self, chat_id: int, user_id: str):
        await self.client.rpc(
            "amifree_touch_member", {"p_chat_id": chat_id, "p_user_id": user_id}
        ).execute()

    async def linked_event(self, user_id: str, group_event_id: str) -> Event | None:
        result = (
            await self.client.table("events")
            .select("*")
            .eq("user_id", user_id)
            .eq("group_event_id", group_event_id)
            .limit(1)
            .execute()
        )
        return Event.model_validate(result.data[0]) if result.data else None

    async def group_rsvp(self, user_id: str, event: GroupEvent, status: Status) -> Event | None:
        result = await self.client.rpc(
            "amifree_group_rsvp",
            {"p_user_id": user_id, "p_group_event_id": event.id, "p_status": status.value},
        ).execute()
        return Event.model_validate(result.data[0]) if result.data else None

    async def group_counts(self, group_event_id: str) -> dict[str, int]:
        result = await self.client.rpc(
            "amifree_group_counts", {"p_group_event_id": group_event_id}
        ).execute()
        return {row["status"]: row["n"] for row in result.data or []}

    async def update_group_event(
        self, user_id: str, event: GroupEvent, draft: EventDraft | None, cancel=False
    ) -> GroupEvent | None:
        result = await self.client.rpc(
            "amifree_update_group_event",
            {
                "p_user_id": user_id,
                "p_group_event_id": event.id,
                "p_draft": draft.model_dump(mode="json") if draft else {},
                "p_version": event.version,
                "p_cancel": cancel,
            },
        ).execute()
        return GroupEvent.model_validate(result.data[0]) if result.data else None

    async def get_user(self, user_id: str) -> User | None:
        result = await self.client.table("users").select("*").eq("id", user_id).limit(1).execute()
        return User.model_validate(result.data[0]) if result.data else None

    async def set_user_flags(self, user_id: str, **flags: bool):
        await self.client.table("users").update(flags).eq("id", user_id).execute()

    async def enqueue_notification(self, user_id: str, kind: str, key: str, payload: dict) -> bool:
        result = await self.client.rpc(
            "amifree_enqueue_notification",
            {"p_user_id": user_id, "p_kind": kind, "p_key": key, "p_payload": payload},
        ).execute()
        return bool(result.data)

    async def claim_notifications(self, limit: int = 50) -> list[dict]:
        result = await self.client.rpc("amifree_claim_notifications", {"p_limit": limit}).execute()
        return result.data or []

    async def finish_notification(self, notification_id: str, ok: bool, permanent: bool):
        await self.client.rpc(
            "amifree_finish_notification",
            {"p_id": notification_id, "p_ok": ok, "p_permanent": permanent},
        ).execute()

    async def create_poll(
        self,
        user_id: str,
        chat_id: int,
        message_id: int,
        title: str,
        minutes: int,
        start: datetime,
        end: datetime,
    ) -> Poll:
        result = await self.client.rpc(
            "amifree_create_poll",
            {
                "p_user_id": user_id,
                "p_chat_id": chat_id,
                "p_source_message_id": message_id,
                "p_title": title,
                "p_duration": minutes,
                "p_window_start": start.isoformat(),
                "p_window_end": end.isoformat(),
            },
        ).execute()
        return Poll.model_validate(result.data[0])

    async def set_poll_message(self, poll_id: str, message_id: int):
        await (
            self.client.table("polls")
            .update({"message_id": message_id})
            .eq("id", poll_id)
            .execute()
        )

    async def get_poll(self, poll_id: str) -> Poll | None:
        result = await self.client.table("polls").select("*").eq("id", poll_id).limit(1).execute()
        return Poll.model_validate(result.data[0]) if result.data else None

    async def poll_join(self, poll_id: str, user_id: str) -> int | None:
        result = await self.client.rpc(
            "amifree_poll_join", {"p_poll_id": poll_id, "p_user_id": user_id}
        ).execute()
        # A closed or missing poll returns SQL null, which the client surfaces as empty data.
        return result.data if isinstance(result.data, int) else None

    async def poll_participants(self, poll_id: str) -> list[str]:
        result = (
            await self.client.table("poll_participants")
            .select("*")
            .eq("poll_id", poll_id)
            .order("user_id")
            .limit(1000)
            .execute()
        )
        return [row["user_id"] for row in result.data]

    async def poll_open(self, poll_id: str, user_id: str, slots: list[dict]) -> Poll | None:
        result = await self.client.rpc(
            "amifree_poll_open", {"p_poll_id": poll_id, "p_user_id": user_id, "p_slots": slots}
        ).execute()
        return Poll.model_validate(result.data[0]) if result.data else None

    async def poll_close(self, poll_id: str, user_id: str) -> Poll | None:
        result = await self.client.rpc(
            "amifree_poll_close", {"p_poll_id": poll_id, "p_user_id": user_id}
        ).execute()
        return Poll.model_validate(result.data[0]) if result.data else None

    async def poll_vote(self, poll_id: str, user_id: str, votes: dict[str, str]) -> bool:
        result = await self.client.rpc(
            "amifree_poll_vote", {"p_poll_id": poll_id, "p_user_id": user_id, "p_votes": votes}
        ).execute()
        return bool(result.data)

    async def poll_tally(self, poll_id: str) -> dict[tuple[int, str], int]:
        result = await self.client.rpc("amifree_poll_tally", {"p_poll_id": poll_id}).execute()
        return {(row["slot"], row["answer"]): row["n"] for row in result.data or []}

    async def _column(self, table: str, column: str, **filters) -> list[str]:
        q = self.client.table(table).select("*")
        for key, value in filters.items():
            q = q.eq(key, value)
        return [row[column] for row in (await q.limit(1000).execute()).data]

    async def group_member_ids(self, chat_id: int) -> list[str]:
        return await self._column("group_members", "user_id", chat_id=chat_id)

    async def group_responder_ids(self, group_event_id: str) -> list[str]:
        return await self._column("events", "user_id", group_event_id=group_event_id)

    async def poll_voter_ids(self, poll_id: str) -> list[str]:
        return sorted(set(await self._column("poll_votes", "user_id", poll_id=poll_id)))

    async def cards_to_chase(
        self, created_before: datetime, now: datetime
    ) -> tuple[list[GroupEvent], list[Poll]]:
        """Cards at least a day old that are still open. Old cards age out after two weeks."""
        oldest = (created_before - timedelta(days=14)).isoformat()
        events = (
            await self.client.table("group_events")
            .select("*")
            .eq("status", "ACTIVE")
            .lte("created_at", created_before.isoformat())
            .gte("created_at", oldest)
            .gt("start_at", now.isoformat())
            .limit(200)
            .execute()
        ).data
        polls = (
            await self.client.table("polls")
            .select("*")
            .neq("status", "CLOSED")
            .lte("created_at", created_before.isoformat())
            .gte("created_at", oldest)
            .gt("window_end", now.isoformat())
            .limit(200)
            .execute()
        ).data
        return [GroupEvent.model_validate(e) for e in events], [
            Poll.model_validate(p) for p in polls
        ]

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
