"""P3.3.8 — FanCommercialState (deterministic observed fan facts, read-only).

Read-only representation of observed commercial behavior for one
creator + one fan. Input to the future Opportunity Engine — never an
opportunity, pricing, affordability, pressure, or LLM artifact.

Authoritative rules (P3.3.8 audit):

- Purchase means ``state = 'purchased' AND transaction_id IS NOT NULL`` on
  ``commerce_offers`` (the P3.3.1 predicate). Nothing else counts.
- Spend means ``SUM(commerce_offers.price_minor)`` — the immutable
  buyer-facing snapshot. Never ``seller_earning``, ``set_price``, segment
  spend, or analytics revenue. NULL prices contribute nothing but the row
  still counts as a purchase.
- Money is USD-only and enforced downstream; rows with a non-USD, non-NULL
  currency are excluded from monetary totals (never converted, never mixed)
  and surfaced via a warning. Legacy NULL-currency rows predate the currency
  column on a USD-enforced path and are included as USD-denominated.
- Recency uses the established 24-hour UTC window on ``purchased_at``
  (purchases) and ``created_at`` (offers) — the same boundary convention as
  the existing timing/history readers.
- Ownership reuses the P3.3.1 primitive verbatim (no duplicated SQL).
  Delivery reuses fulfillment authority (``vault_media_deliveries``,
  ``status = 'sent'`` CUIDs only). Purchase authority and fulfillment
  authority stay separate: delivery never creates ownership.
- There is no authoritative "accepted" state (``clicked`` is engagement),
  no creator-safe first-sale source (``users.first_*`` is global), and no
  refund/chargeback accounting — those fields stay deferred, and strict
  purchased rows are never retroactively subtracted.
- Any database/source failure raises. ``[]``/zero state means "successfully
  queried, no observed activity" — never a masked failure. No partial
  zero-filled state is ever returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.fan_commercial_state")

RECENT_WINDOW_HOURS = 24

STATE_CURRENCY = "USD"


@dataclass(frozen=True)
class FanCommercialState:
    """Observed commercial facts for one creator + one fan (immutable).

    Only observed/derived commercial facts. No affordability, probability,
    pressure, scores, recommendations, segments, or LLM output.
    """

    creator_id: int
    user_id: int

    purchase_count: int
    total_spend_minor: int
    average_order_value_minor: int | None
    highest_purchase_minor: int | None
    last_purchase_at: datetime | None

    recent_purchase_count: int
    recent_spend_minor: int

    purchased_vault_ids: frozenset[str]
    delivered_vault_ids: tuple[str, ...]

    recent_offer_count: int
    recent_rejected_offer_count: int
    last_offer_at: datetime | None
    recent_offered_vault_ids: tuple[str, ...]

    currency: str = STATE_CURRENCY


def _require_ids(creator_id: int, user_id: int) -> None:
    for name, value in (("creator_id", creator_id), ("user_id", user_id)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} is required")


def _canonical_tuple(raw_ids: Any) -> tuple[str, ...]:
    """Canonicalize Vault CUIDs into a deterministically ordered tuple."""
    from commerce.vault_sets import canonical_identity_ids

    try:
        return tuple(canonical_identity_ids(list(raw_ids or [])))
    except ValueError:
        return ()


async def get_fan_commercial_state(creator_id: int, user_id: int) -> FanCommercialState:
    """Read one fan's observed commercial facts (creator-scoped, read-only).

    Runs a fixed set of bounded, creator+user-scoped reads (purchase
    aggregate, offer aggregate, recent snapshot CUIDs, delivered CUIDs) plus
    the P3.3.1 ownership primitive. No N+1 queries, no writes, no cache, no
    network calls. Raises on any source failure — never zero-filled.
    """
    _require_ids(creator_id, user_id)
    from commerce.ownership import fetch_owned_vault_ids

    pool = await get_pool()
    async with pool.acquire() as conn:
        purchase = await conn.fetchrow(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(price_minor) FILTER (
                       WHERE price_minor IS NOT NULL
                         AND (currency IS NULL OR currency = 'USD')), 0) AS spend,
                   MAX(price_minor) FILTER (
                       WHERE price_minor IS NOT NULL
                         AND (currency IS NULL OR currency = 'USD')) AS highest,
                   MAX(purchased_at) AS last_at,
                   COUNT(*) FILTER (
                       WHERE currency IS NOT NULL AND currency <> 'USD') AS non_usd
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND state = 'purchased' AND transaction_id IS NOT NULL
            """,
            creator_id,
            user_id,
        )
        recent_purchase = await conn.fetchrow(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(price_minor) FILTER (
                       WHERE price_minor IS NOT NULL
                         AND (currency IS NULL OR currency = 'USD')), 0) AS spend
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND state = 'purchased' AND transaction_id IS NOT NULL
              AND purchased_at >= NOW() - INTERVAL '24 hours'
            """,
            creator_id,
            user_id,
        )
        offers = await conn.fetchrow(
            """
            SELECT COUNT(*) FILTER (
                       WHERE created_at >= NOW() - INTERVAL '24 hours') AS recent,
                   COUNT(*) FILTER (
                       WHERE state = 'declined'
                         AND created_at >= NOW() - INTERVAL '24 hours') AS rejected,
                   MAX(created_at) AS last_at
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
            """,
            creator_id,
            user_id,
        )
        snapshot_rows = await conn.fetch(
            """
            SELECT DISTINCT v AS vault_item_id
            FROM commerce_offers, unnest(vault_item_ids) AS v
            WHERE creator_id = $1 AND user_id = $2
              AND created_at >= NOW() - INTERVAL '24 hours'
              AND vault_item_ids IS NOT NULL
            """,
            creator_id,
            user_id,
        )
        delivered_rows = await conn.fetch(
            """
            SELECT DISTINCT dropfans_vault_item_id AS vault_item_id
            FROM vault_media_deliveries
            WHERE creator_id = $1 AND user_id = $2
              AND status = 'sent'
              AND dropfans_vault_item_id IS NOT NULL
              AND btrim(dropfans_vault_item_id) <> ''
            """,
            creator_id,
            user_id,
        )
    if purchase is None or recent_purchase is None or offers is None:
        raise RuntimeError("fan commercial state unavailable: empty aggregate result")
    if int(purchase["non_usd"] or 0) > 0:
        logger.warning(
            "fan commercial state: %s non-USD purchase row(s) excluded from "
            "USD totals creator=%s user=%s",
            int(purchase["non_usd"]),
            creator_id,
            user_id,
        )
    purchased_vault_ids = await fetch_owned_vault_ids(creator_id, user_id)

    purchase_count = int(purchase["n"] or 0)
    total_spend = int(purchase["spend"] or 0)
    highest_raw = purchase["highest"]
    return FanCommercialState(
        creator_id=creator_id,
        user_id=user_id,
        purchase_count=purchase_count,
        total_spend_minor=total_spend,
        average_order_value_minor=(total_spend // purchase_count if purchase_count else None),
        highest_purchase_minor=int(highest_raw) if highest_raw is not None else None,
        last_purchase_at=purchase["last_at"],
        recent_purchase_count=int(recent_purchase["n"] or 0),
        recent_spend_minor=int(recent_purchase["spend"] or 0),
        purchased_vault_ids=purchased_vault_ids,
        delivered_vault_ids=_canonical_tuple(
            [r["vault_item_id"] for r in delivered_rows]
        ),
        recent_offer_count=int(offers["recent"] or 0),
        recent_rejected_offer_count=int(offers["rejected"] or 0),
        last_offer_at=offers["last_at"],
        recent_offered_vault_ids=_canonical_tuple(
            [r["vault_item_id"] for r in snapshot_rows]
        ),
    )
