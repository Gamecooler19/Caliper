"""FastAPI dependencies for session authentication and RBAC.

``require_api_key_or_session`` is the dependency for
``POST /api/v1/classify``. It leaves the Module 4 API-key path exactly
as it was: a request with no session cookie goes straight to
:func:`app.api.auth.require_api_key`, unchanged.

A request that carries a *valid* session cookie is authenticated as
that user even if an ``X-API-Key`` header is also present. This matters
behind Caddy, which injects the shared key on every ``/api/v1/*``
request: without session-first resolution a logged-in browser would
always be attributed to the API key. An invalid or expired cookie falls
through to the API-key check rather than failing outright, so the
existing key-injecting deployment keeps working.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Header, Request

from app.api.auth import require_api_key
from app.auth.errors import ForbiddenError, PasswordChangeRequiredError, SessionRequiredError
from app.auth.models import User, UserRole
from app.auth.sessions import (
    SESSION_COOKIE_NAME,
    destroy_session,
    refresh_session,
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    role: UserRole
    must_change_password: bool
    session_id: str


async def resolve_session_user(request: Request) -> AuthenticatedUser | None:
    """The user behind this request's session cookie, or None.

    Refreshes the session's sliding TTL. The user row is the source of
    truth for ``is_active``, ``role`` and ``must_change_password`` (the
    role copied into the session JSON is informational), so a
    deactivation or role change takes effect on the very next request.
    """

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None

    valkey = request.app.state.valkey_client
    session = await refresh_session(valkey, session_id)
    if session is None:
        return None

    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, session.user_id)

    if user is None or not user.is_active:
        await destroy_session(valkey, session_id)
        return None

    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
        session_id=session_id,
    )


async def require_session_allow_password_change(request: Request) -> AuthenticatedUser:
    """A valid session, *without* the must-change-password gate. Used
    only by the routes a user must reach to satisfy that gate
    (``/auth/me``, ``/auth/logout``, ``/auth/change-password``)."""

    user = await resolve_session_user(request)
    if user is None:
        raise SessionRequiredError()
    return user


async def require_session(
    user: AuthenticatedUser = Depends(require_session_allow_password_change),  # noqa: B008
) -> AuthenticatedUser:
    """A valid session whose account has no pending password change."""

    if user.must_change_password:
        raise PasswordChangeRequiredError()
    return user


def require_role(role: UserRole | str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """Dependency factory: a valid session whose role is exactly ``role``."""

    required = UserRole(role)

    async def _dependency(
        user: AuthenticatedUser = Depends(require_session),  # noqa: B008
    ) -> AuthenticatedUser:
        if user.role != required:
            raise ForbiddenError()
        return user

    return _dependency


async def require_api_key_or_session(
    request: Request, x_api_key: str | None = Header(default=None)
) -> str:
    """Authenticate ``POST /api/v1/classify`` by session or API key.

    Returns the actor string for the audit log (the user's email for a
    session, ``"api-client"`` for the API key) and sets
    ``request.state.user_id`` to the user's id for a session or ``None``
    for the API key.
    """

    request.state.user_id = None

    if request.cookies.get(SESSION_COOKIE_NAME):
        user = await resolve_session_user(request)
        if user is not None:
            if user.must_change_password:
                raise PasswordChangeRequiredError()
            request.state.user_id = user.id
            return user.email

    return await require_api_key(x_api_key)
