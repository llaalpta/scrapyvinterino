import pytest
from pydantic import ValidationError

from vinted_monitor.core.config import Settings


def test_detail_required_fields_accept_configured_subset() -> None:
    settings = Settings(vinted_detail_required_fields="title,description,photos")

    assert settings.vinted_detail_required_fields == "title,description,photos"


def test_detail_performance_modes_default_to_observation_and_serial() -> None:
    settings = Settings(_env_file=None)

    assert settings.vinted_detail_fetch_mode == "serial"
    assert settings.vinted_detail_early_filter_mode == "enforced"
    assert settings.egress_diagnostic_reuse_ttl_seconds == 300


def test_detail_required_fields_reject_unknown_field() -> None:
    with pytest.raises(ValidationError, match="unsupported fields: seller_password"):
        Settings(vinted_detail_required_fields="title,seller_password")


@pytest.mark.parametrize(
    "secret_key",
    [
        "change-me",
        "replace-with-a-unique-random-secret-of-at-least-32-characters",
        "too-short",
    ],
)
def test_production_rejects_insecure_app_secret_key(secret_key: str) -> None:
    with pytest.raises(ValidationError, match="unique random value"):
        Settings(_env_file=None, app_env="production", app_secret_key=secret_key)


def test_production_accepts_non_placeholder_app_secret_key() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="x" * 32,
        backend_cors_origins="https://monitor.example.test",
    )

    assert settings.app_env == "production"


def test_local_development_user_requires_email_and_password_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, local_dev_user_email="operator@example.local")


def test_local_development_user_is_rejected_outside_development() -> None:
    with pytest.raises(ValidationError, match="allowed only in development"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="x" * 32,
            backend_cors_origins="https://monitor.example.test",
            local_dev_user_email="operator@example.local",
            local_dev_user_password="development-password",
        )


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "https://monitor.example.test/path",
        "http://monitor.example.test",
        "https://one.example.test,https://two.example.test",
        "https://monitor.example.test.evil.invalid,https://monitor.example.test/",
    ],
)
def test_production_rejects_non_exact_secure_cors_origins(origins: str) -> None:
    with pytest.raises(ValidationError, match="BACKEND_CORS_ORIGINS"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="x" * 32,
            backend_cors_origins=origins,
        )
