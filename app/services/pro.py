"""Clubs Pro: paid with Telegram Stars, per group.

Export and per-event stats are free while EXPORT_REQUIRES_PRO is off, which is the default.
The purchase flow is built so that switching it on needs no code change: /pro in a group
sends the buyer a Stars invoice in private, Telegram confirms the payment, and the group is
Pro for PRO_DAYS more days. Every charge is recorded once, so a replayed update changes
nothing. Refunds are handled by hand through Telegram.
"""

import logging
from datetime import timedelta

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
                    f" Refundable within {s.refund_window_days} days with /refund."
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
    ) -> tuple[str, bool]:
        """Record a finished payment and extend the group.

        Returns (message for the buyer, whether the charge should be refunded at once). A
        payment that cannot be matched to a group is refunded rather than left unexplained."""
        if self.validate(payload, currency, amount):
            # Telegram only delivers payments that passed pre-checkout; this is for safety.
            log.warning("payment_unexpected currency=%s amount=%s", currency, amount)
            return "I couldn't match that payment, so I'm refunding it.", True
        chat_id = int(payload[len(PAYLOAD) :])
        if await self.repo.payment_recorded(charge_id):
            return "That payment was already applied. Thank you!", False
        # Extend first, record second: if anything fails in between, Telegram retries and the
        # group gets the days again. Recording first could keep the Stars and give nothing.
        until = await self.repo.extend_group_pro(
            chat_id, self.settings.pro_days, self.scheduling.clock()
        )
        await self.repo.record_payment(
            charge_id, chat_id, telegram_id, amount, self.settings.pro_days
        )
        return (
            f"✅ Clubs Pro is active for your group until {until:%a %-d %b %Y}. Thank you!"
            f"\nChanged your mind? /refund works for {self.settings.refund_window_days} days.",
            False,
        )

    async def give_back(self, telegram_id: int, charge_id: str) -> bool:
        """Ask Telegram to return the Stars. A charge refunded earlier counts as done."""
        refund = getattr(self.scheduling, "refund_star_payment", None)
        if refund is None:
            return False
        try:
            await refund(telegram_id, charge_id)
        except Exception as exc:
            if "ALREADY_REFUNDED" in str(exc).upper():
                return True
            log.error("refund_failed error_type=%s", type(exc).__name__)
            return False
        return True

    async def refund(self, user, telegram_id: int) -> list[Reply]:
        """/refund: return the buyer's latest Clubs Pro payment and end the time it bought."""
        days = self.settings.refund_window_days
        now = self.scheduling.clock()
        payment = await self.repo.refundable_payment(telegram_id, now - timedelta(days=days))
        if not payment:
            return [
                Reply(
                    f"I can't find a Clubs Pro payment of yours from the last {days} days that is"
                    " still refundable."
                )
            ]
        if not await self.give_back(telegram_id, payment["charge_id"]):
            return [
                Reply(
                    "Telegram didn't accept the refund just now. Nothing changed; try again in a"
                    " minute, or contact the AmIFree team."
                )
            ]
        # Only the call that marks it ends the Pro time, so a double tap cannot take it twice.
        if await self.repo.mark_refunded(payment["charge_id"], now):
            await self.repo.shorten_group_pro(payment["chat_id"], payment["days"], now)
        return [
            Reply(
                f"✅ Refunded {payment['stars']} ⭐. The {payment['days']} days it bought are"
                " removed from that group's Clubs Pro."
            )
        ]
