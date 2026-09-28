"""Phase 5.4 Chk 6H tests — single-creator production readiness.

Proves:

1. The single-creator resolver returns exactly one operational creator from
   the authoritative ``creator_integrations`` source, rejects zero creators
   (CREATOR_CONTEXT_UNAVAILABLE) and ambiguous multiple active creators
   (AMBIGUOUS_CREATOR_CONTEXT), never guesses, and never raises.
2. The worker boundary passes the resolved creator_id into the commerce
   request ONLY when READY; zero/ambiguous resolutions keep the standard
   (non-commerce) draft path with an intact Phase 1 event contract.

The worker wiring is tested through the REAL ``process_message`` boundary
with worker I/O mocked and ``resolve_and_run_commerce`` / the single-creator
resolver mocked at the worker seam (real ``select_commerce_response`` reused).
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from commerce.decision import CommerceDecision, CommerceReason
from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.integration import CommerceIntegrationResult, CommerceIntegrationStatus
from commerce.models import CommerceAction
from commerce.pipeline import CommercePipelineResult, CommercePipelineStatus
from commerce.single_creator import (
    SingleCreatorContext,
    SingleCreatorStatus,
    resolve_single_application_creator,
)
from commerce.strategy import CommerceStrategy, SalesPressure, StrategyKind

pytestmark = [pytest.mark.unit]

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


def _strategy(action: CommerceAction = CommerceAction.OFFER_PPV) -> CommerceStrategy:
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


def _ctx(status: SingleCreatorStatus, creator_id: int | None = None) -> SingleCreatorContext:
    return SingleCreatorContext(status=status, creator_id=creator_id)


async def _run(creator_context: SingleCreatorContext, resolve_result):
    """Run the REAL process_message() with a controlled creator resolution."""
    published: list[dict] = []

    async def _publish(event_type, data, **kwargs):
        published.append({"event_type": event_type, "data": data, **kwargs})
        return "eid"

    resolve = AsyncMock(return_value=resolve_result)
    mocks = {
        "acquire_user_lock": AsyncMock(return_value=True),
        "release_user_lock": AsyncMock(),
        "upsert_user": AsyncMock(),
        "is_user_auto_reply_excluded": AsyncMock(return_value=False),
        "build_qwen3_context": AsyncMock(return_value=CONTEXT),
        "generate_draft": AsyncMock(return_value="standard LLM draft"),
        "generate_draft_with_tools": AsyncMock(return_value="standard LLM draft"),
        "score_draft": AsyncMock(return_value=(0.95, [])),
        "is_auto_reply_enabled": AsyncMock(return_value=True),
        "enqueue_send": AsyncMock(),
        "add_to_operator_queue": AsyncMock(return_value=55),
        "post_process": AsyncMock(),
        "resolve_and_run_commerce": resolve,
        "resolve_single_application_creator": AsyncMock(return_value=creator_context),
    }
    with contextlib.ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"workers.llm_worker.{name}", new=mock))
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
    mocks["published"] = published
    return mocks


# ═══════════════════════════════════════════════════════════════════════════════
# Single-creator resolver
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleCreatorResolver:
    @pytest.mark.asyncio
    async def test_zero_active_creators_is_unavailable(self):
        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[])):
            res = await resolve_single_application_creator()
        assert res.status is SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert res.creator_id is None

    @pytest.mark.asyncio
    async def test_single_active_creator_is_ready(self):
        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[7])):
            res = await resolve_single_application_creator()
        assert res.status is SingleCreatorStatus.READY
        assert res.creator_id == 7

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_unavailable_without_raising(self):
        async def _boom():
            raise RuntimeError("connection refused")

        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(side_effect=_boom)):
            res = await resolve_single_application_creator()
        assert res.status is SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert res.creator_id is None

    @pytest.mark.asyncio
    async def test_two_active_creators_is_ambiguous(self):
        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[3, 9])):
            res = await resolve_single_application_creator()
        assert res.status is SingleCreatorStatus.AMBIGUOUS_CREATOR_CONTEXT
        assert res.creator_id is None

    @pytest.mark.asyncio
    async def test_dao_filters_active_only_and_orders_deterministically(self):
        class FakeConn:
            def __init__(self, rows):
                self._rows = rows
                self.query = None

            async def fetch(self, query, *args):
                self.query = query
                return sorted(self._rows, key=lambda r: r["creator_id"])

        class FakeAcquire:
            def __init__(self, rows):
                self._conn = FakeConn(rows)

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *exc):
                return False

        class FakePool:
            def __init__(self, rows):
                self._acq = FakeAcquire(rows)

            def acquire(self):
                return self._acq

        rows = [{"creator_id": 9}, {"creator_id": 3}]
        pool = FakePool(rows)
        with patch("db.fangate.get_pool", return_value=pool):
            from db.fangate import list_active_creator_ids

            ids = await list_active_creator_ids()
        assert ids == [3, 9]
        conn = pool._acq._conn
        assert "status = 'active'" in conn.query
        assert "ORDER BY creator_id" in conn.query


# ═══════════════════════════════════════════════════════════════════════════════
# Worker boundary wiring
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerSingleCreatorWiring:
    @pytest.mark.asyncio
    async def test_ready_creator_passes_creator_id_into_request(self):
        mocks = await _run(_ctx(SingleCreatorStatus.READY, creator_id=7), _fallback_integration())
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.creator_id == 7
        assert request.user_id == USER_ID
        assert request.messages == CONTEXT

    @pytest.mark.asyncio
    async def test_ready_creator_use_selection_drives_commerce_draft(self):
        mocks = await _run(_ctx(SingleCreatorStatus.READY, creator_id=7), _use_integration())
        mocks["generate_draft"].assert_not_awaited()
        mocks["enqueue_send"].assert_awaited_once()
        sent = mocks["enqueue_send"].await_args.args[0]
        assert sent["content"] == "Here is your private link."
        started = [e for e in mocks["published"] if e["event_type"] == "ai.generation_started"]
        completed = [e for e in mocks["published"] if e["event_type"] == "ai.generation_completed"]
        assert len(started) == 1 and len(completed) == 1
        assert started[0]["generation_id"] == completed[0]["generation_id"]
        assert not [e for e in mocks["published"] if "commerce" in e["event_type"]]

    @pytest.mark.asyncio
    async def test_unavailable_creator_keeps_standard_path(self):
        mocks = await _run(
            _ctx(SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE), _fallback_integration()
        )
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.creator_id is None
        mocks["generate_draft"].assert_awaited_once()
        mocks["enqueue_send"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ambiguous_creator_never_picks_and_keeps_standard_path(self):
        mocks = await _run(
            _ctx(SingleCreatorStatus.AMBIGUOUS_CREATOR_CONTEXT), _fallback_integration()
        )
        request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
        assert request.creator_id is None
        mocks["generate_draft"].assert_awaited_once()
        assert not [e for e in mocks["published"] if "failed" in e["event_type"]]

    @pytest.mark.asyncio
    async def test_resolver_failure_never_breaks_messaging(self):
        async def _boom():
            raise RuntimeError("boom")

        with patch(
            "workers.llm_worker.resolve_single_application_creator",
            new=AsyncMock(side_effect=_boom),
        ):
            # run without the harness default; wrap the worker call manually
            published: list[dict] = []

            async def _publish(event_type, data, **kwargs):
                published.append({"event_type": event_type, "data": data, **kwargs})
                return "eid"

            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True))
                )
                stack.enter_context(patch("workers.llm_worker.release_user_lock", new=AsyncMock()))
                stack.enter_context(patch("workers.llm_worker.upsert_user", new=AsyncMock()))
                stack.enter_context(
                    patch(
                        "workers.llm_worker.is_user_auto_reply_excluded",
                        new=AsyncMock(return_value=False),
                    )
                )
                stack.enter_context(
                    patch("workers.llm_worker.build_qwen3_context", new=AsyncMock(return_value=CONTEXT))
                )
                stack.enter_context(
                    patch("workers.llm_worker.generate_draft", new=AsyncMock(return_value="draft"))
                )
                stack.enter_context(
                    patch("workers.llm_worker.score_draft", new=AsyncMock(return_value=(0.95, [])))
                )
                stack.enter_context(
                    patch(
                        "workers.llm_worker.is_auto_reply_enabled", new=AsyncMock(return_value=True)
                    )
                )
                stack.enter_context(patch("workers.llm_worker.enqueue_send", new=AsyncMock()))
                stack.enter_context(patch("workers.llm_worker.post_process", new=AsyncMock()))
                stack.enter_context(
                    patch(
                        "workers.llm_worker.resolve_and_run_commerce",
                        new=AsyncMock(return_value=_fallback_integration()),
                    )
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
        started = [e for e in published if e["event_type"] == "ai.generation_started"]
        completed = [e for e in published if e["event_type"] == "ai.generation_completed"]
        assert len(started) == 1 and len(completed) == 1
        assert not [e for e in published if e["event_type"] == "ai.generation_failed"]

    @pytest.mark.asyncio
    async def test_conversation_text_never_supplies_creator(self):
        # Any non-READY resolution must yield creator_id=None regardless of the
        # pretended conversation content (behavioral guarantee of the wiring).
        for status in (
            SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE,
            SingleCreatorStatus.AMBIGUOUS_CREATOR_CONTEXT,
        ):
            mocks = await _run(_ctx(status), _fallback_integration())
            request = mocks["resolve_and_run_commerce"].await_args.kwargs["request"]
            assert request.creator_id is None
            assert request.messages == CONTEXT
