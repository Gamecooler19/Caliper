"""Bootstrap the first admin account.

``POST /api/v1/admin/users`` needs an admin session, so the first admin
has to be created out of band:

    docker compose exec -e PYTHONPATH=backend api python -m app.auth.create_admin admin@example.com

Prints a random initial password exactly once to stdout (only its
argon2 hash is stored). The account must change that password and
enroll TOTP on first login, like any other account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.auth.models import User, UserRole
from app.auth.passwords import generate_initial_password, hash_password
from app.db import build_engine, build_session_factory


async def create_admin(email: str) -> str | None:
    """Create the admin and return its initial password, or None if the
    email already exists."""

    email = email.strip().lower()
    engine = build_engine()
    try:
        factory = build_session_factory(engine)
        async with factory() as db:
            if (await db.execute(select(User.id).where(User.email == email))).first():
                return None
            password = generate_initial_password()
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    role=UserRole.ADMIN,
                    totp_enabled=False,
                    must_change_password=True,
                    is_active=True,
                )
            )
            await db.commit()
            return password
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first Caliper admin account.")
    parser.add_argument("email")
    args = parser.parse_args(argv)

    password = asyncio.run(create_admin(args.email))
    if password is None:
        print(f"A user with email {args.email.strip().lower()} already exists.", file=sys.stderr)
        return 1
    print(f"Created admin {args.email.strip().lower()}")
    print(f"Initial password (shown once): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
