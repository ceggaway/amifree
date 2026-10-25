"""Hand a confirmed plan to the user's own calendar, without any calendar sign-in.

Google Calendar: a pre-filled "add event" link the user opens and saves.
Apple Calendar (and Outlook): a standard .ics file. Reusing the AmIFree event id as the UID
and its version as SEQUENCE means re-sending the file after an edit updates the same event
in Apple Calendar instead of adding a second copy.
"""

import json
import re
from datetime import UTC, datetime
from html import escape
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
    # A lone \r ends a line for some calendar apps too: it must not start a new property.
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


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
        # A hint only: Mac Calendar can go to 5 minutes, an iPhone checks every 15 at best.
        "REFRESH-INTERVAL;VALUE=DURATION:PT5M",
        "X-PUBLISHED-TTL:PT5M",
    ]
    for event in sorted(events, key=lambda e: e.start_at):
        lines += _vevent(event, stamp)
    return _calendar(lines + ["END:VCALENDAR"])


def ics_filename(event: Event) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", event.title.lower()).strip("-")[:40] or "event"
    return f"{slug}-{event.start_at:%d%b}.ics".lower()


def subscribe_page(feed_url: str) -> str:
    """The page behind the "Add to Calendar" button: Telegram buttons only open http(s) links,
    so this hands the browser the webcal:// address that opens the calendar app's Subscribe
    screen. The plain link stays on the page for when the browser won't open it."""
    webcal = "webcal://" + feed_url.split("://", 1)[-1]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Add AmIFree to your calendar</title>
<style>
body{{font:17px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;
background:#fff;color:#111;max-width:480px;margin:auto}}
a.add{{display:block;text-align:center;background:#0a7cff;color:#fff;padding:14px;
border-radius:12px;text-decoration:none;font-weight:600;margin:20px 0}}
code{{word-break:break-all;background:#f2f2f5;padding:2px 4px;border-radius:4px}}
@media (prefers-color-scheme: dark){{body{{background:#111;color:#eee}}
code{{background:#2a2a2e}}}}
</style></head><body>
<h1>📅 Add AmIFree to your calendar</h1>
<p>Tap the button, then tap <b>Subscribe</b>.</p>
<a class="add" href="{escape(webcal)}">Add to Calendar</a>
<p>Nothing happened? Open this page in Safari (tap ⋯ or the compass), or copy this link and add
it in Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed
Calendar:</p>
<p><code>{escape(feed_url)}</code></p>
<p>Keep it private: anyone with the link can see your plans.</p>
<script>location.href={json.dumps(webcal)};</script>
</body></html>"""
