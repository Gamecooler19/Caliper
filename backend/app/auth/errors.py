"""Auth-specific API errors and their handlers.

Kept separate from ``app.api.errors`` (which owns the classify
endpoint's error mapping) so Module 6 adds handlers without editing
that file. Registered from ``app.main`` via
:func:`register_auth_exception_handlers`.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AuthError(Exception):
    """Base for Module 6 auth errors."""


class InvalidCredentialsError(AuthError):
    """Wrong/unknown email, wrong password, inactive account, or an
    invalid/expired login token. One response for all of them so the
    API does not reveal which accounts exist or are active."""


class InvalidTotpCodeError(AuthError):
    """A TOTP code that did not verify (wrong, expired, or replayed)."""


class SessionRequiredError(AuthError):
    """No valid session on a session-only route."""


class ForbiddenError(AuthError):
    """Authenticated, but the role does not permit this route."""


class ReadOnlyRoleError(AuthError):
    """A session-authenticated role without classify permission (currently
    just ``auditor``) called ``POST /api/v1/classify``. Distinct from
    :class:`ForbiddenError` so the response carries a specific, stable
    error code. The ``CALIPER_API_KEY`` path has no role concept and
    never raises this."""


class LastAdminGuardError(AuthError):
    """Refused: this admin PATCH would leave zero other active admins
    (deactivating the last one, or demoting the last one away from
    ``admin``)."""


class PasswordChangeRequiredError(AuthError):
    """The account must change its password before using this route."""


class NotFoundError(AuthError):
    """The referenced user does not exist."""


class EmailInUseError(AuthError):
    """A user with that email already exists."""


class AuthRateLimitedError(AuthError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Too many attempts; retry after {retry_after_seconds}s")


def register_auth_exception_handlers(app: FastAPI) -> None:
    async def _invalid_credentials(request: Request, exc: InvalidCredentialsError):
        return JSONResponse(status_code=401, content={"error": "invalid_credentials"})

    async def _invalid_totp(request: Request, exc: InvalidTotpCodeError):
        return JSONResponse(status_code=401, content={"error": "invalid_totp_code"})

    async def _session_required(request: Request, exc: SessionRequiredError):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    async def _forbidden(request: Request, exc: ForbiddenError):
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    async def _read_only_role(request: Request, exc: ReadOnlyRoleError):
        return JSONResponse(status_code=403, content={"error": "read_only_role"})

    async def _last_admin_guard(request: Request, exc: LastAdminGuardError):
        return JSONResponse(status_code=400, content={"error": "last_admin_guard"})

    async def _password_change(request: Request, exc: PasswordChangeRequiredError):
        return JSONResponse(status_code=403, content={"error": "password_change_required"})

    async def _not_found(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    async def _email_in_use(request: Request, exc: EmailInUseError):
        return JSONResponse(status_code=409, content={"error": "email_in_use"})

    async def _rate_limited(request: Request, exc: AuthRateLimitedError):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    app.add_exception_handler(InvalidCredentialsError, _invalid_credentials)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidTotpCodeError, _invalid_totp)  # type: ignore[arg-type]
    app.add_exception_handler(SessionRequiredError, _session_required)  # type: ignore[arg-type]
    app.add_exception_handler(ForbiddenError, _forbidden)  # type: ignore[arg-type]
    app.add_exception_handler(ReadOnlyRoleError, _read_only_role)  # type: ignore[arg-type]
    app.add_exception_handler(LastAdminGuardError, _last_admin_guard)  # type: ignore[arg-type]
    app.add_exception_handler(PasswordChangeRequiredError, _password_change)  # type: ignore[arg-type]
    app.add_exception_handler(NotFoundError, _not_found)  # type: ignore[arg-type]
    app.add_exception_handler(EmailInUseError, _email_in_use)  # type: ignore[arg-type]
    app.add_exception_handler(AuthRateLimitedError, _rate_limited)  # type: ignore[arg-type]
