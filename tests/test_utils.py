"""Tests for backend.utils — password hashing and secure config encryption."""
import pytest
from backend.utils import (
    get_password_hash,
    verify_password,
    encrypt_secure_config,
    decrypt_secure_config,
)

# passlib + bcrypt 5.x on Python 3.13 has a known incompatibility
# (bcrypt dropped __about__). Skip password tests if hashing is broken locally.
_hashing_works = bool(get_password_hash("probe"))
_skip_if_bcrypt_broken = pytest.mark.skipif(
    not _hashing_works,
    reason="passlib/bcrypt version mismatch on this system (works in Docker)",
)


# ── Password hashing ────────────────────────────────────────────────────


@_skip_if_bcrypt_broken
def test_hash_and_verify_password():
    hashed = get_password_hash("mysecretpassword")
    assert hashed  # non-empty string
    assert hashed != "mysecretpassword"  # not stored in plain text
    assert verify_password("mysecretpassword", hashed)


@_skip_if_bcrypt_broken
def test_wrong_password_fails_verification():
    hashed = get_password_hash("correct-password")
    assert not verify_password("wrong-password", hashed)


@_skip_if_bcrypt_broken
def test_password_truncated_at_72_chars():
    """bcrypt only processes the first 72 bytes — our code should handle this."""
    long_pw = "a" * 100
    hashed = get_password_hash(long_pw)
    # Verifying the full 100-char string should still work because
    # both hashing and verification truncate to 72
    assert verify_password(long_pw, hashed)
    # Verifying just the first 72 chars should also work
    assert verify_password("a" * 72, hashed)


@_skip_if_bcrypt_broken
def test_empty_password_hashes():
    hashed = get_password_hash("")
    assert hashed
    assert verify_password("", hashed)


# ── Secure config encryption ────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    config = {
        "server_url": "http://example.com:8000",
        "sync_interval_minutes": 5,
        "browsers": ["chrome", "edge"],
    }
    encrypted = encrypt_secure_config(config)

    # Check encrypted envelope structure
    assert "version" in encrypted
    assert "encrypted_data" in encrypted
    assert "iv" in encrypted
    assert "checksum" in encrypted
    assert "created_at" in encrypted
    assert encrypted["version"] == "1.0"

    # Decrypt and verify
    decrypted = decrypt_secure_config(encrypted)
    assert decrypted == config


def test_encrypt_produces_different_ciphertexts():
    """Each encryption should use a random IV, producing different output."""
    config = {"key": "value"}
    enc1 = encrypt_secure_config(config)
    enc2 = encrypt_secure_config(config)
    assert enc1["encrypted_data"] != enc2["encrypted_data"]
    assert enc1["iv"] != enc2["iv"]
    # But both should decrypt to the same thing
    assert decrypt_secure_config(enc1) == decrypt_secure_config(enc2)


def test_tampered_ciphertext_raises():
    """Modifying encrypted data should cause decryption to fail."""
    config = {"secret": "data"}
    encrypted = encrypt_secure_config(config)

    # Tamper with the encrypted data
    encrypted["encrypted_data"] = encrypted["encrypted_data"][:-4] + "AAAA"

    try:
        decrypt_secure_config(encrypted)
        assert False, "Should have raised an exception"
    except RuntimeError:
        pass  # Expected


def test_encrypt_complex_config():
    """Test encryption with nested objects and unicode."""
    config = {
        "server_url": "http://localhost:8000",
        "monitored_users": ["alice", "bob"],
        "monitored_hours": {"start": "08:00", "end": "16:00"},
        "exit_password": "p@$$w0rd!",
    }
    encrypted = encrypt_secure_config(config)
    decrypted = decrypt_secure_config(encrypted)
    assert decrypted == config
