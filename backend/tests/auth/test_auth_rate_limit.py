"""Login / TOTP throttling via the existing Valkey fixed-window limiter,
under its own keys."""

from __future__ import annotations

import asyncio

import pyotp

from app.api.rate_limit import check_rate_limit
from app.auth import totp
from app.auth.routes import AUTH_ATTEMPT_LIMIT_PER_HOUR, AUTH_IP_ATTEMPT_LIMIT_PER_HOUR
from tests.auth.conftest import DEFAULT_PASSWORD

EMAIL = "analyst@example.com"


def _keys(auth_env, pattern: str) -> list[str]:
    async def _scan():
        return [k async for k in auth_env.valkey.scan_iter(match=pattern)]

    return asyncio.run(_scan())


def test_repeated_failed_logins_are_throttled_with_retry_after(auth_env):
    auth_env.seed_user(EMAIL, totp_secret=totp.generate_secret())
    attempt = lambda password: auth_env.client.post(  # noqa: E731
        "/api/v1/auth/login", json={"email": EMAIL, "password": password}
    )

    statuses = [attempt("wrong password")
                .status_code for _ in range(AUTH_ATTEMPT_LIMIT_PER_HOUR)]
    assert statuses == [401] * AUTH_ATTEMPT_LIMIT_PER_HOUR

    blocked = attempt("wrong password")
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limit_exceeded"}
    assert int(blocked.headers["Retry-After"]) > 0

    # Throttled even with the right password, until the window resets.
    assert attempt(DEFAULT_PASSWORD).status_code == 429


def test_throttle_is_per_email_and_uses_its_own_key_namespace(auth_env):
    auth_env.seed_user(EMAIL, totp_secret=totp.generate_secret())
    auth_env.seed_user("other@example.com", totp_secret=totp.generate_secret())

    for _ in range(AUTH_ATTEMPT_LIMIT_PER_HOUR + 1):
        auth_env.client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"})

    other = auth_env.client.post(
        "/api/v1/auth/login", json={"email": "other@example.com", "password": DEFAULT_PASSWORD}
    )
    assert other.status_code == 200

    # Module 6 fix #1: the per-email key is now (client_ip, email), not
    # email alone -- the TestClient's fixed default client address is
    # "testclient" absent an X-Forwarded-For header.
    assert _keys(auth_env, f"ratelimit:auth-login:testclient:{EMAIL}:*")
    # The classify limiter's namespace was never touched.
    assert _keys(auth_env, "ratelimit:classify:*") == []
    assert _keys(auth_env, "ratelimit:classify-session:*") == []


def test_third_party_rotating_target_emails_from_one_ip_is_caught_by_the_per_ip_ceiling(
    auth_env,
):
    """Module 6 fix #1: a third party that knows no single victim's email
    but rotates target emails from one source IP is still bounded, by
    the coarser client_ip-alone ceiling -- not left unbounded just
    because each (ip, email) key individually stays under its own 10/hour
    limit."""

    auth_env.seed_user(EMAIL, totp_secret=totp.generate_secret())
    attacker_ip = {"X-Forwarded-For": "203.0.113.9"}

    statuses = [
        auth_env.client.post(
            "/api/v1/auth/login",
            json={"email": f"rotating-target-{i}@example.com", "password": "guess"},
            headers=attacker_ip,
        ).status_code
        for i in range(AUTH_IP_ATTEMPT_LIMIT_PER_HOUR)
    ]
    # Every individual (ip, email) pair is brand new, so none of these
    # trip the tight per-(ip, email) ceiling -- they are unknown-email 401s.
    assert statuses == [401] * AUTH_IP_ATTEMPT_LIMIT_PER_HOUR

    blocked = auth_env.client.post(
        "/api/v1/auth/login",
        json={"email": "yet-another-rotating-target@example.com", "password": "guess"},
        headers=attacker_ip,
    )
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limit_exceeded"}

    # A legitimate login for a *different* source IP is unaffected: the
    # per-IP ceiling is scoped to the attacking address, not global.
    legitimate = auth_env.client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": DEFAULT_PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.4"},
    )
    assert legitimate.status_code == 200


def test_per_email_ceiling_still_triggers_independently_of_the_per_ip_ceiling(auth_env):
    """Module 6 fix #1: the tight (client_ip, email) ceiling trips on its
    own at the original 10/hour, well before the coarser per-IP ceiling
    (50/hour) would, and without needing the per-IP ceiling to trip."""

    auth_env.seed_user(EMAIL, totp_secret=totp.generate_secret())
    auth_env.seed_user("other@example.com", totp_secret=totp.generate_secret())
    victim_ip = {"X-Forwarded-For": "203.0.113.20"}

    statuses = [
        auth_env.client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": "wrong"},
            headers=victim_ip,
        ).status_code
        for _ in range(AUTH_ATTEMPT_LIMIT_PER_HOUR)
    ]
    assert statuses == [401] * AUTH_ATTEMPT_LIMIT_PER_HOUR

    blocked = auth_env.client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": DEFAULT_PASSWORD},
        headers=victim_ip,
    )
    assert blocked.status_code == 429

    # The same IP, attacking a *different* email, still succeeds: the
    # per-IP ceiling (50/hour) has not been hit by only 10 requests, so
    # the block above was specifically the per-(ip, email) key tripping.
    other = auth_env.client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": DEFAULT_PASSWORD},
        headers=victim_ip,
    )
    assert other.status_code == 200


def test_email_case_does_not_bypass_the_throttle(auth_env):
    auth_env.seed_user(EMAIL, totp_secret=totp.generate_secret())
    for i in range(AUTH_ATTEMPT_LIMIT_PER_HOUR):
        variant = EMAIL.upper() if i % 2 else EMAIL
        auth_env.client.post("/api/v1/auth/login", json={"email": variant, "password": "wrong"})

    blocked = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL.title(), "password": DEFAULT_PASSWORD}
    )
    assert blocked.status_code == 429


def test_wrong_totp_codes_are_throttled_per_user(auth_env):
    secret = totp.generate_secret()
    auth_env.seed_user(EMAIL, totp_secret=secret)
    challenge = auth_env.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": DEFAULT_PASSWORD}
    ).json()["challenge_token"]
    good = pyotp.TOTP(secret).now()
    wrong = f"{(int(good) + 5) % 1_000_000:06d}"

    statuses = [
        auth_env.client.post(
            "/api/v1/auth/login/totp", json={"challenge_token": challenge, "code": wrong}
        ).status_code
        for _ in range(AUTH_ATTEMPT_LIMIT_PER_HOUR)
    ]
    assert statuses == [401] * AUTH_ATTEMPT_LIMIT_PER_HOUR

    # The correct code no longer helps: brute-forcing 6 digits is capped.
    final = auth_env.client.post(
        "/api/v1/auth/login/totp", json={"challenge_token": challenge, "code": good}
    )
    assert final.status_code == 429
    assert "set-cookie" not in final.headers


async def test_the_existing_limiter_function_is_reused_unchanged_with_a_route_key():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    first = await check_rate_limit(client, "x", limit_per_hour=1, route="auth-login", now=1000.0)
    second = await check_rate_limit(client, "x", limit_per_hour=1, route="auth-login", now=1001.0)
    other_route = await check_rate_limit(
        client, "x", limit_per_hour=1, route="classify", now=1001.0
    )

    assert (first.allowed, second.allowed, other_route.allowed) == (True, False, True)
