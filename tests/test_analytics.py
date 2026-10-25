import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services import analytics


class Recorder:
    def __init__(self):
        self.events = []

    def capture(self, event, distinct_id, properties):
        self.events.append((event, distinct_id, properties))

    def shutdown(self):
        pass


@pytest.fixture
def recorder(monkeypatch):
    client = Recorder()
    monkeypatch.setattr(analytics, "_client", client)
    monkeypatch.setattr(analytics, "_secret", b"test")
    return client


def test_off_without_a_key():
    analytics.configure(Settings(_env_file=None, bot_mode="disabled"))
    assert analytics._client is None
    analytics.identify(42)
    analytics.track("start")  # no client: nothing happens, nothing raises


def test_button_names_drop_ids():
    assert analytics.button("menu:free") == "menu:free"
    assert analytics.button("c:going:3f2a9c1e-0000-4000-8000-000000000000") == "c:going"
    assert analytics.button("confirm:Zx81_kQ2-aa") == "confirm"
    assert analytics.button("plans:2") == "plans"
    assert analytics.button("12345") == "other"


def test_events_use_a_hash_not_the_telegram_id(recorder):
    analytics.identify(123456789)
    analytics.track("command", name="start")
    event, person, properties = recorder.events[0]
    assert event == "command" and properties == {"name": "start"}
    assert "123456789" not in person and len(person) == 24


def test_a_failing_client_never_raises(recorder, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(recorder, "capture", boom)
    analytics.identify(1)
    analytics.track("start")


def test_configure_with_key(caplog):
    caplog.set_level("INFO")
    settings = Settings(
        _env_file=None,
        bot_mode="disabled",
        # Pasted into a hosting dashboard with a stray space and newline.
        posthog_api_key=SecretStr(" phc_test1234\n"),
        posthog_host="https://eu.i.posthog.com/ ",
        telegram_bot_token=SecretStr("token"),
    )
    analytics.configure(settings)
    try:
        assert analytics._client is not None
        assert analytics._client.api_key == "phc_test1234"
        assert analytics._client.host == "https://eu.i.posthog.com"
    finally:
        analytics._client.disabled = True
        analytics.shutdown()
    assert analytics._client is None
    assert "analytics_on host=https://eu.i.posthog.com key=phc_...1234" in caplog.text


def test_upload_failures_are_logged(caplog):
    analytics._upload_failed(RuntimeError("[PostHog] invalid key (401)"), [{}, {}])
    assert "analytics_failed error_type=RuntimeError events=2" in caplog.text


def test_says_when_off(caplog):
    caplog.set_level("INFO")
    analytics.configure(Settings(_env_file=None, bot_mode="disabled"))
    assert "analytics_off reason=no_posthog_api_key" in caplog.text
