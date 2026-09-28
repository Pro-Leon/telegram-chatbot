"""DropFans DAO (commerce rebuild Phase 2). Creator-scoped persistence.

All functions async. Conventions mirror db/fangate.py: creator_id first,
fail-closed None, idempotent upserts with verbatim conflict targets
(asserted by p32/p31 suites), no secrets in logs. Provider HTTP lives in
integrations/dropfans/service.py — imported lazily inside sync_vault_index
only, so this module never creates an import cycle.

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 2.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.dropfans")

MAX_SYNC_PAGES = 20


def _synthetic_id(dropfans_product_id: str) -> int:
    return int(hashlib.sha256(dropfans_product_id.encode("utf-8")).hexdigest(), 16) % 2**62


# ------------------------------------------------------- integration

async def get_dropfans_integration(creator_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM creator_integrations WHERE creator_id = $1", creator_id
        )
        return dict(row) if row else None


async def upsert_dropfans_integration(
    creator_id: int,
    *,
    dropfans_creator_id: str,
    dropfans_username: str | None = None,
    dropfans_display_name: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Live schema enforces encrypted_api_key NOT NULL; the connect flow
        # overwrites it with the real ciphertext immediately after (see
        # integrations/dropfans/service.py connect_creator).
        row = await conn.fetchrow(
            """
            INSERT INTO creator_integrations
                (creator_id, encrypted_api_key, dropfans_creator_id,
                 dropfans_username, dropfans_display_name, status, updated_at)
            VALUES ($1, '', $2, $3, $4, 'active', NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                dropfans_creator_id = EXCLUDED.dropfans_creator_id,
                dropfans_username = EXCLUDED.dropfans_username,
                dropfans_display_name = EXCLUDED.dropfans_display_name,
                status = 'active',
                updated_at = NOW()
            RETURNING *
            """,
            creator_id,
            dropfans_creator_id,
            dropfans_username,
            dropfans_display_name,
        )
        return dict(row)


async def list_active_dropfans_creator_ids() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT creator_id FROM creator_integrations "
            "WHERE status = 'active' AND dropfans_creator_id IS NOT NULL "
            "ORDER BY creator_id ASC"
        )
        return [int(r["creator_id"]) for r in rows]


# ---------------------------------------------------------- products

async def find_dropfans_product(
    creator_id: int, cuid: str
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fangate_products "
            "WHERE creator_id = $1 AND dropfans_product_id = $2",
            creator_id,
            cuid,
        )
        if row is not None:
            return dict(row)
        row = await conn.fetchrow(
            "SELECT * FROM fangate_products "
            "WHERE creator_id = $1 AND (id = $2 OR raw->>'dropfans_product_id' = $3)",
            creator_id,
            _synthetic_id(cuid),
            cuid,
        )
        return dict(row) if row else None


async def find_synthetic_product(
    creator_id: int, synthetic_id: int
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fangate_products WHERE creator_id = $1 AND id = $2",
            creator_id,
            int(synthetic_id),
        )
        return dict(row) if row else None


async def resolve_dropfans_cuid(
    creator_id: int, synthetic_product_id: int
) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT dropfans_product_id, raw->>'dropfans_product_id' AS raw_cuid "
            "FROM fangate_products WHERE creator_id = $1 AND id = $2",
            creator_id,
            int(synthetic_product_id),
        )
        if row is None:
            return None
        return row["dropfans_product_id"] or row["raw_cuid"]


async def upsert_dropfans_product(
    creator_id: int,
    *,
    dropfans_product_id: str,
    name: str,
    price_cents: int,
    buy_url: str,
    status: str = "active",
    allow_download: bool = False,
    media_count: int = 0,
    vault_item_ids: list[str] | None = None,
) -> dict[str, Any]:
    import json

    vault_ids = list(vault_item_ids or [])
    raw_json = json.dumps(
        {"dropfans_product_id": dropfans_product_id, "vaultItemIds": vault_ids}
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO fangate_products
                    (id, creator_id, product_type, title, price_minor, sales_url,
                     is_accessible, is_downloadable, dropfans_product_id,
                     raw, synced_at)
                VALUES ($1,$2,'dropfans',$3,$4,$5,TRUE,$6,$7,$8::jsonb,NOW())
                ON CONFLICT (creator_id, dropfans_product_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    price_minor = EXCLUDED.price_minor,
                    sales_url = EXCLUDED.sales_url,
                    is_accessible = TRUE,
                    is_downloadable = EXCLUDED.is_downloadable,
                    raw = EXCLUDED.raw,
                    synced_at = NOW()
                RETURNING *
                """,
                _synthetic_id(dropfans_product_id),
                creator_id,
                name,
                int(price_cents),
                buy_url,
                bool(allow_download),
                dropfans_product_id,
                raw_json,
            )
            return dict(row)
        except Exception as e:
            if getattr(e, "pgcode", None) != "23505":
                raise
            # Same CUID materialized under another creator (shared global id
            # space): refresh this creator's own row instead of failing.
            row = await conn.fetchrow(
                """
                UPDATE fangate_products SET
                    title = $3, price_minor = $4, sales_url = $5,
                    is_accessible = TRUE, is_downloadable = $6,
                    raw = $7::jsonb, synced_at = NOW()
                WHERE creator_id = $1 AND dropfans_product_id = $2
                RETURNING *
                """,
                creator_id,
                dropfans_product_id,
                name,
                int(price_cents),
                buy_url,
                bool(allow_download),
                raw_json,
            )
            if row is None:
                raise
            return dict(row)


async def list_active_dropfans_products(creator_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fangate_products WHERE creator_id = $1 "
            "AND product_type = 'dropfans' AND is_accessible "
            "ORDER BY id ASC",
            creator_id,
        )
        return [dict(r) for r in rows]


async def count_active_dropfans_products(creator_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM fangate_products WHERE creator_id = $1 "
                "AND product_type = 'dropfans' AND is_accessible",
                creator_id,
            )
        )


# ------------------------------------------------------------- sales

async def has_dropfans_sale_been_recorded(
    creator_id: int, dropfans_product_id: str
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM fangate_transactions WHERE creator_id = $1 "
            "AND transaction_id = $2",
            creator_id,
            f"dropfans:{dropfans_product_id}",
        )
        return row is not None


async def record_dropfans_sale(
    creator_id: int,
    *,
    dropfans_product_id: str,
    buyer_email: str | None = None,
    sale_amount_cents: int = 0,
    sale_id: str | None = None,
    paid_at: str | None = None,
) -> bool:
    if sale_id:
        transaction_id = f"dropfans:{sale_id}"
    elif not buyer_email:
        # Legacy per-product fallback (checked by has_dropfans_sale_been_recorded).
        transaction_id = f"dropfans:{dropfans_product_id}"
    else:
        email_hash = hashlib.sha256(buyer_email.encode()).hexdigest()[:12]
        paid_hash = hashlib.sha256((paid_at or "").encode()).hexdigest()[:12]
        transaction_id = (
            f"dropfans:{dropfans_product_id}:{email_hash}:{sale_amount_cents}:{paid_hash}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO fangate_transactions
                (creator_id, transaction_id, event_type, buyer_email,
                 seller_earning, currency, product_id, user_id, occurred_at)
            VALUES ($1,$2,'dropfans_sale',$3,$4,'USD',$5,NULL,NOW())
            ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING
            RETURNING id, (xmax = 0) AS inserted
            """,
            creator_id,
            transaction_id,
            buyer_email,
            (int(sale_amount_cents) or 0) / 100,
            _synthetic_id(dropfans_product_id),
        )
        if row is None:
            return False
        return bool(row["inserted"])


async def list_recorded_sales(
    creator_id: int, *, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fangate_transactions WHERE creator_id = $1 "
            "AND event_type = 'dropfans_sale' "
            "ORDER BY occurred_at DESC NULLS LAST, id DESC LIMIT $2 OFFSET $3",
            creator_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def count_recorded_sales(creator_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM fangate_transactions WHERE creator_id = $1 "
                "AND event_type = 'dropfans_sale'",
                creator_id,
            )
        )


async def sum_recorded_sales_cents(creator_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COALESCE(SUM(seller_earning * 100), 0) "
                "FROM fangate_transactions WHERE creator_id = $1 "
                "AND event_type = 'dropfans_sale'",
                creator_id,
            )
        )


# ------------------------------------------------- selection config

async def get_selection_config(creator_id: int) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT creator_id, allowed_folders, allowed_tags, hard_mode "
            "FROM dropfans_selection_config WHERE creator_id = $1",
            creator_id,
        )
        if row is None:
            return {
                "creator_id": creator_id,
                "allowed_folders": [],
                "allowed_tags": [],
                "hard_mode": False,
            }
        return {
            "creator_id": row["creator_id"],
            "allowed_folders": list(row["allowed_folders"] or []),
            "allowed_tags": list(row["allowed_tags"] or []),
            "hard_mode": bool(row["hard_mode"]),
        }


async def set_selection_config(
    creator_id: int,
    *,
    allowed_folders: list[str],
    allowed_tags: list[str],
    hard_mode: bool,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO dropfans_selection_config
                (creator_id, allowed_folders, allowed_tags, hard_mode, updated_at)
            VALUES ($1,$2,$3,$4,NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                allowed_folders = EXCLUDED.allowed_folders,
                allowed_tags = EXCLUDED.allowed_tags,
                hard_mode = EXCLUDED.hard_mode,
                updated_at = NOW()
            RETURNING creator_id, allowed_folders, allowed_tags, hard_mode
            """,
            creator_id,
            list(allowed_folders or []),
            list(allowed_tags or []),
            bool(hard_mode),
        )
        return {
            "creator_id": row["creator_id"],
            "allowed_folders": list(row["allowed_folders"] or []),
            "allowed_tags": list(row["allowed_tags"] or []),
            "hard_mode": bool(row["hard_mode"]),
        }


# ------------------------------------------------------- vault index

async def upsert_vault_index(
    creator_id: int,
    vault_item_id: str,
    content_tags: list[str] | None = None,
    folder_id: str | None = None,
    folder_name: str | None = None,
    moderation_status: str | None = None,
    file_type: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO dropfans_vault_index
                (creator_id, vault_item_id, content_tags, folder_id,
                 folder_name, moderation_status, file_type, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
            ON CONFLICT (creator_id, vault_item_id) DO UPDATE SET
                content_tags = EXCLUDED.content_tags,
                folder_id = EXCLUDED.folder_id,
                folder_name = EXCLUDED.folder_name,
                moderation_status = EXCLUDED.moderation_status,
                file_type = EXCLUDED.file_type,
                synced_at = NOW()
            RETURNING *
            """,
            creator_id,
            vault_item_id,
            list(content_tags or []),
            folder_id,
            folder_name,
            moderation_status,
            file_type,
        )
        return dict(row)


async def list_vault_index(creator_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM dropfans_vault_index WHERE creator_id = $1 "
            "ORDER BY vault_item_id ASC",
            creator_id,
        )
        return [dict(r) for r in rows]


async def sync_vault_index(creator_id: int, max_pages: int = 20) -> dict[str, Any]:
    from integrations.dropfans.service import list_vault_items

    max_pages = max(1, min(int(max_pages), 20))
    synced = 0
    pages = 0
    complete = False
    seen: list[str] = []
    for page in range(1, max_pages + 1):
        result = await list_vault_items(creator_id, page=page, limit=50)
        items = list(getattr(result, "items", None) or [])
        pages += 1
        for item in items:
            vid = str(getattr(item, "id", ""))
            if not vid:
                continue
            await upsert_vault_index(
                creator_id,
                vid,
                content_tags=list(getattr(item, "content_tags", None) or []),
                folder_id=getattr(item, "folder_id", None),
                folder_name=getattr(item, "folder_name", None),
                moderation_status=getattr(item, "moderation_status", None),
                file_type=getattr(item, "file_type", None),
            )
            synced += 1
            seen.append(vid)
        if not bool(getattr(result, "has_more", False)):
            complete = True
            break
    pruned = 0
    if complete:
        pool = await get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM dropfans_vault_index "
                "WHERE creator_id = $1 AND NOT (vault_item_id = ANY($2))",
                creator_id,
                seen,
            )
            match = re.search(r"DELETE (\d+)", str(status))
            pruned = int(match.group(1)) if match else 0
    return {"synced": synced, "pages": pages, "complete": complete, "pruned": pruned}


__all__ = [
    "count_active_dropfans_products",
    "count_recorded_sales",
    "find_dropfans_product",
    "find_synthetic_product",
    "get_dropfans_integration",
    "get_pool",
    "get_selection_config",
    "has_dropfans_sale_been_recorded",
    "list_active_dropfans_creator_ids",
    "list_active_dropfans_products",
    "list_recorded_sales",
    "list_vault_index",
    "record_dropfans_sale",
    "resolve_dropfans_cuid",
    "set_selection_config",
    "sum_recorded_sales_cents",
    "sync_vault_index",
    "upsert_dropfans_integration",
    "upsert_dropfans_product",
    "upsert_vault_index",
]
