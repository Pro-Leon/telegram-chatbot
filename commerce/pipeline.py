"""Phase 5.4 Checkpoint 5 — clean application boundary for the commerce pipeline.

This module is the ONLY translation layer between the existing conversation
application (Telegram handler -> memory.context.build_context -> LLM worker)
and the completed commerce domain. It wires NOTHING: no worker, no Redis, no
Telegram, no event bus, no WebSocket. It returns a structured result that a
future checkpoint can consume.

Composition only — every authority stays where it already lives:

    application state (request)
        |  role_content_messages()        (translation, no logic)
        v
    CommerceConversationContext
        |  extract_commerce_signals()     (DeepSeek V4 Flash, advisory)
        v
    decision (decide_from_signals -> deterministic engine)
        |  build_strategy()              (no escalation)
        v
    orchestrate_commerce()               (gate: execute_ppv at most once,
        |                                 SOLE execution authority)
        v
    generate_commerce_response()         (proposal text, validated)
        |
        v
    CommercePipelineResult               (structured, no secrets)

Invariants enforced here:

- DeepSeek contributes ONLY conversational signals (intent flags + bounded
  scores). product_id, price, sales_url, currency, eligibility, age
  verification, purchase state, active offers, cooldowns, attempt budgets,
  and creator_sales_enabled are all caller-supplied application state; the
  pipeline only validates and transports them.
- No product is ever invented: product_identity/product_state are passed
  straight through (None travels as None).
- ``execute_ppv`` is reached ONLY through ``orchestrate_commerce``'s own
  gate (OFFER_PPV + allowed + supplied product). This module never calls it
  directly, never retries it, and never calls it because response
  generation failed.
- One orchestration call per pipeline run. Once ``execute_ppv`` has run
  successfully, a response-generation failure leaves the execution result
  untouched — the offer is never re-executed.
- The result surface carries codes and sealed models only: no credentials,
  ciphertext, authorization headers, raw model output, raw exceptions, or
  tracebacks.
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commerce.context import (
    CommerceConversationContext,
    CommerceConversationMessage,
    NonNegativeFloat,
    ProductCommerceState,
    ProductIdentity,
    ScoreFloat,
    StrictBool,
    StrictNonNegativeInt,
    StrictPositiveInt,
)
from commerce.decision import (
    DEFAULT_COMMERCE_POLICY,
    CommerceDecision,
    CommerceDecisionPolicy,
)
from commerce.dao import mark_offer_declined
from commerce.deepseek import extract_commerce_signals
from commerce.deepseek_response import (
    RESPONSE_FAILURE_CODES,
    CommerceResponse,
    CommerceResponseInput,
    CommerceResponseStatus,
    generate_commerce_response,
)
from commerce.execution import ExecutionResult
from commerce.feedback import RejectionType, classify_rejection
from commerce.models import OFFER_STATES, OfferState, PolicyDecision
from commerce.orchestrator import (
    ORCHESTRATION_FAILURE_CODES,
    CommerceOrchestrationResult,
    orchestrate_commerce,
)
from commerce.relationship import check_operator_handoff
from commerce.signals import (
    _COMMERCIAL_INTENTS,
    PRICE_ASK_THRESHOLD,
    CommerceSignals,
    _derive_conversational_phase,
    decide_from_signals,
)
from commerce.strategy import CommerceStrategy, build_strategy
from commerce.models import CreatorCapabilities

logger = logging.getLogger("commerce_pipeline")

PIPELINE_CREATED_BY = "commerce_pipeline"
PIPELINE_MAX_MESSAGES = 30
PIPELINE_MAX_MESSAGE_CHARS = 800
PIPELINE_PERSONA_MAX = 2000
PIPELINE_CURRENCY_MAX = 12

# Every failure_code a pipeline result may carry: orchestration staging
# codes plus the response adapter's closed failure set.
PIPELINE_FAILURE_CODES = frozenset(set(ORCHESTRATION_FAILURE_CODES) | set(RESPONSE_FAILURE_CODES))


class CommercePipelineStatus(str, Enum):
    """Five-way stage distinction required by the phase contract."""

    COMPLETED = "completed"
    DECISION_FAILED = "decision_failed"
    STRATEGY_FAILED = "strategy_failed"
    EXECUTION_FAILED = "execution_failed"
    RESPONSE_FAILED = "response_failed"
    FAILED = "failed"


class CommercePipelineRequest(BaseModel):
    """Strict application-side input: everything the pipeline may rely on.

    Every commerce-authoritative field (eligibility, product identity/state,
    currency, cooldowns, budgets, offer/purchase state) is supplied by the
    caller from application/DB/Fangate state. The pipeline never derives
    these from the LLM. ``messages`` are raw role/content items exactly as
    produced by ``memory.context.build_context`` (system/user/assistant).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: StrictPositiveInt
    creator_id: StrictPositiveInt

    messages: list[dict[str, Any]] = Field(max_length=PIPELINE_MAX_MESSAGES)

    eligibility: PolicyDecision

    product_identity: ProductIdentity | None = None
    product_state: ProductCommerceState | None = None
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=PIPELINE_CURRENCY_MAX,
    )
    persona: str | None = Field(
        default=None,
        max_length=PIPELINE_PERSONA_MAX,
    )
    policy: CommerceDecisionPolicy | None = None

    relationship_score: ScoreFloat | None = None
    messages_since_last_offer: StrictNonNegativeInt = 0
    messages_since_last_purchase: StrictNonNegativeInt = 0
    hours_since_last_offer: NonNegativeFloat = None
    hours_since_last_purchase: NonNegativeFloat = None
    recent_offer_count: StrictNonNegativeInt = 0
    recent_purchase_count: StrictNonNegativeInt = 0
    recent_sales_attempt_count: StrictNonNegativeInt = 0
    previous_offer_status: str | None = None
    has_active_offer: StrictBool = False
    has_relevant_product: StrictBool = True
    creator_sales_enabled: StrictBool = True

    # Segment context (informational only — not authorization)
    user_segment_names: list[str] = Field(default_factory=list)
    user_segment_count: StrictNonNegativeInt = 0

    # Phase C: Relationship context (from derive_relationship_state)
    relationship_state: str = "cold"
    commercial_pressure: str = "none"
    tip_eligibility: str = "ineligible"
    tip_reason: str = ""
    handoff_needed: StrictBool = False
    handoff_reason: str | None = None

    # Phase C.1-C: Behavioral feedback context (from get_behavioral_feedback_context)
    consecutive_rejections: StrictNonNegativeInt = 0
    total_purchases: StrictNonNegativeInt = 0
    total_tips_received: StrictNonNegativeInt = 0
    hours_since_last_tip: NonNegativeFloat = None
    tip_suggestions_sent: StrictNonNegativeInt = 0
    tip_suggestions_ignored: StrictNonNegativeInt = 0
    aftercare_status: str = "none"
    commercial_paused: StrictBool = False
    fan_expressed_appreciation: StrictBool = False
    fan_asked_how_to_support: StrictBool = False
    repeat_purchase_eligible: StrictBool = False

    # Phase C.1-E: Creator capabilities
    creator_capabilities: CreatorCapabilities | None = None

    # Phase 9: turn-scoped commerce context (worker-supplied, advisory only).
    # None = legacy / unknown: _phase9_present False, fences dormant, every
    # existing behavior preserved. Explicit values engage the float/soft/
    # relationship fences in commerce/decision.py without touching thresholds.
    current_content_interest: bool | None = None
    current_content_disinterest: bool | None = None
    user_initiated_commercial: bool | None = None
    continuation_context: bool | None = None
    warmth_without_commercial_evidence: bool | None = None
    authorization_basis: str | None = None

    @field_validator("eligibility", mode="before")
    @classmethod
    def _eligibility_must_be_verdict(cls, v: Any) -> Any:
        if not isinstance(v, PolicyDecision):
            raise ValueError("eligibility must be a PolicyDecision instance")  # noqa: TRY004 — pydantic wraps into ValidationError
        return v

    @field_validator("policy", mode="before")
    @classmethod
    def _policy_must_be_engine_policy(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, CommerceDecisionPolicy):
            raise ValueError("policy must be a CommerceDecisionPolicy instance")
        return v

    @field_validator("messages")
    @classmethod
    def _messages_shape_valid(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, item in enumerate(v):
            role = item.get("role")
            content = item.get("content")
            if role not in ("system", "user", "assistant"):
                raise ValueError(f"messages[{index}].role must be system/user/assistant")
            if not isinstance(content, str):
                raise ValueError(f"messages[{index}].content must be a string")  # noqa: TRY004 — pydantic wraps into ValidationError
        return v

    @field_validator("previous_offer_status", mode="before")
    @classmethod
    def _offer_status_from_closed_set(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, OfferState):
            return v.value
        if isinstance(v, str) and v in OFFER_STATES:
            return v
        raise ValueError("previous_offer_status must be a known offer state")


class CommercePipelineResult(BaseModel):
    """Structured application-facing outcome — safe metadata only.

    Carries the sealed decision/strategy/execution result and the validated
    response. NEVER carries credentials, ciphertext, authorization headers,
    raw model output, raw exceptions, or tracebacks. ``failure_code`` is
    drawn from the closed ``PIPELINE_FAILURE_CODES`` set.
    """

    model_config = ConfigDict(extra="forbid")

    status: CommercePipelineStatus
    user_id: StrictPositiveInt
    creator_id: StrictPositiveInt
    decision: CommerceDecision | None = None
    strategy: CommerceStrategy | None = None
    execution_result: ExecutionResult | None = None
    response: CommerceResponse | None = None
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
        if v is not None and v not in PIPELINE_FAILURE_CODES:
            raise ValueError("failure_code must be a known pipeline failure code")
        return v


def role_content_messages(items: list[dict[str, Any]]) -> list[CommerceConversationMessage]:
    """Translate raw application messages (build_context shape) into the
    sealed conversation-message type.

    Pure translation with deterministic bounds: at most
    ``PIPELINE_MAX_MESSAGES`` messages (latest kept), each content truncated
    to ``PIPELINE_MAX_MESSAGE_CHARS``. Roles must be system/user/assistant;
    anything else raises ValueError (caller contract violation).
    """
    if not items:
        return []
    if len(items) > PIPELINE_MAX_MESSAGES:
        items = items[-PIPELINE_MAX_MESSAGES:]
    result: list[CommerceConversationMessage] = []
    for item in items:
        role = item.get("role")
        content = item.get("content")
        if role not in ("system", "user", "assistant"):
            raise ValueError("message role must be system/user/assistant")
        if not isinstance(content, str):
            raise TypeError("message content must be a string")  # caller contract violation
        result.append(
            CommerceConversationMessage(role=role, content=content[:PIPELINE_MAX_MESSAGE_CHARS])
        )
    return result


def _apply_signal_flags(
    context: CommerceConversationContext,
    signals: CommerceSignals | None,
    user_message: str | None = None,
) -> CommerceConversationContext:
    """Merge LLM-derived conversational flags onto an app-only context.

    The ONLY signal-derived fields, mapped mechanically exactly like
    ``signal_to_context`` so the orchestrator's internal decision agrees
    with the standalone decision step:
    - user_asked_to_buy      <- explicit_purchase_request AND deterministic verifier
    - user_asked_about_price <- (requested_price quoted or price_interest >= threshold) AND deterministic verifier
    - user_requested_content <- explicit_content_request
    - buying_intent_score    <- purchase_intent
    - relationship_score     <- caller value wins, else relationship_engagement

    ``None`` signals are treated as no-intent (application state wins).
    When ``user_message`` is supplied, explicit/price mapping is AND-gated with
    deterministic verifiers (P0). When ``None`` (legacy tests), legacy behavior preserved.
    """
    if signals is None:
        return context

    # Classify rejection type if there are negative signals
    rejection_type = None
    if signals.negative_intent_tags:
        rejection_type = classify_rejection(
            negative_intent_tags=signals.negative_intent_tags,
            negative_sentiment=signals.negative_sentiment,
            price_interest=signals.price_interest,
            intent_tags=signals.intent_tags,
        )

    # P0 guard: LLM signals AND deterministic raw-text verification
    try:
        from commerce.purchase_intent import is_explicit_purchase_request as _is_explicit
        from commerce.purchase_intent import is_price_inquiry as _is_price
    except Exception:
        _is_explicit = None  # type: ignore
        _is_price = None  # type: ignore

    if _is_explicit is not None and user_message is not None:
        try:
            _explicit_buy = bool(signals.explicit_purchase_request and _is_explicit(user_message))
        except Exception:
            _explicit_buy = False
    else:
        _explicit_buy = signals.explicit_purchase_request

    _llm_price_signal = (
        signals.requested_price is not None or signals.price_interest >= PRICE_ASK_THRESHOLD
    )
    if _is_price is not None and user_message is not None:
        try:
            _price_ask = bool(_llm_price_signal and _is_price(user_message))
        except Exception:
            _price_ask = False
    else:
        _price_ask = _llm_price_signal

    return context.model_copy(
        update={
            "user_requested_content": signals.explicit_content_request,
            "user_asked_about_price": _price_ask,
            "user_asked_to_buy": _explicit_buy,
            "buying_intent_score": signals.purchase_intent,
            "relationship_score": (
                context.relationship_score
                if context.relationship_score is not None
                else signals.relationship_engagement
            ),
            # Phase C.1-B: Conversational intelligence
            "conversational_phase": _derive_conversational_phase(
                relationship_state=context.relationship_state,
                primary_intent=signals.primary_intent,
                has_commercial_intent=bool(set(signals.intent_tags) & _COMMERCIAL_INTENTS),
                explicit_buy=signals.explicit_purchase_request,
                recent_decline=signals.declined_recent_offer,
            ),
            "negative_intent_count": len(signals.negative_intent_tags),
            "signal_confidence": signals.confidence,
            "fan_asks_question": signals.fan_asks_question,
            "has_commercial_intent": bool(set(signals.intent_tags) & _COMMERCIAL_INTENTS),
            # Phase C.1-C: Rejection classification
            "last_rejection_type": rejection_type.value if rejection_type else None,
        }
    )


def build_conversation_context(
    request: CommercePipelineRequest,
) -> CommerceConversationContext:
    """Translate validated application state into the sealed commerce context.

    Pure translation: eligibility, product identity/state, cooldowns,
    budgets, offer/purchase state, and sales enablement are transported
    verbatim. No derived values, no I/O, no LLM touch points.
    """
    return CommerceConversationContext(
        user_id=request.user_id,
        creator_id=request.creator_id,
        messages=role_content_messages(request.messages),
        eligibility=request.eligibility,
        product_identity=request.product_identity,
        product_state=request.product_state,
        relationship_score=request.relationship_score,
        messages_since_last_offer=request.messages_since_last_offer,
        messages_since_last_purchase=request.messages_since_last_purchase,
        hours_since_last_offer=request.hours_since_last_offer,
        hours_since_last_purchase=request.hours_since_last_purchase,
        recent_offer_count=request.recent_offer_count,
        recent_purchase_count=request.recent_purchase_count,
        recent_sales_attempt_count=request.recent_sales_attempt_count,
        previous_offer_status=request.previous_offer_status,
        has_active_offer=request.has_active_offer,
        has_relevant_product=request.has_relevant_product,
        creator_sales_enabled=request.creator_sales_enabled,
        user_segment_names=request.user_segment_names,
        user_segment_count=request.user_segment_count,
        # Phase C: Relationship context
        relationship_state=request.relationship_state,
        commercial_pressure=request.commercial_pressure,
        tip_eligibility=request.tip_eligibility,
        tip_reason=request.tip_reason,
        handoff_needed=request.handoff_needed,
        handoff_reason=request.handoff_reason,
        # Phase 9: turn-scoped commerce context (None = legacy, fences dormant).
        current_content_interest=request.current_content_interest,
        current_content_disinterest=request.current_content_disinterest,
        user_initiated_commercial=request.user_initiated_commercial,
        continuation_context=request.continuation_context,
        warmth_without_commercial_evidence=request.warmth_without_commercial_evidence,
        authorization_basis=request.authorization_basis,
    )


def _request_engine_kwargs(request: CommercePipelineRequest) -> dict[str, Any]:
    """Application-owned decision-context state (all from the request)."""
    return {
        "relationship_score": request.relationship_score,
        "messages_since_last_offer": request.messages_since_last_offer,
        "messages_since_last_purchase": request.messages_since_last_purchase,
        "hours_since_last_offer": request.hours_since_last_offer,
        "hours_since_last_purchase": request.hours_since_last_purchase,
        "recent_offer_count": request.recent_offer_count,
        "recent_purchase_count": request.recent_purchase_count,
        "recent_sales_attempt_count": request.recent_sales_attempt_count,
        "previous_offer_status": request.previous_offer_status,
        "has_active_offer": request.has_active_offer,
        "has_relevant_product": request.has_relevant_product,
        "creator_sales_enabled": request.creator_sales_enabled,
        # Phase C.1-C: Behavioral feedback context
        "consecutive_rejections": request.consecutive_rejections,
        "total_purchases": request.total_purchases,
        "total_tips_received": request.total_tips_received,
        "hours_since_last_tip": request.hours_since_last_tip,
        "tip_suggestions_sent": request.tip_suggestions_sent,
        "tip_suggestions_ignored": request.tip_suggestions_ignored,
        "aftercare_status": request.aftercare_status,
        "commercial_paused": request.commercial_paused,
        "fan_expressed_appreciation": request.fan_expressed_appreciation,
        "fan_asked_how_to_support": request.fan_asked_how_to_support,
        "repeat_purchase_eligible": request.repeat_purchase_eligible,
        # Phase C.1-E: Creator capabilities
        "creator_capabilities": request.creator_capabilities,
        # Phase 9: turn-scoped commerce context (None = legacy, fences dormant).
        "current_content_interest": request.current_content_interest,
        "current_content_disinterest": request.current_content_disinterest,
        "user_initiated_commercial": request.user_initiated_commercial,
        "continuation_context": request.continuation_context,
        "warmth_without_commercial_evidence": request.warmth_without_commercial_evidence,
        "authorization_basis": request.authorization_basis,
    }


def _staged_result(
    request: CommercePipelineRequest,
    *,
    status: CommercePipelineStatus,
    decision: CommerceDecision | None = None,
    strategy: CommerceStrategy | None = None,
    execution_result: ExecutionResult | None = None,
    response: CommerceResponse | None = None,
    failure_code: str | None = None,
) -> CommercePipelineResult:
    return CommercePipelineResult(
        status=status,
        user_id=request.user_id,
        creator_id=request.creator_id,
        decision=decision,
        strategy=strategy,
        execution_result=execution_result,
        response=response,
        failure_code=failure_code,
    )


async def _sealed_caption_facts(
    request: CommercePipelineRequest,
    execution_result: ExecutionResult | None,
) -> tuple[Any | None, Any | None]:
    """Resolve caption facts from the persisted offer when available.

    P3.2 caption-price consistency: the LLM may phrase sealed facts but may
    not alter them. The persisted ``commerce_offers`` row (execution-time
    verified price/link) is authoritative; the pre-execution
    ``request.product_state`` (mirror cache) is only a fallback for paths
    where no offer row exists (e.g. execution failed but a warm fallback
    reply is still generated — in which case validators forbid price/URL
    claims anyway).
    """
    sealed_state = request.product_state
    sealed_currency = request.currency
    try:
        offer_id = getattr(execution_result, "offer_id", None)
    except Exception:
        offer_id = None
    if execution_result is None or offer_id is None:
        return sealed_state, sealed_currency
    try:
        from commerce.dao import get_offer as _get_offer

        row = await _get_offer(request.creator_id, int(offer_id))
    except Exception:
        logger.debug("caption seal: offer lookup failed", exc_info=True)
        return sealed_state, sealed_currency
    if not row:
        return sealed_state, sealed_currency
    try:
        base_state = request.product_state
        sealed_state = ProductCommerceState(
            price_minor=row.get("price_minor"),
            sales_url=row.get("link"),
            is_accessible=bool(getattr(base_state, "is_accessible", True))
            if base_state is not None
            else True,
            age_verification_required=bool(getattr(base_state, "age_verification_required", False))
            if base_state is not None
            else False,
            age_verified=bool(getattr(base_state, "age_verified", False))
            if base_state is not None
            else False,
        )
        sealed_currency = row.get("currency") or request.currency
    except Exception:
        logger.debug("caption seal: sealed state build failed", exc_info=True)
        return request.product_state, request.currency
    return sealed_state, sealed_currency


async def run_commerce_pipeline(request: CommercePipelineRequest, *, signals: Any | None = None, user_message: str | None = None, decision: CommerceDecision | None = None) -> CommercePipelineResult:
    """Run the existing commerce pipeline over translated application state.

    Always returns a structured ``CommercePipelineResult``; never raises for
    engine/strategy/execution/response failures, never hands out internals.

    Failure semantics (stage-distinct):

    - decision failure   -> DECISION_FAILED, no response generation
    - strategy failure   -> STRATEGY_FAILED, no response generation
    - execution failure  -> EXECUTION_FAILED, execution_result preserved
                             verbatim, response still generated (never claims
                             success — the response adapter enforces it)
    - response failure   -> RESPONSE_FAILED, execution_result preserved,
                             NO re-execution
    - unexpected error   -> FAILED/unexpected_error, nothing exposed
    """
    try:
        base_context = build_conversation_context(request)

        if signals is None:
            signals = await extract_commerce_signals(
                [message.model_dump() for message in base_context.messages]
            )
        context = _apply_signal_flags(base_context, signals, user_message=user_message)

        # Re-evaluate operator handoff with LLM-derived signals.
        # The initial handoff check (in state.py) runs before signals are
        # available. Now that signals exist, re-check with signal data so
        # conditions like negative_sentiment, model_uncertainty, and
        # ambiguous high intent can actually fire.
        # Skip when signals are low-information (extraction failed) —
        # model_uncertainty=1.0 is the default fallback, not real uncertainty.
        # High uncertainty only triggers handoff when accompanied by evidence.
        _is_low_info = (
            signals.confidence == 0.0
            and signals.model_uncertainty == 1.0
            and not signals.evidence
            and signals.primary_intent == "uncertain"
        )
        _high_uncertainty_without_evidence = (
            signals.model_uncertainty >= 0.80 and not signals.evidence
        )
        if (
            not context.handoff_needed
            and signals is not None
            and not _is_low_info
            and not _high_uncertainty_without_evidence
        ):
            # Derive handoff-relevant signal counts
            has_complaint_signal = "complaint" in signals.negative_intent_tags
            has_custom_request_signal = "custom_request" in signals.intent_tags
            provider_uncertain_signal = signals.model_uncertainty >= 0.80

            # H3 bot-ask repeat (turn-local window, fail-open): a second
            # bot-accusation/human-demand turn — current tags carry
            # operator_request AND a prior fan turn matches ground-truth
            # bot-ask wording — is treated as a custom request for human
            # attention through the EXISTING has_custom_request input
            # (a repeat demand for a human IS a custom request; zero
            # signature/rule change). First asks get guidance only.
            # Accusation + already-firing complaint needs nothing here.
            _botask_repeated = False
            try:
                from commerce.conversation_strategy import (
                    had_prior_bot_accusation as _had_prior_botask,
                )

                if "operator_request" in signals.intent_tags:
                    _botask_repeated = bool(_had_prior_botask(base_context.messages))
            except Exception:
                _botask_repeated = False

            # H1 complaint triage (turn-local, text-only, fail-open):
            # a positively-identified experience-only complaint (explicit
            # experience wording, no technical payment signal, no payment
            # claim) does not auto-handoff; RECOVER/sincerity handles
            # realization. Unknown/unparseable text stays fail-closed
            # (legacy handoff preserved). Thresholds untouched.
            _triage_experience_only = False
            try:
                from commerce.complaint_triage_evidence import (
                    extract_complaint_triage_evidence as _extract_triage,
                )

                if isinstance(user_message, str) and user_message.strip():
                    _triage_experience_only = bool(
                        _extract_triage(user_message).is_experience_only()
                    )
            except Exception:
                _triage_experience_only = False

            signal_handoff, signal_handoff_reason = check_operator_handoff(
                relationship_state=context.relationship_state,
                commercial_pressure=context.commercial_pressure,
                intent_category=signals.primary_intent,
                buying_intent_score=signals.purchase_intent,
                negative_sentiment=signals.negative_sentiment,
                model_uncertainty=signals.model_uncertainty,
                recent_fulfillment_failures=0,  # Not available from signals
                has_complaint=has_complaint_signal,
                has_custom_request=(
                    has_custom_request_signal
                    or (_botask_repeated and "operator_request" in signals.intent_tags)
                ),
                provider_uncertain=provider_uncertain_signal,
                creator_config_issue=False,  # Not detectable from signals
                complaint_is_experience_only=_triage_experience_only,
            )
            if signal_handoff:
                context = context.model_copy(update={
                    "handoff_needed": True,
                    "handoff_reason": signal_handoff_reason.value if signal_handoff_reason else None,
                })

        # Persist rejection classification as offer decline when appropriate.
        # Only HARD and PRICE_OBJECTION trigger a state transition.
        # SOFT/UNCERTAIN are informational only — no state change.
        if signals.negative_intent_tags:
            rejection_type = classify_rejection(
                negative_intent_tags=signals.negative_intent_tags,
                negative_sentiment=signals.negative_sentiment,
                price_interest=signals.price_interest,
                intent_tags=signals.intent_tags,
            )
            if rejection_type in (RejectionType.HARD, RejectionType.PRICE_OBJECTION):
                declined = await mark_offer_declined(
                    creator_id=request.creator_id,
                    user_id=request.user_id,
                    reason=f"rejection_{rejection_type.value}",
                )
                if declined:
                    logger.info(
                        "offer declined via rejection classification "
                        "(offer_id=%s, user_id=%s, rejection_type=%s)",
                        declined.get("id"),
                        request.user_id,
                        rejection_type.value,
                    )

        # P1.2 canonical decision: evaluate once per pipeline run and reuse
        # for orchestration. When *decision* is already supplied (canonical
        # per-message), reuse it without recomputing; otherwise compute.
        _canonical_decision: CommerceDecision | None = decision
        if _canonical_decision is None:
            try:
                # P0: thread raw user_message for deterministic gating
                _engine_kwargs = dict(_request_engine_kwargs(request))
                if user_message is not None:
                    _engine_kwargs["user_message"] = user_message
                _canonical_decision = decide_from_signals(
                    signals,
                    user_id=request.user_id,
                    creator_id=request.creator_id,
                    eligibility=request.eligibility,
                    policy=request.policy or DEFAULT_COMMERCE_POLICY,
                    **_engine_kwargs,
                )
                # G9 evidence-ledger emission only (log; fail-open; never called
                # from new places; live worker path stays quarantined).
                try:
                    _g9_action = getattr(getattr(_canonical_decision, "action", None), "value", getattr(_canonical_decision, "action", None))
                    _g9_reason = getattr(getattr(_canonical_decision, "reason_code", None), "value", getattr(_canonical_decision, "reason_code", None))
                    logger.info(
                        "G9 commerce_decision user=%s creator=%s action=%s reason=%s allowed=%s confidence=%s",
                        request.user_id, request.creator_id, _g9_action, _g9_reason,
                        bool(getattr(_canonical_decision, "allowed", False)),
                        getattr(_canonical_decision, "confidence", None),
                    )
                except Exception:
                    pass
            except Exception:
                logger.warning(
                    "commerce pipeline decision failure (created_by=%s)",
                    PIPELINE_CREATED_BY,
                    exc_info=True,
                )
                return _staged_result(
                    request,
                    status=CommercePipelineStatus.DECISION_FAILED,
                    failure_code="decision_failure",
                )

            try:
                build_strategy(_canonical_decision, context)
            except Exception:
                logger.warning(
                    "commerce pipeline strategy failure (created_by=%s)",
                    PIPELINE_CREATED_BY,
                    exc_info=True,
                )
                return _staged_result(
                    request,
                    status=CommercePipelineStatus.STRATEGY_FAILED,
                    failure_code="strategy_failure",
                )
        else:
            # Canonical decision supplied — still validate strategy can be built
            # for this context, but do not recompute decision.
            try:
                build_strategy(_canonical_decision, context)
            except Exception:
                logger.warning(
                    "commerce pipeline strategy failure (created_by=%s)",
                    PIPELINE_CREATED_BY,
                    exc_info=True,
                )
                return _staged_result(
                    request,
                    status=CommercePipelineStatus.STRATEGY_FAILED,
                    failure_code="strategy_failure",
                )

        # P1.2: single call site, pass canonical decision with fallback for test spies (no inspect import)
        _orch_kwargs: dict = {"policy": request.policy, "created_by": PIPELINE_CREATED_BY, "decision": _canonical_decision}
        orchestration: CommerceOrchestrationResult | None = None
        for _attempt in range(2):
            try:
                orchestration = await orchestrate_commerce(context, **_orch_kwargs)
                break
            except TypeError as _e:
                if "decision" in str(_e) and "decision" in _orch_kwargs:
                    _orch_kwargs.pop("decision", None)
                    continue
                raise
        assert orchestration is not None

        if orchestration.failure_code == "decision_failure":
            return _staged_result(
                request,
                status=CommercePipelineStatus.DECISION_FAILED,
                failure_code="decision_failure",
            )
        if orchestration.failure_code == "strategy_failure":
            return _staged_result(
                request,
                status=CommercePipelineStatus.STRATEGY_FAILED,
                failure_code="strategy_failure",
            )
        if orchestration.failure_code == "unexpected_error":
            return _staged_result(
                request,
                status=CommercePipelineStatus.FAILED,
                failure_code="unexpected_error",
            )

        execution_failed = orchestration.failure_code == "execution_failure"

        # Phase 78B single-generation gate: only generate commerce language when
        # the deterministic decision is OFFER_PPV and the offer is EXECUTED/
        # ALREADY_EXECUTED. All other paths (NO_OFFER, SOFT_OFFER, FOLLOW_UP,
        # etc.) return a deterministic non-generative response and avoid a
        # second LLM call — makes normal messages exactly 1 generation.
        from commerce.execution import ExecutionStatus
        from commerce.models import CommerceAction

        _needs_commerce_llm = False
        try:
            _action = getattr(orchestration.decision, "action", None) if orchestration.decision else None
            _is_ppv = _action is CommerceAction.OFFER_PPV or str(_action) == "offer_ppv" or "OFFER_PPV" in str(_action)
            # Generate commerce language for any OFFER_PPV decision (even when execution failed)
            # so the fan receives a warm no-offer fallback (VerifiedFacts: No active offer).
            # The 78B gate avoids second LLM for normal non-PPV turns; PPV turns always generate.
            _needs_commerce_llm = bool(_is_ppv)
        except Exception:
            _needs_commerce_llm = False

        if not _needs_commerce_llm:
            # Deterministic no-LLM response — selection will FALLBACK to OneCall reply
            # This is the normal non-PPV path (NO_OFFER, SOFT_OFFER, etc.) — not an error.
            response = CommerceResponse(
                status=CommerceResponseStatus.FAILED,
                failure_code="not_ppv_no_generation",
            )
            # For non-PPV, pipeline is COMPLETED (OneCall will be used).  Do not treat as RESPONSE_FAILED.
            # Keep execution_result for observability but do not expire.
            return _staged_result(
                request,
                status=CommercePipelineStatus.COMPLETED,
                decision=orchestration.decision,
                strategy=orchestration.strategy,
                execution_result=orchestration.execution_result,
                response=response,
                failure_code=None,
            )
        else:
            _sealed_state, _sealed_currency = await _sealed_caption_facts(
                request, orchestration.execution_result
            )
            response = await generate_commerce_response(
                CommerceResponseInput(
                    user_id=request.user_id,
                    creator_id=request.creator_id,
                    conversation=context.messages,
                    decision=orchestration.decision,
                    strategy=orchestration.strategy,
                    execution_result=orchestration.execution_result,
                    persona=request.persona,
                    product_identity=request.product_identity,
                    product_state=_sealed_state,
                    currency=_sealed_currency,
                )
            )

        if response.status is not CommerceResponseStatus.GENERATED:
            if execution_failed:
                return _staged_result(
                    request,
                    status=CommercePipelineStatus.EXECUTION_FAILED,
                    decision=orchestration.decision,
                    strategy=orchestration.strategy,
                    execution_result=orchestration.execution_result,
                    response=response,
                    failure_code="execution_failure",
                )
            # Phase 93B ORANGE-1: EXECUTED but 2nd generation failed → expire only pending orphan
            # Never expire ALREADY_EXECUTED, clicked/purchased/declined.  Best-effort, failure-isolated.
            try:
                _exec = orchestration.execution_result
                if _exec is not None and _exec.status == ExecutionStatus.EXECUTED and _exec.offer_id is not None:
                    from commerce.dao import expire_pending_offer_if_still_pending
                    await expire_pending_offer_if_still_pending(request.creator_id, _exec.offer_id)
            except Exception:
                logger.debug("orphan pending expiry failed", exc_info=True)
            return _staged_result(
                request,
                status=CommercePipelineStatus.RESPONSE_FAILED,
                decision=orchestration.decision,
                strategy=orchestration.strategy,
                execution_result=orchestration.execution_result,
                response=response,
                failure_code=response.failure_code,
            )

        return _staged_result(
            request,
            status=(
                CommercePipelineStatus.EXECUTION_FAILED
                if execution_failed
                else CommercePipelineStatus.COMPLETED
            ),
            decision=orchestration.decision,
            strategy=orchestration.strategy,
            execution_result=orchestration.execution_result,
            response=response,
            failure_code=("execution_failure" if execution_failed else orchestration.failure_code),
        )
    except Exception:
        logger.warning(
            "commerce pipeline unexpected failure (created_by=%s)",
            PIPELINE_CREATED_BY,
            exc_info=True,
        )
        return _staged_result(
            request,
            status=CommercePipelineStatus.FAILED,
            failure_code="unexpected_error",
        )
