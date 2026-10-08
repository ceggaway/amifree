from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.event import Event, GroupEvent, Poll, Status


def event_keyboard(
    event: Event, clash: bool = False, possible: tuple[Event, ...] = ()
) -> InlineKeyboardMarkup:
    def button(label, action, target=event):
        return InlineKeyboardButton(label, callback_data=f"{action}:{target.id}:{target.version}")

    go = "force" if clash else "go"
    rows = [
        [button("Going anyway" if clash else "Going", go), button("Maybe", "maybe")],
        # Shared group plans are edited once by their organiser, not per member.
        [button("Can't make it", "decline")]
        + ([] if event.group_event_id else [button("Edit", "edit")]),
    ]
    if event.status != Status.SAVED:
        rows.append([button("Save", "save")])
    if possible:
        # Choosing one never deletes the other; it only commits the chosen plan.
        rows.append([button("Attend this one", go)])
        rows.extend([button(f"Attend {other.title[:40]}", "go", other)] for other in possible[:3])
    return InlineKeyboardMarkup(rows)


def group_keyboard(event: GroupEvent) -> InlineKeyboardMarkup:
    # No version in the data: a tap always applies to the card's latest state.
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"g:{action}:{event.id}")

    return InlineKeyboardMarkup(
        [
            [button("Going", "go"), button("Maybe", "maybe"), button("Can't", "no")],
            [button("Am I free?", "free"), button("Save", "save")],
            # Organiser only; everyone else gets a private refusal.
            [button("Edit", "edit"), button("Cancel event", "cancel")],
        ]
    )


def poll_keyboard(poll: Poll) -> InlineKeyboardMarkup:
    def button(label, action, extra=""):
        data = f"p:{action}:{poll.id}" + (f":{extra}" if extra else "")
        return InlineKeyboardButton(label, callback_data=data)

    if poll.status == "COLLECTING":
        return InlineKeyboardMarkup(
            [[button("Count me in", "join")], [button("Suggest slots (organiser)", "suggest")]]
        )
    rows = [[button("Fill mine", "fill"), button("Count me in", "join")]]
    for index in range(len(poll.slots)):
        rows.append(
            [
                button(f"{index + 1} ✅", "v", f"{index}F"),
                button(f"{index + 1} 🤔", "v", f"{index}M"),
                button(f"{index + 1} ❌", "v", f"{index}B"),
            ]
        )
    rows.append(
        [button(f"Pick {index + 1}", "pick", str(index)) for index in range(len(poll.slots))]
    )
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(nonce: str, label="Confirm update") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"confirm:{nonce}"),
                InlineKeyboardButton("Keep unchanged", callback_data=f"dismiss:{nonce}"),
            ]
        ]
    )


def remove_block_keyboard(block_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Remove", callback_data=f"unblock:{block_id}")]]
    )


def choose_keyboard(events: list[Event]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{e.title[:35]} · {e.start_at:%d %b %H:%M}",
                    callback_data=f"pick:{e.id}:{e.version}",
                )
            ]
            for e in events
        ]
    )


def page_keyboard(page: int, pages: int, prefix: str = "events") -> InlineKeyboardMarkup:
    buttons = []
    if page:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"{prefix}:{page - 1}"))
    if page + 1 < pages:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"{prefix}:{page + 1}"))
    return InlineKeyboardMarkup([buttons])
