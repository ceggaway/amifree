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

    add_action = "force" if clash else "go"

    def calendar_row():
        from app.services.calendar_export import google_link

        return calendar_keyboard(event, google_link(event)).inline_keyboard[0]

    if event.group_event_id:
        # A member's RSVP to a shared group plan: keep Going / Maybe / Can't, edited
        # once by the organiser from the group, never per member.
        answers = [
            (Status.GOING, button("Going anyway" if clash else "Going", add_action)),
            (Status.MAYBE, button("Maybe", "maybe")),
            (Status.DECLINED, button("Can't make it", "decline")),
        ]
        if event.status == Status.PREVIEW:
            rows = [[b for _, b in answers]]
        else:
            rows = [[b for status, b in answers if status != event.status]]
        if event.status in (Status.SAVED, Status.GOING) and not synced:
            rows.append(calendar_row())
    else:
        # A personal plan: it's in your plans (blocks your time, goes to your calendar)
        # or it isn't. Save and Maybe were removed in favour of one Add to plans.
        add = button("Add anyway" if clash else "Add to plans", add_action)
        edit = button("Edit", "edit")
        if event.status == Status.GOING:
            rows = [[edit, button("Remove", "decline")]]
            if not synced:
                rows.append(calendar_row())
        elif event.status == Status.PREVIEW:
            rows = [[add], [button("Skip", "decline"), edit]]
        else:
            # Declined, or a legacy Saved/Maybe plan: offer to add it to your plans.
            rows = [[add, edit]]
            if event.status == Status.SAVED and not synced:
                rows.append(calendar_row())
    if possible:
        # Choosing one never deletes the other; it only commits the chosen plan.
        rows.append([button("Attend this one", add_action)])
        rows.extend([button(f"Attend {other.title[:40]}", "go", other)] for other in possible[:3])
    return InlineKeyboardMarkup(rows)


def group_keyboard(event: GroupEvent) -> InlineKeyboardMarkup:
    # No version in the data: a tap always applies to the card's latest state.
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"g:{action}:{event.id}")

    rows = [
        [button("Going", "go"), button("Maybe", "maybe"), button("Can't", "no")],
        [button("Am I free?", "free"), button("Who's going?", "who"), button("I'm here", "here")],
        [button("Save", "save")] + ([button("Going to all", "goall")] if event.series_id else []),
        # Organiser only; everyone else gets a private refusal.
        [button("Edit", "edit"), button("Cancel event", "cancel"), button("Nudge", "nudge")],
        [button("Status", "status")],
    ]
    return InlineKeyboardMarkup(rows)


def nudge_keyboard(event: GroupEvent) -> InlineKeyboardMarkup:
    """Answer a group card from the private nudge, without going back to the group."""
    return InlineKeyboardMarkup(group_keyboard(event).inline_keyboard[:1])


def poll_keyboard(
    poll: Poll, heatmap_url: str | None = None, mine_url: str | None = None
) -> InlineKeyboardMarkup:
    def button(label, action, extra=""):
        data = f"p:{action}:{poll.id}" + (f":{extra}" if extra else "")
        return InlineKeyboardButton(label, callback_data=data)

    heatmap = []
    if heatmap_url:
        heatmap.append(InlineKeyboardButton("📊 Who's free when", url=heatmap_url))
    if mine_url:
        heatmap.append(InlineKeyboardButton("✍️ Set my times", url=mine_url))
    heatmap = [heatmap] if heatmap else []
    if poll.status == "COLLECTING":
        return InlineKeyboardMarkup(
            [
                [button("Count me in", "join")],
                [button("Suggest slots (organiser)", "suggest"), button("Nudge", "nudge")],
                *heatmap,
            ]
        )
    rows = [[button("Fill mine", "fill"), button("Count me in", "join")], *heatmap]
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


def attendance_keyboard(event: GroupEvent) -> InlineKeyboardMarkup:
    """The "still coming?" answers, sent privately the day before."""

    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"c:{action}:{event.id}")

    return InlineKeyboardMarkup(
        [[button("Still coming", "y"), button("Not sure", "u"), button("Can't anymore", "n")]]
    )


def suggestion_keyboard(suggestion_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Make event card", callback_data=f"d:ok:{suggestion_id}"),
                InlineKeyboardButton("Ignore", callback_data=f"d:no:{suggestion_id}"),
            ]
        ]
    )


def series_keyboard(event: GroupEvent, cancelled: bool) -> InlineKeyboardMarkup:
    """After the organiser changes one session: apply it to the rest, or stop the series."""

    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"ser:{action}:{event.id}")

    if cancelled:
        return InlineKeyboardMarkup([[button("Cancel all future sessions", "stop")]])
    return InlineKeyboardMarkup([[button("Apply to all future sessions", "all")]])


def confirm_keyboard(nonce: str, label="Confirm update") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"confirm:{nonce}"),
                InlineKeyboardButton("Keep unchanged", callback_data=f"dismiss:{nonce}"),
            ]
        ]
    )


def change_keyboard(nonce: str) -> InlineKeyboardMarkup:
    """What to change on a plan: venue and title are typed as-is, without the AI."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📍 Venue", callback_data=f"chg:venue:{nonce}"),
                InlineKeyboardButton("🕒 Date/time", callback_data=f"chg:time:{nonce}"),
                InlineKeyboardButton("✏️ Title", callback_data=f"chg:title:{nonce}"),
            ],
            [InlineKeyboardButton("Keep unchanged", callback_data=f"dismiss:{nonce}")],
        ]
    )


def deadline_keyboard(deadline_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Done", callback_data=f"dl:done:{deadline_id}"),
                InlineKeyboardButton("🗑 Remove", callback_data=f"dl:rm:{deadline_id}"),
            ]
        ]
    )


def agenda_keyboard(items: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    """One numbered button per agenda item; callback_data is "<kind>:<id>". Rows of six."""
    btns = [
        InlineKeyboardButton(label, callback_data=f"{kind}:{item_id}")
        for label, kind, item_id in items
    ]
    return InlineKeyboardMarkup([btns[i : i + 6] for i in range(0, len(btns), 6)])


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


def attendance_panel_keyboard(
    hex_id: str, labels: list[tuple[str, bool]], digest: str, page: int, pages: int
) -> InlineKeyboardMarkup:
    """One toggle per person, two to a row, with paging and a mark-all-Going shortcut."""
    size = 16
    start = page * size
    buttons = [
        InlineKeyboardButton(
            ("✅ " if present else "⬜ ") + name[:22],
            callback_data=f"at:{hex_id}:{start + offset}:{digest}",
        )
        for offset, (name, present) in enumerate(labels[start : start + size])
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹ Back", callback_data=f"ap:{hex_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=f"ap:{hex_id}:{page}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("More ›", callback_data=f"ap:{hex_id}:{page + 1}"))
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton("Everyone who said Going came", callback_data=f"aa:{hex_id}")]
    )
    return InlineKeyboardMarkup(rows)


def _menu(label: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"menu:{name}")


def menu_keyboard() -> InlineKeyboardMarkup:
    """The four things a set-up user does most; each tap runs the matching command."""
    return InlineKeyboardMarkup(
        [
            [_menu("📋 My plans", "plans"), _menu("🕒 Am I free?", "free")],
            [_menu("📌 Deadlines", "deadlines"), _menu("⚙️ Settings", "settings")],
        ]
    )


def settings_keyboard(reminders_on: bool) -> InlineKeyboardMarkup:
    label, target = ("Turn off", "off") if reminders_on else ("Turn on", "on")
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🔔 {label} reminders", callback_data=f"set:reminders:{target}")]]
    )
