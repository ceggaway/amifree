"""Application use cases; no Telegram network calls and no LLM availability decisions."""

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as day_time
from uuid import UUID

from app.bot import onboarding
from app.bot.keyboards import (
    calendar_settings_keyboard,
    choose_keyboard,
    confirm_keyboard,
    free_keyboard,
    page_keyboard,
    personal_calendars_keyboard,
    remove_block_keyboard,
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
from app.schemas.event import SG, Event, EventDraft, Extraction, GroupEvent, Status
from app.services.availability import check_availability
from app.services.calendar_export import build_feed, build_ics, ics_filename
from app.services.date_resolution import (
    Clarification,
    single_event_guard,
    validate_extraction,
)
from app.services.event_extractor import settle_ambiguity, settle_kind
from app.services.event_updates import match_updates
from app.services.free_time import free_gaps, parse_free, weekend
from app.services.group_events import GroupEventService
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
from app.services.quick_blocks import MAX_RULES, USAGE, describe, parse_block

log = logging.getLogger(__name__)


FEED_PAST, FEED_AHEAD = timedelta(days=30), timedelta(days=400)
FEED_SEEN_EVERY = timedelta(minutes=10)
CALENDAR_REFRESH = timedelta(hours=1)
# Link previews fetch the feed too; they must not count as the user's calendar subscribing.
PREVIEW_BOTS = ("telegrambot", "whatsapp", "facebookexternalhit", "slackbot", "discordbot")


def synced(user: User) -> bool:
    return bool(user.calendar_feed_token and user.calendar_feed_seen_at)


def decided_card(event: Event, availability, note: str, user: User) -> Reply:
    """The tapped card, updated in place: same verdict and details, new status and buttons."""
    card = event_card(event, availability, synced(user))
    if event.status in (Status.SAVED, Status.GOING) and not user.calendar_feed_token:
        note += "\nTip: /calendar puts every plan in your iPhone calendar automatically."
    return Reply(f"{card.text}\n{note}", card.keyboard, edit=True)


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
        self.groups = GroupEventService(self)
        self.polls = PollService(self)
        # Set by the bot layer: re-renders a group card after its headcount changes.
        self.on_group_change = None
        # Set at startup: the outbox worker that sends reminders, alerts and nudges.
        self.notifier = None

    async def flush_notifications(self):
        if self.notifier:
            try:
                await self.notifier.deliver()
            except Exception as exc:
                log.warning("notification_flush_failed error_type=%s", type(exc).__name__)

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
                        "You're all set ✅ Forward me a plan, or use /plans · /free · /calendar"
                        "\nChecking: " + " and ".join(connected) + "."
                    )
                ]
            return [Reply(onboarding.WELCOME, onboarding.welcome_keyboard())]
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
            return await self.list_events(user, plans=True)
        if command in ("event", "plan", "whosfree"):
            return [Reply(f"/{command} works in group chats. Add me to a group and use it there.")]
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
                return [Reply("Tell me when you're busy. " + USAGE)]
            try:
                rule = parse_block(args, self.clock(), explicit=True)
            except Clarification as exc:
                return [Reply(str(exc))]
            return await self.preview_block(user, rule)
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

    async def preview_block(self, user: User, rule: BlockRule) -> list[Reply]:
        if len(await self.repo.list_blocks(user.id)) >= MAX_RULES:
            return [Reply("You've reached the busy block limit. Remove one with /blocks first.")]
        nonce = secrets.token_hex(8)
        await self.repo.set_pending(
            user.id, {"stage": "block", "rule": rule.model_dump(mode="json"), "nonce": nonce}
        )
        note = (
            "\nAdd 'until <date>' to change when it stops repeating."
            if rule.kind == "WEEKLY"
            else ""
        )
        return [
            Reply(
                "Block this time?\n\n" + describe(rule) + note,
                confirm_keyboard(nonce, "Save block"),
            )
        ]

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
            result = await self.importer.import_link(text.strip(), reference)
            await self.repo.import_timetable(user.id, result)
            return [Reply(onboarding.nusmods_added(result.academic_year, result.semester))]
        if CALENDAR_URL.fullmatch(text.strip()):
            # A bare link that is neither NUSMods nor a calendar: say so, don't ask the LLM.
            return [Reply(onboarding.NOT_A_SCHEDULE_LINK)]
        pending = user.pending
        if pending and pending["stage"] in ("confirm", "choose", "delete", "block"):
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
                return [Reply(str(exc))]
            if rule:
                return await self.preview_block(user, rule)
        started = time.monotonic()
        previous = pending.get("extraction") if pending else None
        if pending:
            if not pending.get("use_next_message_reference"):
                reference = datetime.fromisoformat(pending["reference"])
            message_id = pending["message_id"]
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
        extracted = await self.extractor.extract(text, reference, previous)
        if not (pending and pending.get("event_id")):  # edits of saved events keep their kind
            extracted = settle_kind(extracted, clarifying=bool(pending), text=source)
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
        if pending and pending.get("event_id"):
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
        try:
            draft = validate_extraction(
                extracted,
                source,
                reference,
                default_minutes=self.settings.default_event_duration_minutes,
            )
        except Clarification as exc:
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
        await self.repo.set_pending(user.id, state)
        scope = " Everyone who saved it will be told what changed." if group else ""
        return [
            Reply(
                "Send the corrected title, date, start/end time or venue. I will show a preview before applying it."
                + scope
                + "\n/cancel to keep this event unchanged."
            )
        ]

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
            state["stage"] = "clarify"
            await self.repo.set_pending(user.id, state)
            return [Reply(str(exc) + "\n/cancel to keep the existing event unchanged.")]
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
        if not availability.covered:
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
            return [Reply("When do you want to check?", free_keyboard())]
        try:
            query = parse_free(args, self.clock())
        except Clarification as exc:
            return [Reply(f"{exc}\nFor example: /free fri 7-9pm or /free tomorrow")]
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
            busy = [(b.start_at, b.end_at) for b in blocks] + [
                (e.start_at, e.end_at) for e in plans if e.end_at > start
            ]
            gaps = free_gaps(day, busy, now)
            if gaps:
                parts.append(
                    f"🟢 Free on {label}:\n"
                    + "\n".join(f"• {clock(a)} – {clock(b)}" for a, b in gaps)
                )
            elif day == today and now.astimezone(SG).time() >= day_time(22, 30):
                parts.append("No free time left today. I list free time from 8am to 11pm.")
            else:
                parts.append(f"🔴 No free time on {label} between 8am and 11pm.")
        sources = ["your Going and Saved plans", "busy blocks"]
        if any(c.synced_at for c in await self.repo.list_calendars(user.id)):
            sources.append("personal calendar")
        if user.coverage_start:
            sources.append("NUSMods timetable")
        footer = "Checked: " + ", ".join(sources[:-1]) + " and " + sources[-1] + "."
        return Reply("\n\n".join(parts) + "\n\n" + footer)

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
        if data.startswith(("events:", "plans:")):
            prefix, _, page = data.partition(":")
            if not page.isascii() or not page.isdigit() or len(page) > 8:
                return [Reply(f"That page isn't valid. Use /{prefix}.")]
            return await self.list_events(user, int(page), plans=prefix == "plans")
        parts = data.split(":")
        action = parts[0]
        if data.startswith("setup:"):
            found = onboarding.screen(data.removeprefix("setup:"))
            if found is None:
                return [Reply("That button isn't valid. Send /start.")]
            text, keyboard = found
            return [Reply(text, keyboard, edit=True)]
        if data in ("free:today", "free:tomorrow", "free:weekend"):
            today = self.clock().astimezone(SG).date()
            days = {
                "free:today": (today,),
                "free:tomorrow": (today + timedelta(days=1),),
                "free:weekend": weekend(today),
            }[data]
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
            removed = await self.repo.delete_block(user.id, parts[1])
            return [Reply("Busy block removed." if removed else "That block is already gone.")]
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
                await self.repo.create_block(user.id, BlockRule.model_validate(pending["rule"]))
                await self.repo.set_pending(user.id, None)
                await self.safe_action(user.id, "block")
                return [
                    Reply("Blocked. Plans that overlap it will show a clash. /blocks lists them.")
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
                    )
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
                Reply(
                    "Your availability changed. Review the clashes before choosing Going anyway."
                ),
                event_card(event, availability),
            ]
        result = await self.repo.rsvp(user.id, event, statuses[action])
        if not result:
            return [Reply("That event changed. Use /events to see the latest card.")]
        await self.group_changed(result.group_event_id)
        note = {
            Status.GOING: "Added to your commitments: this time is now blocked.",
            Status.MAYBE: "Marked Maybe. This won't block your time.",
            Status.DECLINED: "Marked Can't make it. This won't block your time.",
        }[statuses[action]]
        return [decided_card(result, availability, note, user)]
