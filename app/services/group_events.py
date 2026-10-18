"""Bot-owned event cards in group chats.

The group only ever sees the card and its headcount. Anything derived from a member's
own schedule is returned as a private alert for the person who tapped.
"""

import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from telegram.error import BadRequest, Forbidden

from app.bot.keyboards import (
    attendance_keyboard,
    attendance_panel_keyboard,
    links_keyboard,
    manage_keyboard,
    series_keyboard,
)
from app.bot.messages import Reply, group_card, private_verdict, time_range
from app.schemas.event import SG, EventDraft, GroupEvent, Status
from app.services.app_links import group_link
from app.services.chase import enqueue_nudges
from app.services.date_resolution import Clarification, validate_extraction
from app.services.recurrence import (
    Recurrence,
    first_date,
    parse_recurrence,
    session_dates,
)

log = logging.getLogger(__name__)
EXAMPLE = "/event Dinner Fri 7–9pm at Vivo"
USAGE = f"Reply /event to a message with the plan, or send {EXAMPLE}."
GONE = "This card is no longer available."
ENDED = "This event has ended."
CHECK_IN_OPENS = timedelta(minutes=30)
EXPORT_DAYS = 120
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
RSVP = {
    "go": (Status.GOING, "You're Going."),
    "maybe": (Status.MAYBE, "Marked Maybe."),
    "no": (Status.DECLINED, "Marked Can't make it."),
    "save": (Status.SAVED, "Saved to your /plans."),
}


@dataclass
class GroupResult:
    alert: str | None = None  # shown only to the person who tapped
    card: Reply | None = None  # replaces the bot's own card message
    post: Reply | None = None  # a new message in the group
    group_event_id: str | None = None  # set when `post` is a new card to record
    poll_id: str | None = None  # set when `post` is a new poll card to record
    start: str | None = None  # opens the private chat with this /start parameter
    dm: Reply | None = None  # sent privately to the person who asked
    suggestion_id: str | None = None  # set when `post` is a detection suggestion to record
    adopt_event_id: str | None = None  # the edited message becomes this event's card
    delete_message: bool = False  # remove the message whose button was tapped
    invoice: dict | None = None  # a Telegram Stars invoice to send to the person who asked


def parse_id(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


def dataclass_edit(reply: Reply) -> Reply:
    """Redraw the message the button was on instead of sending a new one."""
    reply.edit = True
    return reply


def clock_date(when: datetime) -> str:
    return f"{when.astimezone(SG):%Y%m%d}"


def member_label(names: dict[str, tuple[str | None, str | None]], user_id: str) -> str:
    name, username = names.get(user_id, (None, None))
    return name or (f"@{username}" if username else "Someone")


class GroupEventService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.repo = scheduling.repo
        self._named: dict[tuple[int, str], tuple[str | None, str | None]] = {}

    def is_ended(self, event: GroupEvent) -> bool:
        return event.status == "ACTIVE" and event.end_at <= self.scheduling.clock()

    async def render(self, event: GroupEvent) -> Reply:
        counts = await self.repo.group_counts(event.id)
        ended = self.is_ended(event)
        went = None
        if ended:
            present = await self.repo.attendees(event.id)
            went = len(present) if present else None
        return group_card(event, counts, went, ended, self.scheduling.settings.event_link(event.id))

    async def remember(self, chat_id: int, user_id: str, name: str | None, username: str | None):
        """Keep the Telegram name of someone who used the bot here, for "Who's going?" and exports."""
        name = " ".join((name or "").split())[:60] or None
        username = (username or "").lstrip("@")[:40] or None
        if not (name or username) or self._named.get((chat_id, user_id)) == (name, username):
            return
        await self.repo.touch_member(chat_id, user_id, name, username)
        if len(self._named) > 20_000:
            self._named.clear()
        self._named[(chat_id, user_id)] = (name, username)

    async def create(
        self,
        telegram_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        reference: datetime,
        name: str | None = None,
        username: str | None = None,
    ) -> GroupResult:
        text = text.strip()
        if not text:
            # Nothing to read: open the form in the app when it's set up.
            form = links_keyboard(
                [("📅 Create an event", group_link(self.scheduling.settings, chat_id, "new"))]
            )
            if form:
                return GroupResult(
                    post=Reply("Fill in the details in the app, or type them: " + EXAMPLE, form)
                )
            return GroupResult(post=Reply(USAGE))
        if len(text) > 4000:
            return GroupResult(post=Reply("That message is too long for an event card."))
        user = await self.repo.ensure_user(telegram_id)
        if not self.scheduling.extraction_limit.allow(telegram_id):
            return GroupResult(alert="You're sending a lot very quickly. Give it a minute.")
        extracted = await self.scheduling.extractor.extract(text, reference, None)
        if extracted.kind == "unknown":
            return GroupResult(post=Reply("I couldn't find an event in that message. " + USAGE))
        await self.remember(chat_id, user.id, name, username)
        try:
            recurrence = parse_recurrence(text, reference)
            if recurrence:
                return await self.create_series(
                    user, chat_id, message_id, text, reference, extracted, recurrence
                )
            draft = validate_extraction(
                extracted,
                text,
                reference,
                default_minutes=self.scheduling.settings.default_event_duration_minutes,
            )
        except Clarification as exc:
            return GroupResult(
                post=Reply(
                    f"I need a bit more to make a card. {exc}\nSend /event with the full plan."
                )
            )
        event = await self.repo.create_group_event(user.id, chat_id, message_id, draft)
        if event.message_id:
            # A retried delivery of the same command: the card is already posted.
            return GroupResult()
        await self.scheduling.safe_action(user.id, "group_event")
        return GroupResult(post=await self.render(event), group_event_id=event.id)

    # ---- recurring events ----

    async def create_series(
        self,
        user,
        chat_id: int,
        message_id: int,
        text: str,
        reference: datetime,
        extracted,
        recurrence: Recurrence,
    ) -> GroupResult:
        if recurrence.until is None:
            raise Clarification(
                'How long does it run? Add "until week 13", "until 30 Nov" or "for 8 weeks".'
            )
        today = reference.astimezone(SG).date().isoformat()
        ambiguity = extracted.ambiguity
        if ambiguity and re.search(r"day|date|every|week|recur|which", ambiguity, re.IGNORECASE):
            ambiguity = None  # the weekdays are exactly what the series is for
        provisional = extracted.model_copy(
            update={"date_text": today, "end_date_text": None, "ambiguity": ambiguity}
        )
        draft = validate_extraction(
            provisional,
            text + "\n" + today,
            reference,
            default_minutes=self.scheduling.settings.default_event_duration_minutes,
        )
        first_event, days = await self.build_series(
            user, chat_id, message_id, draft, recurrence, reference
        )
        if first_event.message_id:
            return GroupResult()  # a retried delivery
        note = (
            f"🔁 {len(days)} sessions, every {recurrence.label()} until {days[-1]:%a %-d %b}."
            " Each session's card is posted a few days before it."
        )
        card = await self.render(first_event)
        return GroupResult(
            post=Reply(card.text + "\n\n" + note, card.keyboard),
            group_event_id=first_event.id,
        )

    async def build_series(
        self,
        user,
        chat_id: int,
        message_id: int,
        draft: EventDraft,
        recurrence: Recurrence,
        reference: datetime,
    ) -> tuple[GroupEvent, list]:
        """Store a series and every session of it, at the draft's time of day and length.

        Repeating the call returns the same rows. Returns (first session, session dates)."""
        if recurrence.until is None:
            raise Clarification("How long does it run? Choose an end date.")
        start_time, length = (
            draft.start_at.timetz().replace(tzinfo=None),
            draft.end_at - draft.start_at,
        )
        # A plan forwarded days ago must not produce sessions that are already over.
        first = first_date(recurrence.weekdays, start_time, max(reference, self.scheduling.clock()))
        days = session_dates(recurrence.weekdays, first, recurrence.until)
        end_time = (datetime.combine(first, start_time) + length).time()
        series = await self.repo.create_series(
            user.id,
            chat_id,
            message_id,
            {
                "title": draft.title,
                "weekdays": recurrence.weekdays,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "location": draft.location,
                "notes": draft.notes,
                "first_date": first.isoformat(),
                "until_date": days[-1].isoformat(),
            },
        )
        first_event = None
        for day in days:
            start = datetime.combine(day, start_time, SG)
            session = EventDraft(
                title=draft.title,
                start_at=start,
                end_at=start + length,
                location=draft.location,
                notes=draft.notes,
                assumed_duration=draft.assumed_duration,
            )
            created = await self.repo.create_session(
                series, day, session, message_id if first_event is None else None
            )
            first_event = first_event or created
        await self.scheduling.safe_action(user.id, "group_series")
        return first_event, days

    async def series_action(self, user, data: str) -> list[Reply]:
        """Organiser buttons sent after changing one session: apply to the rest, or stop."""
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        event = await self.repo.get_group_event(event_id) if event_id else None
        if not event or not event.series_id or event.creator_user_id != user.id:
            return [Reply("Only the organiser can change a series.")]
        series = await self.repo.get_series(event.series_id)
        now = self.scheduling.clock()
        # Sessions already held stay as they were: changing them would rewrite attendance stats.
        later = [
            s
            for s in await self.repo.series_sessions(event.series_id)
            if s.occurrence_date
            and event.occurrence_date
            and s.occurrence_date > event.occurrence_date
            and s.status == "ACTIVE"
            and s.end_at > now
        ]
        if parts[1] == "stop":
            keys = []
            for session in later:
                result = await self.repo.update_group_event(user.id, session, None, True)
                if result:
                    keys.append(f"{result.id}:{result.version}")
                    await self.scheduling.group_changed(session.id)
            await self.repo.update_series(series.id, status="STOPPED")
            await self.summarise_series_change(
                series.id,
                keys,
                f"❌ {event.title}: all sessions after {event.start_at:%a %-d %b} are cancelled."
                " If you added them to your calendar, delete them there too.",
            )
            return [Reply(f"Series stopped. {len(keys)} later session(s) cancelled.")]
        if parts[1] != "all" or event.status != "ACTIVE":
            return [Reply("That button isn't valid.")]
        length = event.end_at - event.start_at
        changed, keys = [], []
        for session in later:
            if session.edited_alone:
                continue
            start = datetime.combine(session.occurrence_date, event.start_at.timetz(), SG)
            draft = EventDraft(
                title=event.title,
                start_at=start,
                end_at=start + length,
                location=event.location,
                notes=event.notes,
                assumed_duration=event.assumed_duration,
            )
            if (
                session.title,
                session.start_at,
                session.end_at,
                session.location,
                session.notes,
            ) == (
                draft.title,
                draft.start_at,
                draft.end_at,
                draft.location,
                draft.notes,
            ):
                continue
            result = await self.repo.update_group_event(user.id, session, draft, False)
            if result:
                changed.append(session.id)
                keys.append(f"{result.id}:{result.version}")
                await self.scheduling.group_changed(session.id)
        # Applying a series-wide change is not "edited on its own": later bulk changes still
        # reach them, and the session it was applied from now matches the series too.
        await self.repo.clear_edited([event.id, *changed])
        await self.repo.update_series(
            series.id,
            title=event.title,
            start_time=event.start_at.timetz().replace(tzinfo=None).isoformat(),
            end_time=event.end_at.timetz().replace(tzinfo=None).isoformat(),
            location=event.location,
            notes=event.notes,
        )
        where = f" at {event.location[:80]}" if event.location else ""
        await self.summarise_series_change(
            series.id,
            keys,
            f"🔔 {event.title} changed from {event.start_at:%a %-d %b} on: now"
            f" {time_range(event.start_at, event.end_at)}{where}. Check /plans for each session.",
        )
        return [
            Reply(
                f"Updated {len(changed)} later session(s). Sessions you changed on their own were"
                " left alone. Members who answered are told what changed."
            )
        ]

    async def summarise_series_change(self, series_id: str, keys: list[str], text: str):
        """One message per member for a change to many sessions, instead of one per session."""
        if keys:
            people = await self.repo.drop_pending_notifications("CHANGE", keys)
            stamp = int(self.scheduling.clock().timestamp())
            for user_id in sorted(set(people)):
                await self.repo.enqueue_notification(
                    user_id, "CHANGE", f"series:{series_id}:{stamp}", {"series_text": text}
                )
        await self.scheduling.flush_notifications()

    def series_followup(self, result: GroupEvent, cancelled: bool) -> list[Reply]:
        if not result.series_id:
            return []
        text = (
            "This was one session of a recurring event. Cancel the rest too?"
            if cancelled
            else "This was one session of a recurring event. Change the later sessions to match?"
        )
        return [Reply(text, series_keyboard(result, cancelled))]

    async def post_due_sessions(self, now: datetime, ahead: timedelta = timedelta(days=4)) -> int:
        """Post the card of any session starting soon whose card is not in the group yet."""
        post = getattr(self.scheduling, "post_group_card", None)
        if post is None:
            return 0
        posted = 0
        for session in await self.repo.unposted_sessions(now, now + ahead):
            try:
                message_id = await post(session.chat_id, await self.render(session))
            except (Forbidden, BadRequest) as exc:
                # The bot was removed from the group, or the chat is gone: stop trying. -1 marks
                # the session as handled, and is never chased, asked about or edited.
                log.warning("series_card_post_refused error_type=%s", type(exc).__name__)
                await self.repo.set_group_message(session.id, -1)
                continue
            except Exception as exc:
                log.warning("series_card_post_failed error_type=%s", type(exc).__name__)
                continue
            if message_id:
                # Posting is when the group first sees the session: chase timing starts here.
                await self.repo.set_group_message(session.id, message_id, posted_at=now)
                posted += 1
        return posted

    # ---- "still coming?", ended cards ----

    async def enqueue_confirmations(self, now: datetime) -> int:
        """Ask everyone marked Going, the day before, whether they are still coming."""
        queued = 0
        for event in await self.repo.group_events_starting(
            now + timedelta(hours=4), now + timedelta(hours=30)
        ):
            if not event.message_id or event.message_id < 0:
                continue
            answers = await self.repo.group_answers(event.id)
            key = f"{event.id}:{int(event.start_at.timestamp())}"
            for user_id, status in sorted(answers.items()):
                if status == Status.GOING.value and user_id != event.creator_user_id:
                    queued += await self.repo.enqueue_notification(
                        user_id, "CONFIRM", key, {"type": "ask", "id": event.id}
                    )
        return queued

    async def sweep_ended(self, now: datetime) -> int:
        """Redraw finished cards once, without buttons, with how many went."""
        done = 0
        for event in await self.repo.ended_group_events(now):
            await self.repo.mark_ended(event.id, now)
            await self.scheduling.group_changed(event.id)
            done += 1
        return done

    async def confirm_attendance(self, telegram_id: int, data: str) -> list[Reply]:
        """A tap on the private "still coming?" message."""
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        event = await self.repo.get_group_event(event_id) if event_id else None
        user = await self.repo.ensure_user(telegram_id)
        linked = await self.repo.linked_event(user.id, event.id) if event else None
        if not event or not linked or event.status != "ACTIVE" or parts[1] not in ("y", "u", "n"):
            return [Reply("That check isn't available any more.")]
        if self.is_ended(event):
            return [Reply(ENDED)]
        when = f"{event.start_at:%a %-d %b}, {time_range(event.start_at, event.end_at)}"
        if parts[1] == "y":
            if linked.status != Status.GOING:
                await self.repo.group_rsvp(user.id, event, Status.GOING)
            await self.repo.set_attendance_check(event.id, user.id, "CONFIRMED")
            text = f"✅ See you there: {event.title}, {when}."
        elif parts[1] == "u":
            await self.repo.set_attendance_check(event.id, user.id, "UNSURE")
            text = f"Noted: unsure about {event.title}, {when}. Tap again once you know."
        else:
            await self.repo.group_rsvp(user.id, event, Status.DECLINED)
            await self.repo.set_attendance_check(event.id, user.id, "DROPPED")
            text = f"Got it: marked Can't make it for {event.title}. Thanks for telling the group."
        await self.scheduling.group_changed(event.id)
        keyboard = attendance_keyboard(event) if parts[1] == "u" else None
        return [Reply(text, keyboard, edit=True)]

    async def attendance_summary(self, event: GroupEvent) -> dict[str, int]:
        answers = await self.repo.group_answers(event.id)
        checks = await self.repo.attendance_checks(event.id)
        going = {u for u, s in answers.items() if s == Status.GOING.value}
        confirmed = sum(1 for u in going if checks.get(u) == "CONFIRMED")
        unsure = sum(1 for u in going if checks.get(u) == "UNSURE")
        dropped = sum(1 for u, a in checks.items() if a == "DROPPED" and u not in going)
        return {
            "going": len(going),
            "confirmed": confirmed,
            "unsure": unsure,
            "dropped": dropped,
            "no_reply": len(going) - confirmed - unsure,
        }

    # ---- reports: stats and export ----

    async def is_pro(self, chat_id: int) -> bool:
        if chat_id in self.scheduling.settings.pro_chats():
            return True
        return await self.repo.group_tier(chat_id, self.scheduling.clock()) == "PRO"

    async def can_export(self, chat_id: int) -> bool:
        """Free for everyone unless EXPORT_REQUIRES_PRO is set."""
        if not self.scheduling.settings.export_requires_pro:
            return True
        return await self.is_pro(chat_id)

    async def report(self, user, chat_id: int, days: int):
        since = self.scheduling.clock() - timedelta(days=days)
        events = await self.repo.chat_events_by(chat_id, user.id, since)
        names = await self.repo.member_names(chat_id)
        rows, series = [], {}
        held = cancelled = 0
        going_total = came_total = 0
        for event in events:
            if event.status == "CANCELLED":
                cancelled += 1
                continue
            answers = await self.repo.group_answers(event.id)
            checks = await self.repo.attendance_checks(event.id)
            present = await self.repo.attendees(event.id)
            ended = event.end_at <= self.scheduling.clock()
            going = {u for u, s in answers.items() if s == Status.GOING.value}
            came = going & present
            if ended:
                held += 1
                going_total += len(going)
                came_total += len(came)
                entry = series.setdefault(event.series_id or event.id, [event.title, 0, 0, 0])
                entry[1] += 1
                entry[2] += len(going)
                entry[3] += len(came)
            for person in sorted(set(answers) | present):
                name, username = names.get(person, (None, None))
                rows.append(
                    {
                        "event": event.title,
                        "date": f"{event.start_at:%Y-%m-%d}",
                        "time": f"{event.start_at:%H:%M}-{event.end_at:%H:%M}",
                        "name": name or "",
                        "username": f"@{username}" if username else "",
                        "rsvp": {
                            "GOING": "Going",
                            "MAYBE": "Maybe",
                            "DECLINED": "Can't",
                            "SAVED": "Saved",
                        }.get(answers.get(person, ""), ""),
                        "still_coming": {
                            "CONFIRMED": "Confirmed",
                            "UNSURE": "Unsure",
                            "DROPPED": "Dropped",
                        }.get(checks.get(person, ""), ""),
                        "attended": ("Yes" if person in present else ("No" if ended else "")),
                    }
                )
        return rows, series, held, cancelled, going_total, came_total

    @staticmethod
    def turnout(going: int, came: int) -> str:
        return f"{round(100 * came / going)}%" if going else "n/a"

    async def stats(self, telegram_id: int, chat_id: int) -> GroupResult:
        user = await self.repo.ensure_user(telegram_id)
        rows, series, held, cancelled, going, came = await self.report(user, chat_id, EXPORT_DAYS)
        if not held:
            return GroupResult(
                post=Reply(
                    "No finished events of yours in this group yet. Stats appear after one ends."
                )
            )
        text = (
            f"📊 Turnout, last {EXPORT_DAYS} days (your events in this group)\n"
            f"Sessions held: {held} · cancelled: {cancelled}\n"
            f"Going {going} · came {came} · turnout {self.turnout(going, came)}\n"
            "Counts people who tapped I'm here, so it is only as complete as check-in."
        )
        if await self.can_export(chat_id):
            lines = [
                f"• {title[:40]}: {n} session(s), turnout {self.turnout(g, c)}"
                for title, n, g, c in series.values()
            ]
            text += "\n\nBy event:\n" + "\n".join(lines[:15])
        else:
            text += "\n\nPer-event breakdown and CSV export are part of Clubs Pro. See /pro."
        return GroupResult(
            post=Reply("📬 I sent you your turnout stats privately."), dm=Reply(text)
        )

    async def export(self, telegram_id: int, chat_id: int, args: str = "") -> GroupResult:
        if not await self.can_export(chat_id):
            return GroupResult(
                post=Reply(
                    "Attendance export is part of Clubs Pro. /stats shows turnout for free."
                    " /pro shows how to upgrade this group."
                )
            )
        user = await self.repo.ensure_user(telegram_id)
        digits = args.strip().split()[0][:4] if args.strip() else ""
        days = min(int(digits), 365) if digits.isdigit() and int(digits) > 0 else EXPORT_DAYS
        rows, _, held, cancelled, going, came = await self.report(user, chat_id, days)
        if not rows:
            return GroupResult(
                post=Reply(f"No events of yours in this group in the last {days} days.")
            )
        buffer = io.StringIO()
        columns = ["event", "date", "time", "name", "username", "rsvp", "still_coming", "attended"]
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            # A name starting with = or + would run as a formula when opened in a spreadsheet.
            writer.writerow(
                {k: ("'" + v if v.startswith(FORMULA_START) else v) for k, v in row.items()}
            )
        content = ("\ufeff" + buffer.getvalue()).encode()
        caption = (
            f"Attendance, last {days} days · {held} held · turnout {self.turnout(going, came)}"
        )
        return GroupResult(
            post=Reply("📬 I sent you the attendance file privately."),
            dm=Reply(
                caption, document=(f"attendance-{clock_date(self.scheduling.clock())}.csv", content)
            ),
        )

    # ---- the organiser marks who came ----

    @staticmethod
    def hex_id(event_id: str) -> str:
        return event_id.replace("-", "")

    @staticmethod
    def from_hex(value: str) -> str | None:
        try:
            return str(UUID(hex=value))
        except ValueError:
            return None

    async def people_for(self, event: GroupEvent) -> tuple[list[str], dict[str, str], set[str]]:
        """Everyone who answered or checked in: Going first, then by name."""
        answers = await self.repo.group_answers(event.id)
        present = await self.repo.attendees(event.id)
        names = await self.repo.member_names(event.chat_id)
        labels = {u: member_label(names, u) for u in set(answers) | present}
        order = {"GOING": 0, "MAYBE": 1, "SAVED": 2, "DECLINED": 3}
        people = sorted(
            labels, key=lambda u: (order.get(answers.get(u, ""), 4), labels[u].lower(), u)
        )
        return people, labels, present

    async def attendance_panel(self, event: GroupEvent, page: int = 0) -> Reply:
        people, labels, present = await self.people_for(event)
        when = f"{event.start_at:%a %-d %b}, {time_range(event.start_at, event.end_at)}"
        if not people:
            return Reply(f"{event.title} · {when}\nNobody has answered or checked in yet.")
        pages = max(1, -(-len(people) // 16))
        page = max(0, min(page, pages - 1))
        digest = hashlib.sha1("".join(people).encode()).hexdigest()[:4]
        going = await self.repo.group_answers(event.id)
        text = (
            f"📋 {event.title} · {when}\nTap a name to mark who came."
            f"\nCame: {len(present)} · said Going: "
            f"{sum(1 for s in going.values() if s == Status.GOING.value)}"
        )
        keyboard = attendance_panel_keyboard(
            self.hex_id(event.id),
            [(labels[u], u in present) for u in people],
            digest,
            page,
            pages,
        )
        return Reply(text, keyboard)

    async def attendance_command(self, telegram_id: int, chat_id: int) -> GroupResult:
        """/attendance in a group: the organiser's latest event that has started, sent privately."""
        user = await self.repo.ensure_user(telegram_id)
        now = self.scheduling.clock()
        mine = await self.repo.chat_events_by(chat_id, user.id, now - timedelta(days=30))
        started = [e for e in mine if e.status == "ACTIVE" and e.start_at <= now]
        if not started:
            return GroupResult(
                post=Reply("You have no event that has started in the last 30 days in this group.")
            )
        event = started[-1]
        return GroupResult(
            post=Reply("📬 I sent you the attendance list privately."),
            dm=await self.attendance_panel(event),
        )

    async def attendance_action(self, user, data: str) -> list[Reply]:
        """at:<event>:<index>:<digest> toggles one person; ap: changes page; aa: marks all Going."""
        parts = data.split(":")
        event_id = self.from_hex(parts[1]) if len(parts) >= 2 else None
        event = await self.repo.get_group_event(event_id) if event_id else None
        if not event or event.creator_user_id != user.id:
            return [Reply("Only the organiser can mark attendance.")]
        kind = parts[0]
        people, _, present = await self.people_for(event)
        if kind == "ap" and len(parts) == 3 and parts[2].isdigit():
            return [dataclass_edit(await self.attendance_panel(event, int(parts[2])))]
        if kind == "aa":
            answers = await self.repo.group_answers(event.id)
            for person in people:
                if answers.get(person) == Status.GOING.value:
                    await self.repo.mark_attendance(event.id, person, True, user.id)
        elif kind == "at" and len(parts) == 4 and parts[2].isdigit():
            digest = hashlib.sha1("".join(people).encode()).hexdigest()[:4]
            index = int(parts[2])
            if parts[3] != digest or index >= len(people):
                # The list changed since this panel was drawn: show the fresh one.
                return [dataclass_edit(await self.attendance_panel(event))]
            person = people[index]
            await self.repo.mark_attendance(event.id, person, person not in present, user.id)
        else:
            return [Reply("That button isn't valid.")]
        await self.scheduling.group_changed(event.id)
        page = int(parts[2]) // 16 if kind == "at" else 0
        return [dataclass_edit(await self.attendance_panel(event, page))]

    async def begin_change(self, user, payload: str) -> list[Reply]:
        """Handle /start edit_<id> or cancel_<id>, reached from the card's organiser buttons."""
        action, _, raw = payload.partition("_")
        event_id = parse_id(raw)
        event = await self.repo.get_group_event(event_id) if event_id else None
        if not event or event.creator_user_id != user.id:
            return [Reply("Only the organiser can change that card.")]
        if event.status != "ACTIVE":
            return [Reply("That event was already cancelled.")]
        return await self.scheduling.begin_change(user, event, cancel=action == "cancel")

    async def manage_panel(self, user, payload: str) -> list[Reply]:
        """Handle /start manage_<id>: the organiser's private menu for one card."""
        event_id = parse_id(payload.partition("_")[2])
        event = await self.repo.get_group_event(event_id) if event_id else None
        if not event or event.creator_user_id != user.id:
            return [Reply("Only the organiser can manage that event.")]
        when = f"{event.start_at:%a %-d %b}, {time_range(event.start_at, event.end_at)}"
        state = (
            "This event was cancelled." if event.status != "ACTIVE" else "What do you want to do?"
        )
        return [Reply(f"⚙️ {event.title} · {when}\n{state}", manage_keyboard(event))]

    async def manage_action(self, user, data: str) -> list[Reply]:
        """m:<action>:<event> from the organiser's private menu."""
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        event = await self.repo.get_group_event(event_id) if event_id else None
        if not event or event.creator_user_id != user.id:
            return [Reply("Only the organiser can manage that event.")]
        action = parts[1]
        if event.status != "ACTIVE":
            return [Reply("That event was cancelled.")]
        if action in ("edit", "cancel"):
            return await self.scheduling.begin_change(user, event, cancel=action == "cancel")
        if action == "status":
            return [Reply(await self.status_text(event))]
        if action == "att":
            return [await self.attendance_panel(event)]
        if action == "nudge":
            if self.is_ended(event):
                return [Reply(ENDED)]
            return [Reply(await self.nudge(event))]
        return [Reply("That button isn't valid.")]

    async def nudge(self, card) -> str:
        queued = await enqueue_nudges(self.repo, card, self.scheduling.clock())
        await self.scheduling.flush_notifications()
        if not queued:
            return "Nobody left to nudge. Each member is nudged at most once per card."
        return (
            f"Nudging {queued} member{'s' if queued != 1 else ''} privately. People who have"
            " never used AmIFree in this group, or turned reminders off, aren't messaged."
        )

    async def from_private(self, telegram_id: int, data: str) -> list[Reply]:
        """An RSVP tapped on a private nudge. Only people already seen in that group may use it."""
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        event = await self.repo.get_group_event(event_id) if event_id else None
        user = await self.repo.ensure_user(telegram_id)
        members = await self.repo.group_member_ids(event.chat_id) if event else []
        if not event or user.id not in members or parts[1] not in RSVP:
            return [Reply("That card isn't available to you.")]
        result = await self.callback(telegram_id, event.chat_id, data)
        if result.card:
            await self.scheduling.group_changed(event.id)
        return [Reply(result.alert or GONE)]

    async def posted(self, group_event_id: str, message_id: int):
        await self.repo.set_group_message(group_event_id, message_id)

    async def callback(
        self,
        telegram_id: int,
        chat_id: int,
        data: str,
        name: str | None = None,
        username: str | None = None,
    ) -> GroupResult:
        parts = data.split(":")
        event_id = parse_id(parts[2]) if len(parts) == 3 else None
        if not event_id:
            return GroupResult(alert="That button isn't valid.")
        event = await self.repo.get_group_event(event_id)
        # A card only works in the chat it was posted to.
        if not event or event.chat_id != chat_id:
            return GroupResult(alert=GONE)
        user = await self.repo.ensure_user(telegram_id)
        await self.repo.touch_member(chat_id, user.id)
        await self.remember(chat_id, user.id, name, username)
        if event.status == "CANCELLED":
            return GroupResult(alert="This event was cancelled.", card=await self.render(event))
        action = parts[1]
        organiser = event.creator_user_id == user.id
        if action == "manage":
            if not organiser:
                return GroupResult(alert="Only the organiser can manage this event.")
            return GroupResult(start=f"manage_{event.id}")
        if action in ("edit", "cancel"):
            if not organiser:
                return GroupResult(alert="Only the organiser can change this card.")
            # Editing happens in the organiser's private chat, then updates this card.
            return GroupResult(start=f"{action}_{event.id}")
        if action == "nudge":
            if not organiser:
                return GroupResult(alert="Only the organiser can nudge members.")
            if self.is_ended(event):
                return GroupResult(alert=ENDED, card=await self.render(event))
            return GroupResult(alert=await self.nudge(event))
        if action == "status":
            if not organiser:
                return GroupResult(alert="Only the organiser can see this.")
            return GroupResult(alert=await self.status_text(event))
        if action == "who":
            return GroupResult(alert=await self.who_text(event))
        if action == "here":
            return await self.check_in(user, event)
        if self.is_ended(event):
            return GroupResult(alert=ENDED, card=await self.render(event))
        linked = await self.repo.linked_event(user.id, event.id)
        availability = await self.scheduling.availability(
            user, event, linked.id if linked else None
        )
        if action == "free":
            return GroupResult(alert=private_verdict(availability))
        if action == "goall":
            return await self.going_to_all(user, event)
        if action not in RSVP:
            return GroupResult(alert="That action isn't available.")
        status, text = RSVP[action]
        if not await self.repo.group_rsvp(user.id, event, status):
            return GroupResult(alert=GONE)
        if status in (Status.GOING, Status.SAVED):
            text = private_verdict(availability, text)
        return GroupResult(alert=text, card=await self.render(event))

    async def who_text(self, event: GroupEvent) -> str:
        answers = await self.repo.group_answers(event.id)
        going = [u for u, s in answers.items() if s == Status.GOING.value]
        if not going:
            return "Nobody has said Going yet."
        names = await self.repo.member_names(event.chat_id)
        labels = sorted((member_label(names, u) for u in going), key=str.lower)
        head, shown = f"Going ({len(labels)}): ", []
        for index, text in enumerate(labels):
            left = len(labels) - index
            if len(head) + len(", ".join([*shown, text])) + len(f" + {left} more") > 190:
                return head + ", ".join(shown) + f" + {left} more"
            shown.append(text)
        return head + ", ".join(shown)

    async def status_text(self, event: GroupEvent) -> str:
        counts = await self.repo.group_counts(event.id)
        line = (
            f"Going {counts.get('GOING', 0)} · Maybe {counts.get('MAYBE', 0)}"
            f" · Can't {counts.get('DECLINED', 0)}"
        )
        now = self.scheduling.clock()
        if event.start_at - now > timedelta(hours=30):
            return line + "\nThe still-coming check goes out the day before."
        summary = await self.attendance_summary(event)
        return (
            line
            + f"\n{summary['confirmed']} confirmed · {summary['unsure']} unsure"
            + f" · {summary['dropped']} dropped · {summary['no_reply']} no reply"
        )

    async def check_in(self, user, event: GroupEvent) -> GroupResult:
        now = self.scheduling.clock()
        if now >= event.end_at:
            return GroupResult(alert=ENDED, card=await self.render(event))
        if now < event.start_at - CHECK_IN_OPENS:
            return GroupResult(alert="Check-in opens 30 minutes before the start.")
        await self.repo.check_in(event.id, user.id, user.id)
        return GroupResult(alert="Checked in ✅ Thanks for coming.")

    async def going_to_all(self, user, event: GroupEvent) -> GroupResult:
        if not event.series_id:
            return GroupResult(alert="This isn't a recurring event.")
        now = self.scheduling.clock()
        count = kept = 0
        for session in await self.repo.series_sessions(event.series_id):
            if session.status != "ACTIVE" or session.end_at <= now:
                continue
            mine = await self.repo.linked_event(user.id, session.id)
            if mine and mine.status == Status.DECLINED and session.id != event.id:
                kept += 1  # a Can't given for one session is a deliberate answer
                continue
            if await self.repo.group_rsvp(user.id, session, Status.GOING):
                count += 1
                if session.id != event.id:
                    await self.scheduling.group_changed(session.id)
        note = f" Kept your Can't for {kept}." if kept else ""
        return GroupResult(
            alert=f"You're Going to {count} session{'s' if count != 1 else ''}.{note} "
            "Use Am I free? on a card to check one.",
            card=await self.render(event),
        )
