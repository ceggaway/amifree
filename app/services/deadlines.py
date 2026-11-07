"""Personal deadlines: a due time to remember and be reminded of, never time you're busy."""

import re
from datetime import datetime, time, timedelta

from app.bot.messages import clock
from app.date_display import display_date
from app.schemas.event import SG, Deadline, DeadlineDraft, Extraction
from app.services.date_resolution import (
    Clarification,
    as_written,
    in_source,
    resolve_date,
    resolve_time,
    restore_meridiem,
    written_relative,
)
from app.services.personal_reminder_settings import MAX_LEAD, interval_label

END_OF_DAY = time(23, 59)  # "due Fri" means by the end of Friday
ASK_DATE = "What's the due date? For example fri, 20 oct or 2026-10-23."
EXAMPLE = "For example: CS3244 report due fri 11:59pm"
# Any time of day written in a message: 5pm, 11:59, 23.59, 2359, 2359hrs, noon, midnight.
# Not a year or a date: "2026-10-23" and "20/10" have no time in them.
WRITTEN_TIME = re.compile(
    r"\d\s*(?:[ap]\.?m\b|hrs?\b)|\d[:.]\d{2}|(?<![\d/-])(?:[01]\d|2[0-3])[0-5]\d(?![\d/-])"
    r"|\bnoon\b|\bmidnight\b",
    re.IGNORECASE,
)
# One written time, as a token: "2359", "2359hrs", "11.59pm", "noon". Not the "21" in "ec2102".
TIME_TOKEN = re.compile(
    r"(?<![\w:.])(?:\d{1,2}(?:[:.]?\d{2})?\s*(?:[ap]\.?m\.?|hrs?|h)?|noon)(?![\w:])", re.IGNORECASE
)


def written_time(raw: str, source: str) -> str:
    """The model rewrote "2359" as "11:59pm": use the sender's own words for the same time."""
    if in_source(raw, source):
        return raw
    try:
        wanted = resolve_time(raw)
    except Clarification:
        return raw
    for token in TIME_TOKEN.findall(source):
        try:
            if resolve_time(token.strip()) == wanted:
                return token.strip()
        except Clarification:
            continue
    return raw


def validate_deadline(extraction: Extraction, source: str, reference: datetime) -> DeadlineDraft:
    if extraction.ambiguity:
        raise Clarification("Please clarify the due date or time: " + extraction.ambiguity[:200])
    # The model writes "tmr" as "30 Oct" or "next mon" as "2 Nov": put back the sender's words.
    extraction = written_relative(extraction, source, reference, weekdays=True)
    raw_time = extraction.start_time_text or extraction.end_time_text
    if raw_time:
        raw_time = written_time(raw_time, source)
    if raw_time and not in_source(raw_time, source) and not WRITTEN_TIME.search(source):
        raw_time = None  # a time the model filled in for a message with none: end of day
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
        raise Clarification(f"{display_date(due)} {clock(due)} has already passed. " + ASK_DATE)
    title = (extraction.title or "Deadline").strip()[:200] or "Deadline"
    return DeadlineDraft(title=title, due_at=due, notes=extraction.notes)


NO_NOTES = {"none", "no", "-", "clear", "remove", "delete"}


def changed_due(text: str, current: datetime, now: datetime) -> datetime:
    """A new due time typed while editing: "sat 5pm", "tmr", "20 oct", "2359".

    Read with plain rules, never the AI. Whatever isn't mentioned stays: a day keeps the time,
    a time keeps the day. Raises Clarification for anything unclear or already past.
    """
    current = current.astimezone(SG)
    raw = " ".join(text.split())
    token = next((m for m in TIME_TOKEN.finditer(raw) if WRITTEN_TIME.search(m.group())), None)
    due_time = current.timetz().replace(tzinfo=None)
    rest = raw
    if re.search(r"\bmidnight\b", raw, re.IGNORECASE):
        due_time = END_OF_DAY
        rest = re.sub(r"\bmidnight\b", " ", rest, flags=re.IGNORECASE)
    elif token:
        due_time = resolve_time(token.group().strip())
        rest = raw[: token.start()] + " " + raw[token.end() :]
    rest = re.sub(r"\b(?:due|by|at|on|before)\b|[,@]", " ", rest, flags=re.IGNORECASE)
    rest = " ".join(rest.split())
    if not rest and not token and "midnight" not in raw.lower():
        raise Clarification("Send the new due date, time, or both, like: sat 5pm, or tmr.")
    try:
        day = resolve_date(rest, now) if rest else current.date()
    except Clarification as exc:
        if not token and re.search(r"(?<![\d/.:-])\d{1,2}(?![\d/.:-])", rest):
            raise Clarification("Is that AM or PM? Send, for example: sat 5pm.") from None
        if "event date" in str(exc):
            raise Clarification("I couldn't read that day. " + ASK_DATE) from None
        raise
    due = datetime.combine(day, due_time, SG)
    if due <= now:
        raise Clarification(f"{display_date(due)} {clock(due)} has already passed. " + ASK_DATE)
    return due


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
    return f"{display_date(due)}, {clock(due)} ({relative})"


def card_text(
    deadline: Deadline,
    now: datetime,
    reminders: bool,
    calendar: str = "",
    *,
    morning: bool = True,
    lead_minutes: int = 180,
) -> str:
    """Describe the selected deadline alert independently of the saved-plans overview.

    The legacy morning argument is accepted for existing callers; it does not add an alert.
    """
    text = f"📌 {deadline.title}\nDue {when(deadline.due_at, now)}"
    if deadline.notes:
        text += "\n" + deadline.notes[:300]
    if calendar:
        text += "\n\n" + calendar
    if reminders:
        promise = reminder_promise(deadline, now, alert=reminders, lead_minutes=lead_minutes)
        text += "\n\n" + promise + " /settings to change reminders."
    else:
        text += "\n\nReminders are off (/settings to turn them on)."
    return text + "\n/deadline lists them all."


def reminder_moments(
    deadline: Deadline, *, morning: bool = True, lead_minutes: int = 180
) -> dict[str, datetime]:
    """One personal alert at the selected interval, independent of the plans overview."""
    return {"final": deadline.due_at.astimezone(SG) - timedelta(minutes=lead_minutes)}


def on_day(moment: datetime, now: datetime) -> str:
    day, today = moment.astimezone(SG).date(), now.astimezone(SG).date()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"on {display_date(moment)}"


def reminder_promise(
    deadline: Deadline,
    now: datetime,
    *,
    morning: bool = True,
    alert: bool = True,
    lead_minutes: int = 180,
) -> str:
    if not alert:
        return "Deadline alerts are off (/settings to turn them on)."
    moment = reminder_moments(deadline, lead_minutes=lead_minutes)["final"]
    if moment <= now:
        return "Your selected alert time has already passed for this deadline."
    return (
        f"I'll remind you {on_day(moment, now)} at {clock(moment)}, "
        f"{interval_label(lead_minutes)} before."
    )


async def enqueue_deadline_reminders(repo, now: datetime) -> int:
    local = now.astimezone(SG)
    deadlines = await repo.deadlines_between(now, now + timedelta(minutes=MAX_LEAD + 1))
    users = {u.id: u for u in await repo.users_by_ids({d.user_id for d in deadlines})}
    queued = 0
    for deadline in deadlines:
        user = users.get(deadline.user_id)
        if not user or not user.deadline_alerts or user.dm_blocked:
            continue
        moment = reminder_moments(deadline, lead_minutes=user.deadline_lead_minutes)["final"]
        if not (moment <= local and moment.date() == local.date() and local < deadline.due_at):
            continue
        if deadline.created_at and deadline.created_at > moment:
            continue
        queued += await repo.enqueue_notification(
            deadline.user_id,
            "DEADLINE",
            f"{deadline.id}:{deadline.due_at.isoformat()}:{user.deadline_lead_minutes}",
            {
                "id": deadline.id,
                "due_at": deadline.due_at.isoformat(),
                "lead_minutes": user.deadline_lead_minutes,
            },
        )
    return queued


def reminder_text(deadline: Deadline, now: datetime) -> str:
    due = deadline.due_at.astimezone(SG)
    return (
        f"⏰ Deadline reminder\n\n📌 {deadline.title}\n"
        f"Due {display_date(due)} · {clock(due)}"
        "\n\nTap Done once it's handed in. /settings to customise reminders."
        "\n/deadline lists them all."
    )


def hours_left(due: datetime, now: datetime) -> str:
    hours = int((due - now).total_seconds() // 3600)
    return f" (in {hours} hr)" if hours >= 1 else " (very soon)"


def due_line(deadline: Deadline) -> str:
    return f"📌 Due {clock(deadline.due_at)}: {deadline.title[:60]}"
