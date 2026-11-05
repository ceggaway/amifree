"""Consistent dates in user-facing bot messages (Singapore local time)."""

from datetime import date, datetime

from app.schemas.event import SG


def display_date(value: date | datetime, *, emoji: bool = True) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(SG)
    return ("📅 " if emoji else "") + value.strftime("%d/%m/%Y (%A)")
