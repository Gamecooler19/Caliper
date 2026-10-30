"""POST /api/v1/classify with the dual API-key / session dependency.

Includes the CALIPER_API_KEY regression tests: the existing key path
must behave exactly as before Module 6. (The pre-existing
``test_api_classify.py`` suite, left unmodified, is the broader
regression net; the tests here add exact-byte and audit assertions.)
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from app.api.schemas import ClassifyResponse
from app.auth import totp
from app.auth.models import UserRole
from app.auth.sessions import SESSION_COOKIE_NAME
from app.db import AuditLogRecord
from tests.auth.conftest import login_fully
from tests.test_api_classify import (
    _mock_pipeline_functions,
    _sample_classification_result,
)

API_KEY = "test-fake-caliper-api-key"
EMAIL = "analyst@example.com"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color=(10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _classify(client, headers=None):
    return client.post(
        "/api/v1/classify",
        headers=headers or {},
        files={"image": ("device.png", _png(), "image/png")},
        data={"description": "A simple non-invasive bandage."},
    )


def _audit_rows(auth_env):
    async def _all(session):
        return (await session.execute(select(AuditLogRecord))).scalars().all()

    return auth_env.run_db(_all)


def _rate_keys(auth_env, pattern: str):
    import asyncio

    async def _scan():
        return [k async for k in auth_env.valkey.scan_iter(match=pattern)]

    return asyncio.run(_scan())


@pytest.fixture
def mocked_pipeline(monkeypatch):
    _mock_pipeline_functions(monkeypatch)


@pytest.fixture
def logged_in_user(auth_env, totp_codes):
    secret = totp.generate_secret()
    user_id = auth_env.seed_user(EMAIL, role=UserRole.ANALYST, totp_secret=secret)
    assert login_fully(auth_env, totp_codes, EMAIL, secret).status_code == 200
    return user_id


def _login_as(auth_env, totp_codes, email: str, role: UserRole):
    secret = totp.generate_secret()
    user_id = auth_env.seed_user(email, role=role, totp_secret=secret)
    assert login_fully(auth_env, totp_codes, email, secret).status_code == 200
    return user_id


# ---------------------------------------------------------------------------
# Role restriction on the session path (Module 6 fix #2)
# ---------------------------------------------------------------------------


def test_auditor_session_gets_403_read_only_role_on_classify(
    auth_env, totp_codes, mocked_pipeline
):
    _login_as(auth_env, totp_codes, "auditor@example.com", UserRole.AUDITOR)

    response = _classify(auth_env.client)

    assert response.status_code == 403
    assert response.json() == {"error": "read_only_role"}
    # Nothing was run or audited as a side effect of the rejected call.
    assert _audit_rows(auth_env) == []


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.ANALYST])
def test_admin_and_analyst_sessions_still_classify_exactly_as_before(
    auth_env, totp_codes, mocked_pipeline, role
):
    """Regression for Module 6 fix #2: the roles that were always
    allowed to classify over a session are entirely unaffected by the
    new auditor restriction."""

    email = f"{role.value}@example.com"
    user_id = _login_as(auth_env, totp_codes, email, role)

    response = _classify(auth_env.client)

    assert response.status_code == 200
    rows = _audit_rows(auth_env)
    assert len(rows) == 1
    assert rows[0].user_id == user_id
    assert rows[0].actor == email
    assert rows[0].action == "classify"


# ---------------------------------------------------------------------------
# Session-authenticated classify
# ---------------------------------------------------------------------------


def test_session_classify_writes_the_real_user_id_to_the_audit_log(
    auth_env, mocked_pipeline, logged_in_user
):
    response = _classify(auth_env.client)

    assert response.status_code == 200
    rows = _audit_rows(auth_env)
    assert len(rows) == 1
    assert rows[0].user_id == logged_in_user
    assert rows[0].actor == EMAIL
    assert rows[0].action == "classify"


def test_session_classify_is_rate_limited_under_its_own_per_user_key(
    auth_env, mocked_pipeline, logged_in_user
):
    _classify(auth_env.client)

    assert _rate_keys(auth_env, f"ratelimit:classify-session:{logged_in_user}:*")
    assert _rate_keys(auth_env, "ratelimit:classify:*") == []


def test_session_classify_limit_is_enforced_per_user(auth_env, mocked_pipeline, logged_in_user):
    statuses = [_classify(auth_env.client).status_code for _ in range(31)]
    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


def test_valid_session_wins_over_an_injected_api_key(auth_env, mocked_pipeline, logged_in_user):
    """Caddy injects X-API-Key on every /api/v1/* request, so a logged-in
    browser sends both. The request must be attributed to the person."""

    response = _classify(auth_env.client, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert _audit_rows(auth_env)[0].user_id == logged_in_user


def test_invalid_session_cookie_falls_back_to_the_api_key(auth_env, mocked_pipeline):
    auth_env.client.cookies.set(SESSION_COOKIE_NAME, "stale-or-forged")

    response = _classify(auth_env.client, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert _audit_rows(auth_env)[0].user_id is None


def test_invalid_session_cookie_without_an_api_key_is_unauthorized(auth_env, mocked_pipeline):
    auth_env.client.cookies.set(SESSION_COOKIE_NAME, "stale-or-forged")

    response = _classify(auth_env.client)

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert _audit_rows(auth_env) == []


# ---------------------------------------------------------------------------
# CALIPER_API_KEY path: unchanged
# ---------------------------------------------------------------------------


def test_api_key_classify_writes_a_null_user_id(auth_env, mocked_pipeline):
    response = _classify(auth_env.client, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    rows = _audit_rows(auth_env)
    assert len(rows) == 1
    assert rows[0].user_id is None
    assert rows[0].actor == "api-client"
    assert rows[0].action == "classify"


def test_api_key_classify_uses_the_original_rate_limit_key(auth_env, mocked_pipeline):
    _classify(auth_env.client, headers={"X-API-Key": API_KEY})

    assert _rate_keys(auth_env, "ratelimit:classify:api-client:*")
    assert _rate_keys(auth_env, "ratelimit:classify-session:*") == []


def test_api_key_classify_response_is_byte_for_byte_the_unchanged_shape(
    auth_env, monkeypatch
):
    result = _sample_classification_result()
    _mock_pipeline_functions(monkeypatch, classification_result=result, report="A short report.")

    response = _classify(auth_env.client, headers={"X-API-Key": API_KEY})

    expected = ClassifyResponse(
        classification=result,
        consistency_flags=[],
        report="A short report.",
        disclaimer=result.disclaimer,
        device_bounding_box={  # type: ignore[arg-type]
            "x1": 5, "y1": 6, "x2": 55, "y2": 46, "image_width": 64, "image_height": 48,
        },
    ).model_dump(mode="json")
    expected_bytes = json.dumps(
        expected, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
    ).encode("utf-8")

    assert response.status_code == 200
    assert response.content == expected_bytes
    assert response.headers["content-type"] == "application/json"
    # An API-key call never receives a session cookie.
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "totally-wrong-key"}, {"X-API-Key": ""}],
    ids=["missing", "wrong", "empty"],
)
def test_api_key_failures_are_the_same_401_body_as_before(auth_env, mocked_pipeline, headers):
    response = _classify(auth_env.client, headers=headers)

    assert response.status_code == 401
    assert response.content == b'{"error":"unauthorized"}'
    assert _audit_rows(auth_env) == []


def test_session_for_an_unknown_user_id_is_not_accepted(auth_env, mocked_pipeline):
    import asyncio

    session_id = "handmade-session"

    async def _plant():
        await auth_env.valkey.set(
            f"session:{session_id}",
            json.dumps(
                {
                    "user_id": str(uuid.uuid4()),
                    "role": "admin",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_seen_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            ex=3600,
        )

    asyncio.run(_plant())
    auth_env.client.cookies.set(SESSION_COOKIE_NAME, session_id)

    assert _classify(auth_env.client).status_code == 401
