"""Application use cases; no Telegram network calls and no LLM availability decisions."""

import logging
import secrets
import time
from collections.abc import Callable
from datetime import datetime

from app.bot.keyboards import choose_keyboard, confirm_keyboard
from app.bot.messages import HELP, Reply, details, event_card
from app.config import Settings
from app.models.user import User
from app.schemas.event import SG, Event, EventDraft, Extraction, Status
from app.services.availability import check_availability
from app.services.date_resolution import Clarification, validate_extraction
from app.services.event_updates import match_updates

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
        )

    async def safe_action(self, user_id, kind, event_id=None, metadata=None):
        # Analytics errors should not break a successful commitment.
        try:
            await self.repo.action(user_id, kind, event_id, metadata)
        except Exception as exc:
            log.warning("analytics_failed error_type=%s action=%s", type(exc).__name__, kind)

    async def command(self, telegram_id: int, command: str) -> list[Reply]:
        user = await self.repo.ensure_user(telegram_id)
        if command == "help":
            return [Reply(HELP)]
        if command == "start":
            await self.safe_action(user.id, "start")
            if user.onboarding_status == "READY":
                return [
                    Reply(
                        f"You're ready. Timetable: {user.academic_year}, semester {user.semester}.\nForward me an event message. Send a fresh NUSMods link to replace your timetable.\n/help for commands."
                    )
                ]
            return [
                Reply(
                    "Forward me an event and I'll tell you if you're free.\n\nSend your NUSMods Share/Sync link to set up AmIFree.\n/help for more information."
                )
            ]
        if command == "cancel":
            await self.repo.set_pending(user.id, None)
            return [Reply("Pending action cleared. Forward an event when you're ready.")]
        if command == "delete":
            nonce = secrets.token_hex(8)
            await self.repo.set_pending(user.id, {"stage": "delete", "nonce": nonce})
            return [
                Reply(
                    "Delete all your AmIFree timetable, events, pending messages and analytics? This cannot be undone.",
                    confirm_keyboard(nonce, "Delete my data"),
                )
            ]
        if command == "events":
            events = await self.repo.recent_events(user.id)
            if not events:
                return [Reply("No recent events yet. Forward an invitation after setup.")]
            return [event_card(e, await self.availability(user, e, e.id)) for e in events[:10]]
        return [Reply("Use /help to see the available commands.")]

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
                    f"Setup complete. Imported {len(result.blocks)} class/exam busy periods for {result.academic_year}, semester {result.semester}.\nCoverage: {result.coverage_start:%d %b %Y}–{result.coverage_end:%d %b %Y}.\nForward me an event message. Only this timetable and your Going commitments are checked."
                )
            ]
        if user.onboarding_status != "READY":
            return [Reply("Send your NUSMods Share/Sync link first to set up AmIFree.")]
        pending = user.pending
        if pending and pending["stage"] in ("confirm", "choose", "delete"):
            return [
                Reply(
                    "Please finish the pending action using its buttons, or send /cancel before forwarding another event."
                )
            ]
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
            state.update({"event_id": pending["event_id"], "base_version": pending["base_version"]})
            existing = await self.repo.get_event(user.id, state["event_id"])
            if not existing or existing.version != state["base_version"]:
                await self.repo.set_pending(user.id, None)
                return [
                    Reply(
                        "That event changed while you were editing. Use /events to open its latest card."
                    )
                ]
            return await self.prepare(user, extracted, state, existing)
        if extracted.kind in ("update", "cancel"):
            candidates = match_updates(extracted, await self.repo.recent_events(user.id))
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

    async def prepare(
        self, user: User, extraction: Extraction, state: dict, existing: Event
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
        availability = await self.availability(user, draft, existing.id)
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
        parts = data.split(":")
        action = parts[0]
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
            if pending["stage"] != "confirm":
                return [Reply("That action is no longer available.")]
            event = await self.repo.get_event(user.id, pending["event_id"])
            if not event or event.version != pending["base_version"]:
                await self.repo.set_pending(user.id, None)
                return [Reply("That event changed. Use /events to review its latest state.")]
            draft = EventDraft.model_validate(pending["draft"]) if not pending["cancel"] else None
            if draft is not None:
                current = self.availability_snapshot(await self.availability(user, draft, event.id))
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
            result = await self.repo.update_event(user.id, event, draft, pending["cancel"])
            await self.repo.set_pending(user.id, None)
            if not result:
                return [Reply("That event changed. Use /events to open its latest card.")]
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
        if action == "edit":
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
            await self.repo.set_pending(
                user.id,
                {
                    "stage": "clarify",
                    "source": details(event) + "\n" + base.model_dump_json(),
                    "reference": self.clock().astimezone(SG).isoformat(),
                    "use_next_message_reference": True,
                    "message_id": event.source_message_id,
                    "extraction": base.model_dump(mode="json"),
                    "event_id": event.id,
                    "base_version": event.version,
                },
            )
            return [
                Reply(
                    "Send the corrected title, date, start/end time or venue. I will show a preview before applying it.\n/cancel to keep this event unchanged."
                )
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
        text = {
            Status.GOING: "Added to your AmIFree commitments.",
            Status.MAYBE: "Marked Maybe. This won't block your availability.",
            Status.DECLINED: "Marked Can't make it. This won't block your availability.",
        }[statuses[action]]
        return [Reply(text), event_card(result, availability)]
