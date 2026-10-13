"""Outbox delivery for reminders, change alerts and nudges.

Rows are claimed, sent, and only then marked sent. The uniqueness key on each row is what
stops a repeated job run, or a retried edit, from messaging anyone twice.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import nudge_keyboard
from app.bot.messages import Reply, details, event_card, verdict_line
from app.schemas.event import SG, Status
from app.services.change_alerts import describe_change
from app.services.chase import card_link, enqueue_auto
from app.services.reminders import enqueue_nightly, summary
from app.services.scheduling import synced

log = logging.getLogger(__name__)
BACKLOG_ALERT_SECONDS = 900


class Undeliverable(Exception):
    """Telegram refused the DM: the user blocked the bot or never opened a private chat."""


class Notifier:
    def __init__(self, scheduling, send: Callable[[int, Reply], Awaitable[None]]):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        self.send = send

    async def render(self, user, row: dict) -> Reply | None:
        payload = row["payload"]
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
            text = summary(start, events)
            return Reply(text) if text else None
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
        queued = await enqueue_nightly(
            self.repo, self.scheduling.clock(), self.scheduling.settings.nightly_hour
        )
        queued += await enqueue_auto(self.repo, self.scheduling.clock())
        try:
            calendars = await self.scheduling.refresh_calendars()
        except Exception as exc:  # a calendar problem must never stop reminders going out
            log.warning("calendar_refresh_tick_failed error_type=%s", type(exc).__name__)
            calendars = 0
        extra = {"calendars": calendars} if calendars else {}
        result = {"queued": queued} | extra | await self.deliver()
        await self.report_backlog()
        return result

    async def stats(self) -> dict:
        return await self.repo.notification_stats(self.scheduling.clock())

    async def report_backlog(self):
        """Make a stuck or failing outbox visible in the logs, where alerts can pick it up."""
        try:
            stats = await self.stats()
        except Exception as exc:
            log.warning("notification_stats_failed error_type=%s", type(exc).__name__)
            return
        age = stats["oldest_undelivered_seconds"] or 0
        if stats["failed"] or age > BACKLOG_ALERT_SECONDS:
            log.warning(
                "notification_backlog pending=%s sending=%s failed=%s oldest_seconds=%s",
                stats["pending"],
                stats["sending"],
                stats["failed"],
                age,
            )
