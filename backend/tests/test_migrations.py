from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_chrome146_migration_uses_canonical_invalid_vinted_session_status() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0010_chrome146_runtime_profile.py").read_text(encoding="utf-8")

    assert "SET status = 'invalid'," in migration
    assert "SET status = 'invalidated'," not in migration


def test_status_normalization_migration_cleans_existing_invalidated_rows() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0011_normalize_vinted_session_invalid_status.py").read_text(
        encoding="utf-8"
    )

    assert "WHERE status = 'invalidated'" in migration
    assert "SET status = 'invalid'," in migration


def test_honest_found_metrics_migration_removes_historical_event_field() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0020_honest_found_metrics.py").read_text(encoding="utf-8")

    assert "details = details - 'items_new'" in migration


def test_scheduler_ui_gate_migration_removes_persisted_enabled_field() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0021_remove_scheduler_ui_gate.py").read_text(encoding="utf-8")

    assert "value = value - 'enabled'" in migration
    assert "WHERE key = 'scheduler' AND value ? 'enabled'" in migration


def test_proxy_only_catalog_migration_removes_persisted_direct_fields() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0022_proxy_only_catalog_egress.py").read_text(encoding="utf-8")

    assert "value - 'allow_direct_without_proxy' - 'direct_max_concurrent_runs'" in migration
    assert "WHERE key = 'scheduler'" in migration


def test_proxy_test_telemetry_migration_drops_obsolete_columns() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0023_remove_proxy_test_telemetry.py").read_text(encoding="utf-8")

    assert 'op.drop_column("proxy_profiles", "last_test_status")' in migration
    assert 'op.drop_column("proxy_profiles", "last_test_ip")' in migration
    assert 'op.drop_column("proxy_profiles", "last_test_error")' in migration


def test_proxy_sticky_contract_migration_backfills_non_null_profile_fields() -> None:
    migration = (BACKEND_ROOT / "alembic/versions/0024_proxy_sticky_contract.py").read_text(
        encoding="utf-8"
    )

    assert 'STICKY_USERNAME_TEMPLATE = "{username};sessid.{session_id}"' in migration
    assert "STICKY_TTL_MINUTES = 25" in migration
    assert 'op.alter_column("proxy_profiles", "sticky_username_template", nullable=False)' in migration
    assert 'op.alter_column("proxy_profiles", "sticky_ttl_minutes", nullable=False)' in migration
    assert "sticky_ttl_minutes BETWEEN 1 AND 120" in migration
