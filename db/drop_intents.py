"""Drop-intent DAO (commerce rebuild Phase 3a). Crash-window convergence.

Tracks DropFans create-post attempts so retries converge instead of
duplicating: pending -> active | failed, failed rearmable to pending.
Terminal active rows never transition. Creator-scoped throughout.

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a.
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.drop_intents")


async def get_or_create_intent(
    creator_id: int,
    *,
    content_key: str,
    canonical_vault_item_ids: list[str],
    price_minor: int,
    currency: str = "USD",
    allow_download: bool = False,
) -> tuple[dict[str, Any], bool]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO dropfans_drop_intents
                (creator_id, content_key, canonical_vault_item_ids,
                 price_minor, currency, allow_download, status)
            VALUES ($1,$2,$3,$4,$5,$6,'pending')
            ON CONFLICT (creator_id, content_key) DO NOTHING
            RETURNING *
            """,
            creator_id,
            content_key,
            list(canonical_vault_item_ids or []),
            int(price_minor),
            currency,
            bool(allow_download),
        )
        if row is not None:
            return dict(row), True
        existing = await conn.fetchrow(
            "SELECT * FROM dropfans_drop_intents "
            "WHERE creator_id = $1 AND content_key = $2",
            creator_id,
            content_key,
        )
        return dict(existing), False


async def get_intent(creator_id: int, content_key: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM dropfans_drop_intents "
            "WHERE creator_id = $1 AND content_key = $2",
            creator_id,
            content_key,
        )
        return dict(row) if row else None


async def rearm_failed_intent(
    creator_id: int,
    content_key: str,
    *,
    canonical_vault_item_ids: list[str] | None = None,
    price_minor: int | None = None,
    currency: str | None = None,
    allow_download: bool | None = None,
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE dropfans_drop_intents SET status = 'pending',
                canonical_vault_item_ids = COALESCE($3, canonical_vault_item_ids),
                price_minor = COALESCE($4, price_minor),
                currency = COALESCE($5, currency),
                allow_download = COALESCE($6, allow_download),
                updated_at = NOW()
            WHERE creator_id = $1 AND content_key = $2 AND status = 'failed'
            RETURNING *
            """,
            creator_id,
            content_key,
            list(canonical_vault_item_ids) if canonical_vault_item_ids is not None else None,
            price_minor,
            currency,
            allow_download,
        )
        return dict(row) if row else None


async def mark_intent_failed(
    creator_id: int, content_key: str, reason: str
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE dropfans_drop_intents SET status = 'failed', error = $3, "
            "updated_at = NOW() WHERE creator_id = $1 AND content_key = $2 "
            "RETURNING *",
            creator_id,
            content_key,
            reason[:500],
        )
        if row is None:
            raise ValueError("intent not found for creator")
        return dict(row)


async def mark_intent_active(
    creator_id: int, content_key: str, dropfans_product_id: str
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE dropfans_drop_intents SET status = 'active', "
            "dropfans_product_id = $3, updated_at = NOW() "
            "WHERE creator_id = $1 AND content_key = $2 RETURNING *",
            creator_id,
            content_key,
            dropfans_product_id,
        )
        if row is None:
            raise ValueError("intent not found for creator")
        return dict(row)


async def note_intent_provider_cuid(
    creator_id: int, content_key: str, dropfans_product_id: str
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE dropfans_drop_intents SET dropfans_product_id = $3, "
            "updated_at = NOW() WHERE creator_id = $1 AND content_key = $2 "
            "AND status = 'pending' RETURNING *",
            creator_id,
            content_key,
            dropfans_product_id,
        )
        return dict(row) if row else None


__all__ = [
    "get_intent",
    "get_or_create_intent",
    "get_pool",
    "mark_intent_active",
    "mark_intent_failed",
    "note_intent_provider_cuid",
    "rearm_failed_intent",
]
