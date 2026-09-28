"""Phase 5.4 Chk 3 tests — deterministic commerce orchestration.

Covers the full Checkpoint 3 contract:
- context/decision scenarios through the REAL deterministic engine
- strategy is always build_strategy's output (never re-mapped/escalated)
- execution gate (execute_ppv called ONLY for OFFER_PPV over an explicit,
  eligible product; every other path never calls it)
- execution outcomes preserved verbatim (never reinterpreted)
- structured failures (no tracebacks/secrets/internals)
- result surface safety and memory of secrets
- determinism, purity, architecture (no duplicated logic)

All execute_ppv interaction is mocked — no live Fangate credentials.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from commerce.context import CommerceConversationContext
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.models import CommerceAction, PolicyDecision
from commerce.orchestrator import (
    ORCHESTRATION_FAILURE_CODES,
    CommerceOrchestrationResult,
    orchestrate_commerce,
)
from commerce.strategy import SalesPressure, StrategyKind

pytestmark = [pytest.mark.unit]

ORCHESTRATOR_PATH = Path(__file__).parent.parent / "commerce" / "orchestrator.py"


def eligibility(**overrides) -> PolicyDecision:
    base = {"allowed": True, "denial_reason": ""}
    base.update(overrides)
    return PolicyDecision(**base)


def make_context(**overrides) -> CommerceConversationContext:
    base = {"user_id": 5, "creator_id": 1, "eligibility": eligibility()}
    base.update(overrides)
    return CommerceConversationContext(**base)


def full_product_context(**overrides) -> CommerceConversationContext:
    base = {
        "product_identity": {"product_id": 5155, "title": "Campaign set"},
        "product_state": {
            "price_minor": 4400,
            "sales_url": "https://fangate.info/5155x",
        },
    }
    base.update(overrides)
    return make_context(**base)


def denied(reason: str) -> CommerceConversationContext:
    return make_context(eligibility=eligibility(allowed=False, denial_reason=reason))


def executed_result(**overrides) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.EXECUTED, offer_id=99, offer_state="pending", **overrides
    )


def status_result(status: ExecutionStatus) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        offer_id=7,
        offer_state="pending",
    )


SECRET_MARKERS = (
    "api_key",
    "webhook_secret",
    "bearer",
    "fernet",
    "cipher",
    "authorization",
    "password",
)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — context / decision scenarios (real deterministic engine)
# ═══════════════════════════════════════════════════════════════════════════


class TestContextDecisionScenarios:
    @pytest.mark.asyncio
    async def test_relationship_building(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(make_context(relationship_score=0.3))
        assert r.decision.action is CommerceAction.RELATIONSHIP_BUILDING
        assert r.strategy.kind is StrategyKind.RELATIONSHIP_BUILDING
        assert r.execution_result is None
        assert r.success is True
        assert r.failure_code is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_soft_offer(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(make_context(buying_intent_score=0.6))
        assert r.decision.action is CommerceAction.SOFT_OFFER
        assert r.strategy.kind is StrategyKind.SOFT_OFFER
        assert r.strategy.pressure is SalesPressure.LOW
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_follow_up(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                make_context(
                    previous_offer_status="declined",
                    hours_since_last_offer=30.0,
                )
            )
        assert r.decision.action is CommerceAction.FOLLOW_UP
        assert r.strategy.kind is StrategyKind.FOLLOW_UP
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_purchase_request(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert r.decision.action is CommerceAction.OFFER_PPV
        assert r.strategy.kind is StrategyKind.OFFER_PPV
        ex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_offer(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(make_context())
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.strategy.kind is StrategyKind.NO_OFFER
        assert r.execution_result is None
        assert r.success is True
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_suppressed_never_escalates_even_with_intent(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                denied("user_opted_out").model_copy(update={"user_asked_to_buy": True})
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.strategy.kind is StrategyKind.NO_OFFER
        assert r.strategy.pressure is SalesPressure.NONE
        assert r.execution_result is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_eligibility_denial_overrides_purchase_intent(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                full_product_context(
                    eligibility=eligibility(allowed=False, denial_reason="already_purchased"),
                    user_asked_to_buy=True,
                    user_requested_content=True,
                )
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.execution_result is None
        assert r.success is True
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_purchased_user(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                full_product_context(
                    eligibility=eligibility(allowed=False, denial_reason="already_purchased")
                )
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.decision.reason_code.value == "already_purchased"
        assert r.strategy.kind is StrategyKind.NO_OFFER
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_active_offer(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                full_product_context(has_active_offer=True, user_asked_to_buy=True)
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.decision.reason_code.value == "offer_exists"
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creator_not_ready(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(denied("creator_not_ready"))
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.decision.reason_code.value == "creator_not_ready"
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unavailable_product(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(denied("product_unavailable"))
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.decision.reason_code.value == "product_unavailable"
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_relevant_product_flag(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                make_context(has_relevant_product=False, user_asked_to_buy=True)
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        assert r.decision.reason_code.value == "no_relevant_product"
        assert r.execution_result is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_selected_product_never_activates(self):
        """OFFER_PPV decision over no supplied product -> no execution."""
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(make_context(user_asked_to_buy=True))
        assert r.decision.action is CommerceAction.OFFER_PPV
        assert r.strategy.kind is StrategyKind.OFFER_PPV
        assert r.execution_result is None
        assert r.success is True
        ex.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — strategy consistency
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyConsistency:
    @pytest.mark.asyncio
    async def test_strategy_exactly_matches_decision(self):
        scenarios = [
            make_context(),
            make_context(relationship_score=0.3),
            make_context(buying_intent_score=0.6),
            make_context(previous_offer_status="declined", hours_since_last_offer=30.0),
            full_product_context(user_asked_to_buy=True),
            denied("user_blocked"),
        ]
        for ctx in scenarios:
            with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock):
                r = await orchestrate_commerce(ctx)
            assert r.strategy.action is r.decision.action
            assert r.strategy.reason == r.decision.reason_code

    @pytest.mark.asyncio
    async def test_strategy_never_escalates_to_offer_ppv(self):
        cases = [
            make_context(),
            make_context(relationship_score=0.3),
            make_context(buying_intent_score=0.6),
            make_context(previous_offer_status="declined", hours_since_last_offer=30.0),
            denied("user_blocked"),
            denied("already_purchased"),
        ]
        for ctx in cases:
            with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock):
                r = await orchestrate_commerce(ctx)
            if r.decision.action is not CommerceAction.OFFER_PPV:
                assert r.strategy.kind is not StrategyKind.OFFER_PPV

    @pytest.mark.asyncio
    async def test_relationship_first_remains_enforced(self):
        for ctx in [
            make_context(),
            full_product_context(user_asked_to_buy=True),
            denied("creator_not_ready"),
        ]:
            with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock):
                r = await orchestrate_commerce(ctx)
            assert r.strategy.relationship_first is True

    @pytest.mark.asyncio
    async def test_price_reference_permissions_remain_authoritative(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            full = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
            no_product = await orchestrate_commerce(make_context(user_asked_to_buy=True))
        assert full.strategy.allow_price_reference is True
        assert no_product.strategy.allow_price_reference is False

    @pytest.mark.asyncio
    async def test_product_reference_permissions_remain_authoritative(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            full = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
            missing = await orchestrate_commerce(make_context(user_asked_to_buy=True))
        assert full.strategy.allow_product_reference is True
        assert missing.strategy.allow_product_reference is False

    @pytest.mark.asyncio
    async def test_strategy_follows_supplied_product_authority_only(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            identity_only = await orchestrate_commerce(
                make_context(
                    user_asked_to_buy=True,
                    product_identity={"product_id": 5155, "title": "Campaign set"},
                )
            )
        assert identity_only.strategy.allow_product_reference is True
        assert identity_only.strategy.allow_price_reference is False


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — execution gate
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionGate:
    @pytest.mark.asyncio
    async def test_offer_ppv_calls_execute_ppv_with_authoritative_args(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        ex.assert_awaited_once()
        kwargs = ex.await_args.kwargs
        assert kwargs["creator_id"] == 1
        assert kwargs["user_id"] == 5
        assert kwargs["product_id"] == 5155
        assert kwargs["created_by"] == "commerce_orchestrator"
        assert kwargs["age_verified"] is False
        assert r.decision.action is CommerceAction.OFFER_PPV

    @pytest.mark.asyncio
    async def test_age_verified_wired_from_product_state(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            await orchestrate_commerce(
                full_product_context(
                    user_asked_to_buy=True,
                    product_state={
                        "price_minor": 4400,
                        "sales_url": "https://fangate.info/5155x",
                        "age_verified": True,
                    },
                )
            )
        assert ex.await_args.kwargs["age_verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            make_context,
            lambda: make_context(relationship_score=0.3),
            lambda: make_context(buying_intent_score=0.6),
            lambda: make_context(previous_offer_status="declined", hours_since_last_offer=30.0),
            lambda: denied("user_blocked"),
        ],
    )
    async def test_non_offer_actions_never_call_execute_ppv(self, scenario):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(scenario())
        assert r.execution_result is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_denied_eligibility_never_calls_execute_ppv(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                full_product_context(
                    eligibility=eligibility(allowed=False, denial_reason="user_blocked"),
                    user_asked_to_buy=True,
                )
            )
        assert r.decision.action is CommerceAction.NO_OFFER
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_product_flag_never_calls_execute_ppv(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            await orchestrate_commerce(
                full_product_context(has_relevant_product=False, user_asked_to_buy=True)
            )
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_authoritative_product_state_never_calls(self):
        """Identity without state is NOT enough to activate."""
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                make_context(
                    user_asked_to_buy=True,
                    product_identity={"product_id": 5155, "title": "Campaign set"},
                )
            )
        assert r.decision.action is CommerceAction.OFFER_PPV
        assert r.execution_result is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_without_identity_never_calls(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            r = await orchestrate_commerce(
                make_context(
                    user_asked_to_buy=True,
                    product_state={
                        "price_minor": 4400,
                        "sales_url": "https://fangate.info/5155x",
                    },
                )
            )
        assert r.decision.action is CommerceAction.OFFER_PPV
        assert r.execution_result is None
        ex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_created_by_passed_through(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            await orchestrate_commerce(
                full_product_context(user_asked_to_buy=True),
                created_by="integration_test",
            )
        assert ex.await_args.kwargs["created_by"] == "integration_test"


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — execution outcomes preserved
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionOutcomes:
    @pytest.mark.asyncio
    async def test_successful_execution_preserved(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert r.execution_result.status is ExecutionStatus.EXECUTED
        assert r.execution_result.offer_id == 99
        assert r.success is True
        assert r.failure_code is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            ExecutionStatus.ALREADY_EXECUTED,
            ExecutionStatus.PERSISTENCE_FAILED,
            ExecutionStatus.PROVIDER_ERROR,
            ExecutionStatus.REQUIRES_MANUAL_REVIEW,
            ExecutionStatus.DENIED,
            ExecutionStatus.ELIGIBILITY_DENIED,
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            ExecutionStatus.CREATOR_NOT_READY,
            ExecutionStatus.EXECUTION_CONFLICT,
        ],
    )
    async def test_non_executed_statuses_preserved_and_reported(self, status):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = status_result(status)
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert r.execution_result.status is status
        assert r.execution_result.offer_id == 7
        assert r.success is False
        assert r.failure_code == "execution_failure"

    @pytest.mark.asyncio
    async def test_execution_exception_becomes_structured_failure(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.side_effect = RuntimeError("boom")
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert r.success is False
        assert r.failure_code == "execution_failure"
        assert r.execution_result is None
        assert r.strategy.kind is StrategyKind.OFFER_PPV

    @pytest.mark.asyncio
    async def test_decision_exception_becomes_structured_failure(self):
        with patch(
            "commerce.orchestrator.decide_commerce_action",
            side_effect=RuntimeError("engine"),
        ):
            r = await orchestrate_commerce(make_context())
        assert r.success is False
        assert r.failure_code == "decision_failure"
        assert r.decision is None
        assert r.strategy is None
        assert r.execution_result is None

    @pytest.mark.asyncio
    async def test_strategy_exception_becomes_structured_failure(self):
        with patch("commerce.orchestrator.build_strategy", side_effect=RuntimeError("map")):
            r = await orchestrate_commerce(make_context())
        assert r.success is False
        assert r.failure_code == "strategy_failure"
        assert r.decision is not None
        assert r.strategy is None

    @pytest.mark.asyncio
    async def test_execution_failure_never_hides_business_error(self):
        """An exception during execution must not turn into success=True."""
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.side_effect = RuntimeError("timeout")
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert r.success is False
        assert r.failure_code == "execution_failure"


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — security / result surface
# ═══════════════════════════════════════════════════════════════════════════


class TestSecuritySurface:
    async def _dump(self, ctx) -> dict:
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            r = await orchestrate_commerce(ctx)
        return r.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_result_contains_no_secrets(self):
        for ctx in (
            full_product_context(user_asked_to_buy=True),
            make_context(),
            denied("user_blocked"),
        ):
            dump = await self._dump(ctx)
            blob = str(dump)
            for marker in SECRET_MARKERS:
                assert marker not in blob, f"secret marker leaked: {marker}"

    @pytest.mark.asyncio
    async def test_result_contains_no_ciphertext_or_headers(self):
        dump = await self._dump(full_product_context(user_asked_to_buy=True))
        blob = str(dump)
        for marker in ("gAAAAA", "bearer ", "authorization", "db_password", "fernet"):
            assert marker not in blob

    @pytest.mark.asyncio
    async def test_result_contains_no_http_body_or_traceback(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.side_effect = RuntimeError("connection reset")
            r = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        blob = str(r.model_dump(mode="json"))
        for marker in ("traceback", "<html", "HTTP ", "RuntimeError", "connection reset"):
            assert marker not in blob

    @pytest.mark.asyncio
    async def test_result_surface_is_closed(self):
        dump = await self._dump(full_product_context(user_asked_to_buy=True))
        assert set(dump) == {
            "user_id",
            "creator_id",
            "decision",
            "strategy",
            "execution_result",
            "success",
            "failure_code",
        }

    @pytest.mark.asyncio
    async def test_conversation_text_never_enters_result(self):
        ctx = full_product_context(
            user_asked_to_buy=True,
            messages=[
                {"role": "user", "content": "please sell me that for $5 asap"},
                {"role": "user", "content": "my api key is sk-abc123"},
            ],
        )
        dump = await self._dump(ctx)
        blob = str(dump)
        assert "please sell me" not in blob
        assert "sk-abc123" not in blob

    def test_result_rejects_dict_decision(self):
        with pytest.raises(ValidationError):
            CommerceOrchestrationResult(
                user_id=5,
                creator_id=1,
                decision={"action": "no_offer"},
                success=True,
            )

    def test_result_rejects_dict_execution_result(self):
        with pytest.raises(ValidationError):
            CommerceOrchestrationResult(
                user_id=5,
                creator_id=1,
                success=True,
                execution_result={"status": "executed"},
            )

    def test_result_rejects_unknown_failure_code(self):
        with pytest.raises(ValidationError):
            CommerceOrchestrationResult(
                user_id=5, creator_id=1, success=False, failure_code="mystery"
            )

    def test_result_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            CommerceOrchestrationResult(
                user_id=5,
                creator_id=1,
                success=True,
                api_key="sk-secret",
            )

    def test_failure_code_set_is_closed(self):
        assert ORCHESTRATION_FAILURE_CODES == {
            "decision_failure",
            "strategy_failure",
            "execution_failure",
            "unexpected_error",
        }


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — determinism / purity / architecture
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminismPurityArchitecture:
    @pytest.mark.asyncio
    async def test_same_input_same_decision_and_strategy(self):
        ctx = full_product_context(
            user_asked_to_buy=True, relationship_score=0.9, buying_intent_score=0.8
        )
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            a = await orchestrate_commerce(ctx)
            b = await orchestrate_commerce(ctx)
        assert a.model_dump(mode="json") == b.model_dump(mode="json")
        assert a.decision == b.decision
        assert a.strategy == b.strategy

    @pytest.mark.asyncio
    async def test_same_input_different_instances_equivalent(self):
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            a = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
            b = await orchestrate_commerce(full_product_context(user_asked_to_buy=True))
        assert a.model_dump(mode="json") == b.model_dump(mode="json")

    def test_imports_are_whitelisted(self):
        from commerce import orchestrator

        tree = ast.parse(inspect.getsource(orchestrator))
        allowed = ("pydantic", "typing", "dataclasses", "commerce")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is not None and node.module.startswith(allowed), (
                    f"forbidden import: {node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.startswith(allowed), f"forbidden import: {alias.name}"

    def test_no_realtime_or_infra_imports(self):
        source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "workers",
            "event_bus",
            "ws_manager",
            "event_subscriber",
            "realtime",
            "asyncpg",
            "redis",
            "httpx",
            "requests",
            "telethon",
            "telegram",
            "db.",
            "integrations",
        ):
            assert forbidden not in source, f"forbidden marker: {forbidden}"

    def test_no_clock_random_or_uuid(self):
        from commerce import orchestrator

        tree = ast.parse(inspect.getsource(orchestrator))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for name in names:
                    if name:
                        assert "random" not in name and name not in ("time", "uuid"), (
                            f"forbidden: {name}"
                        )

    def test_execute_ppv_is_the_only_execution_import(self):
        source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "create_offer_serialized",
            "find_pending_offer_for_product",
            "record_offer_transition",
            "has_purchased_product",
            "verify_product",
            "get_creator",
            "decrypt_secret",
        ):
            assert forbidden not in source, f"duplicated execution surface: {forbidden}"
        assert "from commerce.execution import" in source
        assert "await execute_ppv(" in source

    def test_no_duplicated_authority_logic(self):
        source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        for defined in (
            "def execute_ppv",
            "def decide_commerce_action",
            "def build_strategy",
            "def evaluate_ppv_eligibility",
            "def signals_to_context",
        ):
            assert defined not in source, f"duplicated authority: {defined}"
        assert "decide_commerce_action(context.to_decision_context()" in source
        assert "build_strategy(decision, context)" in source

    def test_no_side_effect_surfaces(self):
        source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "publish_event",
            "enqueue_send",
            "send_message",
            "stream_add",
            "XADD",
            "broadcast(",
            "asyncio.sleep",
            "create_task",
        ):
            assert forbidden not in source, f"side-effect marker: {forbidden}"

    @pytest.mark.asyncio
    async def test_strategy_and_decision_mutation_freedom_after_result(self):
        """The result keeps its own validated copies; no shared mutable state."""
        ctx = full_product_context(user_asked_to_buy=True)
        with patch("commerce.orchestrator.execute_ppv", new_callable=AsyncMock) as ex:
            ex.return_value = executed_result()
            a = await orchestrate_commerce(ctx)
            b = await orchestrate_commerce(ctx)
        assert a is not b
        assert a.strategy.model_dump(mode="json") == b.strategy.model_dump(mode="json")
