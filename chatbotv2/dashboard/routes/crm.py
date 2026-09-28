"""CRM command surface — creator-scoped read-only wiring for commerce/CRM domains.

Exposes the deterministic CRM machinery that previously had no frontend
surface (relationship/intimacy/boundary trajectories, fan commercial
state, offer history, opportunity dry-run, catalog, ledger, and the
pure explain functions) through authenticated, creator-scoped,
bounded JSON endpoints.

Safety contract (mirrors the worker's invariants):
- READ-ONLY except handoff-clear (explicit operator action, audited).
  No endpoint seals, executes, sends, enqueues, mutates offers, or
  spends quota. The opportunity endpoint is a dry-run: evaluation only,
  never persisted (evaluate_opportunity has no write path).
- No raw message text, sexual text, prompt fragments, or permission
  semantics cross the wire: trajectory endpoints render band labels +
  snapshot metadata only (same discipline as the prompt renderers).
- Creator isolation: single-creator resolution, fail-closed 503/[].
- Bounded: limits clamped, datetimes ISO, sets sorted, fail-open errors.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import (
    SingleCreatorStatus,
    resolve_single_application_creator,
)

logger = logging.getLogger("chatbotv2.dashboard.crm")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_creator_or_503() -> int | None:
    try:
        ctx = await resolve_single_application_creator()
    except Exception:
        logger.debug("CRM creator resolution failed (fail-closed)", exc_info=True)
        return None
    if ctx.status is not SingleCreatorStatus.READY or ctx.creator_id is None:
        return None
    return ctx.creator_id


def _jsonable(value: Any) -> Any:
    """Bounded JSON sanitizer: datetimes, sets, enums, dataclasses."""
    try:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, datetime):
            try:
                return value.isoformat()
            except Exception:
                return None
        if isinstance(value, enum.Enum):
            return getattr(value, "value", str(value))
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            try:
                return _jsonable(dataclasses.asdict(value))
            except Exception:
                return None
        if isinstance(value, dict):
            return {str(k)[:64]: _jsonable(v) for k, v in list(value.items())[:50]}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in list(value)[:50]]
        if isinstance(value, (set, frozenset)):
            try:
                return sorted(str(v) for v in value)[:50]
            except Exception:
                return []
        return str(value)[:256]
    except Exception:
        return None


def _band(value: Any) -> str | None:
    try:
        if value is None:
            return None
        token = getattr(value, "value", value)
        text = str(token).strip().lower()
        if not text or text == "unknown":
            return None
        return text
    except Exception:
        return None


def _err(message: str, status: int = 503) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# ---------------------------------------------------------------------------
# Fan 360° — relationship
# ---------------------------------------------------------------------------


@router.get("/api/crm/fan/{user_id}/relationship")
async def api_crm_relationship(user_id: int, auth: dict = Depends(require_auth)):
    """Relationship state, pressure, tip eligibility, trajectory bands."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.relationship import (
            check_tip_eligibility,
            derive_commercial_pressure,
            derive_relationship_state,
        )
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
            get_relationship_anchors,
        )
        from commerce import dao as commerce_dao
        from db.postgres import get_user, get_user_profile

        user = await get_user(user_id) or {}
        try:
            timing = await commerce_dao.get_timing_context(creator_id, user_id)
        except Exception:
            timing = {}
        try:
            behavioral = await commerce_dao.get_behavioral_feedback_context(creator_id, user_id)
        except Exception:
            behavioral = {}
        try:
            profile = await get_user_profile(user_id)
        except Exception:
            profile = {}

        hours_offer = (timing or {}).get("hours_since_last_offer")
        hours_purchase = (timing or {}).get("hours_since_last_purchase")
        last_msg_days = hours_offer / 24.0 if hours_offer is not None else None
        last_buy_days = hours_purchase / 24.0 if hours_purchase is not None else None
        consec = int((behavioral or {}).get("consecutive_rejections", 0) or 0)
        total_buys = int((behavioral or {}).get("total_purchases", 0) or 0)

        history = await commerce_dao.list_offers_for_user(user_id, creator_id=creator_id, limit=1)
        prev_status = history[0].get("state") if history else None

        rel = derive_relationship_state(
            funnel_stage=(user.get("funnel_stage") if isinstance(user, dict) else None),
            purchase_count=total_buys,
            last_purchase_days_ago=last_buy_days,
            last_message_days_ago=last_msg_days,
            message_count=int(user.get("message_count", 0) or 0) if isinstance(user, dict) else 0,
            has_active_offer=bool(prev_status in ("pending", "clicked")),
        )
        pressure = derive_commercial_pressure(
            relationship_state=rel,
            recent_offer_count_24h=int((timing or {}).get("recent_offer_count", 0) or 0),
            recent_purchase_count_24h=int((timing or {}).get("recent_purchase_count", 0) or 0),
            hours_since_last_offer=hours_offer,
            hours_since_last_purchase=hours_purchase,
        )
        tip_elig, tip_reason = check_tip_eligibility(
            relationship_state=rel,
            commercial_pressure=pressure,
            hours_since_last_tip=(behavioral or {}).get("hours_since_last_tip"),
            has_active_offer=bool(prev_status in ("pending", "clicked")),
            recent_purchase_count=int((timing or {}).get("recent_purchase_count", 0) or 0),
            tip_suggestions_sent=int((behavioral or {}).get("tip_suggestions_sent", 0) or 0),
            tip_suggestions_ignored=int((behavioral or {}).get("tip_suggestions_ignored", 0) or 0),
            commercial_paused=consec >= 3,
        )
        bands: dict[str, str | None] = {}
        try:
            anchors = get_relationship_anchors(
                profile if isinstance(profile, dict) else {}, creator_id
            )
            snap = derive_relationship_snapshot(anchors, None)
            for name in ("familiarity", "engagement", "reciprocity", "continuity", "trend"):
                bands[name] = _band(getattr(snap, name, None))
        except Exception:
            logger.debug("CRM relationship snapshot failed (fail-open)", exc_info=True)

        return JSONResponse(
            {
                "user_id": user_id,
                "creator_id": creator_id,
                "relationship_state": getattr(rel, "value", str(rel)),
                "commercial_pressure": getattr(pressure, "value", str(pressure)),
                "tip_eligibility": getattr(tip_elig, "value", str(tip_elig)),
                "tip_reason": tip_reason,
                "previous_offer_status": prev_status,
                "consecutive_rejections": consec,
                "total_purchases": total_buys,
                "trajectory_bands": bands,
                "note": "pressure/tip are baseline (no current-turn context); see trace for per-turn values",
            }
        )
    except Exception:
        logger.debug("CRM relationship failed", exc_info=True)
        return _err("Relationship read failed")


# ---------------------------------------------------------------------------
# Fan 360° — intimacy (bands only, never counters/text)
# ---------------------------------------------------------------------------


@router.get("/api/crm/fan/{user_id}/intimacy")
async def api_crm_intimacy(user_id: int, auth: dict = Depends(require_auth)):
    """Descriptive intimacy bands + dormancy metadata (no raw content)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.intimacy_trajectory import (
            derive_intimacy_snapshot,
            get_intimacy_anchors,
        )
        from db.postgres import get_user_profile

        try:
            profile = await get_user_profile(user_id)
        except Exception:
            profile = {}
        anchors = get_intimacy_anchors(profile if isinstance(profile, dict) else {}, creator_id)
        snap = derive_intimacy_snapshot(anchors, None)
        bands = {}
        for name in (
            "romantic",
            "playful",
            "emotional",
            "sexual_conversation",
            "intimate_continuity",
        ):
            bands[name] = _band(getattr(snap, name, None))
        return JSONResponse(
            {
                "user_id": user_id,
                "creator_id": creator_id,
                "bands": bands,
                "days_since_last_seen": getattr(snap, "days_since_last_seen", None),
                "decay_applied": bool(getattr(snap, "decay_applied", False)),
            }
        )
    except Exception:
        logger.debug("CRM intimacy failed", exc_info=True)
        return _err("Intimacy read failed")


# ---------------------------------------------------------------------------
# Fan 360° — boundaries
# ---------------------------------------------------------------------------


@router.get("/api/crm/fan/{user_id}/boundaries")
async def api_crm_boundaries(user_id: int, auth: dict = Depends(require_auth)):
    """Active boundary constraints + commerce/contact veto state."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.boundary_state import (
            derive_boundary_snapshot,
            get_boundary_constraints,
        )
        from db.postgres import get_user_profile

        try:
            profile = await get_user_profile(user_id)
        except Exception:
            profile = {}
        constraints = get_boundary_constraints(profile, creator_id)
        snap = derive_boundary_snapshot(constraints)
        return JSONResponse(
            {
                "user_id": user_id,
                "creator_id": creator_id,
                "active": list(snap.active),
                "recovering": list(snap.recovering),
                "expired": list(snap.expired),
                "degraded": bool(snap.degraded),
                "blocks_offer": bool(snap.blocks_offer()),
                "blocks_contact": bool(snap.blocks_contact()),
                "wants_close": bool(snap.wants_close()),
            }
        )
    except Exception:
        logger.debug("CRM boundaries failed", exc_info=True)
        return _err("Boundary read failed")


# ---------------------------------------------------------------------------
# Fan 360° — commerce facts (observed state, no scoring)
# ---------------------------------------------------------------------------


@router.get("/api/crm/fan/{user_id}/commerce")
async def api_crm_commerce(user_id: int, auth: dict = Depends(require_auth)):
    """Fan commercial state, offer history, pending offers, handoff state."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce import dao as commerce_dao
        from commerce.conversation_operations import get_handoff_memory
        from commerce.fan_commercial_state import get_fan_commercial_state
        from commerce.offer_history import get_offer_history

        try:
            fan_state = await get_fan_commercial_state(creator_id, user_id)
            fan_state_d = _jsonable(fan_state)
        except Exception:
            fan_state_d = None
        try:
            history = await get_offer_history(creator_id, user_id)
            history_d = _jsonable(history)
        except Exception:
            history_d = None
        try:
            offers = await commerce_dao.list_offers_for_user(
                user_id, creator_id=creator_id, limit=10
            )
            offers_d = _jsonable(offers)
        except Exception:
            offers_d = []
        try:
            timing = await commerce_dao.get_timing_context(creator_id, user_id)
        except Exception:
            timing = {}
        try:
            behavioral = await commerce_dao.get_behavioral_feedback_context(creator_id, user_id)
        except Exception:
            behavioral = {}
        try:
            mem = get_handoff_memory(creator_id, user_id)
            mem_d = _jsonable(mem) if mem else None
        except Exception:
            mem_d = None
        try:
            from commerce.conversation_operations import get_handoff

            durable = await get_handoff(creator_id, user_id)
            durable_d = _jsonable(durable) if durable else None
        except Exception:
            durable_d = None

        return JSONResponse(
            {
                "user_id": user_id,
                "creator_id": creator_id,
                "fan_commercial_state": fan_state_d,
                "offer_history": history_d,
                "recent_offers": offers_d,
                "timing": _jsonable(timing or {}),
                "behavioral": _jsonable(behavioral or {}),
                "handoff_memory": mem_d,
                "handoff_durable": durable_d,
            }
        )
    except Exception:
        logger.debug("CRM commerce failed", exc_info=True)
        return _err("Commerce read failed")


# ---------------------------------------------------------------------------
# Opportunity dry-run (evaluation only — never persisted/sealed/sent)
# ---------------------------------------------------------------------------


@router.get("/api/crm/fan/{user_id}/opportunity")
async def api_crm_opportunity(user_id: int, auth: dict = Depends(require_auth)):
    """Dry-run the Opportunity Engine over recent conversation (read-only)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from core.conversation_state import derive_conversation_state
        from commerce.opportunity_engine import evaluate_opportunity
        from db.postgres import get_recent_messages, get_user

        user = await get_user(user_id) or {}
        recent = await get_recent_messages(user_id, limit=20, creator_id=creator_id)
        cs = derive_conversation_state(recent, user=user)
        result = await evaluate_opportunity(creator_id, user_id, cs, datetime.now(UTC))
        sel = result.selected_candidate
        ineligible = []
        for cand, verdict in list(result.ineligible or [])[:20]:
            ineligible.append(
                {
                    "definition_id": getattr(cand, "definition_id", None),
                    "denial_reasons": list(getattr(verdict, "denial_reasons", []) or []),
                }
            )
        return JSONResponse(
            {
                "user_id": user_id,
                "creator_id": creator_id,
                "has_opportunity": bool(result.has_opportunity),
                "status": result.status,
                "candidates": len(result.candidates or ()),
                "eligible": len(result.eligible_candidates or ()),
                "selected": (
                    {
                        "definition_id": getattr(sel, "definition_id", None),
                        "version": getattr(sel, "version", None),
                        "price_minor": getattr(sel, "price_minor", None),
                        "currency": getattr(sel, "currency", None),
                        "vault_items": len(getattr(sel, "canonical_vault_item_ids", []) or []),
                    }
                    if sel is not None
                    else None
                ),
                "ineligible": ineligible,
                "dry_run": True,
            }
        )
    except Exception:
        logger.debug("CRM opportunity dry-run failed", exc_info=True)
        return _err("Opportunity evaluation unavailable")


# ---------------------------------------------------------------------------
# Handoff clear (explicit operator action, audited)
# ---------------------------------------------------------------------------


@router.post("/api/crm/fan/{user_id}/handoff/clear")
async def api_crm_handoff_clear(user_id: int, auth: dict = Depends(require_auth)):
    """Clear active handoff state so automation may resume (audited)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.conversation_operations import (
            clear_handoff,
            clear_handoff_memory,
        )
        from core.audit import actor_from_auth, record_audit_event

        try:
            clear_handoff_memory(creator_id, user_id)
        except Exception:
            pass
        durable_cleared = False
        try:
            durable_cleared = bool(await clear_handoff(creator_id, user_id))
        except Exception:
            durable_cleared = False
        try:
            await record_audit_event(
                event_type="handoff_clear",
                actor=actor_from_auth(auth),
                creator_id=creator_id,
                user_id=user_id,
                action="POST /api/crm/fan/{id}/handoff/clear",
                state_before="handoff",
                state_after="cleared",
                result="cleared" if durable_cleared else "no_active_handoff",
            )
        except Exception:
            pass
        return JSONResponse({"ok": True, "durable_cleared": durable_cleared})
    except Exception:
        logger.debug("CRM handoff clear failed", exc_info=True)
        return _err("Handoff clear failed", status=500)


# ---------------------------------------------------------------------------
# Ledger + catalog (bounded reads)
# ---------------------------------------------------------------------------


@router.get("/api/crm/opportunities/recent")
async def api_crm_opportunities_recent(
    limit: int = Query(default=25, ge=1, le=50),
    auth: dict = Depends(require_auth),
):
    """Recent opportunity-ledger decisions (bounded, read-only)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT opportunity_id, user_id, evaluated_at, decision_status,
                       outcome_state, selected_definition_id, selected_version,
                       no_selection_reason, sealed_offer_id
                FROM commerce_opportunity_decisions
                WHERE creator_id = $1
                ORDER BY opportunity_id DESC LIMIT $2
                """,
                creator_id,
                int(limit),
            )
        return JSONResponse({"decisions": [_jsonable(dict(r)) for r in rows]})
    except Exception:
        logger.debug("CRM ledger read failed", exc_info=True)
        return _err("Ledger read failed")


@router.get("/api/crm/catalog")
async def api_crm_catalog(auth: dict = Depends(require_auth)):
    """Creator product menu metadata (titles/prices, no authority)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.product_catalog import list_menu

        items = await list_menu(creator_id)
        return JSONResponse({"products": [_jsonable(p) for p in items or []]})
    except Exception:
        logger.debug("CRM catalog read failed", exc_info=True)
        return _err("Catalog read failed")


# ---------------------------------------------------------------------------
# Explain endpoints — pure functions, explicit params, no DB
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return max(0.0, min(1.0, number))
    except Exception:
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


@router.post("/api/crm/explain/desire")
async def api_crm_explain_desire(payload: dict, auth: dict = Depends(require_auth)):
    """Explain desire-stage derivation for explicit inputs (pure, no DB)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.desire import derive_desire_stage

        body = payload if isinstance(payload, dict) else {}
        tags = body.get("intent_tags") or []
        tags = [str(t)[:32] for t in tags if isinstance(t, str)][:5]
        state = derive_desire_stage(
            relationship_state=str(body.get("relationship_state", "cold"))[:32],
            primary_intent=body.get("primary_intent"),
            intent_tags=tags,
            purchase_intent=_num(body.get("purchase_intent")),
            price_interest=_num(body.get("price_interest")),
            explicit_content_request=_bool(body.get("explicit_content_request")),
            explicit_purchase_request=_bool(body.get("explicit_purchase_request")),
            asked_for_free_content=_bool(body.get("asked_for_free_content")),
            has_active_offer=_bool(body.get("has_active_offer")),
            aftercare_status=str(body.get("aftercare_status", "none"))[:16],
            has_purchased=_bool(body.get("has_purchased")),
            consecutive_rejections=int(body.get("consecutive_rejections", 0) or 0),
            fan_asks_question=_bool(body.get("fan_asks_question")),
            commercial_paused=_bool(body.get("commercial_paused")),
        )
        return JSONResponse(
            {
                "stage": getattr(state.stage, "value", str(state.stage)),
                "confidence": state.confidence,
                "evidence": list(state.evidence or []),
            }
        )
    except Exception:
        logger.debug("CRM explain desire failed", exc_info=True)
        return _err("Explain failed", status=500)


@router.post("/api/crm/explain/temperature")
async def api_crm_explain_temperature(payload: dict, auth: dict = Depends(require_auth)):
    """Explain commercial-temperature derivation (pure, no DB)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.temperature import derive_commercial_temperature

        body = payload if isinstance(payload, dict) else {}
        temp = derive_commercial_temperature(
            relationship_score=_num(body.get("relationship_score", 0.2)),
            desire_stage=str(body.get("desire_stage", "relationship"))[:32],
            purchase_intent=_num(body.get("purchase_intent")),
            content_interest=_num(body.get("content_interest")),
            recent_offer_count=int(body.get("recent_offer_count", 0) or 0),
            recent_sales_attempts=int(body.get("recent_sales_attempts", 0) or 0),
            consecutive_rejections=int(body.get("consecutive_rejections", 0) or 0),
            aftercare_status=str(body.get("aftercare_status", "none"))[:16],
            commercial_paused=_bool(body.get("commercial_paused")),
        )
        return JSONResponse(
            {
                "level": str(temp.level),
                "score": temp.score,
                "sales_fatigue": temp.sales_fatigue,
            }
        )
    except Exception:
        logger.debug("CRM explain temperature failed", exc_info=True)
        return _err("Explain failed", status=500)


@router.post("/api/crm/explain/readiness")
async def api_crm_explain_readiness(payload: dict, auth: dict = Depends(require_auth)):
    """Explain readiness + sales-window derivation (pure, no DB)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.offer_readiness import evaluate_offer_readiness
        from commerce.readiness import evaluate_readiness
        from commerce.sales_window import derive_sales_window
        from commerce.warming import derive_warming_state

        body = payload if isinstance(payload, dict) else {}
        desire = str(body.get("desire_stage", "relationship"))[:32]
        temp = str(body.get("temperature", "cold"))[:16]
        intent = _num(body.get("purchase_intent"))
        active = _bool(body.get("has_active_offer"))
        aftercare = _bool(body.get("aftercare_active"))
        cooldown = _bool(body.get("is_on_cooldown"))
        product = _bool(body.get("has_relevant_product", True))
        not_bought = _bool(body.get("not_purchased", True))
        warming = derive_warming_state(
            creator_id=creator_id,
            relationship_state=str(body.get("relationship_state", "cold"))[:32],
            desire_stage=desire,
            purchase_intent=intent,
            recent_offer_count=int(body.get("recent_offer_count", 0) or 0),
            consecutive_rejections=int(body.get("consecutive_rejections", 0) or 0),
            aftercare_status=str(body.get("aftercare_status", "none"))[:16],
            commercial_paused=_bool(body.get("commercial_paused")),
            has_active_offer=active,
        )
        readiness = evaluate_readiness(
            warming=warming,
            desire_stage=desire,
            temperature=temp,
            purchase_intent=intent,
            has_active_offer=active,
            aftercare_active=aftercare,
            is_on_cooldown=cooldown,
            has_relevant_product=product,
            not_purchased=not_bought,
            creator_id=creator_id,
        )
        legacy = evaluate_offer_readiness(
            desire,
            temp,
            purchase_intent=intent,
            has_active_offer=active,
            is_on_cooldown=cooldown,
            aftercare_active=aftercare,
            has_relevant_product=product,
            not_purchased=not_bought,
        )
        window = derive_sales_window(
            desire,
            temp,
            readiness.offer_readiness or "not_ready",
            aftercare_active=aftercare,
            is_on_cooldown=cooldown,
        )
        return JSONResponse(
            {
                "warming": {
                    "level": str(warming.level),
                    "score": warming.score,
                    "available": bool(warming.available),
                    "degraded_reason": warming.degraded_reason,
                },
                "readiness": {
                    "level": str(readiness.level),
                    "available": bool(readiness.available),
                    "degraded_reason": readiness.degraded_reason,
                    "offer_readiness": readiness.offer_readiness,
                },
                "offer_readiness_legacy": getattr(legacy, "value", str(legacy)),
                "sales_window": str(window),
            }
        )
    except Exception:
        logger.debug("CRM explain readiness failed", exc_info=True)
        return _err("Explain failed", status=500)


@router.post("/api/crm/explain/objective")
async def api_crm_explain_objective(payload: dict, auth: dict = Depends(require_auth)):
    """Explain next-objective ranking for explicit inputs (pure, no DB)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.conversation_intelligence import derive_conversation_objective

        body = payload if isinstance(payload, dict) else {}
        selected, candidates = derive_conversation_objective(
            desire=str(body.get("desire", "relationship"))[:32],
            temperature=str(body.get("temperature", "cold"))[:16],
            sales_window=str(body.get("sales_window", "no_window"))[:16],
            offer_readiness=str(body.get("offer_readiness", "not_ready"))[:16],
            has_active_offer=_bool(body.get("has_active_offer")),
            aftercare_status=str(body.get("aftercare_status", "none"))[:16],
            is_on_cooldown=_bool(body.get("is_on_cooldown")),
            has_relevant_product=_bool(body.get("has_relevant_product", True)),
            explicit_purchase_request=_bool(body.get("explicit_purchase_request")),
            explicit_content_request=_bool(body.get("explicit_content_request")),
            has_open_loop=_bool(body.get("has_open_loop")),
            open_loop_importance=_num(body.get("open_loop_importance")),
            has_objection=_bool(body.get("has_objection")),
            is_blocked=_bool(body.get("is_blocked")),
        )
        return JSONResponse(
            {
                "selected": getattr(selected, "value", str(selected)),
                "candidates": [
                    {
                        "objective": getattr(c.objective, "value", str(c.objective)),
                        "priority": c.priority,
                        "eligible": bool(c.eligible),
                        "reason_code": c.reason_code,
                        "blocking_reason": c.blocking_reason,
                    }
                    for c in candidates or []
                ],
            }
        )
    except Exception:
        logger.debug("CRM explain objective failed", exc_info=True)
        return _err("Explain failed", status=500)


@router.post("/api/crm/explain/qualification")
async def api_crm_explain_qualification(payload: dict, auth: dict = Depends(require_auth)):
    """Explain qualification-state derivation (pure, no DB)."""
    creator_id = await _resolve_creator_or_503()
    if creator_id is None:
        return _err("Creator not available")
    try:
        from commerce.qualification import derive_qualification_state

        body = payload if isinstance(payload, dict) else {}
        prefs = body.get("preferences") or {}
        prefs = {str(k)[:32]: str(v)[:64] for k, v in prefs.items() if isinstance(k, str)}
        hist = body.get("purchase_history") or []
        hist = [h for h in hist if isinstance(h, dict)][:10]
        offers = body.get("recent_offers") or []
        offers = [o for o in offers if isinstance(o, dict)][:10]
        state = derive_qualification_state(prefs, hist, offers)
        return JSONResponse(
            {
                "missing_facts": list(state.missing_facts or []),
                "known_facts": list(state.known_facts or []),
                "confidence": state.confidence,
            }
        )
    except Exception:
        logger.debug("CRM explain qualification failed", exc_info=True)
        return _err("Explain failed", status=500)
