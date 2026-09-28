"""Phase 5.4 Chk 5 tests — clean application boundary (commerce.pipeline).

A: request contract (strict, instance-only, bounded, closed sets).
B: role_content_messages translation (bounds + shape).
C: build_conversation_context verbatim transport.
D: identity propagation (user/creator appear everywhere).
E: persona/currency/product propagation into response generation.
F: eligibility propagation (denied eligibility never executes).
G: missing product (OFFER_PPV request, no product -> no execution).
H: signal extraction, flag mapping, low-information safety.
I: decision invocation (engine kwargs from app state, policy flow).
J: strategy invocation (derived from the decision, never re-mapped).
K: orchestrator invocation (created_by, policy, staging codes).
L: response-generation input contract.
M: no execution without OFFER_PPV.
N: no execution without a supplied product.
O: execute_ppv at-most-once.
P: execution failure propagation (result preserved, no re-exec).
Q: response failure after successful execution (no re-exec).
R: decision/strategy/orchestration failure staging.
S: no secret leakage (result surface).
T: no traceback leakage (result surface).
U: determinism (identical inputs -> identical results).
V: no forbidden surfaces (imports / execution tokens / workers).

The pipeline is exercised with the REAL decision engine, REAL strategy
layer, and REAL orchestration gate: only the two LLM touch points
(signal extraction, response generation) and the Fangate activation
(execute_ppv) are mocked — exactly the module boundaries the phase
contract defines.
"""

import ast
import inspect
from unittest.mock import MagicMock

import pytest

from commerce.context import (
    CommerceConversationContext,
    CommerceConversationMessage,
    ProductCommerceState,
    ProductIdentity,
)
from commerce.decision import (
    DEFAULT_COMMERCE_POLICY,
    CommerceDecision,
    CommerceDecisionPolicy,
    CommerceReason,
)
from commerce.deepseek_response import (
    CommerceResponse,
    CommerceResponseStatus,
)
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.models import CommerceAction, OfferState, PolicyDecision
from commerce.orchestrator import (
    CommerceOrchestrationResult,
    orchestrate_commerce,
)
from commerce.pipeline import (
    PIPELINE_CREATED_BY,
    PIPELINE_CURRENCY_MAX,
    PIPELINE_FAILURE_CODES,
    PIPELINE_MAX_MESSAGE_CHARS,
    PIPELINE_MAX_MESSAGES,
    PIPELINE_PERSONA_MAX,
    CommercePipelineRequest,
    CommercePipelineResult,
    CommercePipelineStatus,
    _apply_signal_flags,
    build_conversation_context,
    role_content_messages,
    run_commerce_pipeline,
)
from commerce.signals import PRICE_ASK_THRESHOLD, CommerceSignals
from commerce.strategy import (
    CommerceStrategy,
    CommunicationConstraints,
    SalesPressure,
    StrategyKind,
)

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════


def _policy(allowed=True, denial_reason=""):
    return PolicyDecision(allowed=allowed, denial_reason=denial_reason)


def _identity(**overrides):
    data = {"product_id": 10, "title": "VIP Video Bundle", "available": True}
    data.update(overrides)
    return ProductIdentity(**data)


def _state(**overrides):
    data = {
        "price_minor": 4400,
        "sales_url": "https://shop.example/vip",
        "is_accessible": True,
    }
    data.update(overrides)
    return ProductCommerceState(**data)


def _raw_messages(count=2):
    return [
        {"role": "system", "content": "persona boilerplate"},
        {"role": "user", "content": "hi there"},
        {"role": "assistant", "content": "hey! how are you?"},
    ][-count:]


def _request(**overrides):
    data = {
        "user_id": 1,
        "creator_id": 2,
        "messages": _raw_messages(),
        "eligibility": _policy(),
        "product_identity": _identity(),
        "product_state": _state(),
        "currency": "USD",
    }
    data.update(overrides)
    return CommercePipelineRequest(**data)


def _signals(**overrides):
    base = CommerceSignals.low_information()
    # Set reasonable defaults so existing tests that rely on relationship_score
    # or buying_intent_score aren't overridden by C.1-B guards:
    # - confidence=0.5: avoids step 7.7 (low confidence → conversation)
    # - primary_intent="other": falls through to "engaged_chat" phase,
    #   avoiding rapport/opening phase suppression (step 7.8)
    return base.model_copy(update={
        "confidence": 0.5,
        "primary_intent": "other",
        **overrides,
    })


def _decision(action=CommerceAction.OFFER_PPV, **overrides):
    data = {
        "action": action,
        "reason_code": CommerceReason.STRONG_BUYING_SIGNAL,
        "allowed": True,
        "confidence": 0.95,
    }
    data.update(overrides)
    return CommerceDecision(**data)


def _strategy(action=CommerceAction.OFFER_PPV, **overrides):
    data = {
        "action": action,
        "kind": StrategyKind.OFFER_PPV,
        "pressure": SalesPressure.MODERATE,
        "allow_cta": True,
        "allow_price_reference": True,
        "allow_product_reference": True,
        "relationship_first": True,
        "follow_up_allowed": True,
        "reason": CommerceReason.STRONG_BUYING_SIGNAL,
        "communication_constraints": CommunicationConstraints(),
    }
    data.update(overrides)
    return CommerceStrategy(**data)


def _execution(status=ExecutionStatus.EXECUTED, **overrides):
    data = {"status": status, "offer_id": 77}
    data.update(overrides)
    return ExecutionResult(**data)


def _orchestration(**overrides):
    data = {
        "user_id": 1,
        "creator_id": 2,
        "decision": _decision(),
        "strategy": _strategy(),
        "execution_result": _execution(),
        "success": True,
        "failure_code": None,
    }
    data.update(overrides)
    return CommerceOrchestrationResult(**data)


def _response(status=CommerceResponseStatus.GENERATED, text="sure thing!", failure_code=None):
    data = {
        "status": status,
        "text": None if status is CommerceResponseStatus.FAILED else text,
        "failure_code": failure_code,
    }
    return CommerceResponse(**data)


def _patch_extraction(mp, signals):
    async def _fake_signals(_conversation):
        return signals

    mp.setattr("commerce.pipeline.extract_commerce_signals", _fake_signals)


def _patch_execution(mp, result):
    async def _fake_execute(*args, **kwargs):
        return result

    mp.setattr("commerce.orchestrator.execute_ppv", _fake_execute)


def _patch_counting_execution(mp, calls, result):
    async def _fake_execute(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    mp.setattr("commerce.orchestrator.execute_ppv", _fake_execute)


def _patch_response(mp, result, capture=None):
    async def _fake_generate(input_):
        if capture is not None:
            capture.append(input_)
        return result

    mp.setattr("commerce.pipeline.generate_commerce_response", _fake_generate)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — request contract
# ═══════════════════════════════════════════════════════════════════════════


class TestRequestContract:
    def test_valid_request_builds(self):
        req = _request()
        assert req.user_id == 1
        assert req.creator_id == 2

    def test_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            CommercePipelineRequest(**_request().model_dump(), invented=True)

    def test_user_id_and_creator_id_must_be_positive_ints(self):
        for bad in (0, -1, 1.5, "1", True):
            with pytest.raises(ValueError):
                _request(user_id=bad)
            with pytest.raises(ValueError):
                _request(creator_id=bad)

    def test_messages_required(self):
        data = _request().model_dump()
        del data["messages"]
        with pytest.raises(ValueError):
            CommercePipelineRequest(**data)

    def test_message_role_must_be_system_user_assistant(self):
        with pytest.raises(ValueError):
            _request(messages=[{"role": "admin", "content": "hi"}])

    def test_message_content_must_be_string(self):
        with pytest.raises(ValueError):
            _request(messages=[{"role": "user", "content": 42}])

    def test_messages_capped_by_field(self):
        many = [{"role": "user", "content": "m"} for _ in range(PIPELINE_MAX_MESSAGES + 1)]
        with pytest.raises(ValueError):
            _request(messages=many)

    def test_eligibility_must_be_verdict_instance(self):
        with pytest.raises(ValueError):
            _request(eligibility={"allowed": True})
        with pytest.raises(ValueError):
            _request(eligibility=True)

    def test_policy_must_be_engine_policy_or_none(self):
        assert _request(policy=None).policy is None
        assert _request(policy=DEFAULT_COMMERCE_POLICY).policy is DEFAULT_COMMERCE_POLICY
        with pytest.raises(ValueError):
            _request(policy={"offer_cooldown_hours": 1})

    def test_currency_bounded(self):
        _request(currency=None)
        _request(currency="USD")
        with pytest.raises(ValueError):
            _request(currency="U")
        with pytest.raises(ValueError):
            _request(currency="X" * (PIPELINE_CURRENCY_MAX + 1))

    def test_persona_bounded(self):
        _request(persona="x" * PIPELINE_PERSONA_MAX)
        with pytest.raises(ValueError):
            _request(persona="x" * (PIPELINE_PERSONA_MAX + 1))

    def test_counters_must_be_non_negative(self):
        for field in (
            "messages_since_last_offer",
            "messages_since_last_purchase",
            "recent_offer_count",
            "recent_purchase_count",
            "recent_sales_attempt_count",
        ):
            with pytest.raises(ValueError):
                _request(**{field: -1})

    def test_hours_must_be_non_negative_float_or_none(self):
        _request(hours_since_last_offer=None)
        _request(hours_since_last_purchase=2.5)
        with pytest.raises(ValueError):
            _request(hours_since_last_offer=-1)
        with pytest.raises(ValueError):
            _request(hours_since_last_purchase="2.5")

    def test_state_bools_are_strict(self):
        for field in ("has_active_offer", "has_relevant_product", "creator_sales_enabled"):
            with pytest.raises(ValueError):
                _request(**{field: "yes"})

    def test_previous_offer_status_closed_set(self):
        _request(previous_offer_status=None)
        _request(previous_offer_status="declined")
        assert (
            _request(previous_offer_status=OfferState.DECLINED).previous_offer_status == "declined"
        )
        with pytest.raises(ValueError):
            _request(previous_offer_status="invented")

    def test_relationship_score_bounded(self):
        _request(relationship_score=0.0)
        _request(relationship_score=1.0)
        with pytest.raises(ValueError):
            _request(relationship_score=1.5)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — role_content_messages translation
# ═══════════════════════════════════════════════════════════════════════════


class TestRoleContentMessages:
    def test_empty_input_empty_output(self):
        assert role_content_messages([]) == []

    def test_keeps_latest_when_over_budget(self):
        many = [{"role": "user", "content": f"m{i}"} for i in range(PIPELINE_MAX_MESSAGES + 5)]
        out = role_content_messages(many)
        assert len(out) == PIPELINE_MAX_MESSAGES
        assert out[0].content == "m5"
        assert out[-1].content == f"m{PIPELINE_MAX_MESSAGES + 4}"

    def test_truncates_each_message(self):
        out = role_content_messages([{"role": "user", "content": "x" * 2000}])
        assert len(out) == 1
        assert len(out[0].content) == PIPELINE_MAX_MESSAGE_CHARS

    def test_shorter_messages_untouched(self):
        out = role_content_messages([{"role": "system", "content": "persona"}])
        assert out[0].content == "persona"

    def test_bad_role_rejected(self):
        with pytest.raises(ValueError):
            role_content_messages([{"role": "admin", "content": "hi"}])

    def test_non_string_content_rejected(self):
        with pytest.raises(TypeError):
            role_content_messages([{"role": "user", "content": ["hi"]}])

    def test_produces_sealed_types(self):
        out = role_content_messages([{"role": "user", "content": "hi"}])
        assert isinstance(out[0], CommerceConversationMessage)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — build_conversation_context verbatim transport
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildConversationContext:
    def test_transports_all_fields_verbatim(self):
        req = _request(
            relationship_score=0.7,
            messages_since_last_offer=4,
            messages_since_last_purchase=1,
            hours_since_last_offer=6.0,
            hours_since_last_purchase=12.0,
            recent_offer_count=1,
            recent_purchase_count=2,
            recent_sales_attempt_count=3,
            previous_offer_status="declined",
            has_active_offer=True,
            has_relevant_product=False,
            creator_sales_enabled=False,
        )
        ctx = build_conversation_context(req)
        assert ctx.user_id == 1
        assert ctx.creator_id == 2
        assert ctx.relationship_score == 0.7
        assert ctx.messages_since_last_offer == 4
        assert ctx.messages_since_last_purchase == 1
        assert ctx.hours_since_last_offer == 6.0
        assert ctx.hours_since_last_purchase == 12.0
        assert ctx.recent_offer_count == 1
        assert ctx.recent_purchase_count == 2
        assert ctx.recent_sales_attempt_count == 3
        assert ctx.previous_offer_status == "declined"
        assert ctx.has_active_offer is True
        assert ctx.has_relevant_product is False
        assert ctx.creator_sales_enabled is False

    def test_eligibility_and_product_are_sealed(self):
        req = _request(eligibility=_policy(False, "already_purchased"))
        ctx = build_conversation_context(req)
        assert ctx.eligibility.allowed is False
        assert ctx.eligibility.denial_reason == "already_purchased"
        assert ctx.product_identity is req.product_identity
        assert ctx.product_state is req.product_state

    def test_messages_translated(self):
        ctx = build_conversation_context(_request())
        assert ctx.messages == role_content_messages(_raw_messages())
        assert all(isinstance(m, CommerceConversationMessage) for m in ctx.messages)

    def test_output_is_sealed_context(self):
        ctx = build_conversation_context(_request())
        assert isinstance(ctx, CommerceConversationContext)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — identity propagation
# ═══════════════════════════════════════════════════════════════════════════


class TestIdentityPropagation:
    @pytest.mark.asyncio
    async def test_identity_flows_through_every_stage(self):
        seen = {}
        real_execute = MagicMock()

        async def _fake_execute(*, creator_id, user_id, product_id, **kwargs):
            seen["execute"] = (creator_id, user_id, product_id)
            real_execute()
            return _execution()

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", _fake_execute)
            _patch_response(mp, _response(), capture=seen.setdefault("response_inputs", []))

            result = await run_commerce_pipeline(_request(user_id=41, creator_id=88))

        assert result.user_id == 41
        assert result.creator_id == 88
        assert result.decision is not None
        assert seen["execute"] == (88, 41, 10)
        inp = seen["response_inputs"][0]
        assert inp.user_id == 41
        assert inp.creator_id == 88
        assert inp.decision is result.decision
        assert inp.strategy is result.strategy


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — persona / currency / product into response generation
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseInputPropagation:
    @pytest.mark.asyncio
    async def test_persona_currency_and_product_reach_response(self):
        captured = []
        req = _request(
            persona="warm and playful",
            currency="EUR",
            product_identity=_identity(title="Fan Club Pack"),
            product_state=_state(price_minor=990),
        )
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response(text="here you go"), capture=captured)
            result = await run_commerce_pipeline(req)
        assert result.status is CommercePipelineStatus.COMPLETED
        inp = captured[0]
        assert inp.persona == "warm and playful"
        assert inp.currency == "EUR"
        assert inp.product_identity.title == "Fan Club Pack"
        assert inp.product_state.price_minor == 990

    @pytest.mark.asyncio
    async def test_persona_and_currency_optional(self):
        captured = []
        req = _request(persona=None, currency=None)
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response(), capture=captured)
            await run_commerce_pipeline(req)
        inp = captured[0]
        assert inp.persona is None
        assert inp.currency is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — eligibility propagation
# ═══════════════════════════════════════════════════════════════════════════


class TestEligibilityPropagation:
    @pytest.mark.asyncio
    async def test_denied_eligibility_never_executes(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(
                mp,
                _signals(explicit_purchase_request=True),
            )
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response(text="happy to chat"))

            result = await run_commerce_pipeline(
                _request(eligibility=_policy(False, "already_purchased"))
            )

        assert result.status is CommercePipelineStatus.COMPLETED
        assert result.decision is not None
        assert result.decision.action is CommerceAction.NO_OFFER
        assert result.decision.reason_code is CommerceReason.ALREADY_PURCHASED
        assert result.execution_result is None
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_denial_reason_is_preserved_in_decision_metadata(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(
                _request(eligibility=_policy(False, "age_verification_required"))
            )
        assert result.decision.metadata.get("eligibility_denial") == "age_verification_required"


# ═══════════════════════════════════════════════════════════════════════════
# GROUP G — missing product: no invention, no execution
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingProduct:
    @pytest.mark.asyncio
    async def test_explicit_buy_request_without_product_does_not_execute(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response(text="i would love to help!"))

            result = await run_commerce_pipeline(
                _request(product_identity=None, product_state=None)
            )

        assert result.status is CommercePipelineStatus.COMPLETED
        assert result.decision.action is CommerceAction.OFFER_PPV
        assert result.execution_result is None
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_product_never_invented_in_response_input(self):
        captured = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response(), capture=captured)
            await run_commerce_pipeline(
                _request(product_identity=None, product_state=None, currency=None)
            )
        inp = captured[0]
        assert inp.product_identity is None
        assert inp.product_state is None
        assert inp.currency is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP H — signal extraction, flag mapping, low-information safety
# ═══════════════════════════════════════════════════════════════════════════


class TestSignalMapping:
    @pytest.mark.asyncio
    async def test_explicit_purchase_request_triggers_offer(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.decision.action is CommerceAction.OFFER_PPV
        assert result.decision.reason_code is CommerceReason.STRONG_BUYING_SIGNAL
        execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_requested_price_counts_as_price_ask(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(requested_price=25.0))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.decision.action is CommerceAction.OFFER_PPV

    @pytest.mark.asyncio
    async def test_price_interest_threshold_maps_to_price_ask(self):
        ctx = build_conversation_context(_request())
        below = _apply_signal_flags(ctx, _signals(price_interest=PRICE_ASK_THRESHOLD - 0.01))
        at = _apply_signal_flags(ctx, _signals(price_interest=PRICE_ASK_THRESHOLD))
        assert below.user_asked_about_price is False
        assert at.user_asked_about_price is True

    @pytest.mark.asyncio
    async def test_explicit_content_request_maps_to_flag(self):
        ctx = build_conversation_context(_request())
        flagged = _apply_signal_flags(
            ctx, _signals(explicit_content_request=True, purchase_intent=0.4)
        )
        assert flagged.user_requested_content is True
        assert flagged.buying_intent_score == 0.4

    @pytest.mark.asyncio
    async def test_relationship_score_caller_wins(self):
        ctx = build_conversation_context(_request(relationship_score=0.5))
        merged = _apply_signal_flags(ctx, _signals(relationship_engagement=0.95))
        assert merged.relationship_score == 0.5

    @pytest.mark.asyncio
    async def test_relationship_engagement_used_when_caller_absent(self):
        ctx = build_conversation_context(_request(relationship_score=None))
        merged = _apply_signal_flags(ctx, _signals(relationship_engagement=0.9))
        assert merged.relationship_score == 0.9
        assert ctx.relationship_score is None

    @pytest.mark.asyncio
    async def test_none_signals_are_no_intent(self):
        ctx = build_conversation_context(_request())
        assert _apply_signal_flags(ctx, None) is ctx

    @pytest.mark.asyncio
    async def test_low_information_never_looks_like_interest(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, CommerceSignals.low_information())
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.decision.action is not CommerceAction.OFFER_PPV
        assert result.decision.action is not CommerceAction.SOFT_OFFER
        assert result.decision.action is not CommerceAction.FOLLOW_UP
        assert result.decision.allowed is False
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_signals_receive_translated_messages(self):
        seen = {}

        async def _spy(conversation):
            seen["messages"] = conversation
            return _signals(explicit_purchase_request=True)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.pipeline.extract_commerce_signals", _spy)
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request())
        assert seen["messages"] == _raw_messages()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP I — decision invocation and engine-kwargs from app state
# ═══════════════════════════════════════════════════════════════════════════


class TestDecisionInvocation:
    @pytest.mark.asyncio
    async def test_relationship_score_drives_decision(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request(relationship_score=0.9))
        assert result.decision.action is CommerceAction.SOFT_OFFER
        assert result.decision.reason_code is CommerceReason.RELATIONSHIP_READY

    @pytest.mark.asyncio
    async def test_cooldown_state_from_request_gates_decision(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(
                _request(hours_since_last_offer=2.0, previous_offer_status=None)
            )
        assert result.decision.action is CommerceAction.NO_OFFER
        assert result.decision.reason_code is CommerceReason.COOLDOWN_ACTIVE

    @pytest.mark.asyncio
    async def test_custom_policy_reaches_engine(self):
        tight = CommerceDecisionPolicy(max_offers_per_24h=0)
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request(policy=tight))
        assert result.decision.action is CommerceAction.NO_OFFER
        assert result.decision.reason_code is CommerceReason.TOO_MANY_OFFERS

    @pytest.mark.asyncio
    async def test_engine_kwargs_come_from_request_only(self):
        seen = {}

        def _spy_decision(signals, **kwargs):
            seen["kwargs"] = kwargs
            return _decision()

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.pipeline.decide_from_signals", _spy_decision)
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request())
        kw = seen["kwargs"]
        assert kw["user_id"] == 1
        assert kw["creator_id"] == 2

    @pytest.mark.asyncio
    async def test_decision_engine_failure_stages_decision_failed(self):
        def _boom(signals, **kwargs):
            raise RuntimeError("engine blew up")

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr("commerce.pipeline.decide_from_signals", _boom)
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.DECISION_FAILED
        assert result.failure_code == "decision_failure"
        assert result.decision is None
        assert result.response is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP J — strategy invocation (derived, never re-mapped)
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyInvocation:
    @pytest.mark.asyncio
    async def test_strategy_derived_from_decision(self):
        captured = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response(), capture=captured)
            result = await run_commerce_pipeline(_request())
        assert result.strategy is not None
        assert result.strategy.action is CommerceAction.OFFER_PPV
        assert result.strategy.kind is StrategyKind.OFFER_PPV
        assert captured[0].strategy is result.strategy

    @pytest.mark.asyncio
    async def test_no_offer_never_gets_sell_strategy(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.decision.action is not CommerceAction.OFFER_PPV
        assert result.decision.allowed is False
        assert result.strategy.action is result.decision.action
        assert result.strategy.kind is not StrategyKind.OFFER_PPV
        assert result.strategy.allow_cta is False
        assert result.strategy.allow_price_reference is False
        assert result.strategy.allow_product_reference is False


# ═══════════════════════════════════════════════════════════════════════════
# GROUP K — orchestrator invocation
# ═══════════════════════════════════════════════════════════════════════════


class TestOrchestratorInvocation:
    @pytest.mark.asyncio
    async def test_created_by_and_policy_flow_to_orchestration(self):
        seen = {}
        real_orchestrate = orchestrate_commerce

        async def _spy_orchestrate(context, *, policy=None, created_by=None):
            seen["policy"] = policy
            seen["created_by"] = created_by
            return await real_orchestrate(context, policy=policy, created_by=created_by)

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            mp.setattr("commerce.pipeline.orchestrate_commerce", _spy_orchestrate)
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request())
        assert seen["created_by"] == PIPELINE_CREATED_BY
        assert seen["policy"] is None

    @pytest.mark.asyncio
    async def test_custom_policy_reaches_orchestration(self):
        seen = {}
        tight = CommerceDecisionPolicy(max_offers_per_24h=0)
        real_orchestrate = orchestrate_commerce

        async def _spy(context, *, policy=None, created_by=None):
            seen["policy"] = policy
            return await real_orchestrate(context, policy=policy, created_by=created_by)

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            mp.setattr("commerce.pipeline.orchestrate_commerce", _spy)
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request(policy=tight))
        assert seen["policy"] is tight

    @pytest.mark.asyncio
    async def test_execution_gate_args(self):
        seen = {}

        async def _fake_execute(
            *, creator_id, user_id, product_id, decision, created_by, age_verified
        ):
            seen.update(
                {
                    "creator_id": creator_id,
                    "user_id": user_id,
                    "product_id": product_id,
                    "age_verified": age_verified,
                    "created_by": created_by,
                    "decision": decision,
                }
            )
            return _execution()

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True, purchase_intent=0.9))
            mp.setattr("commerce.orchestrator.execute_ppv", _fake_execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(
                _request(
                    product_identity=_identity(product_id=555),
                    product_state=_state(age_verified=True),
                )
            )
        assert seen["creator_id"] == 2
        assert seen["user_id"] == 1
        assert seen["product_id"] == 555
        assert seen["age_verified"] is True
        assert seen["created_by"] == PIPELINE_CREATED_BY
        assert seen["decision"] is result.decision


# ═══════════════════════════════════════════════════════════════════════════
# GROUP L — response-generation input contract
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseGenerationInput:
    @pytest.mark.asyncio
    async def test_response_input_matches_orchestration(self):
        captured = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response(), capture=captured)
            result = await run_commerce_pipeline(_request())
        inp = captured[0]
        assert list(inp.conversation) == [
            CommerceConversationMessage(role=m["role"], content=m["content"])
            for m in _raw_messages()
        ]
        assert inp.decision is result.decision
        assert inp.strategy is result.strategy
        assert inp.execution_result is result.execution_result


# ═══════════════════════════════════════════════════════════════════════════
# GROUP M — no execution without OFFER_PPV
# ═══════════════════════════════════════════════════════════════════════════


class TestNoExecutionWithoutOfferPpv:
    @pytest.mark.asyncio
    async def test_soft_offer_never_calls_execute(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(purchase_intent=0.6))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.decision.action is CommerceAction.SOFT_OFFER
        execute.assert_not_called()
        assert result.execution_result is None

    @pytest.mark.asyncio
    async def test_follow_up_never_calls_execute(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(
                _request(previous_offer_status="declined", hours_since_last_offer=48.0)
            )
        assert result.decision.action is CommerceAction.FOLLOW_UP
        execute.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP N — no execution without a supplied product
# ═══════════════════════════════════════════════════════════════════════════


class TestNoExecutionWithoutProduct:
    @pytest.mark.asyncio
    async def test_offer_without_identity_or_state_never_executes(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(
                _request(product_identity=None, product_state=None)
            )
        assert result.decision.action is CommerceAction.OFFER_PPV
        execute.assert_not_called()
        assert result.execution_result is None

    @pytest.mark.asyncio
    async def test_partial_product_never_executes(self):
        execute = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request(product_identity=None, product_state=_state()))
        execute.assert_not_called()
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", execute)
            _patch_response(mp, _response())
            await run_commerce_pipeline(_request(product_identity=_identity(), product_state=None))
        execute.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP O — execute_ppv at-most-once
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteAtMostOnce:
    @pytest.mark.asyncio
    async def test_success_path_executes_exactly_once(self):
        calls = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_counting_execution(mp, calls, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.COMPLETED
        assert result.execution_result.offer_id == 77
        assert result.failure_code is None
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_execution_never_re_executed_on_response_failure(self):
        calls = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_counting_execution(mp, calls, _execution())
            _patch_response(
                mp,
                _response(status=CommerceResponseStatus.FAILED, failure_code="model_timeout"),
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.RESPONSE_FAILED
        assert result.failure_code == "model_timeout"
        assert result.execution_result.offer_id == 77
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_execution_never_re_executed_on_failed_execution(self):
        calls = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_counting_execution(mp, calls, _execution(ExecutionStatus.PERSISTENCE_FAILED))
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.EXECUTION_FAILED
        assert result.execution_result.status is ExecutionStatus.PERSISTENCE_FAILED
        assert len(calls) == 1


# ═══════════════════════════════════════════════════════════════════════════
# GROUP P — execution failure propagation
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionFailurePropagation:
    @pytest.mark.asyncio
    async def test_execution_result_preserved_verbatim(self):
        preserved = _execution(
            ExecutionStatus.PROVIDER_ERROR,
            offer_id=99,
            denial_reason="upstream down",
            metadata={"provider": "fangate"},
        )
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, preserved)
            _patch_response(mp, _response(text="ok"))
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.EXECUTION_FAILED
        assert result.failure_code == "execution_failure"
        assert result.execution_result is preserved
        assert result.decision is not None
        assert result.strategy is not None
        assert result.response.status is CommerceResponseStatus.GENERATED

    @pytest.mark.asyncio
    async def test_execute_raising_becomes_execution_failed(self):
        async def _boom(*args, **kwargs):
            raise RuntimeError("fangate hang")

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", _boom)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.EXECUTION_FAILED
        assert result.failure_code == "execution_failure"
        assert result.execution_result is None
        assert result.response is not None

    @pytest.mark.asyncio
    async def test_response_still_generated_after_execution_failure(self):
        captured = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution(ExecutionStatus.DENIED, offer_id=None))
            _patch_response(mp, _response(text="understood"), capture=captured)
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.EXECUTION_FAILED
        assert captured, "response generation must still run"
        assert captured[0].execution_result.status is ExecutionStatus.DENIED


# ═══════════════════════════════════════════════════════════════════════════
# GROUP Q — response failure after successful execution
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseFailureAfterExecution:
    @pytest.mark.asyncio
    async def test_execution_result_survives_response_failure(self):
        captured = []
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(
                mp,
                _response(status=CommerceResponseStatus.FAILED, failure_code="invalid_output"),
                capture=captured,
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.RESPONSE_FAILED
        assert result.failure_code == "invalid_output"
        assert result.execution_result.status is ExecutionStatus.EXECUTED
        assert result.response.status is CommerceResponseStatus.FAILED

    @pytest.mark.asyncio
    async def test_failed_response_with_failed_execution_stays_execution_failed(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution(ExecutionStatus.PROVIDER_ERROR))
            _patch_response(
                mp,
                _response(status=CommerceResponseStatus.FAILED, failure_code="model_timeout"),
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.EXECUTION_FAILED
        assert result.failure_code == "execution_failure"
        assert result.response.status is CommerceResponseStatus.FAILED


# ═══════════════════════════════════════════════════════════════════════════
# GROUP R — decision/strategy/orchestration failure staging
# ═══════════════════════════════════════════════════════════════════════════


class TestStageFailures:
    @pytest.mark.asyncio
    async def test_orchestration_decision_failure_staged(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr(
                "commerce.pipeline.orchestrate_commerce",
                _a_orchestration(_orchestration(success=False, failure_code="decision_failure")),
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.DECISION_FAILED
        assert result.failure_code == "decision_failure"

    @pytest.mark.asyncio
    async def test_orchestration_strategy_failure_staged(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr(
                "commerce.pipeline.orchestrate_commerce",
                _a_orchestration(_orchestration(success=False, failure_code="strategy_failure")),
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.STRATEGY_FAILED
        assert result.failure_code == "strategy_failure"

    @pytest.mark.asyncio
    async def test_orchestration_unexpected_error_staged(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr(
                "commerce.pipeline.orchestrate_commerce",
                _a_orchestration(_orchestration(success=False, failure_code="unexpected_error")),
            )
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.FAILED
        assert result.failure_code == "unexpected_error"

    @pytest.mark.asyncio
    async def test_happy_path_never_stales(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.COMPLETED
        assert result.failure_code is None

    @pytest.mark.asyncio
    async def test_unexpected_pipeline_error_never_raises(self):
        with pytest.MonkeyPatch.context() as mp:

            async def _boom(_conversation):
                raise RuntimeError("adapter blew up")

            mp.setattr("commerce.pipeline.extract_commerce_signals", _boom)
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.FAILED
        assert result.failure_code == "unexpected_error"
        assert result.decision is None
        assert result.response is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP S — no secret leakage on the result surface
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSecretLeakage:
    @pytest.mark.asyncio
    async def test_execute_exception_text_never_reaches_result(self):
        async def _boom(*args, **kwargs):
            raise RuntimeError("SECRET-FANGATE-KEY leaked")

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            mp.setattr("commerce.orchestrator.execute_ppv", _boom)
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert "SECRET-FANGATE-KEY" not in result.model_dump_json()
        assert "SECRET-FANGATE-KEY" not in result.model_dump()

    @pytest.mark.asyncio
    async def test_decision_exception_text_never_reaches_result(self):
        def _boom(signals, **kwargs):
            raise RuntimeError("SECRET-DECISION-INTERNAL")

        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals())
            mp.setattr("commerce.pipeline.decide_from_signals", _boom)
            result = await run_commerce_pipeline(_request())
        assert "SECRET-DECISION-INTERNAL" not in result.model_dump_json()

    @pytest.mark.asyncio
    async def test_result_never_carries_conversation(self):
        with pytest.MonkeyPatch.context() as mp:
            _patch_extraction(mp, _signals(explicit_purchase_request=True))
            _patch_execution(mp, _execution())
            _patch_response(mp, _response())
            result = await run_commerce_pipeline(_request())
        assert "messages" not in result.model_dump()
        assert "conversation" not in result.model_dump()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP T — no traceback leakage on the result surface
# ═══════════════════════════════════════════════════════════════════════════


class TestNoTracebackLeakage:
    @pytest.mark.asyncio
    async def test_traceback_never_in_result_even_on_pipeline_failure(self):
        async def _boom(_conversation):
            raise RuntimeError("whatever")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.pipeline.extract_commerce_signals", _boom)
            result = await run_commerce_pipeline(_request())
        assert result.status is CommercePipelineStatus.FAILED
        assert "Traceback" not in result.model_dump_json()
        assert 'File "' not in result.model_dump_json()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP U — determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_identical_inputs_identical_results(self):
        async def _run():
            req = _request(
                relationship_score=0.7,
                product_identity=_identity(),
                product_state=_state(),
            )
            with pytest.MonkeyPatch.context() as mp:
                _patch_extraction(mp, _signals(explicit_purchase_request=True, purchase_intent=0.9))
                _patch_execution(mp, _execution())
                _patch_response(mp, _response(text="hey!"))
                return await run_commerce_pipeline(req)

        r1 = await _run()
        r2 = await _run()
        assert r1 == r2
        assert r1.status is CommercePipelineStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════
# GROUP V — no forbidden surfaces
# ═══════════════════════════════════════════════════════════════════════════


class TestNoForbiddenSurfaces:
    def test_imports_are_limited(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        allowed_prefixes = (
            "logging",
            "enum",
            "typing",
            "pydantic",
            "commerce.",
        )
        for mod in imported:
            assert mod.startswith(allowed_prefixes), f"forbidden import: {mod}"

    def test_no_infrastructure_imports(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        for forbidden in (
            "core.",
            "workers",
            "chatbotv2",
            "redis",
            "asyncpg",
            "httpx",
            "requests",
            "aiohttp",
            "google.genai",
        ):
            assert forbidden not in source, f"forbidden token: {forbidden}"

    def test_execute_ppv_only_inside_orchestration(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                assert name != "execute_ppv", f"direct execute_ppv call at {node.lineno}"

    def test_one_orchestration_call_per_run(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        calls = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "orchestrate_commerce"
        ]
        assert len(calls) == 1

    def test_no_worker_event_telegram_tokens(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        for forbidden in (
            "enqueue_send",
            "enqueue_inbound",
            "publish_event",
            "add_to_operator_queue",
            "move_to_dlq",
            "ws_manager",
            "event_subscriber",
            "telegram",
            "Telethon",
            "send_message",
        ):
            assert forbidden not in source, f"forbidden token: {forbidden}"

    def test_no_clock_no_randomness(self):
        source = inspect.getsource(__import__("commerce.pipeline", fromlist=["x"]))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "random" not in alias.name and alias.name != "time"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "random" not in node.module and node.module != "time"

    def test_result_status_enums_are_five_way(self):
        assert {s.value for s in CommercePipelineStatus} == {
            "completed",
            "decision_failed",
            "strategy_failed",
            "execution_failed",
            "response_failed",
            "failed",
        }

    def test_failure_codes_closed_set_contains_orchestration_and_response(self):
        assert "decision_failure" in PIPELINE_FAILURE_CODES
        assert "strategy_failure" in PIPELINE_FAILURE_CODES
        assert "execution_failure" in PIPELINE_FAILURE_CODES
        assert "unexpected_error" in PIPELINE_FAILURE_CODES
        assert "model_timeout" in PIPELINE_FAILURE_CODES
        assert "invalid_output" in PIPELINE_FAILURE_CODES

    def test_result_rejects_unknown_failure_code(self):
        with pytest.raises(ValueError):
            CommercePipelineResult(
                status=CommercePipelineStatus.COMPLETED,
                user_id=1,
                creator_id=2,
                failure_code="invented",
            )

    def test_module_constant_created_by_is_stable(self):
        assert PIPELINE_CREATED_BY == "commerce_pipeline"


def _a_orchestration(result):
    async def _fake_orchestrate(*args, **kwargs):
        return result

    return _fake_orchestrate
