"""Application use cases; no Telegram network calls and no LLM availability decisions."""

import logging
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.bot.keyboards import (
    choose_keyboard,
    confirm_keyboard,
    page_keyboard,
    remove_block_keyboard,
)
from app.bot.messages import HELP, Reply, details, event_card
from app.config import Settings
from app.models.timetable import BlockRule
from app.models.user import User
from app.schemas.event import SG, Event, EventDraft, Extraction, GroupEvent, Status
from app.services.availability import check_availability
from app.services.date_resolution import Clarification, validate_extraction
from app.services.event_updates import match_updates
from app.services.group_events import GroupEventService
from app.services.polls import PollService
from app.services.quick_blocks import MAX_RULES, USAGE, describe, parse_block

log = logging.getLogger(__name__)


class SchedulingService:
    def __init__(
        self,
        repository,
        extractor,
        importer,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repo, self.extractor, self.importer, self.settings = (
            repository,
            extractor,
            importer,
            settings,
        )
        self.clock = clock or (lambda: datetime.now(SG))
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
        return check_availability(
            event,
            blocks,
            events,
            exclude_event_id=exclude,
            coverage_start=user.coverage_start,
            coverage_end=user.coverage_end,
            busy_day_threshold=self.settings.busy_day_threshold,
        )

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
            if user.onboarding_status == "READY":
                return [
                    Reply(
                        f"You're ready. Timetable: {user.academic_year}, semester {user.semester}.\nForward me a plan and tap Save. Send a fresh NUSMods link to replace your timetable.\n/plans lists what you've saved. /help for commands."
                    )
                ]
            return [
                Reply(
                    "Forward me a plan and I'll save it and tell you if it clashes.\n\nOptional: send your NUSMods Share/Sync link so your classes and exams are checked too.\nI only act on what you send me, commands and button taps. /help for more."
                )
            ]
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
            event_card(e, await self.availability(user, e, e.id))
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
        if "nusmods.com" in text.lower() and text.strip().startswith(("http://", "https://")):
            result = await self.importer.import_link(text.strip(), reference)
            await self.repo.import_timetable(user.id, result)
            return [
                Reply(
                    f"Setup complete. Imported {len(result.blocks)} class/exam busy periods for {result.academic_year}, semester {result.semester}.\nCoverage: {result.coverage_start:%d %b %Y}–{result.coverage_end:%d %b %Y}.\nForward me a plan. I check this timetable, your busy blocks and your Going plans."
                )
            ]
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

    async def callback(self, telegram_id: int, data: str) -> list[Reply]:
        user = await self.repo.ensure_user(telegram_id)
        if data.startswith(("events:", "plans:")):
            prefix, _, page = data.partition(":")
            if not page.isascii() or not page.isdigit() or len(page) > 8:
                return [Reply(f"That page isn't valid. Use /{prefix}.")]
            return await self.list_events(user, int(page), plans=prefix == "plans")
        parts = data.split(":")
        action = parts[0]
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
            return [
                Reply("Saved. Find it any time with /plans."),
                event_card(result, await self.availability(user, result, result.id)),
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
        text = {
            Status.GOING: "Added to your AmIFree commitments.",
            Status.MAYBE: "Marked Maybe. This won't block your availability.",
            Status.DECLINED: "Marked Can't make it. This won't block your availability.",
        }[statuses[action]]
        return [Reply(text), event_card(result, availability)]
