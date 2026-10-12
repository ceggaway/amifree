from datetime import datetime

from pydantic import BaseModel


class User(BaseModel):
    id: str
    telegram_user_id: int
    timezone: str = "Asia/Singapore"
    onboarding_status: str = "NEW"
    academic_year: str | None = None
    semester: int | None = None
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    pending: dict | None = None
    reminders_enabled: bool = True
    dm_blocked: bool = False
    calendar_feed_token: str | None = None
    calendar_feed_seen_at: datetime | None = None


class PersonalCalendar(BaseModel):
    """One calendar link a user shared. Only its busy times are ever stored."""

    id: str
    user_id: str
    sealed_url: str
    url_hash: str
    label: str
    synced_at: datetime | None = None
    checked_at: datetime | None = None
    error: str | None = None

    @property
    def busy_title(self) -> str:
        """What a clash with this calendar is called: "CCA calendar", "iPhone calendar"."""
        return self.label if self.label.lower().endswith("calendar") else f"{self.label} calendar"
