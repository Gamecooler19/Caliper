"""Admin user-management routes and role-based access control."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth import totp
from app.auth.models import User, UserRole
from app.auth.sessions import SESSION_COOKIE_NAME
from app.db import AuditLogRecord
from tests.auth.conftest import DEFAULT_PASSWORD, login_fully

ADMIN_EMAIL = "admin@example.com"
TARGET_EMAIL = "target@example.com"


def _login(auth_env, totp_codes, email: str, role: UserRole) -> str:
    secret = totp.generate_secret()
    auth_env.seed_user(email, role=role, totp_secret=secret)
    assert login_fully(auth_env, totp_codes, email, secret).status_code == 200
    return secret


# (method, path template, json body)
ADMIN_ENDPOINTS = [
    ("POST", "/api/v1/admin/users", {"email": "new@example.com", "role": "analyst"}),
    ("GET", "/api/v1/admin/users", None),
    ("PATCH", "/api/v1/admin/users/{id}", {"is_active": False}),
    ("POST", "/api/v1/admin/users/{id}/reset-totp", None),
]


@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.AUDITOR])
@pytest.mark.parametrize(
    ("method", "path", "body"), ADMIN_ENDPOINTS, ids=["create", "list", "patch", "reset-totp"]
)
def test_non_admin_roles_get_403_on_every_admin_endpoint(
    auth_env, totp_codes, role, method, path, body
):
    target_id = auth_env.seed_user(TARGET_EMAIL)
    _login(auth_env, totp_codes, "nonadmin@example.com", role)

    response = auth_env.client.request(method, path.format(id=target_id), json=body)

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}
    # And nothing happened as a side effect.
    assert auth_env.get_user(TARGET_EMAIL).is_active is True


@pytest.mark.parametrize(
    ("method", "path", "body"), ADMIN_ENDPOINTS, ids=["create", "list", "patch", "reset-totp"]
)
def test_unauthenticated_requests_to_admin_endpoints_get_401(auth_env, method, path, body):
    response = auth_env.client.request(method, path.format(id=uuid.uuid4()), json=body)
    assert response.status_code == 401


def test_the_shared_api_key_does_not_grant_admin_access(auth_env):
    response = auth_env.client.get(
        "/api/v1/admin/users", headers={"X-API-Key": "test-fake-caliper-api-key"}
    )
    assert response.status_code == 401


def test_admin_creates_a_user_and_sees_the_initial_password_exactly_once(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)

    created = auth_env.client.post(
        "/api/v1/admin/users", json={"email": "  New.Person@Example.COM ", "role": "auditor"}
    )

    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "new.person@example.com"  # lowercased and trimmed
    assert body["role"] == "auditor"
    assert body["must_change_password"] is True
    assert body["totp_enabled"] is False
    assert body["is_active"] is True
    initial_password = body["initial_password"]
    assert len(initial_password) >= 20

    # Stored only as a hash.
    stored = auth_env.get_user("new.person@example.com")
    assert stored.password_hash != initial_password
    assert initial_password not in stored.password_hash
    assert stored.totp_secret_encrypted is None

    # It is never returned again.
    listing = auth_env.client.get("/api/v1/admin/users")
    assert initial_password not in listing.text
    assert "password_hash" not in listing.text
    assert all("initial_password" not in u for u in listing.json())

    # And it works as a login credential for the new account (-> enrollment).
    auth_env.client.cookies.clear()
    login = auth_env.client.post(
        "/api/v1/auth/login", json={"email": "new.person@example.com", "password": initial_password}
    )
    assert login.status_code == 200
    assert login.json()["status"] == "totp_enrollment_required"


def test_creating_a_duplicate_email_conflicts(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    auth_env.seed_user(TARGET_EMAIL)

    response = auth_env.client.post(
        "/api/v1/admin/users", json={"email": TARGET_EMAIL.upper(), "role": "analyst"}
    )

    assert response.status_code == 409
    assert response.json() == {"error": "email_in_use"}


@pytest.mark.parametrize("email", ["", "no-at-sign", "@example.com", "user@"])
def test_creating_a_user_with_an_invalid_email_is_a_bad_request(auth_env, totp_codes, email):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    response = auth_env.client.post("/api/v1/admin/users", json={"email": email, "role": "analyst"})
    assert response.status_code == 400


def test_creating_a_user_with_an_unknown_role_is_rejected(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    response = auth_env.client.post(
        "/api/v1/admin/users", json={"email": "x@example.com", "role": "superuser"}
    )
    assert response.status_code == 422


def test_admin_changes_a_role_and_it_applies_to_the_next_request(auth_env, totp_codes):
    admin_secret = _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    target_id = auth_env.seed_user(TARGET_EMAIL, role=UserRole.ANALYST)

    response = auth_env.client.patch(
        f"/api/v1/admin/users/{target_id}", json={"role": "auditor"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "auditor"
    assert auth_env.get_user(TARGET_EMAIL).role == UserRole.AUDITOR
    assert admin_secret  # admin session untouched


def test_demoting_an_admin_takes_effect_on_their_existing_session(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    other_admin_secret = totp.generate_secret()
    other_id = auth_env.seed_user(
        "other-admin@example.com", role=UserRole.ADMIN, totp_secret=other_admin_secret
    )
    other = auth_env.login_as_other_user("other-admin@example.com", other_admin_secret)
    assert other.get("/api/v1/admin/users").status_code == 200

    auth_env.client.patch(f"/api/v1/admin/users/{other_id}", json={"role": "analyst"})

    assert other.get("/api/v1/admin/users").status_code == 403


def test_deactivating_a_user_blocks_login_and_ends_their_sessions(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    target_secret = totp.generate_secret()
    target_id = auth_env.seed_user(TARGET_EMAIL, totp_secret=target_secret)

    user_client = auth_env.login_as_other_user(TARGET_EMAIL, target_secret)
    assert user_client.get("/api/v1/auth/me").status_code == 200

    patched = auth_env.client.patch(f"/api/v1/admin/users/{target_id}", json={"is_active": False})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    assert user_client.get("/api/v1/auth/me").status_code == 401
    relogin = user_client.post(
        "/api/v1/auth/login", json={"email": TARGET_EMAIL, "password": DEFAULT_PASSWORD}
    )
    assert relogin.status_code == 401

    # Reactivation is possible.
    again = auth_env.client.patch(f"/api/v1/admin/users/{target_id}", json={"is_active": True})
    assert again.json()["is_active"] is True


def test_admin_cannot_deactivate_or_demote_themselves(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    me_id = auth_env.get_user(ADMIN_EMAIL).id

    deactivate = auth_env.client.patch(f"/api/v1/admin/users/{me_id}", json={"is_active": False})
    demote = auth_env.client.patch(f"/api/v1/admin/users/{me_id}", json={"role": "analyst"})

    assert deactivate.status_code == 400
    assert demote.status_code == 400
    assert auth_env.get_user(ADMIN_EMAIL).role == UserRole.ADMIN
    assert auth_env.get_user(ADMIN_EMAIL).is_active is True


def test_patch_with_no_changes_and_unknown_user(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    target_id = auth_env.seed_user(TARGET_EMAIL)

    empty = auth_env.client.patch(f"/api/v1/admin/users/{target_id}", json={})
    missing = auth_env.client.patch(
        f"/api/v1/admin/users/{uuid.uuid4()}", json={"is_active": False}
    )
    missing_reset = auth_env.client.post(f"/api/v1/admin/users/{uuid.uuid4()}/reset-totp")

    assert empty.status_code == 400
    assert missing.status_code == 404
    assert missing_reset.status_code == 404


def test_reset_totp_forces_reenrollment_ends_sessions_and_is_audited(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    admin = auth_env.get_user(ADMIN_EMAIL)
    target_secret = totp.generate_secret()
    target_id = auth_env.seed_user(TARGET_EMAIL, totp_secret=target_secret)

    user_client = auth_env.login_as_other_user(TARGET_EMAIL, target_secret)
    assert user_client.get("/api/v1/auth/me").status_code == 200

    reset = auth_env.client.post(f"/api/v1/admin/users/{target_id}/reset-totp")

    assert reset.status_code == 200
    assert reset.json()["totp_enabled"] is False
    assert user_client.get("/api/v1/auth/me").status_code == 401
    relogin = user_client.post(
        "/api/v1/auth/login", json={"email": TARGET_EMAIL, "password": DEFAULT_PASSWORD}
    )
    assert relogin.json()["status"] == "totp_enrollment_required"

    stored = auth_env.get_user(TARGET_EMAIL)
    assert stored.totp_secret_encrypted is None
    assert stored.totp_enabled is False

    async def _audit(session):
        query = select(AuditLogRecord).where(AuditLogRecord.action.like("admin_reset_totp%"))
        return (await session.execute(query)).scalars().all()

    rows = auth_env.run_db(_audit)
    assert len(rows) == 1
    assert rows[0].action == f"admin_reset_totp:{target_id}"
    assert rows[0].actor == ADMIN_EMAIL
    assert rows[0].user_id == admin.id


def test_admin_session_cookie_is_the_only_thing_that_authorizes(auth_env, totp_codes):
    _login(auth_env, totp_codes, ADMIN_EMAIL, UserRole.ADMIN)
    auth_env.client.cookies.set(SESSION_COOKIE_NAME, "forged-session-id")
    auth_env.client.cookies.delete(SESSION_COOKIE_NAME)  # drop the real one
    auth_env.client.cookies.set(SESSION_COOKIE_NAME, "forged-session-id")

    assert auth_env.client.get("/api/v1/admin/users").status_code == 401

    async def _count(session):
        return len((await session.execute(select(User))).scalars().all())

    assert auth_env.run_db(_count) == 1
