"""Operational Execution — Phase 27 closed-loop wiring.

Pure wiring, no new worker/queue/LLM/DB table, no redesign.
Maps OperationalRecommendation → existing production behavior via
already-authorized mechanisms: set_emergency, disable_experiment, perform_rollback,
make_handoff, record_audit, record_metric.

All execution is:
- fail-closed if not allowed
- revalidates current authority (stale check)
- idempotent via generation_id + action + scope (check_idempotent)
- creator/fan isolated (creator_id:user_id scope)
- bounded trace <500, no content/secrets/PII
- DropFans/LLM authority preserved
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from commerce.operational_intelligence import OperationalRecommendation, OperationalAction
from commerce.production_control import check_idempotent, record_audit, OperationalAuditRecord, record_metric

def _idempotency_key(rec: OperationalRecommendation) -> str:
    gid = rec.generation_id or "no-gen"
    return f"op_exec:{gid}:{rec.recommendation}:{rec.scope}:{rec.creator_id}:{rec.user_id}"

def execute_operational_recommendation(
    rec: OperationalRecommendation,
    *,
    revalidate: bool = True,
) -> dict[str, Any]:
    """Execute a single OperationalRecommendation via existing mechanisms.

    Returns dict with executed, reason, effect.
    No LLM, no new persistence, bounded, isolated.
    """
    # 1. Must be allowed
    if not rec.allowed:
        return {"executed": False, "reason": f"blocked:{rec.blocking_reason}", "action": rec.recommendation, "scope": rec.scope}

    # 2. Idempotency via generation_id
    key = _idempotency_key(rec)
    if check_idempotent(key):
        return {"executed": False, "reason": "idempotent_duplicate", "action": rec.recommendation, "scope": rec.scope}

    # 3. Stale revalidation (fail-closed)
    if revalidate:
        try:
            from commerce.operational_intelligence import _is_recommendation_allowed, OperationalDiagnosis
            # Reconstruct minimal diagnosis for revalidation
            diag = OperationalDiagnosis(
                signal=rec.reason_code, priority=rec.priority, reason_code=rec.reason_code,
                confidence=rec.confidence, evidence=dict(rec.evidence), scope=rec.scope,
                creator_id=rec.creator_id, user_id=rec.user_id,
            )
            # Use the same gating as recommendation creation (checks current is_global_paused etc.)
            allowed, blocking = _is_recommendation_allowed(diag, creator_id=rec.creator_id)
            if not allowed:
                return {"executed": False, "reason": f"stale_blocked:{blocking}", "action": rec.recommendation}
        except Exception:
            return {"executed": False, "reason": "revalidation_failed", "action": rec.recommendation}

    # 4. Execute via existing mechanism per action
    try:
        action = rec.recommendation
        creator_id = rec.creator_id
        evidence = rec.evidence or {}
        generation_id = rec.generation_id or f"exec-{datetime.now(timezone.utc).isoformat()}"

        # Always audit the attempt (bounded)
        try:
            audit = OperationalAuditRecord(
                generation_id=generation_id,
                creator_id=creator_id,
                user_id=rec.user_id,
                objective=evidence.get("strategy") or evidence.get("response_mode") or rec.reason_code,
                strategy=evidence.get("strategy"),
                experiment_id=evidence.get("experiment_id") or evidence.get("experiment"),
                variant=None,
                risk_state=None,
                pressure_score=evidence.get("fatigue") or evidence.get("rejection_rate"),
                decision=f"operational:{action}",
                outcome=action,
            )
            record_audit(audit)
            record_metric(name="operational_action", creator_id=creator_id, user_id=rec.user_id, strategy=evidence.get("strategy"), experiment_id=evidence.get("experiment_id"), value=1.0)
        except Exception:
            pass

        if action in (OperationalAction.NO_ACTION.value, OperationalAction.OBSERVE.value):
            return {"executed": True, "reason": "no_mutation_observe", "action": action, "effect": "audit_only"}

        if action in (OperationalAction.EXPLORE.value, OperationalAction.EXPLOIT.value):
            # Explore/exploit is via existing select_strategy_adaptive exploration_rate, no direct mutation; audit suffices
            return {"executed": True, "reason": "audit_only_explore", "action": action, "effect": "audit_only"}

        if action == OperationalAction.REDUCE_PRESSURE.value:
            # Pressure is per-turn computed, not global; execution is audit + metric, next turn's compute_pressure will naturally be lower if offers/rejections decrease
            record_metric(name="pressure_suppressed", creator_id=creator_id, value=1.0)
            return {"executed": True, "reason": "audit_pressure", "action": action, "effect": "audit_only_pressure"}

        if action in (OperationalAction.SUPPRESS_STRATEGY.value, OperationalAction.ROTATE_STRATEGY.value, OperationalAction.ROTATE_TOPIC.value):
            strat = evidence.get("strategy") or evidence.get("strategy_family")
            if not strat or strat == "unknown":
                return {"executed": False, "reason": "missing_strategy", "action": action}
            # Use existing emergency strategy pause (restart-safe via sentinel -999998)
            try:
                from commerce.production_control import set_emergency, EmergencyControlType
                set_emergency(EmergencyControlType.STRATEGY_PAUSE.value, active=True, target=strat, creator_id=creator_id, reason=rec.reason_code)
                return {"executed": True, "reason": "strategy_paused", "action": action, "effect": f"strategy:{strat} paused"}
            except Exception as e:
                return {"executed": False, "reason": f"pause_failed:{e}", "action": action}

        if action == OperationalAction.SUPPRESS_PRODUCT_FAMILY.value:
            fam = evidence.get("product_family")
            if not fam or fam == "unknown":
                return {"executed": False, "reason": "missing_family", "action": action}
            # P3.3.4 QUARANTINE: no product-family pause exists and none is
            # created here. Audit + metric only; the metric has NO selection
            # authority (ranking/selection never consult ``family_suppressed``).
            # Per-product offer recency remains the only recency signal.
            record_metric(name="family_suppressed", creator_id=creator_id, product_family=fam, value=1.0)
            return {"executed": True, "reason": "audit_family", "action": action, "effect": f"family:{fam} audited_suppressed"}

        if action == OperationalAction.PRIORITIZE_RELATIONSHIP.value:
            # Relationship priority is via relationship_vs_commerce_safety already; audit suffices
            record_metric(name="relationship_prioritized", creator_id=creator_id, value=1.0)
            return {"executed": True, "reason": "audit_relationship", "action": action, "effect": "audit_only"}

        if action == OperationalAction.FOLLOW_UP_OPEN_LOOP.value:
            # Open loop follow-up is via conversation_intelligence when has_open_loop true; execution just ensures audit
            record_metric(name="open_loop_followup", creator_id=creator_id, user_id=rec.user_id, value=1.0)
            return {"executed": True, "reason": "audit_open_loop", "action": action, "effect": "audit_only"}

        if action == OperationalAction.SUPPRESS_REENGAGEMENT.value:
            try:
                from commerce.production_control import set_emergency, EmergencyControlType
                set_emergency(EmergencyControlType.REENGAGEMENT_PAUSE.value, active=True, creator_id=creator_id, reason=rec.reason_code)
                return {"executed": True, "reason": "reengagement_paused", "action": action, "effect": "reengagement_paused"}
            except Exception as e:
                return {"executed": False, "reason": f"pause_failed:{e}", "action": action}

        if action == OperationalAction.PAUSE_EXPERIMENT.value:
            exp = evidence.get("experiment_id") or evidence.get("experiment")
            if not exp or exp == "unknown":
                return {"executed": False, "reason": "missing_experiment", "action": action}
            try:
                from commerce.adaptive_optimization import disable_experiment
                disable_experiment(exp)
                return {"executed": True, "reason": "experiment_paused", "action": action, "effect": f"experiment:{exp} disabled"}
            except Exception as e:
                return {"executed": False, "reason": f"disable_failed:{e}", "action": action}

        if action in (OperationalAction.ROLLBACK_EXPERIMENT.value, OperationalAction.ROLLBACK_ROLLOUT.value):
            # Try rollout first, then experiment
            target = evidence.get("rollout_id") or evidence.get("experiment_id") or evidence.get("strategy")
            if target:
                try:
                    from commerce.production_control import perform_rollback, get_rollout
                    # If target is experiment, rollback that rollout if exists, else disable experiment
                    # Try as rollout_id
                    r = get_rollout(target)
                    if r:
                        res = perform_rollback(target, reason=rec.reason_code)
                        if res.get("ok"):
                            return {"executed": True, "reason": "rollback_performed", "action": action, "effect": f"rollout:{target} rolled_back"}
                    # Fallback to experiment disable
                    from commerce.adaptive_optimization import disable_experiment
                    disable_experiment(target)
                    return {"executed": True, "reason": "experiment_rolled_back", "action": action, "effect": f"experiment:{target} disabled"}
                except Exception as e:
                    return {"executed": False, "reason": f"rollback_failed:{e}", "action": action}
            return {"executed": False, "reason": "missing_target", "action": action}

        if action == OperationalAction.HANDOFF.value:
            try:
                from commerce.conversation_operations import make_handoff, set_handoff_memory
                # Need creator_id and user_id
                if creator_id is not None and rec.user_id is not None:
                    hs = make_handoff(rec.reason_code)
                    set_handoff_memory(creator_id=creator_id, user_id=rec.user_id, state=hs)
                    return {"executed": True, "reason": "handoff_created", "action": action, "effect": f"handoff:{creator_id}:{rec.user_id}"}
                else:
                    return {"executed": False, "reason": "missing_creator_or_user", "action": action}
            except Exception as e:
                return {"executed": False, "reason": f"handoff_failed:{e}", "action": action}

        return {"executed": False, "reason": "unknown_action", "action": action}
    except Exception as e:
        return {"executed": False, "reason": f"exception:{e}", "action": rec.recommendation}
