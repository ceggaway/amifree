"""/whosfree: a poll that fills itself from the schedules of members who opt in.

Privacy rules, enforced here rather than left to the caller:
- only people who tapped "Count me in" have their schedule read;
- slots are suggested only once MIN_PARTICIPANTS have joined;
- the group sees counts per slot, never who is busy or what with.
"""

import re
from datetime import datetime, time, timedelta

from app.bot.keyboards import poll_keyboard
from app.bot.messages import Reply
from app.schemas.event import SG, EventDraft, Poll, Status
from app.services.group_events import GroupResult, parse_id

MIN_PARTICIPANTS = 3
DAY_START, DAY_END, STEP = time(8), time(22), timedelta(minutes=30)
USAGE = "Try: /whosfree project meeting 2h next week"
ANSWERS = {"F": "FREE", "M": "MAYBE", "B": "BUSY"}
HOURS = re.compile(r"\b(\d{1,2}(?:\.5)?)\s*h(?:(?:ou)?rs?)?\b", re.IGNORECASE)
MINUTES = re.compile(r"\b(\d{2,3})\s*min(?:ute)?s?\b", re.IGNORECASE)
WINDOWS = ("this week", "next week", "this weekend", "weekend", "tomorrow", "today")


def parse_request(text: str, now: datetime) -> tuple[str, int, datetime, datetime]:
    """Return (title, duration in minutes, window start, window end)."""
    minutes = 60
    for pattern, scale in ((HOURS, 60), (MINUTES, 1)):
        match = pattern.search(text)
        if match:
            minutes = int(float(match[1]) * scale)
            text = text[: match.start()] + text[match.end() :]
            break
    minutes = max(30, min(minutes, 480))
    today = datetime.combine(now.astimezone(SG).date(), time.min, SG)
    start, end = today + timedelta(days=1), today + timedelta(days=8)
    lowered = text.lower()
    for phrase in WINDOWS:
        index = lowered.find(phrase)
        if index < 0:
            continue
        text = text[:index] + text[index + len(phrase) :]
        monday = today - timedelta(days=today.weekday())
        if phrase == "today":
            start, end = today, today + timedelta(days=1)
        elif phrase == "tomorrow":
            end = start + timedelta(days=1)
        elif phrase == "this week":
            start, end = today, monday + timedelta(days=7)
        elif phrase == "next week":
            start, end = monday + timedelta(days=7), monday + timedelta(days=14)
        else:
            start, end = monday + timedelta(days=5), monday + timedelta(days=7)
            if end <= now:
                start, end = start + timedelta(days=7), end + timedelta(days=7)
        break
    title = " ".join(text.split()).strip(" ,-") or "Meetup"
    return title[:200], minutes, start, end


def suggest_slots(
    start: datetime,
    end: datetime,
    minutes: int,
    busy: dict[str, list[tuple[datetime, datetime]]],
    now: datetime,
) -> list[dict]:
    """The three non-overlapping slots where most participants are free, earliest first on ties."""
    length = timedelta(minutes=minutes)
    candidates = []
    day = start.astimezone(SG).date()
    while datetime.combine(day, time.min, SG) < end:
        slot = datetime.combine(day, DAY_START, SG)
        last = datetime.combine(day, DAY_END, SG) - length
        while slot <= last:
            if slot >= max(start, now) and slot + length <= end:
                free = sum(
                    not any(slot < b_end and slot + length > b_start for b_start, b_end in spans)
                    for spans in busy.values()
                )
                candidates.append((-free, slot))
            slot += STEP
        day += timedelta(days=1)
    chosen: list[tuple[datetime, int]] = []
    for negative, slot in sorted(candidates):
        if all(slot >= other + length or slot + length <= other for other, _ in chosen):
            chosen.append((slot, -negative))
        if len(chosen) == 3:
            break
    return [
        {"start_at": slot.isoformat(), "end_at": (slot + length).isoformat(), "free": free}
        for slot, free in sorted(chosen)
    ]


def slot_draft(poll: Poll, index: int) -> EventDraft:
    slot = poll.slots[index]
    return EventDraft(
        title=poll.title,
        start_at=datetime.fromisoformat(slot["start_at"]),
        end_at=datetime.fromisoformat(slot["end_at"]),
    )


def poll_card(poll: Poll, joined: int, tally: dict[tuple[int, str], int]) -> Reply:
    hours = poll.duration_minutes / 60
    length = f"{hours:g}h" if poll.duration_minutes % 30 == 0 else f"{poll.duration_minutes}min"
    head = f"🗳 Who's free? {poll.title} ({length})"
    if poll.status == "CLOSED":
        return Reply(head + "\nThis poll is closed.")
    if poll.status == "COLLECTING":
        last = poll.window_end - timedelta(days=1)
        return Reply(
            head
            + f"\nLooking at {poll.window_start:%a %-d %b} to {last:%a %-d %b}, 08:00–22:00."
            + "\n\nTap Count me in to let AmIFree use your schedule for this poll. The group only"
            + " sees how many people are free in each slot, never who is busy or why."
            + f"\n\nJoined: {joined} (the organiser can suggest slots once {MIN_PARTICIPANTS} have joined)",
            poll_keyboard(poll),
        )
    lines = []
    for index, slot in enumerate(poll.slots):
        draft = slot_draft(poll, index)
        votes = " · ".join(
            f"{label} {tally.get((index, answer), 0)}"
            for label, answer in (("✅", "FREE"), ("🤔", "MAYBE"), ("❌", "BUSY"))
        )
        lines.append(
            f"{index + 1}. {draft.start_at:%a %-d %b %H:%M}–{draft.end_at:%H:%M}"
            f" · {slot['free']} of {joined} free\n    {votes}"
        )
    return Reply(
        head
        + f"\nBest slots from {joined} schedules:\n\n"
        + "\n".join(lines)
        + "\n\nTap Fill mine to answer from your schedule (only you see the result), or answer by hand.",
        poll_keyboard(poll),
    )


class PollService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo

    async def render(self, poll: Poll) -> Reply:
        joined = len(await self.repo.poll_participants(poll.id))
        return poll_card(poll, joined, await self.repo.poll_tally(poll.id))

    async def create(
        self, telegram_id: int, chat_id: int, message_id: int, args: str
    ) -> GroupResult:
        if not args.strip():
            return GroupResult(post=Reply("What are you scheduling? " + USAGE))
        user = await self.repo.ensure_user(telegram_id)
        title, minutes, start, end = parse_request(args[:300], self.scheduling.clock())
        poll = await self.repo.create_poll(user.id, chat_id, message_id, title, minutes, start, end)
        if poll.message_id:
            return GroupResult()
        return GroupResult(post=await self.render(poll), poll_id=poll.id)

    async def posted(self, poll_id: str, message_id: int):
        await self.repo.set_poll_message(poll_id, message_id)

    async def busy_spans(self, poll: Poll, user_id: str) -> list[tuple[datetime, datetime]]:
        user = await self.repo.get_user(user_id)
        if not user:
            return []
        window = EventDraft(title="window", start_at=poll.window_start, end_at=poll.window_end)
        blocks, events = await self.repo.busy(user.id, window)
        definite = blocks + [e for e in events if e.status == Status.GOING]
        return [(b.start_at, b.end_at) for b in definite]

    async def callback(self, telegram_id: int, chat_id: int, data: str) -> GroupResult:
        parts = data.split(":")
        poll_id = parse_id(parts[2]) if len(parts) in (3, 4) else None
        poll = await self.repo.get_poll(poll_id) if poll_id else None
        if not poll or poll.chat_id != chat_id:
            return GroupResult(alert="This poll is no longer available.")
        if poll.status == "CLOSED":
            return GroupResult(alert="This poll is closed.", card=await self.render(poll))
        user = await self.repo.ensure_user(telegram_id)
        action, extra = parts[1], parts[3] if len(parts) == 4 else ""
        organiser = poll.creator_user_id == user.id
        if action == "join":
            await self.repo.poll_join(poll.id, user.id)
            note = (
                "You're in. Slots are already suggested, so tap Fill mine."
                if poll.status == "OPEN"
                else "You're in. Your schedule is only used to count who is free."
            )
            return GroupResult(alert=note, card=await self.render(poll))
        if action == "nudge":
            if not organiser:
                return GroupResult(alert="Only the organiser can nudge members.")
            return GroupResult(alert=await self.scheduling.groups.nudge(poll))
        if action == "suggest":
            if not organiser:
                return GroupResult(alert="Only the organiser can suggest slots.")
            if poll.status != "COLLECTING":
                return GroupResult(alert="Slots are already suggested.")
            people = await self.repo.poll_participants(poll.id)
            if len(people) < MIN_PARTICIPANTS:
                return GroupResult(
                    alert=f"{len(people)} joined so far. At least {MIN_PARTICIPANTS} are needed, so"
                    " nobody's schedule can be worked out from the result."
                )
            busy = {person: await self.busy_spans(poll, person) for person in people}
            slots = suggest_slots(
                poll.window_start,
                poll.window_end,
                poll.duration_minutes,
                busy,
                self.scheduling.clock(),
            )
            if not slots:
                return GroupResult(alert="There is no time left in that window. Start a new poll.")
            opened = await self.repo.poll_open(poll.id, user.id, slots)
            if not opened:
                return GroupResult(alert="Slots are already suggested.")
            return GroupResult(alert="Slots suggested.", card=await self.render(opened))
        if poll.status != "OPEN":
            return GroupResult(alert="The organiser hasn't suggested slots yet.")
        if action == "fill":
            votes, summary = {}, []
            for index in range(len(poll.slots)):
                availability = await self.scheduling.availability(user, slot_draft(poll, index))
                answer = (
                    "BUSY"
                    if availability.conflicts
                    else ("MAYBE" if availability.possible else "FREE")
                )
                votes[str(index)] = answer
                summary.append(f"{index + 1} {answer.capitalize()}")
            await self.repo.poll_vote(poll.id, user.id, votes)
            return GroupResult(
                alert="Filled from your schedule: "
                + " · ".join(summary)
                + ".\nTap a button to change an answer. Only you can see this.",
                card=await self.render(poll),
            )
        index = int(extra[:1]) if extra[:1].isdigit() else -1
        if not 0 <= index < len(poll.slots):
            return GroupResult(alert="That button isn't valid.")
        if action == "v" and extra[1:] in ANSWERS:
            await self.repo.poll_vote(poll.id, user.id, {str(index): ANSWERS[extra[1:]]})
            return GroupResult(
                alert=f"Slot {index + 1}: {ANSWERS[extra[1:]].capitalize()}.",
                card=await self.render(poll),
            )
        if action == "pick":
            if not organiser:
                return GroupResult(alert="Only the organiser can pick the slot.")
            closed = await self.repo.poll_close(poll.id, user.id)
            if not closed:
                return GroupResult(alert="This poll is closed.")
            # The poll card's own message id keeps event creation idempotent in this chat.
            event = await self.repo.create_group_event(
                user.id, chat_id, poll.message_id or poll.source_message_id, slot_draft(poll, index)
            )
            return GroupResult(
                alert="Event card posted.",
                card=await self.render(closed),
                post=await self.scheduling.groups.render(event),
                group_event_id=event.id,
            )
        return GroupResult(alert="That action isn't available.")
