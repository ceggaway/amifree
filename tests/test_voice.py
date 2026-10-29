"""Voice notes in a private chat are heard, read like a typed message, and never stored."""

import pytest
from telegram import Update
from telegram.ext import Application

from app.bot.handlers import (
    UNSUPPORTED_VOICE,
    VOICE_TOO_LONG,
    VOICE_UNHEARD,
    register_handlers,
    with_heard,
)
from app.bot.messages import Reply
from app.services.voice_reader import MAX_BYTES, VoiceError, VoiceReader
from tests.conftest import extraction, onboard
from tests.telegram_transport import TelegramTransport, voice_payload

SAID = "Dinner with Mei Friday 7 to 9pm at Vivo"


class FakeVoice:
    """Stands in for the speech model: returns canned words, or raises."""

    def __init__(self, result=SAID):
        self.result, self.calls = result, []

    async def read(self, data):
        self.calls.append(data)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def chat(service_factory, reference):
    """A bot with a voice reader attached; returns (bot, service, repo, transport)."""

    async def make(*items, voice=None, **settings):
        service, repo = service_factory(*items, **settings)
        service.voice_reader = voice if voice is not None else FakeVoice()
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


async def test_a_voice_note_becomes_a_card_that_says_what_was_heard(chat):
    bot, service, repo, transport = await chat(extraction())
    async with bot:
        await feed(bot, voice_payload(7))
    assert any(name == "download" for name, _ in transport.calls)
    assert service.voice_reader.calls and service.extractor.calls[0][0] == SAID
    card = transport.sent[-1]["text"]
    assert card.startswith(f"🎙 <i>I heard: “{SAID}”</i>\n\n")
    assert "CCA dinner" in card
    assert len(repo.events) == 1


async def test_a_voice_note_about_a_deadline_saves_a_deadline(chat):
    # Spoken, "tmr" comes out as "tomorrow", and the model may still write a date for it.
    said = "I have a project report due tomorrow"
    due = extraction(
        kind="deadline",
        title="Project report",
        date_text="5 Oct",
        start_time_text=None,
        end_time_text=None,
        location=None,
    )
    bot, service, repo, transport = await chat(due, voice=FakeVoice(said))
    async with bot:
        await feed(bot, voice_payload(7))
    card = transport.sent[-1]["text"]
    assert card.startswith(f"🎙 I heard: “{said}”\n\n📌 Project report")
    assert "Due Mon 5 Oct, 11:59pm (tomorrow)" in card
    assert len(repo.deadlines) == 1 and not repo.events


async def test_a_voice_note_over_a_minute_is_not_sent_to_the_speech_model(chat):
    bot, service, repo, transport = await chat()
    async with bot:
        await feed(bot, voice_payload(7, duration=61))
    assert transport.sent[-1]["text"] == VOICE_TOO_LONG
    assert not service.voice_reader.calls and not repo.events
    assert not any(name == "download" for name, _ in transport.calls)


@pytest.mark.parametrize("result", ["", VoiceError("bad"), TimeoutError("api key sk-secret")])
async def test_an_unheard_voice_note_says_so(chat, result):
    bot, service, repo, transport = await chat(voice=FakeVoice(result))
    async with bot:
        await feed(bot, voice_payload(7))
    assert transport.sent[-1]["text"] == VOICE_UNHEARD
    assert not service.extractor.calls and not repo.events


async def test_without_a_voice_reader_people_are_asked_to_type(chat):
    bot, service, _, transport = await chat()
    service.voice_reader = None
    async with bot:
        await feed(bot, voice_payload(7))
    assert transport.sent[-1]["text"] == UNSUPPORTED_VOICE


async def test_voice_notes_in_groups_are_ignored(chat):
    bot, service, _, transport = await chat()
    async with bot:
        await feed(bot, voice_payload(7, group=True))
    assert not service.voice_reader.calls and not transport.sent


async def test_voice_notes_have_their_own_per_person_limit(chat):
    bot, service, _, transport = await chat(*[extraction() for _ in range(15)])
    async with bot:
        for n in range(11):
            await feed(bot, voice_payload(10 + n))
    assert len(service.voice_reader.calls) == 10
    assert "a lot very quickly" in transport.sent[-1]["text"]


def test_the_heard_line_is_plain_on_plain_replies():
    assert with_heard(Reply("Saved."), "hi").text == "🎙 I heard: “hi”\n\nSaved."


def test_the_heard_line_is_escaped_and_shortened_on_html_cards():
    card = with_heard(Reply("<b>Dinner</b>", parse_mode="HTML"), "Tom & <Mei>" + "x" * 400)
    line = card.text.split("\n\n")[0]
    assert "Tom &amp; &lt;Mei&gt;" in line and line.endswith("…”</i>")
    assert card.text.endswith("<b>Dinner</b>")


# VoiceReader -------------------------------------------------------------------------


class FakeTranscriptions:
    def __init__(self, text):
        self.text, self.kwargs = text, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.text


class FakeClient:
    def __init__(self, text):
        self.audio = type("Audio", (), {"transcriptions": FakeTranscriptions(text)})()


async def test_voice_reader_sends_the_audio_and_tidies_the_words():
    client = FakeClient("  Dinner Fri\n 7pm  ")
    assert await VoiceReader(client, "gpt-4o-mini-transcribe").read(b"ogg") == "Dinner Fri 7pm"
    sent = client.audio.transcriptions.kwargs
    assert sent["model"] == "gpt-4o-mini-transcribe"
    assert sent["file"] == ("voice.ogg", b"ogg", "audio/ogg")


async def test_voice_reader_rejects_empty_and_oversized_audio():
    reader = VoiceReader(FakeClient("x"), "m")
    with pytest.raises(VoiceError):
        await reader.read(b"")
    with pytest.raises(VoiceError):
        await reader.read(b"x" * (MAX_BYTES + 1))
