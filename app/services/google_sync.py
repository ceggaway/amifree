"""Keeps a student's Google Calendar in step with their plans.

The student shares their calendar with AmIFree's Google account (see google_calendar.py).
Instead of hooking every place a plan can change (Going, Skip, an edit, Undo, a group
organiser moving the time), this compares what the student's plans are now with what was last
written to Google, and fixes the difference. It runs after the student's own actions, and every
few minutes in the background, so a change made by someone else catches up as well.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.bot import onboarding
from app.bot.keyboards import google_help_keyboard
from app.bot.messages import Reply
from app.models.user import PersonalCalendar
from app.schemas.event import Status
from app.services.google_calendar import (
    GCAL_PREFIX,
    Access,
    GoogleAccessLost,
    GoogleCalendarError,
    gcal_hash,
)
from app.services.personal_calendar import MAX_CALENDARS, WINDOW_AHEAD, WINDOW_PAST, seal

log = logging.getLogger(__name__)

SYNC_EVERY = timedelta(minutes=10)
PLAN_PAST, PLAN_AHEAD = timedelta(days=30), timedelta(days=400)  # the same range as the feed
KEEP = (Status.GOING, Status.SAVED)
LOST = (
    "I can't change your Google Calendar any more. Share it with AmIFree again (Make "
    "changes), then send me your email."
)
LATER = "I couldn't update your Google Calendar just now. I'll try again."


class GoogleSyncService:
    def __init__(self, service):
        self.s = service
        self.locks: dict[str, asyncio.Lock] = {}
        self.tasks: set[asyncio.Task] = set()

    @property
    def client(self):
        return self.s.google

    @property
    def enabled(self) -> bool:
        return self.client is not None

    # ---- connecting ----

    def steps(self) -> list[Reply]:
        text = onboarding.google_share_steps(self.client.email)
        replies = [Reply(text, onboarding.google_steps_keyboard(), edit=True, parse_mode="HTML")]
        photo = onboarding.google_share_photo()
        if photo:
            replies.append(Reply(onboarding.GOOGLE_PHOTO_CAPTION, photo=photo))
        return replies

    async def connect(self, user, email: str) -> list[Reply]:
        s, now = self.s, self.s.clock()
        email = email.strip().lower()
        digest = gcal_hash(email, s.link_secret)
        calendars = await s.repo.list_calendars(user.id)
        old = await s.repo.get_google_connection(user.id)
        replacing = old and old.calendar_email != email and old.calendar_id
        if not any(c.url_hash == digest for c in calendars) and (
            len(calendars) - (1 if replacing else 0) >= MAX_CALENDARS
        ):
            return [
                Reply(
                    f"You've connected {MAX_CALENDARS} calendars, the most I check. Remove one "
                    "in /calendar first."
                )
            ]
        try:
            try:
                access = await self.client.probe(email)
                busy = (
                    await self.client.busy(email, now - WINDOW_PAST, now + WINDOW_AHEAD)
                    if access is Access.WRITE
                    else None
                )
            except GoogleAccessLost:
                access, busy = Access.NONE, None
        except GoogleCalendarError:
            return [Reply("Google didn't answer just now. Try again in a minute.")]
        if access is not Access.WRITE:
            return [self.not_ready(access)]
        if replacing:
            await s.repo.remove_calendar(user.id, old.calendar_id)  # its connection goes with it
        calendar, _ = await s.repo.add_calendar(
            user.id, seal(GCAL_PREFIX + email, s.link_secret), digest, "Google Calendar"
        )
        await s.repo.replace_calendar(calendar.id, busy, now)
        await s.repo.save_google_connection(user.id, email, calendar.id)
        added = await self.sync(user.id)
        await s.safe_action(user.id, "google_calendar_connected", metadata={"plans": added})
        got = (
            f"I added your {added} current plan{'s' if added != 1 else ''} just now."
            if added
            else "Plans you've already said Going to will appear there too."
        )
        return [
            Reply(
                f"✅ Connected to {email}.\n\nWhen you tap Going on a plan I'll add it to this "
                "calendar and keep it up to date. I only change events I added, and I only "
                f"read when you're busy.\n\n{got}\n\nTo stop, tap Remove under Google Calendar "
                "in /calendar."
            )
        ]

    def not_ready(self, access: Access) -> Reply:
        if access is Access.NONE:
            lead = "I can't see that calendar yet."
            fix = (
                "Check that you shared it with AmIFree and that the email you sent is the one "
                "on that calendar. Then send your email again."
            )
        else:
            lead = (
                "I can see when you're busy, but not your events."
                if access is Access.BUSY_ONLY
                else "I can see your events, but I can't change them."
            )
            fix = (
                "To add plans for you, change my permission in Shared with to a Make changes "
                "option, then send your email again."
            )
        return Reply(f"{lead}\n\n{fix}", google_help_keyboard())

    # ---- busy times ----

    async def refresh_busy(self, calendar: PersonalCalendar, email: str, now) -> None:
        """The hourly refresh of a Google calendar's busy times (errors are shown in /calendar)."""
        repo = self.s.repo
        if not self.enabled:
            await repo.calendar_error(calendar.id, "Google sharing is switched off for now.", now)
            return
        try:
            busy = await self.client.busy(email, now - WINDOW_PAST, now + WINDOW_AHEAD)
        except GoogleAccessLost:
            # A calendar that is no longer shared must not leave stale busy times behind.
            await repo.replace_calendar(calendar.id, [], now)
            await repo.calendar_error(calendar.id, LOST, now)
        except GoogleCalendarError:
            await repo.calendar_error(calendar.id, "I couldn't refresh it just now.", now)
        else:
            await repo.replace_calendar(calendar.id, busy, now)

    # ---- plans -> Google Calendar ----

    async def sync(self, user_id: str) -> int:
        """Make the calendar match the student's plans. Returns how many events it wrote."""
        if not self.enabled:
            return 0
        repo = self.s.repo
        async with self.locks.setdefault(user_id, asyncio.Lock()):
            connection = await repo.get_google_connection(user_id)
            if connection is None:
                return 0
            email, now, written = connection.calendar_email, self.s.clock(), 0
            try:
                wanted = {
                    e.id: e
                    for e in await repo.events_between(
                        now - PLAN_PAST, now + PLAN_AHEAD, list(KEEP), user_id
                    )
                }
                links = await repo.google_links(user_id)
                for event in wanted.values():
                    if links.get(event.id) == event.version:
                        continue
                    await self.client.upsert_event(email, event, existing=event.id in links)
                    await repo.save_google_link(user_id, event.id, event.version)
                    written += 1
                for event_id in links.keys() - wanted.keys():
                    event = await repo.get_event(user_id, event_id)
                    if event is not None and event.status in KEEP:
                        continue  # still a plan, just outside the dates I keep in step
                    await self.client.delete_event(email, event_id.replace("-", ""))
                    await repo.delete_google_link(user_id, event_id)
            except GoogleAccessLost:
                await repo.mark_google_synced(user_id, now, LOST)
                if connection.calendar_id:
                    await repo.calendar_error(connection.calendar_id, LOST, now)
                return written
            except GoogleCalendarError:
                await repo.mark_google_synced(user_id, now, LATER)
                return written
            await repo.mark_google_synced(user_id, now)
            return written

    def sync_soon(self, telegram_id: int):
        """After a message or tap: update Google in the background, so the reply isn't slowed."""
        if not self.enabled:
            return
        task = asyncio.create_task(self._sync_for(telegram_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _sync_for(self, telegram_id: int):
        try:
            user = await self.s.repo.ensure_user(telegram_id)
            if user.google_connected:
                await self.sync(user.id)
        except Exception as exc:  # a Google problem must never break the bot
            log.warning("google_sync_failed error_type=%s", type(exc).__name__)

    async def drain(self):
        """Wait for background syncs; for tests and for shutting down."""
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def sync_due(self, limit: int = 20) -> int:
        """The jobs tick: catches changes the student didn't make (a group organiser moving
        the time) and retries anything that failed. Returns how many calendars were checked."""
        if not self.enabled:
            return 0
        due = await self.s.repo.google_connections_due(self.s.clock() - SYNC_EVERY, limit)
        for connection in due:
            try:
                await self.sync(connection.user_id)
            except Exception as exc:
                log.warning("google_sync_failed error_type=%s", type(exc).__name__)
        return len(due)
