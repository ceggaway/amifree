"""One daily overview of saved plans, either the night before or the same day morning."""

from datetime import datetime, time, timedelta

from app.bot.messages import personal_details
from app.date_display import display_date
from app.schemas.event import SG, Event, Status
from app.services.personal_reminder_settings import overview_target


def tomorrow_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now.astimezone(SG).date() + timedelta(days=1), time.min, SG)
    return start, start + timedelta(days=1)


async def enqueue_overviews(repo, now: datetime, period: str) -> int:
    local = now.astimezone(SG)
    day = local.date() + timedelta(days=period == "evening")
    start = datetime.combine(day, time.min, SG)
    end = start + timedelta(days=1)
    plans = await repo.events_between(start, end, [Status.GOING, Status.SAVED])
    owners = {p.user_id for p in plans if p.user_id}
    queued = 0
    for user in await repo.users_by_ids(owners):
        if not user.overview_enabled or user.overview_period != period or user.dm_blocked:
            continue
        target = overview_target(user, day)
        if not target <= local or (period == "morning" and local.hour >= 12):
            continue
        eligible = [
            p
            for p in plans
            if p.user_id == user.id
            and (not p.created_at or p.created_at <= target)
            and (period == "evening" or p.end_at > local)
        ]
        if not eligible:
            continue
        # One key for the plan date, shared by morning and evening: switching cannot duplicate it.
        queued += await repo.enqueue_notification(
            user.id,
            "NIGHTLY",
            day.isoformat(),
            {
                "date": day.isoformat(),
                "overview": True,
            },
        )
    return queued


async def enqueue_nightly(repo, now: datetime, hour: int = 21) -> int:
    return await enqueue_overviews(repo, now, "evening")


async def enqueue_morning(repo, now: datetime) -> int:
    return await enqueue_overviews(repo, now, "morning")


def summary(
    day: datetime,
    events: list[Event],
    deadlines=(),
    *,
    coming_up=(),
    later=(),
    now=None,
    period: str = "evening",
) -> str | None:
    # Group attendance reminders are independent. Personal overviews never include deadlines.
    plans = sorted(
        (e for e in events if e.status in (Status.GOING, Status.SAVED)), key=lambda e: e.start_at
    )
    if not plans:
        return None
    day_word = "Tomorrow" if period == "evening" else "Today"
    text = f"🌤 <b>Daily overview</b>\n\n{day_word} · {display_date(day)}\n\n"
    text += "\n\n".join(personal_details(e, compact=True) for e in plans[:8])
    if len(plans) > 8:
        text += f"\n\n+ {len(plans) - 8} more · /plans"
    # Details are capped and escaped by personal_details; trim only at complete card boundaries.
    while len(text) > 3700 and "\n\n<b>" in text:
        text = text.rsplit("\n\n<b>", 1)[0] + "\n\nSee /plans for more."
    return text + "\n\n/plans for details. /settings to customise reminders."


def today_text(deadlines, plans: list[Event], now: datetime) -> str | None:
    return summary(now, plans, period="morning")
