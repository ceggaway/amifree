"""All I/O is async and server-side; every event operation requires its owner."""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta

from supabase import AsyncClient

from app.models.timetable import BlockRule, BusyBlock
from app.models.user import GoogleConnection, PersonalCalendar, User
from app.schemas.event import (
    SG,
    Deadline,
    DeadlineDraft,
    Event,
    EventDraft,
    EventSeries,
    GroupEvent,
    Poll,
    Reminder,
    Status,
    Suggestion,
)
from app.services.nusmods import ImportResult
from app.services.quick_blocks import expand_blocks

MAX_CALENDAR_BLOCKS = 3000
# The newest migration this code needs; each migration from 008 on records itself.
SCHEMA_VERSION = 16


def calendar_block(row: dict, titles: dict[str, str]) -> BusyBlock:
    """A personal-calendar busy time, named after its calendar: never after an event."""
    return BusyBlock(
        title=titles.get(row.get("calendar_id"), "Personal calendar"),
        start_at=row["start_at"],
        end_at=row["end_at"],
        source="CALENDAR",
    )


log = logging.getLogger(__name__)
PAGE = 1000  # PostgREST caps one response at this many rows
MAX_PAGES = 50


class SupabaseRepository:
    def __init__(self, client: AsyncClient, pending_ttl_hours: int = 24):
        self.client = client
        self.pending_ttl_hours = pending_ttl_hours

    async def ping(self):
        # Check everything the current code needs (tables, columns and the calendar function),
        # not merely that the host responds. A missing migration fails here, by name, in the log.
        required = {
            "users": "id,calendar_feed_token,calendar_feed_seen_at,google_connected",
            "timetable_blocks": "id",
            "events": "id",
            "event_extractions": "id",
            "user_actions": "id",
            "manual_busy_blocks": "id",
            "notification_logs": "id",
            "polls": "id,deadline_at",
            "group_members": "chat_id,user_id,display_name,username",
            "group_events": "id,series_id,occurrence_date,edited_alone,ended_at",
            "event_series": "id",
            "attendance": "group_event_id",
            "attendance_checks": "group_event_id",
            "group_plans": "chat_id",
            "group_settings": "chat_id",
            "event_suggestions": "id,author_telegram_id",
            "poll_availability": "poll_id",
            "pro_payments": "charge_id,refunded_at",
            "weekly_availability": "user_id,weekday,start_min,end_min",
            "personal_calendars": "id,user_id,url_hash,label,synced_at,checked_at,error",
            "calendar_busy_blocks": "id,user_id,calendar_id,start_at,end_at",
            "google_connections": "user_id,calendar_email,calendar_id,synced_at,error",
            "google_event_links": "user_id,event_id,synced_version",
            "schema_migrations": "version,name",
            "processed_updates": "update_id,status,claimed_at",
            "feedback": "id,message,status,created_at",
        }

        async def has_table(table, columns):
            try:
                await self.client.table(table).select(columns).limit(1).execute()
            except Exception:
                return table
            return None

        async def has_function():
            # A nil calendar id owns nothing, so this probes that migration 007's function
            # exists and is callable without changing any data.
            try:
                await self.client.rpc(
                    "amifree_replace_calendar",
                    {
                        "p_calendar_id": "00000000-0000-0000-0000-000000000000",
                        "p_blocks": [],
                        "p_synced_at": datetime.now(UTC).isoformat(),
                    },
                ).execute()
            except Exception:
                return "function amifree_replace_calendar"
            return None

        async def has_version():
            try:
                rows = (
                    await self.client.table("schema_migrations")
                    .select("version")
                    .order("version", desc=True)
                    .limit(1)
                    .execute()
                ).data
            except Exception:
                return None  # reported as the missing schema_migrations table
            if not rows or rows[0]["version"] < SCHEMA_VERSION:
                return f"schema version {SCHEMA_VERSION}"
            return None

        # Together, so a readiness probe costs one round trip's time, not fifteen.
        checks = [has_table(t, c) for t, c in required.items()] + [has_function(), has_version()]
        missing = [found for found in await asyncio.gather(*checks) if found]
        if len(missing) >= len(required):
            log.error("database_unreachable")
            raise RuntimeError("Database is unreachable")
        if missing:
            log.error("database_schema_incomplete missing=%s", ", ".join(missing))
            raise RuntimeError("Database schema is out of date")

    @staticmethod
    async def _all(build) -> list[dict]:
        """Every row of a query. `build()` returns a fresh, ordered query without a limit.

        Rows come back in pages, so a group with more than 1,000 people is not silently cut."""
        rows: list[dict] = []
        for page in range(MAX_PAGES):
            query = build().range(page * PAGE, page * PAGE + PAGE - 1)
            batch = (await query.execute()).data
            rows += batch
            if len(batch) < PAGE:
                return rows
        log.warning("query_truncated rows=%s", len(rows))
        return rows

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
        calendar = await fetch("calendar_busy_blocks")
        manual = expand_blocks(await self.list_blocks(user_id), draft.start_at, draft.end_at)
        titles = (
            {c.id: c.busy_title for c in await self.list_calendars(user_id)} if calendar else {}
        )
        personal = [calendar_block(b, titles) for b in calendar]
        return [BusyBlock.model_validate(b) for b in blocks] + manual + personal, [
            Event.model_validate(e) for e in events
        ]

    async def blocks_between(self, user_id: str, start: datetime, end: datetime) -> list[BusyBlock]:
        """Classes, exams and manual blocks overlapping [start, end), for calendar views."""
        rows, offset = [], 0
        while True:
            batch = (
                await self.client.table("timetable_blocks")
                .select("*")
                .eq("user_id", user_id)
                .lt("start_at", end.isoformat())
                .gt("end_at", start.isoformat())
                .order("id")
                .range(offset, offset + 499)
                .execute()
            ).data
            rows.extend(batch)
            if len(batch) < 500:
                break
            offset += 500
        manual = expand_blocks(await self.list_blocks(user_id), start, end)
        calendar = (
            await self.client.table("calendar_busy_blocks")
            .select("*")
            .eq("user_id", user_id)
            .lt("start_at", end.isoformat())
            .gt("end_at", start.isoformat())
            .order("id")
            .limit(MAX_CALENDAR_BLOCKS)
            .execute()
        ).data
        titles = (
            {c.id: c.busy_title for c in await self.list_calendars(user_id)} if calendar else {}
        )
        return (
            [BusyBlock.model_validate(b) for b in rows]
            + manual
            + [calendar_block(b, titles) for b in calendar]
        )

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

    # ---- reminders ----

    async def create_feedback(self, message: str, username: str | None, name: str | None) -> None:
        row: dict = {"message": message[:2000]}
        if username:
            row["username"] = username[:64]
        if name:
            row["name"] = name[:128]
        await self.client.table("feedback").insert(row).execute()

    async def create_reminder(self, user_id: str, text: str, remind_at: datetime) -> Reminder:
        row = {"user_id": user_id, "text": text, "remind_at": remind_at.isoformat()}
        result = await self.client.table("reminders").insert(row).execute()
        return Reminder.model_validate(result.data[0])

    async def get_reminder(self, user_id: str, reminder_id: str) -> Reminder | None:
        rows = (
            await self.client.table("reminders")
            .select("*")
            .eq("user_id", user_id)
            .eq("id", reminder_id)
            .limit(1)
            .execute()
        ).data
        return Reminder.model_validate(rows[0]) if rows else None

    async def reminders_between(
        self, start: datetime, end: datetime, user_id: str | None = None
    ) -> list[Reminder]:
        """Open (not done) reminders ringing in [start, end), soonest first."""
        query = (
            self.client.table("reminders")
            .select("*")
            .eq("done", False)
            .gte("remind_at", start.isoformat())
            .lt("remind_at", end.isoformat())
        )
        if user_id is not None:
            query = query.eq("user_id", user_id)
        rows = (await query.order("remind_at").limit(500).execute()).data
        return [Reminder.model_validate(row) for row in rows]

    async def finish_reminder(self, user_id: str, reminder_id: str) -> bool:
        result = (
            await self.client.table("reminders")
            .update({"done": True})
            .eq("user_id", user_id)
            .eq("id", reminder_id)
            .execute()
        )
        return bool(result.data)

    async def snooze_reminder(self, user_id: str, reminder_id: str, remind_at: datetime) -> bool:
        result = (
            await self.client.table("reminders")
            .update({"remind_at": remind_at.isoformat(), "done": False})
            .eq("user_id", user_id)
            .eq("id", reminder_id)
            .execute()
        )
        return bool(result.data)

    async def delete_reminder(self, user_id: str, reminder_id: str) -> bool:
        result = (
            await self.client.table("reminders")
            .delete()
            .eq("user_id", user_id)
            .eq("id", reminder_id)
            .execute()
        )
        return bool(result.data)

    # ---- deadlines ----

    async def create_deadline(
        self, user_id: str, message_id: int | None, draft: DeadlineDraft
    ) -> Deadline:
        if message_id is not None:
            # The same forwarded message twice is one deadline.
            found = (
                await self.client.table("deadlines")
                .select("*")
                .eq("user_id", user_id)
                .eq("source_message_id", message_id)
                .limit(1)
                .execute()
            ).data
            if found:
                return Deadline.model_validate(found[0])
        row = draft.model_dump(mode="json") | {
            "user_id": user_id,
            "source_message_id": message_id,
        }
        result = await self.client.table("deadlines").insert(row).execute()
        return Deadline.model_validate(result.data[0])

    async def get_deadline(self, user_id: str, deadline_id: str) -> Deadline | None:
        rows = (
            await self.client.table("deadlines")
            .select("*")
            .eq("user_id", user_id)
            .eq("id", deadline_id)
            .limit(1)
            .execute()
        ).data
        return Deadline.model_validate(rows[0]) if rows else None

    async def deadlines_between(
        self, start: datetime, end: datetime, user_id: str | None = None
    ) -> list[Deadline]:
        """Open (not done) deadlines due in [start, end), soonest first."""
        query = (
            self.client.table("deadlines")
            .select("*")
            .eq("done", False)
            .gte("due_at", start.isoformat())
            .lt("due_at", end.isoformat())
        )
        if user_id is not None:
            query = query.eq("user_id", user_id)
        rows = (await query.order("due_at").limit(500).execute()).data
        return [Deadline.model_validate(row) for row in rows]

    async def finish_deadline(self, user_id: str, deadline_id: str) -> bool:
        result = (
            await self.client.table("deadlines")
            .update({"done": True})
            .eq("user_id", user_id)
            .eq("id", deadline_id)
            .execute()
        )
        return bool(result.data)

    async def delete_deadline(self, user_id: str, deadline_id: str) -> bool:
        result = (
            await self.client.table("deadlines")
            .delete()
            .eq("user_id", user_id)
            .eq("id", deadline_id)
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

    async def set_group_message(
        self, group_event_id: str, message_id: int, posted_at: datetime | None = None
    ):
        changes = {"message_id": message_id}
        if posted_at:
            # A series session counts as new when its card appears, for the chase timing.
            changes["created_at"] = posted_at.isoformat()
        await self.client.table("group_events").update(changes).eq("id", group_event_id).execute()

    async def get_group_event(self, group_event_id: str) -> GroupEvent | None:
        result = (
            await self.client.table("group_events")
            .select("*")
            .eq("id", group_event_id)
            .limit(1)
            .execute()
        )
        return GroupEvent.model_validate(result.data[0]) if result.data else None

    async def touch_member(
        self, chat_id: int, user_id: str, name: str | None = None, username: str | None = None
    ):
        await self.client.rpc(
            "amifree_touch_member", {"p_chat_id": chat_id, "p_user_id": user_id}
        ).execute()
        if name or username:
            await (
                self.client.table("group_members")
                .update({"display_name": name, "username": username})
                .eq("chat_id", chat_id)
                .eq("user_id", user_id)
                .execute()
            )

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

    async def list_calendars(self, user_id: str) -> list[PersonalCalendar]:
        rows = (
            await self.client.table("personal_calendars")
            .select("*")
            .eq("user_id", user_id)
            .order("id")
            .execute()
        ).data
        calendars = [PersonalCalendar.model_validate(r) for r in rows]
        return sorted(calendars, key=lambda c: c.label.lower())

    async def add_calendar(
        self, user_id: str, sealed: str, url_hash: str, label: str
    ) -> tuple[PersonalCalendar, bool]:
        """Add a calendar, or refresh the stored copy of one already added. (calendar, new)."""
        existing = (
            await self.client.table("personal_calendars")
            .select("*")
            .eq("user_id", user_id)
            .eq("url_hash", url_hash)
            .limit(1)
            .execute()
        ).data
        if existing:
            row = (
                await self.client.table("personal_calendars")
                .update({"sealed_url": sealed, "label": label, "error": None})
                .eq("id", existing[0]["id"])
                .execute()
            ).data[0]
            return PersonalCalendar.model_validate(row), False
        row = (
            await self.client.table("personal_calendars")
            .insert(
                {"user_id": user_id, "sealed_url": sealed, "url_hash": url_hash, "label": label}
            )
            .execute()
        ).data[0]
        return PersonalCalendar.model_validate(row), True

    async def remove_calendar(self, user_id: str, calendar_id: str) -> bool:
        # Scoped to the owner; busy times go with it (on delete cascade).
        rows = (
            await self.client.table("personal_calendars")
            .delete()
            .eq("id", calendar_id)
            .eq("user_id", user_id)
            .execute()
        ).data
        return bool(rows)

    async def replace_calendar(self, calendar_id: str, blocks, synced_at: datetime):
        await self.client.rpc(
            "amifree_replace_calendar",
            {
                "p_calendar_id": calendar_id,
                "p_blocks": [
                    {"start_at": a.isoformat(), "end_at": b.isoformat()} for a, b in blocks
                ],
                "p_synced_at": synced_at.isoformat(),
            },
        ).execute()

    async def calendar_error(self, calendar_id: str, error: str, checked_at: datetime):
        await (
            self.client.table("personal_calendars")
            .update({"error": error[:300], "checked_at": checked_at.isoformat()})
            .eq("id", calendar_id)
            .execute()
        )

    async def calendars_due(self, before: datetime, limit: int = 20) -> list[PersonalCalendar]:
        # Never-checked calendars first, then the stalest; filtered and bounded in the database.
        cutoff = before.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = (
            await self.client.table("personal_calendars")
            .select("*")
            .or_(f"checked_at.is.null,checked_at.lt.{cutoff}")
            .order("checked_at", desc=False, nullsfirst=True)
            .limit(limit)
            .execute()
        ).data
        return [PersonalCalendar.model_validate(r) for r in rows]

    # ---- Google Calendar sharing ----

    async def get_google_connection(self, user_id: str) -> GoogleConnection | None:
        rows = (
            await self.client.table("google_connections")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ).data
        return GoogleConnection.model_validate(rows[0]) if rows else None

    async def google_calendar_owner(self, calendar_email: str) -> str | None:
        """The user already connected to this Google calendar, if any."""
        rows = (
            await self.client.table("google_connections")
            .select("user_id")
            .eq("calendar_email", calendar_email)
            .limit(1)
            .execute()
        ).data
        return rows[0]["user_id"] if rows else None

    async def save_google_connection(
        self, user_id: str, calendar_email: str, calendar_id: str
    ) -> GoogleConnection:
        """Connect, or reconnect to a different calendar. Reconnecting forgets which plans
        were already added, so the new calendar gets them all."""
        await self.client.table("google_connections").delete().eq("user_id", user_id).execute()
        row = (
            await self.client.table("google_connections")
            .insert(
                {"user_id": user_id, "calendar_email": calendar_email, "calendar_id": calendar_id}
            )
            .execute()
        ).data[0]
        await self.set_user_flags(user_id, google_connected=True)
        return GoogleConnection.model_validate(row)

    async def remove_google_connection(self, user_id: str):
        await self.client.table("google_connections").delete().eq("user_id", user_id).execute()
        await self.set_user_flags(user_id, google_connected=False)

    async def google_connections_due(
        self, before: datetime, limit: int = 20
    ) -> list[GoogleConnection]:
        cutoff = before.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = (
            await self.client.table("google_connections")
            .select("*")
            .or_(f"synced_at.is.null,synced_at.lt.{cutoff}")
            .order("synced_at", desc=False, nullsfirst=True)
            .limit(limit)
            .execute()
        ).data
        return [GoogleConnection.model_validate(r) for r in rows]

    async def mark_google_synced(self, user_id: str, when: datetime, error: str | None = None):
        await (
            self.client.table("google_connections")
            .update({"synced_at": when.isoformat(), "error": error[:300] if error else None})
            .eq("user_id", user_id)
            .execute()
        )

    async def google_links(self, user_id: str) -> dict[str, int]:
        rows = (
            await self.client.table("google_event_links")
            .select("event_id,synced_version")
            .eq("user_id", user_id)
            .execute()
        ).data
        return {r["event_id"]: r["synced_version"] for r in rows}

    async def save_google_link(self, user_id: str, event_id: str, version: int):
        await (
            self.client.table("google_event_links")
            .upsert(
                {"user_id": user_id, "event_id": event_id, "synced_version": version},
                on_conflict="user_id,event_id",
            )
            .execute()
        )

    async def delete_google_link(self, user_id: str, event_id: str):
        await (
            self.client.table("google_event_links")
            .delete()
            .eq("user_id", user_id)
            .eq("event_id", event_id)
            .execute()
        )

    async def set_feed_token(self, user_id: str, token: str | None):
        # A new (or no) link always resets "seen": the old subscription no longer works.
        await (
            self.client.table("users")
            .update({"calendar_feed_token": token, "calendar_feed_seen_at": None})
            .eq("id", user_id)
            .execute()
        )

    async def user_by_feed_token(self, token: str) -> User | None:
        result = await (
            self.client.table("users")
            .select("*")
            .eq("calendar_feed_token", token)
            .limit(1)
            .execute()
        )
        return User.model_validate(result.data[0]) if result.data else None

    async def mark_feed_seen(self, user_id: str, when: datetime):
        await (
            self.client.table("users")
            .update({"calendar_feed_seen_at": when.isoformat()})
            .eq("id", user_id)
            .execute()
        )

    async def enqueue_notification(self, user_id: str, kind: str, key: str, payload: dict) -> bool:
        result = await self.client.rpc(
            "amifree_enqueue_notification",
            {"p_user_id": user_id, "p_kind": kind, "p_key": key, "p_payload": payload},
        ).execute()
        return bool(result.data)

    async def claim_notifications(self, limit: int = 50) -> list[dict]:
        result = await self.client.rpc("amifree_claim_notifications", {"p_limit": limit}).execute()
        return result.data or []

    async def claim_update(self, update_id: int) -> bool:
        """True if this caller should process the Telegram update (see migration 008)."""
        result = await self.client.rpc("amifree_claim_update", {"p_update_id": update_id}).execute()
        return bool(result.data)

    async def finish_update(self, update_id: int):
        await self.client.rpc("amifree_finish_update", {"p_update_id": update_id}).execute()

    async def release_update(self, update_id: int):
        await self.client.rpc("amifree_release_update", {"p_update_id": update_id}).execute()

    async def notification_stats(self, now: datetime) -> dict:
        """Backlog counts and the age of the oldest undelivered message, for monitoring."""
        rows = (
            await self.client.table("notification_logs")
            .select("status,created_at")
            .in_("status", ["pending", "sending", "failed"])
            .limit(5000)
            .execute()
        ).data
        counts = {"pending": 0, "sending": 0, "failed": 0}
        oldest = None
        for row in rows:
            counts[row["status"]] += 1
            if row["status"] == "failed":
                continue
            created = datetime.fromisoformat(row["created_at"])
            oldest = created if oldest is None else min(oldest, created)
        age = max(0, int((now - oldest).total_seconds())) if oldest else None
        return counts | {"oldest_undelivered_seconds": age}

    async def drop_pending_notifications(self, kind: str, keys: list[str]) -> list[str]:
        """Remove queued, unsent messages; returns whose they were."""
        dropped = []
        for start in range(0, len(keys), 50):
            rows = (
                await self.client.table("notification_logs")
                .delete()
                .eq("kind", kind)
                .eq("status", "pending")
                .in_("dedupe_key", keys[start : start + 50])
                .execute()
            ).data
            dropped += [r["user_id"] for r in rows]
        return dropped

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
        deadline: datetime | None = None,
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
        poll = Poll.model_validate(result.data[0])
        if deadline and poll.deadline_at is None:
            await (
                self.client.table("polls")
                .update({"deadline_at": deadline.isoformat()})
                .eq("id", poll.id)
                .execute()
            )
            poll.deadline_at = deadline
        return poll

    async def polls_past_deadline(self, now: datetime) -> list[Poll]:
        rows = await self._all(
            lambda: (
                self.client.table("polls")
                .select("*")
                .eq("status", "COLLECTING")
                .lte("deadline_at", now.isoformat())
                .gt("window_end", now.isoformat())
                .order("id")
            )
        )
        return [Poll.model_validate(r) for r in rows]

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
        rows = await self._all(
            lambda: (
                self.client.table("poll_participants")
                .select("*")
                .eq("poll_id", poll_id)
                .order("user_id")
            )
        )
        return [row["user_id"] for row in rows]

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

    async def poll_my_votes(self, poll_id: str, user_id: str) -> dict[int, str]:
        """One member's own answers, slot -> FREE/MAYBE/BUSY."""
        rows = (
            await self.client.table("poll_votes")
            .select("slot,answer")
            .eq("poll_id", poll_id)
            .eq("user_id", user_id)
            .execute()
        ).data
        return {row["slot"]: row["answer"] for row in rows or []}

    async def poll_tally(self, poll_id: str) -> dict[tuple[int, str], int]:
        result = await self.client.rpc("amifree_poll_tally", {"p_poll_id": poll_id}).execute()
        return {(row["slot"], row["answer"]): row["n"] for row in result.data or []}

    async def _column(self, table: str, column: str, **filters) -> list[str]:
        def build():
            q = self.client.table(table).select("*")
            for key, value in filters.items():
                q = q.eq(key, value)
            return q.order(column)

        return [row[column] for row in await self._all(build)]

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
        events = await self._all(
            lambda: (
                self.client.table("group_events")
                .select("*")
                .eq("status", "ACTIVE")
                # Only cards in the group: unposted series sessions are not chased.
                .gt("message_id", 0)
                .lte("created_at", created_before.isoformat())
                .gte("created_at", oldest)
                .gt("start_at", now.isoformat())
                .order("id")
            )
        )
        polls = await self._all(
            lambda: (
                self.client.table("polls")
                .select("*")
                .neq("status", "CLOSED")
                .lte("created_at", created_before.isoformat())
                .gte("created_at", oldest)
                .gt("window_end", now.isoformat())
                .order("id")
            )
        )
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

    # ---- group features: names, series, attendance, plans, detection ----

    async def member_names(self, chat_id: int) -> dict[str, tuple[str | None, str | None]]:
        rows = await self._all(
            lambda: (
                self.client.table("group_members")
                .select("user_id,display_name,username")
                .eq("chat_id", chat_id)
                .order("user_id")
            )
        )
        return {r["user_id"]: (r.get("display_name"), r.get("username")) for r in rows}

    async def group_answers(self, group_event_id: str) -> dict[str, str]:
        """Each responder's answer on a card: user id -> GOING, MAYBE, DECLINED or SAVED."""
        rows = await self._all(
            lambda: (
                self.client.table("events")
                .select("user_id,status")
                .eq("group_event_id", group_event_id)
                .order("user_id")
            )
        )
        return {r["user_id"]: r["status"] for r in rows}

    async def group_events_starting(self, start: datetime, end: datetime) -> list[GroupEvent]:
        rows = await self._all(
            lambda: (
                self.client.table("group_events")
                .select("*")
                .eq("status", "ACTIVE")
                .gte("start_at", start.isoformat())
                .lt("start_at", end.isoformat())
                .order("start_at", desc=False)
                .order("id")
            )
        )
        return [GroupEvent.model_validate(r) for r in rows]

    async def ended_group_events(self, now: datetime) -> list[GroupEvent]:
        """Cards that finished in the last week and were not yet redrawn as ended."""
        rows = await self._all(
            lambda: (
                self.client.table("group_events")
                .select("*")
                .eq("status", "ACTIVE")
                .lte("end_at", now.isoformat())
                .gte("end_at", (now - timedelta(days=7)).isoformat())
                .order("id")
            )
        )
        found = [GroupEvent.model_validate(r) for r in rows]
        return [g for g in found if g.ended_at is None and (g.message_id or 0) > 0]

    async def mark_ended(self, group_event_id: str, when: datetime):
        await (
            self.client.table("group_events")
            .update({"ended_at": when.isoformat()})
            .eq("id", group_event_id)
            .execute()
        )

    async def set_attendance_check(self, group_event_id: str, user_id: str, answer: str):
        await (
            self.client.table("attendance_checks")
            .upsert(
                {"group_event_id": group_event_id, "user_id": user_id, "answer": answer},
                on_conflict="group_event_id,user_id",
            )
            .execute()
        )

    async def attendance_checks(self, group_event_id: str) -> dict[str, str]:
        rows = await self._all(
            lambda: (
                self.client.table("attendance_checks")
                .select("user_id,answer")
                .eq("group_event_id", group_event_id)
                .order("user_id")
            )
        )
        return {r["user_id"]: r["answer"] for r in rows}

    async def check_in(self, group_event_id: str, user_id: str, marked_by: str):
        await self.mark_attendance(group_event_id, user_id, True, marked_by)

    async def mark_attendance(
        self, group_event_id: str, user_id: str, present: bool, marked_by: str
    ):
        await (
            self.client.table("attendance")
            .upsert(
                {
                    "group_event_id": group_event_id,
                    "user_id": user_id,
                    "present": present,
                    "marked_by": marked_by,
                    "marked_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="group_event_id,user_id",
            )
            .execute()
        )

    async def attendees(self, group_event_id: str) -> set[str]:
        rows = await self._all(
            lambda: (
                self.client.table("attendance")
                .select("user_id,present")
                .eq("group_event_id", group_event_id)
                .order("user_id")
            )
        )
        return {r["user_id"] for r in rows if r["present"]}

    async def create_series(
        self,
        user_id: str,
        chat_id: int,
        message_id: int,
        fields: dict,
    ) -> EventSeries:
        existing = (
            await self.client.table("event_series")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("source_message_id", message_id)
            .limit(1)
            .execute()
        ).data
        if existing:
            return EventSeries.model_validate(existing[0])
        row = (
            await self.client.table("event_series")
            .insert(
                {
                    "chat_id": chat_id,
                    "source_message_id": message_id,
                    "creator_user_id": user_id,
                    **fields,
                }
            )
            .execute()
        ).data[0]
        await self.touch_member(chat_id, user_id)
        return EventSeries.model_validate(row)

    async def get_series(self, series_id: str) -> EventSeries | None:
        rows = (
            await self.client.table("event_series")
            .select("*")
            .eq("id", series_id)
            .limit(1)
            .execute()
        ).data
        return EventSeries.model_validate(rows[0]) if rows else None

    async def update_series(self, series_id: str, **fields):
        await self.client.table("event_series").update(fields).eq("id", series_id).execute()

    async def series_sessions(self, series_id: str) -> list[GroupEvent]:
        rows = (
            await self.client.table("group_events")
            .select("*")
            .eq("series_id", series_id)
            .order("occurrence_date", desc=False)
            .limit(200)
            .execute()
        ).data
        return [GroupEvent.model_validate(r) for r in rows]

    async def create_session(
        self,
        series: EventSeries,
        day,
        draft: EventDraft,
        source_message_id: int | None = None,
    ) -> GroupEvent:
        """One dated session of a series; repeating the call returns the same row."""
        existing = (
            await self.client.table("group_events")
            .select("*")
            .eq("series_id", series.id)
            .eq("occurrence_date", day.isoformat())
            .limit(1)
            .execute()
        ).data
        if existing:
            return GroupEvent.model_validate(existing[0])
        row = {
            "chat_id": series.chat_id,
            "source_message_id": source_message_id,
            "creator_user_id": series.creator_user_id,
            "title": draft.title,
            "start_at": draft.start_at.isoformat(),
            "end_at": draft.end_at.isoformat(),
            "location": draft.location,
            "notes": draft.notes,
            "assumed_duration": draft.assumed_duration,
            "series_id": series.id,
            "occurrence_date": day.isoformat(),
        }
        created = (await self.client.table("group_events").insert(row).execute()).data[0]
        return GroupEvent.model_validate(created)

    async def clear_edited(self, group_event_ids: list[str]):
        # A series has at most MAX_SESSIONS sessions, so one request covers them all.
        if group_event_ids:
            await (
                self.client.table("group_events")
                .update({"edited_alone": False})
                .in_("id", group_event_ids)
                .execute()
            )

    async def unposted_sessions(self, start: datetime, end: datetime) -> list[GroupEvent]:
        found = await self.group_events_starting(start, end)
        return [g for g in found if g.series_id and not g.message_id]

    async def chat_events_by(
        self, chat_id: int, creator_user_id: str, since: datetime
    ) -> list[GroupEvent]:
        rows = await self._all(
            lambda: (
                self.client.table("group_events")
                .select("*")
                .eq("chat_id", chat_id)
                .eq("creator_user_id", creator_user_id)
                .gte("start_at", since.isoformat())
                .order("start_at", desc=False)
                .order("id")
            )
        )
        return [GroupEvent.model_validate(r) for r in rows]

    async def chat_events(
        self, chat_id: int, ends_after: datetime, limit: int = 200
    ) -> list[GroupEvent]:
        """A group's active cards that have not finished, soonest first."""
        rows = (
            await self.client.table("group_events")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("status", "ACTIVE")
            .gt("end_at", ends_after.isoformat())
            .order("start_at", desc=False)
            .order("id")
            .limit(limit)
            .execute()
        ).data
        return [GroupEvent.model_validate(r) for r in rows]

    async def chat_polls(self, chat_id: int, now: datetime) -> list[Poll]:
        """A group's polls that are still open and whose window has not passed."""
        rows = (
            await self.client.table("polls")
            .select("*")
            .eq("chat_id", chat_id)
            .neq("status", "CLOSED")
            .gt("window_end", now.isoformat())
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        ).data
        return [Poll.model_validate(r) for r in rows]

    async def group_tier(self, chat_id: int, now: datetime) -> str:
        rows = (
            await self.client.table("group_plans")
            .select("*")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        ).data
        if not rows or rows[0]["tier"] != "PRO":
            return "FREE"
        expires = rows[0].get("expires_at")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now:
            return "FREE"
        return "PRO"

    async def set_group_tier(self, chat_id: int, tier: str, expires_at: datetime | None = None):
        await (
            self.client.table("group_plans")
            .upsert(
                {
                    "chat_id": chat_id,
                    "tier": tier,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="chat_id",
            )
            .execute()
        )

    async def detection_enabled(self, chat_id: int) -> bool:
        rows = (
            await self.client.table("group_settings")
            .select("detection_enabled")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        ).data
        return bool(rows and rows[0]["detection_enabled"])

    async def set_detection(self, chat_id: int, enabled: bool, telegram_id: int):
        await (
            self.client.table("group_settings")
            .upsert(
                {
                    "chat_id": chat_id,
                    "detection_enabled": enabled,
                    "enabled_by_telegram_id": telegram_id if enabled else None,
                    "enabled_at": datetime.now(UTC).isoformat() if enabled else None,
                },
                on_conflict="chat_id",
            )
            .execute()
        )

    async def create_suggestion(
        self,
        chat_id: int,
        source_message_id: int,
        draft: EventDraft,
        author_telegram_id: int | None = None,
    ) -> Suggestion | None:
        """None when this message already has a suggestion (a retried delivery)."""
        found = (
            await self.client.table("event_suggestions")
            .select("id")
            .eq("chat_id", chat_id)
            .eq("source_message_id", source_message_id)
            .limit(1)
            .execute()
        ).data
        if found:
            return None
        row = (
            await self.client.table("event_suggestions")
            .insert(
                {
                    "chat_id": chat_id,
                    "source_message_id": source_message_id,
                    "draft": draft.model_dump(mode="json"),
                    "author_telegram_id": author_telegram_id,
                }
            )
            .execute()
        ).data[0]
        return Suggestion.model_validate(row)

    async def get_suggestion(self, suggestion_id: str) -> Suggestion | None:
        rows = (
            await self.client.table("event_suggestions")
            .select("*")
            .eq("id", suggestion_id)
            .limit(1)
            .execute()
        ).data
        return Suggestion.model_validate(rows[0]) if rows else None

    async def set_suggestion_message(self, suggestion_id: str, message_id: int):
        await (
            self.client.table("event_suggestions")
            .update({"message_id": message_id})
            .eq("id", suggestion_id)
            .execute()
        )

    async def delete_suggestion(self, suggestion_id: str):
        await self.client.table("event_suggestions").delete().eq("id", suggestion_id).execute()

    async def expired_suggestions(self, before: datetime) -> list[Suggestion]:
        rows = await self._all(
            lambda: (
                self.client.table("event_suggestions")
                .select("*")
                .lte("created_at", before.isoformat())
                .order("id")
            )
        )
        return [Suggestion.model_validate(r) for r in rows]

    # ---- poll availability, Clubs Pro payments ----

    async def set_availability(self, poll_id: str, user_id: str, free: list[str]):
        await (
            self.client.table("poll_availability")
            .upsert(
                {
                    "poll_id": poll_id,
                    "user_id": user_id,
                    "free": free,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="poll_id,user_id",
            )
            .execute()
        )

    async def get_availability(self, poll_id: str, user_id: str) -> list[str] | None:
        rows = (
            await self.client.table("poll_availability")
            .select("free")
            .eq("poll_id", poll_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ).data
        return rows[0]["free"] if rows else None

    async def poll_availability(self, poll_id: str) -> dict[str, list[str]]:
        rows = await self._all(
            lambda: (
                self.client.table("poll_availability")
                .select("user_id,free")
                .eq("poll_id", poll_id)
                .order("user_id")
            )
        )
        return {r["user_id"]: r["free"] for r in rows}

    async def payment_recorded(self, charge_id: str) -> bool:
        found = (
            await self.client.table("pro_payments")
            .select("charge_id")
            .eq("charge_id", charge_id)
            .limit(1)
            .execute()
        ).data
        return bool(found)

    async def record_payment(
        self, charge_id: str, chat_id: int, telegram_id: int, stars: int, days: int
    ) -> bool:
        """True the first time a Telegram charge is seen; a replayed update returns False."""
        found = (
            await self.client.table("pro_payments")
            .select("charge_id")
            .eq("charge_id", charge_id)
            .limit(1)
            .execute()
        ).data
        if found:
            return False
        await (
            self.client.table("pro_payments")
            .insert(
                {
                    "charge_id": charge_id,
                    "chat_id": chat_id,
                    "telegram_id": telegram_id,
                    "stars": stars,
                    "days": days,
                }
            )
            .execute()
        )
        return True

    async def extend_group_pro(self, chat_id: int, days: int, now: datetime) -> datetime:
        """Pro until `days` after the later of now and the current expiry."""
        rows = (
            await self.client.table("group_plans")
            .select("tier,expires_at")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        ).data
        base = now
        if rows and rows[0]["tier"] == "PRO" and rows[0].get("expires_at"):
            current = datetime.fromisoformat(rows[0]["expires_at"].replace("Z", "+00:00"))
            base = max(now, current)
        until = base + timedelta(days=days)
        await self.set_group_tier(chat_id, "PRO", until)
        return until

    # ---- usual weekly times, refunds ----

    async def replace_usual(self, user_id: str, windows: list[tuple[int, int, int]]):
        await self.client.rpc(
            "amifree_replace_usual",
            {
                "p_user_id": user_id,
                "p_windows": [{"weekday": d, "start_min": a, "end_min": b} for d, a, b in windows],
            },
        ).execute()

    async def get_usual(self, user_id: str) -> list[tuple[int, int, int]]:
        rows = (
            await self.client.table("weekly_availability")
            .select("weekday,start_min,end_min")
            .eq("user_id", user_id)
            .order("weekday")
            .order("start_min")
            .limit(100)
            .execute()
        ).data
        return [(r["weekday"], r["start_min"], r["end_min"]) for r in rows]

    async def refundable_payment(self, telegram_id: int, since: datetime) -> dict | None:
        """The buyer's newest payment inside the refund window that is not refunded yet."""
        rows = (
            await self.client.table("pro_payments")
            .select("*")
            .eq("telegram_id", telegram_id)
            .gte("paid_at", since.isoformat())
            .order("paid_at", desc=True)
            .limit(20)
            .execute()
        ).data
        return next((r for r in rows if not r.get("refunded_at")), None)

    async def mark_refunded(self, charge_id: str, when: datetime) -> bool:
        """True if this call is the one that marked it, so a refund is never applied twice."""
        rows = (
            await self.client.table("pro_payments")
            .update({"refunded_at": when.isoformat()})
            .eq("charge_id", charge_id)
            .is_("refunded_at", "null")
            .execute()
        ).data
        return bool(rows)

    async def shorten_group_pro(self, chat_id: int, days: int, now: datetime):
        rows = (
            await self.client.table("group_plans")
            .select("tier,expires_at")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        ).data
        if not rows or rows[0]["tier"] != "PRO" or not rows[0].get("expires_at"):
            return
        current = datetime.fromisoformat(rows[0]["expires_at"].replace("Z", "+00:00"))
        left = current - timedelta(days=days)
        if left <= now:
            await self.set_group_tier(chat_id, "FREE")
        else:
            await self.set_group_tier(chat_id, "PRO", left)
