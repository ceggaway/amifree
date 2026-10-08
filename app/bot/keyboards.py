from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.event import Event, Status


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


def page_keyboard(page: int, pages: int, prefix: str = "events") -> InlineKeyboardMarkup:
    buttons = []
    if page:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"{prefix}:{page - 1}"))
    if page + 1 < pages:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"{prefix}:{page + 1}"))
    return InlineKeyboardMarkup([buttons])
