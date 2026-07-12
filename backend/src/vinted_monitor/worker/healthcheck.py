from vinted_monitor.core.config import get_settings
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler_liveness import scheduler_worker_availability


def main() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        if not scheduler_worker_availability(db, settings).available:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
