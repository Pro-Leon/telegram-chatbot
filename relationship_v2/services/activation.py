"""Controlled activation policy (Phase 11).

Caller: FUTURE deployment operator + live wiring (reads server-side
SUNNY_V2_ENABLED, never request payloads). This module is pure policy:
no config reads, no worker edits, no router changes. Enforcement wiring
happens at deployment under the same gates.

Rules (MIGRATION_AND_CUTOVER.md):
- Explicit: enabled flag + allowlist, both server-side.
- Scoped: empty allowlist admits nothing; unknown creators fail closed.
- Reversible: deactivate() returns to safe state; V2 data intact (no deletes).
- Audited: every decision carries actor, scope, reasons, timestamp.
- Blocked by: V1 enabled (dual authority), shadow not passed, commerce
  regressed, dependencies unhealthy.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from relationship_v2.domain.activation import (
    ActivationDecision,
    ActivationPreconditions,
    ActivationScope,
)

logger = logging.getLogger("sunny.v2.activation")


def is_active_for_scope(scope: ActivationScope, creator_id: int) -> bool:
    """Flag + allowlist evaluation. Pure; caller supplies server-side flag."""
    if not scope.enabled:
        return False
    if creator_id <= 0:
        return False
    return creator_id in scope.allowed_creator_ids


def check_preconditions(pre: ActivationPreconditions) -> tuple[bool, list[str]]:
    """Return (ok, blocking_reasons). Any failure blocks activation."""
    blockers: list[str] = []
    if not pre.v1_disabled:
        blockers.append("v1_still_enabled_dual_authority")
    if not pre.shadow_passed:
        blockers.append("shadow_not_passed")
    if not pre.commerce_verified:
        blockers.append("commerce_regressed")
    if not pre.dependencies_healthy:
        blockers.append("dependencies_unhealthy")
    return (not blockers, blockers)


def dependencies_ok(db_ok: bool, redis_ok: bool) -> bool:
    """Health gate: both durable + coordination layers required."""
    return bool(db_ok and redis_ok)


def activate(
    scope: ActivationScope,
    pre: ActivationPreconditions,
    actor: str,
    now: datetime | None = None,
) -> ActivationDecision:
    """Authorize activation scope. Raises on blocked preconditions."""
    if not actor:
        raise ValueError("actor required (audited)")
    ok, blockers = check_preconditions(pre)
    if not ok:
        raise ValueError(f"activation blocked: {','.join(blockers)}")
    if not scope.enabled or not scope.allowed_creator_ids:
        raise ValueError("activation requires enabled flag + non-empty allowlist")
    return ActivationDecision(
        action="activate",
        actor=actor,
        reasons=["preconditions_pass"],
        decided_at=now or datetime.now(UTC),
    )


def deactivate(actor: str, now: datetime | None = None) -> ActivationDecision:
    """Return to safe state. V2 rows preserved for diagnosis (no deletes)."""
    if not actor:
        raise ValueError("actor required (audited)")
    return ActivationDecision(
        action="deactivate",
        actor=actor,
        reasons=["rollback_to_safe_state_data_preserved"],
        decided_at=now or datetime.now(UTC),
    )
