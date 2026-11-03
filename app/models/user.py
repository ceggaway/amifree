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
    # Group check-ins ("Still coming?") and nudges to answer group plans and polls.
    reminders_enabled: bool = True
    # The evening summary, and its Singapore hour (None: NIGHTLY_HOUR). Migration 018.
    summary_enabled: bool = True
    summary_hour: int | None = None
    # The morning "Today" list of deadlines and the 3-hours-before alert. Migration 018.
    deadline_alerts: bool = True
    dm_blocked: bool = False
    calendar_feed_token: str | None = None
    calendar_feed_seen_at: datetime | None = None
    # "apple", "outlook" or "google": the app that fetches the AmIFree calendar (migration 017).
    calendar_feed_app: str | None = None
    google_connected: bool = False


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


class GoogleConnection(BaseModel):
    """A calendar the user shared with AmIFree's Google account, so plans can be added to it."""

    user_id: str
    calendar_email: str
    calendar_id: str | None = None
    connected_at: datetime | None = None
    synced_at: datetime | None = None
    error: str | None = None
