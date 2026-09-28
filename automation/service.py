"""Automation service — the only write authority for autonomous operations.

Orchestrates: eligibility → policy → idempotency → persist → claim →
autonomy check → provider call → result classification → persist result.

Every autonomous provider action MUST go through this service.
No direct DropFans calls from scheduler, LLM, or other workers.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from core.config import get_settings
from db import automation as adb

logger = logging.getLogger("automation.service")
_settings = get_settings()


# ── Error classification ───────────────────────────────────────────────────

def classify_provider_error(exc: Exception) -> tuple[str, str]:
    """Classify a provider exception into (error_class, error_message).

    Returns (error_class, error_message) tuple.
    Never raises — fallback is ("unknown", str(exc)).
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc)

    # DropFans-specific error mapping
    if "DropfansAuthenticationError" in exc_name or "401" in exc_msg:
        return "auth", exc_msg
    if "DropfansAuthorizationError" in exc_name or "403" in exc_msg:
        return "auth", exc_msg
    if "DropfansRateLimitError" in exc_name or "429" in exc_msg:
        return "rate_limit", exc_msg
    if "DropfansTimeoutError" in exc_name or "timeout" in exc_msg.lower():
        return "timeout", exc_msg
    if "DropfansTransportError" in exc_name or "network" in exc_msg.lower():
        return "network", exc_msg
    if "DropfansValidationError" in exc_name or "400" in exc_msg or "422" in exc_msg:
        return "validation", exc_msg
    if "DropfansNotFoundError" in exc_name or "404" in exc_msg:
        return "not_found", exc_msg
    if "DropfansServerError" in exc_name or "500" in exc_msg:
        return "provider", exc_msg

    return "unknown", exc_msg


def is_retryable_error(error_class: str) -> bool:
    """Whether an error class is safe to retry automatically.

    AUTH and VALIDATION are never retried.
    UNKNOWN is never retried (fail-closed for ambiguous errors).
    """
    return error_class in ("rate_limit", "timeout", "network", "provider")


# ── AutomationService ──────────────────────────────────────────────────────


class AutomationService:
    """Central write authority for autonomous provider operations.

    Every autonomous provider action follows:
        ELIGIBILITY → POLICY → IDEMPOTENCY → PERSIST → CLAIM →
        AUTONOMY CHECK → PROVIDER CALL → CLASSIFY → PERSIST RESULT

    No direct provider calls from outside this service.
    """

    async def execute(
        self,
        *,
        creator_id: int,
        action: str,
        target: str,
        params: dict[str, Any] | None = None,
        idempotency_key: str = "",
        correlation_id: str = "",
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Create and persist an automation operation.

        Returns the operation row. The actual provider execution happens
        when the scheduler claims and executes the operation.

        This method is the ONLY entry point for autonomous provider writes.
        """
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Autonomy kill switch — checked immediately before persistence
        if not _settings.autonomy_enabled:
            logger.info(
                "automation.blocked_by_kill_switch",
                extra={"creator_id": creator_id, "action": action},
            )
            return {
                "status": "cancelled",
                "reason": "autonomy_disabled",
                "correlation_id": correlation_id,
            }

        # Persist the operation
        op = await adb.create_operation(
            creator_id=creator_id,
            action=action,
            target=target,
            params=params or {},
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
        )

        if op is None:
            logger.error(
                "automation.persist_failed",
                extra={"creator_id": creator_id, "action": action},
            )
            return {
                "status": "failed",
                "reason": "persistence_failed",
                "correlation_id": correlation_id,
            }

        # Check if this was an idempotent return
        if op.get("id") and op.get("status"):
            logger.info(
                "automation.operation_exists",
                extra={
                    "creator_id": creator_id,
                    "action": action,
                    "operation_id": op["id"],
                    "existing_status": op.get("status"),
                },
            )
            return {
                "status": "exists",
                "operation_id": op["id"],
                "existing_status": op.get("status"),
                "correlation_id": correlation_id,
            }

        op_id = op["id"]
        logger.info(
            "automation.operation_created",
            extra={
                "creator_id": creator_id,
                "action": action,
                "operation_id": op_id,
                "correlation_id": correlation_id,
            },
        )

        return {
            "status": "created",
            "operation_id": op_id,
            "correlation_id": correlation_id,
        }

    async def execute_provider_write(
        self,
        operation_id: int,
        creator_id: int,
        provider_fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a provider write for a claimed operation.

        This is called by the scheduler after claiming an operation.
        Handles: autonomy check → provider call → classify → persist result.

        Returns operation result dict.
        """
        # Autonomy kill switch — checked immediately before provider call
        if not _settings.autonomy_enabled:
            await adb.mark_cancelled(operation_id, creator_id)
            logger.info(
                "automation.kill_switch_during_execution",
                extra={"operation_id": operation_id, "creator_id": creator_id},
            )
            return {"status": "cancelled", "reason": "autonomy_disabled"}

        t0 = time.monotonic()
        try:
            result = await provider_fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)

            await adb.mark_succeeded(
                operation_id,
                creator_id,
                provider_result={"result": result} if result else {},
            )

            logger.info(
                "automation.provider_write_succeeded",
                extra={
                    "operation_id": operation_id,
                    "creator_id": creator_id,
                    "duration_ms": duration_ms,
                },
            )
            return {"status": "succeeded", "result": result}

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_class, error_message = classify_provider_error(exc)

            # Determine next state based on error class and attempt count
            op = await adb.get_operation(operation_id, creator_id)
            attempt_count = op.get("attempt_count", 1) if op else 1
            max_attempts = op.get("max_attempts", 3) if op else 3

            if error_class in ("auth", "validation"):
                # Never retry auth/validation errors
                await adb.mark_failed(
                    operation_id, creator_id, error_class, error_message,
                )
                logger.warning(
                    "automation.provider_write_failed_permanent",
                    extra={
                        "operation_id": operation_id,
                        "creator_id": creator_id,
                        "error_class": error_class,
                        "duration_ms": duration_ms,
                    },
                )
                return {"status": "failed", "error_class": error_class}

            if attempt_count >= max_attempts:
                # Max attempts exceeded
                await adb.mark_failed(
                    operation_id, creator_id, error_class, error_message,
                )
                logger.warning(
                    "automation.provider_write_max_attempts",
                    extra={
                        "operation_id": operation_id,
                        "creator_id": creator_id,
                        "attempt_count": attempt_count,
                        "error_class": error_class,
                        "duration_ms": duration_ms,
                    },
                )
                return {"status": "failed", "error_class": error_class}

            if error_class == "timeout":
                # Timeout → UNKNOWN (ambiguous provider state)
                await adb.mark_unknown(
                    operation_id, creator_id, error_class, error_message,
                )
                logger.warning(
                    "automation.provider_write_unknown",
                    extra={
                        "operation_id": operation_id,
                        "creator_id": creator_id,
                        "error_class": error_class,
                        "duration_ms": duration_ms,
                    },
                )
                return {"status": "unknown", "error_class": error_class}

            if is_retryable_error(error_class):
                # Retryable → RETRYING
                await adb.mark_retrying(
                    operation_id, creator_id, error_class, error_message,
                )
                logger.info(
                    "automation.provider_write_retrying",
                    extra={
                        "operation_id": operation_id,
                        "creator_id": creator_id,
                        "error_class": error_class,
                        "attempt_count": attempt_count,
                        "duration_ms": duration_ms,
                    },
                )
                return {"status": "retrying", "error_class": error_class}

            # Unknown/other → FAILED
            await adb.mark_failed(
                operation_id, creator_id, error_class, error_message,
            )
            logger.warning(
                "automation.provider_write_failed",
                extra={
                    "operation_id": operation_id,
                    "creator_id": creator_id,
                    "error_class": error_class,
                    "duration_ms": duration_ms,
                },
            )
            return {"status": "failed", "error_class": error_class}

    async def cancel(self, operation_id: int, creator_id: int) -> bool:
        """Cancel a pending or retrying operation."""
        return await adb.cancel_operation(operation_id, creator_id)

    async def get_operation(
        self, operation_id: int, creator_id: int,
    ) -> dict[str, Any] | None:
        """Get an operation by ID, creator-scoped."""
        return await adb.get_operation(operation_id, creator_id)

    async def list_operations(
        self,
        creator_id: int,
        status: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List operations for a creator."""
        return await adb.list_operations(
            creator_id, status=status, action=action,
            limit=limit, offset=offset,
        )

    async def get_by_idempotency_key(
        self, creator_id: int, idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Get an operation by idempotency key, creator-scoped."""
        return await adb.get_operation_by_idempotency_key(
            creator_id, idempotency_key,
        )
