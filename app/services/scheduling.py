"""Application use cases; no Telegram network calls and no LLM availability decisions."""

import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as day_time
from uuid import UUID

from app.bot import onboarding
from app.bot.keyboards import (
    agenda_keyboard,
    calendar_settings_keyboard,
    change_keyboard,
    choose_keyboard,
    confirm_keyboard,
    deadline_keyboard,
    free_keyboard,
    menu_keyboard,
    page_keyboard,
    personal_calendars_keyboard,
    plan_undo_keyboard,
    recurring_keyboard,
    reminder_keyboard,
    remove_block_keyboard,
    settings_keyboard,
    undo_keyboard,
    with_undo,
)
from app.bot.messages import (
    HELP,
    Reply,
    checked_line,
    clock,
    details,
    event_card,
    time_range,
    verdict_line,
)
from app.config import Settings
from app.models.timetable import BlockRule
from app.models.user import User
from app.schemas.event import (
    SG,
    DeadlineDraft,
    Event,
    EventDraft,
    Extraction,
    GroupEvent,
    Status,
)
from app.services import quick_reminders
from app.services.availability import check_availability
from app.services.calendar_export import build_feed, build_ics, ics_filename
from app.services.date_resolution import (
    WEEKDAYS,
    Clarification,
    asked_field,
    fill_answer,
    infer_meridiem,
    single_event_guard,
    validate_extraction,
    weekly_weekday,
)
from app.services.deadlines import EXAMPLE as DEADLINE_EXAMPLE
from app.services.deadlines import card_text, due_line, validate_deadline
from app.services.detection import DetectionService
from app.services.event_extractor import settle_ambiguity, settle_kind
from app.services.event_updates import match_updates
from app.services.free_time import free_gaps, parse_free, weekend
from app.services.group_events import GroupEventService
from app.services.group_hub import GroupHub
from app.services.image_reader import PictureError
from app.services.personal_calendar import (
    MAX_CALENDARS,
    CalendarLinkError,
    CalendarLinkGone,
    find_link,
    link_hash,
    seal,
    unseal,
    window_covers,
)
from app.services.personal_calendar import URL as CALENDAR_URL
from app.services.polls import PollService
from app.services.pro import ProService
from app.services.quick_blocks import (
    HORIZON_DAYS,
    MAX_RULES,
    USAGE,
    describe,
    expand_blocks,
    parse_block,
)
from app.services.quick_blocks import (
    RANGE as BLOCK_RANGE,
)
from app.services.ratelimit import RateLimiter
from app.services.usual_times import describe as describe_usual
from app.services.usual_times import parse_usual

log = logging.getLogger(__name__)


FEED_PAST, FEED_AHEAD = timedelta(days=30), timedelta(days=400)
FEED_SEEN_EVERY = timedelta(minutes=10)
CALENDAR_REFRESH = timedelta(hours=1)
# Link previews fetch the feed too; they must not count as the user's calendar subscribing.
TIMEISH = re.compile(r"\d{1,2}[:.]\d{2}|\d\s*[ap]\.?m\b|\b\d{4}\b|\bnoon\b|\bmidnight\b", re.I)
# Words that make a reply small talk rather than a place ("ok thanks", "never mind").
NOT_A_VENUE = frozenset(
    "ok okay k yes no nope yep yeah sure fine thanks thank you ty hi hello hey nvm never mind "
    "nevermind cancel stop same change none nothing pls please lol".split()
)
EDIT_FIELDS = (
    "title",
    "date_text",
    "start_time_text",
    "end_time_text",
    "end_date_text",
    "location",
    "notes",
)


def bare_venue(extracted: Extraction, previous: dict | None, text: str) -> Extraction:
    """When an edit reply changed nothing and holds no date or time, take it as the venue.

    The extractor won't "infer a venue" from a bare phrase, so "clb lvl 3" used to come back
    unchanged. The preview still shows before and after, so a wrong guess is easy to refuse."""
    answer = " ".join(text.split())
    words = set(re.findall(r"[a-z']+", answer.lower()))
    if (
        not previous
        or extracted.kind == "cancel"
        or not answer
        or (words and words <= NOT_A_VENUE)
        or len(answer) > 120
        or answer.startswith("/")
        or TIMEISH.search(answer)
    ):
        return extracted
    current = extracted.model_dump()
    if any(current.get(f) != previous.get(f) for f in EDIT_FIELDS):
        return extracted
    return extracted.model_copy(update={"location": answer})


FREE_EXAMPLES = "For example: /free fri 7-9pm, /free 15 oct or /free next week"
USUAL_HELP = (
    "Tell me when you're usually free and group polls will start from it.\n"
    "Examples: /usual tue evenings, thu evenings, sat all day\n"
    "/usual weekdays after 6pm   ·   /usual mon-fri 6-9pm   ·   /usual clear"
)
SLOW_DOWN = "You're sending a lot very quickly. Give me a minute, then try again."
PREVIEW_BOTS = ("telegrambot", "whatsapp", "facebookexternalhit", "slackbot", "discordbot")
# A lone time in a block with no range ("busy 14 oct 18:30"); never a date such as "14 oct".
BLOCK_TIME = re.compile(
    r"(?<![\w/])(?:at\s+)?(?:\d{1,2}[:.]\d{2}\s*(?:[ap]m)?|\d{1,2}\s*[ap]m|\d{4}\s*hrs?)(?![\w/])",
    re.I,
)


def synced(user: User) -> bool:
    return bool(user.calendar_feed_token and user.calendar_feed_seen_at)


def decided_card(event: Event, availability, note: str, user: User) -> Reply:
    """The tapped card, updated in place: same verdict and details, new status and buttons."""
    card = event_card(event, availability, synced(user))
    if event.status in (Status.SAVED, Status.GOING) and not user.calendar_feed_token:
        note += "\nTip: /calendar puts every plan in your iPhone calendar automatically."
    return Reply(f"{card.text}\n{note}", card.keyboard, edit=True)


UNDO_WINDOW = timedelta(minutes=30)

# Menu button -> the command it runs.
MENU_COMMANDS = {
    "menu:plans": "plans",
    "menu:free": "free",
    "menu:deadlines": "deadlines",
    "menu:settings": "settings",
}


class SchedulingService:
    def __init__(
        self,
        repository,
        extractor,
        importer,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        calendar_reader=None,
        image_reader=None,
    ):
        self.repo, self.extractor, self.importer, self.settings = (
            repository,
            extractor,
            importer,
            settings,
        )
        self.clock = clock or (lambda: datetime.now(SG))
        # Reads personal calendar links; None where no network is available (most tests).
        self.calendar_reader = calendar_reader
        # Reads the text in forwarded pictures; None leaves pictures to their captions.
        self.image_reader = image_reader
        self.groups = GroupEventService(self)
        self.polls = PollService(self)
        # Set by the bot layer: re-renders a group card after its headcount changes.
        self.on_group_change = None
        # Set by the bot layer: re-renders a poll card, and posts a new card into a group.
        self.on_poll_change = None
        self.post_group_card = None
        self.delete_group_message = None
        self.refund_star_payment = None
        self.detection = DetectionService(self)
        self.pro = ProService(self)
        self.hub = GroupHub(self)
        # Set by the bot layer: is this person in that group (asks Telegram), and its name.
        self.check_chat_member = None
        self.chat_title = None
        # Set at startup: the outbox worker that sends reminders, alerts and nudges.
        self.notifier = None
        # What was just removed, kept briefly so Undo can put it back: token -> (expires,
        # user id, kind, fields). In memory only: after a restart an old Undo says it's too late.
        self.removed: dict[str, tuple[datetime, str, str, dict]] = {}
        # Bound what one person can spend: LLM extractions and outbound calendar fetches.
        self.extraction_limit = RateLimiter(15, 300)
        self.picture_limit = RateLimiter(10, 300)  # a picture is also an extraction afterwards
        self.calendar_limit = RateLimiter(8, 600)

    async def flush_notifications(self):
        if self.notifier:
            try:
                await self.notifier.deliver()
            except Exception as exc:
                log.warning("notification_flush_failed error_type=%s", type(exc).__name__)

    async def poll_changed(self, poll_id: str | None):
        if poll_id and self.on_poll_change:
            try:
                await self.on_poll_change(poll_id)
            except Exception as exc:
                log.warning("poll_card_refresh_failed error_type=%s", type(exc).__name__)

    async def group_changed(self, group_event_id: str | None):
        if group_event_id and self.on_group_change:
            try:
                await self.on_group_change(group_event_id)
            except Exception as exc:
                log.warning("group_card_refresh_failed error_type=%s", type(exc).__name__)

    @staticmethod
    def availability_snapshot(availability) -> dict:
        return {
            "covered": availability.covered,
            "conflicts": sorted(
                {
                    f"{block.title}|{block.start_at.isoformat()}|{block.end_at.isoformat()}"
                    for block in availability.conflicts
                }
            ),
        }

    async def availability(self, user: User, event: EventDraft, exclude: str | None = None):
        blocks, events = await self.repo.busy(user.id, event)
        result = check_availability(
            event,
            blocks,
            events,
            exclude_event_id=exclude,
            coverage_start=user.coverage_start,
            coverage_end=user.coverage_end,
            busy_day_threshold=self.settings.busy_day_threshold,
        )
        synced = [c.synced_at for c in await self.repo.list_calendars(user.id) if c.synced_at]
        if synced:
            seen = window_covers(min(synced), event.start_at, event.end_at)
            result = replace(
                result, personal=seen, nusmods=result.covered, covered=result.covered or seen
            )
        return result

    async def safe_action(self, user_id, kind, event_id=None, metadata=None):
        # Analytics errors should not break a successful commitment.
        try:
            await self.repo.action(user_id, kind, event_id, metadata)
        except Exception as exc:
            log.warning("analytics_failed error_type=%s action=%s", type(exc).__name__, kind)

    async def command(self, telegram_id: int, command: str, args: str = "") -> list[Reply]:
        user = await self.repo.ensure_user(telegram_id)
        if command == "help":
            return [Reply(HELP)]
        if command == "start":
            await self.safe_action(user.id, "start")
            if user.dm_blocked:
                # They are talking to the bot again, so DMs can be delivered.
                await self.repo.set_user_flags(user.id, dm_blocked=False)
            if args.startswith(("edit_", "cancel_")):
                return await self.groups.begin_change(user, args)
            if args.startswith("manage_"):
                return await self.groups.manage_panel(user, args)
            connected = []
            if user.onboarding_status == "READY":
                connected.append(f"NUSMods ({user.academic_year}, semester {user.semester})")
            calendars = await self.repo.list_calendars(user.id)
            if calendars:
                names = ", ".join(c.label for c in calendars)
                connected.append(f"your calendar{'s' if len(calendars) > 1 else ''} ({names})")
            if connected:
                return [
                    Reply(
                        "You're all set ✅ Forward me a plan, or pick one below."
                        "\nChecking: " + " and ".join(connected) + ".",
                        menu_keyboard(),
                    )
                ]
            return [Reply(onboarding.WELCOME, onboarding.welcome_keyboard(), parse_mode="HTML")]
        if command == "cancel":
            await self.repo.set_pending(user.id, None)
            return [Reply("Pending action cleared. Forward an event when you're ready.")]
        if command in ("delete", "deletemydata"):
            nonce = secrets.token_hex(8)
            await self.repo.set_pending(user.id, {"stage": "delete", "nonce": nonce})
            return [
                Reply(
                    "Delete all your AmIFree timetable, plans, busy blocks, reminders, pending messages and analytics? This cannot be undone.",
                    confirm_keyboard(nonce, "Delete my data"),
                )
            ]
        if command == "events":
            return await self.list_events(user)
        if command == "plans":
            return await self.agenda(user)
        if command in (
            "event",
            "plan",
            "whosfree",
            "stats",
            "export",
            "detect",
            "attendance",
            "pro",
        ):
            return [Reply(f"/{command} works in group chats. Add me to a group and use it there.")]
        if command == "remind":
            if not args.strip():
                return [
                    Reply("What should I remind you about, and when? " + quick_reminders.EXAMPLE)
                ]
            return await self.add_reminder(user, args, self.clock())
        if command == "settings":
            return [self.settings_panel(user)]
        if command == "reminders":
            choice = args.lower()
            if choice in ("on", "off"):
                await self.repo.set_user_flags(user.id, reminders_enabled=choice == "on")
                return [
                    Reply(
                        "Night-before summaries and nudges are on."
                        if choice == "on"
                        else "Night-before summaries and nudges are off. Change alerts for plans you saved still arrive."
                    )
                ]
            state = "on" if user.reminders_enabled else "off"
            return [Reply(f"Reminders are {state}. Send /reminders on or /reminders off.")]
        if command == "block":
            if not args:
                await self.repo.set_pending(user.id, {"stage": "block_time", "text": ""})
                return [Reply("When are you busy? " + USAGE + "\n/cancel to stop.")]
            try:
                rule = parse_block(args, self.clock(), explicit=True)
            except Clarification as exc:
                return await self.ask_block_time(user, args, exc)
            return await self.preview_block(user, rule)
        if command == "usual":
            return await self.usual_command(user, args)
        if command == "refund":
            return await self.pro.refund(user, telegram_id)
        if command == "deadline" and not args.strip():
            return await self.list_deadlines(user)
        if command == "deadline":
            # The next message (or the rest of this command) is a deadline, whatever the AI says.
            await self.repo.set_pending(
                user.id,
                {
                    "stage": "clarify",
                    "deadline": True,
                    "source": "",
                    "reference": self.clock().astimezone(SG).isoformat(),
                    "use_next_message_reference": True,
                    "message_id": None,
                    "extraction": None,
                },
            )
            return await self.message(telegram_id, 0, args, self.clock())
        if command == "deadlines":
            return await self.list_deadlines(user)
        if command == "calendar":
            return await self.calendar_setup(user)
        if command == "free":
            return await self.free_command(user, args)
        if command == "blocks":
            rules = await self.repo.list_blocks(user.id)
            if not rules:
                return [Reply("No busy blocks yet. " + USAGE)]
            return [Reply(describe(rule), remove_block_keyboard(rule.id)) for rule in rules[:20]]
        return [Reply("Use /help to see the available commands.")]

    async def add_reminder(self, user: User, text: str, reference: datetime) -> list[Reply]:
        try:
            draft = quick_reminders.parse_reminder(text, reference)
        except Clarification as exc:
            return [Reply(str(exc))]
        reminder = await self.repo.create_reminder(user.id, draft.text, draft.remind_at)
        await self.safe_action(user.id, "reminder_created")
        when = quick_reminders.when_text(reminder.remind_at, reference)
        return [
            Reply(
                f"⏰ Got it. I'll remind you {when}: {reminder.text}",
                reminder_keyboard(reminder.id),
            )
        ]

    async def reminder_action(self, user: User, action: str, reminder_id: str) -> list[Reply]:
        try:
            UUID(reminder_id)
        except ValueError:
            return [Reply("That button isn't valid. Use /plans.")]
        reminder = await self.repo.get_reminder(user.id, reminder_id)
        if not reminder:
            return [Reply("That reminder is already gone. /plans lists the rest.", edit=True)]
        if action == "del":
            await self.repo.delete_reminder(user.id, reminder_id)
            token = self.remember_removed(
                user, "reminder", {"text": reminder.text, "remind_at": reminder.remind_at}
            )
            return [Reply(f"🗑 Removed: {reminder.text}", undo_keyboard(token), edit=True)]
        if action == "done":
            await self.repo.finish_reminder(user.id, reminder_id)
            return [Reply(f"✅ Done: {reminder.text}", edit=True)]
        now = self.clock()
        target = quick_reminders.snooze_target(action, now)
        await self.repo.snooze_reminder(user.id, reminder_id, target)
        when = quick_reminders.when_text(target, now)
        return [
            Reply(
                f"😴 Snoozed until {when}: {reminder.text}",
                reminder_keyboard(reminder_id),
                edit=True,
            )
        ]

    def remember_removed(self, user: User, kind: str, fields: dict) -> str:
        now = self.clock()
        self.removed = {k: v for k, v in self.removed.items() if v[0] > now}
        if len(self.removed) >= 500:
            self.removed.pop(next(iter(self.removed)))
        token = secrets.token_hex(4)
        self.removed[token] = (now + UNDO_WINDOW, user.id, kind, fields)
        return token

    async def undo_removal(self, user: User, token: str) -> list[Reply]:
        found = self.removed.pop(token, None)
        if not found or found[0] <= self.clock() or found[1] != user.id:
            return [Reply("It's too late to undo that. You can add it again.", edit=True)]
        _, _, kind, fields = found
        if kind == "deadline":
            deadline = await self.repo.create_deadline(user.id, None, DeadlineDraft(**fields))
            text = card_text(deadline, self.clock(), user.reminders_enabled)
            return [
                Reply(
                    "↩️ Restored.\n" + text.rsplit("\n\n", 1)[0],
                    deadline_keyboard(deadline.id),
                    edit=True,
                )
            ]
        if kind == "reminder":
            reminder = await self.repo.create_reminder(user.id, fields["text"], fields["remind_at"])
            when = quick_reminders.when_text(reminder.remind_at, self.clock())
            return [
                Reply(
                    f"↩️ Restored. I'll remind you {when}: {reminder.text}",
                    reminder_keyboard(reminder.id),
                    edit=True,
                )
            ]
        if kind == "plan_edit":
            event = await self.repo.get_event(user.id, fields["event_id"])
            if not event or event.status == Status.CANCELLED:
                return [Reply("That plan is gone, so there's nothing to undo.", edit=True)]
            keep = {
                k: getattr(event, k) for k in ("title", "start_at", "end_at", "location", "notes")
            }
            draft = EventDraft(**(keep | {fields["key"]: fields["value"]}))
            result = await self.repo.update_event(user.id, event, draft)
            if not result:
                return [Reply("That plan changed since, so I left it as it is.", edit=True)]
            card = event_card(
                result, await self.availability(user, result, result.id), synced(user)
            )
            return [Reply("↩️ Changed back.\n\n" + card.text, card.keyboard, edit=True)]
        rule = await self.repo.create_block(user.id, BlockRule.model_validate(fields))
        return [Reply("↩️ Restored.\n" + describe(rule), remove_block_keyboard(rule.id), edit=True)]

    def settings_panel(self, user: User) -> Reply:
        on = user.reminders_enabled
        text = (
            "⚙️ Settings\n\n"
            f"Reminders: {'on' if on else 'off'}\n"
            "The night-before summary, deadline reminders and nudges about plans you "
            "haven't answered. Change alerts for plans you've added, and reminders you set with /remind, always arrive."
        )
        return Reply(text, settings_keyboard(on))

    async def usual_command(self, user: User, args: str) -> list[Reply]:
        """/usual tue evenings, sat all day: standing free times that pre-fill poll grids."""
        text = args.strip()
        if text.lower() in ("clear", "off", "none", "reset"):
            await self.repo.replace_usual(user.id, [])
            return [Reply("Cleared your usual times. Poll grids start from your schedule again.")]
        if not text:
            current = await self.repo.get_usual(user.id)
            if current:
                return [
                    Reply(
                        "Your usual free times: "
                        + describe_usual(current)
                        + "\n\nChange them with /usual tue evenings, sat all day. /usual clear removes them."
                    )
                ]
            return [Reply(USUAL_HELP)]
        try:
            windows = parse_usual(text)
        except Clarification as exc:
            return [Reply(f"{exc}\n\n{USUAL_HELP}")]
        await self.repo.replace_usual(user.id, windows)
        return [
            Reply(
                "Saved your usual free times: "
                + describe_usual(windows)
                + "\n\nIn group polls you'll be counted as free only in these times, unless you"
                " change a poll's grid before submitting. /usual clear removes them."
            )
        ]

    async def offer_recurring(
        self, user: User, message_id: int, extracted, source: str, reference: datetime, weekday: int
    ) -> list[Reply] | None:
        """A weekly event ("Saturdays, 9:30-11:30am"): ask whether it's one date or every week."""
        day_name = WEEKDAYS[weekday].title()
        try:
            draft = validate_extraction(
                extracted,
                source,
                reference,
                default_minutes=self.settings.default_event_duration_minutes,
            )
        except Clarification:
            return None  # e.g. no end time: the normal questions handle it
        nonce = secrets.token_hex(8)
        await self.repo.set_pending(
            user.id,
            {
                "stage": "recurring",
                "nonce": nonce,
                "message_id": message_id,
                "weekday": weekday,
                "draft": draft.model_dump(mode="json"),
            },
        )
        when = f"{day_name}s, {time_range(draft.start_at, draft.end_at)}"
        return [
            Reply(
                f"🔁 {draft.title}\nThis happens every week: {when}.\n\nWhat should I add?",
                recurring_keyboard(nonce, f"{draft.start_at:%a %-d %b}", day_name),
            )
        ]

    async def choose_recurring(self, user: User, choice: str, nonce: str) -> list[Reply]:
        pending = user.pending
        if not pending or pending.get("stage") != "recurring" or pending.get("nonce") != nonce:
            return [Reply("That choice expired. Forward the post again.")]
        draft = EventDraft.model_validate(pending["draft"])
        await self.repo.set_pending(user.id, None)
        if choice == "once":
            event = await self.repo.create_preview(user.id, pending["message_id"], draft)
            card = event_card(event, await self.availability(user, event, event.id))
            return [Reply(card.text, card.keyboard, edit=True)]
        rule = BlockRule(
            title=draft.title,
            kind="WEEKLY",
            weekday=pending["weekday"],
            start_time=draft.start_at.timetz().replace(tzinfo=None),
            end_time=draft.end_at.timetz().replace(tzinfo=None),
            start_date=draft.start_at.date(),
            until_date=draft.start_at.date() + timedelta(days=HORIZON_DAYS),
        )
        replies = await self.preview_block(user, rule, typed=False)
        return [Reply(r.text, r.keyboard, edit=True) for r in replies]

    async def preview_block(self, user: User, rule: BlockRule, typed: bool = True) -> list[Reply]:
        if len(await self.repo.list_blocks(user.id)) >= MAX_RULES:
            return [Reply("You've reached the busy block limit. Remove one with /blocks first.")]
        nonce = secrets.token_hex(8)
        await self.repo.set_pending(
            user.id, {"stage": "block", "rule": rule.model_dump(mode="json"), "nonce": nonce}
        )
        note = (
            "\nAdd 'until <date>' to change when it stops repeating."
            if rule.kind == "WEEKLY" and typed
            else ""
        )
        return [
            Reply(
                "Block this time?\n\n"
                + describe(rule)
                + note
                + await self.block_clashes(user, rule),
                confirm_keyboard(nonce, "Save block"),
            )
        ]

    async def ask_block_time(self, user: User, text: str, exc: Clarification) -> list[Reply]:
        """'busy 14 oct 18:30' has no end: keep the day and wait for the time."""
        if not str(exc).startswith("What time is this block"):
            return [Reply(str(exc))]
        await self.repo.set_pending(user.id, {"stage": "block_time", "text": text})
        return [Reply(str(exc) + "\n/cancel to stop.")]

    async def answer_block_time(
        self, user: User, text: str, reference: datetime
    ) -> list[Reply] | None:
        """A reply to "What time is this block?". None when it isn't a time, so the message is
        read as an ordinary one (a plan sent meanwhile must not become part of the block)."""
        original = user.pending.get("text", "")
        reply = text.strip()
        lone = BLOCK_TIME.search(original)
        if re.match(r"\s*busy\b", reply, re.I) or not original.strip():
            combined = reply  # a complete new block, or the answer to a bare /block
        elif BLOCK_RANGE.fullmatch(reply):
            # Drop the lone time from 'busy 14 oct 18:30'; the reply holds the full range.
            combined = (BLOCK_TIME.sub(" ", original) + " " + reply).strip()
        elif lone and BLOCK_TIME.fullmatch(reply):
            # 'busy 14 oct 18:30' then '19:30': the reply is the end of the same block.
            head = BLOCK_TIME.sub(" ", original).strip()
            combined = f"{head} {lone[0].strip()}-{reply}"
        else:
            return None
        try:
            rule = parse_block(combined, reference, explicit=True)
        except Clarification as exc:
            return [Reply(str(exc) + "\n/cancel to stop.")]
        return await self.preview_block(user, rule)

    async def block_clashes(self, user: User, rule: BlockRule) -> str:
        """Going and Saved plans this block overlaps, so the clash shows now, not later."""
        now = self.clock()
        if rule.kind == "ONCE":
            occurrences = [(rule.start_at, rule.end_at)]
        else:
            horizon = datetime.combine(rule.until_date, day_time(23, 59), SG) + timedelta(days=1)
            occurrences = [(b.start_at, b.end_at) for b in expand_blocks([rule], now, horizon)]
        occurrences = [(a, b) for a, b in occurrences if b > now]
        if not occurrences:
            return ""
        plans = await self.repo.events_between(
            occurrences[0][0] - timedelta(days=1),
            occurrences[-1][1],
            [Status.GOING, Status.SAVED],
            user.id,
        )
        hits = [
            e
            for e in sorted(plans, key=lambda e: e.start_at)
            if any(e.start_at < b and e.end_at > a for a, b in occurrences)
        ]
        if not hits:
            return ""
        lines = [
            f"• {e.title[:60]} ({e.status.value.title()}, {e.start_at.astimezone(SG):%a %-d %b} "
            f"{time_range(e.start_at, e.end_at)})"
            for e in hits[:5]
        ]
        more = f"\n…and {len(hits) - 5} more" if len(hits) > 5 else ""
        return "\n\n⚠️ This clashes with your plans:\n" + "\n".join(lines) + more

    async def agenda(self, user: User) -> list[Reply]:
        """One message: plans, deadlines and busy time, grouped by day with a button each."""
        now = self.clock()
        today = now.astimezone(SG).date()
        items: list[tuple[datetime, str, str, str]] = []
        for e in await self.repo.recent_events(user.id):
            if e.end_at >= now and e.status in (Status.GOING, Status.SAVED, Status.MAYBE):
                venue = f", {e.location[:40]}" if e.location else ""
                items.append(
                    (
                        e.start_at,
                        "pc",
                        e.id,
                        f"{time_range(e.start_at, e.end_at)}  {e.title[:60]}{venue}",
                    )
                )
        for d in await self.repo.deadlines_between(now, now + timedelta(days=120), user.id):
            items.append((d.due_at, "dc", d.id, f"due {clock(d.due_at)}  {d.title[:60]}"))
        for r in await self.repo.reminders_between(now, now + timedelta(days=60), user.id):
            items.append((r.remind_at, "rc", r.id, f"{clock(r.remind_at)}  ⏰ {r.text[:60]}"))
        for rule in await self.repo.list_blocks(user.id):
            occ = expand_blocks([rule], now, now + timedelta(days=60))
            upcoming = [b for b in occ if b.end_at > now]
            if upcoming:
                b = min(upcoming, key=lambda b: b.start_at)
                weekly = " (weekly)" if rule.kind == "WEEKLY" else ""
                items.append(
                    (
                        b.start_at,
                        "bc",
                        rule.id,
                        f"{time_range(b.start_at, b.end_at)}  {rule.title[:50]} · busy{weekly}",
                    )
                )
        if not items:
            return [Reply("Nothing coming up. Forward a plan, or add a deadline with /deadline.")]
        items.sort(key=lambda it: it[0])
        shown, extra = items[:24], max(0, len(items) - 24)

        def bucket(when: datetime) -> tuple[int, str]:
            day = when.astimezone(SG).date()
            if day <= today:
                return 0, "Today"
            if day == today + timedelta(days=1):
                return 1, "Tomorrow"
            if day <= today + timedelta(days=7):
                return 2, "This week"
            return 3, "Later"

        lines: list[str] = []
        buttons: list = []
        last = None
        for n, (when, kind, item_id, text) in enumerate(shown, 1):
            rank, label = bucket(when)
            if rank != last:
                lines.append(("" if last is None else "\n") + label)
                last = rank
            day_prefix = f"{when:%a %-d %b} " if rank >= 2 else ""
            lines.append(f"{n}. {day_prefix}{text}")
            buttons.append((str(n), kind, item_id))
        footer = f"\n\n+ {extra} more" if extra else ""
        return [
            Reply(
                "Coming up — tap a number to open it.\n\n" + "\n".join(lines) + footer,
                agenda_keyboard(buttons),
            )
        ]

    async def open_item(self, user: User, kind: str, item_id: str) -> list[Reply]:
        """Open one agenda item as its own card, so it can be edited or removed."""
        try:
            UUID(item_id)
        except ValueError:
            return [Reply("That button isn't valid. Use /plans.")]
        if kind == "pc":
            event = await self.repo.get_event(user.id, item_id)
            if not event or event.status in (Status.PREVIEW, Status.DECLINED, Status.CANCELLED):
                return [Reply("That plan isn't in your plans any more. Use /plans.")]
            return [event_card(event, await self.availability(user, event, event.id), synced(user))]
        if kind == "dc":
            deadline = await self.repo.get_deadline(user.id, item_id)
            if not deadline:
                return [Reply("That deadline is gone. Use /plans.")]
            return [
                Reply(
                    card_text(deadline, self.clock(), user.reminders_enabled),
                    deadline_keyboard(deadline.id),
                )
            ]
        if kind == "rc":
            reminder = await self.repo.get_reminder(user.id, item_id)
            if not reminder or reminder.done:
                return [Reply("That reminder is gone. Use /plans.")]
            when = quick_reminders.when_text(reminder.remind_at, self.clock())
            return [Reply(f"⏰ {reminder.text}\nRings {when}.", reminder_keyboard(reminder.id))]
        rule = next((r for r in await self.repo.list_blocks(user.id) if r.id == item_id), None)
        if not rule:
            return [Reply("That busy block is gone. Use /plans.")]
        return [Reply(describe(rule), remove_block_keyboard(rule.id))]

    async def list_events(self, user: User, page: int = 0, plans: bool = False) -> list[Reply]:
        events = await self.repo.recent_events(user.id)
        if plans:
            # Plans are what the user chose to keep; previews and declines stay in /events.
            now = self.clock()
            events = [
                e
                for e in events
                if e.end_at >= now and e.status in (Status.SAVED, Status.GOING, Status.MAYBE)
            ]
            if not events:
                return [Reply("No upcoming plans yet. Forward a plan and tap Save.")]
        if not events:
            return [
                Reply("No upcoming or recent events yet. Forward an invitation to get started.")
            ]
        pages = (len(events) + 9) // 10
        page = min(page, pages - 1)
        replies = [
            event_card(e, await self.availability(user, e, e.id), synced(user))
            for e in events[page * 10 : (page + 1) * 10]
        ]
        if pages > 1:
            label, prefix = ("Plans", "plans") if plans else ("Events", "events")
            replies.append(
                Reply(f"{label}: page {page + 1} of {pages}", page_keyboard(page, pages, prefix))
            )
        return replies

    async def read_picture(self, telegram_id: int, data: bytes, mime: str) -> str | None:
        """The event text in a picture. None when pictures can't be read right now.

        Raises PictureError for a picture that isn't usable or when sent too fast: the vision
        call is the most expensive one, so it has its own per-person limit."""
        if self.image_reader is None:
            return None
        if not self.picture_limit.allow(telegram_id):
            raise PictureError(SLOW_DOWN)
        return await self.image_reader.read(data, mime)

    async def message(
        self, telegram_id: int, message_id: int, text: str, reference: datetime
    ) -> list[Reply]:
        user = await self.repo.ensure_user(telegram_id)
        if len(text) > 8000:
            return [
                Reply(
                    "That message is too long. Send the event title, date, time and venue in a shorter message."
                )
            ]
        try:
            calendar_link = find_link(text, self.settings.app_base_url)
        except CalendarLinkError as exc:
            return [Reply(str(exc))]
        if calendar_link:
            return await self.connect_calendar(user, calendar_link)
        if "nusmods.com" in text.lower() and text.strip().startswith(("http://", "https://")):
            if not self.calendar_limit.allow(user.id):
                return [Reply(SLOW_DOWN)]
            result = await self.importer.import_link(text.strip(), reference)
            await self.repo.import_timetable(user.id, result)
            return [Reply(onboarding.nusmods_added(result.academic_year, result.semester))]
        if CALENDAR_URL.fullmatch(text.strip()):
            # A bare link that is neither NUSMods nor a calendar: say so, don't ask the LLM.
            return [Reply(onboarding.NOT_A_SCHEDULE_LINK)]
        if quick_reminders.is_reminder(text):
            return await self.add_reminder(user, text, reference)
        pending = user.pending
        if pending and pending["stage"] == "free":
            # /free asked "when?": a day or time answers it; anything else is a new message.
            try:
                query = parse_free(text, reference) if len(text) <= 40 else None
            except Clarification:
                query = None
            if query is not None:
                await self.repo.set_pending(user.id, None)
                if query.slot is not None:
                    return [await self.free_slot(user, query)]
                return [await self.free_days(user, query.days)]
            await self.repo.set_pending(user.id, None)
            user.pending = pending = None
        if pending and pending["stage"] == "block_time":
            answer = await self.answer_block_time(user, text, reference)
            if answer is not None:
                return answer
            # Not a time: the question is closed and this is an ordinary message.
            await self.repo.set_pending(user.id, None)
            user.pending = pending = None
        if pending and pending["stage"] == "edit_field":
            return await self.edit_field(user, pending, text)
        if pending and pending["stage"] in ("confirm", "choose", "delete", "block", "recurring"):
            return [
                Reply(
                    "Please finish the pending action using its buttons, or send /cancel before forwarding another event."
                )
            ]
        if not pending:
            # Quick blocks are parsed deterministically and never reach the extractor.
            try:
                rule = parse_block(text, reference)
            except Clarification as exc:
                return await self.ask_block_time(user, text, exc)
            if rule:
                return await self.preview_block(user, rule)
        started = time.monotonic()
        previous = pending.get("extraction") if pending else None
        if not self.extraction_limit.allow(telegram_id):
            return [Reply(SLOW_DOWN)]
        if pending:
            if not pending.get("use_next_message_reference"):
                reference = datetime.fromisoformat(pending["reference"])
            message_id = pending["message_id"]
            if previous:
                text = infer_meridiem(
                    Extraction.model_validate(previous), pending.get("asked"), text
                )
            source = (pending.get("source", "") + "\n" + text).strip()
            if len(source) > 8000:
                return [
                    Reply(
                        "The clarification is too long. Use /cancel and send a concise complete event."
                    )
                ]
            await self.safe_action(user.id, "correction", metadata={"message_id": message_id})
        else:
            source = text
            await self.safe_action(user.id, "forward", metadata={"message_id": message_id})
        extracted = None
        if pending and pending.get("asked") and previous:
            # A short answer to our own question ("8PM", "2000") needs no AI, which could
            # rewrite it into text that isn't in the message and ask the same question again.
            extracted = fill_answer(
                Extraction.model_validate(previous), pending["asked"], text, reference
            )
        if extracted is None:
            extracted = await self.extractor.extract(text, reference, previous)
        if not (pending and pending.get("event_id")):  # edits of saved events keep their kind
            extracted = settle_kind(extracted, clarifying=bool(pending), text=source)
        if pending and not pending.get("event_id"):
            # A clarification keeps what the draft was: a deadline stays a deadline.
            kind = "deadline" if pending.get("deadline") else extracted.kind
            if kind == "deadline" and not pending.get("deadline") and previous:
                kind = "new"
            extracted = extracted.model_copy(update={"kind": kind})
        if pending:
            extracted = settle_ambiguity(extracted, text)
        elif extracted.kind == "new":
            try:
                single_event_guard(text)
            except Clarification as exc:
                return [Reply(str(exc))]
        if self.settings.retain_extractions:
            try:
                await self.repo.extraction(
                    user.id, source, extracted.model_dump(mode="json"), "parsed"
                )
            except Exception as exc:
                log.warning("extraction_audit_failed error_type=%s", type(exc).__name__)
        state = {
            "stage": "clarify",
            "source": source,
            "reference": reference.isoformat(),
            "message_id": message_id,
            "extraction": extracted.model_dump(mode="json"),
        }
        if extracted.kind == "deadline" and not (pending and pending.get("event_id")):
            return await self.save_deadline(user, extracted, state, reference)
        if pending and pending.get("event_id"):
            # "clb lvl 3" alone, with nothing else changed, is the new venue.
            extracted = bare_venue(extracted, previous, text)
            state["extraction"] = extracted.model_dump(mode="json")
            state.update(
                {
                    "event_id": pending["event_id"],
                    "base_version": pending["base_version"],
                    "group": pending.get("group", False),
                }
            )
            existing = await self.load_target(user, state)
            if not existing or existing.version != state["base_version"]:
                await self.repo.set_pending(user.id, None)
                return [
                    Reply(
                        "That event changed while you were editing. Use /events to open its latest card."
                    )
                ]
            return await self.prepare(user, extracted, state, existing)
        if extracted.kind in ("update", "cancel"):
            # Shared group plans are changed once by their organiser, never per member.
            candidates = match_updates(
                extracted,
                [e for e in await self.repo.recent_events(user.id) if not e.group_event_id],
            )
            if not candidates:
                return [
                    Reply(
                        "I couldn't find a recent matching event. Use /events to find its card and tap Edit, or forward the complete invitation."
                    )
                ]
            if len(candidates) != 1 or not (extracted.match_title or extracted.title):
                state.update({"stage": "choose", "candidates": [e.id for e in candidates]})
                await self.repo.set_pending(user.id, state)
                return [
                    Reply("Which event should this change apply to?", choose_keyboard(candidates))
                ]
            return await self.prepare(user, extracted, state, candidates[0])
        if extracted.kind == "unknown":
            return [
                Reply(
                    "I couldn't find an event in that message. Send an invitation with a title, date and start/end times."
                )
            ]
        weekly = None if pending else weekly_weekday(text)
        if weekly is not None and extracted.kind == "new":
            # "Saturdays" isn't a date: use the coming Saturday, so any follow-up question is
            # about what's really missing (usually the end time), not "what date?".
            day_name = WEEKDAYS[weekly].title()
            extracted = extracted.model_copy(update={"date_text": day_name, "ambiguity": None})
            source = f"{source}\n{day_name}"
            state.update(source=source, extraction=extracted.model_dump(mode="json"))
            if extracted.start_time_text:
                offer = await self.offer_recurring(
                    user, message_id, extracted, source, reference, weekly
                )
                if offer:
                    return offer
        try:
            draft = validate_extraction(
                extracted,
                source,
                reference,
                default_minutes=self.settings.default_event_duration_minutes,
            )
        except Clarification as exc:
            state["asked"] = asked_field(str(exc))
            await self.repo.set_pending(user.id, state)
            return [Reply(str(exc) + "\n/cancel to discard this draft.")]
        event = await self.repo.create_preview(user.id, message_id, draft)
        await self.repo.set_pending(user.id, None)
        availability = await self.availability(user, event, event.id)
        await self.safe_action(
            user.id,
            "verdict",
            event.id,
            {
                "verdict": availability.verdict,
                "latency_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return [event_card(event, availability)]

    # ---- deadlines ----

    async def save_deadline(
        self, user: User, extracted: Extraction, state: dict, reference: datetime
    ) -> list[Reply]:
        state["deadline"] = True
        try:
            draft = validate_deadline(extracted, state["source"], reference)
        except Clarification as exc:
            state["asked"] = asked_field(str(exc))
            await self.repo.set_pending(user.id, state)
            return [Reply(str(exc) + "\n/cancel to discard this deadline.")]
        message_id = state.get("message_id") or None
        deadline = await self.repo.create_deadline(user.id, message_id, draft)
        await self.repo.set_pending(user.id, None)
        await self.safe_action(user.id, "deadline")
        return [
            Reply(
                card_text(deadline, self.clock(), user.reminders_enabled),
                deadline_keyboard(deadline.id),
            )
        ]

    async def list_deadlines(self, user: User) -> list[Reply]:
        """Overdue first (they stay until Done or Remove), then upcoming, each with days left."""
        now = self.clock()
        found = await self.repo.deadlines_between(
            now - timedelta(days=365), now + timedelta(days=400), user.id
        )
        if not found:
            return [
                Reply(
                    "No upcoming deadlines. Forward a message with a due date, or send "
                    f"/deadline {DEADLINE_EXAMPLE.removeprefix('For example: ')}"
                )
            ]
        overdue = [d for d in found if d.due_at < now]
        upcoming = [d for d in found if d.due_at >= now]
        replies: list[Reply] = []
        for heading, group in (("⚠️ Overdue", overdue), ("Coming up", upcoming)):
            if not group:
                continue
            if overdue:
                replies.append(Reply(heading))
            replies += [
                Reply(
                    card_text(d, now, user.reminders_enabled).rsplit("\n\n", 1)[0],
                    deadline_keyboard(d.id),
                )
                for d in group[:20]
            ]
            if len(group) > 20:
                replies.append(Reply(f"…and {len(group) - 20} more."))
        return replies

    async def deadline_action(self, user: User, action: str, deadline_id: str) -> list[Reply]:
        try:
            UUID(deadline_id)
        except ValueError:
            return [Reply("That button isn't valid. Use /deadlines.")]
        deadline = await self.repo.get_deadline(user.id, deadline_id)
        if not deadline:
            return [Reply("That deadline is already gone. /deadlines lists the rest.", edit=True)]
        if action == "rm":
            await self.repo.delete_deadline(user.id, deadline_id)
            token = self.remember_removed(
                user,
                "deadline",
                {"title": deadline.title, "due_at": deadline.due_at, "notes": deadline.notes},
            )
            return [Reply(f"🗑 Removed: {deadline.title}", undo_keyboard(token), edit=True)]
        await self.repo.finish_deadline(user.id, deadline_id)
        await self.safe_action(user.id, "deadline_done")
        return [Reply(f"✅ Done: {deadline.title}. No more reminders for it.", edit=True)]

    async def load_target(self, user: User, pending: dict) -> Event | GroupEvent | None:
        """The plan a pending edit applies to: the user's own event, or a card they organise."""
        if pending.get("group"):
            event = await self.repo.get_group_event(pending["event_id"])
            if event and event.creator_user_id == user.id and event.status == "ACTIVE":
                return event
            return None
        return await self.repo.get_event(user.id, pending["event_id"])

    async def own_id(self, user: User, target: Event | GroupEvent) -> str | None:
        """The user's own row for this plan, excluded from their clash check."""
        if isinstance(target, GroupEvent):
            linked = await self.repo.linked_event(user.id, target.id)
            return linked.id if linked else None
        return target.id

    async def begin_change(
        self, user: User, event: Event | GroupEvent, cancel: bool = False
    ) -> list[Reply]:
        group = isinstance(event, GroupEvent)
        state = {"event_id": event.id, "base_version": event.version, "group": group}
        if cancel:
            state.update({"stage": "confirm", "cancel": True, "nonce": secrets.token_hex(8)})
            await self.repo.set_pending(user.id, state)
            return [
                Reply(
                    "Cancel this event for everyone?\n\n" + details(event),
                    confirm_keyboard(state["nonce"], "Confirm cancellation"),
                )
            ]
        # A complete baseline allows the extractor to retain unchanged fields.
        base = Extraction(
            kind="new",
            title=event.title,
            date_text=event.start_at.date().isoformat(),
            start_time_text=event.start_at.strftime("%H:%M"),
            end_time_text=event.end_at.strftime("%H:%M"),
            end_date_text=event.end_at.date().isoformat(),
            location=event.location,
            notes=event.notes,
            match_title=event.title,
            ambiguity=None,
        )
        state.update(
            {
                "stage": "clarify",
                "source": details(event) + "\n" + base.model_dump_json(),
                "reference": self.clock().astimezone(SG).isoformat(),
                "use_next_message_reference": True,
                "message_id": event.source_message_id,
                "extraction": base.model_dump(mode="json"),
            }
        )
        state["nonce"] = secrets.token_hex(8)
        await self.repo.set_pending(user.id, state)
        scope = "\nEveryone who saved it will be told what changed." if group else ""
        return [
            Reply(
                "What do you want to change?\n\n"
                + details(event)
                + "\n\nTap one, or type the change (like: move to 8pm). I'll show a preview first."
                + scope,
                change_keyboard(state["nonce"]),
            )
        ]

    async def choose_field(self, user: User, field: str, nonce: str) -> list[Reply]:
        pending = user.pending
        if not pending or pending.get("nonce") != nonce or not pending.get("event_id"):
            return [Reply("That edit expired. Use /plans and tap Edit again.")]
        pending = {k: v for k, v in pending.items() if k not in ("field", "asked")}
        if field == "time":
            pending["stage"] = "clarify"
            await self.repo.set_pending(user.id, pending)
            return [
                Reply(
                    "Send the new date or time, like: sat 7-9pm, or 20:00-22:00.\n/cancel to keep it unchanged."
                )
            ]
        pending.update(stage="edit_field", field=field)
        await self.repo.set_pending(user.id, pending)
        word = "venue" if field == "venue" else "title"
        return [Reply(f"Send the new {word}.\n/cancel to keep it unchanged.")]

    async def edit_field(self, user: User, pending: dict, text: str) -> list[Reply]:
        """A typed venue or title is used exactly as written."""
        value = " ".join(text.split())
        if not value or len(value) > 200:
            return [Reply("Send it in one line (up to 200 characters), or /cancel.")]
        existing = await self.load_target(user, pending)
        if not existing or existing.version != pending["base_version"]:
            await self.repo.set_pending(user.id, None)
            return [
                Reply(
                    "That event changed while you were editing. Use /plans to open its latest card."
                )
            ]
        key = "location" if pending["field"] == "venue" else "title"
        if not pending.get("group") and isinstance(existing, Event):
            return await self.apply_text_edit(user, existing, key, value)
        extraction = Extraction.model_validate(pending["extraction"]).model_copy(
            update={key: value, "ambiguity": None}
        )
        state = {k: v for k, v in pending.items() if k != "field"}
        state.update(
            stage="clarify",
            source=pending["source"] + "\n" + value,
            extraction=extraction.model_dump(mode="json"),
        )
        return await self.prepare(user, extraction, state, existing)

    async def apply_text_edit(self, user: User, event: Event, key: str, value: str) -> list[Reply]:
        """A new venue or title cannot cause a clash, so it is applied at once, with an Undo."""
        fields = {
            k: getattr(event, k) for k in ("title", "start_at", "end_at", "location", "notes")
        }
        before = fields[key]
        draft = EventDraft(**(fields | {key: value}), assumed_duration=event.assumed_duration)
        result = await self.repo.update_event(user.id, event, draft)
        await self.repo.set_pending(user.id, None)
        if not result:
            return [Reply("That event changed. Use /plans to open its latest card.")]
        token = self.remember_removed(
            user, "plan_edit", {"event_id": result.id, "key": key, "value": before}
        )
        word = "venue" if key == "location" else "title"
        card = event_card(result, await self.availability(user, result, result.id), synced(user))
        return [Reply(f"✏️ Updated the {word}.\n\n{card.text}", with_undo(card.keyboard, token))]

    async def prepare(
        self, user: User, extraction: Extraction, state: dict, existing: Event | GroupEvent
    ) -> list[Reply]:
        state.update({"event_id": existing.id, "base_version": existing.version})
        if extraction.kind == "cancel":
            state.update({"stage": "confirm", "cancel": True, "nonce": secrets.token_hex(8)})
            await self.repo.set_pending(user.id, state)
            return [
                Reply(
                    "Cancel this event?\n\n" + details(existing),
                    confirm_keyboard(state["nonce"], "Confirm cancellation"),
                )
            ]
        try:
            draft = validate_extraction(
                extraction,
                state["source"],
                datetime.fromisoformat(state["reference"]),
                existing=existing,
            )
        except Clarification as exc:
            state.update(stage="clarify", asked=asked_field(str(exc)))
            await self.repo.set_pending(user.id, state)
            return [Reply(str(exc) + "\n/cancel to keep the existing event unchanged.")]
        unchanged = all(
            getattr(draft, key) == getattr(existing, key)
            for key in ("title", "start_at", "end_at", "location", "notes")
        )
        if unchanged:
            state.update(stage="clarify", asked=None, nonce=secrets.token_hex(8))
            await self.repo.set_pending(user.id, state)
            return [
                Reply(
                    "I couldn't tell what to change. Tap what you want to change, or type it again.",
                    change_keyboard(state["nonce"]),
                )
            ]
        state.update(
            {
                "stage": "confirm",
                "cancel": False,
                "draft": draft.model_dump(mode="json"),
                "nonce": secrets.token_hex(8),
            }
        )
        availability = await self.availability(user, draft, await self.own_id(user, existing))
        state["availability_snapshot"] = self.availability_snapshot(availability)
        await self.repo.set_pending(user.id, state)
        clash = (
            "\n⚠️ The new time clashes with: "
            + ", ".join(b.title[:100] for b in availability.conflicts[:5])
            if availability.conflicts
            else ""
        )
        if not availability.covered and user.coverage_start:
            clash += "\n⚠️ Outside imported timetable coverage."
        return [
            Reply(
                "Review this change before I apply it:\n\nBefore:\n"
                + details(existing)
                + "\n\nAfter:\n"
                + details(draft)
                + clash,
                confirm_keyboard(state["nonce"]),
            )
        ]

    # ---- /free ----

    async def free_command(self, user: User, args: str) -> list[Reply]:
        if not args.strip():
            await self.repo.set_pending(user.id, {"stage": "free"})
            return [
                Reply(
                    "When do you want to check? Tap a day, or type one, like: fri, 20 oct, or sat 2-5pm.",
                    free_keyboard(),
                )
            ]
        try:
            query = parse_free(args, self.clock())
        except Clarification as exc:
            return [Reply(f"{exc}\n{FREE_EXAMPLES}")]
        if query.slot is not None:
            return [await self.free_slot(user, query)]
        return [await self.free_days(user, query.days)]

    async def free_slot(self, user: User, query) -> Reply:
        slot = query.slot
        result = await self.availability(user, slot)
        when = f"{slot.start_at:%a %-d %b} · {time_range(slot.start_at, slot.end_at)}"
        if result.conflicts:
            text = verdict_line(result) + "\n" + when
            if len(result.conflicts) > 1:
                text += "\n" + "\n".join(
                    f"• {b.title[:60]} ({time_range(b.start_at, b.end_at)})"
                    for b in result.conflicts[:5]
                )
        else:
            text = "✅ No clashes found · " + when
        if result.possible:
            text += "\nYou also saved " + ", ".join(e.title[:40] for e in result.possible[:3])
            text += " at that time."
        if query.assumed_end:
            text += "\n(No end time given, so I checked 2 hours.)"
        return Reply(text + "\n\n" + checked_line(result))

    async def free_days(self, user: User, days) -> Reply:
        """What's booked on each day, then the free gaps between 8am and 11pm."""
        now = self.clock()
        today = now.astimezone(SG).date()
        parts = []
        for day in days:
            label = f"{day:%a %-d %b}"
            if day < today:
                parts.append(f"{label} has already passed.")
                continue
            start = datetime.combine(day, day_time(0), SG)
            end = start + timedelta(days=1)
            blocks = await self.repo.blocks_between(user.id, start, end)
            plans = await self.repo.events_between(
                start - timedelta(days=1), end, [Status.GOING, Status.SAVED], user.id
            )
            # (start, end, name, decided): a Saved plan is listed but never makes you busy.
            busy = [(b.start_at, b.end_at, b.title, True) for b in blocks] + [
                (
                    e.start_at,
                    e.end_at,
                    f"{e.title} (Going)"
                    if e.status == Status.GOING
                    else f"{e.title} (Saved, not decided)",
                    e.status == Status.GOING,
                )
                for e in plans
                if e.end_at > start
            ]
            busy = [item for item in busy if item[1] > (now if day == today else start)]
            gaps = free_gaps(day, [(a, b) for a, b, _, decided in busy if decided], now)
            # Deadlines never make you busy; they're listed so the day's picture is complete.
            due = "".join(
                "\n" + due_line(d)
                for d in await self.repo.deadlines_between(max(start, now), end, user.id)
            )
            if not busy:
                rest = " for the rest of the day" if day == today else " all day"
                parts.append(f"🟢 {label}: nothing booked, free{rest}.{due}")
                continue
            busy.sort()
            lines = [
                f"• {time_range(max(a, start), min(b, end))} {title[:60]}"
                for a, b, title, _ in busy[:8]
            ]
            if len(busy) > 8:
                lines.append(f"…and {len(busy) - 8} more")
            head = "Busy:" if any(item[3] for item in busy) else "Nothing confirmed:"
            if gaps:
                free = "Free: " + ", ".join(time_range(a, b) for a, b in gaps)
            elif day == today and now.astimezone(SG).time() >= day_time(22, 30):
                free = "No free time left today."
            else:
                free = "🔴 No free time between 8am and 11pm."
            parts.append(f"📅 {label}\n{head}\n" + "\n".join(lines) + "\n" + free + due)
        text = "\n\n".join(parts)
        if len(text) > 3900:  # Telegram refuses anything over 4096 characters
            text = text[:3900].rsplit("\n", 1)[0] + "\n…\nAsk about fewer days to see the rest."
        return Reply(text)

    # ---- subscribed calendar feed (Apple Calendar first) ----

    def feed_url(self, token: str) -> str | None:
        base = self.settings.app_base_url.rstrip("/")
        return f"{base}/cal/{token}.ics" if base else None

    async def calendar_setup(self, user: User, intro: str = "") -> list[Reply]:
        """/calendar: AmIFree → your calendar (feed), then your calendar → AmIFree (busy)."""
        return [await self.feed_setup(user, intro), await self.personal_calendar_status(user)]

    async def feed_setup(self, user: User, intro: str = "") -> Reply:
        if not self.settings.app_base_url:
            return Reply(
                "Automatic calendar sync needs AmIFree to be online at a public address, "
                "which isn't set up yet. For now, use the 🍎 and 📅 buttons on your plans."
            )
        token = user.calendar_feed_token
        if not token:
            token = secrets.token_urlsafe(32)
            await self.repo.set_feed_token(user.id, token)
            await self.safe_action(user.id, "calendar_feed_created")
        seen = user.calendar_feed_seen_at if user.calendar_feed_token else None
        status = (
            f"✅ Connected. Your calendar last synced {seen.astimezone(SG):%-d %b, %-I:%M%p}."
            if seen
            else "Not connected yet."
        )
        text = (
            (intro + "\n\n" if intro else "")
            + "📤 Put every plan in your iPhone calendar automatically\n"
            + status
            + "\n\n1. Tap and hold this link, then Copy:\n"
            + self.feed_url(token)
            + "\n\n2. On your iPhone open Settings → Apps → Calendar → Calendar Accounts → "
            "Add Account → Other → Add Subscribed Calendar. (On older iOS: Settings → Calendar.)"
            "\n3. Paste the link, tap Next, then Save."
            "\n\nOn a Mac: Calendar → File → New Calendar Subscription, then paste."
            "\n\nYour Going and Saved plans then appear in a calendar called AmIFree and update "
            "by themselves when plans change. Keep the link private: anyone with it can see "
            "your plans."
        )
        return Reply(text, calendar_settings_keyboard())

    # ---- personal calendar → AmIFree (busy times only) ----

    @property
    def link_secret(self) -> str:
        return (
            self.settings.calendar_link_key.get_secret_value()
            or self.settings.telegram_bot_token.get_secret_value()
            or "amifree-development-only"
        )

    async def personal_calendar_status(self, user: User) -> Reply:
        calendars = await self.repo.list_calendars(user.id)
        if not calendars:
            return Reply(
                "📥 Let AmIFree see when you're busy\n"
                "Send me your calendar's private link and its busy times will count when I "
                "check your plans and /free. Use several calendars? Send each link. I keep "
                "only start and end times, never titles, places or notes.\n\n"
                "Not sure where to find it? Pick your calendar and I'll show you step by step.",
                onboarding.pick_calendar_keyboard(),
            )
        lines = []
        for c in calendars:
            if c.error:
                lines.append(f"• {c.label}: ⚠️ {c.error}")
            elif c.synced_at:
                lines.append(
                    f"• {c.label} · refreshed {clock(c.synced_at)}, {c.synced_at.astimezone(SG):%-d %b}"
                )
            else:
                lines.append(f"• {c.label} · connecting")
        room = MAX_CALENDARS - len(calendars)
        more = (
            f"Paste another calendar's link to add it ({room} more allowed)."
            if room > 0
            else f"That's the most I check ({MAX_CALENDARS}). Remove one to add another."
        )
        return Reply(
            "📥 Calendars I check for busy times\n"
            + "\n".join(lines)
            + f"\n\n{more} I refresh them every hour and keep only when you're busy, never "
            "what your events are.",
            personal_calendars_keyboard(calendars),
        )

    async def connect_calendar(self, user: User, link: str) -> list[Reply]:
        if self.calendar_reader is None:
            return [Reply("Reading calendar links isn't available right now.")]
        if not self.calendar_limit.allow(user.id):
            return [Reply(SLOW_DOWN)]
        digest = link_hash(link, self.link_secret)
        calendars = await self.repo.list_calendars(user.id)
        known = any(c.url_hash == digest for c in calendars)
        if not known and len(calendars) >= MAX_CALENDARS:
            return [
                Reply(
                    f"You've connected {MAX_CALENDARS} calendars, the most I check. Remove one "
                    "in /calendar first."
                )
            ]
        now = self.clock()
        try:
            data = await self.calendar_reader.read(link, now)
        except CalendarLinkError as exc:
            return [Reply(str(exc))]
        calendar, new = await self.repo.add_calendar(
            user.id, seal(link, self.link_secret), digest, data.label
        )
        await self.repo.replace_calendar(calendar.id, data.busy, now)
        await self.safe_action(
            user.id, "personal_calendar_connected", metadata={"busy": len(data.busy), "new": new}
        )
        if not new:
            return [
                Reply(f"✅ {calendar.label} was already connected. I've refreshed it just now.")
            ]
        total = len(calendars) + 1
        if total == 1:
            return [Reply(onboarding.calendar_added(calendar.label, len(data.busy)))]
        return [Reply(onboarding.another_calendar_added(calendar.label, total))]

    async def remove_calendar(self, user: User, calendar_id: str) -> list[Reply]:
        try:
            UUID(calendar_id)
        except ValueError:
            return [Reply("That button isn't valid. Use /calendar.")]
        calendars = {c.id: c for c in await self.repo.list_calendars(user.id)}
        if calendar_id not in calendars or not await self.repo.remove_calendar(
            user.id, calendar_id
        ):
            return [Reply("That calendar is already removed. Use /calendar to see what's left.")]
        await self.safe_action(user.id, "personal_calendar_disconnected")
        status = await self.personal_calendar_status(user)
        removed = (
            f"Removed {calendars[calendar_id].label}. I've deleted its link and every busy time I "
            "read from it."
        )
        return [Reply(f"{removed}\n\n{status.text}", status.keyboard, edit=True)]

    async def disconnect_calendar(self, user: User) -> list[Reply]:
        for c in await self.repo.list_calendars(user.id):
            await self.repo.remove_calendar(user.id, c.id)
        await self.safe_action(user.id, "personal_calendar_disconnected")
        return [
            Reply(
                "All calendars disconnected. I've deleted their links and every busy time I "
                "read from them."
            )
        ]

    async def refresh_calendars(self, limit: int = 20) -> int:
        """Background refresh, run by the jobs tick. Returns how many calendars were checked."""
        if self.calendar_reader is None:
            return 0
        now = self.clock()
        due = await self.repo.calendars_due(now - CALENDAR_REFRESH, limit)
        for calendar in due:
            link = unseal(calendar.sealed_url, self.link_secret)
            if link is None:
                # The encryption key changed, so the stored link can't be read any more.
                await self.repo.replace_calendar(calendar.id, [], now)
                await self.repo.calendar_error(
                    calendar.id, "Please send me this calendar's link again.", now
                )
                continue
            try:
                data = await self.calendar_reader.read(link, now)
            except CalendarLinkGone as exc:
                # A revoked link must not leave stale busy times behind.
                await self.repo.replace_calendar(calendar.id, [], now)
                await self.repo.calendar_error(calendar.id, str(exc), now)
            except CalendarLinkError as exc:
                await self.repo.calendar_error(calendar.id, str(exc), now)  # keep last good copy
            except Exception as exc:
                log.warning("calendar_refresh_failed error_type=%s", type(exc).__name__)
                await self.repo.calendar_error(calendar.id, "I couldn't refresh it just now.", now)
            else:
                await self.repo.replace_calendar(calendar.id, data.busy, now)
        return len(due)

    async def calendar_change(self, user: User, change: str) -> list[Reply]:
        if change == "off":
            await self.repo.set_feed_token(user.id, None)
            await self.safe_action(user.id, "calendar_feed_off")
            return [
                Reply(
                    "Calendar sync is off and the old link no longer works. To remove the "
                    "AmIFree calendar from your iPhone: Settings → Apps → Calendar → Calendar "
                    "Accounts → Subscribed Calendars → AmIFree → Delete Account."
                )
            ]
        await self.repo.set_feed_token(user.id, None)
        await self.safe_action(user.id, "calendar_feed_reset")
        fresh = user.model_copy(update={"calendar_feed_token": None, "calendar_feed_seen_at": None})
        return [
            await self.feed_setup(
                fresh,
                "New link made. The old one has stopped working, so delete the old AmIFree "
                "calendar on your iPhone and subscribe to this one.",
            )
        ]

    async def calendar_feed(self, token: str, user_agent: str = "") -> bytes | None:
        """The private feed a calendar app subscribes to. None for an unknown link."""
        user = await self.repo.user_by_feed_token(token)
        if not user:
            return None
        now = self.clock()
        events = await self.repo.events_between(
            now - FEED_PAST, now + FEED_AHEAD, [Status.GOING, Status.SAVED], user.id
        )
        preview = any(bot in user_agent.lower() for bot in PREVIEW_BOTS)
        seen = user.calendar_feed_seen_at
        if not preview and (seen is None or now - seen > FEED_SEEN_EVERY):
            await self.repo.mark_feed_seen(user.id, now)
        return build_feed(events, now=now)

    async def calendar_file(self, user: User, event_id: str) -> list[Reply]:
        try:
            UUID(event_id)
        except ValueError:
            return [Reply("That button isn't valid. Use /plans.")]
        event = await self.repo.get_event(user.id, event_id)
        if not event or event.status in (Status.CANCELLED, Status.DECLINED, Status.PREVIEW):
            return [Reply("That plan isn't saved any more. Use /plans to see your plans.")]
        await self.safe_action(user.id, "calendar_export", event.id, {"target": "ics"})
        return [
            Reply(
                "Tap the file, then Add to Calendar. Works with Apple Calendar and Outlook.",
                document=(ics_filename(event), build_ics(event, now=self.clock())),
            )
        ]

    async def callback(self, telegram_id: int, data: str) -> list[Reply]:
        user = await self.repo.ensure_user(telegram_id)
        if data.startswith("g:"):
            return await self.groups.from_private(telegram_id, data)
        if data.startswith("c:"):
            return await self.groups.confirm_attendance(telegram_id, data)
        if data.startswith("m:"):
            return await self.groups.manage_action(user, data)
        if data.startswith("ser:"):
            return await self.groups.series_action(user, data)
        if data.startswith(("at:", "ap:", "aa:")):
            return await self.groups.attendance_action(user, data)
        if data.startswith(("events:", "plans:")):
            prefix, _, page = data.partition(":")
            if not page.isascii() or not page.isdigit() or len(page) > 8:
                return [Reply(f"That page isn't valid. Use /{prefix}.")]
            return await self.list_events(user, int(page), plans=prefix == "plans")
        parts = data.split(":")
        action = parts[0]
        if action in ("pc", "dc", "bc", "rc") and len(parts) == 2:
            return await self.open_item(user, action, parts[1])
        if action == "dl" and len(parts) == 3 and parts[1] in ("done", "rm"):
            return await self.deadline_action(user, parts[1], parts[2])
        if action == "chg" and len(parts) == 3 and parts[1] in ("venue", "time", "title"):
            return await self.choose_field(user, parts[1], parts[2])
        if data.startswith("rec:") and len(parts) == 3 and parts[1] in ("once", "weekly"):
            return await self.choose_recurring(user, parts[1], parts[2])
        if (
            action == "set"
            and len(parts) == 3
            and parts[1] == "reminders"
            and parts[2] in ("on", "off")
        ):
            await self.repo.set_user_flags(user.id, reminders_enabled=parts[2] == "on")
            user = await self.repo.ensure_user(telegram_id)
            panel = self.settings_panel(user)
            return [Reply(panel.text, panel.keyboard, edit=True)]
        if action == "rem" and len(parts) == 3 and parts[1] in ("done", "del", "10", "60", "tmr"):
            return await self.reminder_action(user, parts[1], parts[2])
        if action == "undo" and len(parts) == 2:
            return await self.undo_removal(user, parts[1])
        if data in MENU_COMMANDS:
            return await self.command(telegram_id, MENU_COMMANDS[data])
        if data.startswith("setup:"):
            found = onboarding.screen(data.removeprefix("setup:"))
            if found is None:
                return [Reply("That button isn't valid. Send /start.")]
            text, keyboard = found
            return [
                Reply(
                    text, keyboard, edit=True, parse_mode="HTML" if data == "setup:home" else None
                )
            ]
        if data in ("free:today", "free:tomorrow", "free:weekend"):
            today = self.clock().astimezone(SG).date()
            days = {
                "free:today": (today,),
                "free:tomorrow": (today + timedelta(days=1),),
                "free:weekend": weekend(today),
            }[data]
            if user.pending and user.pending.get("stage") == "free":
                await self.repo.set_pending(user.id, None)
            reply = await self.free_days(user, days)
            return [Reply(reply.text, edit=True)]
        if data == "pcal:off":
            return await self.disconnect_calendar(user)
        if data.startswith("pcal:rm:"):
            return await self.remove_calendar(user, data.removeprefix("pcal:rm:"))
        if action == "cal" and len(parts) == 2 and parts[1] in ("reset", "off"):
            return await self.calendar_change(user, parts[1])
        if action == "ics" and len(parts) == 2:
            return await self.calendar_file(user, parts[1])
        if action == "unblock" and len(parts) == 2:
            try:
                UUID(parts[1])
            except ValueError:
                return [Reply("That button isn't valid. Use /blocks.")]
            rule = next((r for r in await self.repo.list_blocks(user.id) if r.id == parts[1]), None)
            removed = rule is not None and await self.repo.delete_block(user.id, parts[1])
            if not removed:
                return [Reply("That block is already gone.")]
            token = self.remember_removed(user, "block", rule.model_dump(mode="json"))
            return [Reply("Busy block removed.", undo_keyboard(token), edit=True)]
        if action in ("confirm", "dismiss") and len(parts) == 2:
            pending = user.pending
            if not pending or pending.get("nonce") != parts[1]:
                return [Reply("That confirmation expired. Use /events to open the latest card.")]
            if action == "dismiss":
                await self.repo.set_pending(user.id, None)
                return [Reply("Kept unchanged.")]
            if pending["stage"] == "delete":
                await self.repo.delete_user(user.id)
                return [Reply("Your AmIFree data has been deleted. Use /start to set up again.")]
            if pending["stage"] == "block":
                rule = BlockRule.model_validate(pending["rule"])
                await self.repo.create_block(user.id, rule)
                await self.repo.set_pending(user.id, None)
                await self.safe_action(user.id, "block")
                clashes = await self.block_clashes(user, rule)
                return [
                    Reply(
                        "Blocked: "
                        + describe(rule)
                        + clashes
                        + ("\nOpen /plans to change one of them." if clashes else "")
                        + "\n/blocks lists your blocks.",
                        edit=True,
                    )
                ]
            if pending["stage"] != "confirm":
                return [Reply("That action is no longer available.")]
            event = await self.load_target(user, pending)
            if not event or event.version != pending["base_version"]:
                await self.repo.set_pending(user.id, None)
                return [Reply("That event changed. Use /events to review its latest state.")]
            draft = EventDraft.model_validate(pending["draft"]) if not pending["cancel"] else None
            if draft is not None:
                current = self.availability_snapshot(
                    await self.availability(user, draft, await self.own_id(user, event))
                )
                previous = pending.get("availability_snapshot")
                if (
                    previous is None
                    or set(current["conflicts"]) - set(previous["conflicts"])
                    or (previous["covered"] and not current["covered"])
                ):
                    refreshed = await self.prepare(
                        user, Extraction.model_validate(pending["extraction"]), pending, event
                    )
                    return [
                        Reply(
                            "Your availability changed. Review the updated preview and confirm again."
                        ),
                        *refreshed,
                    ]
            if pending.get("group"):
                result = await self.repo.update_group_event(
                    user.id, event, draft, pending["cancel"]
                )
            else:
                result = await self.repo.update_event(user.id, event, draft, pending["cancel"])
            await self.repo.set_pending(user.id, None)
            if not result:
                return [Reply("That event changed. Use /events to open its latest card.")]
            if pending.get("group"):
                await self.group_changed(result.id)
                # Alerts were queued with the change; deliver them now rather than on the next tick.
                await self.flush_notifications()
                return [
                    Reply(
                        "Event cancelled for everyone. Members who saved it are being told."
                        if pending["cancel"]
                        else "Event updated. Everyone who saved it gets one message with the change."
                    ),
                    *self.groups.series_followup(result, pending["cancel"]),
                ]
            return [
                Reply("Event cancelled." if pending["cancel"] else "Event updated."),
                event_card(result, await self.availability(user, result, result.id)),
            ]
        if len(parts) != 3:
            return [Reply("That button isn't valid. Use /events to open a fresh card.")]
        _, event_id, version = parts
        event = await self.repo.get_event(user.id, event_id)
        if not event:
            return [Reply("That event isn't available to you.")]
        if str(event.version) != version or event.status == Status.CANCELLED:
            return [
                Reply("That card is outdated. Here is the latest state:"),
                event_card(event, await self.availability(user, event, event.id)),
            ]
        if action == "pick":
            pending = user.pending
            if not pending or pending["stage"] != "choose" or event.id not in pending["candidates"]:
                return [Reply("That selection expired. Forward the update again.")]
            return await self.prepare(
                user, Extraction.model_validate(pending["extraction"]), pending, event
            )
        if action == "edit" and event.group_event_id:
            return [Reply("Only the organiser can edit this plan, from its card in the group.")]
        if action == "edit":
            return await self.begin_change(user, event)
        if action == "save":
            result = await self.repo.save(user.id, event)
            if not result:
                return [Reply("That event changed. Use /plans to see the latest card.")]
            await self.group_changed(result.group_event_id)
            availability = await self.availability(user, result, result.id)
            return [
                decided_card(result, availability, "Saved. Find it any time with /plans.", user)
            ]
        statuses = {
            "go": Status.GOING,
            "force": Status.GOING,
            "maybe": Status.MAYBE,
            "decline": Status.DECLINED,
        }
        if action not in statuses:
            return [Reply("That action isn't available.")]
        availability = await self.availability(user, event, event.id)
        if action == "go" and availability.conflicts:
            return [
                Reply("Your availability changed. Review the clashes before adding it anyway."),
                event_card(event, availability),
            ]
        was_preview = event.status == Status.PREVIEW
        result = await self.repo.rsvp(user.id, event, statuses[action])
        if not result:
            return [Reply("That event changed. Use /events to see the latest card.")]
        await self.group_changed(result.group_event_id)
        if statuses[action] == Status.DECLINED:
            note = "Skipped." if was_preview else "Removed from your plans."
        else:
            note = {
                Status.GOING: "Added to your plans. This time is now blocked.",
                Status.MAYBE: "Marked Maybe. This won't block your time.",
            }[statuses[action]]
        card = decided_card(result, availability, note, user)
        if statuses[action] == Status.DECLINED and not was_preview and not result.group_event_id:
            # One tap brings it back: Undo is just "add to my plans" again.
            card.keyboard = plan_undo_keyboard(result)
        return [card]
