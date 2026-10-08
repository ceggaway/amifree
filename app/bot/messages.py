from dataclasses import dataclass

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import event_keyboard
from app.schemas.event import Event, EventDraft, Status
from app.services.availability import Availability

HELP = """Forward me a plan and I'll save it and tell you if it clashes.

1. Forward a text invitation with a date and time.
2. Check the card, then tap Save, Going, Maybe, or Can't make it.
3. Optional: send your NUSMods Share/Sync link so classes and exams are checked too.

I check your AmIFree Going plans, your busy blocks and any imported NUSMods timetable.
Outside calendars such as Google Calendar aren't connected, so "No clashes found"
only covers what AmIFree knows about.

Privacy: I only act on messages you send or forward to me, commands and button taps.
I never read group chats.

/start — getting started
/help — this guide
/plans — upcoming saved plans
/events — every recent card, including unsaved previews
/cancel — stop a pending clarification or edit
/deletemydata — delete everything AmIFree stores about you"""

STATUS_LABELS = {
    Status.PREVIEW: "Not saved yet",
    Status.SAVED: "Saved (not decided)",
    Status.GOING: "Going",
    Status.MAYBE: "Maybe",
    Status.DECLINED: "Can't make it",
}


@dataclass
class Reply:
    text: str
    keyboard: InlineKeyboardMarkup | None = None


def details(draft: EventDraft) -> str:
    result = f"{draft.title}\n{draft.start_at:%a, %d %b %Y · %H:%M} → {draft.end_at:%d %b %H:%M} (Singapore)"
    if draft.location:
        result += "\nLocation: " + draft.location
    if draft.notes:
        result += "\n" + draft.notes[:500]
    if draft.assumed_duration:
        duration = int((draft.end_at - draft.start_at).total_seconds() // 60)
        result += (
            f"\n⚠️ End time assumes a {duration}-minute duration. Review before choosing Going."
        )
    return result


def verdict_line(availability: Availability) -> str:
    """The first line of every card: clash, busy day, or no clashes."""
    if availability.conflicts:
        names = availability.conflicts[0].title[:100]
        if len(availability.conflicts) > 1:
            names += f" and {len(availability.conflicts) - 1} more"
        return "⚠️ Clashes with " + names
    if availability.busy_day:
        return f"ℹ️ Busy day: no clashes, but you have {availability.day_count} other plans that day"
    return "✅ No clashes found"


def event_card(event: Event, availability: Availability) -> Reply:
    if event.status == Status.CANCELLED:
        return Reply("Cancelled\n" + details(event))
    body = verdict_line(availability) + "\n\n" + details(event)
    if availability.conflicts:
        body += "\n\nConflicts:\n" + "\n".join(
            f"• {b.title[:150]}: {b.start_at:%d %b %H:%M}–{b.end_at:%H:%M}"
            for b in availability.conflicts[:5]
        )
        if len(availability.conflicts) > 5:
            body += f"\n+ {len(availability.conflicts) - 5} more"
        if availability.busy_day:
            body += f"\nℹ️ Busy day: {availability.day_count} other plans that day."
    if availability.possible:
        body += (
            "\n\nPossible overlap with "
            + ", ".join(e.title[:60] for e in availability.possible[:3])
            + ". Which will you attend?"
        )
    body += "\n\nChecked: your Going plans and busy blocks"
    body += (
        " and NUSMods timetable."
        if availability.covered
        else ". No NUSMods timetable covers this date."
    )
    body += "\nStatus: " + STATUS_LABELS[event.status]
    return Reply(body, event_keyboard(event, bool(availability.conflicts), availability.possible))
