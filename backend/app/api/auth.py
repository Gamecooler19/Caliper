"""Shared-API-key authentication for the Caliper API.

Deployment model: a single shared API key, not per-user accounts. TOTP
MFA and RBAC are explicitly out of scope for Module 4 -- see
architecture.md's Security posture for the longer-run target and
reports/module-4-report.md for why this module doesn't build toward it
yet.

Checks the ``X-API-Key`` header on every route under ``/api/v1``
against the ``CALIPER_API_KEY`` secret. Missing or wrong key raises
:class:`~app.api.errors.UnauthorizedError`, which the registered
exception handler turns into 401 with ``{"error": "unauthorized"}``.
"""

from __future__ import annotations

import hmac

from fastapi import Header

from app.api.errors import UnauthorizedError
from app.security.secrets import load_caliper_api_key

#: Fixed actor identifier recorded on audit log entries and used as the
#: rate-limit key identifier. There is no per-key identity in the
#: single-shared-key model, so every authenticated request is
#: attributed to this literal.
API_CLIENT_IDENTITY = "api-client"


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """FastAPI dependency: validate the ``X-API-Key`` header.

    Returns :data:`API_CLIENT_IDENTITY` on success (the "identity" this
    single-shared-key model has to offer, used as the audit log actor
    and the rate-limit key identifier). Raises
    :class:`~app.api.errors.UnauthorizedError` when the header is
    missing or doesn't match the configured key.

    Uses :func:`hmac.compare_digest` rather than ``==`` to avoid a
    timing side-channel on key comparison.
    """

    if not x_api_key:
        raise UnauthorizedError("Missing X-API-Key header.")

    expected = load_caliper_api_key()
    if not hmac.compare_digest(x_api_key, expected):
        raise UnauthorizedError("X-API-Key header did not match.")

    return API_CLIENT_IDENTITY
