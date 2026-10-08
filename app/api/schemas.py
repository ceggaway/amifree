from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    status: Literal["SAVED", "GOING", "MAYBE", "DECLINED"] | None = None
    # Going over a definite clash must be acknowledged, as with the bot's "Going anyway".
    force: bool = False
    cancel: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)
