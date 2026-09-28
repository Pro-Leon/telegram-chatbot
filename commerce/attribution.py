"""Purchase attribution coordination (Phase 5.2).

Binds a Fangate purchase transaction to the fan who owns the matching active
offer, transitions that offer to 'purchased', attaches the fan to the
transaction row, and updates the daily PPV analytics rollup.

Deterministic and idempotent: relies on the DAO's conditional UPDATEs and the
partial unique index on transaction_id.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from commerce.dao import (
    attach_transaction_user,
    find_pending_offer_for_product,
    mark_offer_purchased,
    record_offer_transition,
)
from commerce.models import PurchaseRecord

logger = logging.getLogger("commerce.attribution")


async def attribute_purchase(
    *,
    creator_id: int,
    user_id: int,
    product_id: int,
    transaction_id: str,
    revenue_minor: int = 0,
    occurred_at: Any = None,
) -> PurchaseRecord | None:
    """Attribute a purchase to the fan owning the active offer for the product.

    Returns a PurchaseRecord when the attribution succeeded, or None when no
    active offer exists or the transaction was already claimed elsewhere.
    """
    offer = await find_pending_offer_for_product(creator_id, user_id, product_id)
    if offer is None:
        return None

    updated = await mark_offer_purchased(creator_id, offer["id"], transaction_id)
    if updated is None:
        return None  # offer already moved on, or transaction claimed elsewhere

    await attach_transaction_user(creator_id, transaction_id, user_id)

    day = (occurred_at or datetime.now(UTC)).date()
    await record_offer_transition(
        creator_id, product_id, "purchased", day=day, revenue_minor=revenue_minor
    )

    return PurchaseRecord(
        offer_id=updated["id"],
        creator_id=creator_id,
        user_id=user_id,
        transaction_id=transaction_id,
        product_id=product_id,
        occurred_at=occurred_at,
    )
