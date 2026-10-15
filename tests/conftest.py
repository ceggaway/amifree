from datetime import datetime

import pytest

from app.config import Settings
from app.schemas.event import SG, Extraction
from app.services.nusmods import ImportResult
from app.services.scheduling import SchedulingService
from tests.fakes import MemoryRepository, QueueExtractor


@pytest.fixture
def reference():
    return datetime(2026, 10, 4, 12, tzinfo=SG)


def extraction(**changes):
    defaults = dict(
        kind="new",
        title="CCA dinner",
        date_text="Friday",
        start_time_text="7",
        end_time_text="9pm",
        end_date_text=None,
        location="Vivo",
        notes=None,
        match_title=None,
        ambiguity=None,
    )
    return Extraction(**(defaults | changes))


@pytest.fixture
def service_factory(reference):
    class Importer:
        async def import_link(self, link, ref):
            return ImportResult(
                [],
                "2026-2027",
                1,
                datetime(2026, 8, 10, tzinfo=SG),
                datetime(2026, 12, 14, tzinfo=SG),
            )

    def make(*items, **settings):
        repo = MemoryRepository()
        service = SchedulingService(
            repo,
            QueueExtractor(*items),
            Importer(),
            # Most tests exercise the "ask for the end time" path; production assumes a length.
            Settings(
                _env_file=None,
                bot_mode="disabled",
                **(
                    {"default_event_duration_minutes": 0, "card_edit_interval_seconds": 0}
                    | settings
                ),
            ),
            clock=lambda: reference,
        )
        return service, repo

    return make


async def onboard(service, reference, telegram_id=1):
    await service.command(telegram_id, "start")
    await service.message(
        telegram_id, 1, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
    )


def tap(reply, label):
    """callback_data of the button with this label, wherever it sits on the card."""
    for row in reply.keyboard.inline_keyboard:
        for b in row:
            if b.text == label:
                return b.callback_data
    raise AssertionError(
        f"No {label!r} button; have {[b.text for r in reply.keyboard.inline_keyboard for b in r]}"
    )
