from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@db:5432/chat"
    # NullPool: no connections shared between event loops (used by the test suite).
    db_disable_pool: bool = False

    upload_dir: Path = Path("/data/uploads")

    # Comma-separated list of allowed browser origins (SPA, API and WS share one origin in compose).
    app_origin: str = "http://localhost:8080"

    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    mail_from: str = "Chat <no-reply@chat.local>"
    mail_backend: Literal["smtp", "memory"] = "smtp"

    # "Keep me signed in": persistent cookie + server TTL in days (sliding).
    session_ttl_days: int = 30
    # Without "Keep me signed in": browser-session cookie + server TTL in hours (sliding).
    session_short_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 60
    log_level: str = "INFO"

    @property
    def allowed_origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.app_origin.split(",") if o.strip()}

    @property
    def public_origin(self) -> str:
        """Origin used to build links in e-mails (first entry of APP_ORIGIN)."""
        return self.app_origin.split(",")[0].strip().rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
