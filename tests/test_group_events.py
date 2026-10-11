import json

from telegram import Update
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.schemas.event import Status
from tests.conftest import extraction, tap
from tests.telegram_transport import TelegramTransport

CHAT = -100500
TEXT = "CCA dinner Friday 7–9pm at Vivo"


def group_message(text, message_id, telegram_id, *, reply_to=None, chat=CHAT):
    message = {
        "message_id": message_id,
        "date": 1791100800,
        "chat": {"id": chat, "type": "supergroup", "title": "CCA"},
        "from": {"id": telegram_id, "is_bot": False, "first_name": "Member"},
        "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if reply_to:
        message["reply_to_message"] = {
            "message_id": message_id - 1,
            "date": 1791100800,
            "chat": message["chat"],
            "from": {"id": 77, "is_bot": False, "first_name": "Friend"},
            "text": reply_to,
        }
    return {"update_id": message_id, "message": message}


def group_tap(data, telegram_id, *, chat=CHAT, update_id=5000):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": telegram_id, "is_bot": False, "first_name": "Member"},
            "chat_instance": "test",
            "data": data,
            "message": {
                "message_id": 101,
                "date": 1791100800,
                "chat": {"id": chat, "type": "supergroup", "title": "CCA"},
                "text": "card",
            },
        },
    }


def make_bot(service):
    transport = TelegramTransport()
    bot = Application.builder().token("123456:test").request(transport).updater(None).build()
    register_handlers(bot, service)
    return bot, transport


def buttons(markup):
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return {b["text"]: b["callback_data"] for row in markup["inline_keyboard"] for b in row}


def alerts(transport):
    return [p for name, p in transport.calls if name == "answerCallbackQuery"]


async def post_card(bot, transport, repo, organiser=1):
    await bot.process_update(
        Update.de_json(group_message("/event", 50, organiser, reply_to=TEXT), bot.bot)
    )
    card = transport.sent[-1]
    return card, buttons(card["reply_markup"]), next(iter(repo.group_events.values()))


async def test_event_card_from_a_replied_message(service_factory):
    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        card, taps, event = await post_card(bot, transport, repo)
    assert service.extractor.calls[0][0] == TEXT
    assert "CCA dinner" in card["text"] and "Location: Vivo" in card["text"]
    assert "Going 0 · Maybe 0 · Can't 0" in card["text"]
    assert list(taps) == [
        "Going",
        "Maybe",
        "Can't",
        "Am I free?",
        "Save",
        "Edit",
        "Cancel event",
        "Nudge",
    ]
    assert all(len(data.encode()) <= 64 for data in taps.values())
    # The bot records its own card message so later edits can re-render it.
    assert event.message_id == 101 and event.chat_id == CHAT


async def test_same_card_gives_each_member_a_private_answer(service_factory, reference):
    service, repo = service_factory(extraction(title="Hall supper"), extraction())
    bot, transport = make_bot(service)
    # Member 2 already has a commitment at that time; member 3 has nothing.
    mine = (await service.message(2, 9, "Hall supper Friday 7–9pm", reference))[0]
    await service.callback(2, mine.keyboard.inline_keyboard[0][0].callback_data)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        posted = len(transport.sent)
        await bot.process_update(Update.de_json(group_tap(taps["Am I free?"], 2), bot.bot))
        await bot.process_update(Update.de_json(group_tap(taps["Am I free?"], 3), bot.bot))
    busy, free = alerts(transport)
    assert "Clashes with Hall supper" in busy["text"] and busy["show_alert"]
    assert free["text"].startswith("✅ No clashes found") and free["show_alert"]
    assert all("Only you can see this" in a["text"] and len(a["text"]) <= 200 for a in (busy, free))
    # Nothing personal is posted to, or edited into, the group chat.
    assert len(transport.sent) == posted
    assert not [c for c in transport.calls if c[0] == "editMessageText"]
    # Checking availability records membership but never an RSVP.
    assert not [e for e in repo.events.values() if e.group_event_id]
    assert len({m for m in repo.members if m[0] == CHAT}) == 3


async def test_rsvp_updates_headcount_and_personal_plans(service_factory, reference):
    service, repo = service_factory(extraction())
    bot, transport = make_bot(service)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        for n, (member, label) in enumerate(
            ((2, "Going"), (3, "Going"), (4, "Maybe"), (5, "Can't"))
        ):
            await bot.process_update(
                Update.de_json(group_tap(taps[label], member, update_id=6000 + n), bot.bot)
            )
        # Changing an answer moves the count rather than adding a second one.
        await bot.process_update(
            Update.de_json(group_tap(taps["Maybe"], 3, update_id=6100), bot.bot)
        )
        # Save keeps an existing answer.
        await bot.process_update(
            Update.de_json(group_tap(taps["Save"], 2, update_id=6101), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_tap(taps["Save"], 6, update_id=6102), bot.bot)
        )
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "Going 1 · Maybe 2 · Can't 1" in edits[-1]["text"]
    assert "Member" not in edits[-1]["text"]  # a count, never names
    linked = {e.user_id: e for e in repo.events.values() if e.group_event_id == event.id}
    assert len(linked) == 5
    two, six = (await repo.ensure_user(2)).id, (await repo.ensure_user(6)).id
    assert linked[two].status == Status.GOING and linked[six].status == Status.SAVED
    # The linked row is the member's own plan: listed in /plans and blocking when Going.
    plans = await service.command(2, "plans")
    assert "CCA dinner" in plans[0].text and "Status: Going" in plans[0].text
    assert "Edit" not in [b.text for row in plans[0].keyboard.inline_keyboard for b in row]
    assert "No upcoming plans" in (await service.command(7, "plans"))[0].text


async def test_going_from_a_group_card_blocks_later_checks(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(title="Other"))
    bot, transport = make_bot(service)
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        await bot.process_update(Update.de_json(group_tap(taps["Going"], 2), bot.bot))
        # A private RSVP on the linked plan re-renders the shared card's headcount.
        mine = (await service.command(2, "plans"))[0]
        await service.callback(2, tap(mine, "Maybe"))
    edits = [p for name, p in transport.calls if name == "editMessageText"]
    assert "Going 0 · Maybe 1" in edits[-1]["text"]
    assert int(edits[-1]["chat_id"]) == CHAT and int(edits[-1]["message_id"]) == 101
    linked = next(e for e in repo.events.values() if e.group_event_id == event.id)
    await service.callback(2, f"go:{linked.id}:{linked.version}")
    other = (await service.message(2, 71, "Other Friday 7–9pm", reference))[0]
    assert other.text.startswith("⚠️ Clashes with CCA dinner")
    linked = next(e for e in repo.events.values() if e.group_event_id == event.id)
    edit = await service.callback(2, f"edit:{linked.id}:{linked.version}")
    assert "Only the organiser" in edit[0].text


async def test_groups_ignore_chatter_and_foreign_cards(service_factory, reference):
    service, repo = service_factory(extraction(), extraction(), extraction(kind="unknown"))
    bot, transport = make_bot(service)
    private = (await service.message(9, 3, TEXT, reference))[0]
    async with bot:
        _, taps, event = await post_card(bot, transport, repo)
        sent = len(transport.sent)
        # Ordinary group messages are never answered, extracted or stored.
        await bot.process_update(Update.de_json(group_message("dinner fri 7pm?", 60, 2), bot.bot))
        assert len(transport.sent) == sent and len(service.extractor.calls) == 2
        # A card's buttons do nothing in a different chat.
        await bot.process_update(Update.de_json(group_tap(taps["Going"], 2, chat=-999), bot.bot))
        # Private-chat buttons still refuse to run in a group.
        await bot.process_update(
            Update.de_json(
                group_tap(private.keyboard.inline_keyboard[0][0].callback_data, 9, update_id=5001),
                bot.bot,
            )
        )
        await bot.process_update(
            Update.de_json(group_tap("g:go:not-a-uuid", 2, update_id=5002), bot.bot)
        )
        await bot.process_update(
            Update.de_json(group_message("/event hello there", 61, 2), bot.bot)
        )
        await bot.process_update(Update.de_json(group_message("/event", 62, 2), bot.bot))
    texts = [a["text"] for a in alerts(transport)]
    assert texts[0] == "This card is no longer available."
    assert "private chat" in texts[1] and "isn't valid" in texts[2]
    assert not [e for e in repo.events.values() if e.group_event_id]
    assert next(iter(repo.events.values())).status == Status.PREVIEW
    assert "couldn't find an event" in transport.sent[-2]["text"]
    assert "Reply /event" in transport.sent[-1]["text"]
    assert len(repo.group_events) == 1


async def test_incomplete_plan_and_retried_command(service_factory):
    service, repo = service_factory(extraction(end_time_text=None), extraction(), extraction())
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(
            Update.de_json(group_message("/event CCA dinner Friday 7pm", 40, 1), bot.bot)
        )
        assert "need a bit more" in transport.sent[-1]["text"] and not repo.group_events
        update = group_message("/event " + TEXT, 41, 1)
        await bot.process_update(Update.de_json(update, bot.bot))
        await bot.process_update(Update.de_json(update, bot.bot))
    assert len(repo.group_events) == 1 and len(transport.sent) == 2
    assert "works in group chats" in (await service.command(1, "event"))[0].text
