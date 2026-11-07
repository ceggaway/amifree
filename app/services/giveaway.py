"""/giveaway: proof for the Starbucks giveaway that a borrowed screenshot can't fake.

The reply shows the sender's own @handle and a check code made from it, so a screenshot
from someone else's chat shows the wrong handle, and an edited handle won't match its code.
The team checks a code with scripts/giveaway_code.py.
"""

import hashlib
import hmac
from datetime import datetime

from app.schemas.event import SG

# No 0/O or 1/I, so codes are easy to read off a screenshot.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def check_code(handle: str, secret: str) -> str:
    """Same handle and secret always give the same code, e.g. "7F3K-2QXM"."""
    key = handle.strip().lstrip("@").lower()
    digest = hmac.new(secret.encode(), f"giveaway:{key}".encode(), hashlib.sha256).digest()
    letters = "".join(ALPHABET[b % len(ALPHABET)] for b in digest[:8])
    return f"{letters[:4]}-{letters[4:]}"


NO_HANDLE = (
    "🎁 Giveaway check\n\n"
    "You need a Telegram username so we can credit your votes. Set one in Telegram "
    "Settings → Username, then send /giveaway again."
)


def giveaway_reply(handle: str, calendars: list[str], now: datetime, secret: str) -> str:
    if calendars:
        status = f"✅ Calendar connected ({', '.join(calendars)})"
        ask = "Screenshot this message and DM it to us to claim your vote."
    else:
        status = "❌ No calendar connected yet"
        ask = "Connect one with /calendar first, then send /giveaway again."
    local = now.astimezone(SG)
    checked = f"{local:%-d %b}, {local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm")
    return (
        "🎁 Giveaway check\n\n"
        f"Handle: @{handle}\n"
        f"{status}\n"
        f"Checked: {checked}\n"
        f"Code: {check_code(handle, secret)}\n\n"
        f"{ask}"
    )
