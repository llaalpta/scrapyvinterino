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
