from __future__ import annotations

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import ensure_local_user


def main() -> None:
    settings = get_settings()
    if settings.local_dev_user_email is None or settings.local_dev_user_password is None:
        print("Development local user bootstrap disabled")
        return

    with SessionLocal() as db:
        result = ensure_local_user(
            db,
            email=settings.local_dev_user_email,
            password=settings.local_dev_user_password.get_secret_value(),
        )
    if result.created:
        action = "created"
    elif result.password_updated or result.reactivated:
        action = "updated"
    else:
        action = "already current"
    print(f"Development local user {result.email}: {action}")


if __name__ == "__main__":
    main()
