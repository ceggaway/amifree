"""Mini App identity: verify Telegram's signed initData, then issue a short-lived session.

The browser is never trusted for identity. `initDataUnsafe`, posted user IDs and query
parameters are ignored; only a valid Telegram signature can produce a session.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int, now: float | None = None) -> int:
    """Return the verified Telegram user ID, or raise AuthError."""
    now = time.time() if now is None else now
    if not bot_token or not init_data or len(init_data) > 8192:
        raise AuthError("Missing or oversized init data")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AuthError("Malformed init data") from exc
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise AuthError("Duplicate fields")
    provided = fields.pop("hash", "")
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise AuthError("Invalid signature")
    try:
        auth_date = int(fields["auth_date"])
        telegram_id = int(json.loads(fields["user"])["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthError("Incomplete init data") from exc
    # A small allowance for clock skew; anything older than max_age must re-open the app.
    if auth_date > now + 60 or now - auth_date > max_age or telegram_id <= 0:
        raise AuthError("Expired init data")
    return telegram_id


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def session_key(bot_token: str, session_secret: str = "") -> bytes:
    """A dedicated secret if configured, otherwise one derived from the bot token."""
    if session_secret:
        return session_secret.encode()
    return hmac.new(bot_token.encode(), b"amifree-miniapp-session", hashlib.sha256).digest()


def issue_token(telegram_id: int, key: bytes, ttl: int, now: float | None = None) -> str:
    now = time.time() if now is None else now
    payload = _b64(json.dumps({"tid": telegram_id, "exp": int(now) + ttl}).encode())
    return payload + "." + _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def verify_token(token: str, key: bytes, now: float | None = None) -> int:
    now = time.time() if now is None else now
    try:
        payload, signature = token.split(".")
        expected = hmac.new(key, payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise AuthError("Invalid session")
        data = json.loads(_unb64(payload))
        telegram_id, expires = int(data["tid"]), int(data["exp"])
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError("Invalid session") from exc
    if expires <= now:
        raise AuthError("Session expired")
    return telegram_id
