"""AI-native intelligence route — per-dialog AI state for the chat view."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from db.postgres import get_pool

router = APIRouter()


@router.get("/api/dialogs/{dialog_id}/ai-intel")
async def api_dialog_ai_intel(dialog_id: int, auth: dict = Depends(require_auth)):
    # M7 (B6): creator-scoped queue/message reads. The users-table row stays
    # structural (no creator column exists).
    from commerce.single_creator import resolve_single_application_creator

    _creator_id = None
    try:
        _ctx = await resolve_single_application_creator()
        from commerce.single_creator import SingleCreatorStatus

        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    if _creator_id is None:
        return JSONResponse(
            {"suggestions": [], "stats": {"outbound_total": 0, "auto_approved": 0, "edited": 0, "avg_confidence": None, "inbound_total": 0, "total": 0}, "user": {"funnel_stage": None, "is_blocked": False, "do_not_auto_reply": False}, "last_outbound": None}
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Pending suggestions (operator queue) for this dialog
        suggestions = await conn.fetch(
            "SELECT id, draft_content, confidence_score, flags, created_at "
            "FROM operator_queue WHERE user_id = $1 AND creator_id = $2 AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 5",
            dialog_id,
            _creator_id,
        )

        # Outbound AI stats: auto vs manual, avg confidence, flag rate
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE direction = 'outbound')                                  AS outbound_total,
                COUNT(*) FILTER (WHERE direction = 'outbound' AND was_auto_approved = true)    AS auto_approved,
                COUNT(*) FILTER (WHERE direction = 'outbound' AND was_edited = true)           AS edited,
                AVG(confidence_score) FILTER (WHERE direction = 'outbound' AND confidence_score IS NOT NULL) AS avg_confidence,
                COUNT(*) FILTER (WHERE direction = 'inbound')                                  AS inbound_total,
                COUNT(*)                                                                        AS total
            FROM messages WHERE user_id = $1 AND creator_id = $2
            """,
            dialog_id,
            _creator_id,
        )

        # User funnel / block state
        user_row = await conn.fetchrow(
            "SELECT funnel_stage, is_blocked, do_not_auto_reply FROM users WHERE id = $1",
            dialog_id,
        )

        # Last outbound for last-routing hint
        last_out = await conn.fetchrow(
            "SELECT was_auto_approved, was_edited, confidence_score, created_at "
            "FROM messages WHERE user_id = $1 AND creator_id = $2 AND direction = 'outbound' "
            "ORDER BY created_at DESC LIMIT 1",
            dialog_id,
            _creator_id,
        )

    return JSONResponse(
        {
            "suggestions": [
                {
                    "id": r["id"],
                    "draft_content": r["draft_content"],
                    "confidence_score": r["confidence_score"],
                    "flags": r["flags"] if isinstance(r["flags"], list) else [],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in suggestions
            ],
            "stats": {
                "outbound_total": stats["outbound_total"] or 0,
                "auto_approved": stats["auto_approved"] or 0,
                "edited": stats["edited"] or 0,
                "avg_confidence": round(float(stats["avg_confidence"]), 3) if stats["avg_confidence"] is not None else None,
                "inbound_total": stats["inbound_total"] or 0,
                "total": stats["total"] or 0,
            },
            "user": {
                "funnel_stage": user_row["funnel_stage"] if user_row else None,
                "is_blocked": bool(user_row["is_blocked"]) if user_row else False,
                "do_not_auto_reply": bool(user_row["do_not_auto_reply"]) if user_row else False,
            },
            "last_outbound": {
                "was_auto_approved": bool(last_out["was_auto_approved"]) if last_out else None,
                "was_edited": bool(last_out["was_edited"]) if last_out else None,
                "confidence_score": last_out["confidence_score"] if last_out else None,
                "created_at": last_out["created_at"].isoformat() if last_out and last_out["created_at"] else None,
            } if last_out else None,
        }
    )
