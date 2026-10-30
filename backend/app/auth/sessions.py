"""Valkey-backed sessions and short-lived login tokens.

A session is JSON ``{user_id, role, created_at, last_seen_at}`` under
``session:{session_id}`` with a 12-hour sliding TTL, refreshed on every
authenticated request. A per-user set ``user_sessions:{user_id}`` lets an
admin action (deactivation, TOTP reset) destroy all of a user's sessions.

The login flow also needs two short-lived, single-purpose tokens
(TOTP challenge after a correct password; enrollment token for an
account with no TOTP yet). They live in the same store under their own
key prefixes.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import uuid
from dataclasses import dataclass

import redis.asyncio as redis_asyncio

SESSION_TTL_SECONDS = 12 * 60 * 60
SESSION_COOKIE_NAME = "caliper_session"

CHALLENGE_TTL_SECONDS = 5 * 60
ENROLLMENT_TTL_SECONDS = 15 * 60

KIND_TOTP_CHALLENGE = "totp_challenge"
KIND_TOTP_ENROLLMENT = "totp_enroll"


@dataclass(frozen=True)
class SessionData:
    session_id: str
    user_id: uuid.UUID
    role: str
    created_at: str
    last_seen_at: str


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


def _user_sessions_key(user_id: uuid.UUID) -> str:
    return f"user_sessions:{user_id}"


def _parse(session_id: str, raw: str) -> SessionData:
    data = json.loads(raw)
    return SessionData(
        session_id=session_id,
        user_id=uuid.UUID(data["user_id"]),
        role=data["role"],
        created_at=data["created_at"],
        last_seen_at=data["last_seen_at"],
    )


async def create_session(client: redis_asyncio.Redis, user_id: uuid.UUID, role: str) -> str:
    session_id = secrets.token_urlsafe(32)
    now = _now()
    payload = json.dumps(
        {"user_id": str(user_id), "role": role, "created_at": now, "last_seen_at": now}
    )
    await client.set(_session_key(session_id), payload, ex=SESSION_TTL_SECONDS)
    await client.sadd(_user_sessions_key(user_id), session_id)  # type: ignore[misc]
    await client.expire(_user_sessions_key(user_id), SESSION_TTL_SECONDS)
    return session_id


async def read_session(client: redis_asyncio.Redis, session_id: str) -> SessionData | None:
    """Read a session without refreshing it."""

    raw = await client.get(_session_key(session_id))
    return _parse(session_id, raw) if raw else None


async def refresh_session(client: redis_asyncio.Redis, session_id: str) -> SessionData | None:
    """Read a session and slide its TTL forward. None if absent/expired."""

    session = await read_session(client, session_id)
    if session is None:
        return None
    payload = json.dumps(
        {
            "user_id": str(session.user_id),
            "role": session.role,
            "created_at": session.created_at,
            "last_seen_at": _now(),
        }
    )
    await client.set(_session_key(session_id), payload, ex=SESSION_TTL_SECONDS)
    await client.expire(_user_sessions_key(session.user_id), SESSION_TTL_SECONDS)
    return session


async def destroy_session(client: redis_asyncio.Redis, session_id: str) -> None:
    session = await read_session(client, session_id)
    await client.delete(_session_key(session_id))
    if session is not None:
        await client.srem(_user_sessions_key(session.user_id), session_id)  # type: ignore[misc]


async def destroy_all_sessions_for_user(client: redis_asyncio.Redis, user_id: uuid.UUID) -> None:
    members = await client.smembers(_user_sessions_key(user_id))  # type: ignore[misc]
    for session_id in members:
        await client.delete(_session_key(session_id))
    await client.delete(_user_sessions_key(user_id))


async def destroy_other_sessions_for_user(
    client: redis_asyncio.Redis, user_id: uuid.UUID, keep_session_id: str
) -> None:
    """End every session of ``user_id`` except ``keep_session_id``."""

    members = await client.smembers(_user_sessions_key(user_id))  # type: ignore[misc]
    for session_id in members:
        if session_id != keep_session_id:
            await client.delete(_session_key(session_id))
            await client.srem(_user_sessions_key(user_id), session_id)  # type: ignore[misc]


async def create_login_token(
    client: redis_asyncio.Redis, kind: str, user_id: uuid.UUID, ttl_seconds: int
) -> str:
    token = secrets.token_urlsafe(32)
    await client.set(f"{kind}:{token}", str(user_id), ex=ttl_seconds)
    return token


async def read_login_token(
    client: redis_asyncio.Redis, kind: str, token: str
) -> uuid.UUID | None:
    raw = await client.get(f"{kind}:{token}")
    return uuid.UUID(raw) if raw else None


async def delete_login_token(client: redis_asyncio.Redis, kind: str, token: str) -> None:
    await client.delete(f"{kind}:{token}")
