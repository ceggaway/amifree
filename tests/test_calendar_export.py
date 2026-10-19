"""Add a confirmed plan to Google or Apple Calendar."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from app.bot.messages import Reply, send_reply
from app.schemas.event import SG, Event, Status
from app.services.calendar_export import build_ics, google_link, ics_filename
from tests.conftest import extraction, tap

TEXT = "CCA dinner Friday 7–9pm at Vivo"


def event(**changes):
    values = dict(
        id="0b6f6c1e-3b1a-4c8e-9d2f-1a2b3c4d5e6f",
        user_id="u1",
        title="CCA dinner",
        start_at=datetime(2026, 10, 9, 19, tzinfo=SG),
        end_at=datetime(2026, 10, 9, 21, tzinfo=SG),
        location="Vivo",
        status=Status.GOING,
        version=3,
    )
    return Event(**(values | changes))


def test_google_link_is_prefilled_in_utc():
    query = parse_qs(urlparse(google_link(event())).query)
    assert query["action"] == ["TEMPLATE"]
    assert query["text"] == ["CCA dinner"]
    assert query["dates"] == ["20261009T110000Z/20261009T130000Z"]  # 7–9pm Singapore
    assert query["location"] == ["Vivo"]
    assert query["ctz"] == ["Asia/Singapore"]


def test_google_link_without_a_venue():
    assert "location" not in parse_qs(urlparse(google_link(event(location=None))).query)


def test_ics_has_the_event_and_a_stable_identity():
    ics = build_ics(event(), now=datetime(2026, 10, 4, tzinfo=UTC)).decode()
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\nDTSTART:20261009T110000Z\r" in ics and "\nDTEND:20261009T130000Z\r" in ics
    # Same UID + higher SEQUENCE: re-sending after an edit updates instead of duplicating.
    assert "UID:0b6f6c1e-3b1a-4c8e-9d2f-1a2b3c4d5e6f@amifree" in ics
    assert "SEQUENCE:3" in ics
    assert "METHOD:PUBLISH" in ics


def test_ics_escapes_text_and_folds_long_lines():
    long_venue = "Raffles Hall, Level 2; Dining hall " + "very long " * 12
    ics = build_ics(event(location=long_venue, notes="Bring $5, cash only")).decode()
    assert "Raffles Hall\\, Level 2\\; Dining" in ics
    assert "Bring $5\\, cash only\\n\\nAdded from AmIFree" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_a_title_cannot_add_lines_to_the_calendar_file():
    ics = build_ics(event(notes="Bring cash\rDTSTART:20991231T000000Z")).decode()
    assert "\rDTSTART" not in ics and "Bring cash\\nDTSTART" in ics.replace("\r\n ", "")


def test_cancelled_ics():
    ics = build_ics(event(), cancelled=True).decode()
    assert "METHOD:CANCEL" in ics and "STATUS:CANCELLED" in ics


def test_filename_is_readable():
    assert ics_filename(event(title="CCA dinner @ Vivo!")) == "cca-dinner-vivo-09oct.ics"


def calendar_row(reply):
    return next(
        row for row in reply.keyboard.inline_keyboard if row[0].text == "📅 Google Calendar"
    )


def has_calendar(reply):
    return any(row[0].text == "📅 Google Calendar" for row in reply.keyboard.inline_keyboard)


async def save_plan(service, reference):
    card = (await service.message(1, 2, TEXT, reference))[0]
    return await service.callback(1, tap(card, "Add to plans"))


async def test_saving_offers_both_calendars(service_factory, reference):
    service, _ = service_factory(extraction())
    saved = await save_plan(service, reference)
    google, apple = calendar_row(saved[0])
    assert google.text == "📅 Google Calendar" and google.url.startswith(
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
    )
    assert apple.text == "🍎 Apple Calendar" and apple.callback_data.startswith("ics:")


async def test_apple_button_sends_the_ics_file(service_factory, reference):
    service, repo = service_factory(extraction())
    saved = await save_plan(service, reference)
    reply = (await service.callback(1, calendar_row(saved[0])[1].callback_data))[0]
    name, content = reply.document
    assert name.endswith(".ics") and b"SUMMARY:CCA dinner" in content
    assert "Add to Calendar" in reply.text
    assert any(a[1] == "calendar_export" for a in repo.actions)


async def test_adding_offers_calendars_a_preview_does_not(service_factory, reference):
    service, _ = service_factory(extraction())
    card = (await service.message(1, 2, TEXT, reference))[0]
    assert not has_calendar(card)  # a preview, not yet added, has no calendar buttons
    added = await service.callback(1, tap(card, "Add to plans"))
    assert calendar_row(added[0])[0].text == "📅 Google Calendar"


async def test_other_users_and_dropped_plans_get_no_file(service_factory, reference):
    service, _ = service_factory(extraction())
    saved = await save_plan(service, reference)
    data = calendar_row(saved[0])[1].callback_data
    assert (await service.callback(2, data))[0].document is None  # not your plan
    assert "isn't valid" in (await service.callback(1, "ics:not-a-uuid"))[0].text
    decline = tap(saved[0], "Remove")
    await service.callback(1, decline)
    assert (await service.callback(1, data))[0].document is None


@pytest.mark.parametrize("document", [None, ("a.ics", b"BEGIN:VCALENDAR")])
async def test_send_reply_uses_a_document_only_when_there_is_one(document):
    calls = []

    class Message:
        async def reply_text(self, text, reply_markup=None, parse_mode=None):
            calls.append(("text", text))

        async def reply_document(self, file, caption=None, reply_markup=None):
            calls.append(("document", file.filename, caption))

    class Update:
        effective_message = Message()

    await send_reply(Update(), Reply("hi", document=document))
    assert calls == ([("text", "hi")] if document is None else [("document", "a.ics", "hi")])


async def test_tapped_card_is_edited_in_place():
    from telegram.error import BadRequest

    calls = []

    class Query:
        def __init__(self, error=None):
            self.error = error

        async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
            calls.append(("edit", text))
            if self.error:
                raise self.error

    class Message:
        async def reply_text(self, text, reply_markup=None, parse_mode=None):
            calls.append(("new", text))

    class Update:
        effective_message = Message()

        def __init__(self, query):
            self.callback_query = query

    await send_reply(Update(Query()), Reply("card", edit=True))
    assert calls == [("edit", "card")]
    calls.clear()
    # Tapping the same button twice: Telegram refuses an identical edit, and that's fine.
    await send_reply(Update(Query(BadRequest("Message is not modified"))), Reply("card", edit=True))
    assert calls == [("edit", "card")]
    calls.clear()
    # A card too old to edit: send the new state instead of losing it.
    await send_reply(Update(Query(BadRequest("Message can't be edited"))), Reply("card", edit=True))
    assert calls == [("edit", "card"), ("new", "card")]
