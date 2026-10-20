"""Request-local webhook outcome, including replies remaining after a send failure."""

from contextvars import ContextVar
from dataclasses import dataclass

from telegram import Update

from app.bot.messages import Reply, send_reply


@dataclass
class Delivery:
    replies: list[Reply] | None = None
    sent: int = 0
    failed: bool = False
    # True while this update holds a database claim that is not yet finished or released.
    claimed: bool = False

    async def flush(self, update: Update):
        while self.sent < len(self.replies or []):
            reply = self.replies[self.sent]
            await send_reply(update, reply)
            self.sent += 1


webhook_delivery: ContextVar[Delivery | None] = ContextVar("webhook_delivery", default=None)

# The tapping/sending user's Telegram handle for this request, so a service method can
# attach it to named feedback without threading it through every call signature.
telegram_profile: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "telegram_profile", default=(None, None)
)
