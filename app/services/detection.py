"""Optional event detection: a group admin switches it on, with a clear privacy notice.

Off by default. While it is off, ordinary group messages are dropped before anything reads or
stores them. While it is on, a message is looked at only if it looks like a plan (a time and a
day), then read once by the same extractor as /event. The result is a suggestion with an
Ignore button that removes itself; the message text is never stored.
"""

import logging
import re
import time
from datetime import datetime, timedelta

from app.bot.keyboards import suggestion_keyboard
from app.bot.messages import Reply, details
from app.schemas.event import EventDraft
from app.services.date_resolution import Clarification, validate_extraction
from app.services.group_events import GroupResult, parse_id
from app.services.ratelimit import RateLimiter
from app.services.recurrence import weekdays_in

log = logging.getLogger(__name__)
SUGGESTION_TTL = timedelta(minutes=10)
CACHE_SECONDS = 60
MIN_CHARS, MAX_CHARS = 12, 600
TIME = re.compile(r"\b\d{1,2}(?:[:.]\d{2})?\s?(?:am|pm)\b|\b\d{1,2}[:.]\d{2}\b", re.IGNORECASE)
DAY = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\btomorrow\b|\btonight\b|\btmr\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b\d{1,2}/\d{1,2}\b",
    re.IGNORECASE,
)

NOTICE = (
    "👀 Event detection is on for this group.\n\n"
    "AmIFree will now look at messages here that mention a day and a time, read them once to"
    " spot a plan, and offer to turn it into an event card. Only a tap on Make event card"
    " saves anything. Message text is not stored, and suggestions remove themselves after"
    " 10 minutes.\n\n"
    "AmIFree must be an admin of this group to see messages. A group admin can turn this off"
    " any time with /detect off."
)
OFF = "Event detection is off. Messages in this group are not read."
ADMIN_ONLY = "Only a group admin can change event detection."
HOW_TO = "Send /detect on or /detect off (group admins only)."


def looks_like_plan(text: str) -> bool:
    return MIN_CHARS <= len(text) <= MAX_CHARS and bool(TIME.search(text) and DAY.search(text))


class DetectionService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        self.limiter = RateLimiter(scheduling.settings.detection_per_hour, 3600)
        self._cache: dict[int, tuple[bool, float]] = {}

    async def enabled(self, chat_id: int) -> bool:
        cached = self._cache.get(chat_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        found = await self.repo.detection_enabled(chat_id)
        if len(self._cache) > 10_000:
            self._cache.clear()
        self._cache[chat_id] = (found, time.monotonic() + CACHE_SECONDS)
        return found

    async def command(
        self, telegram_id: int, chat_id: int, args: str, is_admin: bool
    ) -> GroupResult:
        choice = args.strip().lower()
        if choice not in ("on", "off"):
            state = "on" if await self.enabled(chat_id) else "off"
            return GroupResult(post=Reply(f"Event detection is {state}. {HOW_TO}"))
        if not is_admin:
            return GroupResult(post=Reply(ADMIN_ONLY))
        await self.repo.set_detection(chat_id, choice == "on", telegram_id)
        self._cache.pop(chat_id, None)
        return GroupResult(post=Reply(NOTICE if choice == "on" else OFF))

    async def on_message(
        self, telegram_id: int, chat_id: int, message_id: int, text: str, reference: datetime
    ) -> GroupResult | None:
        """A suggestion for this message, or None. Never raises: detection must stay quiet."""
        if not await self.enabled(chat_id) or not looks_like_plan(text):
            return None
        if weekdays_in(text) or not self.limiter.allow(chat_id):
            return None  # a recurring plan needs /event, which asks how long it runs
        try:
            extracted = await self.scheduling.extractor.extract(text, reference, None)
            if extracted.kind != "new":
                return None
            draft = validate_extraction(
                extracted,
                text,
                reference,
                default_minutes=self.scheduling.settings.default_event_duration_minutes,
            )
        except Clarification:
            return None
        except Exception as exc:
            log.warning("detection_failed error_type=%s", type(exc).__name__)
            return None
        if draft.start_at <= self.scheduling.clock():
            return None
        # Notes can quote the message itself; the suggestion keeps only the plan's facts.
        draft = draft.model_copy(update={"notes": None})
        suggestion = await self.repo.create_suggestion(chat_id, message_id, draft)
        if suggestion is None:
            return None
        text = (
            "👀 Looks like a plan:\n\n" + details(draft) + "\n\nMake an event card for the group?"
        )
        return GroupResult(
            post=Reply(text, suggestion_keyboard(suggestion.id)), suggestion_id=suggestion.id
        )

    async def posted(self, suggestion_id: str, message_id: int):
        await self.repo.set_suggestion_message(suggestion_id, message_id)

    async def callback(
        self,
        telegram_id: int,
        chat_id: int,
        data: str,
        name: str | None = None,
        username: str | None = None,
    ) -> GroupResult:
        parts = data.split(":")
        suggestion_id = parse_id(parts[2]) if len(parts) == 3 else None
        suggestion = await self.repo.get_suggestion(suggestion_id) if suggestion_id else None
        if not suggestion or suggestion.chat_id != chat_id or parts[1] not in ("ok", "no"):
            return GroupResult(alert="That suggestion has expired.", delete_message=True)
        if parts[1] == "no":
            await self.repo.delete_suggestion(suggestion.id)
            return GroupResult(delete_message=True)
        user = await self.repo.ensure_user(telegram_id)
        groups = self.scheduling.groups
        await groups.remember(chat_id, user.id, name, username)
        draft = EventDraft.model_validate(suggestion.draft)
        event = await self.repo.create_group_event(
            user.id, chat_id, suggestion.source_message_id, draft
        )
        await self.repo.delete_suggestion(suggestion.id)
        if event.message_id:
            return GroupResult(alert="That card already exists.", delete_message=True)
        await self.scheduling.safe_action(user.id, "group_event")
        return GroupResult(
            alert="Event card made. You're its organiser.",
            card=await groups.render(event),
            adopt_event_id=event.id,
        )

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
