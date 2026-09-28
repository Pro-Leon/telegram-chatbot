"""Pattern learning (Stage C2). Evidence counts in, pattern verdicts out.

Caller: FUTURE turn pipeline / recall (Stage C). Consumes accumulated
`v2_engagement_signals` evidence (the single pattern store — no duplicate
table); this module is the pure decision layer. No I/O here.

Rules (Phased_Plan Phases 9-10 + sunny_upgrade_v2 §14-15 + 05_MEMORY_SPEC):
- 1 observation -> weak signal, never a rule.
- 2 observations -> emerging.
- 3+ with 2:1 dominance -> learned (positive or negative leaning).
- Contradictory evidence -> contested: hold with reduced confidence.
- Negative evidence decays (explicit half-life); no single-event permanent
  dislike. Scope stays creator+fan (09_SECURITY_AND_PRIVACY).
"""

from __future__ import annotations

import logging
import math
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.pattern_learning")

LEARN_THRESHOLD = 3
DOMINANCE_RATIO = 2.0
DECAY_HALF_LIFE_DAYS = 30.0


class PatternStatus(str, Enum):
    WEAK = "weak"
    EMERGING = "emerging"
    LEARNED = "learned"
    CONTESTED = "contested"


class PatternLeaning(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class PatternAssessment(BaseModel):
    topic: str = Field(min_length=1, max_length=128)
    behavior: str = Field(min_length=1, max_length=64)
    positive: int = Field(ge=0)
    negative: int = Field(ge=0)
    total: int = Field(ge=0)
    status: PatternStatus
    leaning: PatternLeaning
    adjusted_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


def decayed_evidence(
    count: int, age_days: float, half_life_days: float = DECAY_HALF_LIFE_DAYS
) -> float:
    """Exponential decay on stale evidence. Rows remain; weight fades."""
    if count < 0 or age_days < 0:
        raise ValueError("count/age_days must be non-negative")
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    return count * math.pow(0.5, age_days / half_life_days)


def assess_pattern(
    topic: str,
    behavior: str,
    positive: int,
    negative: int,
    base_confidence: float,
) -> PatternAssessment:
    """Decide pattern strength from accumulated evidence. Pure."""
    if not topic or not behavior:
        raise ValueError("topic/behavior required")
    if positive < 0 or negative < 0:
        raise ValueError("evidence counts must be non-negative")
    if not 0.0 <= base_confidence <= 1.0:
        raise ValueError("base_confidence must be in [0, 1]")
    total = positive + negative
    net = positive - negative
    agreement = abs(net) / total if total else 0.0
    adjusted = round(base_confidence * (0.5 + 0.5 * agreement), 4)
    if total == 0:
        return PatternAssessment(
            topic=topic, behavior=behavior, positive=positive, negative=negative,
            total=total, status=PatternStatus.WEAK, leaning=PatternLeaning.NEUTRAL,
            adjusted_confidence=adjusted, reason="no evidence yet",
        )
    if total < LEARN_THRESHOLD:
        leaning = (
            PatternLeaning.POSITIVE if net > 0
            else PatternLeaning.NEGATIVE if net < 0
            else PatternLeaning.NEUTRAL
        )
        label = "single observation never a rule" if total == 1 else "accumulating"
        return PatternAssessment(
            topic=topic, behavior=behavior, positive=positive, negative=negative,
            total=total, status=PatternStatus.WEAK if total == 1 else PatternStatus.EMERGING,
            leaning=leaning, adjusted_confidence=adjusted, reason=label,
        )
    if positive >= DOMINANCE_RATIO * negative and net > 0:
        return PatternAssessment(
            topic=topic, behavior=behavior, positive=positive, negative=negative,
            total=total, status=PatternStatus.LEARNED, leaning=PatternLeaning.POSITIVE,
            adjusted_confidence=adjusted, reason="dominant positive evidence",
        )
    if negative >= DOMINANCE_RATIO * positive and net < 0:
        return PatternAssessment(
            topic=topic, behavior=behavior, positive=positive, negative=negative,
            total=total, status=PatternStatus.LEARNED, leaning=PatternLeaning.NEGATIVE,
            adjusted_confidence=adjusted, reason="dominant negative evidence",
        )
    return PatternAssessment(
        topic=topic, behavior=behavior, positive=positive, negative=negative,
        total=total, status=PatternStatus.CONTESTED, leaning=PatternLeaning.NEUTRAL,
        adjusted_confidence=adjusted, reason="contradictory evidence; hold with reduced confidence",
    )
