"""Re-engagement eligibility (Phase 12).

Deterministic eligibility for autonomous re-engagement of abandoned offers.
Uses existing scheduled_messages infrastructure, no new scheduler.
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("commerce.re_engagement")

async def is_reengagement_eligible(
    *,
    creator_id: int,
    user_id: int,
    has_active_offer: bool,
    offer_age_hours: float | None,
    aftercare_status: str,
    is_on_cooldown: bool,
    consecutive_rejections: int,
    has_relevant_unpurchased: bool,
    relationship_state: str,
) -> tuple[bool, str]:
    """Determine if autonomous re-engagement is allowed.

    Returns (eligible, reason).
    """
    if not has_active_offer:
        return False, "no_active_offer"
    if offer_age_hours is None or offer_age_hours < 48:
        return False, "too_soon"
    if aftercare_status in ("pending", "sent"):
        return False, "aftercare"
    if is_on_cooldown:
        return False, "cooldown"
    if consecutive_rejections >= 3:
        return False, "rejected"
    if not has_relevant_unpurchased:
        return False, "no_relevant_unpurchased"
    if relationship_state in ("do_not_push", "operator_required", "cold"):
        return False, "relationship_not_permitted"
    return True, "eligible"

async def schedule_reengagement_if_eligible(
    *,
    creator_id: int,
    user_id: int,
    product_id: int,
    offer: dict | None = None,
) -> bool:
    """Schedule re-engagement via existing scheduled_messages if eligible.

    Uses dedup_key=reengage:{creator}:{user}:{product} and execute_at NOW+48h.
    Returns True if scheduled or already exists, False otherwise.

    P3.4 convergence: the stale pending ``commerce_offers`` row (``offer``)
    is the sole candidate identity. Commercial eligibility is verified via
    the existing Opportunity Engine / eligibility primitives
    (``commerce.reengagement_eligibility.is_stale_offer_reengageable``)
    before any scheduling. Fail closed (skip) when identity is missing,
    ineligible, or unverifiable. No new offer, price, product, ranking,
    sealing, or legacy fallback. ``product_id`` is preserved verbatim from
    the stale offer for dedup-key continuity only — never recomputed.
    """
    try:
        # P3.4 gate: stale-offer identity is required; never invent a
        # candidate from product/catalog/taxonomy/LLM signals.
        if offer is None:
            logger.debug(
                "re_engagement not eligible for %s:%s reason=missing_stale_offer",
                creator_id,
                user_id,
            )
            return False
        try:
            from commerce.reengagement_eligibility import (
                is_stale_offer_reengageable as _is_stale_reengageable,
            )

            _reengage_ok, _reengage_reason = await _is_stale_reengageable(
                creator_id=creator_id, user_id=user_id, offer=offer
            )
        except Exception:
            logger.debug(
                "re_engagement eligibility check failed for %s:%s",
                creator_id,
                user_id,
                exc_info=True,
            )
            return False
        if not _reengage_ok:
            logger.debug(
                "re_engagement not eligible for %s:%s reason=%s",
                creator_id,
                user_id,
                _reengage_reason,
            )
            return False

        from db.postgres import get_pool
        from commerce.dao import get_timing_context, get_behavioral_feedback_context

        # Check eligibility
        timing = await get_timing_context(creator_id, user_id)
        behavioral = await get_behavioral_feedback_context(creator_id, user_id)
        has_active = False
        offer_age_hours = None
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT created_at FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND state IN ('\''pending'\'','\''clicked'\'') ORDER BY created_at DESC LIMIT 1",
                    creator_id, user_id,
                )
                if row and row["created_at"]:
                    from datetime import datetime, timezone
                    created = row["created_at"]
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    offer_age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                    has_active = True
        except Exception:
            pass

        # Check other conditions via helper
        eligible, reason = await is_reengagement_eligible(
            creator_id=creator_id,
            user_id=user_id,
            has_active_offer=has_active,
            offer_age_hours=offer_age_hours,
            aftercare_status=behavioral.get("aftercare_status", "none"),
            is_on_cooldown=behavioral.get("consecutive_rejections", 0) >= 3 or (timing.get("hours_since_last_offer") or 999) < 24,
            consecutive_rejections=behavioral.get("consecutive_rejections", 0),
            has_relevant_unpurchased=True,  # simplified
            relationship_state="warm",
        )
        if not eligible:
            logger.debug("re_engagement not eligible for %s:%s reason=%s", creator_id, user_id, reason)
            return False

        # Schedule via existing scheduled_messages
        from db.postgres import create_scheduled_message
        from datetime import datetime, timezone, timedelta
        dedup_key = f"reengage:{creator_id}:{user_id}:{product_id}"
        execute_at = datetime.now(timezone.utc) + timedelta(hours=48)
        content = "Hey, still thinking about that set? 😏"
        await create_scheduled_message(
            user_id=user_id,
            execute_at=execute_at,
            content=content,
            dedup_key=dedup_key,
            reason="reengagement",
            creator_id=creator_id,
            actor_type="scheduler",
            actor_id="reengagement",
        )
        return True
    except Exception:
        logger.warning("schedule_reengagement failed", exc_info=True)
        return False
