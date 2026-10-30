"""Password hashing (argon2id via argon2-cffi).

Plaintext passwords exist only as function arguments here; they are
never stored or logged.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

#: Minimum length enforced on user-chosen passwords (change-password).
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()

#: A valid hash of a random value, verified against when a login names
#: an unknown email so that path costs the same as a real verification.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True iff ``password`` matches ``password_hash``. Never raises for
    a mismatch or a malformed hash."""

    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def generate_initial_password() -> str:
    """A random one-time password for a newly created account."""

    return secrets.token_urlsafe(18)
