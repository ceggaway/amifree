"""Auto-chase: privately nudge members who haven't answered a card or poll.

Bots cannot list a group's members, so "members" means people who have used AmIFree in
that group. Each person is nudged at most once per card, whether the organiser pressed
Nudge or the automatic 24-hour chase fired.
"""

from datetime import datetime, timedelta

from app.schemas.event import GroupEvent, Poll

CHASE_AFTER = timedelta(hours=24)


def nudge_key(card: GroupEvent | Poll) -> str:
    return ("event:" if isinstance(card, GroupEvent) else "poll:") + card.id


async def non_responders(repo, card: GroupEvent | Poll) -> list[str]:
    members = set(await repo.group_member_ids(card.chat_id))
    if isinstance(card, GroupEvent):
        answered = set(await repo.group_responder_ids(card.id))
    elif card.status == "OPEN":
        answered = set(await repo.poll_voter_ids(card.id))
    else:
        answered = set(await repo.poll_participants(card.id))
    return sorted(members - answered - {card.creator_user_id})


async def enqueue_nudges(repo, card: GroupEvent | Poll) -> int:
    kind = "event" if isinstance(card, GroupEvent) else "poll"
    queued = 0
    for user_id in await non_responders(repo, card):
        queued += await repo.enqueue_notification(
            user_id, "NUDGE", nudge_key(card), {"type": kind, "id": card.id}
        )
    return queued


async def enqueue_auto(repo, now: datetime) -> int:
    events, polls = await repo.cards_to_chase(now - CHASE_AFTER, now)
    queued = 0
    for card in [*events, *polls]:
        queued += await enqueue_nudges(repo, card)
    return queued


def card_link(card: GroupEvent | Poll) -> str:
    """A link back to the card; Telegram only supports these for supergroups."""
    chat = str(card.chat_id)
    if chat.startswith("-100") and card.message_id:
        return f"\nOpen it in the group: https://t.me/c/{chat[4:]}/{card.message_id}"
    return ""
