"""Phase 5.4 Chk 6C — read-only commerce application-state resolution.

Converts existing DB/application state into the Chk 5 pipeline boundary
(:class:`commerce.pipeline.CommercePipelineRequest`) without wiring anything:

- fan state        -> db.postgres.get_user / is_user_auto_reply_excluded
- creator context  -> db.dropfans.get_dropfans_integration (sole active provider)
- product          -> db.fangate.get_fangate_product (provider-neutral product mirror table)
- offers/purchases -> commerce.dao.list_offers_for_user /
                       find_pending_offer_for_product / has_purchased_product
- eligibility      -> commerce.eligibility.evaluate_ppv_eligibility (never duplicated)

Invariants:

READ-ONLY: only SELECT-style read functions are ever called; this module has
no write path (no INSERT/UPDATE/DELETE, no commerce-execution entry point, no
persistence-state helpers).

DETERMINISTIC: no clock reads (no current-time source), no randomness, no
network, no AI. Every output is a function of the validated request and the DB
snapshot only; identical inputs + identical reads produce identical
resolutions.

NO PRODUCT SELECTION: product state exists only when the caller explicitly
supplies ``product_id`` and it resolves in the creator's local mirror. The
resolver never picks a product from a list and never defaults to one.

EXPLICIT CREATOR RESOLUTION: a caller-supplied ``creator_id`` is verified
against the creators table; without one, the fan's most recent commerce offer
row is the relationship evidence (ORDER BY created_at DESC, id DESC, the DAO's
documented order). When neither source can establish the relationship the
resolution is CREATOR_CONTEXT_UNAVAILABLE — the resolver never guesses a
creator.

CLOCK-WINDOWED FIELDS: hours_since_*, recent_*_count are computed from
existing commerce_offers timestamps (created_at, purchased_at). The resolver
reads the clock once via get_timing_context() and passes the values into the
pipeline request untouched.

FAILURE ISOLATION: any DB failure yields RESOLUTION_FAILED; a sellable
CommercePipelineRequest is never manufactured from partial state.
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commerce import dao as commerce_dao
from commerce.context import (
    PRODUCT_TITLE_MAX,
    ProductCommerceState,
    ProductIdentity,
    StrictPositiveInt,
)
from commerce.eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)
from commerce.pipeline import (
    PIPELINE_CURRENCY_MAX,
    PIPELINE_MAX_MESSAGES,
    PIPELINE_PERSONA_MAX,
    CommercePipelineRequest,
)
from db import fangate as db_fangate
from db import postgres as db_postgres

logger = logging.getLogger("commerce.state")

# Integration statuses: only 'active' makes creator sales enabled; 'error' and
# 'disconnected' are explicit non-ready states (see db/fangate.py transitions).
_INTEGRATION_ACTIVE = "active"


class CommerceResolutionStatus(str, Enum):
    """Closed outcome set of the state-resolution layer."""

    READY = "ready"
    CREATOR_CONTEXT_UNAVAILABLE = "creator_context_unavailable"
    STATE_UNAVAILABLE = "state_unavailable"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    RESOLUTION_FAILED = "resolution_failed"


class CommerceStateRequest(BaseModel):
    """Strictly-typed resolution request. Extras are rejected.

    Only ``user_id`` is required. ``creator_id`` may be supplied to assert the
    fan/creator relationship (verified against the creators table); without it
    the relationship must exist as offer history. ``product_id`` is the ONLY
    product input the resolver accepts — it never selects a product itself.
    ``messages`` follow the build_context shape (system/user/assistant) and
    are transported verbatim into the pipeline request.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: StrictPositiveInt
    creator_id: StrictPositiveInt | None = None
    product_id: StrictPositiveInt | None = None

    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=PIPELINE_MAX_MESSAGES)
    currency: str | None = Field(default=None, min_length=3, max_length=PIPELINE_CURRENCY_MAX)
    persona: str | None = Field(default=None, max_length=PIPELINE_PERSONA_MAX)

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


class CommerceStateResolution(BaseModel):
    """Structured resolution outcome.

    ``status`` is drawn from the closed
    :class:`CommerceResolutionStatus` set. ``request`` is present only when
    the status is READY and is a fully populated
    :class:`commerce.pipeline.CommercePipelineRequest` — never built from
    partial state.
    """

    model_config = ConfigDict(extra="forbid")

    status: CommerceResolutionStatus
    request: CommercePipelineRequest | None = None


async def _resolve_creator_relationship(
    user_id: int,
    explicit_creator_id: int | None,
) -> tuple[int | None, str | None]:
    """Return (creator_id, previous_offer_status) or (None, None).

    Explicit creator ids must exist in the creators table. Otherwise the fan's
    most recent commerce offer row (DAO order: created_at DESC, id DESC) is
    the only relationship evidence. Never invents a creator.
    """
    if explicit_creator_id is not None:
        creator = await db_fangate.get_creator(explicit_creator_id)
        if creator is None:
            return None, None
        history = await commerce_dao.list_offers_for_user(
            user_id, creator_id=explicit_creator_id, limit=1
        )
        previous_offer_status = history[0]["state"] if history else None
        return explicit_creator_id, previous_offer_status

    history = await commerce_dao.list_offers_for_user(user_id, limit=1)
    if not history:
        return None, None
    creator_id = history[0]["creator_id"]
    creator = await db_fangate.get_creator(creator_id)
    if creator is None:
        return None, None
    return creator_id, history[0]["state"]


async def resolve_commerce_state(request: CommerceStateRequest) -> CommerceStateResolution:
    """Resolve authoritative commerce application state into a pipeline request.

    Always returns a :class:`CommerceStateResolution`; never raises for DB or
    resolution failures. Caller contract violations (malformed request) raise
    at the pydantic boundary before any I/O happens.
    """
    try:
        user = await db_postgres.get_user(request.user_id)
        if user is None:
            return CommerceStateResolution(status=CommerceResolutionStatus.STATE_UNAVAILABLE)
        opted_out = await db_postgres.is_user_auto_reply_excluded(request.user_id)

        creator_id, previous_offer_status = await _resolve_creator_relationship(
            request.user_id, request.creator_id
        )
        if creator_id is None:
            return CommerceStateResolution(
                status=CommerceResolutionStatus.CREATOR_CONTEXT_UNAVAILABLE
            )

        # Check Dropfans integration (sole active provider)
        from db import dropfans as db_dropfans
        try:
            df_integration = await db_dropfans.get_dropfans_integration(creator_id)
        except Exception:
            logger.warning(
                "commerce.state dropfans integration lookup failed",
                extra={"creator_id": creator_id},
                exc_info=True,
            )
            df_integration = None
        creator_sales_enabled = (
            df_integration is not None and df_integration.get("status") == _INTEGRATION_ACTIVE
        )

        product = None
        if request.product_id is not None:
            product = await db_fangate.get_fangate_product(creator_id, request.product_id)
            if product is None:
                return CommerceStateResolution(status=CommerceResolutionStatus.PRODUCT_UNAVAILABLE)

        active_offer = False
        already_purchased = False
        if product is not None:
            active_offer = (
                await commerce_dao.find_pending_offer_for_product(
                    creator_id, request.user_id, request.product_id
                )
            ) is not None
            already_purchased = await commerce_dao.has_purchased_product(
                creator_id, request.user_id, request.product_id
            )

        decision = evaluate_ppv_eligibility(
            UserEligibilityState(
                is_blocked=bool(user.get("is_blocked", False)),
                do_not_auto_reply=opted_out,
            ),
            (
                ProductEligibilityState(
                    is_accessible=bool(product.get("is_accessible", False)),
                    sales_url=product.get("sales_url"),
                    price_minor=product.get("price_minor"),
                )
                if product is not None
                else ProductEligibilityState()
            ),
            OfferContext(
                creator_ready=creator_sales_enabled,
                has_active_offer=active_offer,
                already_purchased=already_purchased,
                enforce_age_verification=(
                    bool(product.get("is_verif_age", False)) if product is not None else False
                ),
                age_verified=False,
            ),
        )

        identity = None
        commerce_state = None
        if product is not None:
            identity = ProductIdentity(
                product_id=int(product["id"]),
                title=str(product.get("title") or "")[:PRODUCT_TITLE_MAX],
                available=bool(product.get("is_accessible", False)),
            )
            commerce_state = ProductCommerceState(
                price_minor=product.get("price_minor"),
                sales_url=product.get("sales_url"),
                is_accessible=bool(product.get("is_accessible", False)),
                age_verification_required=bool(product.get("is_verif_age", False)),
            )

        persona = request.persona
        if persona is None:
            # M4 D5: creator-scoped lookup only — the legacy global path could
            # return another creator's persona. creator_id is resolved and
            # fail-closed above, so it is always authoritative here.
            stored_persona = await db_postgres.get_user_persona(request.user_id, creator_id=creator_id)
            if stored_persona:
                persona = stored_persona[:PIPELINE_PERSONA_MAX]

        # Segment context (informational only — failure is non-critical)
        segment_names: list[str] = []
        segment_count = 0
        try:
            from db import segments as sdb
            from segments.evaluator import check_user_in_segment
            from segments.models import RuleGroup

            segments = await sdb.list_segments(creator_id, enabled_only=True)
            for seg in segments:
                rules_data = seg.get("rules")
                if not rules_data:
                    continue
                try:
                    rule = RuleGroup.model_validate(rules_data)
                    if await check_user_in_segment(creator_id, rule, request.user_id):
                        segment_names.append(seg["name"])
                except Exception:  # noqa: BLE001,S112 — segment eval is non-critical
                    continue
            segment_count = len(segment_names)
        except Exception:  # noqa: BLE001,S110
            logger.warning(
                "commerce state: segment evaluation failed for creator=%s user=%s",
                creator_id,
                request.user_id,
                exc_info=True,
            )

        # Timing context for cooldown enforcement — P0 fail-closed: DB failure must not yield fake zero cooldown.
        try:
            timing = await commerce_dao.get_timing_context(creator_id, request.user_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "commerce state: timing context failed for creator=%s user=%s — fail-closed RESOLUTION_FAILED",
                creator_id,
                request.user_id,
                exc_info=True,
            )
            return CommerceStateResolution(status=CommerceResolutionStatus.RESOLUTION_FAILED)

        # Behavioral feedback context (C.1-C) — P0 fail-closed
        try:
            behavioral = await commerce_dao.get_behavioral_feedback_context(
                creator_id, request.user_id
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "commerce state: behavioral feedback failed for creator=%s user=%s — fail-closed RESOLUTION_FAILED",
                creator_id,
                request.user_id,
                exc_info=True,
            )
            return CommerceStateResolution(status=CommerceResolutionStatus.RESOLUTION_FAILED)

        pipeline_request = CommercePipelineRequest(
            user_id=request.user_id,
            creator_id=creator_id,
            messages=request.messages,
            eligibility=decision,
            product_identity=identity,
            product_state=commerce_state,
            currency=request.currency,
            persona=persona,
            previous_offer_status=previous_offer_status,
            has_active_offer=active_offer,
            has_relevant_product=product is not None,
            creator_sales_enabled=creator_sales_enabled,
            user_segment_names=segment_names,
            user_segment_count=segment_count,
            hours_since_last_offer=timing["hours_since_last_offer"],
            hours_since_last_purchase=timing["hours_since_last_purchase"],
            recent_offer_count=timing["recent_offer_count"],
            recent_purchase_count=timing["recent_purchase_count"],
            recent_sales_attempt_count=timing["recent_sales_attempt_count"],
            consecutive_rejections=behavioral["consecutive_rejections"],
            total_purchases=behavioral["total_purchases"],
            total_tips_received=behavioral["total_tips_received"],
            hours_since_last_tip=behavioral["hours_since_last_tip"],
            tip_suggestions_sent=behavioral["tip_suggestions_sent"],
            tip_suggestions_ignored=behavioral["tip_suggestions_ignored"],
        )

        # Phase C: Derive relationship context and inject into request
        try:
            from commerce.relationship import (
                check_operator_handoff,
                check_tip_eligibility,
                derive_commercial_pressure,
                derive_relationship_state,
            )

            # Calculate days since last purchase
            last_purchase_days_ago = None
            if timing.get("hours_since_last_purchase") is not None:
                last_purchase_days_ago = timing["hours_since_last_purchase"] / 24.0

            # Calculate days since last message (from timing context or default)
            last_message_days_ago = None
            if timing.get("hours_since_last_offer") is not None:
                last_message_days_ago = timing["hours_since_last_offer"] / 24.0

            # Derive relationship state
            relationship_state = derive_relationship_state(
                funnel_stage=None,  # Will be resolved from user data
                purchase_count=timing.get("recent_purchase_count", 0),
                last_purchase_days_ago=last_purchase_days_ago,
                last_message_days_ago=last_message_days_ago,
                message_count=0,
                has_active_offer=active_offer,
            )

            # Derive commercial pressure
            commercial_pressure = derive_commercial_pressure(
                relationship_state=relationship_state,
                recent_offer_count_24h=timing.get("recent_offer_count", 0),
                recent_purchase_count_24h=timing.get("recent_purchase_count", 0),
                hours_since_last_offer=timing.get("hours_since_last_offer"),
                hours_since_last_purchase=timing.get("hours_since_last_purchase"),
            )

            # Check tip eligibility
            tip_eligibility, tip_reason = check_tip_eligibility(
                relationship_state=relationship_state,
                commercial_pressure=commercial_pressure,
                hours_since_last_tip=behavioral.get("hours_since_last_tip"),
                has_active_offer=active_offer,
                recent_purchase_count=timing.get("recent_purchase_count", 0),
                tip_suggestions_sent=behavioral.get("tip_suggestions_sent", 0),
                tip_suggestions_ignored=behavioral.get("tip_suggestions_ignored", 0),
                commercial_paused=behavioral.get("consecutive_rejections", 0) >= 3,
            )

            # Check operator handoff
            should_handoff, handoff_reason = check_operator_handoff(
                relationship_state=relationship_state,
                commercial_pressure=commercial_pressure,
            )

            # Derive C.1-C feedback fields
            from commerce.feedback import is_repeat_purchase_eligible

            repeat_eligible = is_repeat_purchase_eligible(
                total_purchases=behavioral["total_purchases"],
                hours_since_last_purchase=timing.get("hours_since_last_purchase"),
                current_engagement=last_message_days_ago is not None and last_message_days_ago < 7.0,
                post_purchase_satisfaction=behavioral.get("post_purchase_satisfaction"),
                commercial_paused=behavioral["consecutive_rejections"] >= 3,
                consecutive_rejections=behavioral["consecutive_rejections"],
            )

            # Commercial pause: 3+ consecutive rejections
            feedback_commercial_paused = behavioral["consecutive_rejections"] >= 3

            # Aftercare status from DB (set on purchase, cleared on follow-up)
            aftercare_status = behavioral.get("aftercare_status", "none")

            # Inject into pipeline request
            pipeline_request = pipeline_request.model_copy(update={
                "relationship_state": relationship_state.value,
                "commercial_pressure": commercial_pressure.value,
                "tip_eligibility": tip_eligibility.value,
                "tip_reason": tip_reason,
                "handoff_needed": should_handoff,
                "handoff_reason": handoff_reason.value if handoff_reason else None,
                "repeat_purchase_eligible": repeat_eligible,
                "commercial_paused": feedback_commercial_paused,
                "aftercare_status": aftercare_status,
            })
        except Exception:
            logger.debug("relationship derivation failed, using defaults", exc_info=True)
        return CommerceStateResolution(
            status=CommerceResolutionStatus.READY, request=pipeline_request
        )
    except Exception:
        logger.warning(
            "commerce state resolution failed (user_id=%s)",
            request.user_id,
            exc_info=True,
        )
        return CommerceStateResolution(status=CommerceResolutionStatus.RESOLUTION_FAILED)
