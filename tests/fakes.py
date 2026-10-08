from datetime import UTC, datetime
from uuid import uuid4

from app.models.user import User
from app.schemas.event import Event, EventDraft, GroupEvent, Status
from app.services.quick_blocks import expand_blocks


class MemoryRepository:
    """Test double only: production always uses SupabaseRepository."""

    def __init__(self):
        self.users = {}
        self.blocks = {}
        self.rules = {}
        self.group_events = {}
        self.members = set()
        self.events = {}
        self.actions = []
        self.available = True

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
        return self.blocks.get(user_id, []) + manual, [
            e.model_copy(deep=True)
            for e in self.events.values()
            if e.user_id == user_id and e.status in (Status.GOING, Status.SAVED)
        ]

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
            **draft.model_dump(),
            id=str(uuid4()),
            chat_id=chat_id,
            source_message_id=message_id,
            creator_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        self.group_events[g.id] = g
        self.members.add((chat_id, user_id))
        return g.model_copy(deep=True)

    async def set_group_message(self, group_event_id, message_id):
        self.group_events[group_event_id].message_id = message_id

    async def get_group_event(self, group_event_id):
        g = self.group_events.get(group_event_id)
        return g.model_copy(deep=True) if g else None

    async def touch_member(self, chat_id, user_id):
        self.members.add((chat_id, user_id))

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

    async def action(self, user_id, kind, event_id=None, metadata=None):
        self.actions.append((user_id, kind, event_id, metadata))

    async def extraction(self, *args):
        pass

    async def delete_user(self, user_id):
        self.users = {t: u for t, u in self.users.items() if u.id != user_id}
        self.events = {k: e for k, e in self.events.items() if e.user_id != user_id}
        self.blocks.pop(user_id, None)
        self.rules.pop(user_id, None)
        self.members = {m for m in self.members if m[1] != user_id}
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
