"""The out-of-band first-admin bootstrap command."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.auth import create_admin
from app.auth.models import User, UserRole
from app.auth.passwords import verify_password
from app.db import Base, build_engine, build_session_factory


@pytest.fixture
def cli_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"

    async def _create():
        engine = build_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())
    monkeypatch.setattr(create_admin, "build_engine", lambda: build_engine(url))
    return url


def _users(url: str) -> list[User]:
    async def _all():
        engine = build_engine(url)
        try:
            async with build_session_factory(engine)() as session:
                return list((await session.execute(select(User))).scalars())
        finally:
            await engine.dispose()

    return asyncio.run(_all())


def test_creates_an_admin_with_a_printed_one_time_password(cli_db, capsys):
    assert create_admin.main(["  Boss@Example.com "]) == 0

    out = capsys.readouterr().out
    password = out.split("Initial password (shown once): ")[1].strip()
    (user,) = _users(cli_db)
    assert user.email == "boss@example.com"
    assert user.role == UserRole.ADMIN
    assert user.must_change_password is True
    assert user.totp_enabled is False
    assert user.password_hash != password
    assert verify_password(user.password_hash, password)


def test_refuses_to_create_a_duplicate(cli_db, capsys):
    assert create_admin.main(["boss@example.com"]) == 0
    capsys.readouterr()

    assert create_admin.main(["BOSS@example.com"]) == 1

    assert "already exists" in capsys.readouterr().err
    assert len(_users(cli_db)) == 1
