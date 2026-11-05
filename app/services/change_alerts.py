"""Wording for change alerts: exactly what changed on a saved plan, nothing else."""

from datetime import datetime

from app.date_display import display_date
from app.schemas.event import SG


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(SG)


def _clock(value: datetime) -> str:
    return value.strftime("%-I:%M%p").replace(":00", "").lower()


def _when(row: dict) -> str:
    start, end = _time(row["start_at"]), _time(row["end_at"])
    if start.date() != end.date():
        return f"{display_date(start)} {_clock(start)} – {display_date(end)} {_clock(end)}"
    return f"{display_date(start)} {_clock(start)}–{_clock(end)}"


def describe_change(before: dict, after: dict, cancelled: bool = False) -> str:
    """For example: 'Dinner changed: Fri 9 Oct 7pm–9pm → 8pm–10pm; venue unchanged'."""
    title = after["title"]
    if cancelled:
        return f"{title} ({_when(before)}) was cancelled by the organiser."
    parts = []
    if before["title"] != after["title"]:
        parts.append(f"renamed from {before['title']}")
    if (before["start_at"], before["end_at"]) != (after["start_at"], after["end_at"]):
        old, new = _when(before), _when(after)
        same_day = (
            _time(before["start_at"]).date()
            == _time(after["start_at"]).date()
            == _time(after["end_at"]).date()
        )
        if same_day:
            new = f"{_clock(_time(after['start_at']))}–{_clock(_time(after['end_at']))}"
        parts.append(f"{old} → {new}")
    else:
        parts.append("time unchanged")
    if (before.get("location") or "") != (after.get("location") or ""):
        parts.append(
            f"venue {before.get('location') or 'not set'} → {after.get('location') or 'removed'}"
        )
    else:
        parts.append("venue unchanged")
    if (before.get("notes") or "") != (after.get("notes") or ""):
        parts.append("notes updated")
    return f"{title} changed: " + "; ".join(parts)
