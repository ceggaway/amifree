from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.user import User
from app.schemas.event import (
    Event,
    EventDraft,
    EventSeries,
    GroupEvent,
    Poll,
    Status,
    Suggestion,
)
from app.services.quick_blocks import expand_blocks


class MemoryRepository:
    """Test double only: production always uses SupabaseRepository."""

    def __init__(self):
        self.users = {}
        self.blocks = {}
        self.rules = {}
        self.group_events = {}
        self.members = set()
        self.notifications = []
        self.updates, self.stale_updates = {}, set()
        self.polls = {}
        self.poll_people = {}
        self.poll_votes = {}
        self.poll_created = {}
        self.events = {}
        self.actions = []
        self.available = True
        self.calendar_blocks = {}  # calendar id -> [(start, end)]
        self.calendars = {}
        self.names = {}  # (chat id, user id) -> (display name, username)
        self.series = {}
        self.checks = {}  # (group event id, user id) -> answer
        self.attended = {}  # (group event id, user id) -> marked_by
        self.tiers = {}  # chat id -> (tier, expires_at)
        self.detection = {}  # chat id -> bool
        self.suggestions = {}
        self.availability = {}  # (poll id, user id) -> [ISO cell starts]
        self.payments = {}  # charge id -> (chat id, telegram id, stars, days)

    async def ping(self):
        if not self.available:
            raise ConnectionError("fake database offline")

    async def ensure_user(self, telegram_id):
        await self.ping()
        if telegram_id not in self.users:
            self.users[telegram_id] = User(id=str(uuid4()), telegram_user_id=telegram_id)
        return self.users[telegram_id].model_copy(deep=True)

    def user(self, user_id):
        return next(u for u in self.users.values() if u.id == user_id)

    async def set_pending(self, user_id, pending):
        self.user(user_id).pending = pending

    async def import_timetable(self, user_id, result):
        self.blocks[user_id] = result.blocks
        user = self.user(user_id)
        user.onboarding_status = "READY"
        user.academic_year, user.semester = result.academic_year, result.semester
        user.coverage_start, user.coverage_end = result.coverage_start, result.coverage_end
        user.pending = None

    async def busy(self, user_id, draft):
        manual = expand_blocks(self.rules.get(user_id, []), draft.start_at, draft.end_at)
        personal = self.personal_blocks(user_id, draft.start_at, draft.end_at)
        return self.blocks.get(user_id, []) + manual + personal, [
            e.model_copy(deep=True)
            for e in self.events.values()
            if e.user_id == user_id and e.status in (Status.GOING, Status.SAVED)
        ]

    async def blocks_between(self, user_id, start, end):
        classes = [b for b in self.blocks.get(user_id, []) if b.start_at < end and b.end_at > start]
        manual = expand_blocks(self.rules.get(user_id, []), start, end)
        return classes + manual + self.personal_blocks(user_id, start, end)

    async def create_block(self, user_id, rule):
        rule = rule.model_copy(update={"id": str(uuid4()), "user_id": user_id})
        self.rules.setdefault(user_id, []).append(rule)
        return rule

    async def list_blocks(self, user_id):
        return list(self.rules.get(user_id, []))

    async def delete_block(self, user_id, block_id):
        rules = self.rules.get(user_id, [])
        self.rules[user_id] = [r for r in rules if r.id != block_id]
        return len(self.rules[user_id]) != len(rules)

    async def create_preview(self, user_id, message_id, draft):
        for e in self.events.values():
            if e.user_id == user_id and e.source_message_id == message_id:
                return e.model_copy(deep=True)
        e = Event(
            **draft.model_dump(),
            id=str(uuid4()),
            user_id=user_id,
            source_message_id=message_id,
            created_at=datetime.now(UTC),
        )
        self.events[e.id] = e
        return e.model_copy(deep=True)

    async def get_event(self, user_id, event_id):
        e = self.events.get(event_id)
        return e.model_copy(deep=True) if e and e.user_id == user_id else None

    async def recent_events(self, user_id):
        return [
            e.model_copy(deep=True)
            for e in self.events.values()
            if e.user_id == user_id and e.status != Status.CANCELLED
        ]

    async def events_between(self, start, end, statuses, user_id=None):
        return [
            e.model_copy(deep=True)
            for e in self.events.values()
            if start <= e.start_at < end
            and e.status in statuses
            and (user_id is None or e.user_id == user_id)
        ]

    async def rsvp(self, user_id, event, status):
        e = self.events.get(event.id)
        if not e or e.user_id != user_id or e.version != event.version:
            return None
        e.status, e.version = status, e.version + 1
        await self.action(user_id, status.value.lower(), e.id)
        return e.model_copy(deep=True)

    async def save(self, user_id, event):
        return await self.rsvp(user_id, event, Status.SAVED)

    async def update_event(self, user_id, event, draft, cancel=False):
        e = self.events.get(event.id)
        if not e or e.user_id != user_id or e.version != event.version:
            return None
        if cancel:
            e.status = Status.CANCELLED
        else:
            for k, v in draft.model_dump().items():
                setattr(e, k, v)
        e.version += 1
        await self.action(user_id, "cancelled" if cancel else "edited", e.id)
        return e.model_copy(deep=True)

    async def create_group_event(self, user_id, chat_id, message_id, draft):
        for g in self.group_events.values():
            if (g.chat_id, g.source_message_id) == (chat_id, message_id):
                return g.model_copy(deep=True)
        g = GroupEvent(
            **draft.model_dump(include=set(EventDraft.model_fields)),
            id=str(uuid4()),
            chat_id=chat_id,
            source_message_id=message_id,
            creator_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        self.group_events[g.id] = g
        self.members.add((chat_id, user_id))
        return g.model_copy(deep=True)

    async def set_group_message(self, group_event_id, message_id, posted_at=None):
        self.group_events[group_event_id].message_id = message_id
        if posted_at:
            self.group_events[group_event_id].created_at = posted_at

    async def get_group_event(self, group_event_id):
        g = self.group_events.get(group_event_id)
        return g.model_copy(deep=True) if g else None

    async def touch_member(self, chat_id, user_id, name=None, username=None):
        self.members.add((chat_id, user_id))
        if name or username:
            self.names[(chat_id, user_id)] = (name, username)

    def linked(self, user_id, group_event_id):
        return next(
            (
                e
                for e in self.events.values()
                if e.user_id == user_id and e.group_event_id == group_event_id
            ),
            None,
        )

    async def linked_event(self, user_id, group_event_id):
        e = self.linked(user_id, group_event_id)
        return e.model_copy(deep=True) if e else None

    async def group_rsvp(self, user_id, event, status):
        g = self.group_events.get(event.id)
        if not g or g.status != "ACTIVE":
            return None
        self.members.add((g.chat_id, user_id))
        e = self.linked(user_id, g.id)
        if e is None:
            fields = g.model_dump(include=set(EventDraft.model_fields))
            e = Event(
                **fields, id=str(uuid4()), user_id=user_id, group_event_id=g.id, status=status
            )
            self.events[e.id] = e
        else:
            if not (status == Status.SAVED and e.status in (Status.GOING, Status.MAYBE)):
                e.status = status
            e.version += 1
        await self.action(user_id, "group_" + status.value.lower(), e.id)
        return e.model_copy(deep=True)

    async def group_counts(self, group_event_id):
        counts = {}
        for e in self.events.values():
            if e.group_event_id == group_event_id:
                counts[e.status.value] = counts.get(e.status.value, 0) + 1
        return counts

    async def update_group_event(self, user_id, event, draft, cancel=False):
        g = self.group_events.get(event.id)
        if (
            not g
            or g.creator_user_id != user_id
            or g.version != event.version
            or g.status != "ACTIVE"
        ):
            return None
        before = g.model_dump(mode="json")
        if cancel:
            g.status = "CANCELLED"
        else:
            for k, v in draft.model_dump().items():
                setattr(g, k, v)
            # Mirrors the SQL function: a changed session is edited on its own.
            g.edited_alone = g.edited_alone or g.series_id is not None
            g.ended_at = None
        g.version += 1
        after = g.model_dump(mode="json")
        for e in self.events.values():
            if e.group_event_id != g.id or e.status == Status.CANCELLED:
                continue
            if e.user_id != user_id and e.status in (Status.GOING, Status.MAYBE, Status.SAVED):
                await self.enqueue_notification(
                    e.user_id,
                    "CHANGE",
                    f"{g.id}:{g.version}",
                    {
                        "group_event_id": g.id,
                        "event_id": e.id,
                        "cancelled": cancel,
                        "before": before,
                        "after": after,
                    },
                )
            for k in EventDraft.model_fields:
                setattr(e, k, getattr(g, k))
            if cancel:
                e.status = Status.CANCELLED
            e.version += 1
        return g.model_copy(deep=True)

    async def get_user(self, user_id):
        user = next((u for u in self.users.values() if u.id == user_id), None)
        return user.model_copy(deep=True) if user else None

    async def list_calendars(self, user_id):
        mine = [c for c in self.calendars.values() if c.user_id == user_id]
        return [c.model_copy() for c in sorted(mine, key=lambda c: c.label.lower())]

    async def add_calendar(self, user_id, sealed, url_hash, label):
        from app.models.user import PersonalCalendar

        for c in self.calendars.values():
            if c.user_id == user_id and c.url_hash == url_hash:
                c.sealed_url, c.label, c.error = sealed, label, None
                return c.model_copy(), False
        c = PersonalCalendar(
            id=str(uuid4()), user_id=user_id, sealed_url=sealed, url_hash=url_hash, label=label
        )
        self.calendars[c.id] = c
        return c.model_copy(), True

    async def remove_calendar(self, user_id, calendar_id):
        c = self.calendars.get(calendar_id)
        if not c or c.user_id != user_id:
            return False
        del self.calendars[calendar_id]
        self.calendar_blocks.pop(calendar_id, None)
        return True

    async def replace_calendar(self, calendar_id, blocks, synced_at):
        if calendar_id not in self.calendars:
            return
        self.calendar_blocks[calendar_id] = list(blocks)
        c = self.calendars[calendar_id]
        c.synced_at, c.checked_at, c.error = synced_at, synced_at, None

    async def calendar_error(self, calendar_id, error, checked_at):
        c = self.calendars[calendar_id]
        c.error, c.checked_at = error[:300], checked_at

    async def calendars_due(self, before, limit=20):
        due = [c for c in self.calendars.values() if not c.checked_at or c.checked_at < before]
        return [c.model_copy() for c in due[:limit]]

    def personal_blocks(self, user_id, start, end):
        from app.models.timetable import BusyBlock

        return [
            BusyBlock(title=c.busy_title, start_at=a, end_at=b, source="CALENDAR")
            for c in self.calendars.values()
            if c.user_id == user_id
            for a, b in self.calendar_blocks.get(c.id, [])
            if a < end and b > start
        ]

    async def set_feed_token(self, user_id, token):
        user = self.user(user_id)
        user.calendar_feed_token, user.calendar_feed_seen_at = token, None

    async def user_by_feed_token(self, token):
        user = next((u for u in self.users.values() if u.calendar_feed_token == token), None)
        return user.model_copy(deep=True) if user else None

    async def mark_feed_seen(self, user_id, when):
        self.user(user_id).calendar_feed_seen_at = when

    async def set_user_flags(self, user_id, **flags):
        for key, value in flags.items():
            setattr(self.user(user_id), key, value)

    async def enqueue_notification(self, user_id, kind, key, payload):
        if any(
            (n["user_id"], n["kind"], n["dedupe_key"]) == (user_id, kind, key)
            for n in self.notifications
        ):
            return False
        self.notifications.append(
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "kind": kind,
                "dedupe_key": key,
                "payload": payload,
                "status": "pending",
                "attempts": 0,
                "created_at": datetime.now(UTC),
            }
        )
        return True

    async def claim_update(self, update_id):
        # Mirrors amifree_claim_update: new updates are claimed; an unfinished one is not
        # retaken until it is stale, and a finished one never is.
        state = self.updates.get(update_id)
        if state is None or (state == "processing" and update_id in self.stale_updates):
            self.updates[update_id] = "processing"
            self.stale_updates.discard(update_id)
            return True
        return False

    async def finish_update(self, update_id):
        self.updates[update_id] = "done"

    async def release_update(self, update_id):
        if self.updates.get(update_id) == "processing":
            del self.updates[update_id]

    async def notification_stats(self, now):
        counts = {"pending": 0, "sending": 0, "failed": 0}
        for n in self.notifications:
            if n["status"] in counts:
                counts[n["status"]] += 1
        open_ = [
            n["created_at"] for n in self.notifications if n["status"] in ("pending", "sending")
        ]
        age = max(0, int((now - min(open_)).total_seconds())) if open_ else None
        return counts | {"oldest_undelivered_seconds": age}

    async def claim_notifications(self, limit=50):
        due = [n for n in self.notifications if n["status"] == "pending"][:limit]
        for n in due:
            n["status"], n["attempts"] = "sending", n["attempts"] + 1
        return [dict(n) for n in due]

    async def drop_pending_notifications(self, kind, keys):
        dropped = [
            n
            for n in self.notifications
            if n["kind"] == kind and n["status"] == "pending" and n["dedupe_key"] in keys
        ]
        self.notifications = [n for n in self.notifications if n not in dropped]
        return [n["user_id"] for n in dropped]

    async def finish_notification(self, notification_id, ok, permanent):
        n = next(n for n in self.notifications if n["id"] == notification_id)
        if ok:
            n["status"] = "sent"
        else:
            n["status"] = "failed" if permanent or n["attempts"] >= 5 else "pending"

    async def create_poll(
        self, user_id, chat_id, message_id, title, minutes, start, end, deadline=None
    ):
        for p in self.polls.values():
            if (p.chat_id, p.source_message_id) == (chat_id, message_id):
                return p.model_copy(deep=True)
        p = Poll(
            id=str(uuid4()),
            chat_id=chat_id,
            source_message_id=message_id,
            creator_user_id=user_id,
            title=title,
            duration_minutes=minutes,
            window_start=start,
            window_end=end,
            deadline_at=deadline,
            created_at=datetime.now(UTC),
        )
        self.polls[p.id] = p
        self.poll_people[p.id] = [user_id]
        self.poll_created[p.id] = datetime.now(UTC)
        self.members.add((chat_id, user_id))
        return p.model_copy(deep=True)

    async def set_poll_message(self, poll_id, message_id):
        self.polls[poll_id].message_id = message_id

    async def get_poll(self, poll_id):
        p = self.polls.get(poll_id)
        return p.model_copy(deep=True) if p else None

    async def poll_join(self, poll_id, user_id):
        p = self.polls[poll_id]
        if p.status == "CLOSED":
            return None
        self.members.add((p.chat_id, user_id))
        if user_id not in self.poll_people[poll_id]:
            self.poll_people[poll_id].append(user_id)
        return len(self.poll_people[poll_id])

    async def poll_participants(self, poll_id):
        return list(self.poll_people.get(poll_id, []))

    async def poll_open(self, poll_id, user_id, slots):
        p = self.polls[poll_id]
        if p.creator_user_id != user_id or p.status != "COLLECTING":
            return None
        p.slots, p.status = slots, "OPEN"
        return p.model_copy(deep=True)

    async def poll_close(self, poll_id, user_id):
        p = self.polls[poll_id]
        if p.creator_user_id != user_id or p.status != "OPEN":
            return None
        p.status = "CLOSED"
        return p.model_copy(deep=True)

    async def poll_vote(self, poll_id, user_id, votes):
        p = self.polls[poll_id]
        if p.status != "OPEN":
            return False
        self.members.add((p.chat_id, user_id))
        for slot, answer in votes.items():
            self.poll_votes[(poll_id, user_id, int(slot))] = answer
        return True

    async def poll_tally(self, poll_id):
        tally = {}
        for (pid, _, slot), answer in self.poll_votes.items():
            if pid == poll_id:
                tally[(slot, answer)] = tally.get((slot, answer), 0) + 1
        return tally

    async def group_member_ids(self, chat_id):
        return [user_id for chat, user_id in self.members if chat == chat_id]

    async def group_responder_ids(self, group_event_id):
        return [e.user_id for e in self.events.values() if e.group_event_id == group_event_id]

    async def poll_voter_ids(self, poll_id):
        return sorted({user_id for (pid, user_id, _) in self.poll_votes if pid == poll_id})

    async def cards_to_chase(self, created_before, now):
        events = [
            g.model_copy(deep=True)
            for g in self.group_events.values()
            if g.status == "ACTIVE"
            and (g.message_id or 0) > 0
            and g.created_at <= created_before
            and g.start_at > now
        ]
        polls = [
            p.model_copy(deep=True)
            for p in self.polls.values()
            if p.status != "CLOSED"
            and self.poll_created[p.id] <= created_before
            and p.window_end > now
        ]
        return events, polls

    async def action(self, user_id, kind, event_id=None, metadata=None):
        self.actions.append((user_id, kind, event_id, metadata))

    # ---- group features ----

    async def polls_past_deadline(self, now):
        return [
            p.model_copy(deep=True)
            for p in self.polls.values()
            if p.status == "COLLECTING"
            and p.deadline_at
            and p.deadline_at <= now
            and p.window_end > now
        ]

    async def member_names(self, chat_id):
        return {
            user_id: self.names.get((chat, user_id), (None, None))
            for chat, user_id in self.members
            if chat == chat_id
        }

    async def group_answers(self, group_event_id):
        return {
            e.user_id: e.status.value
            for e in self.events.values()
            if e.group_event_id == group_event_id
        }

    async def group_events_starting(self, start, end):
        found = [
            g.model_copy(deep=True)
            for g in self.group_events.values()
            if g.status == "ACTIVE" and start <= g.start_at < end
        ]
        return sorted(found, key=lambda g: g.start_at)

    async def ended_group_events(self, now):
        return [
            g.model_copy(deep=True)
            for g in self.group_events.values()
            if g.status == "ACTIVE"
            and now - timedelta(days=7) <= g.end_at <= now
            and g.ended_at is None
            and (g.message_id or 0) > 0
        ]

    async def mark_ended(self, group_event_id, when):
        self.group_events[group_event_id].ended_at = when

    async def set_attendance_check(self, group_event_id, user_id, answer):
        self.checks[(group_event_id, user_id)] = answer

    async def attendance_checks(self, group_event_id):
        return {u: a for (g, u), a in self.checks.items() if g == group_event_id}

    async def check_in(self, group_event_id, user_id, marked_by):
        self.attended[(group_event_id, user_id)] = marked_by

    async def mark_attendance(self, group_event_id, user_id, present, marked_by):
        if present:
            self.attended[(group_event_id, user_id)] = marked_by
        else:
            self.attended.pop((group_event_id, user_id), None)

    async def attendees(self, group_event_id):
        return {u for (g, u) in self.attended if g == group_event_id}

    async def set_availability(self, poll_id, user_id, free):
        self.availability[(poll_id, user_id)] = list(free)

    async def get_availability(self, poll_id, user_id):
        found = self.availability.get((poll_id, user_id))
        return list(found) if found is not None else None

    async def poll_availability(self, poll_id):
        return {u: list(v) for (p, u), v in self.availability.items() if p == poll_id}

    async def record_payment(self, charge_id, chat_id, telegram_id, stars, days):
        if charge_id in self.payments:
            return False
        self.payments[charge_id] = (chat_id, telegram_id, stars, days)
        return True

    async def extend_group_pro(self, chat_id, days, now):
        tier, expires = self.tiers.get(chat_id, ("FREE", None))
        base = max(now, expires) if tier == "PRO" and expires else now
        until = base + timedelta(days=days)
        self.tiers[chat_id] = ("PRO", until)
        return until

    async def create_series(self, user_id, chat_id, message_id, fields):
        for s in self.series.values():
            if (s.chat_id, s.source_message_id) == (chat_id, message_id):
                return s.model_copy(deep=True)
        s = EventSeries(
            id=str(uuid4()),
            chat_id=chat_id,
            source_message_id=message_id,
            creator_user_id=user_id,
            **fields,
        )
        self.series[s.id] = s
        self.members.add((chat_id, user_id))
        return s.model_copy(deep=True)

    async def get_series(self, series_id):
        s = self.series.get(series_id)
        return s.model_copy(deep=True) if s else None

    async def update_series(self, series_id, **fields):
        current = self.series[series_id].model_dump()
        self.series[series_id] = EventSeries.model_validate(current | fields)

    async def series_sessions(self, series_id):
        found = [g for g in self.group_events.values() if g.series_id == series_id]
        return [g.model_copy(deep=True) for g in sorted(found, key=lambda g: g.occurrence_date)]

    async def create_session(self, series, day, draft, source_message_id=None):
        for g in self.group_events.values():
            if g.series_id == series.id and g.occurrence_date == day:
                return g.model_copy(deep=True)
        g = GroupEvent(
            **draft.model_dump(include=set(EventDraft.model_fields)),
            id=str(uuid4()),
            chat_id=series.chat_id,
            source_message_id=source_message_id,
            creator_user_id=series.creator_user_id,
            created_at=datetime.now(UTC),
            series_id=series.id,
            occurrence_date=day,
        )
        self.group_events[g.id] = g
        return g.model_copy(deep=True)

    async def clear_edited(self, group_event_ids):
        for group_event_id in group_event_ids:
            self.group_events[group_event_id].edited_alone = False

    async def unposted_sessions(self, start, end):
        found = await self.group_events_starting(start, end)
        return [g for g in found if g.series_id and not g.message_id]

    async def chat_events_by(self, chat_id, creator_user_id, since):
        found = [
            g.model_copy(deep=True)
            for g in self.group_events.values()
            if g.chat_id == chat_id and g.creator_user_id == creator_user_id and g.start_at >= since
        ]
        return sorted(found, key=lambda g: g.start_at)

    async def group_tier(self, chat_id, now):
        tier, expires = self.tiers.get(chat_id, ("FREE", None))
        return "PRO" if tier == "PRO" and (expires is None or expires > now) else "FREE"

    async def set_group_tier(self, chat_id, tier, expires_at=None):
        self.tiers[chat_id] = (tier, expires_at)

    async def detection_enabled(self, chat_id):
        return self.detection.get(chat_id, False)

    async def set_detection(self, chat_id, enabled, telegram_id):
        self.detection[chat_id] = enabled

    async def create_suggestion(self, chat_id, source_message_id, draft):
        for s in self.suggestions.values():
            if (s.chat_id, s.source_message_id) == (chat_id, source_message_id):
                return None
        s = Suggestion(
            id=str(uuid4()),
            chat_id=chat_id,
            source_message_id=source_message_id,
            draft=draft.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
        self.suggestions[s.id] = s
        return s.model_copy(deep=True)

    async def get_suggestion(self, suggestion_id):
        s = self.suggestions.get(suggestion_id)
        return s.model_copy(deep=True) if s else None

    async def set_suggestion_message(self, suggestion_id, message_id):
        self.suggestions[suggestion_id].message_id = message_id

    async def delete_suggestion(self, suggestion_id):
        self.suggestions.pop(suggestion_id, None)

    async def expired_suggestions(self, before):
        return [
            s.model_copy(deep=True) for s in self.suggestions.values() if s.created_at <= before
        ]

    async def extraction(self, *args):
        pass

    async def delete_user(self, user_id):
        self.users = {t: u for t, u in self.users.items() if u.id != user_id}
        self.events = {k: e for k, e in self.events.items() if e.user_id != user_id}
        self.blocks.pop(user_id, None)
        self.rules.pop(user_id, None)
        for cid in [c.id for c in self.calendars.values() if c.user_id == user_id]:
            self.calendars.pop(cid)
            self.calendar_blocks.pop(cid, None)
        self.members = {m for m in self.members if m[1] != user_id}
        self.names = {k: v for k, v in self.names.items() if k[1] != user_id}
        self.checks = {k: v for k, v in self.checks.items() if k[1] != user_id}
        self.availability = {k: v for k, v in self.availability.items() if k[1] != user_id}
        self.attended = {k: v for k, v in self.attended.items() if k[1] != user_id}
        self.notifications = [n for n in self.notifications if n["user_id"] != user_id]
        for people in self.poll_people.values():
            if user_id in people:
                people.remove(user_id)
        self.poll_votes = {k: v for k, v in self.poll_votes.items() if k[1] != user_id}
        for g in self.group_events.values():
            if g.creator_user_id == user_id:
                g.creator_user_id = None
        self.actions = [a for a in self.actions if a[0] != user_id]


class QueueExtractor:
    def __init__(self, *extractions):
        self.items = list(extractions)
        self.calls = []

    async def extract(self, text, reference, previous=None):
        self.calls.append((text, reference, previous))
        value = self.items.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
