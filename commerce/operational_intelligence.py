"""Operational Intelligence — Phase 26 deterministic layer.

Pure, bounded, creator/fan isolated, no LLM, no DB/Redis/network/DropFans calls.
Consumes data structures from existing modules, produces explainable recommendations
authorized via production_control.

Hierarchy preserved:
  SAFETY > CREATOR ISOLATION > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT FAN INTENT > COMMERCE AUTHORITY > PRODUCTION CONTROL > OPTIMIZATION > LLM LANGUAGE
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import enum

# Reuse existing uncertainty
from commerce.adaptive_optimization import beta_uncertainty, ExtendedEvidence

# ═══════════════════════════════════════════════════════════════════════════════
# Signals (§8)
# ═══════════════════════════════════════════════════════════════════════════════

class OperationalSignal(str, enum.Enum):
    HEALTHY = "HEALTHY"
    RELATIONSHIP_COMMERCE_MISMATCH = "RELATIONSHIP_COMMERCE_MISMATCH"
    STRATEGY_REGRESSION = "STRATEGY_REGRESSION"
    RISING_REJECTION = "RISING_REJECTION"
    FATIGUE = "FATIGUE"
    HANDOFF_SPIKE = "HANDOFF_SPIKE"
    SPAM_RISK = "SPAM_RISK"
    OPEN_LOOP_STAGNATION = "OPEN_LOOP_STAGNATION"
    CONVERSION_DECLINE = "CONVERSION_DECLINE"
    PRODUCT_FAMILY_DEGRADATION = "PRODUCT_FAMILY_DEGRADATION"
    RESPONSE_MODE_DEGRADATION = "RESPONSE_MODE_DEGRADATION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# ═══════════════════════════════════════════════════════════════════════════════
# Actions (§13)
# ═══════════════════════════════════════════════════════════════════════════════

class OperationalAction(str, enum.Enum):
    NO_ACTION = "NO_ACTION"
    OBSERVE = "OBSERVE"
    EXPLORE = "EXPLORE"
    EXPLOIT = "EXPLOIT"
    REDUCE_PRESSURE = "REDUCE_PRESSURE"
    SUPPRESS_STRATEGY = "SUPPRESS_STRATEGY"
    ROTATE_STRATEGY = "ROTATE_STRATEGY"
    ROTATE_TOPIC = "ROTATE_TOPIC"
    SUPPRESS_PRODUCT_FAMILY = "SUPPRESS_PRODUCT_FAMILY"
    PRIORITIZE_RELATIONSHIP = "PRIORITIZE_RELATIONSHIP"
    FOLLOW_UP_OPEN_LOOP = "FOLLOW_UP_OPEN_LOOP"
    SUPPRESS_REENGAGEMENT = "SUPPRESS_REENGAGEMENT"
    PAUSE_EXPERIMENT = "PAUSE_EXPERIMENT"
    ROLLBACK_EXPERIMENT = "ROLLBACK_EXPERIMENT"
    ROLLBACK_ROLLOUT = "ROLLBACK_ROLLOUT"
    HANDOFF = "HANDOFF"

# ═══════════════════════════════════════════════════════════════════════════════
# Priority (§10) deterministic 1-13
# ═══════════════════════════════════════════════════════════════════════════════

class OperationalPriority(int, enum.Enum):
    SAFETY = 1
    GLOBAL_PAUSE = 2
    CREATOR_PAUSE = 3
    HANDOFF = 4
    PRODUCTION_SUPPRESSION = 5
    REGRESSION = 6
    SPAM_FATIGUE = 7
    AFTERCARE_COOLDOWN = 8
    OPEN_LOOP = 9
    RELATIONSHIP = 10
    COMMERCE_OPTIMIZATION = 11
    EXPLORATION = 12
    NORMAL = 13

_PRIORITY_FOR_SIGNAL = {
    OperationalSignal.HANDOFF_SPIKE: OperationalPriority.HANDOFF,
    OperationalSignal.SPAM_RISK: OperationalPriority.SPAM_FATIGUE,
    OperationalSignal.FATIGUE: OperationalPriority.SPAM_FATIGUE,
    OperationalSignal.STRATEGY_REGRESSION: OperationalPriority.REGRESSION,
    OperationalSignal.CONVERSION_DECLINE: OperationalPriority.REGRESSION,
    OperationalSignal.PRODUCT_FAMILY_DEGRADATION: OperationalPriority.REGRESSION,
    OperationalSignal.RESPONSE_MODE_DEGRADATION: OperationalPriority.REGRESSION,
    OperationalSignal.RISING_REJECTION: OperationalPriority.SPAM_FATIGUE,
    OperationalSignal.OPEN_LOOP_STAGNATION: OperationalPriority.OPEN_LOOP,
    OperationalSignal.RELATIONSHIP_COMMERCE_MISMATCH: OperationalPriority.RELATIONSHIP,
    OperationalSignal.HEALTHY: OperationalPriority.NORMAL,
    OperationalSignal.INSUFFICIENT_DATA: OperationalPriority.NORMAL,
}

# ═══════════════════════════════════════════════════════════════════════════════
# Data structures (§7 recommendation)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OperationalDiagnosis:
    signal: str
    priority: int
    reason_code: str
    confidence: float
    evidence: dict[str, Any]
    scope: str  # creator | creator:fan | global
    creator_id: int | None = None
    user_id: int | None = None
    window: str = "24h"

@dataclass
class OperationalRecommendation:
    recommendation: str  # OperationalAction
    priority: int
    reason_code: str
    confidence: float
    evidence: dict[str, Any]
    scope: str
    allowed: bool
    blocking_reason: str | None = None
    source_metrics: dict[str, Any] = field(default_factory=dict)
    creator_id: int | None = None
    user_id: int | None = None
    generation_id: str | None = None
    trace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class OperationalDecision:
    diagnoses: list[OperationalDiagnosis]
    recommendations: list[OperationalRecommendation]
    production_state: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _confidence_for_sample(sample_size: int, positive_rate: float = 0.5) -> tuple[float, str]:
    if sample_size < 5:
        return 0.0, "INSUFFICIENT_DATA"
    # Use Beta uncertainty
    ev = ExtendedEvidence(attempt_count=sample_size, positive_count=int(positive_rate * sample_size))
    try:
        unc = beta_uncertainty(ev)
        conf = round(1 - unc, 3)
    except Exception:
        conf = 0.5
    if conf >= 0.75:
        level = "HIGH_CONFIDENCE"
    elif conf >= 0.5:
        level = "MEDIUM_CONFIDENCE"
    else:
        level = "LOW_CONFIDENCE"
    return conf, level

def _trace_compact(rec: OperationalRecommendation) -> str:
    # <500 chars, no content/secrets/PII
    parts = [
        f"signal={rec.reason_code}",
        f"action={rec.recommendation}",
        f"priority={rec.priority}",
        f"conf={rec.confidence:.2f}",
        f"allowed={str(rec.allowed).lower()}",
    ]
    if rec.evidence:
        for k in ("sample_size","current","baseline","delta","rate","rejection_rate","fatigue","handoff_rate","spam_rate"):
            if k in rec.evidence:
                parts.append(f"{k}={rec.evidence[k]}")
    trace = " ".join(parts)
    return trace[:480]

# ═══════════════════════════════════════════════════════════════════════════════
# Signal Detectors (pure, deterministic, bounded)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_relationship_commerce_mismatch(
    *,
    relationship_health: float,
    commercial_intent: float,
    fatigue: float = 0.0,
    creator_id: int | None = None,
    user_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    # High relationship + low commerce → NO_OFFER
    if fatigue >= 0.30:
        return None  # fatigue dominates
    if relationship_health >= 0.70 and commercial_intent <= 0.35:
        conf, _ = _confidence_for_sample(10, 0.5)  # synthetic, but mismatch is deterministic threshold, not sample
        return OperationalDiagnosis(
            signal=OperationalSignal.RELATIONSHIP_COMMERCE_MISMATCH.value,
            priority=_PRIORITY_FOR_SIGNAL[OperationalSignal.RELATIONSHIP_COMMERCE_MISMATCH].value,
            reason_code="RELATIONSHIP_HEALTH_HIGH_COMMERCE_LOW",
            confidence=0.85,
            evidence={"relationship_health": round(relationship_health,3), "commercial_intent": round(commercial_intent,3), "fatigue": round(fatigue,3)},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, user_id=user_id, window=window,
        )
    return None

def detect_strategy_regression(
    *,
    strategy: str,
    baseline_rate: float,
    current_rate: float,
    sample_size: int,
    creator_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return OperationalDiagnosis(
            signal=OperationalSignal.INSUFFICIENT_DATA.value,
            priority=OperationalPriority.NORMAL.value,
            reason_code="INSUFFICIENT_SAMPLE",
            confidence=0.0,
            evidence={"strategy": strategy, "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    # Use existing regression thresholds 0.20 decline
    if baseline_rate > 0 and current_rate < baseline_rate * 0.80:
        conf, _ = _confidence_for_sample(sample_size, current_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.STRATEGY_REGRESSION.value,
            priority=OperationalPriority.REGRESSION.value,
            reason_code="STRATEGY_REGRESSION_CONFIRMED",
            confidence=conf,
            evidence={"strategy": strategy, "baseline": round(baseline_rate,3), "current": round(current_rate,3), "delta": round(current_rate-baseline_rate,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

def detect_rising_rejection(
    *,
    rejection_rate: float,
    baseline: float | None = None,
    sample_size: int = 0,
    window: str = "24h",
    creator_id: int | None = None,
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return None
    thresh = 0.25
    if baseline is not None and baseline > 0:
        # Use regression threshold 0.25 increase
        if rejection_rate > baseline * 1.25 and rejection_rate > thresh:
            conf, _ = _confidence_for_sample(sample_size, rejection_rate)
            return OperationalDiagnosis(
                signal=OperationalSignal.RISING_REJECTION.value,
                priority=OperationalPriority.SPAM_FATIGUE.value,
                reason_code="REJECTION_RATE_HIGH",
                confidence=conf,
                evidence={"rejection_rate": round(rejection_rate,3), "baseline": round(baseline,3), "sample_size": sample_size, "threshold": thresh},
                scope=f"creator:{creator_id}" if creator_id else "global",
                creator_id=creator_id, window=window,
            )
    else:
        if rejection_rate > 0.34:
            conf, _ = _confidence_for_sample(sample_size, rejection_rate)
            return OperationalDiagnosis(
                signal=OperationalSignal.RISING_REJECTION.value,
                priority=OperationalPriority.SPAM_FATIGUE.value,
                reason_code="REJECTION_RATE_HIGH",
                confidence=conf,
                evidence={"rejection_rate": round(rejection_rate,3), "sample_size": sample_size},
                scope=f"creator:{creator_id}" if creator_id else "global",
                creator_id=creator_id, window=window,
            )
    return None

def detect_fatigue(
    *,
    fatigue: float,
    strategy: str | None = None,
    topic: str | None = None,
    window: str = "24h",
    creator_id: int | None = None,
    user_id: int | None = None,
) -> OperationalDiagnosis | None:
    if fatigue >= 0.30:
        return OperationalDiagnosis(
            signal=OperationalSignal.FATIGUE.value,
            priority=OperationalPriority.SPAM_FATIGUE.value,
            reason_code="FATIGUE_HIGH",
            confidence=0.82,
            evidence={"fatigue": round(fatigue,3), "strategy": strategy or "unknown", "topic": topic or "unknown"},
            scope=f"creator:{creator_id}:fan:{user_id}" if creator_id and user_id else (f"creator:{creator_id}" if creator_id else "global"),
            creator_id=creator_id, user_id=user_id, window=window,
        )
    if fatigue >= 0.15:
        return OperationalDiagnosis(
            signal=OperationalSignal.FATIGUE.value,
            priority=OperationalPriority.SPAM_FATIGUE.value,
            reason_code="FATIGUE_RISING",
            confidence=0.65,
            evidence={"fatigue": round(fatigue,3)},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, user_id=user_id, window=window,
        )
    return None

def detect_handoff_spike(
    *,
    handoff_rate: float,
    sample_size: int = 0,
    window: str = "24h",
    creator_id: int | None = None,
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return None
    if handoff_rate > 0.10:
        conf, _ = _confidence_for_sample(sample_size, handoff_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.HANDOFF_SPIKE.value,
            priority=OperationalPriority.HANDOFF.value,
            reason_code="HANDOFF_RATE_HIGH",
            confidence=conf,
            evidence={"handoff_rate": round(handoff_rate,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

def detect_spam_risk(
    *,
    spam_rate: float,
    reengagement_rate: float | None = None,
    pressure_suppressed_rate: float | None = None,
    sample_size: int = 0,
    window: str = "24h",
    creator_id: int | None = None,
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return None
    if spam_rate > 0.10 or (pressure_suppressed_rate is not None and pressure_suppressed_rate > 0.10):
        conf, _ = _confidence_for_sample(sample_size, spam_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.SPAM_RISK.value,
            priority=OperationalPriority.SPAM_FATIGUE.value,
            reason_code="SPAM_RATE_HIGH",
            confidence=conf,
            evidence={"spam_rate": round(spam_rate,3), "pressure_suppressed_rate": round(pressure_suppressed_rate or 0,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

def detect_open_loop_stagnation(
    *,
    open_loops: list[dict[str, Any]],
    now: datetime | None = None,
    creator_id: int | None = None,
    user_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    now = now or datetime.now(timezone.utc)
    for loop in open_loops:
        if loop.get("status") in ("RESOLVED","EXPIRED","CANCELLED"):
            continue
        importance = loop.get("importance", 0.5)
        if importance < 0.7:
            continue
        created_raw = loop.get("first_seen") or loop.get("created_at") or loop.get("timestamp")
        if not created_raw:
            continue
        try:
            created = datetime.fromisoformat(created_raw.replace("Z","+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            hours = (now - created).total_seconds()/3600
            # Expected lifecycle 7 days for open_loop, if >72h without resolve → stagnation
            if hours > 72:
                return OperationalDiagnosis(
                    signal=OperationalSignal.OPEN_LOOP_STAGNATION.value,
                    priority=OperationalPriority.OPEN_LOOP.value,
                    reason_code="OPEN_LOOP_STAGNATION",
                    confidence=0.78,
                    evidence={"open_loop_subject": loop.get("subject","unknown")[:30], "importance": importance, "hours_stagnant": round(hours,1)},
                    scope=f"creator:{creator_id}:fan:{user_id}" if creator_id and user_id else "global",
                    creator_id=creator_id, user_id=user_id, window=window,
                )
        except Exception:
            continue
    return None

def detect_conversion_decline(
    *,
    baseline_rate: float,
    current_rate: float,
    sample_size: int,
    creator_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return OperationalDiagnosis(
            signal=OperationalSignal.INSUFFICIENT_DATA.value,
            priority=OperationalPriority.NORMAL.value,
            reason_code="INSUFFICIENT_SAMPLE",
            confidence=0.0,
            evidence={"sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    if baseline_rate > 0 and current_rate < baseline_rate * 0.80:
        conf, _ = _confidence_for_sample(sample_size, current_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.CONVERSION_DECLINE.value,
            priority=OperationalPriority.REGRESSION.value,
            reason_code="CONVERSION_DECLINE",
            confidence=conf,
            evidence={"baseline": round(baseline_rate,3), "current": round(current_rate,3), "delta": round(current_rate-baseline_rate,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

def detect_product_family_degradation(
    *,
    family: str,
    baseline_rate: float,
    current_rate: float,
    sample_size: int,
    creator_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return None
    if baseline_rate > 0 and current_rate < baseline_rate * 0.70:
        conf, _ = _confidence_for_sample(sample_size, current_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.PRODUCT_FAMILY_DEGRADATION.value,
            priority=OperationalPriority.REGRESSION.value,
            reason_code="PRODUCT_FAMILY_DEGRADATION",
            confidence=conf,
            evidence={"product_family": family, "baseline": round(baseline_rate,3), "current": round(current_rate,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

def detect_response_mode_degradation(
    *,
    mode: str,
    baseline_rate: float,
    current_rate: float,
    sample_size: int,
    creator_id: int | None = None,
    window: str = "24h",
) -> OperationalDiagnosis | None:
    if sample_size < 5:
        return None
    # If mode's purchase/positive rate materially worse
    if baseline_rate > 0 and current_rate < baseline_rate * 0.70:
        conf, _ = _confidence_for_sample(sample_size, current_rate)
        return OperationalDiagnosis(
            signal=OperationalSignal.RESPONSE_MODE_DEGRADATION.value,
            priority=OperationalPriority.REGRESSION.value,
            reason_code="RESPONSE_MODE_DEGRADATION",
            confidence=conf,
            evidence={"response_mode": mode, "baseline": round(baseline_rate,3), "current": round(current_rate,3), "sample_size": sample_size},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, window=window,
        )
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# Recommendation Mapping (§9, §13)
# ═══════════════════════════════════════════════════════════════════════════════

_SIGNAL_TO_ACTION = {
    OperationalSignal.RELATIONSHIP_COMMERCE_MISMATCH: OperationalAction.PRIORITIZE_RELATIONSHIP,
    OperationalSignal.STRATEGY_REGRESSION: OperationalAction.SUPPRESS_STRATEGY,
    OperationalSignal.RISING_REJECTION: OperationalAction.REDUCE_PRESSURE,
    OperationalSignal.FATIGUE: OperationalAction.ROTATE_STRATEGY,
    OperationalSignal.HANDOFF_SPIKE: OperationalAction.HANDOFF,
    OperationalSignal.SPAM_RISK: OperationalAction.SUPPRESS_REENGAGEMENT,
    OperationalSignal.OPEN_LOOP_STAGNATION: OperationalAction.FOLLOW_UP_OPEN_LOOP,
    OperationalSignal.CONVERSION_DECLINE: OperationalAction.EXPLORE,
    OperationalSignal.PRODUCT_FAMILY_DEGRADATION: OperationalAction.SUPPRESS_PRODUCT_FAMILY,
    OperationalSignal.RESPONSE_MODE_DEGRADATION: OperationalAction.PAUSE_EXPERIMENT,  # safe wording experiment
    OperationalSignal.HEALTHY: OperationalAction.NO_ACTION,
    OperationalSignal.INSUFFICIENT_DATA: OperationalAction.OBSERVE,
}

def recommendation_for_diagnosis(
    diagnosis: OperationalDiagnosis,
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
    generation_id: str | None = None,
) -> OperationalRecommendation:
    action = _SIGNAL_TO_ACTION.get(OperationalSignal(diagnosis.signal), OperationalAction.OBSERVE)
    # Special cases
    if diagnosis.signal == OperationalSignal.FATIGUE.value:
        # Prefer rotate topic if topic present
        if diagnosis.evidence.get("topic") and diagnosis.evidence.get("topic") != "unknown":
            action = OperationalAction.ROTATE_TOPIC
        else:
            action = OperationalAction.ROTATE_STRATEGY
    elif diagnosis.signal == OperationalSignal.RISING_REJECTION.value:
        action = OperationalAction.REDUCE_PRESSURE
    elif diagnosis.signal == OperationalSignal.STRATEGY_REGRESSION.value:
        # Could be rollback if confidence high and sample large
        if diagnosis.confidence > 0.75 and diagnosis.evidence.get("sample_size",0) >= 20:
            action = OperationalAction.ROLLBACK_EXPERIMENT
        else:
            action = OperationalAction.SUPPRESS_STRATEGY
    elif diagnosis.signal == OperationalSignal.CONVERSION_DECLINE.value:
        action = OperationalAction.EXPLORE
    elif diagnosis.signal == OperationalSignal.PRODUCT_FAMILY_DEGRADATION.value:
        action = OperationalAction.SUPPRESS_PRODUCT_FAMILY
    elif diagnosis.signal == OperationalSignal.HEALTHY.value:
        action = OperationalAction.NO_ACTION

    # Determine allowed via production control (fail-closed)
    allowed, blocking_reason = _is_recommendation_allowed(diagnosis, creator_id=creator_id)

    rec = OperationalRecommendation(
        recommendation=action.value,
        priority=diagnosis.priority,
        reason_code=diagnosis.reason_code,
        confidence=diagnosis.confidence,
        evidence=dict(diagnosis.evidence),
        scope=diagnosis.scope,
        allowed=allowed,
        blocking_reason=blocking_reason if not allowed else None,
        source_metrics=dict(diagnosis.evidence),
        creator_id=creator_id or diagnosis.creator_id,
        user_id=user_id or diagnosis.user_id,
        generation_id=generation_id,
    )
    rec.trace = _trace_compact(rec)
    return rec

def _is_recommendation_allowed(diagnosis: OperationalDiagnosis, *, creator_id: int | None) -> tuple[bool, str | None]:
    # Use existing production_control + revenue_intelligence optimization_allowed
    try:
        from commerce.production_control import autonomous_allowed, is_global_paused, derive_production_state, evaluate_production_health, MetricWindow
        from commerce.revenue_intelligence import optimization_allowed as _opt_allowed

        # Global pause blocks everything
        if is_global_paused():
            return False, "global_pause"
        # Creator pause
        cid = creator_id or diagnosis.creator_id
        if cid is not None:
            from commerce.production_control import is_creator_paused
            if is_creator_paused(cid):
                return False, "creator_pause"
        # Check autonomous_allowed for strategy/experiment in evidence
        strat = diagnosis.evidence.get("strategy")
        if strat and strat != "unknown":
            allowed, reason = autonomous_allowed(creator_id=cid, strategy=strat)
            if not allowed:
                return False, reason
        # Check experiment pause if present
        exp = diagnosis.evidence.get("experiment_id") or diagnosis.evidence.get("experiment")
        if exp:
            allowed, reason = autonomous_allowed(creator_id=cid, experiment_id=exp)
            if not allowed:
                return False, reason
        # Check optimization_allowed (covers production_state)
        allowed2, reason2 = _opt_allowed(creator_id=cid, strategy=strat if strat and strat!="unknown" else None)
        if not allowed2:
            return False, reason2
        # Check production state suppressed/handoff/rollback
        try:
            health = evaluate_production_health(creator_id=cid, window=MetricWindow.H24)
            state = derive_production_state(is_paused=is_global_paused(), is_rollback=(health.production_state=="rollback"))
            if health.production_state in ("suppressed","handoff","rollback"):
                # Still allow observe, but not aggressive actions
                if _SIGNAL_TO_ACTION.get(OperationalSignal(diagnosis.signal)) not in (OperationalAction.OBSERVE, OperationalAction.NO_ACTION):
                    return False, f"production_state_{health.production_state}"
        except Exception:
            pass
    except Exception:
        # Fail closed if production control unavailable
        return False, "production_control_unavailable"
    return True, None

# ═══════════════════════════════════════════════════════════════════════════════
# Analyze Operational State (aggregate)
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_operational_state(
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
    relationship_health: float | None = None,
    commercial_intent: float | None = None,
    fatigue: float | None = None,
    rejection_rate: float | None = None,
    rejection_baseline: float | None = None,
    handoff_rate: float | None = None,
    spam_rate: float | None = None,
    pressure_suppressed_rate: float | None = None,
    open_loops: list[dict[str, Any]] | None = None,
    baseline_rate: float | None = None,
    current_rate: float | None = None,
    sample_size: int | None = None,
    product_family: str | None = None,
    response_mode: str | None = None,
    strategy: str | None = None,
    window: str = "24h",
) -> list[OperationalDiagnosis]:
    diagnoses: list[OperationalDiagnosis] = []
    # Healthy check first
    is_healthy = True

    if relationship_health is not None and commercial_intent is not None:
        d = detect_relationship_commerce_mismatch(relationship_health=relationship_health, commercial_intent=commercial_intent, fatigue=fatigue or 0.0, creator_id=creator_id, user_id=user_id, window=window)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if fatigue is not None:
        d = detect_fatigue(fatigue=fatigue, strategy=strategy, creator_id=creator_id, user_id=user_id, window=window)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if rejection_rate is not None:
        d = detect_rising_rejection(rejection_rate=rejection_rate, baseline=rejection_baseline, sample_size=sample_size or 0, window=window, creator_id=creator_id)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if handoff_rate is not None:
        d = detect_handoff_spike(handoff_rate=handoff_rate, sample_size=sample_size or 0, window=window, creator_id=creator_id)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if spam_rate is not None:
        d = detect_spam_risk(spam_rate=spam_rate, pressure_suppressed_rate=pressure_suppressed_rate, sample_size=sample_size or 0, window=window, creator_id=creator_id)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if open_loops is not None:
        d = detect_open_loop_stagnation(open_loops=open_loops, creator_id=creator_id, user_id=user_id, window=window)
        if d:
            diagnoses.append(d)
            is_healthy = False

    if baseline_rate is not None and current_rate is not None and sample_size is not None:
        d = detect_conversion_decline(baseline_rate=baseline_rate, current_rate=current_rate, sample_size=sample_size, creator_id=creator_id, window=window)
        if d and d.signal != OperationalSignal.INSUFFICIENT_DATA.value:
            diagnoses.append(d)
            if d.signal == OperationalSignal.CONVERSION_DECLINE.value:
                is_healthy = False
        elif d and d.signal == OperationalSignal.INSUFFICIENT_DATA.value:
            # Only add insufficient if no other diagnosis
            if not diagnoses:
                diagnoses.append(d)
                is_healthy = False

        # Strategy regression if strategy provided
        if strategy:
            d2 = detect_strategy_regression(strategy=strategy, baseline_rate=baseline_rate, current_rate=current_rate, sample_size=sample_size, creator_id=creator_id, window=window)
            if d2 and d2.signal == OperationalSignal.STRATEGY_REGRESSION.value:
                diagnoses.append(d2)
                is_healthy = False

        if product_family:
            d3 = detect_product_family_degradation(family=product_family, baseline_rate=baseline_rate, current_rate=current_rate, sample_size=sample_size, creator_id=creator_id, window=window)
            if d3:
                diagnoses.append(d3)
                is_healthy = False

        if response_mode:
            d4 = detect_response_mode_degradation(mode=response_mode, baseline_rate=baseline_rate, current_rate=current_rate, sample_size=sample_size, creator_id=creator_id, window=window)
            if d4:
                diagnoses.append(d4)
                is_healthy = False

    if is_healthy and not diagnoses:
        diagnoses.append(OperationalDiagnosis(
            signal=OperationalSignal.HEALTHY.value,
            priority=OperationalPriority.NORMAL.value,
            reason_code="HEALTHY",
            confidence=0.9,
            evidence={"window": window},
            scope=f"creator:{creator_id}" if creator_id else "global",
            creator_id=creator_id, user_id=user_id, window=window,
        ))

    # Sort by priority (deterministic)
    diagnoses.sort(key=lambda d: d.priority)
    return diagnoses

def recommend_from_diagnoses(
    diagnoses: list[OperationalDiagnosis],
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
    generation_id: str | None = None,
) -> list[OperationalRecommendation]:
    recs: list[OperationalRecommendation] = []
    seen_actions: set[str] = set()
    for d in diagnoses:
        rec = recommendation_for_diagnosis(d, creator_id=creator_id, user_id=user_id, generation_id=generation_id)
        # Deduplicate same action at same scope
        key = f"{rec.recommendation}:{rec.scope}"
        if key in seen_actions:
            continue
        seen_actions.add(key)
        recs.append(rec)
    # Sort by priority
    recs.sort(key=lambda r: r.priority)
    return recs

def operational_decision(
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
    generation_id: str | None = None,
    window: str = "24h",
    **kwargs,
) -> OperationalDecision:
    """One-call helper: diagnoses → recommendations → decision with production_state."""
    diags = analyze_operational_state(creator_id=creator_id, user_id=user_id, window=window, **kwargs)
    recs = recommend_from_diagnoses(diags, creator_id=creator_id, user_id=user_id, generation_id=generation_id)
    # Get production state for observability
    prod_state = None
    try:
        from commerce.production_control import evaluate_production_health, derive_production_state, is_global_paused, MetricWindow
        wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
        health = evaluate_production_health(creator_id=creator_id, window=wmap.get(window, MetricWindow.H24))
        prod_state = health.production_state
    except Exception:
        prod_state = "unknown"
    return OperationalDecision(diagnoses=diags, recommendations=recs, production_state=prod_state)

# ═══════════════════════════════════════════════════════════════════════════════
# Single-pass check (reuse)
# ═══════════════════════════════════════════════════════════════════════════════

def verify_operational_single_pass(calls: dict[str, int]) -> tuple[bool, str]:
    from commerce.adaptive_optimization import verify_single_pass
    return verify_single_pass(calls)

# ═══════════════════════════════════════════════════════════════════════════════
# Persistence / Retention — operational intelligence is stateless, no new persistence needed
# ═══════════════════════════════════════════════════════════════════════════════

def retention_check_operational() -> dict[str, Any]:
    # Stateless, no unbounded structures beyond diagnosis/recommendation per call (not stored)
    return {"operational_intelligence_persistence": "stateless_pure", "bounded": True}

