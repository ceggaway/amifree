from dataclasses import dataclass
from datetime import datetime
from html import escape

from telegram import InlineKeyboardMarkup, LinkPreviewOptions

from app.bot.keyboards import event_keyboard, group_keyboard
from app.schemas.event import SG, Event, EventDraft, GroupEvent, Status
from app.services.availability import Availability

HELP = """<b>AmIFree</b> — forward me a plan, I'll tell you if you're free and save it.

<b>How to use me</b>
📩 Forward or type a plan with a time, like "Dinner Fri 7-9pm at Vivo". I check it, then you tap Add to plans or Skip.
⏰ Forward a deadline, like "report due Fri 11:59pm". I'll remind you the day before.
📅 Connect a calendar or NUSMods so I can see your classes and busy times (optional).

<i>"No clashes found" only covers what I know about.</i>

<b>Commands</b>
/plans — what's coming up
/free — am I free? (/free fri 7-9pm)
/deadline — save one, or see your list
/calendar — connect Google, iPhone or Outlook
/settings — reminders and privacy
/feedback — tell the team what you think
/cancel — clear a pending action
/deletemydata — delete my data
/start — set up again

<b>In a group</b>
Reply /event to a plan for a card with Going / Can't and a headcount. /whosfree finds a time for everyone.

<b>Privacy</b>
I only act on what you send me, commands and taps. I never read group chats, and your schedule is never shown to a group."""

STATUS_LABELS = {
    Status.PREVIEW: "Not saved yet",
    Status.SAVED: "Saved (not decided)",
    Status.GOING: "Going",
    Status.MAYBE: "Maybe",
    Status.DECLINED: "Can't make it",
}
# A personal plan is simply in your plans or not; a group RSVP keeps the words above.
PERSONAL_STATUS_LABELS = {
    Status.PREVIEW: "Not added yet",
    Status.GOING: "In your plans",
    Status.DECLINED: "Not in your plans",
}


@dataclass
class Reply:
    text: str
    keyboard: InlineKeyboardMarkup | None = None
    # (filename, content) sent as a Telegram document with text as its caption.
    document: tuple[str, bytes] | None = None
    # Pictures sent together as one swipeable album, with text as the caption of the first.
    photos: tuple[bytes, ...] = ()
    # One picture with text as its caption and the keyboard under it (a step of a guide).
    # Edited in place, its picture is swapped too, so Next and Back flip through the steps.
    photo: bytes | None = None
    # Replace the card whose button was tapped instead of sending a new message.
    edit: bool = False
    parse_mode: str | None = None
    # When these photos form an album that should replace an earlier album in the same chat,
    # the group name both share; the earlier album's messages are deleted before this is sent.
    album_group: str | None = None
    # A text/screen reply that deletes the tracked album for this group before it is shown.
    clear_album_group: str | None = None
    # Keep setup instructions compact instead of showing a website preview below them.
    disable_link_preview: bool = False


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


GROUP_WELCOME = (
    "👋 Thanks for adding AmIFree!\n\n"
    "To get started, send /app here. It opens AmIFree for this group, where you can"
    " create an event or find a time that works for everyone."
)

GROUP_HELP = """👋 AmIFree in this group

/event Dinner Fri 7-9pm at Vivo: post an event card (or reply /event to a plan message)
   Everyone taps Going or Can't, and can check privately whether they're free.
   "every Tue/Thu until week 13" makes a recurring event.
/whosfree project meeting 2h next week: find a time; members tap the times that work
/attendance, /stats, /export: turnout for your events (sent to you privately)
/detect on: admins only, suggests a card when someone posts a plan

Organisers: tap 📱 Details (or ⚙️ Manage) on a card to edit, cancel, nudge or mark who came.
To use your schedule, open a private chat with me and send /start."""


def group_card(
    event: GroupEvent,
    counts: dict[str, int],
    went: int | None = None,
    ended: bool = False,
    app_url: str | None = None,
) -> Reply:
    """The shared card: details and a headcount. Names only ever appear in "Who's going?"."""
    if event.status == "CANCELLED":
        return Reply("❌ Cancelled\n" + details(event))
    if ended:
        # Without check-ins nobody knows who came: say how many planned to, not that they went.
        count = f"{counts.get('GOING', 0)} said Going" if went is None else f"{went} went"
        when = f"{event.start_at:%a %-d %b} · {time_range(event.start_at, event.end_at)}"
        return Reply(f"📣 {event.title} · ended\n{when} · {count}")
    if app_url:
        series = "\n🔁 Repeats. Details lets you say Going to every session." * bool(
            event.series_id
        )
        tip = "\nDetails: check if you're free (only you see it) and who's going."
    else:
        series = "\n🔁 Recurring event. Going to all adds every session." * bool(event.series_id)
        tip = "\nAm I free? shows a private check that only you can see."
    body = (
        "📅 "
        + details(event)
        + series
        + f"\n\nGoing {counts.get('GOING', 0)} · Can't {counts.get('DECLINED', 0)}"
        + tip
    )
    return Reply(body, group_keyboard(event, app_url))


def personal_time(value: datetime) -> str:
    return value.astimezone(SG).strftime("%-I:%M%p").replace(":00", "")


def personal_when(start: datetime, end: datetime) -> str:
    start, end = start.astimezone(SG), end.astimezone(SG)
    if start.date() == end.date():
        return f"{start:%-d %b %Y (%A)} · {personal_time(start)}–{personal_time(end)} SGT"
    return (
        f"{start:%-d %b %Y (%A)} · {personal_time(start)} – "
        f"{end:%-d %b %Y (%A)} · {personal_time(end)} SGT"
    )


def personal_details(event: EventDraft, *, compact: bool = False) -> str:
    title = event.title[:80] if compact else event.title
    text = f"<b>{escape(title)}</b>\n📅 {personal_when(event.start_at, event.end_at)}"
    if event.location:
        text += "\n📍 " + escape(event.location[:80] if compact else event.location)
    if event.assumed_duration:
        text += "\nEnd time estimated."
    return text


def personal_availability(availability: Availability) -> str:
    def conflict_line(plan):
        start, end = plan.start_at.astimezone(SG), plan.end_at.astimezone(SG)
        when = (
            f"{personal_time(start)}–{personal_time(end)} SGT"
            if start.date() == end.date()
            else personal_when(start, end)
        )
        return f"{escape(plan.title)} · {when}"

    conflicts = availability.conflicts
    if len(conflicts) == 1:
        return "⚠️ Overlaps with " + conflict_line(conflicts[0])
    if conflicts:
        return f"⚠️ Overlaps with {len(conflicts)} plans\n" + "\n".join(
            "• " + conflict_line(plan) for plan in conflicts
        )
    return "✅ No clashes found"


def personal_calendar_note(availability: Availability) -> str | None:
    timetable = availability.timetable_connected
    if timetable is None:
        timetable = availability.covered if availability.nusmods is None else availability.nusmods
    personal = availability.personal_connected
    if personal is None:
        personal = availability.personal
    if not timetable and not personal:
        note = "Connect your timetable and personal calendar for a fuller clash check."
    elif not timetable:
        note = "Connect your NUSMods calendar to check for clashes with your classes."
    elif not personal:
        note = "Connect your personal calendar to check for clashes with your other plans."
    else:
        note = None
    return note


def personal_event_card(event: Event, availability: Availability, synced: bool) -> Reply:
    if event.status == Status.CANCELLED:
        return Reply("Cancelled\n\n" + personal_details(event), parse_mode="HTML")
    text = personal_availability(availability) + "\n\n" + personal_details(event)
    added = event.status in (Status.GOING, Status.SAVED)
    personal_connected = availability.personal_connected
    if personal_connected is None:
        personal_connected = availability.personal
    hide_exports = synced or personal_connected
    note = personal_calendar_note(availability)
    if note and not added:
        text += "\n\n<i>" + note + "</i>"
    if added:
        text += "\n\n☑️ <b>Added to your plans.</b>"
        if synced:
            text += "\nSyncing to your calendar."
        if not hide_exports:
            text += (
                "\n\nWant this event in your personal calendar? Tap <b>Google Calendar</b> "
                "or <b>Apple Calendar</b> below, then confirm to add it."
                "\n\nTo add future plans automatically, connect calendar syncing through /calendar."
            )
    elif event.status in (Status.DECLINED, Status.MAYBE):
        text += "\n\n❌ <b>Not in your plans.</b>"
    return Reply(
        text,
        event_keyboard(event, bool(availability.conflicts), synced=hide_exports),
        parse_mode="HTML",
    )


def event_card(event: Event, availability: Availability, synced: bool = False) -> Reply:
    """synced: the user's calendar app subscribes to their AmIFree feed."""
    if not event.group_event_id:
        return personal_event_card(event, availability, synced)
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
    label = STATUS_LABELS[event.status]
    if not event.group_event_id:
        label = PERSONAL_STATUS_LABELS.get(event.status, label)
    body += "\nStatus: " + label
    if synced and event.status in (Status.SAVED, Status.GOING):
        body += "\n📅 Syncing to your calendar automatically."
    keyboard = event_keyboard(event, bool(availability.conflicts), availability.possible, synced)
    return Reply(body, keyboard)


# Per-chat message ids of the last guide picture album, so switching guides deletes the
# previous album instead of stacking them. Keyed by (chat_id, group). In-memory only: after a
# restart an orphaned album simply is not cleaned up, which is harmless.
_guide_albums: dict[tuple[int, str], list[int]] = {}


async def _clear_guide_album(update, group: str) -> None:
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return
    ids = _guide_albums.pop((chat.id, group), None)
    if not ids:
        return
    message = getattr(update, "effective_message", None)
    bot = message.get_bot() if message is not None else None
    if bot is None:
        return
    for message_id in ids:
        try:
            await bot.delete_message(chat.id, message_id)
        except Exception:
            pass  # already gone, too old, or not allowed: nothing to clean up


async def send_reply(update, reply: Reply):
    """Send one reply to the chat the update came from: a document, or plain text."""
    preview_options = (
        {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        if reply.disable_link_preview
        else {}
    )
    if reply.clear_album_group is not None:
        await _clear_guide_album(update, reply.clear_album_group)
    query = getattr(update, "callback_query", None)
    if reply.edit and query is not None and not reply.document:
        from telegram import InputMediaPhoto
        from telegram.error import BadRequest, TelegramError

        tapped = getattr(query, "message", None)
        on_picture = bool(getattr(tapped, "photo", None))
        try:
            if reply.photo is not None and on_picture:
                await query.edit_message_media(
                    InputMediaPhoto(reply.photo, caption=reply.text, parse_mode=reply.parse_mode),
                    reply_markup=reply.keyboard,
                )
                return
            if reply.photo is None and not on_picture:
                await query.edit_message_text(
                    reply.text,
                    reply_markup=reply.keyboard,
                    parse_mode=reply.parse_mode,
                    **preview_options,
                )
                return
            # Text can't become a picture, or a picture text: replace the message instead.
            try:
                await tapped.delete()
            except TelegramError:
                pass
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return  # tapped twice: the card already shows this state
            # Too old to edit, or deleted: fall through and send it as a new message.
    if reply.photo is not None:
        await update.effective_message.reply_photo(
            reply.photo,
            caption=reply.text,
            parse_mode=reply.parse_mode,
            reply_markup=reply.keyboard,
        )
    elif reply.photos:
        from telegram import InputMediaPhoto

        sent = await update.effective_message.reply_media_group(
            [
                InputMediaPhoto(photo, caption=reply.text if i == 0 else None)
                for i, photo in enumerate(reply.photos)
            ]
        )
        chat = getattr(update, "effective_chat", None)
        if reply.album_group is not None and sent and chat is not None:
            _guide_albums[(chat.id, reply.album_group)] = [msg.message_id for msg in sent]
    elif reply.document:
        from telegram import InputFile

        filename, content = reply.document
        await update.effective_message.reply_document(
            InputFile(content, filename=filename), caption=reply.text, reply_markup=reply.keyboard
        )
    else:
        await update.effective_message.reply_text(
            reply.text,
            reply_markup=reply.keyboard,
            parse_mode=reply.parse_mode,
            **preview_options,
        )
