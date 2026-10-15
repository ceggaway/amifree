import asyncio
import json

from telegram.request import BaseRequest


class TelegramTransport(BaseRequest):
    """Telegram Bot API simulator exercising the real PTB serialization/dispatch."""

    def __init__(self):
        self.calls = []
        self.sent = []
        self.documents = []
        self.admins = set()  # telegram ids reported as group admins by getChatMember
        self.blocked = set()  # telegram ids the bot cannot message

    @property
    def read_timeout(self):
        return 30

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        if "/file/bot" in url:  # a file download, not a Bot API call
            self.calls.append(("download", {"url": url}))
            return 200, b"\xff\xd8fake-jpeg-bytes"
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
        elif endpoint == "getChatMember":
            uid = int(params["user_id"])
            result = {
                "status": "administrator" if uid in self.admins else "member",
                "user": {"id": uid, "is_bot": False, "first_name": "Member"},
            }
            if uid in self.admins:
                result["can_be_edited"] = False
                for flag in (
                    "is_anonymous can_manage_chat can_delete_messages can_manage_video_chats"
                    " can_restrict_members can_promote_members can_change_info"
                    " can_invite_users can_post_stories can_edit_stories can_delete_stories"
                ).split():
                    result[flag] = False
        elif endpoint == "sendDocument":
            if int(params["chat_id"]) in self.blocked:
                return 403, json.dumps(
                    {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}
                ).encode()
            files = [f for param in request_data._parameters for f in (param.input_files or [])]
            self.documents.append(
                params
                | {
                    "filename": files[0].filename if files else None,
                    "content": files[0].input_file_content if files else b"",
                }
            )
            result = {
                "message_id": 500 + len(self.documents),
                "date": 1791100800,
                "chat": {"id": int(params["chat_id"]), "type": "private"},
            }
        elif endpoint == "sendMessage":
            if int(params["chat_id"]) in self.blocked:
                return 403, json.dumps(
                    {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}
                ).encode()
            self.sent.append(params)
            result = {
                "message_id": 100 + len(self.sent),
                "date": 1791100800,
                "chat": {"id": int(params["chat_id"]), "type": "private"},
                "text": params["text"],
            }
        elif endpoint == "getFile":
            result = {
                "file_id": params["file_id"],
                "file_unique_id": "u1",
                "file_size": 17,
                "file_path": "photos/poster.jpg",
            }
        elif endpoint == "getUpdates":
            await asyncio.sleep(0.01)
            result = []
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


def photo_payload(caption, message_id=1, telegram_id=1, *, forward_timestamp=None):
    """A photo message: Telegram puts its words in `caption`, not `text`."""
    payload = message_payload("x", message_id, telegram_id, forward_timestamp=forward_timestamp)
    message = payload["message"]
    del message["text"]
    message["photo"] = [{"file_id": "poster", "file_unique_id": "p1", "width": 640, "height": 640}]
    if caption is not None:
        message["caption"] = caption
    return payload


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


def document_payload(mime="image/png", caption=None, message_id=1, telegram_id=1):
    """An image sent as a file (uncompressed), which Telegram delivers as a document."""
    payload = message_payload("x", message_id, telegram_id)
    message = payload["message"]
    del message["text"]
    message["document"] = {
        "file_id": "scan",
        "file_unique_id": "d1",
        "file_name": "poster.png",
        "mime_type": mime,
        "file_size": 20,
    }
    if caption is not None:
        message["caption"] = caption
    return payload


def poll_payload(question, options, message_id=1, telegram_id=1):
    payload = message_payload("x", message_id, telegram_id)
    message = payload["message"]
    del message["text"]
    message["poll"] = {
        "id": "poll-1",
        "question": question,
        "options": [
            {"text": o, "voter_count": 0, "persistent_id": f"opt-{i}"}
            for i, o in enumerate(options)
        ],
        "total_voter_count": 0,
        "is_closed": False,
        "is_anonymous": True,
        "type": "regular",
        "allows_multiple_answers": False,
        "allows_revoting": True,
        "members_only": False,
    }
    return payload
