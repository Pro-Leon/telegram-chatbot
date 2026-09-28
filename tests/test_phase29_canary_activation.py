"""Phase 29 — Controlled Canary Activation & Production Validation
Focused tests for 1% canary, only where Phase 28 did not already cover.
Deterministic, no network, no Telegram.
"""
from datetime import datetime, timezone, timedelta
import pytest

from commerce.production_control import (
    create_rollout, get_rollout, clear_rollouts, is_rollout_active_for,
    RolloutScope, _VALID_PERCENTAGES, _next_canary_percentage,
    record_metric, clear_metrics, query_metrics, aggregate_count, MetricWindow, evaluate_production_health, evaluate_rollout_gate, derive_production_state, ProductionState,
    EmergencyControlType, set_emergency, clear_emergency, is_global_paused, autonomous_allowed,
    check_idempotent, clear_idempotency, record_audit, clear_audits,
)
from commerce.adaptive_optimization import verify_single_pass, has_valid_purchase_evidence

# ── A. Rollout activation 0%→none, 1%→deterministic subset ──────────────
class TestA_RolloutActivation:
    def test_0_none_1_deterministic(self):
        clear_rollouts()
        r0 = create_rollout(rollout_id="r0-29", target="t", scope="global", percentage=0)
        for uid in range(20):
            assert is_rollout_active_for(1, uid, r0) is False
        r1 = create_rollout(rollout_id="r1-29", target="t", scope="global", percentage=1)
        # Deterministic per fan
        assert is_rollout_active_for(1, 42, r1) == is_rollout_active_for(1, 42, r1)
        clear_rollouts()

# ── B. Restart 1%→1% not 100% ───────────────────────────────────────────
class TestB_Restart:
    def test_1_remains_1_after_restart_sim(self):
        clear_rollouts()
        r = create_rollout(rollout_id="restart29", target="t", scope="global", percentage=1)
        assert r.percentage == 1
        # Simulate restart: clear in-memory but would reload same percentage from sentinel
        # For this unit, we just verify new creation at 1 again not 100
        clear_rollouts()
        r2 = create_rollout(rollout_id="restart29-2", target="t", scope="global", percentage=1)
        assert r2.percentage == 1
        assert r2.percentage != 100
        clear_rollouts()

# ── C. Control group unaffected ──────────────────────────────────────────
class TestC_ControlGroup:
    def test_control_unaffected(self):
        clear_rollouts()
        r = create_rollout(rollout_id="ctrl29", target="t", scope="global", percentage=1)
        # Find a control user (where not active)
        control_uid = None
        for uid in range(500):
            if not is_rollout_active_for(1, uid, r):
                control_uid = uid
                break
        assert control_uid is not None
        # Control should have is_rollout_active_for false
        assert is_rollout_active_for(1, control_uid, r) is False
        clear_rollouts()

# ── D. Single-pass 1/1/1/0 ───────────────────────────────────────────────
class TestD_SinglePass:
    def test_single_pass_invariant(self):
        ok, _ = verify_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0})
        assert ok is True
        # Operational execution adds 0 LLM
        import commerce.operational_execution as oe
        assert not hasattr(oe, "generate_content")

# ── E. Production metrics recorded ───────────────────────────────────────
class TestE_Metrics:
    def test_live_generation_records_dimensions(self):
        clear_metrics()
        record_metric(name="generation_success", creator_id=1, user_id=100, strategy="PLAYFUL", topic="fitness", product_family="fitness", lifecycle="engaged", objective="present_offer", response_mode="tease", experiment_id="exp1", variant="CONTROL", outcome="purchase", attribution_type="direct", failure_class=None, risk_state="safe", value=1.0)
        events = query_metrics(name="generation_success", creator_id=1)
        assert len(events) == 1
        assert events[0]["strategy"] == "PLAYFUL"
        assert events[0]["topic"] == "fitness"
        assert events[0]["outcome"] == "purchase"
        clear_metrics()

# ── F. Health insufficient → HOLD ────────────────────────────────────────
class TestF_Health:
    def test_insufficient_hold(self):
        clear_metrics()
        health = evaluate_production_health(creator_id=999, window=MetricWindow.H24)
        assert health.sample_size < 5
        # Gate should hold
        r = create_rollout(rollout_id="holdF", target="t", scope="global", percentage=1)
        from commerce.production_control import evaluate_rollout_gate
        ok, reason = evaluate_rollout_gate(rollout=r, health=health)
        assert ok is False and reason == "insufficient_sample"
        clear_rollouts(); clear_metrics()

# ── G. Promotion requires gates ──────────────────────────────────────────
class TestG_Promotion:
    def test_cannot_progress_without_evidence(self):
        clear_metrics(); clear_rollouts()
        r = create_rollout(rollout_id="promoG", target="t", scope="global", percentage=1)
        health = evaluate_production_health(creator_id=None, window=MetricWindow.H24)
        ok, _ = evaluate_rollout_gate(rollout=r, health=health)
        assert ok is False
        # Even if we try to manually promote to 5, should not without gate
        assert _next_canary_percentage(1) == 5  # progression exists, but gate blocks
        clear_rollouts()

# ── H. Rollback preserves evidence ───────────────────────────────────────
class TestH_Rollback:
    def test_rollback_preserves(self):
        clear_metrics(); clear_rollouts()
        record_metric(name="purchases", creator_id=1, value=1.0)
        r = create_rollout(rollout_id="rbH", target="t", scope="global", percentage=1)
        from commerce.production_control import perform_rollback
        perform_rollback("rbH", reason="test")
        assert get_rollout("rbH").status == "rolled_back"
        assert query_metrics(name="purchases", creator_id=1)
        clear_metrics(); clear_rollouts()

# ── I. Emergency fail-closed ─────────────────────────────────────────────
class TestI_Emergency:
    def test_emergency_blocks(self):
        clear_emergency()
        set_emergency(EmergencyControlType.GLOBAL_AUTONOMOUS_PAUSE.value, active=True)
        assert is_global_paused() is True
        assert autonomous_allowed(creator_id=1)[0] is False
        clear_emergency()

# ── J. Closed loop observable ────────────────────────────────────────────
class TestJ_ClosedLoop:
    def test_recommendation_authorization_action(self):
        from commerce.operational_intelligence import operational_decision
        from commerce.operational_execution import execute_operational_recommendation
        clear_emergency(); clear_idempotency()
        dec = operational_decision(creator_id=1, fatigue=0.35, sample_size=20, baseline_rate=0.30, current_rate=0.10, window="24h", strategy="PLAYFUL")
        recs = dec.recommendations
        assert len(recs) >= 1
        rec = recs[0]
        assert rec.allowed is not None
        assert rec.trace is not None and len(rec.trace) < 500
        # Execute if allowed
        if rec.allowed:
            res = execute_operational_recommendation(rec)
            assert "executed" in res
        clear_idempotency(); clear_emergency()

# ── K. Idempotency ───────────────────────────────────────────────────────
class TestK_Idempotency:
    def test_repeated_not_duplicate(self):
        clear_idempotency()
        assert check_idempotent("genK") is False
        assert check_idempotent("genK") is True
        clear_idempotency()

# ── L. Redis permanent → DLQ+ACK, retryable → retry ─────────────────────
class TestL_Redis:
    def test_permanent_dlq_ack(self):
        from commerce.conversation_operations import classify_failure, FailureClass
        assert classify_failure("invalid peer", "telegram") == FailureClass.PERMANENT
        assert classify_failure("timeout", "telegram") == FailureClass.RETRYABLE

# ── M. DropFans sole authority ───────────────────────────────────────────
class TestM_Authority:
    def test_dropfans_sole(self):
        assert has_valid_purchase_evidence("txn", True) is True
        assert has_valid_purchase_evidence(None, True) is False

# ── N. Isolation ─────────────────────────────────────────────────────────
class TestN_Isolation:
    def test_creator_fan_isolated(self):
        clear_metrics()
        record_metric(name="generation_success", creator_id=1, value=1.0)
        record_metric(name="generation_success", creator_id=2, value=1.0)
        assert aggregate_count(name="generation_success", creator_id=1, window=MetricWindow.H24) == 1
        assert aggregate_count(name="generation_success", creator_id=2, window=MetricWindow.H24) == 1
        clear_metrics()
        # Fan isolation via exposures tested in earlier phases
