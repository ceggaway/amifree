"""Read busy times from a personal calendar link (Google, iCloud, Outlook).

The user pastes their calendar's private iCal address once. AmIFree keeps only the busy
intervals, never titles, places, notes or attendees, and stores the link encrypted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx
import icalendar
import recurring_ical_events
from cryptography.fernet import Fernet, InvalidToken

from app.schemas.event import SG

ALLOWED_HOSTS = (
    "calendar.google.com",
    "icloud.com",
    "outlook.office365.com",
    "outlook.office.com",
    "outlook.live.com",
)
WINDOW_PAST, WINDOW_AHEAD = timedelta(days=1), timedelta(days=120)
MAX_BYTES = 5_000_000
MAX_BLOCKS = 3000
MAX_REDIRECTS = 3
URL = re.compile(r"(?:webcals?|https?)://\S+", re.IGNORECASE)


class CalendarLinkError(ValueError):
    """A problem the user can fix; the message is shown to them as is."""


class CalendarLinkGone(CalendarLinkError):
    """The calendar provider says the link no longer exists or is no longer shared."""


def _allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def normalise(link: str) -> str:
    parts = urlsplit(link.strip())
    scheme = parts.scheme.lower()
    if scheme in ("webcal", "webcals"):
        scheme = "https"
    if scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise CalendarLinkError("Please send the calendar link starting with https:// or webcal://")
    if not _allowed(parts.hostname) or parts.port not in (None, 443):
        raise CalendarLinkError(
            "I can read Google Calendar, iCloud and Outlook calendar links. /calendar shows "
            "where to find yours."
        )
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def find_link(text: str, own_base: str = "") -> str | None:
    """The calendar link in a message that is just a link; None for anything else."""
    text = text.strip()
    if not URL.fullmatch(text):
        return None
    host = (urlsplit(text).hostname or "").lower()
    if own_base and host and host == (urlsplit(own_base).hostname or "").lower():
        raise CalendarLinkError(
            "That's your AmIFree calendar link. Paste it into your calendar app, not here. "
            "/calendar shows the steps."
        )
    is_calendar = _allowed(host) or urlsplit(text).path.lower().endswith(".ics")
    is_calendar = is_calendar or text.lower().startswith(("webcal://", "webcals://"))
    return normalise(text) if is_calendar else None


# ---- storing the link: it reveals the whole calendar, so it is never kept in the clear ----


def cipher(secret: str) -> Fernet:
    key = hashlib.sha256(b"amifree-personal-calendar-link:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(link: str, secret: str) -> str:
    return cipher(secret).encrypt(link.encode()).decode()


def link_hash(link: str, secret: str) -> str:
    """Spots the same link sent twice without storing anything that reveals it."""
    return hmac.new(secret.encode(), normalise(link).encode(), hashlib.sha256).hexdigest()


def unseal(token: str, secret: str) -> str | None:
    try:
        return cipher(secret).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ---- reading busy times ----


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=SG)  # floating time
    return None  # a date: an all-day event


def busy_intervals(data: bytes, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Merged busy intervals overlapping [start, end). Titles and details are discarded here."""
    try:
        calendar = icalendar.Calendar.from_ical(data)
        occurrences = recurring_ical_events.of(calendar, skip_bad_series=True).between(start, end)
    except Exception as exc:
        raise CalendarLinkError("That link didn't give me a calendar I could read.") from exc
    found = []
    for event in occurrences:
        if event.name != "VEVENT":
            continue
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue  # marked "free" in the calendar
        if str(event.get("UID", "")).endswith("@amifree"):
            continue  # AmIFree's own feed: those plans are already counted
        begin = _as_datetime(event.get("DTSTART") and event.decoded("DTSTART"))
        if begin is None:
            continue  # all-day events are birthdays and holidays far more often than busy time
        if event.get("DTEND") is not None:
            finish = _as_datetime(event.decoded("DTEND"))
        elif event.get("DURATION") is not None:
            finish = begin + event.decoded("DURATION")
        else:
            finish = None
        if finish is None or finish <= begin:
            continue
        begin, finish = max(begin, start), min(finish, end)
        if finish > begin:
            found.append((begin.astimezone(SG), finish.astimezone(SG)))
    merged: list[list[datetime]] = []
    for begin, finish in sorted(found):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    return [(a, b) for a, b in merged[:MAX_BLOCKS]]


class CalendarReader:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self, link: str) -> bytes:
        url = normalise(link)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with self.client.stream(
                    "GET", url, follow_redirects=False, timeout=15
                ) as response:
                    if response.is_redirect:
                        # Every hop is checked again: a redirect must not reach other hosts.
                        url = normalise(str(response.url.join(response.headers["location"])))
                        continue
                    if response.status_code in (401, 403, 404, 410):
                        raise CalendarLinkGone(
                            "That calendar link doesn't work any more. It may have been reset or "
                            "unshared. Send me the new link, or /calendar to disconnect."
                        )
                    if response.status_code != 200:
                        raise CalendarLinkError(
                            "Your calendar provider didn't answer just now. I'll try again later."
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise CalendarLinkError(
                                "That calendar is too large for me to read. Try sharing one "
                                "calendar instead of all of them."
                            )
            except httpx.HTTPError as exc:
                raise CalendarLinkError(
                    "I couldn't reach your calendar just now. I'll try again later."
                ) from exc
            if b"BEGIN:VCALENDAR" not in bytes(body[:4096]).upper():
                raise CalendarLinkError(
                    "That link isn't a calendar file. Look for the iCal (.ics) address."
                )
            return bytes(body)
        raise CalendarLinkError("That link redirected too many times.")

    async def read(self, link: str, now: datetime) -> CalendarData:
        data = await self.fetch(link)
        busy = busy_intervals(data, now - WINDOW_PAST, now + WINDOW_AHEAD)
        return CalendarData(calendar_label(data, link), busy)

    async def busy(self, link: str, now: datetime) -> list[tuple[datetime, datetime]]:
        return (await self.read(link, now)).busy


MAX_CALENDARS = 5


def fallback_label(link: str) -> str:
    host = (urlsplit(link).hostname or "").lower()
    if host.endswith("icloud.com"):
        return "iPhone calendar"
    if host.endswith("google.com"):
        return "Google Calendar"
    return "Outlook calendar"


def calendar_label(data: bytes, link: str) -> str:
    """The calendar's own name ("CCA", "Events"): its name only, never an event's."""
    try:
        name = str(icalendar.Calendar.from_ical(data).get("X-WR-CALNAME", ""))
    except Exception:
        name = ""
    name = " ".join(name.split())[:40]
    # Google often names the main calendar after the account's email address.
    return name if name and "@" not in name else fallback_label(link)


@dataclass(frozen=True)
class CalendarData:
    label: str
    busy: list


def window_covers(synced_at: datetime | None, start: datetime, end: datetime) -> bool:
    return bool(synced_at) and start >= synced_at - WINDOW_PAST and end <= synced_at + WINDOW_AHEAD
