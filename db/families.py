"""Content-family DAO (commerce rebuild Phase 3e). Creator-owned groupings.

Thin, non-commercial grouping layer over ``commerce_content_families`` +
``commerce_content_family_members``: a vault item may belong to many
families; families carry no pricing, ranking, or taxonomy semantics
(single-membership flags and inference are out of scope —
see TestFoundationContracts).

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a (DDL) + 3e (DAO).
Evidence: tests/test_p33_content_families.py (CRUD, ownership, multi-family);
db/migrations/20260917000000_p33_content_families.sql; live staging DDL.
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.families")


async def _owned_family_id(conn: Any, creator_id: int, family_id: int) -> int | None:
    """Family id iff it belongs to the creator (deterministic ownership gate)."""
    row = await conn.fetchrow(
        "SELECT id FROM commerce_content_families WHERE creator_id = $1 AND id = $2",
        creator_id,
        family_id,
    )
    return int(row["id"]) if row is not None else None


async def create_content_family(creator_id: int, slug: str, label: str = "") -> dict[str, Any]:
    """Create a family. Slug collisions raise (UNIQUE(creator_id, slug))."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_content_families (creator_id, slug, label)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            creator_id,
            slug,
            label,
        )
        return dict(row)


async def get_content_family(creator_id: int, family_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commerce_content_families WHERE creator_id = $1 AND id = $2",
            creator_id,
            family_id,
        )
        return dict(row) if row else None


async def list_content_families(creator_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM commerce_content_families WHERE creator_id = $1 ORDER BY id ASC",
            creator_id,
        )
        return [dict(r) for r in rows]


async def add_content_family_member(
    creator_id: int, family_id: int, vault_item_id: str
) -> dict[str, Any]:
    """Attach a vault item to a creator-owned family.

    Raises ValueError when the family is not found for the creator;
    duplicate membership raises the DB uniqueness error (both propagate,
    never swallowed).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await _owned_family_id(conn, creator_id, family_id) is None:
            raise ValueError(f"content family {family_id} not found for creator")
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_content_family_members
                (family_id, creator_id, vault_item_id)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            family_id,
            creator_id,
            vault_item_id,
        )
        return dict(row)


async def remove_content_family_member(creator_id: int, family_id: int, vault_item_id: str) -> bool:
    """Detach a vault item. Family row itself is never touched."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await _owned_family_id(conn, creator_id, family_id) is None:
            raise ValueError(f"content family {family_id} not found for creator")
        status = await conn.execute(
            "DELETE FROM commerce_content_family_members "
            "WHERE creator_id = $1 AND family_id = $2 AND vault_item_id = $3",
            creator_id,
            family_id,
            vault_item_id,
        )
        try:
            return int(str(status).split()[-1]) > 0
        except (ValueError, IndexError):
            return False


async def list_family_members(creator_id: int, family_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM commerce_content_family_members "
            "WHERE creator_id = $1 AND family_id = $2 ORDER BY vault_item_id ASC",
            creator_id,
            family_id,
        )
        return [dict(r) for r in rows]


async def get_families_for_vault_item(creator_id: int, vault_item_id: str) -> list[dict[str, Any]]:
    """All creator-owned families containing a vault item (multi-family)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.* FROM commerce_content_families f
            JOIN commerce_content_family_members m
              ON m.family_id = f.id AND m.creator_id = f.creator_id
            WHERE f.creator_id = $1 AND m.vault_item_id = $2
            ORDER BY f.id ASC
            """,
            creator_id,
            vault_item_id,
        )
        return [dict(r) for r in rows]


__all__ = [
    "add_content_family_member",
    "create_content_family",
    "get_content_family",
    "get_families_for_vault_item",
    "get_pool",
    "list_content_families",
    "list_family_members",
    "remove_content_family_member",
]
