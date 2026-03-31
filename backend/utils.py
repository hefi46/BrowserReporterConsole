import base64
import hashlib
import json
import logging
import os
import time
from typing import cast, Any

from passlib.context import CryptContext

logger = logging.getLogger("browser_reporter")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        # Simply limit to 72 characters (not bytes) to be safe
        safe_password = plain_password[:72]
        return pwd_context.verify(safe_password, hashed_password)
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False


def get_password_hash(password: str) -> str:
    # Simply limit to 72 characters (not bytes) to be safe
    safe_password = password[:72]
    return pwd_context.hash(safe_password)

# ---------------------------------------------------------------------------
# Secure Config Encryption Helper
# ---------------------------------------------------------------------------

try:
    # Prefer PyCryptodome (installed as dependency)
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError as e:  # pragma: no cover
    AES = None  # type: ignore
    pad = None  # type: ignore
    unpad = None  # type: ignore
    logger.warning("PyCryptodome not installed. Secure config encryption will fail.")

# Master key – REQUIRED via environment variable.
# The Windows agent must use the exact same key to decrypt config.
_MASTER_KEY_STR = os.getenv("ENCRYPTION_MASTER_KEY", "")
if not _MASTER_KEY_STR:
    logger.critical(
        "ENCRYPTION_MASTER_KEY is not set! "
        "Config encryption/decryption will fail. "
        "Set this in docker-compose.yml or your environment."
    )
_MASTER_KEY = _MASTER_KEY_STR.encode() if _MASTER_KEY_STR else b""
_AES_KEY = hashlib.sha256(_MASTER_KEY).digest() if _MASTER_KEY else b""


def encrypt_secure_config(plain_config: dict) -> dict:
    """Encrypt the *plain_config* dict following the specification expected by
    the Windows collector (AES-256-CBC + SHA-256 checksum).

    Returns the dict that should be saved as *secureconfig.json*.
    """
    if AES is None:
        raise RuntimeError("PyCryptodome missing – install pycryptodome to use secure config generation")

    # Ensure type checker knows AES is available
    assert AES is not None  # type: ignore[assertion]

    # Dump compact JSON (no whitespace) so checksum matches regardless of spacing
    raw_bytes = json.dumps(plain_config, separators=(",", ":")).encode()
    iv = os.urandom(16)
    cipher = cast(Any, AES).new(_AES_KEY, AES.MODE_CBC, iv)  # type: ignore
    ct = cipher.encrypt(pad(raw_bytes, AES.block_size))

    return {
        "version": "1.0",
        "encrypted_data": base64.b64encode(ct).decode(),
        "iv": base64.b64encode(iv).decode(),
        "checksum": hashlib.sha256(raw_bytes).hexdigest(),
        "created_at": int(time.time()),
    }


def decrypt_secure_config(encrypted_config: dict) -> dict:
    """Decrypt the *encrypted_config* dict back to the original plain configuration.
    
    Args:
        encrypted_config: Dict with 'encrypted_data', 'iv', 'checksum', etc.
        
    Returns:
        The original plain configuration dict.
        
    Raises:
        RuntimeError: If PyCryptodome is missing or decryption fails.
        ValueError: If checksum verification fails.
    """
    if AES is None or unpad is None:
        raise RuntimeError("PyCryptodome missing – install pycryptodome to use secure config decryption")

    # Ensure type checker knows AES is available
    assert AES is not None  # type: ignore[assertion]
    assert unpad is not None  # type: ignore[assertion]

    try:
        # Decode base64 data
        encrypted_data = base64.b64decode(encrypted_config["encrypted_data"])
        iv = base64.b64decode(encrypted_config["iv"])
        expected_checksum = encrypted_config["checksum"]
        
        # Decrypt the data
        cipher = cast(Any, AES).new(_AES_KEY, AES.MODE_CBC, iv)  # type: ignore
        padded_data = cipher.decrypt(encrypted_data)
        
        # Remove padding
        raw_bytes = cast(Any, unpad)(padded_data, AES.block_size)  # type: ignore
        
        # Verify checksum
        actual_checksum = hashlib.sha256(raw_bytes).hexdigest()
        if actual_checksum != expected_checksum:
            raise ValueError(f"Checksum verification failed. Expected: {expected_checksum}, Got: {actual_checksum}")
        
        # Parse JSON
        config_json = raw_bytes.decode('utf-8')
        return json.loads(config_json)
        
    except Exception as e:
        raise RuntimeError(f"Failed to decrypt secure config: {str(e)}") from e