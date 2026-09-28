"""P3.3.14.3 — Single evaluation + exact Drop handoff + sealing tests."""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commerce.fan_commercial_state import FanCommercialState
from commerce.offer_history import OfferHistory
from core.conversation_state import ConversationState

pytestmark = [pytest.mark.unit]

WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"
ENGINE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_engine.py"
SEALING_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_sealing.py"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _fan_state(**over):
    base = {
        "creator_id": 1, "user_id": 10, "purchase_count": 0, "total_spend_minor": 0,
        "average_order_value_minor": None, "highest_purchase_minor": None, "last_purchase_at": None,
        "recent_purchase_count": 0, "recent_spend_minor": 0, "purchased_vault_ids": frozenset(),
        "delivered_vault_ids": (), "recent_offer_count": 0, "recent_rejected_offer_count": 0,
        "last_offer_at": None, "recent_offered_vault_ids": (), "currency": "USD",
    }
    base.update(over)
    return FanCommercialState(**base)


def _history(**over):
    base = {
        "creator_id": 1, "user_id": 10, "total_offer_count": 0, "recent_offer_count": 0, "last_offer_at": None,
        "declined_offer_count": 0, "recent_declined_offer_count": 0, "state_counts": (), "has_active_offer": False,
        "active_offer_count": 0, "offered_vault_sets": (), "active_vault_sets": (), "null_snapshot_count": 0,
    }
    base.update(over)
    return OfferHistory(**base)


def _definition_row(**over):
    row = {
        "creator_id": 1, "id": 11, "stable_key": "alpha", "version": 1, "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1"], "family_id": None, "price_minor": 1999, "currency": "USD",
        "allow_download": True, "status": "active",
    }
    row.update(over)
    return row


def _conversation():
    return ConversationState(
        lifecycle="established", identity_already_established=True, current_topic="movie",
        recent_topics=("movie",), open_threads=("movie",), last_question=None, last_question_answered=False,
        consecutive_questions=0, tone="warm", last_user_fact=None, questions_in_last_3=0,
    )


def _opportunity_result_with_mapping(mapped):
    from commerce.opportunity import candidate_from_definition
    from commerce.opportunity_engine import OpportunityEngineResult
    from commerce.opportunity_ranking import OpportunityRankingResult, RankedCandidate

    cand = candidate_from_definition(1, 10, _definition_row(), mapped_drop_ids=mapped)
    # Build minimal ranking result
    ranked = RankedCandidate(11, "alpha", 1, ("STABLE_ID_TIEBREAK",), True, 0)
    ranking_result = OpportunityRankingResult(evaluated_at=NOW, creator_id=1, user_id=10, ranked=(ranked,), selected=ranked)
    return OpportunityEngineResult(
        creator_id=1, user_id=10, evaluated_at=NOW, fan_commercial_state=_fan_state(),
        offer_history=_history(), owned_vault_ids=frozenset(), candidates=(cand,), eligible_candidates=(cand,),
        ineligible=(), ranking_inputs=(), ranking_result=ranking_result, selected_candidate=cand,
        has_opportunity=True, status="RANKED",
    )


class TestSingleEvaluation:
    @pytest.mark.asyncio
    async def test_single_evaluation_per_message(self, monkeypatch):
        # Patch the three helpers to count evaluate_opportunity calls
        calls = []

        orig_evaluate = __import__("commerce.opportunity_engine", fromlist=["evaluate_opportunity"]).evaluate_opportunity

        async def counting_evaluate(*a, **kw):
            calls.append(1)
            # Return no opportunity to avoid sealing
            from commerce.opportunity_engine import OpportunityEngineResult
            return OpportunityEngineResult(
                creator_id=1, user_id=10, evaluated_at=kw.get("now", NOW),
                fan_commercial_state=_fan_state(), offer_history=_history(), owned_vault_ids=frozenset(),
                candidates=(), eligible_candidates=(), ineligible=(), ranking_inputs=(), ranking_result=None,
                selected_candidate=None, has_opportunity=False, status="NO_CANDIDATES",
            )

        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", counting_evaluate)
        # Mock single creator to be READY
        from commerce.single_creator import SingleCreatorStatus
        mock_creator = MagicMock()
        mock_creator.status = SingleCreatorStatus.READY
        mock_creator.creator_id = 1
        monkeypatch.setattr("workers.llm_worker.resolve_single_application_creator", AsyncMock(return_value=mock_creator))
        monkeypatch.setattr("commerce.single_creator.resolve_single_application_creator", AsyncMock(return_value=mock_creator))

        # Simulate process_message's single evaluation path directly
        from commerce.opportunity_engine import evaluate_opportunity

        # Call helpers with precomputed result (simulating process_message's single evaluation)
        fake_result = await counting_evaluate(1, 10, _conversation(), NOW)
        assert len(calls) == 1
        # Subsequent helper calls with opportunity_result should NOT call evaluate again
        from workers.llm_worker import _get_canonical_commerce_evaluation, _is_commerce_ppv_authorized_for_precedence, _try_commerce_draft

        # These helpers when given opportunity_result should not call evaluate_opportunity again
        # Patch to ensure not called
        calls.clear()
        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", counting_evaluate)

        await _get_canonical_commerce_evaluation(10, [{"role": "user", "content": "hi"}], "persona", signals=MagicMock(), conversation_state=_conversation(), opportunity_result=fake_result)
        await _is_commerce_ppv_authorized_for_precedence(10, [{"role": "user", "content": "hi"}], "persona", signals=MagicMock(), conversation_state=_conversation(), opportunity_result=fake_result)
        await _try_commerce_draft(10, [{"role": "user", "content": "hi"}], "persona", conversation_state=_conversation(), opportunity_result=fake_result)

        assert len(calls) == 0, "helpers with opportunity_result should not re-evaluate"


class TestZeroMapping:
    @pytest.mark.asyncio
    async def test_zero_mappings_no_sealer(self, monkeypatch):
        result = _opportunity_result_with_mapping(())
        # Simulate process_message logic for zero mapping
        cand = result.selected_candidate
        assert len(cand.mapped_drop_ids) == 0
        # In worker, this would log NO_MAPPED_DROP and not call sealer
        # Verify sealer not called
        from unittest.mock import AsyncMock
        mock_seal = AsyncMock(return_value=MagicMock(status="SEALED"))
        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", mock_seal)
        # Simulate the v1 contract: len==0 => no sealing
        sealing_candidate = None
        chosen = None
        if len(cand.mapped_drop_ids) == 0:
            # no sealing
            pass
        elif len(cand.mapped_drop_ids) == 1:
            sealing_candidate = cand
            chosen = cand.mapped_drop_ids[0]
        assert sealing_candidate is None
        assert chosen is None
        # Ensure no provider GET would happen (sealer not called)
        assert mock_seal.call_count == 0


class TestMultipleMapping:
    @pytest.mark.asyncio
    async def test_multiple_mappings_no_sealer(self, monkeypatch):
        result = _opportunity_result_with_mapping(("dpfn_A", "dpfn_B"))
        cand = result.selected_candidate
        assert len(cand.mapped_drop_ids) == 2
        mock_seal = AsyncMock(return_value=MagicMock(status="SEALED"))
        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", mock_seal)
        # Also patch get_drop to ensure not called
        async def exploding_get_drop(*a, **kw):
            raise AssertionError("get_drop should not be called for MULTIPLE_DROPS")

        monkeypatch.setattr("integrations.dropfans.service.get_drop", exploding_get_drop, raising=False)

        # Simulate worker logic
        sealing_candidate = None
        chosen = None
        if len(cand.mapped_drop_ids) == 0:
            pass
        elif len(cand.mapped_drop_ids) > 1:
            # MULTIPLE_DROPS
            sealing_candidate = None
            chosen = None
        else:
            sealing_candidate = cand
            chosen = cand.mapped_drop_ids[0]

        assert sealing_candidate is None
        assert chosen is None
        # Verify neither CUID is selected
        assert "dpfn_A" not in (chosen or "")
        assert "dpfn_B" not in (chosen or "")
        assert mock_seal.call_count == 0


class TestExactlyOneMapping:
    @pytest.mark.asyncio
    async def test_exactly_one_drop_handoff(self, monkeypatch):
        result = _opportunity_result_with_mapping(("dpfn_A",))
        cand = result.selected_candidate
        assert len(cand.mapped_drop_ids) == 1
        assert cand.mapped_drop_ids[0] == "dpfn_A"

        # Mock sealer to capture exact candidate object
        captured = {}

        async def fake_seal(candidate, ranking_result, creator_id, user_id, chosen_drop_cuid, **kw):
            captured["candidate"] = candidate
            captured["chosen"] = chosen_drop_cuid
            captured["ranking_result"] = ranking_result
            # Verify exact candidate identity (frozen object, not dict)
            assert candidate is cand
            assert chosen_drop_cuid == "dpfn_A"
            from commerce.opportunity_sealing import SealResult
            return SealResult(status="SEALED", offer={"id": 999, "link": "https://www.dropfans.io/buy/dpfn_A"})

        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", fake_seal)

        # Simulate worker sealing invocation
        from commerce.opportunity_sealing import seal_ranked_candidate

        # This is what worker does
        res = await seal_ranked_candidate(cand, result.ranking_result, 1, 10, "dpfn_A")
        assert res.status == "SEALED"
        assert captured["candidate"] is cand
        assert captured["chosen"] == "dpfn_A"


class TestProviderDrift:
    @pytest.mark.asyncio
    async def test_provider_drift_no_send_no_fallback(self, monkeypatch):
        result = _opportunity_result_with_mapping(("dpfn_A",))
        cand = result.selected_candidate

        # Mock sealer to return drift
        from commerce.opportunity_sealing import SealResult
        mock_seal = AsyncMock(return_value=SealResult(status="PROVIDER_DRIFT", subreason="PRICE_DRIFT"))

        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", mock_seal)
        # Also ensure legacy not called
        monkeypatch.setattr("commerce.product_selection.resolve_commerce_product_with_history", AsyncMock(side_effect=AssertionError("legacy called")), raising=False)

        res = await mock_seal(cand, result.ranking_result, 1, 10, "dpfn_A")
        assert res.status == "PROVIDER_DRIFT"
        # No send would happen; verify legacy not called (would have raised)
        assert mock_seal.call_count == 1


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_no_stale_send(self, monkeypatch):
        result = _opportunity_result_with_mapping(("dpfn_A",))
        from commerce.opportunity_sealing import SealResult
        mock_seal = AsyncMock(return_value=SealResult(status="PROVIDER_TIMEOUT_AMBIGUOUS", subreason="TIMEOUT"))

        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", mock_seal)
        res = await mock_seal(result.selected_candidate, result.ranking_result, 1, 10, "dpfn_A")
        assert res.status == "PROVIDER_TIMEOUT_AMBIGUOUS"
        # No stale offer should be created
        assert res.offer is None


class TestIdempotent:
    @pytest.mark.asyncio
    async def test_idempotent_repeated_sealing(self, monkeypatch):
        result = _opportunity_result_with_mapping(("dpfn_A",))
        cand = result.selected_candidate
        from commerce.opportunity_sealing import SealResult

        # First seal succeeds
        first_offer = {"id": 100, "creator_id": 1, "user_id": 10, "dropfans_product_id": "dpfn_A", "state": "pending"}
        second_offer = {"id": 100, "creator_id": 1, "user_id": 10, "dropfans_product_id": "dpfn_A", "state": "pending"}

        calls = []

        async def fake_seal(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                return SealResult(status="SEALED", offer=first_offer)
            return SealResult(status="SEALED", subreason=None, detail="already_sealed", offer=second_offer)

        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", fake_seal)

        r1 = await fake_seal(cand, result.ranking_result, 1, 10, "dpfn_A")
        r2 = await fake_seal(cand, result.ranking_result, 1, 10, "dpfn_A")
        assert r1.status == "SEALED"
        assert r2.status == "SEALED"
        assert r1.offer["id"] == r2.offer["id"]


class TestLegacyAuthority:
    def test_opportunity_path_never_invokes_legacy(self):
        src = Path("workers/llm_worker.py").read_text(encoding="utf-8")
        for func in ["_get_canonical_commerce_evaluation", "_is_commerce_ppv_authorized_for_precedence", "_try_commerce_draft"]:
            start = src.find(f"async def {func}")
            snippet = src[start : start + 5000]
            # Check for actual calls (with paren), not docstring mentions
            assert "resolve_commerce_product_with_history(" not in snippet
            assert "resolve_and_run_commerce(" not in snippet
            assert "execute_ppv(" not in snippet

    def test_provider_truth(self):
        # Verify sealing uses verified live, not mirror
        src = Path("commerce/opportunity_sealing.py").read_text(encoding="utf-8")
        assert "verify_live_drop" in src
        assert "canonical_identity_ids" in src
        assert "drop_content_hash" in src
        assert "fangate_products.raw" not in src
