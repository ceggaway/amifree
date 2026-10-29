"""Turns a voice note sent in a private chat into text.

The words go through the same extractor and the same deterministic date/time checks as a
typed message, and the card shows what was heard so a misheard name or time can be caught.
The audio is only held in memory for the request: it is never written to disk or stored.
"""

from openai import AsyncOpenAI

MAX_SECONDS = 60
# A minute of Telegram's Opus audio is well under 1 MB; anything far bigger isn't a voice note.
MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 2000
# Spelling hints only: the transcript is still checked like any other message.
PROMPT = (
    "A student in Singapore telling their calendar bot about a plan or a deadline: the event"
    " or assignment (often with a course code like CS2103T), day, date, start and end times"
    " or due time, and the venue, e.g. dinner Friday 7 to 9pm at VivoCity, or CS2103T"
    " report due tomorrow 11:59pm."
)


class VoiceError(RuntimeError):
    """The voice note can't be used: too long, too large, or the speech service failed."""


class VoiceReader:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client, self.model = client, model

    async def read(self, data: bytes) -> str:
        """The words in the voice note, or "" when nothing was said."""
        if not data or len(data) > MAX_BYTES:
            raise VoiceError("Voice note is empty or too large")
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=("voice.ogg", data, "audio/ogg"),
            prompt=PROMPT,
            response_format="text",
        )
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return " ".join((text or "").split())[:MAX_CHARS]
