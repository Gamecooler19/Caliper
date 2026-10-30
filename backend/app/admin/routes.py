"""Admin-only user management: /api/v1/admin/users.

Every route is behind ``require_role("admin")``. There is no email or
invitation system: ``POST /admin/users`` generates a random initial
password and returns it exactly once in the response body for the admin
to hand over; only its argon2 hash is stored and it is never logged.

Module 6 fix #3: every mutating route here writes one entry to the
generic ``admin_audit_log`` table (``app.security.audit.record_admin_audit_entry``),
including ``reset-totp``, migrated onto this table for consistency --
it previously wrote to ``AuditLogEntry``/``audit_log``, Module 1's
classification-run audit log, which is the wrong table for an admin
action and is left untouched here.

Module 6 fix #4: a PATCH that would deactivate a user or change their
role away from ``admin`` is rejected with 400 ``last_admin_guard``
unless at least one *other* active admin would remain. Checked against
the database inside the same transaction as the change, before it is
applied.

This replaces the previous, narrower "an admin cannot deactivate or
demote their own account" rule, which blocked self-targeting PATCHes
unconditionally -- even when a second active admin existed to take
over. That was really just the degenerate case of this guard (a lone
admin acting on themselves is exactly "zero other active admins would
remain"); the guard now applies uniformly to self- and other-targeting
PATCHes, so a lone admin still cannot strand the account, but an admin
with a colleague can step down or be demoted.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import InvalidRequestError
from app.auth.dependencies import AuthenticatedUser, require_role
from app.auth.errors import EmailInUseError, LastAdminGuardError, NotFoundError
from app.auth.models import User, UserRole
from app.auth.passwords import generate_initial_password, hash_password
from app.auth.routes import normalize_email
from app.auth.sessions import destroy_all_sessions_for_user
from app.security.audit import record_admin_audit_entry

router = APIRouter(prefix="/api/v1/admin")

_require_admin = require_role(UserRole.ADMIN)


class CreateUserRequest(BaseModel):
    email: str
    role: UserRole


class UpdateUserRequest(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    is_active: bool
    totp_enabled: bool
    must_change_password: bool
    created_at: dt.datetime
    last_login_at: dt.datetime | None


class CreatedUserOut(UserOut):
    #: Shown once, here only. Never stored in plaintext, never logged.
    initial_password: str


def _to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


async def _other_active_admin_count(db: AsyncSession, exclude_user_id: uuid.UUID) -> int:
    """How many *other* active admins exist, excluding ``exclude_user_id``.

    Used by the last-admin guard (Module 6 fix #4): a deactivation or a
    demotion away from admin is refused unless this is at least 1.
    """

    query = select(func.count()).where(
        User.role == UserRole.ADMIN,
        User.is_active.is_(True),
        User.id != exclude_user_id,
    )
    return (await db.execute(query)).scalar_one()


@router.post("/users", response_model=CreatedUserOut, status_code=201)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(_require_admin),  # noqa: B008
) -> CreatedUserOut:
    email = normalize_email(body.email)
    local, _, domain = email.partition("@")
    if not local or not domain or len(email) > 320:
        raise InvalidRequestError("A valid email address is required.")

    initial_password = generate_initial_password()
    user = User(
        email=email,
        password_hash=hash_password(initial_password),
        role=body.role,
        totp_enabled=False,
        must_change_password=True,
        is_active=True,
    )
    async with request.app.state.db_session_factory() as db:
        existing = (await db.execute(select(User.id).where(User.email == email))).first()
        if existing is not None:
            raise EmailInUseError()
        db.add(user)
        try:
            await db.commit()
        except IntegrityError as exc:
            raise EmailInUseError() from exc
        await db.refresh(user)

        await record_admin_audit_entry(
            db,
            actor_user_id=admin.id,
            action="user_created",
            target_user_id=user.id,
            details={"email": email, "role": body.role.value},
        )
        await db.commit()

    return CreatedUserOut(**_to_out(user).model_dump(), initial_password=initial_password)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    request: Request,
    admin: AuthenticatedUser = Depends(_require_admin),  # noqa: B008
) -> list[UserOut]:
    async with request.app.state.db_session_factory() as db:
        users = (await db.execute(select(User).order_by(User.created_at, User.email))).scalars()
        return [_to_out(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(_require_admin),  # noqa: B008
) -> UserOut:
    if body.role is None and body.is_active is None:
        raise InvalidRequestError("Provide 'role' and/or 'is_active'.")

    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise NotFoundError()

        is_deactivating = body.is_active is False and user.is_active is True
        is_demoting_from_admin = (
            body.role is not None
            and body.role != UserRole.ADMIN
            and user.role == UserRole.ADMIN
            and user.is_active is True
        )
        if (is_deactivating or is_demoting_from_admin) and (
            await _other_active_admin_count(db, user_id) < 1
        ):
            raise LastAdminGuardError()

        old_role = user.role
        old_is_active = user.is_active
        if body.role is not None:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active

        if body.role is not None and body.role != old_role:
            await record_admin_audit_entry(
                db,
                actor_user_id=admin.id,
                action="role_changed",
                target_user_id=user_id,
                details={"old_role": old_role.value, "new_role": body.role.value},
            )
        if body.is_active is not None and body.is_active != old_is_active:
            await record_admin_audit_entry(
                db,
                actor_user_id=admin.id,
                action="user_deactivated" if body.is_active is False else "user_reactivated",
                target_user_id=user_id,
                details={"is_active": body.is_active},
            )

        await db.commit()
        out = _to_out(user)

    if body.is_active is False:
        await destroy_all_sessions_for_user(request.app.state.valkey_client, user_id)
    return out


@router.post("/users/{user_id}/reset-totp", response_model=UserOut)
async def reset_totp(
    user_id: uuid.UUID,
    request: Request,
    admin: AuthenticatedUser = Depends(_require_admin),  # noqa: B008
) -> UserOut:
    """Clear the user's TOTP enrollment so they must enroll again, end
    their sessions, and write an audit entry attributed to the admin."""

    async with request.app.state.db_session_factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise NotFoundError()
        user.totp_secret_encrypted = None
        user.totp_enabled = False
        await record_admin_audit_entry(
            db,
            actor_user_id=admin.id,
            action="totp_reset",
            target_user_id=user_id,
            details={},
        )
        await db.commit()
        out = _to_out(user)

    await destroy_all_sessions_for_user(request.app.state.valkey_client, user_id)
    return out
