"""Authentication routes: /api/v1/auth/*.

Login is two steps and never yields a session on the password step
alone:

1. ``POST /auth/login`` (email + password) returns either a short-lived
   TOTP *challenge* token (account has TOTP enrolled) or a short-lived
   *enrollment* token (it does not).
2. ``POST /auth/login/totp`` exchanges challenge token + code for the
   session cookie. For an unenrolled account, ``/auth/totp/enroll/begin``
   and ``/auth/totp/enroll/confirm`` do the enrollment, and a successful
   confirm (a valid code from the freshly issued secret) starts the
   session.

Trust model, stated plainly: the initial password an admin hands to a
new user is the bootstrap credential. Whoever holds it and reaches the
enrollment step first binds their authenticator to the account. An
admin ``reset-totp`` re-opens that same window.

Login and TOTP attempts are throttled with the existing Valkey
fixed-window limiter (``app.api.rate_limit.check_rate_limit``) under
their own ``route`` keys. There is no separate lockout state.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.errors import InvalidRequestError
from app.api.rate_limit import check_rate_limit
from app.auth import totp
from app.auth.dependencies import AuthenticatedUser, require_session_allow_password_change
from app.auth.errors import (
    AuthRateLimitedError,
    InvalidCredentialsError,
    InvalidTotpCodeError,
)
from app.auth.models import User
from app.auth.passwords import (
    DUMMY_HASH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from app.auth.sessions import (
    CHALLENGE_TTL_SECONDS,
    ENROLLMENT_TTL_SECONDS,
    KIND_TOTP_CHALLENGE,
    KIND_TOTP_ENROLLMENT,
    SESSION_COOKIE_NAME,
    create_login_token,
    create_session,
    delete_login_token,
    destroy_session,
    read_login_token,
)

router = APIRouter(prefix="/api/v1/auth")

#: Login / TOTP / password-change attempts allowed per hour, per key.
#: A protective default, same spirit as the classify limit.
AUTH_ATTEMPT_LIMIT_PER_HOUR = 10

ROUTE_LOGIN = "auth-login"
ROUTE_TOTP = "auth-totp"
ROUTE_CHANGE_PASSWORD = "auth-change-password"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    status: Literal["totp_required", "totp_enrollment_required"]
    challenge_token: str | None = None
    enrollment_token: str | None = None


class TotpLoginRequest(BaseModel):
    challenge_token: str
    code: str


class EnrollBeginRequest(BaseModel):
    enrollment_token: str


class EnrollBeginResponse(BaseModel):
    secret: str
    otpauth_uri: str


class EnrollConfirmRequest(BaseModel):
    enrollment_token: str
    code: str


class SessionStartedResponse(BaseModel):
    status: Literal["ok"] = "ok"
    must_change_password: bool


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def _throttle(request: Request, route: str, key: str) -> None:
    result = await check_rate_limit(
        request.app.state.valkey_client,
        key,
        limit_per_hour=AUTH_ATTEMPT_LIMIT_PER_HOUR,
        route=route,
    )
    if not result.allowed:
        raise AuthRateLimitedError(result.retry_after_seconds)


def _set_session_cookie(response: Response, session_id: str) -> None:
    # No max_age: a browser-session cookie. The server-side sliding TTL
    # in Valkey is the source of truth for expiry.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


async def _start_session(request: Request, response: Response, user: User) -> str:
    session_id = await create_session(request.app.state.valkey_client, user.id, user.role.value)
    _set_session_cookie(response, session_id)
    return session_id


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    email = normalize_email(body.email)
    await _throttle(request, ROUTE_LOGIN, email)

    async with request.app.state.db_session_factory() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # Always run one argon2 verification so an unknown email costs the
    # same as a known one.
    password_ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if user is None or not password_ok or not user.is_active:
        raise InvalidCredentialsError()

    valkey = request.app.state.valkey_client
    if user.totp_enabled and user.totp_secret_encrypted:
        token = await create_login_token(
            valkey, KIND_TOTP_CHALLENGE, user.id, CHALLENGE_TTL_SECONDS
        )
        return LoginResponse(status="totp_required", challenge_token=token)

    token = await create_login_token(valkey, KIND_TOTP_ENROLLMENT, user.id, ENROLLMENT_TTL_SECONDS)
    return LoginResponse(status="totp_enrollment_required", enrollment_token=token)


async def _verified_code_or_raise(request: Request, user: User, secret: str, code: str) -> None:
    if not totp.verify_code(secret, code):
        raise InvalidTotpCodeError()
    if not await totp.consume_code_once(request.app.state.valkey_client, user.id, code):
        raise InvalidTotpCodeError()


async def _mark_logged_in(request: Request, user_id: uuid.UUID, *, enable_totp: bool) -> User:
    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, user_id)
        assert user is not None
        user.last_login_at = dt.datetime.now(dt.UTC)
        if enable_totp:
            user.totp_enabled = True
        await db.commit()
        return user


@router.post("/login/totp", response_model=SessionStartedResponse)
async def login_totp(
    body: TotpLoginRequest, request: Request, response: Response
) -> SessionStartedResponse:
    valkey = request.app.state.valkey_client
    user_id = await read_login_token(valkey, KIND_TOTP_CHALLENGE, body.challenge_token)
    if user_id is None:
        raise InvalidCredentialsError()

    await _throttle(request, ROUTE_TOTP, str(user_id))

    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or not user.totp_enabled
        or not user.totp_secret_encrypted
    ):
        raise InvalidCredentialsError()

    await _verified_code_or_raise(
        request, user, totp.decrypt_secret(user.totp_secret_encrypted), body.code
    )

    await delete_login_token(valkey, KIND_TOTP_CHALLENGE, body.challenge_token)
    user = await _mark_logged_in(request, user.id, enable_totp=False)
    await _start_session(request, response, user)
    return SessionStartedResponse(must_change_password=user.must_change_password)


async def _enrollment_user(request: Request, token: str) -> User:
    user_id = await read_login_token(request.app.state.valkey_client, KIND_TOTP_ENROLLMENT, token)
    if user_id is None:
        raise InvalidCredentialsError()
    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, user_id)
    if user is None or not user.is_active or user.totp_enabled:
        raise InvalidCredentialsError()
    return user


@router.post("/totp/enroll/begin", response_model=EnrollBeginResponse)
async def enroll_begin(body: EnrollBeginRequest, request: Request) -> EnrollBeginResponse:
    user = await _enrollment_user(request, body.enrollment_token)
    await _throttle(request, ROUTE_TOTP, str(user.id))

    secret = totp.generate_secret()
    async with request.app.state.db_session_factory() as db:
        stored = await db.get(User, user.id)
        assert stored is not None
        stored.totp_secret_encrypted = totp.encrypt_secret(secret)
        stored.totp_enabled = False
        await db.commit()

    return EnrollBeginResponse(secret=secret, otpauth_uri=totp.provisioning_uri(secret, user.email))


@router.post("/totp/enroll/confirm", response_model=SessionStartedResponse)
async def enroll_confirm(
    body: EnrollConfirmRequest, request: Request, response: Response
) -> SessionStartedResponse:
    user = await _enrollment_user(request, body.enrollment_token)
    await _throttle(request, ROUTE_TOTP, str(user.id))

    if not user.totp_secret_encrypted:
        raise InvalidRequestError("Call /api/v1/auth/totp/enroll/begin first.")

    await _verified_code_or_raise(
        request, user, totp.decrypt_secret(user.totp_secret_encrypted), body.code
    )

    await delete_login_token(
        request.app.state.valkey_client, KIND_TOTP_ENROLLMENT, body.enrollment_token
    )
    user = await _mark_logged_in(request, user.id, enable_totp=True)
    await _start_session(request, response, user)
    return SessionStartedResponse(must_change_password=user.must_change_password)


@router.post("/logout", response_model=StatusResponse)
async def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_session_allow_password_change),  # noqa: B008
) -> StatusResponse:
    await destroy_session(request.app.state.valkey_client, user.session_id)
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True, secure=True, samesite="strict"
    )
    return StatusResponse()


@router.get("/me", response_model=MeResponse)
async def me(
    user: AuthenticatedUser = Depends(require_session_allow_password_change),  # noqa: B008
) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role.value,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password", response_model=StatusResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_session_allow_password_change),  # noqa: B008
) -> StatusResponse:
    await _throttle(request, ROUTE_CHANGE_PASSWORD, str(user.id))

    async with request.app.state.db_session_factory() as db:
        stored = await db.get(User, user.id)
        assert stored is not None
        if not verify_password(stored.password_hash, body.current_password):
            raise InvalidCredentialsError()
        if len(body.new_password) < MIN_PASSWORD_LENGTH:
            raise InvalidRequestError(
                f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if body.new_password == body.current_password:
            raise InvalidRequestError("New password must differ from the current password.")
        stored.password_hash = hash_password(body.new_password)
        stored.must_change_password = False
        await db.commit()

    return StatusResponse()
