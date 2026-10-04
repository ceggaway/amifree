"""Request-local webhook outcome, including replies remaining after a send failure."""

from contextvars import ContextVar
from dataclasses import dataclass

from telegram import Update

from app.bot.messages import Reply


@dataclass
class Delivery:
    replies: list[Reply] | None = None
    sent: int = 0
    failed: bool = False

    async def flush(self, update: Update):
        while self.sent < len(self.replies or []):
            reply = self.replies[self.sent]
            await update.effective_message.reply_text(reply.text, reply_markup=reply.keyboard)
            self.sent += 1


webhook_delivery: ContextVar[Delivery | None] = ContextVar("webhook_delivery", default=None)
