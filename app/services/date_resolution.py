import logging
import re
from datetime import date, datetime, time, timedelta

from dateutil import parser

from app.schemas.event import SG, EventDraft, Extraction

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Clarification(ValueError):
    """Expected input gap, displayed to the user without a stack trace."""


RELATIVE = {
    "today": 0,
    "tdy": 0,
    "tonight": 0,
    "tonite": 0,
    "tomorrow": 1,
    "tmr": 1,
    "tmrw": 1,
    "tml": 1,
    "later today": 0,
    "this morning": 0,
    "this afternoon": 0,
    "this evening": 0,
    "this eve": 0,
    "today night": 0,
    "tomorrow morning": 1,
    "tomorrow afternoon": 1,
    "tomorrow evening": 1,
    "tomorrow night": 1,
    "tmr morning": 1,
    "tmr afternoon": 1,
    "tmr evening": 1,
    "tmr night": 1,
    "day after tomorrow": 2,
}
_WEEKDAY_ABBR = {s: n for n, full in enumerate(WEEKDAYS) for s in (full, full[:3])}
_WEEKDAY_ABBR.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})


_PLURAL_DAY = re.compile(r"\b(mon|tues|wednes|thurs|fri|satur|sun)days\b", re.IGNORECASE)
_STEM = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "wednes": 2, "weds": 2, "thu": 3, "thur": 3}
_STEM |= {"thurs": 3, "fri": 4, "sat": 5, "satur": 5, "sun": 6}


# A whole day name or a usual short form, never part of a word ("every month" is not Monday).
_DAY_NAME = (
    r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:s|nesday)?|thu(?:r(?:s(?:day)?)?)?"
    r"|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
)
_DAY_WORD = _DAY_NAME + r"\b"
# "every Tue/Thu", "every Tuesday and Thursday", "each Mon, Wed & Fri".
_EVERY_LIST = re.compile(
    rf"\b(?:every|each)\s+({_DAY_WORD}(?:\s*(?:/|,|&|\+|and)\s*{_DAY_WORD})*)", re.IGNORECASE
)


# Days listed together: "Wednesday and Thursdays", "Mon/Weds". One plural day in the list
# makes all of them weekly; "Wednesdayand" (a missing space) still counts as a list.
_ANY_DAY = rf"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)days\b|{_DAY_NAME}(?=\b|and\b))"
_DAY_LIST = re.compile(rf"\b{_ANY_DAY}(?:\s*(?:/|,|&|\+|and\b)\s*{_ANY_DAY})+", re.IGNORECASE)


def weekly_weekdays(text: str) -> list[int]:
    """The weekdays of a weekly event ("Saturdays", "every Tue/Thu"), Monday first."""
    days = {_STEM[m.lower()] for m in _PLURAL_DAY.findall(text)}
    for match in _DAY_LIST.finditer(text):
        if _PLURAL_DAY.search(match[0]):
            for day in re.findall(rf"\b{_ANY_DAY}", match[0], re.IGNORECASE):
                days.add(_WEEKDAY_ABBR[day.lower()[:3]])
    for listed in _EVERY_LIST.findall(text):
        for day in re.findall(rf"\b{_DAY_WORD}", listed, re.IGNORECASE):
            days.add(_WEEKDAY_ABBR[day.lower()[:3]])
    return sorted(days)


def next_weekday_name(weekdays: list[int], reference: datetime) -> str:
    """The day name of whichever weekday comes first: "Thursday" for Tue/Thu on a Wednesday."""
    names = [WEEKDAYS[d].title() for d in weekdays]
    return min(names, key=lambda name: resolve_date(name, reference))


def weekly_weekday(text: str) -> int | None:
    """The weekday of a weekly event on one day only ("Saturdays", "every Tue"), or None."""
    days = weekly_weekdays(text)
    return days[0] if len(days) == 1 else None


_WEEKLY = re.compile(r"\b(?:weekly|every\s+week|each\s+week)\b", re.IGNORECASE)


def repeats_weekly(text: str) -> bool:
    """Any weekly wording ("Tuesdays and Thursdays", "every Tue", "weekly")."""
    return bool(_PLURAL_DAY.search(text) or _EVERY_LIST.search(text) or _WEEKLY.search(text))


def resolve_date(raw: str, reference: datetime) -> date:
    raw = raw.strip().lower()
    today = reference.astimezone(SG).date()
    # "15/10 (Thu)" -> "Thu 15/10"; "on the 17th" -> "17th"
    paren = re.fullmatch(r"(.+?)\s*\(([a-z]+)\)", raw)
    if paren:
        raw = f"{paren[2]} {paren[1]}"
    raw = re.sub(r"^(?:on\s+)?(?:the\s+)?", "", raw).strip(" ,.")
    if raw in RELATIVE:
        return today + timedelta(days=RELATIVE[raw])
    # A weekday next to a full date must agree, in either order: "Tue 13 Oct", "22nd October, Thursday".
    before = re.fullmatch(r"([a-z]+),?\s+(\d.*|[a-z]{3,9}\s+\d.*)", raw)
    after = re.fullmatch(r"(\d.*?|[a-z]{3,9}\s+\d.*?)[,\s]+([a-z]+)", raw)
    combo = None
    if before and before[1] in _WEEKDAY_ABBR:
        combo = (before[1], before[2])
    elif after and after[2] in _WEEKDAY_ABBR:
        combo = (after[2], after[1])
    if combo:
        weekday, rest = combo
        resolved = resolve_date(rest, reference)
        if resolved.weekday() != _WEEKDAY_ABBR[weekday]:
            raise Clarification(
                f"{resolved:%d %b} is a {resolved:%A}, not a {WEEKDAYS[_WEEKDAY_ABBR[weekday]].title()}. Which date is it?"
            )
        return resolved
    # A bare day of the month ("17th"): the next time that day comes round.
    ordinal = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", raw)
    if ordinal:
        day, month, year = int(ordinal[1]), today.month, today.year
        for _ in range(3):
            try:
                candidate = date(year, month, day)
            except ValueError:
                candidate = None
            if candidate and candidate >= today:
                return candidate
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
        raise Clarification("Please send a valid event date, including the month.")
    match = re.fullmatch(r"(?:(this|next|coming)\s+)?([a-z]+)", raw)
    if match:
        prefix, day = match.groups()
        weekdays = _WEEKDAY_ABBR
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
    value = raw.lower().replace(" ", "")
    value = re.sub(r"(?:hrs|hr|h)$", "", value)  # 1900hrs
    if value in ("noon", "12noon", "midday"):
        return time(12, 0)
    if value in ("midnight", "12midnight"):
        return time(0, 0)
    value = re.sub(r"([ap])\.?m\.?$", r"\1m", value)  # 7p.m. -> 7pm
    m = re.fullmatch(r"(\d{1,2})(?:([:.])(\d{2}))?(am|pm)?", value)
    compact = None if m else re.fullmatch(r"(\d{1,2})(\d{2})(am|pm)?", value)
    if not (m or compact):
        raise Clarification("Please send the time as 7pm or 19:00.")
    if m:
        h, separator, minute, ampm = int(m[1]), m[2], int(m[3] or 0), m[4] or meridiem
    else:
        # 730pm, 1930, 0800. Four digits without am/pm are 24-hour time.
        h, minute, ampm = int(compact[1]), int(compact[2]), compact[3]
        separator = ":" if len(value.rstrip("apm")) == 4 and not ampm else "."
        ampm = ampm or (meridiem if separator == "." else None)
    if minute > 59:
        raise Clarification("Please send a valid time.")
    if ampm:
        if not 1 <= h <= 12:
            raise Clarification("Please send a valid AM/PM time.")
        h = h % 12 + (12 if ampm == "pm" else 0)
    elif separator != ":" and h <= 12:
        # "7", "7.30" or "730" with no am/pm anywhere: ask rather than guess.
        raise Clarification("Is that AM or PM? Send, for example, 7pm.")
    if not 0 <= h <= 23:
        raise Clarification("Please send a valid time.")
    return time(h, minute)


_DATE_ALIASES = [
    (r"\b(?:tomorrow|tmrw|tml)\b", "tmr"),
    (r"\b(?:today|tdy)\b", "today"),
    *[(rf"\b{full}\b", full[:3]) for full in WEEKDAYS],
    (r"\b(tue|wed)s\b", r"\1"),
    (r"\b(thu)rs?\b", r"\1"),
]


def _canonical(text: str) -> str:
    text = text.casefold()
    for pattern, replacement in _DATE_ALIASES:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?<=\d)[:.](?=\d{2})", "", text)  # 7:30 / 7.30 -> 730
    text = re.sub(r"(?<=\d)\s+(?=[ap]\.?m\b)", "", text)  # 11 pm -> 11pm
    return re.sub(r"\s+", " ", text).strip()


def _contains(haystack: str, needle: str) -> bool:
    """Whole-token match: "Wed" is not in "Wedding", "7" is not in "17", "730" is in "730pm"."""
    if not needle:
        return False
    before = r"(?<![0-9])" if needle[0].isdigit() else r"(?<![a-z])" if needle[0].isalpha() else ""
    after = r"(?![0-9])" if needle[-1].isdigit() else r"(?![a-z])" if needle[-1].isalpha() else ""
    return bool(re.search(before + re.escape(needle) + after, haystack))


def in_source(value: str, source: str) -> bool:
    """True if the model's date/time text is in the message, allowing harmless rewording.

    Accepted: "tomorrow" for "tmr", "Saturday" for "Sat", "7:30pm" for "730pm",
    "14:00" for "1400". Never accepted: anything that adds information, such as a year.
    """
    if _contains(source.casefold(), value.strip().casefold()):
        return True
    if _contains(_canonical(source), _canonical(value)):
        return True
    return _shared_suffix_start(value, source)


_NUM = r"(?:\d{3,4}|\d{1,2}(?:[:.]\d{2})?)"
_SEP = r"\s*(?:-|–|—|to|till|until)\s*"


def _shared_suffix_start(value: str, source: str) -> bool:
    """'2pm' for a message that says '2-4pm': the model copied the range's pm onto the start."""
    m = re.fullmatch(r"(\d{1,2})(?:[:.]?(\d{2}))?\s*([ap])\.?m", value.strip().casefold())
    if not m:
        return False
    hour, minutes, ampm = m[1], m[2] or "00", m[3]
    start = rf"{hour}(?:[:.]?{minutes})?" if minutes == "00" else rf"{hour}[:.]?{minutes}"
    pattern = rf"(?<![\d:.]){start}{_SEP}{_NUM}\s*{ampm}\.?m\b"
    return bool(re.search(pattern, source.casefold()))


_RANGE = re.compile(
    rf"(?<![\d:./-])({_NUM})\s*([ap]\.?m|hrs)?{_SEP}({_NUM})\s*([ap]\.?m|hrs)?(?![\d/-])",
    re.IGNORECASE,
)


def time_ranges(text: str) -> set:
    """Distinct written time ranges such as 7-9pm, 11am-2pm, 1400 – 1500, 19:00 - 22:00."""
    found = set()
    for m in _RANGE.finditer(text):
        a, a_suffix, b, b_suffix = m.groups()
        clock = a_suffix or b_suffix or ":" in a + b or (len(a) >= 3 and len(b) >= 3)
        if clock:
            # The same hours written two ways ("7-9pm", "7:00 PM – 9:00 PM": a caption and the
            # poster's own text) are one range. Fall back to the written form if unreadable.
            try:
                key = (
                    resolve_time(a + (a_suffix or b_suffix or "")),
                    resolve_time(b + (b_suffix or a_suffix or "")),
                )
            except Clarification:
                key = re.sub(r"\s+", "", m[0].casefold())
            found.add(key)
    return found


def asked_field(question: str) -> str | None:
    """Which detail a clarifying question asked for, so the reply can fill it directly."""
    if question.startswith("What time does this event end"):
        return "end"
    if question.startswith("What time does this event start"):
        return "start"
    if question.startswith(("What date is this event", "What is the event date")):
        return "date"
    if "What's the due date?" in question:
        return "date"
    return None


_ANSWER_LEAD = re.compile(
    r"^(?:it\s+)?(?:(?:ends?|starts?)(?:\s+at)?|till|until|to|at|from|on)\s+", re.I
)


def infer_meridiem(previous: Extraction, asked: str | None, reply: str) -> str:
    """A bare "8" after a 7pm start means 8pm: add the am/pm that puts the end after the start."""
    answer = _ANSWER_LEAD.sub("", reply.strip().strip(".!")).strip()
    if asked != "end" or not re.fullmatch(r"\d{1,2}(?:\.\d{2})?", answer):
        return reply
    try:
        start = resolve_time(previous.start_time_text or "")
    except Clarification:
        return reply
    for suffix in ("am", "pm"):
        try:
            if resolve_time(answer + suffix) > start:
                return f"{reply.strip()}{suffix}"
        except Clarification:
            continue
    return reply


def fill_answer(previous: Extraction, asked: str, reply: str, reference: datetime):
    """Fill the asked-for detail from a short reply ("8PM", "2000", "7-9pm", "15 oct").

    Returns the completed extraction, or None when the reply doesn't simply answer the
    question; the caller then lets the language model read it as before.
    """
    answer = _ANSWER_LEAD.sub("", reply.strip().strip(".!")).strip()
    if not answer or len(answer) > 40:
        return None
    if asked in ("start", "end"):
        both = re.fullmatch(rf"(.+?){_SEP}(.+)", answer)
        try:
            if both:
                resolve_time(both[2])  # the end must be a real time; the start may borrow its am/pm
                if not re.fullmatch(rf"{_NUM}\s*(?:[ap]\.?m\.?|hrs?)?|noon", both[1].strip(), re.I):
                    return None
                return previous.model_copy(
                    update={
                        "start_time_text": both[1].strip(),
                        "end_time_text": both[2].strip(),
                        "ambiguity": None,
                    }
                )
            resolve_time(answer)
        except Clarification:
            return None
        field = "end_time_text" if asked == "end" else "start_time_text"
        return previous.model_copy(update={field: answer, "ambiguity": None})
    if asked == "date":
        try:
            resolve_date(answer, reference)
        except Clarification:
            return None
        return previous.model_copy(update={"date_text": answer, "ambiguity": None})
    return None


def single_event_guard(text: str) -> None:
    """A fresh message with two different time ranges is almost always two events."""
    found = time_ranges(text)
    if len(found) >= 2:
        # Only the matched times are logged, never the message.
        logging.getLogger(__name__).warning("single_event_guard ranges=%s", sorted(map(str, found)))
        raise Clarification(
            "That message has more than one event. Send me the one you want to save, "
            "for example: Bake sale Wed 11am-2pm."
        )


# Where the name of a plan stops: a day, a relative day, a date or time number, or "at"/"on".
_TITLE_END = re.compile(
    r"\b(?:(?:mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)(?:day)?s?"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
    r"|today|tonight|tdy|tmr|tmrw|tml|tomorrow|this|next|every|each|on|at|from)\b"
    r"|(?<![A-Za-z0-9])\d|@",  # "CS3244" stays a name; "16/10" and "7pm" end it
    re.IGNORECASE,
)
# Chatty openers that are not part of the name: "eh guys makan tmr" is "Makan".
_TITLE_LEAD = re.compile(
    r"^(?:(?:eh|hey|hi|hello|yo|oi|guys|all|everyone|so|ok|okay|reminder|let'?s|lets|jio)\b"
    r"[\s,!:.-]*)+",
    re.IGNORECASE,
)


def title_from_text(source: str) -> str:
    """A name for a plan the model left untitled: the words before its first day or time.

    "Training Tue 7-9pm and Thu 6-8pm" is "Training". "Event" only when the message
    starts with the time, or the opening is too long to be a name.
    """
    line = next((s.strip() for s in source.splitlines() if s.strip()), "")
    end = _TITLE_END.search(line)
    head = _TITLE_LEAD.sub("", line[: end.start()] if end else line)
    head = re.sub(r"[^\w&'+/()#-]+$", "", re.sub(r"^\W+", "", head)).strip()
    if not head or len(head.split()) > 6:
        return "Event"
    return head[:1].upper() + head[1:]


def _bare_meridiem_end(extraction: Extraction) -> Extraction:
    """The model split "4pm" into start "4" and end "pm": the pm belongs to the start."""
    end = (extraction.end_time_text or "").strip().casefold().replace(".", "")
    start = extraction.start_time_text
    if end not in ("am", "pm") or not start:
        return extraction
    if not re.search(r"[ap]\.?m", start, re.I):
        start = start.strip() + end
    return extraction.model_copy(update={"start_time_text": start, "end_time_text": None})


def as_written(raw: str | None, source: str) -> str | None:
    if not raw or _contains(source.casefold(), raw.strip().casefold()):
        return raw
    for a, b in ((":", "."), (".", ":")):
        swapped = raw.strip().replace(a, b)
        if swapped != raw.strip() and _contains(source.casefold(), swapped.casefold()):
            return swapped
    return raw


def restore_meridiem(raw: str | None, source: str) -> str | None:
    if not raw or re.search(r"[ap]\.?m|noon|midnight|hrs?$", raw, re.I):
        return raw
    wanted = _canonical(raw)
    match = re.search(rf"(?<![\d:.]){re.escape(wanted)}\s*([ap])\.?m\b", _canonical(source))
    return f"{raw.strip()}{match[1]}m" if match else raw


_DAY = (
    r"(?:today|tomorrow|tmr|tmrw|this|mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
)
# Explicit phrases only. A bare "night" or "evening" is often part of a title ("Games Night").
_DAY_PARTS = (
    (re.compile(rf"\b(?:{_DAY}\s+morning|in the morning)\b", re.I), "am", range(5, 12)),
    (
        re.compile(rf"\b(?:{_DAY}\s+afternoon|in the afternoon)\b", re.I),
        "pm",
        (12, 1, 2, 3, 4, 5, 6),
    ),
    (
        re.compile(
            rf"\b(?:tonight|tonite|{_DAY}\s+(?:evening|eve|night)|in the evening|at night)\b", re.I
        ),
        "pm",
        range(5, 12),
    ),
)


def day_part_meridiem(source: str, hour: int | None = None) -> str | None:
    """ "7 tonight" means 7pm and "9 tmr morning" means 9am.

    With `hour`, only when that hour fits the part: "11 this afternoon" is left to be asked."""
    for pattern, meridiem, hours in _DAY_PARTS:
        if pattern.search(source) and (hour is None or hour in hours):
            return meridiem
    return None


_OTHER_TIMEZONE = re.compile(
    r"\b(?:uk|us|usa|eastern|pacific|central|london|european|japan|korea|india|australia|"
    r"est|edt|pst|pdt|cst|cet|cest|bst|gmt|utc|jst|kst|aest)\s+time\b"
    r"|\b(?:gmt|utc|est|edt|pst|pdt|cet|cest|bst)\b",
    re.IGNORECASE,
)


def _spoken_noon(extraction: Extraction, source: str) -> Extraction:
    """The model turns "noon" into "12", which is not in the message and fails verification."""
    update = {}
    for field in ("start_time_text", "end_time_text"):
        value = (getattr(extraction, field) or "").strip()
        if value in ("12", "12:00", "12.00") and not in_source(value, source):
            word = re.search(r"\b(noon|midday)\b", source, re.I)
            if word:
                update[field] = word[1].lower()
    return extraction.model_copy(update=update) if update else extraction


def _with_day_part(raw: str | None, source: str) -> str | None:
    """A bare hour gets the day part's am/pm ("7" + "tonight" -> "7pm"); anything else is kept."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.]\d{2})?\s*", raw or "")
    if not m:
        return raw
    part = day_part_meridiem(source, int(m[1]))
    return raw.strip() + part if part else raw


_AM_OR_PM = re.compile(r"\bam\b.*\bpm\b|\bpm\b.*\bam\b|am/pm|a\.m\.", re.IGNORECASE)


_RELATIVE_WORD = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in sorted(RELATIVE, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _written_relative(extraction: Extraction, source: str, reference: datetime) -> Extraction:
    """The model converted "tmr" to "13 Oct": use the word the sender wrote, if it is the same day.

    Only when the converted date isn't in the message and every relative word in it ("tmr",
    "tonight") means that one day, so a real date is never swapped for a guess.
    """
    value = extraction.date_text
    if not value or in_source(value, source):
        return extraction
    written = _RELATIVE_WORD.findall(source)
    try:
        day = resolve_date(value, reference)
        if not written or {resolve_date(w, reference) for w in written} != {day}:
            return extraction
    except Clarification:
        return extraction
    return extraction.model_copy(update={"date_text": written[0]})


def validate_extraction(
    extraction: Extraction,
    source: str,
    reference: datetime,
    *,
    default_minutes=0,
    existing: EventDraft | None = None,
) -> EventDraft:
    if (
        extraction.ambiguity
        and _AM_OR_PM.search(extraction.ambiguity)
        and _with_day_part(extraction.start_time_text, source) != extraction.start_time_text
    ):
        # "dinner at 7 tonight": the day part already says pm.
        extraction = extraction.model_copy(update={"ambiguity": None})
    if extraction.ambiguity:
        raise Clarification("Please clarify the event date and time: " + extraction.ambiguity[:200])
    if _OTHER_TIMEZONE.search(source):
        raise Clarification(
            "That time looks like it is in another timezone. Send me the Singapore time, "
            "for example: Call Friday 8-9pm."
        )
    extraction = _spoken_noon(extraction, source)
    extraction = _bare_meridiem_end(extraction)
    extraction = _written_relative(extraction, source, reference)
    for field in ("date_text", "start_time_text", "end_time_text", "end_date_text"):
        value = getattr(extraction, field)
        if value and not in_source(value, source):
            raise Clarification(
                "I couldn't verify the date/time in your message. Please send the exact date and start/end times."
            )
    # The model sometimes drops a written am/pm ("6:30pm" -> "6:30"), which would turn an
    # evening into a morning. Put it back from the message itself.
    # Use the separator the sender wrote: "6.30" (12-hour, shares the range's pm) must not
    # become "6:30" (24-hour, 6:30am) just because the model swapped the dot for a colon.
    extraction = extraction.model_copy(
        update={
            "start_time_text": as_written(extraction.start_time_text, source),
            "end_time_text": as_written(extraction.end_time_text, source),
        }
    )
    # The model sometimes puts the whole range in the start field ("4–6pm").
    split = re.fullmatch(rf"(.+?){_SEP}(.+)", extraction.start_time_text or "")
    if split:
        extraction = extraction.model_copy(
            update={
                "start_time_text": split[1],
                "end_time_text": extraction.end_time_text or split[2],
            }
        )
    extraction = extraction.model_copy(
        update={
            "start_time_text": restore_meridiem(extraction.start_time_text, source),
            "end_time_text": restore_meridiem(extraction.end_time_text, source),
        }
    )
    extraction = extraction.model_copy(
        update={
            "start_time_text": _with_day_part(extraction.start_time_text, source),
            "end_time_text": _with_day_part(extraction.end_time_text, source),
        }
    )
    if not extraction.date_text and existing is None:
        raise Clarification("What date is this event?")
    if not extraction.start_time_text and existing is None:
        raise Clarification("What time does this event start? For example 7pm or 19:00.")
    d = (
        resolve_date(extraction.date_text, reference)
        if extraction.date_text
        else existing.start_at.date()
    )
    end_meridiem_match = re.search(
        r"(am|pm)$", (extraction.end_time_text or "").replace(" ", "").lower()
    )
    start_raw = extraction.start_time_text
    # "7-9pm", "6.30 to 8pm", "730-9pm": the start borrows the end's am/pm.
    shared_suffix = bool(
        start_raw
        and re.fullmatch(r"\d{1,2}(?:[:.]?\d{2})?", start_raw.strip())
        # "07:00" and "00:30" are 24-hour; "7:30" is not.
        and 1 <= int(re.match(r"\d{1,2}", start_raw.strip())[0]) <= 12
        and not re.match(r"0\d[:.]", start_raw.strip())
    )
    start_time = (
        resolve_time(
            start_raw, end_meridiem_match[1] if shared_suffix and end_meridiem_match else None
        )
        if start_raw
        else existing.start_at.timetz().replace(tzinfo=None)
    )
    end_time = resolve_time(extraction.end_time_text) if extraction.end_time_text else None
    # Shared suffix in ranges: 11–1pm means 11am to 1pm.
    if (
        shared_suffix
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
        if end_time == time(0, 0) and not extraction.end_date_text and end <= start:
            end += timedelta(days=1)  # "10pm-12am" ends at midnight, not before it started
    elif existing:
        end = start + (existing.end_at - existing.start_at)
        assumed = existing.assumed_duration
    elif default_minutes:
        end = start + timedelta(minutes=default_minutes)
        assumed = True
    else:
        raise Clarification("What time does this event end? For example 9pm or 21:00.")
    if end <= start:
        raise Clarification(
            "The end time must follow the start. For an overnight event, include the end date."
        )
    if (end - start).total_seconds() > 7 * 86400:
        raise Clarification(
            "Please clarify the start and end dates; events longer than seven days aren't supported."
        )
    return EventDraft(
        title=(extraction.title or (existing.title if existing else title_from_text(source)))[:200],
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
