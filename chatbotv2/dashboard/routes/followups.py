"""Dashboard follow-up management routes (P2.2).

List, detail, cancel, and health-check scheduled follow-up messages.
All data operations are creator-scoped via _require_creator().
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.routes.fangate import _require_creator
from commerce.single_creator import resolve_single_application_creator
from db.postgres import (
    cancel_scheduled_message_rich,
    count_scheduled_messages,
    get_scheduled_message_with_user,
    get_scheduled_stats,
    list_scheduled_messages_with_users,
)

logger = logging.getLogger("chatbotv2.dashboard.followups")

router = APIRouter()
AuthSession = Annotated[dict, Depends(require_auth)]


async def _get_creator_id() -> int:
    """Resolve the single application creator, raising 404 if none configured."""
    ctx = await resolve_single_application_creator()
    if ctx.creator_id is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No creator integration configured")
    return ctx.creator_id


# ── API Routes ──────────────────────────────────────────────────────────────


@router.get("/api/followups")
async def api_followups_list(
    auth: AuthSession,
    status: str | None = Query(default=None, description="Filter by status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List scheduled follow-up messages with user info, creator-scoped."""
    creator_id = await _get_creator_id()
    await _require_creator(creator_id, auth)

    offset = (page - 1) * page_size
    messages = await list_scheduled_messages_with_users(
        creator_id=creator_id,
        status=status,
        limit=page_size,
        offset=offset,
    )
    total = await count_scheduled_messages(creator_id=creator_id, status=status)
    stats = await get_scheduled_stats(creator_id=creator_id)

    # Serialize datetimes
    for msg in messages:
        for key in ("execute_at", "created_at", "updated_at"):
            if msg.get(key):
                msg[key] = msg[key].isoformat()

    return JSONResponse(
        {
            "messages": messages,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, -(-total // page_size)),
            },
            "stats": stats,
        }
    )


@router.get("/api/followups/health")
async def api_followups_health(auth: AuthSession):
    """Health check for scheduled messages subsystem.

    Returns aggregate stats, scheduler worker heartbeat status, and lag info
    (oldest pending execute_at) so operators can determine if the scheduler
    is alive, processing jobs, and keeping up.
    """
    creator_id = await _get_creator_id()
    await _require_creator(creator_id, auth)
    stats = await get_scheduled_stats(creator_id=creator_id)

    failed = stats.get("failed", 0)
    pending = stats.get("pending", 0) + stats.get("processing", 0)

    if failed > 0 and failed >= pending:
        health = "degraded"
    elif failed > 0:
        health = "warning"
    else:
        health = "healthy"

    # Check scheduler worker heartbeat
    scheduler_status = None
    try:
        from core.worker_heartbeat import read_worker_status

        scheduler_status = await read_worker_status("scheduler_1")
    except Exception:
        logger.debug("Could not read scheduler heartbeat", exc_info=True)

    if scheduler_status:
        age = scheduler_status.get("age_seconds", 999)
        ttl = scheduler_status.get("ttl_seconds", 0)
        if ttl <= 0:
            scheduler_health = "dead"
        elif age > 60:
            scheduler_health = "stale"
        else:
            scheduler_health = "alive"
    else:
        scheduler_health = "unknown"

    # Compute lag: oldest pending execute_at
    lag_info = await _compute_lag(creator_id)

    return JSONResponse(
        {
            "status": health,
            "stats": stats,
            "scheduler": {
                "health": scheduler_health,
                "heartbeat": scheduler_status,
            },
            "lag": lag_info,
        }
    )


async def _compute_lag(creator_id: int) -> dict:
    """Compute scheduler lag from oldest pending execute_at."""
    from datetime import datetime, timezone

    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MIN(execute_at) AS oldest_pending, COUNT(*) AS pending_count "
            "FROM scheduled_messages "
            "WHERE status = 'pending' AND creator_id = $1",
            creator_id,
        )
        if not row or row["oldest_pending"] is None:
            return {"oldest_pending": None, "lag_seconds": 0, "pending_count": 0}

        now = datetime.now(timezone.utc)
        oldest = row["oldest_pending"]
        lag = max(0, (now - oldest).total_seconds())
        return {
            "oldest_pending": oldest.isoformat(),
            "lag_seconds": round(lag, 1),
            "pending_count": row["pending_count"],
        }


@router.get("/api/followups/{message_id}")
async def api_followups_detail(message_id: int, auth: AuthSession):
    """Fetch a single scheduled follow-up with user info."""
    creator_id = await _get_creator_id()
    await _require_creator(creator_id, auth)

    msg = await get_scheduled_message_with_user(message_id, creator_id=creator_id)
    if msg is None:
        return JSONResponse(
            {"error": "NotFound", "message": "Scheduled message not found"},
            status_code=404,
        )

    # Serialize datetimes
    for key in (
        "execute_at",
        "created_at",
        "updated_at",
        "claimed_at",
        "completed_at",
        "failed_at",
    ):
        if msg.get(key):
            msg[key] = msg[key].isoformat()

    return JSONResponse({"message": msg})


@router.post("/api/followups/{message_id}/cancel")
async def api_followups_cancel(message_id: int, auth: AuthSession):
    """Cancel a scheduled follow-up message."""
    creator_id = await _get_creator_id()
    await _require_creator(creator_id, auth)

    result = await cancel_scheduled_message_rich(message_id, creator_id=creator_id)
    status_code = 200 if result["cancelled"] else 409
    # M7 (B1/B4/B5): durable cancellation record with the server-derived
    # human actor (previously the cancelling operator was stored nowhere —
    # scheduled_messages has no cancelled_by column).
    try:
        from core.audit import actor_from_auth, record_audit_event

        await record_audit_event(
            event_type="cancel" if result["cancelled"] else "cancel_raced",
            actor=actor_from_auth(auth),
            creator_id=creator_id,
            action="POST /api/followups/{id}/cancel",
            schedule_id=message_id,
            state_before=result.get("status"),
            state_after="cancelled" if result["cancelled"] else result.get("status"),
            result="cancelled" if result["cancelled"] else "conflict",
            error=None if result["cancelled"] else result.get("reason"),
        )
    except Exception:
        pass
    return JSONResponse(result, status_code=status_code)
