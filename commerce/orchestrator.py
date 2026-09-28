"""Deterministic commerce orchestration (Phase 5.4 Chk 3).

The orchestrator is a pure COORDINATOR of the existing deterministic
machinery — it composes, it never reinvents:

    CommerceConversationContext
        |  context.to_decision_context()
        v
    decide_commerce_action()          (authoritative decision)
        |
        v
    build_strategy()                  (authoritative strategy)
        |
        v
    execute_ppv()                     (ONLY for an authorized OFFER_PPV
                                       over an explicitly supplied product)
        |
        v
    CommerceOrchestrationResult

Rules enforced here:

- The decision engine decides. Eligibility is authoritative: a denied
  ``context.eligibility`` can never execute a PPV, even with buying signals.
- The strategy is always derived with :func:`commerce.strategy.build_strategy`
  — no second mapping, no escalation (NO_OFFER / SUPPRESSED /
  RELATIONSHIP_BUILDING / SOFT_OFFER / FOLLOW_UP can never become OFFER_PPV).
- The execution gate: ``execute_ppv`` is invoked ONLY when the decision is
  OFFER_PPV AND the context carries an explicitly supplied product (both
  identity and authoritative commerce state) AND eligibility allows.
  The orchestrator never manufactures an offer; it never accepts price,
  URL, currency, or product parameters from callers. ``execute_ppv``
  remains the sole PPV activation authority and its result is preserved
  verbatim — never reinterpreted.
- Failures are structured codes (decision_failure, strategy_failure,
  execution_failure, unexpected_error). No tracebacks, credentials,
  ciphertext, raw HTTP bodies, or infrastructure internals ever appear in
  the result.

Determinism: no random, no clock, no UUIDs, no hidden counters, no LLM
calls. Identical inputs produce equivalent results. The ONLY side effect in
the whole module is the already-authorized ``execute_ppv`` call.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commerce.context import CommerceConversationContext, StrictPositiveInt
from commerce.decision import (
    DEFAULT_COMMERCE_POLICY,
    CommerceDecision,
    CommerceDecisionPolicy,
    decide_commerce_action,
)
from commerce.execution import ExecutionResult, ExecutionStatus, execute_ppv
from commerce.models import CommerceAction
from commerce.strategy import CommerceStrategy, build_strategy

StrictBool = Annotated[bool, Field(strict=True)]

ORCHESTRATION_FAILURE_CODES = frozenset(
    {"decision_failure", "strategy_failure", "execution_failure", "unexpected_error"}
)

DEFAULT_CREATED_BY = "commerce_orchestrator"


@dataclass(frozen=True)
class _ActivationParams:
    """The only sanctioned activation input: an explicitly supplied product."""

    product_id: int
    age_verified: bool


def _activation_for(context: CommerceConversationContext) -> _ActivationParams | None:
    """Derive the activation input from authoritative context only.

    Conversation text, quoted prices, and inferred products NEVER count:
    identity, authoritative commerce state, and eligibility must all be
    explicitly present.
    """
    if not context.eligibility.allowed:
        return None
    identity = context.product_identity
    state = context.product_state
    if identity is None or state is None:
        return None
    return _ActivationParams(
        product_id=identity.product_id,
        age_verified=state.age_verified,
    )


class CommerceOrchestrationResult(BaseModel):
    """Structured orchestration outcome — safe metadata only.

    Carries the authoritative decision, strategy, and (when executed) the
    verbatim ``ExecutionResult``. NEVER carries secrets, ciphertext,
    authorization headers, raw HTTP bodies, LLM output, or tracebacks.
    ``conversation messages`` are not part of this surface at all.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: StrictPositiveInt
    creator_id: StrictPositiveInt
    decision: CommerceDecision | None = None
    strategy: CommerceStrategy | None = None
    execution_result: ExecutionResult | None = None
    success: StrictBool
    failure_code: str | None = None

    @field_validator("decision", mode="before")
    @classmethod
    def _decision_must_be_instance(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, CommerceDecision):
            raise ValueError("decision must be a CommerceDecision instance")
        return v

    @field_validator("execution_result", mode="before")
    @classmethod
    def _execution_result_must_be_instance(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, ExecutionResult):
            raise ValueError("execution_result must be an ExecutionResult instance")
        return v

    @field_validator("failure_code")
    @classmethod
    def _failure_code_from_closed_set(cls, v: str | None) -> str | None:
        if v is not None and v not in ORCHESTRATION_FAILURE_CODES:
            raise ValueError("failure_code must be a known orchestration failure code")
        return v


def _draft_result(
    context: CommerceConversationContext,
    *,
    decision: CommerceDecision | None = None,
    strategy: CommerceStrategy | None = None,
    execution_result: ExecutionResult | None = None,
    failure_code: str,
) -> CommerceOrchestrationResult:
    return CommerceOrchestrationResult(
        user_id=context.user_id,
        creator_id=context.creator_id,
        decision=decision,
        strategy=strategy,
        execution_result=execution_result,
        success=False,
        failure_code=failure_code,
    )


async def orchestrate_commerce(
    context: CommerceConversationContext,
    *,
    policy: CommerceDecisionPolicy | None = None,
    created_by: str = DEFAULT_CREATED_BY,
    decision: CommerceDecision | None = None,
) -> CommerceOrchestrationResult:
    """Run the deterministic commerce pipeline for one conversation context.

    Coordinator only: the decision engine, the strategy layer, and
    ``execute_ppv`` stay the sole authorities for their own concerns.
    Results are always produced — even on failure — and never contain
    internals beyond the stable failure code.

    P1.2 convergence: when *decision* is supplied it is the canonical
    per-message decision and the engine is NOT re-evaluated; otherwise
    the decision is derived from *context* as before (backward compat).
    """
    if policy is None:
        policy = DEFAULT_COMMERCE_POLICY

    # 1. Decision authority: the deterministic engine, verbatim.
    # P1.2: reuse canonical decision when supplied (single evaluation per message).
    if decision is None:
        try:
            decision = decide_commerce_action(context.to_decision_context(), policy=policy)
        except Exception:  # noqa: BLE001 — engine must never explode orchestration
            return _draft_result(context, failure_code="decision_failure")
    else:
        # Supplied canonical decision must be a valid CommerceDecision; validate
        # that it is an instance to avoid silent misuse.
        if not isinstance(decision, CommerceDecision):
            return _draft_result(context, failure_code="decision_failure")

    # 2. Strategy authority: always derived, never re-mapped.
    try:
        strategy = build_strategy(decision, context)
    except Exception:  # noqa: BLE001 — strategy must never explode orchestration
        return _draft_result(context, decision=decision, failure_code="strategy_failure")

    # 3. Execution gate: OFFER_PPV decision over an explicit, eligible product.
    execution_result: ExecutionResult | None = None
    failure_code: str | None = None
    activation = _activation_for(context)
    if decision.action is CommerceAction.OFFER_PPV and decision.allowed and activation is not None:
        try:
            execution_result = await execute_ppv(
                creator_id=context.creator_id,
                user_id=context.user_id,
                product_id=activation.product_id,
                decision=decision,
                created_by=created_by,
                age_verified=activation.age_verified,
            )
        except Exception:  # noqa: BLE001 — execution authority never leaks internals
            failure_code = "execution_failure"
        else:
            # The execution result is authoritative: any non-executed status
            # is preserved verbatim AND reported as not-success.
            if execution_result is None or execution_result.status is not ExecutionStatus.EXECUTED:
                failure_code = "execution_failure"

    try:
        return CommerceOrchestrationResult(
            user_id=context.user_id,
            creator_id=context.creator_id,
            decision=decision,
            strategy=strategy,
            execution_result=execution_result,
            success=failure_code is None,
            failure_code=failure_code,
        )
    except Exception:  # noqa: BLE001 — result assembly must never fabricate success
        return _draft_result(
            context,
            decision=decision,
            strategy=strategy,
            failure_code="unexpected_error",
        )
