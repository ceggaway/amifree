from collections.abc import Iterable
from dataclasses import dataclass

from app.models.timetable import BusyBlock
from app.schemas.event import Event, EventDraft, Status


@dataclass(frozen=True)
class Availability:
    conflicts: tuple[EventDraft, ...]
    covered: bool = True

    @property
    def verdict(self) -> str:
        if self.conflicts:
            return "CLASH"
        return "FREE" if self.covered else "UNKNOWN"


def check_availability(
    candidate: EventDraft,
    timetable: Iterable[BusyBlock],
    events: Iterable[Event],
    *,
    exclude_event_id: str | None = None,
    coverage_start=None,
    coverage_end=None,
) -> Availability:
    """Half-open intervals; Maybe/Declined/Cancelled/Preview never block."""
    blocks = list(timetable) + [
        e for e in events if e.status == Status.GOING and e.id != exclude_event_id
    ]
    conflicts = tuple(
        b for b in blocks if candidate.start_at < b.end_at and candidate.end_at > b.start_at
    )
    covered = (
        coverage_start is not None
        and coverage_end is not None
        and candidate.start_at >= coverage_start
        and candidate.end_at <= coverage_end
    )
    return Availability(conflicts, covered)
