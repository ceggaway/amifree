from datetime import date, datetime, time, timedelta

import pytest

from app.schemas.event import SG, EventDraft
from app.services.date_resolution import Clarification
from app.services.nusmods import ImportResult
from app.services.quick_blocks import expand_blocks, parse_block
from tests.conftest import extraction

# Reference is Sunday 4 Oct 2026, 12:00 Singapore.


def test_one_off_block_resolves_an_absolute_date(reference):
    rule = parse_block("busy sat 2–6pm", reference)
    assert rule.kind == "ONCE" and rule.title == "Busy"
    assert rule.start_at == datetime(2026, 10, 10, 14, tzinfo=SG)
    assert rule.end_at == datetime(2026, 10, 10, 18, tzinfo=SG)


def test_weekly_block_defaults_to_a_ninety_day_horizon(reference):
    rule = parse_block("work every Wed 6–10pm", reference)
    assert (rule.kind, rule.title, rule.weekday) == ("WEEKLY", "Work", 2)
    assert (rule.start_time, rule.end_time) == (time(18), time(22))
    assert rule.start_date == date(2026, 10, 7)
    assert rule.until_date == date(2026, 10, 7) + timedelta(days=90)
    explicit = parse_block("tuition every Weds 18:00-20:00 until 30 Nov", reference)
    assert explicit.until_date == date(2026, 11, 30) and explicit.title == "Tuition"


def test_ordinary_invitations_are_not_claimed(reference):
    assert parse_block("CCA dinner Friday 7–9pm at Vivo", reference) is None
    assert parse_block("Training every week\nWed 6-10pm at MPSH", reference) is None
    # The explicit command accepts a titled one-off block.
    rule = parse_block("dentist tomorrow 3-4pm", reference, explicit=True)
    assert rule.title == "Dentist" and rule.start_at == datetime(2026, 10, 5, 15, tzinfo=SG)


@pytest.mark.parametrize("text", ["busy sat", "busy 2-6pm", "work every Wed 10pm-2am"])
def test_incomplete_blocks_ask_for_clarification(reference, text):
    with pytest.raises(Clarification):
        parse_block(text, reference)


def test_weekly_expansion_is_half_open_and_bounded(reference):
    rule = parse_block("work every Wed 6–10pm until 21 Oct", reference)

    def window(day, start, end):
        return expand_blocks(
            [rule],
            datetime(2026, 10, day, start, tzinfo=SG),
            datetime(2026, 10, day, end, tzinfo=SG),
        )

    assert len(window(7, 19, 20)) == 1 and len(window(14, 19, 20)) == 1
    assert window(6, 19, 20) == []  # Tuesday
    assert window(7, 22, 23) == [] and window(7, 17, 18) == []  # back-to-back
    assert window(28, 19, 20) == []  # after the until date
    month = expand_blocks(
        [rule], datetime(2026, 10, 1, tzinfo=SG), datetime(2026, 11, 1, tzinfo=SG)
    )
    assert [b.start_at.day for b in month] == [7, 14, 21]


async def test_weekly_block_clashes_and_survives_reimport(service_factory, reference):
    wednesday = extraction(date_text="Wednesday", start_time_text="19:00", end_time_text="20:00")
    tuesday = extraction(date_text="Tuesday", start_time_text="19:00", end_time_text="20:00")
    after = extraction(date_text="Wednesday", start_time_text="22:00", end_time_text="23:00")
    service, repo = service_factory(wednesday, tuesday, after, wednesday)
    preview = (await service.message(1, 2, "work every Wed 6–10pm", reference))[0]
    assert "Every Wednesday 18:00 → 22:00" in preview.text and not service.extractor.calls
    user = await repo.ensure_user(1)
    assert not await repo.list_blocks(user.id)
    done = await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    assert "Blocked" in done[0].text
    clash = (await service.message(1, 3, "Dinner Wednesday 19:00 20:00", reference))[0]
    assert clash.text.startswith("⚠️ Clashes with Work")
    free = (await service.message(1, 4, "Dinner Tuesday 19:00 20:00", reference))[0]
    assert free.text.startswith("✅ No clashes found")
    edge = (await service.message(1, 5, "Supper Wednesday 22:00 23:00", reference))[0]
    assert edge.text.startswith("✅ No clashes found")
    # A NUSMods (re)import replaces timetable blocks only.
    await service.message(1, 6, "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference)
    await repo.import_timetable(
        user.id,
        ImportResult([], "2026-2027", 1, reference, reference + timedelta(days=60)),
    )
    assert len(await repo.list_blocks(user.id)) == 1
    again = (await service.message(1, 7, "Dinner Wednesday 19:00 20:00", reference))[0]
    assert again.text.startswith("⚠️ Clashes with Work")


async def test_block_command_list_remove_and_isolation(service_factory, reference):
    service, repo = service_factory()
    assert "busy sat" in (await service.command(1, "block"))[0].text
    assert "What time" in (await service.command(1, "block", "busy sat"))[0].text
    preview = (await service.command(1, "block", "busy sat 2-6pm"))[0]
    assert "Sat, 10 Oct 2026 · 14:00 → 18:00" in preview.text
    kept = await service.callback(1, preview.keyboard.inline_keyboard[0][1].callback_data)
    assert "Kept unchanged" in kept[0].text
    assert "No busy blocks" in (await service.command(1, "blocks"))[0].text
    preview = (await service.command(1, "block", "busy sat 2-6pm"))[0]
    await service.callback(1, preview.keyboard.inline_keyboard[0][0].callback_data)
    listed = (await service.command(1, "blocks"))[0]
    remove = listed.keyboard.inline_keyboard[0][0].callback_data
    assert len(remove.encode()) <= 64
    # Another user can neither see nor remove it.
    assert "No busy blocks" in (await service.command(2, "blocks"))[0].text
    assert "already gone" in (await service.callback(2, remove))[0].text
    assert "isn't valid" in (await service.callback(1, "unblock:not-a-uuid"))[0].text
    assert "removed" in (await service.callback(1, remove))[0].text
    user = await repo.ensure_user(1)
    draft = EventDraft(
        title="x",
        start_at=datetime(2026, 10, 10, 15, tzinfo=SG),
        end_at=datetime(2026, 10, 10, 16, tzinfo=SG),
    )
    assert (await repo.busy(user.id, draft))[0] == []
