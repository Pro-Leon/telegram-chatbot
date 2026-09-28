"""Conversation attention and assignment routes."""

import logging

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import reviewed_at_to_str, verify_operator_active
from chatbotv2.dashboard.schemas import AssignmentUpdateRequest, AttentionUpdateRequest
from core.event_bus import publish_event
from db.postgres import upsert_conversation_attention

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.patch("/api/analytics/conversations/{user_id}/attention")
async def api_update_conversation_attention(
    user_id: int,
    req: AttentionUpdateRequest,
    auth: dict = Depends(require_auth),
):
    if req.status is not None and req.status not in ("new", "reviewed"):
        return JSONResponse(
            {"error": "Invalid status. Allowed: new, reviewed"},
            status_code=400,
        )
    try:
        result = await upsert_conversation_attention(
            user_id=user_id,
            status=req.status,
            reviewed_by=auth.get("username"),
        )
    except Exception:
        logger.exception("Failed to update attention for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to update attention")

    if req.status is not None:
        _creator_id = None
        try:
            from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
            _ctx = await resolve_single_application_creator()
            if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                _creator_id = _ctx.creator_id
        except Exception:
            _creator_id = None
        await publish_event(
            "conversation.attention_changed",
            {
                "user_id": user_id,
                "status": result.get("status", req.status),
                "reviewed_by": result.get("reviewed_by"),
                "reviewed_at": reviewed_at_to_str(result.get("reviewed_at")),
            },
            scope="user",
            dialog_id=user_id,
            creator_id=_creator_id,
        )

    return JSONResponse(jsonable_encoder(result))


@router.patch("/api/analytics/conversations/{user_id}/assignment")
async def api_update_conversation_assignment(
    user_id: int,
    req: AssignmentUpdateRequest,
    auth: dict = Depends(require_auth),
):
    err = await verify_operator_active(req.assigned_operator_id)
    if err:
        return err

    try:
        result = await upsert_conversation_attention(
            user_id=user_id,
            assigned_operator_id=req.assigned_operator_id,
            reviewed_by=auth.get("username"),
        )
    except Exception:
        logger.exception("Failed to update assignment for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to update assignment")

    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.assigned",
        {
            "user_id": user_id,
            "assigned_operator_id": req.assigned_operator_id,
            "assigned_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse(jsonable_encoder(result))
