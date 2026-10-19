"""Talk to Google Calendar as AmIFree's own Google account (a service account).

A student shares their calendar with AmIFree's Google email and chooses "Make changes to
events". There is no sign-in screen for the student, so Google's app review and its
100-user limit for apps in testing do not apply. The student can stop it at any time by
removing AmIFree from "Shared with".

What this module does:
  * probe: how much access did the student give?
  * busy: busy times only (titles, places and notes are dropped here, never stored)
  * upsert_event / delete_event: keep AmIFree's own events in their calendar up to date

It never touches an event it did not create, because it only knows the ids of its own.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.schemas.event import SG, Event

log = logging.getLogger("amifree.google")

API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"
FOOTER = "Added from AmIFree"
MAX_PAGES = 10
MAX_BLOCKS = 3000
# A Google calendar is stored like a calendar link: sealed, under this prefix.
GCAL_PREFIX = "gcal:"
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def gcal_hash(email: str, secret: str) -> str:
    """Spots the same calendar connected twice, like personal_calendar.link_hash."""
    return hmac.new(
        secret.encode(), (GCAL_PREFIX + email.lower()).encode(), hashlib.sha256
    ).hexdigest()


class GoogleCalendarError(Exception):
    """Google could not be reached or refused for a reason the student can't fix. Temporary."""


class GoogleAccessLost(GoogleCalendarError):
    """The calendar isn't shared with AmIFree (any more), or the permission was lowered."""


class Access(StrEnum):
    NONE = "none"  # not shared with AmIFree
    BUSY_ONLY = "busy"  # "See only free/busy"
    READ = "read"  # "See all event details"
    WRITE = "write"  # either "Make changes" choice, or the owner


def load_key(raw: str) -> dict | None:
    """The service-account key, as JSON text or as base64 of that text (easier in Render)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        text = raw if raw.startswith("{") else base64.b64decode(raw, validate=True).decode()
        info = json.loads(text)
    except ValueError:
        return None
    needed = ("client_email", "private_key")
    return info if isinstance(info, dict) and all(info.get(k) for k in needed) else None


def google_event_id(event: Event) -> str:
    """Google accepts ids made of a-v and 0-9; a uuid without dashes qualifies. A fixed id
    means that adding the same plan twice can never make two events."""
    return event.id.replace("-", "").lower()


def event_body(event: Event) -> dict:
    description = f"{event.notes.strip()}\n\n{FOOTER}" if event.notes else FOOTER
    body = {
        "id": google_event_id(event),
        "status": "confirmed",
        "summary": event.title,
        "description": description[:2000],
        "start": {
            "dateTime": event.start_at.astimezone(SG).isoformat(),
            "timeZone": "Asia/Singapore",
        },
        "end": {"dateTime": event.end_at.astimezone(SG).isoformat(), "timeZone": "Asia/Singapore"},
        # How AmIFree recognises its own events when it reads the calendar for busy times.
        "extendedProperties": {"private": {"amifree": "1"}},
    }
    if event.location:
        body["location"] = event.location
    return body


def merge(found: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for begin, finish in sorted(found):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    return [(a, b) for a, b in merged[:MAX_BLOCKS]]


def busy_from_items(items: list[dict]) -> list[tuple[datetime, datetime]]:
    """Merged busy intervals from calendar events. The same rules as a calendar link: no
    all-day events, nothing marked free, nothing cancelled, nothing AmIFree added itself."""
    found = []
    for item in items:
        if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
            continue
        if (item.get("extendedProperties") or {}).get("private", {}).get("amifree"):
            continue
        start, end = item.get("start") or {}, item.get("end") or {}
        if "dateTime" not in start or "dateTime" not in end:
            continue  # all-day: birthdays and holidays far more often than busy time
        try:
            begin = datetime.fromisoformat(start["dateTime"]).astimezone(SG)
            finish = datetime.fromisoformat(end["dateTime"]).astimezone(SG)
        except ValueError:
            continue
        if finish > begin:
            found.append((begin, finish))
    return merge(found)


class GoogleCalendar:
    def __init__(self, client: httpx.AsyncClient, key: dict):
        self.client = client
        self.key = key
        self.email: str = key["client_email"]  # what students share their calendar with
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    # ---- signing in as AmIFree ----

    def _assertion(self, now: int) -> str:
        def b64(data: bytes) -> bytes:
            return base64.urlsafe_b64encode(data).rstrip(b"=")

        uri = self.key.get("token_uri") or "https://oauth2.googleapis.com/token"
        header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = b64(
            json.dumps(
                {"iss": self.email, "scope": SCOPE, "aud": uri, "iat": now, "exp": now + 3600}
            ).encode()
        )
        signing_input = header + b"." + claims
        private = serialization.load_pem_private_key(self.key["private_key"].encode(), None)
        signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + b64(signature)).decode()

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            now = int(time.time())
            uri = self.key.get("token_uri") or "https://oauth2.googleapis.com/token"
            try:
                response = await self.client.post(
                    uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": self._assertion(now),
                    },
                    timeout=15,
                )
            except httpx.HTTPError as exc:
                raise GoogleCalendarError("Google didn't answer just now.") from exc
            if response.status_code != 200:
                # A wrong or revoked key: only the owner can fix it, so log it for them.
                log.error("google_signin_failed status=%s", response.status_code)
                raise GoogleCalendarError("AmIFree couldn't sign in to Google.")
            data = response.json()
            self._token = data["access_token"]
            self._expires = time.time() + int(data.get("expires_in", 3600))
            return self._token

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await self._access_token()
        try:
            return await self.client.request(
                method,
                f"{API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise GoogleCalendarError("Google didn't answer just now.") from exc

    @staticmethod
    def _unexpected(response: httpx.Response) -> GoogleCalendarError:
        log.warning("google_calendar_unexpected status=%s", response.status_code)
        return GoogleCalendarError("Google Calendar had a problem just now.")

    # ---- what the student gave us ----

    async def probe(self, calendar_email: str) -> Access:
        cal = quote(calendar_email, safe="")
        response = await self._call("GET", f"/users/me/calendarList/{cal}")
        if response.status_code == 404:
            # Shared calendars aren't in AmIFree's list until it adds them.
            response = await self._call(
                "POST", "/users/me/calendarList", json={"id": calendar_email}
            )
        if response.status_code in (403, 404):
            return Access.NONE
        if response.status_code != 200:
            raise self._unexpected(response)
        role = response.json().get("accessRole", "")
        if role in ("writer", "owner"):
            return Access.WRITE
        if role == "reader":
            return Access.READ
        if role == "freeBusyReader":
            return Access.BUSY_ONLY
        return Access.NONE

    # ---- reading busy times ----

    async def busy(
        self, calendar_email: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        cal = quote(calendar_email, safe="")
        items, token = [], None
        for _ in range(MAX_PAGES):
            params = {
                "timeMin": start.astimezone(SG).isoformat(),
                "timeMax": end.astimezone(SG).isoformat(),
                "singleEvents": "true",
                "maxResults": "250",
                # Titles, places and notes are not even asked for.
                "fields": "nextPageToken,items(status,transparency,start,end,extendedProperties/private)",
            }
            if token:
                params["pageToken"] = token
            response = await self._call("GET", f"/calendars/{cal}/events", params=params)
            if response.status_code in (403, 404):
                raise GoogleAccessLost("AmIFree can't see that Google calendar any more.")
            if response.status_code != 200:
                raise self._unexpected(response)
            data = response.json()
            items += data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                break
        return busy_from_items(items)

    # ---- AmIFree's own events ----

    async def upsert_event(self, calendar_email: str, event: Event, existing: bool) -> bool:
        """Add the plan, or bring the event already added up to date. False means the student
        deleted it themselves, and AmIFree leaves it deleted."""
        cal = quote(calendar_email, safe="")
        body = event_body(event)
        if not existing:
            response = await self._call(
                "POST", f"/calendars/{cal}/events", params={"sendUpdates": "none"}, json=body
            )
            if response.status_code in (200, 201):
                return True
            if response.status_code != 409:  # 409: it was added before; update it instead
                return self._write_failed(response)
        response = await self._call(
            "PUT",
            f"/calendars/{cal}/events/{body['id']}",
            params={"sendUpdates": "none"},
            json=body,
        )
        if response.status_code == 200:
            return True
        if response.status_code in (404, 410):
            return False
        return self._write_failed(response)

    async def delete_event(self, calendar_email: str, event: Event | str):
        event_id = event if isinstance(event, str) else google_event_id(event)
        cal = quote(calendar_email, safe="")
        response = await self._call(
            "DELETE", f"/calendars/{cal}/events/{event_id}", params={"sendUpdates": "none"}
        )
        if response.status_code in (200, 204, 404, 410):
            return
        self._write_failed(response)

    def _write_failed(self, response: httpx.Response) -> bool:
        if response.status_code in (403, 404):
            raise GoogleAccessLost("AmIFree can't change that Google calendar any more.")
        raise self._unexpected(response)
