from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.event import Event, GroupEvent, Poll, Status


def event_keyboard(
    event: Event,
    clash: bool = False,
    possible: tuple[Event, ...] = (),
    synced: bool = False,
) -> InlineKeyboardMarkup:
    def button(label, action, target=event):
        return InlineKeyboardButton(label, callback_data=f"{action}:{target.id}:{target.version}")

    go = "force" if clash else "go"
    # Shared group plans are edited once by their organiser, not per member.
    edit = [] if event.group_event_id else [button("Edit", "edit")]
    if event.status == Status.PREVIEW:
        rows = [
            [button("Going anyway" if clash else "Going", go), button("Maybe", "maybe")],
            [button("Can't make it", "decline")] + edit,
            [button("Save", "save")],
        ]
    else:
        # Once decided, offer only the other answers. No Save: on a Going plan it would
        # quietly downgrade the commitment to "Saved (not decided)".
        answers = [
            (Status.GOING, button("Going anyway" if clash else "Going", go)),
            (Status.MAYBE, button("Maybe", "maybe")),
            (Status.DECLINED, button("Can't make it", "decline")),
        ]
        rows = [[b for status, b in answers if status != event.status]]
        if edit:
            rows.append(edit)
        # With a live calendar subscription the plan is already on its way: no buttons.
        if event.status in (Status.SAVED, Status.GOING) and not synced:
            from app.services.calendar_export import google_link

            rows.append(calendar_keyboard(event, google_link(event)).inline_keyboard[0])
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
            [button("Edit", "edit"), button("Cancel event", "cancel"), button("Nudge", "nudge")],
        ]
    )


def nudge_keyboard(event: GroupEvent) -> InlineKeyboardMarkup:
    """Answer a group card from the private nudge, without going back to the group."""
    return InlineKeyboardMarkup(group_keyboard(event).inline_keyboard[:1])


def poll_keyboard(poll: Poll) -> InlineKeyboardMarkup:
    def button(label, action, extra=""):
        data = f"p:{action}:{poll.id}" + (f":{extra}" if extra else "")
        return InlineKeyboardButton(label, callback_data=data)

    if poll.status == "COLLECTING":
        return InlineKeyboardMarkup(
            [
                [button("Count me in", "join")],
                [button("Suggest slots (organiser)", "suggest"), button("Nudge", "nudge")],
            ]
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
        + [button("Nudge", "nudge")]
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


def calendar_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("New link", callback_data="cal:reset"),
                InlineKeyboardButton("Turn off", callback_data="cal:off"),
            ]
        ]
    )


def recurring_keyboard(nonce: str, next_date: str, day_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Just the next one ({next_date})", callback_data=f"rec:once:{nonce}"
                )
            ],
            [InlineKeyboardButton(f"Every {day_name}", callback_data=f"rec:weekly:{nonce}")],
            [InlineKeyboardButton("Neither", callback_data=f"dismiss:{nonce}")],
        ]
    )


def free_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Today", callback_data="free:today"),
                InlineKeyboardButton("Tomorrow", callback_data="free:tomorrow"),
                InlineKeyboardButton("This weekend", callback_data="free:weekend"),
            ]
        ]
    )


def personal_calendars_keyboard(calendars) -> InlineKeyboardMarkup:
    """One Remove button per connected calendar, then help adding another."""
    rows = [
        [InlineKeyboardButton(f"Remove {c.label[:30]}", callback_data=f"pcal:rm:{c.id}")]
        for c in calendars
    ]
    rows.append([InlineKeyboardButton("How do I add another?", callback_data="setup:calendar")])
    return InlineKeyboardMarkup(rows)


def calendar_keyboard(event: Event, google_url: str) -> InlineKeyboardMarkup:
    """Offered once a plan is Saved or Going: add it to the user's own calendar."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Google Calendar", url=google_url),
                InlineKeyboardButton("🍎 Apple Calendar", callback_data=f"ics:{event.id}"),
            ]
        ]
    )
