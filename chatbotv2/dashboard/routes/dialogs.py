"""Dialog listing and history routes."""

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from db import segments as sdb
from db.postgres import get_pool
from segments.evaluator import compile_rule
from segments.models import RuleGroup

router = APIRouter()


@router.get("/api/dialogs")
async def api_dialogs(
    segment_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    pool = await get_pool()

    where_clause = ""
    params: list = []

    if segment_id is not None:
        ctx = await resolve_single_application_creator()
        if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
            return JSONResponse({"error": "Creator integration not available"}, status_code=503)
        segment = await sdb.get_segment(ctx.creator_id, segment_id)
        if not segment:
            return JSONResponse({"error": "Segment not found"}, status_code=404)
        if not segment.get("is_enabled", True):
            return JSONResponse({"error": "Segment is disabled"}, status_code=400)
        rules = RuleGroup.model_validate(segment["rules"])
        where_clause, params = compile_rule(ctx.creator_id, rules)
        where_clause = f"WHERE {where_clause} "
    else:
        # M7 (B6): the unsegmented list was a global users read. Scope
        # membership to fans with activity under the resolved creator
        # (fail-closed when the creator is unavailable).
        ctx = await resolve_single_application_creator()
        if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
            return JSONResponse({"error": "Creator integration not available"}, status_code=503)
        where_clause = (
            "WHERE (EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id AND m.creator_id = $1) "
            "OR EXISTS (SELECT 1 FROM operator_queue q WHERE q.user_id = u.id AND q.creator_id = $1)) "
        )
        params = [ctx.creator_id]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, username, first_name, message_count, last_seen FROM users u {where_clause}ORDER BY last_seen DESC",
            *params,
        )
    return JSONResponse(jsonable_encoder([dict(r) for r in rows]))


@router.get("/api/dialogs/{dialog_id}/history")
async def api_dialog_history(
    dialog_id: int,
    limit: int = Query(default=50),
    auth: dict = Depends(require_auth),
):
    from fastapi import HTTPException

    # M4 D3: dialog history is creator-dependent — scope to the session
    # creator and fail closed when creator identity is unavailable.
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, direction, content, draft_content, was_edited, "
            "was_auto_approved, confidence_score, sent_at, created_at "
            "FROM messages WHERE user_id = $1 AND creator_id = $2 ORDER BY created_at DESC LIMIT $3",
            dialog_id,
            ctx.creator_id,
            limit,
        )
    return JSONResponse(
        [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "direction": r["direction"],
                "content": r["content"],
                "draft_content": r["draft_content"],
                "was_edited": r["was_edited"],
                "was_auto_approved": r["was_auto_approved"],
                "confidence_score": r["confidence_score"],
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )
