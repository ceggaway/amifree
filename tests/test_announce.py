import asyncio
from datetime import UTC, datetime, timedelta

from telegram.error import Forbidden, RetryAfter

from scripts.announce import SETUP_TIP, TEXT, deliver, eligible, message_for

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def row(**over):
    base = {
        "dm_blocked": False,
        "reminders_enabled": True,
        "onboarding_status": "READY",
        "updated_at": (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
    }
    return base | over


def test_everyone_but_blocked_gets_it_and_unfinished_setup_gets_the_tip():
    since = NOW - timedelta(days=30)
    assert eligible(row(), since)
    assert not eligible(row(dm_blocked=True), since)
    assert eligible(row(reminders_enabled=False), since)
    assert eligible(row(onboarding_status="NEW"), since)
    assert message_for(row()) == TEXT
    assert message_for(row(onboarding_status="NEW")) == TEXT + SETUP_TIP
    assert not eligible(row(updated_at=(NOW - timedelta(days=40)).isoformat()), since)
    assert eligible(row(updated_at=(NOW - timedelta(days=40)).isoformat()), None)


class FakeBot:
    def __init__(self, *errors):
        self.errors = list(errors)
        self.sent = []

    async def send_message(self, chat_id, text):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text))


def test_deliver_waits_out_rate_limit_and_reports_blocked(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("scripts.announce.asyncio.sleep", no_sleep)
    bot = FakeBot(RetryAfter(2))
    assert asyncio.run(deliver(bot, 1)) == "sent"
    assert bot.sent == [(1, TEXT)]
    assert asyncio.run(deliver(FakeBot(Forbidden("blocked")), 1)) == "blocked"
