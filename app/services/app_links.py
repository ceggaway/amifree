"""Links that open the Mini App on one page, from a group or a card.

Telegram only allows web_app buttons in private chats, so groups use t.me links
(t.me/<bot>/<app>?startapp=<page>), which open the same Mini App. A start parameter may only
hold letters, digits, _ and -, at most 64 of them.
"""

import re

CODE = re.compile(r"^([np])(\d{1,20})$")


def chat_code(chat_id: int) -> str:
    """A group id as Telegram allows it in a start link: n1001234567890 for -1001234567890."""
    return ("n" if chat_id < 0 else "p") + str(abs(chat_id))


def parse_chat_code(code: str) -> int | None:
    match = CODE.match(code or "")
    if not match:
        return None
    return -int(match[2]) if match[1] == "n" else int(match[2])


def group_link(settings, chat_id: int, page: str = "g") -> str | None:
    """The app for one group: "g" its events, "new" create an event, "find" find a time."""
    return settings.app_link(f"{page}_{chat_code(chat_id)}")


def group_links(settings, chat_id: int) -> list[tuple[str, str | None]]:
    return [
        ("📅 Create an event", group_link(settings, chat_id, "new")),
        ("🗳 Find a time that works", group_link(settings, chat_id, "find")),
        ("📋 This group's events", group_link(settings, chat_id)),
    ]
