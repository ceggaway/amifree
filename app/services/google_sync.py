"""Keeps a student's Google Calendar in step with their plans.

The student shares their calendar with AmIFree's Google account (see google_calendar.py).
Instead of hooking every place a plan can change (Going, Skip, an edit, Undo, a group
organiser moving the time), this compares what the student's plans are now with what was last
written to Google, and fixes the difference. It runs after the student's own actions, and every
few minutes in the background, so a change made by someone else catches up as well.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta

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
CODE_TTL, MAX_TRIES = timedelta(minutes=30), 5  # the one-time code that proves ownership
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

    def steps(self, phone: bool = False) -> list[Reply]:
        if phone:
            text = onboarding.google_share_steps_phone(self.client.email)
            photos = onboarding.google_phone_photos()
            caption = onboarding.PHOTOS_CAPTION
        else:
            text = onboarding.google_share_steps(self.client.email)
            photos = onboarding.google_share_photos()
            caption = onboarding.GOOGLE_PHOTOS_CAPTION
        keyboard = onboarding.google_steps_keyboard(phone)
        if photos:
            text += "\n\n📸 <b>Picture guide below</b> ↓"
        replies = [Reply(text, keyboard, edit=True, parse_mode="HTML", clear_album_group="setup")]
        if photos:
            replies.append(Reply(caption, photos=photos, album_group="setup"))
        return replies

    async def connect(self, user, email: str) -> list[Reply]:
        """Step 1 of 2: check the calendar is shared, then put a one-time code in it.

        Sharing is with AmIFree, not with one person, so anyone who knows the email could
        otherwise claim it. Only someone who can open the calendar can read the code back."""
        s, now = self.s, self.s.clock()
        email = email.strip().lower()
        refused = await self.refused(user, email)
        if refused:
            return [refused]
        try:
            try:
                access = await self.client.probe(email)
                code = f"{secrets.randbelow(10**6):06d}"
                event_id = (
                    await self.client.add_code(email, code, now) if access is Access.WRITE else None
                )
            except GoogleAccessLost:
                access = Access.NONE
        except GoogleCalendarError:
            return [Reply("Google didn't answer just now. Try again in a minute.")]
        if access is not Access.WRITE:
            return [self.not_ready(access)]
        await self.discard(user.pending)  # an earlier code that was never used
        await s.repo.set_pending(
            user.id,
            {
                "stage": "gcal_code",
                "email": email,
                "code": self.digest(code),
                "event": event_id,
                "expires": (now + CODE_TTL).isoformat(),
                "tries": 0,
            },
        )
        return [
            Reply(
                f"One last step, to check {email} is yours.\n\nI've put an event called"
                ' "AmIFree code" in that calendar today. Open Google Calendar and send me the'
                " 6-digit number in it.\n\n/cancel to stop."
            )
        ]

    async def verify(self, user, pending: dict, text: str) -> list[Reply]:
        """Step 2 of 2: the code sent back. Connects on a match."""
        s, now = self.s, self.s.clock()
        if datetime.fromisoformat(pending["expires"]) <= now:
            await s.repo.set_pending(user.id, None)
            await self.discard(pending)
            return [Reply("That code has expired. Send your email again for a new one.")]
        if not hmac.compare_digest(self.digest(text.strip()), pending["code"]):
            tries = pending.get("tries", 0) + 1
            if tries >= MAX_TRIES:
                await s.repo.set_pending(user.id, None)
                await self.discard(pending)
                return [Reply("That's not it, and that was the last try. Send your email again.")]
            await s.repo.set_pending(user.id, pending | {"tries": tries})
            return [
                Reply(
                    'That\'s not the code. Look for the event "AmIFree code" in your Google'
                    " Calendar today and send me its 6-digit number. /cancel to stop."
                )
            ]
        await s.repo.set_pending(user.id, None)
        await self.discard(pending)
        return await self.finish(user, pending["email"])

    async def discard(self, pending: dict | None):
        """Remove an unused code event from the calendar. Best effort: it is harmless."""
        if not pending or pending.get("stage") != "gcal_code" or not pending.get("event"):
            return
        try:
            await self.client.delete_event(pending["email"], pending["event"])
        except GoogleCalendarError as exc:
            log.warning("google_code_cleanup_failed error_type=%s", type(exc).__name__)

    def digest(self, code: str) -> str:
        return hmac.new(self.s.link_secret.encode(), code.encode(), hashlib.sha256).hexdigest()

    async def refused(self, user, email: str) -> Reply | None:
        """Why this calendar can't be connected to this person, or None."""
        s = self.s
        owner = await s.repo.google_calendar_owner(email)
        if owner and owner != user.id:
            return Reply(
                "That calendar is already connected to another AmIFree account. If it's yours,"
                " remove it there first."
            )
        digest = gcal_hash(email, s.link_secret)
        calendars = await s.repo.list_calendars(user.id)
        old = await s.repo.get_google_connection(user.id)
        replacing = old and old.calendar_email != email and old.calendar_id
        if not any(c.url_hash == digest for c in calendars) and (
            len(calendars) - (1 if replacing else 0) >= MAX_CALENDARS
        ):
            return Reply(
                f"You've connected {MAX_CALENDARS} calendars, the most I check. Remove one in"
                " /calendar first."
            )
        return None

    async def finish(self, user, email: str) -> list[Reply]:
        """Connect a calendar whose owner has been checked."""
        s, now = self.s, self.s.clock()
        refused = await self.refused(user, email)  # checked again: time passed since step 1
        if refused:
            return [refused]
        digest = gcal_hash(email, s.link_secret)
        old = await s.repo.get_google_connection(user.id)
        replacing = old and old.calendar_email != email and old.calendar_id
        try:
            busy = await self.client.busy(email, now - WINDOW_PAST, now + WINDOW_AHEAD)
        except GoogleAccessLost:
            return [self.not_ready(Access.NONE)]
        except GoogleCalendarError:
            return [Reply("Google didn't answer just now. Send your email again in a minute.")]
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
