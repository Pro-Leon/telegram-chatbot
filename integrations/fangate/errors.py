"""Fangate API exception hierarchy.

Every exception retains safe operational context (operation, status code,
Fangate message, correlation id) but NEVER credentials, authorization
headers, or complete sensitive request bodies.
"""

from typing import Any


class FangateError(Exception):
    """Base class for all Fangate integration errors."""

    def __init__(
        self,
        operation: str,
        message: str | None = None,
        status_code: int | None = None,
        correlation_id: str | None = None,
        payload: Any | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.correlation_id = correlation_id
        self.payload = payload
        super().__init__(message or f"Fangate operation '{operation}' failed")

    @property
    def message(self) -> str:
        return str(self.args[0])

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(operation={self.operation}, status={self.status_code}, message={self.message})"


class FangateAuthenticationError(FangateError):
    """401 — missing/invalid bearer token."""


class FangateAuthorizationError(FangateError):
    """403 — token valid but resource not permitted for the caller."""


class FangateNotFoundError(FangateError):
    """404 — resource does not exist."""


class FangateValidationError(FangateError):
    """422/400 — request rejected by Fangate validation."""


class FangateRateLimitError(FangateError):
    """429 — rate limited; retry_after may be parsed from Retry-After."""

    def __init__(
        self,
        operation: str,
        message: str | None = None,
        status_code: int | None = 429,
        correlation_id: str | None = None,
        retry_after: float | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(
            operation,
            message=message,
            status_code=status_code,
            correlation_id=correlation_id,
            payload=payload,
        )
        self.retry_after = retry_after


class FangateServerError(FangateError):
    """5xx — Fangate backend failure."""


class FangateResponseError(FangateError):
    """Response succeeded at HTTP level but could not be parsed/enveloped."""


class FangateTransportError(FangateError):
    """Network/transport failure reaching Fangate."""


class FangateTimeoutError(FangateTransportError):
    """Request to Fangate timed out."""
