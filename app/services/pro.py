"""Clubs Pro: paid with Telegram Stars, per group.

Export and per-event stats are free while EXPORT_REQUIRES_PRO is off, which is the default.
The purchase flow is built so that switching it on needs no code change: /pro in a group
sends the buyer a Stars invoice in private, Telegram confirms the payment, and the group is
Pro for PRO_DAYS more days. Every charge is recorded once, so a replayed update changes
nothing. Refunds are handled by hand through Telegram.
"""

import logging
from datetime import datetime

from app.bot.messages import Reply
from app.services.group_events import GroupResult

log = logging.getLogger(__name__)
PAYLOAD = "pro:"


class ProService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo

    @property
    def settings(self):
        return self.scheduling.settings

    async def status(self, chat_id: int) -> GroupResult:
        s = self.settings
        if not s.export_requires_pro:
            return GroupResult(
                post=Reply(
                    "Clubs Pro is free for now: /stats, /export and /attendance are open to"
                    " everyone in this group."
                )
            )
        now = self.scheduling.clock()
        if chat_id in s.pro_chats():
            return GroupResult(post=Reply("Clubs Pro is active for this group."))
        if await self.repo.group_tier(chat_id, now) == "PRO":
            return GroupResult(post=Reply("Clubs Pro is active for this group."))
        return GroupResult(
            post=Reply(
                f"Clubs Pro unlocks the attendance CSV and per-event turnout for {s.pro_days}"
                f" days: {s.pro_price_stars} ⭐. I'm sending you the invoice privately."
            ),
            invoice={
                "chat_id": chat_id,
                "stars": s.pro_price_stars,
                "days": s.pro_days,
                "title": "AmIFree Clubs Pro",
                "description": (
                    f"Attendance export and turnout stats for this group, {s.pro_days} days."
                ),
            },
        )

    def validate(self, payload: str, currency: str, amount: int) -> str | None:
        """None when the pre-checkout is acceptable, else the reason to show the buyer."""
        if not payload.startswith(PAYLOAD) or not payload[len(PAYLOAD) :].lstrip("-").isdigit():
            return "That invoice isn't valid. Use /pro in your group."
        if currency != "XTR" or amount != self.settings.pro_price_stars:
            return "The price changed. Use /pro in your group for a new invoice."
        return None

    async def complete(
        self, telegram_id: int, payload: str, charge_id: str, currency: str, amount: int
    ) -> str:
        """Record a finished payment and extend the group. Returns the message for the buyer."""
        if self.validate(payload, currency, amount):
            # Telegram only delivers payments that passed pre-checkout; this is for safety.
            log.warning("payment_unexpected currency=%s amount=%s", currency, amount)
            return "I couldn't match that payment. Please contact the AmIFree team."
        chat_id = int(payload[len(PAYLOAD) :])
        fresh = await self.repo.record_payment(
            charge_id, chat_id, telegram_id, amount, self.settings.pro_days
        )
        if not fresh:
            return "That payment was already applied. Thank you!"
        until = await self.repo.extend_group_pro(
            chat_id, self.settings.pro_days, self.scheduling.clock()
        )
        return f"✅ Clubs Pro is active for your group until {until:%a %-d %b %Y}. Thank you!"

    @staticmethod
    def describe(until: datetime) -> str:
        return f"{until:%-d %b %Y}"
