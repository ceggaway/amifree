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
