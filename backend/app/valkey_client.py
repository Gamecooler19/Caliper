"""Valkey client construction.

This is the one module allowed to construct a Valkey/Redis client.
Reads its connection string through the secrets layer
(``app.security.secrets.load_valkey_url``), never hardcoded or read
from a committed file. Backs the API rate limiter in
``backend/app/api/rate_limit.py``.

Uses the ``redis`` package's async client -- Valkey is wire-compatible
with the Redis protocol, which is why ``docker-compose.yml``'s existing
``valkey`` service (added in Module 1, unused until now) needs no new
service to support this.
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio

from app.security.secrets import load_valkey_url


def build_valkey_client(url: str | None = None) -> redis_asyncio.Redis:
    """Build an async Valkey/Redis client.

    ``url`` is accepted explicitly (useful for tests against a
    throwaway or real local Valkey/Redis instance); when omitted, it is
    loaded from the secrets layer, which in turn reads it from the
    environment.
    """

    connection_url = url if url is not None else load_valkey_url()
    return redis_asyncio.from_url(connection_url, decode_responses=True)
