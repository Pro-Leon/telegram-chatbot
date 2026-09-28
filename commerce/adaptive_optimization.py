"""Adaptive Conversation Optimization — Phase 20 deterministic layer.

Pure, deterministic, no LLM, no new queue/worker, no architecture redesign.
Implements:
- ConversationObservation contract
- Strategy exposure logging (attributable, bounded, no PII)
- Canonical outcome taxonomy + deterministic weights
- Hierarchical strategy evidence (fan/topic/product/lifecycle)
- Sample-size safety, Beta uncertainty, exploration vs exploitation, bounded exploration budget
- Strategy fatigue
- Negative learning with decay
- Purchase attribution window (direct/assisted/organic) — DropFans sole authority
- Lifecycle-specific metrics
- Relationship vs commerce quality metrics
- Strategy score (explainable, bounded)
- Decision trace
- Regression detection
- Experiment contract (deterministic hash, stable, creator-isolated)
- Bounded retention via JSONB aggregation
- Authority hierarchy enforcement

All persistence via existing user_profiles JSONB (bounded) — no new table required.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
import enum


# ── Canonical outcome taxonomy (§5) ──────────────────────────────────────

class CanonicalOutcome(str, enum.Enum):
    NO_SIGNAL = "no_signal"
    POSITIVE_ENGAGEMENT = "positive_engagement"
    TOPIC_CONTINUATION = "topic_continuation"
    INTEREST_INCREASE = "interest_increase"
    DESIRE_INCREASE = "desire_increase"
    DESIRE_DECREASE = "desire_decrease"
    QUESTION_ANSWERED = "question_answered"
    OPEN_LOOP_RESOLVED = "open_loop_resolved"
    PREFERENCE_LEARNED = "preference_learned"
    OBJECTION = "objection"
    OBJECTION_RESOLVED = "objection_resolved"
    OFFER_REQUEST = "offer_request"
    PURCHASE = "purchase"
    AFTERCARE_ENGAGEMENT = "aftercare_engagement"
    REPEAT_PURCHASE = "repeat_purchase"
    REJECTION = "rejection"
    COOLDOWN = "cooldown"
    HANDOFF = "handoff"
    CONVERSATION_END = "conversation_end"


# Deterministic outcome weights (§6) — learning evidence only, never price/authority
# Ordering justified by commerce funnel: PURCHASE strongest, REPEAT higher, negative reduces preference
OUTCOME_WEIGHTS: dict[str, float] = {
    CanonicalOutcome.REPEAT_PURCHASE.value: 12.0,
    CanonicalOutcome.PURCHASE.value: 10.0,
    CanonicalOutcome.OBJECTION_RESOLVED.value: 6.0,
    CanonicalOutcome.DESIRE_INCREASE.value: 4.0,
    CanonicalOutcome.INTEREST_INCREASE.value: 3.0,
    CanonicalOutcome.AFTERCARE_ENGAGEMENT.value: 3.0,
    CanonicalOutcome.POSITIVE_ENGAGEMENT.value: 2.0,
    CanonicalOutcome.OPEN_LOOP_RESOLVED.value: 2.5,
    CanonicalOutcome.TOPIC_CONTINUATION.value: 1.5,
    CanonicalOutcome.QUESTION_ANSWERED.value: 1.5,
    CanonicalOutcome.PREFERENCE_LEARNED.value: 1.0,
    CanonicalOutcome.OFFER_REQUEST.value: 2.0,
    CanonicalOutcome.NO_SIGNAL.value: 0.0,
    CanonicalOutcome.CONVERSATION_END.value: 0.0,
    CanonicalOutcome.OBJECTION.value: -1.5,
    CanonicalOutcome.DESIRE_DECREASE.value: -2.0,
    CanonicalOutcome.COOLDOWN.value: -3.0,
    CanonicalOutcome.HANDOFF.value: -1.0,
    CanonicalOutcome.REJECTION.value: -4.0,
}

def outcome_strength(outcome: str | CanonicalOutcome) -> float:
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    # normalize: allow upper case
    key = key.lower()
    return OUTCOME_WEIGHTS.get(key, 0.0)


# ── ConversationObservation (§3) ─────────────────────────────────────────

@dataclass
class ConversationObservation:
    creator_id: int | None
    user_id: int
    generation_id: str
    timestamp: str  # ISO8601 UTC

    conversation_state: str | None = None
    relationship_state: str | None = None

    desire_stage: str | None = None
    desire_confidence: float | None = None

    commercial_temperature: str | None = None
    sales_window: str | None = None

    offer_readiness: str | None = None
    offer_authorized: bool = False

    next_best_action: str | None = None
    conversation_objective: str | None = None

    strategy_family: str | None = None
    strategy_variant: str | None = None

    response_mode: str | None = None
    question_policy: str | None = None

    current_topic: str | None = None
    open_threads: tuple[str, ...] = field(default_factory=tuple)

    relevant_product_id: int | None = None
    relevant_product_family: str | None = None
    product_selection_reason: str | None = None

    objection_type: str | None = None
    qualification_state: str | None = None

    memory_retrieved_count: int = 0
    open_loop_count: int = 0

    fan_message_classification: str | None = None
    outcome: str | None = None

    desire_before: str | None = None
    desire_after: str | None = None

    purchase_event: bool = False
    aftercare_state: str | None = None

    scoring_result: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["open_threads"] = list(d["open_threads"])
        return d

def build_observation(
    *,
    creator_id: int | None,
    user_id: int,
    generation_id: str,
    timestamp: str | None = None,
    conversation_state: str | None = None,
    relationship_state: str | None = None,
    desire_stage: str | None = None,
    desire_confidence: float | None = None,
    commercial_temperature: str | None = None,
    sales_window: str | None = None,
    offer_readiness: str | None = None,
    offer_authorized: bool = False,
    next_best_action: str | None = None,
    conversation_objective: str | None = None,
    strategy_family: str | None = None,
    strategy_variant: str | None = None,
    response_mode: str | None = None,
    question_policy: str | None = None,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
    relevant_product_id: int | None = None,
    relevant_product_family: str | None = None,
    product_selection_reason: str | None = None,
    objection_type: str | None = None,
    qualification_state: str | None = None,
    memory_retrieved_count: int = 0,
    open_loop_count: int = 0,
    fan_message_classification: str | None = None,
    outcome: str | None = None,
    desire_before: str | None = None,
    desire_after: str | None = None,
    purchase_event: bool = False,
    aftercare_state: str | None = None,
    scoring_result: float | None = None,
) -> ConversationObservation:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    return ConversationObservation(
        creator_id=creator_id, user_id=user_id, generation_id=generation_id, timestamp=ts,
        conversation_state=conversation_state, relationship_state=relationship_state,
        desire_stage=desire_stage, desire_confidence=desire_confidence,
        commercial_temperature=commercial_temperature, sales_window=sales_window,
        offer_readiness=offer_readiness, offer_authorized=offer_authorized,
        next_best_action=next_best_action, conversation_objective=conversation_objective,
        strategy_family=strategy_family, strategy_variant=strategy_variant,
        response_mode=response_mode, question_policy=question_policy,
        current_topic=current_topic, open_threads=tuple(open_threads),
        relevant_product_id=relevant_product_id, relevant_product_family=relevant_product_family,
        product_selection_reason=product_selection_reason,
        objection_type=objection_type, qualification_state=qualification_state,
        memory_retrieved_count=memory_retrieved_count, open_loop_count=open_loop_count,
        fan_message_classification=fan_message_classification, outcome=outcome,
        desire_before=desire_before, desire_after=desire_after,
        purchase_event=purchase_event, aftercare_state=aftercare_state,
        scoring_result=scoring_result,
    )


# ── Strategy Exposure (§4) ───────────────────────────────────────────────

@dataclass
class StrategyExposure:
    creator_id: int
    user_id: int
    generation_id: str
    strategy_family: str
    strategy_variant: str | None
    topic: str | None
    conversation_stage: str | None
    desire_stage: str | None
    temperature: str | None
    sales_window: str | None
    next_best_action: str | None
    response_mode: str | None
    question_policy: str | None
    product_id: int | None = None
    product_family: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Phase 10: explicit version attribution. Additive with safe defaults;
    # existing callers without versions remain readable as legacy.
    # strategy_version is distinct from experiment/variant/config/ranking.
    strategy_version: str = "strategy.v1"
    config_version: str = "unversioned-legacy"
    experiment_id: str | None = None
    experiment_variant: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def make_exposure(
    *,
    creator_id: int,
    user_id: int,
    generation_id: str,
    strategy_family: str,
    strategy_variant: str | None = None,
    topic: str | None = None,
    conversation_stage: str | None = None,
    desire_stage: str | None = None,
    temperature: str | None = None,
    sales_window: str | None = None,
    next_best_action: str | None = None,
    response_mode: str | None = None,
    question_policy: str | None = None,
    product_id: int | None = None,
    product_family: str | None = None,
    timestamp: str | None = None,
    strategy_version: str | None = None,
    config_version: str | None = None,
    experiment_id: str | None = None,
    experiment_variant: str | None = None,
) -> StrategyExposure:
    # Phase 10: resolve runtime config version fail-safe (never blocks).
    _cfg = config_version
    if _cfg is None:
        try:
            from commerce.phase10_learning import get_active_config_version
            _cfg = get_active_config_version(creator_scope=creator_id)
        except Exception:
            _cfg = "unversioned-legacy"
    return StrategyExposure(
        creator_id=creator_id, user_id=user_id, generation_id=generation_id,
        strategy_family=strategy_family, strategy_variant=strategy_variant,
        topic=topic, conversation_stage=conversation_stage,
        desire_stage=desire_stage, temperature=temperature, sales_window=sales_window,
        next_best_action=next_best_action, response_mode=response_mode,
        question_policy=question_policy, product_id=product_id, product_family=product_family,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        strategy_version=strategy_version or "strategy.v1",
        config_version=_cfg or "unversioned-legacy",
        experiment_id=experiment_id,
        experiment_variant=experiment_variant,
    )

# In-memory exposure ring buffer for tests / fallback when DB not available
_exposure_buffer: dict[str, list[dict[str, Any]]] = {}  # key = f"{creator_id}:{user_id}"
_EXPOSURE_MAX = 50

def _exposure_key(creator_id: int, user_id: int) -> str:
    return f"{creator_id}:{user_id}"

def record_exposure_memory(exposure: StrategyExposure) -> None:
    """Record exposure in-memory (bounded ring, no DB). For tests."""
    k = _exposure_key(exposure.creator_id, exposure.user_id)
    buf = _exposure_buffer.get(k, [])
    buf.append(exposure.to_dict())
    if len(buf) > _EXPOSURE_MAX:
        buf = buf[-_EXPOSURE_MAX:]
    _exposure_buffer[k] = buf

def get_exposures_memory(creator_id: int, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    k = _exposure_key(creator_id, user_id)
    return list(_exposure_buffer.get(k, [])[-limit:])

def clear_exposures_memory() -> None:
    _exposure_buffer.clear()

def attributable_exposure_for_generation(creator_id: int, user_id: int, generation_id: str) -> dict[str, Any] | None:
    for e in get_exposures_memory(creator_id, user_id, limit=100):
        if e.get("generation_id") == generation_id:
            return e
    return None

# DB-backed exposure via user_profiles JSONB (bounded)
async def persist_exposure(exposure: StrategyExposure) -> bool:
    """Persist exposure to user_profiles JSONB bounded ring. Best-effort."""
    try:
        # M4 D6: whole-facts write under SELECT ... FOR UPDATE (own namespace only).
        from db.postgres import mutate_user_profile_atomically

        def _mutate(facts) -> bool:
            by_creator = facts.get("strategy_exposures_by_creator", {})
            key = str(exposure.creator_id)
            lst = by_creator.get(key, [])
            # enforce bound
            lst.append(exposure.to_dict())
            if len(lst) > _EXPOSURE_MAX:
                # keep most recent
                lst = lst[-_EXPOSURE_MAX:]
            by_creator[key] = lst
            facts["strategy_exposures_by_creator"] = by_creator
            # also prune exposures older than 30d to enforce retention
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                pruned = []
                for e in lst:
                    try:
                        ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts >= cutoff:
                            pruned.append(e)
                    except Exception:
                        pruned.append(e)
                # never prune to empty if all old — keep at least 10 most recent
                if not pruned and lst:
                    pruned = lst[-10:]
                by_creator[key] = pruned
                facts["strategy_exposures_by_creator"] = by_creator
            except Exception:
                pass
            return True

        persisted = await mutate_user_profile_atomically(exposure.user_id, _mutate)
        # also in-memory for fast path
        record_exposure_memory(exposure)
        return bool(persisted)
    except Exception:
        # fallback to memory only
        record_exposure_memory(exposure)
        return False


# ── Outcome Attribution — canonical classifier (§5, §6) ─────────────────

def classify_canonical_outcome(
    *,
    fan_message: str | None = None,
    desire_before: str | None = None,
    desire_after: str | None = None,
    has_purchase: bool = False,
    is_repeat_purchase: bool = False,
    has_objection: bool = False,
    objection_resolved: bool = False,
    topic_continued: bool = False,
    question_answered: bool = False,
    open_loop_resolved: bool = False,
    preference_learned: bool = False,
    aftercare_engaged: bool = False,
    is_handoff: bool = False,
    is_cooldown: bool = False,
    is_conversation_end: bool = False,
    offer_requested: bool = False,
    fan_message_length: int | None = None,
    positive_signals: list[str] | None = None,
) -> CanonicalOutcome:
    """Deterministic canonical outcome — observable evidence only, no fabrication."""
    low = (fan_message or "").lower().strip()
    # Priority: handoff > purchase > repeat > aftercare > conversation_end > cooldown
    if is_handoff:
        return CanonicalOutcome.HANDOFF
    if is_repeat_purchase and has_purchase:
        return CanonicalOutcome.REPEAT_PURCHASE
    if has_purchase:
        return CanonicalOutcome.PURCHASE
    if aftercare_engaged:
        return CanonicalOutcome.AFTERCARE_ENGAGEMENT
    if is_conversation_end:
        return CanonicalOutcome.CONVERSATION_END
    if is_cooldown:
        return CanonicalOutcome.COOLDOWN
    if objection_resolved:
        return CanonicalOutcome.OBJECTION_RESOLVED
    if has_objection:
        # distinguish rejection vs objection
        if low in ("nah", "no", "not interested", "nope", "stop"):
            return CanonicalOutcome.REJECTION
        return CanonicalOutcome.OBJECTION
    if offer_requested or ("how much" in low or "where can i buy" in low or "buy" in low and "want to buy" in low):
        return CanonicalOutcome.OFFER_REQUEST
    if open_loop_resolved:
        return CanonicalOutcome.OPEN_LOOP_RESOLVED
    if question_answered:
        return CanonicalOutcome.QUESTION_ANSWERED
    if preference_learned:
        return CanonicalOutcome.PREFERENCE_LEARNED
    # Desire progression (requires both before/after)
    _order = ["relationship", "curiosity", "interest", "desire", "qualification", "offer_ready", "purchase", "aftercare", "repeat"]
    try:
        if desire_before and desire_after and desire_before != desire_after:
            if desire_before in _order and desire_after in _order:
                bi = _order.index(desire_before)
                ai = _order.index(desire_after)
                if ai > bi:
                    if desire_after in ("desire", "qualification", "offer_ready"):
                        return CanonicalOutcome.DESIRE_INCREASE
                    return CanonicalOutcome.INTEREST_INCREASE
                elif ai < bi:
                    return CanonicalOutcome.DESIRE_DECREASE
    except Exception:
        pass
    if topic_continued:
        return CanonicalOutcome.TOPIC_CONTINUATION
    # Positive engagement heuristics (deterministic, bounded)
    if positive_signals or low:
        if any(w in low for w in ("love", "amazing", "great", "awesome", "thanks", "thank you", "yes", "sure", "sounds good")):
            return CanonicalOutcome.POSITIVE_ENGAGEMENT
        if fan_message_length is not None and fan_message_length > 20:
            # long reply is continuation
            if len(low) > 20:
                return CanonicalOutcome.POSITIVE_ENGAGEMENT
    if fan_message is not None:
        l = fan_message_length if fan_message_length is not None else len((fan_message or "").strip())
        if l == 0:
            return CanonicalOutcome.NO_SIGNAL
        if l < 5:
            # very short without positive signal -> no_signal, not low_engagement (we use taxonomy)
            return CanonicalOutcome.NO_SIGNAL
        return CanonicalOutcome.POSITIVE_ENGAGEMENT
    return CanonicalOutcome.NO_SIGNAL

# Compatibility wrapper: old classify_outcome signature returns CanonicalOutcome
def classify_outcome_compat(previous_strategy: str, fan_message: str, desire_before: str, desire_after: str) -> CanonicalOutcome:
    has_obj = any(w in fan_message.lower() for w in ["too expensive", "maybe later", "not now", "broke"])
    is_reject = fan_message.lower().strip() in ["nah", "no", "not interested"]
    is_request = "how much" in fan_message.lower() or "where can i buy" in fan_message.lower()
    if is_reject:
        return CanonicalOutcome.REJECTION
    if has_obj:
        return CanonicalOutcome.OBJECTION
    if is_request:
        return CanonicalOutcome.OFFER_REQUEST
    if len(fan_message.strip()) < 5:
        return CanonicalOutcome.NO_SIGNAL
    if desire_after in ("desire", "qualification", "offer_ready") and desire_before in ("relationship", "curiosity"):
        return CanonicalOutcome.DESIRE_INCREASE
    if len(fan_message.strip()) > 20:
        return CanonicalOutcome.POSITIVE_ENGAGEMENT
    return CanonicalOutcome.NO_SIGNAL


# ── Strategy Evidence Extended (§7, §8, §9, §13) ───────────────────────

@dataclass
class ExtendedEvidence:
    attempt_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    purchase_count: int = 0
    last_used: str | None = None
    last_positive: str | None = None
    confidence: float = 0.5

    @property
    def positive_rate(self) -> float:
        return self.positive_count / max(1, self.attempt_count)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

# Sample-size safety thresholds
MIN_EVIDENCE_FAN_TOPIC = 5
MIN_EVIDENCE_FAN = 5
MIN_EVIDENCE_CREATOR_TOPIC = 10
MIN_EVIDENCE_CREATOR = 10
MIN_EVIDENCE_SAFE_DEFAULT = 0

def is_evidence_sufficient(ev: ExtendedEvidence, threshold: int = 5) -> bool:
    return ev.attempt_count >= threshold

# Confidence interval / uncertainty via Beta posterior (§9)
def beta_uncertainty(ev: ExtendedEvidence) -> float:
    """Deterministic uncertainty via Beta posterior std dev. Bounded 0..0.5."""
    a = ev.positive_count + 1
    b = (ev.attempt_count - ev.positive_count) + 1
    # Beta variance = a*b / ((a+b)^2 * (a+b+1))
    total = a + b
    try:
        var = (a * b) / ((total * total) * (total + 1))
        std = math.sqrt(var)
        # clamp
        return max(0.02, min(0.5, std))
    except Exception:
        return 0.5

def wilson_uncertainty(ev: ExtendedEvidence) -> float:
    # fallback simple
    return 1.0 / math.sqrt(ev.attempt_count + 1) * 0.5

def estimated_performance(ev: ExtendedEvidence) -> float:
    """Estimated performance = positive_rate adjusted toward 0.5 when uncertain."""
    pr = ev.positive_rate
    unc = beta_uncertainty(ev)
    # shrink toward prior 0.5 proportionally to uncertainty
    return pr * (1 - unc) + 0.5 * unc

def decayed_confidence(ev: ExtendedEvidence, days_since: float) -> float:
    return ev.confidence * math.exp(-days_since / 30.0)

def days_since_last(ev: ExtendedEvidence) -> float:
    if not ev.last_used:
        return 30.0
    try:
        dt = datetime.fromisoformat(ev.last_used.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        return max(0.0, delta)
    except Exception:
        return 30.0

# Strategy score (§21) — explainable, bounded 0..1
def strategy_score(
    ev: ExtendedEvidence,
    *,
    fatigue_penalty: float = 0.0,
    topic_relevance: float = 0.0,
    recency_days: float | None = None,
) -> float:
    """Deterministic bounded score. Positive evidence weighted, negative suppressed, purchase boosted, uncertainty penalized, fatigue reduced."""
    if ev.attempt_count == 0:
        return 0.30  # safe prior
    ds = recency_days if recency_days is not None else days_since_last(ev)
    decay = math.exp(-ds / 30.0)
    base = ev.positive_rate * decay
    # purchase bonus (normalized)
    purchase_bonus = min(0.2, ev.purchase_count * 0.05)
    # negative penalty
    neg_rate = ev.negative_count / max(1, ev.attempt_count)
    neg_penalty = neg_rate * 0.3
    # uncertainty discount
    unc = beta_uncertainty(ev)
    # fatigue
    fat = max(0.0, min(0.5, fatigue_penalty))
    # topic relevance small boost
    topic_boost = max(0.0, min(0.1, topic_relevance * 0.1))
    raw = base + purchase_bonus - neg_penalty - unc * 0.2 - fat + topic_boost
    # bounded 0..1
    return max(0.0, min(1.0, raw))

def strategy_trace(
    *,
    objective: str,
    strategy: str,
    source: str,
    evidence: ExtendedEvidence,
    fatigue: float,
    mode: str,
) -> str:
    unc = beta_uncertainty(evidence)
    return f"OBJECTIVE={objective} STRATEGY={strategy} SOURCE={source} EVIDENCE={evidence.attempt_count}_ATTEMPTS POSITIVE_RATE={evidence.positive_rate:.2f} UNCERTAINTY={unc:.2f} FATIGUE={fatigue:.2f} MODE={mode}"


# ── Exploration vs Exploitation (§10, §11) ──────────────────────────────

class StrategyMode(str, enum.Enum):
    EXPLORE = "explore"
    EXPLOIT = "exploit"
    SAFE_DEFAULT = "safe_default"

EXPLORATION_RATE_DEFAULT = 0.10  # bounded 10%
AUTHORITY_GATES = frozenset(["SAFETY", "HUMAN_HANDOFF", "AFTERCARE", "OBJECTION", "DIRECT_PURCHASE_REQUEST", "COMMERCE_AUTHORITY", "COOLDOWN", "REJECTION", "CREATOR_ISOLATION"])

def should_explore(ev: ExtendedEvidence | None, exploration_rate: float = EXPLORATION_RATE_DEFAULT) -> bool:
    if ev is None or ev.attempt_count < 5:
        return True
    unc = beta_uncertainty(ev)
    # promising but uncertain -> controlled exploration
    if unc > 0.2 and ev.attempt_count < 20:
        # exploration budget limits
        return exploration_rate > 0.0
    return False

def exploration_budget_ok(exposures_last_n: int, total_last_n: int, rate: float = EXPLORATION_RATE_DEFAULT) -> bool:
    if total_last_n == 0:
        return True
    actual = exposures_last_n / max(1, total_last_n)
    return actual < rate

# Deterministic strategy selection with hierarchy, uncertainty, fatigue, safety
def select_strategy_adaptive(
    evidence_map: dict[str, ExtendedEvidence],
    eligible: list[str],
    *,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle_stage: str | None = None,
    objective: str | None = None,
    # hierarchical evidence maps (optional)
    fan_topic_evidence: dict[str, ExtendedEvidence] | None = None,
    fan_evidence: dict[str, ExtendedEvidence] | None = None,
    creator_topic_evidence: dict[str, ExtendedEvidence] | None = None,
    creator_evidence: dict[str, ExtendedEvidence] | None = None,
    # fatigue
    fatigue_map: dict[str, float] | None = None,
    # exploration control
    exploration_rate: float = EXPLORATION_RATE_DEFAULT,
    recent_exposures: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Returns (strategy, source, mode). Never overrides authority gates."""
    if not eligible:
        return "RELATIONSHIP_BUILD", "SAFE_DEFAULT", StrategyMode.SAFE_DEFAULT.value

    # Authority gate check — if objective is safety-critical, we must not delegate to exploration
    if objective and objective.upper() in AUTHORITY_GATES:
        # still select but mark as SAFE_DEFAULT (no learning override)
        return eligible[0], "SAFE_DEFAULT", StrategyMode.SAFE_DEFAULT.value

    fatigue_map = fatigue_map or {}

    # Helper to get hierarchical evidence for a strategy
    def _hierarchical_evidence(strategy: str) -> tuple[ExtendedEvidence, str]:
        # Try fan+topic first
        key_topic = f"{strategy}:{topic}" if topic else None
        key_pf = f"{strategy}:{product_family}" if product_family else None
        key_lc = f"{strategy}:{lifecycle_stage}" if lifecycle_stage else None

        # 1. FAN_TOPIC_HISTORY
        if fan_topic_evidence is not None and key_topic and key_topic in fan_topic_evidence:
            ev = fan_topic_evidence[key_topic]
            if is_evidence_sufficient(ev, MIN_EVIDENCE_FAN_TOPIC):
                return ev, "FAN_TOPIC_HISTORY"
        # Also check evidence_map with composite key (for test harness)
        if key_topic and key_topic in evidence_map and is_evidence_sufficient(evidence_map[key_topic], MIN_EVIDENCE_FAN_TOPIC):
            return evidence_map[key_topic], "FAN_TOPIC_HISTORY"
        # product family specificity
        if key_pf and key_pf in evidence_map and is_evidence_sufficient(evidence_map[key_pf], MIN_EVIDENCE_FAN_TOPIC):
            return evidence_map[key_pf], "FAN_TOPIC_HISTORY"
        if key_lc and key_lc in evidence_map and is_evidence_sufficient(evidence_map[key_lc], MIN_EVIDENCE_FAN):
            return evidence_map[key_lc], "FAN_TOPIC_HISTORY"

        # 2. FAN_HISTORY
        if fan_evidence is not None and strategy in fan_evidence and is_evidence_sufficient(fan_evidence[strategy], MIN_EVIDENCE_FAN):
            return fan_evidence[strategy], "FAN_HISTORY"
        if strategy in evidence_map and is_evidence_sufficient(evidence_map[strategy], MIN_EVIDENCE_FAN):
            return evidence_map[strategy], "FAN_HISTORY"

        # 3. CREATOR_TOPIC_HISTORY
        if creator_topic_evidence is not None and key_topic and key_topic in creator_topic_evidence:
            ev = creator_topic_evidence[key_topic]
            if is_evidence_sufficient(ev, MIN_EVIDENCE_CREATOR_TOPIC):
                return ev, "CREATOR_TOPIC_HISTORY"
        # 4. CREATOR_HISTORY
        if creator_evidence is not None and strategy in creator_evidence and is_evidence_sufficient(creator_evidence[strategy], MIN_EVIDENCE_CREATOR):
            return creator_evidence[strategy], "CREATOR_HISTORY"
        if strategy in evidence_map and is_evidence_sufficient(evidence_map[strategy], MIN_EVIDENCE_CREATOR):
            # fallback: if generic evidence exists but not enough for fan, use creator level
            return evidence_map[strategy], "CREATOR_HISTORY"

        # 5. SAFE_DEFAULT — insufficient evidence at any level
        return evidence_map.get(strategy, ExtendedEvidence()), "SAFE_DEFAULT"

    # If no evidence at all, safe default
    if not evidence_map or all(e.attempt_count == 0 for e in evidence_map.values()):
        # check hierarchical maps too
        has_any = any(
            m and any(v.attempt_count > 0 for v in m.values())
            for m in [fan_topic_evidence, fan_evidence, creator_topic_evidence, creator_evidence]
        )
        if not has_any:
            return eligible[0], "SAFE_DEFAULT", StrategyMode.SAFE_DEFAULT.value

    # Score candidates
    best = None
    best_score = -1
    best_ev = ExtendedEvidence()
    best_source = "SAFE_DEFAULT"
    scores: dict[str, float] = {}

    for strat in eligible:
        ev, source = _hierarchical_evidence(strat)
        # If source is SAFE_DEFAULT and evidence insufficient, use exploration prior
        if source == "SAFE_DEFAULT" and ev.attempt_count < 5:
            # prioritize under-observed safe strategies
            # need to avoid large scan penalty; we treat score as 0.3 + small exploration bonus
            fatigue = fatigue_map.get(strat, 0.0)
            score = 0.3 - fatigue
            # exploration candidate
        else:
            fatigue = fatigue_map.get(strat, 0.0)
            ds = days_since_last(ev)
            score = strategy_score(ev, fatigue_penalty=fatigue, recency_days=ds)
        scores[strat] = score
        if score > best_score:
            best_score = score
            best = strat
            best_ev = ev
            best_source = source

    if best is None:
        best = eligible[0]
        best_source = "SAFE_DEFAULT"
        best_ev = ExtendedEvidence()

    # Determine mode
    # Insufficient evidence → explore safely among eligible safe low-risk under-observed
    if best_ev.attempt_count < 5:
        # Check exploration budget if we have recent exposures
        if recent_exposures is not None:
            explore_count = sum(1 for e in recent_exposures[-10:] if e.get("mode") == "explore")
            if not exploration_budget_ok(explore_count, 10, exploration_rate):
                return best, best_source, StrategyMode.EXPLOIT.value
        # Find least-observed eligible safe strategy
        # Never explore by changing price/product — only strategy wording
        least = min(eligible, key=lambda s: evidence_map.get(s, ExtendedEvidence()).attempt_count if s in evidence_map else 0)
        if evidence_map.get(least, ExtendedEvidence()).attempt_count < 2 and least != best:
            return least, "EXPLORATION", StrategyMode.EXPLORE.value
        return best, best_source if best_source != "SAFE_DEFAULT" else "EXPLORATION", StrategyMode.EXPLORE.value

    # Strong evidence → exploit
    unc = beta_uncertainty(best_ev)
    if best_ev.attempt_count >= 10 and unc < 0.15 and best_ev.positive_rate > 0.6:
        return best, best_source, StrategyMode.EXPLOIT.value

    # Uncertain but promising → controlled exploration
    if unc > 0.2 and best_ev.attempt_count < 20:
        if exploration_rate > 0 and exploration_budget_ok(1, 10, exploration_rate):
            return best, best_source, StrategyMode.EXPLORE.value
        return best, best_source, StrategyMode.EXPLOIT.value

    # Negative evidence suppress
    if best_ev.negative_count > best_ev.positive_count * 1.5 and best_ev.attempt_count >= 5:
        # suppressed — try next best non-negative
        sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        for strat, sc in sorted_candidates[1:]:
            ev2, src2 = _hierarchical_evidence(strat)
            if ev2.negative_count <= ev2.positive_count:
                return strat, src2, StrategyMode.EXPLOIT.value
        return best, best_source, StrategyMode.EXPLOIT.value

    return best, best_source, StrategyMode.EXPLOIT.value


# ── Strategy Fatigue (§12) ───────────────────────────────────────────────

def compute_fatigue(recent_exposures: list[dict[str, Any]], strategy: str, window: int = 5) -> float:
    """Detect same strategy repeated excessively in last window."""
    if not recent_exposures:
        return 0.0
    last_n = recent_exposures[-window:]
    count = sum(1 for e in last_n if e.get("strategy_family") == strategy or e.get("strategy") == strategy or e.get("strategy_variant") == strategy)
    if count >= 3:
        # fatigue 0.10 per excess repeat
        return min(0.5, (count - 2) * 0.15)
    return 0.0

def fatigue_penalty_map(recent_exposures: list[dict[str, Any]], eligible: list[str]) -> dict[str, float]:
    return {s: compute_fatigue(recent_exposures, s) for s in eligible}

# Response mode / question pattern / product family fatigue helpers
def is_response_mode_fatigued(recent_exposures: list[dict[str, Any]], mode: str, threshold: int = 3) -> bool:
    if not recent_exposures:
        return False
    cnt = sum(1 for e in recent_exposures[-5:] if e.get("response_mode") == mode)
    return cnt >= threshold

def is_question_pattern_fatigued(recent_exposures: list[dict[str, Any]], threshold: int = 3) -> bool:
    if not recent_exposures:
        return False
    cnt = sum(1 for e in recent_exposures[-5:] if e.get("question_policy") and e["question_policy"] != "NO_QUESTION")
    return cnt >= threshold

def is_product_family_fatigued(recent_exposures: list[dict[str, Any]], family: str, threshold: int = 2) -> bool:
    if not recent_exposures or not family:
        return False
    cnt = sum(1 for e in recent_exposures[-5:] if e.get("product_family") == family)
    return cnt >= threshold


# ── Purchase Attribution (§16, §17) ─────────────────────────────────────

class AttributionType(str, enum.Enum):
    DIRECT = "direct"
    ASSISTED = "assisted"
    ORGANIC = "organic"
    UNKNOWN = "unknown"

def attribute_purchase(
    *,
    strategy_exposure_time: datetime | None,
    purchase_time: datetime | None,
    transaction_evidence: bool,
    offer_created_time: datetime | None = None,
) -> str:
    """Deterministic attribution window. Only when transaction evidence exists."""
    if not transaction_evidence:
        return AttributionType.UNKNOWN.value
    if strategy_exposure_time is None or purchase_time is None:
        return AttributionType.UNKNOWN.value
    # ensure tz aware
    for dt in (strategy_exposure_time, purchase_time):
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    delta_h = (purchase_time - strategy_exposure_time).total_seconds() / 3600
    if delta_h < 0:
        return AttributionType.UNKNOWN.value
    if delta_h <= 24:
        return AttributionType.DIRECT.value
    if delta_h <= 24 * 7:
        return AttributionType.ASSISTED.value
    return AttributionType.ORGANIC.value

def has_valid_purchase_evidence(transaction_id: str | None, dropfans_record: bool) -> bool:
    """Purchase must originate from DropFans transaction/webhook authority, not conversational text."""
    if not transaction_id:
        return False
    if not dropfans_record:
        return False
    return True

# Ensure UNKNOWN also returns value directly
ATTRIBUTION_UNKNOWN = AttributionType.UNKNOWN.value


# ── Lifecycle Value (§18) ────────────────────────────────────────────────

LIFECYCLE_STAGES = [
    "RELATIONSHIP_BUILD", "CONTINUE_TOPIC", "EXPLORE_INTEREST", "DEEPEN_DESIRE",
    "QUALIFY", "PRESENT_OFFER", "COMPLETE_PURCHASE", "AFTERCARE", "RE_ENGAGE"
]

def lifecycle_specific_outcome_weights(stage: str) -> dict[str, float]:
    """Same strategy may have different value at different stages. Example: question strategy excellent at RELATIONSHIP_BUILD but harmful at PRESENT_OFFER."""
    base = OUTCOME_WEIGHTS.copy()
    sl = stage.upper()
    if sl == "RELATIONSHIP_BUILD":
        base[CanonicalOutcome.QUESTION_ANSWERED.value] = 3.0
        base[CanonicalOutcome.POSITIVE_ENGAGEMENT.value] = 3.0
        base[CanonicalOutcome.PURCHASE.value] = 5.0  # less valuable early
    elif sl == "PRESENT_OFFER":
        base[CanonicalOutcome.QUESTION_ANSWERED.value] = -1.0  # harmful
        base[CanonicalOutcome.PURCHASE.value] = 12.0
        base[CanonicalOutcome.REJECTION.value] = -6.0
    elif sl == "AFTERCARE":
        base[CanonicalOutcome.AFTERCARE_ENGAGEMENT.value] = 8.0
        base[CanonicalOutcome.PURCHASE.value] = -2.0  # upsell during aftercare harmful
    return base

def stage_for_objective(objective: str) -> str:
    mapping = {
        "relationship_build": "RELATIONSHIP_BUILD",
        "continue_topic": "CONTINUE_TOPIC",
        "explore_interest": "EXPLORE_INTEREST",
        "deepen_desire": "DEEPEN_DESIRE",
        "qualify": "QUALIFY",
        "present_offer": "PRESENT_OFFER",
        "complete_purchase": "COMPLETE_PURCHASE",
        "aftercare": "AFTERCARE",
        "re_engage": "RE_ENGAGE",
        "follow_up_open_loop": "CONTINUE_TOPIC",
        "handle_objection": "QUALIFY",
        "learn_preference": "EXPLORE_INTEREST",
        "human_handoff": "RE_ENGAGE",
        "wait": "RE_ENGAGE",
    }
    return mapping.get(objective.lower(), "RELATIONSHIP_BUILD")


# ── Relationship & Commerce Quality Metrics (§19, §20) ─────────────────

def compute_relationship_metrics(events: list[dict[str, Any]]) -> dict[str, float]:
    """Deterministic relationship signals, not revenue only."""
    if not events:
        return {"reply_rate": 0.0, "continuation": 0.0, "positive_rate": 0.0, "return_rate": 0.0}
    total = len(events)
    replies = sum(1 for e in events if e.get("outcome") not in ("no_signal", "rejection", "cooldown"))
    positives = sum(1 for e in events if e.get("outcome") in ("positive_engagement", "topic_continuation", "desire_increase", "interest_increase"))
    continuations = sum(1 for e in events if e.get("outcome") in ("topic_continuation", "positive_engagement"))
    returns = sum(1 for e in events if e.get("outcome") in ("aftercare_engagement", "repeat_purchase"))
    return {
        "reply_rate": round(replies / max(1, total), 3),
        "continuation": round(continuations / max(1, total), 3),
        "positive_rate": round(positives / max(1, total), 3),
        "return_rate": round(returns / max(1, total), 3),
    }

def compute_commerce_metrics(offers: list[dict[str, Any]]) -> dict[str, float]:
    """Track offer→purchase funnel quality."""
    if not offers:
        return {"offer_to_purchase": 0.0, "purchase_to_aftercare": 0.0, "aftercare_to_repeat": 0.0}
    created = sum(1 for o in offers if o.get("state") in ("pending", "clicked", "purchased", "declined", "expired"))
    purchased = sum(1 for o in offers if o.get("state") == "purchased")
    aftercare = sum(1 for o in offers if o.get("aftercare_status") in ("pending", "sent", "completed"))
    repeat = sum(1 for o in offers if o.get("is_repeat"))
    return {
        "offer_to_purchase": round(purchased / max(1, created), 3),
        "purchase_to_aftercare": round(aftercare / max(1, purchased), 3) if purchased else 0.0,
        "aftercare_to_repeat": round(repeat / max(1, aftercare), 3) if aftercare else 0.0,
    }

def detect_commerce_violations(events: list[dict[str, Any]]) -> list[str]:
    violations = []
    for e in events:
        if e.get("invalid_offer_attempt"):
            violations.append("invalid_offer")
        if e.get("unauthorized_price_attempt"):
            violations.append("unauthorized_price")
        if e.get("wrong_product_attempt"):
            violations.append("wrong_product")
        if e.get("commerce_scoring_failure"):
            violations.append("commerce_scoring_failure")
        if e.get("dlq_event"):
            violations.append("dlq")
    return violations


# ── Regression Detection (§23) ──────────────────────────────────────────

def detect_regression(current: dict[str, float], baseline: dict[str, float], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """Simple bounded thresholds and rolling comparisons."""
    thresholds = thresholds or {
        "conversion_decline": 0.20,
        "engagement_decline": 0.15,
        "rejection_increase": 0.25,
        "cooldown_increase": 0.30,
    }
    regressions: list[str] = []
    # conversion decline
    if baseline.get("conversion", 0) > 0:
        if current.get("conversion", 0) < baseline["conversion"] * (1 - thresholds["conversion_decline"]):
            regressions.append("conversion_decline")
    if baseline.get("engagement", 0) > 0:
        if current.get("engagement", 0) < baseline["engagement"] * (1 - thresholds["engagement_decline"]):
            regressions.append("engagement_decline")
    if baseline.get("rejection_rate", 0) > 0:
        if current.get("rejection_rate", 0) > baseline["rejection_rate"] * (1 + thresholds["rejection_increase"]):
            regressions.append("rejection_increase")
    if baseline.get("cooldown_rate", 0) > 0:
        if current.get("cooldown_rate", 0) > baseline["cooldown_rate"] * (1 + thresholds["cooldown_increase"]):
            regressions.append("cooldown_increase")
    # also simple absolute drops
    for k in ("conversion", "engagement"):
        if k in current and k in baseline and baseline[k] > 0.1:
            if current[k] < baseline[k] - 0.1:
                if f"{k}_decline" not in regressions:
                    regressions.append(f"{k}_decline")
    return {"is_regression": len(regressions) > 0, "reasons": regressions, "current": current, "baseline": baseline}


# ── Experiment Contract (§24, §25, §26, §27, §28) ─────────────────────

@dataclass
class Experiment:
    experiment_id: str
    creator_id: int
    strategy_family: str
    strategy_variant: str | None = None
    allocation: float = 0.10  # 10% default
    eligibility: str = "all"  # e.g., "warm_fans", "all"
    start_time: str | None = None
    end_time: str | None = None
    status: str = "active"  # active | paused | disabled | expired

    def is_active(self, now: datetime | None = None) -> bool:
        if self.status in ("disabled", "paused"):
            return False
        now = now or datetime.now(timezone.utc)
        try:
            if self.start_time:
                st = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if now < st:
                    return False
            if self.end_time:
                et = datetime.fromisoformat(self.end_time.replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                if now > et:
                    return False
        except Exception:
            pass
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def deterministic_assignment(creator_id: int, user_id: int, experiment_id: str) -> float:
    """hash(creator_id + user_id + experiment_id) stable 0..1"""
    raw = f"{creator_id}:{user_id}:{experiment_id}".encode()
    h = hashlib.sha256(raw).hexdigest()
    # use first 8 hex chars → 0..2^32
    val = int(h[:8], 16) / (2**32)
    return val

def assign_variant(creator_id: int, user_id: int, experiment: Experiment) -> str:
    """CONTROL vs EXPERIMENT deterministic stable."""
    if not experiment.is_active():
        return "CONTROL"
    bucket = deterministic_assignment(creator_id, user_id, experiment.experiment_id)
    if bucket < experiment.allocation:
        return "EXPERIMENT"
    return "CONTROL"

def experiment_safe_to_apply(experiment: Experiment, proposed_change: dict[str, Any]) -> tuple[bool, str]:
    """Experiment may change only conversation strategy/response mode/question policy/non-authoritative wording."""
    forbidden_keys = {"price", "purchase_url", "product_id", "dropfans_offer", "creator_isolation", "purchased_exclusion", "aftercare_gate", "cooldown", "dlq", "dedup", "telegram_identity", "commerce_authority"}
    for k in proposed_change:
        if k.lower() in forbidden_keys or "price" in k.lower() or "purchase" in k.lower():
            return False, f"forbidden_change:{k}"
    allowed = {"conversation_strategy", "response_mode", "question_policy", "wording", "strategy_family", "strategy_variant"}
    # if any key not in allowed, but not forbidden, still allow if it's strategy-related
    for k in proposed_change:
        if k not in allowed and k.lower() not in forbidden_keys:
            # allow strategy-ish keys
            if "strategy" in k.lower() or "response" in k.lower() or "question" in k.lower() or "wording" in k.lower():
                continue
            return False, f"unexpected_change:{k}"
    return True, "safe"

# In-memory experiment registry (for tests). Production would use user_profiles JSONB per creator.
_experiment_registry: dict[str, Experiment] = {}

def register_experiment(exp: Experiment) -> None:
    _experiment_registry[exp.experiment_id] = exp

def get_experiment(experiment_id: str) -> Experiment | None:
    return _experiment_registry.get(experiment_id)

def clear_experiments() -> None:
    _experiment_registry.clear()

def disable_experiment(experiment_id: str) -> None:
    if experiment_id in _experiment_registry:
        _experiment_registry[experiment_id].status = "disabled"

# DB-backed experiment via user_profiles (bounded)
async def persist_experiment(exp: Experiment) -> bool:
    try:
        from db.postgres import get_user_profile, update_user_profile
        # Use a sentinel user_id 0 for creator-level storage? Instead store per creator in a global key via user_profiles of creator?
        # We reuse user_profiles with user_id = creator_id + offset to avoid new table: store in facts['experiments_by_creator'][creator_id]
        # For simplicity, store in creator's own profile bucket via a synthetic user_id = -creator_id
        synthetic_uid = -abs(exp.creator_id)  # negative sentinel
        try:
            facts = await get_user_profile(synthetic_uid)
        except Exception:
            facts = {}
        by_creator = facts.get("experiments_by_creator", {})
        by_creator[exp.experiment_id] = exp.to_dict()
        facts["experiments_by_creator"] = by_creator
        await update_user_profile(synthetic_uid, facts)
        register_experiment(exp)
        return True
    except Exception:
        register_experiment(exp)
        return False


# ── Authority Hierarchy Enforcement (§44) ───────────────────────────────

AUTHORITY_HIERARCHY = [
    "SAFETY_HUMAN_HANDOFF",
    "IDENTITY_CREATOR_ISOLATION",
    "TRUTHFULNESS",
    "PURCHASE_TRANSACTION_AUTHORITY",
    "AFTERCARE",
    "OBJECTION_RECOVERY",
    "EXPLICIT_FAN_REQUEST",
    "CONVERSATION_INTELLIGENCE",
    "COMMERCE_READINESS",
    "STRATEGY_LEARNING",
    "EXPERIMENTATION",
    "LLM_WORDING",
]

def is_strategy_allowed(objective: str, aftercare_active: bool, is_on_cooldown: bool, has_objection: bool, is_handoff: bool) -> tuple[bool, str]:
    """Check if strategy optimization is allowed to influence this turn."""
    if is_handoff:
        return False, "handoff"
    if aftercare_active:
        return False, "aftercare"
    if has_objection:
        return False, "objection"
    if is_on_cooldown and objective.lower() == "present_offer":
        return False, "cooldown"
    # explicit DIRECT_REQUEST etc. are handled via objective gate before strategy selection
    return True, "allowed"

def validate_no_authority_bypass(proposed_strategy: str | None, objective: str, aftercare_active: bool) -> bool:
    """Ensure learning never causes upsell during aftercare or before aftercare completion."""
    if aftercare_active and proposed_strategy and "offer" in proposed_strategy.lower():
        return False
    return True


# ── Bounded retention helpers (§32) ─────────────────────────────────────

def prune_by_retention(items: list[dict[str, Any]], max_items: int = 50, max_age_days: int = 30, timestamp_key: str = "timestamp") -> list[dict[str, Any]]:
    """Aggregation/decay/bounded JSONB — keep most recent, prune old."""
    if len(items) <= max_items:
        # still check age
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        filtered = []
        for it in items:
            ts_raw = it.get(timestamp_key)
            if not ts_raw:
                filtered.append(it)
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    filtered.append(it)
            except Exception:
                filtered.append(it)
        # if all old, keep most recent max_items
        if not filtered and items:
            return items[-max_items:]
        return filtered[-max_items:] if len(filtered) > max_items else filtered
    # over limit — keep most recent
    pruned = items[-max_items:]
    # also apply age pruning but never prune to empty
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    aged = []
    for it in pruned:
        ts_raw = it.get(timestamp_key)
        if not ts_raw:
            aged.append(it)
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                aged.append(it)
        except Exception:
            aged.append(it)
    return aged if aged else pruned


# ── Re-engagement measurement (§36) ─────────────────────────────────────

@dataclass
class ReengagementMetrics:
    eligible: int = 0
    scheduled: int = 0
    sent: int = 0
    replied: int = 0
    positive: int = 0
    ignored: int = 0
    rejected: int = 0
    purchased: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

def compute_reengagement_rate(m: ReengagementMetrics) -> dict[str, float]:
    if m.sent == 0:
        return {"reply_rate": 0.0, "positive_rate": 0.0, "purchase_rate": 0.0}
    return {
        "reply_rate": round(m.replied / max(1, m.sent), 3),
        "positive_rate": round(m.positive / max(1, m.sent), 3),
        "purchase_rate": round(m.purchased / max(1, m.sent), 3),
    }


# ── Security: fan text cannot manipulate system (§43) ────────────────────

_MANIPULATION_PATTERNS = [
    re.compile(r"mark.*successful", re.I),
    re.compile(r"strategy.*evidence", re.I),
    re.compile(r"purchase.*status", re.I),
    re.compile(r"experiment.*assignment", re.I),
]

def is_fan_manipulation_attempt(text: str) -> bool:
    for pat in _MANIPULATION_PATTERNS:
        if pat.search(text):
            return True
    return False

def sanitize_fan_input_for_learning(text: str) -> str:
    """Fan text must never directly set evidence; only system observations may."""
    # Always classify via deterministic observation, never trust fan claim
    return text  # placeholder — caller must use classify_canonical_outcome with system state, not fan claim


# ── Single-pass verification (§30, X) ────────────────────────────────────

def verify_single_pass(calls: dict[str, int]) -> tuple[bool, str]:
    """Prove 1 signal + 1 Qwen + 1 scoring and 0 new LLM calls."""
    if calls.get("extract_commerce_signals", 0) != 1:
        return False, f"expected 1 signal, got {calls.get('extract_commerce_signals', 0)}"
    if calls.get("qwen", 0) != 1:
        return False, f"expected 1 Qwen, got {calls.get('qwen', 0)}"
    if calls.get("scoring", 0) != 1:
        return False, f"expected 1 scoring, got {calls.get('scoring', 0)}"
    if calls.get("additional_llm", 0) != 0:
        return False, f"expected 0 additional LLM, got {calls.get('additional_llm', 0)}"
    return True, "single_pass_ok"

# ── Helper: creator isolation check (§33) ─────────────────────────────────

def ensure_creator_isolation(creator_id: int, evidence_owner_creator_id: int) -> bool:
    return creator_id == evidence_owner_creator_id

# ── Exported for tests ───────────────────────────────────────────────────
__all__ = [
    "CanonicalOutcome", "OUTCOME_WEIGHTS", "outcome_strength",
    "ConversationObservation", "build_observation",
    "StrategyExposure", "make_exposure", "record_exposure_memory", "get_exposures_memory", "clear_exposures_memory", "persist_exposure", "attributable_exposure_for_generation",
    "classify_canonical_outcome", "classify_outcome_compat",
    "ExtendedEvidence", "is_evidence_sufficient", "beta_uncertainty", "estimated_performance", "decayed_confidence", "strategy_score", "strategy_trace",
    "StrategyMode", "should_explore", "exploration_budget_ok", "select_strategy_adaptive",
    "compute_fatigue", "fatigue_penalty_map", "is_response_mode_fatigued", "is_question_pattern_fatigued", "is_product_family_fatigued",
    "attribute_purchase", "has_valid_purchase_evidence",
    "lifecycle_specific_outcome_weights", "stage_for_objective",
    "compute_relationship_metrics", "compute_commerce_metrics", "detect_commerce_violations",
    "detect_regression",
    "Experiment", "deterministic_assignment", "assign_variant", "experiment_safe_to_apply", "register_experiment", "get_experiment", "clear_experiments", "disable_experiment", "persist_experiment",
    "is_strategy_allowed", "validate_no_authority_bypass",
    "prune_by_retention",
    "ReengagementMetrics", "compute_reengagement_rate",
    "is_fan_manipulation_attempt",
    "verify_single_pass",
    "ensure_creator_isolation",
]
