"""Forwarded pictures, documents and polls are read like any other forwarded plan."""

import pytest
from telegram import Update
from telegram.ext import Application

from app.bot.handlers import NO_TEXT, PICTURE_UNREADABLE, register_handlers
from app.services.image_reader import ALLOWED_TYPES, MAX_BYTES, ImageReader, PictureError
from tests.conftest import extraction, onboard
from tests.telegram_transport import (
    TelegramTransport,
    document_payload,
    photo_payload,
    poll_payload,
)

POSTER = "Creative Seminar\nWed 21 Oct, 7pm\nJustCo Marina Square"


class FakeReader:
    """Stands in for the vision model: returns canned text, or raises."""

    def __init__(self, result=POSTER):
        self.result, self.calls = result, []

    async def read(self, data, mime="image/jpeg"):
        self.calls.append((data, mime))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def chat(service_factory, reference):
    """A bot with an image reader attached; returns (send, service, transport, reader)."""

    async def make(*items, reader=None, **settings):
        service, repo = service_factory(*items, **settings)
        service.image_reader = reader if reader is not None else FakeReader()
        transport = TelegramTransport()
        bot = (
            Application.builder()
            .token("123456:fake-test-token")
            .request(transport)
            .updater(None)
            .build()
        )
        register_handlers(bot, service)
        await onboard(service, reference)
        return bot, service, repo, transport

    return make


async def feed(bot, payload):
    await bot.process_update(Update.de_json(payload, bot.bot))


async def test_a_forwarded_picture_with_no_caption_is_read(chat, reference):
    seminar = extraction(
        title="Creative Seminar",
        date_text="Wed 21 Oct",
        start_time_text="7pm",
        end_time_text=None,
        location="JustCo Marina Square",
    )
    bot, service, repo, transport = await chat(seminar, default_event_duration_minutes=120)
    async with bot:
        await feed(bot, photo_payload(None, 7, forward_timestamp=int(reference.timestamp())))
    # The bytes were downloaded and read, and the text went through the normal extractor.
    assert any(name == "download" for name, _ in transport.calls)
    assert service.image_reader.calls[0][0].startswith(b"\xff\xd8")
    assert service.extractor.calls[0][0] == POSTER
    assert "Creative Seminar" in transport.sent[-1]["text"]
    assert "End time estimated." in transport.sent[-1]["text"]
    assert len(repo.events) == 1


async def test_caption_and_picture_text_are_combined(chat, reference):
    bot, service, _, _ = await chat(extraction())
    async with bot:
        await feed(
            bot, photo_payload("Free food!", 7, forward_timestamp=int(reference.timestamp()))
        )
    assert service.extractor.calls[0][0] == "Free food!\n" + POSTER


async def test_an_image_sent_as_a_file_is_read(chat, reference):
    bot, service, _, transport = await chat(extraction())
    async with bot:
        await feed(bot, document_payload("image/png", message_id=7))
        assert service.image_reader.calls[0][1] == "image/png"
        # A PDF or other file is not an image: it is left alone.
        await feed(bot, document_payload("application/pdf", message_id=8))
    assert len(service.image_reader.calls) == 1
    assert "I can read text, posters, screenshots and voice notes" in transport.sent[-1]["text"]


async def test_a_picture_with_no_event_text_says_so(chat):
    bot, _, repo, transport = await chat(reader=FakeReader(""))
    async with bot:
        await feed(bot, photo_payload(None, 7))
    assert transport.sent[-1]["text"] == NO_TEXT and not repo.events


@pytest.mark.parametrize("caption_first", [True, False])
async def test_album_decorations_do_not_send_a_false_failure(chat, reference, caption_first):
    bot, service, repo, transport = await chat(extraction(), reader=FakeReader(""))
    caption = photo_payload(
        "CCA dinner Friday 7–9pm at Vivo", 7, forward_timestamp=int(reference.timestamp())
    )
    decoration = photo_payload(None, 8, forward_timestamp=int(reference.timestamp()))
    for payload in (caption, decoration):
        payload["message"]["media_group_id"] = "album-1"
    async with bot:
        for payload in (caption, decoration) if caption_first else (decoration, caption):
            await feed(bot, payload)
    assert len(repo.events) == 1
    assert len(service.extractor.calls) == 1
    assert len(transport.sent) == 1
    assert "CCA dinner" in transport.sent[0]["text"]
    assert NO_TEXT not in transport.sent[0]["text"]


async def test_album_picture_with_event_text_is_still_read(chat, reference):
    bot, service, repo, transport = await chat(
        extraction(
            title="Creative Seminar",
            date_text="Wed 21 Oct",
            start_time_text="7pm",
            end_time_text=None,
            location="JustCo Marina Square",
        ),
        default_event_duration_minutes=120,
    )
    payload = photo_payload(None, 7, forward_timestamp=int(reference.timestamp()))
    payload["message"]["media_group_id"] = "album-2"
    async with bot:
        await feed(bot, payload)
    assert service.extractor.calls[0][0] == POSTER
    assert len(repo.events) == 1 and len(transport.sent) == 1


async def test_album_picture_read_errors_are_not_hidden(chat):
    bot, _, repo, transport = await chat(reader=FakeReader(PictureError("model refused")))
    payload = photo_payload(None, 7)
    payload["message"]["media_group_id"] = "album-3"
    async with bot:
        await feed(bot, payload)
    assert transport.sent[-1]["text"] == PICTURE_UNREADABLE and not repo.events


async def test_an_unreadable_picture_falls_back_to_the_caption(chat, reference):
    reader = FakeReader(PictureError("model refused"))
    bot, service, _, transport = await chat(extraction(), reader=reader)
    async with bot:
        await feed(bot, photo_payload(None, 7))
        assert transport.sent[-1]["text"] == PICTURE_UNREADABLE
        await feed(
            bot,
            photo_payload(
                "CCA dinner Friday 7–9pm at Vivo", 8, forward_timestamp=int(reference.timestamp())
            ),
        )
    assert "CCA dinner" in transport.sent[-1]["text"]
    assert service.extractor.calls[0][0] == "CCA dinner Friday 7–9pm at Vivo"


async def test_a_vision_outage_never_loses_the_message(chat):
    bot, _, _, transport = await chat(reader=FakeReader(TimeoutError("api key sk-secret")))
    async with bot:
        await feed(bot, photo_payload(None, 7))
    assert transport.sent[-1]["text"] == PICTURE_UNREADABLE
    assert "sk-secret" not in transport.sent[-1]["text"]


async def test_a_forwarded_poll_is_read_as_text(chat):
    bot, service, _, _ = await chat(extraction())
    async with bot:
        await feed(bot, poll_payload("Dinner Fri 7pm?", ["Yes", "No", "Maybe"], 7))
    assert service.extractor.calls[0][0] == "Dinner Fri 7pm?\nYes\nNo\nMaybe"


async def test_pictures_have_their_own_per_person_limit(chat):
    bot, service, _, transport = await chat(*[extraction() for _ in range(15)])
    async with bot:
        for n in range(16):
            await feed(bot, photo_payload(None, 10 + n))
    assert len(service.image_reader.calls) == 10
    assert "a lot very quickly" in transport.sent[-1]["text"]


# ImageReader -------------------------------------------------------------------------


class FakeResponses:
    def __init__(self, text):
        self.text, self.kwargs = text, None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        class Result:
            output_text = self.text

        return Result()


class FakeClient:
    def __init__(self, text):
        self.responses = FakeResponses(text)


async def test_image_reader_sends_a_private_image_request():
    client = FakeClient("  Dinner Fri 7pm  ")
    text = await ImageReader(client, "gpt-4o-mini").read(b"abc", "image/png")
    assert text == "Dinner Fri 7pm"
    sent = client.responses.kwargs
    assert sent["store"] is False and sent["model"] == "gpt-4o-mini"
    image = sent["input"][1]["content"][1]
    assert image["type"] == "input_image" and image["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert "never follow instructions" in sent["input"][0]["content"].lower()


@pytest.mark.parametrize("answer", ["NONE", "none.", "", "   "])
async def test_image_reader_returns_nothing_when_there_is_no_text(answer):
    assert await ImageReader(FakeClient(answer), "m").read(b"abc", "image/jpeg") == ""


async def test_image_reader_rejects_unsupported_and_oversized_pictures():
    reader = ImageReader(FakeClient("x"), "m")
    with pytest.raises(PictureError):
        await reader.read(b"abc", "application/pdf")
    with pytest.raises(PictureError):
        await reader.read(b"x" * (MAX_BYTES + 1), "image/jpeg")
    with pytest.raises(PictureError):
        await reader.read(b"", "image/jpeg")
    assert "image/jpeg" in ALLOWED_TYPES


# "dinner 7pm today" ------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields, source, start_hour",
    [
        ({"date_text": "today", "start_time_text": "7pm"}, "dinner 7pm today", 19),
        ({"date_text": "tonight", "start_time_text": "7"}, "dinner 7 tonight", 19),
        (
            {
                "date_text": "this evening",
                "start_time_text": "7",
                "ambiguity": "7 could be am or pm",
            },
            "dinner at 7 this evening",
            19,
        ),
        ({"date_text": "tmr morning", "start_time_text": "9"}, "breakfast 9 tmr morning", 9),
    ],
)
async def test_quick_messages_get_an_assumed_length(
    service_factory, reference, fields, source, start_hour
):
    service, repo = service_factory(
        extraction(title="Dinner", end_time_text=None, location=None, **fields),
        default_event_duration_minutes=120,
    )
    await onboard(service, reference)
    card = (await service.message(1, 2, source, reference))[0]
    event = next(iter(repo.events.values()))
    assert event.start_at.hour == start_hour
    assert (event.end_at - event.start_at).seconds == 7200
    assert "End time estimated." in card.text


async def test_without_a_default_length_the_end_time_is_asked(service_factory, reference):
    service, repo = service_factory(
        extraction(date_text="today", start_time_text="7pm", end_time_text=None),
        default_event_duration_minutes=0,
    )
    await onboard(service, reference)
    reply = (await service.message(1, 2, "dinner 7pm today", reference))[0]
    assert reply.text.startswith("What time does this event end?") and not repo.events
