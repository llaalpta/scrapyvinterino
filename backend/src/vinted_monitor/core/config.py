from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    vinted_detail_concurrency: int = 2

    # Worker consumer (Producer-Consumer pattern)
    worker_consumer_count: int = 2
    worker_task_queue_key: str = "vinted:task_queue"
    worker_blpop_timeout_seconds: int = 5
    worker_max_retry_attempts: int = 3

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

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
