"""Fangate commerce integration boundary."""

from .errors import (
    FangateAuthenticationError,
    FangateAuthorizationError,
    FangateError,
    FangateNotFoundError,
    FangateRateLimitError,
    FangateResponseError,
    FangateServerError,
    FangateTimeoutError,
    FangateTransportError,
    FangateValidationError,
)
from .models import (
    FangateProduct,
    FangateProductPage,
    FangateWallet,
    FangateWalletTransaction,
    FangateWebhook,
    FangateWebhookEventPayload,
)
from .security import (
    CredentialVault,
    VaultDecryptionError,
    VaultUnavailableError,
    decrypt_secret,
    encrypt_secret,
    get_vault,
)

__all__ = [
    "CredentialVault",
    "FangateAuthenticationError",
    "FangateAuthorizationError",
    "FangateError",
    "FangateNotFoundError",
    "FangateProduct",
    "FangateProductPage",
    "FangateRateLimitError",
    "FangateResponseError",
    "FangateServerError",
    "FangateTimeoutError",
    "FangateTransportError",
    "FangateValidationError",
    "FangateWallet",
    "FangateWalletTransaction",
    "FangateWebhook",
    "FangateWebhookEventPayload",
    "VaultDecryptionError",
    "VaultUnavailableError",
    "decrypt_secret",
    "encrypt_secret",
    "get_vault",
]
