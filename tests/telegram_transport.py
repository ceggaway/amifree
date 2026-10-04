import asyncio
import json

from telegram.request import BaseRequest


class TelegramTransport(BaseRequest):
    """Telegram Bot API simulator exercising the real PTB serialization/dispatch."""

    def __init__(self):
        self.calls = []
        self.sent = []

    @property
    def read_timeout(self):
        return 30

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result = {
                "id": 123456,
                "is_bot": True,
                "first_name": "AmIFree",
                "username": "AmIFreeTestBot",
            }
        elif endpoint == "sendMessage":
            self.sent.append(params)
            result = {
                "message_id": 100 + len(self.sent),
                "date": 1791100800,
                "chat": {"id": int(params["chat_id"]), "type": "private"},
                "text": params["text"],
            }
        elif endpoint == "getUpdates":
            await asyncio.sleep(0.01)
            result = []
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


def message_payload(text, message_id=1, telegram_id=1, *, group=False, forward_timestamp=None):
    result = {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": 1791100800,
            "chat": {"id": -1 if group else telegram_id, "type": "group" if group else "private"},
            "from": {"id": telegram_id, "is_bot": False, "first_name": "Student"},
            "text": text,
        },
    }
    if text.startswith("/"):
        result["message"]["entities"] = [
            {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}
        ]
    if forward_timestamp:
        result["message"]["forward_origin"] = {
            "type": "hidden_user",
            "date": forward_timestamp,
            "sender_user_name": "Friend",
        }
    return result


def callback_payload(data, telegram_id=1, *, group=False):
    return {
        "update_id": 1000,
        "callback_query": {
            "id": "callback_test",
            "from": {"id": telegram_id, "is_bot": False, "first_name": "Student"},
            "chat_instance": "test",
            "data": data,
            "message": {
                "message_id": 100,
                "date": 1791100800,
                "chat": {
                    "id": -1 if group else telegram_id,
                    "type": "group" if group else "private",
                },
                "text": "event card",
            },
        },
    }
