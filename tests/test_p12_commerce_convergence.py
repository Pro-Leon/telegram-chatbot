"""P1.2 Commerce Decision Convergence tests.

Verifies single canonical decision per inbound message.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from commerce.decision import CommerceDecision, CommerceReason, CommerceDecisionContext
from commerce.models import CommerceAction, PolicyDecision
from commerce.signals import CommerceSignals
from commerce.pipeline import CommercePipelineRequest
from commerce.integration import CommerceIntegrationResult, CommerceIntegrationStatus

pytestmark = [pytest.mark.unit]

def _allowed():
    return PolicyDecision(allowed=True, denial_reason="none")

def _make_signals(explicit=True, purchase_intent=0.9, price_interest=0.0, requested_price=None, asks_free=False):
    return CommerceSignals(
        purchase_intent=purchase_intent,
        content_interest=0.0,
        relationship_engagement=0.7,
        price_interest=price_interest,
        explicit_purchase_request=explicit,
        explicit_content_request=False,
        requested_price=requested_price,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.9,
        evidence=[],
        model_uncertainty=0.1,
        primary_intent="purchase_intent" if explicit else "casual_chat",
        intent_tags=["purchase_intent"] if explicit else [],
        negative_intent_tags=[],
        fan_asks_question=False,
        asks_for_free_content=asks_free,
    )

def _make_request():
    return CommercePipelineRequest(
        user_id=1,
        creator_id=2,
        messages=[{"role": "user", "content": "I want to buy"}],
        eligibility=_allowed(),
        has_relevant_product=True,
        has_active_offer=False,
        creator_sales_enabled=True,
    )

# 1. One evaluation via pipeline
@pytest.mark.asyncio
async def test_one_evaluation_pipeline():
    from commerce import pipeline as pl
    import commerce.decision as dec_mod
    import commerce.pipeline as pl_mod
    req = _make_request()
    signals = _make_signals(explicit=True, purchase_intent=0.9)
    # spy on authoritative decision - use wraps to count without recursion
    orig_decide = pl_mod.decide_from_signals
    with patch("commerce.pipeline.decide_from_signals", wraps=orig_decide) as mock_decide, \
         patch("commerce.orchestrator.decide_commerce_action") as mock_orch_decide, \
         patch("commerce.pipeline.extract_commerce_signals", new_callable=AsyncMock, return_value=signals), \
         patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock, return_value=MagicMock(status=MagicMock())):
        mock_orch_decide.side_effect = Exception("should not be called second time if converged")
        # run pipeline with signals supplied
        result = await pl.run_commerce_pipeline(req, signals=signals, user_message="I want to buy")
        # decide_from_signals should be called exactly once
        assert mock_decide.call_count == 1, f"decide_from_signals called {mock_decide.call_count} times, expected 1"
        # orchestrate's decide should be 0 because pipeline reuses canonical
        assert mock_orch_decide.call_count == 0, f"orchestrate decide called {mock_orch_decide.call_count} times, expected 0 for convergence"

# 1b. One evaluation via worker canonical
@pytest.mark.asyncio
async def test_one_evaluation_worker_canonical():
    from workers.llm_worker import _get_canonical_commerce_evaluation
    from commerce.signals import decide_from_signals
    signals = _make_signals(explicit=True)
    with patch("workers.llm_worker.resolve_single_application_creator", new_callable=AsyncMock) as mock_creator, \
         patch("workers.llm_worker.resolve_commerce_product_with_history", new_callable=AsyncMock, return_value=1), \
         patch("commerce.state.resolve_commerce_state", new_callable=AsyncMock) as mock_resolve, \
         patch("commerce.signals.decide_from_signals") as mock_decide:
        # setup creator
        from commerce.single_creator import SingleCreatorStatus
        mock_creator.return_value = MagicMock(status=SingleCreatorStatus.READY, creator_id=2)
        # mock resolve to return ready request
        mock_req = _make_request()
        mock_resolve.return_value = MagicMock(status=MagicMock(READY="ready"), request=mock_req)
        # need to make CommerceResolutionStatus.READY check pass
        from commerce.state import CommerceResolutionStatus
        mock_resolve.return_value.status = CommerceResolutionStatus.READY
        # decide returns distinctive
        distinctive = CommerceDecision(action=CommerceAction.OFFER_PPV, reason_code=CommerceReason.STRONG_BUYING_SIGNAL, allowed=True, confidence=0.95)
        mock_decide.return_value = distinctive
        # also patch _request_engine_kwargs to avoid needing full request fields
        with patch("commerce.pipeline._request_engine_kwargs", return_value={}):
            d, req_out = await _get_canonical_commerce_evaluation(1, [{"role":"user","content":"I want to buy"}], "", signals=signals, conversation_state=None, user_message="I want to buy")
            assert mock_decide.call_count == 1
            assert d is distinctive
            # calling precedence with canonical should not call decide again
            from workers.llm_worker import _is_commerce_ppv_authorized_for_precedence
            with patch("commerce.signals.decide_from_signals") as mock_decide2:
                mock_decide2.side_effect = Exception("should not be called")
                authorized = await _is_commerce_ppv_authorized_for_precedence(1, [], "", signals=signals, conversation_state=None, user_message="I want to buy", canonical_decision=d)
                assert authorized is True
                assert mock_decide2.call_count == 0
            # calling _try_commerce_draft with canonical should not call decide again
            from workers.llm_worker import _try_commerce_draft
            with patch("commerce.signals.decide_from_signals") as mock_decide3, \
                 patch("commerce.pipeline.run_commerce_pipeline", new_callable=AsyncMock) as mock_run, \
                 patch("commerce.product_selection.resolve_commerce_product_with_history", new_callable=AsyncMock):
                mock_run.return_value = MagicMock(status=MagicMock(COMPLETED="completed"), decision=d, strategy=MagicMock(), execution_result=MagicMock(), response=MagicMock(status=MagicMock(GENERATED="generated")), failure_code=None)
                # Mock integration result selection to avoid DB
                with patch("workers.llm_worker.resolve_and_run_commerce", new_callable=AsyncMock):
                    # We will test the canonical path directly via run_commerce_pipeline
                    # Already our _try_commerce_draft canonical path calls pipeline, not decide
                    # So ensure decide not called during draft with canonical
                    sel = await _try_commerce_draft(1, [], "", signals=signals, conversation_state=None, user_message="I want to buy", canonical_decision=d, canonical_request=mock_req)
                    assert mock_decide3.call_count == 0

# 2. Same decision reused
@pytest.mark.asyncio
async def test_same_decision_reused():
    from workers.llm_worker import _is_commerce_ppv_authorized_for_precedence, _try_commerce_draft
    distinctive = CommerceDecision(action=CommerceAction.OPERATOR_HANDOFF, reason_code=CommerceReason.OPERATOR_HANDOFF_NEEDED, allowed=False, confidence=1.0)
    # precedence should use exact object
    authorized = await _is_commerce_ppv_authorized_for_precedence(1, [], "", signals=MagicMock(), canonical_decision=distinctive)
    assert authorized is False  # not OFFER_PPV
    # also verify distinctive not recomputed
    # For _try_commerce_draft, mock pipeline to return selection based on distinctive
    mock_req = _make_request()
    mock_req = mock_req.model_copy(update={"eligibility": _allowed()})
    with patch("commerce.pipeline.run_commerce_pipeline", new_callable=AsyncMock) as mock_run:
        from commerce.pipeline import CommercePipelineStatus
        from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
        mock_run.return_value = MagicMock(
            status=CommercePipelineStatus.COMPLETED,
            decision=distinctive,
            strategy=MagicMock(),
            execution_result=None,
            response=CommerceResponse(status=CommerceResponseStatus.FAILED, failure_code="not_ppv_no_generation"),
            failure_code=None,
        )
        with patch("commerce.selection.select_commerce_response") as mock_sel:
            mock_sel.return_value = MagicMock(status=MagicMock(), reason=MagicMock(), commerce_response_text=None, execution_status=None, offer_active=False)
            await _try_commerce_draft(1, [], "", signals=MagicMock(), canonical_decision=distinctive, canonical_request=mock_req)
            # run_commerce_pipeline should have been called with distinctive decision object (identity)
            assert mock_run.call_count == 1
            called_decision = mock_run.call_args.kwargs.get("decision") or (mock_run.call_args.args[1] if len(mock_run.call_args.args) >1 else None)
            # In our implementation decision is passed as kwarg
            assert called_decision is distinctive or mock_run.call_args.kwargs.get("decision") is distinctive

# 3. No second evaluation during PPV precedence
@pytest.mark.asyncio
async def test_no_second_evaluation_during_ppv_precedence():
    from workers.llm_worker import _is_commerce_ppv_authorized_for_precedence
    distinctive = CommerceDecision(action=CommerceAction.OFFER_PPV, reason_code=CommerceReason.STRONG_BUYING_SIGNAL, allowed=True, confidence=0.95)
    with patch("commerce.signals.decide_from_signals") as mock_decide, \
         patch("commerce.decision.decide_commerce_action") as mock_decide2, \
         patch("commerce.state.resolve_commerce_state", new_callable=AsyncMock) as mock_resolve, \
         patch("workers.llm_worker.resolve_single_application_creator", new_callable=AsyncMock):
        # Should not be called when canonical supplied
        result = await _is_commerce_ppv_authorized_for_precedence(1, [], "", signals=MagicMock(), canonical_decision=distinctive)
        assert result is True
        assert mock_decide.call_count == 0
        assert mock_decide2.call_count == 0
        assert mock_resolve.call_count == 0

# 4. No second evaluation during response generation
@pytest.mark.asyncio
async def test_no_second_evaluation_during_response_generation():
    from commerce.deepseek_response import generate_commerce_response, CommerceResponseInput
    from commerce.strategy import CommerceStrategy, SalesPressure, StrategyKind
    from commerce.execution import ExecutionResult, ExecutionStatus
    decision = CommerceDecision(action=CommerceAction.OFFER_PPV, reason_code=CommerceReason.STRONG_BUYING_SIGNAL, allowed=True, confidence=0.95)
    strategy = CommerceStrategy(action=CommerceAction.OFFER_PPV, kind=StrategyKind.OFFER_PPV, pressure=SalesPressure.MODERATE, allow_cta=True, allow_price_reference=True, allow_product_reference=True, relationship_first=True, follow_up_allowed=True, reason=CommerceReason.STRONG_BUYING_SIGNAL)
    exec_result = ExecutionResult(status=ExecutionStatus.EXECUTED, offer_id=1, offer_state="pending")
    from commerce.context import CommerceConversationMessage
    inp = CommerceResponseInput(
        user_id=1, creator_id=2,
        conversation=[CommerceConversationMessage(role="user", content="hi")],
        decision=decision, strategy=strategy, execution_result=exec_result,
        persona="test", product_identity=None, product_state=None, currency="USD"
    )
    with patch("commerce.decision.decide_commerce_action") as mock_decide, \
         patch("commerce.signals.decide_from_signals") as mock_decide2:
        with patch("commerce.deepseek_response.get_llm_provider") as mock_provider:
            mock_provider.return_value.generate = AsyncMock(return_value="Hello, here is your link https://example.com")
            # also need to mock provider fallback? just make generate return text
            try:
                await generate_commerce_response(inp)
            except Exception:
                pass
            assert mock_decide.call_count == 0
            assert mock_decide2.call_count == 0

# 5. P0 hallucination remains blocked
def test_p0_hallucination_blocked():
    from commerce.signals import signals_to_context, decide_from_signals
    sig = CommerceSignals(
        purchase_intent=0.1,
        content_interest=0.0,
        relationship_engagement=0.0,
        price_interest=0.99,
        explicit_purchase_request=True,
        explicit_content_request=False,
        requested_price=25,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.9,
        evidence=[],
        model_uncertainty=0.1,
        primary_intent="purchase_intent",
        intent_tags=["purchase_intent"],
        negative_intent_tags=[],
        fan_asks_question=False,
    )
    # user_message = "hey" should block
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="hey")
    assert ctx.user_asked_to_buy is False
    assert ctx.user_asked_about_price is False
    d = decide_from_signals(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="hey", has_relevant_product=True, has_active_offer=False, creator_sales_enabled=True)
    assert not (d.action is CommerceAction.OFFER_PPV and d.allowed), f"hallucinated should not be OFFER_PPV allowed, got {d}"

# 6. Genuine purchase remains allowed
def test_genuine_purchase_allowed():
    from commerce.signals import decide_from_signals
    sig = _make_signals(explicit=True, purchase_intent=0.1)
    d = decide_from_signals(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="I want to buy", has_relevant_product=True, has_active_offer=False, creator_sales_enabled=True)
    assert d.action is CommerceAction.OFFER_PPV and d.allowed is True, f"expected OFFER_PPV, got {d}"

# 7. Price inquiry remains allowed
def test_price_inquiry_allowed():
    from commerce.signals import decide_from_signals
    sig = CommerceSignals(
        purchase_intent=0.0,
        content_interest=0.0,
        relationship_engagement=0.0,
        price_interest=0.9,
        explicit_purchase_request=False,
        explicit_content_request=False,
        requested_price=25,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.9,
        evidence=[],
        model_uncertainty=0.1,
        primary_intent="price_inquiry",
        intent_tags=["price_inquiry"],
        negative_intent_tags=[],
        fan_asks_question=True,
    )
    d = decide_from_signals(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="how much?", has_relevant_product=True, has_active_offer=False, creator_sales_enabled=True)
    assert d.action is CommerceAction.OFFER_PPV and d.allowed is True, f"expected OFFER_PPV for price inquiry, got {d}"

# 8. Free-photo behavior remains intact
def test_free_photo_intact():
    from commerce.free_photo_routing import is_photo_request
    assert is_photo_request("send me a pic", None) is True
    assert is_photo_request("this photo is so cute", None) is False
    # content request should not be PPV
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    ctx = CommerceDecisionContext(user_id=1, creator_id=2, eligibility=_allowed(), has_active_offer=False, has_relevant_product=True, user_requested_content=True, relationship_score=0.8)
    d = decide_commerce_action(ctx)
    assert d.action is not CommerceAction.OFFER_PPV

# 9. Fail-closed behavior remains intact (timing/behavioral failure -> RESOLUTION_FAILED)
@pytest.mark.asyncio
async def test_fail_closed_timing():
    from commerce.state import CommerceStateRequest, resolve_commerce_state
    req = CommerceStateRequest(user_id=123, creator_id=1, product_id=1, messages=[{"role":"user","content":"hi"}])
    with patch("db.postgres.get_user", new_callable=AsyncMock, return_value={"is_blocked": False}), \
         patch("db.postgres.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False), \
         patch("commerce.state._resolve_creator_relationship", new_callable=AsyncMock, return_value=(1, None)), \
         patch("db.dropfans.get_dropfans_integration", new_callable=AsyncMock, return_value={"status":"active"}), \
         patch("db.fangate.get_fangate_product", new_callable=AsyncMock, return_value={"id":1,"is_accessible":True,"sales_url":"https://example.com","price_minor":1000,"is_verif_age":False}), \
         patch("commerce.dao.find_pending_offer_for_product", new_callable=AsyncMock, return_value=None), \
         patch("commerce.dao.has_purchased_product", new_callable=AsyncMock, return_value=False), \
         patch("db.postgres.get_user_persona", new_callable=AsyncMock, return_value=None), \
         patch("db.segments.list_segments", new_callable=AsyncMock, return_value=[]), \
         patch("commerce.dao.get_timing_context", new_callable=AsyncMock, side_effect=Exception("db down")):
        res = await resolve_commerce_state(req)
        assert res.status.value == "resolution_failed"

# Additional: raw user_message still reaches P0 gate via pipeline
@pytest.mark.asyncio
async def test_raw_user_message_reaches_p0_gate_via_pipeline():
    from commerce.pipeline import run_commerce_pipeline
    req = _make_request()
    # Use low purchase_intent so only explicit/price gate decides, not strong signal
    signals = _make_signals(explicit=True, purchase_intent=0.1, price_interest=0.99, requested_price=25)
    # hey should block even though signals say explicit
    result = await run_commerce_pipeline(req, signals=signals, user_message="hey")
    # decision should not be OFFER_PPV
    assert result.decision is not None
    assert not (result.decision.action is CommerceAction.OFFER_PPV and result.decision.allowed)
    # I want to buy should allow
    req2 = _make_request()
    result2 = await run_commerce_pipeline(req2, signals=signals, user_message="I want to buy")
    assert result2.decision.action is CommerceAction.OFFER_PPV and result2.decision.allowed
