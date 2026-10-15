"""The group-feature repository methods against real PostgreSQL (migration 009)."""

import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions

from app.db.supabase import SupabaseRepository
from app.schemas.event import SG, EventDraft, Status
from tests.sql_transport import ROOT, SQLGateway

pytestmark = pytest.mark.skipif(
    not shutil.which("node")
    or not (ROOT / "scripts/sql-tests/node_modules/@electric-sql/pglite").exists(),
    reason="Install optional SQL test engine: npm --prefix scripts/sql-tests ci",
)
NOW = datetime(2026, 10, 5, 12, tzinfo=SG)
SERIES = {
    "title": "Training",
    "weekdays": [1, 3],
    "start_time": "19:00:00",
    "end_time": "21:00:00",
    "location": "UTSRC",
    "notes": None,
    "first_date": "2026-10-06",
    "until_date": "2026-10-15",
}


@asynccontextmanager
async def database():
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        yield db, SupabaseRepository(client)


def draft(day: date) -> EventDraft:
    start = datetime.combine(day, time(19), SG)
    return EventDraft(title="Training", start_at=start, end_at=start + timedelta(hours=2))


async def test_series_sessions_and_single_session_edits():
    async with database() as (db, repo):
        organiser, member = await repo.ensure_user(1), await repo.ensure_user(2)
        series = await repo.create_series(organiser.id, -500, 40, SERIES)
        again = await repo.create_series(organiser.id, -500, 40, SERIES)
        assert again.id == series.id and series.weekdays == [1, 3]
        assert series.start_time == time(19) and series.first_date == date(2026, 10, 6)
        days = [date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 13), date(2026, 10, 15)]
        created = [
            await repo.create_session(series, d, draft(d), 40 if d == days[0] else None)
            for d in days
        ]
        # The same date again returns the same row rather than a second session.
        assert (await repo.create_session(series, days[1], draft(days[1]))).id == created[1].id
        found = await repo.series_sessions(series.id)
        assert [g.occurrence_date for g in found] == days
        assert all(g.series_id == series.id and not g.edited_alone for g in found)

        # Sessions without a card are not "posted"; once a card exists it is skipped.
        soon = await repo.unposted_sessions(NOW, NOW + timedelta(days=4))
        assert [g.occurrence_date for g in soon] == [days[0], days[1]]
        await repo.set_group_message(created[0].id, 77)
        assert [g.id for g in await repo.unposted_sessions(NOW, NOW + timedelta(days=4))] == [
            created[1].id
        ]

        # Changing one session marks only that one as edited on its own.
        moved = draft(days[1]).model_copy(
            update={
                "start_at": datetime(2026, 10, 8, 20, tzinfo=SG),
                "end_at": datetime(2026, 10, 8, 22, tzinfo=SG),
            }
        )
        changed = await repo.update_group_event(organiser.id, created[1], moved)
        assert changed.edited_alone is True and changed.start_at.hour == 20
        rows = {g.id: g for g in await repo.series_sessions(series.id)}
        assert rows[created[0].id].edited_alone is False
        await repo.clear_edited([created[1].id])
        assert (await repo.get_group_event(created[1].id)).edited_alone is False
        await repo.update_series(series.id, status="STOPPED", title="Training v2")
        stored = await repo.get_series(series.id)
        assert stored.status == "STOPPED" and stored.title == "Training v2"
        # Deleting the organiser keeps the series for the others.
        await repo.delete_user(organiser.id)
        assert (await repo.get_series(series.id)).creator_user_id is None
        assert member.id


async def test_attendance_checks_and_names():
    async with database() as (db, repo):
        organiser, member, other = [await repo.ensure_user(n) for n in (1, 2, 3)]
        event = await repo.create_group_event(organiser.id, -500, 41, draft(date(2026, 10, 9)))
        await repo.group_rsvp(member.id, event, Status.GOING)
        await repo.group_rsvp(other.id, event, Status.MAYBE)
        assert await repo.group_answers(event.id) == {member.id: "GOING", other.id: "MAYBE"}
        await repo.set_attendance_check(event.id, member.id, "UNSURE")
        await repo.set_attendance_check(event.id, member.id, "CONFIRMED")  # an upsert
        assert await repo.attendance_checks(event.id) == {member.id: "CONFIRMED"}
        await repo.check_in(event.id, member.id, member.id)
        await repo.check_in(event.id, member.id, member.id)
        assert await repo.attendees(event.id) == {member.id}

        await repo.touch_member(-500, member.id, "Wei Ling", "weiling")
        await repo.touch_member(-500, other.id)
        names = await repo.member_names(-500)
        assert names[member.id] == ("Wei Ling", "weiling") and names[other.id] == (None, None)
        # Everything personal goes when the person is deleted.
        await repo.delete_user(member.id)
        assert await db.query("select * from attendance") == []
        assert await db.query("select * from attendance_checks") == []
        assert member.id not in await repo.member_names(-500)


async def test_ended_cards_are_found_once():
    async with database() as (db, repo):
        organiser = await repo.ensure_user(1)
        past = datetime(2026, 10, 1, 19, tzinfo=SG)
        event = await repo.create_group_event(
            organiser.id,
            -500,
            42,
            EventDraft(title="Dinner", start_at=past, end_at=past + timedelta(hours=2)),
        )
        # No card in the group yet: nothing to redraw.
        assert await repo.ended_group_events(NOW) == []
        await repo.set_group_message(event.id, 9)
        assert [g.id for g in await repo.ended_group_events(NOW)] == [event.id]
        await repo.mark_ended(event.id, NOW)
        assert await repo.ended_group_events(NOW) == []
        # Older than a week is left alone.
        assert await repo.ended_group_events(NOW + timedelta(days=30)) == []
        found = await repo.group_events_starting(
            past - timedelta(hours=1), past + timedelta(hours=1)
        )
        assert [g.id for g in found] == [event.id]


async def test_plans_settings_suggestions_and_poll_deadlines():
    async with database() as (db, repo):
        organiser = await repo.ensure_user(1)
        assert await repo.group_tier(-500, NOW) == "FREE"
        await repo.set_group_tier(-500, "PRO", NOW + timedelta(days=30))
        assert await repo.group_tier(-500, NOW) == "PRO"
        assert await repo.group_tier(-500, NOW + timedelta(days=31)) == "FREE"
        await repo.set_group_tier(-500, "FREE")
        assert await repo.group_tier(-500, NOW) == "FREE"

        assert await repo.detection_enabled(-500) is False
        await repo.set_detection(-500, True, 1)
        assert await repo.detection_enabled(-500) is True
        await repo.set_detection(-500, False, 1)
        assert await repo.detection_enabled(-500) is False

        first = await repo.create_suggestion(-500, 60, draft(date(2026, 10, 9)))
        assert first and await repo.create_suggestion(-500, 60, draft(date(2026, 10, 9))) is None
        await repo.set_suggestion_message(first.id, 71)
        loaded = await repo.get_suggestion(first.id)
        assert loaded.message_id == 71 and loaded.draft["title"] == "Training"
        assert await repo.expired_suggestions(NOW - timedelta(days=3650)) == []
        assert [
            s.id for s in await repo.expired_suggestions(datetime.now(SG) + timedelta(hours=1))
        ] == [first.id]
        await repo.delete_suggestion(first.id)
        assert await repo.get_suggestion(first.id) is None

        start = datetime(2026, 10, 12, tzinfo=SG)
        deadline = datetime(2026, 10, 6, 23, 59, tzinfo=SG)
        poll = await repo.create_poll(
            organiser.id, -500, 61, "Sync", 60, start, start + timedelta(days=1), deadline
        )
        assert poll.deadline_at == deadline
        assert await repo.polls_past_deadline(deadline - timedelta(hours=1)) == []
        assert [p.id for p in await repo.polls_past_deadline(deadline)] == [poll.id]
        # A retried command keeps the first deadline.
        again = await repo.create_poll(
            organiser.id, -500, 61, "Sync", 60, start, start + timedelta(days=1), NOW
        )
        assert again.deadline_at == deadline


async def test_large_groups_are_read_in_full():
    """PostgREST answers at most 1,000 rows at once; a bigger group must not be cut."""
    async with database() as (db, repo):
        organiser = await repo.ensure_user(1)
        event = await repo.create_group_event(organiser.id, -700, 80, draft(date(2026, 10, 9)))
        await db.query(
            "insert into users(telegram_user_id) select 100000+n from generate_series(1,1300) n"
        )
        await db.query(
            "insert into group_members(chat_id,user_id) select -700,id from users"
            " where telegram_user_id>100000"
        )
        await db.query(
            "insert into events(user_id,group_event_id,title,start_at,end_at,status)"
            " select id,$1,'x',now(),now()+interval '1 hour','GOING' from users"
            " where telegram_user_id>100000",
            [event.id],
        )
        assert len(await repo.group_member_ids(-700)) == 1301  # 1,300 plus the organiser
        answers = await repo.group_answers(event.id)
        assert len(answers) == 1300 and set(answers.values()) == {"GOING"}
        assert len(await repo.member_names(-700)) == 1301
        assert len(await repo.group_responder_ids(event.id)) == 1300


async def test_availability_and_payments_in_postgresql():
    async with database() as (db, repo):
        organiser = await repo.ensure_user(1)
        start = datetime(2026, 10, 12, tzinfo=SG)
        poll = await repo.create_poll(
            organiser.id, -500, 90, "Sync", 60, start, start + timedelta(days=1)
        )
        assert await repo.get_availability(poll.id, organiser.id) is None
        await repo.set_availability(poll.id, organiser.id, ["2026-10-12T08:00:00+08:00"])
        await repo.set_availability(poll.id, organiser.id, ["2026-10-12T09:00:00+08:00"])
        assert await repo.get_availability(poll.id, organiser.id) == ["2026-10-12T09:00:00+08:00"]
        assert await repo.poll_availability(poll.id) == {
            organiser.id: ["2026-10-12T09:00:00+08:00"]
        }
        await repo.delete_user(organiser.id)
        assert await db.query("select * from poll_availability") == []

        assert await repo.record_payment("c1", -500, 1, 250, 30) is True
        assert await repo.record_payment("c1", -500, 1, 250, 30) is False
        first = await repo.extend_group_pro(-500, 30, NOW)
        second = await repo.extend_group_pro(-500, 30, NOW)
        assert first == NOW + timedelta(days=30) and second == first + timedelta(days=30)
        assert await repo.group_tier(-500, NOW) == "PRO"
        assert await repo.group_tier(-500, second + timedelta(seconds=1)) == "FREE"


async def test_usual_times_and_refunds_in_postgresql():
    async with database() as (db, repo):
        user = await repo.ensure_user(1)
        assert await repo.get_usual(user.id) == []
        await repo.replace_usual(user.id, [(1, 1080, 1320), (3, 1080, 1320), (1, 1080, 1320)])
        assert await repo.get_usual(user.id) == [(1, 1080, 1320), (3, 1080, 1320)]
        # Replacing swaps the whole pattern; an invalid one is refused and changes nothing.
        await repo.replace_usual(user.id, [(5, 480, 1320)])
        assert await repo.get_usual(user.id) == [(5, 480, 1320)]
        with pytest.raises(RuntimeError, match="check constraint"):
            await repo.replace_usual(user.id, [(9, 480, 1320)])
        assert await repo.get_usual(user.id) == [(5, 480, 1320)]
        await repo.replace_usual(user.id, [])
        assert await repo.get_usual(user.id) == []
        await repo.replace_usual(user.id, [(0, 600, 720)])
        await repo.delete_user(user.id)
        assert await db.query("select * from weekly_availability") == []

        await repo.record_payment("c1", -500, 77, 250, 30)
        await repo.record_payment("c2", -500, 77, 250, 30)
        await repo.record_payment("c3", -600, 88, 250, 30)
        # The buyer's own newest payment inside the window; others' are not theirs to refund.
        found = await repo.refundable_payment(77, datetime.now(SG) - timedelta(days=3))
        assert found["charge_id"] == "c2" and found["telegram_id"] == 77
        assert await repo.refundable_payment(77, datetime.now(SG) + timedelta(days=1)) is None
        assert await repo.mark_refunded("c2", NOW) is True
        assert await repo.mark_refunded("c2", NOW) is False  # only one caller wins
        assert (await repo.refundable_payment(77, datetime.now(SG) - timedelta(days=3)))[
            "charge_id"
        ] == "c1"

        await repo.set_group_tier(-500, "PRO", NOW + timedelta(days=45))
        await repo.shorten_group_pro(-500, 30, NOW)
        assert await repo.group_tier(-500, NOW) == "PRO"
        await repo.shorten_group_pro(-500, 30, NOW)  # nothing left once it falls below now
        assert await repo.group_tier(-500, NOW) == "FREE"
        await repo.shorten_group_pro(-999, 30, NOW)  # an unknown group is not an error
