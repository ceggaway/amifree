"""/giveaway: shows the sender's own handle, calendar status and a check code."""

from app.bot.delivery import telegram_profile
from app.services.giveaway import check_code


async def test_no_username_asks_them_to_set_one(service_factory):
    service, _ = service_factory()
    telegram_profile.set((None, "Ben"))
    reply = (await service.command(1, "giveaway"))[0]
    assert "need a Telegram username" in reply.text


async def test_without_a_calendar_points_to_calendar(service_factory):
    service, _ = service_factory()
    telegram_profile.set(("ben_lim", "Ben"))
    text = (await service.command(1, "giveaway"))[0].text
    assert "Handle: @ben_lim" in text
    assert "❌ No calendar connected yet" in text
    assert "/calendar" in text


async def test_connected_calendar_shows_its_name_and_code(service_factory):
    service, repo = service_factory()
    telegram_profile.set(("ben_lim", "Ben"))
    user = await repo.ensure_user(1)
    await repo.add_calendar(user.id, "sealed", "hash", "iPhone")
    text = (await service.command(1, "giveaway"))[0].text
    assert "✅ Calendar connected (iPhone)" in text
    assert f"Code: {check_code('ben_lim', service.link_secret)}" in text
    assert "Screenshot this message" in text


async def test_broken_calendar_does_not_count(service_factory):
    service, repo = service_factory()
    telegram_profile.set(("ben_lim", "Ben"))
    user = await repo.ensure_user(1)
    cal, _ = await repo.add_calendar(user.id, "sealed", "hash", "iPhone")
    repo.calendars[cal.id].error = "link stopped working"
    text = (await service.command(1, "giveaway"))[0].text
    assert "❌ No calendar connected yet" in text


async def test_google_only_counts(service_factory):
    service, repo = service_factory()
    telegram_profile.set(("amy_tan", "Amy"))
    user = await repo.ensure_user(1)
    await repo.save_google_connection(user.id, "amy@gmail.com", None)
    text = (await service.command(1, "giveaway"))[0].text
    assert "✅ Calendar connected (Google Calendar)" in text


def test_code_ignores_at_sign_and_case_but_not_handle():
    assert check_code("@Ben_Lim", "s") == check_code("ben_lim", "s")
    assert check_code("ben_lim", "s") != check_code("amy_tan", "s")
    assert check_code("ben_lim", "s") != check_code("ben_lim", "other secret")
    code = check_code("ben_lim", "s")
    assert len(code) == 9 and code[4] == "-"
