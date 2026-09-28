"""Dropfans credential encryption.

Reuses the same Fernet (AES-128-CBC + HMAC-SHA256) encryption pattern
as the Fangate security module. The master key is derived via SHA-256
+ base64url encoding.

Credentials are only decrypted for the duration of a single API call.
Plaintext is never logged, returned, or exposed to the frontend.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings

logger = logging.getLogger("dropfans.security")


class VaultUnavailableError(RuntimeError):
    """Raised when the encryption master key is not configured."""


class VaultDecryptionError(RuntimeError):
    """Raised when stored ciphertext cannot be decrypted."""


class DropfansCredentialVault:
    """Fernet-based credential encryption vault."""

    def __init__(self, master_key: str | None = None) -> None:
        self._master_key = master_key
        self._fernet: Fernet | None = None

    @property
    def configured(self) -> bool:
        return bool(self._master_key)

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            if not self._master_key:
                raise VaultUnavailableError(
                    "Dropfans encryption master key not configured. "
                    "Set DROPFANS_ENC_KEY environment variable."
                )
            fernet_key = derive_fernet_key(self._master_key)
            self._fernet = Fernet(fernet_key)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext and return a Fernet token (ASCII string)."""
        f = self._get_fernet()
        return f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token and return plaintext."""
        f = self._get_fernet()
        try:
            return f.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.error("Dropfans vault decryption failed — invalid token")
            raise VaultDecryptionError("Invalid or corrupted ciphertext") from None


def derive_fernet_key(master_key: str) -> bytes:
    """Derive a Fernet key from a master key via SHA-256 + base64url."""
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_vault: DropfansCredentialVault | None = None


def get_vault() -> DropfansCredentialVault:
    """Return a process-wide singleton vault."""
    global _vault
    if _vault is None:
        settings = get_settings()
        master_key = getattr(settings, "dropfans_enc_key", None) or getattr(settings, "fangate_enc_key", None)
        _vault = DropfansCredentialVault(master_key)
    return _vault


def encrypt_secret(plaintext: str) -> str:
    """Convenience: encrypt a secret using the vault."""
    return get_vault().encrypt(plaintext)


def decrypt_secret(token: str) -> str:
    """Convenience: decrypt a secret using the vault."""
    return get_vault().decrypt(token)


def _sanitize(record: Any) -> Any:
    """Hook for logging_config sanitization."""
    return record
