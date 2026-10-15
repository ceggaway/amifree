"""A member's usual weekly free times: "tue evenings, thu evenings, sat all day".

Parsed without a language model. Times are windows inside the poll grid (08:00 to 22:00,
Singapore time). Saved as (weekday, start minute, end minute), weekday 0 = Monday.
"""

import re

from app.services.date_resolution import WEEKDAYS, Clarification

GRID_START, GRID_END = 8 * 60, 22 * 60
MAX_WINDOWS = 28
NAMED = {
    "morning": (GRID_START, 12 * 60),
    "afternoon": (12 * 60, 18 * 60),
    "evening": (18 * 60, GRID_END),
    "all day": (GRID_START, GRID_END),
    "whole day": (GRID_START, GRID_END),
    "any time": (GRID_START, GRID_END),
    "anytime": (GRID_START, GRID_END),
    "free": (GRID_START, GRID_END),
}
_STEM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAYNAME = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:s|nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?"
    r"|sat(?:urday)?|sun(?:day)?"
)
_DAY_WORD = re.compile(rf"\b({_DAYNAME})s?\b", re.IGNORECASE)
_RANGE_OF_DAYS = re.compile(
    rf"\b({_DAYNAME})s?\s*(?:-|–|to|through|thru)\s*({_DAYNAME})s?\b", re.IGNORECASE
)
_GROUPS = {
    "weekdays": range(0, 5),
    "weekends": range(5, 7),
    "weekend": range(5, 7),
    "every day": range(7),
    "everyday": range(7),
    "daily": range(7),
    "all week": range(7),
    "every night": range(7),
}
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|noon|midday"
_RANGE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|to|till|until)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)
_AFTER = re.compile(
    rf"\b(?:after|from|post|starting)\s+(?:{_TIME})(?:\s*(?:onwards?|on))?", re.IGNORECASE
)
_ONWARDS = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:onwards?|and later|\+)", re.IGNORECASE
)
_BEFORE = re.compile(rf"\b(?:before|until|till|by)\s+(?:{_TIME})\b", re.IGNORECASE)


def _minutes(
    hour: str | None, minute: str | None, suffix: str | None, word: str | None = None
) -> int:
    if word:  # noon / midday
        return 12 * 60
    h, m = int(hour), int(minute or 0)
    if not 0 <= h <= 24 or m > 59:
        raise Clarification("That time isn't valid. Use something like 6pm or 18:00.")
    if suffix:
        h = h % 12 + (12 if suffix.lower() == "pm" else 0)
    elif 1 <= h <= 7:
        h += 12  # "after 6" means 6pm: nobody's grid starts at 6am
    return h * 60 + m


def _clip(start: int, end: int) -> tuple[int, int] | None:
    start, end = max(start, GRID_START), min(end, GRID_END)
    return (start, end) if end > start else None


def days_in(clause: str) -> set[int]:
    found: set[int] = set()
    text = clause.lower()
    for name, span in _GROUPS.items():
        if re.search(rf"\b{name}\b", text):
            found |= set(span)
    for match in _RANGE_OF_DAYS.finditer(text):
        a, b = _STEM[match[1][:3].lower()], _STEM[match[2][:3].lower()]
        found |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
    for match in _DAY_WORD.finditer(text):
        found.add(_STEM[match[1][:3].lower()])
    return found


def times_in(clause: str) -> list[tuple[int, int]]:
    text = clause.lower()
    found: list[tuple[int, int]] = []
    for match in _RANGE.finditer(text):
        sh, sm, ss, eh, em, es = match.groups()
        end = _minutes(eh, em, es)
        start = _minutes(sh, sm, ss or es)
        if start >= end and not ss:  # "11-1pm": the start is the morning one
            start = _minutes(sh, sm, "am" if es.lower() == "pm" else "pm")
        window = _clip(start, end)
        if window:
            found.append(window)
    text = _RANGE.sub(" ", text)
    for match in _AFTER.finditer(text):
        start = _minutes(match[1], match[2], match[3], "noon" if match[1] is None else None)
        window = _clip(start, GRID_END)
        if window:
            found.append(window)
    text = _AFTER.sub(" ", text)
    for match in _ONWARDS.finditer(text):
        window = _clip(_minutes(match[1], match[2], match[3]), GRID_END)
        if window:
            found.append(window)
    text = _ONWARDS.sub(" ", text)
    for match in _BEFORE.finditer(text):
        end = _minutes(match[1], match[2], match[3], "noon" if match[1] is None else None)
        window = _clip(GRID_START, end)
        if window:
            found.append(window)
    text = _BEFORE.sub(" ", text)
    for name, (a, b) in NAMED.items():
        if re.search(rf"\b{name}s?\b", text):
            found.append((a, b))
    return found


def merge(windows: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for day, start, end in sorted(set(windows)):
        if out and out[-1][0] == day and start <= out[-1][2]:
            out[-1] = (day, out[-1][1], max(out[-1][2], end))
        else:
            out.append((day, start, end))
    return out


def parse_usual(text: str) -> list[tuple[int, int, int]]:
    clauses = [c.strip() for c in re.split(r"[;,\n]+", text) if c.strip()]
    if not clauses:
        raise Clarification("Tell me your usual free times, e.g. /usual tue evenings, sat all day.")
    entries = [(days_in(c), times_in(c)) for c in clauses]
    # "tue, thu evenings": a clause that only names days takes the times that follow it.
    for i in range(len(entries) - 2, -1, -1):
        days, times = entries[i]
        if days and not times and entries[i + 1][1]:
            entries[i] = (days, entries[i + 1][1])
    windows: list[tuple[int, int, int]] = []
    for days, times in entries:
        if times and not days:
            raise Clarification("Which days? e.g. /usual tue evenings, sat all day.")
        if days and not times:
            names = ", ".join(WEEKDAYS[d].title() for d in sorted(days))
            raise Clarification(
                f"When on {names}? Say evening, afternoon, all day, or a range like 6-9pm."
            )
        for day in sorted(days):
            windows += [(day, a, b) for a, b in times]
    if not windows:
        raise Clarification(
            "I couldn't find days and times there. Try /usual tue evenings, sat all day."
        )
    merged = merge(windows)
    if len(merged) > MAX_WINDOWS:
        raise Clarification("That is a lot of separate windows. Keep it to a few per day.")
    return merged


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def describe(windows: list[tuple[int, int, int]]) -> str:
    return " · ".join(
        f"{WEEKDAYS[d][:3].title()} {_hhmm(a)}–{_hhmm(b)}" for d, a, b in sorted(windows)
    )


def covers(windows: list[tuple[int, int, int]], weekday: int, start: int, end: int) -> bool:
    return any(d == weekday and a <= start and end <= b for d, a, b in windows)
