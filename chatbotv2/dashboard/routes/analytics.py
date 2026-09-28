"""Analytics dashboard and conversation detail routes."""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import (
    validate_attention_filter,
    validate_date_range,
    validate_sort_params,
)
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from db import segments as sdb
from db.postgres import (
    get_conversation_activity,
    get_conversation_detail,
    get_conversations_analytics,
    get_dashboard_analytics,
)
from segments.evaluator import get_segment_members
from segments.models import RuleGroup

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/analytics/dashboard")
async def api_analytics_dashboard(
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    err = validate_date_range(start_date, end_date)
    if err:
        return err

    try:
        analytics = await get_dashboard_analytics(
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        logger.exception("Failed to compute dashboard analytics")
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Analytics computation failed")

    effective_start = start_date or "all time"
    effective_end = end_date or "now"

    return JSONResponse(
        {
            "period": {
                "start": effective_start,
                "end": effective_end,
            },
            **analytics,
        }
    )


@router.get("/api/analytics/conversations")
async def api_analytics_conversations(
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort_by: str = Query(default="last_activity"),
    sort_order: str = Query(default="desc"),
    attention: str | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    assigned_operator_id: int | None = Query(default=None),
    segment_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    err = validate_date_range(start_date, end_date)
    if err:
        return err
    err = validate_sort_params(sort_by, sort_order)
    if err:
        return err
    err = validate_attention_filter(attention)
    if err:
        return err

    resolved_user_ids = None
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
        members = await get_segment_members(ctx.creator_id, rules, limit=10000, offset=0)
        resolved_user_ids = [m["id"] for m in members]
        if not resolved_user_ids:
            return JSONResponse({"items": [], "pagination": {"page": 1, "page_size": page_size, "total": 0, "total_pages": 0}})

    try:
        result = await get_conversations_analytics(
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            attention_filter=attention,
            tag_id=tag_id,
            assigned_operator_id=assigned_operator_id,
            user_ids=resolved_user_ids,
        )
    except Exception:
        logger.exception("Failed to compute conversations analytics")
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Analytics computation failed")

    return JSONResponse(result)


@router.get("/api/analytics/conversations/{user_id}")
async def api_analytics_conversation_detail(
    user_id: int,
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    err = validate_date_range(start_date, end_date)
    if err:
        return err

    try:
        result = await get_conversation_detail(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        logger.exception("Failed to compute conversation detail for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Analytics computation failed")

    if result is None:
        return JSONResponse(
            {"error": "User not found"},
            status_code=404,
        )

    return JSONResponse(result)


@router.get("/api/analytics/conversations/{user_id}/activity")
async def api_conversation_activity(
    user_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    auth: dict = Depends(require_auth),
):
    try:
        result = await get_conversation_activity(
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
    except Exception:
        logger.exception("Failed to fetch activity for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Activity fetch failed")

    return JSONResponse(result)
