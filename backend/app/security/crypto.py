"""Column-level encryption for sensitive database values.

``app.security.secrets`` only ever loaded environment-sourced
application secrets; nothing in the codebase encrypted database column
values before Module 6. This is the smallest utility that need
requires: a thin wrapper over ``cryptography``'s Fernet (AES-128-CBC
with an HMAC-SHA256 authentication tag, random IV per message), keyed
by the ``COLUMN_ENCRYPTION_KEY`` secret loaded through the existing
secrets layer. It exists for ``users.totp_secret_encrypted`` and is the
only encryption primitive in the codebase.

Nothing here logs plaintext or ciphertext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.security.secrets import load_column_encryption_key


class DecryptionError(RuntimeError):
    """Raised when a stored value cannot be decrypted (wrong key or
    corrupted/tampered ciphertext)."""


def _fernet() -> Fernet:
    return Fernet(load_column_encryption_key().encode())


def encrypt_text(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return a urlsafe-base64 token string."""

    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_text(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt_text`."""

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Could not decrypt stored value.") from exc
