"""P3.5.4B — Historical advisory dry-run harness (unit, no DB/provider/Redis).

Proves the bounded sweep reconstructs frozen inputs, classifies agreement /
divergence per row with isolation, and cannot reach commerce side effects.
Advisors here are test doubles; no optimizer exists.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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


def ledger_row(oid=5, **over):
    base = {
        "opportunity_id": oid, "creator_id": 1, "user_id": 10,
        "generation_id": f"gen-{oid}", "evaluated_at": NOW,
        "decision_snapshot": json.dumps(snapshot()),
        "selected_definition_id": 11, "selected_version": 2,
        "selected_stable_key": "alpha", "decision_status": "SEALED",
        "sealed_offer_id": 40 + oid, "reengagement_of": None,
        "outcome_state": "PENDING", "outcome_at": None,
        "attribution_status": "unattributed", "attribution_confidence": "full",
        "exposure_state": "NONE", "exposure_at": None, "exposure_source": None,
    }
    base.update(over)
    return base


class FakeConn:
    def __init__(self, fetches=None):
        self._fetches = list(fetches or [])
        self.calls: list[tuple] = []

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
        if not self._fetches:
            return []
        return self._fetches.pop(0)

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        return None

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))
        return "UPDATE 0"


def make_pool(conn):
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def patch_dry_run_pool(monkeypatch, conn):
    monkeypatch.setattr(
        "commerce.opportunity_dry_run.get_pool", AsyncMock(return_value=make_pool(conn))
    )
    return conn


def advisor_for(scores):
    from commerce.opportunity_optimization import AdvisoryOptimizationResult

    def _advise(inp):
        return AdvisoryOptimizationResult(
            creator_id=inp.creator_id, opportunity_id=inp.opportunity_id,
            candidate_scores=tuple(scores), optimizer_version="test-0",
            input_policy_version=inp.policy_version,
        )

    return _advise


class TestDryRunPopulation:
    @pytest.mark.asyncio
    async def test_bounded_creator_scoped_ordered_query(self, monkeypatch):
        conn = patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, limit=500)
        assert out.total == 0
        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "creator_id = $1" in sql
        assert "ORDER BY creator_id ASC, opportunity_id ASC" in sql
        assert "LIMIT $" in sql and params[0] == 1 and params[-1] == 200  # capped

    @pytest.mark.asyncio
    async def test_evaluated_range_supported(self, monkeypatch):
        conn = patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[]]))
        from commerce.opportunity_dry_run import run_dry_run
        from datetime import timedelta

        await run_dry_run(creator_id=1, limit=10,
                          evaluated_after=NOW - timedelta(days=30),
                          evaluated_before=NOW)
        sql = conn.calls[0][1]
        assert "evaluated_at >= $2" in sql and "evaluated_at < $3" in sql

    @pytest.mark.asyncio
    async def test_invalid_scope_raises(self, monkeypatch):
        patch_dry_run_pool(monkeypatch, FakeConn())
        from commerce.opportunity_dry_run import run_dry_run

        with pytest.raises(ValueError):
            await run_dry_run(creator_id=0)
        with pytest.raises(ValueError):
            await run_dry_run(creator_id=1, as_of=datetime(2026, 9, 1))


class TestDryRunClassification:
    @pytest.mark.asyncio
    async def test_agreement_and_divergence(self, monkeypatch):
        rows = [ledger_row(5), ledger_row(6)]
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[rows]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((12, 1, 0.8),)))
        assert (out.total, out.agreements, out.divergences) == (2, 0, 2)
        assert out.recommendations == 2 and out.v1_selections == 2

    @pytest.mark.asyncio
    async def test_agreement_path(self, monkeypatch):
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5)]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        assert (out.agreements, out.divergences) == (1, 0)
        row = out.rows[0]
        assert row.classification == "AGREEMENT"
        assert row.v1_candidate == (11, 2) and row.advisory_candidate == (11, 2)

    @pytest.mark.asyncio
    async def test_abstention_default_without_advisor(self, monkeypatch):
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5)]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1)
        assert (out.abstentions, out.recommendations) == (1, 0)
        assert out.rows[0].classification == "ADVISORY_ABSTAIN"

    @pytest.mark.asyncio
    async def test_no_v1_selection_counted_separately(self, monkeypatch):
        snap = snapshot()
        snap["selected"] = None
        row = ledger_row(5, selected_definition_id=None, selected_version=None,
                         decision_status="NO_SELECTION",
                         decision_snapshot=json.dumps(snap))
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[row]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        assert out.no_v1_selection == 1 and out.divergences == 0 and out.agreements == 0

    @pytest.mark.asyncio
    async def test_recovered_rows_separate_cohort(self, monkeypatch):
        snap = snapshot()
        snap["eligible"] = []
        snap["selected"] = None
        snap["ranking"] = {"policy_version": None, "ranked_order": [], "factors": {}}
        snap["recovery"] = {"recovered": True}
        row = ledger_row(5, generation_id="recovered:1:42",
                         decision_snapshot=json.dumps(snap),
                         selected_definition_id=None, selected_version=None,
                         attribution_confidence="partial")
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[row]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        assert out.recovered_count == 1
        assert out.agreements == 0 and out.divergences == 0
        assert out.rows[0].classification == "RECOVERED_INPUT"
        assert out.rows[0].recovered is True

    @pytest.mark.asyncio
    async def test_per_row_failure_isolation(self, monkeypatch):
        bad = {"opportunity_id": 6, "creator_id": 1}  # missing identity/snapshot
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5), bad]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        assert out.total == 2 and out.unavailable_inputs == 1 and out.agreements == 1
        kinds = {r.classification for r in out.rows}
        assert "INPUT_UNAVAILABLE" in kinds

    @pytest.mark.asyncio
    async def test_advisor_failure_is_validation_error(self, monkeypatch):
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5)]]))
        from commerce.opportunity_dry_run import run_dry_run

        def _boom(inp):
            raise RuntimeError("advisor down")

        out = await run_dry_run(creator_id=1, advisor=_boom)
        assert out.rows[0].classification == "VALIDATION_ERROR"
        assert out.rows[0].error and "advisor_failed" in out.rows[0].error

    @pytest.mark.asyncio
    async def test_async_advisor_supported(self, monkeypatch):
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5)]]))
        from commerce.opportunity_dry_run import run_dry_run

        async def _advise(inp):
            from commerce.opportunity_optimization import AdvisoryOptimizationResult

            return AdvisoryOptimizationResult(
                creator_id=inp.creator_id, opportunity_id=inp.opportunity_id,
                candidate_scores=((11, 2, 0.3),), optimizer_version="t",
                input_policy_version=inp.policy_version)

        out = await run_dry_run(creator_id=1, advisor=_advise)
        assert out.agreements == 1

    @pytest.mark.asyncio
    async def test_summary_counts_and_policy_breakdown(self, monkeypatch):
        rows = [ledger_row(5), ledger_row(6)]
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[rows]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((12, 1, 0.8),)))
        assert out.total == 2 and out.valid_inputs == 2
        assert out.policy_version_breakdown == (("v1", 2),)
        assert out.validator_rejections_by_gate == ()
        assert len(out.rows) == 2
        assert out.rows[0].governance_state == "UNEVALUABLE_FROM_HISTORY"

    def test_summary_has_no_revenue_metrics(self):
        import dataclasses

        from commerce.opportunity_dry_run import DryRunRowResult, DryRunSummary

        row_fields = {f.name for f in dataclasses.fields(DryRunRowResult)}
        summary_fields = {f.name for f in dataclasses.fields(DryRunSummary)}
        for forbidden in ("revenue", "uplift", "price", "elasticity", "conversion", "roi"):
            assert not any(forbidden in f for f in row_fields | summary_fields), forbidden


class TestDryRunReengagement:
    @pytest.mark.asyncio
    async def test_children_are_distinct_agreement_rows(self, monkeypatch):
        parent = ledger_row(5)
        child = ledger_row(6, reengagement_of=5, sealed_offer_id=42)
        patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[parent, child]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        assert out.total == 2 and out.agreements == 2
        assert out.rows[0].opportunity_id == 5 and out.rows[1].opportunity_id == 6


class TestDryRunIsolation:
    def test_no_commerce_side_effect_imports(self):
        import commerce.opportunity_dry_run as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("opportunity_sealing", "opportunity_execution", "record_purchase",
                      "record_offer_terminal", "mark_offer_", "enqueue_send", "execute_sealed",
                      "post_purchase", "create_offer", "create_drop", "Dropfans",
                      "get_redis", "publish_event", "llm_provider", "generate_draft",
                      "adaptive_optimization", "strategy_learning", "ppv_analytics"):
            assert token not in src, token

    def test_no_insert_update_post_statements(self):
        import commerce.opportunity_dry_run as mod
        import commerce.opportunity_validation as val

        combined = open(mod.__file__, encoding="utf-8").read() + open(val.__file__, encoding="utf-8").read()
        upper = combined.upper()
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "POST ", "PATCH ", " XADD"):
            assert token not in upper, token

    def test_no_sealing_import_in_dry_run_path(self):
        import commerce.opportunity_dry_run as mod
        import commerce.opportunity_validation as val

        for m in (mod, val):
            src = open(m.__file__, encoding="utf-8").read()
            assert "opportunity_sealing" not in src

    @pytest.mark.asyncio
    async def test_cross_creator_rows_never_mix(self, monkeypatch):
        other = ledger_row(7)
        other["creator_id"] = 2
        conn = patch_dry_run_pool(monkeypatch, FakeConn(fetches=[[ledger_row(5), other]]))
        from commerce.opportunity_dry_run import run_dry_run

        out = await run_dry_run(creator_id=1, advisor=advisor_for(((11, 2, 0.8),)))
        # Sweep query itself is creator-scoped; the foreign row models a
        # hypothetical leak and must fail closed, never validate.
        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "creator_id = $1" in sql and params[0] == 1
        kinds = {r.classification for r in out.rows}
        assert "AGREEMENT" in kinds
        foreign = [r for r in out.rows if r.opportunity_id == 7][0]
        assert foreign.classification in ("INPUT_UNAVAILABLE", "VALIDATION_ERROR",
                                          "ADVISORY_INVALID", "ADVISORY_ABSTAIN")

    def test_synthetic_generation_stays_creator_scoped(self):
        from commerce.opportunity_recovery import synthetic_generation_id

        assert synthetic_generation_id(1, 42) != synthetic_generation_id(2, 42)


class TestDryRunTemporal:
    @pytest.mark.asyncio
    async def test_post_evaluation_purchase_cannot_alter_validation(self, monkeypatch):
        from commerce.opportunity_validation import validate_advisory
        from commerce.opportunity_optimization import (
            AdvisoryOptimizationResult, build_optimization_input,
        )

        row = ledger_row(5)
        inp = build_optimization_input(ledger_row=row)
        adv = AdvisoryOptimizationResult(
            creator_id=1, opportunity_id=5, candidate_scores=((12, 1, 0.8),),
            optimizer_version="t", input_policy_version="v1")
        before = validate_advisory(inp, adv, sealed_offer_id=42)
        # Simulate a later purchase landing on the source row: rebuild from
        # the same frozen snapshot — validation is unchanged by construction.
        row["outcome_state"] = "PURCHASED"
        row["transaction_id"] = "txn-late"
        after = validate_advisory(
            build_optimization_input(ledger_row=row), adv, sealed_offer_id=42)
        assert (before.classification, after.classification) == ("VALID", "VALID")
        assert before.advisory_candidate == after.advisory_candidate == (12, 1)

    def test_transaction_ids_never_enter_input(self):
        import dataclasses

        from commerce.opportunity_optimization import OptimizationInput

        blob = str([{f.name} for f in dataclasses.fields(OptimizationInput)])
        assert "transaction_id" not in blob

    @pytest.mark.asyncio
    async def test_naive_as_of_rejected_by_harness(self):
        from datetime import datetime

        from commerce.opportunity_dry_run import run_dry_run

        with pytest.raises(ValueError):
            await run_dry_run(creator_id=1, as_of=datetime(2026, 9, 1))
