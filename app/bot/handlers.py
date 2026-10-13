import logging

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.delivery import webhook_delivery
from app.bot.messages import Reply, send_reply
from app.services.image_reader import MAX_BYTES, PictureError
from app.services.jobs import Notifier, Undeliverable
from app.services.nusmods import ImportError
from app.services.scheduling import SLOW_DOWN

log = logging.getLogger(__name__)
GROUP_COMMANDS = {"event", "plan", "whosfree"}
PRIVATE_ONLY = "Please use AmIFree in a private chat. Open the bot and send /start."
NO_TEXT = (
    "I couldn't find any event details there. Forward the post or picture again, or type"
    " them, like:\nDinner Fri 7-9pm at Vivo"
)
UNSUPPORTED_PICTURE = (
    "I can't read pictures right now. Forward the post with its text, or type the details,"
    " like:\nDinner Fri 7-9pm at Vivo"
)
PICTURE_UNREADABLE = (
    "I couldn't read that picture just now. Forward it with its caption, or type the"
    " details, like:\nDinner Fri 7-9pm at Vivo"
)
ERROR = "I couldn't process that message. Please try again, or send the date/time manually. /cancel clears a pending draft."


async def send(update: Update, replies: list[Reply]):
    delivery = webhook_delivery.get()
    if delivery is not None:
        delivery.replies = replies
        await delivery.flush(update)
        return
    for reply in replies:
        await send_reply(update, reply)


async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: str):
    if not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await group_dispatch(update, context, operation)
        return
    service = context.application.bot_data["service"]
    try:
        if operation == "callback":
            try:
                await update.callback_query.answer()
            except TelegramError:
                # Expired callback acknowledgements must not prevent safe replay.
                pass
            replies = await service.callback(
                update.effective_user.id, update.callback_query.data or ""
            )
        elif operation == "command":
            name, args = split_command(update.effective_message.text)
            replies = await service.command(update.effective_user.id, name, args)
        else:
            try:
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            except TelegramError:
                pass
            message = update.effective_message
            reference = message.forward_origin.date if message.forward_origin else message.date
            text, notice = await gather_text(message, context, service, update.effective_user.id)
            if not text.strip():
                replies = [Reply(notice or NO_TEXT)]
            else:
                replies = await service.message(
                    update.effective_user.id, message.message_id, text, reference
                )
        await send(update, replies)
    except ImportError as exc:
        await send(update, [Reply(str(exc))])
    except Exception as exc:
        delivery = webhook_delivery.get()
        if delivery is not None:
            delivery.failed = True
            raise
        # Third-party exception strings can include API URLs/tokens: log types only.
        log.error(
            "bot_request_failed update_id=%s operation=%s error_type=%s",
            update.update_id,
            operation,
            type(exc).__name__,
        )
        await send(update, [Reply(ERROR)])


async def gather_text(message, context, service, telegram_id: int) -> tuple[str, str | None]:
    """All the words in a forwarded message, whatever it carries.

    Text and captions are used as they are. A poll contributes its question and options.
    A picture (a photo, or an image sent as a file) is read for its text and added after the
    caption. Returns (text, a notice to show when there is no text at all)."""
    parts = [message.text or message.caption or ""]
    if message.poll:
        parts += [message.poll.question] + [o.text for o in message.poll.options]
    picture, mime = None, "image/jpeg"
    if message.photo:
        picture = message.photo[-1]
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        picture, mime = message.document, message.document.mime_type
    notice = None
    if picture is not None and service.image_reader is None:
        notice = UNSUPPORTED_PICTURE  # pictures can't be read here: only the caption counts
    elif picture is not None:
        try:
            if (picture.file_size or 0) > MAX_BYTES:
                raise PictureError("too large")
            file = await context.bot.get_file(picture.file_id)
            data = bytes(await file.download_as_bytearray())
            seen = await service.read_picture(telegram_id, data, mime)
            if seen:
                parts.append(seen)
        except PictureError as exc:
            # A caption can still carry the plan; without one, say what went wrong.
            notice = str(exc) if str(exc) == SLOW_DOWN else PICTURE_UNREADABLE
        except Exception as exc:
            # Download or vision-API trouble must not lose the caption or the user's message.
            log.warning("picture_read_failed error_type=%s", type(exc).__name__)
            notice = PICTURE_UNREADABLE
    return "\n".join(p.strip() for p in parts if p and p.strip()), notice


def split_command(text: str) -> tuple[str, str]:
    head, _, args = text.partition(" ")
    return head.split("@")[0].lstrip("/").lower(), args.strip()


async def deliver_group(update: Update, service, result):
    query = update.callback_query
    if query:
        try:
            url = (
                f"https://t.me/{query.get_bot().username}?start={result.start}"
                if result.start
                else None
            )
            await query.answer(result.alert, show_alert=bool(result.alert), url=url)
        except TelegramError:
            pass
        if result.card:
            try:
                await query.edit_message_text(result.card.text, reply_markup=result.card.keyboard)
            except BadRequest:
                # Telegram rejects an edit that leaves the card unchanged.
                pass
    if result.post:
        sent = await update.effective_message.reply_text(
            result.post.text, reply_markup=result.post.keyboard
        )
        if result.group_event_id:
            await service.groups.posted(result.group_event_id, sent.message_id)
        if result.poll_id:
            await service.polls.posted(result.poll_id, sent.message_id)


async def group_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: str):
    """Groups: only explicit commands and taps on the bot's own cards are handled.

    Ordinary group messages are never read, stored or answered. Anything personal goes
    back as a callback alert that only the person who tapped can see.
    """
    service = context.application.bot_data["service"]
    query, message = update.callback_query, update.effective_message
    try:
        if operation == "callback":
            data = query.data or ""
            if not data.startswith(("g:", "p:")):
                await query.answer(
                    "Open a private chat with AmIFree to check availability.", show_alert=True
                )
                return
            handler = service.groups if data.startswith("g:") else service.polls
            result = await handler.callback(
                update.effective_user.id, update.effective_chat.id, data
            )
        elif operation == "command":
            name, args = split_command(message.text)
            if name not in GROUP_COMMANDS:
                await send(update, [Reply(PRIVATE_ONLY)])
                return
            if name == "whosfree":
                result = await service.polls.create(
                    update.effective_user.id, update.effective_chat.id, message.message_id, args
                )
            else:
                source = message.reply_to_message if not args else None
                text = args or (source.text or source.caption or "" if source else "")
                origin = source.forward_origin if source else None
                reference = origin.date if origin else (source.date if source else message.date)
                result = await service.groups.create(
                    update.effective_user.id,
                    update.effective_chat.id,
                    message.message_id,
                    text,
                    reference,
                )
        else:
            return
        await deliver_group(update, service, result)
    except Exception as exc:
        delivery = webhook_delivery.get()
        if delivery is not None:
            delivery.failed = True
            raise
        log.error(
            "group_request_failed update_id=%s operation=%s error_type=%s",
            update.update_id,
            operation,
            type(exc).__name__,
        )
        try:
            if query:
                await query.answer("Something went wrong. Please try again.", show_alert=True)
            else:
                await message.reply_text("I couldn't make that card. Please try again.")
        except TelegramError:
            pass


async def command(update, context):
    await dispatch(update, context, "command")


async def message(update, context):
    await dispatch(update, context, "message")


async def callback(update, context):
    await dispatch(update, context, "callback")


async def unsupported(update, context):
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await send(
            update,
            [
                Reply(
                    "I can read text, posters and screenshots, but not voice notes, videos or"
                    " stickers. Forward the post with its text or picture, or type the"
                    " details, like:\nDinner Fri 7-9pm at Vivo"
                )
            ],
        )


async def error_handler(update, context):
    delivery = webhook_delivery.get()
    if delivery is not None:
        delivery.failed = True
    log.error(
        "telegram_handler_failed update_id=%s error_type=%s",
        getattr(update, "update_id", None),
        type(context.error).__name__,
    )


def attach_notifier(application: Application, service) -> Notifier:
    """Give the outbox worker a way to DM users through this bot."""

    async def send_dm(telegram_id: int, reply: Reply):
        try:
            await application.bot.send_message(telegram_id, reply.text, reply_markup=reply.keyboard)
        except (Forbidden, BadRequest) as exc:
            # Blocked, or the user never opened a private chat with the bot.
            raise Undeliverable from exc

    service.notifier = Notifier(service, send_dm)
    return service.notifier


def register_handlers(application: Application, service):
    application.bot_data["service"] = service
    attach_notifier(application, service)

    async def refresh_card(group_event_id: str):
        event = await service.repo.get_group_event(group_event_id)
        if not event or not event.message_id:
            return
        card = await service.groups.render(event)
        try:
            await application.bot.edit_message_text(
                card.text,
                chat_id=event.chat_id,
                message_id=event.message_id,
                reply_markup=card.keyboard,
            )
        except TelegramError:
            pass

    service.on_group_change = refresh_card
    application.add_handler(
        CommandHandler(
            [
                "start",
                "help",
                "plans",
                "events",
                "block",
                "blocks",
                "reminders",
                "calendar",
                "free",
                "cancel",
                "delete",
                "deletemydata",
            ],
            command,
        )
    )
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    # A photo, video or file with a caption: read the caption like any forwarded plan.
    application.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, message))
    # A picture on its own (a poster or a screenshot), an image sent as a file, or a poll.
    application.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE | filters.POLL) & ~filters.COMMAND, message
        )
    )
    application.add_handler(MessageHandler(filters.COMMAND, command))
    application.add_handler(MessageHandler(filters.ALL, unsupported))
    application.add_error_handler(error_handler)
