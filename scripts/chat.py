"""Chat with the bot in your terminal: real bot logic and real OpenAI, no Telegram or Supabase.

    python scripts/chat.py

Type a message as if forwarding it to the bot. Commands work too (/plans, /busy, /help...).
Buttons are numbered: type `1` to tap button 1. Data lives in memory and is gone on exit.

    :user 2         switch to a second Telegram user (to test isolation)
    :now 2026-10-16 18:00   pretend it is this Singapore time
    :raw            show the AI's raw extraction for the last message
    :quit
"""

import asyncio
import json
import sys
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from app.config import Settings  # noqa: E402
from app.schemas.event import SG  # noqa: E402
from app.services.event_extractor import EventExtractor  # noqa: E402
from app.services.nusmods import NUSModsImporter  # noqa: E402
from app.services.personal_calendar import CalendarReader  # noqa: E402
from app.services.scheduling import SchedulingService  # noqa: E402
from tests.fakes import MemoryRepository  # noqa: E402


class LoggingExtractor:
    """Real extractor that remembers its last output for :raw."""

    def __init__(self, inner):
        self.inner, self.last = inner, None

    async def extract(self, text, reference, previous=None):
        self.last = await self.inner.extract(text, reference, previous)
        return self.last


class NoKeyExtractor:
    last = None

    async def extract(self, *args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY is empty in .env, so event messages can't be read")


def show(replies, buttons):
    buttons.clear()
    for reply in replies:
        print("\n┌─ bot  ✏️ updated the card above" if reply.edit else "\n┌─ bot")
        for line in reply.text.splitlines():
            print("│ " + line)
        if reply.document:
            name, content = reply.document
            path = Path(tempfile.gettempdir()) / name
            path.write_bytes(content)
            print(f"│ 📎 {name} — saved to {path}")
            print(f'│    open it with:  open "{path}"')
        if reply.keyboard:
            for row in reply.keyboard.inline_keyboard:
                cells = []
                for b in row:
                    buttons.append(b)
                    target = f" → {b.url}" if b.url else ""
                    cells.append(f"[{len(buttons)}] {b.text}" + (" ↗" if target else ""))
                print("│ " + "   ".join(cells))
        print("└─")


async def main():
    settings = Settings(bot_mode="disabled")
    key = settings.openai_api_key.get_secret_value()
    async with httpx.AsyncClient(timeout=20) as http:
        extractor = (
            LoggingExtractor(EventExtractor(AsyncOpenAI(api_key=key), settings.openai_model))
            if key
            else NoKeyExtractor()
        )
        now = {"value": None}
        service = SchedulingService(
            MemoryRepository(),
            extractor,
            NUSModsImporter(http, settings.nusmods_academic_year),
            settings,
            clock=lambda: now["value"] or datetime.now(SG),
            calendar_reader=CalendarReader(http),
        )
        user, message_id, buttons = 1, 0, []
        print(__doc__.split("\n\n", 1)[1])
        if not key:
            print(
                "⚠️  No OPENAI_API_KEY: commands and `busy sat 2-6pm` work, forwarded plans won't.\n"
            )
        while True:
            try:
                text = (await asyncio.to_thread(input, f"\nyou (user {user})> ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not text:
                continue
            try:
                if text in (":quit", ":q"):
                    return
                if text.startswith(":user"):
                    user = int(text.split()[1])
                    print(f"Now user {user}.")
                    continue
                if text.startswith(":now"):
                    value = text[4:].strip()
                    now["value"] = (
                        datetime.fromisoformat(value).replace(tzinfo=SG) if value else None
                    )
                    print("Clock:", now["value"] or "real time")
                    continue
                if text == ":raw":
                    last = getattr(extractor, "last", None)
                    print(json.dumps(last.model_dump(), indent=2) if last else "Nothing yet.")
                    continue
                if text.isdigit() and buttons:
                    button = buttons[int(text) - 1]
                    if button.url:
                        print("Link button. Opening it in your browser:", button.url[:80] + "…")
                        webbrowser.open(button.url)
                        continue
                    show(await service.callback(user, button.callback_data), buttons)
                elif text.startswith("/"):
                    name, _, args = text[1:].partition(" ")
                    show(await service.command(user, name.split("@")[0], args), buttons)
                else:
                    message_id += 1
                    show(await service.message(user, message_id, text, service.clock()), buttons)
            except Exception as exc:
                print(f"\n💥 {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
