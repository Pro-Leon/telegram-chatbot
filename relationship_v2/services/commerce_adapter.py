"""Commerce adapter (Phase 8). V2 is a client; commerce confirms.

Caller: FUTURE turn orchestrator (Phase 9+) supplies ports wired to the
PRESERVED commerce surface (eligibility / opportunity_engine / purchase
state). This module never imports commerce, providers, or commerce tables;
all commerce access flows through caller-supplied ports. Unit tests use fakes.

Flows (COMMERCE_CONTRACT.md + 06_COMMERCE_BOUNDARY.md):
- READ: ports return authoritative snapshots -> normalized CommerceContext.
- REQUEST: explicit CommerceActionRequest with idempotency key; V2 expresses
  intent, never executes.
- CONFIRMATION: raw result mapped to CommerceActionResult; only CONFIRMED
  authorizes commerce claims in generated text.
- Errors map to strategy actions (retry/replan/handoff/suppress), never to
  invented success. Timeouts are unknown, never confirmed.
- Purchase feedback: reconciled purchase events -> PURCHASE episode inputs +
  buyer context; unreconciled claims rejected.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from relationship_v2.domain.commerce import CommerceContext, PurchaseFeedback, StrategyAction
from relationship_v2.domain.commerce_ref import (
    CommerceActionRequest,
    CommerceActionResult,
    CommerceActionResultCode,
)

logger = logging.getLogger("sunny.v2.commerce_adapter")

EligibilityPort = Callable[[int, int], Awaitable[dict]]
OpportunityPort = Callable[[int, int], Awaitable[dict]]
PurchaseStatePort = Callable[[int, int], Awaitable[dict]]

COMMERCE_SNAPSHOT_TTL_SECONDS = 120


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _scope_ok(creator_id: int, user_id: int) -> None:
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")


async def read_context(
    creator_id: int,
    user_id: int,
    eligibility: EligibilityPort,
    opportunity: OpportunityPort,
    purchase_state: PurchaseStatePort,
    now: datetime | None = None,
) -> CommerceContext:
    """READ flow. Ports raise on timeout/failure -> caller sees unknown, never confirmed."""
    _scope_ok(creator_id, user_id)
    if eligibility is None or opportunity is None or purchase_state is None:
        raise ValueError("commerce ports required (no silent fallback)")
    ts = now or _utcnow()
    elig = await eligibility(creator_id, user_id)
    opp = await opportunity(creator_id, user_id)
    purch = await purchase_state(creator_id, user_id)
    constraints: list[str] = []
    if not elig.get("eligible", False):
        constraints.append(f"ineligible:{elig.get('reason', 'unknown')}")
    return CommerceContext(
        creator_id=creator_id,
        user_id=user_id,
        purchase_status=str(purch.get("status", "unknown")),
        purchase_count=int(purch.get("count", 0)),
        last_purchase_at=purch.get("last_at"),
        active_offer=bool(opp.get("active_offer", False)),
        available_opportunities=list(opp.get("opportunities", [])),
        owned_content_refs=list(purch.get("owned_refs", [])),
        aftercare_state=str(purch.get("aftercare", "none")),
        cooldowns=dict(opp.get("cooldowns", {})),
        deterministic_constraints=constraints,
        as_of=ts,
    )


def is_snapshot_stale(context: CommerceContext, now: datetime | None = None) -> bool:
    """Stale commerce snapshots block PRESENT_OFFER (FAILURE_MODES.md)."""
    ts = now or _utcnow()
    return (ts - context.as_of).total_seconds() > COMMERCE_SNAPSHOT_TTL_SECONDS


def build_action_request(
    creator_id: int,
    user_id: int,
    action: str,
    idempotency_key: str,
    commerce_request_id: str | None = None,
) -> CommerceActionRequest:
    """REQUEST flow: explicit structure crossing the boundary (never prose)."""
    _scope_ok(creator_id, user_id)
    if not action or not idempotency_key:
        raise ValueError("action/idempotency_key required")
    return CommerceActionRequest(
        commerce_request_id=commerce_request_id or f"v2-{idempotency_key}",
        creator_id=creator_id,
        user_id=user_id,
        action=action,
        idempotency_key=idempotency_key,
        owner="relationship_v2",
    )


def map_confirmation(
    request: CommerceActionRequest, code: str, confirmed_at: datetime | None = None
) -> CommerceActionResult:
    """Map a raw commerce outcome code. Unknown codes -> verification_failed."""
    try:
        parsed = CommerceActionResultCode(code)
    except ValueError:
        parsed = CommerceActionResultCode.VERIFICATION_FAILED
    if parsed == CommerceActionResultCode.CONFIRMED and confirmed_at is None:
        raise ValueError("confirmed results require confirmed_at")
    return CommerceActionResult(
        commerce_request_id=request.commerce_request_id,
        code=parsed,
        confirmed_at=confirmed_at,
    )


def map_error_to_strategy(code: CommerceActionResultCode) -> StrategyAction:
    """Error -> strategy (COMMERCE_CONTRACT.md). Never invented success."""
    mapping = {
        CommerceActionResultCode.CONFIRMED: StrategyAction.REPLAN,
        CommerceActionResultCode.DENIED: StrategyAction.HANDOFF,
        CommerceActionResultCode.INELIGIBLE: StrategyAction.SUPPRESS,
        CommerceActionResultCode.UNAVAILABLE: StrategyAction.REPLAN,
        CommerceActionResultCode.CONFLICT: StrategyAction.SUPPRESS,
        CommerceActionResultCode.PROVIDER_ERROR: StrategyAction.RETRY,
        CommerceActionResultCode.PERSISTENCE_FAILED: StrategyAction.RETRY,
        CommerceActionResultCode.VERIFICATION_FAILED: StrategyAction.HANDOFF,
    }
    return mapping[code]


def build_purchase_feedback(event: dict) -> PurchaseFeedback:
    """Purchase feedback (06 §Purchase feedback). Reconciliation-gated.

    Commerce records -> V2 receives event -> PURCHASE episode inputs.
    Unreconciled claims rejected: confirmation only after reconciliation.
    """
    for field in ("creator_id", "user_id", "summary", "provenance"):
        if not event.get(field):
            raise ValueError(f"purchase event missing {field} (fail-closed)")
    if not event.get("reconciled", False):
        raise ValueError("unreconciled purchase claim rejected")
    return PurchaseFeedback(
        creator_id=int(event["creator_id"]),
        user_id=int(event["user_id"]),
        summary=str(event["summary"])[:280],
        reconciled=True,
        provenance=str(event["provenance"]),
    )


def confirmation_payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def record_requested(
    request: CommerceActionRequest,
    relationship_id: UUID | None = None,
    conversation_id: UUID | None = None,
    generation_id: str | None = None,
) -> dict:
    """Emit commerce.action.requested. Consumer FUTURE: audit, queue."""
    from relationship_v2.persistence.repository import create_relationship_event

    event_id = f"commerce-req-{request.commerce_request_id}"
    return await create_relationship_event(
        event_id=event_id,
        event_type="commerce.action.requested",
        creator_id=request.creator_id,
        user_id=request.user_id,
        idempotency_key=request.idempotency_key,
        producer="relationship_v2.services.commerce_adapter",
        payload={"action": request.action, "owner": request.owner},
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )


async def record_confirmed(
    result: CommerceActionResult,
    request: CommerceActionRequest,
    relationship_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> dict:
    """Emit commerce.action.confirmed + cache the confirmation ref."""
    from relationship_v2.persistence.repository import (
        create_commerce_ref,
        create_relationship_event,
    )

    if result.code != CommerceActionResultCode.CONFIRMED:
        raise ValueError("only CONFIRMED results are cached as refs")
    event_id = f"commerce-conf-{request.commerce_request_id}"
    event = await create_relationship_event(
        event_id=event_id,
        event_type="commerce.action.confirmed",
        creator_id=request.creator_id,
        user_id=request.user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.commerce_adapter",
        payload={"code": result.code.value},
        relationship_id=relationship_id,
        conversation_id=conversation_id,
    )
    if relationship_id is not None:
        await create_commerce_ref(
            creator_id=request.creator_id,
            user_id=request.user_id,
            relationship_id=relationship_id,
            commerce_request_id=request.commerce_request_id,
            payload_hash=confirmation_payload_hash(event_id),
            confirmed_at=result.confirmed_at or _utcnow(),
        )
    return event
