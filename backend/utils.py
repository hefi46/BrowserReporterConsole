from passlib.context import CryptContext
from typing import cast, Any

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# ---------------------------------------------------------------------------
# Secure Config Encryption Helper
# ---------------------------------------------------------------------------
import json
import base64
import hashlib
import os
import time

try:
    # Prefer PyCryptodome (installed as dependency)
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError as e:  # pragma: no cover
    AES = None  # type: ignore
    pad = None  # type: ignore
    print("⚠️  PyCryptodome not installed. Secure config encryption will fail.")

# Master key hard-coded in Windows collector
_MASTER_KEY = b"BrowserReporter2024!MasterKey"
_AES_KEY = hashlib.sha256(_MASTER_KEY).digest()  # 32-byte key for AES-256


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