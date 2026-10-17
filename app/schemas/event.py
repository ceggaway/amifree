from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SG = ZoneInfo("Asia/Singapore")


class Extraction(BaseModel):
    """Required nullable fields keep the OpenAI structured-output schema strict."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["new", "update", "cancel", "deadline", "unknown"]
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
    SAVED = "SAVED"
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
    group_event_id: str | None = None
    created_at: datetime | None = None


class Poll(BaseModel):
    id: str
    chat_id: int
    source_message_id: int | None = None
    message_id: int | None = None
    creator_user_id: str | None = None
    title: str
    duration_minutes: int
    window_start: datetime
    window_end: datetime
    status: Literal["COLLECTING", "OPEN", "CLOSED"] = "COLLECTING"
    slots: list[dict] = []
    # Members are asked to answer by then; slots are suggested automatically when it passes.
    deadline_at: datetime | None = None
    created_at: datetime | None = None

    @field_validator("window_start", "window_end", "deadline_at")
    @classmethod
    def local(cls, value):
        return value.astimezone(SG) if value else value


class GroupEvent(EventDraft):
    """The shared card. Members' attendance lives in their own linked Event rows."""

    id: str
    chat_id: int
    source_message_id: int | None = None
    message_id: int | None = None
    creator_user_id: str | None = None
    status: Literal["ACTIVE", "CANCELLED"] = "ACTIVE"
    version: int = 1
    created_at: datetime | None = None
    # Set for one session of a recurring event.
    series_id: str | None = None
    occurrence_date: date | None = None
    edited_alone: bool = False
    # Set once the card was redrawn in its "ended" state.
    ended_at: datetime | None = None


class EventSeries(BaseModel):
    """A recurring group event: every listed weekday from first_date until until_date."""

    id: str
    chat_id: int
    source_message_id: int | None = None
    creator_user_id: str | None = None
    title: str
    weekdays: list[int]
    start_time: time
    end_time: time
    location: str | None = None
    notes: str | None = None
    first_date: date
    until_date: date
    status: Literal["ACTIVE", "STOPPED"] = "ACTIVE"


class Suggestion(BaseModel):
    """An event spotted in a group where detection is on: the proposed card, not the message."""

    id: str
    chat_id: int
    source_message_id: int
    message_id: int | None = None
    draft: dict
    created_at: datetime | None = None


class DeadlineDraft(BaseModel):
    """Something due: a moment to remember, never time the user is busy."""

    title: str = Field(min_length=1, max_length=200)
    due_at: datetime
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(SG)


class Reminder(BaseModel):
    """A quick reminder: a message at an exact time, never time the user is busy."""

    id: str
    user_id: str
    text: str = Field(min_length=1, max_length=200)
    remind_at: datetime
    done: bool = False
    created_at: datetime | None = None

    @field_validator("remind_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(SG)


class Deadline(DeadlineDraft):
    id: str
    user_id: str
    done: bool = False
    source_message_id: int | None = None
    created_at: datetime | None = None
