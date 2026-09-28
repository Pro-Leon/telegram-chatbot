"""Full-text search route."""

import logging
import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from db import segments as sdb
from db.postgres import search_conversation_notes, search_messages, search_users
from segments.evaluator import compile_rule
from segments.models import RuleGroup

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/search")
async def api_search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    scope: str = Query(default="all"),
    user_id: int | None = Query(default=None),
    direction: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    segment_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    auth: dict = Depends(require_auth),
):
    import datetime as _dt

    allowed_scope = {"messages", "notes", "users", "all"}
    if scope not in allowed_scope:
        return JSONResponse(
            {"error": f"Invalid scope. Allowed: {', '.join(sorted(allowed_scope))}"},
            status_code=400,
        )

    if direction and direction not in ("inbound", "outbound"):
        return JSONResponse(
            {"error": "direction must be 'inbound' or 'outbound'"},
            status_code=400,
        )

    if date_from:
        try:
            _dt.date.fromisoformat(date_from)
        except ValueError:
            return JSONResponse(
                {"error": "Invalid date_from format. Use YYYY-MM-DD."},
                status_code=400,
            )
    if date_to:
        try:
            _dt.date.fromisoformat(date_to)
        except ValueError:
            return JSONResponse(
                {"error": "Invalid date_to format. Use YYYY-MM-DD."},
                status_code=400,
            )

    # Resolve segment filter if provided
    segment_user_ids = None
    if segment_id is not None:
        ctx = await resolve_single_application_creator()
        if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
            return JSONResponse({"error": "Creator integration not available"}, status_code=503)
        segment = await sdb.get_segment(ctx.creator_id, segment_id)
        if not segment:
            return JSONResponse({"error": "Segment not found"}, status_code=404)
        if not segment.get("enabled", True):
            return JSONResponse({"error": "Segment is disabled"}, status_code=400)
        try:
            rules = RuleGroup.model_validate(segment["rules"])
            where_clause, params = compile_rule(ctx.creator_id, rules)
            from db.postgres import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"SELECT id FROM users u WHERE {where_clause}",
                    *params,
                )
            segment_user_ids = [r["id"] for r in rows]
            if not segment_user_ids:
                return JSONResponse({"query": q, "scope": scope, "items": [], "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}})
        except Exception:
            logger.exception("Failed to resolve segment for search")
            return JSONResponse({"error": "Failed to apply segment filter"}, status_code=400)

    try:
        from typing import Any

        results: dict[str, Any] = {"query": q, "scope": scope, "items": []}

        if scope in ("messages", "all"):
            msg_user_filter = user_id
            if segment_user_ids is not None and user_id is None:
                # For message search, we filter by user_ids in segment
                # This is a limitation - we can only filter by one user_id at a time
                # For now, skip message search if segment filter is set without user_id
                pass
            else:
                # M7 (B6): creator-scoped message search (fail-closed when the
                # creator is unavailable). Notes/users tables have no creator
                # column (structural exceptions, documented).
                _search_ctx = await resolve_single_application_creator()
                _search_creator = (
                    _search_ctx.creator_id
                    if _search_ctx.status == SingleCreatorStatus.READY
                    and _search_ctx.creator_id is not None
                    else None
                )
                if _search_creator is None:
                    if scope == "messages":
                        results["pagination"] = {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
                    pass
                else:
                    msg_result = await search_messages(
                        query=q,
                        user_id=msg_user_filter,
                        direction=direction,
                        date_from=date_from,
                        date_to=date_to,
                        page=page,
                        page_size=page_size if scope == "messages" else 10,
                        creator_id=_search_creator,
                    )
                    for item in msg_result["items"]:
                        item["type"] = "message"
                    results["items"].extend(msg_result["items"])
                    if scope == "messages":
                        results["pagination"] = msg_result["pagination"]

        if scope in ("notes", "all"):
            note_user_filter = user_id
            if segment_user_ids is not None and user_id is None:
                # Similar limitation for notes search
                pass
            else:
                note_result = await search_conversation_notes(
                    query=q,
                    user_id=note_user_filter,
                    page=page,
                    page_size=page_size if scope == "notes" else 10,
                )
                for item in note_result["items"]:
                    item["type"] = "note"
                results["items"].extend(note_result["items"])
                if scope == "notes":
                    results["pagination"] = note_result["pagination"]

        if scope in ("users", "all"):
            user_result = await search_users(
                query=q,
                page=page,
                page_size=page_size if scope == "users" else 10,
            )
            # Filter users by segment if segment_user_ids is provided
            if segment_user_ids is not None:
                segment_set = set(segment_user_ids)
                user_result["items"] = [
                    item for item in user_result["items"]
                    if item.get("id") in segment_set
                ]
                total = len(user_result["items"])
                start = (page - 1) * page_size
                end = start + page_size
                user_result["items"] = user_result["items"][start:end]
                user_result["pagination"] = {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": math.ceil(total / page_size) if page_size > 0 else 0,
                }
            for item in user_result["items"]:
                item["type"] = "user"
            results["items"].extend(user_result["items"])
            if scope == "users":
                results["pagination"] = user_result["pagination"]

        if scope == "all":
            results["items"].sort(key=lambda x: x.get("similarity", 0), reverse=True)
            total = len(results["items"])
            start = (page - 1) * page_size
            end = start + page_size
            results["items"] = results["items"][start:end]
            results["pagination"] = {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if page_size > 0 else 0,
            }

    except Exception:
        logger.exception("Search failed")
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Search computation failed")

    return JSONResponse(results)
