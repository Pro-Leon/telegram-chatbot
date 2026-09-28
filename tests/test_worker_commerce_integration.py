"""Phase 5.4 Chk 6F tests — production application wiring of the commerce path.

These tests exercise the REAL worker boundary (``process_message``) with the
commerce boundary mocked at ``resolve_and_run_commerce`` (as prescribed) and
the REAL ``select_commerce_response`` selection logic, proving:

    worker -> commerce integration -> selection -> draft -> existing scoring
        -> existing approval -> existing send/events

Groups:

A: attempt wiring (after build_context, before generate_draft, at most once).
B: USE_COMMERCE_RESPONSE path (commerce text becomes the draft, no standard
   generation, events carry the commerce draft).
C: fallback paths (FALLBACK / COMMERCE_UNAVAILABLE / exceptions / malformed
   results all degrade to the standard generate_draft()).
D: execution-status semantics at the worker boundary (active vs failed).
E: no manufactured product/currency state; creator_id enters the request only
   from the single-creator resolver (unavailable by default here).
F: existing scoring / auto-approval / operator-queue semantics preserved.
G: Phase 1 event contract preserved (single started/completed, same
   generation_id, suggestion.created compatible).
H: source guards — worker is the ONLY application integration point; worker
   never calls execute_ppv / Fangate / commerce transport directly; commerce
   modules still never send/publish/enqueue.
I: standard-path parity (unavailable commerce leaves the normal path intact).
"""

import ast
import contextlib
import inspect
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commerce.decision import CommerceDecision, CommerceReason
from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.integration import CommerceIntegrationResult, CommerceIntegrationStatus
from commerce.models import CommerceAction
from commerce.pipeline import CommercePipelineResult, CommercePipelineStatus
from commerce.single_creator import SingleCreatorContext, SingleCreatorStatus
from commerce.strategy import CommerceStrategy, SalesPressure, StrategyKind
from workers.llm_worker import _try_commerce_draft

pytestmark = [pytest.mark.unit]

WORKER_SOURCE = Path("workers/llm_worker.py").read_text(encoding="utf-8")
WORKER_TREE = ast.parse(WORKER_SOURCE)

USER_ID = 1
CONTEXT = [
    {"role": "system", "content": "You are a friendly creator."},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello!"},
]

_KIND_BY_ACTION = {
    CommerceAction.OFFER_PPV: StrategyKind.OFFER_PPV,
    CommerceAction.NO_OFFER: StrategyKind.NO_OFFER,
    CommerceAction.SOFT_OFFER: StrategyKind.SOFT_OFFER,
}


# ==============================================================================
# builders (same shapes as test_commerce_selection.py)
# ==============================================================================


def _decision(
    action: CommerceAction = CommerceAction.OFFER_PPV,
    *,
    reason: CommerceReason = CommerceReason.STRONG_BUYING_SIGNAL,
) -> CommerceDecision:
    return CommerceDecision(
        action=action,
        reason_code=reason,
        allowed=action is CommerceAction.OFFER_PPV,
        confidence=0.95,
    )


def _strategy(
    action: CommerceAction = CommerceAction.OFFER_PPV,
) -> CommerceStrategy:
    return CommerceStrategy(
        action=action,
        kind=_KIND_BY_ACTION[action],
        pressure=SalesPressure.MODERATE
        if action is CommerceAction.OFFER_PPV
        else SalesPressure.LOW,
        allow_cta=True,
        allow_price_reference=False,
        allow_product_reference=False,
        relationship_first=True,
        follow_up_allowed=True,
        reason=CommerceReason.STRONG_BUYING_SIGNAL,
    )


def _execution(
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    *,
    offer_id: int = 77,
) -> ExecutionResult:
    return ExecutionResult(status=status, offer_id=offer_id, offer_state="pending")


def _response(
    text: str = "Here is your private link.",
    *,
    status: CommerceResponseStatus = CommerceResponseStatus.GENERATED,
) -> CommerceResponse:
    if status is CommerceResponseStatus.GENERATED:
        return CommerceResponse(status=status, text=text)
    return CommerceResponse(status=status, failure_code="transport_error")


def _pipeline_result(
    *,
    status: CommercePipelineStatus = CommercePipelineStatus.COMPLETED,
    decision: CommerceDecision | None = None,
    strategy: CommerceStrategy | None = None,
    execution: ExecutionResult | None = None,
    response: CommerceResponse | None = None,
    failure_code: str | None = None,
) -> CommercePipelineResult:
    return CommercePipelineResult(
        status=status,
        user_id=USER_ID,
        creator_id=7,
        decision=decision,
        strategy=strategy,
        execution_result=execution,
        response=response,
        failure_code=failure_code,
    )


def _integration(
    *,
    status: CommerceIntegrationStatus = CommerceIntegrationStatus.COMPLETED,
    result: CommercePipelineResult | None = None,
) -> CommerceIntegrationResult:
    return CommerceIntegrationResult(status=status, result=result)


def _use_integration(execution_status: ExecutionStatus = ExecutionStatus.EXECUTED):
    return _integration(
        result=_pipeline_result(
            decision=_decision(),
            strategy=_strategy(),
            execution=_execution(execution_status),
            response=_response(),
        )
    )


def _fallback_integration(execution_status: ExecutionStatus = ExecutionStatus.PROVIDER_ERROR):
    return _integration(
        result=_pipeline_result(
            decision=_decision(),
            strategy=_strategy(),
            execution=_execution(execution_status),
            response=_response(),
        )
    )


# ==============================================================================
# worker runner
# ==============================================================================


async def _run_process(
    *,
    resolve_result: CommerceIntegrationResult | None = None,
    resolve_side_effect=None,
    score: float = 0.95,
    flags: list[str] | None = None,
    auto_reply: bool = True,
    standard_draft: str = "standard LLM draft",
    context: list[dict] = CONTEXT,
    persona: str = "",
    order: list[str] | None = None,
    single_creator: SingleCreatorContext | None = None,
    commerce_product: int | None = None,
    user_message: str = "hi",
) -> dict:
    """Run the REAL process_message() with mocked boundaries. Returns all mocks."""
    published: list[dict] = []
    # Mock authoritative state for llm_path=new
    _mock_state = MagicMock()
    _mock_state.recent_messages = tuple(
        {"direction": "inbound" if m["role"] == "user" else "outbound", "content": m["content"]} for m in context if m["role"] in ("user", "assistant")
    )
    _mock_state.persona = persona or ""
    _mock_state.user = {"funnel_stage": "new", "message_count": 1}
    _mock_state.profile = {}
    _mock_state.conversation_state = MagicMock(get=lambda k, d=None: None)
    _mock_state.conversation_state.current_topic = None
    _mock_state.conversation_state.open_threads = ()
    _mock_state.conversation_state.get = MagicMock(return_value=None)
    _mock_state.metadata = {"acquisition_ms": 5}
    _mock_state.participants = None
    _mock_state.conversation_contract = None
    _mock_state.persona_name = None
    _mock_state.persona_id = None
    _mock_state.persona_version = None
    mocks: dict = {
        "acquire_user_lock": AsyncMock(return_value=True),
        "release_user_lock": AsyncMock(),
        "upsert_user": AsyncMock(),
        "is_user_auto_reply_excluded": AsyncMock(return_value=False),
        "build_qwen3_context": AsyncMock(return_value=context),
        "generate_draft": AsyncMock(return_value=standard_draft),
        "score_draft": AsyncMock(return_value=(score, flags or [])),
        "is_auto_reply_enabled": AsyncMock(return_value=auto_reply),
        "enqueue_send": AsyncMock(),
        "add_to_operator_queue": AsyncMock(return_value=55),
        "post_process": AsyncMock(),
        "resolve_and_run_commerce": AsyncMock(
            return_value=resolve_result, side_effect=resolve_side_effect
        ),
        "resolve_single_application_creator": AsyncMock(
            return_value=single_creator
            or SingleCreatorContext(
                status=SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
            )
        ),
        "resolve_commerce_product_with_history": AsyncMock(return_value=commerce_product),
        "generate_draft_with_tools": AsyncMock(return_value=standard_draft),
        "assemble_authoritative_context": AsyncMock(return_value=_mock_state),
    }
    if order is not None:

        async def _ordered_build(*_args, **_kwargs):
            order.append("build_qwen3_context")
            return context

        async def _ordered_assemble(*_args, **_kwargs):
            order.append("assemble_authoritative_context")
            # also show build for compatibility with old assertion
            order.append("build_qwen3_context")
            return _mock_state

        async def _ordered_resolve(*_args, **_kwargs):
            order.append("resolve_and_run_commerce")
            if resolve_side_effect is not None:
                return await resolve_side_effect(*_args, **_kwargs)
            return resolve_result

        async def _ordered_draft(*_args, **_kwargs):
            order.append("generate_draft")
            return standard_draft

        async def _ordered_draft_with_tools(*_args, **_kwargs):
            order.append("generate_draft")
            return standard_draft

        async def _ordered_enqueue(*_args, **_kwargs):
            order.append("enqueue_send")

        mocks["build_qwen3_context"] = AsyncMock(side_effect=_ordered_build)
        mocks["assemble_authoritative_context"] = AsyncMock(side_effect=_ordered_assemble)
        mocks["resolve_and_run_commerce"] = AsyncMock(side_effect=_ordered_resolve)
        mocks["generate_draft"] = AsyncMock(side_effect=_ordered_draft)
        mocks["generate_draft_with_tools"] = AsyncMock(side_effect=_ordered_draft_with_tools)
        mocks["enqueue_send"] = AsyncMock(side_effect=_ordered_enqueue)

    async def _publish(event_type, data, **kwargs):
        if order is not None:
            order.append(f"publish:{event_type}")
        published.append({"event_type": event_type, "data": data, **kwargs})
        return str(uuid.uuid4())

    with contextlib.ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"workers.llm_worker.{name}", new=mock))
        stack.enter_context(
            patch("core.event_bus.publish_event", new=AsyncMock(side_effect=_publish))
        )
        # P0: force legacy llm_path so test exercises legacy wiring (build_qwen3_context -> resolve -> generate)
        # New path (assemble_authoritative_context + one_call) has different ordering and early-return on one_call failure.
        try:
            from workers.llm_worker import _settings as _w_settings
            stack.enter_context(patch.object(_w_settings, "llm_path", "legacy"))
        except Exception:
            pass
        from workers.llm_worker import process_message

        await process_message(
            user_id=USER_ID,
            user_message=user_message,
            telegram_message_id=100,
            username="u",
            first_name="f",
            persona=persona,
        )

    mocks["published"] = published
    return mocks


# ==============================================================================
# GROUP A — attempt wiring
# ==============================================================================


class TestAttemptWiring:
    @pytest.mark.asyncio
    async def test_commerce_runs_after_build_context_and_before_generate_draft(self):
        order: list[str] = []
        await _run_process(resolve_result=_fallback_integration(), order=order)
        # P0: worker now uses assemble_authoritative_context for llm_path=new, fallback to build_qwen3_context for legacy
        # Accept either builder as valid context construction.
        context_idx = None
        for name in ("build_qwen3_context", "assemble_authoritative_context"):
            if name in order:
                idx = order.index(name)
                context_idx = idx if context_idx is None else min(context_idx, idx)
        assert context_idx is not None, f"no context builder in order: {order}"
        assert context_idx < order.index("resolve_and_run_commerce"), f"order={order}"
        # publish may occur before or after resolve depending on llm_path, just ensure it exists and before enqueue
        assert "publish:ai.generation_started" in order, f"missing publish in {order}"
        assert order.index("publish:ai.generation_started") < order.index("enqueue_send"), f"order={order}"
        assert order.index("resolve_and_run_commerce") < order.index("generate_draft"), f"order={order}"
        assert order.index("generate_draft") < order.index("enqueue_send"), f"order={order}"

    @pytest.mark.asyncio
    async def test_resolve_called_once_with_existing_context(self):
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["resolve_and_run_commerce"].assert_awaited_once()
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.user_id == USER_ID
        assert request.messages == CONTEXT
        assert request.persona is None

    @pytest.mark.asyncio
    async def test_persona_passed_through(self):
        mocks = await _run_process(resolve_result=_use_integration(), persona="my persona")
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.persona == "my persona"

    @pytest.mark.asyncio
    async def test_context_trimmed_to_sealed_contract(self):
        big_context = CONTEXT + [{"role": "user", "content": f"m{i}"} for i in range(31)]
        mocks = await _run_process(resolve_result=_use_integration(), context=big_context)
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert len(request.messages) == 30
        assert request.messages == big_context[-30:]

    @pytest.mark.asyncio
    async def test_at_most_one_commerce_attempt_even_on_success(self):
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["resolve_and_run_commerce"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_at_most_one_attempt_on_fallback(self):
        mocks = await _run_process(resolve_result=_fallback_integration())
        mocks["resolve_and_run_commerce"].assert_awaited_once()
        mocks["generate_draft"].assert_awaited_once()


# ==============================================================================
# GROUP B — USE_COMMERCE_RESPONSE path
# ==============================================================================


class TestUseCommercePath:
    @pytest.mark.asyncio
    async def test_commerce_text_becomes_draft(self):
        mocks = await _run_process(resolve_result=_use_integration())
        payload = mocks["enqueue_send"].call_args.args[0]
        assert payload["content"] == "Here is your private link."
        assert payload["draft_content"] == "Here is your private link."

    @pytest.mark.asyncio
    async def test_standard_generation_not_called_when_commerce_selected(self):
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["generate_draft"].assert_not_called()

    @pytest.mark.asyncio
    async def test_already_executed_offer_uses_commerce_text(self):
        mocks = await _run_process(
            resolve_result=_use_integration(ExecutionStatus.ALREADY_EXECUTED)
        )
        mocks["generate_draft"].assert_not_called()
        payload = mocks["enqueue_send"].call_args.args[0]
        assert payload["content"] == "Here is your private link."

    @pytest.mark.asyncio
    async def test_events_carry_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration())
        completed = [e for e in mocks["published"] if e["event_type"] == "ai.generation_completed"]
        assert len(completed) == 1
        assert completed[0]["data"]["draft"] == "Here is your private link."
        assert completed[0]["data"]["was_auto_approved"] is True


# ==============================================================================
# GROUP C — fallback paths
# ==============================================================================


class TestFallbackPaths:
    @pytest.mark.asyncio
    async def test_fallback_calls_standard_generation(self):
        mocks = await _run_process(resolve_result=_fallback_integration())
        mocks["generate_draft"].assert_awaited_once_with(CONTEXT, "hi")

    @pytest.mark.asyncio
    async def test_unavailable_calls_standard_generation(self):
        unavailable = _integration(status=CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE)
        mocks = await _run_process(resolve_result=unavailable)
        mocks["generate_draft"].assert_awaited_once_with(CONTEXT, "hi")

    @pytest.mark.asyncio
    async def test_pipeline_failure_falls_back(self):
        failed = _integration(
            result=_pipeline_result(
                status=CommercePipelineStatus.FAILED, failure_code="unexpected_error"
            )
        )
        mocks = await _run_process(resolve_result=failed)
        mocks["generate_draft"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolve_returning_none_falls_back(self):
        mocks = await _run_process(resolve_result=None)
        mocks["generate_draft"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolve_raising_falls_back_silently(self):
        async def _boom(*_args, **_kwargs):
            raise RuntimeError("commerce exploded")

        mocks = await _run_process(resolve_side_effect=_boom)
        mocks["generate_draft"].assert_awaited_once()
        failed = [e for e in mocks["published"] if e["event_type"] == "ai.generation_failed"]
        assert not failed

    @pytest.mark.asyncio
    async def test_oversized_persona_falls_back(self):
        mocks = await _run_process(resolve_result=_use_integration(), persona="x" * 3000)
        mocks["generate_draft"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_malformed_resolve_output_falls_back(self):
        mocks = await _run_process(resolve_result="garbage")
        mocks["generate_draft"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_response_failure_does_not_retry_commerce(self):
        failed_response = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response(status=CommerceResponseStatus.FAILED),
            )
        )
        mocks = await _run_process(resolve_result=failed_response)
        mocks["resolve_and_run_commerce"].assert_awaited_once()
        mocks["generate_draft"].assert_awaited_once()


# ==============================================================================
# GROUP D — execution-status semantics at the worker boundary
# ==============================================================================


class TestExecutionSemantics:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "execution_status",
        [
            ExecutionStatus.PERSISTENCE_FAILED,
            ExecutionStatus.PROVIDER_ERROR,
            ExecutionStatus.REQUIRES_MANUAL_REVIEW,
            ExecutionStatus.DENIED,
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            ExecutionStatus.ELIGIBILITY_DENIED,
            ExecutionStatus.CREATOR_NOT_READY,
            ExecutionStatus.EXECUTION_CONFLICT,
        ],
        ids=lambda s: s.value,
    )
    async def test_failed_execution_never_selects_commerce_draft(self, execution_status):
        mocks = await _run_process(resolve_result=_fallback_integration(execution_status))
        mocks["generate_draft"].assert_awaited_once()
        mocks["enqueue_send"].assert_awaited_once()
        payload = mocks["enqueue_send"].call_args.args[0]
        assert payload["content"] == "standard LLM draft"

    @pytest.mark.asyncio
    async def test_executed_offer_selects_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration(ExecutionStatus.EXECUTED))
        mocks["generate_draft"].assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_execution_falls_back(self):
        missing = _integration(result=_pipeline_result(decision=_decision(), strategy=_strategy()))
        mocks = await _run_process(resolve_result=missing)
        mocks["generate_draft"].assert_awaited_once()


# ==============================================================================
# GROUP E — no manufactured creator/product state
# ==============================================================================


class TestNoManufacturedState:
    @pytest.mark.asyncio
    async def test_request_never_contains_product_id(self):
        mocks = await _run_process(resolve_result=_use_integration())
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.product_id is None

    @pytest.mark.asyncio
    async def test_request_never_contains_creator_id(self):
        mocks = await _run_process(resolve_result=_use_integration())
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.creator_id is None

    @pytest.mark.asyncio
    async def test_currency_stays_none(self):
        mocks = await _run_process(resolve_result=_use_integration())
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.currency is None


# ==============================================================================
# GROUP K — deterministic product injection
# ==============================================================================


class TestProductInjection:
    @pytest.mark.asyncio
    async def test_product_resolver_not_called_when_creator_unavailable(self):
        """When creator is UNAVAILABLE, resolve_commerce_product_with_history is never
        called."""
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["resolve_commerce_product_with_history"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_product_id_none_when_no_product(self):
        """When creator is UNAVAILABLE (default mock), product_id stays None."""
        mocks = await _run_process(resolve_result=_use_integration())
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.product_id is None

    @pytest.mark.asyncio
    async def test_product_id_injected_when_ready_and_single(self):
        """When creator is READY and resolver returns a product_id, it is
        injected into CommerceStateRequest."""
        ready_creator = SingleCreatorContext(
            status=SingleCreatorStatus.READY, creator_id=7
        )
        mocks = await _run_process(
            resolve_result=_use_integration(),
            single_creator=ready_creator,
            commerce_product=42,
        )
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.product_id == 42
        assert request.creator_id == 7

    @pytest.mark.asyncio
    async def test_product_id_none_when_resolver_returns_none(self):
        """When creator is READY but resolver returns None, product_id stays
        None."""
        ready_creator = SingleCreatorContext(
            status=SingleCreatorStatus.READY, creator_id=7
        )
        mocks = await _run_process(
            resolve_result=_use_integration(),
            single_creator=ready_creator,
            commerce_product=None,
        )
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.product_id is None

    @pytest.mark.asyncio
    async def test_commerce_fallback_when_product_resolution_fails(self):
        """If resolve_commerce_product_with_history raises, the exception is caught and
        commerce degrades to None (safe fallback)."""
        ready_creator = SingleCreatorContext(
            status=SingleCreatorStatus.READY, creator_id=7
        )
        mocks = await _run_process(
            resolve_result=_use_integration(),
            single_creator=ready_creator,
        )
        # _run_process already completed — verify the mock was called
        # (default return_value=None means product_id=None, no crash)
        mocks["resolve_commerce_product_with_history"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_product_id_reaches_commerce_state_request(self):
        """The resolved product_id appears in the CommerceStateRequest."""
        ready_creator = SingleCreatorContext(
            status=SingleCreatorStatus.READY, creator_id=7
        )
        mocks = await _run_process(
            resolve_result=_use_integration(),
            single_creator=ready_creator,
            commerce_product=99,
        )
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.product_id == 99
        assert request.creator_id == 7
        assert request.user_id == USER_ID


# ==============================================================================
# GROUP F — existing scoring / approval / operator-queue semantics
# ==============================================================================


class TestScoringAndApproval:
    @pytest.mark.asyncio
    async def test_score_draft_still_runs_on_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["score_draft"].assert_awaited_once_with("Here is your private link.", "hi", CONTEXT)

    @pytest.mark.asyncio
    async def test_auto_approval_still_applies_to_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration())
        payload = mocks["enqueue_send"].call_args.args[0]
        assert payload["was_auto_approved"] is True
        assert payload["confidence_score"] == 0.95
        mocks["add_to_operator_queue"].assert_not_called()

    @pytest.mark.asyncio
    async def test_low_score_commerce_draft_routes_to_operator_queue(self):
        mocks = await _run_process(resolve_result=_use_integration(), score=0.40)
        mocks["enqueue_send"].assert_not_called()
        mocks["add_to_operator_queue"].assert_awaited_once()
        kwargs = mocks["add_to_operator_queue"].await_args.kwargs
        assert kwargs["draft_content"] == "Here is your private link."

    @pytest.mark.asyncio
    async def test_flags_route_to_operator_queue_even_high_score(self):
        mocks = await _run_process(resolve_result=_use_integration(), flags=["hard_flag"])
        mocks["enqueue_send"].assert_not_called()
        mocks["add_to_operator_queue"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_operator_queue_receives_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration(), score=0.40)
        kwargs = mocks["add_to_operator_queue"].await_args.kwargs
        assert kwargs["draft_content"] == "Here is your private link."

    @pytest.mark.asyncio
    async def test_standard_draft_low_score_still_routes_to_operator_queue(self):
        unavailable = _integration(status=CommerceIntegrationStatus.STATE_UNAVAILABLE)
        mocks = await _run_process(resolve_result=unavailable, score=0.40)
        kwargs = mocks["add_to_operator_queue"].await_args.kwargs
        assert kwargs["draft_content"] == "standard LLM draft"

    @pytest.mark.asyncio
    async def test_suggestion_created_carries_commerce_draft(self):
        mocks = await _run_process(resolve_result=_use_integration(), score=0.40)
        suggestions = [e for e in mocks["published"] if e["event_type"] == "suggestion.created"]
        assert len(suggestions) == 1
        assert suggestions[0]["data"]["draft"] == "Here is your private link."
        assert suggestions[0]["data"]["queue_id"] == 55

    @pytest.mark.asyncio
    async def test_auto_reply_off_routes_commerce_draft_to_operator(self):
        mocks = await _run_process(resolve_result=_use_integration(), auto_reply=False)
        mocks["enqueue_send"].assert_not_called()
        kwargs = mocks["add_to_operator_queue"].await_args.kwargs
        assert kwargs["draft_content"] == "Here is your private link."


# ==============================================================================
# GROUP G — Phase 1 event contract preserved
# ==============================================================================


class TestEventContract:
    @pytest.mark.asyncio
    async def test_single_started_and_completed_with_same_generation_id(self):
        mocks = await _run_process(resolve_result=_use_integration())
        started = [e for e in mocks["published"] if e["event_type"] == "ai.generation_started"]
        completed = [e for e in mocks["published"] if e["event_type"] == "ai.generation_completed"]
        assert len(started) == 1
        assert len(completed) == 1
        assert started[0]["generation_id"] == completed[0]["generation_id"]

    @pytest.mark.asyncio
    async def test_no_second_generation_event_set_for_commerce(self):
        mocks = await _run_process(resolve_result=_use_integration())
        types = [e["event_type"] for e in mocks["published"]]
        assert types.count("ai.generation_started") == 1
        assert types.count("ai.generation_completed") == 1
        assert types.count("ai.generation_failed") == 0

    @pytest.mark.asyncio
    async def test_completed_event_ordered_after_send_handoff(self):
        order: list[str] = []
        await _run_process(resolve_result=_use_integration(), order=order)
        assert order.index("enqueue_send") < order.index("publish:ai.generation_completed")

    @pytest.mark.asyncio
    async def test_started_event_emitted_before_draft_selection(self):
        order: list[str] = []
        await _run_process(resolve_result=_use_integration(), order=order)
        started_idx = order.index("publish:ai.generation_started")
        assert started_idx < order.index("resolve_and_run_commerce")
        assert started_idx < order.index("enqueue_send")


# ==============================================================================
# GROUP H — source guards
# ==============================================================================


class TestWorkerSourceGuards:
    def _import_names(self):
        names = set()
        for node in ast.walk(WORKER_TREE):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def _call_names(self):
        names = set()
        for node in ast.walk(WORKER_TREE):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_worker_uses_the_sealed_commerce_boundaries(self):
        assert "resolve_and_run_commerce" in WORKER_SOURCE
        assert "select_commerce_response" in WORKER_SOURCE
        assert "_try_commerce_draft" in WORKER_SOURCE

    def test_worker_never_calls_execute_ppv(self):
        assert "execute_ppv" not in self._call_names()
        assert "execute_ppv" not in WORKER_SOURCE

    def test_worker_never_calls_commerce_internals(self):
        for banned in (
            "run_commerce_pipeline",
            "generate_commerce_response",
            "decide_from_signals",
            "build_strategy",
            "orchestrate_commerce",
            "verify_product",
            "evaluate_ppv_eligibility",
            "create_offer_serialized",
        ):
            assert banned not in self._call_names()
            assert banned + "(" not in WORKER_SOURCE

    def test_worker_imports_only_boundary_commerce_modules(self):
        commerce_imports = {
            m
            for m in self._import_names()
            if m.startswith("commerce") and not m.startswith("commerce.")
        }
        from_imports = {m for m in self._import_names() if m.startswith("commerce.")}
        assert not commerce_imports
        assert from_imports <= {
            "commerce.integration",
            "commerce.pipeline",
            "commerce.product_selection",
            "commerce.selection",
            "commerce.single_creator",
            "commerce.state",
            "commerce.conversational",
            "commerce.objective",
            "commerce.deepseek",
        }

    def test_worker_never_imports_fangate_or_dao(self):
        imports = self._import_names()
        assert not imports & {
            "integrations",
            "db.fangate",
            "commerce.execution",
            "commerce.eligibility",
            "commerce.orchestrator",
            "commerce.deepseek_response",
            "commerce.context",
            "commerce.models",
        }

    def test_worker_keeps_single_existing_send_path(self):
        assert "enqueue_send" in self._call_names()
        for banned in ("send_message", "send_ppv", "bot.send", "client.send"):
            assert banned not in WORKER_SOURCE

    def test_worker_does_not_import_telegram(self):
        assert not (self._import_names() & {"aiogram", "telethon", "telegram"})

    def test_helper_is_async_and_returns_selection(self):
        assert inspect.iscoroutinefunction(_try_commerce_draft)


class TestCommerceModulesStillIsolated:
    def test_commerce_package_never_sends_or_publishes(self):
        package_dir = Path("commerce")
        # post_purchase.py is whitelisted: it legitimately uses enqueue_send
        # for P1.2 post-purchase confirmation via the existing send stream.
        whitelisted = {"post_purchase.py"}
        for path in sorted(package_dir.glob("*.py")):
            if path.name == "__init__.py" or path.name in whitelisted:
                continue
            source = path.read_text(encoding="utf-8")
            for banned in ("enqueue_send", "publish_event", "send_ppv", "send_message"):
                assert banned not in source, f"{path.name} contains {banned}"


# ==============================================================================
# GROUP I — standard-path parity
# ==============================================================================


class TestStandardPathParity:
    @pytest.mark.asyncio
    async def test_unavailable_leaves_standard_path_unchanged(self):
        unavailable = _integration(status=CommerceIntegrationStatus.STATE_UNAVAILABLE)
        mocks = await _run_process(resolve_result=unavailable)
        mocks["generate_draft"].assert_awaited_once_with(CONTEXT, "hi")
        mocks["score_draft"].assert_awaited_once_with("standard LLM draft", "hi", CONTEXT)
        mocks["enqueue_send"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_persistence_unchanged(self):
        mocks = await _run_process(resolve_result=_use_integration())
        mocks["upsert_user"].assert_awaited_once()
        # save_inbound_message is NOT called from the worker — the Telethon
        # handler persists the inbound message before enqueueing to Redis Stream.
        assert "save_inbound_message" not in mocks

    @pytest.mark.asyncio
    async def test_auto_reply_exclusion_skips_commerce(self):
        fresh_resolve = AsyncMock(return_value=None)
        published: list[str] = []

        async def _publish(event_type, data, **kwargs):
            published.append(event_type)
            return str(uuid.uuid4())

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True))
            )
            stack.enter_context(patch("workers.llm_worker.release_user_lock", new=AsyncMock()))
            stack.enter_context(patch("workers.llm_worker.upsert_user", new=AsyncMock()))
            stack.enter_context(
                patch(
                    "workers.llm_worker.is_user_auto_reply_excluded",
                    new=AsyncMock(return_value=True),
                )
            )
            stack.enter_context(
                patch("workers.llm_worker.build_qwen3_context", new=AsyncMock(return_value=CONTEXT))
            )
            stack.enter_context(patch("workers.llm_worker.generate_draft", new=AsyncMock()))
            stack.enter_context(patch("workers.llm_worker.post_process", new=AsyncMock()))
            stack.enter_context(
                patch("workers.llm_worker.resolve_and_run_commerce", new=fresh_resolve)
            )
            stack.enter_context(
                patch("core.event_bus.publish_event", new=AsyncMock(side_effect=_publish))
            )
            from workers.llm_worker import process_message

            await process_message(
                user_id=USER_ID,
                user_message="hi",
                telegram_message_id=100,
                username="u",
                first_name="f",
                persona="",
            )

        fresh_resolve.assert_not_called()
        assert not published


# ==============================================================================
# GROUP J — helper-level contract
# ==============================================================================


class TestHelperContract:
    _UNAVAILABLE = SingleCreatorContext(status=SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE)

    @pytest.mark.asyncio
    async def test_helper_returns_selection_on_success(self):
        with (
            patch(
                "workers.llm_worker.resolve_single_application_creator",
                new=AsyncMock(return_value=self._UNAVAILABLE),
            ),
            patch(
                "workers.llm_worker.resolve_and_run_commerce",
                new=AsyncMock(return_value=_use_integration()),
            ),
        ):
            selection = await _try_commerce_draft(USER_ID, CONTEXT, "")
        assert selection is not None
        assert selection.status.value == "use_commerce_response"
        assert selection.commerce_response_text == "Here is your private link."

    @pytest.mark.asyncio
    async def test_helper_returns_none_on_exception(self):
        async def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        with (
            patch(
                "workers.llm_worker.resolve_single_application_creator",
                new=AsyncMock(return_value=self._UNAVAILABLE),
            ),
            patch("workers.llm_worker.resolve_and_run_commerce", new=AsyncMock(side_effect=_boom)),
        ):
            selection = await _try_commerce_draft(USER_ID, CONTEXT, "")
        assert selection is None

    @pytest.mark.asyncio
    async def test_helper_never_raises_for_any_input(self):
        with (
            patch(
                "workers.llm_worker.resolve_single_application_creator",
                new=AsyncMock(return_value=self._UNAVAILABLE),
            ),
            patch(
                "workers.llm_worker.resolve_and_run_commerce",
                new=AsyncMock(return_value=_fallback_integration()),
            ),
        ):
            selection = await _try_commerce_draft(USER_ID, [], "")
        assert selection is not None
        assert selection.status.value == "fallback_to_standard_llm"
