"""P3.3.14.2 — Legacy PPV authority quarantine tests (unit, no provider/DB writes).

Proves worker no longer uses legacy selector for autonomous PPV and
propose_product_offer is advisory only.
"""

import ast
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.llm_tools import ToolAuthContext, dispatch_tool, ToolErrorCode
from core.conversation_state import ConversationState

pytestmark = [pytest.mark.unit]

WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"
TOOLS_PATH = Path(__file__).parent.parent / "core" / "llm_tools.py"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _fan_state():  # minimal FanCommercialState-like
    from commerce.fan_commercial_state import FanCommercialState

    return FanCommercialState(
        creator_id=1,
        user_id=10,
        purchase_count=0,
        total_spend_minor=0,
        average_order_value_minor=None,
        highest_purchase_minor=None,
        last_purchase_at=None,
        recent_purchase_count=0,
        recent_spend_minor=0,
        purchased_vault_ids=frozenset(),
        delivered_vault_ids=(),
        recent_offer_count=0,
        recent_rejected_offer_count=0,
        last_offer_at=None,
        recent_offered_vault_ids=(),
    )


def _history():
    from commerce.offer_history import OfferHistory

    return OfferHistory(
        creator_id=1,
        user_id=10,
        total_offer_count=0,
        recent_offer_count=0,
        last_offer_at=None,
        declined_offer_count=0,
        recent_declined_offer_count=0,
        state_counts=(),
        has_active_offer=False,
        active_offer_count=0,
        offered_vault_sets=(),
        active_vault_sets=(),
        null_snapshot_count=0,
    )


def _definition_row(**over):
    row = {
        "creator_id": 1,
        "id": 11,
        "stable_key": "alpha",
        "version": 1,
        "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    row.update(over)
    return row


def _conversation():
    return ConversationState(
        lifecycle="established",
        identity_already_established=True,
        current_topic="movie",
        recent_topics=("movie",),
        open_threads=("movie",),
        last_question=None,
        last_question_answered=False,
        consecutive_questions=0,
        tone="warm",
        last_user_fact=None,
        questions_in_last_3=0,
    )


class TestWorkerNoLegacySelector:
    @pytest.mark.asyncio
    async def test_worker_does_not_call_legacy_selector(self, monkeypatch):
        # Patch legacy selector to explode if called
        async def exploding_selector(*a, **kw):
            raise AssertionError("legacy selector called")

        monkeypatch.setattr("commerce.product_selection.resolve_commerce_product_with_history", exploding_selector, raising=False)
        # Also patch workers import path if exists
        monkeypatch.setattr("workers.llm_worker.resolve_commerce_product_with_history", exploding_selector, raising=False)

        # Mock opportunity engine to return no opportunity
        from commerce.opportunity_engine import OpportunityEngineResult

        fake_result = OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            owned_vault_ids=frozenset(),
            candidates=(),
            eligible_candidates=(),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_CANDIDATES",
        )
        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", AsyncMock(return_value=fake_result))
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1)))

        from workers.llm_worker import _try_commerce_draft

        # Use deterministic context
        res = await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        # Should not have called legacy selector, should return None (no PPV)
        assert res is None

    @pytest.mark.asyncio
    async def test_worker_calls_opportunity_engine(self, monkeypatch):
        calls = []

        async def fake_evaluate(creator_id, user_id, cs, now):
            calls.append((creator_id, user_id, type(cs).__name__, now))
            from commerce.opportunity_engine import OpportunityEngineResult

            return OpportunityEngineResult(
                creator_id=creator_id,
                user_id=user_id,
                evaluated_at=now,
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                owned_vault_ids=frozenset(),
                candidates=(),
                eligible_candidates=(),
                ineligible=(),
                ranking_inputs=(),
                ranking_result=None,
                selected_candidate=None,
                has_opportunity=False,
                status="NO_CANDIDATES",
            )

        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", fake_evaluate)

        from commerce.single_creator import SingleCreatorStatus

        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("workers.llm_worker.resolve_single_application_creator", AsyncMock(return_value=mock_creator))
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))

        from workers.llm_worker import _try_commerce_draft

        await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        assert len(calls) == 1
        assert calls[0][0] == 1 and calls[0][1] == 10

    @pytest.mark.asyncio
    async def test_no_opportunity_no_execute(self, monkeypatch):
        from commerce.opportunity_engine import OpportunityEngineResult

        fake_result = OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            owned_vault_ids=frozenset(),
            candidates=(),
            eligible_candidates=(),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_CANDIDATES",
        )
        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", AsyncMock(return_value=fake_result))
        from commerce.single_creator import SingleCreatorStatus

        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))

        # Patch legacy execution to explode if called
        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("resolve_and_run_commerce called")), raising=False)
        monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", AsyncMock(side_effect=AssertionError("run_commerce_pipeline called")), raising=False)
        monkeypatch.setattr("commerce.orchestrator.orchestrate_commerce", AsyncMock(side_effect=AssertionError("orchestrate_commerce called")), raising=False)
        monkeypatch.setattr("commerce.execution.execute_ppv", AsyncMock(side_effect=AssertionError("execute_ppv called")), raising=False)
        monkeypatch.setattr("commerce.dao.create_offer_serialized", AsyncMock(side_effect=AssertionError("create_offer called")), raising=False)

        from workers.llm_worker import _try_commerce_draft

        res = await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        assert res is None

    @pytest.mark.asyncio
    async def test_opportunity_found_no_execute(self, monkeypatch):
        from commerce.opportunity import candidate_from_definition
        from commerce.opportunity_engine import OpportunityEngineResult
        from commerce.opportunity_ranking import OpportunityRankingResult, RankedCandidate

        cand = candidate_from_definition(1, 10, _definition_row(), mapped_drop_ids=["drop_abc"])
        fake_ranked = OpportunityRankingResult(
            evaluated_at=NOW, creator_id=1, user_id=10, ranked=(RankedCandidate(11, "alpha", 1, ("STABLE_ID_TIEBREAK",), True, 0),), selected=RankedCandidate(11, "alpha", 1, ("STABLE_ID_TIEBREAK",), True, 0)
        )
        fake_result = OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            owned_vault_ids=frozenset(),
            candidates=(cand,),
            eligible_candidates=(cand,),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=fake_ranked,
            selected_candidate=cand,
            has_opportunity=True,
            status="RANKED",
        )
        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", AsyncMock(return_value=fake_result))
        from commerce.single_creator import SingleCreatorStatus

        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))

        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("should not call")), raising=False)
        monkeypatch.setattr("commerce.execution.execute_ppv", AsyncMock(side_effect=AssertionError("should not call execute_ppv")), raising=False)
        monkeypatch.setattr("commerce.dao.create_offer_serialized", AsyncMock(side_effect=AssertionError("should not call create_offer")), raising=False)
        monkeypatch.setattr("integrations.dropfans.service.get_drop", AsyncMock(side_effect=AssertionError("should not call get_drop")), raising=False)

        from workers.llm_worker import _try_commerce_draft

        res = await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        assert res is None

    @pytest.mark.asyncio
    async def test_engine_failure_no_legacy(self, monkeypatch):
        async def failing_evaluate(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", failing_evaluate)
        from commerce.single_creator import SingleCreatorStatus

        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))

        monkeypatch.setattr("commerce.product_selection.resolve_commerce_product_with_history", AsyncMock(side_effect=AssertionError("legacy called")), raising=False)
        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("legacy called")), raising=False)

        from workers.llm_worker import _try_commerce_draft

        res = await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        assert res is None


class TestToolQuarantine:
    @pytest.mark.asyncio
    async def test_propose_product_offer_no_commerce_execution(self, monkeypatch):
        auth = ToolAuthContext(creator_id=1, user_id=10, creator_sales_enabled=True)
        # Patch legacy commerce to explode if called
        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("resolve_and_run_commerce called")), raising=False)
        monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", AsyncMock(side_effect=AssertionError("run_commerce_pipeline called")), raising=False)
        monkeypatch.setattr("commerce.execution.execute_ppv", AsyncMock(side_effect=AssertionError("execute_ppv called")), raising=False)
        monkeypatch.setattr("commerce.dao.create_offer_serialized", AsyncMock(side_effect=AssertionError("create_offer called")), raising=False)
        monkeypatch.setattr("db.fangate.get_fangate_product", AsyncMock(return_value={"title": "t", "price_minor": 100}))
        monkeypatch.setattr("db.postgres.insert_tool_audit_log", AsyncMock(return_value=True))
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt-1"))

        result = await dispatch_tool("propose_product_offer", {"product_id": 1}, auth)
        assert not result.success
        assert result.error_code == ToolErrorCode.BUSINESS_RULE_REJECTED
        assert "Opportunity Engine" in result.safe_message

    @pytest.mark.asyncio
    async def test_propose_product_offer_with_valid_product_still_no_ppv(self, monkeypatch):
        auth = ToolAuthContext(creator_id=1, user_id=10, creator_sales_enabled=True)
        monkeypatch.setattr("db.fangate.get_fangate_product", AsyncMock(return_value={"title": "Valid", "price_minor": 850}))
        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("should not execute")), raising=False)
        monkeypatch.setattr("db.postgres.insert_tool_audit_log", AsyncMock(return_value=True))
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt-1"))

        result = await dispatch_tool("propose_product_offer", {"product_id": 1}, auth)
        assert not result.success
        assert "Opportunity Engine" in result.safe_message

    @pytest.mark.asyncio
    async def test_no_legacy_fallback_when_no_opportunity(self, monkeypatch):
        from commerce.opportunity_engine import OpportunityEngineResult

        fake_result = OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            owned_vault_ids=frozenset(),
            candidates=(),
            eligible_candidates=(),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_CANDIDATES",
        )
        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", AsyncMock(return_value=fake_result))
        from commerce.single_creator import SingleCreatorStatus

        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))
        # Make legacy explode if called
        monkeypatch.setattr("commerce.product_selection.resolve_commerce_product_with_history", AsyncMock(side_effect=AssertionError("legacy fallback")), raising=False)
        monkeypatch.setattr("commerce.integration.resolve_and_run_commerce", AsyncMock(side_effect=AssertionError("legacy fallback")), raising=False)

        from workers.llm_worker import _try_commerce_draft

        res = await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona")
        assert res is None


class TestStaticBoundary:
    def test_worker_does_not_import_legacy_selector(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        # Import line removed
        assert "from commerce.product_selection import resolve_commerce_product_with_history" not in src
        # No call to legacy selector in the three quarantined functions
        # Check that the function bodies do not contain the string
        quarantined_funcs = ["_get_canonical_commerce_evaluation", "_is_commerce_ppv_authorized_for_precedence", "_try_commerce_draft"]
        # Simple check: after quarantine, the worker file should have zero occurrences of resolve_commerce_product_with_history in the quarantined sections
        # The file may still have the string in comments, but not in active code; we assert the import is gone and the three functions don't call it
        assert src.count("resolve_commerce_product_with_history") == 0, "legacy selector still present"

    def test_worker_uses_opportunity_engine(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        assert "from commerce.opportunity_engine import evaluate_opportunity" in src or "commerce.opportunity_engine" in src
        assert "evaluate_opportunity" in src

    def test_tool_does_not_call_legacy_pipeline(self):
        src = TOOLS_PATH.read_text(encoding="utf-8")
        # Extract the propose handler
        start = src.find("async def _handle_propose_product_offer")
        handler_src = src[start : start + 3000]
        assert "resolve_and_run_commerce" not in handler_src
        assert "run_commerce_pipeline" not in handler_src
        assert "orchestrate_commerce" not in handler_src
        assert "execute_ppv" not in handler_src
        assert "create_offer" not in handler_src

    def test_no_feature_flag(self):
        worker_src = WORKER_PATH.read_text(encoding="utf-8")
        tools_src = TOOLS_PATH.read_text(encoding="utf-8")
        for tok in ["USE_OPPORTUNITY_ENGINE", "ENABLE_NEW_COMMERCE", "LEGACY_PPV_ENABLED", "opportunity_engine_enabled"]:
            assert tok not in worker_src
            assert tok not in tools_src

    def test_opportunity_engine_still_pure(self):
        eng_src = (Path(__file__).parent.parent / "commerce" / "opportunity_engine.py").read_text(encoding="utf-8")
        for tok in [
            "from integrations.dropfans",
            "from commerce.opportunity_sealing",
            "from commerce.execution import",
            "import redis",
            "from redis",
            "from db.redis",
            "execute_ppv(",
            "create_offer(",
            "get_drop(",
        ]:
            assert tok not in eng_src
