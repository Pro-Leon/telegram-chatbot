"""Data access for commerce offers and fan purchase attribution.

Every operation is creator-scoped. State transitions are conditional UPDATEs,
so concurrent webhook deliveries cannot double-apply state (keywords: the
state filter in each UPDATE, the partial unique index on transaction_id, and
the atomic assertion in ``attach_transaction_user``).
"""

import json
import logging
from core.event_bus import publish_event
from datetime import UTC, datetime
from typing import Any

from commerce.models import PurchaseRecord
from db.postgres import get_pool

logger = logging.getLogger("commerce.dao")


async def create_offer(
    creator_id: int,
    user_id: int,
    product_id: int,
    link: str,
    *,
    price_minor: int | None = None,
    currency: str | None = None,
    reason: str | None = None,
    created_by: str,
    expires_at: Any | None = None,
    dropfans_product_id: str | None = None,
    vault_item_ids: list[str] | None = None,
    media_count: int | None = None,
    drop_content_hash: str | None = None,
) -> dict[str, Any]:
    """Persist a new PPV offer in 'pending' state. Returns the created row.

    P3.2C F1: ``dropfans_product_id`` + ``vault_item_ids`` (+ derived
    ``media_count``/``drop_content_hash``) form the immutable offer-time
    content snapshot and are written atomically with the row. The snapshot is
    REQUIRED — a missing snapshot fails closed here (ValueError, no SQL), and
    a schema error fails closed by propagation. There is deliberately no
    legacy snapshot-less fallback: silently discarding snapshot data would
    violate the P3.2 safety contract (diagnostic: commerce_snapshot_required /
    commerce_snapshot_persistence_failed).
    """
    if not vault_item_ids:
        raise ValueError("commerce_snapshot_required: vault_item_ids must not be empty")
    if not dropfans_product_id or not str(dropfans_product_id).strip():
        raise ValueError("commerce_snapshot_required: dropfans_product_id is required")
    pool = await get_pool()
    snapshot_ids = list(vault_item_ids)
    snapshot_count = len(snapshot_ids)
    if drop_content_hash is None:
        from commerce.vault_sets import drop_content_hash as _snapshot_hash

        drop_content_hash = _snapshot_hash(snapshot_ids)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO commerce_offers (
                    creator_id, user_id, product_id, link, price_minor, currency,
                    reason, created_by, expires_at,
                    dropfans_product_id, vault_item_ids, media_count, drop_content_hash
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text[], $12, $13)
                RETURNING *
                """,
                creator_id,
                user_id,
                product_id,
                link,
                price_minor,
                currency,
                reason,
                created_by,
                expires_at,
                str(dropfans_product_id).strip(),
                snapshot_ids,
                snapshot_count,
                drop_content_hash,
            )
        except Exception as exc:
            # P3.2C F1: fail closed. A snapshot that cannot be persisted must
            # never become a snapshot-less row. Propagate with a diagnostic.
            logger.warning(
                "commerce_snapshot_persistence_failed creator=%s product=%s: %s",
                creator_id,
                product_id,
                exc.__class__.__name__,
            )
            raise
        return dict(row)


async def create_offer_serialized(
    creator_id: int,
    user_id: int,
    product_id: int,
    link: str,
    *,
    price_minor: int | None = None,
    currency: str | None = None,
    reason: str | None = None,
    created_by: str,
    expires_at: Any | None = None,
    dropfans_product_id: str | None = None,
    vault_item_ids: list[str] | None = None,
    media_count: int | None = None,
    drop_content_hash: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Idempotently create a PPV offer under a deterministic serialization key.

    The deterministic logical identity is (creator_id, user_id, product_id):
    the same fan/creator/product pair maps to the same advisory lock, so
    concurrent executions of the same logical PPV are serialized at the
    database. The redeemable-offer check runs INSIDE the locked transaction,
    making the application check race-free.

    Returns (row, created): ``created`` is True only when this call inserted
    the offer; otherwise the returned row is the already-existing redeemable
    offer (caller should report 'already_executed').

    P3.2C F1: snapshot REQUIRED (fail closed, no SQL on missing snapshot);
    schema errors propagate (diagnostic: commerce_snapshot_persistence_failed).
    No legacy snapshot-less fallback exists.
    """
    if not vault_item_ids:
        raise ValueError("commerce_snapshot_required: vault_item_ids must not be empty")
    if not dropfans_product_id or not str(dropfans_product_id).strip():
        raise ValueError("commerce_snapshot_required: dropfans_product_id is required")
    pool = await get_pool()
    lock_key = f"ppv_offer:{creator_id}:{user_id}:{product_id}"
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            lock_key,
        )
        existing = await conn.fetchrow(
            """
            SELECT * FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2 AND product_id = $3
              AND state IN ('pending', 'clicked')
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            creator_id,
            user_id,
            product_id,
        )
        if existing:
            return dict(existing), False
        snapshot_ids = list(vault_item_ids)
        snapshot_count = len(snapshot_ids)
        if drop_content_hash is None:
            from commerce.vault_sets import drop_content_hash as _snapshot_hash

            drop_content_hash = _snapshot_hash(snapshot_ids)
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO commerce_offers (
                    creator_id, user_id, product_id, link, price_minor, currency,
                    reason, created_by, expires_at,
                    dropfans_product_id, vault_item_ids, media_count, drop_content_hash
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text[], $12, $13)
                RETURNING *
                """,
                creator_id,
                user_id,
                product_id,
                link,
                price_minor,
                currency,
                reason,
                created_by,
                expires_at,
                str(dropfans_product_id).strip(),
                snapshot_ids,
                snapshot_count,
                drop_content_hash,
            )
        except Exception as exc:
            # P3.2C F1: fail closed — never degrade to a snapshot-less row.
            logger.warning(
                "commerce_snapshot_persistence_failed creator=%s product=%s: %s",
                creator_id,
                product_id,
                exc.__class__.__name__,
            )
            raise
        return dict(row), True


async def get_offer(creator_id: int, offer_id: int) -> dict[str, Any] | None:
    """Return one offer, refusing cross-creator access."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commerce_offers WHERE creator_id = $1 AND id = $2",
            creator_id,
            offer_id,
        )
        return dict(row) if row else None


async def list_offers_for_user(
    user_id: int,
    *,
    creator_id: int | None = None,
    state: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List offers for a fan, optionally filtered by creator and/or state."""
    pool = await get_pool()
    clauses = ["user_id = $1"]
    params: list[Any] = [user_id]
    if creator_id is not None:
        params.append(creator_id)
        clauses.append(f"creator_id = ${len(params)}")
    if state is not None:
        params.append(state)
        clauses.append(f"state = ${len(params)}")
    params.extend([limit, offset])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM commerce_offers
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [dict(r) for r in rows]


async def list_offers_for_creator(
    creator_id: int,
    *,
    user_id: int | None = None,
    state: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List offers for a creator, optionally filtered by fan and/or state."""
    pool = await get_pool()
    clauses = ["creator_id = $1"]
    params: list[Any] = [creator_id]
    if user_id is not None:
        params.append(user_id)
        clauses.append(f"user_id = ${len(params)}")
    if state is not None:
        params.append(state)
        clauses.append(f"state = ${len(params)}")
    params.extend([limit, offset])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM commerce_offers
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [dict(r) for r in rows]


async def find_pending_offer_for_product(
    creator_id: int, user_id: int, product_id: int
) -> dict[str, Any] | None:
    """Return the oldest still-redeemable offer for a product+fan.

    'Redeemable' means pending or clicked (link opened but not yet paid), so a
    purchase that follows a click still attributes to the same offer.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2 AND product_id = $3
              AND state IN ('pending', 'clicked')
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            creator_id,
            user_id,
            product_id,
        )
        return dict(row) if row else None


async def find_pending_offers_for_product(
    creator_id: int, product_id: int
) -> list[dict[str, Any]]:
    """Return all still-redeemable offers for a product (any user).

    'Redeemable' means pending or clicked. Used by webhook attribution to
    determine whether a purchase can be deterministically attributed.
    Creator-scoped via SQL.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM commerce_offers
            WHERE creator_id = $1 AND product_id = $2
              AND state IN ('pending', 'clicked')
            ORDER BY created_at ASC, id ASC
            """,
            creator_id,
            product_id,
        )
        return [dict(r) for r in rows]


async def _disambiguate_via_buyer_identity(
    conn: Any,
    creator_id: int,
    candidates: list[Any],
    transaction_id: str,
) -> Any | None:
    """Deterministically narrow ambiguous candidates via buyer identity already persisted.

    Inspects fangate_transactions buyer_email / buyer_external_id for this
    transaction_id and, if exactly one candidate's user has a historic
    fangate_transactions row with the same buyer_email, returns that candidate.
    Never invents identity; returns None if ambiguous or no match.
    """
    try:
        txn_row = await conn.fetchrow(
            "SELECT buyer_email FROM fangate_transactions WHERE creator_id=$1 AND transaction_id=$2 LIMIT 1",
            creator_id,
            transaction_id,
        )
        buyer_email = (txn_row["buyer_email"] if txn_row and txn_row["buyer_email"] else None)
        if buyer_email:
            buyer_email = str(buyer_email).strip().lower()
        else:
            return None
        if not buyer_email or "@" not in buyer_email:
            return None
        # Find distinct user_ids that historically have this buyer_email for this creator
        rows = await conn.fetch(
            """
            SELECT DISTINCT user_id FROM fangate_transactions
            WHERE creator_id=$1 AND LOWER(buyer_email)=$2 AND user_id IS NOT NULL
            LIMIT 5
            """,
            creator_id,
            buyer_email,
        )
        user_ids = [r["user_id"] for r in rows if r["user_id"] is not None]
        if len(user_ids) != 1:
            return None
        matched_user = user_ids[0]
        matched = [c for c in candidates if int(c["user_id"]) == int(matched_user)]
        if len(matched) == 1:
            logger.info(
                "webhook_attribution: disambiguated via buyer_email creator=%s product=%s txn=%s user=%s",
                creator_id,
                candidates[0]["product_id"] if candidates else None,
                transaction_id,
                matched_user,
            )
            return matched[0]
    except Exception:
        logger.debug("disambiguation via buyer_email failed", exc_info=True)
    return None


async def _record_ambiguous_recovery(
    conn: Any,
    creator_id: int,
    transaction_id: str,
    product_id: int,
    candidates: list[Any],
    occurred_at: Any | None = None,
) -> None:
    """Persist operator-visible ambiguous_offer recovery record, idempotent on (creator, transaction_id)."""
    try:
        # Attempt to fetch buyer identity for observability
        buyer_email = None
        buyer_external_id = None
        try:
            txn_row = await conn.fetchrow(
                "SELECT buyer_email FROM fangate_transactions WHERE creator_id=$1 AND transaction_id=$2 LIMIT 1",
                creator_id,
                transaction_id,
            )
            if txn_row:
                buyer_email = txn_row["buyer_email"]
        except Exception:
            pass
        candidate_ids = [int(c["id"]) for c in candidates]
        # Best-effort publish to DLQ stream for dashboards that tail DLQ
        try:
            from db.redis import DLQ_STREAM, get_redis
            import json as _json
            import time as _time
            r = await get_redis()
            await r.xadd(DLQ_STREAM, {
                "message_id": f"ambiguous:{creator_id}:{transaction_id}",
                "reason": "ambiguous_offer",
                "stream": "commerce",
                "failure_timestamp": str(int(_time.time())),
                "replay_count": "0",
                "payload": _json.dumps({
                    "creator_id": str(creator_id),
                    "transaction_id": transaction_id,
                    "product_id": str(product_id),
                    "candidate_offer_ids": candidate_ids,
                    "buyer_email": buyer_email,
                    "occurred_at": str(occurred_at) if occurred_at else None,
                }),
                "worker_id": "commerce.dao",
            })
        except Exception:
            logger.debug("ambiguous DLQ publish failed", exc_info=True)
        # Persist to SQL recovery table (idempotent)
        await conn.execute(
            """
            INSERT INTO ambiguous_purchase_recoveries
                (creator_id, transaction_id, product_id, candidate_offer_ids, buyer_email, buyer_external_id, occurred_at, reason)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'ambiguous_offer')
            ON CONFLICT (creator_id, transaction_id) DO NOTHING
            """,
            creator_id,
            transaction_id,
            product_id,
            candidate_ids,
            buyer_email,
            buyer_external_id,
            occurred_at,
        )
    except Exception:
        logger.warning("failed to record ambiguous_offer recovery creator=%s txn=%s", creator_id, transaction_id, exc_info=True)


async def attribute_purchase_from_webhook(
    creator_id: int,
    product_id: int,
    transaction_id: str,
    revenue_minor: int = 0,
    occurred_at: Any = None,
) -> PurchaseRecord | None:
    """Atomically attribute a webhook purchase to a single pending offer.

    Returns a PurchaseRecord when exactly one pending offer for the product
    exists and was successfully transitioned to 'purchased'. Returns None
    when:

    - zero redeemable offers exist (no one to attribute to)
    - two or more redeemable offers exist (ambiguous — fail-closed)
    - the transaction was already claimed by another offer
    - any DAO operation fails (entire attempt is abandoned)

    This function is idempotent: re-calling after a successful attribution
    returns None (offer already moved past redeemable states).

    All operations run inside a single DB transaction for atomicity.
    P2.1: ambiguous >1 now attempts deterministic buyer_email disambiguation
    before fail-closed and records an operator-visible recovery row.
    P2.1: first-sale ledger (users.first_*) is updated atomically inside the
    same transaction plus commerce.sale_recorded emitted best-effort.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # 1. Find all pending/clicked offers for this product (creator-scoped).
        candidates = await conn.fetch(
            """
            SELECT * FROM commerce_offers
            WHERE creator_id = $1 AND product_id = $2
              AND state IN ('pending', 'clicked')
            ORDER BY created_at ASC, id ASC
            """,
            creator_id,
            product_id,
        )

        if len(candidates) == 0:
            logger.debug(
                "webhook_attribution: creator=%s product=%s — no pending offers",
                creator_id,
                product_id,
            )
            return None

        if len(candidates) > 1:
            # P2.1 D1: attempt deterministic buyer_email disambiguation before fail-closed
            disambiguated = await _disambiguate_via_buyer_identity(conn, creator_id, list(candidates), transaction_id)
            if disambiguated is not None:
                candidates = [disambiguated]
            else:
                logger.info(
                    "webhook_attribution: creator=%s product=%s — %d pending offers, "
                    "ambiguous (fail-closed)",
                    creator_id,
                    product_id,
                    len(candidates),
                )
                # Operator-visible recovery record, idempotent
                await _record_ambiguous_recovery(conn, creator_id, transaction_id, product_id, list(candidates), occurred_at=occurred_at)
                return None

        offer = candidates[0]
        user_id = offer["user_id"]
        offer_id = offer["id"]

        # 2. Transition offer to 'purchased' (conditional: only pending/clicked).
        updated = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'purchased',
                purchased_at = NOW(),
                transaction_id = $3
            WHERE creator_id = $1 AND id = $2
              AND state IN ('pending', 'clicked')
              AND (transaction_id IS NULL OR transaction_id = $3)
            RETURNING *
            """,
            creator_id,
            offer_id,
            transaction_id,
        )
        if updated is None:
            # Offer already moved on, or transaction claimed elsewhere.
            logger.debug(
                "webhook_attribution: creator=%s offer=%s — already transitioned",
                creator_id,
                offer_id,
            )
            return None

        # 3. Attach the Telegram user to the transaction (non-overwriting).
        await conn.execute(
            """
            UPDATE fangate_transactions
            SET user_id = $3
            WHERE creator_id = $1 AND transaction_id = $2 AND user_id IS NULL
            """,
            creator_id,
            transaction_id,
            user_id,
        )

        # 4. Record the purchase analytics (idempotent UPSERT).
        from datetime import UTC, datetime as _dt

        day = (occurred_at or _dt.now(UTC)).date()
        await conn.execute(
            """
            INSERT INTO ppv_analytics_daily (creator_id, product_id, day, offers_purchased)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (creator_id, product_id, day) DO UPDATE SET
                offers_purchased = ppv_analytics_daily.offers_purchased + 1
            """,
            creator_id,
            product_id,
            day,
        )
        if revenue_minor:
            await conn.execute(
                """
                INSERT INTO ppv_analytics_daily (creator_id, product_id, day, revenue_minor)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (creator_id, product_id, day) DO UPDATE SET
                    revenue_minor = ppv_analytics_daily.revenue_minor + EXCLUDED.revenue_minor
                """,
                creator_id,
                product_id,
                day,
                revenue_minor,
            )

        # P2.1 D2: atomic first-sale ledger — same transaction, creator/user-scoped funnel lock, first_* immutable
        is_first_sale = False
        first_sale_offer_id = None
        first_sale_price_minor = None
        first_sale_currency = None
        try:
            # Funnel lock (creator/user scoped) — ordering consistent: offer then funnel
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"funnel:{creator_id}:{user_id}",
            )
            # Check existing first-sale state FOR UPDATE
            existing_user = await conn.fetchrow(
                "SELECT funnel_stage, first_purchase_at, first_offer_id, first_transaction_id FROM users WHERE id=$1 FOR UPDATE",
                user_id,
            )
            if existing_user is not None and existing_user["first_purchase_at"] is None:
                # Prefer authoritative occurred_at, else purchased_at just set, else NOW
                _purchase_ts = occurred_at if occurred_at is not None else updated["purchased_at"] if updated and updated.get("purchased_at") else None
                if _purchase_ts is None:
                    from datetime import datetime as _dt2
                    _purchase_ts = _dt2.now(UTC)
                # Capture price/currency from the purchased offer for event/analytics
                try:
                    first_sale_price_minor = updated.get("price_minor") if isinstance(updated, dict) else None
                    first_sale_currency = updated.get("currency") if isinstance(updated, dict) else None
                except Exception:
                    pass
                res = await conn.execute(
                    """
                    UPDATE users
                    SET funnel_stage='converted',
                        first_purchase_at=$2,
                        first_offer_id=$3,
                        first_transaction_id=$4
                    WHERE id=$1 AND first_purchase_at IS NULL
                    """,
                    user_id,
                    _purchase_ts,
                    updated["id"],
                    transaction_id,
                )
                # asyncpg execute returns "UPDATE N"
                if res.endswith("UPDATE 1"):
                    is_first_sale = True
                    first_sale_offer_id = updated["id"]
                else:
                    # Race: another tx set it first
                    is_first_sale = False
            else:
                # Already have first sale — still ensure converted
                if existing_user is not None and existing_user["funnel_stage"] != "converted":
                    await conn.execute("UPDATE users SET funnel_stage='converted' WHERE id=$1 AND funnel_stage<>'converted'", user_id)
                is_first_sale = False
                first_sale_offer_id = existing_user["first_offer_id"] if existing_user else None
        except Exception:
            logger.warning("first-sale ledger update failed creator=%s user=%s txn=%s", creator_id, user_id, transaction_id, exc_info=True)
            is_first_sale = False

        logger.info(
            "webhook_attribution: creator=%s user=%s product=%s txn=%s — attributed first_sale=%s",
            creator_id,
            user_id,
            product_id,
            transaction_id,
            is_first_sale,
        )

        # Capture for post-commit publish (outside TX)
        _post_commit_publish = {
            "offer_id": updated["id"],
            "creator_id": creator_id,
            "user_id": user_id,
            "product_id": product_id,
            "transaction_id": transaction_id,
            "price_minor": updated.get("price_minor") if isinstance(updated, dict) else None,
            "currency": updated.get("currency") if isinstance(updated, dict) else None,
            "revenue_minor": revenue_minor,
            "first_sale": is_first_sale,
            "occurred_at": str(occurred_at) if occurred_at else None,
        }
        _ret_record = PurchaseRecord(
            offer_id=updated["id"],
            creator_id=creator_id,
            user_id=user_id,
            transaction_id=transaction_id,
            product_id=product_id,
            occurred_at=occurred_at,
        )
    # Outside transaction — publish best-effort, never blocks commit
    try:
        from core.event_bus import publish_event as _pub
        await _pub(
            "commerce.sale_recorded",
            {
                "offer_id": _post_commit_publish["offer_id"],
                "creator_id": _post_commit_publish["creator_id"],
                "user_id": _post_commit_publish["user_id"],
                "product_id": _post_commit_publish["product_id"],
                "transaction_id": _post_commit_publish["transaction_id"],
                "price_minor": _post_commit_publish["price_minor"],
                "currency": _post_commit_publish["currency"],
                "revenue_minor": _post_commit_publish["revenue_minor"],
                "first_sale": _post_commit_publish["first_sale"],
                "occurred_at": _post_commit_publish["occurred_at"],
            },
            creator_id=_post_commit_publish["creator_id"],
            user_id=_post_commit_publish["user_id"],
            scope="user",
        )
    except Exception:
        logger.debug("commerce.sale_recorded publish failed (fail-open) txn=%s", transaction_id, exc_info=True)

    # P3.5.1: resolve the purchase to its opportunity ledger row (isolated).
    # Consumes authoritative attribution above; never re-attributes.
    try:
        from commerce.opportunity_ledger import record_purchase_by_offer
        await record_purchase_by_offer(
            creator_id=_post_commit_publish["creator_id"],
            offer_id=_post_commit_publish["offer_id"],
            transaction_id=transaction_id,
            price_minor=_post_commit_publish["price_minor"],
            currency=_post_commit_publish["currency"],
        )
    except Exception:
        logger.debug("opportunity ledger purchase link failed (isolated) txn=%s", transaction_id, exc_info=True)

    return _ret_record


async def has_purchased_product(creator_id: int, user_id: int, product_id: int) -> bool:
    """Return whether the fan already paid for the product (any creator offer
    in 'purchased' state carrying a Fangate transaction)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2 AND product_id = $3
              AND state = 'purchased' AND transaction_id IS NOT NULL
            LIMIT 1
            """,
            creator_id,
            user_id,
            product_id,
        )
        return row is not None


async def get_owned_vault_ids(creator_id: int, user_id: int) -> frozenset[str]:
    """Return the canonical set of Vault CUIDs the fan owns via completed purchases.

    P3.3.1 item-level ownership primitive. A Vault item is owned when it
    appears in an immutable ``commerce_offers.vault_item_ids`` snapshot on a
    row scoped to the same creator and user with ``state = 'purchased'`` and
    ``transaction_id IS NOT NULL``.

    Delivery is NOT consulted: a fan who paid but has not yet received media
    still owns it. Legacy rows with ``vault_item_ids IS NULL`` contribute
    nothing (no reconstruction attempted); they remain protected by the
    existing product-level ownership paths. Only ``'purchased'`` qualifies —
    pending/clicked/declined/expired/revoked rows never contribute.

    Returns an empty frozenset when no qualifying rows exist. Database/query
    failures propagate as exceptions (fail-closed) and are never converted
    into an empty set, since a false-empty set could cause an already-owned
    item to be offered again.
    """
    from commerce.vault_sets import canonical_identity_ids

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT v AS vault_item_id
            FROM commerce_offers, unnest(vault_item_ids) AS v
            WHERE creator_id = $1 AND user_id = $2
              AND state = 'purchased' AND transaction_id IS NOT NULL
              AND vault_item_ids IS NOT NULL
            """,
            creator_id,
            user_id,
        )
    if not rows:
        return frozenset()
    return frozenset(canonical_identity_ids([r["vault_item_id"] for r in rows]))


async def get_timing_context(creator_id: int, user_id: int) -> dict[str, Any]:
    """Compute cooldown timing fields for the deterministic decision engine.

    Returns a dict with:
    - hours_since_last_offer: float | None
    - hours_since_last_purchase: float | None
    - recent_offer_count: int (active offers in last 24h)
    - recent_purchase_count: int (purchases in last 24h)
    - recent_sales_attempt_count: int (all offers in last 24h)

    All queries are creator-scoped and time-bounded. Returns neutral defaults
    on DB failure (fail-closed: no fabricated timing data).
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            # Most recent offer for this user+creator.
            last_offer = await conn.fetchrow(
                """
                SELECT created_at, purchased_at
                FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                creator_id,
                user_id,
            )

            hours_since_last_offer: float | None = None
            hours_since_last_purchase: float | None = None

            if last_offer is not None:
                now = datetime.now(UTC)
                hours_since_last_offer = (
                    now - last_offer["created_at"]
                ).total_seconds() / 3600
                if last_offer["purchased_at"] is not None:
                    hours_since_last_purchase = (
                        now - last_offer["purchased_at"]
                    ).total_seconds() / 3600

            # 24-hour counts (all offers, active offers, purchases).
            counts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total_offers,
                    COUNT(*) FILTER (WHERE state = 'purchased') AS purchases,
                    COUNT(*) FILTER (WHERE state IN ('pending', 'clicked')) AS active_offers
                FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND created_at >= NOW() - INTERVAL '24 hours'
                """,
                creator_id,
                user_id,
            )

            return {
                "hours_since_last_offer": hours_since_last_offer,
                "hours_since_last_purchase": hours_since_last_purchase,
                "recent_offer_count": int(counts["active_offers"]) if counts else 0,
                "recent_purchase_count": int(counts["purchases"]) if counts else 0,
                "recent_sales_attempt_count": int(counts["total_offers"]) if counts else 0,
            }
    except Exception:
        logger.warning(
            "commerce dao: get_timing_context failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        raise


async def _query_aftercare_status(conn, creator_id: int, user_id: int) -> str:
    """Query the aftercare status of the most recent purchased offer.

    Returns 'none' if no purchased offer exists.
    """
    row = await conn.fetchrow(
        """
        SELECT aftercare_status FROM commerce_offers
        WHERE creator_id = $1 AND user_id = $2
          AND state = 'purchased'
        ORDER BY purchased_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """,
        creator_id,
        user_id,
    )
    return row["aftercare_status"] if row else "none"


async def get_behavioral_feedback_context(
    creator_id: int, user_id: int
) -> dict[str, Any]:
    """Query behavioral feedback context for the decision engine.

    Returns C.1-C feedback values:
    - consecutive_rejections: int
    - total_purchases: int (all time)
    - total_tips_received: int (if tracked)
    - hours_since_last_tip: float | None
    - tip_suggestions_sent: int
    - tip_suggestions_ignored: int

    All queries are creator-scoped. Returns neutral defaults on failure.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            # Count consecutive rejections from most recent offer backwards
            recent_offers = await conn.fetch(
                """
                SELECT state FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT 10
                """,
                creator_id,
                user_id,
            )
            consecutive_rejections = 0
            for offer in recent_offers:
                if offer["state"] in ("declined", "revoked"):
                    consecutive_rejections += 1
                elif offer["state"] == "purchased":
                    break  # Purchase resets rejection streak
                else:
                    break  # Other states also break the streak

            # Total purchases all time
            purchase_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased'
                """,
                creator_id,
                user_id,
            )

            # Tip history — now wired via tool_audit_log (suggest_tip proposals)
            # Previously hard-zero (P2-13 / P0-01). Now creator-scoped durable count.
            try:
                tip_sent = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM tool_audit_log
                    WHERE creator_id = $1 AND user_id = $2
                      AND tool_name = 'suggest_tip'
                      AND outcome = 'completed'
                      AND created_at >= NOW() - INTERVAL '30 days'
                    """,
                    creator_id, user_id,
                )
                tip_ignored = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM tool_audit_log
                    WHERE creator_id = $1 AND user_id = $2
                      AND tool_name = 'suggest_tip'
                      AND outcome = 'rejected'
                      AND created_at >= NOW() - INTERVAL '30 days'
                    """,
                    creator_id, user_id,
                )
                last_tip_at = await conn.fetchval(
                    """
                    SELECT MAX(created_at) FROM tool_audit_log
                    WHERE creator_id = $1 AND user_id = $2
                      AND tool_name = 'suggest_tip'
                      AND outcome = 'completed'
                    """,
                    creator_id, user_id,
                )
                hours_since_tip: float | None = None
                if last_tip_at is not None:
                    # last_tip_at is TIMESTAMPTZ
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    if last_tip_at.tzinfo is None:
                        last_tip_at = last_tip_at.replace(tzinfo=timezone.utc)
                    hours_since_tip = (now - last_tip_at).total_seconds() / 3600
            except Exception:
                tip_sent, tip_ignored, hours_since_tip = 0, 0, None

            return {
                "consecutive_rejections": consecutive_rejections,
                "total_purchases": int(purchase_count) if purchase_count else 0,
                "total_tips_received": 0,
                "hours_since_last_tip": hours_since_tip,
                "tip_suggestions_sent": int(tip_sent) if tip_sent else 0,
                "tip_suggestions_ignored": int(tip_ignored) if tip_ignored else 0,
                "aftercare_status": await _query_aftercare_status(conn, creator_id, user_id),
            }
    except Exception:
        logger.warning(
            "commerce dao: get_behavioral_feedback_context failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        raise


async def mark_offer_clicked(creator_id: int, offer_id: int) -> dict[str, Any] | None:
    """Transition an offer to 'clicked'. Returns the updated row, or None if
    the offer is not in 'pending' (already moved on / other creator)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'clicked', clicked_at = NOW()
            WHERE creator_id = $1 AND id = $2 AND state = 'pending'
            RETURNING *
            """,
            creator_id,
            offer_id,
        )
        return dict(row) if row else None


async def mark_offer_purchased(
    creator_id: int, offer_id: int, transaction_id: str
) -> dict[str, Any] | None:
    """Transition an offer to 'purchased' with its Fangate transaction.

    Only 'pending'/'clicked' offers transition, and only once per transaction:
    the partial unique index on transaction_id rejects a second offer claiming
    the same purchase. Idempotent for a retried delivery of the same transaction.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'purchased',
                purchased_at = NOW(),
                transaction_id = $3
            WHERE creator_id = $1 AND id = $2
              AND state IN ('pending', 'clicked')
              AND (transaction_id IS NULL OR transaction_id = $3)
            RETURNING *
            """,
            creator_id,
            offer_id,
            transaction_id,
        )
        return dict(row) if row else None


async def mark_offer_expired(creator_id: int, offer_id: int) -> dict[str, Any] | None:
    """Transition an unfulfilled offer to 'expired'. Returns the updated row,
    or None if the offer has already moved past 'pending'/'clicked'."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'expired'
            WHERE creator_id = $1 AND id = $2 AND state IN ('pending', 'clicked')
            RETURNING *
            """,
            creator_id,
            offer_id,
        )
        if row is None:
            return None
        # P3.5.1: terminal outcome linkage (isolated; reason untouched).
        try:
            from commerce.opportunity_ledger import record_offer_terminal_outcome
            await record_offer_terminal_outcome(
                creator_id=creator_id, offer_id=row["id"], outcome_state="EXPIRED"
            )
        except Exception:
            logger.debug("opportunity ledger expiry link failed (isolated)", exc_info=True)
        return dict(row)


async def expire_pending_offer_if_still_pending(creator_id: int, offer_id: int) -> dict[str, Any] | None:
    """Phase 93B ORANGE-1: expire ONLY pending (not clicked) orphan after EXECUTED + 2nd gen failure.

    Required condition per remediation: UPDATE ... SET state='expired' WHERE id=$1 AND state='pending'
    Never expires clicked/purchased/declined/already expired.  Creator-scoped via creator_id.
    Returns the updated row or None if not still pending.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'expired'
            WHERE creator_id = $1 AND id = $2 AND state = 'pending'
            RETURNING *
            """,
            creator_id,
            offer_id,
        )
        if row is None:
            return None
        # P3.5.1: terminal outcome linkage (isolated; reason untouched).
        try:
            from commerce.opportunity_ledger import record_offer_terminal_outcome
            await record_offer_terminal_outcome(
                creator_id=creator_id, offer_id=row["id"], outcome_state="EXPIRED"
            )
        except Exception:
            logger.debug("opportunity ledger expiry link failed (isolated)", exc_info=True)
        return dict(row)


async def mark_offer_declined(
    creator_id: int, user_id: int, reason: str = "fan_rejected"
) -> dict[str, Any] | None:
    """Transition the most recent active offer for a user to 'declined'.

    Identifies the active offer by (creator_id, user_id) with state IN
    ('pending', 'clicked'), ordered by most recent. Only transitions ONE offer.
    Idempotent: if no active offer exists, returns None.

    P3.5.1: sealed reason-envelope provenance is preserved — decline
    metadata is merged into a structured envelope (or appended to legacy
    text), never overwriting definition identity.

    Args:
        creator_id: Creator scope (never crosses creator boundaries).
        user_id: Fan scope.
        reason: Decline reason for audit trail.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        prev = await conn.fetchrow(
            """
            SELECT id, reason FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND state IN ('pending', 'clicked')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            creator_id,
            user_id,
        )
        if prev is None:
            return None
        try:
            from commerce.opportunity_ledger import merge_outcome_into_reason
            merged_reason = merge_outcome_into_reason(prev["reason"], "DECLINED", detail=reason)
        except Exception:
            logger.debug("decline reason merge failed, preserving raw reason", exc_info=True)
            merged_reason = reason
        row = await conn.fetchrow(
            """
            UPDATE commerce_offers
            SET state = 'declined', reason = $3
            WHERE creator_id = $1 AND id = $2 AND state IN ('pending', 'clicked')
            RETURNING *
            """,
            creator_id,
            prev["id"],
            merged_reason,
        )
        if row is None:
            return None
        # P3.5.1: terminal outcome linkage (isolated).
        try:
            from commerce.opportunity_ledger import record_offer_terminal_outcome
            await record_offer_terminal_outcome(
                creator_id=creator_id, offer_id=row["id"], outcome_state="DECLINED"
            )
        except Exception:
            logger.debug("opportunity ledger decline link failed (isolated)", exc_info=True)
        return dict(row)


async def attach_transaction_user(
    creator_id: int, transaction_id: str, user_id: int
) -> dict[str, Any] | None:
    """Attribute a purchase transaction to a fan.

    Atomic and non-overwriting: only sets user_id when it is still NULL, so two
    concurrent webhook deliveries cannot double-attribute, and an existing
    attribution is never silently replaced.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE fangate_transactions
            SET user_id = $3
            WHERE creator_id = $1 AND transaction_id = $2 AND user_id IS NULL
            RETURNING id, creator_id, transaction_id, event_type, product_id,
                      seller_earning, set_price, currency, occurred_at, user_id
            """,
            creator_id,
            transaction_id,
            user_id,
        )
        return dict(row) if row else None


# â”€â”€ PPV intelligence (Phase 5.2) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_ANALYTIC_COLUMNS = frozenset(
    {
        "offers_created",
        "offers_clicked",
        "offers_purchased",
        "offers_declined",
        "offers_expired",
        "offers_revoked",
        "revenue_minor",
    }
)

_STATE_TO_COLUMN = {
    "pending": "offers_created",
    "clicked": "offers_clicked",
    "purchased": "offers_purchased",
    "declined": "offers_declined",
    "expired": "offers_expired",
    "revoked": "offers_revoked",
}


async def record_eligibility_decision(
    creator_id: int,
    user_id: int,
    product_id: int,
    *,
    decision: bool,
    denial_reason: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one automated eligibility evaluation for audit. The inputs
    snapshot is JSONB and must never contain credentials."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ppv_eligibility_decisions
                (creator_id, user_id, product_id, decision, denial_reason, inputs)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, creator_id, user_id, product_id, decision,
                      denial_reason, evaluated_at
            """,
            creator_id,
            user_id,
            product_id,
            bool(decision),
            denial_reason,
            json.dumps(inputs or {}),
        )
        return dict(row)


async def list_eligibility_decisions(
    creator_id: int,
    *,
    user_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List audited eligibility evaluations for a creator, optionally for one fan."""
    clauses = ["creator_id = $1"]
    params: list[Any] = [creator_id]
    if user_id is not None:
        params.append(user_id)
        clauses.append(f"user_id = ${len(params)}")
    params.extend([limit, offset])
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, user_id, product_id, decision, denial_reason, evaluated_at
            FROM ppv_eligibility_decisions
            WHERE {" AND ".join(clauses)}
            ORDER BY evaluated_at DESC, id DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [dict(r) for r in rows]


async def increment_analytics_counter(
    creator_id: int,
    product_id: int,
    day: Any,
    column: str,
    amount: int = 1,
) -> None:
    """Atomically bump one counter in a daily PPV funnel rollup row.

    The column name is validated against a fixed whitelist; it is never
    built from caller input.
    """
    if column not in _ANALYTIC_COLUMNS:
        raise ValueError(f"Unknown analytics column: {column!r}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO ppv_analytics_daily (creator_id, product_id, day, {column})
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (creator_id, product_id, day) DO UPDATE SET
                {column} = ppv_analytics_daily.{column} + EXCLUDED.{column}
            """,
            creator_id,
            product_id,
            day,
            amount,
        )


async def record_offer_transition(
    creator_id: int,
    product_id: int,
    state: str,
    *,
    day: Any,
    revenue_minor: int = 0,
) -> str:
    """Bump the daily rollup counter corresponding to an offer transition.

    Returns the column name that was incremented.
    """
    column = _STATE_TO_COLUMN.get(state)
    if column is None:
        raise ValueError(f"Unknown offer state: {state!r}")
    await increment_analytics_counter(creator_id, product_id, day, column, 1)
    if state == "purchased" and revenue_minor:
        await increment_analytics_counter(
            creator_id, product_id, day, "revenue_minor", revenue_minor
        )
    return column


async def get_ppv_funnel(
    creator_id: int,
    *,
    start_date: Any = None,
    end_date: Any = None,
) -> list[dict[str, Any]]:
    """Aggregate the PPV funnel per product for a date range (inclusive)."""
    clauses = ["creator_id = $1"]
    params: list[Any] = [creator_id]
    if start_date is not None:
        params.append(start_date)
        clauses.append(f"day >= ${len(params)}")
    if end_date is not None:
        params.append(end_date)
        clauses.append(f"day <= ${len(params)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                product_id,
                COALESCE(SUM(offers_created), 0)   AS offers_created,
                COALESCE(SUM(offers_clicked), 0)   AS offers_clicked,
                COALESCE(SUM(offers_purchased), 0) AS offers_purchased,
                COALESCE(SUM(offers_declined), 0)  AS offers_declined,
                COALESCE(SUM(offers_expired), 0)   AS offers_expired,
                COALESCE(SUM(offers_revoked), 0)   AS offers_revoked,
                COALESCE(SUM(revenue_minor), 0)    AS revenue_minor
            FROM ppv_analytics_daily
            WHERE {" AND ".join(clauses)}
            GROUP BY product_id
            ORDER BY product_id
            """,
            *params,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Aftercare persistence
# ---------------------------------------------------------------------------

async def mark_aftercare_pending(creator_id: int, user_id: int) -> bool:
    """Set aftercare_status to 'pending' on the most recent purchased offer.

    Called after a purchase is confirmed. Only affects offers with state='purchased'
    that don't already have aftercare in progress. Returns True if updated.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE commerce_offers
            SET aftercare_status = 'pending'
            WHERE id = (
                SELECT id FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased'
                  AND aftercare_status = 'none'
                ORDER BY purchased_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            )
            """,
            creator_id,
            user_id,
        )
        return result.endswith("UPDATE 1")


async def mark_aftercare_completed(creator_id: int, user_id: int) -> bool:
    """Set aftercare_status to 'completed' on active aftercare offers.

    Called after the aftercare follow-up is sent. Transitions 'pending' or
    'sent' → 'completed'. Returns True if updated.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE commerce_offers
            SET aftercare_status = 'completed'
            WHERE id = (
                SELECT id FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased'
                  AND aftercare_status IN ('pending', 'sent')
                ORDER BY purchased_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            )
            """,
            creator_id,
            user_id,
        )
        return result.endswith("UPDATE 1")


async def get_recent_offered_product_ids(creator_id: int, user_id: int, hours: int = 24) -> set[int]:
    """Return set of product_ids offered to the fan in the last N hours.

    Used for per-product content fatigue: recently offered same product should be suppressed
    even if is_on_cooldown is not yet triggered for that specific product.
    Creator+user scoped, includes pending/clicked/declined.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT product_id FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND created_at >= NOW() - ($3 || ' hours')::interval
                  AND state IN ('pending', 'clicked', 'declined', 'expired')
                """,
                creator_id,
                user_id,
                str(hours),
            )
            return {int(r["product_id"]) for r in rows if r["product_id"] is not None}
    except Exception:
        logger.warning("get_recent_offered_product_ids failed for %s:%s", creator_id, user_id, exc_info=True)
        return set()


async def get_recent_offered_groups(creator_id: int, user_id: int, hours: int = 24) -> set[str]:
    """Return set of descriptive display groups for products offered in last N hours.

    P3.3.4 QUARANTINE: this helper derives title-inferred ``bundle_group``
    values for compatibility/observability only. A shared inferred group is
    descriptive similarity — NOT a commercial relationship, ownership,
    eligibility, fatigue, or suppression signal. Production ranking/selection
    (``commerce.content_matching.rank_products_by_relevance``,
    ``commerce.product_selection``) MUST ignore the returned set for
    commercial decisions; only actual per-product offer history
    (``get_recent_offered_product_ids``) may penalize relevance. Retained
    (not deleted) to preserve the public signature for callers/tests.
    """
    try:
        from commerce.vault_taxonomy import parse_taxonomy
        product_ids = await get_recent_offered_product_ids(creator_id, user_id, hours)
        if not product_ids:
            return set()
        pool = await get_pool()
        groups: set[str] = set()
        async with pool.acquire() as conn:
            for pid in product_ids:
                row = await conn.fetchrow("SELECT title FROM fangate_products WHERE id=$1 AND creator_id=$2", pid, creator_id)
                if row and row["title"]:
                    tax = parse_taxonomy(row["title"])
                    if tax.bundle_group:
                        groups.add(tax.bundle_group)
        return groups
    except Exception:
        logger.warning("get_recent_offered_groups failed for %s:%s", creator_id, user_id, exc_info=True)
        return set()


async def get_aftercare_status(creator_id: int, user_id: int) -> str:
    """Get the current aftercare status for a user.

    Returns the aftercare_status of the most recent purchased offer,
    or 'none' if no purchased offer exists.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT aftercare_status FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND state = 'purchased'
            ORDER BY purchased_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            creator_id,
            user_id,
        )
        return row["aftercare_status"] if row else "none"


# ── P2.1 First-Sale helpers ───────────────────────────────────────────────


async def get_first_sale_info(creator_id: int, user_id: int) -> dict[str, Any] | None:
    """Return first-sale ledger for a creator/user, or None if never purchased.

    KPI reconstruction source of truth: users.first_* + commerce_offers
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.id as user_id, u.funnel_stage, u.first_purchase_at, u.first_offer_id, u.first_transaction_id,
                   co.purchased_at, co.price_minor, co.currency, co.product_id
            FROM users u
            LEFT JOIN commerce_offers co ON co.id = u.first_offer_id
            WHERE u.id=$1
            """,
            user_id,
        )
        if not row or row["first_purchase_at"] is None:
            return None
        # Ensure creator owns the first offer (isolation)
        if row["first_offer_id"] is not None:
            offer_creator = await conn.fetchval("SELECT creator_id FROM commerce_offers WHERE id=$1", row["first_offer_id"])
            if offer_creator is not None and int(offer_creator) != int(creator_id):
                return None
        return dict(row)


async def list_ambiguous_recoveries(creator_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """List operator-visible ambiguous_offer recovery records for a creator."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ambiguous_purchase_recoveries WHERE creator_id=$1 ORDER BY created_at DESC LIMIT $2",
            creator_id, limit,
        )
        return [dict(r) for r in rows]


async def get_ambiguous_recovery(creator_id: int, transaction_id: str) -> dict[str, Any] | None:
    """Fetch a single ambiguous recovery record by creator+transaction_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM ambiguous_purchase_recoveries WHERE creator_id=$1 AND transaction_id=$2",
            creator_id, transaction_id,
        )
        return dict(row) if row else None


async def resolve_ambiguous_recovery(creator_id: int, transaction_id: str, chosen_offer_id: int) -> dict[str, Any] | None:
    """Operator/System resolution of an ambiguous payment: attribute to chosen offer idempotently.

    Validates chosen_offer_id is among candidates and still pending/clicked,
    then reuses attribute path. Marks recovery resolved_at.
    Returns PurchaseRecord-like dict on success, else None.
    """
    pool = await get_pool()
    # Verify recovery exists and not yet resolved
    rec = await get_ambiguous_recovery(creator_id, transaction_id)
    if not rec or rec["resolved_at"] is not None:
        return None
    candidate_ids = list(rec["candidate_offer_ids"] or [])
    if chosen_offer_id not in candidate_ids:
        return None
    # Fetch transaction product_id/occurred_at for attribution
    async with pool.acquire() as conn:
        txn = await conn.fetchrow(
            "SELECT product_id, occurred_at, buyer_email FROM fangate_transactions WHERE creator_id=$1 AND transaction_id=$2",
            creator_id, transaction_id,
        )
        if not txn or txn["product_id"] is None:
            return None
        product_id = int(txn["product_id"])
        occurred_at = txn["occurred_at"]
    # Determine revenue_minor from transaction seller_earning if present
    revenue_minor = 0
    try:
        async with pool.acquire() as conn:
            rev = await conn.fetchval("SELECT seller_earning FROM fangate_transactions WHERE creator_id=$1 AND transaction_id=$2", creator_id, transaction_id)
            if rev is not None:
                revenue_minor = int(round(float(rev) * 100))
    except Exception:
        pass
    # Narrow candidates by deleting other pending offers' ambiguity? Actually we will attempt to attribute
    # Forcing single-candidate by temporarily marking other candidates as expired? Instead reuse direct purchase transition for chosen offer.
    # Use has_purchased check + direct update for chosen offer (creator-scoped)
    try:
        from commerce.dao import mark_offer_purchased

        updated = await mark_offer_purchased(creator_id, chosen_offer_id, transaction_id)
        if not updated:
            return None
        # Attach user if needed (mark_offer_purchased does not attach transaction user; do here)
        user_id = updated["user_id"]
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE fangate_transactions SET user_id=$3 WHERE creator_id=$1 AND transaction_id=$2 AND user_id IS NULL",
                creator_id, transaction_id, user_id,
            )
            # Update first-sale ledger (same logic as dao funnel)
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"funnel:{creator_id}:{user_id}",
            )
            existing = await conn.fetchrow("SELECT first_purchase_at FROM users WHERE id=$1 FOR UPDATE", user_id)
            if existing and existing["first_purchase_at"] is None:
                _ts = occurred_at or updated.get("purchased_at")
                from datetime import UTC, datetime as _dt
                if _ts is None:
                    _ts = _dt.now(UTC)
                await conn.execute(
                    "UPDATE users SET funnel_stage='converted', first_purchase_at=$2, first_offer_id=$3, first_transaction_id=$4 WHERE id=$1 AND first_purchase_at IS NULL",
                    user_id, _ts, chosen_offer_id, transaction_id,
                )
                is_first = True
            else:
                await conn.execute("UPDATE users SET funnel_stage='converted' WHERE id=$1 AND funnel_stage<>'converted'", user_id)
                is_first = False
            # Mark recovery resolved
            await conn.execute(
                "UPDATE ambiguous_purchase_recoveries SET resolved_at=NOW(), resolved_offer_id=$3 WHERE creator_id=$1 AND transaction_id=$2",
                creator_id, transaction_id, chosen_offer_id,
            )
            # Emit sale event
            try:
                from core.event_bus import publish_event as _pub
                await _pub(
                    "commerce.sale_recorded",
                    {"offer_id": chosen_offer_id, "creator_id": creator_id, "user_id": user_id, "product_id": product_id, "transaction_id": transaction_id, "price_minor": updated.get("price_minor"), "currency": updated.get("currency"), "revenue_minor": revenue_minor, "first_sale": is_first, "occurred_at": str(occurred_at) if occurred_at else None},
                    creator_id=creator_id, user_id=user_id, scope="user",
                )
            except Exception:
                pass
            # Trigger post-purchase
            from commerce.models import PurchaseRecord
            from commerce.post_purchase import handle_post_purchase
            rec2 = PurchaseRecord(offer_id=chosen_offer_id, creator_id=creator_id, user_id=user_id, transaction_id=transaction_id, product_id=product_id, occurred_at=occurred_at)
            try:
                await handle_post_purchase(rec2)
            except Exception:
                pass
            return dict(updated)
    except Exception:
        logger.warning("resolve_ambiguous_recovery failed creator=%s txn=%s", creator_id, transaction_id, exc_info=True)
        return None
