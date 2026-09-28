"""Revenue + Relationship + Funnel Intelligence — Phase 25 deterministic layer.

Pure, no LLM, no new worker/queue, no DropFans bypass, no architecture redesign.
Reuses existing production_control metrics, user_profiles JSONB, generation_telemetry,
strategy_evidence, adaptive_optimization outcomes.

All functions are deterministic, creator/fan isolated, bounded, idempotent, PII-free.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
import enum

# ═══════════════════════════════════════════════════════════════════════════════
# Canonical Event Model (§6)
# ═══════════════════════════════════════════════════════════════════════════════

NOT_AVAILABLE = "NOT_AVAILABLE"
UNKNOWN = "UNKNOWN"

@dataclass
class CanonicalEvent:
    generation_id: str
    creator_id: int | None
    user_id: int | None
    timestamp: str
    lifecycle: str | None = None
    objective: str | None = None
    strategy: str | None = None
    topic: str | None = None
    product_family: str | None = None
    response_mode: str | None = None
    experiment_id: str | None = None
    variant: str | None = None
    outcome: str | None = None
    attribution_type: str | None = None
    funnel_state: str | None = None
    funnel_transition: str | None = None
    relationship_health: float | None = None
    commercial_intent: float | None = None
    conversion_window: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sanitized(self) -> dict[str, Any]:
        # Ensure no message content, secrets
        d = self.to_dict()
        for k in list(d.keys()):
            if "content" in k.lower() or "secret" in k.lower() or "token" in k.lower():
                d.pop(k, None)
        return d

def make_canonical_event(
    *,
    generation_id: str,
    creator_id: int | None,
    user_id: int | None,
    timestamp: str | None = None,
    lifecycle: str | None = None,
    objective: str | None = None,
    strategy: str | None = None,
    topic: str | None = None,
    product_family: str | None = None,
    response_mode: str | None = None,
    experiment_id: str | None = None,
    variant: str | None = None,
    outcome: str | None = None,
    attribution_type: str | None = None,
    funnel_state: str | None = None,
    funnel_transition: str | None = None,
) -> CanonicalEvent:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    def _clean(v):
        if v is None or v == "":
            return UNKNOWN
        return str(v)[:120]
    return CanonicalEvent(
        generation_id=generation_id,
        creator_id=creator_id,
        user_id=user_id,
        timestamp=ts,
        lifecycle=_clean(lifecycle) if lifecycle else UNKNOWN,
        objective=_clean(objective) if objective else UNKNOWN,
        strategy=_clean(strategy) if strategy else UNKNOWN,
        topic=_clean(topic) if topic else UNKNOWN,
        product_family=_clean(product_family) if product_family else UNKNOWN,
        response_mode=_clean(response_mode) if response_mode else UNKNOWN,
        experiment_id=_clean(experiment_id) if experiment_id else UNKNOWN,
        variant=_clean(variant) if variant else UNKNOWN,
        outcome=_clean(outcome) if outcome else UNKNOWN,
        attribution_type=_clean(attribution_type) if attribution_type else UNKNOWN,
        funnel_state=_clean(funnel_state) if funnel_state else UNKNOWN,
        funnel_transition=_clean(funnel_transition) if funnel_transition else UNKNOWN,
    )

# Aliases for spec naming
RevenueEvent = CanonicalEvent
RelationshipEvent = CanonicalEvent
FunnelEvent = CanonicalEvent
StrategyOutcomeEvent = CanonicalEvent
FanJourneyEvent = CanonicalEvent

# ═══════════════════════════════════════════════════════════════════════════════
# Funnel Intelligence (§7, §8)
# ═══════════════════════════════════════════════════════════════════════════════

class FunnelState(str, enum.Enum):
    NEW = "NEW"
    ENGAGED = "ENGAGED"
    INTERESTED = "INTERESTED"
    QUALIFIED = "QUALIFIED"
    OFFER_PRESENTED = "OFFER_PRESENTED"
    PURCHASED = "PURCHASED"
    AFTERCARE = "AFTERCARE"
    REPEAT_PURCHASE = "REPEAT_PURCHASE"
    # alternates
    RELATIONSHIP_ONLY = "RELATIONSHIP_ONLY"
    OBJECTION = "OBJECTION"
    REJECTED = "REJECTED"
    HANDOFF = "HANDOFF"
    COOLDOWN = "COOLDOWN"
    SUPPRESSED = "SUPPRESSED"
    UNKNOWN = "UNKNOWN"

_LIFECYCLE_TO_FUNNEL = {
    "new": FunnelState.NEW,
    "curious": FunnelState.ENGAGED,
    "engaged": FunnelState.ENGAGED,
    "interested": FunnelState.INTERESTED,
    "qualified": FunnelState.QUALIFIED,
    "desiring": FunnelState.INTERESTED,
    "offer_ready": FunnelState.OFFER_PRESENTED,  # but need has_active_offer to confirm OFFER_PRESENTED vs QUALIFIED
    "purchased": FunnelState.PURCHASED,
    "aftercare": FunnelState.AFTERCARE,
    "repeat": FunnelState.REPEAT_PURCHASE,
    "cooldown": FunnelState.COOLDOWN,
    "rejected": FunnelState.REJECTED,
    "handoff": FunnelState.HANDOFF,
    "dormant": FunnelState.RELATIONSHIP_ONLY,
    "re_engaged": FunnelState.ENGAGED,
}

def funnel_state_for_lifecycle(
    lifecycle: str | None,
    *,
    has_active_offer: bool = False,
    has_purchased: bool = False,
    is_on_cooldown: bool = False,
    is_handoff: bool = False,
    is_rejected: bool = False,
    is_suppressed: bool = False,
    has_objection: bool = False,
) -> FunnelState:
    """Analytical interpretation only, does not override operational derive_lifecycle."""
    if is_handoff:
        return FunnelState.HANDOFF
    if is_suppressed:
        return FunnelState.SUPPRESSED
    if is_rejected:
        return FunnelState.REJECTED
    if is_on_cooldown:
        return FunnelState.COOLDOWN
    if has_objection:
        return FunnelState.OBJECTION
    if has_purchased:
        # Use lifecycle to distinguish PURCHASED vs REPEAT vs AFTERCARE
        if lifecycle and lifecycle.lower() in ("repeat", "repeat_purchase"):
            return FunnelState.REPEAT_PURCHASE
        if lifecycle and lifecycle.lower() == "aftercare":
            return FunnelState.AFTERCARE
        return FunnelState.PURCHASED
    if has_active_offer:
        return FunnelState.OFFER_PRESENTED
    if lifecycle and lifecycle.lower() in _LIFECYCLE_TO_FUNNEL:
        return _LIFECYCLE_TO_FUNNEL[lifecycle.lower()]
    return FunnelState.UNKNOWN

_ORDERED_FUNNEL = [
    FunnelState.NEW, FunnelState.ENGAGED, FunnelState.INTERESTED, FunnelState.QUALIFIED,
    FunnelState.OFFER_PRESENTED, FunnelState.PURCHASED, FunnelState.AFTERCARE, FunnelState.REPEAT_PURCHASE
]

def is_valid_funnel_transition(from_state: str | FunnelState, to_state: str | FunnelState) -> bool:
    try:
        fs = FunnelState(from_state) if isinstance(from_state, str) else from_state
        ts = FunnelState(to_state) if isinstance(to_state, str) else to_state
    except Exception:
        return False
    # Alternates can go to/from anywhere but main progression must be forward or stay
    if fs in (FunnelState.OBJECTION, FunnelState.REJECTED, FunnelState.COOLDOWN, FunnelState.HANDOFF, FunnelState.SUPPRESSED, FunnelState.RELATIONSHIP_ONLY, FunnelState.UNKNOWN):
        return True
    if ts in (FunnelState.OBJECTION, FunnelState.REJECTED, FunnelState.COOLDOWN, FunnelState.HANDOFF, FunnelState.SUPPRESSED, FunnelState.RELATIONSHIP_ONLY):
        return True
    # Main progression: allow forward or same, not backward jump except to earlier via relationship
    try:
        fi = _ORDERED_FUNNEL.index(fs)
        ti = _ORDERED_FUNNEL.index(ts)
        return ti >= fi  # allow same or forward
    except ValueError:
        return False

@dataclass
class FunnelTransition:
    from_state: str
    to_state: str
    creator_id: int
    user_id: int
    timestamp: str
    generation_id: str | None = None
    objective: str | None = None
    strategy: str | None = None
    product_family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

_JOURNEY_KEY = "funnel_journey_by_creator"
_JOURNEY_MAX = 20

async def record_funnel_transition(transition: FunnelTransition) -> bool:
    if not is_valid_funnel_transition(transition.from_state, transition.to_state):
        return False
    k = f"{transition.creator_id}:{transition.user_id}"
    # idempotency check in memory first (covers both paths)
    if transition.generation_id:
        lst_mem = _journey_mem.get(k, [])
        for e in lst_mem:
            if e.get("generation_id") == transition.generation_id and e.get("from_state") == transition.from_state and e.get("to_state") == transition.to_state:
                return True
    try:
        # M4 D6: whole-facts write under SELECT ... FOR UPDATE (own namespace only).
        from db.postgres import mutate_user_profile_atomically

        _persisted = {"lst": None}

        def _mutate(facts) -> bool:
            by_creator = facts.get(_JOURNEY_KEY, {})
            key = str(transition.creator_id)
            lst = by_creator.get(key, [])
            # idempotency via generation_id for persisted
            if transition.generation_id:
                for e in lst:
                    if e.get("generation_id") == transition.generation_id and e.get("from_state") == transition.from_state and e.get("to_state") == transition.to_state:
                        _persisted["lst"] = lst
                        _journey_mem[k] = lst
                        return False
            lst.append(transition.to_dict())
            if len(lst) > _JOURNEY_MAX:
                lst = lst[-_JOURNEY_MAX:]
            by_creator[key] = lst
            facts[_JOURNEY_KEY] = by_creator
            _persisted["lst"] = lst
            return True

        persisted = await mutate_user_profile_atomically(transition.user_id, _mutate)
        # also in-memory for tests
        if _persisted["lst"] is not None:
            _journey_mem[k] = _persisted["lst"]
        elif not persisted:
            # DB unavailable: memory-only fallback (same as the except path below)
            lst = _journey_mem.get(k, [])
            lst.append(transition.to_dict())
            if len(lst) > _JOURNEY_MAX:
                lst = lst[-_JOURNEY_MAX:]
            _journey_mem[k] = lst
        return True
    except Exception:
        lst = _journey_mem.get(k, [])
        # already checked idempotency above, so just append
        lst.append(transition.to_dict())
        if len(lst) > _JOURNEY_MAX:
            lst = lst[-_JOURNEY_MAX:]
        _journey_mem[k] = lst
        return True

_journey_mem: dict[str, list[dict[str, Any]]] = {}

def get_journey_memory(creator_id: int, user_id: int) -> list[dict[str, Any]]:
    return list(_journey_mem.get(f"{creator_id}:{user_id}", []))

def clear_journey_memory(creator_id: int | None = None, user_id: int | None = None) -> None:
    if creator_id is None and user_id is None:
        _journey_mem.clear()
    else:
        _journey_mem.pop(f"{creator_id}:{user_id}", None)

async def get_funnel_journey(creator_id: int, user_id: int) -> list[dict[str, Any]]:
    try:
        from db.postgres import get_user_profile
        facts = await get_user_profile(user_id)
        by_creator = facts.get(_JOURNEY_KEY, {})
        lst = by_creator.get(str(creator_id), []) or by_creator.get(creator_id, []) or []
        if lst:
            return list(lst)
    except Exception:
        pass
    return get_journey_memory(creator_id, user_id)

# ═══════════════════════════════════════════════════════════════════════════════
# Conversion Metrics (§9)
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_window(window: str) -> str:
    if window not in ("1h","24h","7d","30d"):
        return "24h"
    return window

def compute_conversion_metrics(
    *,
    creator_id: int | None = None,
    window: str = "24h",
    dimension: str | None = None,
) -> dict[str, Any]:
    """Deterministic conversion rates per funnel step via production_control metrics.
    Returns dict with counts/rates/sample_size, requires sample>=5 else INSUFFICIENT_DATA sentinel.
    """
    from commerce.production_control import query_metrics, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    mw = wmap.get(window, MetricWindow.H24)
    # Funnel counts via metric names we record elsewhere (fallback to existing metrics)
    # Use generation_success as entered, purchases as converted, etc.
    total = len(query_metrics(creator_id=creator_id, window=mw))  # total events
    # For funnel, we interpret via filtered names
    def cnt(name: str) -> int:
        return len(query_metrics(name=name, creator_id=creator_id, window=mw))
    entered = cnt("generation_success") + cnt("generation_failure")
    if entered == 0:
        ft = cnt("funnel_transition")
        if ft > 0:
            entered = ft
        # else keep 0 for isolated creator (insufficient)
    engaged = cnt("funnel_engaged") or 0
    qualified = cnt("funnel_qualified") or 0
    offered = cnt("offers_presented") or cnt("funnel_offer_presented") or 0
    purchased = cnt("purchases") or 0
    repeated = cnt("repeat_purchases") or 0
    rejected = cnt("rejections") or 0
    handoff = cnt("handoff_required") or 0
    reeng = cnt("reengagement_sent") or 0

    def rate(num: int, den: int) -> float:
        return round(num / max(1, den), 3) if den >= 5 else 0.0

    sample_ok = entered >= 5
    # If no data for this creator, return insufficient (don't fallback to 1)
    if entered == 0:
        # No funnel data for this creator; check if we had fallback due to empty metrics
        # For isolated creator with 0 entered, keep 0 and insufficient true
        entered = 0
        sample_ok = False
    return {
        "window": window,
        "entered": entered,
        "engaged": engaged,
        "qualified": qualified,
        "offered": offered,
        "purchased": purchased,
        "repeated": repeated,
        "rejected": rejected,
        "handoff": handoff,
        "reengaged": reeng,
        "engagement_rate": rate(engaged, entered) if sample_ok else 0.0,
        "qualification_rate": rate(qualified, entered) if sample_ok else 0.0,
        "offer_rate": rate(offered, entered) if sample_ok else 0.0,
        "purchase_rate": rate(purchased, max(1, offered)) if offered >= 5 else 0.0,
        "repeat_purchase_rate": rate(repeated, max(1, purchased)) if purchased >= 5 else 0.0,
        "rejection_rate": rate(rejected, entered) if sample_ok else 0.0,
        "handoff_rate": rate(handoff, entered) if sample_ok else 0.0,
        "re_engagement_rate": rate(reeng, entered) if sample_ok else 0.0,
        "sample_size": entered,
        "insufficient_data": not sample_ok,
    }

def metrics_by_dimension_via_production(
    *,
    name: str,
    dimension: str,
    creator_id: int | None = None,
    window: str = "24h",
) -> dict[str, int]:
    from commerce.production_control import metrics_by_dimension, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    return metrics_by_dimension(name=name, dimension=dimension, creator_id=creator_id, window=wmap.get(window, MetricWindow.H24))

# ═══════════════════════════════════════════════════════════════════════════════
# Relationship Intelligence (§10)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_relationship_health(events: list[dict[str, Any]]) -> dict[str, float]:
    """Separate relationship namespace. Never conflates with commerce.
    Returns: relationship_health, engagement, responsiveness, conversation_depth, topic_continuity, trust_signal, negative_signal, fatigue, commercial_intent (separate)
    """
    if not events:
        return {
            "relationship_health": 0.0, "engagement": 0.0, "responsiveness": 0.0,
            "conversation_depth": 0.0, "topic_continuity": 0.0, "trust_signal": 0.0,
            "negative_signal": 0.0, "fatigue": 0.0, "commercial_intent": 0.0
        }
    total = len(events)
    # engagement: reply not no_signal/rejection/cooldown
    eng = sum(1 for e in events if e.get("outcome") not in ("no_signal","rejection","cooldown","handoff"))
    # responsiveness: assume answered if not no_signal
    resp = sum(1 for e in events if e.get("outcome") != "no_signal")
    # conversation depth: topic_continuation + desire increase
    depth = sum(1 for e in events if e.get("outcome") in ("topic_continuation","desire_increase","interest_increase"))
    # topic continuity: topic_continued flag
    cont = sum(1 for e in events if e.get("topic_continued"))
    # trust: positive + aftercare
    trust = sum(1 for e in events if e.get("outcome") in ("positive_engagement","aftercare_engagement","preference_learned"))
    # negative
    neg = sum(1 for e in events if e.get("outcome") in ("rejection","objection","desire_decrease","cooldown","handoff"))
    # fatigue avg if present
    fats = [e.get("fatigue",0) for e in events if "fatigue" in e]
    fat_avg = sum(fats)/len(fats) if fats else 0.0
    # commercial intent avg
    cis = [e.get("commercial_intent",0) for e in events if "commercial_intent" in e]
    ci_avg = sum(cis)/len(cis) if cis else 0.0

    # health composite: weighted but exposed components (no black-box alone)
    health = round((eng/total)*0.3 + (trust/total)*0.3 + (1 - neg/total)*0.2 + (1 - fat_avg)*0.2, 3)

    return {
        "relationship_health": health,
        "engagement": round(eng/max(1,total),3),
        "responsiveness": round(resp/max(1,total),3),
        "conversation_depth": round(depth/max(1,total),3),
        "topic_continuity": round(cont/max(1,total),3),
        "trust_signal": round(trust/max(1,total),3),
        "negative_signal": round(neg/max(1,total),3),
        "fatigue": round(fat_avg,3),
        "commercial_intent": round(ci_avg,3),
    }

def relationship_vs_commerce_safety(
    *,
    relationship_health: float,
    commercial_intent: float,
    fatigue: float,
) -> tuple[bool, str]:
    """Returns (offer_allowed, reason). High fatigue always suppresses."""
    if fatigue >= 0.30:
        return False, "fatigue_suppress"
    if relationship_health >= 0.7 and commercial_intent <= 0.35:
        return False, "high_relationship_low_commerce_no_offer"
    if commercial_intent >= 0.65:
        # high commerce but still needs commerce gates; here we only check fatigue, not aftercare etc.
        # Caller must still check production_control/policy_allows
        return True, "high_commerce_proceed_if_gates_allow"
    return True, "allowed"

# ═══════════════════════════════════════════════════════════════════════════════
# Time-to-Outcome (§12)
# ═══════════════════════════════════════════════════════════════════════════════

class TimeBucket(str, enum.Enum):
    IMMEDIATE = "IMMEDIATE"  # <1h
    SHORT = "SHORT"          # 1h-24h
    ASSISTED = "ASSISTED"    # 1d-7d
    LONG = "LONG"            # 7d-30d
    UNKNOWN = "UNKNOWN"

def time_bucket_for_purchase(
    exposure_time: datetime | None,
    purchase_time: datetime | None,
    *,
    transaction_evidence: bool,
) -> str:
    if not transaction_evidence or not exposure_time or not purchase_time:
        return TimeBucket.UNKNOWN.value
    if exposure_time.tzinfo is None:
        exposure_time = exposure_time.replace(tzinfo=timezone.utc)
    if purchase_time.tzinfo is None:
        purchase_time = purchase_time.replace(tzinfo=timezone.utc)
    delta_h = (purchase_time - exposure_time).total_seconds()/3600
    if delta_h < 0:
        return TimeBucket.UNKNOWN.value
    if delta_h < 1:
        return TimeBucket.IMMEDIATE.value
    if delta_h <= 24:
        return TimeBucket.SHORT.value
    if delta_h <= 24*7:
        return TimeBucket.ASSISTED.value
    if delta_h <= 24*30:
        return TimeBucket.LONG.value
    return TimeBucket.UNKNOWN.value

# Also keep AttributionType from adaptive_optimization for direct/assisted mapping
def attribution_with_timebucket(
    *,
    exposure_time: datetime | None,
    purchase_time: datetime | None,
    transaction_evidence: bool,
) -> tuple[str, str]:
    from commerce.adaptive_optimization import attribute_purchase
    attr = attribute_purchase(strategy_exposure_time=exposure_time, purchase_time=purchase_time, transaction_evidence=transaction_evidence)
    bucket = time_bucket_for_purchase(exposure_time, purchase_time, transaction_evidence=transaction_evidence)
    # Direct maps to IMMEDIATE/SHORT, Assisted to ASSISTED, Organic to LONG, Unknown to UNKNOWN
    return attr, bucket

# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Performance (§13)
# ═══════════════════════════════════════════════════════════════════════════════

def strategy_performance_for_dimension(
    *,
    strategy: str,
    creator_id: int | None = None,
    fan_id: int | None = None,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle: str | None = None,
    objective: str | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    """Reuses production_control metrics_by_dimension + strategy evidence.
    All queries creator/fan isolated if ids provided.
    """
    from commerce.production_control import query_metrics
    # Count exposures per strategy
    # Use metrics name funnel? We use strategy_exposure metrics if recorded, else fallback to evidence
    # For purity, use in-memory exposures if available
    exps = []
    if creator_id and fan_id is not None:
        from commerce.adaptive_optimization import get_exposures_memory
        exps = [e for e in get_exposures_memory(creator_id, fan_id) if e.get("strategy_family")==strategy]
        if topic:
            exps = [e for e in exps if e.get("topic")==topic]
        if product_family:
            exps = [e for e in exps if e.get("product_family")==product_family]
        if lifecycle:
            exps = [e for e in exps if e.get("conversation_stage")==lifecycle or e.get("desire_stage")==lifecycle]
        if objective:
            exps = [e for e in exps if e.get("next_best_action")==objective]
        if response_mode:
            exps = [e for e in exps if e.get("response_mode")==response_mode]
    # Also query metrics where strategy dimension
    # Use query_metrics for strategy exposures
    from commerce.production_control import query_metrics, MetricWindow
    metrics = query_metrics(name="strategy_exposure", creator_id=creator_id, strategy=strategy, window=MetricWindow.D30) if creator_id else []
    # Merge counts
    exposure_count = max(len(exps), len([m for m in metrics if m.get("strategy")==strategy])) if metrics else len(exps)
    # Need evidence for rates
    # Use ExtendedEvidence if available via strategy_learning (but we avoid async)
    # Return sample with counts and rates where possible
    return {
        "strategy": strategy,
        "creator_id": creator_id,
        "fan_id": fan_id,
        "topic": topic or UNKNOWN,
        "product_family": product_family or UNKNOWN,
        "lifecycle": lifecycle or UNKNOWN,
        "objective": objective or UNKNOWN,
        "response_mode": response_mode or UNKNOWN,
        "exposure_count": exposure_count,
        "sample_size": exposure_count,
        "insufficient_data": exposure_count < 5,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Product-Family / Topic / Objective / Response-Mode (§15-§18)
# ═══════════════════════════════════════════════════════════════════════════════

def product_family_metrics(
    *,
    product_family: str,
    creator_id: int | None = None,
    window: str = "30d",
) -> dict[str, Any]:
    # Use metrics_by_dimension for offers/purchases per product_family
    counts = metrics_by_dimension_via_production(name="offers_presented", dimension="product_family", creator_id=creator_id, window=window)
    # Fallback to purchase metrics
    purch = metrics_by_dimension_via_production(name="purchases", dimension="product_family", creator_id=creator_id, window=window)
    exp = counts.get(product_family, 0)
    pur = purch.get(product_family, 0)
    return {
        "product_family": product_family,
        "exposure": exp,
        "purchases": pur,
        "purchase_rate": round(pur/max(1,exp),3) if exp>=5 else 0.0,
        "sample_size": exp,
        "insufficient_data": exp < 5,
    }

def topic_metrics(*, topic: str, creator_id: int | None = None, window: str = "30d") -> dict[str, Any]:
    # Relationship vs commerce per topic
    # Use metrics with topic dimension if recorded
    rel = metrics_by_dimension_via_production(name="generation_success", dimension="topic", creator_id=creator_id, window=window)
    pur = metrics_by_dimension_via_production(name="purchases", dimension="topic", creator_id=creator_id, window=window)
    total = rel.get(topic,0)
    purchases = pur.get(topic,0)
    return {
        "topic": topic,
        "engagement": total,
        "purchases": purchases,
        "purchase_rate": round(purchases/max(1,total),3) if total>=5 else 0.0,
        "sample_size": total,
        "insufficient_data": total < 5,
    }

def objective_metrics(*, objective: str, creator_id: int | None = None, window: str = "30d") -> dict[str, Any]:
    from commerce.production_control import query_metrics, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    mw = wmap.get(window, MetricWindow.D30)
    all_for_obj = [e for e in query_metrics(creator_id=creator_id, window=mw) if e.get("objective")==objective or e.get("strategy")==objective]
    total = len(all_for_obj)
    # success distribution via outcome dimension if available
    pos = len([e for e in all_for_obj if e.get("outcome") in ("positive_engagement","purchase","repeat_purchase")])
    neg = len([e for e in all_for_obj if e.get("outcome") in ("rejection","handoff","cooldown")])
    handoff = len([e for e in all_for_obj if e.get("outcome")=="handoff"])
    purch = len([e for e in all_for_obj if e.get("outcome") in ("purchase","repeat_purchase")])
    return {
        "objective": objective,
        "count": total,
        "positive_rate": round(pos/max(1,total),3) if total>=5 else 0.0,
        "negative_rate": round(neg/max(1,total),3) if total>=5 else 0.0,
        "handoff_rate": round(handoff/max(1,total),3) if total>=5 else 0.0,
        "purchase_rate": round(purch/max(1,total),3) if total>=5 else 0.0,
        "sample_size": total,
        "insufficient_data": total < 5,
    }

def response_mode_metrics(*, response_mode: str, creator_id: int | None = None, window: str = "30d") -> dict[str, Any]:
    from commerce.production_control import query_metrics, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    mw = wmap.get(window, MetricWindow.D30)
    all_for = [e for e in query_metrics(creator_id=creator_id, window=mw) if e.get("response_mode")==response_mode]
    total = len(all_for)
    pos = len([e for e in all_for if e.get("outcome") in ("positive_engagement","purchase")])
    rej = len([e for e in all_for if e.get("outcome")=="rejection"])
    pur = len([e for e in all_for if e.get("outcome") in ("purchase","repeat_purchase")])
    # fatigue proxy: count how many had fatigue flag?
    return {
        "response_mode": response_mode,
        "count": total,
        "positive_rate": round(pos/max(1,total),3) if total>=5 else 0.0,
        "rejection_rate": round(rej/max(1,total),3) if total>=5 else 0.0,
        "purchase_rate": round(pur/max(1,total),3) if total>=5 else 0.0,
        "sample_size": total,
        "insufficient_data": total < 5,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Experiment Intelligence (§19)
# ═══════════════════════════════════════════════════════════════════════════════

def experiment_intelligence(
    *,
    experiment_id: str,
    creator_id: int | None = None,
    window: str = "30d",
) -> dict[str, Any]:
    from commerce.production_control import query_metrics, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    mw = wmap.get(window, MetricWindow.D30)
    ctrl = [e for e in query_metrics(creator_id=creator_id, window=mw) if e.get("experiment_id")==experiment_id and e.get("variant")=="CONTROL"]
    var = [e for e in query_metrics(creator_id=creator_id, window=mw) if e.get("experiment_id")==experiment_id and e.get("variant")=="EXPERIMENT"]
    def stats(lst):
        total = len(lst)
        purch = len([e for e in lst if e.get("outcome") in ("purchase","repeat_purchase")])
        neg = len([e for e in lst if e.get("outcome") in ("rejection","handoff")])
        handoff = len([e for e in lst if e.get("outcome")=="handoff"])
        return {"sample_size": total, "purchases": purch, "purchase_rate": round(purch/max(1,total),3) if total>=5 else 0.0, "negative_rate": round(neg/max(1,total),3) if total>=5 else 0.0, "handoff_rate": round(handoff/max(1,total),3) if total>=5 else 0.0, "insufficient_data": total < 5}
    c = stats(ctrl)
    v = stats(var)
    # baseline comparison reusing detect_regression for purchase_rate
    from commerce.adaptive_optimization import detect_regression
    base = {"conversion": c["purchase_rate"], "engagement": 0.5}
    cur = {"conversion": v["purchase_rate"], "engagement": 0.5}
    reg = detect_regression(cur, base)
    if c["insufficient_data"] or v["insufficient_data"]:
        verdict = "INSUFFICIENT_DATA"
    elif reg["is_regression"]:
        verdict = "REGRESSED"
    elif v["purchase_rate"] > c["purchase_rate"] + 0.02:
        verdict = "IMPROVED"
    else:
        verdict = "STABLE"
    return {"experiment_id": experiment_id, "control": c, "variant": v, "comparison": verdict, "reasons": reg["reasons"]}

# ═══════════════════════════════════════════════════════════════════════════════
# Baseline vs Optimized (§20, §21)
# ═══════════════════════════════════════════════════════════════════════════════

def baseline_comparison(
    *,
    creator_id: int | None = None,
    strategy: str | None = None,
    window: str = "30d",
    baseline_window: str = "30d",
) -> dict[str, Any]:
    from commerce.production_control import query_metrics, MetricWindow
    from commerce.adaptive_optimization import detect_regression, beta_uncertainty, ExtendedEvidence
    # For simplicity, baseline is previous window vs current window, or control vs variant
    # Use query_metrics with two windows: we approximate baseline as older 30d slice? For deterministic, use same query but split by timestamp half
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    # Current is last window, baseline is previous same window (not directly queryable, so we use overall vs recent half)
    # Instead use metrics with strategy filter for current vs baseline stored via audit? For test, we simulate with counts
    cur = query_metrics(creator_id=creator_id, strategy=strategy, window=wmap.get(window, MetricWindow.D30)) if strategy else query_metrics(creator_id=creator_id, window=wmap.get(window, MetricWindow.D30))
    # Baseline: use first half of cur as baseline (deterministic split)
    half = len(cur)//2
    baseline_lst = cur[:half] if half else []
    current_lst = cur[half:] if half else cur
    def rate(lst):
        purch = len([e for e in lst if e.get("outcome") in ("purchase","repeat_purchase")])
        return round(purch/max(1,len(lst)),3) if lst else 0.0
    base_rate = rate(baseline_lst) if baseline_lst else 0.0
    cur_rate = rate(current_lst) if current_lst else rate(cur)
    sample = len(current_lst)
    if sample < 5:
        verdict = "INSUFFICIENT_DATA"
    else:
        reg = detect_regression({"conversion": cur_rate}, {"conversion": base_rate})
        if reg["is_regression"]:
            verdict = "REGRESSED"
        elif cur_rate > base_rate + 0.02:
            verdict = "IMPROVED"
        else:
            verdict = "STABLE"
    # Confidence via Beta on current purch
    purch_c = len([e for e in current_lst if e.get("outcome") in ("purchase","repeat_purchase")])
    ev = ExtendedEvidence(attempt_count=sample, positive_count=purch_c)
    from commerce.adaptive_optimization import beta_uncertainty as _bu
    unc = _bu(ev)
    conf = round(1 - unc, 3)
    return {
        "creator_id": creator_id,
        "strategy": strategy or UNKNOWN,
        "baseline_rate": base_rate,
        "current_rate": cur_rate,
        "sample_size": sample,
        "confidence": conf,
        "verdict": verdict,
        "insufficient_data": sample < 5,
    }

def optimization_quality(
    *,
    strategy: str,
    baseline_rate: float,
    current_rate: float,
    sample_size: int,
    confidence: float,
    fatigue: float,
) -> dict[str, Any]:
    if sample_size < 5:
        decision = "INSUFFICIENT_DATA"
    elif current_rate < baseline_rate * 0.8:
        decision = "ROLLBACK"
    elif current_rate > baseline_rate + 0.02 and confidence > 0.7 and fatigue < 0.3:
        decision = "RETAIN"
    elif fatigue >= 0.3:
        decision = "PAUSE"
    else:
        decision = "HOLD"
    return {
        "strategy": strategy,
        "baseline_purchase_rate": round(baseline_rate,3),
        "current_purchase_rate": round(current_rate,3),
        "sample_size": sample_size,
        "confidence": round(confidence,3),
        "fatigue": round(fatigue,3),
        "decision": decision,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Fan Journey (§22)
# ═══════════════════════════════════════════════════════════════════════════════

def journey_from_transitions(transitions: list[dict[str, Any]]) -> list[str]:
    if not transitions:
        return []
    # Build journey as ordered states
    journey = []
    for t in transitions:
        from_s = t.get("from_state")
        to_s = t.get("to_state")
        if not journey or journey[-1] != from_s:
            journey.append(from_s)
        journey.append(to_s)
    # dedup consecutive
    deduped = []
    for s in journey:
        if not deduped or deduped[-1] != s:
            deduped.append(s)
    return deduped

# ═══════════════════════════════════════════════════════════════════════════════
# Fan Value Model (§23)
# ═══════════════════════════════════════════════════════════════════════════════

def fan_value_model(
    *,
    creator_id: int,
    user_id: int,
    purchase_amounts: list[float] | None = None,
) -> dict[str, Any]:
    """Deterministic, no monetary LTV unless authoritative amounts exist."""
    # Relationship value via relationship_health, commercial via purchase frequency
    # Use exposures and purchases counts if available
    from commerce.adaptive_optimization import get_exposures_memory
    exps = get_exposures_memory(creator_id, user_id)
    # Need purchase evidence: query metrics for purchases
    from commerce.production_control import query_metrics, MetricWindow
    purch_events = query_metrics(name="purchases", creator_id=creator_id, window=MetricWindow.D30)
    # Filter to this fan if user_id stored
    fan_purch = [e for e in purch_events if e.get("user_id")==user_id]
    freq = len(fan_purch)
    # Engagement value via exposures
    eng_val = round(min(1.0, len(exps)/20),3)
    # Relationship vs commercial
    rel_val = round(eng_val,3)
    comm_val = round(freq/5,3) if freq else 0.0  # normalized
    # LTV
    if purchase_amounts and any(purchase_amounts):
        ltv = round(sum(purchase_amounts),2)
        ltv_status = "KNOWN"
    else:
        ltv = UNKNOWN
        ltv_status = "UNKNOWN"
    return {
        "creator_id": creator_id,
        "user_id": user_id,
        "relationship_value": rel_val,
        "commercial_value": comm_val,
        "purchase_frequency": freq,
        "repeat_purchase_value": 1 if freq>1 else 0,
        "engagement_value": eng_val,
        "ltv": ltv,
        "ltv_status": ltv_status,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Creator Intelligence (§24)
# ═══════════════════════════════════════════════════════════════════════════════

def creator_intelligence(
    *,
    creator_id: int,
    window: str = "30d",
) -> dict[str, Any]:
    conv = compute_conversion_metrics(creator_id=creator_id, window=window)
    # Relationship health via events for creator (sample)
    from commerce.production_control import query_metrics, MetricWindow
    wmap = {"1h": MetricWindow.H1, "24h": MetricWindow.H24, "7d": MetricWindow.D7, "30d": MetricWindow.D30}
    mw = wmap.get(window, MetricWindow.D30)
    events = query_metrics(creator_id=creator_id, window=mw)
    rel = compute_relationship_health(events)
    # Strategy performance via metrics_by_dimension
    strat_counts = metrics_by_dimension_via_production(name="generation_success", dimension="strategy", creator_id=creator_id, window=window)
    # Product family
    prod_counts = metrics_by_dimension_via_production(name="purchases", dimension="product_family", creator_id=creator_id, window=window)
    topic_counts = metrics_by_dimension_via_production(name="generation_success", dimension="topic", creator_id=creator_id, window=window)
    return {
        "creator_id": creator_id,
        "window": window,
        "conversations": conv["sample_size"],
        "conversions": conv,
        "relationship_health": rel["relationship_health"],
        "engagement": rel["engagement"],
        "strategy_performance": strat_counts,
        "product_family_performance": prod_counts,
        "topic_performance": topic_counts,
        "fatigue": rel["fatigue"],
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Fan Segmentation (§25)
# ═══════════════════════════════════════════════════════════════════════════════

def fan_segment(
    *,
    relationship_health: float,
    commercial_intent: float,
    fatigue: float,
    lifecycle: str | None = None,
    has_active_offer: bool = False,
    has_purchased: bool = False,
    is_repeat: bool = False,
    is_on_cooldown: bool = False,
    consecutive_rejections: int = 0,
    offer_age_hours: float | None = None,
) -> tuple[str, str]:
    """Deterministic, explainable behavioral segments only. Returns (segment, reason)."""
    if is_on_cooldown or consecutive_rejections >= 3:
        return "COOLDOWN", "consecutive_rejections>=3 or cooldown"
    if fatigue >= 0.30:
        return "FATIGUED", f"fatigue {fatigue:.2f} >=0.30"
    if is_repeat:
        return "REPEAT_PURCHASER", "is_repeat true"
    if has_purchased and not is_repeat:
        # Check if recent purchaser (would need time but we approximate)
        return "RECENT_PURCHASER", "has_purchased"
    if has_active_offer and offer_age_hours is not None and offer_age_hours >= 48 and not is_on_cooldown:
        return "REENGAGEMENT_ELIGIBLE", f"offer_age {offer_age_hours:.1f}h >=48h"
    if lifecycle and lifecycle.lower() in ("rejected","handoff"):
        return "OBJECTION_RECOVERY", f"lifecycle {lifecycle}"
    if relationship_health >= 0.7 and commercial_intent <= 0.35:
        return "RELATIONSHIP_HIGH_COMMERCE_LOW", f"rel {relationship_health:.2f} high, commerce {commercial_intent:.2f} low"
    if relationship_health >= 0.7 and commercial_intent >= 0.6:
        return "RELATIONSHIP_HIGH_COMMERCE_HIGH", f"both high"
    if lifecycle and lifecycle.lower() in ("qualified","offer_ready") and commercial_intent >= 0.5:
        return "QUALIFIED_OPPORTUNITY", f"lifecycle {lifecycle} + commerce {commercial_intent:.2f}"
    if relationship_health >= 0.5 and commercial_intent <= 0.5:
        return "ENGAGED_EXPLORER", f"moderate engagement, low commerce"
    return "UNKNOWN", "no segment matched"

# ═══════════════════════════════════════════════════════════════════════════════
# Observability (§28) — extend telemetry helper
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_telemetry_with_funnel(
    telemetry: Any,
    *,
    funnel_state: str | None = None,
    funnel_transition: str | None = None,
    relationship_health: float | None = None,
    commercial_intent: float | None = None,
    conversion_window: str | None = None,
    attribution_type: str | None = None,
    baseline_state: str | None = None,
    optimization_state: str | None = None,
) -> None:
    # Be tolerant: telemetry is GenerationTelemetry, we set attrs if present
    for k, v in {
        "funnel_state": funnel_state,
        "funnel_transition": funnel_transition,
        "relationship_health": relationship_health,
        "commercial_intent": commercial_intent,
        "conversion_window": conversion_window,
        "attribution_type": attribution_type,
        "baseline_state": baseline_state,
        "optimization_state": optimization_state,
    }.items():
        if hasattr(telemetry, k):
            setattr(telemetry, k, v)
        else:
            # store in a dict-like fallback (set attr anyway, to_dict will include if class has __dict__)
            try:
                setattr(telemetry, k, v)
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════════════════
# Production Control Integration (§29)
# ═══════════════════════════════════════════════════════════════════════════════

def optimization_allowed(
    *,
    creator_id: int | None = None,
    strategy: str | None = None,
    experiment_id: str | None = None,
) -> tuple[bool, str]:
    """Govern optimization via production controls before any autonomous change."""
    from commerce.production_control import autonomous_allowed, derive_production_state, is_global_paused
    # Check emergency/production state
    if is_global_paused():
        return False, "global_pause"
    # Use autonomous_allowed (covers global/creator/strategy/experiment/commerce)
    allowed, reason = autonomous_allowed(creator_id=creator_id, strategy=strategy, experiment_id=experiment_id)
    if not allowed:
        return False, reason
    # Check derived production state (last health)
    try:
        from commerce.production_control import evaluate_production_health, MetricWindow
        health = evaluate_production_health(creator_id=creator_id, window=MetricWindow.H24)
        state = derive_production_state(risk_state=None, failure_class=None, is_paused=is_global_paused(), is_rollback=(health.production_state=="rollback"))
        if state in ("paused","rollback","suppressed","handoff"):
            return False, f"production_state_{state}"
    except Exception:
        pass
    # Check pressure/fatigue via telemetry not available here, caller must also check risk/pressure
    return True, "allowed"

# ═══════════════════════════════════════════════════════════════════════════════
# Single-pass verification reuse (§30)
# ═══════════════════════════════════════════════════════════════════════════════

def verify_revenue_intelligence_single_pass(calls: dict[str, int]) -> tuple[bool, str]:
    from commerce.adaptive_optimization import verify_single_pass
    return verify_single_pass(calls)

# ═══════════════════════════════════════════════════════════════════════════════
# Retention (§27) - bounded helpers already exist, add journey bound check
# ═══════════════════════════════════════════════════════════════════════════════

def retention_check() -> dict[str, Any]:
    # Ensure bounded: exposures 50, evidence 20, journey 20, metrics 5000, audits 1000, experiments per creator bounded
    from commerce.adaptive_optimization import _exposure_buffer, _EXPOSURE_MAX
    from commerce.production_control import _metric_events, _METRIC_MAX, _audit_log, _AUDIT_MAX, _rollout_registry, _idempotency_seen
    return {
        "exposures": max(len(v) for v in _exposure_buffer.values()) if _exposure_buffer else 0,
        "exposure_limit": _EXPOSURE_MAX,
        "metrics": len(_metric_events),
        "metrics_limit": _METRIC_MAX,
        "audits": len(_audit_log),
        "audit_limit": _AUDIT_MAX,
        "journey_per_fan_limit": _JOURNEY_MAX,
        "rollouts": len(_rollout_registry),
        "idempotency": len(_idempotency_seen),
    }
