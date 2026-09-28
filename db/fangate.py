"""Fangate DAO (commerce rebuild Phase 1). Creator-scoped persistence.

All functions async. Conventions (per call-site + test extraction):
- creator_id is arg 0 everywhere except creator-list endpoints.
- get_* -> dict | None (fail-closed); list_* -> list[dict];
  count_* -> int; *_transaction/webhook_event inserts -> bool.
- Idempotency / scoping substrings asserted by tests are preserved
  verbatim (see docs/COMMERCE_DB_REBUILD_SPEC.md).
- Secrets never logged; integration status reads are allowlisted shapes.
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.fangate")


async def _pool():
    return await get_pool()


# ---------------------------------------------------------------- creators

async def get_creator(creator_id: int) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM creators WHERE id = $1", creator_id)
        return dict(row) if row else None


async def list_creators() -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM creators ORDER BY id ASC")
        return [dict(r) for r in rows]


async def create_creator(name: str, display_name: str | None = None) -> dict[str, Any]:
    if not name or not name.strip():
        raise ValueError("name required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO creators (name, display_name) VALUES ($1, $2) RETURNING *",
            name.strip(),
            display_name,
        )
        return dict(row)


# ------------------------------------------------------- integrations

async def get_creator_integration(creator_id: int) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM creator_integrations WHERE creator_id = $1", creator_id
        )
        return dict(row) if row else None


async def upsert_creator_integration(
    creator_id: int,
    encrypted_api_key: str,
    api_key_name: str | None = None,
    currency_code: str | None = None,
) -> dict[str, Any]:
    if not encrypted_api_key:
        raise ValueError("encrypted_api_key required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO creator_integrations
                (creator_id, encrypted_api_key, api_key_name, currency_code,
                 status, updated_at)
            VALUES ($1, $2, $3, $4, 'active', NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                encrypted_api_key = EXCLUDED.encrypted_api_key,
                api_key_name = EXCLUDED.api_key_name,
                currency_code = EXCLUDED.currency_code,
                status = 'active',
                updated_at = NOW()
            RETURNING *
            """,
            creator_id,
            encrypted_api_key,
            api_key_name,
            currency_code,
        )
        return dict(row)


async def list_active_creator_ids() -> list[int]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT creator_id FROM creator_integrations "
            "WHERE status = 'active' ORDER BY creator_id ASC"
        )
        return [int(r["creator_id"]) for r in rows]


async def get_any_creator_id_with_integration() -> int | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT creator_id FROM creator_integrations "
            "ORDER BY creator_id ASC LIMIT 1"
        )
        return int(row["creator_id"]) if row else None


async def list_integration_statuses() -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT creator_id, status, last_success_at, last_error_at, "
            "last_error FROM creator_integrations ORDER BY creator_id ASC"
        )
        return [dict(r) for r in rows]


async def record_integration_success(creator_id: int) -> None:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE creator_integrations SET status = 'active', "
            "last_success_at = NOW(), last_error = NULL, updated_at = NOW() "
            "WHERE creator_id = $1",
            creator_id,
        )


async def record_integration_error(creator_id: int, error: str) -> None:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE creator_integrations SET status = 'error', "
            "last_error = $2, last_error_at = NOW(), updated_at = NOW() "
            "WHERE creator_id = $1",
            creator_id,
            error[:500],
        )


async def update_integration_webhook(
    creator_id: int,
    webhook_id: int | None,
    encrypted_secret: str | None,
) -> None:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE creator_integrations SET webhook_id = $2, "
            "encrypted_webhook_secret = $3, updated_at = NOW() "
            "WHERE creator_id = $1",
            creator_id,
            webhook_id,
            encrypted_secret,
        )


# ------------------------------------------------------------ products

_PRODUCT_COLS = (
    "id, creator_id, product_type, title, preview, preview_blurred, "
    "price_minor, in_collection, link, sales_url, link_clicks, unlocks, "
    "total_earnings, folder_id, folder, media, is_adult_content, "
    "is_verif_age, is_epoch_enabled, is_should_consent, is_downloadable, "
    "is_accessible, private_description, public_description, raw, synced_at"
)


async def list_fangate_products(
    creator_id: int, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_PRODUCT_COLS} FROM fangate_products "
            "WHERE creator_id = $1 ORDER BY id ASC LIMIT $2 OFFSET $3",
            creator_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def count_fangate_products(creator_id: int) -> int:
    pool = await _pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM fangate_products WHERE creator_id = $1",
                creator_id,
            )
        )


async def get_fangate_product(
    creator_id: int, product_id: int | str
) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PRODUCT_COLS} FROM fangate_products "
            "WHERE creator_id = $1 AND id = $2",
            creator_id,
            int(product_id),
        )
        return dict(row) if row else None


async def upsert_fangate_product(creator_id: int, product: Any) -> None:
    get = (
        (lambda k, d=None: product.get(k, d))
        if isinstance(product, dict)
        else (lambda k, d=None: getattr(product, k, d))
    )
    import json

    raw = get("raw", {})
    raw_json = json.dumps(raw) if not isinstance(raw, str) else raw
    media = get("media")
    media_json = json.dumps(media) if media is not None and not isinstance(media, str) else media
    folder = get("folder")
    folder_json = (
        json.dumps(folder) if folder is not None and not isinstance(folder, str) else folder
    )
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fangate_products
                (id, creator_id, product_type, title, preview, preview_blurred,
                 price_minor, in_collection, link, sales_url, link_clicks,
                 unlocks, total_earnings, folder_id, folder, media,
                 is_adult_content, is_verif_age, is_epoch_enabled,
                 is_should_consent, is_downloadable, is_accessible,
                 private_description, public_description, raw, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16::jsonb,
                    $17,$18,$19,$20,$21,$22,$23,$24,$25::jsonb,NOW())
            ON CONFLICT (id) DO UPDATE SET
                creator_id = EXCLUDED.creator_id,
                product_type = EXCLUDED.product_type,
                title = EXCLUDED.title,
                preview = EXCLUDED.preview,
                preview_blurred = EXCLUDED.preview_blurred,
                price_minor = EXCLUDED.price_minor,
                in_collection = EXCLUDED.in_collection,
                link = EXCLUDED.link,
                sales_url = EXCLUDED.sales_url,
                folder_id = EXCLUDED.folder_id,
                folder = EXCLUDED.folder,
                media = EXCLUDED.media,
                is_accessible = EXCLUDED.is_accessible,
                is_downloadable = EXCLUDED.is_downloadable,
                raw = EXCLUDED.raw,
                synced_at = NOW()
            """,
            int(get("id")),
            creator_id,
            get("product_type") or get("type"),
            get("title"),
            get("preview"),
            get("preview_blurred"),
            get("price_minor"),
            bool(get("in_collection", False)),
            get("link"),
            get("sales_url") or get("link"),
            get("link_clicks"),
            get("unlocks"),
            get("total_earnings"),
            get("folder_id"),
            folder_json,
            media_json,
            bool(get("is_adult_content", False)),
            bool(get("is_verif_age", False)),
            bool(get("is_epoch_enabled", False)),
            bool(get("is_should_consent", False)),
            bool(get("is_downloadable", False)),
            bool(get("is_accessible", False)),
            get("private_description"),
            get("public_description"),
            raw_json,
        )


async def delete_fangate_product(creator_id: int, product_id: int | str) -> None:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM fangate_products WHERE creator_id = $1 AND id = $2",
            creator_id,
            int(product_id),
        )


async def find_product_by_media_id(
    creator_id: int, media_id: int
) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PRODUCT_COLS} FROM fangate_products "
            "WHERE creator_id = $1 AND raw->'media' @> $2::jsonb LIMIT 1",
            creator_id,
            f'[{int(media_id)}]',
        )
        if row is None:
            row = await conn.fetchrow(
                f"SELECT {_PRODUCT_COLS} FROM fangate_products "
                "WHERE creator_id = $1 AND raw::text LIKE $2 LIMIT 1",
                creator_id,
                f'%"media_id": {int(media_id)}%',
            )
        return dict(row) if row else None


# --------------------------------------------------------- transactions

async def list_fangate_transactions(
    creator_id: int, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fangate_transactions WHERE creator_id = $1 "
            "ORDER BY occurred_at DESC NULLS LAST, id DESC LIMIT $2 OFFSET $3",
            creator_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def count_fangate_transactions(creator_id: int) -> int:
    pool = await _pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM fangate_transactions WHERE creator_id = $1",
                creator_id,
            )
        )


async def upsert_fangate_transaction(
    creator_id: int,
    transaction_id: str,
    event_type: str,
    buyer_email: str | None = None,
    seller_earning: float | None = None,
    currency: str | None = None,
    product_id: int | None = None,
    set_price: float | None = None,
    occurred_at: Any | None = None,
    delivery_id: str | None = None,
) -> bool:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO fangate_transactions
                (creator_id, transaction_id, event_type, buyer_email,
                 seller_earning, currency, product_id, set_price,
                 occurred_at, delivery_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING
            RETURNING id, (xmax = 0) AS inserted
            """,
            creator_id,
            transaction_id,
            event_type,
            buyer_email,
            seller_earning,
            currency,
            product_id,
            set_price,
            occurred_at,
            delivery_id,
        )
        if row is None:
            return False
        return bool(row["inserted"])


# --------------------------------------------------------------- wallet

async def list_fangate_wallet_entries(
    creator_id: int, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fangate_wallet_entries WHERE creator_id = $1 "
            "ORDER BY id DESC LIMIT $2 OFFSET $3",
            creator_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def count_fangate_wallet_entries(creator_id: int) -> int:
    pool = await _pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM fangate_wallet_entries WHERE creator_id = $1",
                creator_id,
            )
        )


async def upsert_fangate_wallet_entry(creator_id: int, entry: Any) -> None:
    get = (
        (lambda k, d=None: entry.get(k, d))
        if isinstance(entry, dict)
        else (lambda k, d=None: getattr(entry, k, d))
    )
    import json

    raw = get("raw", {})
    raw_json = json.dumps(raw) if not isinstance(raw, str) else raw
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fangate_wallet_entries
                (creator_id, wallet_tx_id, amount_minor, txn_type,
                 created_at, title, raw, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,NOW())
            ON CONFLICT (creator_id, wallet_tx_id) DO UPDATE SET
                amount_minor = EXCLUDED.amount_minor,
                txn_type = EXCLUDED.txn_type,
                title = EXCLUDED.title,
                raw = EXCLUDED.raw,
                synced_at = NOW()
            """,
            creator_id,
            int(get("id") if get("id") is not None else get("wallet_tx_id")),
            get("amount_minor"),
            get("txn_type"),
            str(get("created_at")) if get("created_at") is not None else None,
            get("title"),
            raw_json,
        )


# ------------------------------------------------------- webhook events

async def insert_fangate_webhook_event(
    creator_id: int,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
    signature_valid: bool,
) -> bool:
    import json

    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO fangate_webhook_events
                (creator_id, delivery_id, event, payload, signature_valid)
            VALUES ($1,$2,$3,$4::jsonb,$5)
            ON CONFLICT (creator_id, delivery_id) DO NOTHING
            RETURNING id, (xmax = 0) AS inserted
            """,
            creator_id,
            delivery_id,
            event,
            json.dumps(payload or {}),
            bool(signature_valid),
        )
        if row is None:
            return False
        return bool(row["inserted"])


async def get_fangate_webhook_event(
    creator_id: int, delivery_id: str
) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fangate_webhook_events "
            "WHERE creator_id = $1 AND delivery_id = $2",
            creator_id,
            delivery_id,
        )
        return dict(row) if row else None


async def mark_webhook_event_processed(creator_id: int, delivery_id: str) -> None:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE fangate_webhook_events SET processed = TRUE "
            "WHERE creator_id = $1 AND delivery_id = $2",
            creator_id,
            delivery_id,
        )


__all__ = [
    "count_fangate_products",
    "count_fangate_transactions",
    "count_fangate_wallet_entries",
    "create_creator",
    "delete_fangate_product",
    "find_product_by_media_id",
    "get_any_creator_id_with_integration",
    "get_creator",
    "get_creator_integration",
    "get_fangate_product",
    "get_fangate_webhook_event",
    "insert_fangate_webhook_event",
    "list_active_creator_ids",
    "list_creators",
    "list_fangate_products",
    "list_fangate_transactions",
    "list_fangate_wallet_entries",
    "list_integration_statuses",
    "mark_webhook_event_processed",
    "record_integration_error",
    "record_integration_success",
    "update_integration_webhook",
    "upsert_creator_integration",
    "upsert_fangate_product",
    "upsert_fangate_transaction",
    "upsert_fangate_wallet_entry",
]
