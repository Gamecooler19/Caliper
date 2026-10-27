"""Valkey-backed rate limiter for the Caliper API.

Owns ``check_rate_limit``, applied to ``POST /api/v1/classify``
specifically. Defaults to 30 requests per hour per API key.

That default is a protective default, not a precisely justified
figure: it exists so one key can't exhaust the Meta Model API quota or
the deployment's storage budget by accident or misuse, not because 30
is a number derived from any particular traffic analysis. A deployment
expecting heavier legitimate use should raise it.

Implementation: a fixed-window counter keyed by
``ratelimit:classify:{key_identifier}:{window_start_epoch_hour}``, using
Valkey's ``INCR`` (atomic) and ``EXPIRE``. A fixed window can allow a
short burst at the window boundary (up to ~2x the limit across two
adjacent windows); that tradeoff is accepted here in exchange for the
simplicity of a single atomic INCR per request, consistent with this
being a protective default rather than a precision instrument.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import redis.asyncio as redis_asyncio

#: Protective default, not a precisely justified figure -- see module
#: docstring.
DEFAULT_LIMIT_PER_HOUR = 30

WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one rate-limit check.

    ``retry_after_seconds`` is only meaningful when ``allowed`` is
    False; it is the number of seconds until the current fixed window
    resets.
    """

    allowed: bool
    retry_after_seconds: int


async def check_rate_limit(
    client: redis_asyncio.Redis,
    key_identifier: str,
    *,
    limit_per_hour: int = DEFAULT_LIMIT_PER_HOUR,
    route: str = "classify",
    now: float | None = None,
) -> RateLimitResult:
    """Check and record one request against ``key_identifier``'s
    per-hour limit for ``route``.

    Increments the counter for the current fixed hour window
    unconditionally (every call counts, whether it ends up allowed or
    not, matching a strict "N requests per hour" contract), then
    compares the post-increment count against ``limit_per_hour``.

    Returns a :class:`RateLimitResult`. Never raises for a normal
    "limit exceeded" outcome -- that is a valid, expected result the
    caller maps to an HTTP 429, not an error from this function's
    point of view.
    """

    current_time = now if now is not None else time.time()
    window_start = int(current_time // WINDOW_SECONDS) * WINDOW_SECONDS
    redis_key = f"ratelimit:{route}:{key_identifier}:{window_start}"

    count = await client.incr(redis_key)
    if count == 1:
        # First request in this window: set the window's expiry so the
        # key disappears on its own once the window has fully elapsed.
        await client.expire(redis_key, WINDOW_SECONDS)

    if count <= limit_per_hour:
        return RateLimitResult(allowed=True, retry_after_seconds=0)

    retry_after = window_start + WINDOW_SECONDS - int(current_time)
    return RateLimitResult(allowed=False, retry_after_seconds=max(retry_after, 1))
