import re
from datetime import date, datetime, time, timedelta

from dateutil import parser

from app.schemas.event import SG, EventDraft, Extraction

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Clarification(ValueError):
    """Expected input gap, displayed to the user without a stack trace."""


def resolve_date(raw: str, reference: datetime) -> date:
    raw = raw.strip().lower()
    today = reference.astimezone(SG).date()
    relative = {"today": 0, "tomorrow": 1, "tonight": 0, "day after tomorrow": 2}
    if raw in relative:
        return today + timedelta(days=relative[raw])
    match = re.fullmatch(r"(?:(this|next|coming)\s+)?([a-z]+)", raw)
    if match:
        prefix, day = match.groups()
        weekdays = {s: n for n, full in enumerate(WEEKDAYS) for s in (full, full[:3])}
        if day in weekdays:
            delta = (weekdays[day] - today.weekday()) % 7
            # 'Next' means that weekday in the following calendar week.
            if prefix == "next":
                delta = 7 - today.weekday() + weekdays[day]
            return today + timedelta(days=delta)
    # Only complete explicit dates; never let dateutil fill a missing day/month.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError as e:
            raise Clarification("Please send a valid event date, including the year.") from e
    if not (
        re.fullmatch(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?", raw)
        or re.fullmatch(
            r"(?:\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}|[a-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?)(?:,?\s+\d{4})?",
            raw,
        )
    ):
        raise Clarification(
            "What is the event date? Use YYYY-MM-DD, a day/month date, or a weekday."
        )
    explicit_year = bool(re.search(r"\d{4}", raw))
    try:
        parsed = parser.parse(raw, dayfirst=True, default=datetime(today.year, 1, 1)).date()
        if not explicit_year and parsed < today:
            parsed = parsed.replace(year=today.year + 1)
        return parsed
    except (ValueError, OverflowError) as e:
        raise Clarification("Please send a valid event date, including the year.") from e


def resolve_time(raw: str, meridiem: str | None = None) -> time:
    value = raw.lower().replace(".", "").replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", value)
    if not m:
        raise Clarification("Please send the time as 7pm or 19:00.")
    h, minute, ampm = int(m[1]), int(m[2] or 0), m[3] or meridiem
    if minute > 59:
        raise Clarification("Please send a valid time.")
    if ampm:
        if not 1 <= h <= 12:
            raise Clarification("Please send a valid AM/PM time.")
        h = h % 12 + (12 if ampm == "pm" else 0)
    elif m[2] is None and h <= 12:
        raise Clarification("Is that AM or PM? Send, for example, 7pm.")
    if not 0 <= h <= 23:
        raise Clarification("Please send a valid time.")
    return time(h, minute)


def validate_extraction(
    extraction: Extraction,
    source: str,
    reference: datetime,
    *,
    default_minutes=0,
    existing: EventDraft | None = None,
) -> EventDraft:
    if extraction.ambiguity:
        raise Clarification("Please clarify the event date and time: " + extraction.ambiguity[:200])
    for field in ("date_text", "start_time_text", "end_time_text", "end_date_text"):
        value = getattr(extraction, field)
        if value and value.casefold() not in source.casefold():
            raise Clarification(
                "I couldn't verify the date/time in your message. Please send the exact date and start/end times."
            )
    if not extraction.date_text and existing is None:
        raise Clarification("What date is this event?")
    if not extraction.start_time_text and existing is None:
        raise Clarification("What time does this event start? Include AM/PM or use 24-hour time.")
    d = (
        resolve_date(extraction.date_text, reference)
        if extraction.date_text
        else existing.start_at.date()
    )
    end_meridiem_match = re.search(
        r"(am|pm)$", (extraction.end_time_text or "").replace(" ", "").lower()
    )
    start_raw = extraction.start_time_text
    start_time = (
        resolve_time(start_raw, end_meridiem_match[1] if end_meridiem_match else None)
        if start_raw
        else existing.start_at.timetz().replace(tzinfo=None)
    )
    end_time = resolve_time(extraction.end_time_text) if extraction.end_time_text else None
    # Shared suffix in ranges: 11–1pm means 11am to 1pm.
    if (
        start_raw
        and not re.search(r"[ap]m", start_raw, re.I)
        and end_meridiem_match
        and end_time
        and start_time > end_time
        and start_time.hour >= 12
    ):
        start_time = time(start_time.hour - 12, start_time.minute)
    start = datetime.combine(d, start_time, SG)
    assumed = False
    if end_time:
        end_date = (
            resolve_date(extraction.end_date_text, reference) if extraction.end_date_text else d
        )
        end = datetime.combine(end_date, end_time, SG)
    elif existing:
        end = start + (existing.end_at - existing.start_at)
        assumed = existing.assumed_duration
    elif default_minutes:
        end = start + timedelta(minutes=default_minutes)
        assumed = True
    else:
        raise Clarification("What time does this event end? Include AM/PM or use 24-hour time.")
    if end <= start:
        raise Clarification(
            "The end time must follow the start. For an overnight event, include the end date."
        )
    if (end - start).total_seconds() > 7 * 86400:
        raise Clarification(
            "Please clarify the start and end dates; events longer than seven days aren't supported."
        )
    return EventDraft(
        title=(extraction.title or (existing.title if existing else "Event"))[:200],
        start_at=start,
        end_at=end,
        location=(
            extraction.location
            if extraction.location is not None
            else (existing.location if existing else None)
        ),
        notes=extraction.notes
        if extraction.notes is not None
        else (existing.notes if existing else None),
        assumed_duration=assumed,
    )
