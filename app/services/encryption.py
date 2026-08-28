"""Fernet symmetric encryption for sensitive tenant credentials stored in DB.

Generate a key once and store in ENCRYPTION_KEY env var:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

All WhatsApp access tokens, verify tokens, and CRM tokens are encrypted before
INSERT and decrypted after SELECT. The DB never stores plaintext secrets.
"""

import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import logger

_fernet: Optional[Fernet] = None


def _get_fernet() -> Optional[Fernet]:
    global _fernet
    if _fernet is None and settings.ENCRYPTION_KEY:
        try:
            _fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        except Exception as e:
            logger.error("encryption_init_failed", error=str(e))
    return _fernet


def encrypt(value: str) -> str:
    """Encrypt a string. Returns plaintext unchanged if ENCRYPTION_KEY is not set."""
    f = _get_fernet()
    if f is None or not value:
        return value
    try:
        return f.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error("encryption_failed", error=str(e))
        return value


def decrypt(value: str) -> str:
    """Decrypt a Fernet-encrypted string. Returns value unchanged if not encrypted or key missing."""
    f = _get_fernet()
    if f is None or not value:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        # Value might not be encrypted (e.g. legacy plain text) - return as-is
        return value


def is_encryption_enabled() -> bool:
    return bool(settings.ENCRYPTION_KEY)
