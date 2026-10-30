"""Login, TOTP, enrollment, session and must-change-password behavior."""

from __future__ import annotations

import json
import logging

import pyotp
import pytest

from app.auth import totp
from app.auth.models import UserRole
from app.auth.sessions import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS
from tests.auth.conftest import DEFAULT_PASSWORD, login_fully

EMAIL = "analyst@example.com"


@pytest.fixture
def secret() -> str:
    return totp.generate_secret()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_full_login_happy_path_sets_hardened_session_cookie(auth_env, totp_codes, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    client = auth_env.client

    step1 = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD})
    assert step1.status_code == 200
    assert step1.json()["status"] == "totp_required"
    assert step1.json()["challenge_token"]
    # The password step alone never yields a session.
    assert "set-cookie" not in step1.headers
    assert client.get("/api/v1/auth/me").status_code == 401

    step2 = client.post(
        "/api/v1/auth/login/totp",
        json={"challenge_token": step1.json()["challenge_token"], "code": totp_codes.next(secret)},
    )
    assert step2.status_code == 200
    assert step2.json() == {"status": "ok", "must_change_password": False}

    set_cookie = step2.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    attrs = [a.strip().lower() for a in set_cookie.split(";")]
    assert "httponly" in attrs
    assert "secure" in attrs
    assert "samesite=strict" in attrs

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    assert me.json()["role"] == "analyst"
    assert auth_env.get_user(EMAIL).last_login_at is not None


def test_logout_destroys_the_session(auth_env, totp_codes, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    login_fully(auth_env, totp_codes, EMAIL, secret)
    session_id = auth_env.client.cookies.get(SESSION_COOKIE_NAME)
    assert session_id

    response = auth_env.client.post("/api/v1/auth/logout")
    assert response.status_code == 200

    # Replay the old cookie explicitly (logout also clears the jar).
    replay = auth_env.client.get(
        "/api/v1/auth/me", headers={"Cookie": f"{SESSION_COOKIE_NAME}={session_id}"}
    )
    assert replay.status_code == 401


def test_session_is_stored_as_json_with_a_12_hour_sliding_ttl(auth_env, totp_codes, secret):
    import asyncio

    auth_env.seed_user(EMAIL, totp_secret=secret)
    login_fully(auth_env, totp_codes, EMAIL, secret)
    session_id = auth_env.client.cookies.get(SESSION_COOKIE_NAME)

    async def _inspect():
        key = f"session:{session_id}"
        raw = await auth_env.valkey.get(key)
        ttl_after_login = await auth_env.valkey.ttl(key)
        await auth_env.valkey.expire(key, 60)  # simulate time passing
        return raw, ttl_after_login

    raw, ttl_after_login = asyncio.run(_inspect())
    data = json.loads(raw)
    assert set(data) == {"user_id", "role", "created_at", "last_seen_at"}
    assert data["role"] == "analyst"
    assert 0 < ttl_after_login <= SESSION_TTL_SECONDS == 43200

    # Any authenticated request slides the TTL back out to 12 hours.
    assert auth_env.client.get("/api/v1/auth/me").status_code == 200

    async def _ttl():
        return await auth_env.valkey.ttl(f"session:{session_id}")

    assert asyncio.run(_ttl()) > 60 * 60 * 11


def test_challenge_token_is_single_use(auth_env, totp_codes, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    first = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()
    body = {"challenge_token": first["challenge_token"], "code": totp_codes.next(secret)}
    assert auth_env.client.post("/api/v1/auth/login/totp", json=body).status_code == 200

    body["code"] = totp_codes.next(secret)
    assert auth_env.client.post("/api/v1/auth/login/totp", json=body).status_code == 401


def test_a_used_totp_code_cannot_be_replayed(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    code = pyotp.TOTP(secret).now()
    results = []
    for _ in range(2):
        challenge = auth_env.client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
        ).json()["challenge_token"]
        results.append(
            auth_env.client.post(
                "/api/v1/auth/login/totp", json={"challenge_token": challenge, "code": code}
            ).status_code
        )
    assert results == [200, 401]


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_wrong_password_is_rejected_without_a_token_or_cookie(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)

    response = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "not the password"}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    assert "set-cookie" not in response.headers


def test_unknown_email_gets_the_same_response_as_a_wrong_password(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)

    wrong_password = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "nope nope nope nope"}
    )
    unknown = auth_env.client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "nope nope nope"}
    )

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json() == wrong_password.json()


def test_wrong_totp_code_is_rejected_without_a_cookie(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    challenge = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()["challenge_token"]
    good = pyotp.TOTP(secret).now()
    wrong = f"{(int(good) + 7) % 1_000_000:06d}"

    response = auth_env.client.post(
        "/api/v1/auth/login/totp", json={"challenge_token": challenge, "code": wrong}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_totp_code"}
    assert "set-cookie" not in response.headers
    assert auth_env.client.get("/api/v1/auth/me").status_code == 401


def test_invalid_challenge_token_is_rejected(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    response = auth_env.client.post(
        "/api/v1/auth/login/totp",
        json={"challenge_token": "made-up-token", "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_inactive_account_is_rejected_even_with_correct_credentials(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret, is_active=False)

    response = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    assert "challenge_token" not in response.json()
    assert "set-cookie" not in response.headers


def test_account_deactivated_after_login_loses_its_session(auth_env, totp_codes, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    login_fully(auth_env, totp_codes, EMAIL, secret)
    assert auth_env.client.get("/api/v1/auth/me").status_code == 200

    from sqlalchemy import update

    from app.auth.models import User

    async def _deactivate(session):
        await session.execute(update(User).where(User.email == EMAIL).values(is_active=False))
        await session.commit()

    auth_env.run_db(_deactivate)

    assert auth_env.client.get("/api/v1/auth/me").status_code == 401


def test_unenrolled_account_gets_enrollment_required_and_no_session(auth_env):
    auth_env.seed_user(EMAIL)  # no TOTP secret

    response = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "totp_enrollment_required"
    assert body["enrollment_token"]
    assert body.get("challenge_token") is None
    assert "set-cookie" not in response.headers
    assert auth_env.client.get("/api/v1/auth/me").status_code == 401


def test_enrollment_token_cannot_be_used_as_a_totp_challenge(auth_env):
    auth_env.seed_user(EMAIL)
    token = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()["enrollment_token"]

    response = auth_env.client.post(
        "/api/v1/auth/login/totp", json={"challenge_token": token, "code": "123456"}
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def test_enrollment_stores_the_secret_encrypted_and_then_logs_in(auth_env):
    auth_env.seed_user(EMAIL)
    client = auth_env.client
    token = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()["enrollment_token"]

    begin = client.post("/api/v1/auth/totp/enroll/begin", json={"enrollment_token": token})
    assert begin.status_code == 200
    secret = begin.json()["secret"]
    assert begin.json()["otpauth_uri"].startswith("otpauth://totp/")

    stored = auth_env.get_user(EMAIL)
    assert stored.totp_secret_encrypted is not None
    assert secret not in stored.totp_secret_encrypted
    assert totp.decrypt_secret(stored.totp_secret_encrypted) == secret
    assert stored.totp_enabled is False  # not enabled until a code is proven

    wrong = f"{(int(pyotp.TOTP(secret).now()) + 3) % 1_000_000:06d}"
    bad = client.post(
        "/api/v1/auth/totp/enroll/confirm", json={"enrollment_token": token, "code": wrong}
    )
    assert bad.status_code == 401
    assert "set-cookie" not in bad.headers
    assert auth_env.get_user(EMAIL).totp_enabled is False

    good = client.post(
        "/api/v1/auth/totp/enroll/confirm",
        json={"enrollment_token": token, "code": pyotp.TOTP(secret).now()},
    )
    assert good.status_code == 200
    assert "httponly" in good.headers["set-cookie"].lower()
    assert auth_env.get_user(EMAIL).totp_enabled is True
    assert client.get("/api/v1/auth/me").status_code == 200

    # The next login now asks for a TOTP challenge instead of enrollment.
    client.cookies.clear()
    again = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD})
    assert again.json()["status"] == "totp_required"


def test_enroll_confirm_before_begin_is_a_bad_request(auth_env):
    auth_env.seed_user(EMAIL)
    token = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()["enrollment_token"]

    response = auth_env.client.post(
        "/api/v1/auth/totp/enroll/confirm", json={"enrollment_token": token, "code": "123456"}
    )

    assert response.status_code == 400


def test_already_enrolled_account_cannot_use_enrollment_endpoints(auth_env, secret):
    auth_env.seed_user(EMAIL, totp_secret=secret)
    response = auth_env.client.post(
        "/api/v1/auth/totp/enroll/begin", json={"enrollment_token": "anything"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# must_change_password gate
# ---------------------------------------------------------------------------


def _login_must_change(auth_env, totp_codes, secret, role=UserRole.ADMIN):
    auth_env.seed_user(EMAIL, totp_secret=secret, role=role, must_change_password=True)
    response = login_fully(auth_env, totp_codes, EMAIL, secret)
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


def test_must_change_password_blocks_other_routes_until_changed(auth_env, totp_codes, secret):
    _login_must_change(auth_env, totp_codes, secret)
    client = auth_env.client

    # Reachable: the routes needed to satisfy the gate.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True

    # Blocked: everything else, including admin routes for an admin.
    blocked = client.get("/api/v1/admin/users")
    assert blocked.status_code == 403
    assert blocked.json() == {"error": "password_change_required"}
    classify = client.post(
        "/api/v1/classify",
        files={"image": ("d.png", b"x", "image/png")},
        data={"description": "d"},
    )
    assert classify.status_code == 403
    assert classify.json() == {"error": "password_change_required"}

    changed = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a brand new passphrase"},
    )
    assert changed.status_code == 200

    assert client.get("/api/v1/admin/users").status_code == 200
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is False


def test_change_password_rejects_wrong_current_short_and_unchanged(auth_env, totp_codes, secret):
    _login_must_change(auth_env, totp_codes, secret)
    post = auth_env.client.post

    wrong_current = post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "a brand new passphrase"},
    )
    too_short = post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
    )
    unchanged = post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
    )

    assert wrong_current.status_code == 401
    assert too_short.status_code == 400
    assert unchanged.status_code == 400
    assert auth_env.get_user(EMAIL).must_change_password is True


def test_changing_password_ends_the_users_other_sessions_but_not_the_current_one(
    auth_env, totp_codes, secret
):
    auth_env.seed_user(EMAIL, totp_secret=secret, must_change_password=True)
    # A second device, logged in earlier (e.g. with a password someone else knows).
    other_device = auth_env.login_as_other_user(EMAIL, secret)
    assert other_device.get("/api/v1/auth/me").status_code == 200
    totp_codes.next(secret)  # step 0 was consumed by the other device
    assert login_fully(auth_env, totp_codes, EMAIL, secret).status_code == 200

    changed = auth_env.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a brand new passphrase"},
    )

    assert changed.status_code == 200
    assert other_device.get("/api/v1/auth/me").status_code == 401
    assert auth_env.client.get("/api/v1/auth/me").status_code == 200


def test_new_password_is_what_works_for_the_next_login(auth_env, totp_codes, secret):
    _login_must_change(auth_env, totp_codes, secret)
    new_password = "a brand new passphrase"
    auth_env.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": new_password},
    )
    auth_env.client.cookies.clear()

    old = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    )
    new = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": new_password}
    )

    assert old.status_code == 401
    assert new.status_code == 200


# ---------------------------------------------------------------------------
# No plaintext secrets in logs
# ---------------------------------------------------------------------------


def test_passwords_and_totp_secrets_never_appear_in_logs(auth_env, caplog):
    auth_env.seed_user(EMAIL)
    with caplog.at_level(logging.DEBUG):
        token = auth_env.client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
        ).json()["enrollment_token"]
        secret = auth_env.client.post(
            "/api/v1/auth/totp/enroll/begin", json={"enrollment_token": token}
        ).json()["secret"]
        code = pyotp.TOTP(secret).now()
        auth_env.client.post(
            "/api/v1/auth/totp/enroll/confirm", json={"enrollment_token": token, "code": code}
        )
        auth_env.client.post(
            "/api/v1/auth/change-password",
            json={"current_password": DEFAULT_PASSWORD, "new_password": "never logged passphrase"},
        )

    for sensitive in (DEFAULT_PASSWORD, "never logged passphrase", secret, code):
        assert sensitive not in caplog.text
