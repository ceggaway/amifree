"""Personal reminder screens and deterministic timing input."""

import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.messages import Reply
from app.models.user import User
from app.schemas.event import SG
from app.services.date_resolution import Clarification, resolve_time

PERIODS = {"morning": "Same day morning", "evening": "Night before"}
MAX_LEAD = 30 * 24 * 60


def delivery_minute(user: User) -> int:
    return getattr(user, f"overview_{user.overview_period}_minute")


def time_label(minute: int) -> str:
    hour, minute = divmod(minute, 60)
    return (
        f"{hour % 12 or 12}" + (f":{minute:02d}" if minute else "") + ("PM" if hour >= 12 else "AM")
    )


def interval_label(minutes: int) -> str:
    parts = []
    for size, name in ((1440, "day"), (60, "hour"), (1, "minute")):
        count, minutes = divmod(minutes, size)
        if count:
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    return " ".join(parts)


def overview_label(user: User) -> str:
    return f"{PERIODS[user.overview_period]} · {time_label(delivery_minute(user))} SGT"


def keyboard(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text, callback_data=f"prefs:{action}") for text, action in row]
            for row in rows
        ]
    )


def screen(user: User, page: str = "home", *, note: str = "") -> Reply:
    def on(enabled):
        return "On" if enabled else "Off"

    overview = overview_label(user)
    interval = interval_label(user.deadline_lead_minutes) + " before"
    if page == "home":
        text = (
            "<b>⚙️ Settings</b>\n\n"
            f"🌤 <b>Daily overview · {on(user.overview_enabled)}</b>\n"
            "Your saved plans, delivered the night before or the same day morning.\n"
            f"<b>Current timing:</b> {overview}\n\n"
            f"⏰ <b>Deadline alerts · {on(user.deadline_alerts)}</b>\n"
            "Reminders before your deadlines.\n"
            f"<b>Current timing:</b> {interval}\n\nChoose a reminder to customise."
        )
        buttons = keyboard([("🌤 Daily overview", "overview"), ("⏰ Deadline alerts", "deadline")])
    elif page in ("overview", "deadline"):
        is_overview = page == "overview"
        enabled = user.overview_enabled if is_overview else user.deadline_alerts
        title = "🌤 Daily overview" if is_overview else "⏰ Deadline alerts"
        description = (
            "Receive your saved plans the night before or the same day morning."
            if is_overview
            else "Receive a reminder before each deadline."
        )
        text = (
            f"<b>{title}</b>\n\n{description}\n\n<b>Status:</b> {on(enabled)}\n"
            f"<b>Current timing:</b> {overview if is_overview else interval}"
        )
        toggle = ("Turn off" if enabled else "Turn on", f"{page}:{'off' if enabled else 'on'}")
        back = ("‹ Back", "home")
        if not enabled:
            buttons = keyboard([toggle, back])
        elif is_overview:
            buttons = keyboard(
                [
                    (f"{'✓ ' if user.overview_period == p else ''}{PERIODS[p]}", f"period:{p}")
                    for p in ("evening", "morning")
                ],
                [("Change timing", "overview:time"), toggle, back],
            )
        else:
            buttons = keyboard([("Change timing", "deadline:timing"), toggle, back])
    elif page == "overview_time":
        text = (
            "🌤 <b>Daily overview · Timing</b>\n\n"
            "Send your preferred delivery time using AM/PM or 24-hour format.\n\n"
            f"<b>Selected:</b> {PERIODS[user.overview_period]}\n"
            f"<b>Current timing:</b> {time_label(delivery_minute(user))} SGT"
        )
        buttons = keyboard([("‹ Back", "overview")])
    elif page == "deadline_timing":
        text = (
            "⏰ <b>Deadline alerts · Timing</b>\n\n"
            "Choose how long <b>before each deadline</b> to receive a reminder.\n\n"
            f"<b>Current timing:</b> {interval}"
        )
        buttons = keyboard(
            [
                (
                    f"{'✓ ' if user.deadline_lead_minutes == m else ''}{interval_label(m)}",
                    f"lead:{m}",
                )
                for m in (60, 180, 1440)
            ],
            [("Custom timing", "deadline:custom"), ("‹ Back", "deadline")],
        )
    elif page == "deadline_custom":
        text = (
            "⏰ <b>Deadline alerts · Custom timing</b>\n\n"
            "Send how far before each deadline you’d like a reminder, in minutes, hours or days.\n"
            "Choose between 1 minute and 30 days.\n\n"
            f"<b>Current timing:</b> {interval}"
        )
        buttons = keyboard([("‹ Back", "deadline_timing")])
    else:
        raise ValueError("Unknown personal reminder screen")
    if note:
        text += "\n\n" + escape(note, quote=False)
    return Reply(text, buttons, parse_mode="HTML")


def parse_delivery(text: str) -> tuple[int, str]:
    raw = " ".join(text.split()).rstrip(".!?")
    raw = re.sub(r"\s+(?:sgt|singapore time)$", "", raw, flags=re.I).rstrip(" ,")
    raw = re.sub(r"^(?:please\s+)?(?:remind me\s+)?(?:at\s+)?", "", raw, flags=re.I)
    value = resolve_time(raw)
    minute = value.hour * 60 + value.minute
    if minute < 12 * 60:
        actual = "morning"
    elif minute >= 18 * 60:
        actual = "evening"
    else:
        raise Clarification("Choose a morning time before 12PM, or a night-before time from 6PM.")
    return minute, actual


def parse_interval(text: str) -> int:
    raw = " ".join(text.lower().split()).rstrip(".!?")
    raw = re.sub(r"^(?:please\s+)?(?:remind me\s+)?", "", raw)
    raw = re.sub(r"\s+before(?:\s+(?:the\s+)?deadline)?$", "", raw)
    token = re.compile(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d)\b")
    total = Decimal(0)
    end = 0
    for match in token.finditer(raw):
        if raw[end : match.start()].strip(" ,") not in ("", "and"):
            raise Clarification("Send a duration in minutes, hours or days.")
        unit = match[2][0]
        total += Decimal(match[1]) * {"m": 1, "h": 60, "d": 1440}[unit]
        end = match.end()
    if not end or raw[end:].strip() or total != int(total) or not 1 <= total <= MAX_LEAD:
        raise Clarification("Choose a duration between 1 minute and 30 days, using whole minutes.")
    return int(total)


def overview_target(user: User, day) -> datetime:
    minute = delivery_minute(user)
    delivery_day = day - timedelta(days=1) if user.overview_period == "evening" else day
    return datetime.combine(delivery_day, time(minute // 60, minute % 60), SG)
