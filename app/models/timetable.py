from app.schemas.event import EventDraft


class BusyBlock(EventDraft):
    source: str = "NUSMODS"
