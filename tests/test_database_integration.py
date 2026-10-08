import shutil
from datetime import datetime, timedelta

import httpx
import pytest
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions

from app.config import Settings
from app.db.supabase import SupabaseRepository
from app.models.timetable import BusyBlock
from app.schemas.event import SG, EventDraft, Status
from app.services.nusmods import ImportResult
from app.services.quick_blocks import parse_block
from app.services.scheduling import SchedulingService
from tests.conftest import extraction
from tests.fakes import QueueExtractor
from tests.sql_transport import ROOT, SQLGateway

pytestmark = pytest.mark.skipif(
    not shutil.which("node")
    or not (ROOT / "scripts/sql-tests/node_modules/@electric-sql/pglite").exists(),
    reason="Install optional SQL test engine: npm --prefix scripts/sql-tests ci",
)


async def test_old_upcoming_commitment_remains_discoverable():
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        user = await repo.ensure_user(1)
        start = datetime.now(SG) + timedelta(days=7)
        draft = EventDraft(
            title="Future booking", start_at=start, end_at=start + timedelta(hours=1)
        )
        event = await repo.create_preview(user.id, 1, draft)
        await repo.rsvp(user.id, event, Status.GOING)
        await db.query(
            "update events set created_at=now()-interval '31 days' where id=$1", [event.id]
        )
        assert [e.id for e in await repo.recent_events(user.id)] == [event.id]
        _, busy = await repo.busy(user.id, draft)
        assert [e.id for e in busy] == [event.id]
        other = await repo.ensure_user(2)
        assert await repo.recent_events(other.id) == []
        service = SchedulingService(
            repo,
            QueueExtractor(extraction(kind="cancel", match_title="Future booking")),
            None,
            Settings(_env_file=None, bot_mode="disabled"),
        )
        await db.query(
            "update users set onboarding_status='READY', coverage_start=now(), coverage_end=now()+interval '30 days' where id=$1",
            [user.id],
        )
        assert "Future booking" in (await service.command(1, "events"))[0].text
        replies = await service.message(1, 2, "Cancel Future booking", datetime.now(SG))
        assert replies[0].keyboard is not None
        # Cross the API page size and overlap the upcoming/recent sets: none may
        # disappear or appear twice when both discovery queries return them.
        await db.query(
            "insert into events(user_id,source_message_id,title,start_at,end_at) "
            "select $1, n+10, 'Booking ' || n, now()+interval '8 days', "
            "now()+interval '8 days 1 hour' from generate_series(1,501) n",
            [user.id],
        )
        discovered = await repo.recent_events(user.id)
        assert len(discovered) == len({e.id for e in discovered}) == 502


async def test_supabase_repository_connected_to_postgresql(reference):
    class Importer:
        async def import_link(self, *args):
            return ImportResult(
                [
                    BusyBlock(
                        title="Lecture", start_at=reference, end_at=reference + timedelta(hours=1)
                    )
                ],
                "2026-2027",
                1,
                datetime(2026, 8, 10, tzinfo=SG),
                datetime(2026, 12, 14, tzinfo=SG),
            )

    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        service = SchedulingService(
            repo,
            QueueExtractor(extraction(), extraction(), extraction()),
            Importer(),
            Settings(_env_file=None, bot_mode="disabled"),
        )
        await repo.ping()
        await repo.cleanup(7)
        for uid in (1, 2):
            await service.command(uid, "start")
            await service.message(
                uid, 1, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
            )
        first = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
        assert "No clashes found" in first.text
        await service.callback(1, first.keyboard.inline_keyboard[0][0].callback_data)
        second = (await service.message(1, 3, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
        assert "Clashes with" in second.text
        other = (await service.message(2, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
        assert "No clashes found" in other.text
        assert (
            "isn't available to you"
            in (await service.callback(2, first.keyboard.inline_keyboard[0][0].callback_data))[
                0
            ].text
        )
        assert len(await db.query("select * from events")) == 3
        assert len(await db.query("select * from events where status='GOING'")) == 1
        assert len(await db.query("select * from timetable_blocks")) == 2
        user = await repo.ensure_user(1)
        await repo.set_pending(user.id, {"stage": "delete", "nonce": "expired"})
        await db.query(
            "update users set pending_expires_at=now()-interval '1 hour' where id=$1", [user.id]
        )
        assert (await repo.ensure_user(1)).pending is None
        deletion = (await service.command(1, "delete"))[0]
        await service.callback(1, deletion.keyboard.inline_keyboard[0][0].callback_data)
        assert len(await db.query("select * from events")) == 1
        assert len(await db.query("select * from users")) == 1


async def test_manual_blocks_in_postgresql_survive_reimport(reference):
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        user, other = await repo.ensure_user(1), await repo.ensure_user(2)
        weekly = await repo.create_block(user.id, parse_block("work every Wed 6–10pm", reference))
        once = await repo.create_block(user.id, parse_block("busy sat 2–6pm", reference))
        assert weekly.id and weekly.weekday == 2 and once.kind == "ONCE"
        wednesday = EventDraft(
            title="Dinner",
            start_at=datetime(2026, 10, 14, 19, tzinfo=SG),
            end_at=datetime(2026, 10, 14, 20, tzinfo=SG),
        )
        assert [b.title for b in (await repo.busy(user.id, wednesday))[0]] == ["Work"]
        assert (await repo.busy(other.id, wednesday))[0] == []
        await repo.import_timetable(
            user.id,
            ImportResult(
                [],
                "2026-2027",
                1,
                datetime(2026, 8, 10, tzinfo=SG),
                datetime(2026, 12, 14, tzinfo=SG),
            ),
        )
        assert len(await repo.list_blocks(user.id)) == 2
        assert not await repo.delete_block(other.id, once.id)
        assert await repo.delete_block(user.id, once.id)
        # Saved plans are returned for the possible-overlap check; previews are not.
        event = await repo.create_preview(user.id, 5, wednesday)
        assert (await repo.busy(user.id, wednesday))[1] == []
        saved = await repo.save(user.id, event)
        assert saved.status == Status.SAVED
        assert [e.id for e in (await repo.busy(user.id, wednesday))[1]] == [event.id]
        await repo.delete_user(user.id)
        assert await db.query("select * from manual_busy_blocks") == []


async def test_group_cards_in_postgresql(reference):
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        organiser, member, outsider = [await repo.ensure_user(n) for n in (1, 2, 3)]
        draft = EventDraft(
            title="Dinner",
            start_at=datetime(2026, 10, 9, 19, tzinfo=SG),
            end_at=datetime(2026, 10, 9, 21, tzinfo=SG),
            location="Vivo",
        )
        card = await repo.create_group_event(organiser.id, -500, 10, draft)
        assert (await repo.create_group_event(organiser.id, -500, 10, draft)).id == card.id
        await repo.set_group_message(card.id, 77)
        assert (await repo.get_group_event(card.id)).message_id == 77
        assert await repo.linked_event(member.id, card.id) is None
        going = await repo.group_rsvp(member.id, card, Status.GOING)
        assert going.status == Status.GOING and going.group_event_id == card.id
        assert going.source_message_id is None and going.location == "Vivo"
        # Saving keeps the answer; a new answer replaces it on the same row.
        assert (await repo.group_rsvp(member.id, card, Status.SAVED)).status == Status.GOING
        await repo.group_rsvp(organiser.id, card, Status.MAYBE)
        assert await repo.group_counts(card.id) == {"GOING": 1, "MAYBE": 1}
        assert (await repo.group_rsvp(member.id, card, Status.DECLINED)).id == going.id
        assert await repo.group_counts(card.id) == {"DECLINED": 1, "MAYBE": 1}
        await repo.touch_member(-500, outsider.id)
        await repo.touch_member(-500, outsider.id)
        assert len(await db.query("select * from group_members where chat_id=-500")) == 3
        # The linked row behaves as the member's own plan.
        await repo.group_rsvp(member.id, card, Status.GOING)
        assert [e.id for e in (await repo.busy(member.id, draft))[1]] == [going.id]
        assert [e.id for e in await repo.recent_events(member.id)] == [going.id]
        # Deleting the organiser keeps the shared card and other members' plans.
        await repo.delete_user(organiser.id)
        kept = await repo.get_group_event(card.id)
        assert kept and kept.creator_user_id is None
        assert await repo.group_counts(card.id) == {"GOING": 1}


async def test_change_outbox_in_postgresql(reference):
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        organiser, going, declined = [await repo.ensure_user(n) for n in (1, 2, 3)]
        draft = EventDraft(
            title="Dinner",
            start_at=datetime(2026, 10, 9, 19, tzinfo=SG),
            end_at=datetime(2026, 10, 9, 21, tzinfo=SG),
        )
        card = await repo.create_group_event(organiser.id, -500, 10, draft)
        await repo.group_rsvp(organiser.id, card, Status.GOING)
        await repo.group_rsvp(going.id, card, Status.GOING)
        await repo.group_rsvp(declined.id, card, Status.DECLINED)
        later = draft.model_copy(
            update={
                "start_at": draft.start_at + timedelta(hours=1),
                "end_at": draft.end_at + timedelta(hours=1),
            }
        )
        assert await repo.update_group_event(going.id, card, later) is None
        updated = await repo.update_group_event(organiser.id, card, later)
        assert updated.version == 2 and updated.start_at.hour == 20
        assert await repo.update_group_event(organiser.id, card, later) is None  # stale version
        # The nightly job reads Going plans across users for one Singapore day.
        day = datetime(2026, 10, 9, tzinfo=SG)
        found = await repo.events_between(day, day + timedelta(days=1), [Status.GOING])
        assert sorted(e.user_id for e in found) == sorted([organiser.id, going.id])
        mine = await repo.events_between(
            day, day + timedelta(days=1), [Status.GOING, Status.DECLINED], declined.id
        )
        assert [e.status for e in mine] == [Status.DECLINED]
        assert (
            await repo.events_between(
                day + timedelta(days=1), day + timedelta(days=2), [Status.GOING]
            )
            == []
        )
        rows = await repo.claim_notifications()
        assert [(r["user_id"], r["kind"], r["status"]) for r in rows] == [
            (going.id, "CHANGE", "sending")
        ]
        assert rows[0]["payload"]["before"]["start_at"] != rows[0]["payload"]["after"]["start_at"]
        assert await repo.claim_notifications() == []
        # A failed send returns to the queue with a backoff, then succeeds exactly once.
        await repo.finish_notification(rows[0]["id"], False, False)
        assert await repo.claim_notifications() == []
        await db.query("update notification_logs set next_attempt_at=now()-interval '1 minute'")
        retry = await repo.claim_notifications()
        assert retry[0]["attempts"] == 2
        await repo.finish_notification(retry[0]["id"], True, False)
        assert (await db.query("select status,sent_at from notification_logs"))[0][
            "status"
        ] == "sent"
        assert await repo.enqueue_notification(going.id, "NIGHTLY", "2026-10-09", {"n": 1})
        assert not await repo.enqueue_notification(going.id, "NIGHTLY", "2026-10-09", {"n": 2})
        await repo.set_user_flags(going.id, dm_blocked=True, reminders_enabled=False)
        user = await repo.get_user(going.id)
        assert user.dm_blocked and not user.reminders_enabled
        assert await repo.get_user(str(organiser.id)[:-4] + "0000") is None
        await repo.delete_user(going.id)
        assert await db.query("select * from notification_logs") == []


async def test_polls_in_postgresql(reference):
    async with (
        SQLGateway() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(db.handle)) as http,
    ):
        client = await acreate_client(
            "https://sql-test.supabase.co",
            "local-schema-test-key",
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(client)
        organiser, member, third = [await repo.ensure_user(n) for n in (1, 2, 3)]
        start = datetime(2026, 10, 5, tzinfo=SG)
        poll = await repo.create_poll(
            organiser.id, -500, 30, "Sync", 60, start, start + timedelta(days=1)
        )
        again = await repo.create_poll(
            organiser.id, -500, 30, "Sync", 60, start, start + timedelta(days=1)
        )
        assert again.id == poll.id and poll.window_start == start and poll.status == "COLLECTING"
        await repo.set_poll_message(poll.id, 55)
        assert await repo.poll_participants(poll.id) == [organiser.id]
        assert await repo.poll_join(poll.id, member.id) == 2
        assert await repo.poll_join(poll.id, member.id) == 2
        assert not await repo.poll_vote(poll.id, member.id, {"0": "FREE"})
        slots = [
            {
                "start_at": "2026-10-05T14:00:00+08:00",
                "end_at": "2026-10-05T15:00:00+08:00",
                "free": 2,
            },
            {
                "start_at": "2026-10-05T16:00:00+08:00",
                "end_at": "2026-10-05T17:00:00+08:00",
                "free": 2,
            },
        ]
        assert await repo.poll_open(poll.id, member.id, slots) is None
        opened = await repo.poll_open(poll.id, organiser.id, slots)
        assert opened.status == "OPEN" and opened.slots == slots and opened.message_id == 55
        assert await repo.poll_open(poll.id, organiser.id, slots) is None
        assert await repo.poll_vote(poll.id, member.id, {"0": "FREE", "1": "BUSY", "2": "FREE"})
        assert await repo.poll_vote(poll.id, third.id, {"0": "FREE"})
        assert await repo.poll_vote(poll.id, third.id, {"0": "MAYBE"})
        assert await repo.poll_tally(poll.id) == {(0, "FREE"): 1, (0, "MAYBE"): 1, (1, "BUSY"): 1}
        assert await repo.poll_close(poll.id, member.id) is None
        assert (await repo.poll_close(poll.id, organiser.id)).status == "CLOSED"
        assert await repo.poll_join(poll.id, third.id) is None
        await repo.delete_user(member.id)
        assert await repo.poll_tally(poll.id) == {(0, "MAYBE"): 1}
        assert await repo.poll_participants(poll.id) == [organiser.id]
