"""Stats and settings routes."""

from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from db.postgres import get_pool
from db.redis import (
    DLQ_STREAM,
    INBOUND_STREAM,
    get_redis,
    is_auto_reply_enabled,
    set_auto_reply_enabled,
)

router = APIRouter()


@router.get("/api/stats")
async def api_stats(auth: dict = Depends(require_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")
        pending_queue = await conn.fetchval(
            "SELECT COUNT(*) FROM operator_queue WHERE status = 'pending'"
        )
        blocked_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE")

    r = await get_redis()
    pending_inbound = await r.xlen(INBOUND_STREAM)
    dlq_count = await r.xlen(DLQ_STREAM)

    return JSONResponse(
        {
            "total_users": total_users,
            "total_messages": total_messages,
            "pending_queue": pending_queue,
            "blocked_users": blocked_users,
            "pending_inbound": pending_inbound,
            "dlq_count": dlq_count,
        }
    )


@router.get("/api/settings/auto-reply")
async def api_get_auto_reply(auth: dict = Depends(require_auth)):
    enabled = await is_auto_reply_enabled()
    return JSONResponse({"auto_reply_enabled": enabled})


@router.post("/api/settings/auto-reply")
async def api_set_auto_reply(
    enabled: bool = Form(...),
    auth: dict = Depends(require_auth),
):
    await set_auto_reply_enabled(enabled)
    return JSONResponse({"auto_reply_enabled": enabled})
