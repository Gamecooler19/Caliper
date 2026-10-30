"""TOTP (RFC 6238) secret generation, encrypted storage, and verification."""

from __future__ import annotations

import uuid

import pyotp
import redis.asyncio as redis_asyncio

from app.security.crypto import decrypt_text, encrypt_text

ISSUER = "Caliper"

#: Accept the previous and next 30s step too, to tolerate clock drift.
VALID_WINDOW = 1

#: A code is remembered this long after use (> 3 steps) so it can't be replayed.
_USED_CODE_TTL_SECONDS = 90


def generate_secret() -> str:
    """A new random base32 TOTP secret (plaintext; encrypt before storing)."""

    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return encrypt_text(secret)


def decrypt_secret(encrypted: str) -> str:
    return decrypt_text(encrypted)


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_code(secret: str, code: str) -> bool:
    """True iff ``code`` is a valid current (+/- one step) 6-digit code."""

    cleaned = code.strip()
    if len(cleaned) != 6 or not cleaned.isdigit():
        return False
    return bool(pyotp.TOTP(secret).verify(cleaned, valid_window=VALID_WINDOW))


async def consume_code_once(
    client: redis_asyncio.Redis, user_id: uuid.UUID, code: str
) -> bool:
    """Record ``code`` as used for ``user_id``; False if it already was
    (replay within the validity window)."""

    key = f"totp_used:{user_id}:{code.strip()}"
    return bool(await client.set(key, "1", nx=True, ex=_USED_CODE_TTL_SECONDS))
