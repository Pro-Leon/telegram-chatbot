"""Automation domain models.

Provider-neutral types for autonomous operation lifecycle.
Contains NO provider calls, NO credentials, NO API keys.

State machine:
    PENDING -> RUNNING -> SUCCEEDED
                         -> FAILED
                         -> RETRYING -> RUNNING
                         -> UNKNOWN
    PENDING -> CANCELLED
    RETRYING -> CANCELLED
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class AutomationAction(str, enum.Enum):
    """Actions the automation system can perform.

    Extensible — future provider actions can be added without changing
    persistence semantics. The enum value is stored in the DB.
    """

    CREATE_DROP = "create_drop"
    CREATE_POST = "create_post"
    DELETE_POST = "delete_post"
    MANAGE_VAULT = "manage_vault"


class AutomationStatus(str, enum.Enum):
    """Operation lifecycle states.

    UNKNOWN exists for provider ambiguity: the request was sent but the
    application cannot determine whether the provider executed the write.
    UNKNOWN must NOT automatically transition to SUCCEEDED or be retried.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AutomationErrorClass(str, enum.Enum):
    """Error classification for retry decisions.

    Phase B stores these; Phase C decides which are retryable.
    """

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    PROVIDER = "provider"
    UNKNOWN = "unknown"


# Valid state transitions (from -> allowed targets)
_VALID_TRANSITIONS: dict[AutomationStatus, set[AutomationStatus]] = {
    AutomationStatus.PENDING: {AutomationStatus.RUNNING, AutomationStatus.CANCELLED},
    AutomationStatus.RUNNING: {
        AutomationStatus.SUCCEEDED,
        AutomationStatus.FAILED,
        AutomationStatus.RETRYING,
        AutomationStatus.UNKNOWN,
    },
    AutomationStatus.RETRYING: {AutomationStatus.RUNNING, AutomationStatus.CANCELLED},
    AutomationStatus.CANCELLED: set(),
    AutomationStatus.SUCCEEDED: set(),
    AutomationStatus.FAILED: set(),
    AutomationStatus.UNKNOWN: set(),
}


def is_valid_transition(current: AutomationStatus, target: AutomationStatus) -> bool:
    """Check whether a status transition is allowed."""
    return target in _VALID_TRANSITIONS.get(current, set())


@dataclass
class AutomationOperation:
    """A persisted automation operation.

    Provider-neutral — carries no credentials, no API keys, no provider objects.
    The ``params`` and ``provider_result`` dicts are JSONB in the DB.
    """

    creator_id: int
    action: AutomationAction
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    correlation_id: str = ""
    status: AutomationStatus = AutomationStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: datetime | None = None
    provider_result: dict[str, Any] = field(default_factory=dict)
    error_class: AutomationErrorClass | None = None
    error_message: str = ""
    id: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None
    cancelled_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "AutomationOperation":
        """Construct from an asyncpg Record or dict."""
        raw_status = row["status"]
        status = (
            AutomationStatus(raw_status)
            if isinstance(raw_status, str)
            else raw_status
        )
        raw_action = row["action"]
        action = (
            AutomationAction(raw_action)
            if isinstance(raw_action, str)
            else raw_action
        )
        raw_error = row.get("error_class")
        error_class = (
            AutomationErrorClass(raw_error)
            if isinstance(raw_error, str)
            else raw_error
        )
        return cls(
            id=row["id"],
            creator_id=row["creator_id"],
            action=action,
            target=row["target"],
            params=dict(row["params"]) if row.get("params") else {},
            idempotency_key=row["idempotency_key"] or "",
            correlation_id=row["correlation_id"] or "",
            status=status,
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            next_attempt_at=row["next_attempt_at"],
            provider_result=dict(row["provider_result"]) if row.get("provider_result") else {},
            error_class=error_class,
            error_message=row["error_message"] or "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
            cancelled_at=row["cancelled_at"],
        )


@dataclass(frozen=True)
class AutomationResult:
    """Execution outcome — separate from persistence.

    Carries the result of a provider call without executing anything itself.
    """

    success: bool
    provider_result: dict[str, Any] = field(default_factory=dict)
    error_class: AutomationErrorClass | None = None
    error_message: str = ""
    duration_ms: int = 0
