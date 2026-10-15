"""Auto-chase: privately nudge members who haven't answered a card or poll.

Bots cannot list a group's members, so "members" means people who have used AmIFree in
that group. Each card has up to two automatic stages, halfway to the deadline (or a day after
posting, whichever is sooner) and three hours before it. A person is nudged at most once per
stage, whether the organiser pressed Nudge or the automatic chase fired.

The deadline of a poll is its reply-by time; the deadline of an event card is its start.
"""

from datetime import datetime, timedelta

from app.schemas.event import GroupEvent, Poll

CHASE_AFTER = timedelta(hours=24)
FINAL_BEFORE = timedelta(hours=3)
DEFAULT_REPLY_WINDOW = timedelta(hours=48)


def nudge_key(card: GroupEvent | Poll) -> str:
    return ("event:" if isinstance(card, GroupEvent) else "poll:") + card.id


def response_deadline(card: GroupEvent | Poll) -> datetime:
    if isinstance(card, GroupEvent):
        return card.start_at
    if card.deadline_at:
        return card.deadline_at
    return (card.created_at or card.window_start) + DEFAULT_REPLY_WINDOW


def stages(card: GroupEvent | Poll) -> list[tuple[str, datetime]]:
    """The automatic chase stages in time order, as (name, when it becomes due)."""
    deadline = response_deadline(card)
    created = card.created_at or deadline - DEFAULT_REPLY_WINDOW
    first = min(created + CHASE_AFTER, created + (deadline - created) / 2)
    final = max(deadline - FINAL_BEFORE, first)
    return [("mid", first), ("final", final)]


def current_stage(card: GroupEvent | Poll, now: datetime) -> str | None:
    """The latest stage already due, or None while the card is still fresh."""
    due = [name for name, when in stages(card) if when <= now]
    return due[-1] if due else None


async def non_responders(repo, card: GroupEvent | Poll) -> list[str]:
    members = set(await repo.group_member_ids(card.chat_id))
    if isinstance(card, GroupEvent):
        answered = set(await repo.group_responder_ids(card.id))
    elif card.status == "OPEN":
        answered = set(await repo.poll_voter_ids(card.id))
    else:
        answered = set(await repo.poll_participants(card.id))
    return sorted(members - answered - {card.creator_user_id})


async def enqueue_nudges(repo, card: GroupEvent | Poll, now: datetime) -> int:
    kind = "event" if isinstance(card, GroupEvent) else "poll"
    key = f"{nudge_key(card)}:{current_stage(card, now) or 'start'}"
    queued = 0
    for user_id in await non_responders(repo, card):
        queued += await repo.enqueue_notification(
            user_id, "NUDGE", key, {"type": kind, "id": card.id}
        )
    return queued


async def enqueue_auto(repo, now: datetime) -> int:
    events, polls = await repo.cards_to_chase(now, now)
    queued = 0
    for card in [*events, *polls]:
        # A session whose card isn't in the group yet has nobody to chase.
        if (card.message_id or 0) > 0 and current_stage(card, now):
            queued += await enqueue_nudges(repo, card, now)
    return queued


def card_link(card: GroupEvent | Poll) -> str:
    """A link back to the card; Telegram only supports these for supergroups."""
    chat = str(card.chat_id)
    if chat.startswith("-100") and card.message_id:
        return f"\nOpen it in the group: https://t.me/c/{chat[4:]}/{card.message_id}"
    return ""
