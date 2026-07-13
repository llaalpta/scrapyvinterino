from __future__ import annotations

import argparse
import getpass

from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import create_local_user


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local PWA user without exposing the password")
    parser.add_argument("--email", required=True, help="Local login email")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match")

    try:
        with SessionLocal() as db:
            user = create_local_user(db, email=args.email, password=password)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Created local user {user.email}")


if __name__ == "__main__":
    main()
