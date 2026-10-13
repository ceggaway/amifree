from dataclasses import dataclass
from datetime import datetime

from telegram import InlineKeyboardMarkup

from app.bot.keyboards import event_keyboard, group_keyboard
from app.schemas.event import SG, Event, EventDraft, GroupEvent, Status
from app.services.availability import Availability

HELP = """Forward me a plan and I'll save it and tell you if it clashes.

1. Forward a text invitation with a date and time.
   Or ask: /free fri 7-9pm, /free tomorrow.
2. Check the card, then tap Save, Going, Maybe, or Can't make it.
3. Optional: send your NUSMods Share/Sync link so classes and exams are checked too.

I check your AmIFree Going plans, your busy blocks, any imported NUSMods timetable and,
if you connect it with /calendar, your own calendar's busy times. "No clashes found"
only covers what AmIFree knows about.

/calendar puts every Going and Saved plan in your iPhone calendar automatically, and lets
you connect your own calendar so its busy times count too.

In a group: reply /event to a plan message to post a card with Going / Maybe / Can't,
a private "Am I free?" check and a headcount. /whosfree <what> suggests times from
the schedules of members who opt in.

Privacy: I only act on messages you send or forward to me, commands and button taps. Pictures you forward are read for their text and not kept.
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
    # (filename, content) sent as a Telegram document with text as its caption.
    document: tuple[str, bytes] | None = None
    # Replace the card whose button was tapped instead of sending a new message.
    edit: bool = False


def details(draft: EventDraft) -> str:
    result = (
        f"{draft.title}\n{draft.start_at:%a, %d %b %Y · %H:%M} → {draft.end_at:%d %b %H:%M} SGT"
    )
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


def clock(value: datetime) -> str:
    """7pm, 7:30pm: the way people write times."""
    value = value.astimezone(SG)
    return f"{value:%-I:%M%p}".replace(":00", "").lower()


def time_range(start: datetime, end: datetime) -> str:
    """7–9pm, 11am–1pm, 7:30–8pm."""
    a, b = clock(start), clock(end)
    if a[-2:] == b[-2:]:
        a = a[:-2]
    return f"{a}–{b}"


def duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if not hours:
        return f"{mins} min"
    return f"{hours} hr" + (f" {mins} min" if mins else "")


def clash_detail(candidate: EventDraft, busy: EventDraft) -> str:
    """How much of the plan a clash takes, so the student can decide to come late or leave early.

    It is still a clash either way: a partial overlap is never reported as free.
    """
    title = busy.title[:80]
    starts_before = busy.start_at <= candidate.start_at
    ends_after = busy.end_at >= candidate.end_at
    if starts_before and ends_after:
        return f"Clashes with {title} (the whole time)"
    if starts_before:
        missed = int((busy.end_at - candidate.start_at).total_seconds() // 60)
        return (
            f"Clashes with {title}: it ends at {clock(busy.end_at)}, "
            f"so you'd miss the first {duration(missed)}"
        )
    if ends_after:
        missed = int((candidate.end_at - busy.start_at).total_seconds() // 60)
        return (
            f"Clashes with {title}: you'd need to leave at {clock(busy.start_at)} "
            f"(the last {duration(missed)})"
        )
    return f"Clashes with {title} from {time_range(busy.start_at, busy.end_at)}"


def checked_line(availability: Availability) -> str:
    """What the answer is based on: "No clashes found" only covers what AmIFree knows."""
    nusmods = availability.covered if availability.nusmods is None else availability.nusmods
    checked = ["your Going plans", "busy blocks"]
    if availability.personal:
        checked.append("personal calendar")
    if nusmods:
        checked.append("NUSMods timetable")
    line = "Checked: " + ", ".join(checked[:-1]) + " and " + checked[-1] + "."
    return line if nusmods else line + " No NUSMods timetable covers this date."


def verdict_line(availability: Availability) -> str:
    """The first line of every card: clash, busy day, or no clashes."""
    if availability.conflicts:
        if len(availability.conflicts) == 1 and availability.candidate is not None:
            return "⚠️ " + clash_detail(availability.candidate, availability.conflicts[0])
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
    if len(availability.conflicts) > 1 or (availability.conflicts and not availability.candidate):
        first = availability.conflicts[0]
        text += f" ({time_range(first.start_at, first.end_at)})"
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


def event_card(event: Event, availability: Availability, synced: bool = False) -> Reply:
    """synced: the user's calendar app subscribes to their AmIFree feed."""
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
    body += "\n\n" + checked_line(availability)
    body += "\nStatus: " + STATUS_LABELS[event.status]
    if synced and event.status in (Status.SAVED, Status.GOING):
        body += "\n📅 Syncing to your calendar automatically."
    keyboard = event_keyboard(event, bool(availability.conflicts), availability.possible, synced)
    return Reply(body, keyboard)


async def send_reply(update, reply: Reply):
    """Send one reply to the chat the update came from: a document, or plain text."""
    query = getattr(update, "callback_query", None)
    if reply.edit and query is not None and not reply.document:
        from telegram.error import BadRequest

        try:
            await query.edit_message_text(reply.text, reply_markup=reply.keyboard)
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return  # tapped twice: the card already shows this state
            # Too old to edit, or deleted: fall through and send it as a new message.
    if reply.document:
        from telegram import InputFile

        filename, content = reply.document
        await update.effective_message.reply_document(
            InputFile(content, filename=filename), caption=reply.text, reply_markup=reply.keyboard
        )
    else:
        await update.effective_message.reply_text(reply.text, reply_markup=reply.keyboard)
