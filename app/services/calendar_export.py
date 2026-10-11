"""Hand a confirmed plan to the user's own calendar, without any calendar sign-in.

Google Calendar: a pre-filled "add event" link the user opens and saves.
Apple Calendar (and Outlook): a standard .ics file. Reusing the AmIFree event id as the UID
and its version as SEQUENCE means re-sending the file after an edit updates the same event
in Apple Calendar instead of adding a second copy.
"""

import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from app.schemas.event import Event

GOOGLE_TEMPLATE = "https://calendar.google.com/calendar/render"
FOOTER = "Added from AmIFree"


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _description(event: Event) -> str:
    return f"{event.notes.strip()}\n\n{FOOTER}" if event.notes else FOOTER


def google_link(event: Event) -> str:
    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": f"{_utc(event.start_at)}/{_utc(event.end_at)}",
        "ctz": "Asia/Singapore",
        "details": _description(event)[:500],
    }
    if event.location:
        params["location"] = event.location
    return f"{GOOGLE_TEMPLATE}?{urlencode(params)}"


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, current = [], b""
    for char in line:
        encoded = char.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def _vevent(event: Event, stamp: str, cancelled: bool = False) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.id}@amifree",
        f"DTSTAMP:{stamp}",
        f"SEQUENCE:{event.version}",
        f"DTSTART:{_utc(event.start_at)}",
        f"DTEND:{_utc(event.end_at)}",
        f"SUMMARY:{_escape(event.title)}",
        f"DESCRIPTION:{_escape(_description(event))}",
    ]
    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    if cancelled:
        lines.append("STATUS:CANCELLED")
    return lines + ["END:VEVENT"]


def _calendar(lines: list[str]) -> bytes:
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()


def build_ics(event: Event, *, now: datetime | None = None, cancelled: bool = False) -> bytes:
    """One event, sent as a file the user taps to add."""
    stamp = _utc(now or datetime.now(UTC))
    return _calendar(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//AmIFree//Telegram scheduling//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:CANCEL" if cancelled else "METHOD:PUBLISH",
            *_vevent(event, stamp, cancelled),
            "END:VCALENDAR",
        ]
    )


def build_feed(events: list[Event], *, now: datetime | None = None) -> bytes:
    """The subscribed calendar: every current plan. A plan that is dropped or cancelled is
    simply absent, and the subscribing calendar app removes it on its next refresh."""
    stamp = _utc(now or datetime.now(UTC))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AmIFree//Telegram scheduling//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:AmIFree",
        "X-WR-TIMEZONE:Asia/Singapore",
        # Ask calendar apps to check back often; each app decides how often it really does.
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
        "X-PUBLISHED-TTL:PT15M",
    ]
    for event in sorted(events, key=lambda e: e.start_at):
        lines += _vevent(event, stamp)
    return _calendar(lines + ["END:VCALENDAR"])


def ics_filename(event: Event) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", event.title.lower()).strip("-")[:40] or "event"
    return f"{slug}-{event.start_at:%d%b}.ics".lower()
