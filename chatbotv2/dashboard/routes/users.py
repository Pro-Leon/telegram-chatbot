"""User management routes."""

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import verify_user_exists
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from db import segments as sdb
from db.postgres import (
    get_pool,
    get_user_analytics,
    get_user_message_timeline,
    is_user_auto_reply_excluded,
    set_user_auto_reply_exclusion,
)
from segments.evaluator import compile_rule, explain_user_segments
from segments.models import RuleGroup

router = APIRouter()


@router.get("/api/user/{user_id}")
async def api_get_user(user_id: int, auth: dict = Depends(require_auth)):
    result = await verify_user_exists(user_id)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(jsonable_encoder(result))


@router.get("/api/users")
async def api_users(
    limit: int = Query(default=50),
    offset: int = Query(default=0),
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
        # M7 (B6): same creator-scoped membership as /api/dialogs (see above).
        ctx = await resolve_single_application_creator()
        if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
            return JSONResponse({"error": "Creator integration not available"}, status_code=503)
        where_clause = (
            "WHERE (EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id AND m.creator_id = $1) "
            "OR EXISTS (SELECT 1 FROM operator_queue q WHERE q.user_id = u.id AND q.creator_id = $1)) "
        )
        params = [ctx.creator_id]

    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, username, first_name, message_count, last_seen FROM users u {where_clause}ORDER BY last_seen DESC LIMIT ${limit_idx} OFFSET ${offset_idx}",
            *params, limit, offset,
        )
        return JSONResponse(jsonable_encoder([dict(r) for r in rows]))


@router.post("/api/user/{user_id}/block")
async def api_block_user(user_id: int, auth: dict = Depends(require_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_blocked = TRUE WHERE id = $1", user_id)
    return JSONResponse({"ok": True})


@router.post("/api/user/{user_id}/unblock")
async def api_unblock_user(user_id: int, auth: dict = Depends(require_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_blocked = FALSE WHERE id = $1", user_id)
    return JSONResponse({"ok": True})


@router.post("/api/user/{user_id}/auto-reply")
async def api_set_auto_reply_exclusion(
    user_id: int, enabled: bool = Form(...), auth: dict = Depends(require_auth)
):
    await set_user_auto_reply_exclusion(user_id, exclude=enabled)
    return JSONResponse({"ok": True})


@router.get("/api/user/{user_id}/auto-reply")
async def api_get_auto_reply_exclusion(user_id: int, auth: dict = Depends(require_auth)):
    excluded = await is_user_auto_reply_excluded(user_id)
    return JSONResponse({"do_not_auto_reply": excluded})


@router.get("/api/user/{user_id}/analytics")
async def api_user_analytics(
    user_id: int,
    auth: dict = Depends(require_auth),
):
    # M7 (B6): creator-scoped message stats/timeline. The users-table row
    # itself stays structural (no creator column exists).
    _analytics_ctx = await resolve_single_application_creator()
    _analytics_creator = (
        _analytics_ctx.creator_id
        if _analytics_ctx.status == SingleCreatorStatus.READY
        and _analytics_ctx.creator_id is not None
        else None
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, username, first_name, last_seen, is_blocked FROM users WHERE id = $1",
            user_id,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

    analytics = await get_user_analytics(user_id, creator_id=_analytics_creator)
    timeline = await get_user_message_timeline(user_id, days=30, creator_id=_analytics_creator)
    return JSONResponse(
        jsonable_encoder({"user": dict(user), "analytics": analytics, "timeline": timeline})
    )


@router.post("/api/user/{user_id}/notes")
async def api_update_user_notes(
    user_id: int,
    notes: str = Form(...),
    auth: dict = Depends(require_auth),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET notes = $1 WHERE id = $2", notes, user_id)
    return JSONResponse({"ok": True})


@router.get("/api/user/{user_id}/segments")
async def api_user_segments(user_id: int, auth: dict = Depends(require_auth)):
    """Return all enabled segments with membership status for this user."""
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        return JSONResponse([], status_code=200)

    segments = await sdb.list_segments(ctx.creator_id, enabled_only=True)
    results = []
    for seg in segments:
        rules = seg.get("rules")
        if not rules:
            continue
        rule = RuleGroup.model_validate(rules)
        where_clause, params = compile_rule(ctx.creator_id, rule)
        uid_idx = len(params) + 1
        pool = await get_pool()
        async with pool.acquire() as conn:
            is_member = await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM users u WHERE u.id = ${uid_idx} AND {where_clause})",
                *params, user_id,
            )
        results.append({
            "id": seg["id"],
            "name": seg["name"],
            "is_member": bool(is_member),
            "member_count": seg.get("member_count", 0),
        })
    return JSONResponse(results)


@router.get("/api/user/{user_id}/segments/explain")
async def api_user_segments_explain(user_id: int, auth: dict = Depends(require_auth)):
    """Return detailed explanation of all segment memberships for a user."""
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        return JSONResponse({"user_id": user_id, "segments": []}, status_code=200)

    results = await explain_user_segments(ctx.creator_id, user_id)
    return JSONResponse({"user_id": user_id, "segments": results})


@router.get("/api/users/segments-batch")
async def api_users_segments_batch(
    user_ids: str = Query(default=""),
    auth: dict = Depends(require_auth),
):
    """Return segment memberships for multiple users in one call.

    Accepts a comma-separated list of user IDs. Returns a dict mapping
    each user ID to its segment membership list.
    """
    if not user_ids:
        return JSONResponse({})

    try:
        ids = [int(x.strip()) for x in user_ids.split(",") if x.strip()]
    except ValueError:
        return JSONResponse({"error": "Invalid user_ids format"}, status_code=400)

    if len(ids) > 100:
        return JSONResponse({"error": "Maximum 100 user_ids per request"}, status_code=400)

    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        return JSONResponse({str(uid): [] for uid in ids})

    segments = await sdb.list_segments(ctx.creator_id, enabled_only=True)
    result: dict[int, list] = {uid: [] for uid in ids}

    for seg in segments:
        rules = seg.get("rules")
        if not rules:
            continue
        rule = RuleGroup.model_validate(rules)
        where_clause, params = compile_rule(ctx.creator_id, rule)
        pool = await get_pool()
        async with pool.acquire() as conn:
            for uid in ids:
                uid_idx = len(params) + 1
                is_member = await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM users u WHERE u.id = ${uid_idx} AND {where_clause})",
                    *params, uid,
                )
                if is_member:
                    result[uid].append({
                        "id": seg["id"],
                        "name": seg["name"],
                        "is_member": True,
                    })

    return JSONResponse({str(k): v for k, v in result.items()})
