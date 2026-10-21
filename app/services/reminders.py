"""Night-before summary: one message listing tomorrow's Going plans with venues."""

from datetime import datetime, time, timedelta
from html import escape

from app.bot.messages import clock, personal_details
from app.schemas.event import SG, Deadline, Event, Status


def tomorrow_window(now: datetime) -> tuple[datetime, datetime]:
    day = now.astimezone(SG).date() + timedelta(days=1)
    start = datetime.combine(day, time.min, SG)
    return start, start + timedelta(days=1)


async def enqueue_nightly(repo, now: datetime, hour: int) -> int:
    """Queue at most one summary per user per day, from `hour` until midnight Singapore time.

    A run that happens after midnight does nothing: "tomorrow" would be the wrong day.
    """
    if now.astimezone(SG).hour < hour:
        return 0
    start, end = tomorrow_window(now)
    users = {e.user_id for e in await repo.events_between(start, end, [Status.GOING])}
    queued = 0
    for user_id in sorted(users):
        queued += await repo.enqueue_notification(
            user_id, "NIGHTLY", start.date().isoformat(), {"date": start.date().isoformat()}
        )
    return queued


def summary(day: datetime, events: list[Event], deadlines: list[Deadline] = ()) -> str | None:
    going = sorted((e for e in events if e.status == Status.GOING), key=lambda e: e.start_at)
    if not going:
        return None

    html_mode = any(not e.group_event_id for e in going)
    limit = 10 if html_mode else 15

    def line(e: Event) -> str:
        if not e.group_event_id:
            return personal_details(e, compact=True)
        venue = f" @ {e.location[:80]}" if e.location else ""
        text = f"• {e.start_at:%H:%M}–{e.end_at:%H:%M} {e.title[:80]}{venue}"
        return escape(text) if html_mode else text

    separator = "\n\n" if html_mode else "\n"
    text = f"Tomorrow, {day:%a %-d %b}:\n" + separator.join(line(e) for e in going[:limit])
    if len(going) > limit:
        text += f"\n+ {len(going) - limit} more"
    undecided = [e for e in events if e.status == Status.SAVED]
    if undecided:
        text += "\n\nStill undecided: " + ", ".join(
            (escape(e.title[:40]) if html_mode else e.title[:40]) for e in undecided[:5]
        )
    if deadlines:
        text += "\n\n📌 Due: " + ", ".join(
            f"{escape(d.title[:40]) if html_mode else d.title[:40]} ({clock(d.due_at)})"
            for d in deadlines[:5]
        )
    return text + "\n\n/plans for details. /reminders off stops these summaries."
