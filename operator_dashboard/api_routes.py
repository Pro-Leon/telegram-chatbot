
from fastapi import APIRouter, HTTPException

from db.postgres import (
    get_pending_queue_items,
    get_queue_item,
    get_recent_messages,
    resolve_queue_item,
    save_outbound_message,
)
from ingestion.bot import send_message

router = APIRouter(prefix="/api")


@router.get("/queue")
async def get_queue(limit: int = 20):
    items = await get_pending_queue_items(limit=limit)
    return [
        {
            "id": item["id"],
            "user_id": item["user_id"],
            "draft_content": item["draft_content"],
            "confidence_score": item["confidence_score"],
            "flags": item.get("flags", []),
            "status": item["status"],
            "created_at": item["created_at"].isoformat() if item["created_at"] else None,
        }
        for item in items
    ]


@router.post("/queue/{queue_id}/approve")
async def approve_message(queue_id: int):
    item = await get_queue_item(queue_id)
    if not item or item.get("status") != "pending":
        raise HTTPException(status_code=404, detail="Queue item not found or already handled")

    sent = await send_message(item["user_id"], item["draft_content"])
    await save_outbound_message(
        user_id=item["user_id"],
        content=item["draft_content"],
        draft_content=item["draft_content"],
        was_edited=False,
        was_auto_approved=False,
        confidence_score=item["confidence_score"],
        operator_id=int(item.get("assigned_to") or 0),
        telegram_message_id=sent.message_id,
    )
    await resolve_queue_item(queue_id, "approved")
    return {"status": "ok", "message_id": sent.message_id}


@router.post("/queue/{queue_id}/reject")
async def reject_message(queue_id: int):
    item = await get_queue_item(queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    await resolve_queue_item(queue_id, "rejected")
    return {"status": "ok"}


@router.post("/queue/{queue_id}/edit")
async def edit_message(queue_id: int, body: dict):
    item = await get_queue_item(queue_id)
    if not item or item.get("status") != "pending":
        raise HTTPException(status_code=404, detail="Queue item not found or already handled")

    new_content = body.get("content", "")
    if not new_content:
        raise HTTPException(status_code=400, detail="Content is required")

    sent = await send_message(item["user_id"], new_content)
    await save_outbound_message(
        user_id=item["user_id"],
        content=new_content,
        draft_content=item["draft_content"],
        was_edited=True,
        was_auto_approved=False,
        confidence_score=item["confidence_score"],
        operator_id=int(item.get("assigned_to") or 0),
        telegram_message_id=sent.message_id,
    )
    await resolve_queue_item(queue_id, "edited", final_content=new_content)
    return {"status": "ok", "message_id": sent.message_id}


@router.get("/messages/recent")
async def recent_messages(limit: int = 50):
    rows = await get_recent_messages(0, limit=limit)
    return [
        {
            "direction": m["direction"],
            "content": m["content"],
            "created_at": m["created_at"].isoformat() if m["created_at"] else None,
        }
        for m in rows
    ]


@router.get("/stats")
async def get_stats():
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")
        pending_queue = await conn.fetchval(
            "SELECT COUNT(*) FROM operator_queue WHERE status = 'pending'"
        )
        auto_approved = await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE was_auto_approved = true"
        )
    return {
        "total_users": total_users,
        "total_messages": total_messages,
        "pending_queue": pending_queue,
        "auto_approved": auto_approved,
    }
