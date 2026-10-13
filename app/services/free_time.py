"""/free: check a time, or list the free gaps in a day. No LLM: the text is short and regular."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.schemas.event import SG, EventDraft, Extraction
from app.services.date_resolution import resolve_date, validate_extraction

DAY_START, DAY_END = time(8), time(23)
MIN_GAP = timedelta(minutes=30)
DEFAULT_LENGTH = 120  # "/free fri 7pm" checks 7–9pm
_NUM = r"(?:\d{3,4}|\d{1,2}(?:[:.]\d{2})?)"
_SUFFIX = r"(?:\s*(?:[ap]\.?m\.?|hrs?))?"
RANGE = re.compile(
    # Never inside a date: neither "2026-10-01" nor "16/10-17/10" holds a time range.
    rf"(?<![\w:./-])(?P<a>{_NUM}{_SUFFIX}|noon)\s*(?:-|–|—|to|till|until)\s*(?P<b>{_NUM}{_SUFFIX}|noon|midnight)(?![\w/-])",
    re.IGNORECASE,
)
SINGLE = re.compile(
    r"(?<![\w:./])(?:at\s+)?(?P<a>\d{1,2}(?:[:.]\d{2})?\s*[ap]\.?m\.?|\d{1,2}:\d{2}|\d{4}\s*hrs?|noon)(?![\w])",
    re.IGNORECASE,
)


BARE_END = re.compile(r"(?<=\s)(?:at\s+)?(?P<a>\d{1,2})$")


@dataclass(frozen=True)
class FreeQuery:
    days: tuple[date, ...] = ()  # list free time on these days
    slot: EventDraft | None = None  # or check this exact time
    assumed_end: bool = False


def weekend(today: date) -> tuple[date, ...]:
    if today.weekday() == 6:
        return (today,)
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return (saturday, saturday + timedelta(days=1))


MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# "oct 15" / "october 15th" read like "15 oct", which the date resolver already understands.
MONTH_FIRST = re.compile(
    rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*[:.]\d)", re.IGNORECASE
)


def week_days(today: date, *, next_week: bool) -> tuple[date, ...]:
    monday = today - timedelta(days=today.weekday())
    if next_week:
        return tuple(monday + timedelta(days=7 + i) for i in range(7))
    return tuple(today + timedelta(days=i) for i in range(7 - today.weekday()))


def parse_free(text: str, reference: datetime) -> FreeQuery:
    raw = " ".join(text.split())
    today = reference.astimezone(SG).date()
    lowered = raw.lower().strip(" ?.!")
    if lowered in ("weekend", "this weekend", "the weekend"):
        return FreeQuery(days=weekend(today))
    if lowered in ("this week", "rest of the week", "the week"):
        return FreeQuery(days=week_days(today, next_week=False))
    if lowered == "next week":
        return FreeQuery(days=week_days(today, next_week=True))
    raw = MONTH_FIRST.sub(lambda m: f"{m[2]} {m[1]}", raw)
    # A bare trailing number ("fri 7") is a time too, so the user is asked "AM or PM?".
    times = RANGE.search(raw) or SINGLE.search(raw) or BARE_END.search(raw)
    date_text = (raw[: times.start()] + " " + raw[times.end() :]) if times else raw
    date_text = re.sub(r"\b(?:on|am i free|free)\b", " ", date_text, flags=re.I)
    date_text = " ".join(date_text.split()).strip(" ,?.!")
    if not times:
        day = resolve_date(date_text, reference) if date_text else today
        return FreeQuery(days=(day,))
    start, end = times.group("a"), times.groupdict().get("b")
    source = raw if date_text else raw + " today"
    draft = validate_extraction(
        Extraction(
            kind="new",
            title="Free check",
            date_text=date_text or "today",
            start_time_text=start.strip(),
            end_time_text=end.strip() if end else None,
            end_date_text=None,
            location=None,
            notes=None,
            match_title=None,
            ambiguity=None,
        ),
        source,
        reference,
        default_minutes=DEFAULT_LENGTH,
    )
    return FreeQuery(slot=draft, assumed_end=end is None)


def free_gaps(day: date, busy: list[tuple[datetime, datetime]], now: datetime) -> list:
    """Gaps of at least 30 minutes between 8am and 11pm, skipping time already past."""
    start = datetime.combine(day, DAY_START, SG)
    end = datetime.combine(day, DAY_END, SG)
    if now.astimezone(SG).date() == day:
        # From the next quarter hour: "free from 3:47pm" reads oddly.
        rounded = now.astimezone(SG).replace(second=0, microsecond=0)
        rounded += timedelta(minutes=-rounded.minute % 15)
        start = max(start, rounded)
    gaps, cursor = [], start
    for a, b in sorted(busy):
        if b <= cursor or a >= end:
            continue
        if a - cursor >= MIN_GAP:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if end - cursor >= MIN_GAP:
        gaps.append((cursor, end))
    return gaps
