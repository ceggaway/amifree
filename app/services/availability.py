from collections.abc import Iterable
from dataclasses import dataclass

from app.models.timetable import BusyBlock
from app.schemas.event import SG, Event, EventDraft, Status


@dataclass(frozen=True)
class Availability:
    conflicts: tuple[EventDraft, ...]
    covered: bool = True
    # Saved plans are undecided: they are reported, never treated as commitments.
    possible: tuple[Event, ...] = ()
    day_count: int = 0
    busy_day: bool = False

    @property
    def verdict(self) -> str:
        if self.conflicts:
            return "CLASH"
        return "FREE" if self.covered else "UNKNOWN"


def overlaps(a: EventDraft, b: EventDraft) -> bool:
    return a.start_at < b.end_at and a.end_at > b.start_at


def check_availability(
    candidate: EventDraft,
    timetable: Iterable[BusyBlock],
    events: Iterable[Event],
    *,
    exclude_event_id: str | None = None,
    coverage_start=None,
    coverage_end=None,
    busy_day_threshold: int = 3,
) -> Availability:
    """Half-open intervals; Maybe/Declined/Cancelled/Preview never block."""
    events = [e for e in events if e.id != exclude_event_id]
    going = [e for e in events if e.status == Status.GOING]
    conflicts = tuple(b for b in list(timetable) + going if overlaps(candidate, b))
    possible = tuple(e for e in events if e.status == Status.SAVED and overlaps(candidate, e))
    covered = (
        coverage_start is not None
        and coverage_end is not None
        and candidate.start_at >= coverage_start
        and candidate.end_at <= coverage_end
    )
    day = candidate.start_at.astimezone(SG).date()
    day_count = sum(e.start_at.astimezone(SG).date() == day for e in going)
    return Availability(
        conflicts,
        covered,
        possible,
        day_count,
        busy_day_threshold > 0 and day_count >= busy_day_threshold,
    )
