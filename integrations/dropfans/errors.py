"""Dropfans integration error hierarchy.

Mirrors the Fangate error pattern for consistency.
Every exception retains safe operational context but NEVER retains
credentials, authorization headers, or sensitive request bodies.
"""

from __future__ import annotations

from typing import Any


class DropfansError(Exception):
    """Base class for all Dropfans errors."""

    def __init__(
        self,
        operation: str,
        message: str | None = None,
        status_code: int | None = None,
        correlation_id: str | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message or f"DropfansError({operation})")
        self.operation = operation
        self.status_code = status_code
        self.correlation_id = correlation_id
        self.payload = payload

    @property
    def message(self) -> str:
        return str(self.args[0])

    def __str__(self) -> str:
        parts = [f"operation={self.operation!r}"]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.correlation_id:
            parts.append(f"corr={self.correlation_id!r}")
        if self.message:
            parts.append(f"message={self.message!r}")
        return f"DropfansError({', '.join(parts)})"


class DropfansAuthenticationError(DropfansError):
    """401 -- missing/invalid bearer token."""


class DropfansAuthorizationError(DropfansError):
    """403 -- token valid but resource not permitted."""


class DropfansNotFoundError(DropfansError):
    """404 -- resource not found."""


class DropfansValidationError(DropfansError):
    """400/422 -- request validation failed."""


class DropfansRateLimitError(DropfansError):
    """429 -- rate limit exceeded."""

    def __init__(
        self,
        operation: str,
        message: str | None = None,
        status_code: int | None = 429,
        correlation_id: str | None = None,
        retry_after: float | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(operation, message, status_code, correlation_id, payload)
        self.retry_after = retry_after


class DropfansServerError(DropfansError):
    """5xx -- server-side error."""


class DropfansResponseError(DropfansError):
    """HTTP succeeded but response unparseable."""


class DropfansTransportError(DropfansError):
    """Network failure."""


class DropfansTimeoutError(DropfansTransportError):
    """Request timeout."""
