"""Bulk operations routes (assign, tag, attention)."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import (
    reviewed_at_to_str,
    validate_bulk_user_ids,
)
from chatbotv2.dashboard.schemas import (
    BulkAssignRequest,
    BulkAttentionRequest,
    BulkTagRequest,
)
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from core.event_bus import publish_event
from db import segments as sdb
from db.postgres import (
    assign_conversation_tag,
    get_conversation_tag,
    get_pool,
    upsert_conversation_attention,
)
from segments.evaluator import get_segment_members
from segments.models import RuleGroup

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()

MAX_BULK_USER_IDS = 100


async def _resolve_user_ids(req_user_ids: list[int], segment_id: int | None) -> tuple[list[int], JSONResponse | None]:
    """Resolve effective user_ids from explicit list and/or segment.

    Returns (user_ids_list, error_response_or_None).
    """
    resolved = list(req_user_ids)

    if segment_id is not None:
        ctx = await resolve_single_application_creator()
        if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
            return [], JSONResponse({"error": "Creator integration not available"}, status_code=503)
        segment = await sdb.get_segment(ctx.creator_id, segment_id)
        if not segment:
            return [], JSONResponse({"error": "Segment not found"}, status_code=404)
        if not segment.get("enabled", True):
            return [], JSONResponse({"error": "Segment is disabled"}, status_code=400)
        rules = RuleGroup.model_validate(segment["rules"])
        members = await get_segment_members(ctx.creator_id, rules, limit=MAX_BULK_USER_IDS, offset=0)
        member_ids = [m["id"] for m in members]
        resolved = list(set(resolved + member_ids))

    if not resolved:
        return [], JSONResponse({"error": "user_ids is empty (provide user_ids and/or segment_id)"}, status_code=400)
    if len(resolved) > MAX_BULK_USER_IDS:
        return [], JSONResponse({"error": f"Maximum {MAX_BULK_USER_IDS} user_ids per request (resolved {len(resolved)})"}, status_code=400)
    return resolved, None


@router.post("/api/analytics/conversations/bulk/assign")
async def api_bulk_assign_conversations(
    req: BulkAssignRequest,
    auth: dict = Depends(require_auth),
):
    user_ids, err = await _resolve_user_ids(req.user_ids, req.segment_id)
    if err:
        return err

    if req.assigned_operator_id is not None and req.assigned_operator_id != 0:
        from chatbotv2.dashboard.dependencies import verify_operator_active

        err = await verify_operator_active(req.assigned_operator_id)
        if err:
            return err

    updated = 0
    errors = []
    for uid in user_ids:
        try:
            result = await upsert_conversation_attention(
                user_id=uid,
                assigned_operator_id=req.assigned_operator_id,
                reviewed_by=auth.get("username"),
            )
            updated += 1
            _creator_id = None
            try:
                _ctx = await resolve_single_application_creator()
                if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                    _creator_id = _ctx.creator_id
            except Exception:
                _creator_id = None
            await publish_event(
                "conversation.assigned",
                {
                    "user_id": uid,
                    "assigned_operator_id": result.get("assigned_operator_id"),
                    "assigned_by": auth.get("username"),
                },
                scope="user",
                dialog_id=uid,
                creator_id=_creator_id,
            )
        except Exception:
            logger.exception("Failed to assign user %s", uid)
            errors.append(uid)

    return JSONResponse(
        {
            "updated": updated,
            "errors": errors,
        }
    )


@router.post("/api/analytics/conversations/bulk/tag")
async def api_bulk_tag_conversations(
    req: BulkTagRequest,
    auth: dict = Depends(require_auth),
):
    user_ids, err = await _resolve_user_ids(req.user_ids, req.segment_id)
    if err:
        return err

    tag = await get_conversation_tag(req.tag_id)
    if not tag:
        return JSONResponse({"error": "Tag not found"}, status_code=404)

    assigned = 0
    errors = []
    for uid in user_ids:
        try:
            ok = await assign_conversation_tag(uid, req.tag_id, assigned_by=auth.get("username"))
            if ok:
                assigned += 1
                _creator_id = None
                try:
                    _ctx = await resolve_single_application_creator()
                    if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                        _creator_id = _ctx.creator_id
                except Exception:
                    _creator_id = None
                await publish_event(
                    "conversation.tag_changed",
                    {
                        "user_id": uid,
                        "tag_id": req.tag_id,
                        "tag_name": tag["name"],
                        "action": "assigned",
                        "changed_by": auth.get("username"),
                    },
                    scope="user",
                    dialog_id=uid,
                    creator_id=_creator_id,
                )
        except Exception:
            logger.exception("Failed to tag user %s", uid)
            errors.append(uid)

    return JSONResponse(
        {
            "assigned": assigned,
            "errors": errors,
        }
    )


@router.patch("/api/analytics/conversations/bulk/attention")
async def api_bulk_update_attention(
    req: BulkAttentionRequest,
    auth: dict = Depends(require_auth),
):
    user_ids, err = await _resolve_user_ids(req.user_ids, req.segment_id)
    if err:
        return err
    if req.status not in ("new", "reviewed"):
        return JSONResponse(
            {"error": "Invalid status. Allowed: new, reviewed"},
            status_code=400,
        )

    updated = 0
    errors = []
    for uid in user_ids:
        try:
            result = await upsert_conversation_attention(
                user_id=uid,
                status=req.status,
                reviewed_by=auth.get("username"),
            )
            updated += 1
            _creator_id = None
            try:
                _ctx = await resolve_single_application_creator()
                if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                    _creator_id = _ctx.creator_id
            except Exception:
                _creator_id = None
            await publish_event(
                "conversation.attention_changed",
                {
                    "user_id": uid,
                    "status": result.get("status", req.status),
                    "reviewed_by": result.get("reviewed_by"),
                    "reviewed_at": reviewed_at_to_str(result.get("reviewed_at")),
                    "changed_by": auth.get("username"),
                },
                scope="user",
                dialog_id=uid,
                creator_id=_creator_id,
            )
        except Exception:
            logger.exception("Failed to update attention for user %s", uid)
            errors.append(uid)

    return JSONResponse(
        {
            "updated": updated,
            "errors": errors,
        }
    )
