import asyncio

import httpx
import pytest
from telegram.ext import Application

import app.main as main_module
from app.config import Settings
from app.main import create_app
from tests.fakes import MemoryRepository
from tests.telegram_transport import TelegramTransport


@pytest.mark.parametrize("mode", ["polling", "webhook"])
async def test_real_application_startup_and_shutdown(monkeypatch, mode):
    transport, poll_transport = TelegramTransport(), TelegramTransport()
    repo = MemoryRepository()
    cleanups = []

    async def cleanup(days):
        cleanups.append(days)

    repo.cleanup = cleanup

    async def fake_create(*args, **kwargs):
        return object()

    monkeypatch.setattr(main_module, "acreate_client", fake_create)
    monkeypatch.setattr(main_module, "SupabaseRepository", lambda *args: repo)
    monkeypatch.setattr(
        main_module,
        "telegram_builder",
        lambda: (
            Application.builder()
            .concurrent_updates(False)
            .request(transport)
            .get_updates_request(poll_transport)
        ),
    )
    settings = Settings(
        _env_file=None,
        bot_mode=mode,
        telegram_bot_token="123456:fake-test-token",
        openai_api_key="test-key",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="test-key",
        app_base_url="https://example.test",
        telegram_webhook_secret="test_secret_123456",
        mini_app_url="https://example.test/app/",
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        assert app.state.ready and app.state.telegram.running
        assert cleanups == [7]
        assert (await client.get("/readyz")).status_code == 200
        calls = dict(transport.calls)
        assert "setMyCommands" in calls
        # The menu button lists commands; the Mini App opens from buttons in /start.
        assert "COMMANDS" in str(calls["setChatMenuButton"])
        if mode == "webhook":
            assert calls["setWebhook"]["url"] == "https://example.test/telegram/webhook"
            assert calls["setWebhook"]["secret_token"] == "test_secret_123456"
        else:
            assert "deleteWebhook" in calls and "setWebhook" not in calls
            assert app.state.telegram.updater.running
            await asyncio.sleep(0.03)
            assert any(name == "getUpdates" for name, _ in poll_transport.calls)
    assert not app.state.ready and not app.state.telegram.running
    assert not app.state.telegram.updater.running


async def test_startup_database_error_is_sanitized(monkeypatch):
    repo = MemoryRepository()
    repo.available = False

    async def fake_create(*args, **kwargs):
        return object()

    monkeypatch.setattr(main_module, "acreate_client", fake_create)
    monkeypatch.setattr(main_module, "SupabaseRepository", lambda *args: repo)
    app = create_app(
        Settings(
            _env_file=None,
            telegram_bot_token="123456:test",
            openai_api_key="test-key",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="test-key",
        )
    )
    with pytest.raises(RuntimeError, match="Apply app/db/schema.sql"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.parametrize(
    "settings",
    [
        {
            "bot_mode": "webhook",
            "app_base_url": "http://insecure.test",
            "telegram_webhook_secret": "test_secret_123456",
        },
        {
            "bot_mode": "webhook",
            "app_base_url": "https://example.test",
            "telegram_webhook_secret": "short",
        },
    ],
)
def test_webhook_misconfiguration_rejected(settings):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **settings)


def test_settings_errors_do_not_print_secrets():
    with pytest.raises(ValueError) as error:
        Settings(
            _env_file=None,
            bot_mode="webhook",
            app_base_url="http://insecure.test",
            telegram_bot_token="private-token-value",
            openai_api_key="private-key-value",
        )
    assert "private-token-value" not in str(error.value)
    assert "private-key-value" not in str(error.value)


async def test_mini_app_files_are_compressed_and_cached():
    dist = main_module.Path(main_module.__file__).resolve().parents[1] / "web" / "dist"
    scripts = sorted((dist / "assets").glob("*.js")) if (dist / "index.html").is_file() else []
    if not scripts:
        pytest.skip("Mini App not built")
    app = create_app(Settings(_env_file=None, telegram_bot_token="123456:fake-test-token"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        script = await client.get(
            f"/app/assets/{scripts[0].name}", headers={"Accept-Encoding": "gzip"}
        )
        assert script.headers["content-encoding"] == "gzip"
        assert "immutable" in script.headers["cache-control"]
        page = await client.get("/app/")
        assert page.headers["cache-control"] == "no-cache"
