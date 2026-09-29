"""Offer-definition catalog DAO (commerce rebuild Phase 3a).

Deterministic offer catalog: stable_key + versioned definitions mapping to
DropFans CUIDs. Creator-scoped throughout; lifecycle draft -> active ->
retired only (price/ids immutable post-create). No seeding, no taxonomy,
historical fill in this module (suite-asserted prohibitions).

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a.
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.offer_definitions")

OFFER_TYPES = frozenset({"SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"})

_VALID_STATUSES = ("draft", "active", "retired")


def _canonical_ids(vault_item_ids: list[str]) -> list[str]:
    seen = sorted({str(v).strip() for v in (vault_item_ids or []) if str(v).strip()})
    if not 1 <= len(seen) <= 10:
        raise ValueError("vault_item_ids must hold 1..10 canonical ids")
    return seen


async def create_offer_definition(
    creator_id: int,
    stable_key: str,
    offer_type: str,
    vault_item_ids: list[str],
    price_minor: int,
    currency: str,
    allow_download: bool,
    *,
    version: int = 1,
    status: str = "draft",
    family_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import json

    if not stable_key or not stable_key.strip():
        raise ValueError("stable_key required")
    if offer_type not in OFFER_TYPES:
        raise ValueError(f"offer_type must be one of {sorted(OFFER_TYPES)}")
    if not isinstance(price_minor, int) or isinstance(price_minor, bool) or price_minor < 0:
        raise ValueError("price_minor must be a non-negative int")
    if status not in _VALID_STATUSES:
        raise ValueError("status must be draft|active|retired")
    canonical = _canonical_ids(vault_item_ids)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if family_id is not None:
            owner = await conn.fetchrow(
                "SELECT id FROM commerce_content_families "
                "WHERE id = $1 AND creator_id = $2",
                family_id,
                creator_id,
            )
            if owner is None:
                raise ValueError("family_id not found for creator")
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_offer_definitions
                (creator_id, stable_key, offer_type, version,
                 canonical_vault_item_ids, family_id, price_minor,
                 currency, allow_download, status, config)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
            RETURNING *
            """,
            creator_id,
            stable_key.strip(),
            offer_type,
            version,
            canonical,
            family_id,
            price_minor,
            currency,
            bool(allow_download),
            status,
            json.dumps(config) if config is not None else None,
        )
        return dict(row)


async def get_offer_definition(
    creator_id: int, definition_id: int, version: int | None = None
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if version is None:
            row = await conn.fetchrow(
                "SELECT * FROM commerce_offer_definitions "
                "WHERE creator_id = $1 AND id = $2 ORDER BY version DESC LIMIT 1",
                creator_id,
                definition_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM commerce_offer_definitions "
                "WHERE creator_id = $1 AND id = $2 AND version = $3",
                creator_id,
                definition_id,
                version,
            )
        return dict(row) if row else None


async def get_offer_definition_by_key(
    creator_id: int, stable_key: str, version: int
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commerce_offer_definitions "
            "WHERE creator_id = $1 AND stable_key = $2 AND version = $3",
            creator_id,
            stable_key,
            version,
        )
        return dict(row) if row else None


async def list_offer_definitions(
    creator_id: int, status: str | None = None
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status is None:
            rows = await conn.fetch(
                "SELECT * FROM commerce_offer_definitions "
                "WHERE creator_id = $1 ORDER BY id ASC",
                creator_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM commerce_offer_definitions "
                "WHERE creator_id = $1 AND status = $2 ORDER BY id ASC",
                creator_id,
                status,
            )
        return [dict(r) for r in rows]


async def list_offer_definition_drops(
    creator_id: int, definition_id: int | None = None
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if definition_id is None:
            rows = await conn.fetch(
                "SELECT definition_id, definition_version, creator_id, "
                "dropfans_product_id, created_at "
                "FROM commerce_offer_definition_drops "
                "WHERE creator_id = $1 ORDER BY definition_id ASC",
                creator_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT definition_id, definition_version, creator_id, "
                "dropfans_product_id, created_at "
                "FROM commerce_offer_definition_drops "
                "WHERE creator_id = $1 AND definition_id = $2 ORDER BY definition_id ASC",
                creator_id,
                definition_id,
            )
        return [dict(r) for r in rows]


async def map_offer_definition_drop(
    creator_id: int,
    definition_id: int,
    definition_version: int,
    dropfans_product_id: str,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchrow(
            "SELECT id FROM commerce_offer_definitions "
            "WHERE id = $1 AND creator_id = $2 AND version = $3",
            definition_id,
            creator_id,
            definition_version,
        )
        if owner is None:
            raise ValueError("definition not found for creator")
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_offer_definition_drops
                (definition_id, definition_version, creator_id, dropfans_product_id)
            VALUES ($1,$2,$3,$4)
            RETURNING definition_id, definition_version, creator_id,
                      dropfans_product_id, created_at
            """,
            definition_id,
            definition_version,
            creator_id,
            dropfans_product_id,
        )
        return dict(row)


async def activate_offer_definition(
    creator_id: int, definition_id: int
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE commerce_offer_definitions SET status = 'active', "
            "updated_at = NOW() WHERE creator_id = $1 AND id = $2 "
            "RETURNING *",
            creator_id,
            definition_id,
        )
        if row is None:
            raise ValueError("definition not found for creator")
        return dict(row)


async def retire_offer_definition(
    creator_id: int, definition_id: int
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE commerce_offer_definitions SET status = 'retired', "
            "updated_at = NOW() WHERE creator_id = $1 AND id = $2 "
            "RETURNING *",
            creator_id,
            definition_id,
        )
        if row is None:
            raise ValueError("definition not found for creator")
        return dict(row)


__all__ = [
    "OFFER_TYPES",
    "activate_offer_definition",
    "create_offer_definition",
    "get_offer_definition",
    "get_offer_definition_by_key",
    "get_pool",
    "list_offer_definition_drops",
    "list_offer_definitions",
    "map_offer_definition_drop",
    "retire_offer_definition",
]
