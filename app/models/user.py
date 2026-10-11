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
    personal_calendar_url: str | None = None  # encrypted
    personal_calendar_synced_at: datetime | None = None
    personal_calendar_error: str | None = None
    personal_calendar_checked_at: datetime | None = None
