"""Recurring group events: "Training every Tue/Thu until week 13".

A series is a list of weekdays, a first date and a last date. Each session becomes its own
group card row, so RSVPs, nudges, "still coming?" checks and attendance all work per session.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.schemas.event import SG
from app.services.date_resolution import WEEKDAYS, Clarification, resolve_date
from app.services.nusmods import CALENDAR

MAX_SESSIONS = 60
MAX_WEEKS = 26
_STEM = {
    "mon": 0,
    "tue": 1,
    "tues": 1,
    "wed": 2,
    "weds": 2,
    "wednes": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "fri": 4,
    "sat": 5,
    "satur": 5,
    "sun": 6,
}
_DAY = r"(?:mon|tues?|wed(?:nes)?|weds|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?s?"
_LIST = re.compile(
    rf"\b(?:every|each)\s+({_DAY}(?:\s*(?:[/,&+]|and|or)\s*{_DAY})*)\b", re.IGNORECASE
)
_PLURAL = re.compile(r"\b(mon|tues|wednes|thurs|fri|satur|sun)days\b", re.IGNORECASE)
_DAY_WORD = re.compile(rf"\b({_DAY})\b", re.IGNORECASE)
_WEEK = re.compile(
    r"\b(?:until|till|til|through|thru|to|up to|ends?)\s+(?:the\s+end\s+of\s+)?"
    r"(?:week|wk)\s*(\d{1,2})\b",
    re.IGNORECASE,
)
_FOR_WEEKS = re.compile(r"\bfor\s+(?:the\s+next\s+)?(\d{1,2})\s+weeks?\b", re.IGNORECASE)
_UNTIL = re.compile(
    r"\b(?:until|till|til|through|thru)\s+(\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}(?:,?\s+\d{4})?"
    r"|[a-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?"
    r"|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


@dataclass
class Recurrence:
    weekdays: list[int]  # 0 = Monday
    until: date | None  # None: the message did not say how long it runs

    def label(self) -> str:
        return "/".join(WEEKDAYS[d][:3].title() for d in self.weekdays)


def _days(text: str) -> set[int]:
    return {_STEM[w.lower().removesuffix("s").removesuffix("day")] for w in _DAY_WORD.findall(text)}


def weekdays_in(text: str) -> list[int]:
    """The weekdays a message says repeat, or [] when it names none as repeating."""
    found: set[int] = set()
    for match in _LIST.finditer(text):
        found |= _days(match[1])
    found |= {_STEM[m.lower()] for m in _PLURAL.findall(text)}
    return sorted(found)


def semester_start(reference: datetime) -> date | None:
    """First Monday of the semester that is running (or about to start) on `reference`."""
    today = reference.astimezone(SG).date()
    best = None
    for year in CALENDAR.values():
        for sem in year.values():
            start = date(*sem["start"])
            if start - timedelta(days=14) <= today and (best is None or start > best):
                best = start
    return best


def week_end(week: int, reference: datetime) -> date:
    """Last day of NUS teaching week `week`. The recess week sits between weeks 6 and 7."""
    start = semester_start(reference)
    if start is None:
        raise Clarification("I don't have this semester's calendar. Give an end date instead.")
    offset = week - 1 if week <= 6 else week
    return start + timedelta(weeks=offset, days=6)


def parse_recurrence(text: str, reference: datetime) -> Recurrence | None:
    days = weekdays_in(text)
    if not days:
        return None
    until = None
    if match := _WEEK.search(text):
        week = int(match[1])
        if not 1 <= week <= 13:
            raise Clarification("Teaching weeks run from 1 to 13. Which week does it end?")
        until = week_end(week, reference)
    elif match := _UNTIL.search(text):
        try:
            until = resolve_date(match[1], reference)
        except Clarification:
            until = None  # "till 10 tonight" is a time, not an end date: ask how long it runs
    elif match := _FOR_WEEKS.search(text):
        until = reference.astimezone(SG).date() + timedelta(weeks=int(match[1]))
    return Recurrence(days, until)


def first_date(weekdays: list[int], start_time, reference: datetime) -> date:
    """The next date on one of the weekdays whose start has not already passed."""
    now = reference.astimezone(SG)
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() in weekdays and datetime.combine(day, start_time, SG) > now:
            return day
    raise Clarification("I couldn't work out the first date. Send it again with the weekday.")


def session_dates(weekdays: list[int], first: date, until: date) -> list[date]:
    if until < first:
        raise Clarification("The end date is before the first session. Check the dates and resend.")
    if until > first + timedelta(weeks=MAX_WEEKS):
        raise Clarification(
            f"I can set up at most {MAX_WEEKS} weeks at a time. Pick an earlier end."
        )
    days, day = [], first
    while day <= until:
        if day.weekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    if len(days) > MAX_SESSIONS:
        raise Clarification("That is a lot of sessions. Pick an earlier end date.")
    return days
