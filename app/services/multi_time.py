"""Two or more times in one message: separate events ("and") or one event's options ("or").

The model lists the extra times in other_times; this splits them into one Extraction per time,
each then checked by validate_extraction like any other draft, so a time that isn't in the
message is never saved.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from app.schemas.event import EventDraft, Extraction
from app.services.date_resolution import Clarification, repeats_weekly, validate_extraction

MAX_TIMES = 5
_CHOICE = re.compile(
    r"\bor\b|\beither\b|\bwhichever\b|\bpick one\b|\bwhich (?:one|day|time)\b", re.I
)
# What the model writes as "ambiguity" about the times it already listed, not a question.
_LISTED = re.compile(
    r"alternativ|option|either|multiple|several|more than one|^\s*(?:one|separate)\s*$", re.I
)
# Between two days that share one set of hours: "Friday or Saturday", "Sat & Sun", "Mon/Tue".
_JOINED = re.compile(r"[\s,&/+]*(?:and|or|n|to)?[\s,&/+]*")
# The rest of a written range after its start: "-3pm" in "2-3pm".
_RANGE_END = r"\s*(?:-|–|—|to|till|until)\s*(\d{3,4}|\d{1,2}(?:[:.]\d{2})?(?:\s*[ap]\.?m)?)(?![\d])"


def settle_times(extraction: Extraction, text: str) -> Extraction:
    """Deterministic guards on the model's other_times, shared by the bot and the eval.

    - Times listed with relation "one" (gpt-4o-mini does this often) are still listed: "or",
      "either" or "whichever" in the message makes them choices, anything else separate events.
    - An ambiguity that only describes those listed times ("alternatives") is dropped.
    """
    if not extraction.other_times:
        return extraction
    update = {}
    if extraction.relation not in ("separate", "alternatives"):
        update["relation"] = "alternatives" if _CHOICE.search(text) else "separate"
    if extraction.ambiguity and _LISTED.search(extraction.ambiguity):
        update["ambiguity"] = None
    return extraction.model_copy(update=update)


def is_multi(extraction: Extraction, text: str) -> bool:
    """A fresh new-event message with more than one time the bot can offer in a picker.

    Expects an extraction that went through settle_times.
    """
    return (
        extraction.kind == "new"
        and extraction.relation in ("separate", "alternatives")
        and bool(extraction.other_times)
        # Ambiguity ("at 7", "every Tue") is asked about first, as for a single event.
        and not extraction.ambiguity
        # "Tuesdays and Thursdays 7-9pm" repeats every week: the recurring flows handle it.
        and not repeats_weekly(text)
    )


def fold_answer(extraction: Extraction, answer: bool = True) -> Extraction:
    """One draft. What the model put in other_times fills the draft's own empty fields
    instead of being lost: a clarification's "till 4pm", or a weekly post's "7-9pm" listed
    there instead of in the main fields (answer=False: a fresh message, not a reply)."""
    others = extraction.other_times or []
    update = {"relation": None, "other_times": None}
    if others:
        first = others[0]
        for field, value in first.model_dump().items():
            if value and getattr(extraction, field) is None:
                update[field] = value
        if (
            answer
            and extraction.start_time_text
            and not extraction.end_time_text
            and first.start_time_text
            and not first.end_time_text
        ):
            # "till 4pm" read as a new start: with a start already known, it is the end.
            update.pop("start_time_text", None)
            update["end_time_text"] = first.start_time_text
    return extraction.model_copy(update=update)


def _joined(before: str | None, after: str | None, source: str) -> bool:
    """Is after written right after before ("Sat & Sun"), so they share the hours?"""
    if not before or not after:
        return False
    text = source.casefold()
    start = text.find(before.casefold())
    if start < 0:
        return False
    end = text.find(after.casefold(), start + len(before))
    return end >= 0 and bool(_JOINED.fullmatch(text[start + len(before) : end]))


def _written_end(item: Extraction, source: str) -> Extraction:
    """A start with its end dropped ("2" from "Mon 19 Oct 2-3pm"): put back the written end."""
    if not item.start_time_text or item.end_time_text:
        return item
    text = source.casefold()
    after = text.find(item.date_text.casefold()) if item.date_text else -1
    match = re.compile(
        rf"(?<![\d:.]){re.escape(item.start_time_text.strip().casefold())}{_RANGE_END}"
    ).search(text, max(after, 0))
    return item.model_copy(update={"end_time_text": match[1]}) if match else item


def split_times(extraction: Extraction, source: str | None = None) -> list[Extraction]:
    """One Extraction per time. A field left null is taken from the time before it.

    With the message as source, a day with no hours of its own ("Sat & Sun 9am-6pm") only
    shares the hours when it is written next to the day before it, so a due date in meeting
    notes never becomes a 10pm event.
    """
    first = extraction.model_copy(update={"relation": None, "other_times": None})
    if first.end_date_text and any(
        other.date_text == first.end_date_text for other in extraction.other_times or []
    ):
        # "Hackathon Sat & Sun 9am-6pm": Sunday is the next session, not Saturday's end.
        first = first.model_copy(update={"end_date_text": None})
    items = [first]
    for other in (extraction.other_times or [])[: MAX_TIMES - 1]:
        before = items[-1]
        timed = other.start_time_text or other.end_time_text
        dated = other.date_text or other.end_date_text
        if (
            not timed
            and source is not None
            and not _joined(before.date_text, other.date_text, source)
        ):
            continue
        items.append(
            before.model_copy(
                update={
                    "title": other.title or before.title,
                    "date_text": other.date_text if dated else before.date_text,
                    "end_date_text": other.end_date_text if dated else before.end_date_text,
                    # A new start without an end ("Fri 7pm or Sat 6pm") gets its own end:
                    # the default duration, not the hours of the time before it. An end
                    # without a start ("Friday or Saturday 7-9pm" read as Sat "9pm") keeps
                    # the start before it.
                    "start_time_text": other.start_time_text or before.start_time_text,
                    "end_time_text": other.end_time_text if timed else before.end_time_text,
                    "location": other.location or before.location,
                }
            )
        )
    return items


@dataclass
class Times:
    drafts: list[EventDraft]
    unread: list[str]  # titles of the times that failed the date/time checks


def read_times(
    extraction: Extraction, source: str, reference: datetime, default_minutes: int = 0
) -> Times:
    drafts, unread = [], []
    for item in split_times(extraction, source):
        try:
            draft = validate_extraction(
                _written_end(item, source), source, reference, default_minutes=default_minutes
            )
        except Clarification:
            unread.append(item.title or "Event")
            continue
        if any(
            (d.start_at, d.end_at, d.title) == (draft.start_at, draft.end_at, draft.title)
            for d in drafts
        ):
            continue  # the model repeated the first time in other_times
        drafts.append(draft)
    return Times(_name_parts(drafts, source), unread)


def _label(title: str, source: str) -> bool:
    """Whether the message starts a line with this title as a label: "Full run: Thu 7pm"."""
    return bool(re.search(rf"(?m)^\s*{re.escape(title)}\s*:", source, re.IGNORECASE))


def _name_parts(drafts: list[EventDraft], source: str) -> list[EventDraft]:
    """Labelled parts of one named event all carry its name: the model writes "Grease
    blocking" for "Blocking: Tue 7pm" but sometimes just "Full run" for the next line."""
    if len(drafts) < 2:
        return drafts
    first = drafts[0].title
    for label in re.findall(r"(?m)^\s*([^\W\d][^:\n]{0,40}?)\s*:", source):
        if first.casefold().endswith(" " + label.casefold()):
            name = first[: -len(label) - 1]
            break
    else:
        return drafts
    return [drafts[0]] + [
        d.model_copy(update={"title": f"{name} {_lower_first(d.title)}"})
        if _label(d.title, source) and name.casefold() not in d.title.casefold()
        else d
        for d in drafts[1:]
    ]


def item_message_id(message_id: int, index: int) -> int:
    """Events are unique per source message, so each extra time gets its own negative id."""
    return message_id if index == 0 else -(message_id * 10 + index)


def _lower_first(title: str) -> str:
    """ "Full run" -> "full run", but "UCC tour" stays."""
    return title[0].lower() + title[1:] if title[1:2].islower() else title
