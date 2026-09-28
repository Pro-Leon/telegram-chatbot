"""Vault service layer.

Thin orchestration over Dropfans service (vault/drop operations) and
db.vault (delivery history). Dropfans is the sole active commerce provider.
Fangate is legacy-only.
"""

from __future__ import annotations

import logging
from typing import Any

from db import fangate as fdb
from db import vault as vdb
from integrations.dropfans import service as df_service
from integrations.dropfans.errors import DropfansError
from vault.aggregate import aggregate_media_from_products, products_to_vault_products
from vault.models import VaultDeliveryRecord, VaultMediaItem

logger = logging.getLogger("vault.service")


async def list_media(
    creator_id: int,
    *,
    user_id: int | None = None,
    product_id: int | None = None,
    folder_id: str | None = None,
    collection_only: bool = False,
    media_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List aggregated media items from the provider-neutral product mirror.

    Pagination is product-level (offset applies to products, not media items).
    """
    products = await fdb.list_fangate_products(creator_id, limit=limit, offset=offset)
    total_products = await fdb.count_fangate_products(creator_id)

    delivered_ids = set()
    delivered_map = None
    if user_id is not None:
        delivered_map = await vdb.get_delivered_media_map(creator_id, user_id)
        delivered_ids = set(delivered_map.keys())

    items = aggregate_media_from_products(
        products, delivered_ids=delivered_ids, delivered_map=delivered_map
    )

    if product_id is not None:
        items = [i for i in items if i.product_id == product_id]
    if folder_id is not None:
        items = [i for i in items if i.folder_id == folder_id]
    if collection_only:
        items = [i for i in items if i.in_collection]
    if media_type is not None:
        items = [i for i in items if i.media_type == media_type]

    return {
        "items": [i.to_dict() for i in items],
        "total_products": total_products,
        "limit": limit,
        "offset": offset,
    }


async def get_media(creator_id: int, media_id: int) -> VaultMediaItem | None:
    """Find a single media item by ID using JSONB index query."""
    product = await fdb.find_product_by_media_id(creator_id, media_id)
    if not product:
        return None
    items = aggregate_media_from_products([product])
    for item in items:
        if item.media_id == media_id:
            return item
    return None


async def list_products(creator_id: int, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List products with Vault metadata."""
    products = await fdb.list_fangate_products(creator_id, limit=limit, offset=offset)
    total = await fdb.count_fangate_products(creator_id)
    vault_products = products_to_vault_products(products)
    return {
        "items": [p.to_dict() for p in vault_products],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_product(creator_id: int, product_id: int) -> dict[str, Any] | None:
    """Get a single product from the mirror."""
    return await fdb.get_fangate_product(creator_id, product_id)


async def list_folders(creator_id: int) -> list[dict[str, Any]]:
    """List content folders via Dropfans API."""
    try:
        return await df_service.list_vault_folders(creator_id)
    except DropfansError:
        logger.warning("Failed to list Dropfans folders", extra={"creator_id": creator_id})
        return []


async def attach_media(creator_id: int, product_id: int, media_ids: list[int]) -> dict[str, Any]:
    """Attach media to a product. Delegates to Dropfans vault operations."""
    logger.warning(
        "attach_media called — Dropfans uses vault items directly, not product-media linking",
        extra={"creator_id": creator_id, "product_id": product_id},
    )
    return {"product_id": product_id, "media_count": 0, "media": []}


async def delete_media(creator_id: int, media_id: int) -> bool:
    """Delete a vault item via Dropfans API."""
    try:
        await df_service.delete_vault_item(creator_id, str(media_id))
        return True
    except DropfansError:
        logger.warning("Failed to delete Dropfans vault item", extra={"media_id": media_id})
        return False


async def record_delivery(
    creator_id: int,
    user_id: int,
    fangate_media_id: int,
    product_id: int | None = None,
    telegram_message_id: int | None = None,
) -> VaultDeliveryRecord:
    """Record a successful media delivery.

    Idempotent: if already recorded, returns the existing record.
    """
    await vdb.record_delivery(
        creator_id, user_id, fangate_media_id, product_id, telegram_message_id
    )
    record = await vdb.get_delivery(creator_id, user_id, fangate_media_id)
    if record:
        return VaultDeliveryRecord(
            id=record["id"],
            creator_id=record["creator_id"],
            user_id=record["user_id"],
            fangate_media_id=record["fangate_media_id"],
            product_id=record.get("product_id"),
            telegram_message_id=record.get("telegram_message_id"),
            sent_at=str(record.get("sent_at")),
            status=record.get("status", "sent"),
        )
    return VaultDeliveryRecord(
        id=0,
        creator_id=creator_id,
        user_id=user_id,
        fangate_media_id=fangate_media_id,
        product_id=product_id,
        telegram_message_id=telegram_message_id,
        status="sent",
    )


async def reserve_delivery(
    creator_id: int,
    user_id: int,
    fangate_media_id: int,
    product_id: int | None = None,
) -> int | None:
    """Atomically reserve a delivery intent before Telegram send."""
    return await vdb.reserve_delivery(creator_id, user_id, fangate_media_id, product_id)


async def finalize_delivery(
    delivery_id: int,
    telegram_message_id: int | None = None,
    creator_id: int | None = None,
) -> bool:
    """Update a reserved delivery to 'sent' after Telegram success.

    M7 (B8): pass ``creator_id`` for ownership scoping; id-only calls retain
    legacy behavior with a deprecation warning from the DAO layer.
    """
    return await vdb.finalize_delivery(delivery_id, telegram_message_id, creator_id)


async def release_delivery(delivery_id: int, creator_id: int | None = None) -> bool:
    """Remove a pending delivery reservation on failure.

    M7 (B8): pass ``creator_id`` for ownership scoping.
    """
    return await vdb.release_delivery(delivery_id, creator_id)


async def has_fan_received_media(creator_id: int, user_id: int, fangate_media_id: int) -> bool:
    """Check if a fan has already received a media item."""
    return await vdb.has_user_received_media(creator_id, user_id, fangate_media_id)


async def get_fan_unseen_media(creator_id: int, user_id: int, media_ids: list[int]) -> list[int]:
    """Return media_ids the fan hasn't received yet."""
    unseen = await vdb.get_unseen_media_ids(creator_id, user_id, media_ids)
    return sorted(unseen)


async def list_deliveries(
    creator_id: int,
    *,
    user_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List delivery records."""
    items = await vdb.list_deliveries(creator_id, user_id=user_id, limit=limit, offset=offset)
    total = await vdb.count_deliveries(creator_id, user_id=user_id)
    return {"items": items, "total": total}


# ── Analytics ──────────────────────────────────────────────────────────────


async def get_vault_analytics(creator_id: int) -> dict[str, Any]:
    """Aggregate overview combining product metrics + CRM delivery data.

    Returns a snapshot of:
    - Product counts (from product mirror)
    - Delivery stats (from CRM)
    - Recent delivery activity
    """
    products = await fdb.list_fangate_products(creator_id, limit=1000, offset=0)
    total_products = len(products)
    total_media = 0

    for p in products:
        raw_media = p.get("raw", {})
        if isinstance(raw_media, str):
            import json
            try:
                raw_media = json.loads(raw_media)
            except (json.JSONDecodeError, TypeError):
                raw_media = {}
        media_list = raw_media.get("media") or []
        total_media += len([m for m in media_list if isinstance(m, dict) and "id" in m])

    # CRM delivery stats
    delivery_stats = await vdb.get_delivery_stats(creator_id)

    # Recent deliveries
    recent = await vdb.get_recent_deliveries(creator_id, limit=10)

    # Top fans
    top_fans = await vdb.get_top_fans(creator_id, limit=5)

    # Dropfans earnings (best-effort, last 30 days)
    earnings_summary = {}
    try:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        earnings_summary = await df_service.get_earnings(
            creator_id,
            start_date=(now - timedelta(days=30)).strftime("%Y-%m-%d"),
            end_date=now.strftime("%Y-%m-%d"),
        )
    except DropfansError:
        logger.warning(
            "vault analytics: Dropfans earnings query failed for creator=%s",
            creator_id,
            exc_info=True,
        )

    return {
        "products": {
            "total": total_products,
            "total_media": total_media,
        },
        "earnings": earnings_summary,
        "deliveries": {
            "total": delivery_stats.get("total_deliveries", 0),
            "unique_fans": delivery_stats.get("unique_fans", 0),
            "unique_media": delivery_stats.get("unique_media", 0),
            "sent": delivery_stats.get("sent_count", 0),
            "pending": delivery_stats.get("pending_count", 0),
        },
        "recent_deliveries": recent,
        "top_fans": top_fans,
    }


async def get_product_performance(creator_id: int) -> list[dict[str, Any]]:
    """Return per-product performance with CRM delivery counts."""
    products = await fdb.list_fangate_products(creator_id, limit=1000, offset=0)
    delivery_counts = await vdb.get_product_delivery_counts(creator_id)

    result = []
    for p in products:
        raw_media = p.get("raw", {})
        if isinstance(raw_media, str):
            import json
            try:
                raw_media = json.loads(raw_media)
            except (json.JSONDecodeError, TypeError):
                raw_media = {}
        media_list = raw_media.get("media") or []
        media_count = len([m for m in media_list if isinstance(m, dict) and "id" in m])

        product_id = int(p["id"])
        result.append({
            "id": product_id,
            "title": p.get("title"),
            "price_minor": p.get("price_minor"),
            "media_count": media_count,
            "delivery_count": delivery_counts.get(product_id, 0),
            "preview_url": p.get("preview_url"),
        })

    result.sort(key=lambda x: -x["delivery_count"])
    return result


async def get_fan_analytics(
    creator_id: int,
    user_id: int,
) -> dict[str, Any]:
    """Return analytics for a specific fan."""
    history = await vdb.get_fan_delivery_history(creator_id, user_id, limit=100)
    total = await vdb.get_fan_delivery_count(creator_id, user_id)

    product_cache: dict[int, str] = {}
    enriched_history = []
    for h in history:
        pid = h.get("product_id")
        if pid and pid not in product_cache:
            product = await fdb.get_fangate_product(creator_id, pid)
            product_cache[pid] = product.get("title") if product else f"Product #{pid}"
        enriched_history.append({
            "id": h["id"],
            "fangate_media_id": h["fangate_media_id"],
            "product_id": pid,
            "product_title": product_cache.get(pid) if pid else None,
            "sent_at": h["sent_at"].isoformat() if h.get("sent_at") else None,
            "status": h.get("status", "sent"),
        })

    first_delivery = history[-1]["sent_at"].isoformat() if history and history[-1].get("sent_at") else None
    last_delivery = history[0]["sent_at"].isoformat() if history and history[0].get("sent_at") else None

    return {
        "user_id": user_id,
        "total_deliveries": total,
        "first_delivery": first_delivery,
        "last_delivery": last_delivery,
        "history": enriched_history,
    }
