from datetime import datetime
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SG = ZoneInfo("Asia/Singapore")


class Extraction(BaseModel):
    """Required nullable fields keep the OpenAI structured-output schema strict."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["new", "update", "cancel", "unknown"]
    title: str | None
    date_text: str | None
    start_time_text: str | None
    end_time_text: str | None
    end_date_text: str | None
    location: str | None
    notes: str | None
    match_title: str | None
    ambiguity: str | None


class Status(StrEnum):
    PREVIEW = "PREVIEW"
    GOING = "GOING"
    MAYBE = "MAYBE"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


class EventDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)
    assumed_duration: bool = False

    @field_validator("start_at", "end_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(SG)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_at <= self.start_at:
            raise ValueError("End must follow start")
        if (self.end_at - self.start_at).total_seconds() > 7 * 86400:
            raise ValueError("Events longer than seven days need clarification")
        return self


class Event(EventDraft):
    id: str
    user_id: str
    status: Status = Status.PREVIEW
    version: int = 1
    source_message_id: int | None = None
    created_at: datetime | None = None
