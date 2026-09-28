"""P3.5.4B — Historical deterministic validator (unit, no DB/provider/Redis).

Proves hypothetical advice is checked against frozen decision-time facts in
mandatory gate order, with advisory/output safety, governance honesty, and
zero commerce side effects. No optimizer exists; advisors here are test
doubles only.
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
        "stable_key": "alpha" if definition_id == 11 else "beta",
        "offer_type": "SMALL_BUNDLE",
        "vault_ids": ["V1", "V2"] if definition_id == 11 else ["V3"],
        "price_minor": 1999 if definition_id == 11 else 999,
        "currency": "USD",
        "mapped_drop_ids": ["drop_x"] if definition_id == 11 else ["drop_y"],
    }
    base.update(over)
    return base


def snapshot(**over):
    base = {
        "eligible": [candidate(), candidate(12, 1)],
        "ineligible": [],
        "selected": candidate(),
        "ranking": {"policy_version": "v1", "ranked_order": [11, 12], "factors": {}},
        "conversation": {"lifecycle": "established", "current_topic": "t",
                         "recent_topics": [], "open_threads": []},
        "fan": {"creator_id": 1, "user_id": 10, "purchase_count": 0,
                "total_spend_minor": 0, "purchased_vault_ids": [],
                "delivered_vault_ids": [], "recent_offer_count": 0,
                "recent_rejected_offer_count": 0, "recent_offered_vault_ids": [],
                "recent_purchase_count": 0, "recent_spend_minor": 0},
        "history": {"creator_id": 1, "user_id": 10, "total_offer_count": 0,
                    "recent_offer_count": 0, "declined_offer_count": 0,
                    "recent_declined_offer_count": 0, "state_counts": {},
                    "has_active_offer": False, "active_offer_count": 0,
                    "offered_vault_sets": [], "active_vault_sets": [],
                    "null_snapshot_count": 0, "definition_identity_available": False},
    }
    base.update(over)
    return base


def ledger_row(**over):
    base = {
        "opportunity_id": 5, "creator_id": 1, "user_id": 10,
        "generation_id": "gen-1", "evaluated_at": NOW,
        "decision_snapshot": json.dumps(snapshot()),
        "selected_definition_id": 11, "selected_version": 2,
        "selected_stable_key": "alpha", "decision_status": "SEALED",
        "sealed_offer_id": 42, "reengagement_of": None,
    }
    base.update(over)
    return base


def make_input(**over):
    from commerce.opportunity_optimization import build_optimization_input

    row = ledger_row()
    row.update(over.pop("row_over", {}))
    snap = snapshot()
    snap.update(over.pop("snap_over", {}))
    row["decision_snapshot"] = json.dumps(snap)
    return build_optimization_input(ledger_row=row)


def advise(scores, **over):
    from commerce.opportunity_optimization import AdvisoryOptimizationResult

    kw = {"creator_id": 1, "opportunity_id": 5, "candidate_scores": scores,
          "optimizer_version": "test-0", "input_policy_version": "v1"}
    kw.update(over)
    return AdvisoryOptimizationResult(**kw)


def validate(inp, adv, **kw):
    from commerce.opportunity_validation import validate_advisory

    params = {"sealed_offer_id": 42, "ranked_order": (11, 12)}
    params.update(kw)
    return validate_advisory(inp, adv, **params)


class TestGateOrder:
    def test_mandatory_order(self):
        from commerce.opportunity_validation import GATE_ORDER

        assert GATE_ORDER == ("creator_scope", "candidate_identity", "ownership",
                              "eligibility", "single_drop_rule", "governance",
                              "provider_truth")

    def test_gates_recorded_in_order(self):
        out = validate(make_input(), advise(((12, 1, 0.7),)))
        assert [g.gate for g in out.gate_results] == list(
            __import__("commerce.opportunity_validation", fromlist=["GATE_ORDER"]).GATE_ORDER)
        assert out.classification == "VALID" and out.valid is True
        assert out.historical is True

    def test_first_failed_gate_reported(self):
        out = validate(make_input(), advise(((99, 9, 0.7),)))
        assert out.first_failed_gate == "candidate_identity"
        assert out.classification == "ADVISORY_INVALID"


class TestCreatorScope:
    def test_wrong_creator_rejects(self):
        out = validate(make_input(), advise(((11, 2, 0.5),), creator_id=2))
        assert (out.classification, out.first_failed_gate) == ("ADVISORY_INVALID", "creator_scope")

    def test_wrong_opportunity_rejects(self):
        out = validate(make_input(), advise(((11, 2, 0.5),), opportunity_id=6))
        assert (out.classification, out.first_failed_gate) == ("ADVISORY_INVALID", "creator_scope")

    def test_wrong_policy_version_rejects(self):
        out = validate(make_input(), advise(((11, 2, 0.5),), input_policy_version="v9"))
        assert (out.classification, out.first_failed_gate) == ("ADVISORY_INVALID", "creator_scope")


class TestCandidateIdentity:
    def test_unknown_candidate_invalid(self):
        out = validate(make_input(), advise(((77, 1, 0.9),)))
        assert out.classification == "ADVISORY_INVALID"

    def test_wrong_version_invalid(self):
        out = validate(make_input(), advise(((11, 9, 0.9),)))
        assert out.classification == "ADVISORY_INVALID"

    def test_duplicate_reference_invalid(self):
        out = validate(make_input(), advise(((11, 2, 0.5), (11, 2, 0.4))))
        assert out.classification == "ADVISORY_INVALID"

    def test_frozen_price_used_not_optimizer_price(self):
        # Advisory carries no price field at all; validator evaluates the
        # frozen candidate price through production eligibility instead.
        snap = snapshot()
        snap["eligible"][0]["price_minor"] = -5
        inp = make_input(snap_over={"eligible": snap["eligible"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert out.classification == "ADVISORY_NOT_ELIGIBLE"
        assert "INVALID_PRICE" in out.denial_reasons


class TestOwnership:
    def test_owned_overlap_rejects(self):
        snap = snapshot()
        snap["fan"]["purchased_vault_ids"] = ["V1", "V2"]
        inp = make_input(snap_over={"fan": snap["fan"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert out.classification == "ADVISORY_NOT_ELIGIBLE"
        assert out.first_failed_gate == "ownership"
        assert out.denial_reasons == ("ownership_overlap",)

    def test_partial_overlap_rejects(self):
        snap = snapshot()
        snap["fan"]["purchased_vault_ids"] = ["V1"]
        inp = make_input(snap_over={"fan": snap["fan"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert out.classification == "ADVISORY_NOT_ELIGIBLE"

    def test_zero_overlap_passes(self):
        out = validate(make_input(), advise(((11, 2, 0.9),)))
        assert [g for g in out.gate_results if g.gate == "ownership"][0].passed is True


class TestEligibilityReuse:
    def test_denial_reasons_use_production_vocabulary(self):
        import commerce.opportunity_eligibility as elig

        allowed = {n for n in dir(elig) if n.isupper()}
        snap = snapshot()
        snap["eligible"][0]["price_minor"] = -5
        inp = make_input(snap_over={"eligible": snap["eligible"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert out.denial_reasons and set(out.denial_reasons) <= allowed

    def test_active_duplicate_denied_verbatim(self):
        snap = snapshot()
        snap["history"]["active_vault_sets"] = [["V1", "V2"]]
        inp = make_input(snap_over={"history": snap["history"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert out.classification == "ADVISORY_NOT_ELIGIBLE"
        assert "ACTIVE_DUPLICATE_OFFER" in out.denial_reasons


class TestSingleDropRule:
    def test_zero_drops_rejected(self):
        snap = snapshot()
        snap["eligible"][0]["mapped_drop_ids"] = []
        inp = make_input(snap_over={"eligible": snap["eligible"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert (out.classification, out.first_failed_gate) == ("VALIDATOR_REJECTED", "single_drop_rule")
        gate = [g for g in out.gate_results if g.gate == "single_drop_rule"][0]
        assert gate.reason == "NO_MAPPED_DROP"

    def test_multiple_drops_rejected(self):
        snap = snapshot()
        snap["eligible"][0]["mapped_drop_ids"] = ["a", "b"]
        inp = make_input(snap_over={"eligible": snap["eligible"]})
        out = validate(inp, advise(((11, 2, 0.9),)))
        assert (out.classification, out.first_failed_gate) == ("VALIDATOR_REJECTED", "single_drop_rule")
        gate = [g for g in out.gate_results if g.gate == "single_drop_rule"][0]
        assert gate.reason == "MULTIPLE_DROPS"

    def test_no_dropfans_calls(self):
        import commerce.opportunity_validation as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert "opportunity_sealing" not in src
        assert "Dropfans" not in src and "dropfans" not in src.replace("mapped_drop", "")


class TestGovernance:
    def test_governance_unevaluable_never_pass(self):
        out = validate(make_input(), advise(((11, 2, 0.9),)))
        gate = [g for g in out.gate_results if g.gate == "governance"][0]
        assert gate.passed is None
        assert gate.reason == "UNEVALUABLE_FROM_HISTORY"
        assert out.governance_state == "UNEVALUABLE_FROM_HISTORY"

    def test_unevaluable_governance_blocks_nothing_and_excuses_nothing(self):
        out = validate(make_input(), advise(((12, 1, 0.9),)))
        assert out.classification == "VALID"  # governance neither passes nor fails advice
        bad = validate(make_input(), advise(((99, 1, 0.9),)))
        assert bad.classification == "ADVISORY_INVALID"  # unrelated gates still enforced

    def test_no_operator_state_read(self):
        import commerce.opportunity_validation as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("production_control", "autonomous_allowed", "is_strategy_paused",
                      "compute_pressure", "derive_risk", "get_user_profile"):
            assert token not in src, token


class TestProviderTruth:
    def test_sealed_record_satisfies_gate(self):
        out = validate(make_input(), advise(((11, 2, 0.9),)))
        gate = [g for g in out.gate_results if g.gate == "provider_truth"][0]
        assert (gate.passed, gate.reason) == (True, "sealed_record_present")

    def test_absent_seal_record_is_unevaluable_not_failure(self):
        out = validate(make_input(), advise(((11, 2, 0.9),)), sealed_offer_id=None)
        gate = [g for g in out.gate_results if g.gate == "provider_truth"][0]
        # No recorded send on this input either (PENDING-era row shape) -> unevaluable.
        assert gate.passed is None
        assert out.classification == "VALID"

    def test_zero_provider_calls(self):
        import commerce.opportunity_validation as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("get_drop", "create_drop", "POST", "integrations", "httpx", "aiohttp"):
            assert token not in src, token


class TestAdvisoryOutputValidation:
    def test_nonfinite_score_rejected_at_construction(self):
        from commerce.opportunity_optimization import AdvisoryOptimizationResult

        with pytest.raises(ValueError):
            AdvisoryOptimizationResult(creator_id=1, opportunity_id=5,
                                       candidate_scores=((11, 2, float("nan")),))

    def test_abstain_classification(self):
        from commerce.opportunity_optimization import abstain_result

        out = validate(make_input(), abstain_result(creator_id=1, opportunity_id=5))
        assert out.classification == "ADVISORY_ABSTAIN" and out.valid is True

    def test_empty_scores_abstain_equivalent(self):
        out = validate(make_input(), advise(()))
        assert out.classification == "ADVISORY_ABSTAIN"

    def test_output_type_has_no_forbidden_fields(self):
        from commerce.opportunity_optimization import (
            AdvisoryOptimizationResult, FORBIDDEN_OUTPUT_FIELDS,
        )

        fields = {f.name for f in dataclasses.fields(AdvisoryOptimizationResult)}
        assert not (fields & FORBIDDEN_OUTPUT_FIELDS)

    def test_score_magnitude_does_not_affect_validity(self):
        low = validate(make_input(), advise(((12, 1, 0.01),)))
        high = validate(make_input(), advise(((12, 1, 999.99),)))
        assert (low.classification, high.classification) == ("VALID", "VALID")
        assert low.advisory_candidate == high.advisory_candidate == (12, 1)


class TestAgreement:
    def test_agreement(self):
        from commerce.opportunity_validation import compare_with_v1

        inp = make_input()
        assert compare_with_v1(inp, (11, 2)) == ("AGREEMENT", (11, 2))

    def test_divergence(self):
        from commerce.opportunity_validation import compare_with_v1

        inp = make_input()
        assert compare_with_v1(inp, (12, 1)) == ("DIVERGENCE", (11, 2))

    def test_no_v1_selection(self):
        from commerce.opportunity_optimization import build_optimization_input
        from commerce.opportunity_validation import compare_with_v1

        row = ledger_row(selected_definition_id=None, selected_version=None)
        snap = snapshot()
        snap["selected"] = None
        row["decision_snapshot"] = json.dumps(snap)
        inp = build_optimization_input(ledger_row=row)
        assert compare_with_v1(inp, (11, 2))[0] == "NO_V1_SELECTION"

    def test_tie_break_deterministic_without_rank_order(self):
        from commerce.opportunity_validation import select_advisory_candidate

        adv = advise(((12, 1, 0.5), (11, 2, 0.5)))
        assert select_advisory_candidate(adv) == (11, 2)
        assert select_advisory_candidate(adv) == select_advisory_candidate(adv)

    def test_tie_break_uses_v1_rank_order_when_supplied(self):
        from commerce.opportunity_validation import select_advisory_candidate

        adv = advise(((12, 1, 0.5), (11, 2, 0.5)))
        assert select_advisory_candidate(adv, ranked_order=(12, 11)) == (12, 1)

    def test_historical_catalog_change_cannot_rewrite_comparison(self):
        from commerce.opportunity_validation import compare_with_v1

        inp = make_input()
        first = compare_with_v1(inp, (12, 1))
        # Rebuilt input from the same frozen snapshot compares identically
        # regardless of any current catalog state (builder reads no catalog).
        again = compare_with_v1(make_input(), (12, 1))
        assert first == again == ("DIVERGENCE", (11, 2))
