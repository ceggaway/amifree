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
    key = settings.posthog_api_key.get_secret_value()
    if not key:
        return
    from posthog import Posthog

    _client = Posthog(key, host=settings.posthog_host, disable_geoip=True)
    # Keyed so the hash can't be reversed by trying every Telegram id.
    _secret = hashlib.sha256(
        b"analytics:" + settings.telegram_bot_token.get_secret_value().encode()
    ).digest()


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
