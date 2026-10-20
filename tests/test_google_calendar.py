"""Share your Google Calendar with AmIFree: the Google client, then the bot flow around it."""

import base64
import json
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.timetable import BusyBlock  # noqa: F401  (documents what /free reads)
from app.schemas.event import SG, Event, Status
from app.services.google_calendar import (
    Access,
    GoogleAccessLost,
    GoogleCalendar,
    GoogleCalendarError,
    busy_from_items,
    event_body,
    google_event_id,
    load_key,
)
from tests.conftest import extraction, onboard, tap

BOT = "amifree@demo.iam.gserviceaccount.com"
DINNER = "CCA dinner Friday 7–9pm at Vivo"


def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


KEY = {"client_email": BOT, "private_key": pem()}


def event(**changes) -> Event:
    data = dict(
        id="12345678-1234-1234-1234-123456789abc",
        user_id="u1",
        title="CCA dinner",
        start_at=datetime(2026, 10, 9, 19, tzinfo=SG),
        end_at=datetime(2026, 10, 9, 21, tzinfo=SG),
        location="Vivo",
        status=Status.GOING,
        version=1,
    )
    return Event.model_validate(data | changes)


# ---------- the key ----------


def test_the_key_can_be_json_or_base64_and_junk_is_ignored():
    text = json.dumps(KEY)
    assert load_key(text)["client_email"] == BOT
    assert load_key(base64.b64encode(text.encode()).decode())["client_email"] == BOT
    assert load_key("") is None and load_key("not a key") is None
    assert load_key(json.dumps({"client_email": BOT})) is None  # no private key


# ---------- what is written to Google ----------


def test_event_id_is_fixed_so_a_plan_can_never_be_added_twice():
    one = event()
    assert google_event_id(one) == "12345678123412341234123456789abc"
    assert google_event_id(one) == google_event_id(event(version=5))


def test_event_body():
    body = event_body(event(notes="Bring cash"))
    assert body["summary"] == "CCA dinner" and body["location"] == "Vivo"
    assert body["start"]["dateTime"] == "2026-10-09T19:00:00+08:00"
    assert body["description"] == "Bring cash\n\nAdded from AmIFree"
    assert body["extendedProperties"]["private"] == {"amifree": "1"}
    assert "location" not in event_body(event(location=None))


# ---------- what is read from Google ----------


def timed(a, b, **extra):
    return {
        "start": {"dateTime": f"2026-10-12T{a}:00+08:00"},
        "end": {"dateTime": f"2026-10-12T{b}:00+08:00"},
    } | extra


def test_only_real_busy_time_counts():
    items = [
        timed("09:00", "10:00"),
        timed("09:30", "11:00"),  # overlaps: merged
        timed("13:00", "14:00", transparency="transparent"),  # marked free
        timed("15:00", "16:00", status="cancelled"),
        timed("17:00", "18:00", extendedProperties={"private": {"amifree": "1"}}),  # ours
        {"start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}},  # all day
        {"summary": "Broken"},
    ]
    assert busy_from_items(items) == [
        (datetime(2026, 10, 12, 9, tzinfo=SG), datetime(2026, 10, 12, 11, tzinfo=SG))
    ]


# ---------- the client, against a pretend Google ----------


def client(handler) -> GoogleCalendar:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return handler(request)

    return GoogleCalendar(httpx.AsyncClient(transport=httpx.MockTransport(route)), KEY)


async def test_signing_in_sends_a_signed_request_once():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"accessRole": "writer"})

    google = client(handler)
    assert google.email == BOT
    await google.probe("me@gmail.com")
    await google.probe("me@gmail.com")
    assert seen == ["Bearer tok", "Bearer tok"]


async def test_a_refused_key_is_a_google_error_not_a_crash():
    def route(request):
        return httpx.Response(401, json={"error": "invalid_grant"})

    google = GoogleCalendar(httpx.AsyncClient(transport=httpx.MockTransport(route)), KEY)
    with pytest.raises(GoogleCalendarError):
        await google.probe("me@gmail.com")


@pytest.mark.parametrize(
    ("role", "access"),
    [
        ("owner", Access.WRITE),
        ("writer", Access.WRITE),
        ("writerWithoutPrivateAccess", Access.WRITE),
        ("reader", Access.READ),
        ("freeBusyReader", Access.BUSY_ONLY),
    ],
)
async def test_probe_reads_the_permission_the_student_gave(role, access):
    google = client(lambda r: httpx.Response(200, json={"accessRole": role}))
    assert await google.probe("me@gmail.com") == access


async def test_probe_adds_a_shared_calendar_to_its_list_first():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"accessRole": "writer"})

    assert await client(handler).probe("me@gmail.com") == Access.WRITE
    assert calls == ["GET", "POST"]


async def test_probe_says_none_when_nothing_is_shared():
    assert await client(lambda r: httpx.Response(404)).probe("me@gmail.com") == Access.NONE
    assert await client(lambda r: httpx.Response(403)).probe("me@gmail.com") == Access.NONE


async def test_busy_asks_only_for_times_and_follows_pages():
    pages = []

    def handler(request):
        params = dict(request.url.params)
        pages.append(params)
        assert "summary" not in params["fields"] and "location" not in params["fields"]
        if "pageToken" not in params:
            return httpx.Response(
                200, json={"items": [timed("09:00", "10:00")], "nextPageToken": "n"}
            )
        return httpx.Response(200, json={"items": [timed("11:00", "12:00")]})

    busy = await client(handler).busy(
        "me@gmail.com", datetime(2026, 10, 12, tzinfo=SG), datetime(2026, 10, 13, tzinfo=SG)
    )
    assert len(busy) == 2 and len(pages) == 2


async def test_busy_when_no_longer_shared():
    google = client(lambda r: httpx.Response(404))
    with pytest.raises(GoogleAccessLost):
        await google.busy(
            "me@gmail.com", datetime(2026, 10, 12, tzinfo=SG), datetime(2026, 10, 13, tzinfo=SG)
        )


async def test_adding_an_event_that_exists_updates_it():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(409 if request.method == "POST" else 200, json={})

    assert await client(handler).upsert_event("me@gmail.com", event(), existing=False) is True
    assert calls == ["POST", "PUT"]


async def test_the_code_event_is_marked_free_and_never_counted_as_busy():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "abc123"})

    now = datetime(2026, 10, 12, 9, tzinfo=SG)
    assert await client(handler).add_code("me@gmail.com", "482913", now) == "abc123"
    body = sent[0]
    assert body["summary"] == "AmIFree code 482913" and body["transparency"] == "transparent"
    assert busy_from_items([body]) == []
    with pytest.raises(GoogleAccessLost):
        await client(lambda r: httpx.Response(403)).add_code("me@gmail.com", "1", now)


async def test_an_event_the_student_deleted_is_left_deleted():
    google = client(lambda r: httpx.Response(404))
    assert await google.upsert_event("me@gmail.com", event(), existing=True) is False


async def test_losing_permission_is_reported():
    google = client(lambda r: httpx.Response(403))
    with pytest.raises(GoogleAccessLost):
        await google.upsert_event("me@gmail.com", event(), existing=False)
    with pytest.raises(GoogleAccessLost):
        await google.delete_event("me@gmail.com", event())


async def test_deleting_what_is_already_gone_is_fine():
    await client(lambda r: httpx.Response(410)).delete_event("me@gmail.com", event())
    await client(lambda r: httpx.Response(404)).delete_event("me@gmail.com", "abc")


# ---------- the bot ----------


class FakeGoogle:
    """The Google client's three jobs, remembered in memory."""

    email = BOT

    def __init__(self, access=Access.WRITE):
        self.access = access
        self.busy_times = []
        self.events = {}
        self.calls = []
        self.lost = False

    async def probe(self, email):
        return self.access

    async def busy(self, email, start, end):
        if self.lost:
            raise GoogleAccessLost("lost")
        return list(self.busy_times)

    async def upsert_event(self, email, event, existing):
        if self.lost:
            raise GoogleAccessLost("lost")
        self.calls.append(("upsert", event.title, existing))
        self.events[event.id] = event
        return True

    async def add_code(self, email, code, now):
        self.code = code
        self.calls.append(("code", email))
        return "code-event"

    async def delete_event(self, email, event_id):
        if self.lost:
            raise GoogleAccessLost("lost")
        if event_id == "code-event":
            self.code = None
            return
        self.calls.append(("delete", event_id))
        self.events = {k: v for k, v in self.events.items() if k.replace("-", "") != event_id}


@pytest.fixture
def with_google(service_factory):
    def make(*items, access=Access.WRITE):
        service, repo = service_factory(*items)
        service.google = FakeGoogle(access)
        return service, repo, service.google

    return make


async def connect(service, telegram_id, email, reference, message_id=9):
    """Send the email, then the code AmIFree put in that calendar. Returns the last reply."""
    asked = (await service.message(telegram_id, message_id, email, reference))[0]
    code = getattr(service.google, "code", None)
    if not code or "6-digit" not in asked.text:
        return asked
    return (await service.message(telegram_id, message_id + 100, code, reference))[0]


async def add_dinner(service, reference):
    card = (await service.message(1, 2, DINNER, reference))[0]
    return (await service.callback(1, tap(card, "Add to plans")))[0]


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row]


async def test_google_is_one_button_and_the_choice_comes_after(service_factory, reference):
    service, _ = service_factory()
    await onboard(service, reference)
    pick = (await service.callback(1, "setup:calendar"))[0]
    assert labels(pick) == ["🍎 iPhone / iCloud", "📅 Google", "📧 Outlook", "« Back"]
    # Without the bot's Google account there is only the link.
    link_only = (await service.callback(1, "setup:google"))[0]
    assert "gcal:start" not in str(link_only.keyboard)
    service.google = FakeGoogle()
    choice = (await service.callback(1, "setup:google"))[0]
    assert labels(choice) == [
        "🔗 Share it (adds plans)",
        "📋 Paste a link (read only)",
        "« Other calendars",
    ]
    link = (await service.callback(1, "setup:googlelink"))[0]
    assert "Secret address" in link.text or "link" in link.text.lower()


async def test_share_steps_are_phone_first_with_a_computer_switch(with_google, reference):
    service, _, _ = with_google()
    await onboard(service, reference)
    default = await service.callback(1, "gcal:start")
    assert "Google Calendar app on your phone" in default[0].text
    assert default[0].parse_mode == "HTML"
    labels = [b.text for row in default[0].keyboard.inline_keyboard for b in row]
    assert "💻 On a computer instead?" in labels
    assert len(default) == 2 and len(default[1].photos) == 5
    assert all(ph.startswith(b"\x89PNG") for ph in default[1].photos)
    computer = (await service.callback(1, "gcal:computer"))[0]
    assert "calendar.google.com" in computer.text
    clabels = [b.text for row in computer.keyboard.inline_keyboard for b in row]
    assert "📱 On my phone instead?" in clabels
    back = (await service.callback(1, "gcal:start"))[0]
    assert "Google Calendar app on your phone" in back.text


async def test_the_steps_show_the_bots_google_email(with_google, reference):
    service, _, _ = with_google()
    await onboard(service, reference)
    steps = (await service.callback(1, "gcal:start"))[0]
    assert f"<code>{BOT}</code>" in steps.text and steps.parse_mode == "HTML" and steps.edit
    assert "Make changes" in steps.text


async def test_connecting_adds_current_plans_and_counts_busy_time(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await add_dinner(service, reference)
    google.busy_times = [(reference + timedelta(days=1), reference + timedelta(days=1, hours=2))]

    reply = await connect(service, 1, "Me@Gmail.com", reference, 9)

    assert "Connected to me@gmail.com" in reply.text and "added your 1 current plan" in reply.text
    assert google.calls == [("code", "me@gmail.com"), ("upsert", "CCA dinner", False)]
    connection = repo.google[repo.users[1].id]
    assert connection.calendar_email == "me@gmail.com" and repo.users[1].google_connected
    (calendar,) = await repo.list_calendars(repo.users[1].id)
    assert calendar.label == "Google Calendar" and repo.calendar_blocks[calendar.id]


async def test_a_calendar_that_is_not_shared_yet(with_google, reference):
    service, repo, _ = with_google(access=Access.NONE)
    await onboard(service, reference)
    reply = await connect(service, 1, "me@gmail.com", reference, 9)
    assert "can't see that calendar yet" in reply.text and "Show the steps again" in labels(reply)
    assert not repo.google and not repo.users[1].google_connected


@pytest.mark.parametrize(
    ("access", "words"),
    [
        (Access.READ, "can't change them"),
        (Access.BUSY_ONLY, "when you're busy, but not your events"),
    ],
)
async def test_view_only_permission_asks_for_make_changes(with_google, reference, access, words):
    service, repo, _ = with_google(access=access)
    await onboard(service, reference)
    reply = await connect(service, 1, "me@gmail.com", reference, 9)
    assert words in reply.text and "Make changes" in reply.text
    assert not repo.google


async def test_an_email_is_just_a_message_when_google_is_off(service_factory, reference):
    service, repo = service_factory(extraction(kind="unknown"))
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    assert not repo.google


async def test_new_plans_edits_and_removals_follow_the_student(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    user_id = repo.users[1].id

    await add_dinner(service, reference)
    assert await service.google_sync.sync(user_id) == 1
    assert google.calls[-1] == ("upsert", "CCA dinner", False)

    assert await service.google_sync.sync(user_id) == 0  # nothing changed: nothing sent

    stored = next(iter(repo.events.values()))
    edited = (await service.apply_text_edit(repo.users[1], stored, "location", "UTown"))[0]
    assert await service.google_sync.sync(user_id) == 1
    assert google.calls[-1] == ("upsert", "CCA dinner", True)
    assert next(iter(google.events.values())).location == "UTown"

    removed = (await service.callback(1, tap(edited, "Remove")))[0]
    assert "Removed" in removed.text
    await service.google_sync.sync(user_id)
    assert google.calls[-1][0] == "delete" and not google.events
    assert not await repo.google_links(user_id)

    await service.callback(1, tap(removed, "↩️ Undo"))  # Undo puts it back in Google as well
    await service.google_sync.sync(user_id)
    assert google.calls[-1] == ("upsert", "CCA dinner", False) and len(google.events) == 1


async def test_a_tap_updates_google_in_the_background(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    await add_dinner(service, reference)
    service.google_sync_soon(1)
    await service.google_sync.drain()
    assert google.calls == [("code", "me@gmail.com"), ("upsert", "CCA dinner", False)]


async def test_people_who_did_not_connect_cost_nothing(service_factory, reference):
    service, repo = service_factory(extraction())
    service.google = FakeGoogle()
    await onboard(service, reference)
    await add_dinner(service, reference)
    service.google_sync_soon(1)
    await service.google_sync.drain()
    assert service.google.calls == []


async def test_the_background_check_catches_changes_made_by_someone_else(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await add_dinner(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    google.calls.clear()
    stored = next(iter(repo.events.values()))
    # An organiser moves the plan: the student did nothing, so only the tick can notice.
    repo.events[stored.id] = stored.model_copy(update={"version": stored.version + 1})
    service.clock = lambda: reference + timedelta(minutes=30)
    assert await service.google_sync.sync_due() == 1
    assert google.calls == [("upsert", "CCA dinner", True)]
    assert await service.google_sync.sync_due() == 0  # just done: not due again yet


async def test_losing_permission_is_shown_and_clears_stale_busy_times(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    user_id = repo.users[1].id
    (calendar,) = await repo.list_calendars(user_id)
    google.busy_times = [(reference, reference + timedelta(hours=1))]
    google.lost = True

    await add_dinner(service, reference)
    await service.google_sync.sync(user_id)
    assert "Share it with AmIFree again" in repo.google[user_id].error

    service.clock = lambda: reference + timedelta(hours=2)
    await service.refresh_calendars()
    assert repo.calendar_blocks[calendar.id] == []
    status = (await service.command(1, "calendar"))[0]
    assert "⚠️" in status.text and "Share it with AmIFree again" in status.text


async def test_removing_the_google_calendar_disconnects_it(with_google, reference):
    service, repo, google = with_google(extraction())
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference)
    status = (await service.command(1, "calendar"))[0]
    assert "Plans you say Going to are added here" in status.text

    gone = (await service.callback(1, tap(status, "Remove Google Calendar")))[0]

    assert "stay in your calendar" in gone.text
    assert not repo.google and not repo.users[1].google_connected
    await add_dinner(service, reference)
    service.google_sync_soon(1)
    await service.google_sync.drain()
    assert [c for c in google.calls if c[0] != "code"] == []


async def test_connecting_a_different_email_replaces_the_old_one(with_google, reference):
    service, repo, _ = with_google()
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    await connect(service, 1, "other@gmail.com", reference, 10)
    user_id = repo.users[1].id
    assert repo.google[user_id].calendar_email == "other@gmail.com"
    assert len(await repo.list_calendars(user_id)) == 1


async def test_connecting_needs_the_code_from_that_calendar(with_google, reference):
    service, repo, google = with_google()
    await onboard(service, reference)
    asked = (await service.message(1, 9, "me@gmail.com", reference))[0]
    assert "6-digit" in asked.text and google.code and repo.users[1].id not in repo.google
    # Someone who can't open the calendar can only guess: five wrong tries end it.
    wrong = "000000" if google.code != "000000" else "111111"
    for n in range(4):
        reply = (await service.message(1, 20 + n, wrong, reference))[0]
        assert "not the code" in reply.text
    reply = (await service.message(1, 30, wrong, reference))[0]
    assert "last try" in reply.text and google.code is None  # the code event is removed
    assert repo.users[1].id not in repo.google
    # The real owner reads the code in their calendar and sends it back.
    await service.message(1, 31, "me@gmail.com", reference)
    done = (await service.message(1, 32, f" {google.code} ", reference))[0]
    assert "Connected to me@gmail.com" in done.text and repo.users[1].id in repo.google
    assert google.code is None


async def test_a_code_expires_and_cancel_removes_it(with_google, reference):
    service, repo, google = with_google()
    await onboard(service, reference)
    await service.message(1, 9, "me@gmail.com", reference)
    code = google.code
    service.clock = lambda: reference + timedelta(minutes=31)
    late = (await service.message(1, 10, code, reference))[0]
    assert "expired" in late.text and repo.users[1].id not in repo.google
    await service.message(1, 11, "me@gmail.com", reference)
    await service.command(1, "cancel")
    assert google.code is None and repo.users[1].pending is None


async def test_someone_elses_connected_calendar_cannot_be_claimed(with_google, reference):
    service, repo, google = with_google()
    await onboard(service, reference)
    await onboard(service, reference, telegram_id=2)
    await connect(service, 1, "me@gmail.com", reference, 9)
    calls = len(google.calls)
    reply = (await service.message(2, 9, "Me@gmail.com", reference))[0]
    assert "another AmIFree account" in reply.text
    assert repo.users[2].id not in repo.google and len(google.calls) == calls
    assert not await repo.list_calendars(repo.users[2].id)


async def test_cards_say_plans_sync_once_connected(with_google, reference):
    service, repo, _ = with_google(extraction())
    await onboard(service, reference)
    await connect(service, 1, "me@gmail.com", reference, 9)
    added = await add_dinner(service, reference)
    assert "Syncing to your calendar automatically" in added.text
    assert "/calendar puts every plan in your iPhone calendar" not in added.text


async def test_the_steps_come_with_a_picture_of_the_sharing_box(with_google, reference):
    service, _, _ = with_google()
    await onboard(service, reference)
    replies = await service.callback(1, "gcal:start")
    assert replies[0].edit and "Share your Google Calendar" in replies[0].text
    assert len(replies[1].photos) == 5
    assert all(p.startswith(b"\x89PNG") for p in replies[1].photos)
    assert len(replies[1].text) <= 1024  # Telegram's caption limit
