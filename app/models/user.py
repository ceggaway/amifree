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
