"""Phase 5.4 Chk 6D — state to commerce pipeline composition.

Thin composition boundary that proves authoritative application state can
populate the EXISTING Chk 5 pipeline boundary:

    CommerceStateRequest
        |  resolve_commerce_state()   (6C read-only resolver)
        v
    CommercePipelineRequest
        |  run_commerce_pipeline()    (existing pipeline, called EXACTLY once)
        v
    CommercePipelineResult

This module does NOT reimplement anything:

- no queries (resolution stays in commerce.state)
- no eligibility/decision/strategy/execution logic (all stay in their modules)
- no LLM adapter, no Gemini transport, no Fangate client
- no product selection, no creator guessing, no clock, no randomness
- no direct execute_ppv() call: activation may ONLY happen downstream inside
  run_commerce_pipeline -> orchestrate_commerce -> execute_ppv

App-state-wins semantics are inherited untouched: the resolver supplies the
authoritative fields; the pipeline supplies the ONLY place where conversational
signals (LLM estimates) may influence the decision; and signals_to_context
keeps application state in control. Conversation text is transported verbatim
and can never become product identity, price, sales URL, or eligibility.

On any resolution/pipeline failure the layer returns a structured
:class:`CommerceIntegrationResult`; it never fabricates a sellable state and
never exposes tracebacks, secrets, raw database errors, or conversation
content on the result surface.
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from commerce import pipeline as commerce_pipeline
from commerce.decision import CommerceDecisionPolicy
from commerce.pipeline import (
    PIPELINE_FAILURE_CODES,
    CommercePipelineResult,
)
from commerce.state import CommerceResolutionStatus, CommerceStateRequest, resolve_commerce_state

logger = logging.getLogger("commerce.integration")


class CommerceIntegrationStatus(str, Enum):
    """Closed outcome set of the composition layer.

    Failure values mirror ``commerce.state.CommerceResolutionStatus`` 1:1 so a
    resolution failure is never reinterpreted. ``eligibility_unavailable`` is
    reserved: 6C always produces an eligibility verdict, so today it cannot
    occur.
    """

    COMPLETED = "completed"
    CREATOR_CONTEXT_UNAVAILABLE = "creator_context_unavailable"
    STATE_UNAVAILABLE = "state_unavailable"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    ELIGIBILITY_UNAVAILABLE = "eligibility_unavailable"
    RESOLUTION_FAILED = "resolution_failed"
    FAILED = "failed"


_RESOLUTION_TO_INTEGRATION = {
    CommerceResolutionStatus.CREATOR_CONTEXT_UNAVAILABLE: (
        CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE
    ),
    CommerceResolutionStatus.STATE_UNAVAILABLE: CommerceIntegrationStatus.STATE_UNAVAILABLE,
    CommerceResolutionStatus.PRODUCT_UNAVAILABLE: CommerceIntegrationStatus.PRODUCT_UNAVAILABLE,
    CommerceResolutionStatus.RESOLUTION_FAILED: CommerceIntegrationStatus.RESOLUTION_FAILED,
}


class CommerceIntegrationResult(BaseModel):
    """Safe envelope around the existing pipeline result.

    ``status`` is the composition outcome. ``result`` is the sealed
    ``CommercePipelineResult`` whenever the pipeline ran — its status, decision,
    strategy, execution_result, response, and failure_code are preserved
    verbatim, never copied into new fields. ``failure_code`` mirrors the
    pipeline failure code at the composition surface (same closed set). No
    tracebacks, secrets, raw exceptions, or conversation content ever appear
    here.
    """

    model_config = ConfigDict(extra="forbid")

    status: CommerceIntegrationStatus
    result: CommercePipelineResult | None = None
    failure_code: str | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _result_must_be_verdict(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, CommercePipelineResult):
            raise ValueError("result must be a CommercePipelineResult instance")
        return v

    @field_validator("failure_code")
    @classmethod
    def _failure_code_from_closed_set(cls, v: str | None) -> str | None:
        if v is not None and v not in PIPELINE_FAILURE_CODES:
            raise ValueError("failure_code must be a known pipeline failure code")
        return v


async def resolve_and_run_commerce(
    *,
    request: CommerceStateRequest,
    policy: CommerceDecisionPolicy | None = None,
    signals: Any | None = None,
    user_message: str | None = None,
) -> CommerceIntegrationResult:
    """Resolve authoritative state and run the existing pipeline once.

    1. State resolution via ``resolve_commerce_state`` — never reimplemented.
    2. A non-READY resolution returns its closed-set status verbatim; the
       pipeline is never called with fabricated state.
    3. The resolved ``CommercePipelineRequest`` receives an optional policy
       override and is handed to ``run_commerce_pipeline`` EXACTLY once.
    4. The pipeline result is preserved (including execution failure) in the
       envelope. Unexpected exceptions map to ``FAILED``/``unexpected_error``.
    """
    try:
        resolution = await resolve_commerce_state(request)
    except Exception:
        logger.warning("commerce integration: state resolution raised", exc_info=True)
        return CommerceIntegrationResult(
            status=CommerceIntegrationStatus.FAILED, failure_code="unexpected_error"
        )

    if resolution.status is not CommerceResolutionStatus.READY:
        status = _RESOLUTION_TO_INTEGRATION[resolution.status]
        return CommerceIntegrationResult(status=status)

    pipeline_request = resolution.request
    if policy is not None:
        pipeline_request = pipeline_request.model_copy(update={"policy": policy})

    try:
        pipeline_result = await commerce_pipeline.run_commerce_pipeline(pipeline_request, signals=signals, user_message=user_message)
    except Exception:
        logger.warning("commerce integration: pipeline raised", exc_info=True)
        return CommerceIntegrationResult(
            status=CommerceIntegrationStatus.FAILED, failure_code="unexpected_error"
        )

    return CommerceIntegrationResult(
        status=CommerceIntegrationStatus.COMPLETED,
        result=pipeline_result,
        failure_code=pipeline_result.failure_code,
    )
