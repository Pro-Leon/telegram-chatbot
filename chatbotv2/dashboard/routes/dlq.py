"""Dead Letter Queue management routes."""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from db.redis import (
    cleanup_expired_dlq_entries,
    count_dlq_entries,
    delete_dlq_entry,
    get_dlq_entry,
    list_dlq_entries,
    replay_dlq_entry,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/dlq")
async def api_list_dlq(
    stream: str | None = Query(None, description="Filter by stream (send/inbound)"),
    count: int = Query(50, ge=1, le=500),
    auth: dict = Depends(require_auth),
):
    stream_filter = stream if stream and stream != "all" else None
    entries = await list_dlq_entries(count=count, stream_filter=stream_filter)
    return JSONResponse(jsonable_encoder(entries))


@router.get("/api/dlq/count")
async def api_dlq_count(
    stream: str | None = Query(None, description="Filter by stream (send/inbound)"),
    auth: dict = Depends(require_auth),
):
    stream_filter = stream if stream and stream != "all" else None
    count = await count_dlq_entries(stream_filter=stream_filter)
    return JSONResponse(jsonable_encoder({"count": count}))


@router.get("/api/dlq/{entry_id}")
async def api_get_dlq_entry(entry_id: str, auth: dict = Depends(require_auth)):
    entry = await get_dlq_entry(entry_id)
    if entry is None:
        return JSONResponse({"error": "Entry not found"}, status_code=404)
    return JSONResponse(jsonable_encoder(entry))


@router.post("/api/dlq/{entry_id}/replay")
async def api_replay_dlq_entry(entry_id: str, auth: dict = Depends(require_auth)):
    result = await replay_dlq_entry(entry_id)
    if not result["success"]:
        return JSONResponse(jsonable_encoder(result), status_code=400)
    return JSONResponse(jsonable_encoder(result))


@router.delete("/api/dlq/{entry_id}")
async def api_delete_dlq_entry(entry_id: str, auth: dict = Depends(require_auth)):
    deleted = await delete_dlq_entry(entry_id)
    if not deleted:
        return JSONResponse({"error": "Entry not found"}, status_code=404)
    return JSONResponse(jsonable_encoder({"deleted": True}))


@router.post("/api/dlq/cleanup")
async def api_cleanup_dlq(auth: dict = Depends(require_auth)):
    deleted = await cleanup_expired_dlq_entries()
    return JSONResponse(jsonable_encoder({"deleted": deleted}))
