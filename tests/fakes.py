from datetime import UTC, datetime
from uuid import uuid4

from app.models.user import User
from app.schemas.event import Event, Status


class MemoryRepository:
    """Test double only: production always uses SupabaseRepository."""

    def __init__(self):
        self.users = {}
        self.blocks = {}
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
        return self.blocks.get(user_id, []), [
            e.model_copy(deep=True)
            for e in self.events.values()
            if e.user_id == user_id and e.status == Status.GOING
        ]

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

    async def action(self, user_id, kind, event_id=None, metadata=None):
        self.actions.append((user_id, kind, event_id, metadata))

    async def extraction(self, *args):
        pass

    async def delete_user(self, user_id):
        self.users = {t: u for t, u in self.users.items() if u.id != user_id}
        self.events = {k: e for k, e in self.events.items() if e.user_id != user_id}
        self.blocks.pop(user_id, None)
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
