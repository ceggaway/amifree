"""Personal calendar link: paste once, busy times (only) count in clash checks."""

from datetime import datetime, timedelta

import httpx
import pytest

from app.schemas.event import SG
from app.services.personal_calendar import (
    CalendarLinkError,
    CalendarLinkGone,
    CalendarReader,
    busy_intervals,
    find_link,
    normalise,
    seal,
    unseal,
)
from tests.conftest import extraction, tap

NOW = datetime(2026, 10, 12, 10, tzinfo=SG)
GOOGLE = "https://calendar.google.com/calendar/ical/me%40gmail.com/private-abc123/basic.ics"


def ics(*events: str, extra: str = "") -> bytes:
    body = "\n".join(f"BEGIN:VEVENT\n{e.strip()}\nEND:VEVENT" for e in events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{extra}{body}\nEND:VCALENDAR\n".encode()


def window(data, days=30):
    return busy_intervals(data, NOW, NOW + timedelta(days=days))


# ---------- reading busy times ----------


def test_weekly_event_expands_and_titles_are_dropped():
    data = ics(
        """UID:1
SUMMARY:Therapy (private)
LOCATION:Clinic
DTSTART;TZID=Asia/Singapore:20261013T190000
DTEND;TZID=Asia/Singapore:20261013T200000
RRULE:FREQ=WEEKLY;COUNT=3"""
    )
    busy = window(data)
    assert [(a.day, a.hour, b.hour) for a, b in busy] == [(13, 19, 20), (20, 19, 20), (27, 19, 20)]
    assert all(isinstance(x, datetime) for pair in busy for x in pair)  # nothing else survives


def test_what_does_not_count_as_busy():
    data = ics(
        "UID:a\nSUMMARY:Mum's birthday\nDTSTART;VALUE=DATE:20261014\nDTEND;VALUE=DATE:20261015",
        "UID:b\nSUMMARY:Free\nTRANSP:TRANSPARENT\nDTSTART:20261015T020000Z\nDTEND:20261015T030000Z",
        "UID:c\nSTATUS:CANCELLED\nDTSTART:20261016T020000Z\nDTEND:20261016T030000Z",
        "UID:d@amifree\nSUMMARY:From our own feed\nDTSTART:20261017T020000Z\nDTEND:20261017T030000Z",
    )
    assert window(data) == []


def test_overlaps_merge_and_formats_are_understood():
    data = ics(
        "UID:1\nDTSTART:20261013T110000Z\nDTEND:20261013T130000Z",  # 7-9pm SGT in UTC
        "UID:2\nDTSTART;TZID=Asia/Singapore:20261013T200000\nDTEND;TZID=Asia/Singapore:20261013T220000",
        "UID:3\nDTSTART:20261014T090000\nDURATION:PT90M",  # floating time = Singapore
    )
    busy = window(data)
    assert [(f"{a:%d %H:%M}", f"{b:%d %H:%M}") for a, b in busy] == [
        ("13 19:00", "13 22:00"),
        ("14 09:00", "14 10:30"),
    ]


def test_busy_times_are_clipped_to_the_window():
    data = ics(
        "UID:1\nDTSTART;TZID=Asia/Singapore:20261011T200000\nDTEND;TZID=Asia/Singapore:20261012T120000"
    )
    assert window(data) == [(NOW, datetime(2026, 10, 12, 12, tzinfo=SG))]


def test_not_a_calendar():
    with pytest.raises(CalendarLinkError):
        window(b"<html>Sign in</html>")


# ---------- which links are accepted ----------


@pytest.mark.parametrize(
    "link,expected",
    [
        (GOOGLE, GOOGLE),
        (
            "webcal://p123-caldav.icloud.com/published/2/XYZ",
            "https://p123-caldav.icloud.com/published/2/XYZ",
        ),
        (
            "https://outlook.office365.com/owa/calendar/a@b.com/x/calendar.ics",
            "https://outlook.office365.com/owa/calendar/a@b.com/x/calendar.ics",
        ),
    ],
)
def test_supported_links(link, expected):
    assert normalise(link) == expected


@pytest.mark.parametrize(
    "link",
    [
        "http://calendar.google.com/calendar/ical/x/basic.ics",  # not encrypted
        "https://evil.example/calendar.ics",  # not a calendar provider
        "https://calendar.google.com.evil.example/basic.ics",
        "https://user:pw@calendar.google.com/basic.ics",
        "https://calendar.google.com:8443/basic.ics",
        "https://169.254.169.254/latest/meta-data.ics",  # cloud metadata address
    ],
)
def test_unsafe_or_unsupported_links_are_refused(link):
    with pytest.raises(CalendarLinkError):
        normalise(link)


def test_only_a_message_that_is_a_calendar_link_is_claimed():
    assert find_link(GOOGLE) == GOOGLE
    assert find_link("CCA dinner Friday 7-9pm at Vivo") is None
    assert find_link("https://nusmods.com/timetable/sem-1/share?CS3244=LEC:1") is None
    assert find_link(f"my calendar {GOOGLE}") is None  # not just a link: the LLM handles it
    with pytest.raises(CalendarLinkError, match="Google Calendar, iCloud and Outlook"):
        find_link("https://evil.example/cal.ics")
    with pytest.raises(CalendarLinkError, match="your AmIFree calendar link"):
        find_link("https://amifree.example/cal/abc.ics", "https://amifree.example")


def test_link_is_stored_encrypted():
    sealed = seal(GOOGLE, "key-one")
    assert "google" not in sealed and "private-abc123" not in sealed
    assert unseal(sealed, "key-one") == GOOGLE
    assert unseal(sealed, "key-two") is None


# ---------- fetching over the network ----------


def reader(handler):
    return CalendarReader(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_fetch_follows_safe_redirects_only():
    good = ics("UID:1\nDTSTART:20261013T110000Z\nDTEND:20261013T130000Z")

    def handler(request):
        if request.url.path == "/start.ics":
            return httpx.Response(
                302, headers={"location": "https://p1-caldav.icloud.com/real.ics"}
            )
        if request.url.host == "p1-caldav.icloud.com":
            return httpx.Response(200, content=good)
        if request.url.path == "/to-evil.ics":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})
        raise AssertionError(f"must not fetch {request.url}")

    r = reader(handler)
    assert len(await r.busy("https://calendar.google.com/start.ics", NOW)) == 1
    with pytest.raises(CalendarLinkError):
        await r.fetch("https://calendar.google.com/to-evil.ics")


@pytest.mark.parametrize(
    "response,error",
    [
        (httpx.Response(404), CalendarLinkGone),
        (httpx.Response(403), CalendarLinkGone),
        (httpx.Response(503), CalendarLinkError),
        (httpx.Response(200, content=b"<html>login</html>"), CalendarLinkError),
        (httpx.Response(200, content=b"BEGIN:VCALENDAR" + b"x" * 5_000_001), CalendarLinkError),
    ],
)
async def test_fetch_failures(response, error):
    with pytest.raises(error):
        await reader(lambda request: response).fetch(GOOGLE)


# ---------- in the bot ----------


class FakeReader:
    def __init__(self, busy=(), error=None):
        self.result, self.error, self.calls = list(busy), error, []

    async def busy(self, link, now):
        self.calls.append(link)
        if self.error:
            raise self.error
        return self.result


FRIDAY_DINNER = (datetime(2026, 10, 9, 19, 30, tzinfo=SG), datetime(2026, 10, 9, 20, 30, tzinfo=SG))


@pytest.fixture
def with_calendar(service_factory):
    def make(*items, busy=(FRIDAY_DINNER,), error=None):
        service, repo = service_factory(*items)
        service.calendar_reader = FakeReader(busy, error)
        return service, repo

    return make


async def test_pasting_a_link_connects_and_counts_busy_times(with_calendar, reference):
    service, repo = with_calendar(extraction())
    reply = (await service.message(1, 1, GOOGLE, reference))[0]
    assert "✅ Personal calendar connected" in reply.text and "1 busy time " in reply.text
    assert "never what the event is" in reply.text
    user = await repo.ensure_user(1)
    assert user.personal_calendar_url and "google" not in user.personal_calendar_url
    card = (await service.message(1, 2, "CCA dinner Friday 7–9pm at Vivo", reference))[0]
    assert card.text.startswith("⚠️ Clashes with Personal calendar")
    assert "Checked: your Going plans, busy blocks and personal calendar." in card.text
    # The card names the source, never what the event in their calendar actually is.
    assert "• Personal calendar: 09 Oct 19:30–20:30" in card.text


async def test_a_bad_link_is_explained_and_not_saved(with_calendar, reference):
    service, repo = with_calendar(
        error=CalendarLinkGone("That calendar link doesn't work any more.")
    )
    reply = (await service.message(1, 1, GOOGLE, reference))[0]
    assert "doesn't work any more" in reply.text
    assert not (await repo.ensure_user(1)).personal_calendar_url


async def test_calendar_screen_shows_status_and_disconnect(with_calendar, reference):
    service, repo = with_calendar()
    replies = await service.command(1, "calendar")
    assert "Secret address in iCal format" in replies[-1].text  # not connected: where to find it
    await service.message(1, 1, GOOGLE, reference)
    status = (await service.command(1, "calendar"))[-1]
    assert "✅ Connected" in status.text
    gone = (await service.callback(1, tap(status, "Disconnect my calendar")))[0]
    assert "deleted its link and every busy time" in gone.text
    user = await repo.ensure_user(1)
    assert user.personal_calendar_url is None and not repo.calendar_blocks.get(user.id)


async def test_hourly_refresh(with_calendar, reference):
    service, repo = with_calendar()
    await service.message(1, 1, GOOGLE, reference)
    user_id = (await repo.ensure_user(1)).id
    assert await service.refresh_calendars() == 0  # just synced
    service.calendar_reader.result = []  # the event was deleted in their calendar
    service.clock = lambda: reference + timedelta(hours=1, minutes=1)
    assert await service.refresh_calendars() == 1
    assert repo.calendar_blocks[user_id] == []


async def test_revoked_link_clears_busy_times_and_says_so(with_calendar, reference):
    service, repo = with_calendar()
    await service.message(1, 1, GOOGLE, reference)
    user_id = (await repo.ensure_user(1)).id
    service.calendar_reader.error = CalendarLinkGone("That calendar link doesn't work any more.")
    service.clock = lambda: reference + timedelta(hours=2)
    await service.refresh_calendars()
    assert repo.calendar_blocks[user_id] == []
    assert "doesn't work any more" in (await service.command(1, "calendar"))[-1].text
    # Not retried every minute: the next try is an hour later.
    service.clock = lambda: reference + timedelta(hours=2, minutes=5)
    assert await service.refresh_calendars() == 0


async def test_a_temporary_outage_keeps_the_last_good_copy(with_calendar, reference):
    service, repo = with_calendar()
    await service.message(1, 1, GOOGLE, reference)
    user_id = (await repo.ensure_user(1)).id
    service.calendar_reader.error = CalendarLinkError("I couldn't reach your calendar just now.")
    service.clock = lambda: reference + timedelta(hours=2)
    await service.refresh_calendars()
    assert repo.calendar_blocks[user_id] == [FRIDAY_DINNER]


async def test_changed_encryption_key_asks_for_the_link_again(with_calendar, reference):
    service, repo = with_calendar()
    await service.message(1, 1, GOOGLE, reference)
    service.settings = service.settings.model_copy(
        update={"calendar_link_key": type(service.settings.calendar_link_key)("a-new-key")}
    )
    service.clock = lambda: reference + timedelta(hours=2)
    await service.refresh_calendars()
    user = await repo.ensure_user(1)
    assert user.personal_calendar_url is None
    assert "send me your calendar link again" in (await service.command(1, "calendar"))[-1].text


async def test_jobs_tick_refreshes_calendars(with_calendar, reference):
    from app.services.jobs import Notifier

    service, _ = with_calendar()
    await service.message(1, 1, GOOGLE, reference)
    service.clock = lambda: reference + timedelta(hours=2)

    async def send(*args):
        pass

    result = await Notifier(service, send).run()
    assert result["calendars"] == 1
