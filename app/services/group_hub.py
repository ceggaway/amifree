"""The group side of the Mini App: one place to see, create and run a group's events.

A link from a group (t.me/<bot>/<app>?startapp=g_<chat code>) says which group the app is
for, but anyone can edit a link. Every call therefore checks with Telegram that the person is
in that group before showing or changing anything, and organiser actions check that the
person made the card. Names and headcounts follow the same rules as the cards in the chat:
"Who's going?" names only people who said Going, and nobody else's schedule is ever shown.
"""

import logging
import time as monotonic
from datetime import date, datetime, time, timedelta
from uuid import UUID

from pydantic import ValidationError
from telegram.error import BadRequest, Forbidden, TelegramError

from app.schemas.event import SG, EventDraft, GroupEvent, Status
from app.services.app_links import chat_code
from app.services.date_resolution import Clarification
from app.services.group_events import CHECK_IN_OPENS, member_label
from app.services.polls import MIN_PARTICIPANTS
from app.services.ratelimit import RateLimiter
from app.services.recurrence import Recurrence

log = logging.getLogger(__name__)
MEMBER_TTL, OUTSIDER_TTL = 300, 30  # seconds a Telegram membership answer is reused
POLL_DAYS = 14
STATUSES = {"GOING": Status.GOING, "MAYBE": Status.MAYBE, "DECLINED": Status.DECLINED}


class NotFound(Exception):
    """Not in that group, or no such card: both look the same to the caller."""


class Refused(Exception):
    """A request that can't be done, with the reason to show."""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail


def request_message_id(request_id: str) -> int:
    """A stable stand-in for the /event message, so a retried form submit makes one card.

    Negative, so it can never equal a real Telegram message id."""
    try:
        value = UUID(request_id)
    except ValueError:
        raise Refused("That request isn't valid. Reload and try again.") from None
    return -(int(value.hex[:13], 16) + 1)


class GroupHub:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        # From each person's signed Mini App sign-in: (first name, @username).
        self.profiles: dict[int, tuple[str | None, str | None]] = {}
        self._members: dict[tuple[int, int], tuple[bool, float]] = {}
        self._titles: dict[int, str | None] = {}
        self.create_limit = RateLimiter(10, 300)

    @property
    def groups(self):
        return self.scheduling.groups

    def clock(self) -> datetime:
        return self.scheduling.clock()

    def remember_profile(self, telegram_id: int, name: str | None, username: str | None):
        if len(self.profiles) > 20_000:
            self.profiles.clear()
        self.profiles[telegram_id] = (name, username)

    # ---- who may see a group ----

    async def is_member(self, user, chat_id: int) -> bool:
        key = (chat_id, user.telegram_user_id)
        cached = self._members.get(key)
        if cached and cached[1] > monotonic.monotonic():
            return cached[0]
        check = getattr(self.scheduling, "check_chat_member", None)
        if check is not None:
            try:
                allowed = await check(chat_id, user.telegram_user_id)
            except TelegramError:
                raise Refused("Telegram didn't answer just now. Try again in a moment.") from None
        else:
            # No bot connection (tests, scripts): fall back to people seen in the group.
            allowed = user.id in await self.repo.group_member_ids(chat_id)
        if len(self._members) > 20_000:
            self._members.clear()
        ttl = MEMBER_TTL if allowed else OUTSIDER_TTL
        self._members[key] = (allowed, monotonic.monotonic() + ttl)
        if allowed:
            # Count them as a member here, so headcounts, nudges and names include them.
            name, username = self.profiles.get(user.telegram_user_id, (None, None))
            await self.repo.touch_member(chat_id, user.id)
            await self.groups.remember(chat_id, user.id, name, username)
        return allowed

    async def require_member(self, user, chat_id: int):
        if not await self.is_member(user, chat_id):
            raise NotFound

    async def event_for(self, user, event_id: str) -> GroupEvent:
        event = await self.repo.get_group_event(event_id)
        if not event:
            raise NotFound
        await self.require_member(user, event.chat_id)
        return event

    async def organised(self, user, event_id: str) -> GroupEvent:
        event = await self.event_for(user, event_id)
        if event.creator_user_id != user.id:
            raise Refused("Only the organiser can do that.")
        return event

    async def title(self, chat_id: int) -> str | None:
        if chat_id not in self._titles:
            lookup = getattr(self.scheduling, "chat_title", None)
            title = None
            if lookup is not None:
                try:
                    title = await lookup(chat_id)
                except Exception as exc:
                    log.warning("chat_title_failed error_type=%s", type(exc).__name__)
            if len(self._titles) > 5000:
                self._titles.clear()
            self._titles[chat_id] = title
        return self._titles[chat_id]

    # ---- reading ----

    async def overview(self, user, chat_id: int) -> dict:
        await self.require_member(user, chat_id)
        now = self.clock()
        shown, series_seen = [], set()
        for event in await self.repo.chat_events(chat_id, now):
            # A series shows its next session only; the rest are a tap away on that card.
            if event.series_id:
                if event.series_id in series_seen:
                    continue
                series_seen.add(event.series_id)
            if event.start_at > now + timedelta(days=60):
                break
            shown.append(await self.summary(user, event))
            if len(shown) == 30:
                break
        polls = []
        for poll in await self.repo.chat_polls(chat_id, now):
            people = await self.repo.poll_participants(poll.id)
            polls.append(
                {
                    "id": poll.id,
                    "title": poll.title,
                    "status": poll.status,
                    "duration_minutes": poll.duration_minutes,
                    "joined": len(people),
                    "joined_by_me": user.id in people,
                    "deadline_at": poll.deadline_at.isoformat() if poll.deadline_at else None,
                    "min_joined": MIN_PARTICIPANTS,
                }
            )
        return {
            "chat": chat_code(chat_id),
            "title": await self.title(chat_id),
            "events": shown,
            "polls": polls,
        }

    async def summary(self, user, event: GroupEvent) -> dict:
        counts = await self.repo.group_counts(event.id)
        linked = await self.repo.linked_event(user.id, event.id)
        return {
            "id": event.id,
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "location": event.location,
            "counts": {s: counts.get(s, 0) for s in ("GOING", "MAYBE", "DECLINED")},
            "my_status": linked.status.value if linked else None,
            "recurring": bool(event.series_id),
            "organiser": event.creator_user_id == user.id,
        }

    async def detail(self, user, event: GroupEvent) -> dict:
        """Everything the event page shows. "availability" is the caller's own clash check."""
        now = self.clock()
        linked = await self.repo.linked_event(user.id, event.id)
        counts = await self.repo.group_counts(event.id)
        found = await self.scheduling.availability(user, event, linked.id if linked else None)
        answers = await self.repo.group_answers(event.id)
        names = await self.repo.member_names(event.chat_id)
        present = await self.repo.attendees(event.id)
        going = sorted(
            (member_label(names, u) for u, s in answers.items() if s == Status.GOING.value),
            key=str.lower,
        )
        organiser = event.creator_user_id == user.id
        data = {
            "id": event.id,
            "chat": chat_code(event.chat_id),
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "location": event.location,
            "notes": event.notes,
            "status": event.status,
            "version": event.version,
            "ended": event.status == "ACTIVE" and event.end_at <= now,
            "recurring": bool(event.series_id),
            "counts": {s: counts.get(s, 0) for s in ("GOING", "MAYBE", "DECLINED")},
            "my_status": linked.status.value if linked else None,
            "availability": found,
            "going": going,
            "checked_in": user.id in present,
            "check_in_open": event.start_at - CHECK_IN_OPENS <= now < event.end_at,
            "organiser": organiser,
        }
        if organiser:
            people, labels, _ = await self.groups.people_for(event)
            data["manage"] = {
                "summary": await self.groups.attendance_summary(event),
                "came": len(present),
                "people": [
                    {
                        "id": person,
                        "name": labels[person],
                        "answer": answers.get(person),
                        "present": person in present,
                    }
                    for person in people
                ],
            }
        return data

    # ---- answering ----

    async def rsvp(self, user, event: GroupEvent, status: str, force: bool = False) -> dict:
        if event.status != "ACTIVE":
            raise Refused("This event was cancelled.")
        if self.groups.is_ended(event):
            raise Refused("This event has ended.")
        wanted = STATUSES.get(status)
        if wanted is None:
            raise Refused("Choose Going, Maybe or Can't.")
        if wanted == Status.GOING and not force:
            linked = await self.repo.linked_event(user.id, event.id)
            found = await self.scheduling.availability(user, event, linked.id if linked else None)
            if found.conflicts:
                raise Refused(
                    "That clashes with something you have.",
                    {"reason": "clash", "availability": found},
                )
        if not await self.repo.group_rsvp(user.id, event, wanted):
            raise Refused("This card is no longer available.")
        await self.scheduling.group_changed(event.id)
        return await self.detail(user, event)

    async def check_in(self, user, event: GroupEvent) -> dict:
        now = self.clock()
        if event.status != "ACTIVE" or now >= event.end_at:
            raise Refused("This event has ended.")
        if now < event.start_at - CHECK_IN_OPENS:
            raise Refused("Check-in opens 30 minutes before the start.")
        await self.repo.check_in(event.id, user.id, user.id)
        return await self.detail(user, event)

    async def going_to_all(self, user, event: GroupEvent) -> dict:
        result = await self.groups.going_to_all(user, event)
        await self.scheduling.group_changed(event.id)
        return {"message": result.alert, "event": await self.detail(user, event)}

    # ---- organiser ----

    async def edit(self, user, event: GroupEvent, version: int, changes: dict) -> dict:
        if event.status != "ACTIVE":
            raise Refused("This event was cancelled.")
        if event.version != version:
            raise Refused(
                "Someone changed this event. Reload to see the latest.", {"reason": "stale"}
            )
        try:
            draft = EventDraft(**(event.model_dump(include=set(EventDraft.model_fields)) | changes))
        except ValidationError:
            raise Refused("Check the title and the start and end times.") from None
        if draft.end_at <= self.clock():
            raise Refused("That time has already passed.")
        result = await self.repo.update_group_event(user.id, event, draft, False)
        if not result:
            raise Refused(
                "Someone changed this event. Reload to see the latest.", {"reason": "stale"}
            )
        await self.scheduling.group_changed(result.id)
        await self.scheduling.flush_notifications()
        return {"event": await self.detail(user, result), "series_followup": bool(result.series_id)}

    async def cancel(self, user, event: GroupEvent, version: int) -> dict:
        if event.status != "ACTIVE":
            raise Refused("This event was already cancelled.")
        if event.version != version:
            raise Refused(
                "Someone changed this event. Reload to see the latest.", {"reason": "stale"}
            )
        result = await self.repo.update_group_event(user.id, event, None, True)
        if not result:
            raise Refused(
                "Someone changed this event. Reload to see the latest.", {"reason": "stale"}
            )
        await self.scheduling.group_changed(result.id)
        await self.scheduling.flush_notifications()
        return {"event": await self.detail(user, result), "series_followup": bool(result.series_id)}

    async def series(self, user, event: GroupEvent, action: str) -> dict:
        if action not in ("all", "stop"):
            raise Refused("Choose apply to all or cancel all.")
        replies = await self.groups.series_action(user, f"ser:{action}:{event.id}")
        return {"message": replies[0].text}

    async def nudge(self, user, event: GroupEvent) -> dict:
        if event.status != "ACTIVE" or self.groups.is_ended(event):
            raise Refused("This event is over, so there's nobody to nudge.")
        return {"message": await self.groups.nudge(event)}

    async def mark(self, user, event: GroupEvent, person: str | None, present: bool) -> dict:
        people, _, _ = await self.groups.people_for(event)
        if person is None:
            answers = await self.repo.group_answers(event.id)
            for someone in people:
                if answers.get(someone) == Status.GOING.value:
                    await self.repo.mark_attendance(event.id, someone, True, user.id)
        elif person in people:
            await self.repo.mark_attendance(event.id, person, present, user.id)
        else:
            raise Refused("That person hasn't answered this event.")
        await self.scheduling.group_changed(event.id)
        return await self.detail(user, event)

    # ---- find-a-time polls ----

    async def poll_for(self, user, poll_id: str):
        poll = await self.repo.get_poll(poll_id)
        if not poll:
            raise NotFound
        await self.require_member(user, poll.chat_id)
        return poll

    async def organised_poll(self, user, poll_id: str):
        poll = await self.poll_for(user, poll_id)
        if poll.creator_user_id != user.id:
            raise Refused("Only the organiser can do that.")
        return poll

    async def poll_detail(self, user, poll) -> dict:
        """The poll page: suggested times with counts, the caller's own answers, and (for the
        organiser) what they can do next. Counts only, never who answered what."""
        people = await self.repo.poll_participants(poll.id)
        tally = await self.repo.poll_tally(poll.id)
        return {
            "id": poll.id,
            "chat": chat_code(poll.chat_id),
            "title": poll.title,
            "status": poll.status,
            "duration_minutes": poll.duration_minutes,
            "deadline_at": poll.deadline_at.isoformat() if poll.deadline_at else None,
            "joined": len(people),
            "joined_by_me": user.id in people,
            "min_joined": MIN_PARTICIPANTS,
            "slots": [
                {
                    "start_at": slot["start_at"],
                    "end_at": slot["end_at"],
                    "free": slot.get("free", 0),
                    "of": slot.get("of", len(people)),
                    "yes": tally.get((index, "FREE"), 0),
                    "maybe": tally.get((index, "MAYBE"), 0),
                }
                for index, slot in enumerate(poll.slots or [])
            ],
            "mine": {
                str(k): v for k, v in (await self.repo.poll_my_votes(poll.id, user.id)).items()
            },
            "organiser": poll.creator_user_id == user.id,
        }

    async def poll_join(self, user, poll) -> dict:
        if poll.status == "CLOSED":
            raise Refused("This poll is closed.")
        await self.repo.poll_join(poll.id, user.id)
        await self.scheduling.poll_changed(poll.id)
        return await self.poll_detail(user, await self.repo.get_poll(poll.id))

    async def poll_vote(self, user, poll, slot: int, answer: str) -> dict:
        if poll.status != "OPEN":
            raise Refused(
                "This poll is closed." if poll.status == "CLOSED" else "No times suggested yet."
            )
        if slot >= len(poll.slots):
            raise Refused("That time isn't in this poll.")
        await self.repo.poll_vote(poll.id, user.id, {str(slot): answer})
        await self.scheduling.poll_changed(poll.id)
        return await self.poll_detail(user, poll)

    async def poll_suggest(self, user, poll) -> dict:
        if poll.status != "COLLECTING":
            raise Refused("Times are already suggested.")
        opened, note = await self.scheduling.polls.suggest(poll)
        if not opened:
            raise Refused(note)
        await self.scheduling.poll_changed(poll.id)
        return {"message": "Times suggested. Members can now tick them in the group."}

    async def poll_pick(self, user, poll, slot: int) -> dict:
        if poll.status != "OPEN" or slot >= len(poll.slots):
            raise Refused("This poll is closed." if poll.status == "CLOSED" else "Pick a time.")
        picked = await self.scheduling.polls.pick(user, poll, slot)
        if not picked:
            raise Refused("This poll is closed.")
        _, event = picked
        await self.scheduling.poll_changed(poll.id)
        if not event.message_id:
            posted = await self.post(poll.chat_id, await self.groups.render(event))
            await self.repo.set_group_message(event.id, posted)
        return {"event_id": event.id}

    async def poll_nudge(self, user, poll) -> dict:
        if poll.status == "CLOSED":
            raise Refused("This poll is closed, so there's nobody to nudge.")
        return {"message": await self.groups.nudge(poll)}

    # ---- creating ----

    async def post(self, chat_id: int, card) -> int:
        post = getattr(self.scheduling, "post_group_card", None)
        if post is None:
            raise Refused("AmIFree can't post right now. Try again in a minute.")
        try:
            return await post(chat_id, card)
        except (Forbidden, BadRequest):
            raise Refused(
                "I can't post in that group. Add AmIFree back to it, then try again."
            ) from None

    async def create_event(
        self,
        user,
        chat_id: int,
        request_id: str,
        fields: dict,
        weekdays: list[int],
        until: date | None,
    ) -> dict:
        await self.require_member(user, chat_id)
        if not self.create_limit.allow(user.id):
            raise Refused("You're creating a lot very quickly. Give it a minute.")
        message_id = request_message_id(request_id)
        try:
            draft = EventDraft(**fields)
        except ValidationError:
            raise Refused("Check the title and the start and end times.") from None
        now = self.clock()
        if draft.start_at <= now and not weekdays:
            raise Refused("That time has already passed.")
        note = ""
        if weekdays:
            if until is None:
                raise Refused("Choose the last date for the repeating event.")
            recurrence = Recurrence(sorted(set(weekdays)), until)
            try:
                event, days = await self.groups.build_series(
                    user, chat_id, message_id, draft, recurrence, now
                )
            except Clarification as exc:
                raise Refused(str(exc)) from None
            note = (
                f"\n\n🔁 {len(days)} sessions, every {recurrence.label()} until"
                f" {days[-1]:%a %-d %b}. Each session's card is posted a few days before it."
            )
        else:
            event = await self.repo.create_group_event(user.id, chat_id, message_id, draft)
        if event.message_id:
            return {"id": event.id}  # a retried submit: the card is already in the group
        card = await self.groups.render(event)
        card.text += note
        try:
            posted = await self.post(chat_id, card)
        except Refused:
            # Nothing visible was made: take the card back rather than leave a silent one.
            await self.repo.update_group_event(user.id, event, None, True)
            raise
        await self.repo.set_group_message(event.id, posted)
        await self.scheduling.safe_action(user.id, "group_event", metadata={"from": "app"})
        return {"id": event.id}

    async def create_poll(
        self,
        user,
        chat_id: int,
        request_id: str,
        title: str,
        minutes: int,
        first: date,
        last: date,
        reply_by: date | None,
    ) -> dict:
        await self.require_member(user, chat_id)
        if not self.create_limit.allow(user.id):
            raise Refused("You're creating a lot very quickly. Give it a minute.")
        message_id = request_message_id(request_id)
        now = self.clock()
        start = max(datetime.combine(first, time.min, SG), now)
        end = datetime.combine(last + timedelta(days=1), time.min, SG)
        if last < first or end <= now:
            raise Refused("Choose dates from today on, with the last on or after the first.")
        if (last - first).days >= POLL_DAYS:
            raise Refused(f"Choose at most {POLL_DAYS} days.")
        latest = end - timedelta(minutes=minutes)
        deadline = (
            datetime.combine(reply_by, time(23, 59), SG) if reply_by else now + (end - now) / 2
        )
        deadline = max(min(deadline, latest), now + timedelta(minutes=30))
        poll = await self.repo.create_poll(
            user.id, chat_id, message_id, title.strip()[:200], minutes, start, end, deadline
        )
        if poll.message_id:
            return {"id": poll.id}
        # As with /whosfree, asking for the poll is the organiser's own Count me in.
        posted = await self.post(chat_id, await self.scheduling.polls.render(poll))
        await self.scheduling.polls.posted(poll.id, posted)
        await self.scheduling.safe_action(user.id, "poll", metadata={"from": "app"})
        return {"id": poll.id}
