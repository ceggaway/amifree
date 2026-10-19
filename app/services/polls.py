"""/whosfree: a poll that fills itself from the schedules of members who opt in.

Privacy rules, enforced here rather than left to the caller:
- only people who tapped "Count me in" have their schedule read;
- slots are suggested only once MIN_PARTICIPANTS have joined;
- the group sees counts per slot, never who is busy or what with.
"""

import re
from datetime import datetime, time, timedelta

from app.bot.keyboards import links_keyboard, poll_keyboard
from app.bot.messages import Reply, clock
from app.schemas.event import SG, EventDraft, Poll, Status
from app.services.app_links import group_link
from app.services.date_resolution import Clarification, resolve_date
from app.services.group_events import GroupResult, parse_id
from app.services.usual_times import covers

MIN_PARTICIPANTS = 3
DAY_START, DAY_END, STEP = time(8), time(22), timedelta(minutes=30)
USAGE = "Try: /whosfree project meeting 2h next week"
ANSWERS = {"F": "FREE", "M": "MAYBE", "B": "BUSY"}
HOURS = re.compile(r"\b(\d{1,2}(?:\.5)?)\s*h(?:(?:ou)?rs?)?\b", re.IGNORECASE)
MINUTES = re.compile(r"\b(\d{2,3})\s*min(?:ute)?s?\b", re.IGNORECASE)
# Longest first, so "this weekend" is not read as "this week" followed by "end".
WINDOWS = ("this weekend", "next week", "this week", "weekend", "tomorrow", "today")
WINDOW = re.compile(r"\b(" + "|".join(WINDOWS) + r")\b", re.IGNORECASE)


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
    if match := WINDOW.search(text):
        phrase = match[1].lower()
        text = text[: match.start()] + text[match.end() :]
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
    title = " ".join(text.split()).strip(" ,-") or "Meetup"
    return title[:200], minutes, start, end


def clashes(spans: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    return any(start < b_end and end > b_start for b_start, b_end in spans)


def suggest_slots(
    start: datetime,
    end: datetime,
    minutes: int,
    busy: dict[str, list[tuple[datetime, datetime]]],
    now: datetime,
    unknown: int = 0,
) -> list[dict]:
    """The three non-overlapping slots where most participants have no known clash.

    `busy` holds only people whose schedule is known; `unknown` counts joiners with no schedule
    data, so a slot can say "2 of 3 with a schedule" instead of treating missing data as free."""
    length = timedelta(minutes=minutes)
    candidates = []
    day = start.astimezone(SG).date()
    while datetime.combine(day, time.min, SG) < end:
        slot = datetime.combine(day, DAY_START, SG)
        last = datetime.combine(day, DAY_END, SG) - length
        while slot <= last:
            if slot >= max(start, now) and slot + length <= end:
                free = sum(not clashes(spans, slot, slot + length) for spans in busy.values())
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
        {
            "start_at": slot.isoformat(),
            "end_at": (slot + length).isoformat(),
            "free": free,
            "of": len(busy),
            "unknown": unknown,
        }
        for slot, free in sorted(chosen)
    ]


BY_DAY = re.compile(
    r"\b(?:(?:reply|respond|answer|rsvp)\s+)?by\s+"
    r"(tomorrow|tmr|today|tonight|(?:this\s+|next\s+)?(?:mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)"
    r"[a-z]*|\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}|[a-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)
DEFAULT_DEADLINE = timedelta(hours=48)


def parse_deadline(text: str, now: datetime) -> tuple[str, datetime]:
    """Split "reply by Sun" off a /whosfree request. Returns (rest of the text, deadline).

    The deadline is 11:59pm that day, or two days from now when none is given or it has passed."""
    match = BY_DAY.search(text)
    if match:
        try:
            day = resolve_date(match[1], now)
        except Clarification:
            day = None
        if day:
            deadline = datetime.combine(day, time(23, 59), SG)
            if deadline > now:
                return (text[: match.start()] + text[match.end() :]).strip(), deadline
    return text, now.astimezone(SG) + DEFAULT_DEADLINE


def heatmap_cells(
    start: datetime,
    end: datetime,
    busy: dict[str, list[tuple[datetime, datetime]]],
    now: datetime,
    max_days: int = 14,
) -> list[dict]:
    """Per day, the number of people with no known clash in each 30-minute cell.

    Counts only: nothing here says who is busy. Cells already in the past are left out."""
    days = []
    day = start.astimezone(SG).date()
    shown = 0
    while datetime.combine(day, time.min, SG) < end and shown < max_days:
        cells = []
        slot = datetime.combine(day, DAY_START, SG)
        stop = datetime.combine(day, DAY_END, SG)
        while slot < stop:
            nxt = slot + STEP
            if slot >= max(start, now) and nxt <= end:
                free = sum(not clashes(spans, slot, nxt) for spans in busy.values())
                cells.append({"start_at": slot.isoformat(), "free": free})
            slot = nxt
        if cells:
            days.append({"date": day.isoformat(), "cells": cells})
            shown += 1
        day += timedelta(days=1)
    return days


def slot_draft(poll: Poll, index: int) -> EventDraft:
    slot = poll.slots[index]
    return EventDraft(
        title=poll.title,
        start_at=datetime.fromisoformat(slot["start_at"]),
        end_at=datetime.fromisoformat(slot["end_at"]),
    )


def poll_card(
    poll: Poll,
    joined: int,
    tally: dict[tuple[int, str], int],
    app_url: str | None = None,
    now: datetime | None = None,
) -> Reply:
    hours = poll.duration_minutes / 60
    length = f"{hours:g}h" if poll.duration_minutes % 30 == 0 else f"{poll.duration_minutes}min"
    head = f"🗳 Who's free? {poll.title} ({length})"
    if poll.status == "CLOSED":
        return Reply(head + "\nThis poll is closed.")
    deadline = (
        f"\nReply by {poll.deadline_at:%a %-d %b}, {clock(poll.deadline_at)}"
        if poll.deadline_at
        else ""
    )
    if poll.status == "COLLECTING":
        last = poll.window_end - timedelta(days=1)
        suggest = "suggests them in 📱 Open" if app_url else "taps Suggest"
        if now and poll.deadline_at and poll.deadline_at <= now:
            # The automatic suggestion ran and could not open the poll: say why, not "soon".
            when = (
                f"\n\nJoined: {joined}. The reply time has passed, but slots need at least"
                f" {MIN_PARTICIPANTS} members whose schedule AmIFree can read. Members can send"
                " their NUSMods or calendar link to the bot"
                + (", or set their times in 📱 Open" if app_url else "")
                + f". Then the organiser {suggest}."
            )
        else:
            when = (
                f"\n\nJoined: {joined}. Slots are suggested when the reply time passes, or"
                f" sooner if the organiser {suggest} ({MIN_PARTICIPANTS} must have joined)."
            )
        return Reply(
            head
            + f"\nLooking at {poll.window_start:%a %-d %b} to {last:%a %-d %b}, 08:00–22:00."
            + deadline
            + "\n\nTap Count me in to let AmIFree use your schedule for this poll. The group only"
            + " sees how many people are free in each slot, never who is busy or why."
            + when,
            poll_keyboard(poll, app_url),
        )
    lines = []
    for index, slot in enumerate(poll.slots):
        draft = slot_draft(poll, index)
        count = tally.get((index, "FREE"), 0)
        votes = f"✅ {count} vote{'s' if count != 1 else ''}"
        lines.append(
            f"{index + 1}. {draft.start_at:%a %-d %b %H:%M}–{draft.end_at:%H:%M}"
            f" · {slot['free']} of {slot.get('of', joined)} with no known clash\n    {votes}"
        )
    unknown = max((s.get("unknown", 0) for s in poll.slots), default=0)
    known = poll.slots[0].get("of", joined) if poll.slots else joined
    coverage = (
        f"\n{unknown} member{'s' if unknown != 1 else ''} had no schedule to check, so"
        " they aren't counted. Answer by hand."
        if unknown
        else ""
    )
    return Reply(
        head
        + f"\nBest slots from {known} schedule{'s' if known != 1 else ''}"
        + " (no known clashes, not confirmed free):\n\n"
        + "\n".join(lines)
        + coverage
        + deadline
        + "\n\nTap the time that works best for you. One vote each: tapping another time"
        " moves your vote, and tapping yours again takes it back.",
        poll_keyboard(poll, app_url),
    )


class PollService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        # Poll id -> participants at the last automatic attempt; retried only when it changes.
        self._tried: dict[str, int] = {}

    async def render(self, poll: Poll) -> Reply:
        joined = len(await self.repo.poll_participants(poll.id))
        s = self.scheduling.settings
        return poll_card(
            poll,
            joined,
            await self.repo.poll_tally(poll.id),
            s.heatmap_link(poll.id),
            self.scheduling.clock(),
        )

    async def create(
        self, telegram_id: int, chat_id: int, message_id: int, args: str
    ) -> GroupResult:
        if not args.strip():
            form = links_keyboard(
                [
                    (
                        "🗳 Find a time that works",
                        group_link(self.scheduling.settings, chat_id, "find"),
                    )
                ]
            )
            if form:
                return GroupResult(
                    post=Reply("Pick the dates and length in the app, or type it. " + USAGE, form)
                )
            return GroupResult(post=Reply("What are you scheduling? " + USAGE))
        user = await self.repo.ensure_user(telegram_id)
        now = self.scheduling.clock()
        request = args[:300]
        rest, deadline = parse_deadline(request, now)
        title, minutes, start, end = parse_request(rest, now)
        # Slots must be suggested while there is still time to meet. A chosen reply-by time is
        # kept unless it leaves no slot; the default is at most halfway through the window.
        latest = end - timedelta(minutes=minutes) if rest != request else now + (end - now) / 2
        deadline = max(min(deadline, latest), now + timedelta(minutes=30))
        poll = await self.repo.create_poll(
            user.id, chat_id, message_id, title, minutes, start, end, deadline
        )
        if poll.message_id:
            return GroupResult()
        return GroupResult(post=await self.render(poll), poll_id=poll.id)

    async def posted(self, poll_id: str, message_id: int):
        await self.repo.set_poll_message(poll_id, message_id)

    async def busy_spans(self, poll: Poll, user_id: str) -> list[tuple[datetime, datetime]] | None:
        """Busy times in the poll window, or None when we know nothing about this person.

        A member who set their own times is known even without a schedule: every cell they
        did not tick counts as busy, on top of whatever their schedule already blocks."""
        spans = await self.schedule_spans(poll, user_id)
        submitted = await self.repo.get_availability(poll.id, user_id)
        if submitted is None:
            usual = await self.repo.get_usual(user_id)
            if not usual:
                return spans
            # "Only free on Tuesday evenings": everything outside those windows is busy.
            outside = [
                (cell, cell + STEP) for cell in self.cells(poll) if not self.in_usual(usual, cell)
            ]
            return (spans or []) + outside
        ticked = set(submitted)
        extra = [(cell, cell + STEP) for cell in self.cells(poll) if cell.isoformat() not in ticked]
        return (spans or []) + extra

    @staticmethod
    def in_usual(usual, cell: datetime) -> bool:
        local = cell.astimezone(SG)
        start = local.hour * 60 + local.minute
        return covers(usual, local.weekday(), start, start + 30)

    @staticmethod
    def cells(poll: Poll) -> list[datetime]:
        days = heatmap_cells(poll.window_start, poll.window_end, {}, poll.window_start)
        return [datetime.fromisoformat(c["start_at"]) for d in days for c in d["cells"]]

    async def schedule_spans(
        self, poll: Poll, user_id: str
    ) -> list[tuple[datetime, datetime]] | None:
        """Busy times from the member's schedule only, or None when we hold none.

        With no timetable, calendar or blocks, "nothing busy" would only mean "nothing known",
        and must not be counted as free."""
        user = await self.repo.get_user(user_id)
        if not user:
            return None
        window = EventDraft(title="window", start_at=poll.window_start, end_at=poll.window_end)
        blocks, events = await self.repo.busy(user.id, window)
        if not (blocks or events or user.coverage_start):
            # Nothing in this window: still a known schedule if they use AmIFree for one at all.
            synced = any(c.synced_at for c in await self.repo.list_calendars(user.id))
            if not (synced or await self.repo.list_blocks(user.id)):
                return None
        definite = blocks + [e for e in events if e.status == Status.GOING]
        return [(b.start_at, b.end_at) for b in definite]

    async def suggest(self, poll: Poll) -> tuple[Poll | None, str]:
        """Open a collecting poll with the three best slots. (poll or None, message)."""
        people = await self.repo.poll_participants(poll.id)
        if len(people) < MIN_PARTICIPANTS:
            return None, (
                f"{len(people)} joined so far. At least {MIN_PARTICIPANTS} are needed, so"
                " nobody's schedule can be worked out from the result."
            )
        spans = {person: await self.busy_spans(poll, person) for person in people}
        busy = {person: found for person, found in spans.items() if found is not None}
        if len(busy) < MIN_PARTICIPANTS:
            return None, (
                f"{len(people)} joined, but only {len(busy)} have a schedule AmIFree can"
                f" read. At least {MIN_PARTICIPANTS} are needed, so nobody's schedule can be"
                " worked out from the result. Members can send their NUSMods or calendar link"
                " to the bot."
            )
        slots = suggest_slots(
            poll.window_start,
            poll.window_end,
            poll.duration_minutes,
            busy,
            self.scheduling.clock(),
            unknown=len(people) - len(busy),
        )
        if not slots:
            return None, "There is no time left in that window. Start a new poll."
        opened = await self.repo.poll_open(poll.id, poll.creator_user_id, slots)
        if not opened:
            return None, "Slots are already suggested."
        return opened, "Slots suggested."

    async def sweep_deadlines(self, now: datetime) -> int:
        """Suggest slots for polls whose reply time has passed. Returns how many opened."""
        opened = 0
        for poll in await self.repo.polls_past_deadline(now):
            if not poll.creator_user_id or not poll.message_id:
                continue
            joined = len(await self.repo.poll_participants(poll.id))
            if self._tried.get(poll.id) == joined:
                continue
            if len(self._tried) > 5000:
                self._tried.clear()
            self._tried[poll.id] = joined
            found, _ = await self.suggest(poll)
            opened += bool(found)
            # Redrawn either way: opened with slots, or saying why it could not open yet.
            await self.scheduling.poll_changed(poll.id)
        return opened

    async def may_answer(self, poll: Poll, user_id: str) -> bool:
        if user_id in await self.repo.poll_participants(poll.id):
            return True
        return user_id in await self.repo.group_member_ids(poll.chat_id)

    async def my_grid(self, user_id: str, poll_id: str) -> dict | None:
        """The member's own grid. Blocked cells say only that they are blocked, never why."""
        poll = await self.repo.get_poll(poll_id)
        if not poll or not await self.may_answer(poll, user_id):
            return None
        spans = await self.schedule_spans(poll, user_id)
        submitted = await self.repo.get_availability(poll.id, user_id)
        usual = await self.repo.get_usual(user_id) if submitted is None else []
        ticked = set(submitted or [])
        now = self.scheduling.clock()
        days = heatmap_cells(poll.window_start, poll.window_end, {}, max(poll.window_start, now))
        for day in days:
            for cell in day["cells"]:
                start = datetime.fromisoformat(cell["start_at"])
                if clashes(spans or [], start, start + STEP):
                    state = "busy"
                elif submitted is not None:
                    state = "free" if cell["start_at"] in ticked else "off"
                elif usual:
                    # Pre-filled from "every Tuesday evening" style usual times.
                    state = "free" if self.in_usual(usual, start) else "off"
                else:
                    # Nothing ticked yet: pre-select what the schedule leaves open, but never
                    # assume free for someone whose schedule we do not hold.
                    state = "free" if spans is not None else "off"
                cell["state"] = state
                cell.pop("free", None)
        return {
            "poll_id": poll.id,
            "title": poll.title,
            "duration_minutes": poll.duration_minutes,
            "closed": poll.status == "CLOSED",
            "deadline_at": poll.deadline_at.isoformat() if poll.deadline_at else None,
            "known": spans is not None or bool(usual),
            "submitted": submitted is not None,
            "from_usual": bool(usual),
            "days": days,
        }

    async def save_grid(self, user_id: str, poll_id: str, free: list[str]) -> dict | None:
        """Store the cells a member ticked. Cells their schedule blocks are dropped."""
        poll = await self.repo.get_poll(poll_id)
        if not poll or not await self.may_answer(poll, user_id):
            return None
        if poll.status == "CLOSED":
            raise ValueError("This poll is closed.")
        valid = {c.isoformat() for c in self.cells(poll)}
        chosen = set()
        for raw in free:
            try:
                cell = datetime.fromisoformat(raw).astimezone(SG)
            except ValueError:
                raise ValueError("A time in your answer isn't valid.") from None
            if cell.isoformat() in valid:
                chosen.add(cell.isoformat())
        spans = await self.schedule_spans(poll, user_id) or []
        kept = sorted(
            c
            for c in chosen
            if not clashes(spans, datetime.fromisoformat(c), datetime.fromisoformat(c) + STEP)
        )
        await self.repo.poll_join(poll.id, user_id)
        await self.repo.set_availability(poll.id, user_id, kept)
        await self.scheduling.poll_changed(poll.id)
        return {"saved": len(kept), "dropped": len(chosen) - len(kept)}

    async def heatmap(self, user_id: str, poll_id: str) -> dict | None:
        """Free counts per half hour for a poll's participants; None when it isn't theirs to see.

        Only people who joined may look, and only once enough schedules are known that a cell
        cannot be traced back to one person."""
        poll = await self.repo.get_poll(poll_id)
        people = await self.repo.poll_participants(poll.id) if poll else []
        if user_id not in people:
            return None
        spans = {person: await self.busy_spans(poll, person) for person in people}
        busy = {person: found for person, found in spans.items() if found is not None}
        base = {
            "poll_id": poll.id,
            "title": poll.title,
            "duration_minutes": poll.duration_minutes,
            "joined": len(people),
            "known": len(busy),
            "min_known": MIN_PARTICIPANTS,
        }
        if len(busy) < MIN_PARTICIPANTS:
            return base | {"ready": False, "days": [], "max_free": 0}
        days = heatmap_cells(poll.window_start, poll.window_end, busy, self.scheduling.clock())
        return base | {"ready": True, "days": days, "max_free": len(busy)}

    async def choose(self, poll: Poll, user_id: str, index: int | None) -> int | None:
        """One vote per person: `index` gets their vote and every other time is cleared.
        Tapping the time they already chose (or None) takes the vote back. Every slot is
        written in one call, so fast repeated taps can never leave two votes."""
        mine = await self.repo.poll_my_votes(poll.id, user_id)
        if index is not None and mine.get(index) == "FREE":
            index = None
        votes = {str(i): "FREE" if i == index else "BUSY" for i in range(len(poll.slots))}
        await self.repo.poll_vote(poll.id, user_id, votes)
        return index

    async def pick(self, user, poll: Poll, index: int) -> tuple[Poll, object] | None:
        """Close the poll on one slot and make its event (not yet posted). None if closed."""
        closed = await self.repo.poll_close(poll.id, user.id)
        if not closed:
            return None
        # The poll card's own message id keeps event creation idempotent in this chat.
        event = await self.repo.create_group_event(
            user.id,
            poll.chat_id,
            poll.message_id or poll.source_message_id,
            slot_draft(poll, index),
        )
        return closed, event

    async def callback(
        self,
        telegram_id: int,
        chat_id: int,
        data: str,
        name: str | None = None,
        username: str | None = None,
    ) -> GroupResult:
        parts = data.split(":")
        poll_id = parse_id(parts[2]) if len(parts) in (3, 4) else None
        poll = await self.repo.get_poll(poll_id) if poll_id else None
        if not poll or poll.chat_id != chat_id:
            return GroupResult(alert="This poll is no longer available.")
        if poll.status == "CLOSED":
            return GroupResult(alert="This poll is closed.", card=await self.render(poll))
        user = await self.repo.ensure_user(telegram_id)
        await self.scheduling.groups.remember(chat_id, user.id, name, username)
        action, extra = parts[1], parts[3] if len(parts) == 4 else ""
        organiser = poll.creator_user_id == user.id
        if action == "join":
            await self.repo.poll_join(poll.id, user.id)
            note = (
                "You're in. Times are already suggested: tap the one that works best."
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
            opened, note = await self.suggest(poll)
            return GroupResult(alert=note, card=await self.render(opened) if opened else None)
        if poll.status != "OPEN":
            return GroupResult(alert="The organiser hasn't suggested slots yet.")
        if action == "fill":
            spans = await self.busy_spans(poll, user.id)
            if spans is None:
                # An empty schedule would answer "Free" to everything: say so instead.
                return GroupResult(
                    alert="I have no schedule for you yet, so I can't fill this in. Send your"
                    " NUSMods or calendar link to the bot privately, or answer by hand."
                )
            # Cards from before one-vote polls: vote for the first time the schedule leaves free.
            best = None
            for index in range(len(poll.slots)):
                draft = slot_draft(poll, index)
                availability = await self.scheduling.availability(user, draft)
                # The spans also hold the cells left unticked in "Set my times" and the time
                # outside their usual times, which the schedule check alone does not know.
                if not availability.conflicts and not clashes(spans, draft.start_at, draft.end_at):
                    best = index
                    break
            if best is None:
                return GroupResult(
                    alert="Your schedule clashes with every time. Only you see this."
                )
            mine = await self.repo.poll_my_votes(poll.id, user.id)
            if mine.get(best) != "FREE":
                await self.choose(poll, user.id, best)
            return GroupResult(
                alert=f"Voted for time {best + 1}, the first one your schedule leaves free."
                " Only you see this.",
                card=await self.render(poll),
            )
        index = int(extra[:1]) if extra[:1].isdigit() else -1
        if not 0 <= index < len(poll.slots):
            return GroupResult(alert="That button isn't valid.")
        # "t" is today's card; "v…F" is a ✅ on cards from before one-vote polls.
        if action == "t" or (action == "v" and extra[1:] == "F"):
            chosen = await self.choose(poll, user.id, index)
            return GroupResult(
                alert=f"Your vote: time {index + 1} ✅. Tap another time to move it."
                if chosen is not None
                else "Vote taken back. Tap a time to vote again.",
                card=await self.render(poll),
            )
        if action == "v" and extra[1:] in ANSWERS:
            # 🤔 / ❌ on an old card: that time doesn't get their vote.
            mine = await self.repo.poll_my_votes(poll.id, user.id)
            if mine.get(index) == "FREE":
                await self.choose(poll, user.id, None)
            return GroupResult(
                alert="Noted. Tap the time that works best to vote for it.",
                card=await self.render(poll),
            )
        if action == "pick":
            if not organiser:
                return GroupResult(alert="Only the organiser can pick the slot.")
            picked = await self.pick(user, poll, index)
            if not picked:
                return GroupResult(alert="This poll is closed.")
            closed, event = picked
            return GroupResult(
                alert="Event card posted.",
                card=await self.render(closed),
                post=await self.scheduling.groups.render(event),
                group_event_id=event.id,
            )
        return GroupResult(alert="That action isn't available.")
