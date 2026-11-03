"""Night-before summary: tomorrow's plans and deadlines, and what's due soon after.

Sent when tomorrow has a plan (Going or still undecided) or a deadline. On Sunday nights it
also goes to anyone with a deadline in the next 30 days, with a "Later this month" list, so
big deadlines are seen weeks ahead without a long list every night.
"""

from datetime import datetime, time, timedelta
from html import escape

from app.bot.messages import clock, personal_details
from app.schemas.event import SG, Deadline, Event, Status
from app.services.deadlines import when

COMING_UP_DAYS = 7  # listed every night: due in the week after tomorrow
MONTH_DAYS = 30  # listed on Sunday nights: due later this month
LIST_LIMIT = 5


def tomorrow_window(now: datetime) -> tuple[datetime, datetime]:
    day = now.astimezone(SG).date() + timedelta(days=1)
    start = datetime.combine(day, time.min, SG)
    return start, start + timedelta(days=1)


def is_weekly(day: datetime) -> bool:
    """The summary sent on Sunday night (for Monday) also looks a month ahead."""
    return day.weekday() == 0


async def enqueue_nightly(repo, now: datetime, hour: int) -> int:
    """Queue at most one summary per user per day, from `hour` until midnight Singapore time.

    A run that happens after midnight does nothing: "tomorrow" would be the wrong day.
    """
    if now.astimezone(SG).hour < hour:
        return 0
    start, end = tomorrow_window(now)
    users = {e.user_id for e in await repo.events_between(start, end, [Status.GOING, Status.SAVED])}
    users |= await repo.deadline_owners(start, end)
    if is_weekly(start):
        users |= await repo.deadline_owners(start, start + timedelta(days=MONTH_DAYS))
    queued = 0
    for user_id in sorted(users):
        queued += await repo.enqueue_notification(
            user_id, "NIGHTLY", start.date().isoformat(), {"date": start.date().isoformat()}
        )
    return queued


def summary(
    day: datetime,
    events: list[Event],
    deadlines: list[Deadline] = (),
    *,
    coming_up: list[Deadline] = (),
    later: list[Deadline] = (),
    now: datetime | None = None,
) -> str | None:
    """deadlines: due tomorrow. coming_up: due in the week after. later: Sunday's month list.

    None when there's nothing to say: no plan or deadline tomorrow and no month list.
    """
    going = sorted((e for e in events if e.status == Status.GOING), key=lambda e: e.start_at)
    undecided = [e for e in events if e.status == Status.SAVED]
    if not (going or undecided or deadlines or later):
        return None
    now = now or day
    html_mode = any(not e.group_event_id for e in going)
    limit = 10 if html_mode else 15

    def safe(text: str) -> str:
        return escape(text) if html_mode else text

    def line(e: Event) -> str:
        if not e.group_event_id:
            return personal_details(e, compact=True)
        venue = f" @ {e.location[:80]}" if e.location else ""
        return safe(f"• {e.start_at:%H:%M}–{e.end_at:%H:%M} {e.title[:80]}{venue}")

    def listed(items: list[Deadline], describe) -> str:
        text = "\n".join(f"• {safe(d.title[:60])} · {describe(d)}" for d in items[:LIST_LIMIT])
        if len(items) > LIST_LIMIT:
            text += f"\n+ {len(items) - LIST_LIMIT} more · /deadline"
        return text

    separator = "\n\n" if html_mode else "\n"
    text = f"Tomorrow, {day:%a %-d %b}:"
    if going:
        text += "\n" + separator.join(line(e) for e in going[:limit])
        if len(going) > limit:
            text += f"\n+ {len(going) - limit} more"
    elif not (undecided or deadlines):
        text += " nothing planned yet."
    if undecided:
        text += "\n\nStill undecided: " + ", ".join(safe(e.title[:40]) for e in undecided[:5])
    if deadlines:
        text += "\n\n📌 Due tomorrow\n" + listed(deadlines, lambda d: clock(d.due_at))
    if coming_up:
        text += "\n\n🔜 Coming up\n" + listed(coming_up, lambda d: when(d.due_at, now))
    if later:
        text += "\n\n🗓 Later this month\n" + listed(
            later, lambda d: f"{d.due_at.astimezone(SG):%a %-d %b}"
        )
    return text + "\n\n/plans for details. /reminders off stops these summaries."
