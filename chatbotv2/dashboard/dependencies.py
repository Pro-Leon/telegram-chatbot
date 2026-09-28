"""Shared validation helpers for dashboard routes."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from fastapi.responses import JSONResponse

from db.postgres import get_pool


def validate_date_range(
    start_date: str | None,
    end_date: str | None,
) -> JSONResponse | None:
    """Validate date range parameters. Returns JSONResponse on error, None on success."""
    if start_date:
        try:
            _dt.date.fromisoformat(start_date)
        except ValueError:
            return JSONResponse(
                {"error": "Invalid start_date format. Use YYYY-MM-DD."},
                status_code=400,
            )
    if end_date:
        try:
            _dt.date.fromisoformat(end_date)
        except ValueError:
            return JSONResponse(
                {"error": "Invalid end_date format. Use YYYY-MM-DD."},
                status_code=400,
            )
    if start_date and end_date and start_date > end_date:
        return JSONResponse(
            {"error": "start_date must be before or equal to end_date."},
            status_code=400,
        )
    if start_date and end_date:
        s = _dt.date.fromisoformat(start_date)
        e = _dt.date.fromisoformat(end_date)
        if (e - s).days > 365:
            return JSONResponse(
                {"error": "Date range must not exceed 365 days."},
                status_code=400,
            )
    return None


def validate_sort_params(sort_by: str, sort_order: str) -> JSONResponse | None:
    """Validate sort parameters. Returns JSONResponse on error, None on success."""
    allowed_sort = {
        "last_activity",
        "message_count",
        "response_time",
        "queue_count",
        "confidence",
        "user_id",
    }
    if sort_by not in allowed_sort:
        return JSONResponse(
            {"error": f"Invalid sort_by. Allowed: {', '.join(sorted(allowed_sort))}"},
            status_code=400,
        )
    if sort_order.lower() not in ("asc", "desc"):
        return JSONResponse(
            {"error": "sort_order must be 'asc' or 'desc'."},
            status_code=400,
        )
    return None


def validate_attention_filter(attention: str | None) -> JSONResponse | None:
    """Validate attention filter parameter. Returns JSONResponse on error, None on success."""
    allowed_attention = {"needs_attention", "quiet", None}
    if attention not in allowed_attention:
        return JSONResponse(
            {"error": "Invalid attention filter. Allowed: needs_attention, quiet"},
            status_code=400,
        )
    return None


def validate_bulk_user_ids(user_ids: list[int]) -> JSONResponse | None:
    """Validate bulk operation user_ids. Returns JSONResponse on error, None on success."""
    if not user_ids:
        return JSONResponse({"error": "user_ids is empty"}, status_code=400)
    if len(user_ids) > 100:
        return JSONResponse({"error": "Maximum 100 user_ids per request"}, status_code=400)
    return None


def validate_note_content(content: str | None) -> tuple[str, JSONResponse | None]:
    """Validate and strip note content. Returns (stripped_content, error_response_or_None)."""
    content = (content or "").strip()
    if not content:
        return content, JSONResponse(
            {"error": "Note content cannot be empty"},
            status_code=400,
        )
    if len(content) > 2000:
        return content, JSONResponse(
            {"error": "Note content must be 2000 characters or fewer"},
            status_code=400,
        )
    return content, None


def validate_tag_name(
    name: str | None, description: str | None
) -> tuple[str, str | None, JSONResponse | None]:
    """Validate and strip tag name/description. Returns (name, description, error_or_None)."""
    name = (name or "").strip()
    if not name:
        return (
            name,
            None,
            JSONResponse(
                {"error": "Tag name cannot be empty"},
                status_code=400,
            ),
        )
    if len(name) > 50:
        return (
            name,
            None,
            JSONResponse(
                {"error": "Tag name must be 50 characters or fewer"},
                status_code=400,
            ),
        )
    description = (description or "").strip() if description else None
    if description and len(description) > 200:
        return (
            name,
            description,
            JSONResponse(
                {"error": "Tag description must be 200 characters or fewer"},
                status_code=400,
            ),
        )
    return name, description, None


async def verify_user_exists(user_id: int) -> dict[str, Any] | JSONResponse:
    """Fetch user row or return 404 JSONResponse. Returns dict on success."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, first_name, message_count, funnel_stage, is_blocked, notes FROM users WHERE id = $1",
            user_id,
        )
        if not row:
            return JSONResponse({"error": "User not found"}, status_code=404)
        return dict(row)


async def verify_user_id_exists(user_id: int) -> JSONResponse | None:
    """Check user exists by ID only. Returns None on success, JSONResponse 404 on failure."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchval("SELECT id FROM users WHERE id = $1", user_id)
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
    return None


async def verify_operator_active(operator_id: int) -> JSONResponse | None:
    """Check operator is active. Returns None on success, JSONResponse 400 on failure."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        op = await conn.fetchrow(
            "SELECT id FROM operators WHERE id = $1 AND is_active = TRUE",
            operator_id,
        )
        if not op:
            return JSONResponse(
                {"error": "Operator not found or inactive"},
                status_code=400,
            )
    return None


def reviewed_at_to_str(reviewed_at_raw: Any) -> str | None:
    """Convert reviewed_at value to ISO string."""
    if hasattr(reviewed_at_raw, "isoformat"):
        return reviewed_at_raw.isoformat()
    if reviewed_at_raw is not None:
        return str(reviewed_at_raw)
    return None
