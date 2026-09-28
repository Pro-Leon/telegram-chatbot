"""P3.1 — Deterministic LLM context assembly.

Gathers relevant application state from PostgreSQL and builds a structured
``LLMContext`` that enriches the LLM's system prompt with deterministic
commerce/activity facts.  The LLM may USE these facts to formulate a
response; it may NOT modify them.

Architecture:

    PostgreSQL
        ↓
    build_llm_context(creator_id, user_id)
        ↓
    LLMContext (frozen dataclass — typed, deterministic, bounded)
        ↓
    render_context(context) → system-prompt section
        ↓
    Existing memory/context.py::build_context()
        ↓
    Existing LLM generation

Invariants:

- DETERMINISTIC: identical DB state + identical args → equivalent context.
- READ-ONLY: only SELECT queries; no writes, no side effects.
- BOUNDED: every list is capped; overall context size is bounded.
- CREATOR-SCOPED: every commerce query filters by creator_id.
- FAILURE-ISOLATED: any single source failure degrades gracefully.
- NO SECRETS: buyer email, API keys, webhook secrets, ciphertext are
  never exposed.
- NO TRANSACTIONAL AUTHORITY: context is informational only.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from commerce.relationship import (
    derive_commercial_pressure,
)

from core.config import get_settings
from memory.context_limits import (
    MAX_ACTIVE_OFFERS,
    MAX_CONTEXT_MESSAGES,
    MAX_PURCHASE_HISTORY,
)

logger = logging.getLogger("context_assembler")
_settings = get_settings()


# ── Typed context contract ─────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMContext:
    """Immutable, deterministic context for LLM generation.

    Every field is sourced from application/database state.
    No field can be altered by conversation text.
    """

    user_id: int

    # User
    funnel_stage: str
    is_blocked: bool
    do_not_auto_reply: bool
    first_name: str
    message_count: int

    # Conversation
    conversation_summary: str | None
    recent_messages: list[dict[str, Any]] = field(default_factory=list)

    # Commerce
    purchase_count: int = 0
    recent_purchases: list[dict[str, Any]] = field(default_factory=list)
    has_active_offer: bool = False
    active_offer: dict[str, Any] | None = None

    # Product (deterministic only — None when ambiguous)
    product_title: str | None = None
    product_price_minor: int | None = None
    product_currency: str | None = None
    product_sales_url: str | None = None

    # Activity
    last_purchase_at: str | None = None
    last_followup_at: str | None = None

    # Creator
    creator_name: str | None = None
    creator_sales_enabled: bool = False

    # Segments (informational context only — not authorization)
    segment_names: list[str] = field(default_factory=list)
    segment_count: int = 0

    # Phase C: Relationship context (deterministic, derived from observable data)
    relationship_state: str = "cold"
    commercial_pressure: str = "none"
    tip_eligibility: str = "ineligible"
    tip_reason: str = ""
    handoff_needed: bool = False
    handoff_reason: str | None = None

    # Phase C.1-C: Behavioral feedback context (deterministic, from DB)
    consecutive_rejections: int = 0
    total_purchases: int = 0
    aftercare_status: str = "none"
    commercial_paused: bool = False
    repeat_purchase_eligible: bool = False

    # Phase 2.2: history degraded tag (True only when history reads errored;
    # genuine empty history is degraded=False). No DB write.
    history_degraded: bool = False


# ── DB query helpers (private) ─────────────────────────────────────────────


async def _get_user_safe(user_id: int) -> tuple[dict[str, Any], bool]:
    """Fetch user row. Returns ``(user, degraded)``; safe defaults on failure."""
    try:
        from db.postgres import get_user_durable

        user, degraded, source = await get_user_durable(user_id)
        if degraded:
            logger.warning(
                "context_assembler: user query degraded user=%s source=%s", user_id, source
            )
        if user is None:
            return {
                "first_name": "there",
                "funnel_stage": "new",
                "is_blocked": False,
                "do_not_auto_reply": False,
                "message_count": 0,
            }, degraded
        return {
            "first_name": user.get("first_name") or "there",
            "funnel_stage": user.get("funnel_stage") or "new",
            "is_blocked": bool(user.get("is_blocked", False)),
            "do_not_auto_reply": bool(user.get("do_not_auto_reply", False)),
            "message_count": int(user.get("message_count") or 0),
        }, degraded
    except Exception:
        logger.warning(
            "context_assembler: user query degraded user=%s source=error", user_id, exc_info=True
        )
        return {
            "first_name": "there",
            "funnel_stage": "new",
            "is_blocked": False,
            "do_not_auto_reply": False,
            "message_count": 0,
        }, True


async def _get_recent_messages_safe(
    user_id: int, limit: int, creator_id: int | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch recent messages. Returns ``(rows, degraded)``; [] on failure."""
    if creator_id is None:
        logger.warning("context_assembler: missing creator_id for user=%s – strict isolation, returning empty", user_id)
        return [], False
    try:
        from db.postgres import get_recent_messages_durable

        rows, degraded, source = await get_recent_messages_durable(
            user_id, limit=limit, creator_id=creator_id
        )
        if degraded:
            logger.warning(
                "context_assembler: messages query degraded user=%s source=%s", user_id, source
            )
        return rows, degraded
    except Exception:
        logger.warning(
            "context_assembler: messages query degraded user=%s source=error", user_id, exc_info=True
        )
        return [], True


async def _get_summary_safe(user_id: int, creator_id: int | None = None) -> tuple[str | None, bool]:
    """Fetch latest conversation summary. Returns ``(summary, degraded)``."""
    if creator_id is None:
        logger.warning("context_assembler: missing creator_id for user=%s – strict isolation, returning None", user_id)
        return None, False
    try:
        from db.postgres import get_latest_summary_durable

        summary, degraded, source = await get_latest_summary_durable(
            user_id, creator_id=creator_id
        )
        if degraded:
            logger.warning(
                "context_assembler: summary query degraded user=%s source=%s", user_id, source
            )
        return summary, degraded
    except Exception:
        logger.warning(
            "context_assembler: summary query degraded user=%s source=error", user_id, exc_info=True
        )
        return None, True


async def _get_purchases_safe(
    creator_id: int, user_id: int, limit: int
) -> tuple[int, list[dict[str, Any]]]:
    """Fetch purchase history (count + recent purchases).

    Returns (purchase_count, recent_purchases_list).
    Each purchase dict contains only conversationally useful fields.
    """
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            # Total count
            count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased' AND transaction_id IS NOT NULL
                """,
                creator_id,
                user_id,
            )
            purchase_count = int(count_row["cnt"]) if count_row else 0

            # Recent purchases (deterministic ordering)
            rows = await conn.fetch(
                """
                SELECT fp.title AS product_title,
                       co.price_minor,
                       co.currency,
                       co.purchased_at
                FROM commerce_offers co
                LEFT JOIN fangate_products fp
                    ON fp.creator_id = co.creator_id AND fp.id = co.product_id
                WHERE co.creator_id = $1 AND co.user_id = $2
                  AND co.state = 'purchased' AND co.transaction_id IS NOT NULL
                ORDER BY co.purchased_at DESC NULLS LAST, co.id DESC
                LIMIT $3
                """,
                creator_id,
                user_id,
                limit,
            )
            recent = [
                {
                    "product_title": r["product_title"] or "Unknown",
                    "price_minor": r["price_minor"],
                    "currency": r["currency"],
                    "occurred_at": r["purchased_at"].isoformat() if r["purchased_at"] else None,
                }
                for r in rows
            ]
            return purchase_count, recent
    except Exception:
        logger.warning(
            "context_assembler: purchase query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return 0, []


async def _get_active_offers_safe(
    creator_id: int, user_id: int, limit: int
) -> tuple[bool, dict[str, Any] | None]:
    """Fetch active (pending/clicked) offers.

    Returns (has_active_offer, most_recent_active_offer_or_None).
    """
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fp.title AS product_title,
                       co.price_minor,
                       co.currency,
                       co.state,
                       co.created_at
                FROM commerce_offers co
                LEFT JOIN fangate_products fp
                    ON fp.creator_id = co.creator_id AND fp.id = co.product_id
                WHERE co.creator_id = $1 AND co.user_id = $2
                  AND co.state IN ('pending', 'clicked')
                ORDER BY co.created_at DESC, co.id DESC
                LIMIT $3
                """,
                creator_id,
                user_id,
                limit,
            )
            if not rows:
                return False, None
            # Most recent is first (ORDER BY DESC)
            most_recent = rows[0]
            return True, {
                "product_title": most_recent["product_title"] or "Unknown",
                "price_minor": most_recent["price_minor"],
                "currency": most_recent["currency"],
                "state": most_recent["state"],
                "created_at": (
                    most_recent["created_at"].isoformat()
                    if most_recent["created_at"]
                    else None
                ),
            }
    except Exception:
        logger.warning(
            "context_assembler: offer query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return False, None


async def _get_product_info_safe(
    creator_id: int, user_id: int
) -> tuple[str | None, int | None, str | None, str | None]:
    """Resolve product info deterministically.

    Only returns product info when there is exactly one relevant product
    (from the active offer). Otherwise returns all None (fail-closed).
    """
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            # Find products from active offers for this user
            rows = await conn.fetch(
                """
                SELECT DISTINCT co.product_id
                FROM commerce_offers co
                WHERE co.creator_id = $1 AND co.user_id = $2
                  AND co.state IN ('pending', 'clicked')
                """,
                creator_id,
                user_id,
            )
            if len(rows) != 1:
                # Ambiguous or no product — fail closed
                return None, None, None, None

            product_id = rows[0]["product_id"]

            # Get product details (creator-scoped, provider-neutral mirror)
            from db.fangate import get_fangate_product

            product = await get_fangate_product(creator_id, product_id)
            if product is None:
                return None, None, None, None

            # Dropfans uses USD as the sole active provider
            currency = "USD"

            return (
                product.get("title"),
                product.get("price_minor"),
                currency,
                product.get("sales_url"),
            )
    except Exception:
        logger.warning(
            "context_assembler: product query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return None, None, None, None


async def _get_creator_info_safe(
    creator_id: int,
) -> tuple[str | None, bool]:
    """Fetch creator name and sales-enabled state."""
    try:
        from db.fangate import get_creator
        from db import dropfans as ddb

        creator = await get_creator(creator_id)
        # Check Dropfans integration (sole active provider)
        try:
            df_integration = await ddb.get_dropfans_integration(creator_id)
        except Exception:
            logger.warning(
                "context_assembler: dropfans integration lookup failed (creator=%s)",
                creator_id,
                exc_info=True,
            )
            df_integration = None
        if df_integration and df_integration.get("status") == "active":
            name = df_integration.get("dropfans_display_name") or (
                creator.get("display_name") or creator.get("name") if creator else None
            )
            return name, True

        # Dropfans not configured — sales disabled
        name = creator.get("display_name") or creator.get("name") if creator else None
        return name, False
    except Exception:
        logger.warning(
            "context_assembler: creator query failed (creator=%s)",
            creator_id,
            exc_info=True,
        )
        return None, False


async def _get_last_followup_at_safe(
    creator_id: int, user_id: int
) -> str | None:
    """Fetch most recent scheduled follow-up timestamp."""
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT created_at FROM scheduled_messages
                WHERE creator_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                creator_id,
                user_id,
            )
            if row and row["created_at"]:
                return row["created_at"].isoformat()
            return None
    except Exception:
        logger.warning(
            "context_assembler: follow-up query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return None


async def _get_last_purchase_at_safe(
    creator_id: int, user_id: int
) -> str | None:
    """Fetch most recent purchase timestamp."""
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT purchased_at FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased' AND transaction_id IS NOT NULL
                ORDER BY purchased_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                creator_id,
                user_id,
            )
            if row and row["purchased_at"]:
                return row["purchased_at"].isoformat()
            return None
    except Exception:
        logger.warning(
            "context_assembler: last purchase query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return None


async def _get_segments_safe(
    creator_id: int, user_id: int
) -> tuple[int, list[str]]:
    """Return (count, list_of_segment_names) the user belongs to.

    Uses the existing segment evaluation engine to check all enabled
    segments for this creator. Returns (0, []) on failure.
    """
    try:
        from db import segments as sdb
        from segments.evaluator import check_user_in_segment
        from segments.models import RuleGroup

        segments = await sdb.list_segments(creator_id, enabled_only=True)
        member_names: list[str] = []
        for seg in segments:
            rules_data = seg.get("rules")
            if not rules_data:
                continue
            try:
                rule = RuleGroup.model_validate(rules_data)
                is_member = await check_user_in_segment(creator_id, rule, user_id)
                if is_member:
                    member_names.append(seg["name"])
            except Exception:  # noqa: BLE001,S112 — per-segment eval failure is non-critical
                continue
        return len(member_names), member_names
    except Exception:
        logger.warning(
            "context_assembler: segment query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return 0, []


# ── Public assembler ────────────────────────────────────────────────────────


async def build_llm_context(
    creator_id: int,
    user_id: int,
    user_data: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    summary: str | None = None,
) -> LLMContext:
    """Build a deterministic, bounded LLMContext from application state.

    All queries are creator-scoped.  Failures degrade gracefully per source.
    No LLM calls.  No writes.  No randomness.  No clock reads.

    B2: If *user_data*, *recent_messages*, or *summary* are provided they are
    reused instead of re-querying PostgreSQL — generation-local reuse.
    """
    max_messages = _settings.max_context_messages
    max_purchases = _settings.max_purchase_history
    max_offers = _settings.max_active_offers

    # Parallel-safe: all queries are independent reads
    # Each wrapped in try/except for failure isolation — one source failing
    # must not prevent other sources from being queried or returned.
    _DEFAULT_USER: dict[str, Any] = {
        "first_name": "there",
        "is_blocked": False,
        "do_not_auto_reply": False,
        "funnel_stage": "new",
        "message_count": 0,
    }

    # B2: use provided data if available, else fetch (durable: degraded tracked)
    _history_degraded = False
    if user_data is None:
        try:
            user_data, _u_deg = await _get_user_safe(user_id)
            _history_degraded = _history_degraded or bool(_u_deg)
        except Exception:
            user_data = _DEFAULT_USER
            _history_degraded = True
    else:
        # Normalize the provided user_data to match expected shape
        user_data = {
            "first_name": user_data.get("first_name") or "there",
            "funnel_stage": user_data.get("funnel_stage") or "new",
            "is_blocked": bool(user_data.get("is_blocked", False)),
            "do_not_auto_reply": bool(user_data.get("do_not_auto_reply", False)),
            "message_count": int(user_data.get("message_count") or 0),
        }

    if recent_messages is None:
        try:
            recent_messages, _r_deg = await _get_recent_messages_safe(user_id, max_messages, creator_id=creator_id)
            _history_degraded = _history_degraded or bool(_r_deg)
        except Exception:
            recent_messages = []
            _history_degraded = True

    if summary is None:
        try:
            summary, _s_deg = await _get_summary_safe(user_id, creator_id=creator_id)
            _history_degraded = _history_degraded or bool(_s_deg)
        except Exception:
            summary = None
            _history_degraded = True

    # B3: parallelize all independent sub-queries using asyncio.gather
    import asyncio

    async def _safe(fn, *args, default):
        try:
            return await fn(*args)
        except Exception:
            return default

    _gather_results = await asyncio.gather(
        _safe(_get_purchases_safe, creator_id, user_id, max_purchases, default=(0, [])),
        _safe(_get_active_offers_safe, creator_id, user_id, max_offers, default=(False, None)),
        _safe(_get_product_info_safe, creator_id, user_id, default=(None, None, None, None)),
        _safe(_get_creator_info_safe, creator_id, default=(None, False)),
        _safe(_get_last_purchase_at_safe, creator_id, user_id, default=None),
        _safe(_get_last_followup_at_safe, creator_id, user_id, default=None),
        _safe(_get_segments_safe, creator_id, user_id, default=(0, [])),
        return_exceptions=True,
    )

    purchase_count, recent_purchases = _gather_results[0] if not isinstance(_gather_results[0], Exception) else (0, [])
    has_active_offer, active_offer = _gather_results[1] if not isinstance(_gather_results[1], Exception) else (False, None)
    product_title, product_price, product_currency, product_url = _gather_results[2] if not isinstance(_gather_results[2], Exception) else (None, None, None, None)
    creator_name, creator_sales_enabled = _gather_results[3] if not isinstance(_gather_results[3], Exception) else (None, False)
    last_purchase_at = _gather_results[4] if not isinstance(_gather_results[4], Exception) else None
    last_followup_at = _gather_results[5] if not isinstance(_gather_results[5], Exception) else None
    segment_count, segment_names = _gather_results[6] if not isinstance(_gather_results[6], Exception) else (0, [])

    # Phase C.1-C: Behavioral feedback context (best-effort)
    consecutive_rejections_val = 0
    total_purchases_val = 0
    aftercare_status_val = "none"
    commercial_paused_val = False
    repeat_purchase_eligible_val = False

    try:
        from commerce.dao import get_behavioral_feedback_context
        behavioral = await get_behavioral_feedback_context(creator_id, user_id)
        consecutive_rejections_val = behavioral["consecutive_rejections"]
        total_purchases_val = behavioral["total_purchases"]
        aftercare_status_val = behavioral.get("aftercare_status", "none")
        commercial_paused_val = consecutive_rejections_val >= 3

        from commerce.feedback import is_repeat_purchase_eligible
        repeat_purchase_eligible_val = is_repeat_purchase_eligible(
            total_purchases=total_purchases_val,
            hours_since_last_purchase=None,  # Not queried here, defaults safe
            current_engagement=len(recent_messages) > 0,
            post_purchase_satisfaction=None,
            commercial_paused=commercial_paused_val,
            consecutive_rejections=consecutive_rejections_val,
        )
    except Exception:
        pass

    # Phase C: Derive relationship state from observable data
    rel_state = "cold"
    commercial_pressure_val = "none"
    tip_eligibility_val = "ineligible"
    tip_reason_val = ""
    handoff_needed = False
    handoff_reason_val = None

    try:
        # Calculate days since last purchase for the relationship derivation
        last_purchase_days_ago = None
        if last_purchase_at:
            try:
                from datetime import datetime, timezone
                last_purchase_dt = datetime.fromisoformat(last_purchase_at.replace("Z", "+00:00"))
                last_purchase_days_ago = (datetime.now(timezone.utc) - last_purchase_dt).total_seconds() / 86400
            except Exception:
                pass

        # Calculate days since last message
        last_message_days_ago = None
        if recent_messages:
            try:
                from datetime import datetime, timezone
                last_msg = recent_messages[0]  # most recent message
                if "timestamp" in last_msg:
                    last_msg_dt = datetime.fromisoformat(last_msg["timestamp"].replace("Z", "+00:00"))
                    last_message_days_ago = (datetime.now(timezone.utc) - last_msg_dt).total_seconds() / 86400
            except Exception:
                pass

        # Phase 2.4: single lifecycle owner (thin consumer — no local derivation).
        from core.conversation_state import derive_lifecycle_state as _lifecycle_owner

        _life = _lifecycle_owner(
            funnel_stage=user_data["funnel_stage"],
            purchase_count=purchase_count,
            last_purchase_days_ago=last_purchase_days_ago,
            last_message_days_ago=last_message_days_ago,
            message_count=user_data["message_count"],
            has_active_offer=has_active_offer,
        )
        from commerce.relationship import RelationshipState as _RelState

        relationship_state = _RelState(_life.relationship_state)
        rel_state = relationship_state.value

        # Derive commercial pressure
        from commerce.relationship import derive_commercial_pressure
        pressure = derive_commercial_pressure(
            relationship_state=relationship_state,
        )
        commercial_pressure_val = pressure.value

        # Check tip eligibility — now wired with real history (P0-01: was hard-zero)
        hstl = behavioral.get("hours_since_last_tip") if 'behavioral' in locals() else None
        tip_sent = behavioral.get("tip_suggestions_sent", 0) if 'behavioral' in locals() else 0
        tip_ignored = behavioral.get("tip_suggestions_ignored", 0) if 'behavioral' in locals() else 0
        tip_eligibility, tip_reason = check_tip_eligibility(
            relationship_state=relationship_state,
            commercial_pressure=pressure,
            hours_since_last_tip=hstl,
            tip_suggestions_sent=tip_sent,
            tip_suggestions_ignored=tip_ignored,
        )
        tip_eligibility_val = tip_eligibility.value
        tip_reason_val = tip_reason

        # Check operator handoff
        from commerce.relationship import check_operator_handoff
        should_handoff, handoff_reason = check_operator_handoff(
            relationship_state=relationship_state,
            commercial_pressure=pressure,
        )
        handoff_needed = should_handoff
        handoff_reason_val = handoff_reason.value if handoff_reason else None

    except Exception:
        pass

    return LLMContext(
        user_id=user_id,
        funnel_stage=user_data["funnel_stage"],
        is_blocked=user_data["is_blocked"],
        do_not_auto_reply=user_data["do_not_auto_reply"],
        first_name=user_data["first_name"],
        message_count=user_data["message_count"],
        conversation_summary=summary,
        recent_messages=recent_messages,
        purchase_count=purchase_count,
        recent_purchases=recent_purchases,
        has_active_offer=has_active_offer,
        active_offer=active_offer,
        product_title=product_title,
        product_price_minor=product_price,
        product_currency=product_currency,
        product_sales_url=product_url,
        last_purchase_at=last_purchase_at,
        last_followup_at=last_followup_at,
        creator_name=creator_name,
        creator_sales_enabled=creator_sales_enabled,
        segment_names=segment_names,
        segment_count=segment_count,
        # Phase C: Relationship context
        relationship_state=rel_state,
        commercial_pressure=commercial_pressure_val,
        tip_eligibility=tip_eligibility_val,
        tip_reason=tip_reason_val,
        handoff_needed=handoff_needed,
        handoff_reason=handoff_reason_val,
        # Phase C.1-C: Behavioral feedback context
        consecutive_rejections=consecutive_rejections_val,
        total_purchases=total_purchases_val,
        aftercare_status=aftercare_status_val,
        commercial_paused=commercial_paused_val,
        repeat_purchase_eligible=repeat_purchase_eligible_val,
        # Phase 2.2: history degraded tag (ERROR-sourced empties, not silent)
        history_degraded=bool(_history_degraded),
    )


# ── Renderer ───────────────────────────────────────────────────────────────


def render_context(ctx: LLMContext) -> str:
    """Render LLMContext into a deterministic system-prompt section.

    Output is compact structured text.  No prose.  No secrets.
    No internal IDs.  No buyer email.
    """
    lines: list[str] = []

    # Funnel / user state
    lines.append(f"Funnel stage: {ctx.funnel_stage}")
    lines.append(f"Auto-reply allowed: {'no' if ctx.do_not_auto_reply else 'yes'}")

    # Purchase history
    lines.append(f"Purchases: {ctx.purchase_count}")
    if ctx.recent_purchases:
        for p in ctx.recent_purchases:
            title = p.get("product_title") or "Unknown"
            price = p.get("price_minor")
            currency = p.get("currency") or ""
            occurred = p.get("occurred_at") or "unknown"
            price_str = f" {price} {currency}" if price is not None else ""
            lines.append(f"- {title}{price_str} — {occurred}")

    # Active offer
    if ctx.has_active_offer and ctx.active_offer:
        offer = ctx.active_offer
        title = offer.get("product_title") or "Unknown"
        price = offer.get("price_minor")
        currency = offer.get("currency") or ""
        state = offer.get("state") or "unknown"
        price_str = f" {price} {currency}" if price is not None else ""
        lines.append(f"Active offer: {title}{price_str} — {state}")

    # Product (only when deterministic)
    if ctx.product_title:
        price = ctx.product_price_minor
        currency = ctx.product_currency or ""
        price_str = f" {price} {currency}" if price is not None else ""
        lines.append(f"Current product: {ctx.product_title}{price_str}")

    # Activity
    if ctx.last_purchase_at:
        lines.append(f"Last purchase: {ctx.last_purchase_at}")
    if ctx.last_followup_at:
        lines.append(f"Last follow-up: {ctx.last_followup_at}")

    # Creator
    if ctx.creator_name:
        sales = "yes" if ctx.creator_sales_enabled else "no"
        lines.append(f"Creator: {ctx.creator_name} (sales enabled: {sales})")

    # Segments (informational context only)
    if ctx.segment_count > 0:
        lines.append(f"Segments: {', '.join(ctx.segment_names)}")

    # Phase C: Relationship context
    if ctx.relationship_state != "cold":
        lines.append(f"Relationship: {ctx.relationship_state}")
    if ctx.commercial_pressure != "none":
        lines.append(f"Commercial pressure: {ctx.commercial_pressure}")
    if ctx.tip_eligibility == "eligible":
        lines.append(f"Tip eligible: yes — {ctx.tip_reason}")
    if ctx.handoff_needed:
        lines.append(f"Operator handoff: {ctx.handoff_reason or 'needed'}")

    # Phase C.1-C: Behavioral feedback (compact, actionable)
    if ctx.consecutive_rejections > 0:
        lines.append(f"Rejections: {ctx.consecutive_rejections} consecutive")
    if ctx.commercial_paused:
        lines.append("Commercial pause: active (suppress offers)")
    if ctx.aftercare_status != "none":
        lines.append(f"Aftercare: {ctx.aftercare_status}")
    if ctx.repeat_purchase_eligible:
        lines.append("Repeat purchase: eligible")

    # P1-03: Abandoned offer detection (pending >48h)
    if ctx.has_active_offer and ctx.active_offer:
        # active_offer is dict with created_at, title etc. Check age
        try:
            from datetime import datetime, timezone
            created = ctx.active_offer.get("created_at") if isinstance(ctx.active_offer, dict) else None
            if created:
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                if age_h >= 48:
                    title = ctx.active_offer.get("title") or ctx.product_title or "previous offer"
                    lines.append(f"Abandoned offer: {title} ({int(age_h)}h ago, status pending)")
        except Exception:
            pass

    return "\n".join(lines)
