"""Shared pytest fixtures for the backend test suite.

The autouse ``no_real_network_calls`` fixture enforces that the default
test run (everything except ``backend/tests/integration/``) never makes
a real network call to api.meta.ai or to object storage. It does this by
asserting that respx is in its default "mocked" mode for every test
function, and by failing loudly if any test leaves an httpx request
unmocked.
"""

from __future__ import annotations

import pytest
import respx


@pytest.fixture(autouse=True)
def no_real_network_calls(request: pytest.FixtureRequest):
    """Guarantee the default suite makes zero real network calls.

    Tests under ``backend/tests/integration/`` opt out explicitly (they
    are skipped by default anyway, gated on RUN_LIVE_META_API_TESTS=1),
    everything else runs inside a respx router that raises if a request
    doesn't match a registered mock route, rather than letting it fall
    through to the real network.
    """

    if "integration" in str(request.node.fspath):
        yield
        return

    with respx.mock(assert_all_called=False) as router:
        # No routes registered by default: any unmocked httpx call made
        # during a test in this suite raises respx.AllMockedAssertionError
        # instead of reaching the network. Individual tests register the
        # routes they need on this same router via the `respx_router`
        # fixture below, or via `respx.mock` directly.
        request.node.respx_router = router
        yield router


@pytest.fixture
def respx_router(no_real_network_calls):
    """Expose the active respx router for tests that register mock routes."""

    return no_real_network_calls


@pytest.fixture(autouse=True)
def meta_model_api_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Provide deterministic, fake secret values for every mocked test so
    the secrets layer never raises MissingSecretError mid-test and no
    test accidentally depends on a developer's real local environment.

    Skipped for tests under ``backend/tests/integration/``: those tests
    are meant to use the developer's real, configured secrets (a real
    META_MODEL_API_KEY, real storage credentials) when explicitly
    enabled, and must not have them overwritten with fake values.
    """

    if "integration" in str(request.node.fspath):
        yield
        return

    monkeypatch.setenv("META_MODEL_API_KEY", "test-fake-meta-model-api-key")
    monkeypatch.setenv("SAM_MODEL_ID", "sam-3.1")
    monkeypatch.setenv("MUSE_SPARK_MODEL_ID", "muse-spark-1.3")
    monkeypatch.setenv("STORAGE_ENDPOINT_URL", "https://storage.test.invalid")
    monkeypatch.setenv("STORAGE_ACCESS_KEY", "test-fake-access-key")
    monkeypatch.setenv("STORAGE_SECRET_KEY", "test-fake-secret-key")
    monkeypatch.setenv("STORAGE_BUCKET", "test-fake-bucket")
    yield
