"""Outbox delivery for reminders, change alerts and nudges.

Rows are claimed, sent, and only then marked sent. The uniqueness key on each row is what
stops a repeated job run, or a retried edit, from messaging anyone twice.
"""

import logging
from collections.abc import Awaitable, Callable

from app.bot.messages import Reply, event_card, verdict_line
from app.services.change_alerts import describe_change

log = logging.getLogger(__name__)


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
                return Reply(text)
            # The clash check is recomputed now, against the plan's new time.
            availability = await self.scheduling.availability(user, event, event.id)
            card = event_card(event, availability)
            return Reply(text + "\n\n" + verdict_line(availability), card.keyboard)
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
        return await self.deliver()
