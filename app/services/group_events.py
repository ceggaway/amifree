"""Bot-owned event cards in group chats.

The group only ever sees the card and its headcount. Anything derived from a member's
own schedule is returned as a private alert for the person who tapped.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.bot.messages import Reply, group_card, private_verdict
from app.schemas.event import GroupEvent, Status
from app.services.date_resolution import Clarification, validate_extraction

log = logging.getLogger(__name__)
USAGE = "Reply /event to a message with the plan, or send /event Dinner Fri 7–9pm at Vivo."
GONE = "This card is no longer available."
RSVP = {
    "go": (Status.GOING, "You're Going."),
    "maybe": (Status.MAYBE, "Marked Maybe."),
    "no": (Status.DECLINED, "Marked Can't make it."),
    "save": (Status.SAVED, "Saved to your /plans."),
}


@dataclass
class GroupResult:
    alert: str | None = None  # shown only to the person who tapped
    card: Reply | None = None  # replaces the bot's own card message
    post: Reply | None = None  # a new message in the group
    group_event_id: str | None = None  # set when `post` is a new card to record
    url: str | None = None  # opens the private chat with a start parameter


def parse_id(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


class GroupEventService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo

    async def render(self, event: GroupEvent) -> Reply:
        return group_card(event, await self.repo.group_counts(event.id))

    async def create(
        self, telegram_id: int, chat_id: int, message_id: int, text: str, reference: datetime
    ) -> GroupResult:
        text = text.strip()
        if not text:
            return GroupResult(post=Reply(USAGE))
        if len(text) > 4000:
            return GroupResult(post=Reply("That message is too long for an event card."))
        user = await self.repo.ensure_user(telegram_id)
        extracted = await self.scheduling.extractor.extract(text, reference, None)
        if extracted.kind == "unknown":
            return GroupResult(post=Reply("I couldn't find an event in that message. " + USAGE))
        try:
            draft = validate_extraction(
                extracted,
                text,
                reference,
                default_minutes=self.scheduling.settings.default_event_duration_minutes,
            )
        except Clarification as exc:
            return GroupResult(
                post=Reply(
                    f"I need a bit more to make a card. {exc}\nSend /event with the full plan."
                )
            )
        event = await self.repo.create_group_event(user.id, chat_id, message_id, draft)
        if event.message_id:
            # A retried delivery of the same command: the card is already posted.
            return GroupResult()
        await self.scheduling.safe_action(user.id, "group_event")
        return GroupResult(post=await self.render(event), group_event_id=event.id)

    async def posted(self, group_event_id: str, message_id: int):
        await self.repo.set_group_message(group_event_id, message_id)

    async def callback(self, telegram_id: int, chat_id: int, data: str) -> GroupResult:
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        if not event_id:
            return GroupResult(alert="That button isn't valid.")
        event = await self.repo.get_group_event(event_id)
        # A card only works in the chat it was posted to.
        if not event or event.chat_id != chat_id:
            return GroupResult(alert=GONE)
        user = await self.repo.ensure_user(telegram_id)
        await self.repo.touch_member(chat_id, user.id)
        if event.status == "CANCELLED":
            return GroupResult(alert="This event was cancelled.", card=await self.render(event))
        action = parts[1]
        linked = await self.repo.linked_event(user.id, event.id)
        availability = await self.scheduling.availability(
            user, event, linked.id if linked else None
        )
        if action == "free":
            return GroupResult(alert=private_verdict(availability))
        if action not in RSVP:
            return GroupResult(alert="That action isn't available.")
        status, text = RSVP[action]
        if not await self.repo.group_rsvp(user.id, event, status):
            return GroupResult(alert=GONE)
        if status in (Status.GOING, Status.SAVED):
            text = private_verdict(availability, text)
        return GroupResult(alert=text, card=await self.render(event))
