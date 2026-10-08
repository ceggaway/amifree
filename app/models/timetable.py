from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.event import EventDraft


class BusyBlock(EventDraft):
    source: str = "NUSMODS"


class BlockRule(BaseModel):
    """A user-entered busy period: one dated interval, or a weekly rule expanded on demand."""

    id: str | None = None
    user_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["ONCE", "WEEKLY"]
    start_at: datetime | None = None
    end_at: datetime | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    start_date: date | None = None
    until_date: date | None = None
