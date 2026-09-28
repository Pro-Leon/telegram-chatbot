"""P3.5.6 — Offline Advisory Optimizer Prototype (unit, offline only).

Proves the prototype remains strictly an offline purchase-given-sent-
exposure classifier with creator isolation, chronological safety,
mandatory abstention, deterministic behavior, P3.5.4B validator
integration, production import barrier, and synthetic-fixture integrity.

No DB, no provider, no Redis, no sealing/execution/sending, no Dropfans,
no experiments, no price optimization, no causal claims. All fixtures are
deterministic synthetic bundles clearly marked synthetic.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.unit]

UTC = timezone.utc
START = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cohort(creator_id=1, n=12):
    from commerce.offline_optimizer import build_synthetic_cohort

    return build_synthetic_cohort(creator_id=creator_id, start=START, n=n)


def _train_model(creator_id=1, n=12):
    from commerce.offline_optimizer import (
        build_creator_dataset,
        train_creator_model,
    )

    bundles = _cohort(creator_id=creator_id, n=n)
    dataset = build_creator_dataset(creator_id=creator_id, bundles=bundles)
    return train_creator_model(dataset), bundles, dataset


def _cutoffs(n=12):
    train_end = START + timedelta(hours=24 * 6)
    test_start = train_end
    test_end = START + timedelta(hours=24 * (n + 1))
    return train_end, test_start, test_end


# ---------------------------------------------------------------------------
# A. Label construction
# ---------------------------------------------------------------------------


class TestLabelConstruction:
    def test_mature_purchase_is_positive(self):
        from commerce.offline_optimizer import (
            TRAIN_LABEL_PURCHASED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=5, label="POSITIVE",
            outcome_state="PURCHASED", transaction_id="txn-1",
        )
        out = build_supervised_label(evidence=ev, ledger_row={"reengagement_of": None})
        assert out.kind == TRAIN_LABEL_PURCHASED and out.binary == 1
        assert out.mature is True and out.sent_exposure is True

    def test_mature_declined_is_negative(self):
        from commerce.offline_optimizer import (
            TRAIN_LABEL_DECLINED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=6, label="COMMERCIAL_NEGATIVE",
            outcome_state="DECLINED", transaction_id=None,
            attribution_status="unattributed",
        )
        # Commercial negatives carry FULL quality in this shape.
        ev = dict(ev)
        ev["exposure_state"] = "SENT"
        ev["maturity_state"] = "MATURE"
        ev["evidence_quality"] = "FULL"
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == TRAIN_LABEL_DECLINED and out.binary == 0

    def test_mature_expired_is_negative(self):
        from commerce.offline_optimizer import (
            TRAIN_LABEL_EXPIRED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=7, label="COMMERCIAL_NEGATIVE",
            outcome_state="EXPIRED", transaction_id=None,
            attribution_status="unattributed",
        )
        ev = dict(ev)
        ev["exposure_state"] = "SENT"
        ev["maturity_state"] = "MATURE"
        ev["evidence_quality"] = "FULL"
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == TRAIN_LABEL_EXPIRED and out.binary == 0

    def test_censored_excluded(self):
        from commerce.offline_optimizer import (
            CLASS_CENSORED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=8, label="CENSORED",
            maturity_state="IMMATURE", exposure_state="SENT",
            evidence_quality="FULL", transaction_id=None,
            outcome_state=None,
        )
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_CENSORED and out.binary is None

    def test_unavailable_excluded(self):
        from commerce.offline_optimizer import (
            CLASS_UNAVAILABLE,
            build_supervised_label,
        )

        out = build_supervised_label(evidence=None, ledger_row={})
        assert out.kind == CLASS_UNAVAILABLE and out.binary is None

    def test_recovered_quarantined(self):
        from commerce.offline_optimizer import (
            CLASS_RECOVERED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=9, label="POSITIVE",
            outcome_state="PURCHASED", recovered=True,
        )
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_RECOVERED and out.binary is None

    def test_unattributed_purchase_excluded(self):
        from commerce.offline_optimizer import (
            CLASS_UNATTRIBUTED,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=10, label="POSITIVE",
            outcome_state="PURCHASED", transaction_id=None,
            attribution_status="unattributed",
        )
        ev = dict(ev)
        ev["evidence_quality"] = "UNATTRIBUTED"
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_UNATTRIBUTED and out.binary is None

    def test_send_failure_is_process_negative(self):
        from commerce.offline_optimizer import (
            CLASS_PROCESS_NEGATIVE,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=11, label="PROCESS_NEGATIVE",
            exposure_state="SEND_ATTEMPTED", maturity_state="MATURE",
            outcome_state="SEND_FAILED", transaction_id=None,
            attribution_status="unattributed",
        )
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_PROCESS_NEGATIVE and out.binary is None

    def test_seal_failure_is_process_negative(self):
        from commerce.offline_optimizer import (
            CLASS_PROCESS_NEGATIVE,
            build_supervised_label,
        )

        ev = {
            "creator_id": 1, "opportunity_id": 12,
            "exposure_state": "SEALED", "label": "PROCESS_NEGATIVE",
            "maturity_state": "MATURE", "evidence_quality": "FULL",
            "attribution_status": "unattributed", "transaction_id": None,
            "outcome_state": "SEAL_FAILED", "recovered": False,
        }
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_PROCESS_NEGATIVE and out.binary is None

    def test_no_selection_and_no_opportunity_distinct(self):
        from commerce.offline_optimizer import (
            CLASS_NO_OPPORTUNITY,
            CLASS_NO_SELECTION,
            build_supervised_label,
        )

        for label, expected in (("NO_SELECTION", CLASS_NO_SELECTION), ("NO_OPPORTUNITY", CLASS_NO_OPPORTUNITY)):
            ev = {
                "creator_id": 1, "opportunity_id": 20,
                "exposure_state": "DECISION", "label": label,
                "maturity_state": "MATURE", "evidence_quality": "FULL",
                "attribution_status": "unattributed", "transaction_id": None,
                "outcome_state": label, "recovered": False,
            }
            out = build_supervised_label(evidence=ev, ledger_row={})
            assert out.kind == expected and out.binary is None

    def test_partial_not_pooled_into_full(self):
        from commerce.offline_optimizer import (
            CLASS_PARTIAL,
            build_supervised_label,
            make_synthetic_evidence,
        )

        ev = make_synthetic_evidence(
            creator_id=1, opportunity_id=21, label="POSITIVE",
            outcome_state="PURCHASED",
        )
        ev = dict(ev)
        ev["evidence_quality"] = "PARTIAL"
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_PARTIAL and out.binary is None

    def test_non_sent_exposure_excluded(self):
        from commerce.offline_optimizer import (
            CLASS_CENSORED,
            build_supervised_label,
        )

        ev = {
            "creator_id": 1, "opportunity_id": 22,
            "exposure_state": "SEALED", "label": "CENSORED",
            "maturity_state": "MATURE", "evidence_quality": "FULL",
            "attribution_status": "unattributed", "transaction_id": None,
            "outcome_state": None, "recovered": False,
        }
        out = build_supervised_label(evidence=ev, ledger_row={})
        assert out.kind == CLASS_CENSORED and out.binary is None


# ---------------------------------------------------------------------------
# B. Temporal safety
# ---------------------------------------------------------------------------


class TestTemporalSafety:
    def test_future_purchase_cannot_enter_features(self):
        from commerce.offline_optimizer import extract_features, make_synthetic_bundle

        at = START
        first = make_synthetic_bundle(
            creator_id=1, opportunity_id=501, evaluated_at=at,
            label="POSITIVE", outcome_state="PURCHASED",
        )
        second = make_synthetic_bundle(
            creator_id=1, opportunity_id=502, evaluated_at=at,
            label="COMMERCIAL_NEGATIVE", outcome_state="DECLINED",
            offer_type="SMALL_BUNDLE", price_minor=1999,
        )
        # Same frozen commercial facts -> same feature-relevant snapshot
        # projection for the selected offer; the future outcome label lives
        # only in evidence, never in features.
        assert extract_features(first.input) == extract_features(second.input)

    def test_transaction_ids_never_enter_features(self):
        from commerce.offline_optimizer import extract_features

        _, bundles, _ = _train_model()
        feats = extract_features(bundles[0].input)
        blob = str(sorted(feats.items())).lower()
        assert "txn" not in blob and "transaction" not in blob
        assert "user" not in str(list(feats.keys())).lower()

    def test_user_id_never_a_feature(self):
        from commerce.offline_optimizer import FEATURE_NAMES, extract_features

        assert "user_id" not in FEATURE_NAMES
        _, bundles, _ = _train_model()
        assert "user_id" not in extract_features(bundles[0].input)

    def test_creator_id_never_a_feature(self):
        from commerce.offline_optimizer import FEATURE_NAMES

        assert "creator_id" not in FEATURE_NAMES

    def test_outcome_fields_not_features(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        # Feature extraction must not USE outcome-side fields as features.
        # The docstring legitimately names the forbidden fields in prose
        # prohibitions, so scan key/attribute usage, not mere mentions.
        extract_src = src.split("def extract_features")[1].split("def _features_tuple")[0]
        # Strip the docstring: keep only code after the closing quotes.
        parts = extract_src.split('"""')
        code_only = '"""'.join(parts[2:]) if len(parts) >= 3 else extract_src
        for token in ('"transaction_id"', "'transaction_id'", ".transaction_id",
                      '"purchased_price', "'purchased_price", ".purchased_price",
                      '"outcome_at"', "'outcome_at'", ".outcome_at",
                      '"outcome_state"', "'outcome_state", ".outcome_state",
                      '["user_id"]', "['user_id']", ".user_id",
                      '["creator_id"]', "['creator_id']",
                      '["evaluated_at"]', "['evaluated_at']"):
            assert token not in code_only, token

    def test_current_state_readers_absent(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("get_pool", "get_redis", "get_offer_definition",
                      "dropfans", "salesCount", "ppv_analytics", "llm_provider"):
            assert token not in joined, token

    def test_chronology_violation_raises(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort()
        with pytest.raises(ValueError):
            chronological_holdout_evaluate(
                creator_id=1, bundles=bundles,
                train_end=START + timedelta(days=8),
                test_start=START + timedelta(days=6),
                test_end=START + timedelta(days=13),
            )

    def test_naive_cutoff_raises(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort()
        with pytest.raises(ValueError):
            chronological_holdout_evaluate(
                creator_id=1, bundles=bundles,
                train_end=datetime(2026, 8, 7),
                test_start=START + timedelta(days=7),
                test_end=START + timedelta(days=13),
            )


# ---------------------------------------------------------------------------
# C. Creator isolation
# ---------------------------------------------------------------------------


class TestCreatorIsolation:
    def test_cross_creator_dataset_raises(self):
        from commerce.offline_optimizer import build_creator_dataset

        bundles = _cohort(creator_id=1) + _cohort(creator_id=2)
        with pytest.raises(ValueError):
            build_creator_dataset(creator_id=1, bundles=bundles)

    def test_cross_creator_evidence_mismatch_raises(self):
        from commerce.offline_optimizer import make_training_example

        bundles = _cohort(creator_id=1, n=2)
        bundle = bundles[0]
        bad_evidence = dict(bundle.evidence)
        bad_evidence["creator_id"] = 2
        with pytest.raises(ValueError):
            make_training_example(
                input=bundle.input, evidence=bad_evidence, ledger_row=bundle.ledger_row
            )

    def test_cross_creator_prediction_abstains(self):
        outcome, _, _ = _train_model(creator_id=1)
        assert outcome.model is not None
        other = _cohort(creator_id=2, n=2)[0]
        from commerce.offline_optimizer import predict_for_input

        pred = predict_for_input(outcome.model, other.input)
        assert pred.abstain is True and pred.probability is None
        assert pred.abstain_reason == "creator_isolation"

    def test_train_evaluate_independently_per_creator(self):
        from commerce.offline_optimizer import (
            build_creator_dataset,
            chronological_holdout_evaluate,
            train_creator_model,
        )

        for creator in (1, 2):
            bundles = _cohort(creator_id=creator)
            dataset = build_creator_dataset(creator_id=creator, bundles=bundles)
            assert all(e.creator_id == creator for e in dataset.examples)
            train_end, test_start, test_end = _cutoffs()
            report = chronological_holdout_evaluate(
                creator_id=creator, bundles=bundles,
                train_end=train_end, test_start=test_start, test_end=test_end,
            )
            assert report.creator_id == creator
            assert all(r.creator_id == creator for r in report.rows)

    def test_no_global_or_pooled_model(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read().lower()
        assert "global model" not in src or "no silent aggregation" in src
        assert "hierarchical" not in src or "prohibited" in src or "never" in src


# ---------------------------------------------------------------------------
# D. Abstention
# ---------------------------------------------------------------------------


class TestAbstention:
    def test_insufficient_data_abstains(self):
        from commerce.offline_optimizer import (
            build_creator_dataset,
            train_creator_model,
        )

        bundles = _cohort(creator_id=1, n=2)
        dataset = build_creator_dataset(creator_id=1, bundles=bundles)
        outcome = train_creator_model(dataset)
        assert outcome.abstained is True
        assert outcome.model is None
        assert outcome.abstain_reason == "insufficient_eligible_training_evidence"

    def test_malformed_input_abstains(self):
        from commerce.offline_optimizer import predict_for_input

        outcome, _, _ = _train_model()
        assert outcome.model is not None
        pred = predict_for_input(outcome.model, "not-an-input")  # type: ignore[arg-type]
        assert pred.abstain is True and pred.probability is None

    def test_unsupported_schema_abstains(self):
        import dataclasses

        from commerce.offline_optimizer import predict_for_input

        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        bad_model = dataclasses.replace(outcome.model, feature_schema_version="bogus.v9")
        pred = predict_for_input(bad_model, bundles[0].input)
        assert pred.abstain is True
        assert pred.abstain_reason == "unsupported_feature_schema"

    def test_invalid_prediction_abstains(self):
        import dataclasses

        from commerce.offline_optimizer import predict_for_input

        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        bad_model = dataclasses.replace(outcome.model, prior_log_odds=float("inf"))
        pred = predict_for_input(bad_model, bundles[0].input)
        assert pred.abstain is True
        assert pred.abstain_reason in ("invalid_prediction", "missing_required_features:ValueError")

    def test_recovered_input_abstains(self):
        from commerce.offline_optimizer import make_synthetic_bundle, predict_for_input

        outcome, _, _ = _train_model()
        assert outcome.model is not None
        recovered = make_synthetic_bundle(
            creator_id=1, opportunity_id=900, evaluated_at=START,
            label="POSITIVE", outcome_state="PURCHASED", recovered=True,
        )
        # Recovered evidence must surface through the frozen input context.
        assert recovered.input.evidence_context is not None
        assert recovered.input.evidence_context.recovered is True
        pred = predict_for_input(outcome.model, recovered.input)
        assert pred.abstain is True

    def test_child_input_abstains_from_primary(self):
        from commerce.offline_optimizer import make_synthetic_bundle, predict_for_input

        outcome, _, _ = _train_model()
        assert outcome.model is not None
        child = make_synthetic_bundle(
            creator_id=1, opportunity_id=901, evaluated_at=START,
            label="CENSORED", outcome_state="SENT", reengagement_of=900,
        )
        assert child.input.reengagement_context is not None
        assert child.input.reengagement_context.is_child is True
        pred = predict_for_input(outcome.model, child.input)
        assert pred.abstain is True
        assert pred.abstain_reason == "reengagement_child_excluded_from_primary"

    def test_abstention_preserves_v1(self):
        from commerce.offline_optimizer import predict_for_input, prediction_to_advisory

        outcome, bundles, _ = _train_model(n=2)  # insufficient -> no model
        assert outcome.model is None
        target = _cohort(creator_id=1, n=2)[0]
        pred = predict_for_input(None, target.input, abstain_reason_if_no_model="insufficient_eligible_training_evidence")
        assert pred.abstain is True
        advisory = prediction_to_advisory(target.input, pred)
        assert advisory.abstain is True and advisory.candidate_scores == ()


# ---------------------------------------------------------------------------
# E. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_fixture_same_model(self):
        from commerce.offline_optimizer import build_creator_dataset, train_creator_model

        first = train_creator_model(build_creator_dataset(creator_id=1, bundles=_cohort()))
        second = train_creator_model(build_creator_dataset(creator_id=1, bundles=_cohort()))
        assert first.model is not None and second.model is not None
        assert first.model.prior_log_odds == second.model.prior_log_odds
        assert first.model.feature_llr == second.model.feature_llr
        assert (first.model.n_train, first.model.n_pos, first.model.n_neg) == (
            second.model.n_train, second.model.n_pos, second.model.n_neg)

    def test_repeated_predictions_identical(self):
        from commerce.offline_optimizer import predict_for_input

        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        first = [predict_for_input(outcome.model, b.input).probability for b in bundles]
        second = [predict_for_input(outcome.model, b.input).probability for b in bundles]
        assert first == second

    def test_repeated_evaluation_identical(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort()
        train_end, test_start, test_end = _cutoffs()
        first = chronological_holdout_evaluate(
            creator_id=1, bundles=bundles,
            train_end=train_end, test_start=test_start, test_end=test_end)
        second = chronological_holdout_evaluate(
            creator_id=1, bundles=bundles,
            train_end=train_end, test_start=test_start, test_end=test_end)
        assert first.coverage == second.coverage
        assert first.accuracy == second.accuracy
        assert first.brier == second.brier
        assert [(r.opportunity_id, r.probability, r.classification) for r in first.rows] == [
            (r.opportunity_id, r.probability, r.classification) for r in second.rows]

    def test_feature_llr_order_deterministic(self):
        outcome, _, _ = _train_model()
        assert outcome.model is not None
        keys = [k for k, _ in outcome.model.feature_llr]
        assert keys == sorted(keys)
        for _, value in outcome.model.feature_llr:
            assert math.isfinite(value)


# ---------------------------------------------------------------------------
# F. Versioning
# ---------------------------------------------------------------------------


class TestVersioning:
    def test_feature_schema_present_and_immutable(self):
        from commerce import offline_optimizer as mod

        assert mod.FEATURE_SCHEMA_VERSION == "p356.features.v1"
        assert isinstance(mod.FEATURE_NAMES, tuple) and len(mod.FEATURE_NAMES) == 16

    def test_policy_version_preserved_not_learned(self):
        from commerce.offline_optimizer import extract_features

        _, bundles, _ = _train_model()
        assert bundles[0].input.policy_version == "v1"
        assert "policy_version" not in extract_features(bundles[0].input)

    def test_optimizer_version_distinct(self):
        from commerce import offline_optimizer as mod

        assert mod.OPTIMIZER_VERSION != mod.FEATURE_SCHEMA_VERSION
        assert mod.OPTIMIZER_VERSION.startswith("p356.")
        outcome, _, _ = _train_model()
        assert outcome.model is not None
        assert outcome.model.optimizer_version == mod.OPTIMIZER_VERSION
        assert outcome.model.feature_schema_version == mod.FEATURE_SCHEMA_VERSION

    def test_metadata_separates_three_versions(self):
        from commerce.offline_optimizer import model_metadata

        outcome, _, _ = _train_model()
        assert outcome.model is not None
        meta = model_metadata(outcome.model)
        assert meta["feature_schema_version"] == "p356.features.v1"
        assert meta["optimizer_version"] == "p356.offline.proto.v1"
        assert "ranking_policy_versions" in meta
        assert "v1" in meta["ranking_policy_versions"]

    def test_training_example_carries_schema(self):
        _, _, dataset = _train_model()
        assert dataset.feature_schema_version == "p356.features.v1"
        assert all(e.feature_schema_version == "p356.features.v1" for e in dataset.examples)


# ---------------------------------------------------------------------------
# G. Re-engagement
# ---------------------------------------------------------------------------


class TestReengagement:
    def test_children_excluded_from_primary(self):
        from commerce.offline_optimizer import (
            CLASS_REENGAGEMENT_CHILD,
            build_creator_dataset,
            make_synthetic_bundle,
        )

        parent = make_synthetic_bundle(
            creator_id=1, opportunity_id=700, evaluated_at=START,
            label="POSITIVE", outcome_state="PURCHASED",
        )
        child = make_synthetic_bundle(
            creator_id=1, opportunity_id=701,
            evaluated_at=START + timedelta(hours=1),
            label="POSITIVE", outcome_state="PURCHASED",
            reengagement_of=700,
        )
        dataset = build_creator_dataset(creator_id=1, bundles=[parent, child])
        assert dataset.n_bundles == 2
        assert len(dataset.examples) == 1
        assert dataset.examples[0].opportunity_id == 700
        assert dict(dataset.excluded_counts).get(CLASS_REENGAGEMENT_CHILD) == 1

    def test_child_linkage_preserved(self):
        from commerce.offline_optimizer import make_synthetic_bundle

        child = make_synthetic_bundle(
            creator_id=1, opportunity_id=701,
            evaluated_at=START + timedelta(hours=1),
            label="CENSORED", outcome_state="SENT", reengagement_of=700,
        )
        assert child.input.reengagement_context is not None
        assert child.input.reengagement_context.is_child is True
        assert child.input.reengagement_context.reengagement_of == 700
        assert child.ledger_row["reengagement_of"] == 700

    def test_no_duplicate_revenue_accounting(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert "revenue_events" not in src or "single-winner" in src.lower()
        assert "Option A" in src
        # Evaluation reports carry no revenue fields by construction.
        fields = {f.name for f in dataclasses.fields(mod.EvaluationReport)}
        assert not any("revenue" in f for f in fields)
        row_fields = {f.name for f in dataclasses.fields(mod.EvalRow)}
        assert not any("revenue" in f for f in row_fields)


# ---------------------------------------------------------------------------
# H. Validator integration
# ---------------------------------------------------------------------------


class TestValidatorIntegration:
    def test_valid_advisory_through_validator(self):
        from commerce.offline_optimizer import predict_for_input, prediction_to_advisory
        from commerce.opportunity_validation import AGREEMENT, VALID, compare_with_v1, validate_advisory

        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        target = bundles[0]
        pred = predict_for_input(outcome.model, target.input)
        assert pred.abstain is False and pred.probability is not None
        advisory = prediction_to_advisory(target.input, pred)
        assert advisory.abstain is False and len(advisory.candidate_scores) == 1
        result = validate_advisory(
            target.input, advisory,
            sealed_offer_id=target.ledger_row.get("sealed_offer_id"),
        )
        assert result.classification == VALID
        comparison, v1 = compare_with_v1(target.input, result.advisory_candidate)
        assert comparison == AGREEMENT
        assert v1 == result.advisory_candidate

    def test_invalid_candidate_rejected(self):
        from commerce.opportunity_optimization import AdvisoryOptimizationResult
        from commerce.opportunity_validation import ADVISORY_INVALID, validate_advisory

        bundles = _cohort(n=2)
        target = bundles[0]
        advisory = AdvisoryOptimizationResult(
            creator_id=target.input.creator_id,
            opportunity_id=target.input.opportunity_id,
            candidate_scores=((999999, 1, 0.9),),
            optimizer_version="p356.offline.proto.v1",
            input_policy_version=target.input.policy_version,
        )
        result = validate_advisory(target.input, advisory)
        assert result.valid is False
        assert result.classification == ADVISORY_INVALID

    def test_forbidden_authority_fields_impossible(self):
        from commerce.opportunity_optimization import AdvisoryOptimizationResult

        fields = {f.name for f in dataclasses.fields(AdvisoryOptimizationResult)}
        assert fields == {"creator_id", "opportunity_id", "candidate_scores",
                          "abstain", "optimizer_version", "input_policy_version"}
        for forbidden in ("price_minor", "currency", "vault_ids", "drop_ids",
                          "checkout_url", "eligibility", "ownership", "seal",
                          "send", "transaction_id"):
            assert forbidden not in fields

    def test_abstention_cannot_suppress_v1(self):
        from commerce.offline_optimizer import predict_for_input, prediction_to_advisory
        from commerce.opportunity_validation import ADVISORY_ABSTAIN, validate_advisory

        bundles = _cohort(n=2)
        target = bundles[0]
        assert target.input.selected_definition_id is not None  # v1 selection exists
        pred = predict_for_input(None, target.input)
        advisory = prediction_to_advisory(target.input, pred)
        result = validate_advisory(target.input, advisory)
        assert result.classification == ADVISORY_ABSTAIN
        # v1 selection is untouched by the abstention.
        assert target.input.selected_definition_id is not None

    def test_agreement_taxonomy_on_holdout(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort()
        train_end, test_start, test_end = _cutoffs()
        report = chronological_holdout_evaluate(
            creator_id=1, bundles=bundles,
            train_end=train_end, test_start=test_start, test_end=test_end)
        allowed = {"AGREEMENT", "DIVERGENCE", "ADVISORY_ABSTAIN",
                   "ADVISORY_INVALID", "ADVISORY_NOT_ELIGIBLE",
                   "VALIDATOR_REJECTED", "NO_V1_SELECTION",
                   "INPUT_UNAVAILABLE", "RECOVERED_INPUT", "VALIDATION_ERROR"}
        assert all(r.classification in allowed for r in report.rows)
        # Safest prototype scores only the exposed identity: divergence is
        # zero by design (no counterfactual ranking from observational data).
        assert report.divergences == 0
        assert report.agreements == report.n_predicted_primary

    def test_extrapolative_scorer_labeled(self):
        import commerce.offline_optimizer as mod

        assert "EXTRAPOLATIVE" in mod.score_candidates_extrapolative.__doc__
        assert "OFF-POLICY" in mod.score_candidates_extrapolative.__doc__
        assert "NOT validated" in mod.score_candidates_extrapolative.__doc__
        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        advisory = mod.score_candidates_extrapolative(bundles[0].input, outcome.model)
        assert advisory.abstain is False  # diagnostic path emits scores
        assert mod.EXTRAPOLATIVE_WARNING.startswith("EXTRAPOLATIVE")


# ---------------------------------------------------------------------------
# Chronological evaluation + metrics
# ---------------------------------------------------------------------------


class TestChronologicalEvaluation:
    def test_holdout_counts_and_metrics(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort(n=12)
        train_end, test_start, test_end = _cutoffs(n=12)
        report = chronological_holdout_evaluate(
            creator_id=1, bundles=bundles,
            train_end=train_end, test_start=test_start, test_end=test_end)
        assert report.abstained_training is False
        assert (report.n_train_primary, report.n_train_pos, report.n_train_neg) == (6, 3, 3)
        assert (report.n_test_primary, report.n_test_pos, report.n_test_neg) == (6, 3, 3)
        assert report.n_test_bundles == 6
        assert report.coverage == pytest.approx(1.0)
        assert report.abstention_rate == pytest.approx(0.0)
        assert report.n_predicted_primary == 6
        assert report.accuracy is not None and report.accuracy >= 0.5
        assert report.precision is not None and report.recall is not None
        assert report.brier is not None and 0.0 <= report.brier <= 1.0
        assert report.mean_predicted is not None and report.observed_rate is not None
        assert report.calibration_gap is not None and report.calibration_gap >= 0.0
        assert report.policy_breakdown == (("v1", 6),)

    def test_no_revenue_uplift_or_causal_metrics(self):
        import commerce.offline_optimizer as mod

        fields = {f.name for f in dataclasses.fields(mod.EvaluationReport)}
        for forbidden in ("revenue", "uplift", "roi", "elasticity", "causal", "conversion"):
            assert not any(forbidden in f for f in fields), forbidden
        src = open(mod.__file__, encoding="utf-8").read().lower()
        assert "revenue uplift" not in src
        assert "causal improvement" not in src or "never" in src or "not" in src

    def test_score_semantics_documented(self):
        import commerce.offline_optimizer as mod

        assert "conditional on sent exposure" in mod.SCORE_SEMANTICS
        normalized_doc = " ".join(mod.__doc__.split())
        assert "conditional on sent exposure" in normalized_doc
        outcome, bundles, _ = _train_model()
        assert outcome.model is not None
        from commerce.offline_optimizer import predict_for_input

        pred = predict_for_input(outcome.model, bundles[0].input)
        assert pred.score_semantics == mod.SCORE_SEMANTICS
        assert pred.is_extrapolative is False

    def test_expanding_window_runs(self):
        from commerce.offline_optimizer import expanding_window_evaluate

        bundles = _cohort(n=12)
        first_end = START + timedelta(hours=24 * 6)
        second_end = START + timedelta(hours=24 * 8)
        test_end = START + timedelta(hours=24 * 13)
        reports = expanding_window_evaluate(
            creator_id=1, bundles=bundles,
            cutoffs=[(first_end, first_end, second_end), (second_end, second_end, test_end)],
        )
        assert len(reports) == 2
        assert reports[0].n_train_primary == 6
        assert reports[1].n_train_primary == 8

    def test_insufficient_training_in_report(self):
        from commerce.offline_optimizer import chronological_holdout_evaluate

        bundles = _cohort(n=4)
        report = chronological_holdout_evaluate(
            creator_id=1, bundles=bundles,
            train_end=START + timedelta(hours=24 * 2),
            test_start=START + timedelta(hours=24 * 2),
            test_end=START + timedelta(hours=24 * 5),
        )
        assert report.abstained_training is True
        assert report.training_abstain_reason == "insufficient_eligible_training_evidence"
        assert report.n_predicted_primary == 0
        assert report.accuracy is None and report.brier is None


# ---------------------------------------------------------------------------
# Feature contract
# ---------------------------------------------------------------------------


class TestFeatureContract:
    def test_feature_names_frozen(self):
        from commerce.offline_optimizer import FEATURE_NAMES

        assert tuple(FEATURE_NAMES) == (
            "offer_type", "price_bucket", "currency", "family_presence",
            "vault_count_bucket", "drop_mapping", "fan_purchase_bucket",
            "fan_spend_bucket", "fan_recent_offer_bucket", "fan_rejected_bucket",
            "history_total_bucket", "history_declined_bucket", "has_active_offer",
            "lifecycle", "topic_presence", "owned_count_bucket",
        )

    def test_price_is_fact_not_signal(self):
        import commerce.offline_optimizer as mod

        assert "never" in mod._price_bucket.__doc__.lower()
        assert "price" in mod._price_bucket.__doc__.lower()
        assert mod._price_bucket(499) == "LOW"
        assert mod._price_bucket(1999) == "MID"
        assert mod._price_bucket(5000) == "HIGH"
        assert mod._price_bucket(None) == "MISSING"

    def test_evidence_never_enters_features(self):
        from commerce.offline_optimizer import extract_features, make_synthetic_bundle

        bundle = make_synthetic_bundle(
            creator_id=1, opportunity_id=600, evaluated_at=START,
            label="POSITIVE", outcome_state="PURCHASED",
        )
        feats = extract_features(bundle.input)
        assert set(feats.keys()) == set(__import__(
            "commerce.offline_optimizer", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES)
        for value in feats.values():
            assert "txn" not in str(value).lower()

    def test_missing_selected_abstains_via_extraction_error(self):
        import json

        from commerce.opportunity_optimization import build_optimization_input
        from commerce.offline_optimizer import extract_features

        bundles = _cohort(n=2)
        row = dict(bundles[0].ledger_row)
        snapshot = json.loads(row["decision_snapshot"])
        snapshot["selected"] = None
        row["decision_snapshot"] = json.dumps(snapshot)
        row["selected_definition_id"] = None
        row["selected_version"] = None
        with pytest.raises(ValueError):
            inp = build_optimization_input(ledger_row=row, evidence=bundles[0].evidence)
            extract_features(inp)


# ---------------------------------------------------------------------------
# I. Production isolation
# ---------------------------------------------------------------------------


class TestProductionIsolation:
    def test_no_production_importer(self):
        import pathlib

        root = pathlib.Path(__file__).parent.parent
        candidates = list((root / "commerce").glob("*.py")) + list((root / "workers").glob("*.py"))
        candidates += [root / "commerce" / "pipeline.py", root / "commerce" / "opportunity_engine.py"]
        hits: list[str] = []
        seen: set[str] = set()
        for path in candidates:
            if not path.exists() or path.name == "offline_optimizer.py":
                continue
            try:
                src = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "offline_optimizer" in src and str(path) not in seen:
                hits.append(path.name)
                seen.add(str(path))
        assert hits == []

    def test_scheduler_and_execution_paths_clean(self):
        import pathlib

        root = pathlib.Path(__file__).parent.parent
        for rel in ("workers/scheduler_worker.py", "workers/llm_worker.py",
                    "workers/send_worker.py", "commerce/opportunity_engine.py",
                    "commerce/opportunity_sealing.py", "commerce/opportunity_execution.py",
                    "commerce/execution.py", "commerce/pipeline.py"):
            path = root / rel
            if not path.exists():
                continue
            assert "offline_optimizer" not in path.read_text(encoding="utf-8"), rel

    def test_optimizer_has_no_production_side_effect_imports(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        # Scan imports/calls only: the module docstring legitimately names
        # forbidden production readers in prose prohibitions (same
        # convention as P3.5.4A contract tests).
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("opportunity_sealing", "opportunity_execution", "opportunity_engine",
                      "record_purchase", "enqueue_send", "execute_sealed", "create_offer",
                      "create_drop", "get_redis", "publish_event",
                      "generate_draft", "strategy_learning", "adaptive_optimization"):
            assert token not in joined, token
        # experiment_id/variant_id and Dropfans service calls must never
        # appear as code (prose mentions of the prohibition are allowed).
        assert "get_drop" not in joined and "dropfans.service" not in joined
        assert "from integrations" not in joined

    def test_optimizer_has_no_persistence(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        upper = src.upper()
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "ALTER TABLE"):
            assert token not in upper, token
        assert "get_pool" not in src

    def test_optimizer_dependency_light(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("sklearn", "numpy", "scipy", "pandas", "torch", "tensorflow", "xgboost"):
            assert token not in src, token

    def test_no_price_optimization_or_causal_claims(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert "No price optimization" in src or "never" in src.lower()
        assert "NOT causal" in src or "never claims" in src.lower() or "never" in src.lower()


# ---------------------------------------------------------------------------
# J. Synthetic fixture integrity
# ---------------------------------------------------------------------------


class TestSyntheticIntegrity:
    def test_synthetic_marker_present(self):
        from commerce.offline_optimizer import SYNTHETIC_MARKER

        bundles = _cohort(n=2)
        assert bundles[0].ledger_row.get("synthetic") == SYNTHETIC_MARKER
        assert bundles[0].evidence.get("synthetic") == SYNTHETIC_MARKER
        assert str(bundles[0].ledger_row.get("generation_id", "")).startswith("synthetic:")

    def test_synthetic_outcomes_not_production_evidence(self):
        import commerce.offline_optimizer as mod

        assert "NEVER production" in mod.__doc__ or "never production" in mod.__doc__.lower()
        assert "SYNTHETIC" in mod.__doc__

    def test_no_production_db_mutation(self):
        import commerce.offline_optimizer as mod

        src = open(mod.__file__, encoding="utf-8").read()
        assert "get_pool" not in src
        upper = src.upper()
        assert "INSERT INTO" not in upper and "UPDATE " not in upper

    def test_synthetic_cohort_deterministic(self):
        first = [(b.input.opportunity_id, b.input.evaluated_at) for b in _cohort()]
        second = [(b.input.opportunity_id, b.input.evaluated_at) for b in _cohort()]
        assert first == second
        labels = [b.evidence["label"] for b in _cohort(n=4)]
        assert labels == ["POSITIVE", "COMMERCIAL_NEGATIVE", "POSITIVE", "COMMERCIAL_NEGATIVE"]

    def test_evidence_policy_version_consumed(self):
        from commerce.offline_optimizer import EXPECTED_MATURITY_POLICY_VERSION

        assert EXPECTED_MATURITY_POLICY_VERSION == "p353b.v1"
        bundles = _cohort(n=2)
        assert bundles[0].evidence["maturity_policy_version"] == "p353b.v1"
