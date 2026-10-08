import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.api.auth import AuthError, issue_token, session_key, verify_init_data, verify_token

TOKEN = "123456:fake-test-token"
NOW = 1_800_000_000


def sign(fields: dict, token: str = TOKEN) -> str:
    """Build initData the way Telegram does, for a given bot token."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields | {"hash": digest})


def init_data(telegram_id=42, auth_date=NOW, token=TOKEN, **extra):
    user = json.dumps({"id": telegram_id, "first_name": "Student"}, separators=(",", ":"))
    return sign({"auth_date": str(auth_date), "query_id": "AAF", "user": user} | extra, token)


def test_valid_init_data_yields_the_signed_user():
    assert verify_init_data(init_data(), TOKEN, 3600, NOW + 10) == 42
    # Unknown extra fields are covered by the signature rather than rejected.
    assert verify_init_data(init_data(signature="abc", chat_type="private"), TOKEN, 3600, NOW) == 42


def test_forged_or_tampered_init_data_is_rejected():
    good = init_data()
    cases = [
        "",
        "not a query string",
        good.replace("hash=", "hash=0"),
        good.replace("42", "43"),  # someone else's ID under the original signature
        init_data(token="999:another-bot"),  # signed by a different bot
        "&".join(p for p in good.split("&") if not p.startswith("hash=")),
        good + "&auth_date=1",  # duplicate field
        sign({"auth_date": str(NOW)}),  # correctly signed but no user
        sign({"auth_date": "soon", "user": '{"id":42}'}),
        sign({"auth_date": str(NOW), "user": '{"id":-5}'}),
        "x" * 9000,
    ]
    for case in cases:
        with pytest.raises(AuthError):
            verify_init_data(case, TOKEN, 3600, NOW)
    with pytest.raises(AuthError):
        verify_init_data(good, "", 3600, NOW)


def test_stale_and_future_init_data_is_rejected():
    assert verify_init_data(init_data(), TOKEN, 3600, NOW + 3600) == 42
    with pytest.raises(AuthError):
        verify_init_data(init_data(), TOKEN, 3600, NOW + 3601)
    with pytest.raises(AuthError):
        verify_init_data(init_data(auth_date=NOW + 600), TOKEN, 3600, NOW)


def test_session_tokens_expire_and_cannot_be_altered():
    key = session_key(TOKEN)
    token = issue_token(42, key, 3600, NOW)
    assert verify_token(token, key, NOW + 3599) == 42
    payload, signature = token.split(".")
    other = issue_token(43, key, 3600, NOW).split(".")[0]
    for bad in (
        "",
        "abc",
        payload + ".",
        other + "." + signature,  # another user's payload with this signature
        token + "x",
        issue_token(42, session_key("999:another-bot"), 3600, NOW),
        "a.b.c",
    ):
        with pytest.raises(AuthError):
            verify_token(bad, key, NOW)
    with pytest.raises(AuthError):
        verify_token(token, key, NOW + 3600)
    assert session_key(TOKEN, "s" * 32) == b"s" * 32 != key
