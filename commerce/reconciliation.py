"""Purchase attribution reconciliation.

Dropfans sale polling + unattributed purchase reconciliation.

When a Dropfans sale is detected via polling, it is persisted but may not
have a matching pending offer. This module provides:

1. Dropfans sale polling (via scheduler worker)
2. Unattributed purchase reconciliation (matches pending offers)

Triggered by the existing scheduler worker on each poll cycle.

Invariants:
- Creator isolation: queries are scoped by creator_id.
- Idempotent: running reconciliation twice produces no duplicate fulfillment.
- Bounded: processes at most BATCH_SIZE transactions per cycle.
- Fail-closed: when identity cannot be established, the transaction remains
  unattributed.
- PostgreSQL remains authoritative: vault delivery reservations, offer state
  transitions, and analytics are all driven by existing mechanisms.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.reconciliation")

# Bounded batch: process at most N unattributed transactions per cycle.
BATCH_SIZE = 50

# Only reconcile transactions from the last 7 days. Older unattributed
# transactions are left for manual investigation.
RECONCILIATION_WINDOW_HOURS = 7 * 24


async def reconcile_unattributed_purchases() -> int:
    """Find and attribute unattributed Fangate purchases to pending offers.

    Returns the number of successfully attributed transactions.

    Designed to be called periodically by the scheduler worker. Each call:
    1. Queries for unattributed transactions (user_id IS NULL, product_id NOT NULL)
    2. For each, attempts to find exactly one pending offer for the same creator+product
    3. If found, transitions the offer to 'purchased' and attaches the user_id
    4. Triggers post-purchase fulfillment for each successful attribution

    Idempotent: the offer state transition is conditional (only pending/clicked),
    so re-running on an already-attributed transaction is a no-op.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            unattributed = await conn.fetch(
                """
                SELECT id, creator_id, transaction_id, product_id, occurred_at
                FROM fangate_transactions
                WHERE user_id IS NULL
                  AND product_id IS NOT NULL
                  AND created_at >= NOW() - CAST($1 AS numeric) * INTERVAL '1 hour'
                ORDER BY created_at ASC
                LIMIT $2
                """,
                RECONCILIATION_WINDOW_HOURS,
                BATCH_SIZE,
            )
    except Exception:
        logger.warning(
            "reconciliation: failed to query unattributed transactions",
            exc_info=True,
        )
        return 0

    if not unattributed:
        return 0

    attributed_count = 0

    for txn in unattributed:
        creator_id = txn["creator_id"]
        transaction_id = txn["transaction_id"]
        product_id = txn["product_id"]
        occurred_at = txn["occurred_at"]

        try:
            success = await _reconcile_single(
                creator_id=creator_id,
                transaction_id=transaction_id,
                product_id=product_id,
                occurred_at=occurred_at,
            )
            if success:
                attributed_count += 1
        except Exception:
            logger.warning(
                "reconciliation: failed to reconcile txn=%s creator=%s product=%s",
                transaction_id,
                creator_id,
                product_id,
                exc_info=True,
            )

    if attributed_count > 0:
        logger.info(
            "reconciliation: attributed %d of %d unattributed transactions",
            attributed_count,
            len(unattributed),
        )

    return attributed_count


async def _reconcile_single(
    *,
    creator_id: int,
    transaction_id: str,
    product_id: int,
    occurred_at: datetime | None,
) -> bool:
    """Attempt to attribute a single unattributed transaction.

    Returns True if attribution succeeded, False otherwise.
    Uses the same atomic logic as attribute_purchase_from_webhook but
    operates on the transaction's own creator_id for isolation.
    P2.1: buyer_email disambiguation + ambiguous recovery parity with dao.
    """
    from commerce.post_purchase import handle_post_purchase

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Find all pending/clicked offers for this creator+product.
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
            # No matching offer yet — skip, try again later.
            return False

        if len(candidates) > 1:
            try:
                from commerce.dao import _disambiguate_via_buyer_identity as _disamb, _record_ambiguous_recovery as _rec
                disambiguated = await _disamb(conn, creator_id, list(candidates), transaction_id)
                if disambiguated is not None:
                    candidates = [disambiguated]
                else:
                    logger.info(
                        "reconciliation: creator=%s product=%s txn=%s — %d pending offers, "
                        "ambiguous (fail-closed)",
                        creator_id,
                        product_id,
                        transaction_id,
                        len(candidates),
                    )
                    try:
                        await _rec(conn, creator_id, transaction_id, product_id, list(candidates), occurred_at=occurred_at)
                    except Exception:
                        logger.debug("reconciliation ambiguous recovery failed", exc_info=True)
                    return False
            except Exception:
                logger.info(
                    "reconciliation: creator=%s product=%s txn=%s — %d pending offers, "
                    "ambiguous (fail-closed)",
                    creator_id,
                    product_id,
                    transaction_id,
                    len(candidates),
                )
                return False

        offer = candidates[0]
        user_id = offer["user_id"]
        offer_id = offer["id"]

        # Transition offer to 'purchased' (conditional: only pending/clicked).
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
            return False

        # Attach the Telegram user to the transaction (non-overwriting).
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

        # Record analytics (idempotent UPSERT).
        day = (occurred_at or datetime.now(UTC)).date()
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

        # P2.1 D2: atomic first-sale ledger — same transaction, funnel lock
        is_first_sale_rec = False
        try:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"funnel:{creator_id}:{user_id}",
            )
            existing = await conn.fetchrow(
                "SELECT funnel_stage, first_purchase_at, first_offer_id FROM users WHERE id=$1 FOR UPDATE",
                user_id,
            )
            if existing is not None and existing["first_purchase_at"] is None:
                _ts = occurred_at or updated["purchased_at"]
                if _ts is None:
                    from datetime import datetime as _dt2
                    _ts = _dt2.now(UTC)
                res = await conn.execute(
                    """
                    UPDATE users
                    SET funnel_stage='converted',
                        first_purchase_at=$2,
                        first_offer_id=$3,
                        first_transaction_id=$4
                    WHERE id=$1 AND first_purchase_at IS NULL
                    """,
                    user_id, _ts, updated["id"], transaction_id,
                )
                if res.endswith("UPDATE 1"):
                    is_first_sale_rec = True
            else:
                if existing is not None and existing["funnel_stage"] != "converted":
                    await conn.execute("UPDATE users SET funnel_stage='converted' WHERE id=$1 AND funnel_stage<>'converted'", user_id)
        except Exception:
            logger.warning("reconciliation first-sale ledger failed creator=%s user=%s", creator_id, user_id, exc_info=True)

        logger.info(
            "reconciliation: creator=%s user=%s product=%s txn=%s offer=%s — attributed first_sale=%s",
            creator_id,
            user_id,
            product_id,
            transaction_id,
            offer_id,
            is_first_sale_rec,
        )

        # P2.1 D5: best-effort sale_recorded event
        try:
            from core.event_bus import publish_event as _pub2
            _price2 = updated.get("price_minor") if isinstance(updated, dict) else None
            _curr2 = updated.get("currency") if isinstance(updated, dict) else None
            await _pub2(
                "commerce.sale_recorded",
                {
                    "offer_id": updated["id"],
                    "creator_id": creator_id,
                    "user_id": user_id,
                    "product_id": product_id,
                    "transaction_id": transaction_id,
                    "price_minor": _price2,
                    "currency": _curr2,
                    "revenue_minor": 0,
                    "first_sale": is_first_sale_rec,
                    "occurred_at": str(occurred_at) if occurred_at else None,
                },
                creator_id=creator_id,
                user_id=user_id,
                scope="user",
            )
        except Exception:
            logger.debug("reconciliation sale_recorded publish failed", exc_info=True)

        # P3.5.1: resolve the purchase to its opportunity ledger row (isolated).
        try:
            from commerce.opportunity_ledger import record_purchase_by_offer
            _price_rec = updated.get("price_minor") if isinstance(updated, dict) else None
            _curr_rec = updated.get("currency") if isinstance(updated, dict) else None
            await record_purchase_by_offer(
                creator_id=creator_id,
                offer_id=offer_id,
                transaction_id=transaction_id,
                price_minor=_price_rec,
                currency=_curr_rec,
            )
        except Exception:
            logger.debug("reconciliation opportunity ledger link failed (isolated)", exc_info=True)

    # Post-purchase fulfillment (outside the transaction, best-effort).
    from commerce.models import PurchaseRecord

    record = PurchaseRecord(
        offer_id=offer_id,
        creator_id=creator_id,
        user_id=user_id,
        transaction_id=transaction_id,
        product_id=product_id,
        occurred_at=occurred_at,
    )

    try:
        await handle_post_purchase(record)
    except Exception:
        logger.warning(
            "reconciliation: post-purchase fulfillment failed txn=%s creator=%s",
            transaction_id,
            creator_id,
            exc_info=True,
        )
        # Fail-closed: fulfillment failure means the purchase is not fully handled.
        # Return False so reconciliation retries on the next cycle.
        return False

    return True


# ---------------------------------------------------------------------------
# Dropfans sale polling + reconciliation
# ---------------------------------------------------------------------------


async def reconcile_dropfans_sales() -> int:
    """Poll Dropfans for new sales and record them idempotently.

    Designed to be called by the scheduler worker on each poll cycle.
    Returns the number of newly recorded sales.

    Dropfans is the sole active commerce provider.
    """
    try:
        from db import dropfans as ddb
        from db import fangate as fdb
        from integrations.dropfans import service as df_service
        from integrations.dropfans.errors import DropfansError

        # Find all creators with Dropfans integrations
        creator_ids = await fdb.list_active_creator_ids()
        if not creator_ids:
            return 0

        total_new = 0
        for cid in creator_ids:
            try:
                integration = await ddb.get_dropfans_integration(cid)
                if not integration or not integration.get("dropfans_creator_id"):
                    continue
                result = await df_service.reconcile_sales(cid)
                total_new += result.get("newly_recorded", 0)
            except DropfansError:
                logger.debug("Dropfans reconciliation skipped for creator %s", cid)
            except Exception:
                logger.warning(
                    "Dropfans reconciliation failed for creator %s", cid, exc_info=True
                )

        if total_new > 0:
            logger.info("reconciliation: recorded %d new Dropfans sales", total_new)
        return total_new
    except Exception:
        logger.warning("reconciliation: Dropfans polling failed", exc_info=True)
        return 0


async def check_sale_now_for_user(
    creator_id: int,
    user_id: int,
    limit: int = 10,
) -> dict[str, dict[str, Any]]:
    """Targeted on-demand sale check for one fan's open offers (P3-A).

    Narrowest safe caller around ``check_sale_now``: resolves the fan's
    pending/clicked offers to Dropfans product CUIDs and checks only those.
    Read-only: calls check-status only (no earnings), performs no DB writes
    and mutates no commerce authority. Returns ``{dropfans_product_id: sale}``.
    """
    from typing import Any as _Any

    if not isinstance(creator_id, int) or creator_id <= 0:
        raise ValueError("creator_id is required for check_sale_now_for_user")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id is required for check_sale_now_for_user")
    try:
        from commerce import dao as _dao
        from db import fangate as _fdb
        from integrations.dropfans import service as _df_service

        pending: list[dict[str, _Any]] = []
        try:
            pending.extend(
                await _dao.list_offers_for_user(
                    user_id, creator_id=creator_id, state="pending", limit=limit
                )
            )
        except Exception:
            logger.debug("check_sale_now_for_user pending lookup failed", exc_info=True)
        try:
            pending.extend(
                await _dao.list_offers_for_user(
                    user_id, creator_id=creator_id, state="clicked", limit=limit
                )
            )
        except Exception:
            logger.debug("check_sale_now_for_user clicked lookup failed", exc_info=True)
        if not pending:
            return {}
        # Map internal product IDs to Dropfans CUIDs (authoritative binding).
        cuid_by_internal: dict[int, str] = {}
        for offer in pending[:limit]:
            try:
                pid = offer.get("product_id")
                if pid is None:
                    continue
                product = await _fdb.get_fangate_product(creator_id, pid)
                if not product:
                    continue
                raw = product.get("raw", {}) or {}
                if isinstance(raw, str):
                    import json as _json

                    try:
                        raw = _json.loads(raw)
                    except Exception:
                        raw = {}
                cuid = raw.get("dropfans_product_id")
                if cuid and str(cuid).strip():
                    cuid_by_internal[int(pid)] = str(cuid).strip()
            except Exception:
                continue
        if not cuid_by_internal:
            return {}
        return await _df_service.check_sale_now(
            creator_id, list(cuid_by_internal.values())
        )
    except (ValueError, TypeError):
        raise
    except Exception:
        logger.debug("check_sale_now_for_user failed", exc_info=True)
        return {}


async def compute_attribution_divergence(
    creator_id: int,
    product_id: int | None = None,
    day: Any | None = None,
) -> dict[str, Any]:
    """Read-only attribution/reconciliation divergence indicator (P3-E, P3.1 F-01/F-03).

    Compares gross recorded Dropfans sales (``fangate_transactions``,
    event_type ``dropfans_sale``) against attributed purchases
    (``commerce_offers`` state ``purchased``) for the same
    creator/product/day scope.

    This is a *reconciliation/attribution* signal — it is NOT a refund or
    chargeback count. A positive divergence can represent attribution lag,
    unattributed purchases, ``occurred_at`` vs ``purchased_at`` date-boundary
    timing differences, or other reconciliation mismatch. It must never be
    interpreted as confirmed refunds/chargebacks, and this helper performs
    no writes to offers, funnel, first-sale or entitlement state (metric
    emission only, best-effort).
    """
    from datetime import date as _date

    if not isinstance(creator_id, int) or creator_id <= 0:
        raise ValueError("creator_id is required for compute_attribution_divergence")
    pool = await get_pool()
    day_value = day
    try:
        gross = 0
        net = 0
        gross_revenue_minor = 0
        async with pool.acquire() as conn:
            if product_id is not None and day_value is not None:
                gross = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM fangate_transactions
                    WHERE creator_id = $1 AND event_type = 'dropfans_sale'
                      AND product_id = $2 AND occurred_at::date = $3::date
                    """,
                    creator_id,
                    int(product_id),
                    str(day_value),
                ) or 0
                net = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM commerce_offers
                    WHERE creator_id = $1 AND product_id = $2
                      AND state = 'purchased' AND purchased_at::date = $3::date
                    """,
                    creator_id,
                    int(product_id),
                    str(day_value),
                ) or 0
                try:
                    gross_revenue_minor = await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(seller_earning * 100), 0)
                        FROM fangate_transactions
                        WHERE creator_id = $1 AND event_type = 'dropfans_sale'
                          AND product_id = $2 AND occurred_at::date = $3::date
                        """,
                        creator_id,
                        int(product_id),
                        str(day_value),
                    ) or 0
                except Exception:
                    gross_revenue_minor = 0
            elif product_id is not None:
                gross = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM fangate_transactions
                    WHERE creator_id = $1 AND event_type = 'dropfans_sale'
                      AND product_id = $2
                    """,
                    creator_id,
                    int(product_id),
                ) or 0
                net = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM commerce_offers
                    WHERE creator_id = $1 AND product_id = $2
                      AND state = 'purchased'
                    """,
                    creator_id,
                    int(product_id),
                ) or 0
            else:
                gross = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM fangate_transactions
                    WHERE creator_id = $1 AND event_type = 'dropfans_sale'
                    """,
                    creator_id,
                ) or 0
                net = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM commerce_offers
                    WHERE creator_id = $1 AND state = 'purchased'
                    """,
                    creator_id,
                ) or 0
        divergence = int(gross or 0) - int(net or 0)
        result: dict[str, Any] = {
            "creator_id": creator_id,
            "product_id": product_id,
            "day": str(day_value) if day_value is not None else None,
            "gross_count": int(gross or 0),
            "net_purchased_count": int(net or 0),
            "divergence": divergence,
            "gross_revenue_minor": int(gross_revenue_minor or 0),
        }
        # Best-effort observability via existing metrics infra (no DB writes).
        try:
            from commerce.production_control import record_metric as _record

            _record(
                name="commerce.attribution_divergence",
                creator_id=creator_id,
                product_family=f"product:{product_id}" if product_id is not None else None,
                topic=str(day_value) if day_value is not None else None,
                value=float(divergence),
            )
        except Exception:
            logger.debug("attribution divergence metric failed", exc_info=True)
        return result
    except (ValueError, TypeError):
        raise
    except Exception:
        logger.debug("compute_attribution_divergence failed", exc_info=True)
        return {
            "creator_id": creator_id,
            "product_id": product_id,
            "day": str(day_value) if day_value is not None else None,
            "gross_count": 0,
            "net_purchased_count": 0,
            "divergence": 0,
            "gross_revenue_minor": 0,
        }


async def compute_refund_divergence(
    creator_id: int,
    product_id: int | None = None,
    day: Any | None = None,
) -> dict[str, Any]:
    """Deprecated alias of :func:`compute_attribution_divergence` (P3.1 F-03).

    Retained for backward compatibility only. The quantity measured was
    never a literal refund count; new production code must use
    ``compute_attribution_divergence`` and the
    ``commerce.attribution_divergence`` metric.
    """
    return await compute_attribution_divergence(
        creator_id, product_id=product_id, day=day
    )


async def emit_attribution_divergence_snapshot(*, limit: int = 5) -> int:
    """Bounded best-effort attribution-divergence snapshot (P3.1 F-01).

    Derives a small set of distinct recent (creator, product, day) scopes
    from already-recorded ``dropfans_sale`` rows and emits one read-only
    divergence metric per scope. No Dropfans API calls, no purchase/offer/
    entitlement writes. Failures are swallowed (returns count emitted).
    """
    cap = max(1, int(limit or 1))
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT creator_id, product_id, occurred_at::date AS day
                FROM fangate_transactions
                WHERE event_type = 'dropfans_sale'
                ORDER BY occurred_at DESC
                LIMIT $1
                """,
                cap * 4,
            )
    except Exception:
        logger.debug("attribution divergence snapshot scope query failed", exc_info=True)
        return 0
    emitted = 0
    seen: set[tuple[int, Any, str]] = set()
    for row in rows or []:
        try:
            key = (int(row["creator_id"]), row.get("product_id"), str(row.get("day")))
        except (TypeError, ValueError, AttributeError):
            continue
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > cap:
            break
        try:
            await compute_attribution_divergence(
                key[0], product_id=key[1], day=key[2]
            )
            emitted += 1
        except Exception:
            logger.debug("attribution divergence snapshot emit failed", exc_info=True)
            continue
    return emitted


async def recover_incomplete_post_purchases(limit: int = 10) -> int:
    """P2.1 D4: Re-enter post-purchase for purchased offers where funnel/first-sale is incomplete.

    Finds bounded purchased offers whose creator/user still has funnel_stage <> 'converted'
    or first_purchase_at IS NULL, and safely re-calls handle_post_purchase idempotently.
    Uses existing dedup (post_purchase:{tx}:{user}, scheduled dedup, send dedup).
    Returns count of re-entered records.
    Best-effort, fail-open, does not backfill historic data beyond bound.
    """
    pool = await get_pool()
    try:
        rows = await pool.fetch(
            """
            SELECT co.id as offer_id, co.creator_id, co.user_id, co.product_id, co.transaction_id, co.purchased_at,
                   u.funnel_stage, u.first_purchase_at
            FROM commerce_offers co
            JOIN users u ON u.id = co.user_id
            WHERE co.state='purchased' AND co.transaction_id IS NOT NULL
              AND (u.funnel_stage <> 'converted' OR u.first_purchase_at IS NULL)
              AND co.purchased_at >= NOW() - INTERVAL '30 days'
            ORDER BY co.purchased_at ASC
            LIMIT $1
            """,
            limit,
        )
    except Exception:
        logger.warning("recover_incomplete_post_purchases query failed", exc_info=True)
        return 0
    if not rows:
        return 0
    reentered = 0
    from commerce.models import PurchaseRecord
    from commerce.post_purchase import handle_post_purchase
    for r in rows:
        try:
            # Acquire funnel lock per candidate to avoid concurrent re-entry races
            # Use a short per-row transaction
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"funnel:{r['creator_id']}:{r['user_id']}",
                    )
            except Exception:
                pass
            record = PurchaseRecord(
                offer_id=r["offer_id"],
                creator_id=r["creator_id"],
                user_id=r["user_id"],
                transaction_id=r["transaction_id"],
                product_id=r["product_id"],
                occurred_at=r["purchased_at"],
            )
            await handle_post_purchase(record)
            reentered += 1
        except Exception:
            logger.warning(
                "recover_incomplete_post_purchases failed offer=%s txn=%s",
                r["offer_id"], r["transaction_id"], exc_info=True
            )
    if reentered:
        logger.info("recover_incomplete_post_purchases re-entered %d records", reentered)
    return reentered


async def reconcile_all() -> int:
    """Run all reconciliation: Dropfans sales + unattributed purchases + incomplete post-purchase recovery.

    Designed to be the single entry point for the scheduler worker.
    """
    dropfans_new = await reconcile_dropfans_sales()
    attributed = await reconcile_unattributed_purchases()
    # P2.1 D4: bounded recovery for crash-after-commit without funnel
    try:
        recovered = await recover_incomplete_post_purchases(limit=10)
    except Exception:
        recovered = 0
        logger.debug("recover_incomplete_post_purchases failed", exc_info=True)
    # P3.1 F-01: bounded best-effort attribution-divergence snapshot.
    # Read-only (existing rows + metric emission only); must never fail
    # reconciliation and must not add Dropfans API calls or state changes.
    try:
        await emit_attribution_divergence_snapshot(limit=5)
    except Exception:
        logger.debug("emit_attribution_divergence_snapshot failed", exc_info=True)
    # P3.5.2: bounded orphan decision-ledger recovery (observability repair
    # only; isolated; count kept out of the int total to preserve the
    # attribution-count contract — see summary log instead).
    try:
        from commerce.opportunity_recovery import recover_orphan_decisions as _recover_orphans

        _orphan_summary = await _recover_orphans(limit=25, dry_run=False)
        logger.info("reconciliation: orphan decision recovery %s", _orphan_summary)
    except Exception:
        logger.debug("orphan decision recovery failed (isolated)", exc_info=True)
    return dropfans_new + attributed + recovered
