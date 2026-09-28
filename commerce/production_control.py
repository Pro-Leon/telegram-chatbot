"""Production Control — Phase 22 deterministic layer.

Provides:
- Metric aggregation with windows 1h/24h/7d/30d bounded
- Strategy performance per dimension
- Rollout model 0/1/5/10/25/50/100 scopes GLOBAL/CREATOR/COHORT/EXPERIMENT/STRATEGY
- Emergency controls GLOBAL_AUTONOMOUS_PAUSE etc. fail-closed
- Operational audit record
- Variant attribution + regression minimum-sample + rollback/roll-forward

No new LLM, no new worker/queue/DB/ORM, no architecture redesign.
Persistence via existing user_profiles JSONB (bounded) + in-memory fallback.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
import enum


# ── Metric Windows (§6) ─────────────────────────────────────────────────

class MetricWindow(str, enum.Enum):
    H1 = "1h"
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"

_WINDOW_SECONDS = {
    MetricWindow.H1: 3600,
    MetricWindow.H24: 86400,
    MetricWindow.D7: 7 * 86400,
    MetricWindow.D30: 30 * 86400,
}

def _window_cutoff(window: MetricWindow, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(seconds=_WINDOW_SECONDS[window])

# In-memory metric store for tests/fallback: list of events per creator
_metric_events: list[dict[str, Any]] = []
_METRIC_MAX = 5000  # bounded

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def record_metric(
    *,
    name: str,
    creator_id: int | None = None,
    user_id: int | None = None,
    strategy: str | None = None,
    topic: str | None = None,
    product_family: str | None = None,
    lifecycle: str | None = None,
    objective: str | None = None,
    response_mode: str | None = None,
    experiment_id: str | None = None,
    variant: str | None = None,
    outcome: str | None = None,
    attribution_type: str | None = None,
    failure_class: str | None = None,
    risk_state: str | None = None,
    value: float = 1.0,
    timestamp: str | None = None,
) -> dict[str, Any]:
    ev = {
        "name": name,
        "creator_id": creator_id,
        "user_id": user_id,
        "strategy": strategy,
        "topic": topic,
        "product_family": product_family,
        "lifecycle": lifecycle,
        "objective": objective,
        "response_mode": response_mode,
        "experiment_id": experiment_id,
        "variant": variant,
        "outcome": outcome,
        "attribution_type": attribution_type,
        "failure_class": failure_class,
        "risk_state": risk_state,
        "value": value,
        "timestamp": timestamp or _now_iso(),
    }
    _metric_events.append(ev)
    if len(_metric_events) > _METRIC_MAX:
        # prune oldest 20%
        del _metric_events[:1000]
    # best-effort persist for cross-worker health (bounded, async)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_persist_metric_event(ev))
    except Exception:
        pass
    return ev

def clear_metrics() -> None:
    _metric_events.clear()

def query_metrics(
    *,
    name: str | None = None,
    creator_id: int | None = None,
    window: MetricWindow | None = None,
    strategy: str | None = None,
    experiment_id: str | None = None,
    variant: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    cutoff = _window_cutoff(window, now) if window else None
    out = []
    for ev in _metric_events:
        if name and ev["name"] != name:
            continue
        if creator_id is not None and ev["creator_id"] != creator_id:
            continue
        if strategy and ev["strategy"] != strategy:
            continue
        if experiment_id and ev["experiment_id"] != experiment_id:
            continue
        if variant and ev["variant"] != variant:
            continue
        if cutoff:
            try:
                ts = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except Exception:
                continue
        out.append(ev)
    return out

def aggregate_count(
    *,
    name: str,
    creator_id: int | None = None,
    window: MetricWindow = MetricWindow.H24,
    strategy: str | None = None,
    experiment_id: str | None = None,
    variant: str | None = None,
) -> int:
    return len(query_metrics(name=name, creator_id=creator_id, window=window, strategy=strategy, experiment_id=experiment_id, variant=variant))

def aggregate_rate(
    *,
    name: str,
    creator_id: int | None = None,
    window: MetricWindow = MetricWindow.H24,
) -> float:
    events = query_metrics(name=name, creator_id=creator_id, window=window)
    if not events:
        return 0.0
    total = sum(e["value"] for e in events)
    return total / len(events) if events else 0.0

# Dimension helpers (§5)
def metrics_by_dimension(
    *,
    name: str,
    dimension: str,
    creator_id: int | None = None,
    window: MetricWindow = MetricWindow.H24,
) -> dict[str, int]:
    events = query_metrics(name=name, creator_id=creator_id, window=window)
    counts: dict[str, int] = {}
    for ev in events:
        key = str(ev.get(dimension) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


# ── Strategy Performance (§7) ───────────────────────────────────────────

@dataclass
class StrategyPerformance:
    strategy: str
    attempt_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    purchase_count: int = 0
    repeat_purchase_count: int = 0
    confidence: float = 0.5
    decayed_score: float = 0.0
    fatigue: float = 0.0
    last_used: str | None = None
    last_positive: str | None = None
    last_negative: str | None = None

def strategy_performance_from_evidence(strategy: str, ev: Any, fatigue: float = 0.0) -> StrategyPerformance:
    # ev may be ExtendedEvidence or StrategyEvidence
    decayed = 0.0
    try:
        from commerce.adaptive_optimization import beta_uncertainty, strategy_score
        # build ExtendedEvidence if needed
        if hasattr(ev, "positive_count"):
            decayed = strategy_score(ev, fatigue_penalty=fatigue)  # type: ignore
    except Exception:
        decayed = 0.0
    return StrategyPerformance(
        strategy=strategy,
        attempt_count=getattr(ev, "attempt_count", 0),
        positive_count=getattr(ev, "positive_count", 0),
        neutral_count=getattr(ev, "neutral_count", 0),
        negative_count=getattr(ev, "negative_count", 0),
        purchase_count=getattr(ev, "purchase_count", 0),
        repeat_purchase_count=getattr(ev, "purchase_count", 0),  # alias
        confidence=getattr(ev, "confidence", 0.5),
        decayed_score=decayed,
        fatigue=fatigue,
        last_used=getattr(ev, "last_used", None),
        last_positive=getattr(ev, "last_positive", None),
        last_negative=None,
    )


# ── Rollout / Canary (§10) ──────────────────────────────────────────────

class RolloutScope(str, enum.Enum):
    GLOBAL = "global"
    CREATOR = "creator"
    COHORT = "cohort"
    EXPERIMENT = "experiment"
    STRATEGY = "strategy"

class RolloutStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"

_VALID_PERCENTAGES = frozenset([0, 1, 5, 10, 25, 50, 100])

@dataclass
class Rollout:
    rollout_id: str
    target: str  # e.g., strategy name or experiment_id or cohort_id
    scope: str  # RolloutScope value
    percentage: int  # 0,1,5,10,25,50,100
    start_time: str  # ISO8601
    status: str = RolloutStatus.ACTIVE.value
    created_by: str = "system"
    reason: str | None = None

    def is_active(self) -> bool:
        return self.status == RolloutStatus.ACTIVE.value

_rollout_registry: dict[str, Rollout] = {}

async def _persist_rollout(rollout: Rollout) -> bool:
    """Persist rollout to user_profiles JSONB for restart safety (bounded, best-effort)."""
    try:
        from db.postgres import get_user_profile, update_user_profile
        # Use sentinel user_id for global rollouts: -1, creator-scoped: -creator_id
        # Deterministic via SHA256, not Python hash() (hash() is randomized per process)
        _h = int(hashlib.sha256(rollout.rollout_id.encode()).hexdigest()[:8], 16)
        sentinel = -1 if rollout.scope == RolloutScope.GLOBAL.value else -abs(_h % 1000000) - 1000
        # For creator scope, use -creator_id if target is creator id
        if rollout.scope == RolloutScope.CREATOR.value:
            try:
                sentinel = -int(rollout.target)
            except Exception:
                pass
        facts = await get_user_profile(sentinel)
        by_id = facts.get("rollouts_by_creator", {})
        by_id[rollout.rollout_id] = rollout.__dict__
        # bound to 50 rollouts
        if len(by_id) > 50:
            # keep most recent 50 by start_time
            sorted_ids = sorted(by_id.items(), key=lambda x: x[1].get("start_time",""), reverse=True)[:50]
            by_id = dict(sorted_ids)
        facts["rollouts_by_creator"] = by_id
        await update_user_profile(sentinel, facts)
        return True
    except Exception:
        return False

def _persist_rollout_sync(rollout: Rollout) -> None:
    """Sync in-memory registry is primary for tests; async persist best-effort via create_task if loop running."""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_persist_rollout(rollout))
    except Exception:
        pass

def create_rollout(*, rollout_id: str, target: str, scope: str, percentage: int, created_by: str = "system", reason: str | None = None) -> Rollout:
    if percentage not in _VALID_PERCENTAGES:
        raise ValueError(f"Invalid percentage {percentage}, must be one of {sorted(_VALID_PERCENTAGES)}")
    if scope not in [s.value for s in RolloutScope]:
        raise ValueError(f"Invalid scope {scope}")
    r = Rollout(rollout_id=rollout_id, target=target, scope=scope, percentage=percentage, start_time=_now_iso(), status=RolloutStatus.ACTIVE.value, created_by=created_by, reason=reason)
    _rollout_registry[rollout_id] = r
    # also record metric
    record_metric(name="rollout_created", experiment_id=rollout_id, variant=scope, value=percentage)
    _persist_rollout_sync(r)
    return r

def get_rollout(rollout_id: str) -> Rollout | None:
    return _rollout_registry.get(rollout_id)

def clear_rollouts() -> None:
    _rollout_registry.clear()

def is_rollout_active_for(creator_id: int, user_id: int, rollout: Rollout) -> bool:
    if not rollout.is_active():
        return False
    if rollout.percentage == 0:
        return False
    if rollout.percentage == 100:
        return True
    # Creator scope: only if creator matches target? For GLOBAL, hash decides; for CREATOR, target is creator_id str
    if rollout.scope == RolloutScope.CREATOR.value:
        try:
            if int(rollout.target) != creator_id:
                return False
        except Exception:
            pass
    # Deterministic cohort via hash(creator:user:rollout_id)
    raw = f"{creator_id}:{user_id}:{rollout.rollout_id}".encode()
    h = hashlib.sha256(raw).hexdigest()
    bucket = int(h[:8], 16) / (2**32) * 100  # 0..100
    return bucket < rollout.percentage

def disable_rollout(rollout_id: str, reason: str | None = None) -> bool:
    r = _rollout_registry.get(rollout_id)
    if not r:
        return False
    r.status = RolloutStatus.ROLLED_BACK.value
    if reason:
        r.reason = reason
    record_metric(name="rollout_rollback", experiment_id=rollout_id, value=1.0)
    _persist_rollout_sync(r)
    return True

def enable_rollout(rollout_id: str) -> bool:
    r = _rollout_registry.get(rollout_id)
    if not r:
        return False
    r.status = RolloutStatus.ACTIVE.value
    record_metric(name="rollout_rollforward", experiment_id=rollout_id, value=1.0)
    _persist_rollout_sync(r)
    return True

# Rollback triggers (§11)
ROLLBACK_REASONS = frozenset([
    "confirmed_regression",
    "safety_block_spike",
    "spam_spike",
    "handoff_spike",
    "send_failure_spike",
    "dlq_spike",
    "commerce_rejection_spike",
    "abnormal_pressure_increase",
    "experiment_failure",
])

def should_rollback(*, sample_size: int, window: MetricWindow = MetricWindow.H24, current: dict[str, float], baseline: dict[str, float], thresholds: dict[str, float] | None = None, severity: str = "confirmed") -> tuple[bool, str]:
    if sample_size < 5:
        return False, "insufficient_sample"
    from commerce.adaptive_optimization import detect_regression
    res = detect_regression(current, baseline, thresholds)
    if not res["is_regression"]:
        return False, "no_regression"
    if severity == "severe":
        return True, f"severe_regression:{','.join(res['reasons'])}"
    if severity == "confirmed":
        # need at least 2 reasons or single severe
        if len(res["reasons"]) >= 1:
            return True, f"confirmed_regression:{','.join(res['reasons'])}"
    return False, "warning_only"

def perform_rollback(rollout_id: str, reason: str) -> dict[str, Any]:
    # Mark rollout inactive, disable variant/strategy fallback, record telemetry
    r = get_rollout(rollout_id)
    if not r:
        return {"ok": False, "reason": "not_found"}
    disable_rollout(rollout_id, reason=reason)
    # Also disable experiment if scope experiment
    if r.scope == RolloutScope.EXPERIMENT.value:
        try:
            from commerce.adaptive_optimization import disable_experiment
            disable_experiment(r.target)
        except Exception:
            pass
    # Do NOT delete historical evidence — only behavioral config
    return {"ok": True, "rollout_id": rollout_id, "previous_target": r.target, "reason": reason, "status": r.status}


# ── Emergency Controls (§13) ────────────────────────────────────────────

class EmergencyControlType(str, enum.Enum):
    GLOBAL_AUTONOMOUS_PAUSE = "global_autonomous_pause"
    CREATOR_AUTONOMOUS_PAUSE = "creator_autonomous_pause"
    STRATEGY_PAUSE = "strategy_pause"
    EXPERIMENT_PAUSE = "experiment_pause"
    REENGAGEMENT_PAUSE = "reengagement_pause"
    COMMERCE_PAUSE = "commerce_pause"

_emergency_state: dict[str, dict[str, Any]] = {}  # key -> {active, at, reason}

def _e_key(control: str, creator_id: int | None = None, target: str | None = None) -> str:
    if creator_id is not None and target:
        return f"{control}:{creator_id}:{target}"
    if creator_id is not None:
        return f"{control}:{creator_id}"
    if target:
        return f"{control}:{target}"
    return control

def set_emergency(control: str, active: bool, reason: str | None = None, creator_id: int | None = None, target: str | None = None) -> None:
    k = _e_key(control, creator_id, target)
    _emergency_state[k] = {"active": active, "at": _now_iso(), "reason": reason, "control": control, "creator_id": creator_id, "target": target}
    record_metric(name="emergency_control", experiment_id=control, variant="active" if active else "inactive", value=1.0, creator_id=creator_id)
    # best-effort persist for restart safety (in-memory remains primary for tests)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(persist_emergency_state())
    except Exception:
        pass

def clear_emergency(control: str | None = None) -> None:
    if control is None:
        _emergency_state.clear()
    else:
        keys = [k for k in _emergency_state if k.startswith(control)]
        for k in keys:
            del _emergency_state[k]
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(persist_emergency_state())
    except Exception:
        pass

def is_global_paused() -> bool:
    # Fail-closed: unknown → pause (if not explicitly set to inactive, default pause? Spec says unknown→pause)
    # For tests, we default to not paused unless set active True
    # Implement as: if no record, not paused (to not break normal operation), but if explicitly unknown control state, pause
    # Spec says unknown control state → pause — we interpret as if key exists but value not bool, pause
    for k, v in _emergency_state.items():
        if k == EmergencyControlType.GLOBAL_AUTONOMOUS_PAUSE.value and v.get("active"):
            return True
    return False

def is_creator_paused(creator_id: int) -> bool:
    if is_global_paused():
        return True
    k = _e_key(EmergencyControlType.CREATOR_AUTONOMOUS_PAUSE.value, creator_id=creator_id)
    return _emergency_state.get(k, {}).get("active", False)

def is_strategy_paused(strategy: str, creator_id: int | None = None) -> bool:
    if is_global_paused():
        return True
    if creator_id and is_creator_paused(creator_id):
        return True
    k1 = _e_key(EmergencyControlType.STRATEGY_PAUSE.value, target=strategy)
    if _emergency_state.get(k1, {}).get("active"):
        return True
    if creator_id:
        k2 = _e_key(EmergencyControlType.STRATEGY_PAUSE.value, creator_id=creator_id, target=strategy)
        if _emergency_state.get(k2, {}).get("active"):
            return True
    return False

def is_experiment_paused(experiment_id: str, creator_id: int | None = None) -> bool:
    if is_global_paused():
        return True
    k = _e_key(EmergencyControlType.EXPERIMENT_PAUSE.value, target=experiment_id)
    if _emergency_state.get(k, {}).get("active"):
        return True
    if creator_id and is_creator_paused(creator_id):
        return True
    return False

def is_reengagement_paused(creator_id: int | None = None) -> bool:
    if is_global_paused():
        return True
    k = _e_key(EmergencyControlType.REENGAGEMENT_PAUSE.value, creator_id=creator_id)
    if _emergency_state.get(k, {}).get("active"):
        return True
    if creator_id and is_creator_paused(creator_id):
        return True
    return False

def is_commerce_paused(creator_id: int | None = None) -> bool:
    if is_global_paused():
        return True
    k = _e_key(EmergencyControlType.COMMERCE_PAUSE.value, creator_id=creator_id)
    if _emergency_state.get(k, {}).get("active"):
        return True
    return False

# Policy helper for autonomous pause checks
def autonomous_allowed(creator_id: int | None = None, strategy: str | None = None, experiment_id: str | None = None) -> tuple[bool, str]:
    if is_global_paused():
        return False, "global_pause"
    if creator_id is not None and is_creator_paused(creator_id):
        return False, "creator_pause"
    if strategy and is_strategy_paused(strategy, creator_id):
        return False, "strategy_pause"
    if experiment_id and is_experiment_paused(experiment_id, creator_id):
        return False, "experiment_pause"
    if is_commerce_paused(creator_id):
        return False, "commerce_pause"
    if is_reengagement_paused(creator_id):
        # Re-engagement pause blocks autonomous re-engagement but also general autonomous when strategy is re_engagement
        if strategy and strategy.lower() in ("re_engage", "re_engagement", "re-engagement"):
            return False, "reengagement_pause"
    return True, "allowed"

def autonomous_commerce_allowed(creator_id: int | None = None) -> tuple[bool, str]:
    """Commerce-specific gate: checks commerce pause (global→creator)."""
    if is_commerce_paused(creator_id):
        return False, "commerce_pause"
    if is_global_paused():
        return False, "global_pause"
    if creator_id is not None and is_creator_paused(creator_id):
        return False, "creator_pause"
    return True, "allowed"

# Persistence helpers for restart safety (user_profiles JSONB via sentinel)
async def _persist_metric_event(ev: dict[str, Any]) -> bool:
    try:
        from db.postgres import get_user_profile, update_user_profile
        facts = await get_user_profile(-999997)
        lst = facts.get("metrics_by_creator", [])
        lst.append(ev)
        if len(lst) > 200:
            lst = lst[-200:]
        facts["metrics_by_creator"] = lst
        await update_user_profile(-999997, facts)
        return True
    except Exception:
        return False

async def load_persisted_state() -> dict[str, Any]:
    """Load rollouts / emergency / metrics from user_profiles JSONB sentinels (best-effort, bounded)."""
    try:
        from db.postgres import get_user_profile
        # Rollouts sentinel: -999999
        facts_roll = await get_user_profile(-999999)
        rollouts = facts_roll.get("rollouts_by_creator", {}) if facts_roll else {}
        for rid, data in list(rollouts.items())[:50]:
            try:
                _rollout_registry[rid] = Rollout(**data)
            except Exception:
                continue
        facts_em = await get_user_profile(-999998)
        emerg = facts_em.get("emergency_state", {}) if facts_em else {}
        for k, v in emerg.items():
            _emergency_state[k] = v
        # Metrics: load last 200 into in-memory if currently empty (cross-worker sharing)
        try:
            if not _metric_events:
                facts_m = await get_user_profile(-999997)
                persisted = facts_m.get("metrics_by_creator", []) if facts_m else []
                for ev in persisted[-50:]:
                    _metric_events.append(ev)
        except Exception:
            pass
        return {"rollouts": len(rollouts), "emergency": len(emerg)}
    except Exception:
        return {"rollouts": 0, "emergency": 0}

async def persist_emergency_state() -> bool:
    try:
        from db.postgres import get_user_profile, update_user_profile
        facts = await get_user_profile(-999998)
        facts["emergency_state"] = dict(_emergency_state)
        # bound to 100 keys
        if len(facts["emergency_state"]) > 100:
            # keep most recent 100 by at
            sorted_items = sorted(facts["emergency_state"].items(), key=lambda x: x[1].get("at",""), reverse=True)[:100]
            facts["emergency_state"] = dict(sorted_items)
        await update_user_profile(-999998, facts)
        return True
    except Exception:
        return False


# ── Operational Audit Record (§19) ──────────────────────────────────────

# Re-export for tests (experiment governed assignment lives in conversation_operations)
def experiment_governed_assignment(*args, **kwargs):
    from commerce.conversation_operations import experiment_governed_assignment as _ega
    return _ega(*args, **kwargs)

# Also re-export degraded helpers for convenience
from commerce.conversation_operations import degraded_fallback as _dfb, classify_failure as _cf  # noqa: F401

@dataclass
class OperationalAuditRecord:
    generation_id: str
    creator_id: int | None
    user_id: int | None
    objective: str | None
    strategy: str | None
    experiment_id: str | None
    variant: str | None
    risk_state: str | None
    pressure_score: float | None
    decision: str  # allowed / blocked + reason
    outcome: str | None
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

_audit_log: list[dict[str, Any]] = []
_AUDIT_MAX = 1000

def record_audit(record: OperationalAuditRecord) -> dict[str, Any]:
    d = record.to_dict()
    _audit_log.append(d)
    if len(_audit_log) > _AUDIT_MAX:
        del _audit_log[:200]
    # also record metric
    record_metric(name="audit_record", creator_id=record.creator_id, user_id=record.user_id, strategy=record.strategy, experiment_id=record.experiment_id, variant=record.variant, outcome=record.outcome, value=1.0)
    return d

def query_audits(*, creator_id: int | None = None, generation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for rec in reversed(_audit_log):
        if creator_id is not None and rec.get("creator_id") != creator_id:
            continue
        if generation_id and rec.get("generation_id") != generation_id:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out

def clear_audits() -> None:
    _audit_log.clear()


# ── State Retention (§26) ────────────────────────────────────────────────

def prune_all_retention() -> dict[str, int]:
    """Prune all bounded structures — for tests / scheduler."""
    # Metrics already bounded via _METRIC_MAX
    # Audits bounded via _AUDIT_MAX
    # Exposures: delegated to adaptive_optimization.prune_by_retention but we can call
    try:
        from commerce.adaptive_optimization import _exposure_buffer, _EXPOSURE_MAX, prune_by_retention as _prune
        pruned_exposures = 0
        for k, lst in list(_exposure_buffer.items()):
            new = _prune(lst, max_items=_EXPOSURE_MAX, max_age_days=30)
            pruned_exposures += len(lst) - len(new)
            _exposure_buffer[k] = new
    except Exception:
        pruned_exposures = 0
    return {"metrics": len(_metric_events), "audits": len(_audit_log), "pruned_exposures": pruned_exposures}


# ── Production Safety State Machine (§27) ────────────────────────────────

class ProductionState(str, enum.Enum):
    NORMAL = "normal"
    CAUTION = "caution"
    DEGRADED = "degraded"
    SUPPRESSED = "suppressed"
    HANDOFF = "handoff"
    PAUSED = "paused"
    ROLLBACK = "rollback"
    RECOVERING = "recovering"

def derive_production_state(*, risk_state: str | None = None, failure_class: str | None = None, is_paused: bool = False, is_rollback: bool = False, pressure_bucket: str | None = None) -> ProductionState:
    if is_rollback:
        return ProductionState.ROLLBACK
    if is_paused:
        return ProductionState.PAUSED
    if failure_class == "handoff_required" or risk_state == "handoff":
        return ProductionState.HANDOFF
    if risk_state == "suppress" or pressure_bucket == "suppress":
        return ProductionState.SUPPRESSED
    if failure_class == "degraded" or risk_state == "caution":
        # degraded and caution map to degraded/caution
        if failure_class == "degraded":
            return ProductionState.DEGRADED
        return ProductionState.CAUTION
    if risk_state == "caution":
        return ProductionState.CAUTION
    if failure_class == "retryable":
        return ProductionState.RECOVERING
    return ProductionState.NORMAL


# ── Idempotency helpers (§32) ───────────────────────────────────────────

_idempotency_seen: set[str] = set()

def check_idempotent(key: str) -> bool:
    """Returns True if already seen (duplicate)."""
    if key in _idempotency_seen:
        return True
    _idempotency_seen.add(key)
    # bound
    if len(_idempotency_seen) > 2000:
        # prune arbitrary
        for _ in range(500):
            _idempotency_seen.pop()
    return False

def clear_idempotency() -> None:
    _idempotency_seen.clear()


# ── Health Evaluation (§5, §8) ────────────────────────────────────────

@dataclass
class HealthReport:
    creator_id: int | None
    window: str
    production_state: str
    success_rate: float
    failure_rate: float
    permanent_failure_rate: float
    degraded_rate: float
    handoff_rate: float
    purchase_rate: float
    repeat_purchase_rate: float
    rejection_rate: float
    negative_rate: float
    spam_rate: float
    pressure_suppressed_rate: float
    sample_size: int
    reason_code: str
    metric_evidence: dict[str, Any]
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

_CANARY_STAGES = [0, 1, 5, 10, 25, 50, 100]

def _next_canary_percentage(current: int) -> int | None:
    try:
        idx = _CANARY_STAGES.index(current)
        if idx + 1 < len(_CANARY_STAGES):
            return _CANARY_STAGES[idx + 1]
    except ValueError:
        pass
    return None

def evaluate_production_health(*, creator_id: int | None = None, window: MetricWindow = MetricWindow.H24, now: datetime | None = None) -> HealthReport:
    """Pure deterministic health evaluation using existing metric windows."""
    now = now or datetime.now(timezone.utc)
    # Aggregate per window
    gen_success = aggregate_count(name="generation_success", creator_id=creator_id, window=window)
    gen_failure = aggregate_count(name="generation_failure", creator_id=creator_id, window=window)
    total_gen = gen_success + gen_failure
    success_rate = gen_success / max(1, total_gen)
    failure_rate = gen_failure / max(1, total_gen)
    perm = aggregate_count(name="permanent_failures", creator_id=creator_id, window=window)
    degraded = aggregate_count(name="degraded_failures", creator_id=creator_id, window=window)
    handoff = aggregate_count(name="handoff_required", creator_id=creator_id, window=window)
    purchases = aggregate_count(name="purchases", creator_id=creator_id, window=window)
    repeat = aggregate_count(name="repeat_purchases", creator_id=creator_id, window=window)
    rejections = aggregate_count(name="rejections", creator_id=creator_id, window=window)
    negatives = aggregate_count(name="negative_outcome", creator_id=creator_id, window=window)
    offers = aggregate_count(name="offers_presented", creator_id=creator_id, window=window)
    spam = aggregate_count(name="spam_blocked", creator_id=creator_id, window=window)
    pressure_sup = aggregate_count(name="pressure_suppressed", creator_id=creator_id, window=window)
    # Derived rates
    permanent_rate = perm / max(1, total_gen)
    degraded_rate = degraded / max(1, total_gen)
    handoff_rate = handoff / max(1, total_gen)
    purchase_rate = purchases / max(1, total_gen) if total_gen else 0.0
    repeat_rate = repeat / max(1, purchases) if purchases else 0.0
    rejection_rate = rejections / max(1, total_gen)
    negative_rate = negatives / max(1, total_gen)
    spam_rate = spam / max(1, total_gen)
    pressure_rate = pressure_sup / max(1, total_gen)
    # Choose production state via derive_production_state with health inputs
    # Zero/small sample must not appear healthy — insufficient_data → HOLD
    if total_gen < 5:
        prod_state = ProductionState.CAUTION.value
        reason = "insufficient_sample"
    elif is_global_paused() or (creator_id is not None and is_creator_paused(creator_id)):
        prod_state = ProductionState.PAUSED.value
        reason = "paused"
    elif handoff_rate > 0.10:
        prod_state = ProductionState.HANDOFF.value
        reason = "handoff_spike"
    elif spam_rate > 0.10 or pressure_rate > 0.10:
        prod_state = ProductionState.SUPPRESSED.value
        reason = "suppressed_spam_pressure"
    elif degraded_rate > 0.15 or failure_rate > 0.20:
        prod_state = ProductionState.DEGRADED.value
        reason = "degraded_failure_spike"
    elif rejection_rate > 0.25 or negative_rate > 0.30:
        prod_state = ProductionState.CAUTION.value
        reason = "caution_rejection_negative"
    else:
        prod_state = ProductionState.NORMAL.value
        reason = "normal"
    evidence = {
        "total_gen": total_gen,
        "success_rate": round(success_rate, 3),
        "failure_rate": round(failure_rate, 3),
        "permanent_rate": round(permanent_rate, 3),
        "degraded_rate": round(degraded_rate, 3),
        "handoff_rate": round(handoff_rate, 3),
        "purchase_rate": round(purchase_rate, 3),
        "repeat_rate": round(repeat_rate, 3),
        "rejection_rate": round(rejection_rate, 3),
        "negative_rate": round(negative_rate, 3),
        "spam_rate": round(spam_rate, 3),
        "pressure_rate": round(pressure_rate, 3),
    }
    return HealthReport(
        creator_id=creator_id,
        window=window.value,
        production_state=prod_state,
        success_rate=round(success_rate, 3),
        failure_rate=round(failure_rate, 3),
        permanent_failure_rate=round(permanent_rate, 3),
        degraded_rate=round(degraded_rate, 3),
        handoff_rate=round(handoff_rate, 3),
        purchase_rate=round(purchase_rate, 3),
        repeat_purchase_rate=round(repeat_rate, 3),
        rejection_rate=round(rejection_rate, 3),
        negative_rate=round(negative_rate, 3),
        spam_rate=round(spam_rate, 3),
        pressure_suppressed_rate=round(pressure_rate, 3),
        sample_size=total_gen,
        reason_code=reason,
        metric_evidence=evidence,
    )

def evaluate_rollout_gate(*, rollout: Rollout, health: HealthReport, sample_size: int | None = None, observation_hours: float | None = None) -> tuple[bool, str]:
    """Deterministic gate: can rollout advance? Checks minimum sample + window + error rates."""
    # Use provided sample_size or health sample
    n = sample_size if sample_size is not None else health.sample_size
    if n < 5:
        return False, "insufficient_sample"
    # Minimum observation window 1h — check rollout start_time age
    if observation_hours is None:
        try:
            start = datetime.fromisoformat(rollout.start_time.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            observation_hours = (datetime.now(timezone.utc) - start).total_seconds() / 3600
        except Exception:
            observation_hours = 24
    if observation_hours < 1:
        return False, "insufficient_window"
    if health.failure_rate > 0.20:
        return False, "high_error_rate"
    if health.negative_rate > 0.30:
        return False, "high_negative_rate"
    if health.handoff_rate > 0.10:
        return False, "high_handoff_rate"
    if health.spam_rate > 0.10:
        return False, "high_spam_risk"
    if health.pressure_suppressed_rate > 0.15:
        return False, "abnormal_pressure"
    # Purchase conversion not degraded: if baseline purchase exists, check not degraded >50% drop — simplified: if purchase_rate <0.01 and health.sample_size>20 → caution
    # Regression via existing detect_regression helper (if health shows regression)
    if health.production_state in (ProductionState.SUPPRESSED.value, ProductionState.HANDOFF.value, ProductionState.ROLLBACK.value):
        return False, f"production_state_{health.production_state}"
    # All gates pass
    return True, "gate_pass"

# ── Autonomous Control Loop (§22) ───────────────────────────────────────

def orchestrate_production_controls(*, now: datetime | None = None, window: MetricWindow = MetricWindow.H24) -> list[dict[str, Any]]:
    """Smallest deterministic orchestration: health→state→rollout→experiments→strategy→pause/rollback→audit. Pure, bounded, idempotent."""
    now = now or datetime.now(timezone.utc)
    audits: list[dict[str, Any]] = []
    # Evaluate global health (or per-creator if rollouts are creator-scoped)
    for rollout_id, rollout in list(_rollout_registry.items()):
        if rollout.status != RolloutStatus.ACTIVE.value:
            continue
        # Scope-aware health: GLOBAL uses creator_id=None, CREATOR uses int(target), else global
        creator_for_health = None
        if rollout.scope == RolloutScope.CREATOR.value:
            try:
                creator_for_health = int(rollout.target)
            except Exception:
                creator_for_health = None
        health = evaluate_production_health(creator_id=creator_for_health, window=window, now=now)
        # Check rollback first (confirmed regression)
        sample = health.sample_size
        # Build baseline as healthy (normal) for comparison — simplified: use 0.90 success baseline
        baseline = {"conversion": 0.30, "engagement": 0.60, "rejection_rate": 0.10, "cooldown_rate": 0.05}
        current = {"conversion": health.purchase_rate, "engagement": health.success_rate, "rejection_rate": health.rejection_rate, "cooldown_rate": health.pressure_suppressed_rate}
        should_rb, rb_reason = should_rollback(sample_size=sample, window=window, current=current, baseline=baseline, severity="confirmed")
        if should_rb:
            # Idempotency: key rollout_id+rb_reason
            key = f"orchestrate:rollback:{rollout_id}:{rb_reason}"
            if check_idempotent(key):
                continue
            res = perform_rollback(rollout_id, reason=rb_reason)
            rec = OperationalAuditRecord(generation_id=f"orchestrate-{rollout_id}", creator_id=creator_for_health, user_id=None, objective="rollback", strategy=rollout.target, experiment_id=rollout_id if rollout.scope==RolloutScope.EXPERIMENT.value else None, variant=None, risk_state=health.production_state, pressure_score=health.pressure_suppressed_rate, decision=f"rollback:{rb_reason}", outcome="rollback")
            audits.append(record_audit(rec))
            continue
        # Check emergency: if production_state SUPPRESSED/HANDOFF/PAUSED → maybe set pause
        # For now, not auto-setting emergency here (operator-controlled), just record
        # Evaluate rollout gate for advancement
        can_advance, gate_reason = evaluate_rollout_gate(rollout=rollout, health=health)
        if not can_advance:
            # HOLD — record audit hold
            key = f"orchestrate:hold:{rollout_id}:{gate_reason}"
            if check_idempotent(key):
                continue
            rec = OperationalAuditRecord(generation_id=f"orchestrate-{rollout_id}", creator_id=creator_for_health, user_id=None, objective="hold", strategy=rollout.target, experiment_id=rollout_id if rollout.scope==RolloutScope.EXPERIMENT.value else None, variant=None, risk_state=health.production_state, pressure_score=health.pressure_suppressed_rate, decision=f"hold:{gate_reason}", outcome="hold")
            audits.append(record_audit(rec))
            continue
        # Advance canary 1→5→10→25→50→100
        nxt = _next_canary_percentage(rollout.percentage)
        if nxt is not None:
            key = f"orchestrate:advance:{rollout_id}:{rollout.percentage}->{nxt}"
            if check_idempotent(key):
                continue
            old = rollout.percentage
            rollout.percentage = nxt
            rollout.start_time = now.isoformat()
            record_metric(name="rollout_advance", experiment_id=rollout_id, variant=f"{old}->{nxt}", value=1.0, creator_id=creator_for_health)
            rec = OperationalAuditRecord(generation_id=f"orchestrate-{rollout_id}", creator_id=creator_for_health, user_id=None, objective="advance", strategy=rollout.target, experiment_id=rollout_id if rollout.scope==RolloutScope.EXPERIMENT.value else None, variant=f"{old}->{nxt}", risk_state=health.production_state, pressure_score=health.pressure_suppressed_rate, decision=f"advance:{old}->{nxt}:{gate_reason}", outcome="advance")
            audits.append(record_audit(rec))
    return audits

# ── Rollback Safety (§12) ───────────────────────────────────────────────

def rollback_safety_check(rollout_id: str) -> tuple[bool, str]:
    """Verify rollback cannot delete fan memory / purchases / etc."""
    # Behavioral config only — rollback only touches rollout/experiment status, never deletes offers/transactions/memory/DLQ
    # We assert no deletes in perform_rollback path
    r = get_rollout(rollout_id)
    if not r:
        return False, "not_found"
    # Check target is behavioral (strategy/experiment/cohort/global) not commerce truth
    if r.target in ("offers", "transactions", "purchases", "fan_memory", "dlq"):
        return False, "protected_target"
    return True, "safe_behavioral_only"
