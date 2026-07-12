import pytest
from pydantic import ValidationError

from vinted_monitor.core.config import Settings


def test_detail_required_fields_accept_configured_subset() -> None:
    settings = Settings(vinted_detail_required_fields="title,description,photos")

    assert settings.vinted_detail_required_fields == "title,description,photos"


def test_detail_performance_modes_default_to_observation_and_serial() -> None:
    settings = Settings(_env_file=None)

    assert settings.vinted_detail_fetch_mode == "serial"
    assert settings.vinted_detail_early_filter_mode == "shadow"
    assert settings.egress_diagnostic_reuse_ttl_seconds == 300


def test_detail_required_fields_reject_unknown_field() -> None:
    with pytest.raises(ValidationError, match="unsupported fields: seller_password"):
        Settings(vinted_detail_required_fields="title,seller_password")


def test_detail_retry_backoffs_match_attempt_budget() -> None:
    with pytest.raises(ValidationError, match="one delay per retry"):
        Settings(vinted_detail_max_attempts=3, vinted_detail_retry_backoffs_seconds=(30,))


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
    settings = Settings(_env_file=None, app_env="production", app_secret_key="x" * 32)

    assert settings.app_env == "production"
