from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.event import Event


def event_keyboard(event: Event, clash: bool = False) -> InlineKeyboardMarkup:
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"{action}:{event.id}:{event.version}")

    return InlineKeyboardMarkup(
        [
            [
                button("Going anyway" if clash else "Going", "force" if clash else "go"),
                button("Maybe", "maybe"),
            ],
            [button("Can't make it", "decline"), button("Edit", "edit")],
        ]
    )


def confirm_keyboard(nonce: str, label="Confirm update") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"confirm:{nonce}"),
                InlineKeyboardButton("Keep unchanged", callback_data=f"dismiss:{nonce}"),
            ]
        ]
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


def page_keyboard(page: int, pages: int) -> InlineKeyboardMarkup:
    buttons = []
    if page:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"events:{page - 1}"))
    if page + 1 < pages:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"events:{page + 1}"))
    return InlineKeyboardMarkup([buttons])
