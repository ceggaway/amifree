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

END_OF_DAY = time(23, 59)  # "due Fri" means by the end of Friday
REMIND_AT = time(9)  # the Today list on the morning it's due
FINAL_HOURS = 3  # the last alert, before it's due
FINAL_EARLIEST = time(12)  # on the due day, earlier than this the 9am list is enough
QUIET_FROM = time(23)  # no alerts from 11pm until the 9am list
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
    deadline: Deadline, now: datetime, reminders: bool, calendar: str = "", *, morning: bool = True
) -> str:
    """reminders: the 3-hours-before switch; morning: the 9am Today list's switch.
    calendar: what happened in the student's calendar, said only once it's true."""
    text = f"📌 {deadline.title}\nDue {when(deadline.due_at, now)}"
    if deadline.notes:
        text += "\n" + deadline.notes[:300]
    if calendar:
        text += "\n\n" + calendar
    if reminders or morning:
        promise = reminder_promise(deadline, now, morning=morning, alert=reminders)
        text += "\n\n" + promise + " /settings to change reminders."
    else:
        text += "\n\nReminders are off (/settings to turn them on)."
    return text + "\n/deadline lists them all."


def reminder_moments(deadline: Deadline, *, morning: bool = True) -> dict[str, datetime]:
    """When each reminder is due.

    "today": the 9am Today list on the due day, if it's due after 9am.
    "final": 3 hours before it's due. Skipped in quiet hours (11pm to 9am), and on the due
    day before noon, when the 9am list was only a few hours earlier (unless morning=False:
    the person turned that list off).
    The day before is the evening summary's job ("Due tomorrow"), not a separate message.
    """
    due = deadline.due_at.astimezone(SG)
    moments = {}
    nine = datetime.combine(due.date(), REMIND_AT, SG)
    if nine < due:
        moments["today"] = nine
    final = due - timedelta(hours=FINAL_HOURS)
    if final.date() == due.date():
        earliest = FINAL_EARLIEST if morning else REMIND_AT
        timely = earliest <= final.time() < QUIET_FROM
    else:
        timely = REMIND_AT <= final.time() < QUIET_FROM
    if timely:
        moments["final"] = final
    return moments


def on_day(moment: datetime, now: datetime) -> str:
    day, today = moment.astimezone(SG).date(), now.astimezone(SG).date()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"on {display_date(moment)}"


def reminder_promise(
    deadline: Deadline, now: datetime, *, morning: bool = True, alert: bool = True
) -> str:
    """The reminders still to come, so the card never promises one that won't be sent.
    morning and alert are the person's switches for the 9am list and the 3-hours-before alert."""
    moments = {k: m for k, m in reminder_moments(deadline, morning=morning).items() if m > now}
    today = moments.get("today") if morning else None
    final = moments.get("final") if alert else None
    if today and final:
        return (
            f"I'll remind you {on_day(today, now)} at 9am, and again at {clock(final)},"
            " 3 hours before."
        )
    if today:
        return f"I'll remind you {on_day(today, now)} at 9am."
    if final:
        return f"I'll remind you {on_day(final, now)} at {clock(final)}, 3 hours before."
    if deadline.due_at.astimezone(SG).date() > now.astimezone(SG).date():
        return "It'll be in your evening summary the night before."
    return "It's due soon, so there's no reminder to send."


async def enqueue_deadline_reminders(repo, now: datetime) -> int:
    """Queue reminders whose moment has come, on that same day, while still before the due time.

    Only the "final" alert is queued here; the 9am Today list is enqueue_morning's (reminders.py).
    A reminder whose moment was before the deadline was added is skipped: they've just seen it.
    The (user, kind, key) uniqueness on the outbox stops any repeat.
    """
    local = now.astimezone(SG)
    deadlines = await repo.deadlines_between(now, now + timedelta(days=2))
    # Before noon on the due day the alert is only for people who turned the 9am list off.
    early = {
        d.user_id
        for d in deadlines
        if "final" in reminder_moments(d, morning=False) and "final" not in reminder_moments(d)
    }
    no_morning = (
        {u.id for u in await repo.users_by_ids(early) if not u.morning_enabled} if early else set()
    )
    queued = 0
    for deadline in deadlines:
        moments = reminder_moments(deadline, morning=deadline.user_id not in no_morning)
        for which, moment in moments.items():
            if which == "today":
                continue
            if not (moment <= local and moment.date() == local.date() and local < deadline.due_at):
                continue
            if deadline.created_at and deadline.created_at > moment:
                continue
            queued += await repo.enqueue_notification(
                deadline.user_id, "DEADLINE", f"{deadline.id}:{which}", {"id": deadline.id}
            )
    return queued


def reminder_text(deadline: Deadline, now: datetime) -> str:
    """The 3-hours-before alert."""
    due = deadline.due_at.astimezone(SG)
    hours = max(1, round((due - now).total_seconds() / 3600))
    head = f"⏰ Due at {clock(due)}, in about {hours} hr: {deadline.title}"
    return (
        head + "\n\nTap Done once it's handed in. /settings to change reminders."
        "\n/deadline lists them all."
    )


def hours_left(due: datetime, now: datetime) -> str:
    hours = int((due - now).total_seconds() // 3600)
    return f" (in {hours} hr)" if hours >= 1 else " (very soon)"


def due_line(deadline: Deadline) -> str:
    return f"📌 Due {clock(deadline.due_at)}: {deadline.title[:60]}"
