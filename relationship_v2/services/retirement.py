"""V1 retirement readiness (Phase 12).

Caller: FUTURE removal approval workflow (separate approval required).
This module evaluates readiness and produces the ordered removal plan.
It deletes nothing: removal executes only after verified stability AND
explicit approval. V1 stays quarantined (DISABLED) until then.

Order (10_LEGACY_ISOLATION.md + NO PREMATURE LEGACY DELETION rule):
  legacy writes off -> verify -> legacy reads off -> verify ->
  quarantine period -> remove -> verify commerce + rollback intact.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from relationship_v2.domain.retirement import RetirementDecision, RetirementReadiness

logger = logging.getLogger("sunny.v2.retirement")

REMOVAL_STEPS: tuple[str, ...] = (
    "disable_legacy_writes",
    "verify_no_legacy_writes",
    "disable_legacy_reads",
    "verify_no_legacy_reads",
    "quarantine_period",
    "remove_legacy_modules",
    "verify_commerce_intact",
    "verify_rollback_intact",
)


def check_readiness(state: RetirementReadiness) -> tuple[bool, list[str]]:
    """Return (ready, blockers). Approval is itself a required gate."""
    blockers: list[str] = []
    if not state.v2_stable:
        blockers.append("v2_not_stable")
    if not state.v2_primary:
        blockers.append("v2_not_primary")
    if not state.legacy_writes_disabled:
        blockers.append("legacy_writes_active")
    if not state.legacy_reads_disabled:
        blockers.append("legacy_reads_active")
    if not state.commerce_independent:
        blockers.append("commerce_not_independent")
    if not state.rollback_tested:
        blockers.append("rollback_untested")
    if not state.approval_granted:
        blockers.append("approval_missing")
    return (not blockers, blockers)


def plan_removal(
    state: RetirementReadiness, actor: str, now: datetime | None = None
) -> RetirementDecision:
    """Produce the removal plan or a blocked decision. Never deletes."""
    if not actor:
        raise ValueError("actor required (audited)")
    ready, blockers = check_readiness(state)
    if not ready:
        return RetirementDecision(
            action="blocked",
            actor=actor,
            blockers=blockers,
            decided_at=now or datetime.now(UTC),
        )
    return RetirementDecision(
        action="remove_per_plan",
        actor=actor,
        steps=list(REMOVAL_STEPS),
        decided_at=now or datetime.now(UTC),
    )
