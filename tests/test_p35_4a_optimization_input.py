"""P3.5.4A — Sanitized Optimization Input Contract (unit, no DB/provider/Redis).

Proves the advisory boundary is point-in-time safe, creator-isolated,
immutable, and authority-free: frozen snapshot projection only, no live
rereads, no optimizer in the production path, abstention preserves v1.
"""

import dataclasses
import json
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.unit]

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def candidate(definition_id=11, version=2, **over):
    base = {
        "definition_id": definition_id,
        "version": version,
        "stable_key": "alpha",
        "offer_type": "SMALL_BUNDLE",
        "vault_ids": ["V1", "V2"],
        "price_minor": 1999,
        "currency": "USD",
        "mapped_drop_ids": ["drop_x"],
    }
    base.update(over)
    return base


def snapshot(**over):
    base = {
        "eligible": [candidate(), candidate(definition_id=12, version=1, stable_key="beta")],
        "ineligible": [],
        "selected": candidate(),
        "ranking": {
            "policy_version": "v1",
            "ranked_order": [11, 12],
            "factors": {"11": ["NOVEL_CANONICAL_SET"]},
        },
        "conversation": {
            "lifecycle": "established",
            "current_topic": "movie",
            "recent_topics": [],
            "open_threads": [],
        },
        "fan": {
            "creator_id": 1,
            "user_id": 10,
            "purchase_count": 2,
            "total_spend_minor": 5000,
            "average_order_value_minor": 2500,
            "highest_purchase_minor": 3000,
            "last_purchase_at": "2026-08-20T12:00:00+00:00",
            "recent_purchase_count": 0,
            "recent_spend_minor": 0,
            "purchased_vault_ids": ["V9"],
            "delivered_vault_ids": ["V9"],
            "recent_offer_count": 1,
            "recent_rejected_offer_count": 0,
            "last_offer_at": "2026-08-28T12:00:00+00:00",
            "recent_offered_vault_ids": ["V1"],
            "currency": "USD",
        },
        "history": {
            "creator_id": 1,
            "user_id": 10,
            "total_offer_count": 3,
            "recent_offer_count": 1,
            "last_offer_at": "2026-08-28T12:00:00+00:00",
            "declined_offer_count": 1,
            "recent_declined_offer_count": 0,
            "state_counts": {"pending": 1, "declined": 1, "purchased": 1},
            "has_active_offer": True,
            "active_offer_count": 1,
            "offered_vault_sets": [["V1", "V2"]],
            "active_vault_sets": [["V1", "V2"]],
            "null_snapshot_count": 0,
            "definition_identity_available": False,
        },
    }
    base.update(over)
    return base


def ledger_row(**over):
    base = {
        "opportunity_id": 5,
        "creator_id": 1,
        "user_id": 10,
        "generation_id": "gen-1",
        "evaluated_at": NOW,
        "decision_snapshot": json.dumps(snapshot()),
        "selected_definition_id": 11,
        "selected_version": 2,
        "selected_stable_key": "alpha",
        "decision_status": "SEALED",
        "sealed_offer_id": 42,
        "reengagement_of": None,
    }
    base.update(over)
    return base


def evidence(**over):
    base = {
        "creator_id": 1,
        "opportunity_id": 5,
        "exposure_state": "SENT",
        "exposure_at": NOW.isoformat(),
        "label": "CENSORED",
        "maturity_state": "IMMATURE",
        "maturity_policy_version": "p353b.v1",
        "evidence_quality": "FULL",
        "attribution_status": "unattributed",
        "recovered": False,
        "label_as_of": NOW.isoformat(),
        "reengagement_of": None,
        "sealed_offer_id": 42,
    }
    base.update(over)
    return base


def build(**kw):
    from commerce.opportunity_optimization import build_optimization_input

    args = {"ledger_row": ledger_row()}
    args.update(kw)
    return build_optimization_input(**args)


# ---------------------------------------------------------------------------
# Contract construction
# ---------------------------------------------------------------------------


class TestContractConstruction:
    def test_valid_immutable_input(self):
        out = build()
        assert (out.creator_id, out.opportunity_id, out.user_id) == (1, 5, 10)
        assert out.evaluated_at == NOW
        assert len(out.frozen_candidates) == 2
        with pytest.raises(Exception):
            out.creator_id = 2  # type: ignore[misc]

    def test_candidate_snapshot_projection(self):
        out = build()
        first = out.frozen_candidates[0]
        assert first.identity() == (11, 2)
        assert first.price_minor == 1999 and first.currency == "USD"
        assert first.canonical_vault_ids == ("V1", "V2")
        assert first.mapped_drop_ids == ("drop_x",)
        assert out.selected_definition_id == 11
        assert out.selected_definition_version == 2

    def test_policy_version(self):
        assert build().policy_version == "v1"
        row = ledger_row()
        snap = snapshot()
        snap["ranking"] = {"policy_version": None, "ranked_order": [], "factors": {}}
        row["decision_snapshot"] = json.dumps(snap)
        from commerce.opportunity_optimization import build_optimization_input

        out = build_optimization_input(ledger_row=row)
        assert out.policy_version is None  # unknown, never invented

    def test_ownership_context(self):
        out = build()
        assert out.ownership_context.owned_vault_ids == frozenset({"V9"})
        assert out.ownership_context.source == "snapshot"

    def test_fan_summary(self):
        fan = build().fan_commercial_summary
        assert fan.purchase_count == 2 and fan.total_spend_minor == 5000
        assert fan.average_order_value_minor == 2500
        assert fan.highest_purchase_minor == 3000
        assert fan.last_purchase_at == "2026-08-20T12:00:00+00:00"
        assert fan.delivered_vault_ids == ("V9",)

    def test_offer_history_summary(self):
        hist = build().offer_history_summary
        assert hist.total_offer_count == 3
        assert hist.state_counts == (("declined", 1), ("pending", 1), ("purchased", 1))
        assert hist.has_active_offer is True
        assert hist.definition_identity_available is False

    def test_conversation_subset(self):
        conv = build().conversation_context
        assert conv.lifecycle == "established"
        assert conv.current_topic == "movie"
        assert conv.recent_topics == () and conv.open_threads == ()
        assert not hasattr(conv, "tone") and not hasattr(conv, "prose")

    def test_evidence_context(self):
        out = build(evidence=evidence())
        assert out.evidence_context.label == "CENSORED"
        assert out.evidence_context.exposure_state == "SENT"
        assert out.evidence_context.evidence_quality == "FULL"
        assert not hasattr(out.evidence_context, "transaction_id")

    def test_reengagement_context(self):
        out = build()
        assert out.reengagement_context.is_child is False
        assert out.reengagement_context.reengagement_of is None
        row = ledger_row(opportunity_id=6, reengagement_of=5)
        from commerce.opportunity_optimization import build_optimization_input

        child = build_optimization_input(ledger_row=row)
        assert child.reengagement_context.is_child is True
        assert child.reengagement_context.reengagement_of == 5

    def test_missing_snapshot_fails_closed(self):
        from commerce.opportunity_optimization import build_optimization_input

        with pytest.raises(ValueError):
            build_optimization_input(ledger_row=ledger_row(decision_snapshot="{}"))
        with pytest.raises(ValueError):
            build_optimization_input(ledger_row=ledger_row(decision_snapshot="not-json"))

    def test_unidentified_candidate_fails_closed(self):
        from commerce.opportunity_optimization import build_optimization_input

        snap = snapshot()
        snap["eligible"] = [candidate(definition_id=None)]
        with pytest.raises(ValueError):
            build_optimization_input(ledger_row=ledger_row(decision_snapshot=json.dumps(snap)))


# ---------------------------------------------------------------------------
# Temporal safety
# ---------------------------------------------------------------------------


class TestTemporalSafety:
    def test_future_purchase_excluded(self):
        # Snapshot frozen before a later purchase: input reflects the snapshot.
        out = build()
        assert out.fan_commercial_summary.purchase_count == 2
        assert out.fan_commercial_summary.last_purchase_at == "2026-08-20T12:00:00+00:00"

    def test_future_offer_and_history_excluded(self):
        out = build()
        assert out.offer_history_summary.total_offer_count == 3
        assert out.fan_commercial_summary.recent_offer_count == 1

    def test_future_conversation_excluded(self):
        out = build()
        assert out.conversation_context.current_topic == "movie"

    def test_current_price_mutation_does_not_rewrite(self):
        # Builder never consults live catalog: same snapshot ⇒ same candidate.
        first, second = build(), build()
        assert first.frozen_candidates == second.frozen_candidates
        assert first.frozen_candidates[0].price_minor == 1999

    def test_current_catalog_mutation_does_not_rewrite(self):
        out = build()
        assert out.frozen_candidates[1].identity() == (12, 1)
        assert out.frozen_candidates[1].mapped_drop_ids == ("drop_x",)

    def test_builder_has_no_live_state_access(self):
        import commerce.opportunity_optimization as mod

        assert not hasattr(mod, "get_pool")
        assert not hasattr(mod, "get_offer_definition")
        # Scan imports/calls only: the contract docstring legitimately names
        # the forbidden live readers in prose prohibitions.
        src = open(mod.__file__, encoding="utf-8").read()
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("get_pool", "get_offer_definition", "dropfans.service",
                      "get_redis", "llm_provider", "ppv_analytics"):
            assert token not in joined, token

    def test_outcome_fields_cutoff_gated(self):
        from commerce.opportunity_optimization import build_evidence_context

        ctx = build_evidence_context(evidence(label="POSITIVE"), creator_id=1, opportunity_id=5)
        assert ctx.label == "POSITIVE"
        ctx2 = build_evidence_context(None, creator_id=1, opportunity_id=5)
        assert ctx2.label == "UNAVAILABLE" and ctx2.evidence_quality == "UNAVAILABLE"

    def test_no_future_label_leakage(self):
        out = build(evidence=evidence(label="CENSORED", maturity_state="IMMATURE"))
        assert out.evidence_context.label == "CENSORED"
        assert "txn" not in str(dataclasses.asdict(out)).lower()


# ---------------------------------------------------------------------------
# Evidence safety
# ---------------------------------------------------------------------------


class TestEvidenceSafety:
    def test_mature_evidence_preserved(self):
        ctx = build(evidence=evidence(
            label="POSITIVE", maturity_state="MATURE", evidence_quality="FULL",
        )).evidence_context
        assert (ctx.label, ctx.maturity_state, ctx.evidence_quality) == ("POSITIVE", "MATURE", "FULL")

    def test_censored_evidence_preserved(self):
        ctx = build(evidence=evidence(label="CENSORED")).evidence_context
        assert ctx.label == "CENSORED"

    def test_unavailable_evidence_preserved(self):
        ctx = build().evidence_context
        assert ctx.label == "UNAVAILABLE" and ctx.evidence_quality == "UNAVAILABLE"

    def test_recovered_evidence_marked_partial(self):
        ctx = build(evidence=evidence(
            evidence_quality="PARTIAL", recovered=True, label="POSITIVE",
        )).evidence_context
        assert ctx.evidence_quality == "PARTIAL" and ctx.recovered is True

    def test_no_numerical_weights(self):
        import re

        import commerce.opportunity_optimization as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert set(re.findall(r"(?<![\w.])\d+\.\d+", src)) == set()

    def test_commercial_negative_and_process_labels_preserved(self):
        from commerce.opportunity_optimization import build_evidence_context

        assert build_evidence_context(
            evidence(label="COMMERCIAL_NEGATIVE"), creator_id=1, opportunity_id=5
        ).label == "COMMERCIAL_NEGATIVE"
        assert build_evidence_context(
            evidence(label="PROCESS_NEGATIVE"), creator_id=1, opportunity_id=5
        ).label == "PROCESS_NEGATIVE"
        assert build_evidence_context(
            evidence(label="NO_OPPORTUNITY"), creator_id=1, opportunity_id=5
        ).label == "NO_OPPORTUNITY"


# ---------------------------------------------------------------------------
# Creator safety
# ---------------------------------------------------------------------------


class TestCreatorSafety:
    def test_cross_creator_candidate_rejection(self):
        from commerce.opportunity_optimization import build_optimization_input

        snap = snapshot()  # fan.creator_id == 1
        with pytest.raises(ValueError):
            build_optimization_input(ledger_row=ledger_row(creator_id=2, decision_snapshot=json.dumps(snap)))

    def test_cross_creator_history_rejection(self):
        from commerce.opportunity_optimization import build_history_summary

        with pytest.raises(ValueError):
            build_history_summary(snapshot()["history"], creator_id=2, user_id=10)

    def test_cross_creator_evidence_rejection(self):
        from commerce.opportunity_optimization import build_evidence_context

        with pytest.raises(ValueError):
            build_evidence_context(evidence(), creator_id=2, opportunity_id=5)

    def test_cross_creator_ownership_rejection(self):
        from commerce.opportunity_optimization import build_fan_summary

        with pytest.raises(ValueError):
            build_fan_summary(snapshot()["fan"], creator_id=2, user_id=10)


# ---------------------------------------------------------------------------
# Authority safety
# ---------------------------------------------------------------------------


class TestAuthoritySafety:
    def test_contract_has_no_commerce_side_effects(self):
        import commerce.opportunity_optimization as mod

        for name in ("seal", "execute", "send", "attribute", "purchase", "record_"):
            assert not hasattr(mod, name), name
        assert not hasattr(mod, "evaluate_opportunity")
        assert not hasattr(mod, "rank_candidates")

    def test_output_cannot_carry_forbidden_authority(self):
        import dataclasses

        from commerce.opportunity_optimization import AdvisoryOptimizationResult

        fields = {f.name for f in dataclasses.fields(AdvisoryOptimizationResult)}
        assert fields <= {"creator_id", "opportunity_id", "candidate_scores", "abstain",
                          "optimizer_version", "input_policy_version"}
        with pytest.raises(Exception):
            AdvisoryOptimizationResult(
                creator_id=1, opportunity_id=5,
                candidate_scores=((11, 2, float("nan")),),
            )

    def test_abstention_preserves_v1_path(self):
        from commerce.opportunity_optimization import abstain_result

        out = abstain_result(creator_id=1, opportunity_id=5, input_policy_version="v1")
        assert out.abstain is True and out.candidate_scores == ()
        assert out.input_policy_version == "v1"

    def test_no_optimizer_invocation_in_production_path(self):
        import pathlib

        root = pathlib.Path(__file__).parent.parent
        hits = []
        for rel in ("workers/llm_worker.py", "workers/send_worker.py", "workers/scheduler_worker.py",
                    "commerce/pipeline.py", "commerce/opportunity_engine.py",
                    "commerce/opportunity_ranking.py", "commerce/opportunity_sealing.py",
                    "commerce/opportunity_execution.py"):
            src = (root / rel).read_text(encoding="utf-8")
            if "opportunity_optimization" in src:
                hits.append(rel)
        assert hits == []

    def test_adaptive_optimizer_not_wired(self):
        import commerce.opportunity_optimization as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("select_strategy_adaptive", "should_explore", "Experiment",
                      "adaptive_optimization", "strategy_learning"):
            assert token not in src, token


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_frozen_input(self):
        out = build()
        with pytest.raises(Exception):
            out.policy_version = "v2"  # type: ignore[misc]

    def test_frozen_candidate_collection(self):
        out = build()
        assert isinstance(out.frozen_candidates, tuple)
        with pytest.raises(Exception):
            out.frozen_candidates[0].price_minor = 1  # type: ignore[misc]

    def test_immutable_ids_and_content(self):
        out = build()
        assert isinstance(out.ownership_context.owned_vault_ids, frozenset)
        assert isinstance(out.fan_commercial_summary.recent_offered_vault_ids, tuple)
        with pytest.raises(Exception):
            out.reengagement_context.is_child = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Re-engagement
# ---------------------------------------------------------------------------


class TestReengagement:
    def test_parent_child_distinction(self):
        from commerce.opportunity_optimization import build_reengagement_context

        parent = build_reengagement_context(ledger_row(), offer_exposures=None, opportunity_id=5)
        assert parent.is_child is False and parent.revenue_events is None
        child = build_reengagement_context(
            ledger_row(opportunity_id=6, reengagement_of=5),
            {"exposures": [{}, {}], "purchase_winner_opportunity_id": 5, "revenue_events": 1},
            opportunity_id=6,
        )
        assert child.is_child is True and child.sibling_touch_count == 1
        assert child.purchase_winner_opportunity_id == 5 and child.revenue_events == 1

    def test_one_revenue_event_semantics(self):
        out = build(offer_exposures={
            "exposures": [{}, {}], "purchase_winner_opportunity_id": 5, "revenue_events": 1,
        })
        assert out.reengagement_context.revenue_events == 1

    def test_no_duplicate_positive_labels(self):
        import commerce.opportunity_optimization as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert "revenue_events" in src
        # Contract carries linkage only; attribution stays single-winner upstream.
        assert "record_purchase_by_offer" not in src

    def test_validator_boundary_lists_required_checks(self):
        from commerce.opportunity_optimization import validation_checklist

        names = [name for name, _ in validation_checklist()]
        assert names == ["creator_scope", "eligibility", "ownership", "candidate_identity",
                         "single_drop_rule", "governance", "provider_truth"]
