from dataclasses import dataclass

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import event_keyboard
from app.schemas.event import Event, EventDraft, Status
from app.services.availability import Availability

HELP = """Forward me an event and I'll tell you if you're free.

1. Send your NUSMods Share/Sync link.
2. Forward a text invitation with a date and time.
3. Review the details, then tap Going, Maybe, or Can't make it.

I check your imported classes, exams and AmIFree Going commitments.
Google Calendar and group scheduling aren't connected in this version.

/start — setup or readiness
/help — this guide
/events — recent event cards
/cancel — stop a pending clarification or edit
/delete — delete your timetable, events and analytics"""


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


def event_card(event: Event, availability: Availability) -> Reply:
    if event.status == Status.CANCELLED:
        return Reply("Cancelled\n" + details(event))
    heading = {
        "FREE": "✅ You're free",
        "CLASH": "⚠️ Clash detected",
        "UNKNOWN": "⚠️ Timetable coverage unavailable for this date",
    }[availability.verdict]
    body = heading + "\n\n" + details(event)
    if availability.conflicts:
        body += "\n\nConflicts:\n" + "\n".join(
            f"• {b.title[:150]}: {b.start_at:%d %b %H:%M}–{b.end_at:%H:%M}"
            for b in availability.conflicts[:5]
        )
        if len(availability.conflicts) > 5:
            body += f"\n+ {len(availability.conflicts) - 5} more"
    if not availability.covered:
        body += "\nThis is outside your imported semester. Only Going commitments can be checked."
    body += "\n\nStatus: " + event.status.value.capitalize()
    return Reply(body, event_keyboard(event, bool(availability.conflicts)))
