"""Product analytics in PostHog. Off unless POSTHOG_API_KEY is set.

People are identified by a keyed hash of their Telegram id, never the id itself, and
events carry names and small labels only: no message text, titles, links or chat ids.
"""

import hashlib
import hmac
import logging
import re
from contextvars import ContextVar

log = logging.getLogger(__name__)

_person: ContextVar[str | None] = ContextVar("analytics_person", default=None)
_client = None
_secret = b""
# Callback data is "word:word:<id or nonce>..."; only the leading words name the button.
_WORD = re.compile(r"[a-z_]{1,12}")


def configure(settings):
    """Start sending events. Called once at startup; tests never call it, so nothing is sent."""
    global _client, _secret
    # Stripped: a key pasted into a hosting dashboard often brings a space or newline along.
    key = settings.posthog_api_key.get_secret_value().strip()
    host = settings.posthog_host.strip().rstrip("/")
    if not key:
        log.info("analytics_off reason=no_posthog_api_key")
        return
    from posthog import Posthog

    _client = Posthog(key, host=host, disable_geoip=True, on_error=_upload_failed)
    # PostHog accepts unknown keys and drops their events, so show enough to compare it with
    # the project's key (public, unlike personal phx_ keys) when nothing arrives.
    log.info("analytics_on host=%s key=%s...%s", host, key[:4], key[-4:])
    # Keyed so the hash can't be reversed by trying every Telegram id.
    _secret = hashlib.sha256(
        b"analytics:" + settings.telegram_bot_token.get_secret_value().encode()
    ).digest()


def _upload_failed(error, batch):
    # Runs on PostHog's sending thread; without this a bad key or host fails silently.
    log.warning(
        "analytics_failed error_type=%s events=%s detail=%s",
        type(error).__name__,
        len(batch),
        str(error)[:200],
    )


def shutdown():
    global _client
    if _client is not None:
        _client.shutdown()  # sends anything still queued
        _client = None


def identify(telegram_id: int):
    """Events from here on in this update or request belong to this person."""
    if _client is not None:
        _person.set(hmac.new(_secret, str(telegram_id).encode(), hashlib.sha256).hexdigest()[:24])


def button(data: str) -> str:
    """'c:going:3f2a…' -> 'c:going'; ids, nonces and page numbers are dropped."""
    words = []
    for part in data.split(":")[:2]:
        if not _WORD.fullmatch(part):
            break
        words.append(part)
    return ":".join(words) or "other"


def track(event: str, **properties):
    person = _person.get()
    if _client is None or person is None:
        return
    try:
        _client.capture(event, distinct_id=person, properties=properties)
    except Exception as exc:
        # Analytics must never break a reply.
        log.warning("analytics_failed error_type=%s event=%s", type(exc).__name__, event)
