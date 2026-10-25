"""Retired group-message detection: reject old controls and clean up old suggestions."""

import logging
from datetime import datetime, timedelta

from app.bot.messages import Reply
from app.services.group_events import GroupResult

log = logging.getLogger(__name__)
SUGGESTION_TTL = timedelta(minutes=10)
REMOVED = "Spotting plans in chat is no longer supported. Create events in the mini app."


class DetectionService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo

    async def enabled(self, chat_id: int) -> bool:
        # Stored settings from older versions must never reactivate message reading.
        return False

    async def command(
        self, telegram_id: int, chat_id: int, args: str, is_admin: bool
    ) -> GroupResult:
        return GroupResult(post=Reply(REMOVED))

    async def on_message(
        self, telegram_id: int, chat_id: int, message_id: int, text: str, reference: datetime
    ) -> None:
        return None

    async def posted(self, suggestion_id: str, message_id: int):
        await self.repo.set_suggestion_message(suggestion_id, message_id)

    async def callback(self, telegram_id: int, chat_id: int, data: str, **kwargs) -> GroupResult:
        # Older suggestion buttons cannot create an event after the feature is retired.
        return GroupResult(alert=REMOVED, delete_message=True)

    async def sweep(self, now: datetime) -> int:
        """Remove suggestions nobody answered."""
        removed = 0
        for suggestion in await self.repo.expired_suggestions(now - SUGGESTION_TTL):
            remove = getattr(self.scheduling, "delete_group_message", None)
            if remove and suggestion.message_id:
                try:
                    await remove(suggestion.chat_id, suggestion.message_id)
                except Exception as exc:
                    log.warning("suggestion_cleanup_failed error_type=%s", type(exc).__name__)
            await self.repo.delete_suggestion(suggestion.id)
            removed += 1
        return removed
