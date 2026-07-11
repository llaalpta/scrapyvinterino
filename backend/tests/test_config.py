import pytest
from pydantic import ValidationError

from vinted_monitor.core.config import Settings


def test_detail_required_fields_accept_configured_subset() -> None:
    settings = Settings(vinted_detail_required_fields="title,description,photos")

    assert settings.vinted_detail_required_fields == "title,description,photos"


def test_detail_required_fields_reject_unknown_field() -> None:
    with pytest.raises(ValidationError, match="unsupported fields: seller_password"):
        Settings(vinted_detail_required_fields="title,seller_password")


def test_detail_retry_backoffs_match_attempt_budget() -> None:
    with pytest.raises(ValidationError, match="one delay per retry"):
        Settings(vinted_detail_max_attempts=3, vinted_detail_retry_backoffs_seconds=(30,))
