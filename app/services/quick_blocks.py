"""Deterministic quick blocks such as 'busy sat 2–6pm' or 'work every Wed 6–10pm'.

Recurrence never goes through the LLM extractor, which treats it as ambiguous.
"""

import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from app.models.timetable import BlockRule, BusyBlock
from app.schemas.event import SG, Extraction
from app.services.date_resolution import (
    WEEKDAYS,
    Clarification,
    resolve_date,
    validate_extraction,
)

HORIZON_DAYS = 90
MAX_RULES = 100
TIME = r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
RANGE = re.compile(rf"({TIME})\s*(?:-|–|—|to)\s*({TIME})", re.IGNORECASE)
EVERY = re.compile(r"\bevery\s+([a-z]+)", re.IGNORECASE)
UNTIL = re.compile(r"\b(?:until|till)\s+(.+)$", re.IGNORECASE)
USAGE = "Try: busy sat 2–6pm, or: work every Wed 6–10pm until 30 Nov"


def weekday_token(word: str) -> str | None:
    """Return the three-letter weekday for 'wed', 'weds', 'wednesday' and similar."""
    word = word.lower().rstrip("s") if word.lower() not in WEEKDAYS else word.lower()
    for full in WEEKDAYS:
        if len(word) >= 3 and full.startswith(word):
            return full[:3]
    return None


def parse_block(text: str, reference: datetime, *, explicit: bool = False) -> BlockRule | None:
    """Return a rule, None when the text isn't a quick block, or raise Clarification.

    Without `explicit` (the /block command) only short one-line messages that start with
    'busy' or contain 'every <weekday>' are claimed, so ordinary invitations still reach
    the event extractor.
    """
    text = " ".join(text.split()) if explicit else text.strip()
    weekly = EVERY.search(text)
    weekly_day = weekday_token(weekly[1]) if weekly else None
    if not explicit:
        claimed = re.match(r"busy\b", text, re.IGNORECASE) or weekly_day
        if not claimed or "\n" in text or len(text) > 120:
            return None
    until = None
    match = UNTIL.search(text)
    if match:
        until = resolve_date(match[1].strip(" ."), reference)
        text = text[: match.start()].strip()
    times = RANGE.search(text)
    if not times:
        raise Clarification(
            "What time is this block? Send the start and end, like 6:30-7pm or 18:30-19:00."
        )
    head = text[: times.start()].strip(" ,")
    if weekly_day:
        weekly = EVERY.search(head)
        if not weekly:
            raise Clarification("Put the day before the time. " + USAGE)
        title, date_text = head[: weekly.start()].strip(" ,"), weekly_day
    else:
        words = head.split()
        date_text = None
        for size in (3, 2, 1):
            candidate = " ".join(words[-size:])
            if len(words) >= size:
                try:
                    resolve_date(candidate, reference)
                except Clarification:
                    continue
                date_text, words = candidate, words[:-size]
                break
        if date_text is None:
            raise Clarification("Which day is this block? " + USAGE)
        title = " ".join(words)
    if not title or title.lower() == "busy":
        title = "Busy"
    elif title.lower().startswith("busy "):
        title = title[5:].strip()
    # Reuse the event date/time rules, including shared suffixes such as 2–6pm.
    draft = validate_extraction(
        Extraction(
            kind="new",
            title=title[:1].upper() + title[1:],
            date_text=date_text,
            start_time_text=times[1].strip(),
            end_time_text=times[2].strip(),
            end_date_text=None,
            location=None,
            notes=None,
            match_title=None,
            ambiguity=None,
        ),
        text,
        reference,
    )
    if not weekly_day:
        return BlockRule(
            title=draft.title, kind="ONCE", start_at=draft.start_at, end_at=draft.end_at
        )
    start_date = draft.start_at.date()
    if until is not None and until < start_date:
        raise Clarification("The end date is before the first occurrence. " + USAGE)
    return BlockRule(
        title=draft.title,
        kind="WEEKLY",
        weekday=start_date.weekday(),
        start_time=draft.start_at.time(),
        end_time=draft.end_at.time(),
        start_date=start_date,
        until_date=until or start_date + timedelta(days=HORIZON_DAYS),
    )


def expand_blocks(rules: Iterable[BlockRule], start: datetime, end: datetime) -> list[BusyBlock]:
    """Occurrences overlapping the half-open window [start, end)."""
    blocks = []
    for rule in rules:
        if rule.kind == "ONCE":
            if rule.start_at < end and rule.end_at > start:
                blocks.append(
                    BusyBlock(
                        title=rule.title,
                        start_at=rule.start_at,
                        end_at=rule.end_at,
                        source="MANUAL",
                    )
                )
            continue
        day = max(rule.start_date, start.astimezone(SG).date())
        day += timedelta(days=(rule.weekday - day.weekday()) % 7)
        last = end.astimezone(SG).date()
        if rule.until_date is not None:
            last = min(last, rule.until_date)
        while day <= last:
            block_start = datetime.combine(day, rule.start_time, SG)
            block_end = datetime.combine(day, rule.end_time, SG)
            if block_start < end and block_end > start:
                blocks.append(
                    BusyBlock(
                        title=rule.title, start_at=block_start, end_at=block_end, source="MANUAL"
                    )
                )
            day += timedelta(days=7)
    return blocks


def describe(rule: BlockRule) -> str:
    if rule.kind == "ONCE":
        start, end = rule.start_at.astimezone(SG), rule.end_at.astimezone(SG)
        return f"{rule.title}\n{start:%a, %d %b %Y · %H:%M} → {end:%H:%M} SGT"
    text = (
        f"{rule.title}\nEvery {WEEKDAYS[rule.weekday].capitalize()} "
        f"{rule.start_time:%H:%M} → {rule.end_time:%H:%M} SGT\n"
        f"From {rule.start_date:%d %b %Y}"
    )
    if rule.until_date:
        text += f" until {rule.until_date:%d %b %Y}"
    return text
