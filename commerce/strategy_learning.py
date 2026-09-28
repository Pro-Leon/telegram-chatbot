"""Strategy Learning — deterministic, creator-scoped, bounded (Phase 19 → Phase 20 extended)."""
from __future__ import annotations
import logging
import math
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any
from dataclasses import dataclass

logger = logging.getLogger("commerce.strategy_learning")
# Phase 20 extended helpers (re-export for tests)
from commerce.adaptive_optimization import (
    beta_uncertainty as _beta_unc,
    strategy_score as _strategy_score,
    decayed_confidence,
    is_evidence_sufficient,
    compute_fatigue,
    fatigue_penalty_map,
    select_strategy_adaptive,
    ExtendedEvidence,
    StrategyMode,
    MIN_EVIDENCE_FAN,
    MIN_EVIDENCE_FAN_TOPIC,
    MIN_EVIDENCE_CREATOR,
    MIN_EVIDENCE_CREATOR_TOPIC,
    OUTCOME_WEIGHTS,
    outcome_strength,
)

@dataclass
class StrategyEvidence:
    attempt_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    purchase_count: int = 0
    last_used: str | None = None
    last_positive: str | None = None
    confidence: float = 0.5
    # Phase 20: optional dimensions (kept in dict for backward compat, not required fields)
    topic: str | None = None
    product_family: str | None = None
    lifecycle_stage: str | None = None

    @property
    def total(self) -> int:
        return self.attempt_count

    @property
    def positive_rate(self) -> float:
        return self.positive_count / max(1, self.attempt_count)

async def get_strategy_evidence(creator_id: int, user_id: int) -> dict[str, StrategyEvidence]:
    try:
        from db.postgres import get_user_profile
        facts = await get_user_profile(user_id)
        by_creator = facts.get("strategy_evidence_by_creator", {})
        raw = by_creator.get(str(creator_id), {}) or by_creator.get(creator_id, {}) or {}
        result = {}
        for k, v in raw.items():
            result[k] = StrategyEvidence(**v)
        return result
    except Exception:
        return {}

async def update_strategy_evidence(creator_id: int, user_id: int, strategy: str, outcome: str) -> None:
    try:
        from db.postgres import mutate_user_profile_atomically

        # M4 D6: whole-facts write runs under SELECT ... FOR UPDATE so other
        # creators' namespaces on the same user row cannot be clobbered.
        def _mutate(facts) -> bool:
            by_creator = facts.get("strategy_evidence_by_creator", {})
            key = str(creator_id)
            ev_dict = by_creator.get(key, {})
            ev = StrategyEvidence(**ev_dict.get(strategy, {})) if strategy in ev_dict else StrategyEvidence()
            ev.attempt_count += 1
            ev.last_used = datetime.now(timezone.utc).isoformat()
            if outcome in ("POSITIVE_ENGAGEMENT", "QUESTION_ANSWERED", "TOPIC_CONTINUED", "INTEREST_SIGNAL", "DESIRE_INCREASE", "OFFER_ACCEPTED", "PURCHASE", "AFTERCARE_RESPONSE"):
                ev.positive_count += 1
                ev.last_positive = ev.last_used
                ev.confidence = min(1.0, ev.confidence + 0.05)
            elif outcome in ("NEUTRAL_ENGAGEMENT", "TOPIC_CHANGED"):
                ev.neutral_count += 1
            elif outcome in ("LOW_ENGAGEMENT", "QUESTION_IGNORED", "OBJECTION", "REJECTION", "OFFER_DECLINED"):
                ev.negative_count += 1
                ev.confidence = max(0.1, ev.confidence - 0.05)
            if outcome == "PURCHASE":
                ev.purchase_count += 1
                ev.confidence = min(1.0, ev.confidence + 0.1)
            # Decay old evidence: not needed here, handled in selection
            # Bound to 10 strategies
            ev_dict[strategy] = ev.__dict__
            if len(ev_dict) > 10:
                # Keep most recent by last_used
                sorted_items = sorted(ev_dict.items(), key=lambda x: x[1].get("last_used", ""), reverse=True)[:10]
                ev_dict = dict(sorted_items)
            by_creator[key] = ev_dict
            facts["strategy_evidence_by_creator"] = by_creator
            return True

        await mutate_user_profile_atomically(user_id, _mutate)
    except Exception:
        logger.warning("update_strategy_evidence failed", exc_info=True)

def decay_evidence(evidence: StrategyEvidence, days_since: float) -> float:
    """Deterministic decay: evidence * exp(-days/30)."""
    return evidence.confidence * math.exp(-days_since / 30.0)

def _to_extended(ev: StrategyEvidence) -> ExtendedEvidence:
    return ExtendedEvidence(
        attempt_count=ev.attempt_count, positive_count=ev.positive_count,
        neutral_count=ev.neutral_count, negative_count=ev.negative_count,
        purchase_count=ev.purchase_count, last_used=ev.last_used,
        last_positive=ev.last_positive, confidence=ev.confidence,
    )

def select_strategy(evidence_map: dict[str, StrategyEvidence], eligible: list[str]) -> tuple[str, str]:
    """Legacy select preserved; delegates to adaptive with backward compat thresholds."""
    if not eligible:
        return "RELATIONSHIP_BUILD", "SAFE_DEFAULT"
    # If still flat and simple, keep original logic for backward compat when no extended dimensions needed
    # But also support extended adaptive when evidence_map contains composite keys
    # Detect composite keys (contains ':')
    has_composite = any(":" in k for k in evidence_map.keys())
    has_topic_product = any(getattr(v, "topic", None) or getattr(v, "product_family", None) for v in evidence_map.values())
    if has_composite or has_topic_product:
        # Use adaptive with default 5 threshold (Phase 20 sample-size safety)
        ext_map = {k: _to_extended(v) for k, v in evidence_map.items()}
        strat, source, _mode = select_strategy_adaptive(ext_map, eligible, exploration_rate=0.10)
        return strat, source
    # Original Phase 19 logic (threshold 3) for backward compat
    candidates = [(s, evidence_map.get(s, StrategyEvidence())) for s in eligible]
    if all(ev.attempt_count == 0 for _, ev in candidates):
        return eligible[0], "SAFE_DEFAULT"
    best = None
    best_score = -1
    best_source = "SAFE_DEFAULT"
    for strat, ev in candidates:
        try:
            last_used = datetime.fromisoformat(ev.last_used.replace("Z", "+00:00")) if ev.last_used else None
            if last_used and last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - last_used).total_seconds() / 86400 if last_used else 30
        except Exception:
            days = 30
        decayed = decay_evidence(ev, days)
        score = ev.positive_rate * decayed if ev.attempt_count >= 3 else 0.3
        if score > best_score:
            best_score = score
            best = strat
            if ev.attempt_count >= 3:
                best_source = "FAN_HISTORY" if ev.attempt_count >= 3 else "SAFE_DEFAULT"
            else:
                best_source = "EXPLORATION"
    if best is None:
        best = eligible[0]
        best_source = "SAFE_DEFAULT"
    if evidence_map.get(best, StrategyEvidence()).attempt_count < 2:
        least = min(eligible, key=lambda s: evidence_map.get(s, StrategyEvidence()).attempt_count)
        if least != best and evidence_map.get(least, StrategyEvidence()).attempt_count < 2:
            return least, "EXPLORATION"
    return best, best_source

# ── Phase 20 extended API ─────────────────────────────────────────────

def beta_uncertainty_for_evidence(ev: StrategyEvidence) -> float:
    return _beta_unc(_to_extended(ev))

def strategy_score_for_evidence(ev: StrategyEvidence, *, fatigue_penalty: float = 0.0, topic_relevance: float = 0.0) -> float:
    return _strategy_score(_to_extended(ev), fatigue_penalty=fatigue_penalty, topic_relevance=topic_relevance)

def select_strategy_hierarchical(
    fan_topic_evidence: dict[str, StrategyEvidence],
    fan_evidence: dict[str, StrategyEvidence],
    creator_topic_evidence: dict[str, StrategyEvidence],
    creator_evidence: dict[str, StrategyEvidence],
    eligible: list[str],
    *,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle_stage: str | None = None,
    objective: str | None = None,
    fatigue_map: dict[str, float] | None = None,
) -> tuple[str, str, str]:
    """Hierarchical selection: FAN_TOPIC > FAN > CREATOR_TOPIC > CREATOR > SAFE_DEFAULT with sample-size safety."""
    def _conv(m: dict[str, StrategyEvidence]) -> dict[str, ExtendedEvidence]:
        return {k: _to_extended(v) for k, v in m.items()}
    return select_strategy_adaptive(
        {}, eligible,
        topic=topic, product_family=product_family, lifecycle_stage=lifecycle_stage,
        objective=objective,
        fan_topic_evidence=_conv(fan_topic_evidence),
        fan_evidence=_conv(fan_evidence),
        creator_topic_evidence=_conv(creator_topic_evidence),
        creator_evidence=_conv(creator_evidence),
        fatigue_map=fatigue_map,
    )

async def get_strategy_evidence_hierarchical(
    creator_id: int, user_id: int, topic: str | None = None, product_family: str | None = None, lifecycle_stage: str | None = None
) -> dict[str, StrategyEvidence]:
    """Fetch evidence with optional topic/product/lifecycle filtering (creator-isolated)."""
    base = await get_strategy_evidence(creator_id, user_id)
    if not topic and not product_family and not lifecycle_stage:
        return base
    # Filter to composite keys matching dimensions if present
    filtered: dict[str, StrategyEvidence] = {}
    for k, v in base.items():
        # key may be "strategy" or "strategy:topic:product_family:lifecycle"
        parts = k.split(":")
        strat = parts[0]
        # Check if evidence matches requested dimensions when stored with topic
        if topic and v.topic and v.topic != topic:
            continue
        if product_family and v.product_family and v.product_family != product_family:
            continue
        if lifecycle_stage and v.lifecycle_stage and v.lifecycle_stage != lifecycle_stage:
            continue
        filtered[k] = v
    return filtered

async def update_strategy_evidence_extended(
    creator_id: int,
    user_id: int,
    strategy: str,
    outcome: str,
    *,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle_stage: str | None = None,
    generation_id: str | None = None,
) -> None:
    """Extended update that stores topic/product_family/lifecycle dimensions and dedups by generation_id."""
    try:
        from db.postgres import mutate_user_profile_atomically

        # M4 D6: whole-facts write runs under SELECT ... FOR UPDATE so other
        # creators' namespaces on the same user row cannot be clobbered.
        def _mutate(facts) -> bool:
            by_creator = facts.get("strategy_evidence_by_creator", {})
            key = str(creator_id)
            ev_dict = by_creator.get(key, {})
            # Composite key for hierarchical evidence
            composite = strategy
            if topic or product_family or lifecycle_stage:
                parts = [strategy]
                if topic:
                    parts.append(topic)
                if product_family:
                    parts.append(product_family)
                if lifecycle_stage:
                    parts.append(lifecycle_stage)
                composite = ":".join(parts)
            # Dedup: if generation_id already recorded, skip
            if generation_id:
                seen = facts.get("strategy_generation_seen_by_creator", {}).get(key, [])
                if generation_id in seen:
                    return False
            ev = StrategyEvidence(**ev_dict.get(composite, {})) if composite in ev_dict else StrategyEvidence()
            # also copy base strategy if composite new but base exists
            if composite != strategy and composite not in ev_dict and strategy in ev_dict:
                base = StrategyEvidence(**ev_dict[strategy])
                # don't copy attempt counts, start fresh for specificity but inherit confidence prior
                ev.confidence = base.confidence
            ev.attempt_count += 1
            ev.last_used = datetime.now(timezone.utc).isoformat()
            if topic:
                ev.topic = topic
            if product_family:
                ev.product_family = product_family
            if lifecycle_stage:
                ev.lifecycle_stage = lifecycle_stage
            # Outcome mapping (extended weights)
            pos_outcomes = ("POSITIVE_ENGAGEMENT", "QUESTION_ANSWERED", "TOPIC_CONTINUED", "TOPIC_CONTINUATION", "INTEREST_SIGNAL", "INTEREST_INCREASE", "DESIRE_INCREASE", "OFFER_ACCEPTED", "OFFER_REQUEST", "PURCHASE", "REPEAT_PURCHASE", "AFTERCARE_RESPONSE", "AFTERCARE_ENGAGEMENT", "POSITIVE", "OPEN_LOOP_RESOLVED", "PREFERENCE_LEARNED", "OBJECTION_RESOLVED")
            neg_outcomes = ("LOW_ENGAGEMENT", "QUESTION_IGNORED", "OBJECTION", "REJECTION", "OFFER_DECLINED", "DESIRE_DECREASE", "COOLDOWN", "HANDOFF")
            neut_outcomes = ("NEUTRAL_ENGAGEMENT", "TOPIC_CHANGED", "NO_SIGNAL", "CONVERSATION_END")
            up = outcome.upper()
            if up in pos_outcomes:
                ev.positive_count += 1
                ev.last_positive = ev.last_used
                ev.confidence = min(1.0, ev.confidence + 0.05)
            elif up in neut_outcomes:
                ev.neutral_count += 1
            elif up in neg_outcomes:
                ev.negative_count += 1
                ev.confidence = max(0.1, ev.confidence - 0.05)
            if up in ("PURCHASE", "REPEAT_PURCHASE"):
                ev.purchase_count += 1
                ev.confidence = min(1.0, ev.confidence + 0.10)
                # also update base strategy for fan-level purchase bonus
                if composite != strategy:
                    base_ev = StrategyEvidence(**ev_dict.get(strategy, {})) if strategy in ev_dict else StrategyEvidence()
                    base_ev.attempt_count += 1
                    base_ev.positive_count += 1
                    base_ev.purchase_count += 1
                    base_ev.last_used = ev.last_used
                    base_ev.last_positive = ev.last_used
                    base_ev.confidence = min(1.0, (base_ev.confidence or 0.5) + 0.10)
                    ev_dict[strategy] = base_ev.__dict__
            # Bounded to 20 now (10 fan + 10 hierarchical)
            ev_dict[composite] = ev.__dict__
            if len(ev_dict) > 20:
                sorted_items = sorted(ev_dict.items(), key=lambda x: x[1].get("last_used", ""), reverse=True)[:20]
                ev_dict = dict(sorted_items)
            by_creator[key] = ev_dict
            facts["strategy_evidence_by_creator"] = by_creator
            if generation_id:
                seen_map = facts.get("strategy_generation_seen_by_creator", {})
                seen = seen_map.get(key, [])
                seen.append(generation_id)
                # bound to 100
                if len(seen) > 100:
                    seen = seen[-100:]
                seen_map[key] = seen
                facts["strategy_generation_seen_by_creator"] = seen_map
            return True

        await mutate_user_profile_atomically(user_id, _mutate)
    except Exception:
        logger.warning("update_strategy_evidence_extended failed", exc_info=True)

def get_composite_key(strategy: str, topic: str | None = None, product_family: str | None = None, lifecycle_stage: str | None = None) -> str:
    parts = [strategy]
    if topic:
        parts.append(topic)
    if product_family:
        parts.append(product_family)
    if lifecycle_stage:
        parts.append(lifecycle_stage)
    return ":".join(parts)
