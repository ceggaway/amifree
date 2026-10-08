from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    telegram_bot_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    bot_mode: Literal["polling", "webhook", "disabled"] = "polling"
    app_base_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    nusmods_academic_year: str = ""
    default_event_duration_minutes: int = Field(default=0, ge=0, le=1440)
    retain_extractions: bool = False
    extraction_retention_days: int = Field(default=7, ge=1, le=90)
    pending_ttl_hours: int = Field(default=24, ge=1, le=168)
    # Other Going plans on the same day before a card warns "Busy day"; 0 disables it.
    busy_day_threshold: int = Field(default=3, ge=0, le=20)
    # Singapore hour from which the night-before summary may be sent.
    nightly_hour: int = Field(default=20, ge=0, le=23)
    # Shared secret for POST /internal/jobs/run; the endpoint is disabled while blank.
    jobs_secret: SecretStr = SecretStr("")
    # HTTPS URL of the Mini App, e.g. https://your-service.onrender.com/app/ . Blank hides it.
    mini_app_url: str = ""
    # Exact origin of a separately hosted dev frontend (e.g. a Vite tunnel). Enables CORS for it.
    mini_app_dev_origin: str = ""
    # Signs Mini App sessions. Blank derives a key from the bot token.
    session_secret: SecretStr = SecretStr("")
    session_ttl: int = Field(default=3600, ge=300, le=86400)
    init_data_max_age: int = Field(default=3600, ge=60, le=86400)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def webhook_settings(self):
        if self.bot_mode == "webhook":
            parsed = urlparse(self.app_base_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Webhook mode requires an HTTPS APP_BASE_URL")
            import re

            if not re.fullmatch(
                r"[A-Za-z0-9_-]{16,256}", self.telegram_webhook_secret.get_secret_value()
            ):
                raise ValueError("Webhook secret must contain 16–256 letters, digits, _ or -")
        if self.mini_app_url and urlparse(self.mini_app_url).scheme != "https":
            raise ValueError("MINI_APP_URL must be an HTTPS URL")
        origin = urlparse(self.mini_app_dev_origin)
        if self.mini_app_dev_origin and (
            origin.scheme not in ("http", "https")
            or not origin.netloc
            or origin.path not in ("", "/")
        ):
            raise ValueError("MINI_APP_DEV_ORIGIN must be an exact origin such as https://host")
        secret = self.session_secret.get_secret_value()
        if secret and len(secret) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return self

    def require_credentials(self):
        missing = [
            name.upper()
            for name in (
                "telegram_bot_token",
                "openai_api_key",
                "supabase_url",
                "supabase_service_role_key",
            )
            if not (
                getattr(self, name).get_secret_value()
                if isinstance(getattr(self, name), SecretStr)
                else getattr(self, name)
            )
        ]
        if missing:
            raise RuntimeError("Configure .env first: " + ", ".join(missing))
        parsed = urlparse(self.supabase_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("SUPABASE_URL must be an HTTPS project URL")
