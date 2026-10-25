"""Authenticated JSON API for the Mini App.

Every route resolves the user from the session token and goes through the same service
and repository methods as the bot, so there is one source of truth and one clash engine.
"""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from app.api.auth import (
    AuthError,
    init_data_profile,
    issue_token,
    session_key,
    verify_init_data,
    verify_token,
)
from app.api.schemas import (
    AttendanceRequest,
    AuthRequest,
    AvailabilityRequest,
    BlockRequest,
    DetectionRequest,
    ExportRequest,
    GroupEventForm,
    GroupEventPatch,
    PlanPatch,
    PollForm,
    PollPickRequest,
    PollVoteRequest,
    RsvpRequest,
    SeriesRequest,
    SettingsPatch,
    VersionRequest,
)
from app.config import Settings
from app.models.user import User
from app.schemas.event import Event, EventDraft, Status
from app.services import analytics
from app.services.app_links import parse_chat_code
from app.services.availability import Availability
from app.services.date_resolution import Clarification
from app.services.group_hub import NotFound, Refused
from app.services.quick_blocks import MAX_RULES, describe, parse_block
from app.services.ratelimit import RateLimiter

PLAN_STATUSES = [Status.SAVED, Status.GOING, Status.MAYBE]
EDITABLE = ("title", "start_at", "end_at", "location", "notes")


def block_json(block) -> dict:
    return {
        "title": block.title,
        "start_at": block.start_at.isoformat(),
        "end_at": block.end_at.isoformat(),
        "location": getattr(block, "location", None),
        "source": getattr(block, "source", "PLAN"),
    }


def availability_json(availability: Availability) -> dict:
    return {
        "verdict": availability.verdict,
        "covered": availability.covered,
        "busy_day": availability.busy_day,
        "day_count": availability.day_count,
        "conflicts": [block_json(b) for b in availability.conflicts[:10]],
        "possible": [{"id": e.id, "title": e.title} for e in availability.possible[:10]],
    }


def plan_json(event: Event, availability: Availability | None = None) -> dict:
    data = {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "location": event.location,
        "notes": event.notes,
        "status": event.status.value,
        "version": event.version,
        "shared": bool(event.group_event_id),
        "group_event_id": event.group_event_id,
    }
    if availability is not None:
        data["availability"] = availability_json(availability)
    return data


def rule_json(rule) -> dict:
    return rule.model_dump(mode="json", exclude={"user_id"}) | {"description": describe(rule)}


def window(start: datetime, end: datetime, max_days: int):
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, "Times must include a timezone offset")
    if end <= start or end - start > timedelta(days=max_days):
        raise HTTPException(422, f"Choose a range of at most {max_days} days")


def hub_json(data):
    """Turn the hub's Availability objects into the same JSON the rest of the API uses."""
    if isinstance(data, Availability):
        return availability_json(data)
    if isinstance(data, dict):
        return {k: hub_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [hub_json(v) for v in data]
    return data


async def hub_call(work):
    """Run a hub call: not yours and missing both read 404; a refusal carries its reason."""
    try:
        return hub_json(await work)
    except NotFound:
        raise HTTPException(404, "Not found") from None
    except Refused as exc:
        raise HTTPException(409, hub_json({"message": str(exc), **(exc.detail or {})})) from None


def valid_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(404, "Not found") from None


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")
    token = settings.telegram_bot_token.get_secret_value()
    key = session_key(token, settings.session_secret.get_secret_value()) if token else b""

    def service_of(request: Request):
        service = request.app.state.service
        if not request.app.state.ready or not service or not token:
            raise HTTPException(503, "AmIFree is not ready")
        return service

    async def current_user(request: Request) -> User:
        service = service_of(request)
        scheme, _, value = request.headers.get("Authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise AuthError("Missing session")
            telegram_id = verify_token(value.strip(), key)
        except AuthError:
            raise HTTPException(401, "Sign in from Telegram again") from None
        analytics.identify(telegram_id)
        return await service.repo.ensure_user(telegram_id)

    CurrentUser = Annotated[User, Depends(current_user)]
    # Sign-in is already gated by Telegram's signature; this only bounds a flood of bad
    # attempts. Generous on purpose: a shared key must never lock real users out.
    sign_in_limit = RateLimiter(120, 60)

    def client_ip(request: Request) -> str:
        # Hosting proxies (Render included) put the caller first in X-Forwarded-For. Keying on
        # the last hop would be the proxy itself: one bucket for everybody.
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        return forwarded[:64] or (request.client.host if request.client else "unknown")

    @router.post("/auth/telegram")
    async def authenticate(body: AuthRequest, request: Request):
        service = service_of(request)
        if not sign_in_limit.allow(client_ip(request)):
            raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
        try:
            telegram_id = verify_init_data(body.init_data, token, settings.init_data_max_age)
        except AuthError:
            raise HTTPException(401, "Telegram sign-in could not be verified") from None
        await service.repo.ensure_user(telegram_id)
        analytics.identify(telegram_id)
        analytics.track("app_opened")
        # Kept so people who only use the app still have a name in "Who's going?".
        service.hub.remember_profile(telegram_id, *init_data_profile(body.init_data))
        return {
            "token": issue_token(telegram_id, key, settings.session_ttl),
            "expires_in": settings.session_ttl,
        }

    @router.get("/me")
    async def me(request: Request, user: CurrentUser):
        calendars = await service_of(request).repo.list_calendars(user.id)
        return {
            "telegram_user_id": user.telegram_user_id,
            "reminders_enabled": user.reminders_enabled,
            "nusmods": {
                "connected": user.onboarding_status == "READY",
                "academic_year": user.academic_year,
                "semester": user.semester,
                "coverage_start": user.coverage_start,
                "coverage_end": user.coverage_end,
            },
            "calendars": [
                {
                    "label": c.label,
                    "synced_at": c.synced_at.isoformat() if c.synced_at else None,
                    "status": "ERROR" if c.error else ("CONNECTED" if c.synced_at else "PENDING"),
                }
                for c in calendars
            ],
            "outside_calendars_connected": any(c.synced_at and not c.error for c in calendars),
        }

    @router.patch("/me")
    async def update_settings(body: SettingsPatch, request: Request, user: CurrentUser):
        service = service_of(request)
        await service.repo.set_user_flags(user.id, reminders_enabled=body.reminders_enabled)
        return {"reminders_enabled": body.reminders_enabled}

    async def plans_between(service, user, start, end):
        events = await service.repo.events_between(start, end, PLAN_STATUSES, user.id)
        events.sort(key=lambda e: (e.start_at, e.id))
        return [plan_json(e, await service.availability(user, e, e.id)) for e in events[:200]]

    @router.get("/plans")
    async def plans(
        request: Request,
        user: CurrentUser,
        start: datetime | None = None,
        end: datetime | None = None,
    ):
        service = service_of(request)
        start = start or service.clock()
        end = end or start + timedelta(days=60)
        window(start, end, 92)
        return {"plans": await plans_between(service, user, start, end)}

    @router.get("/calendar")
    async def calendar(request: Request, start: datetime, end: datetime, user: CurrentUser):
        service = service_of(request)
        window(start, end, 42)
        blocks = await service.repo.blocks_between(user.id, start, end)
        return {
            "plans": await plans_between(service, user, start, end),
            "blocks": [block_json(b) for b in sorted(blocks, key=lambda b: b.start_at)[:500]],
        }

    @router.get("/availability")
    async def availability(request: Request, start: datetime, end: datetime, user: CurrentUser):
        service = service_of(request)
        window(start, end, 7)
        draft = EventDraft(title="Check", start_at=start, end_at=end)
        return availability_json(await service.availability(user, draft))

    @router.get("/blocks")
    async def list_blocks(request: Request, user: CurrentUser):
        rules = await service_of(request).repo.list_blocks(user.id)
        return {"blocks": [rule_json(rule) for rule in rules]}

    @router.post("/blocks")
    async def create_block(body: BlockRequest, request: Request, user: CurrentUser):
        service = service_of(request)
        try:
            rule = parse_block(body.text, service.clock(), explicit=True)
        except Clarification as exc:
            raise HTTPException(422, str(exc)) from None
        if not body.confirm:
            return {"saved": False, "block": rule_json(rule)}
        if len(await service.repo.list_blocks(user.id)) >= MAX_RULES:
            raise HTTPException(409, "Busy block limit reached")
        return {"saved": True, "block": rule_json(await service.repo.create_block(user.id, rule))}

    @router.delete("/blocks/{block_id}", status_code=204)
    async def delete_block(block_id: str, request: Request, user: CurrentUser):
        if not await service_of(request).repo.delete_block(user.id, valid_id(block_id)):
            raise HTTPException(404, "Not found")
        return Response(status_code=204)

    @router.patch("/plans/{plan_id}")
    async def patch_plan(plan_id: str, body: PlanPatch, request: Request, user: CurrentUser):
        service = service_of(request)
        # Another user's ID is indistinguishable from one that doesn't exist.
        event = await service.repo.get_event(user.id, valid_id(plan_id))
        if not event:
            raise HTTPException(404, "Not found")
        if event.version != body.version or event.status == Status.CANCELLED:
            raise HTTPException(409, {"reason": "stale", "plan": plan_json(event)})
        changes = {f: getattr(body, f) for f in EDITABLE if f in body.model_fields_set}
        if (changes or body.cancel) and event.group_event_id:
            raise HTTPException(403, "Only the organiser can change a shared plan")
        if changes or body.cancel:
            try:
                draft = EventDraft(
                    **(event.model_dump(include=set(EventDraft.model_fields)) | changes)
                )
            except ValidationError:
                raise HTTPException(422, "Check the title and the start and end times") from None
            event = await service.repo.update_event(user.id, event, draft, body.cancel)
            if not event:
                raise HTTPException(409, {"reason": "stale"})
        if body.status and not body.cancel:
            status = Status(body.status)
            found = await service.availability(user, event, event.id)
            if status == Status.GOING and found.conflicts and not body.force:
                raise HTTPException(
                    409, {"reason": "clash", "availability": availability_json(found)}
                )
            event = (
                await service.repo.save(user.id, event)
                if status == Status.SAVED
                else await service.repo.rsvp(user.id, event, status)
            )
            if not event:
                raise HTTPException(409, {"reason": "stale"})
            await service.group_changed(event.group_event_id)
        return plan_json(event, await service.availability(user, event, event.id))

    # ---- groups: the Mini App opened from a group or one of its cards ----

    def chat_of(code: str) -> int:
        chat_id = parse_chat_code(code)
        if chat_id is None:
            raise HTTPException(404, "Not found")
        return chat_id

    async def group_card(request: Request, user: User, event_id: str, organiser=False):
        hub = service_of(request).hub
        found = hub.organised if organiser else hub.event_for
        return hub, await hub_call(found(user, valid_id(event_id)))

    @router.get("/groups/{code}")
    async def group_overview(code: str, request: Request, user: CurrentUser):
        return await hub_call(service_of(request).hub.overview(user, chat_of(code)))

    @router.post("/groups/{code}/events")
    async def create_group_event(
        code: str, body: GroupEventForm, request: Request, user: CurrentUser
    ):
        fields = body.model_dump(include={"title", "start_at", "end_at", "location", "notes"})
        return await hub_call(
            service_of(request).hub.create_event(
                user, chat_of(code), body.request_id, fields, body.weekdays, body.until
            )
        )

    @router.post("/groups/{code}/polls")
    async def create_group_poll(code: str, body: PollForm, request: Request, user: CurrentUser):
        return await hub_call(
            service_of(request).hub.create_poll(
                user,
                chat_of(code),
                body.request_id,
                body.title,
                body.minutes,
                body.first,
                body.last,
                body.reply_by,
            )
        )

    @router.get("/groups/{code}/stats")
    async def group_stats(code: str, request: Request, user: CurrentUser, days: int = 120):
        if not 1 <= days <= 365:
            raise HTTPException(422, "Choose between 1 and 365 days")
        return await hub_call(service_of(request).hub.stats(user, chat_of(code), days))

    @router.post("/groups/{code}/export")
    async def group_export(code: str, body: ExportRequest, request: Request, user: CurrentUser):
        return await hub_call(service_of(request).hub.export(user, chat_of(code), body.days))

    @router.put("/groups/{code}/detection")
    async def group_detection(
        code: str, body: DetectionRequest, request: Request, user: CurrentUser
    ):
        return await hub_call(service_of(request).hub.set_detection(user, chat_of(code), body.on))

    @router.get("/group-events/{event_id}")
    async def group_event(event_id: str, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id)
        return await hub_call(hub.detail(user, event))

    @router.post("/group-events/{event_id}/rsvp")
    async def group_rsvp(event_id: str, body: RsvpRequest, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id)
        return await hub_call(hub.rsvp(user, event, body.status, body.force))

    @router.post("/group-events/{event_id}/check-in")
    async def group_check_in(event_id: str, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id)
        return await hub_call(hub.check_in(user, event))

    @router.post("/group-events/{event_id}/going-to-all")
    async def group_going_to_all(event_id: str, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id)
        return await hub_call(hub.going_to_all(user, event))

    @router.patch("/group-events/{event_id}")
    async def edit_group_event(
        event_id: str, body: GroupEventPatch, request: Request, user: CurrentUser
    ):
        hub, event = await group_card(request, user, event_id, organiser=True)
        changes = {f: getattr(body, f) for f in EDITABLE if f in body.model_fields_set}
        return await hub_call(hub.edit(user, event, body.version, changes))

    @router.post("/group-events/{event_id}/cancel")
    async def cancel_group_event(
        event_id: str, body: VersionRequest, request: Request, user: CurrentUser
    ):
        hub, event = await group_card(request, user, event_id, organiser=True)
        return await hub_call(hub.cancel(user, event, body.version))

    @router.post("/group-events/{event_id}/series")
    async def group_series(event_id: str, body: SeriesRequest, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id, organiser=True)
        return await hub_call(hub.series(user, event, body.action))

    @router.post("/group-events/{event_id}/nudge")
    async def nudge_group_event(event_id: str, request: Request, user: CurrentUser):
        hub, event = await group_card(request, user, event_id, organiser=True)
        return await hub_call(hub.nudge(user, event))

    @router.put("/group-events/{event_id}/attendance")
    async def mark_group_attendance(
        event_id: str, body: AttendanceRequest, request: Request, user: CurrentUser
    ):
        hub, event = await group_card(request, user, event_id, organiser=True)
        person = valid_id(body.user_id) if body.user_id else None
        return await hub_call(hub.mark(user, event, person, body.present))

    # ---- polls: the page behind a poll card's 📱 Open ----

    @router.get("/polls/{poll_id}")
    async def poll_page(poll_id: str, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.poll_for(user, valid_id(poll_id)))
        return await hub_call(hub.poll_detail(user, poll))

    @router.post("/polls/{poll_id}/join")
    async def poll_join(poll_id: str, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.poll_for(user, valid_id(poll_id)))
        return await hub_call(hub.poll_join(user, poll))

    @router.post("/polls/{poll_id}/vote")
    async def poll_vote(poll_id: str, body: PollVoteRequest, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.poll_for(user, valid_id(poll_id)))
        return await hub_call(hub.poll_vote(user, poll, body.slot, body.answer))

    @router.post("/polls/{poll_id}/suggest")
    async def poll_suggest(poll_id: str, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.organised_poll(user, valid_id(poll_id)))
        return await hub_call(hub.poll_suggest(user, poll))

    @router.post("/polls/{poll_id}/pick")
    async def poll_pick(poll_id: str, body: PollPickRequest, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.organised_poll(user, valid_id(poll_id)))
        return await hub_call(hub.poll_pick(user, poll, body.slot))

    @router.post("/polls/{poll_id}/nudge")
    async def poll_nudge(poll_id: str, request: Request, user: CurrentUser):
        hub = service_of(request).hub
        poll = await hub_call(hub.organised_poll(user, valid_id(poll_id)))
        return await hub_call(hub.poll_nudge(user, poll))

    # Each heatmap reads every participant's schedule: bound how often one person can ask.
    heatmap_limit = RateLimiter(20, 60)

    @router.get("/polls/{poll_id}/heatmap")
    async def poll_heatmap(poll_id: str, request: Request, user: CurrentUser):
        """How many poll participants are free in each half hour. Counts only, never names."""
        service = service_of(request)
        if not heatmap_limit.allow(user.id):
            raise HTTPException(429, "Too many refreshes. Wait a minute and try again.")
        found = await service.polls.heatmap(user.id, valid_id(poll_id))
        # Not a participant and no such poll look the same.
        if found is None:
            raise HTTPException(404, "Not found")
        return found

    @router.get("/polls/{poll_id}/availability")
    async def my_availability(poll_id: str, request: Request, user: CurrentUser):
        """The caller's own grid for a poll: pre-filled from their schedule, never sharing why."""
        if not heatmap_limit.allow(user.id):
            raise HTTPException(429, "Too many refreshes. Wait a minute and try again.")
        found = await service_of(request).polls.my_grid(user.id, valid_id(poll_id))
        if found is None:
            raise HTTPException(404, "Not found")
        return found

    @router.put("/polls/{poll_id}/availability")
    async def save_availability(
        poll_id: str, body: AvailabilityRequest, request: Request, user: CurrentUser
    ):
        if not heatmap_limit.allow(user.id):
            raise HTTPException(429, "Too many requests. Wait a minute and try again.")
        try:
            found = await service_of(request).polls.save_grid(user.id, valid_id(poll_id), body.free)
        except ValueError as exc:
            raise HTTPException(409 if "closed" in str(exc) else 422, str(exc)) from None
        if found is None:
            raise HTTPException(404, "Not found")
        return found

    return router
