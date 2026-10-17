"""Personal deadlines: a due time to remember and be reminded of, never time you're busy."""

from datetime import datetime, time, timedelta

from app.bot.messages import clock
from app.schemas.event import SG, Deadline, DeadlineDraft, Extraction
from app.services.date_resolution import (
    Clarification,
    as_written,
    in_source,
    resolve_date,
    resolve_time,
    restore_meridiem,
)

END_OF_DAY = time(23, 59)  # "due Fri" means by the end of Friday
REMIND_AT = time(9)  # the day before, and the morning it's due
ASK_DATE = "What's the due date? For example fri, 20 oct or 2026-10-23."
EXAMPLE = "For example: CS3244 report due fri 11:59pm"


def validate_deadline(extraction: Extraction, source: str, reference: datetime) -> DeadlineDraft:
    if extraction.ambiguity:
        raise Clarification("Please clarify the due date or time: " + extraction.ambiguity[:200])
    raw_time = extraction.start_time_text or extraction.end_time_text
    for value in (extraction.date_text, raw_time):
        if value and not in_source(value, source):
            raise Clarification("I couldn't find the due date in your message. " + ASK_DATE)
    if not extraction.date_text:
        raise Clarification(ASK_DATE)
    day = resolve_date(extraction.date_text, reference)
    due_time = END_OF_DAY
    if raw_time:
        raw_time = restore_meridiem(as_written(raw_time, source), source)
        if raw_time.strip().lower() not in ("midnight", "12midnight", "12 midnight"):
            due_time = resolve_time(raw_time)
    due = datetime.combine(day, due_time, SG)
    if due <= reference:
        raise Clarification(f"{due:%a %-d %b} {clock(due)} has already passed. " + ASK_DATE)
    title = (extraction.title or "Deadline").strip()[:200] or "Deadline"
    return DeadlineDraft(title=title, due_at=due, notes=extraction.notes)


def when(due: datetime, now: datetime) -> str:
    """'Fri 23 Oct, 11:59pm (in 8 days)'."""
    due, today = due.astimezone(SG), now.astimezone(SG).date()
    days = (due.date() - today).days
    if due < now:
        relative = (
            "overdue today" if days == 0 else f"{-days} day{'s' if days < -1 else ''} overdue"
        )
    elif days == 0:
        hours = int((due - now).total_seconds() // 3600)
        left = f"in {hours} hr" if hours >= 1 else "very soon"
        relative = f"today, {left}"
    elif days == 1:
        relative = "tomorrow"
    else:
        relative = f"in {days} days"
    return f"{due:%a %-d %b}, {clock(due)} ({relative})"


def card_text(deadline: Deadline, now: datetime, reminders: bool) -> str:
    text = f"📌 {deadline.title}\nDue {when(deadline.due_at, now)}"
    if deadline.notes:
        text += "\n" + deadline.notes[:300]
    if reminders:
        text += "\n\nI'll remind you the day before and on the morning it's due."
    else:
        text += "\n\nReminders are off (/reminders on to turn them on)."
    return text + "\n/deadlines lists them all."


def reminder_moments(deadline: Deadline) -> dict[str, datetime]:
    """When each reminder is due: 9am the day before, and 9am on the day (if before it's due)."""
    due = deadline.due_at.astimezone(SG)
    moments = {"day": datetime.combine(due.date() - timedelta(days=1), REMIND_AT, SG)}
    morning = datetime.combine(due.date(), REMIND_AT, SG)
    if morning < due:
        moments["morning"] = morning
    return moments


async def enqueue_deadline_reminders(repo, now: datetime) -> int:
    """Queue reminders whose moment has come, on that same day, while still before the due time.

    A reminder whose moment was before the deadline was added is skipped: they've just seen it.
    The (user, kind, key) uniqueness on the outbox stops any repeat.
    """
    local = now.astimezone(SG)
    queued = 0
    for deadline in await repo.deadlines_between(now, now + timedelta(days=2)):
        for which, moment in reminder_moments(deadline).items():
            if not (moment <= local and moment.date() == local.date() and local < deadline.due_at):
                continue
            if deadline.created_at and deadline.created_at > moment:
                continue
            queued += await repo.enqueue_notification(
                deadline.user_id, "DEADLINE", f"{deadline.id}:{which}", {"id": deadline.id}
            )
    return queued


def reminder_text(deadline: Deadline, now: datetime) -> str:
    due = deadline.due_at.astimezone(SG)
    today = now.astimezone(SG).date()
    if due.date() == today:
        head = f"📌 Due today at {clock(due)}: {deadline.title}"
    elif due.date() == today + timedelta(days=1):
        head = f"📌 Due tomorrow ({due:%a %-d %b}, {clock(due)}): {deadline.title}"
    else:
        head = f"📌 Due {when(due, now)}: {deadline.title}"
    return head + "\n\nTap Done once it's handed in. /reminders off stops reminders."


def due_line(deadline: Deadline) -> str:
    return f"📌 Due {clock(deadline.due_at)}: {deadline.title[:60]}"
