"""Unit tests: password hashing and TOTP primitives."""

from __future__ import annotations

import datetime as dt
import uuid

import fakeredis.aioredis
import pyotp
import pytest

from app.auth import totp
from app.auth.passwords import (
    DUMMY_HASH,
    generate_initial_password,
    hash_password,
    verify_password,
)
from app.security.crypto import DecryptionError, decrypt_text, encrypt_text

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_password_hash_verify_round_trip():
    hashed = hash_password("a long enough password")
    assert verify_password(hashed, "a long enough password") is True


def test_password_wrong_password_does_not_verify():
    hashed = hash_password("a long enough password")
    assert verify_password(hashed, "a different password") is False


def test_password_hash_is_argon2id_and_not_plaintext():
    hashed = hash_password("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert "a long enough password" not in hashed


def test_password_hashes_are_salted():
    assert hash_password("same password here") != hash_password("same password here")


def test_malformed_stored_hash_verifies_false_instead_of_raising():
    assert verify_password("not-a-hash", "anything") is False


def test_dummy_hash_is_a_valid_hash_that_matches_nothing_guessable():
    assert DUMMY_HASH.startswith("$argon2id$")
    assert verify_password(DUMMY_HASH, "password") is False


def test_generated_initial_passwords_are_long_and_distinct():
    a, b = generate_initial_password(), generate_initial_password()
    assert a != b
    assert len(a) >= 20


# ---------------------------------------------------------------------------
# Column encryption (the primitive TOTP storage relies on)
# ---------------------------------------------------------------------------


def test_encrypt_text_round_trip_and_is_not_plaintext():
    token = encrypt_text("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in token
    assert decrypt_text(token) == "JBSWY3DPEHPK3PXP"


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch):
    from cryptography.fernet import Fernet

    token = encrypt_text("secret")
    monkeypatch.setenv("COLUMN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        decrypt_text(token)


def test_decrypt_tampered_ciphertext_raises():
    token = encrypt_text("secret")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        decrypt_text(tampered)


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def test_generated_secret_is_base32_and_usable():
    secret = totp.generate_secret()
    assert len(secret) >= 16
    pyotp.TOTP(secret).now()  # raises if not valid base32


def test_secret_encrypted_storage_round_trip_hides_plaintext():
    secret = totp.generate_secret()
    stored = totp.encrypt_secret(secret)
    assert secret not in stored
    assert totp.decrypt_secret(stored) == secret


def test_provisioning_uri_names_issuer_and_account():
    uri = totp.provisioning_uri(totp.generate_secret(), "user@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "Caliper" in uri
    assert "user%40example.com" in uri


def test_valid_code_verifies():
    secret = totp.generate_secret()
    assert totp.verify_code(secret, pyotp.TOTP(secret).now()) is True


def test_adjacent_step_is_accepted_for_clock_drift():
    secret = totp.generate_secret()
    next_step = pyotp.TOTP(secret).at(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30))
    assert totp.verify_code(secret, next_step) is True


def test_code_from_far_outside_window_is_rejected():
    secret = totp.generate_secret()
    old = pyotp.TOTP(secret).at(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10))
    assert totp.verify_code(secret, old) is False


def test_wrong_code_is_rejected():
    secret = totp.generate_secret()
    good = pyotp.TOTP(secret).now()
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    assert totp.verify_code(secret, wrong) is False


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 456"])
def test_malformed_codes_are_rejected(code: str):
    assert totp.verify_code(totp.generate_secret(), code) is False


async def test_consume_code_once_rejects_replay():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()
    assert await totp.consume_code_once(client, user_id, "123456") is True
    assert await totp.consume_code_once(client, user_id, "123456") is False
    # Same code for a different user is independent.
    assert await totp.consume_code_once(client, uuid.uuid4(), "123456") is True
