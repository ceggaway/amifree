from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    init_data: str = Field(min_length=1, max_length=8192)


class BlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=200)
    # False returns the resolved block for review; True stores it.
    confirm: bool = False


class PlanPatch(BaseModel):
    """Every change names the version it was made against; stale writes are refused."""

    model_config = ConfigDict(extra="forbid")
    version: int
    status: Literal["SAVED", "GOING", "DECLINED"] | None = None
    # Going over a definite clash must be acknowledged, as with the bot's "Going anyway".
    force: bool = False
    cancel: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class AvailabilityRequest(BaseModel):
    """The half-hour cells (ISO start times) a member is free in."""

    model_config = ConfigDict(extra="forbid")
    free: list[Annotated[str, Field(min_length=10, max_length=40)]] = Field(max_length=800)


RequestId = Annotated[str, Field(min_length=32, max_length=36)]


class GroupEventForm(BaseModel):
    """A new event card from the Mini App. request_id makes a retried submit post one card."""

    model_config = ConfigDict(extra="forbid")
    request_id: RequestId
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)
    # Repeat on these weekdays (0 = Monday) until `until`; empty for a one-off event.
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(default=[], max_length=7)
    until: date | None = None


class PollForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: RequestId
    title: str = Field(min_length=1, max_length=200)
    minutes: int = Field(ge=30, le=480)
    first: date
    last: date
    reply_by: date | None = None


class RsvpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["GOING", "DECLINED"]
    # Going over a definite clash must be acknowledged, as with the bot's "Going anyway".
    force: bool = False


class GroupEventPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int


class SeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["all", "stop"]


class AttendanceRequest(BaseModel):
    """Mark one person (by the id the event page gave), or everyone who said Going."""

    model_config = ConfigDict(extra="forbid")
    user_id: str | None = Field(default=None, max_length=36)
    present: bool = True


class PollVoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: int = Field(ge=0, le=2)
    answer: Literal["FREE", "BUSY"]  # vote for this time, or take the vote back


class PollPickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: int = Field(ge=0, le=2)


class PollPickTimeRequest(BaseModel):
    """Any start time from the heatmap; the poll's own length sets the end."""

    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=120, ge=1, le=365)


class DetectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reminders_enabled: bool
