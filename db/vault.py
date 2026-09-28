"""Vault-delivery DAO (commerce rebuild Phase 3b). Reservation lifecycle.

Ledger over ``vault_media_deliveries`` tracking which media went to which
fan, per creator. Two key families share the table:

- Fangate media keyed by ``fangate_media_id INTEGER`` with
  ``UNIQUE(creator_id, user_id, fangate_media_id)``.
- DropFans vault items keyed by ``dropfans_vault_item_id TEXT`` with
  partial ``UNIQUE(creator_id, user_id, dropfans_vault_item_id)
  WHERE dropfans_vault_item_id IS NOT NULL``.

Lifecycle: reserve (pending) -> Telegram send -> finalize (pending->sent);
failures release the pending row; the send loop reaps stale pending rows
older than ``max_age_minutes`` (UNKNOWN-attempt reservations shielded via
``skip_ids``). Only ``ResponseSent``-equivalent paths mutate; reads are
fail-closed (None/[]/0/{}), creator-scoped throughout.

Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3b. Evidence: call sites
in chatbotv2/main.py, vault/service.py, commerce/post_purchase.py; tests
test_vault_reservation_recovery.py, test_vault_analytics.py,
test_m7_audit_integrity.py, test_h4_batch3_unknown_sealed_recovery.py;
live DDL on staging (vault_media_deliveries + partial indexes).
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.vault")


def _affected_rows(status: str) -> int:
    """Parse asyncpg execute() status tags like 'UPDATE 1' / 'DELETE 0'."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


# ── Reservation lifecycle ─────────────────────────────────────────────


async def reserve_delivery(
    creator_id: int,
    user_id: int,
    fangate_media_id: int,
    product_id: int | None = None,
) -> int | None:
    """Atomically reserve a delivery intent before Telegram send.

    INSERT pending … ON CONFLICT DO NOTHING on
    UNIQUE(creator_id, user_id, fangate_media_id): the first reserver wins,
    duplicates get None (no duplicate send). Returns the reservation id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO vault_media_deliveries
                (creator_id, user_id, fangate_media_id, product_id, status)
            VALUES ($1, $2, $3, $4, 'pending')
            ON CONFLICT (creator_id, user_id, fangate_media_id) DO NOTHING
            RETURNING id
            """,
            creator_id,
            user_id,
            fangate_media_id,
            product_id,
        )
        return int(row["id"]) if row is not None else None


async def finalize_delivery(
    delivery_id: int,
    telegram_message_id: int | None = None,
    creator_id: int | None = None,
) -> bool:
    """Mark a reserved delivery 'sent' after Telegram success.

    Conditional UPDATE … WHERE status='pending' (idempotent: already-sent
    or reaped rows update 0 rows → False). Pass creator_id for ownership
    scoping; id-only calls keep legacy behavior with a deprecation warning.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            logger.warning(
                "db.vault.finalize_delivery without creator_id is deprecated; "
                "pass creator_id for ownership scoping"
            )
            status = await conn.execute(
                """
                UPDATE vault_media_deliveries
                SET status = 'sent',
                    telegram_message_id = COALESCE($2, telegram_message_id),
                    sent_at = NOW()
                WHERE id = $1 AND status = 'pending'
                """,
                delivery_id,
                telegram_message_id,
            )
        else:
            status = await conn.execute(
                """
                UPDATE vault_media_deliveries
                SET status = 'sent',
                    telegram_message_id = COALESCE($2, telegram_message_id),
                    sent_at = NOW()
                WHERE id = $1 AND creator_id = $3 AND status = 'pending'
                """,
                delivery_id,
                telegram_message_id,
                creator_id,
            )
        return _affected_rows(status) > 0


async def release_delivery(delivery_id: int, creator_id: int | None = None) -> bool:
    """Remove a pending reservation on failure (pre-send release).

    DELETE status-pending-only: sent rows are never removed. Pass
    creator_id for ownership scoping; id-only calls keep legacy behavior
    with a deprecation warning.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            logger.warning(
                "db.vault.release_delivery without creator_id is deprecated; "
                "pass creator_id for ownership scoping"
            )
            status = await conn.execute(
                "DELETE FROM vault_media_deliveries WHERE id = $1 AND status = 'pending'",
                delivery_id,
            )
        else:
            status = await conn.execute(
                "DELETE FROM vault_media_deliveries "
                "WHERE id = $1 AND creator_id = $2 AND status = 'pending'",
                delivery_id,
                creator_id,
            )
        return _affected_rows(status) > 0


async def release_stale_reservations(
    max_age_minutes: int = 5,
    batch_size: int = 50,
    skip_ids: list[int] | tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    """Reap stale pending reservations older than the threshold.

    Global (cross-creator) by design: recovery cleans all stale rows.
    Reservations tied to a recorded UNKNOWN attempt are evidence, not
    orphans — callers shield them via skip_ids. Strict age cutoff
    (created_at < NOW() - age); sent rows never touched. Returns the
    reaped rows for logging/reconciliation.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if skip_ids:
            rows = await conn.fetch(
                """
                DELETE FROM vault_media_deliveries
                WHERE id IN (
                    SELECT id FROM vault_media_deliveries
                    WHERE status = 'pending'
                      AND created_at < NOW() - CAST($1 AS numeric) * INTERVAL '1 minute'
                      AND NOT (id = ANY($3))
                    ORDER BY created_at
                    LIMIT $2
                )
                RETURNING *
                """,
                max_age_minutes,
                batch_size,
                list(skip_ids),
            )
        else:
            rows = await conn.fetch(
                """
                DELETE FROM vault_media_deliveries
                WHERE id IN (
                    SELECT id FROM vault_media_deliveries
                    WHERE status = 'pending'
                      AND created_at < NOW() - CAST($1 AS numeric) * INTERVAL '1 minute'
                    ORDER BY created_at
                    LIMIT $2
                )
                RETURNING *
                """,
                max_age_minutes,
                batch_size,
            )
        return [dict(r) for r in rows]


# ── Direct record / reads (Fangate-keyed) ─────────────────────────────


async def record_delivery(
    creator_id: int,
    user_id: int,
    fangate_media_id: int,
    product_id: int | None = None,
    telegram_message_id: int | None = None,
) -> bool:
    """Record a completed delivery directly (post-send path).

    Idempotent INSERT … ON CONFLICT DO NOTHING on
    UNIQUE(creator_id, user_id, fangate_media_id). True=inserted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO vault_media_deliveries
                (creator_id, user_id, fangate_media_id, product_id,
                 telegram_message_id, status, sent_at)
            VALUES ($1, $2, $3, $4, $5, 'sent', NOW())
            ON CONFLICT (creator_id, user_id, fangate_media_id) DO NOTHING
            RETURNING id
            """,
            creator_id,
            user_id,
            fangate_media_id,
            product_id,
            telegram_message_id,
        )
        return row is not None


async def get_delivery(
    creator_id: int, user_id: int, fangate_media_id: int
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 AND fangate_media_id = $3 "
            "LIMIT 1",
            creator_id,
            user_id,
            fangate_media_id,
        )
        return dict(row) if row else None


async def get_delivered_media_map(creator_id: int, user_id: int) -> dict[int, dict[str, Any]]:
    """Map of {fangate_media_id: delivery row} for a fan (overlay lacks)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 "
            "AND fangate_media_id IS NOT NULL",
            creator_id,
            user_id,
        )
        return {int(r["fangate_media_id"]): dict(r) for r in rows}


async def has_user_received_media(creator_id: int, user_id: int, fangate_media_id: int) -> bool:
    """True iff a 'sent' delivery exists (pending reservations don't count)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 AND fangate_media_id = $3 "
            "AND status = 'sent' LIMIT 1",
            creator_id,
            user_id,
            fangate_media_id,
        )
        return row is not None


async def get_unseen_media_ids(creator_id: int, user_id: int, media_ids: list[int]) -> list[int]:
    """Return the subset of media_ids with no 'sent' delivery (input order)."""
    if not media_ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT fangate_media_id FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 AND status = 'sent' "
            "AND fangate_media_id = ANY($3)",
            creator_id,
            user_id,
            list(media_ids),
        )
        seen = {int(r["fangate_media_id"]) for r in rows}
        return [m for m in media_ids if m not in seen]


async def list_deliveries(
    creator_id: int,
    user_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            rows = await conn.fetch(
                "SELECT * FROM vault_media_deliveries "
                "WHERE creator_id = $1 ORDER BY id DESC LIMIT $2 OFFSET $3",
                creator_id,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM vault_media_deliveries "
                "WHERE creator_id = $1 AND user_id = $2 "
                "ORDER BY id DESC LIMIT $3 OFFSET $4",
                creator_id,
                user_id,
                limit,
                offset,
            )
        return [dict(r) for r in rows]


async def count_deliveries(creator_id: int, user_id: int | None = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM vault_media_deliveries WHERE creator_id = $1",
                creator_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM vault_media_deliveries "
                "WHERE creator_id = $1 AND user_id = $2",
                creator_id,
                user_id,
            )
        return int(row["cnt"]) if row else 0


# ── Analytics ─────────────────────────────────────────────────────────


async def get_delivery_stats(creator_id: int) -> dict[str, int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_deliveries,
                   COUNT(DISTINCT user_id) AS unique_fans,
                   COUNT(DISTINCT fangate_media_id) AS unique_media,
                   COUNT(*) FILTER (WHERE status = 'sent') AS sent_count,
                   COUNT(*) FILTER (WHERE status = 'pending') AS pending_count
            FROM vault_media_deliveries
            WHERE creator_id = $1
            """,
            creator_id,
        )
        if row is None:
            return {
                "total_deliveries": 0,
                "unique_fans": 0,
                "unique_media": 0,
                "sent_count": 0,
                "pending_count": 0,
            }
        return {
            "total_deliveries": int(row["total_deliveries"] or 0),
            "unique_fans": int(row["unique_fans"] or 0),
            "unique_media": int(row["unique_media"] or 0),
            "sent_count": int(row["sent_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
        }


async def get_recent_deliveries(creator_id: int, limit: int = 10) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.*, u.first_name, u.username
            FROM vault_media_deliveries v
            LEFT JOIN users u ON u.id = v.user_id
            WHERE v.creator_id = $1
            ORDER BY v.id DESC
            LIMIT $2
            """,
            creator_id,
            limit,
        )
        return [dict(r) for r in rows]


async def get_top_fans(creator_id: int, limit: int = 5) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.user_id, COUNT(*) AS delivery_count,
                   MAX(v.sent_at) AS last_delivery,
                   u.first_name, u.username
            FROM vault_media_deliveries v
            LEFT JOIN users u ON u.id = v.user_id
            WHERE v.creator_id = $1 AND v.status = 'sent'
            GROUP BY v.user_id, u.first_name, u.username
            ORDER BY delivery_count DESC
            LIMIT $2
            """,
            creator_id,
            limit,
        )
        return [dict(r) for r in rows]


async def get_product_delivery_counts(creator_id: int) -> dict[int, int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT product_id, COUNT(*) AS cnt FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND status = 'sent' "
            "AND product_id IS NOT NULL GROUP BY product_id",
            creator_id,
        )
        return {int(r["product_id"]): int(r["cnt"]) for r in rows}


async def get_media_delivery_counts(creator_id: int) -> dict[int, int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT fangate_media_id, COUNT(*) AS cnt FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND status = 'sent' "
            "AND fangate_media_id IS NOT NULL GROUP BY fangate_media_id",
            creator_id,
        )
        return {int(r["fangate_media_id"]): int(r["cnt"]) for r in rows}


async def get_fan_delivery_history(
    creator_id: int, user_id: int, limit: int = 100
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 "
            "ORDER BY id DESC LIMIT $3",
            creator_id,
            user_id,
            limit,
        )
        return [dict(r) for r in rows]


async def get_fan_delivery_count(creator_id: int, user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 AND status = 'sent'",
            creator_id,
            user_id,
        )
        return int(row["cnt"]) if row else 0


# ── DropFans-keyed variants ───────────────────────────────────────────


async def finalize_dropfans_delivery(
    creator_id: int,
    user_id: int,
    vault_item_id: str,
    telegram_message_id: int | None = None,
) -> bool:
    """Mark a DropFans pending row 'sent' after Telegram success.

    Conditional UPDATE … WHERE status='pending' (idempotent). Keyed by
    (creator, user, dropfans_vault_item_id).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE vault_media_deliveries
            SET status = 'sent',
                telegram_message_id = COALESCE($4, telegram_message_id),
                sent_at = NOW()
            WHERE creator_id = $1 AND user_id = $2
              AND dropfans_vault_item_id = $3 AND status = 'pending'
            """,
            creator_id,
            user_id,
            vault_item_id,
            telegram_message_id,
        )
        return _affected_rows(status) > 0


async def release_dropfans_delivery(creator_id: int, user_id: int, vault_item_id: str) -> bool:
    """Remove a DropFans pending row on failure (pending-only)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM vault_media_deliveries "
            "WHERE creator_id = $1 AND user_id = $2 "
            "AND dropfans_vault_item_id = $3 AND status = 'pending'",
            creator_id,
            user_id,
            vault_item_id,
        )
        return _affected_rows(status) > 0


__all__ = [
    "count_deliveries",
    "finalize_delivery",
    "finalize_dropfans_delivery",
    "get_delivered_media_map",
    "get_delivery",
    "get_delivery_stats",
    "get_fan_delivery_count",
    "get_fan_delivery_history",
    "get_media_delivery_counts",
    "get_pool",
    "get_product_delivery_counts",
    "get_recent_deliveries",
    "get_top_fans",
    "get_unseen_media_ids",
    "has_user_received_media",
    "list_deliveries",
    "record_delivery",
    "release_delivery",
    "release_dropfans_delivery",
    "release_stale_reservations",
    "reserve_delivery",
]
