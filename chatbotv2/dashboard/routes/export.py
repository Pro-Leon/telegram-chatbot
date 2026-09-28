"""CSV export routes."""

import io
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.csv_helpers import (
    _EXPORT_MAX_ROWS,
    build_activity_csv_rows,
    build_conversations_csv_rows,
    build_detail_csv,
)
from chatbotv2.dashboard.dependencies import (
    validate_attention_filter,
    validate_date_range,
    validate_sort_params,
)
from db.postgres import (
    get_conversation_activity_export_rows,
    get_conversation_detail,
    get_conversations_export_rows,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/analytics/conversations/export")
async def api_export_conversations_csv(
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    sort_by: str = Query(default="last_activity"),
    sort_order: str = Query(default="desc"),
    attention: str | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    assigned_operator_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),  # noqa: B008
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

    try:
        items = await get_conversations_export_rows(
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_order=sort_order,
            attention_filter=attention,
            tag_id=tag_id,
            assigned_operator_id=assigned_operator_id,
            max_rows=_EXPORT_MAX_ROWS,
        )
    except Exception:
        logger.exception("Failed to export conversations CSV")
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Export failed")

    csv_content = build_conversations_csv_rows(items)
    filename = "conversations_export.csv"

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/analytics/conversations/{user_id}/activity/export")
async def api_export_conversation_activity_csv(
    user_id: int,
    auth: dict = Depends(require_auth),  # noqa: B008
):
    try:
        items = await get_conversation_activity_export_rows(
            user_id=user_id,
            max_rows=_EXPORT_MAX_ROWS,
        )
    except Exception:
        logger.exception("Failed to export activity CSV for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Export failed")

    csv_content = build_activity_csv_rows(items)
    filename = f"activity_user_{user_id}.csv"

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/analytics/conversations/{user_id}/export")
async def api_export_conversation_detail_csv(
    user_id: int,
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    auth: dict = Depends(require_auth),  # noqa: B008
):
    err = validate_date_range(start_date, end_date)
    if err:
        return err

    try:
        detail = await get_conversation_detail(user_id, start_date, end_date)
    except Exception:
        logger.exception("Failed to export detail CSV for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Export failed")

    if not detail:
        return JSONResponse(
            {"error": "Conversation not found"},
            status_code=404,
        )

    csv_content = build_detail_csv(detail, user_id)
    filename = f"conversation_{user_id}_detail.csv"

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
