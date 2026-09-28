"""Fan-segment DAO (commerce rebuild Phase 3c). Creator-scoped CRUD.

Backs the dashboard segment API, the evaluator, and context assembly over
``fan_segments``. Name uniqueness per creator via
``UNIQUE(creator_id, LOWER(name))``: creates upsert on the name key.

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3c. Evidence: call sites
in chatbotv2/dashboard/routes/*.py, segments/evaluator.py,
context_engine/gatherer.py, commerce/state.py, memory/context_assembler.py;
tests test_segments.py, test_segment_intelligence.py; docs
PHASE_5_1_IMPLEMENTATION_MAP.md SQL patterns; live staging DDL.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.segments")

_COLUMNS = (
    "id, creator_id, name, description, rules, enabled, "
    "member_count, last_evaluated_at, created_at, updated_at"
)


def _decode(row: Any) -> dict[str, Any]:
    """Row → dict, parsing jsonb `rules` (asyncpg returns it as str)."""
    d = dict(row)
    rules = d.get("rules")
    if isinstance(rules, str):
        try:
            d["rules"] = json.loads(rules)
        except json.JSONDecodeError:
            logger.warning("db.segments: unparseable rules jsonb, keeping raw")
    return d


def _affected_rows(status: str) -> int:
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def create_segment(
    creator_id: int,
    name: str,
    description: str = "",
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a segment; name collisions upsert (description/rules refresh).

    ON CONFLICT (creator_id, LOWER(name)) DO UPDATE — preset re-activation
    converges instead of duplicating.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO fan_segments (creator_id, name, description, rules)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (creator_id, LOWER(name)) DO UPDATE SET
                description = EXCLUDED.description,
                rules = EXCLUDED.rules,
                updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            creator_id,
            name,
            description,
            json.dumps(rules or {}),
        )
        return _decode(row)


async def list_segments(creator_id: int, enabled_only: bool = False) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if enabled_only:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM fan_segments "
                "WHERE creator_id = $1 AND enabled = TRUE "
                "ORDER BY created_at DESC",
                creator_id,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM fan_segments "
                "WHERE creator_id = $1 ORDER BY created_at DESC",
                creator_id,
            )
        return [_decode(r) for r in rows]


async def get_segment(creator_id: int, segment_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM fan_segments WHERE creator_id = $1 AND id = $2",
            creator_id,
            segment_id,
        )
        return _decode(row) if row else None


async def update_segment(creator_id: int, segment_id: int, **updates: Any) -> dict[str, Any] | None:
    """Partial update (name/description/rules). Unknown keys ignored.

    Returns the updated row, or None when nothing matched / nothing to set.
    """
    sets: list[str] = []
    params: list[Any] = [creator_id, segment_id]
    if "name" in updates and updates["name"] is not None:
        params.append(updates["name"])
        sets.append(f"name = ${len(params)}")
    if "description" in updates and updates["description"] is not None:
        params.append(updates["description"])
        sets.append(f"description = ${len(params)}")
    if "rules" in updates and updates["rules"] is not None:
        params.append(json.dumps(updates["rules"]))
        sets.append(f"rules = ${len(params)}::jsonb")
    if not sets:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE fan_segments SET {', '.join(sets)}, updated_at = NOW() "
            "WHERE creator_id = $1 AND id = $2 "
            f"RETURNING {_COLUMNS}",
            *params,
        )
        return _decode(row) if row else None


async def delete_segment(creator_id: int, segment_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM fan_segments WHERE creator_id = $1 AND id = $2",
            creator_id,
            segment_id,
        )
        return _affected_rows(status) > 0


async def toggle_segment(creator_id: int, segment_id: int, enabled: bool) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE fan_segments SET enabled = $3, updated_at = NOW() "
            "WHERE creator_id = $1 AND id = $2 "
            f"RETURNING {_COLUMNS}",
            creator_id,
            segment_id,
            bool(enabled),
        )
        return _decode(row) if row else None


async def duplicate_segment(
    creator_id: int, segment_id: int, new_name: str
) -> dict[str, Any] | None:
    """Copy a segment row under a new name (counts reset by defaults)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO fan_segments
                (creator_id, name, description, rules, enabled)
            SELECT creator_id, $3, description, rules, enabled
            FROM fan_segments
            WHERE creator_id = $1 AND id = $2
            ON CONFLICT (creator_id, LOWER(name)) DO NOTHING
            RETURNING {_COLUMNS}
            """,
            creator_id,
            segment_id,
            new_name,
        )
        return _decode(row) if row else None


async def update_member_count(creator_id: int, segment_id: int, count: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE fan_segments SET member_count = $3, updated_at = NOW() "
            "WHERE creator_id = $1 AND id = $2",
            creator_id,
            segment_id,
            int(count),
        )
        return _affected_rows(status) > 0


async def update_last_evaluated(creator_id: int, segment_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE fan_segments SET last_evaluated_at = NOW(), updated_at = NOW() "
            "WHERE creator_id = $1 AND id = $2",
            creator_id,
            segment_id,
        )
        return _affected_rows(status) > 0


__all__ = [
    "create_segment",
    "delete_segment",
    "duplicate_segment",
    "get_pool",
    "get_segment",
    "list_segments",
    "toggle_segment",
    "update_last_evaluated",
    "update_member_count",
    "update_segment",
]
