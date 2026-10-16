import asyncio
import logging
import warnings

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from app.bot.delivery import webhook_delivery
from app.bot.messages import Reply, send_reply
from app.services.image_reader import MAX_BYTES, PictureError
from app.services.jobs import Notifier, Undeliverable
from app.services.nusmods import ImportError
from app.services.scheduling import SLOW_DOWN

log = logging.getLogger(__name__)
GROUP_COMMANDS = {"event", "plan", "whosfree", "stats", "export", "detect", "attendance", "pro"}
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


async def send_private(bot, telegram_id: int, reply: Reply):
    """A direct message to one person. Raises Forbidden/BadRequest when they never opened the bot."""
    if reply.document:
        from telegram import InputFile

        filename, content = reply.document
        await bot.send_document(
            telegram_id, InputFile(content, filename=filename), caption=reply.text
        )
    else:
        await bot.send_message(telegram_id, reply.text, reply_markup=reply.keyboard)


def addressed_to_me(text: str, username: str | None) -> bool:
    head = (text or "").split(maxsplit=1)[0] if (text or "").strip() else ""
    _, at, target = head.partition("@")
    return not at or not username or target.lower() == username.lower()


async def is_admin(bot, message) -> bool:
    # An anonymous admin posts as the group itself.
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    except TelegramError:
        return False
    return member.status in ("creator", "administrator")


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
        if result.adopt_event_id:
            # A detection suggestion becomes the card: edits now go to this message.
            await service.groups.posted(result.adopt_event_id, query.message.message_id)
        if result.card:
            try:
                await query.edit_message_text(result.card.text, reply_markup=result.card.keyboard)
            except RetryAfter:
                # Telegram's edit limit for this group: the answer is saved, so redraw later.
                parts = (query.data or "").split(":")
                if parts[0] == "g" and len(parts) == 3:
                    await service.group_changed(parts[2])
            except TelegramError:
                # Telegram rejects an edit that leaves the card unchanged, or one too old.
                pass
        if result.delete_message:
            try:
                await query.message.delete()
            except TelegramError:
                pass
    if result.post:
        sent = await update.effective_message.reply_text(
            result.post.text, reply_markup=result.post.keyboard
        )
        if result.group_event_id:
            await service.groups.posted(result.group_event_id, sent.message_id)
        if result.poll_id:
            await service.polls.posted(result.poll_id, sent.message_id)
        if result.suggestion_id:
            await service.detection.posted(result.suggestion_id, sent.message_id)
    if result.invoice:
        invoice = result.invoice
        try:
            from telegram import LabeledPrice

            await update.get_bot().send_invoice(
                chat_id=update.effective_user.id,
                title=invoice["title"],
                description=invoice["description"],
                payload=f"pro:{invoice['chat_id']}",
                currency="XTR",  # Telegram Stars: no provider token
                prices=[LabeledPrice("Clubs Pro", invoice["stars"])],
                provider_token="",
            )
        except (Forbidden, BadRequest):
            await update.effective_message.reply_text(
                "I can't message you yet. Open a private chat with me, tap Start, then try /pro again."
            )
    if result.dm:
        bot = update.get_bot()
        try:
            await send_private(bot, update.effective_user.id, result.dm)
        except (Forbidden, BadRequest):
            await update.effective_message.reply_text(
                "I can't message you yet. Open a private chat with me, tap Start, then try again."
            )


async def group_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: str):
    """Groups: only explicit commands and taps on the bot's own cards are handled.

    Ordinary group messages are never read, stored or answered. Anything personal goes
    back as a callback alert that only the person who tapped can see.
    """
    service = context.application.bot_data["service"]
    query, message = update.callback_query, update.effective_message
    user = update.effective_user
    who = {"name": user.first_name, "username": user.username}
    try:
        if operation == "callback":
            data = query.data or ""
            handlers = {"g:": service.groups, "p:": service.polls, "d:": service.detection}
            handler = next((h for prefix, h in handlers.items() if data.startswith(prefix)), None)
            if handler is None:
                await query.answer(
                    "Open a private chat with AmIFree to check availability.", show_alert=True
                )
                return
            result = await handler.callback(user.id, update.effective_chat.id, data, **who)
        elif operation == "command":
            name, args = split_command(message.text)
            if not addressed_to_me(message.text, context.bot.username):
                return  # /stats@OtherBot belongs to another bot in this group
            if name not in GROUP_COMMANDS:
                await send(update, [Reply(PRIVATE_ONLY)])
                return
            if name == "whosfree":
                result = await service.polls.create(
                    user.id, update.effective_chat.id, message.message_id, args
                )
            elif name == "stats":
                result = await service.groups.stats(user.id, update.effective_chat.id)
            elif name == "attendance":
                result = await service.groups.attendance_command(user.id, update.effective_chat.id)
            elif name == "pro":
                result = await service.pro.status(update.effective_chat.id)
            elif name == "export":
                result = await service.groups.export(user.id, update.effective_chat.id, args)
            elif name == "detect":
                result = await service.detection.command(
                    user.id, update.effective_chat.id, args, await is_admin(context.bot, message)
                )
            else:
                source = message.reply_to_message if not args else None
                text = args or (source.text or source.caption or "" if source else "")
                origin = source.forward_origin if source else None
                reference = origin.date if origin else (source.date if source else message.date)
                result = await service.groups.create(
                    user.id,
                    update.effective_chat.id,
                    message.message_id,
                    text,
                    reference,
                    **who,
                )
        elif operation == "message":
            # Ordinary group messages are dropped unless an admin switched detection on.
            if message.from_user and message.from_user.is_bot:
                return
            try:
                result = await service.detection.on_message(
                    user.id,
                    update.effective_chat.id,
                    message.message_id,
                    message.text or message.caption or "",
                    message.date,
                )
                if result is not None:
                    await deliver_group(update, service, result)
            except Exception as exc:
                # Never answer ordinary chatter with an error, and never ask Telegram to retry it.
                log.warning("detection_message_failed error_type=%s", type(exc).__name__)
            return
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


class CardRefresher:
    """Edits a group's card at most once per interval; changes in between are combined.

    Telegram limits how often a bot may edit messages in one group. The first change shows at
    once, and a burst of RSVPs afterwards becomes a single edit carrying the latest headcount.
    When Telegram still says "retry after N seconds", the edit is tried again then."""

    MAX_KEYS = 5000

    def __init__(self, interval: float, edit):
        self.interval, self.edit = interval, edit
        self.last: dict[str, float] = {}
        self.waiting: dict[str, asyncio.Task] = {}

    async def refresh(self, key: str):
        loop = asyncio.get_running_loop()
        wait = self.last.get(key, float("-inf")) + self.interval - loop.time()
        if wait > 0:
            self.later(key, wait)
            return
        await self.run(key)

    async def run(self, key: str):
        loop = asyncio.get_running_loop()
        if len(self.last) > self.MAX_KEYS:
            cutoff = loop.time() - max(self.interval, 60)
            self.last = {k: t for k, t in self.last.items() if t > cutoff}
        self.last[key] = loop.time()
        try:
            await self.edit(key)
        except RetryAfter as exc:
            with warnings.catch_warnings():
                # PTB is moving retry_after from seconds to a timedelta; accept either.
                warnings.simplefilter("ignore")
                seconds = exc.retry_after
            seconds = seconds.total_seconds() if hasattr(seconds, "total_seconds") else seconds
            self.later(key, float(seconds) + 0.5)

    def later(self, key: str, wait: float):
        if key in self.waiting:
            return  # the pending edit renders the latest state when it runs

        async def delayed():
            try:
                await asyncio.sleep(wait)
            finally:
                self.waiting.pop(key, None)
            try:
                await self.run(key)
            except Exception as exc:
                log.warning("card_refresh_failed error_type=%s", type(exc).__name__)

        self.waiting[key] = asyncio.create_task(delayed())


def register_handlers(application: Application, service):
    application.bot_data["service"] = service
    attach_notifier(application, service)

    async def edit_card(group_event_id: str):
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
        except RetryAfter:
            raise  # CardRefresher tries again when Telegram allows
        except TelegramError:
            pass

    async def edit_poll(poll_id: str):
        poll = await service.repo.get_poll(poll_id)
        if not poll or not poll.message_id:
            return
        card = await service.polls.render(poll)
        try:
            await application.bot.edit_message_text(
                card.text,
                chat_id=poll.chat_id,
                message_id=poll.message_id,
                reply_markup=card.keyboard,
            )
        except TelegramError:
            pass

    async def post_card(chat_id: int, card: Reply) -> int | None:
        sent = await application.bot.send_message(chat_id, card.text, reply_markup=card.keyboard)
        return sent.message_id

    async def delete_message(chat_id: int, message_id: int):
        try:
            await application.bot.delete_message(chat_id, message_id)
        except TelegramError:
            pass

    refresher = CardRefresher(service.settings.card_edit_interval_seconds, edit_card)

    async def refund_stars(telegram_id: int, charge_id: str):
        await application.bot.refund_star_payment(telegram_id, charge_id)

    service.refund_star_payment = refund_stars
    service.on_group_change = refresher.refresh
    service.on_poll_change = edit_poll
    service.post_group_card = post_card
    service.delete_group_message = delete_message
    application.add_handler(
        CommandHandler(
            [
                "start",
                "help",
                "plans",
                "events",
                "block",
                "blocks",
                "deadline",
                "deadlines",
                "reminders",
                "calendar",
                "usual",
                "refund",
                "free",
                "cancel",
                "delete",
                "deletemydata",
            ],
            command,
        )
    )

    async def precheckout(update, context):
        query = update.pre_checkout_query
        problem = service.pro.validate(query.invoice_payload, query.currency, query.total_amount)
        await query.answer(ok=problem is None, error_message=problem)

    async def paid(update, context):
        payment = update.effective_message.successful_payment
        try:
            text, refund_now = await service.pro.complete(
                update.effective_user.id,
                payment.invoice_payload,
                payment.telegram_payment_charge_id,
                payment.currency,
                payment.total_amount,
            )
            if refund_now and not await service.pro.give_back(
                update.effective_user.id, payment.telegram_payment_charge_id
            ):
                text += "\nI couldn't return it automatically: please contact the AmIFree team."
        except Exception as exc:
            # The money moved: never lose the record to a transient error. Telegram retries.
            delivery = webhook_delivery.get()
            if delivery is not None:
                delivery.failed = True
            log.error("payment_record_failed error_type=%s", type(exc).__name__)
            raise
        await send(update, [Reply(text)])

    application.add_handler(PreCheckoutQueryHandler(precheckout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid))
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
