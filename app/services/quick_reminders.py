"""Quick reminders: "remind me at 5pm to buy milk" rings at that exact time, never blocks time.

Read with plain rules (no AI), so the answer is predictable: a time or a "in 30 minutes", an
optional day, and what to remind about.
"""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.bot.messages import clock
from app.schemas.event import SG, Reminder
from app.services.date_resolution import Clarification, resolve_date, resolve_time

EXAMPLE = "For example: remind me at 5pm to buy milk"
DEFAULT_TIME = time(9)  # "remind me tomorrow to ..." rings at 9am
TONIGHT = time(20)
MAX_TEXT = 200

STARTS = re.compile(r"^\s*(?:please\s+)?remind\s+me\b", re.IGNORECASE)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?"
IN_DURATION = re.compile(
    r"\bin\s+(\d+(?:\.\d+)?|an?|half an)\s*(min(?:ute)?s?|hrs?|hours?)\b", re.IGNORECASE
)
TIME = re.compile(
    r"\b(?:at\s+)?(noon|midnight|\d{1,2}(?:[:.]\d{2})?\s*[ap]\.?m\.?|\d{1,2}:\d{2})(?!\w)"
    r"|\bat\s+(\d{1,2}(?:[:.]\d{2})?)(?![\w:])",
    re.IGNORECASE,
)
DATE = re.compile(
    rf"\b(?:on\s+)?(day after tomorrow|tomorrow|tmrw|tmr|today|tonight|tonite|"
    rf"(?:(?:this|next)\s+)?{_DAY}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?|"
    rf"\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}})\b",
    re.IGNORECASE,
)


@dataclass
class ReminderDraft:
    text: str
    remind_at: datetime


def is_reminder(text: str) -> bool:
    return bool(STARTS.match(text))


def _cut(text: str, match: re.Match) -> str:
    return (text[: match.start()] + " " + text[match.end() :]).strip()


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    text = re.sub(r"^(?:to|that|about)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\s+(?:at|on|by|in|for|to))+$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.;:-")


def parse_reminder(args: str, reference: datetime) -> ReminderDraft:
    """ "remind me at 5pm to buy milk" -> ("buy milk", 5pm). Raises Clarification if unclear."""
    reference = reference.astimezone(SG)
    text = STARTS.sub("", args, count=1).strip()

    remind_at: datetime | None = None
    found = IN_DURATION.search(text)
    if found:
        amount, unit = found[1].lower(), found[2].lower()
        n = {"a": 1.0, "an": 1.0, "half an": 0.5}.get(amount) or float(amount)
        minutes = n * (1 if unit.startswith("min") else 60)
        if not 1 <= minutes <= 60 * 24 * 7:
            raise Clarification("Choose a reminder between 1 minute and 7 days from now.")
        remind_at = reference + timedelta(minutes=minutes)
        text = _cut(text, found)

    day = None
    if remind_at is None:
        at = None
        found = TIME.search(text)
        if found:
            at = resolve_time(found[1] or found[2])
            text = _cut(text, found)
        found = DATE.search(text)
        raw_day = found[1].lower() if found else None
        if found:
            text = _cut(text, found)
        if at is None and raw_day is None:
            raise Clarification("When should I remind you? " + EXAMPLE)
        if raw_day:
            day = resolve_date(raw_day, reference)
        if at is None:
            at = TONIGHT if raw_day in ("tonight", "tonite") else DEFAULT_TIME
        if day is None:
            day = reference.date()
            if datetime.combine(day, at, SG) <= reference:
                day += timedelta(days=1)  # "remind me at 9am" at 3pm means tomorrow
        remind_at = datetime.combine(day, at, SG)
        if remind_at <= reference:
            raise Clarification(
                f"{remind_at:%a %-d %b} {clock(remind_at)} has already passed. " + EXAMPLE
            )

    body = _clean(text)
    if not body:
        raise Clarification("What should I remind you about? " + EXAMPLE)
    body = body[0].upper() + body[1:]
    return ReminderDraft(body[:MAX_TEXT], remind_at.replace(second=0, microsecond=0))


def when_text(remind_at: datetime, now: datetime) -> str:
    """'today at 5pm', 'tomorrow at 9am', 'Fri 23 Oct at 5pm'."""
    at, today = remind_at.astimezone(SG), now.astimezone(SG).date()
    days = (at.date() - today).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else f"{at:%a %-d %b}"
    return f"{day} at {clock(at)}"


def snooze_target(kind: str, now: datetime) -> datetime:
    now = now.astimezone(SG)
    if kind == "10":
        return now + timedelta(minutes=10)
    if kind == "60":
        return now + timedelta(hours=1)
    return datetime.combine(now.date() + timedelta(days=1), DEFAULT_TIME, SG)


def ringing_text(reminder: Reminder) -> str:
    return f"⏰ {reminder.text}"


async def enqueue_reminders(repo, now: datetime) -> int:
    """Queue reminders whose time has come. A snooze changes the time, so it queues afresh.

    Only the last day is looked at, so a reminder that sat unsent for a long outage is not
    sent days late. The (user, kind, key) uniqueness on the outbox stops any repeat.
    """
    queued = 0
    for r in await repo.reminders_between(now - timedelta(days=1), now + timedelta(seconds=1)):
        queued += await repo.enqueue_notification(
            r.user_id, "REMIND", f"{r.id}:{r.remind_at.isoformat()}", {"id": r.id}
        )
    return queued
