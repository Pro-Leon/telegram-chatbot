"""Response engine (Phase 7).

Caller: FUTURE turn orchestrator per generation (Phase 9 wires enqueue).
Owner of: plan persistence + validation verdicts + outbound planned/validated
  transitions. Producer: message.generated (consumers FUTURE: queue Phase 9,
  observability). Phase 1 realtime events untouched (PRESERVED contract).

Pipeline covered: interpret -> plan -> generate (port) -> validate.
Delivery/enqueue mechanics (locks, DLQ, dedupe lease) are Phase 9.
LLM is advisory: GenerationPort is caller-supplied; Phase 7 never imports
provider clients or the DISABLED V1 one-call pipeline.

Hard rules enforced:
- Commerce facts only from confirmations passed in (never generated text).
- Generated text cannot mutate state; only validated outcomes applied by caller.
- Invalid output never auto-sends: needs_review or suppressed with stable code.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from relationship_v2.domain.response import (
    GeneratedResponse,
    OutboundState,
    ResponseIntent,
    ResponsePlan,
    ValidationOutcome,
    ValidationVerdict,
)

logger = logging.getLogger("sunny.v2.response_engine")

GenerationPort = Callable[[ResponsePlan], Awaitable[GeneratedResponse]]

_PRICE_RE = re.compile(r"\$\s?\d+(?:\.\d{1,2})?")
_PURCHASE_CLAIM_RE = re.compile(
    r"\b(purchased|bought|you own|unlocked|payment (?:received|confirmed))\b",
    re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def interpret(text: str) -> ResponseIntent:
    """Deterministic intent baseline. Model-assisted interpretation FUTURE."""
    lowered = (text or "").lower()
    if any(w in lowered for w in ("don't talk", "stop", "leave me alone", "opt out")):
        return ResponseIntent.BOUNDARY_RESPECT
    if any(w in lowered for w in ("price", "how much", "buy", "ppv", "content", "pics")):
        return ResponseIntent.COMMERCE_TRANSITION
    if "?" in text:
        return ResponseIntent.QUESTION
    if any(w in lowered for w in ("miss you", "missed", "long time", "you're back", "back")):
        return ResponseIntent.REACTIVATE
    if any(w in lowered for w in ("thank", "loved it", "amazing")):
        return ResponseIntent.AFTERCARE
    return ResponseIntent.ACKNOWLEDGE


def build_plan(
    generation_id: str,
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    intent: ResponseIntent,
    key_points: list[str],
    commerce_refs: list[str],
    forbidden_claims: list[str],
    provenance: str,
    now: datetime | None = None,
) -> ResponsePlan:
    """Pure plan construction. Commerce refs must come from confirmations."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if not generation_id or not provenance:
        raise ValueError("generation_id/provenance required")
    return ResponsePlan(
        generation_id=generation_id,
        creator_id=creator_id,
        user_id=user_id,
        relationship_id=relationship_id,
        intent=intent,
        key_points=[k[:280] for k in key_points],
        commerce_refs=list(commerce_refs),
        forbidden_claims=list(forbidden_claims),
        provenance=provenance,
        created_at=now or _utcnow(),
    )


def plan_hash(plan: ResponsePlan) -> str:
    payload = plan.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def generate(plan: ResponsePlan, port: GenerationPort) -> GeneratedResponse:
    """Invoke the caller-supplied generation port. No provider imports here."""
    if port is None:
        raise ValueError("generation port required (no silent fallback text)")
    return await port(plan)


def validate_response(
    generated: GeneratedResponse,
    plan: ResponsePlan,
    confirmed_commerce_text: str = "",
    boundary_topics: list[str] | None = None,
    known_fact_values: list[str] | None = None,
) -> ValidationVerdict:
    """Deterministic rails. Returns verdict; caller routes (send/review/drop)."""
    flags: list[str] = []
    text = generated.text or ""
    # Commerce rails: prices/purchases only when quoting a confirmation.
    price_claims = _PRICE_RE.findall(text)
    if price_claims and not confirmed_commerce_text:
        flags.append("invented_price")
    elif price_claims and not any(p.strip("$ ") in confirmed_commerce_text for p in price_claims):
        flags.append("incorrect_price")
    if _PURCHASE_CLAIM_RE.search(text) and not confirmed_commerce_text:
        flags.append("invented_purchase")
    # Boundary rails: never pursue a bounded topic.
    for topic in boundary_topics or []:
        if topic and topic.lower() in text.lower():
            flags.append("boundary_violation")
            break
    # Plan rails: forbidden claims listed on the plan must not appear.
    for claim in plan.forbidden_claims:
        if claim and claim.lower() in text.lower():
            flags.append("forbidden_claim")
            break
    # Memory rails: full contradiction scoring against known current facts is
    # Phase 7.1 (needs fact-key metadata the plan does not carry yet).
    if any(f in ("invented_price", "invented_purchase", "boundary_violation") for f in flags):
        return ValidationVerdict(
            outcome=ValidationOutcome.SUPPRESSED,
            flags=flags,
            reason="hard_rail_violation",
        )
    if flags:
        return ValidationVerdict(
            outcome=ValidationOutcome.NEEDS_REVIEW, flags=flags, reason="soft_rail_hit"
        )
    return ValidationVerdict(outcome=ValidationOutcome.VALID, reason="rails_pass")


def next_outbound_state(current: OutboundState, signal: str) -> OutboundState | None:
    """Outbound lifecycle guard. None = invalid (planned->sent blocked)."""
    allowed: dict[OutboundState, dict[str, OutboundState]] = {
        OutboundState.PLANNED: {
            "validate": OutboundState.VALIDATED,
            "suppress": OutboundState.SUPPRESSED,
        },
        OutboundState.VALIDATED: {
            "enqueue": OutboundState.ENQUEUED,
            "suppress": OutboundState.SUPPRESSED,
        },
        OutboundState.ENQUEUED: {"sent": OutboundState.SENT, "fail": OutboundState.FAILED},
        OutboundState.FAILED: {"enqueue": OutboundState.ENQUEUED},
        OutboundState.SENT: {},
        OutboundState.SUPPRESSED: {},
    }
    return allowed.get(current, {}).get(signal)


async def record_generation(
    plan: ResponsePlan,
    generated: GeneratedResponse | None,
    verdict: ValidationVerdict,
    conversation_id: UUID | None = None,
) -> dict:
    """Emit message.generated durable event. Consumer FUTURE: queue, audit."""
    from relationship_v2.persistence.repository import create_relationship_event

    event_id = f"gen-{plan.generation_id}-{verdict.outcome.value}"
    return await create_relationship_event(
        event_id=event_id,
        event_type="message.generated",
        creator_id=plan.creator_id,
        user_id=plan.user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.response_engine",
        payload={
            "plan_hash": plan_hash(plan),
            "intent": plan.intent.value,
            "outcome": verdict.outcome.value,
            "flags": verdict.flags,
            "has_text": generated is not None,
        },
        relationship_id=plan.relationship_id,
        conversation_id=conversation_id,
        generation_id=plan.generation_id,
    )
