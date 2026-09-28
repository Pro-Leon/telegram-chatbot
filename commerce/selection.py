"""Phase 5.4 Chk 6E — commerce applicability / selection boundary.

Answers the production question:

    "Given a resolved CommerceIntegrationResult, should the caller use the
    commerce-generated response or fall back to the normal LLM generation
    path?"

This is a PURE, deterministic interpretation of ALREADY-PRODUCED structured
results. It is NOT a decision engine, NOT an approval step, and NOT an
execution authority. It never enqueues, sends, publishes, scores, approves,
or mutates anything: it only picks WHICH draft the future 6F caller should
present downstream.

Inputs are sealed types only (``CommerceIntegrationResult`` -> sealed
``CommercePipelineResult``). No conversation text is parsed here — product,
price, URL, and offer validity were already established by the guarded
adapter; selection never re-validates natural language.

State machine (closed):

    integration status        -> COMMERCE_UNAVAILABLE
      (creator_context_unavailable / state_unavailable / product_unavailable /
       eligibility_unavailable / resolution_failed / failed / malformed input)

    pipeline COMPLETED + non-executing action
      (no_offer / relationship_building / soft_offer / follow_up /
       dont_offer-suppressed) -> FALLBACK_TO_STANDARD_LLM

    pipeline stage failure
      (decision_failed / strategy_failed / failed) -> FALLBACK, unexpected

    execution failure
      (any status outside EXECUTED/ALREADY_EXECUTED) -> FALLBACK, execution
      semantics preserved (execution_status passed through, offer_active False)

    invalid/empty G
      (response missing / FAILED / empty text) -> FALLBACK, never sent

    COMPLETED + OFFER_PPV + EXECUTED/ALREADY_EXECUTED + valid GENERATED
    response -> USE_COMMERCE_RESPONSE

Determinism: no clock, no randomness, no I/O, no imports of
datetime/time/random/uuid. Identical input yields an identical result.

Security: the result surface is closed (status, reason,
commerce_response_text, execution_status, offer_active) and never carries
credentials, webhook secrets, ciphertext, prompts, raw conversation,
tracebacks, or transport objects.
"""

import enum
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.integration import (
    CommerceIntegrationResult,
    CommerceIntegrationStatus,
)
from commerce.models import CommerceAction
from commerce.pipeline import (
    CommercePipelineResult,
    CommercePipelineStatus,
)

logger = logging.getLogger("commerce.selection")

# The ONLY execution statuses that prove an active offer exists. This must
# stay identical to ``commerce.deepseek_response._OFFER_ACTIVE_STATUSES`` so
# selection never claims an offer the adapter would not claim.
OFFER_ACTIVE_STATUSES = frozenset({ExecutionStatus.EXECUTED, ExecutionStatus.ALREADY_EXECUTED})

# Non-executing commerce actions: the strategy is conversational, not a PPV
# activation. Selection never forces commerce output for these — the standard
# conversational LLM should handle them.
_NON_EXECUTING_ACTIONS = frozenset(
    {
        CommerceAction.NO_OFFER,
        CommerceAction.RELATIONSHIP_BUILDING,
        CommerceAction.SOFT_OFFER,
        CommerceAction.FOLLOW_UP,
        CommerceAction.DONT_OFFER,
        CommerceAction.CHAT,
        CommerceAction.TIP_SUGGESTION,
        CommerceAction.OPERATOR_HANDOFF,
    }
)


class CommerceSelectionStatus(str, enum.Enum):
    """Closed outcome of the selection boundary."""

    USE_COMMERCE_RESPONSE = "use_commerce_response"
    FALLBACK_TO_STANDARD_LLM = "fallback_to_standard_llm"
    COMMERCE_UNAVAILABLE = "commerce_unavailable"


class CommerceSelectionReason(str, enum.Enum):
    """Closed reason set; never free-form exception strings."""

    COMMERCE_COMPLETED = "commerce_completed"
    COMMERCE_NOT_APPLICABLE = "commerce_not_applicable"
    RESPONSE_GENERATION_FAILED = "response_generation_failed"
    EXECUTION_FAILED = "execution_failed"
    STATE_UNAVAILABLE = "state_unavailable"
    CREATOR_CONTEXT_UNAVAILABLE = "creator_context_unavailable"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    ELIGIBILITY_UNAVAILABLE = "eligibility_unavailable"
    RESOLUTION_FAILED = "resolution_failed"
    EMPTY_RESPONSE = "empty_response"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED_ERROR = "unexpected_error"


_STATUS_TO_REASON = {
    CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE: (
        CommerceSelectionReason.CREATOR_CONTEXT_UNAVAILABLE
    ),
    CommerceIntegrationStatus.STATE_UNAVAILABLE: CommerceSelectionReason.STATE_UNAVAILABLE,
    CommerceIntegrationStatus.PRODUCT_UNAVAILABLE: CommerceSelectionReason.PRODUCT_UNAVAILABLE,
    CommerceIntegrationStatus.ELIGIBILITY_UNAVAILABLE: (
        CommerceSelectionReason.ELIGIBILITY_UNAVAILABLE
    ),
    CommerceIntegrationStatus.RESOLUTION_FAILED: CommerceSelectionReason.RESOLUTION_FAILED,
    CommerceIntegrationStatus.FAILED: CommerceSelectionReason.UNEXPECTED_ERROR,
}

_USE_REASONS = frozenset({CommerceSelectionReason.COMMERCE_COMPLETED})
_FALLBACK_REASONS = frozenset(
    {
        CommerceSelectionReason.COMMERCE_NOT_APPLICABLE,
        CommerceSelectionReason.RESPONSE_GENERATION_FAILED,
        CommerceSelectionReason.EXECUTION_FAILED,
        CommerceSelectionReason.EMPTY_RESPONSE,
        CommerceSelectionReason.INVALID_RESPONSE,
        CommerceSelectionReason.UNEXPECTED_ERROR,
    }
)
_UNAVAILABLE_REASONS = frozenset(
    {
        CommerceSelectionReason.STATE_UNAVAILABLE,
        CommerceSelectionReason.CREATOR_CONTEXT_UNAVAILABLE,
        CommerceSelectionReason.PRODUCT_UNAVAILABLE,
        CommerceSelectionReason.ELIGIBILITY_UNAVAILABLE,
        CommerceSelectionReason.RESOLUTION_FAILED,
        CommerceSelectionReason.UNEXPECTED_ERROR,
    }
)


class CommerceSelectionResult(BaseModel):
    """Safe, closed selection verdict for the future 6F caller.

    ``offer_active`` is the only derived security flag: it is True ONLY when
    the execution status sits in ``OFFER_ACTIVE_STATUSES``. It is never
    inferred from text and can never be set for a failed execution.
    """

    model_config = ConfigDict(extra="forbid")

    status: CommerceSelectionStatus
    reason: CommerceSelectionReason
    commerce_response_text: str | None = None
    execution_status: ExecutionStatus | None = None
    offer_active: bool = False

    @field_validator("execution_status", mode="before")
    @classmethod
    def _execution_status_typed(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, ExecutionStatus):
            raise ValueError("execution_status must be an ExecutionStatus member")
        return v

    @model_validator(mode="after")
    def _closed_consistency(self) -> "CommerceSelectionResult":
        if self.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE:
            if self.reason not in _USE_REASONS or not self.commerce_response_text:
                raise ValueError(
                    "USE_COMMERCE_RESPONSE requires commerce_completed reason and text"
                )
            if not self.offer_active or self.execution_status not in OFFER_ACTIVE_STATUSES:
                raise ValueError("USE_COMMERCE_RESPONSE requires a proven active offer")
        elif self.status is CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM:
            if self.reason not in _FALLBACK_REASONS:
                raise ValueError("inconsistent fallback reason")
            if self.commerce_response_text is not None:
                raise ValueError("fallback results must not carry commerce text")
        else:  # COMMERCE_UNAVAILABLE
            if self.reason not in _UNAVAILABLE_REASONS:
                raise ValueError("inconsistent unavailable reason")
            if self.commerce_response_text is not None or self.offer_active:
                raise ValueError("unavailable results carry no text and no offer claim")
        if self.offer_active and self.execution_status not in OFFER_ACTIVE_STATUSES:
            raise ValueError("offer_active requires an active execution status")
        return self


def _result(
    *,
    status: CommerceSelectionStatus,
    reason: CommerceSelectionReason,
    text: str | None = None,
    execution: ExecutionResult | None = None,
    offer_active: bool = False,
) -> CommerceSelectionResult:
    return CommerceSelectionResult(
        status=status,
        reason=reason,
        commerce_response_text=text,
        execution_status=(execution.status if execution is not None else None),
        offer_active=offer_active,
    )


def _pipeline_action(result: CommercePipelineResult) -> CommerceAction | None:
    """The authoritative action from the sealed result (decision wins)."""
    if result.decision is not None:
        return result.decision.action
    if result.strategy is not None:
        return result.strategy.action
    return None


def select_commerce_response(
    integration_result: CommerceIntegrationResult,
) -> CommerceSelectionResult:
    """Select whether the caller should use the commerce-generated response.

    Pure and deterministic; never raises. Malformed input yields
    COMMERCE_UNAVAILABLE/UNEXPECTED_ERROR rather than an escaping exception.
    """
    if integration_result is None or not isinstance(integration_result, CommerceIntegrationResult):
        return _result(
            status=CommerceSelectionStatus.COMMERCE_UNAVAILABLE,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )

    if integration_result.status is not CommerceIntegrationStatus.COMPLETED:
        reason = _STATUS_TO_REASON[integration_result.status]
        return _result(
            status=CommerceSelectionStatus.COMMERCE_UNAVAILABLE,
            reason=reason,
        )

    result = integration_result.result
    if result is None or not isinstance(result, CommercePipelineResult):
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )

    if result.status is CommercePipelineStatus.DECISION_FAILED:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )
    if result.status is CommercePipelineStatus.STRATEGY_FAILED:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )
    if result.status is CommercePipelineStatus.FAILED:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )

    action = _pipeline_action(result)
    if action is None:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.UNEXPECTED_ERROR,
        )

    if action in _NON_EXECUTING_ACTIONS:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.COMMERCE_NOT_APPLICABLE,
            execution=result.execution_result,
        )

    # action is OFFER_PPV: an active offer must be provable.
    execution = result.execution_result
    if execution is None or not isinstance(execution, ExecutionResult):
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.EXECUTION_FAILED,
        )
    if execution.status not in OFFER_ACTIVE_STATUSES:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.EXECUTION_FAILED,
            execution=execution,
        )

    response = result.response
    if response is None or not isinstance(response, CommerceResponse):
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.RESPONSE_GENERATION_FAILED,
            execution=execution,
        )
    if response.status is not CommerceResponseStatus.GENERATED:
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.RESPONSE_GENERATION_FAILED,
            execution=execution,
        )
    if not response.text or not response.text.strip():
        return _result(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.EMPTY_RESPONSE,
            execution=execution,
        )

    return _result(
        status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
        reason=CommerceSelectionReason.COMMERCE_COMPLETED,
        text=response.text.strip(),
        execution=execution,
        offer_active=True,
    )
