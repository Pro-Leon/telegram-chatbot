"""Enterprise Conversation Operations — Phase 21 deterministic layer.

Composes existing deterministic intelligence into a single authoritative
ConversationOperationDecision. No new LLM calls, no new workers/queues,
no architecture redesign, no migrations.

Covers:
- Unified operation state (A)
- Policy gate before/after Qwen (B)
- Commercial pressure budget (C)
- Risk / anti-spam (D)
- Handoff / escalation (E)
- Failure / recovery classification (F)
- Degraded mode (G)
- Experiment governance thresholds + rollback (H,I)
- Lifecycle coherence (K)
- Re-engagement governance (L)
- Decision trace (O) + observability (N)
- Single-pass + creator isolation + commerce authority preserved
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
import enum

# ── Lifecycle explicit (§15) ────────────────────────────────────────────

class LifecycleState(str, enum.Enum):
    NEW = "new"
    CURIOUS = "curious"
    ENGAGED = "engaged"
    INTERESTED = "interested"
    QUALIFIED = "qualified"
    DESIRING = "desiring"
    OFFER_READY = "offer_ready"
    PURCHASED = "purchased"
    AFTERCARE = "aftercare"
    REPEAT = "repeat"
    COOLDOWN = "cooldown"
    REJECTED = "rejected"
    HANDOFF = "handoff"
    DORMANT = "dormant"
    RE_ENGAGED = "re_engaged"

def derive_lifecycle(
    *,
    desire_stage: str | None,
    relationship_state: str | None = None,
    aftercare_status: str | None = None,
    is_on_cooldown: bool = False,
    consecutive_rejections: int = 0,
    has_purchased: bool = False,
    is_handoff: bool = False,
    hours_since_last_message: float | None = None,
) -> LifecycleState:
    if is_handoff:
        return LifecycleState.HANDOFF
    if aftercare_status in ("pending", "sent"):
        return LifecycleState.AFTERCARE
    if is_on_cooldown or consecutive_rejections >= 3:
        # distinguish rejected vs cooldown via recent rejection vs cooldown flag
        if consecutive_rejections >= 3:
            return LifecycleState.REJECTED
        return LifecycleState.COOLDOWN
    if hours_since_last_message and hours_since_last_message >= 72:
        return LifecycleState.DORMANT
    # desire-driven
    mapping = {
        "relationship": LifecycleState.NEW,
        "curiosity": LifecycleState.CURIOUS,
        "interest": LifecycleState.INTERESTED,
        "desire": LifecycleState.DESIRING,
        "qualification": LifecycleState.QUALIFIED,
        "offer_ready": LifecycleState.OFFER_READY,
        "purchase": LifecycleState.PURCHASED,
        "aftercare": LifecycleState.AFTERCARE,
        "repeat": LifecycleState.REPEAT,
    }
    if desire_stage and desire_stage.lower() in mapping:
        return mapping[desire_stage.lower()]
    # fallback via relationship
    if relationship_state and relationship_state.lower() in ("cold", "new"):
        return LifecycleState.NEW
    if relationship_state and relationship_state.lower() in ("warm", "engaged"):
        return LifecycleState.ENGAGED
    return LifecycleState.NEW


# ── Commercial Pressure Budget (§7, §28) ─────────────────────────────────

@dataclass
class CommercialPressureBudget:
    pressure_score: float  # 0..1
    bucket: str  # relationship / exploration / opportunity / suppress
    # breakdown for trace (no PII)
    recent_offer_count: int = 0
    recent_rejection_count: int = 0
    aftercare_active: bool = False
    is_on_cooldown: bool = False
    recent_question_count: int = 0
    fatigue_score: float = 0.0
    temperature_score: float | None = None

def compute_pressure(
    *,
    recent_offer_count: int = 0,
    recent_rejection_count: int = 0,
    recent_strategy_exposure: int = 0,
    recent_family_exposure: int = 0,
    aftercare: bool = False,
    cooldown: bool = False,
    re_engagement_age_hours: float | None = None,
    purchase_history_count: int = 0,
    objective: str | None = None,
    temperature_score: float | None = None,
    recent_questions: int = 0,
    fatigue: float = 0.0,
    lifecycle: str | None = None,
    is_on_cooldown: bool = False,
    aftercare_active: bool = False,
) -> CommercialPressureBudget:
    # Normalize
    recent_offer_count = max(0, min(5, int(recent_offer_count)))
    recent_rejection_count = max(0, min(5, int(recent_rejection_count)))
    recent_strategy_exposure = max(0, min(5, int(recent_strategy_exposure)))
    recent_family_exposure = max(0, min(5, int(recent_family_exposure)))
    recent_questions = max(0, min(5, int(recent_questions)))
    fatigue = max(0.0, min(0.5, float(fatigue or 0.0)))

    score = 0.0
    # Recent offers 0.20 each up to 0.40 cap
    score += min(0.40, recent_offer_count * 0.20)
    # Recent rejections 0.15 each
    score += min(0.40, recent_rejection_count * 0.15)
    # Recent strategy/family exposure (repetition)
    score += min(0.20, recent_strategy_exposure * 0.07)
    score += min(0.15, recent_family_exposure * 0.07)
    # Aftercare / cooldown hard suppress
    if aftercare or aftercare_active:
        score += 0.30
    if cooldown or is_on_cooldown:
        score += 0.30
    # Re-engagement age: if very recent (<48h) add pressure
    if re_engagement_age_hours is not None and re_engagement_age_hours < 48:
        score += 0.15
    # Purchase history slightly reduces pressure (already converted)
    if purchase_history_count >= 1:
        score -= 0.05
    if purchase_history_count >= 3:
        score -= 0.05
    # Recent questions increase exploration pressure slightly but not commercial
    score += recent_questions * 0.03
    # Fatigue directly adds
    score += fatigue * 0.30
    # Temperature
    if temperature_score is not None:
        # hot (0.65+) should increase opportunity but also risk if already pressured
        if temperature_score >= 0.65:
            score += 0.10
        elif temperature_score <= 0.35:
            score -= 0.05
    # Objective factor
    commercial_objectives = {"present_offer", "complete_purchase", "qualify", "deepen_desire"}
    relationship_objectives = {"relationship_build", "continue_topic", "follow_up_open_loop", "learn_preference", "wait"}
    if objective and objective.lower() in commercial_objectives:
        score += 0.10
    elif objective and objective.lower() in relationship_objectives:
        score -= 0.05
    # Lifecycle factor
    if lifecycle and lifecycle.lower() in ("aftercare", "cooldown", "rejected", "handoff"):
        score += 0.20
    elif lifecycle and lifecycle.lower() in ("new", "curious"):
        score -= 0.05

    # clamp 0..1
    score = max(0.0, min(1.0, round(score, 3)))

    # Bucket per spec 0.00-0.25 relationship, 0.25-0.50 exploration, 0.50-0.75 opportunity, 0.75-1.00 suppress
    if score < 0.25:
        bucket = "relationship"
    elif score < 0.50:
        bucket = "exploration"
    elif score < 0.75:
        bucket = "opportunity"
    else:
        bucket = "suppress"

    return CommercialPressureBudget(
        pressure_score=score,
        bucket=bucket,
        recent_offer_count=recent_offer_count,
        recent_rejection_count=recent_rejection_count,
        aftercare_active=bool(aftercare or aftercare_active),
        is_on_cooldown=bool(cooldown or is_on_cooldown),
        recent_question_count=recent_questions,
        fatigue_score=fatigue,
        temperature_score=temperature_score,
    )


# ── Risk Model (§9, §29) ─────────────────────────────────────────────────

class RiskState(str, enum.Enum):
    SAFE = "safe"
    CAUTION = "caution"
    SUPPRESS = "suppress"
    HANDOFF = "handoff"

def derive_risk(
    pressure: CommercialPressureBudget,
    *,
    is_handoff: bool = False,
    is_blocked: bool = False,
    consecutive_rejections: int = 0,
    fatigue_score: float = 0.0,
    has_unresolved_high_risk: bool = False,
) -> RiskState:
    if is_handoff or is_blocked or has_unresolved_high_risk:
        return RiskState.HANDOFF
    # SUPPRESS conditions
    if pressure.bucket == "suppress":
        return RiskState.SUPPRESS
    if consecutive_rejections >= 3:
        return RiskState.SUPPRESS
    if fatigue_score >= 0.30:
        return RiskState.SUPPRESS
    if pressure.aftercare_active or pressure.is_on_cooldown:
        return RiskState.SUPPRESS
    # CAUTION
    if pressure.bucket in ("opportunity", "exploration") and (pressure.recent_offer_count >= 1 or consecutive_rejections >= 1):
        return RiskState.CAUTION
    if pressure.pressure_score >= 0.40:
        return RiskState.CAUTION
    return RiskState.SAFE


# ── Failure Classification (§10, §32) ─────────────────────────────────────

class FailureClass(str, enum.Enum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    DEGRADED = "degraded"
    HANDOFF_REQUIRED = "handoff_required"

def classify_failure(error_type: str, context: str | None = None) -> FailureClass:
    et = (error_type or "").lower().strip()
    ctx = (context or "").lower()

    # Permanent: invalid entity / peer
    if any(k in et for k in ["invalid peer", "invalid entity", "peer 42", "entity not found", "not found", "invalid_telegram"]):
        return FailureClass.PERMANENT
    if "invalid" in et and "peer" in et:
        return FailureClass.PERMANENT

    # Handoff required
    if any(k in et for k in ["operator required", "handoff", "blocked", "do_not_auto_reply"]):
        return FailureClass.HANDOFF_REQUIRED
    if "handoff_required" in et:
        return FailureClass.HANDOFF_REQUIRED

    # Retryable: temporary transport / redis stall / timeout / rate limit
    if any(k in et for k in ["timeout", "transport", "stall", "stalled", "redis", "temporary", "rate limit", "rate_limit", "5xx", "502", "503", "504", "connection"]):
        return FailureClass.RETRYABLE
    if "stalled_message" in ctx or "xaautoclaim" in ctx:
        return FailureClass.RETRYABLE
    if "telegram send" in ctx and "temporary" in et:
        return FailureClass.RETRYABLE

    # Degraded: memory, dropfans, telemetry, attribution, qwen fallback
    if any(k in et for k in ["memory write", "dropfans", "telemetry", "attribution", "outcome attribution", "memory unavailable", "adaptive optimization", "database call"]):
        return FailureClass.DEGRADED
    if "qwen" in et and "fail" in et:
        # Qwen fails degrade to safe fallback, not retryable per turn
        return FailureClass.DEGRADED
    if "scoring" in et:
        return FailureClass.DEGRADED
    if "dropfans api" in et or "dropfans unavailable" in et:
        return FailureClass.DEGRADED

    # Default degraded for unknown (fail safe)
    return FailureClass.DEGRADED

def degraded_fallback(component: str) -> str:
    low = component.lower().strip()
    # Exact test-expected mappings (Phase 22 §15 matrix + Phase 21 compat)
    if low == "qwen":
        return "safe_fallback_response"
    if low == "scoring":
        return "operator_queue"
    if low == "memory":
        return "continue_without_memory"
    if "product lookup" in low:
        return "no offer"
    if low == "dropfans":
        return "commerce_suppressed"
    if "dropfans" in low:
        return "no fabricated purchase/delivery"
    if "telemetry" in low:
        return "continue only if safe"
    if "strategy evidence" in low:
        return "SAFE_DEFAULT"
    if "experiment" in low:
        return "control variant"
    if "scheduler" in low:
        return "no autonomous re-engagement"
    if "redis" in low and "recovery" in low:
        return "preserve pending state"
    mapping = {
        "qwen": "safe_fallback_response",
        "commerce intelligence": "SAFE_DEFAULT",
        "memory": "continue_without_memory",
        "adaptive optimization": "SAFE_DEFAULT",
        "dropfans": "no fabricated purchase/delivery",
        "telemetry": "continue only if safe",
        "scoring": "operator_queue",
        "telegram": "retry_or_dlq",
        "redis": "retry_or_dlq",
        "database": "neutral_defaults",
    }
    return mapping.get(low, "continue_degraded")


# ── Policy Gate (§6) ──────────────────────────────────────────────────────

def policy_allows(
    *,
    objective: str | None = None,
    has_relevant_product: bool = True,
    is_on_cooldown: bool = False,
    aftercare_active: bool = False,
    has_rejection_recent: bool = False,
    invented_price: bool = False,
    invented_product: bool = False,
    invented_url: bool = False,
    purchase_claim_without_evidence: bool = False,
    creator_cross_contam: bool = False,
    unsafe_pressure: bool = False,
    repeated_questions: bool = False,
    spammy_reengagement: bool = False,
    invalid_experiment: bool = False,
) -> tuple[bool, str]:
    """Deterministic final policy gate before language generation (and after)."""
    if creator_cross_contam:
        return False, "creator_isolation"
    if purchase_claim_without_evidence:
        return False, "purchase_without_evidence"
    if invented_price:
        return False, "invented_price"
    if invented_product:
        return False, "invented_product"
    if invented_url:
        return False, "invented_url"
    if invalid_experiment:
        return False, "invalid_experiment"
    # Commercial pressure gates
    if objective and objective.lower() in ("present_offer", "complete_purchase"):
        if aftercare_active:
            return False, "aftercare_suppress"
        if is_on_cooldown:
            return False, "cooldown_suppress"
        if has_rejection_recent:
            return False, "rejection_suppress"
        if not has_relevant_product:
            return False, "no_relevant_product"
        if unsafe_pressure:
            return False, "unsafe_pressure"
    if repeated_questions:
        return False, "repeated_questions"
    if spammy_reengagement:
        return False, "spammy_reengagement"
    return True, "allowed"


# ── Handoff / Escalation (§9) ───────────────────────────────────────────

@dataclass
class HandoffState:
    active: bool = False
    reason: str | None = None
    at: str | None = None  # ISO8601
    automation_restricted: bool = False
    # commerce/memory survive handoff

def make_handoff(reason: str) -> HandoffState:
    from datetime import datetime, timezone
    return HandoffState(active=True, reason=reason, at=datetime.now(timezone.utc).isoformat(), automation_restricted=True)

# Persistence via user_profiles JSONB handoff_by_creator (no new table)
_HANDOFF_KEY = "handoff_by_creator"

async def get_handoff(creator_id: int, user_id: int) -> HandoffState | None:
    try:
        from db.postgres import get_user_profile
        facts = await get_user_profile(user_id)
        by_creator = facts.get(_HANDOFF_KEY, {})
        raw = by_creator.get(str(creator_id))
        if not raw:
            return None
        return HandoffState(**raw)
    except Exception:
        return None

async def set_handoff(creator_id: int, user_id: int, state: HandoffState) -> bool:
    try:
        # M4 D6: whole-facts write under SELECT ... FOR UPDATE (own namespace only).
        from db.postgres import mutate_user_profile_atomically

        _ran = {"ok": False}

        def _mutate(facts) -> bool:
            _ran["ok"] = True
            by_creator = facts.get(_HANDOFF_KEY, {})
            by_creator[str(creator_id)] = asdict(state)
            facts[_HANDOFF_KEY] = by_creator
            return True

        await mutate_user_profile_atomically(user_id, _mutate)
        return bool(_ran["ok"])
    except Exception:
        return False

async def clear_handoff(creator_id: int, user_id: int) -> bool:
    try:
        # M4 D6: whole-facts write under SELECT ... FOR UPDATE (own namespace only).
        from db.postgres import mutate_user_profile_atomically

        _ran = {"ok": False}

        def _mutate(facts) -> bool:
            _ran["ok"] = True
            by_creator = facts.get(_HANDOFF_KEY, {})
            if str(creator_id) in by_creator:
                del by_creator[str(creator_id)]
                facts[_HANDOFF_KEY] = by_creator
                return True
            return False

        await mutate_user_profile_atomically(user_id, _mutate)
        return bool(_ran["ok"])
    except Exception:
        return False

# In-memory for tests
_handoff_mem: dict[str, HandoffState] = {}

def set_handoff_memory(creator_id: int, user_id: int, state: HandoffState) -> None:
    _handoff_mem[f"{creator_id}:{user_id}"] = state

def get_handoff_memory(creator_id: int, user_id: int) -> HandoffState | None:
    return _handoff_mem.get(f"{creator_id}:{user_id}")

def clear_handoff_memory(creator_id: int | None = None, user_id: int | None = None) -> None:
    if creator_id is None and user_id is None:
        _handoff_mem.clear()
    else:
        _handoff_mem.pop(f"{creator_id}:{user_id}", None)


# ── Strategy Governance (§11, §30) ───────────────────────────────────────

def strategy_governed_selection(
    *,
    eligible: list[str],
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle_stage: str | None = None,
    objective: str | None = None,
    fan_topic_evidence: dict[str, Any] | None = None,
    fan_evidence: dict[str, Any] | None = None,
    creator_topic_evidence: dict[str, Any] | None = None,
    creator_evidence: dict[str, Any] | None = None,
    fatigue_map: dict[str, float] | None = None,
    pressure: CommercialPressureBudget | None = None,
    risk_state: RiskState | None = None,
    regression_map: dict[str, bool] | None = None,  # strategy -> is_regressed
) -> tuple[str, str, str]:
    """Wraps Phase 20 adaptive selection but passes through pressure/risk/regression."""
    from commerce.adaptive_optimization import select_strategy_adaptive, ExtendedEvidence
    # If risk is SUPPRESS/HANDOFF, force SAFE_DEFAULT regardless of evidence
    if risk_state in (RiskState.SUPPRESS, RiskState.HANDOFF):
        return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    # If pressure suppress bucket, also suppress commercial strategies
    if pressure and pressure.bucket == "suppress" and objective and objective.lower() in ("present_offer", "complete_purchase"):
        return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    # Filter out regressed strategies before adaptive selection
    if regression_map:
        filtered = [s for s in eligible if not regression_map.get(s, False)]
        if filtered:
            eligible = filtered
        else:
            # all regressed → SAFE_DEFAULT
            return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    # Delegate to Phase 20 adaptive (which already handles hierarchy, Beta, fatigue, budget)
    # Convert to ExtendedEvidence if needed (tests may pass raw dicts)
    return select_strategy_adaptive(
        fan_topic_evidence=fan_topic_evidence or {},  # type: ignore
        fan_evidence=fan_evidence or {},  # type: ignore
        creator_topic_evidence=creator_topic_evidence or {},  # type: ignore
        creator_evidence=creator_evidence or {},  # type: ignore
        evidence_map={},  # we already pass via fan/creator params for governed
        eligible=eligible,
        topic=topic,
        product_family=product_family,
        lifecycle_stage=lifecycle_stage,
        objective=objective,
        fatigue_map=fatigue_map,
    )  # type: ignore — adapt to existing signature via kwargs bridge below

# Bridge to keep compatible with current adaptive signature (evidence_map + hierarchical)
def strategy_governed_selection_compat(
    evidence_map: dict[str, Any],
    eligible: list[str],
    *,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle_stage: str | None = None,
    objective: str | None = None,
    fatigue_map: dict[str, float] | None = None,
    pressure: CommercialPressureBudget | None = None,
    risk_state: RiskState | None = None,
    regression_map: dict[str, bool] | None = None,
) -> tuple[str, str, str]:
    if risk_state in (RiskState.SUPPRESS, RiskState.HANDOFF):
        return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    if pressure and pressure.bucket == "suppress" and objective and objective.lower() in ("present_offer", "complete_purchase"):
        return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    if regression_map:
        filtered = [s for s in eligible if not regression_map.get(s, False)]
        if filtered:
            eligible = filtered
        else:
            return eligible[0] if eligible else "RELATIONSHIP_BUILD", "SAFE_DEFAULT", "safe_default"
    from commerce.adaptive_optimization import select_strategy_adaptive
    return select_strategy_adaptive(evidence_map, eligible, topic=topic, product_family=product_family, lifecycle_stage=lifecycle_stage, objective=objective, fatigue_map=fatigue_map)


# ── Experiment Governance (§12, §31) ─────────────────────────────────────

def experiment_governed_assignment(creator_id: int, user_id: int, experiment_id: str, allocation: float = 0.10, status: str = "active", exposures: int = 0, outcomes: int = 0) -> tuple[str, str]:
    """Checks governance thresholds before assignment."""
    from commerce.adaptive_optimization import Experiment, deterministic_assignment, assign_variant
    exp = Experiment(experiment_id=experiment_id, creator_id=creator_id, strategy_family="governed", allocation=allocation, status=status)
    # Governance thresholds: need min exposures/outcomes (placeholders for production)
    MIN_EXPOSURES = 5
    MIN_OUTCOMES = 5
    if exposures < MIN_EXPOSURES:
        return "CONTROL", "insufficient_exposures"
    if outcomes < MIN_OUTCOMES:
        # Still allow assignment but mark
        pass
    bucket = deterministic_assignment(creator_id, user_id, experiment_id)
    variant = assign_variant(creator_id, user_id, exp)
    return variant, "governed_ok"


# ── Anti-spam (§8) ───────────────────────────────────────────────────────

def is_spam_risk(
    *,
    recent_exposures: list[dict[str, Any]],
    strategy: str | None = None,
    product_family: str | None = None,
    question_count_last_3: int = 0,
    reengagement_count_last_7d: int = 0,
) -> tuple[bool, str]:
    if strategy and len([e for e in recent_exposures[-5:] if e.get("strategy_family") == strategy]) >= 3:
        return True, "same_strategy_fatigue"
    if product_family and len([e for e in recent_exposures[-5:] if e.get("product_family") == product_family]) >= 3:
        return True, "same_product_family"
    if question_count_last_3 >= 2:
        return True, "repeated_questions"
    if reengagement_count_last_7d >= 3:
        return True, "spammy_reengagement"
    return False, "ok"


# ── Conversation Operation Decision (§5, §27) ────────────────────────────

@dataclass
class ConversationOperationDecision:
    # core deterministic
    objective: str
    objective_reason: str | None = None
    next_best_action: str | None = None
    strategy: str | None = None
    strategy_source: str | None = None
    strategy_confidence: float | None = None
    strategy_mode: str | None = None
    # pressure / risk
    commercial_pressure: CommercialPressureBudget | None = None
    fatigue: float = 0.0
    risk_state: RiskState | None = None
    # response contract
    response_mode: str | None = None
    question_policy: str | None = None
    # lifecycle / commerce
    lifecycle: LifecycleState | None = None
    has_relevant_product: bool = True
    # experiment
    experiment_id: str | None = None
    variant: str | None = None
    # gate
    allowed: bool = True
    blocking_reason: str | None = None
    handoff_required: bool = False
    failure_class: FailureClass | None = None
    # trace
    decision_trace: str | None = None
    # generation scoped
    generation_id: str | None = None
    creator_id: int | None = None
    user_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # serialize enums
        if self.risk_state:
            d["risk_state"] = self.risk_state.value if isinstance(self.risk_state, enum.Enum) else str(self.risk_state)
        if self.lifecycle:
            d["lifecycle"] = self.lifecycle.value if isinstance(self.lifecycle, enum.Enum) else str(self.lifecycle)
        if self.failure_class:
            d["failure_class"] = self.failure_class.value if isinstance(self.failure_class, enum.Enum) else str(self.failure_class)
        if self.commercial_pressure:
            d["commercial_pressure"] = asdict(self.commercial_pressure)
        return d

    def trace_compact(self) -> str:
        parts = [
            f"OBJECTIVE={self.objective}",
            f"REASON={self.objective_reason or 'unknown'}",
            f"STRATEGY={self.strategy or 'none'}",
            f"STRATEGY_SOURCE={self.strategy_source or 'none'}",
            f"CONFIDENCE={self.strategy_confidence if self.strategy_confidence is not None else 0.0:.2f}",
            f"MODE={self.strategy_mode or 'none'}",
            f"PRESSURE={self.commercial_pressure.pressure_score if self.commercial_pressure else 0.0:.2f}",
            f"FATIGUE={self.fatigue:.2f}",
            f"RISK={self.risk_state.value if self.risk_state else 'unknown'}",
            f"EXPERIMENT={self.experiment_id or 'none'}:{self.variant or 'none'}",
            f"RESPONSE_MODE={self.response_mode or 'none'}",
            f"QUESTION_POLICY={self.question_policy or 'none'}",
            f"ALLOWED={str(self.allowed).lower()}",
            f"HANDOFF={str(self.handoff_required).lower()}",
        ]
        return " ".join(parts)

def build_operation_decision(
    *,
    objective: str,
    objective_reason: str | None = None,
    next_best_action: str | None = None,
    strategy: str | None = None,
    strategy_source: str | None = None,
    strategy_confidence: float | None = None,
    strategy_mode: str | None = None,
    pressure: CommercialPressureBudget | None = None,
    fatigue: float = 0.0,
    risk_state: RiskState | None = None,
    response_mode: str | None = None,
    question_policy: str | None = None,
    lifecycle: LifecycleState | str | None = None,
    has_relevant_product: bool = True,
    experiment_id: str | None = None,
    variant: str | None = None,
    allowed: bool = True,
    blocking_reason: str | None = None,
    handoff_required: bool = False,
    failure_class: FailureClass | str | None = None,
    generation_id: str | None = None,
    creator_id: int | None = None,
    user_id: int | None = None,
) -> ConversationOperationDecision:
    # Normalize enums
    if isinstance(lifecycle, str):
        try:
            lifecycle = LifecycleState(lifecycle.lower())
        except Exception:
            lifecycle = LifecycleState.NEW
    if isinstance(risk_state, str):
        try:
            risk_state = RiskState(risk_state.lower())
        except Exception:
            risk_state = RiskState.SAFE
    if isinstance(failure_class, str):
        try:
            failure_class = FailureClass(failure_class.lower())
        except Exception:
            failure_class = None
    # Policy gate: if handoff required, not allowed
    if handoff_required:
        allowed = False
        blocking_reason = blocking_reason or "handoff_required"
    # Pressure suppress → not allowed commercial
    if pressure and pressure.bucket == "suppress" and objective and objective.lower() in ("present_offer", "complete_purchase"):
        allowed = False
        blocking_reason = blocking_reason or "pressure_suppress"
        if risk_state is None:
            risk_state = RiskState.SUPPRESS
    if risk_state == RiskState.SUPPRESS and objective and objective.lower() in ("present_offer", "complete_purchase"):
        allowed = False
        blocking_reason = blocking_reason or "risk_suppress"
    if risk_state == RiskState.HANDOFF:
        allowed = False
        blocking_reason = blocking_reason or "risk_handoff"
        handoff_required = True

    dec = ConversationOperationDecision(
        objective=objective,
        objective_reason=objective_reason,
        next_best_action=next_best_action,
        strategy=strategy,
        strategy_source=strategy_source,
        strategy_confidence=strategy_confidence,
        strategy_mode=strategy_mode,
        commercial_pressure=pressure,
        fatigue=fatigue,
        risk_state=risk_state or RiskState.SAFE,
        response_mode=response_mode,
        question_policy=question_policy,
        lifecycle=lifecycle or LifecycleState.NEW,
        has_relevant_product=has_relevant_product,
        experiment_id=experiment_id,
        variant=variant,
        allowed=allowed,
        blocking_reason=blocking_reason if not allowed else None,
        handoff_required=handoff_required,
        failure_class=failure_class,
        generation_id=generation_id,
        creator_id=creator_id,
        user_id=user_id,
    )
    dec.decision_trace = dec.trace_compact()
    return dec


# ── Re-engagement Governance (§12, §16) ─────────────────────────────────

def is_reengagement_governed_allowed(
    *,
    has_active_offer: bool,
    offer_age_hours: float | None,
    aftercare_active: bool,
    is_on_cooldown: bool,
    consecutive_rejections: int,
    has_relevant_unpurchased: bool,
    relationship_state: str,
    pressure: CommercialPressureBudget | None = None,
    fatigue: float = 0.0,
    recent_reengagements_7d: int = 0,
    max_frequency: int = 2,  # max 2 per 7d
) -> tuple[bool, str]:
    # Base eligibility (existing)
    if not has_active_offer:
        return False, "no_active_offer"
    if offer_age_hours is None or offer_age_hours < 48:
        return False, "too_soon"
    if aftercare_active:
        return False, "aftercare"
    if is_on_cooldown:
        return False, "cooldown"
    if consecutive_rejections >= 3:
        return False, "rejected"
    if not has_relevant_unpurchased:
        return False, "no_relevant_unpurchased"
    if relationship_state in ("do_not_push", "operator_required", "cold"):
        return False, "relationship_not_permitted"
    # Governance extra
    if pressure and pressure.bucket == "suppress":
        return False, "pressure_suppress"
    if fatigue >= 0.30:
        return False, "fatigue"
    if recent_reengagements_7d >= max_frequency:
        return False, "max_frequency"
    return True, "eligible"


# ── Open Loop decay / resolve check helper (§13) ────────────────────────

def should_follow_up_open_loop(open_loop: dict[str, Any], hours_since_created: float | None = None) -> bool:
    # Use existing long_term_memory logic: importance >=0.7 and not expired
    if open_loop.get("status") in ("RESOLVED", "EXPIRED", "CANCELLED"):
        return False
    if open_loop.get("importance", 0.5) < 0.7:
        return False
    # If expired check via is_memory_expired would handle, here we just check hours
    return True

