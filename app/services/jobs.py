"""Outbox delivery for reminders, change alerts and nudges.

Rows are claimed, sent, and only then marked sent. The uniqueness key on each row is what
stops a repeated job run, or a retried edit, from messaging anyone twice.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import attendance_keyboard, deadline_keyboard, nudge_keyboard
from app.bot.messages import Reply, details, event_card, time_range, verdict_line
from app.schemas.event import SG, Status
from app.services.change_alerts import describe_change
from app.services.chase import card_link, enqueue_auto
from app.services.deadlines import enqueue_deadline_reminders, reminder_text
from app.services.reminders import enqueue_nightly, summary
from app.services.scheduling import synced

log = logging.getLogger(__name__)
BACKLOG_ALERT_SECONDS = 900
FAILURES_BEFORE_ALERT = 3  # consecutive ticks a background step must fail


class Undeliverable(Exception):
    """Telegram refused the DM: the user blocked the bot or never opened a private chat."""


class Notifier:
    def __init__(self, scheduling, send: Callable[[int, Reply], Awaitable[None]]):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        self.send = send
        # The in-process timer and POST /internal/jobs/run may overlap; posting a session
        # card twice is the one step the database cannot de-duplicate, so runs take turns.
        self.running = asyncio.Lock()
        self.step_failures: dict[str, int] = {}
        self.alerted: dict[str, datetime] = {}

    async def render(self, user, row: dict) -> Reply | None:
        payload = row["payload"]
        if row["kind"] == "CHANGE" and payload.get("series_text"):
            return Reply(payload["series_text"])
        if row["kind"] == "CHANGE":
            text = describe_change(payload["before"], payload["after"], payload.get("cancelled"))
            event = await self.repo.get_event(user.id, payload["event_id"])
            if payload.get("cancelled") or not event:
                return Reply(text + "\n\nIf you added it to your calendar, delete it there too.")
            # The clash check is recomputed now, against the plan's new time.
            availability = await self.scheduling.availability(user, event, event.id)
            card = event_card(event, availability, synced(user))
            rows = list(card.keyboard.inline_keyboard) if card.keyboard else []
            if event.status in (Status.SAVED, Status.GOING) and not synced(user):
                # The card already carries the calendar buttons. Apple's .ics reuses the
                # event's UID, so it replaces the old entry; a Google link can only add.
                text += (
                    "\n\nAdded it to your calendar? 🍎 replaces the old entry; "
                    "for 📅 Google, add the new one and delete the old."
                )
            return Reply(
                text + "\n\n" + verdict_line(availability),
                InlineKeyboardMarkup(rows) if rows else None,
            )
        if not user.reminders_enabled:
            return None
        if row["kind"] == "NIGHTLY":
            start = datetime.combine(date.fromisoformat(payload["date"]), time.min, SG)
            # Read at send time, so a plan cancelled since queuing is not announced.
            events = await self.repo.events_between(
                start, start + timedelta(days=1), [Status.GOING, Status.SAVED], user.id
            )
            due = await self.repo.deadlines_between(start, start + timedelta(days=1), user.id)
            text = summary(start, events, due)
            return Reply(text) if text else None
        if row["kind"] == "CONFIRM":
            event = await self.repo.get_group_event(payload["id"])
            now = self.scheduling.clock()
            if not event or event.status != "ACTIVE" or event.end_at <= now:
                return None
            linked = await self.repo.linked_event(user.id, event.id)
            # Only people still marked Going are asked; someone who changed their mind is not.
            if not linked or linked.status != Status.GOING:
                return None
            when = f"{event.start_at:%a %-d %b}, {time_range(event.start_at, event.end_at)}"
            where = f" at {event.location[:80]}" if event.location else ""
            return Reply(
                f"⏰ Still coming to {event.title[:80]}?\n{when}{where}"
                + card_link(event)
                + "\n\nThe organiser sees only how many are confirmed. /reminders off stops these.",
                attendance_keyboard(event),
            )
        if row["kind"] == "DEADLINE":
            # Read at send time: a deadline marked done or removed since is not announced.
            deadline = await self.repo.get_deadline(user.id, payload["id"])
            now = self.scheduling.clock()
            if not deadline or deadline.done or deadline.due_at <= now:
                return None
            return Reply(reminder_text(deadline, now), deadline_keyboard(deadline.id))
        if row["kind"] == "NUDGE" and payload["type"] == "event":
            event = await self.repo.get_group_event(payload["id"])
            now = self.scheduling.clock()
            # Skip if the card is over, or they answered while the nudge was queued.
            if not event or event.status != "ACTIVE" or event.end_at <= now:
                return None
            if await self.repo.linked_event(user.id, event.id):
                return None
            availability = await self.scheduling.availability(user, event)
            return Reply(
                "📣 Your group is waiting for your answer:\n\n"
                + details(event)
                + "\n\n"
                + verdict_line(availability)
                + card_link(event)
                + "\n\n/reminders off stops nudges.",
                nudge_keyboard(event),
            )
        if row["kind"] == "NUDGE":
            poll = await self.repo.get_poll(payload["id"])
            if not poll or poll.status == "CLOSED":
                return None
            answered = (
                await self.repo.poll_voter_ids(poll.id)
                if poll.status == "OPEN"
                else await self.repo.poll_participants(poll.id)
            )
            if user.id in answered:
                return None
            ask = (
                "tap Fill mine to answer from your schedule"
                if poll.status == "OPEN"
                else "tap Count me in so your free time is counted"
            )
            return Reply(
                f"🗳 Your group is choosing a time for {poll.title}. Open the poll and {ask}."
                + card_link(poll)
                + "\n\n/reminders off stops nudges."
            )
        return None

    async def deliver(self, limit: int = 50) -> dict[str, int]:
        counts = {"sent": 0, "failed": 0, "skipped": 0}
        for row in await self.repo.claim_notifications(limit):
            user = await self.repo.get_user(row["user_id"])
            try:
                reply = await self.render(user, row) if user and not user.dm_blocked else None
                if reply is None:
                    await self.repo.finish_notification(row["id"], False, True)
                    counts["skipped"] += 1
                    continue
                await self.send(user.telegram_user_id, reply)
            except Undeliverable:
                await self.repo.set_user_flags(user.id, dm_blocked=True)
                await self.repo.finish_notification(row["id"], False, True)
                counts["skipped"] += 1
            except Exception as exc:
                # Left pending with a backoff; the next run retries it.
                log.warning(
                    "notification_failed kind=%s error_type=%s", row["kind"], type(exc).__name__
                )
                await self.repo.finish_notification(row["id"], False, False)
                counts["failed"] += 1
            else:
                await self.repo.finish_notification(row["id"], True, False)
                counts["sent"] += 1
        return counts

    async def run(self) -> dict[str, int]:
        """One idempotent tick: safe to call from a scheduler as often as wanted."""
        async with self.running:
            return await self._run()

    async def _run(self) -> dict[str, int]:
        queued = await enqueue_nightly(
            self.repo, self.scheduling.clock(), self.scheduling.settings.nightly_hour
        )
        queued += await enqueue_auto(self.repo, self.scheduling.clock())
        extra = await self.group_tick()
        queued += extra.pop("confirmations", 0)
        queued += await enqueue_deadline_reminders(self.repo, self.scheduling.clock())
        try:
            calendars = await self.scheduling.refresh_calendars()
        except Exception as exc:  # a calendar problem must never stop reminders going out
            log.warning("calendar_refresh_tick_failed error_type=%s", type(exc).__name__)
            calendars = 0
        if calendars:
            extra["calendars"] = calendars
        result = {"queued": queued} | extra | await self.deliver()
        await self.report_backlog()
        return result

    async def group_tick(self) -> dict[str, int]:
        """Group housekeeping. Each step is isolated: one failing must not stop the rest."""
        now = self.scheduling.clock()
        steps = (
            ("confirmations", self.scheduling.groups.enqueue_confirmations),
            ("ended", self.scheduling.groups.sweep_ended),
            ("posted", self.scheduling.groups.post_due_sessions),
            ("suggested", self.scheduling.polls.sweep_deadlines),
            ("cleaned", self.scheduling.detection.sweep),
        )
        found: dict[str, int] = {}
        for name, step in steps:
            try:
                count = await step(now)
            except Exception as exc:
                log.warning("group_tick_failed step=%s error_type=%s", name, type(exc).__name__)
                self.step_failures[name] = self.step_failures.get(name, 0) + 1
                if self.step_failures[name] >= FAILURES_BEFORE_ALERT:
                    await self.alert(
                        f"step:{name}",
                        f"the background step '{name}' has failed {self.step_failures[name]}"
                        f" times in a row ({type(exc).__name__}). Group cards may be stale.",
                    )
                continue
            self.step_failures.pop(name, None)
            if count:
                found[name] = count
        return found

    async def alert(self, key: str, text: str):
        """Tell the operator once per cooldown. Never raises: monitoring must not break jobs."""
        settings = self.scheduling.settings
        if not settings.alert_telegram_id:
            return
        now = self.scheduling.clock()
        last = self.alerted.get(key)
        if last and now - last < timedelta(minutes=settings.alert_cooldown_minutes):
            return
        self.alerted[key] = now
        try:
            await self.send(settings.alert_telegram_id, Reply("⚠️ AmIFree: " + text))
        except Exception as exc:
            log.warning("alert_not_delivered error_type=%s", type(exc).__name__)

    async def stats(self) -> dict:
        found = await self.repo.notification_stats(self.scheduling.clock())
        return found | {"step_failures": dict(self.step_failures)}

    async def report_backlog(self):
        """Make a stuck or failing outbox visible in the logs, where alerts can pick it up."""
        try:
            stats = await self.stats()
        except Exception as exc:
            log.warning("notification_stats_failed error_type=%s", type(exc).__name__)
            return
        age = stats["oldest_undelivered_seconds"] or 0
        if age > BACKLOG_ALERT_SECONDS:
            # Failed rows alone are normal (people who blocked the bot); a stuck queue is not.
            await self.alert(
                "backlog",
                f"{stats['pending']} messages waiting, oldest {age // 60} min."
                " Telegram or the database may be down.",
            )
        if stats["failed"] or age > BACKLOG_ALERT_SECONDS:
            log.warning(
                "notification_backlog pending=%s sending=%s failed=%s oldest_seconds=%s",
                stats["pending"],
                stats["sending"],
                stats["failed"],
                age,
            )
