"""Automation-operation DAO (commerce rebuild Phase 3d). State machine ledger.

Durable record + claim race + terminal-state guards for autonomous
operations over ``automation_operations``. Lifecycle:

pending → running → succeeded | failed | retrying | unknown
pending → cancelled, retrying → running | cancelled

Terminal states (succeeded/failed/cancelled/unknown) never transition.
Idempotency key scoped per creator with a partial unique index that
ignores empty keys: ``UNIQUE(creator_id, idempotency_key)
WHERE idempotency_key <> ''``. The unique constraint is authoritative
for races; the pre-check SELECT converges the common path.

DB errors propagate (no swallowing): callers distinguish persistence
failure from idempotent-exists. No secrets are persisted (params carry
only non-credential operation arguments).

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3d. Evidence: call
sites in automation/service.py; tests/test_automation_persistence.py;
live staging DDL.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.automation")

_VALID_STATUSES = frozenset(
    {"pending", "running", "succeeded", "failed", "retrying", "unknown", "cancelled"}
)

# Allowed sources per transition target (terminal states have no outgoing edge).
_SOURCES = {
    "running": ("pending", "retrying"),
    "succeeded": ("running",),
    "failed": ("running",),
    "retrying": ("running",),
    "unknown": ("running",),
    "cancelled": ("pending", "retrying", "running"),
}


def _decode(row: Any) -> dict[str, Any]:
    """Row → dict, parsing jsonb params/provider_result (str from asyncpg)."""
    d = dict(row)
    for key in ("params", "provider_result"):
        value = d.get(key)
        if isinstance(value, str):
            try:
                d[key] = json.loads(value)
            except json.JSONDecodeError:
                logger.warning("db.automation: unparseable %s jsonb, keeping raw", key)
    return d


def _affected_rows(status: str) -> int:
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def create_operation(
    creator_id: int,
    action: str,
    target: str | None = None,
    params: dict[str, Any] | None = None,
    idempotency_key: str = "",
    correlation_id: str | None = None,
    max_attempts: int = 3,
) -> dict[str, Any] | None:
    """Persist a new operation, or return the existing one for a known key.

    Non-empty key: SELECT first (converges duplicates); INSERT on miss;
    on a lost insert race the partial unique index wins and we re-SELECT.
    Empty key: straight INSERT (duplicates allowed). Returns the row, or
    None when the insert produced no row.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        key = idempotency_key or ""
        if key:
            existing = await conn.fetchrow(
                "SELECT * FROM automation_operations "
                "WHERE creator_id = $1 AND idempotency_key = $2 LIMIT 1",
                creator_id,
                key,
            )
            if existing is not None:
                return _decode(existing)
        row = await conn.fetchrow(
            """INSERT INTO automation_operations
                (creator_id, action, target, params, idempotency_key,
                 correlation_id, max_attempts, status)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, 'pending')
            ON CONFLICT (creator_id, idempotency_key)
                WHERE idempotency_key <> '' DO NOTHING
            RETURNING *""",
            creator_id,
            action,
            target,
            json.dumps(params or {}),
            key,
            correlation_id or "",
            int(max_attempts),
        )
        if row is not None:
            return _decode(row)
        if not key:
            return None
        existing = await conn.fetchrow(
            "SELECT * FROM automation_operations "
            "WHERE creator_id = $1 AND idempotency_key = $2 LIMIT 1",
            creator_id,
            key,
        )
        return _decode(existing) if existing is not None else None


async def get_operation(operation_id: int, creator_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM automation_operations WHERE id = $1 AND creator_id = $2 LIMIT 1",
            operation_id,
            creator_id,
        )
        return _decode(row) if row else None


async def get_operation_by_idempotency_key(
    creator_id: int, idempotency_key: str
) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM automation_operations "
            "WHERE creator_id = $1 AND idempotency_key = $2 LIMIT 1",
            creator_id,
            idempotency_key,
        )
        return _decode(row) if row else None


async def claim_operation(operation_id: int, creator_id: int) -> dict[str, Any] | None:
    """Claim a pending/retrying operation into running (the claim race).

    Conditional UPDATE … RETURNING: exactly one claimant gets the row;
    losers (and terminal rows) get None. attempt_count increments in SQL.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE automation_operations
            SET status = 'running',
                attempt_count = attempt_count + 1,
                started_at = NOW(),
                updated_at = NOW()
            WHERE id = $1 AND creator_id = $2
              AND status IN ('pending', 'retrying')
            RETURNING *""",
            operation_id,
            creator_id,
        )
        return _decode(row) if row else None


async def _set_state(
    operation_id: int,
    creator_id: int,
    target: str,
    *,
    error_class: str | None = None,
    error_message: str | None = None,
    provider_result: dict[str, Any] | None = None,
) -> bool:
    """Conditional terminal/progress transition. False when not applicable."""
    sets = ["status = $3", "updated_at = NOW()"]
    params: list[Any] = [operation_id, creator_id, target]
    if error_class is not None:
        params.append(error_class)
        sets.append(f"error_class = ${len(params)}")
    if error_message is not None:
        params.append(error_message)
        sets.append(f"error_message = ${len(params)}")
    if provider_result is not None:
        params.append(json.dumps(provider_result))
        sets.append(f"provider_result = ${len(params)}::jsonb")
    if target in ("succeeded", "failed"):
        sets.append("finished_at = NOW()")
    if target == "cancelled":
        sets.append("cancelled_at = NOW()")
    sources = _SOURCES[target]
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            f"UPDATE automation_operations SET {', '.join(sets)} "
            "WHERE id = $1 AND creator_id = $2 "
            f"AND status IN ({', '.join(repr(s) for s in sources)})",
            *params,
        )
        return _affected_rows(status) > 0


async def mark_succeeded(
    operation_id: int,
    creator_id: int,
    provider_result: dict[str, Any] | None = None,
) -> bool:
    """running → succeeded, persisting the provider result + finished_at."""
    return await _set_state(
        operation_id, creator_id, "succeeded", provider_result=provider_result or {}
    )


async def mark_failed(
    operation_id: int,
    creator_id: int,
    error_class: str,
    error_message: str,
) -> bool:
    """running → failed, persisting error classification + finished_at."""
    return await _set_state(
        operation_id,
        creator_id,
        "failed",
        error_class=error_class,
        error_message=error_message,
    )


async def mark_retrying(
    operation_id: int,
    creator_id: int,
    error_class: str,
    error_message: str,
) -> bool:
    """running → retrying. Retry scheduling reads next_attempt_at (NULL = due)."""
    return await _set_state(
        operation_id,
        creator_id,
        "retrying",
        error_class=error_class,
        error_message=error_message,
    )


async def mark_unknown(
    operation_id: int,
    creator_id: int,
    error_class: str = "unknown",
    error_message: str = "",
) -> bool:
    """running → unknown (ambiguous outcome; terminal, never auto-retried)."""
    return await _set_state(
        operation_id,
        creator_id,
        "unknown",
        error_class=error_class,
        error_message=error_message,
    )


async def mark_cancelled(operation_id: int, creator_id: int) -> bool:
    """Force-cancel from pending/retrying/running (kill-switch path)."""
    return await _set_state(operation_id, creator_id, "cancelled")


async def transition_operation(
    operation_id: int,
    creator_id: int,
    target_status: str,
    error_class: str | None = None,
    error_message: str | None = None,
    provider_result: dict[str, Any] | None = None,
) -> bool:
    """Generic guarded transition. Unknown targets return False (no I/O)."""
    if target_status not in _SOURCES:
        return False
    return await _set_state(
        operation_id,
        creator_id,
        target_status,
        error_class=error_class,
        error_message=error_message,
        provider_result=provider_result,
    )


async def cancel_operation(operation_id: int, creator_id: int) -> bool:
    """Operator cancel: pending/retrying → cancelled (running excluded)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            """UPDATE automation_operations
            SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
            WHERE id = $1 AND creator_id = $2
              AND status IN ('pending', 'retrying')""",
            operation_id,
            creator_id,
        )
        return _affected_rows(status) > 0


async def list_operations(
    creator_id: int,
    status: str | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status is not None and action is not None:
            rows = await conn.fetch(
                "SELECT * FROM automation_operations "
                "WHERE creator_id = $1 AND status = $2 AND action = $3 "
                "ORDER BY created_at DESC LIMIT $4 OFFSET $5",
                creator_id,
                status,
                action,
                limit,
                offset,
            )
        elif status is not None:
            rows = await conn.fetch(
                "SELECT * FROM automation_operations "
                "WHERE creator_id = $1 AND status = $2 "
                "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                creator_id,
                status,
                limit,
                offset,
            )
        elif action is not None:
            rows = await conn.fetch(
                "SELECT * FROM automation_operations "
                "WHERE creator_id = $1 AND action = $2 "
                "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                creator_id,
                action,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM automation_operations "
                "WHERE creator_id = $1 "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                creator_id,
                limit,
                offset,
            )
        return [_decode(r) for r in rows]


__all__ = [
    "cancel_operation",
    "claim_operation",
    "create_operation",
    "get_operation",
    "get_operation_by_idempotency_key",
    "get_pool",
    "list_operations",
    "mark_cancelled",
    "mark_failed",
    "mark_retrying",
    "mark_succeeded",
    "mark_unknown",
    "transition_operation",
]
