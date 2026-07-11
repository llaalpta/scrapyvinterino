from functools import lru_cache

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VINTED_DETAIL_FIELD_ALLOWLIST = frozenset(
    {"title", "description", "brand", "size", "status", "price_amount", "currency", "photos"}
)
INSECURE_APP_SECRET_KEYS = frozenset(
    {
        "change-me",
        "replace-with-a-unique-random-secret-of-at-least-32-characters",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://vinted:vinted@postgres:5432/vinted_monitor"
    backend_cors_origins: str = "http://localhost:5173"
    redis_url: str = "redis://redis:6379/0"
    seen_cache_ttl_seconds: int = 86400
    seen_processing_ttl_seconds: int = 120
    seen_cache_max_per_monitor: int = 10000

    vinted_base_url: AnyHttpUrl = "https://www.vinted.es"
    vinted_request_timeout_ms: int = 15000
    vinted_request_retries: int = 1
    vinted_fast_catalog_per_page: int = 5
    vinted_detail_max_candidates_per_run: int = 5
    vinted_detail_concurrency: int = Field(default=1, ge=1)
    vinted_detail_required_fields: str = "title,description,brand,size,status,price_amount,currency,photos"
    vinted_detail_max_attempts: int = Field(default=3, ge=1, le=10)
    vinted_detail_retry_backoffs_seconds: tuple[int, ...] = (30, 120)

    # Worker consumer (Producer-Consumer pattern)
    worker_consumer_count: int = Field(default=2, ge=1, le=32)
    worker_task_queue_key: str = "vinted:task_queue"
    worker_reserve_timeout_seconds: int = Field(default=5, ge=1, le=300)
    worker_max_retry_attempts: int = Field(default=3, ge=1, le=20)

    # curl_cffi / anti-bot evasion
    curl_impersonate_browser: str = "chrome146"
    vinted_direct_catalog_enabled: bool = False
    vinted_target_country_code: str = "ES"
    vinted_target_locale: str = "es-ES"
    vinted_target_accept_language: str = "en-GB,en;q=0.9"
    vinted_target_screen: str = "1920x1080"
    vinted_target_vinted_screen: str = "catalog"
    vinted_prepared_session_required: bool = True
    vinted_session_max_requests: int = 50
    vinted_session_ttl_minutes: int = 120
    vinted_datadome_collector_enabled: bool = True
    vinted_datadome_collector_url: AnyHttpUrl = "https://dd.vinted.lt/js"
    vinted_datadome_collector_default_ddv: str = "5.7.0"
    vinted_datadome_client_key: str | None = Field(default=None, repr=False)
    human_delay_min_seconds: float = 1.2
    human_delay_max_seconds: float = 3.8
    datadome_challenge_penalty_multiplier: int = 2
    proxy_sticky_username_template: str = "{username}-session-{session_id}"
    egress_diagnostic_url: str | None = "https://ipwho.is/"

    scheduler_enabled: bool = False
    scheduler_max_concurrent_runs: int = 2
    scheduler_per_source_concurrency: int = 1
    scheduler_poll_interval_seconds: int = 5
    scheduler_timezone: str = "Europe/Madrid"
    log_level: str = "INFO"

    vinted_auth_enabled: bool = False
    vinted_auth_cookie: str | None = Field(default=None, repr=False)
    vinted_auth_csrf_token: str | None = Field(default=None, repr=False)
    action_requests_enabled: bool = False

    @model_validator(mode="after")
    def validate_detail_retry_config(self) -> "Settings":
        required_fields = {
            field.strip() for field in self.vinted_detail_required_fields.split(",") if field.strip()
        }
        if not required_fields:
            raise ValueError("VINTED_DETAIL_REQUIRED_FIELDS must contain at least one field")
        unknown_fields = required_fields - VINTED_DETAIL_FIELD_ALLOWLIST
        if unknown_fields:
            raise ValueError(
                "VINTED_DETAIL_REQUIRED_FIELDS contains unsupported fields: "
                + ", ".join(sorted(unknown_fields))
            )
        if len(self.vinted_detail_retry_backoffs_seconds) != self.vinted_detail_max_attempts - 1:
            raise ValueError("VINTED_DETAIL_RETRY_BACKOFFS_SECONDS must contain one delay per retry")
        if any(delay < 0 for delay in self.vinted_detail_retry_backoffs_seconds):
            raise ValueError("VINTED_DETAIL_RETRY_BACKOFFS_SECONDS cannot contain negative delays")
        return self

    @model_validator(mode="after")
    def validate_production_secret_key(self) -> "Settings":
        if self.app_env.strip().lower() in {"development", "test"}:
            return self
        secret_key = self.app_secret_key.strip()
        if len(secret_key) < 32 or secret_key.lower() in INSECURE_APP_SECRET_KEYS:
            raise ValueError("APP_SECRET_KEY must be a unique random value of at least 32 characters outside development")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
