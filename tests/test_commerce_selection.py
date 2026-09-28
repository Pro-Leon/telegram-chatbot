"""Phase 5.4 Chk 6E tests — commerce applicability / selection boundary.

Groups:

A: selection surface (closed statuses, closed reasons, sealed result model).
B: generated-response success (USE_COMMERCE_RESPONSE path).
C: response generation failures (missing / FAILED / RESPONSE_FAILED status).
D: empty response text (defensive emptiness).

E: execution failure matrix (every non-active execution status -> FALLBACK).

F: non-commerce actions fall back (all five non-executing actions).

G: no-product authority (explicit purchase intent w/o product never invents).

H: integration state failures -> COMMERCE_UNAVAILABLE (1:1 reason mapping).
I: pipeline stage failures -> FALLBACK (decision/strategy/failed).
J: foreign/malformed input isolation (never raises).

K: result envelope consistency rules (closed invariants).
L: determinism (identical input -> identical verdict, no clock/randomness).
M: security surface (AST/source: no forbidden imports or call sites).
N: no direct execution/send/publish call sites (source-level proof).
O: OFFER_ACTIVE_STATUSES parity with the response adapter.
P: decision (not strategy) is authoritative for the chosen action.
Q: whitespace/unicode handling for the selected reply text.
R: execution-status passthrough fidelity for fallback verdicts.
S: offer_active flag semantics (only proven-active executions claim it).
T: result payload never exposes conversation, prompts, or secrets.

``select_commerce_response`` is intentionally PURE and synchronous: it only
reads already-produced sealed results and never touches I/O, the clock,
randomness, workers, Telegram, Redis, or the event bus.
"""

import ast
import inspect
from pathlib import Path

import pytest

from commerce.decision import CommerceDecision, CommerceReason
from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.integration import CommerceIntegrationResult, CommerceIntegrationStatus
from commerce.models import CommerceAction
from commerce.pipeline import CommercePipelineResult, CommercePipelineStatus
from commerce.selection import (
    CommerceSelectionReason,
    CommerceSelectionResult,
    CommerceSelectionStatus,
    select_commerce_response,
)
from commerce.strategy import CommerceStrategy, SalesPressure, StrategyKind

pytestmark = [pytest.mark.unit]

SELECTION_SOURCE = Path("commerce/selection.py").read_text(encoding="utf-8")
SELECTION_TREE = ast.parse(SELECTION_SOURCE)

USER_ID = 42
CREATOR_ID = 7

# The exact active-offer parity set from commerce/deepseek_response.py:195.
_ACTIVE_STATUSES = frozenset({ExecutionStatus.EXECUTED, ExecutionStatus.ALREADY_EXECUTED})

_KIND_BY_ACTION = {
    CommerceAction.NO_OFFER: StrategyKind.NO_OFFER,
    CommerceAction.RELATIONSHIP_BUILDING: StrategyKind.RELATIONSHIP_BUILDING,
    CommerceAction.SOFT_OFFER: StrategyKind.SOFT_OFFER,
    CommerceAction.OFFER_PPV: StrategyKind.OFFER_PPV,
    CommerceAction.FOLLOW_UP: StrategyKind.FOLLOW_UP,
    CommerceAction.DONT_OFFER: StrategyKind.SUPPRESSED,
}

_NON_EXECUTING_ACTIONS = (
    CommerceAction.NO_OFFER,
    CommerceAction.RELATIONSHIP_BUILDING,
    CommerceAction.SOFT_OFFER,
    CommerceAction.FOLLOW_UP,
    CommerceAction.DONT_OFFER,
)

_NON_ACTIVE_EXECUTION_STATUSES = (
    ExecutionStatus.DENIED,
    ExecutionStatus.PRODUCT_UNAVAILABLE,
    ExecutionStatus.ELIGIBILITY_DENIED,
    ExecutionStatus.CREATOR_NOT_READY,
    ExecutionStatus.PROVIDER_ERROR,
    ExecutionStatus.PERSISTENCE_FAILED,
    ExecutionStatus.EXECUTION_CONFLICT,
    ExecutionStatus.REQUIRES_MANUAL_REVIEW,
)


# ==============================================================================
# builders (patterned after test_commerce_pipeline.py / test_commerce_integration.py)
# ==============================================================================


def _decision(
    action: CommerceAction = CommerceAction.OFFER_PPV,
    *,
    reason: CommerceReason = CommerceReason.STRONG_BUYING_SIGNAL,
    allowed: bool = True,
    review: bool = False,
) -> CommerceDecision:
    return CommerceDecision(
        action=action,
        reason_code=reason,
        allowed=allowed,
        confidence=0.95,
        requires_human_review=review,
    )


def _strategy(
    action: CommerceAction = CommerceAction.OFFER_PPV,
    *,
    reason: CommerceReason = CommerceReason.STRONG_BUYING_SIGNAL,
) -> CommerceStrategy:
    return CommerceStrategy(
        action=action,
        kind=_KIND_BY_ACTION[action],
        pressure=SalesPressure.LOW,
        allow_cta=True,
        allow_price_reference=False,
        allow_product_reference=False,
        relationship_first=True,
        follow_up_allowed=True,
        reason=reason,
    )


def _execution(
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    *,
    offer_id: int = 77,
    offer_state: str = "pending",
) -> ExecutionResult:
    return ExecutionResult(status=status, offer_id=offer_id, offer_state=offer_state)


def _response(
    text: str = "Here is your private link.",
    *,
    status: CommerceResponseStatus = CommerceResponseStatus.GENERATED,
    failure_code: str = "transport_error",
) -> CommerceResponse:
    if status is CommerceResponseStatus.GENERATED:
        return CommerceResponse(status=status, text=text)
    return CommerceResponse(status=status, failure_code=failure_code)


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
        creator_id=CREATOR_ID,
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
    failure_code: str | None = None,
) -> CommerceIntegrationResult:
    return CommerceIntegrationResult(status=status, result=result, failure_code=failure_code)


def _happy_pipeline() -> CommercePipelineResult:
    return _pipeline_result(
        decision=_decision(),
        strategy=_strategy(),
        execution=_execution(),
        response=_response(),
    )


def _happy_integration() -> CommerceIntegrationResult:
    return _integration(result=_happy_pipeline())


# ==============================================================================
# GROUP A — selection surface
# ==============================================================================


class TestSelectionSurface:
    def test_status_value_set_is_closed(self):
        assert {s.value for s in CommerceSelectionStatus} == {
            "use_commerce_response",
            "fallback_to_standard_llm",
            "commerce_unavailable",
        }

    def test_reason_value_set_is_closed(self):
        assert {r.value for r in CommerceSelectionReason} == {
            "commerce_completed",
            "commerce_not_applicable",
            "response_generation_failed",
            "execution_failed",
            "state_unavailable",
            "creator_context_unavailable",
            "product_unavailable",
            "eligibility_unavailable",
            "resolution_failed",
            "empty_response",
            "invalid_response",
            "unexpected_error",
        }

    def test_result_rejects_extra_fields(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE, nope=1)

    def test_result_fields_are_closed(self):
        assert set(CommerceSelectionResult.model_fields) == {
            "status",
            "reason",
            "commerce_response_text",
            "execution_status",
            "offer_active",
        }

    def test_api_is_sync_single_arg(self):
        assert inspect.signature(select_commerce_response).parameters["integration_result"].kind
        assert not inspect.iscoroutinefunction(select_commerce_response)

    def test_unknown_status_rejected(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status="made_up", reason=CommerceSelectionReason.UNEXPECTED_ERROR
            )

    def test_unknown_reason_rejected(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
                reason="made_up",
            )


# ==============================================================================
# GROUP B — generated-response success path
# ==============================================================================


class TestGeneratedResponseSuccess:
    def test_happy_path_uses_commerce_response(self):
        verdict = select_commerce_response(_happy_integration())
        assert verdict.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
        assert verdict.reason is CommerceSelectionReason.COMMERCE_COMPLETED
        assert verdict.commerce_response_text == "Here is your private link."
        assert verdict.execution_status is ExecutionStatus.EXECUTED
        assert verdict.offer_active is True

    def test_text_is_whitespace_stripped(self):
        response = _response("   hello there   ")
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=response,
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
        assert verdict.commerce_response_text == "hello there"

    def test_already_executed_offer_still_supported(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(ExecutionStatus.ALREADY_EXECUTED),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
        assert verdict.execution_status is ExecutionStatus.ALREADY_EXECUTED
        assert verdict.offer_active is True

    def test_non_ascii_text_is_preserved(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response("He aquí el enlace privado."),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.commerce_response_text == "He aquí el enlace privado."


# ==============================================================================
# GROUP C — response generation failures
# ==============================================================================


class TestResponseGenerationFailures:
    def test_missing_response_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=None,
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.RESPONSE_GENERATION_FAILED
        assert verdict.commerce_response_text is None
        assert verdict.offer_active is False

    def test_failed_response_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response(status=CommerceResponseStatus.FAILED),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.RESPONSE_GENERATION_FAILED
        assert verdict.commerce_response_text is None

    def test_pipeline_response_failed_status_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                status=CommercePipelineStatus.RESPONSE_FAILED,
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response(status=CommerceResponseStatus.FAILED),
                failure_code="transport_error",
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.RESPONSE_GENERATION_FAILED

    def test_pipeline_execution_failed_with_generated_response_still_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                status=CommercePipelineStatus.EXECUTION_FAILED,
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(ExecutionStatus.PROVIDER_ERROR),
                response=_response(),
                failure_code="execution_failure",
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.EXECUTION_FAILED
        assert verdict.commerce_response_text is None
        assert verdict.offer_active is False


# ==============================================================================
# GROUP D — empty response text
# ==============================================================================


class TestEmptyResponse:
    def _mute_response_text(self, text: str) -> CommerceIntegrationResult:
        pipeline = _pipeline_result(
            decision=_decision(),
            strategy=_strategy(),
            execution=_execution(),
            response=_response(),
        )
        pipeline.response.text = text  # post-construction: pydantic won't re-validate
        return _integration(result=pipeline)

    def test_blank_text_falls_back_as_empty(self):
        verdict = select_commerce_response(self._mute_response_text("   \n\t  "))
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.EMPTY_RESPONSE
        assert verdict.commerce_response_text is None

    def test_generated_response_cannot_be_empty_at_construction(self):
        with pytest.raises(ValueError):
            _response("   ")

    def test_whitespace_only_text_never_claims_offer(self):
        verdict = select_commerce_response(self._mute_response_text(""))
        assert verdict.offer_active is False
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM


# ==============================================================================
# GROUP E — execution failure matrix
# ==============================================================================


class TestExecutionFailureMatrix:
    @pytest.mark.parametrize(
        "status",
        _NON_ACTIVE_EXECUTION_STATUSES,
        ids=lambda s: s.value,
    )
    def test_non_active_execution_status_never_supported(self, status):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(status),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.EXECUTION_FAILED
        assert verdict.commerce_response_text is None
        assert verdict.offer_active is False
        assert verdict.execution_status is status

    def test_requires_manual_review_is_never_public_offer(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(ExecutionStatus.REQUIRES_MANUAL_REVIEW),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.EXECUTION_FAILED
        assert verdict.offer_active is False

    def test_missing_execution_result_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=None,
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.EXECUTION_FAILED
        assert verdict.execution_status is None

    def test_execution_failure_does_not_hide_status(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(ExecutionStatus.PERSISTENCE_FAILED),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.execution_status is ExecutionStatus.PERSISTENCE_FAILED
        assert verdict.offer_active is False


# ==============================================================================
# GROUP F — non-commerce actions fall back
# ==============================================================================


class TestNonCommerceActionFallback:
    @pytest.mark.parametrize("action", _NON_EXECUTING_ACTIONS, ids=lambda a: a.value)
    def test_every_non_executing_action_falls_back(self, action):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(action, allowed=action is CommerceAction.FOLLOW_UP),
                strategy=_strategy(action),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.COMMERCE_NOT_APPLICABLE
        assert verdict.commerce_response_text is None

    def test_non_executing_action_with_generated_response_still_falls_back(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.SOFT_OFFER),
                strategy=_strategy(CommerceAction.SOFT_OFFER),
                response=_response("a soft mention"),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.COMMERCE_NOT_APPLICABLE
        assert verdict.commerce_response_text is None

    def test_suppressed_action_falls_back(self):
        integration = _integration(
            result=_pipeline_result(decision=_decision(CommerceAction.DONT_OFFER))
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.COMMERCE_NOT_APPLICABLE

    def test_no_offer_never_selects_commerce_text(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.NO_OFFER),
                strategy=_strategy(CommerceAction.NO_OFFER),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.commerce_response_text is None


# ==============================================================================
# GROUP G — no-product authority
# ==============================================================================


class TestNoProductAuthority:
    def test_no_product_purchase_intent_never_invents(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(
                    CommerceAction.NO_OFFER, reason=CommerceReason.NO_RELEVANT_PRODUCT
                ),
                strategy=_strategy(CommerceAction.NO_OFFER),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.COMMERCE_NOT_APPLICABLE
        assert verdict.commerce_response_text is None

    def test_no_product_with_active_offer_still_no_invention(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(
                    CommerceAction.NO_OFFER, reason=CommerceReason.NO_RELEVANT_PRODUCT
                ),
                execution=_execution(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.commerce_response_text is None
        assert verdict.offer_active is False


# ==============================================================================
# GROUP H — integration state failures -> COMMERCE_UNAVAILABLE
# ==============================================================================


class TestIntegrationStateFailures:
    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            (
                CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE,
                CommerceSelectionReason.CREATOR_CONTEXT_UNAVAILABLE,
            ),
            (
                CommerceIntegrationStatus.STATE_UNAVAILABLE,
                CommerceSelectionReason.STATE_UNAVAILABLE,
            ),
            (
                CommerceIntegrationStatus.PRODUCT_UNAVAILABLE,
                CommerceSelectionReason.PRODUCT_UNAVAILABLE,
            ),
            (
                CommerceIntegrationStatus.ELIGIBILITY_UNAVAILABLE,
                CommerceSelectionReason.ELIGIBILITY_UNAVAILABLE,
            ),
            (
                CommerceIntegrationStatus.RESOLUTION_FAILED,
                CommerceSelectionReason.RESOLUTION_FAILED,
            ),
            (CommerceIntegrationStatus.FAILED, CommerceSelectionReason.UNEXPECTED_ERROR),
        ],
        ids=lambda item: item.value if hasattr(item, "value") else str(item),
    )
    def test_each_state_failure_maps_1_to_1(self, status, reason):
        verdict = select_commerce_response(_integration(status=status))
        assert verdict.status is CommerceSelectionStatus.COMMERCE_UNAVAILABLE
        assert verdict.reason is reason
        assert verdict.commerce_response_text is None
        assert verdict.execution_status is None
        assert verdict.offer_active is False

    def test_creator_context_unavailable(self):
        verdict = select_commerce_response(
            _integration(status=CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE)
        )
        assert verdict.status is CommerceSelectionStatus.COMMERCE_UNAVAILABLE
        assert verdict.reason is CommerceSelectionReason.CREATOR_CONTEXT_UNAVAILABLE


# ==============================================================================
# GROUP I — pipeline stage failures -> FALLBACK
# ==============================================================================


class TestPipelineStageFailures:
    @pytest.mark.parametrize(
        "status",
        [
            CommercePipelineStatus.DECISION_FAILED,
            CommercePipelineStatus.STRATEGY_FAILED,
            CommercePipelineStatus.FAILED,
        ],
        ids=lambda s: s.value,
    )
    def test_stage_failures_fallback_without_internals(self, status):
        integration = _integration(
            result=_pipeline_result(status=status, failure_code="unexpected_error")
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.UNEXPECTED_ERROR
        assert verdict.commerce_response_text is None
        assert verdict.execution_status is None


# ==============================================================================
# GROUP J — foreign/malformed input isolation
# ==============================================================================


class TestForeignInputIsolation:
    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "not a result",
            42,
            [],
            {},
            CommercePipelineResult(
                status=CommercePipelineStatus.COMPLETED, user_id=USER_ID, creator_id=CREATOR_ID
            ),
        ],
        ids=["none", "str", "int", "list", "dict", "pipeline_result"],
    )
    def test_never_raises_on_foreign_inputs(self, bad):
        verdict = select_commerce_response(bad)  # type: ignore[arg-type]
        assert verdict.status is CommerceSelectionStatus.COMMERCE_UNAVAILABLE
        assert verdict.reason is CommerceSelectionReason.UNEXPECTED_ERROR

    def test_completed_integration_without_pipeline_result_falls_back(self):
        verdict = select_commerce_response(_integration(status=CommerceIntegrationStatus.COMPLETED))
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.UNEXPECTED_ERROR

    def test_malformed_pipeline_result_never_selects_text(self):
        integration = _integration(
            status=CommerceIntegrationStatus.COMPLETED,
            result=_pipeline_result(status=CommercePipelineStatus.COMPLETED),
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.commerce_response_text is None

    def test_no_action_present_falls_back(self):
        integration = _integration(result=_pipeline_result(decision=None, strategy=None))
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.UNEXPECTED_ERROR


# ==============================================================================
# GROUP K — result envelope consistency rules
# ==============================================================================


class TestResultEnvelopeConsistency:
    def test_use_requires_commerce_completed_reason(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
                reason=CommerceSelectionReason.UNEXPECTED_ERROR,
                commerce_response_text="x",
                execution_status=ExecutionStatus.EXECUTED,
                offer_active=True,
            )

    def test_use_requires_text(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
                reason=CommerceSelectionReason.COMMERCE_COMPLETED,
                execution_status=ExecutionStatus.EXECUTED,
                offer_active=True,
            )

    def test_use_requires_proven_active_offer(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
                reason=CommerceSelectionReason.COMMERCE_COMPLETED,
                commerce_response_text="x",
                execution_status=ExecutionStatus.PROVIDER_ERROR,
                offer_active=True,
            )

    def test_fallback_must_not_carry_commerce_text(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
                reason=CommerceSelectionReason.COMMERCE_NOT_APPLICABLE,
                commerce_response_text="leak",
            )

    def test_unavailable_must_not_carry_text_or_offer(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.COMMERCE_UNAVAILABLE,
                reason=CommerceSelectionReason.STATE_UNAVAILABLE,
                commerce_response_text="nope",
            )
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.COMMERCE_UNAVAILABLE,
                reason=CommerceSelectionReason.STATE_UNAVAILABLE,
                offer_active=True,
                execution_status=ExecutionStatus.EXECUTED,
            )

    def test_offer_active_requires_active_execution_status(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
                reason=CommerceSelectionReason.COMMERCE_COMPLETED,
                commerce_response_text="x",
                execution_status=ExecutionStatus.PROVIDER_ERROR,
                offer_active=True,
            )


# ==============================================================================
# GROUP L — determinism
# ==============================================================================


class TestDeterminism:
    def test_identical_input_identical_verdict(self):
        first = select_commerce_response(_happy_integration())
        second = select_commerce_response(_happy_integration())
        assert first.model_dump() == second.model_dump()

    def test_identical_input_identical_failure_verdict(self):
        integration = _integration(status=CommerceIntegrationStatus.PRODUCT_UNAVAILABLE)
        first = select_commerce_response(integration)
        second = select_commerce_response(integration)
        assert first.model_dump() == second.model_dump()

    def test_no_clock_or_randomness_in_selection(self):
        imports = set()
        calls = set()
        for node in ast.walk(SELECTION_TREE):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                calls.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        assert not (imports & {"datetime", "time", "random", "uuid"})
        assert not calls & {"time", "random", "uuid", "datetime", "time_ns", "monotonic"}


# ==============================================================================
# GROUP M — security surface
# ==============================================================================


class TestSecuritySurface:
    def _import_names(self):
        names = set()
        for node in ast.walk(SELECTION_TREE):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def _call_names(self):
        names = set()
        for node in ast.walk(SELECTION_TREE):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_imports_are_minimal(self):
        assert self._import_names() <= {"logging", "enum", "typing", "pydantic", "commerce"}

    def test_no_forbidden_runtime_imports(self):
        assert not (
            self._import_names()
            & {
                "workers",
                "event_bus",
                "redis",
                "telegram",
                "httpx",
                "requests",
                "asyncpg",
                "websockets",
                "google",
                "deepseek",
            }
        )

    def test_no_credential_or_secret_fields(self):
        for banned in (
            "api_key",
            "apiKey",
            "token",
            "secret",
            "password",
            "credential",
            "ciphertext",
            "authorization",
            "bearer",
            "conversation",
            "prompt",
            "traceback",
        ):
            assert banned not in CommerceSelectionResult.model_fields

    def test_result_fields_are_closed(self):
        assert set(CommerceSelectionResult.model_fields) == {
            "status",
            "reason",
            "commerce_response_text",
            "execution_status",
            "offer_active",
        }

    def test_no_prompt_or_transport_symbols_published(self):
        for banned in (
            "prompt",
            "conversation",
            "api_key",
            "apiKey",
            "secret",
            "bearer",
            "webhook",
            "ciphertext",
        ):
            assert banned not in CommerceSelectionResult.model_fields


# ==============================================================================
# GROUP N — no direct execution/send/publish call sites
# ==============================================================================


class TestNoExecutionAuthority:
    def _call_names(self):
        names = set()
        for node in ast.walk(SELECTION_TREE):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_no_execution_call_sites(self):
        for banned in (
            "execute_ppv",
            "create_offer",
            "send_ppv",
            "enqueue_send",
            "publish_event",
            "generate_content",
        ):
            assert banned not in self._call_names()
            assert banned + "(" not in SELECTION_SOURCE

    def test_no_pipeline_or_decision_call_sites(self):
        for banned in (
            "run_commerce_pipeline",
            "decide_from_signals",
            "generate_commerce_response",
            "extract_commerce_signals",
            "build_strategy",
            "orchestrate_commerce",
            "resolve_commerce_state",
            "verify_product",
        ):
            assert banned not in self._call_names()
            assert banned + "(" not in SELECTION_SOURCE

    def test_no_side_effect_import_star(self):
        assert "import *" not in SELECTION_SOURCE


# ==============================================================================
# GROUP O — OFFER_ACTIVE_STATUSES parity
# ==============================================================================


class TestOfferActiveParity:
    def test_active_statuses_match_the_response_adapter(self):
        from commerce.selection import OFFER_ACTIVE_STATUSES as selection_active

        assert selection_active == _ACTIVE_STATUSES

    def test_exactly_two_active_statuses(self):
        from commerce.selection import OFFER_ACTIVE_STATUSES

        assert OFFER_ACTIVE_STATUSES == frozenset(
            {ExecutionStatus.EXECUTED, ExecutionStatus.ALREADY_EXECUTED}
        )

    def test_every_other_status_is_inactive(self):
        assert set(ExecutionStatus) - _ACTIVE_STATUSES == set(_NON_ACTIVE_EXECUTION_STATUSES)


# ==============================================================================
# GROUP P — decision (not strategy) is authoritative
# ==============================================================================


class TestDecisionAuthority:
    def test_decision_offer_ppv_wins_over_strategy(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.OFFER_PPV),
                strategy=_strategy(CommerceAction.NO_OFFER),
                execution=_execution(),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE

    def test_decision_no_offer_wins_over_offer_ppv_strategy(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.NO_OFFER),
                strategy=_strategy(CommerceAction.OFFER_PPV),
                execution=_execution(),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM
        assert verdict.reason is CommerceSelectionReason.COMMERCE_NOT_APPLICABLE


# ==============================================================================
# GROUP Q — whitespace/unicode handling
# ==============================================================================


class TestWhitespaceAndUnicode:
    def test_internal_newlines_are_preserved(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response("first line\nsecond line"),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.commerce_response_text == "first line\nsecond line"

    def test_leading_and_trailing_whitespace_stripped(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(),
                response=_response("  padded  "),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.commerce_response_text == "padded"


# ==============================================================================
# GROUP R — execution-status passthrough fidelity
# ==============================================================================


class TestExecutionPassthrough:
    def test_fallback_preserves_execution_status(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(),
                strategy=_strategy(),
                execution=_execution(ExecutionStatus.REQUIRES_MANUAL_REVIEW),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.execution_status is ExecutionStatus.REQUIRES_MANUAL_REVIEW
        assert verdict.offer_active is False

    def test_non_executing_action_passes_execution_through(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.SOFT_OFFER),
                strategy=_strategy(CommerceAction.SOFT_OFFER),
                execution=_execution(ExecutionStatus.DENIED),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.execution_status is ExecutionStatus.DENIED
        assert verdict.offer_active is False

    def test_success_preserves_execution_status(self):
        verdict = select_commerce_response(_happy_integration())
        assert verdict.execution_status is ExecutionStatus.EXECUTED


# ==============================================================================
# GROUP S — offer_active flag semantics
# ==============================================================================


class TestOfferActiveSemantics:
    def test_use_always_claims_only_when_active_execution(self):
        for status in _ACTIVE_STATUSES:
            integration = _integration(
                result=_pipeline_result(
                    decision=_decision(),
                    strategy=_strategy(),
                    execution=_execution(status),
                    response=_response(),
                )
            )
            verdict = select_commerce_response(integration)
            assert verdict.offer_active is True

    def test_fallback_never_claims_offer(self):
        integration = _integration(
            result=_pipeline_result(
                decision=_decision(CommerceAction.NO_OFFER),
                strategy=_strategy(CommerceAction.NO_OFFER),
                response=_response(),
            )
        )
        verdict = select_commerce_response(integration)
        assert verdict.offer_active is False

    def test_unavailable_never_claims_offer(self):
        verdict = select_commerce_response(_integration(status=CommerceIntegrationStatus.FAILED))
        assert verdict.offer_active is False

    def test_offer_active_is_never_writable_to_true_without_active_status(self):
        with pytest.raises(ValueError):
            CommerceSelectionResult(
                status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
                reason=CommerceSelectionReason.COMMERCE_COMPLETED,
                commerce_response_text="x",
                execution_status=ExecutionStatus.PROVIDER_ERROR,
                offer_active=True,
            )


# ==============================================================================
# GROUP T — payload never exposes conversation, prompts, or secrets
# ==============================================================================


class TestPayloadSecrecy:
    def test_dump_contains_no_conversation_or_prompts(self):
        verdict = select_commerce_response(_happy_integration())
        dump = verdict.model_dump_json()
        assert "conversation" not in dump
        assert "prompt" not in dump
        assert "messages" not in dump
        assert "traceback" not in dump

    def test_dump_contains_no_credentials(self):
        verdict = select_commerce_response(_happy_integration())
        dump = verdict.model_dump_json()
        for banned in ("api_key", "secret", "bearer", "authorization", "ciphertext", "webhook"):
            assert banned not in dump

    def test_dump_returns_only_closed_fields(self):
        verdict = select_commerce_response(_happy_integration())
        assert set(verdict.model_dump()) == {
            "status",
            "reason",
            "commerce_response_text",
            "execution_status",
            "offer_active",
        }

    def test_failure_payload_is_minimal(self):
        verdict = select_commerce_response(_integration(status=CommerceIntegrationStatus.FAILED))
        assert set(verdict.model_dump()) == {
            "status",
            "reason",
            "commerce_response_text",
            "execution_status",
            "offer_active",
        }
        assert verdict.model_dump() == {
            "status": CommerceSelectionStatus.COMMERCE_UNAVAILABLE,
            "reason": CommerceSelectionReason.UNEXPECTED_ERROR,
            "commerce_response_text": None,
            "execution_status": None,
            "offer_active": False,
        }
