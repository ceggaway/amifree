import logging

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.delivery import webhook_delivery
from app.bot.messages import Reply
from app.services.nusmods import ImportError

log = logging.getLogger(__name__)
ERROR = "I couldn't process that message. Please try again, or send the date/time manually. /cancel clears a pending draft."


async def send(update: Update, replies: list[Reply]):
    delivery = webhook_delivery.get()
    if delivery is not None:
        delivery.replies = replies
        await delivery.flush(update)
        return
    for reply in replies:
        await update.effective_message.reply_text(reply.text, reply_markup=reply.keyboard)


async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: str):
    if not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        # Never show personal details or handle private RSVP callbacks in groups.
        if update.callback_query:
            await update.callback_query.answer(
                "Open a private chat with AmIFree to check availability.", show_alert=True
            )
        else:
            await send(
                update,
                [Reply("Please use AmIFree in a private chat. Open the bot and send /start.")],
            )
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
            head, _, args = update.effective_message.text.partition(" ")
            command = head.split("@")[0].lstrip("/").lower()
            replies = await service.command(update.effective_user.id, command, args.strip())
        else:
            try:
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            except TelegramError:
                pass
            message = update.effective_message
            reference = message.forward_origin.date if message.forward_origin else message.date
            replies = await service.message(
                update.effective_user.id, message.message_id, message.text, reference
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
                    "Please forward a text event. Photos, posters, audio and recurring events aren't supported in this MVP."
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


def register_handlers(application: Application, service):
    application.bot_data["service"] = service
    application.add_handler(
        CommandHandler(
            ["start", "help", "plans", "events", "cancel", "delete", "deletemydata"], command
        )
    )
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    application.add_handler(MessageHandler(filters.COMMAND, command))
    application.add_handler(MessageHandler(filters.ALL, unsupported))
    application.add_error_handler(error_handler)
