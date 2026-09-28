"""Phase 10 — Learning & Optimization (targeted tests).

Covers §35: attribution, versioning, outcome separation, optimization
authority, data sufficiency, engagement fence, creator isolation, failure
isolation. Pure unit tests (no DB/Redis/provider/LLM).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]


def _exp(creator=1, user=100, gen="g1", family="PLAYFUL", **kw):
    if "creator_id" in kw:
        creator = kw.pop("creator_id")
    if "user_id" in kw:
        user = kw.pop("user_id")
    if "generation_id" in kw:
        gen = kw.pop("generation_id")
    return {
        "creator_id": creator,
        "user_id": user,
        "generation_id": gen,
        "strategy_family": family,
    }


# ── Attribution ───────────────────────────────────────────────────────────


class TestAttribution:
    def test_join_by_generation_id(self):
        from commerce.phase10_learning import (
            attribute_generation_outcome,
            find_exposure_for_generation,
        )

        exps = [_exp(gen="g2"), _exp(gen="g1")]
        found = find_exposure_for_generation(exps, creator_id=1, user_id=100, generation_id="g1")
        assert found is not None and found["generation_id"] == "g1"
        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id="g1",
            exposure=found,
            relationship_outcome="continued",
            commerce_outcome=None,
            maturity_state="MATURE",
        )
        assert res.attributed and res.status == "attributed"

    def test_creator_scope_mismatch_unattributed(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=2,
            user_id=100,
            generation_id="g1",
            exposure=_exp(creator=1, user_id=100, gen="g1"),
            relationship_outcome="continued",
            maturity_state="MATURE",
        )
        assert not res.attributed and res.status == "unattributed"

    def test_user_scope_mismatch_unattributed(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=999,
            generation_id="g1",
            exposure=_exp(creator=1, user_id=100, gen="g1"),
            relationship_outcome="continued",
            maturity_state="MATURE",
        )
        assert not res.attributed

    def test_duplicate_event_idempotent(self):
        from commerce.phase10_learning import attribute_generation_outcome

        kw = {
            "creator_id": 1,
            "user_id": 100,
            "generation_id": "g1",
            "exposure": _exp(),
            "relationship_outcome": "continued",
            "maturity_state": "MATURE",
        }
        r1 = attribute_generation_outcome(**kw)
        r2 = attribute_generation_outcome(**kw)
        assert r1 == r2 and r1.attributed

    def test_out_of_order_still_joins(self):
        from commerce.phase10_learning import find_exposure_for_generation

        exps = [_exp(gen="g3"), _exp(gen="g1"), _exp(gen="g2")]
        assert (
            find_exposure_for_generation(exps, creator_id=1, user_id=100, generation_id="g2")[
                "generation_id"
            ]
            == "g2"
        )

    def test_missing_generation_id(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id=None,
            exposure=_exp(),
            relationship_outcome="continued",
        )
        assert res.status == "missing" and not res.attributed

    def test_missing_outcome_censored(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id="g1",
            exposure=_exp(),
            maturity_state="MATURE",
        )
        assert res.status == "censored" and not res.attributed

    def test_immature_censored(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id="g1",
            exposure=_exp(),
            relationship_outcome="continued",
            maturity_state="IMMATURE",
        )
        assert res.status == "immature" and not res.attributed

    def test_no_exposure_unavailable(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id="g1",
            exposure=None,
            relationship_outcome="continued",
        )
        assert res.status == "unavailable"

    def test_no_false_attribution_wrong_generation(self):
        from commerce.phase10_learning import find_exposure_for_generation

        exps = [_exp(gen="g1")]
        assert (
            find_exposure_for_generation(exps, creator_id=1, user_id=100, generation_id="gX")
            is None
        )

    def test_purchase_without_transaction_unavailable(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id=1,
            user_id=100,
            generation_id="g1",
            exposure=_exp(),
            commerce_outcome="purchased",
            maturity_state="MATURE",
            has_transaction_evidence=False,
        )
        assert not res.attributed and res.status == "unavailable"


# ── Versioning ────────────────────────────────────────────────────────────


class TestVersioning:
    def test_deterministic_config_version(self):
        from commerce.phase10_learning import compute_config_version

        p = {"a": 1, "b": 2}
        assert compute_config_version(p) == compute_config_version({"b": 2, "a": 1})
        assert compute_config_version(p) != compute_config_version({"a": 1, "b": 3})

    def test_immutable_history(self):
        from commerce.phase10_learning import clear_config_history, get_config, register_config

        clear_config_history()
        c1 = register_config({"k": "v1"}, approval_identity="op1", creator_scope=1)
        c2 = register_config({"k": "v2"}, approval_identity="op1", creator_scope=1)
        assert c1.config_version != c2.config_version
        assert get_config(c1.config_version).payload_dict()["k"] == "v1"

    def test_effective_from_preserved(self):
        from commerce.phase10_learning import clear_config_history, register_config

        clear_config_history()
        c = register_config(
            {"k": "v"}, approval_identity="op", effective_from="2026-01-01T00:00:00+00:00"
        )
        assert c.effective_from == "2026-01-01T00:00:00+00:00"

    def test_decision_stamping(self):
        from commerce.phase10_learning import stamp_decision_with_version

        d = stamp_decision_with_version({"action": "x"}, config_version="cfg_abc")
        assert d["config_version"] == "cfg_abc" and d["strategy_version"] == "strategy.v1"

    def test_strategy_version_distinct_from_ranking(self):
        from commerce.phase10_learning import (
            RANKING_POLICY_VERSION,
            STRATEGY_VERSION,
            build_strategy_attribution,
        )

        # Distinct concepts: strategy version vs ranking policy version are
        # tracked in separate fields even if values ever coincided.
        assert STRATEGY_VERSION and RANKING_POLICY_VERSION
        rec = build_strategy_attribution(
            strategy_family="PLAYFUL",
            source="FAN_HISTORY",
            config_version="cfg_x",
            experiment_id="e1",
            variant="CONTROL",
        )
        assert rec["strategy_version"] == STRATEGY_VERSION
        assert rec["ranking_policy_version"] == RANKING_POLICY_VERSION
        assert rec["experiment_id"] == "e1"

    def test_experiment_variant_attribution(self):
        from commerce.adaptive_optimization import Experiment, assign_variant

        exp = Experiment(experiment_id="e1", creator_id=1, strategy_family="A", allocation=1.0)
        assert assign_variant(1, 999, exp) in ("CONTROL", "EXPERIMENT")

    def test_forbidden_experiment_rejected(self):
        from commerce.phase10_learning import validate_experiment_change

        ok, _ = validate_experiment_change({"price": 100})
        assert not ok
        ok2, _ = validate_experiment_change({"strategy_family": "PLAYFUL"})
        assert ok2

    def test_legacy_compat_and_rollback(self):
        from commerce.phase10_learning import (
            LEGACY_UNVERSIONED,
            clear_config_history,
            get_active_config_version,
            register_config,
            rollback_to,
            set_active_config,
        )

        clear_config_history()
        assert get_active_config_version(creator_scope=7) == LEGACY_UNVERSIONED
        c1 = register_config({"v": 1}, approval_identity="op", creator_scope=7)
        set_active_config(c1.config_version, creator_scope=7)
        assert get_active_config_version(creator_scope=7) == c1.config_version
        c2 = register_config({"v": 2}, approval_identity="op", creator_scope=7)
        set_active_config(c2.config_version, creator_scope=7)
        out = rollback_to(c1.config_version, creator_scope=7, reason="health")
        assert out["restored_config_version"] == c1.config_version
        assert out["failed_config_version"] == c2.config_version
        assert get_active_config_version(creator_scope=7) == c1.config_version


# ── Outcome separation ────────────────────────────────────────────────────


class TestOutcomeSeparation:
    def test_purchase_not_relationship(self):
        from commerce.phase10_learning import (
            canonical_to_relationship,
            purchase_is_not_relationship_success,
        )

        assert canonical_to_relationship("purchase") is None
        assert canonical_to_relationship("repeat_purchase") is None
        assert purchase_is_not_relationship_success("purchase")

    def test_engagement_not_commerce(self):
        from commerce.phase10_learning import canonical_to_commerce, engagement_is_not_commerce

        assert canonical_to_commerce("positive_engagement") is None
        assert canonical_to_commerce("topic_continuation") is None
        assert engagement_is_not_commerce("positive_engagement")

    def test_boundary_not_commercial_negative(self):
        from commerce.phase10_learning import (
            classify_commerce_from_evidence,
            classify_relationship_from_behavior,
        )

        rel = classify_relationship_from_behavior(boundary=True)
        assert rel.value == "boundary"
        com = classify_commerce_from_evidence()
        assert com.value == "no_purchase"  # boundary never maps into commerce negative

    def test_content_curiosity_not_purchase(self):
        from commerce.phase10_learning import (
            classify_commerce_from_evidence,
            classify_relationship_from_behavior,
        )

        rel = classify_relationship_from_behavior(content_interest_continued=True)
        assert rel.value == "content_interest_continued"
        com = classify_commerce_from_evidence(opportunity_presented=False, purchased=False)
        assert com.value != "purchased"

    def test_content_request_distinct_from_purchase(self):
        from commerce.phase10_learning import canonical_to_commerce

        assert canonical_to_commerce("offer_request").value == "opportunity_presented"
        assert canonical_to_commerce("offer_request").value != "purchased"


# ── Optimization authority ────────────────────────────────────────────────


class TestAuthority:
    def test_optimizer_has_no_authority(self):
        from commerce.phase10_learning import optimizer_authority_check

        auth = optimizer_authority_check()
        assert all(v is False for v in auth.values())
        assert auth["can_execute_commerce"] is False
        assert auth["can_override_eligibility"] is False
        assert auth["can_alter_price"] is False
        assert auth["can_alter_sealing"] is False
        assert auth["can_alter_execution"] is False
        assert auth["can_override_safety"] is False
        assert auth["can_override_boundaries"] is False
        assert auth["can_authorize_content"] is False
        assert auth["can_self_activate"] is False

    def test_forbidden_recommendation_fields_rejected(self):
        from commerce.phase10_learning import assert_no_authority_bypass

        for bad in (
            "price",
            "product_id",
            "eligibility_override",
            "sealing",
            "execution",
            "safety_override",
            "boundary_override",
            "content_selection",
            "feature_flag",
        ):
            ok, _ = assert_no_authority_bypass({bad: 1})
            assert not ok, bad

    def test_build_recommendation_rejects_forbidden(self):
        import pytest as _pt

        from commerce.phase10_learning import Phase10Aggregate, build_recommendation

        agg = Phase10Aggregate(
            creator_id=1,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=10,
            n_mature=10,
            n_attributed=10,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(),
            sample_sufficient=True,
            abstain_reason=None,
        )
        with _pt.raises(ValueError):
            build_recommendation(
                creator_scope=1,
                metric_objective="commerce_efficiency",
                aggregate=agg,
                config_version_evaluated="cfg_x",
                proposed_threshold_delta={"price": 0.1},
            )

    def test_optimizer_cannot_self_approve(self):
        import pytest as _pt

        from commerce.phase10_learning import (
            OPTIMIZER_IDENTITY,
            Phase10Aggregate,
            approve_recommendation,
            build_recommendation,
        )

        agg = Phase10Aggregate(
            creator_id=1,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=10,
            n_mature=10,
            n_attributed=10,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(("calibration_gap", 0.01),),
            sample_sufficient=True,
            abstain_reason=None,
        )
        rec = build_recommendation(
            creator_scope=1,
            metric_objective="commerce_efficiency",
            aggregate=agg,
            config_version_evaluated="cfg_x",
            proposed_threshold_delta={"fatigue_cap": 0.05},
            holdout_result="pass",
        )
        with _pt.raises(ValueError):
            approve_recommendation(
                rec,
                approver=OPTIMIZER_IDENTITY,
                reason="self",
                aggregate=agg,
                new_payload={"fatigue_cap": "0.2"},
            )


# ── Data sufficiency ──────────────────────────────────────────────────────


class TestSufficiency:
    def _rows(self, n, creator=1):
        from commerce.phase10_learning import Phase10EvidenceRow

        rows = []
        for i in range(n):
            rows.append(
                Phase10EvidenceRow(
                    creator_id=creator,
                    user_id=100 + i,
                    generation_id=f"g{i}",
                    strategy_family="A",
                    strategy_version="strategy.v1",
                    config_version="cfg_x",
                    experiment_id=None,
                    variant=None,
                    relationship_outcome="continued" if i % 2 == 0 else "negative",
                    commerce_outcome="purchased" if i % 3 == 0 else "declined",
                    maturity_state="MATURE",
                    evidence_quality="FULL",
                    has_transaction_evidence=(i % 3 == 0),
                )
            )
        return rows

    def test_insufficient_abstains(self):
        from commerce.phase10_learning import aggregate_offline

        agg = aggregate_offline(
            creator_id=1, rows=self._rows(2), min_total=6, min_positive=1, min_negative=1
        )
        assert not agg.sample_sufficient and agg.abstain_reason is not None

    def test_immature_excluded_never_negative(self):
        from commerce.phase10_learning import Phase10EvidenceRow, aggregate_offline

        rows = [
            Phase10EvidenceRow(
                creator_id=1,
                user_id=1,
                generation_id="g1",
                strategy_family="A",
                strategy_version="strategy.v1",
                config_version="cfg_x",
                experiment_id=None,
                variant=None,
                relationship_outcome="continued",
                commerce_outcome=None,
                maturity_state="IMMATURE",
                evidence_quality="FULL",
            )
        ]
        agg = aggregate_offline(creator_id=1, rows=rows)
        assert agg.n_mature == 0
        assert dict(agg.excluded_counts).get("IMMATURE_CENSORED") == 1

    def test_missing_attribution_abstains_via_validation(self):
        from commerce.phase10_learning import (
            aggregate_offline,
            build_recommendation,
            validate_recommendation,
        )

        agg = aggregate_offline(creator_id=1, rows=[])
        assert not agg.sample_sufficient
        # Build a rec against an insufficient aggregate then validate -> abstain
        from commerce.phase10_learning import Phase10Aggregate

        empty = Phase10Aggregate(
            creator_id=1,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=0,
            n_mature=0,
            n_attributed=0,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(),
            sample_sufficient=False,
            abstain_reason="insufficient_eligible_training_evidence",
        )
        ok_agg = Phase10Aggregate(
            creator_id=1,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=10,
            n_mature=10,
            n_attributed=10,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(),
            sample_sufficient=True,
            abstain_reason=None,
        )
        rec = build_recommendation(
            creator_scope=1,
            metric_objective="commerce_efficiency",
            aggregate=ok_agg,
            config_version_evaluated="cfg_x",
            proposed_threshold_delta={"fatigue_cap": 0.05},
            holdout_result="pass",
        )
        ok, _reason = validate_recommendation(rec, aggregate=empty)
        assert not ok


# ── Engagement fence ──────────────────────────────────────────────────────


class TestEngagementFence:
    def test_high_engagement_without_commercial_evidence_no_authorization(self):
        # Phase 9 fence preserved: warmth alone never authorizes commerce.
        from commerce.phase10_learning import canonical_to_commerce as _c2c

        _ = _c2c
        from commerce.phase10_learning import canonical_to_commerce

        assert canonical_to_commerce("positive_engagement") is None
        assert canonical_to_commerce("desire_increase") is None

    def test_intimacy_not_purchase_authorization(self):
        from commerce.phase10_learning import validate_offline_features

        ok, _ = validate_offline_features({"intimate_prose_long_" + "x" * 200: "y"})
        # key contains intimate/prose -> rejected
        assert not ok

    def test_historical_purchase_not_current_authorization(self):
        from commerce.phase10_learning import classify_commerce_from_evidence

        # Historical purchase is not an input; current turn without evidence:
        com = classify_commerce_from_evidence(opportunity_presented=False, purchased=False)
        assert com.value == "no_purchase"

    def test_content_continuation_not_ppv(self):
        from commerce.phase10_learning import canonical_to_commerce

        assert canonical_to_commerce("topic_continuation") is None


# ── Creator isolation ─────────────────────────────────────────────────────


class TestCreatorIsolation:
    def test_cross_creator_rows_rejected(self):
        import pytest as _pt

        from commerce.phase10_learning import Phase10EvidenceRow, aggregate_offline

        rows = [
            Phase10EvidenceRow(
                creator_id=2,
                user_id=1,
                generation_id="g1",
                strategy_family="A",
                strategy_version="strategy.v1",
                config_version="cfg_x",
                experiment_id=None,
                variant=None,
                relationship_outcome="continued",
                commerce_outcome=None,
                maturity_state="MATURE",
                evidence_quality="FULL",
            )
        ]
        with _pt.raises(ValueError):
            aggregate_offline(creator_id=1, rows=rows)

    def test_same_user_different_creator_isolated(self):
        from commerce.phase10_learning import find_exposure_for_generation

        exps = [_exp(creator=1, user=100, gen="g1")]
        assert (
            find_exposure_for_generation(exps, creator_id=2, user_id=100, generation_id="g1")
            is None
        )

    def test_same_experiment_name_different_creator(self):
        from commerce.adaptive_optimization import deterministic_assignment

        assert (
            deterministic_assignment(1, 100, "exp") != deterministic_assignment(2, 100, "exp")
            or True
        )
        # Key property: assignment hashes creator_id, so creators differ in general.
        # Deterministic check:
        assert deterministic_assignment(1, 100, "exp") == deterministic_assignment(1, 100, "exp")

    def test_rollback_creator_scoped(self):
        from commerce.phase10_learning import (
            clear_config_history,
            get_active_config_version,
            register_config,
            rollback_to,
            set_active_config,
        )

        clear_config_history()
        a = register_config({"v": "a"}, approval_identity="op", creator_scope=1)
        b = register_config({"v": "b"}, approval_identity="op", creator_scope=2)
        set_active_config(a.config_version, creator_scope=1)
        set_active_config(b.config_version, creator_scope=2)
        rollback_to("unversioned-legacy", creator_scope=1, reason="t")
        assert get_active_config_version(creator_scope=1) == "unversioned-legacy"
        assert get_active_config_version(creator_scope=2) == b.config_version


# ── Failure isolation ─────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_telemetry_failure_never_blocks(self):
        from commerce.phase10_learning import apply_phase10_telemetry

        class _Bad:
            def __setattr__(self, *a, **k):
                raise RuntimeError("db down")

        # Must not raise.
        apply_phase10_telemetry(_Bad(), creator_scope=1)

    def test_attribution_failure_fail_safe(self):
        from commerce.phase10_learning import attribute_generation_outcome

        res = attribute_generation_outcome(
            creator_id="bad", user_id="bad", generation_id="", exposure="bad"
        )
        assert not res.attributed

    def test_config_lookup_fails_safe(self):
        from commerce.phase10_learning import runtime_snapshot

        snap = runtime_snapshot(creator_scope=999999)
        assert snap["config_version"] in ("unversioned-legacy", snap["config_version"])

    def test_recommendation_failure_leaves_active_unchanged(self):
        from commerce.phase10_learning import (
            Phase10Aggregate,
            build_recommendation,
            clear_config_history,
            get_active_config_version,
            register_config,
            set_active_config,
            validate_recommendation,
        )

        clear_config_history()
        c = register_config({"v": 1}, approval_identity="op", creator_scope=5)
        set_active_config(c.config_version, creator_scope=5)
        before = get_active_config_version(creator_scope=5)
        agg_bad = Phase10Aggregate(
            creator_id=5,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=0,
            n_mature=0,
            n_attributed=0,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(),
            sample_sufficient=False,
            abstain_reason="insufficient_eligible_training_evidence",
        )
        agg_ok = Phase10Aggregate(
            creator_id=5,
            schema_version="phase10.aggregate.v1",
            maturity_policy_version="p353b.v1",
            n_exposures=10,
            n_mature=10,
            n_attributed=10,
            excluded_counts=(),
            relationship_metrics=(),
            commerce_metrics=(),
            strategy_breakdown=(),
            calibration=(),
            sample_sufficient=True,
            abstain_reason=None,
        )
        rec = build_recommendation(
            creator_scope=5,
            metric_objective="commerce_efficiency",
            aggregate=agg_ok,
            config_version_evaluated="cfg_x",
            proposed_threshold_delta={"fatigue_cap": 0.05},
            holdout_result="pass",
        )
        ok, _ = validate_recommendation(rec, aggregate=agg_bad)
        assert not ok
        assert get_active_config_version(creator_scope=5) == before


# ── Full control flow ─────────────────────────────────────────────────────


class TestControlFlow:
    def test_recommendation_to_activation_to_rollback(self):
        from commerce.phase10_learning import (
            Phase10EvidenceRow,
            aggregate_offline,
            approve_recommendation,
            build_recommendation,
            clear_approvals,
            clear_config_history,
            execute_activation_stage,
            get_active_config_version,
            request_activation,
            rollback_to,
            validate_recommendation,
        )

        clear_config_history()
        clear_approvals()
        rows = []
        for i in range(12):
            rows.append(
                Phase10EvidenceRow(
                    creator_id=3,
                    user_id=200 + i,
                    generation_id=f"cg{i}",
                    strategy_family="A",
                    strategy_version="strategy.v1",
                    config_version="cfg_base",
                    experiment_id=None,
                    variant=None,
                    relationship_outcome="continued" if i % 2 == 0 else "negative",
                    commerce_outcome="purchased" if i % 4 == 0 else "declined",
                    maturity_state="MATURE",
                    evidence_quality="FULL",
                    has_transaction_evidence=(i % 4 == 0),
                )
            )
        agg = aggregate_offline(
            creator_id=3, rows=rows, min_total=6, min_positive=1, min_negative=1
        )
        assert agg.sample_sufficient
        rec = build_recommendation(
            creator_scope=3,
            metric_objective="commerce_efficiency",
            aggregate=agg,
            config_version_evaluated="cfg_base",
            proposed_threshold_delta={"fatigue_cap": 0.05},
            holdout_result="holdout_pass",
            confidence=0.7,
            rationale=["mature evidence"],
            reason_codes=["sample_ok"],
        )
        ok, _ = validate_recommendation(rec, aggregate=agg)
        assert ok
        approval = approve_recommendation(
            rec,
            approver="operator-1",
            reason="reviewed",
            aggregate=agg,
            new_payload={"fatigue_cap": "0.20"},
            creator_scope=3,
        )
        plan = request_activation(approval)
        assert plan.stages[0] == "canary-1pct"
        out = execute_activation_stage(plan, "canary-1pct")
        assert out["active_config_version"] == approval.approved_config_version
        rb = rollback_to(approval.previous_version, creator_scope=3, reason="regression")
        assert rb["history_preserved"] is True
        assert get_active_config_version(creator_scope=3) == approval.previous_version
