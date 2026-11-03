"""The evening summary: tomorrow's plans and deadlines, the week ahead, and a monthly look."""

from datetime import datetime
from types import SimpleNamespace

from app.schemas.event import SG, Status
from app.services.reminders import summary
from tests.conftest import extraction, tap
from tests.test_deadlines import at, due, reminders_at


async def nightly(service, repo, now):
    return [r for r in await reminders_at(service, repo, now) if r.text.startswith("Tomorrow")]


async def add_deadlines(service, *texts):
    for n, text in enumerate(texts):
        await service.message(1, 100 + n, text, datetime(2026, 10, 4, 12, tzinfo=SG))


async def test_sunday_night_lists_tomorrow_the_week_and_the_month(service_factory):
    service, repo = service_factory(
        due(title="MA1521 quiz", date_text="Mon", start_time_text="9am"),
        due(title="CS3244 report"),
        due(title="CS2103T project", date_text="2026-10-20"),
    )
    await add_deadlines(
        service,
        "MA1521 quiz due Mon 9am",
        "CS3244 report due Fri 11:59pm",
        "CS2103T project due 2026-10-20 11:59pm",
    )
    (sent,) = await nightly(service, repo, at(4, 20, 5))  # Sunday
    assert sent.text == (
        "Tomorrow, Mon 5 Oct:\n\n"
        "📌 Due tomorrow\n• MA1521 quiz · 9am\n\n"
        "🔜 Coming up\n• CS3244 report · Fri 9 Oct, 11:59pm (in 5 days)\n\n"
        "🗓 Later this month\n• CS2103T project · Tue 20 Oct\n\n"
        "/plans for details. /reminders off stops these summaries."
    )


async def test_other_nights_skip_days_with_only_later_deadlines(service_factory):
    service, repo = service_factory(due(), due(title="CS2103T project", date_text="2026-10-20"))
    await add_deadlines(
        service, "CS3244 report due Fri 11:59pm", "CS2103T project due 2026-10-20 11:59pm"
    )
    # Monday night: nothing tomorrow, so no message, even with deadlines later in the week.
    assert await nightly(service, repo, at(5, 20, 5)) == []
    # Thursday night: the report is due tomorrow, and the month list is left for Sunday.
    (sent,) = await nightly(service, repo, at(8, 20, 5))
    assert "📌 Due tomorrow\n• CS3244 report · 11:59pm" in sent.text
    assert "Later this month" not in sent.text and "CS2103T" not in sent.text


async def test_undecided_plans_can_be_answered_from_the_summary(service_factory, reference):
    service, repo = service_factory(
        extraction(
            title="Badminton", date_text="Monday", start_time_text="8pm", end_time_text="10pm"
        )
    )
    await service.message(1, 1, "Badminton Monday 8pm-10pm", reference)
    plan = next(iter(repo.events.values()))
    await repo.save(plan.user_id, plan)
    (sent,) = await nightly(service, repo, at(4, 20, 5))
    assert "Still undecided: Badminton" in sent.text
    await service.callback(1, tap(sent, "✅ Going: Badminton"))
    assert next(iter(repo.events.values())).status == Status.GOING


def test_long_lists_stop_at_five_and_point_to_the_full_list():
    now = datetime(2026, 10, 4, 20, tzinfo=SG)
    soon = [
        SimpleNamespace(title=f"Task {n}", due_at=datetime(2026, 10, 6 + n % 5, 9, tzinfo=SG))
        for n in range(7)
    ]
    first = SimpleNamespace(title="Quiz", due_at=datetime(2026, 10, 5, 9, tzinfo=SG))
    text = summary(datetime(2026, 10, 5, tzinfo=SG), [], [first], coming_up=soon, now=now)
    assert text.count("• Task") == 5 and "+ 2 more · /deadline" in text


def test_nothing_tomorrow_and_no_month_list_sends_nothing():
    later = [SimpleNamespace(title="Essay", due_at=datetime(2026, 10, 9, tzinfo=SG))]
    assert summary(datetime(2026, 10, 6, tzinfo=SG), [], [], coming_up=later) is None
