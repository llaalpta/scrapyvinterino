from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://vinted:vinted@postgres:5432/vinted_monitor"
    backend_cors_origins: str = "http://localhost:5173"

    vinted_base_url: AnyHttpUrl = "https://www.vinted.es"
    vinted_proxy_enabled: bool = False
    vinted_proxy_url: str | None = None
    vinted_request_timeout_ms: int = 15000
    vinted_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )

    scheduler_enabled: bool = False
    log_level: str = "INFO"

    vinted_auth_enabled: bool = False
    vinted_auth_cookie: str | None = Field(default=None, repr=False)
    vinted_auth_csrf_token: str | None = Field(default=None, repr=False)
    action_requests_enabled: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
