"""Settings navigation, persisted custom timings and independent personal reminders."""

from unittest.mock import AsyncMock

import pytest

from app.bot.messages import Reply, send_reply
from app.services.date_resolution import Clarification
from app.services.personal_reminder_settings import parse_delivery, parse_interval
from tests.conftest import tap
from tests.test_deadlines import at, due, reminders_at


def rows(reply):
    return [[b.text for b in row] for row in reply.keyboard.inline_keyboard]


async def overview(service):
    home = (await service.command(1, "settings"))[0]
    return (await service.callback(1, tap(home, "🌤 Daily overview")))[0]


async def alerts(service):
    home = (await service.command(1, "settings"))[0]
    return (await service.callback(1, tap(home, "⏰ Deadline alerts")))[0]


async def test_settings_rows_and_independent_off_preferences(service_factory):
    service, repo = service_factory()
    home = (await service.command(1, "settings"))[0]
    assert rows(home) == [["🌤 Daily overview", "⏰ Deadline alerts"]]
    assert "Group" not in home.text and "group" not in home.text
    page = await overview(service)
    assert rows(page) == [
        ["Night before", "✓ Same day morning"],
        ["Change timing", "Turn off", "‹ Back"],
    ]
    off = (await service.callback(1, tap(page, "Turn off")))[0]
    assert rows(off) == [["Turn on", "‹ Back"]]
    assert "<b>Status:</b> Off" in off.text
    user = await repo.ensure_user(1)
    assert not user.overview_enabled and user.deadline_alerts and user.reminders_enabled
    page = await alerts(service)
    assert rows(page) == [["Change timing", "Turn off", "‹ Back"]]
    timing = (await service.callback(1, tap(page, "Change timing")))[0]
    assert rows(timing) == [["1 hour", "✓ 3 hours", "1 day"], ["Custom timing", "‹ Back"]]
    page = (await service.callback(1, tap(timing, "1 day")))[0]
    off = (await service.callback(1, tap(page, "Turn off")))[0]
    assert rows(off) == [["Turn on", "‹ Back"]]
    on = (await service.callback(1, tap(off, "Turn on")))[0]
    assert "1 day before" in on.text
    assert (await repo.ensure_user(1)).deadline_lead_minutes == 1440


async def test_custom_delivery_is_persisted_and_updates_original_message(
    service_factory, reference
):
    service, repo = service_factory()
    page = await overview(service)
    prompt = (await service.callback(1, tap(page, "Change timing"), message_id=77))[0]
    assert rows(prompt) == [["‹ Back"]]
    invalid = (await service.message(1, 2, "7", reference))[0]
    assert "AM or PM" in invalid.text
    assert invalid.edit_message_id == 77
    assert (await repo.ensure_user(1)).overview_morning_minute == 540
    saved = (await service.message(1, 3, "7:30am", reference))[0]
    assert saved.edit_message_id == 77 and saved.parse_mode == "HTML"
    user = await repo.ensure_user(1)
    assert user.overview_morning_minute == 450 and user.pending is None
    assert "7:30AM SGT" in saved.text
    night = (await service.callback(1, tap(saved, "Night before")))[0]
    await service.callback(1, tap(night, "Change timing"), message_id=77)
    saved = (await service.message(1, 4, "22:15", reference))[0]
    user = await repo.ensure_user(1)
    assert user.overview_evening_minute == 1335 and user.overview_morning_minute == 450
    off = (await service.callback(1, tap(saved, "Turn off")))[0]
    on = (await service.callback(1, tap(off, "Turn on")))[0]
    assert "10:15PM SGT" in on.text


async def test_wrong_period_requires_explicit_confirmation(service_factory, reference):
    service, repo = service_factory()
    page = await overview(service)
    await service.callback(1, tap(page, "Change timing"), message_id=77)
    choice = (await service.message(1, 2, "8pm", reference))[0]
    assert (await repo.ensure_user(1)).overview_period == "morning"
    assert "Switch and use this time?" in choice.text
    page = (await service.callback(1, tap(choice, "Use Night before")))[0]
    assert "Night before · 8PM SGT" in page.text
    assert (await repo.ensure_user(1)).pending is None


async def test_back_and_commands_cancel_unfinished_timing(service_factory):
    service, repo = service_factory()
    page = await overview(service)
    prompt = (await service.callback(1, tap(page, "Change timing")))[0]
    await service.callback(1, tap(prompt, "‹ Back"))
    assert (await repo.ensure_user(1)).pending is None
    await service.callback(1, "prefs:overview:time")
    await service.command(1, "plans")
    assert (await repo.ensure_user(1)).pending is None


async def test_custom_deadline_duration_survives_off_and_does_not_change_overview(
    service_factory, reference
):
    service, repo = service_factory()
    page = await alerts(service)
    timing = (await service.callback(1, tap(page, "Change timing")))[0]
    await service.callback(1, tap(timing, "Custom timing"), message_id=88)
    bad = (await service.message(1, 2, "-3 hours", reference))[0]
    assert "minutes, hours or days" in bad.text
    assert (await repo.ensure_user(1)).deadline_lead_minutes == 180
    page = (await service.message(1, 3, "2 hours 30 minutes", reference))[0]
    assert page.edit_message_id == 88 and "2 hours 30 minutes before" in page.text
    user = await repo.ensure_user(1)
    assert user.deadline_lead_minutes == 150 and user.overview_enabled


@pytest.mark.parametrize(
    "text,minutes", [("90 minutes", 90), ("1.5 hours", 90), ("2 days", 2880), ("1h 30m", 90)]
)
def test_duration_inputs(text, minutes):
    assert parse_interval(text) == minutes


async def test_custom_deadline_alert_fires_at_chosen_interval_only(service_factory, reference):
    service, repo = service_factory(due(date_text="Mon", start_time_text="5pm"))
    await service.command(1, "deadline", "Report due Mon 5pm")
    user = await repo.ensure_user(1)
    await repo.set_user_flags(user.id, deadline_lead_minutes=1440)
    for d in repo.deadlines.values():
        d.created_at = reference
    assert await reminders_at(service, repo, at(4, 16, 59)) == []
    (sent,) = await reminders_at(service, repo, at(4, 17))
    assert "Deadline reminder" in sent.text
    assert await reminders_at(service, repo, at(4, 17, 1)) == []
    assert await reminders_at(service, repo, at(5, 14)) == []


async def test_typed_answer_edits_bot_message():
    from types import SimpleNamespace

    bot = SimpleNamespace(edit_message_text=AsyncMock())
    update = SimpleNamespace(
        callback_query=None, effective_chat=SimpleNamespace(id=1), get_bot=lambda: bot
    )
    await send_reply(update, Reply("Saved", edit_message_id=77, parse_mode="HTML"))
    bot.edit_message_text.assert_awaited_once_with(
        chat_id=1, message_id=77, text="Saved", parse_mode="HTML", reply_markup=None
    )


@pytest.mark.parametrize(
    "raw,minute,period",
    [
        ("at 9am", 540, "morning"),
        ("9am SGT", 540, "morning"),
        ("remind me at 7:30 am Singapore time", 450, "morning"),
        ("09:15", 555, "morning"),
        ("9 a.m.", 540, "morning"),
        ("0730", 450, "morning"),
        ("12am", 0, "morning"),
        ("18:00", 1080, "evening"),
        ("11:59pm", 1439, "evening"),
    ],
)
def test_common_delivery_replies(raw, minute, period):
    assert parse_delivery(raw) == (minute, period)


@pytest.mark.parametrize("raw", ["", "9", "25:00", "9:99am", "12pm", "3pm", "-9am", "9am UTC"])
def test_invalid_delivery_replies_ask_for_clarification(raw):
    with pytest.raises(Clarification):
        parse_delivery(raw)


@pytest.mark.parametrize(
    "raw", ["0 minutes", "-1 hour", "31 days", "1.1 minutes", "9am", "3", "", "1 hour or 2 hours"]
)
def test_invalid_intervals_do_not_get_guessed(raw):
    with pytest.raises(Clarification):
        parse_interval(raw)


def test_multiline_and_polite_duration_replies():
    assert parse_interval("2 hours\n30 minutes") == 150
    assert parse_interval("please remind me 2 hours before the deadline.") == 120


async def test_cancelled_confirmation_cannot_override_saved_timing(service_factory, reference):
    service, repo = service_factory()
    page = await overview(service)
    await service.callback(1, tap(page, "Change timing"), message_id=77)
    choice = (await service.message(1, 2, "8pm", reference))[0]
    old_confirm = tap(choice, "Use Night before")
    await service.callback(1, tap(choice, "‹ Back"))
    reply = (await service.callback(1, old_confirm))[0]
    assert "expired" in reply.text
    user = await repo.ensure_user(1)
    assert user.overview_period == "morning" and user.overview_morning_minute == 540


async def test_old_confirmation_cannot_be_used_by_another_user(service_factory, reference):
    service, repo = service_factory()
    page = await overview(service)
    await service.callback(1, tap(page, "Change timing"))
    choice = (await service.message(1, 2, "8pm", reference))[0]
    reply = (await service.callback(2, tap(choice, "Use Night before")))[0]
    assert "expired" in reply.text
    assert (await repo.ensure_user(2)).overview_period == "morning"
    reply = (await service.callback(1, tap(choice, "Use Night before")))[0]
    assert "Night before · 8PM SGT" in reply.text


@pytest.mark.parametrize("label,minutes", [("1 hour", 60), ("✓ 3 hours", 180), ("1 day", 1440)])
async def test_every_deadline_preset_saves_and_returns_to_alerts(service_factory, label, minutes):
    service, repo = service_factory()
    page = await alerts(service)
    timing = (await service.callback(1, tap(page, "Change timing")))[0]
    page = (await service.callback(1, tap(timing, label)))[0]
    assert page.text.startswith("<b>⏰ Deadline alerts</b>")
    assert rows(page) == [["Change timing", "Turn off", "‹ Back"]]
    assert (await repo.ensure_user(1)).deadline_lead_minutes == minutes
    home = (await service.callback(1, tap(page, "‹ Back")))[0]
    assert rows(home) == [["🌤 Daily overview", "⏰ Deadline alerts"]]


async def test_back_from_custom_interval_keeps_previous_value(service_factory):
    service, repo = service_factory()
    page = await alerts(service)
    timing = (await service.callback(1, tap(page, "Change timing")))[0]
    custom = (await service.callback(1, tap(timing, "Custom timing")))[0]
    back = (await service.callback(1, tap(custom, "‹ Back")))[0]
    assert back.text.startswith("⏰ <b>Deadline alerts · Timing</b>")
    assert (await repo.ensure_user(1)).deadline_lead_minutes == 180
    assert (await repo.ensure_user(1)).pending is None


async def test_disabled_settings_cannot_be_reenabled_by_stale_timing_buttons(service_factory):
    service, repo = service_factory()
    page = await overview(service)
    time_button = tap(page, "Change timing")
    off = (await service.callback(1, tap(page, "Turn off")))[0]
    old = (await service.callback(1, time_button))[0]
    assert rows(old) == rows(off) == [["Turn on", "‹ Back"]]
    assert not (await repo.ensure_user(1)).overview_enabled
    assert (await repo.ensure_user(1)).pending is None


async def test_real_telegram_dispatch_edits_same_settings_message(service_factory):
    from telegram import Update

    from tests.telegram_transport import callback_payload, message_payload
    from tests.test_group_events import buttons, make_bot

    service, repo = service_factory()
    app, transport = make_bot(service)
    async with app:
        await app.process_update(Update.de_json(message_payload("/settings", 100), app.bot))
        original_id = 101
        data = buttons(transport.sent[-1]["reply_markup"])["🌤 Daily overview"]

        async def click(data, update_id):
            payload = callback_payload(data)
            payload["update_id"] = update_id
            payload["callback_query"]["message"]["message_id"] = original_id
            await app.process_update(Update.de_json(payload, app.bot))

        await click(data, 2001)
        edits = [p for name, p in transport.calls if name == "editMessageText"]
        await click(buttons(edits[-1]["reply_markup"])["Change timing"], 2002)
        for update_id, text in [(3001, "25:00"), (3002, "9"), (3003, "at 7:30am SGT")]:
            await app.process_update(Update.de_json(message_payload(text, update_id), app.bot))
        user = await repo.ensure_user(1)
        assert user.overview_morning_minute == 450 and user.pending is None
        edits = [p for name, p in transport.calls if name == "editMessageText"]
        assert len(transport.sent) == 1
        assert all(int(p["message_id"]) == original_id for p in edits)
        assert "7:30AM SGT" in edits[-1]["text"]
        assert edits[-1]["parse_mode"] == "HTML"
        assert not service.extractor.calls and not repo.events
