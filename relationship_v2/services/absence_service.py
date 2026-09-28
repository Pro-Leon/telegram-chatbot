"""Absence and return intelligence (Stage C6). Gaps in, return plans out.

Caller: FUTURE turn pipeline on FanReturned / inbound after inactivity
(Stage D orchestration: load relationship -> absence -> history -> loops
-> state -> generate). No I/O here — pure classification + planning.

Rules (Phased_Plan Phase 15 + 04_STATE_MACHINE + sunny_upgrade_v2 §21):
- The relationship survives absence; a return is never a cold start and
  never a new identity (derive_lifecycle owns dormancy; this module plans
  the return).
- 3d+: recall prior relationship, continue an unresolved thread.
- 30d+: retain durable identity, acknowledge the return naturally.
- Guidance is internal direction for the planner/LLM, never hardcoded
  fan-facing phrases (ESCALATION_ENGINE rule 1).
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.absence_service")

BRIEF_ABSENCE_DAYS = 3
LONG_ABSENCE_DAYS = 30


class AbsenceBand(str, Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    LONG_ABSENCE = "long_absence"


class ReturnPlan(BaseModel):
    band: AbsenceBand
    absence_days: int | None = None
    retain_identity: bool = True
    continue_thread: bool = False
    acknowledge_return: bool = False
    guidance: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}


def classify_absence(days_since_last: int | None) -> AbsenceBand:
    """Band an inactivity gap. Unknown recency treats the fan as current."""
    if days_since_last is None:
        return AbsenceBand.ACTIVE
    if days_since_last < 0:
        raise ValueError("days_since_last must be >= 0")
    if days_since_last >= LONG_ABSENCE_DAYS:
        return AbsenceBand.LONG_ABSENCE
    if days_since_last >= BRIEF_ABSENCE_DAYS:
        return AbsenceBand.LAPSED
    return AbsenceBand.ACTIVE


def plan_return(
    days_since_last: int | None,
    has_open_threads: bool,
    provenance: str,
) -> ReturnPlan:
    """Plan a return from absence. Identity always retained; never cold-start."""
    if not provenance:
        raise ValueError("provenance required")
    band = classify_absence(days_since_last)
    if band == AbsenceBand.LONG_ABSENCE:
        return ReturnPlan(
            band=band,
            absence_days=days_since_last,
            retain_identity=True,
            continue_thread=has_open_threads,
            acknowledge_return=True,
            guidance=(
                "long-standing relationship returned; retain durable identity and "
                "important memories; acknowledge the return naturally; "
                + (
                    "continue an unresolved thread where it fits"
                    if has_open_threads
                    else "re-establish the current thread"
                )
            ),
            provenance=provenance,
        )
    if band == AbsenceBand.LAPSED:
        return ReturnPlan(
            band=band,
            absence_days=days_since_last,
            retain_identity=True,
            continue_thread=has_open_threads,
            acknowledge_return=False,
            guidance=(
                "recall prior relationship and momentum; "
                + (
                    "continue the unresolved thread if appropriate"
                    if has_open_threads
                    else "resume where the conversation left off"
                )
            ),
            provenance=provenance,
        )
    return ReturnPlan(
        band=band,
        absence_days=days_since_last,
        retain_identity=True,
        continue_thread=True,
        acknowledge_return=False,
        guidance="active conversation; continue the current thread",
        provenance=provenance,
    )
