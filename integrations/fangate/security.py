"""Credential vault for Fangate secrets at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256 authenticated encryption) from the
cryptography package. The master key is provided via the FANGATE_ENC_KEY
environment variable and is NEVER persisted, logged, or returned by the API.

Only ciphertext is stored in PostgreSQL. There is no plaintext API-key
storage anywhere in the CRM.
"""

import base64
import hashlib
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings

logger = logging.getLogger("integrations.fangate.security")


class VaultUnavailableError(RuntimeError):
    """Raised when the encryption master key is not configured."""


class VaultDecryptionError(RuntimeError):
    """Raised when stored ciphertext cannot be decrypted."""


def derive_fernet_key(master_key: str) -> bytes:
    """Derive a Fernet key from an arbitrary master secret.

    Fernet keys are 32 url-safe base64 bytes; we deterministically derive
    one from the configured passphrase via SHA-256 so operators can use any
    strong passphrase in FANGATE_ENC_KEY.
    """
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class CredentialVault:
    """Encrypts/decrypts secrets using a configured master key."""

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
                    "Fangate credential vault requires FANGATE_ENC_KEY to be configured "
                    "(set it in the environment before persisting any credential)."
                )
            self._fernet = Fernet(derive_fernet_key(self._master_key))
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Return Fernet token (str) for the given secret. Raises
        VaultUnavailableError when no master key is configured."""
        return self._get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Return the plaintext secret. Never log the result."""
        try:
            return self._get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            logger.error(
                "Failed to decrypt a stored Fangate credential (invalid master key or "
                "corrupted ciphertext)"
            )
            raise VaultDecryptionError("Stored Fangate credential could not be decrypted") from exc


_vault: CredentialVault | None = None


def get_vault() -> CredentialVault:
    """Return the process-wide CredentialVault (cached)."""
    global _vault
    if _vault is None:
        _vault = CredentialVault(get_settings().fangate_enc_key)
    return _vault


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for at-rest storage."""
    return get_vault().encrypt(plaintext)


def decrypt_secret(token: str) -> str:
    """Decrypt a stored secret."""
    return get_vault().decrypt(token)


def _sanitize(record: Any) -> Any:
    """Import hook: allow logging_config sanitization to be applied."""
    return record
