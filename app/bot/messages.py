from dataclasses import dataclass

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import event_keyboard, group_keyboard
from app.schemas.event import Event, EventDraft, GroupEvent, Status
from app.services.availability import Availability

HELP = """Forward me a plan and I'll save it and tell you if it clashes.

1. Forward a text invitation with a date and time.
2. Check the card, then tap Save, Going, Maybe, or Can't make it.
3. Optional: send your NUSMods Share/Sync link so classes and exams are checked too.

I check your AmIFree Going plans, your busy blocks and any imported NUSMods timetable.
Outside calendars such as Google Calendar aren't connected, so "No clashes found"
only covers what AmIFree knows about.

In a group: reply /event to a plan message to post a card with Going / Maybe / Can't,
a private "Am I free?" check and a headcount. /whosfree <what> suggests times from
the schedules of members who opt in.

Privacy: I only act on messages you send or forward to me, commands and button taps.
I never read group chats, and your schedule is never shown to a group.

/start — getting started
/help — this guide
/plans — upcoming saved plans
/block — mark yourself busy, e.g. "busy sat 2–6pm" or "work every Wed 6–10pm"
/blocks — list or remove busy blocks
/reminders — turn the night-before summary on or off
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


def private_verdict(availability: Availability, prefix: str = "") -> str:
    """A short popup for one member; Telegram limits callback alerts to 200 characters."""
    note = "\nOnly you can see this."
    text = (prefix + "\n" if prefix else "") + verdict_line(availability)
    if availability.conflicts:
        first = availability.conflicts[0]
        text += f" ({first.start_at:%H:%M}–{first.end_at:%H:%M})"
    if availability.possible:
        text += f"\nPossible overlap with {availability.possible[0].title[:40]}."
    return text[: 200 - len(note)] + note


def group_card(event: GroupEvent, counts: dict[str, int]) -> Reply:
    """The shared card: details and a headcount, never anyone's schedule or name."""
    if event.status == "CANCELLED":
        return Reply("❌ Cancelled\n" + details(event))
    body = (
        "📅 "
        + details(event)
        + f"\n\nGoing {counts.get('GOING', 0)} · Maybe {counts.get('MAYBE', 0)}"
        + f" · Can't {counts.get('DECLINED', 0)}"
        + "\nAm I free? shows a private check that only you can see."
    )
    return Reply(body, group_keyboard(event))


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
