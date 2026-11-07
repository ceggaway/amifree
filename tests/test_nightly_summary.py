"""A daily overview contains only saved plans and is delivered once per plan date."""

from app.schemas.event import Status
from app.services.jobs import Notifier
from app.services.reminders import enqueue_nightly, summary
from tests.conftest import extraction, tap
from tests.test_deadlines import at, due, reminders_at
from tests.test_notifications import Outbox


async def saved_plan(service_factory, reference):
    service, repo = service_factory(
        extraction(
            title="Badminton", date_text="Monday", start_time_text="8pm", end_time_text="10pm"
        )
    )
    card = (await service.message(1, 1, "Badminton Monday 8pm-10pm", reference))[0]
    await service.callback(1, tap(card, "Add to plans"))
    for event in repo.events.values():
        event.created_at = reference
    return service, repo, await repo.ensure_user(1)


async def test_one_morning_overview_and_no_evening_duplicate(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    assert await reminders_at(service, repo, at(4, 21)) == []
    assert await reminders_at(service, repo, at(5, 8, 59)) == []
    (sent,) = await reminders_at(service, repo, at(5, 9))
    assert "Daily overview" in sent.text and "Today · 📅 05/10/2026 (Mon)" in sent.text
    assert "<b>Badminton</b>" in sent.text and sent.parse_mode == "HTML"
    assert await reminders_at(service, repo, at(5, 9, 1)) == []
    await repo.set_user_flags(user.id, overview_period="evening")
    # No plans tomorrow, so changing the period doesn't send a second overview.
    assert await reminders_at(service, repo, at(5, 21)) == []


async def test_night_before_and_morning_share_a_deduplication_key(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    await repo.set_user_flags(user.id, overview_period="evening")
    assert await reminders_at(service, repo, at(4, 20, 59)) == []
    (sent,) = await reminders_at(service, repo, at(4, 21))
    assert "Tomorrow · 📅 05/10/2026 (Mon)" in sent.text
    await repo.set_user_flags(user.id, overview_period="morning")
    assert await reminders_at(service, repo, at(5, 9)) == []


async def test_custom_time_is_observed_to_the_minute(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    await repo.set_user_flags(user.id, overview_morning_minute=7 * 60 + 45)
    assert await reminders_at(service, repo, at(5, 7, 44)) == []
    assert len(await reminders_at(service, repo, at(5, 7, 45))) == 1
    assert await reminders_at(service, repo, at(5, 9)) == []


async def test_daily_overview_off_does_not_disable_deadline_alerts(service_factory, reference):
    service, repo = service_factory(due(date_text="Mon", start_time_text="1pm"))
    await service.message(1, 1, "Report due Mon 1pm", reference)
    user = await repo.ensure_user(1)
    await repo.set_user_flags(user.id, overview_enabled=False)
    for deadline in repo.deadlines.values():
        deadline.created_at = reference
    assert await reminders_at(service, repo, at(5, 9)) == []
    (sent,) = await reminders_at(service, repo, at(5, 10))
    assert "Deadline reminder" in sent.text


async def test_overviews_never_include_deadlines_or_unsaved_previews(service_factory, reference):
    service, repo = service_factory(due(date_text="Mon"), extraction(date_text="Monday"))
    await service.message(1, 1, "Report due Mon 11:59pm", reference)
    await service.message(1, 2, "Dinner Monday 7-9pm", reference)  # preview only
    for d in repo.deadlines.values():
        d.created_at = reference
    assert await reminders_at(service, repo, at(5, 9)) == []
    assert summary(at(5, 0), [], list(repo.deadlines.values())) is None


async def test_saved_status_is_included_and_cancelled_plan_is_not(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    event = next(iter(repo.events.values()))
    event.status = Status.SAVED
    assert "Badminton" in summary(at(5, 0), [event])
    event.status = Status.CANCELLED
    assert summary(at(5, 0), [event]) is None


async def test_time_changed_after_queue_is_deferred_and_not_lost(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    await repo.set_user_flags(user.id, overview_period="evening", overview_evening_minute=20 * 60)
    assert await enqueue_nightly(repo, at(4, 20)) == 1
    await repo.set_user_flags(user.id, overview_evening_minute=21 * 60)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    service.clock = lambda: at(4, 20)
    assert (await service.notifier.deliver())["deferred"] == 1
    assert not outbox.sent
    service.clock = lambda: at(4, 21)
    assert (await service.notifier.deliver())["sent"] == 1
    assert len(outbox.sent) == 1


async def test_late_morning_overview_and_empty_day_are_skipped(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    assert await reminders_at(service, repo, at(5, 12)) == []
    assert await reminders_at(service, repo, at(6, 9)) == []


async def test_personal_reminders_off_keeps_group_delivery_enabled(service_factory):
    service, repo = service_factory()
    user = await repo.ensure_user(1)
    await service.command(1, "reminders", "off")
    user = await repo.ensure_user(1)
    assert not user.overview_enabled and not user.deadline_alerts
    assert user.reminders_enabled
    await service.command(1, "reminders", "on")
    user = await repo.ensure_user(1)
    assert user.overview_enabled and user.deadline_alerts


async def test_moving_a_deferred_overview_earlier_wakes_its_queue(service_factory, reference):
    service, repo, user = await saved_plan(service_factory, reference)
    await repo.set_user_flags(user.id, overview_period="evening", overview_evening_minute=20 * 60)
    assert await enqueue_nightly(repo, at(4, 20)) == 1
    await repo.set_user_flags(user.id, overview_evening_minute=22 * 60)
    outbox = Outbox()
    service.notifier = Notifier(service, outbox)
    service.clock = lambda: at(4, 20)
    assert (await service.notifier.deliver())["deferred"] == 1
    assert repo.notifications[0]["next_attempt_at"] == at(4, 22)
    service.clock = lambda: at(4, 20, 15)
    await service.save_personal_preferences(
        await repo.ensure_user(1), overview_evening_minute=20 * 60 + 30
    )
    assert repo.notifications[0]["next_attempt_at"] == at(4, 20, 15)
    assert (await service.notifier.deliver())["deferred"] == 1
    assert repo.notifications[0]["next_attempt_at"] == at(4, 20, 30)
    service.clock = lambda: at(4, 20, 30)
    assert (await service.notifier.deliver())["sent"] == 1
    assert len(outbox.sent) == 1
