"""P3.6 — Production Evidence Accrual & Optimizer Readiness (unit, read-only).

Proves the production evidence pipeline can reconstruct frozen decision
facts, exposure, maturity, and outcomes for future offline-readiness
gates — without touching v1 authority, pricing, sealing, sending,
experiments, or the offline prototype (which remains unimported by all
production files, including the readiness module itself).

No DB, no provider, no Redis, no Dropfans, no writes. Rows are built with
the real production builders (ledger snapshot, evidence classifier,
frozen-input contract), never with synthetic prototype fixtures.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.unit]

UTC = timezone.utc
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
EVAL = NOW - timedelta(days=10)
OUTCOME_AT = NOW - timedelta(days=8)
SEND_AT = NOW - timedelta(days=9)


def candidate(definition_id=11, version=2, **over):
    base = {
        "definition_id": definition_id,
        "version": version,
        "stable_key": "alpha" if definition_id == 11 else "beta",
        "offer_type": "SMALL_BUNDLE" if definition_id == 11 else "SINGLE",
        "canonical_vault_item_ids": ["V1", "V2"] if definition_id == 11 else ["V3"],
        "price_minor": 1999 if definition_id == 11 else 999,
        "currency": "USD",
        "mapped_drop_ids": ["drop_x"] if definition_id == 11 else ["drop_y"],
    }
    base.update(over)
    return base


def opportunity_result(creator_id=1, user_id=10, **over):
    base = {
        "eligible_candidates": [candidate(), candidate(12, 1)],
        "ineligible": [],
        "ranking_result": {
            "policy_version": "v1",
            "ranked": [
                {"definition_id": 11, "factors": ["NOVEL_CANONICAL_SET"]},
                {"definition_id": 12, "factors": ["STABLE_ID_TIEBREAK"]},
            ],
        },
        "selected_candidate": candidate(),
        "has_opportunity": True,
        "status": "DECIDED",
        "fan_commercial_state": {
            "creator_id": creator_id,
            "user_id": user_id,
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
        "offer_history": {
            "creator_id": creator_id,
            "user_id": user_id,
            "total_offer_count": 3,
            "recent_offer_count": 1,
            "last_offer_at": "2026-08-28T12:00:00+00:00",
            "declined_offer_count": 1,
            "recent_declined_offer_count": 0,
            "state_counts": {"pending": 1, "declined": 1, "purchased": 1},
            "has_active_offer": False,
            "active_offer_count": 0,
            "offered_vault_sets": [["V1", "V2"]],
            "active_vault_sets": [],
            "null_snapshot_count": 0,
            "definition_identity_available": False,
        },
        "ranking_conversation": {
            "lifecycle": "established",
            "current_topic": "movie",
            "recent_topics": [],
            "open_threads": [],
        },
    }
    base.update(over)
    return base


def ledger_row(oid=5, creator_id=1, user_id=10, **over):
    from commerce.opportunity_ledger import build_decision_snapshot

    snapshot = build_decision_snapshot(opportunity_result(creator_id, user_id))
    base = {
        "opportunity_id": oid,
        "creator_id": creator_id,
        "user_id": user_id,
        "generation_id": f"gen-{oid}",
        "evaluated_at": EVAL,
        "decision_snapshot": json.dumps(snapshot, sort_keys=True),
        "selected_definition_id": 11,
        "selected_version": 2,
        "selected_stable_key": "alpha",
        "decision_status": "SEALED",
        "sealed_offer_id": 40 + oid,
        "reengagement_of": None,
        "outcome_state": "PENDING",
        "outcome_at": None,
        "transaction_id": None,
        "purchased_price_minor": None,
        "purchased_currency": None,
        "attribution_status": "unattributed",
        "attribution_confidence": "full",
        "exposure_state": "NONE",
        "exposure_at": None,
        "exposure_source": None,
    }
    base.update(over)
    return base


def sent_row(oid=5, **over):
    base = ledger_row(oid, **over)
    base.update(
        outcome_state="SENT",
        outcome_at=SEND_AT,
        exposure_state="SENT",
        exposure_at=SEND_AT,
        exposure_source="sealed_execution",
    )
    return base


def purchased_row(oid=5, **over):
    base = sent_row(oid, **over)
    base.update(
        outcome_state="PURCHASED",
        outcome_at=OUTCOME_AT,
        transaction_id=f"txn-{oid}",
        purchased_price_minor=1999,
        purchased_currency="USD",
        attribution_status="attributed",
    )
    return base


def declined_row(oid=5, outcome="DECLINED", **over):
    base = sent_row(oid, **over)
    base.update(outcome_state=outcome, outcome_at=OUTCOME_AT)
    return base


def summarize(rows):
    from commerce.optimizer_readiness import summarize_rows

    return summarize_rows(rows, as_of=NOW)


# ---------------------------------------------------------------------------
# Evidence: snapshot completeness, exposure, SENT, failures, attribution
# ---------------------------------------------------------------------------


class TestEvidencePath:
    def test_snapshot_captures_required_identity(self):
        row = ledger_row()
        snapshot = json.loads(row["decision_snapshot"])
        assert snapshot["selected"]["definition_id"] == 11
        assert snapshot["selected"]["version"] == 2
        assert snapshot["ranking"]["policy_version"] == "v1"
        assert snapshot["ranking"]["ranked_order"] == [11, 12]
        assert len(snapshot["eligible"]) == 2

    def test_snapshot_carries_fan_history_conversation(self):
        row = ledger_row()
        snapshot = json.loads(row["decision_snapshot"])
        assert snapshot["fan"]["purchase_count"] == 2
        assert snapshot["fan"]["purchased_vault_ids"] == ["V9"]
        assert snapshot["history"]["total_offer_count"] == 3
        assert snapshot["conversation"]["lifecycle"] == "established"

    def test_exposure_linkage_single_opportunity(self):
        row = purchased_row()
        assert row["sealed_offer_id"] == 45
        assert row["exposure_state"] == "SENT"
        assert row["exposure_at"] == SEND_AT
        assert row["exposure_source"] == "sealed_execution"

    def test_sent_means_recorded_send_not_delivery(self):
        from commerce.optimizer_readiness import diagnose_row

        diag = diagnose_row(sent_row(), as_of=NOW)
        assert diag.exposure_state == "SENT"
        assert diag.evidence_label == "CENSORED"  # mature but unresolved: never negative

    def test_failed_send_never_sent(self):
        row = ledger_row(
            outcome_state="SEND_FAILED",
            outcome_at=OUTCOME_AT,
            exposure_state="SEND_ATTEMPTED",
            exposure_at=SEND_AT,
            exposure_source="sealed_execution",
        )
        from commerce.optimizer_readiness import diagnose_row

        diag = diagnose_row(row, as_of=NOW)
        assert diag.exposure_state == "SEND_ATTEMPTED"
        assert diag.exclusion == "process_negative"
        assert diag.eligible_primary is False

    def test_purchase_attribution_single_winner(self):
        report = summarize([purchased_row(5), purchased_row(6, sealed_offer_id=45)])
        assert report.n_pos == 2  # distinct offers: distinct winners
        dupe = summarize([purchased_row(5), purchased_row(6, sealed_offer_id=45, transaction_id="txn-5")])
        assert dupe.multiple_purchase_offers == ((1, 45),)

    def test_mature_negatives_eligible_process_separate(self):
        report = summarize([declined_row(5), declined_row(6, outcome="EXPIRED")])
        assert (report.n_neg, report.n_primary) == (2, 2)
        assert report.n_process_negative == 0

    def test_censored_stays_censored(self):
        report = summarize([sent_row(5)])
        assert report.n_censored == 1 and report.n_primary == 0

    def test_creator_isolation_in_evidence(self):
        from commerce.optimizer_readiness import diagnose_row

        row = ledger_row(creator_id=2)
        diag = diagnose_row(row, as_of=NOW)
        assert diag.creator_id == 2
        report = summarize([ledger_row(creator_id=1), ledger_row(oid=6, creator_id=2)])
        assert report.n_creators == 2
        by_creator = {c.creator_id: c for c in report.creators}
        assert by_creator[1].n_rows == 1 and by_creator[2].n_rows == 1

    def test_malformed_evidence_fail_closed(self):
        from commerce.optimizer_readiness import diagnose_row

        diag = diagnose_row({"opportunity_id": 1}, as_of=NOW)
        assert diag.eligible_primary is False
        assert diag.exclusion in ("unavailable", "input_unavailable")
        assert diag.binary is None


# ---------------------------------------------------------------------------
# Historical reconstruction: frozen inputs without live state
# ---------------------------------------------------------------------------


class TestHistoricalReconstruction:
    def test_frozen_input_reconstructs(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence
        from commerce.opportunity_optimization import build_optimization_input

        row = purchased_row()
        evidence = classify_opportunity_evidence(row, as_of=NOW)
        inp = build_optimization_input(ledger_row=row, evidence=evidence)
        assert inp.creator_id == 1 and inp.opportunity_id == 5
        assert inp.policy_version == "v1"
        assert inp.selected_definition_id == 11
        assert inp.ownership_context.owned_vault_ids == frozenset({"V9"})

    def test_frozen_ownership_not_current(self):
        import commerce.optimizer_readiness as mod

        src = open(mod.__file__, encoding="utf-8").read()
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("fetch_owned_vault_ids", "get_offer_definition", "dropfans.service",
                      "get_redis", "llm_provider", "ppv_analytics"):
            assert token not in joined, token

    def test_frozen_candidate_identity(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence
        from commerce.opportunity_optimization import build_optimization_input

        row = purchased_row()
        evidence = classify_opportunity_evidence(row, as_of=NOW)
        inp = build_optimization_input(ledger_row=row, evidence=evidence)
        assert inp.frozen_candidates[0].identity() == (11, 2)
        assert inp.frozen_candidates[0].price_minor == 1999

    def test_no_future_outcome_leakage_into_input(self):
        import dataclasses

        from commerce.opportunity_optimization import OptimizationInput

        blob = str([{f.name} for f in dataclasses.fields(OptimizationInput)])
        assert "transaction_id" not in blob
        assert "outcome_state" not in blob

    def test_family_gap_documented_not_fabricated(self):
        from commerce.optimizer_readiness import FEATURE_READINESS_NOTES

        notes = dict((name, (status, note)) for name, status, note in FEATURE_READINESS_NOTES)
        assert notes["family_presence"][0] == "degraded"
        assert "never fabricated" in notes["family_presence"][1]
        # All other features suppliable from frozen snapshots.
        for name, (status, _note) in notes.items():
            if name != "family_presence":
                assert status == "suppliable", name

    def test_feature_names_mirror_prototype(self):
        from commerce.optimizer_readiness import EXPECTED_FEATURE_SCHEMA, FEATURE_NAMES

        assert EXPECTED_FEATURE_SCHEMA == "p356.features.v1"
        assert len(FEATURE_NAMES) == 16
        assert "family_presence" in FEATURE_NAMES


# ---------------------------------------------------------------------------
# Readiness: counts, floors, holdout, exclusions
# ---------------------------------------------------------------------------


class TestReadinessGates:
    def test_global_counts(self):
        report = summarize([
            purchased_row(5), purchased_row(6),
            declined_row(7), declined_row(8, outcome="EXPIRED"),
            sent_row(9),
        ])
        assert report.n_rows == 5
        assert report.n_sent == 5
        assert (report.n_primary, report.n_pos, report.n_neg) == (4, 2, 2)
        assert report.n_censored == 1
        assert report.n_creators == 1

    def test_prototype_floors_mirrored(self):
        from commerce.optimizer_readiness import (
            MIN_TRAIN_NEGATIVE,
            MIN_TRAIN_POSITIVE,
            MIN_TRAIN_TOTAL,
        )

        assert (MIN_TRAIN_TOTAL, MIN_TRAIN_POSITIVE, MIN_TRAIN_NEGATIVE) == (6, 2, 2)

    def test_floors_not_met_on_empty(self):
        report = summarize([purchased_row(5)])
        creator = report.creators[0]
        assert creator.floors_met is False
        assert "primary=1" in creator.floors_detail
        assert creator.holdout_feasible is False

    def test_floors_met_and_holdout_feasible(self):
        rows = [purchased_row(oid) if i % 2 == 0 else declined_row(oid)
                for i, oid in enumerate(range(5, 17))]
        # Spread evaluations across distinct days for chronological span.
        spread = []
        for i, row in enumerate(rows):
            row = dict(row)
            row["evaluated_at"] = NOW - timedelta(days=12 - i)
            spread.append(row)
        report = summarize(spread)
        creator = report.creators[0]
        assert creator.n_primary == 12
        assert creator.floors_met is True
        assert creator.holdout_feasible is True
        assert creator.span_hours is not None and creator.span_hours > 0

    def test_holdout_requires_temporal_separation(self):
        rows = [purchased_row(oid) if i % 2 == 0 else declined_row(oid)
                for i, oid in enumerate(range(5, 17))]
        report = summarize(rows)  # all share one evaluated_at
        assert report.creators[0].holdout_feasible is False
        assert report.creators[0].holdout_detail == "insufficient_chronological_span"

    def test_recovered_excluded(self):
        row = purchased_row()
        row = dict(row)
        row["generation_id"] = "recovered:1:45"
        snapshot = json.loads(row["decision_snapshot"])
        snapshot["recovery"] = {"recovered": True}
        row["decision_snapshot"] = json.dumps(snapshot, sort_keys=True)
        report = summarize([row])
        assert report.n_recovered == 1 and report.n_primary == 0

    def test_partial_excluded(self):
        from commerce.optimizer_readiness import diagnose_row

        row = purchased_row()
        row = dict(row)
        row["generation_id"] = "recovered:1:45"
        diag = diagnose_row(row, as_of=NOW)
        assert diag.exclusion == "recovered"  # recovery implies PARTIAL quality

    def test_reengagement_child_separate(self):
        parent = purchased_row(5)
        child = dict(sent_row(6, sealed_offer_id=45))
        child["reengagement_of"] = 5
        report = summarize([parent, child])
        assert report.n_children == 1
        assert report.n_primary == 1  # parent only
        assert report.multiple_purchase_offers == ()

    def test_synthetic_excluded(self):
        row = dict(purchased_row())
        row["generation_id"] = "synthetic:1:5"
        row["synthetic"] = "SYNTHETIC_P356_FIXTURE"
        report = summarize([row])
        assert report.n_synthetic == 1 and report.n_primary == 0

    def test_unavailable_excluded(self):
        report = summarize([{"opportunity_id": 1}])
        assert report.n_unavailable == 1 and report.n_primary == 0

    def test_no_optimistic_defaults(self):
        from commerce.optimizer_readiness import diagnose_row

        for bad in (None, {}, {"opportunity_id": 1, "creator_id": 1}):
            diag = diagnose_row(bad, as_of=NOW)
            assert diag.eligible_primary is False and diag.binary is None

    def test_naive_as_of_raises(self):
        from commerce.optimizer_readiness import diagnose_row, summarize_rows

        with pytest.raises(ValueError):
            diagnose_row(ledger_row(), as_of=datetime(2026, 9, 14))
        with pytest.raises(ValueError):
            summarize_rows([ledger_row()], as_of=datetime(2026, 9, 14))

    def test_parity_with_prototype_labels(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence
        from commerce.optimizer_readiness import diagnose_row
        from commerce.offline_optimizer import build_supervised_label

        cases = [
            purchased_row(5), declined_row(6), declined_row(7, outcome="EXPIRED"),
            sent_row(8),
            ledger_row(9, outcome_state="SEND_FAILED", outcome_at=OUTCOME_AT,
                       exposure_state="SEND_ATTEMPTED", exposure_at=SEND_AT,
                       exposure_source="sealed_execution"),
        ]
        for row in cases:
            evidence = classify_opportunity_evidence(row, as_of=NOW)
            proto = build_supervised_label(evidence=evidence, ledger_row=row)
            diag = diagnose_row(row, as_of=NOW)
            proto_eligible = proto.kind in ("PURCHASED", "DECLINED", "EXPIRED")
            assert diag.eligible_primary == proto_eligible, row["opportunity_id"]
            if proto_eligible:
                assert diag.binary == proto.binary


# ---------------------------------------------------------------------------
# Production isolation: prototype stays offline, readiness stays authority-free
# ---------------------------------------------------------------------------


class TestProductionIsolation:
    def test_prototype_still_unimported(self):
        import pathlib

        root = pathlib.Path(__file__).parent.parent
        hits = []
        for path in list((root / "commerce").glob("*.py")) + list((root / "workers").glob("*.py")):
            if path.name in ("offline_optimizer.py",):
                continue
            try:
                src = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "offline_optimizer" in src:
                hits.append(path.name)
        assert hits == []

    def test_readiness_has_no_authority_tokens(self):
        import commerce.optimizer_readiness as mod

        src = open(mod.__file__, encoding="utf-8").read()
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("opportunity_sealing", "opportunity_execution", "opportunity_engine",
                      "record_purchase", "enqueue_send", "execute_sealed", "create_offer",
                      "create_drop", "generate_draft", "strategy_learning",
                      "Experiment", "experiment_id", "variant_id"):
            assert token not in joined, token

    def test_readiness_read_only_statements(self):
        import re

        import commerce.optimizer_readiness as mod

        src = open(mod.__file__, encoding="utf-8").read()
        upper = src.upper()
        assert "SELECT " in upper
        for token in ("INSERT INTO", "DELETE FROM", "CREATE TABLE", "DROP TABLE", "ALTER TABLE"):
            assert token not in upper, token
        # No SQL data-modification statement: scan logical lines for a
        # statement keyword at line start (prose mentions are allowed).
        for line in src.splitlines():
            assert re.match(r"^\s*(UPDATE|INSERT|DELETE|CREATE|DROP|ALTER)\b", line, re.IGNORECASE) is None, line
        assert "UPDATE commerce_opportunity" not in upper

    def test_no_scores_or_weights_in_readiness(self):
        import dataclasses

        import commerce.optimizer_readiness as mod

        fields = set()
        for cls in (mod.RowDiagnosis, mod.CreatorReadiness, mod.GlobalReadiness):
            fields |= {f.name for f in dataclasses.fields(cls)}
        for forbidden in ("score", "weight", "probability", "price_minor", "elasticity",
                          "uplift", "revenue", "experiment", "variant"):
            assert not any(forbidden in f for f in fields), forbidden
        src = open(mod.__file__, encoding="utf-8").read().lower()
        assert "log-odds" not in src and "sigmoid" not in src

    def test_fetch_validates_without_db(self):
        import pytest

        from commerce.optimizer_readiness import fetch_opportunity_rows

        async def _run():
            with pytest.raises(ValueError):
                await fetch_opportunity_rows(creator_id=0)
            with pytest.raises(ValueError):
                await fetch_opportunity_rows(creator_id=1, evaluated_after=datetime(2026, 9, 1))

        import asyncio

        asyncio.run(_run())

    def test_no_migration_created(self):
        import pathlib

        migrations = list(pathlib.Path("db/migrations").glob("*p36*"))
        assert migrations == []
