"""Open-loop lifecycle (Stage C1). Guarded transitions + due derivation.

Caller: FUTURE turn pipeline / recall (Stage C). Owner of loop status
transitions; rows owned by `persistence.repository`. Pure here — the repo
functions enforce scope + terminal guards in SQL as well.

Signals: due (expected time passed), reference (followed up), resolve
(outcome known), expire (stale), dismiss (fan closed it / not worthy).
Terminal RESOLVED/EXPIRED/DISMISSED never reopen; a new loop is opened
instead so history stays append-only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from relationship_v2.domain.open_loop import (
    OpenLoopStatus,
    is_valid_open_loop_transition,
)

logger = logging.getLogger("sunny.v2.open_loops")


class LoopOutcome(str, Enum):
    ADVANCED = "advanced"
    REJECTED = "rejected"


class LoopResult(BaseModel):
    outcome: LoopOutcome
    status: OpenLoopStatus
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


_SIGNAL_TARGETS: dict[str, OpenLoopStatus] = {
    "due": OpenLoopStatus.DUE,
    "reference": OpenLoopStatus.REFERENCED,
    "resolve": OpenLoopStatus.RESOLVED,
    "expire": OpenLoopStatus.EXPIRED,
    "dismiss": OpenLoopStatus.DISMISSED,
}


def transition_loop(current: OpenLoopStatus, signal: str) -> LoopResult:
    """Guarded status transition. Unknown signals and terminal exits reject."""
    if signal not in _SIGNAL_TARGETS:
        return LoopResult(outcome=LoopOutcome.REJECTED, status=current, reason="unknown_signal")
    nxt = _SIGNAL_TARGETS[signal]
    if is_valid_open_loop_transition(current, nxt):
        return LoopResult(outcome=LoopOutcome.ADVANCED, status=nxt, reason="guard_pass")
    return LoopResult(
        outcome=LoopOutcome.REJECTED, status=current, reason="terminal_or_invalid"
    )


def is_due(status: OpenLoopStatus, expected_at: datetime | None, now: datetime | None = None) -> bool:
    """Time-derived due: actionable loop whose expected time has passed."""
    if status not in (OpenLoopStatus.OPEN, OpenLoopStatus.REFERENCED):
        return False
    if expected_at is None:
        return False
    return (now or datetime.now(UTC)) >= expected_at
