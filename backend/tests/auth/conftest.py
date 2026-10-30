"""Fixtures for the Module 6 auth tests.

The app runs its real lifespan against a disposable SQLite file and a
fakeredis client. The client's base URL is https so the Secure session
cookie round-trips through httpx's cookie jar like it would in a
browser.

Everything here lives in this package's own conftest so the existing
test modules (and the root conftest) stay unmodified.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import fakeredis.aioredis
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User, UserRole
from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret
from app.db import Base, build_engine, build_session_factory

#: A throwaway Fernet key, valid format, used only by tests.
TEST_COLUMN_KEY = "SIYuMaqZWr289QZmLQ5W87lcLfjwheEAL4i1IfokErU="

DEFAULT_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def column_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMN_ENCRYPTION_KEY", TEST_COLUMN_KEY)


@dataclass
class AuthEnv:
    client: TestClient
    valkey: Any
    db_url: str

    def run_db(self, fn: Callable[[AsyncSession], Any]) -> Any:
        """Run ``fn(session)`` against the test database on a fresh
        engine and return its result."""

        async def _go() -> Any:
            engine = build_engine(self.db_url)
            try:
                async with build_session_factory(engine)() as session:
                    return await fn(session)
            finally:
                await engine.dispose()

        return asyncio.run(_go())

    def seed_user(
        self,
        email: str,
        *,
        password: str = DEFAULT_PASSWORD,
        role: UserRole = UserRole.ANALYST,
        totp_secret: str | None = None,
        totp_enabled: bool | None = None,
        must_change_password: bool = False,
        is_active: bool = True,
    ) -> uuid.UUID:
        """Insert a user directly. If ``totp_secret`` is given, it is
        stored encrypted and TOTP is enabled unless overridden."""

        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            totp_secret_encrypted=encrypt_secret(totp_secret) if totp_secret else None,
            totp_enabled=bool(totp_secret) if totp_enabled is None else totp_enabled,
            must_change_password=must_change_password,
            is_active=is_active,
        )

        async def _add(session: AsyncSession) -> uuid.UUID:
            session.add(user)
            await session.commit()
            return user.id

        return self.run_db(_add)

    def login_as_other_user(self, email: str, secret: str, password: str = DEFAULT_PASSWORD):
        """A second client (own cookie jar) logged in as ``email``.

        Not entered as a context manager on purpose: the app's lifespan
        is already running via ``self.client``, and re-running it would
        close the shared fake Valkey.
        """

        other = TestClient(
            self.client.app, base_url="https://testserver", raise_server_exceptions=False
        )
        first = other.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert first.status_code == 200, first.text
        second = other.post(
            "/api/v1/auth/login/totp",
            json={
                "challenge_token": first.json()["challenge_token"],
                "code": pyotp.TOTP(secret).now(),
            },
        )
        assert second.status_code == 200, second.text
        return other

    def get_user(self, email: str) -> User:
        async def _get(session: AsyncSession) -> User:
            return (await session.execute(select(User).where(User.email == email))).scalar_one()

        return self.run_db(_get)


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth_test.db'}"
    engine = build_engine(db_url)

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())

    valkey = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.main.build_engine", lambda: build_engine(db_url))
    monkeypatch.setattr("app.main.build_valkey_client", lambda: valkey)

    from app.main import app

    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield AuthEnv(client=client, valkey=valkey, db_url=db_url)


class TotpCodes:
    """Hands out valid, distinct TOTP codes for a secret.

    The server rejects a code it has already accepted (replay
    protection) and accepts the current step and its neighbours, so
    successive logins in one test use the current and next step. Each
    secret supports two successful verifications per test.
    """

    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def next(self, secret: str) -> str:
        n = self._used.get(secret, 0)
        self._used[secret] = n + 1
        when = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30 * n)
        return pyotp.TOTP(secret).at(when)


@pytest.fixture
def totp_codes() -> TotpCodes:
    return TotpCodes()


def login_fully(
    env: AuthEnv, codes: TotpCodes, email: str, secret: str, password: str = DEFAULT_PASSWORD
):
    """Run the full two-step login for an enrolled user; return the
    /login/totp response (the session cookie is now in the client's jar)."""

    first = env.client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert first.status_code == 200, first.text
    return env.client.post(
        "/api/v1/auth/login/totp",
        json={"challenge_token": first.json()["challenge_token"], "code": codes.next(secret)},
    )
