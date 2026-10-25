from telegram import Update

from tests.test_group_events import CHAT, make_bot

BOT_ID = 123456  # the id the fake Telegram transport gives this bot


def joined(update_id, *members):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1791100800,
            "chat": {"id": CHAT, "type": "supergroup", "title": "CCA"},
            "from": {"id": 1, "is_bot": False, "first_name": "Member"},
            "new_chat_members": [
                {"id": member, "is_bot": member == BOT_ID, "first_name": "Someone"}
                for member in members
            ],
        },
    }


def sent_texts(transport):
    return [call["text"] for call in transport.sent if "text" in call]


async def test_bot_says_hello_when_added_to_a_group(service_factory):
    service, _ = service_factory()
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(Update.de_json(joined(9900, BOT_ID), bot.bot))
    texts = sent_texts(transport)
    assert len(texts) == 1
    assert "send /app" in texts[0]


async def test_bot_stays_quiet_when_someone_else_joins(service_factory):
    service, _ = service_factory()
    bot, transport = make_bot(service)
    async with bot:
        await bot.process_update(Update.de_json(joined(9901, 42), bot.bot))
    assert sent_texts(transport) == []
