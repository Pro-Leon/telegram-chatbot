"""Vault media aggregation.

Derives a deduplicated media view from the Fangate product mirror.
Each unique media_id appears once with all associated products listed.
"""

from __future__ import annotations

import logging
from typing import Any

from vault.models import VaultMediaItem, VaultProduct, VaultProductRef

logger = logging.getLogger("vault.aggregate")


def aggregate_media_from_products(
    products: list[dict[str, Any]],
    delivered_ids: set[int] | None = None,
    delivered_map: dict[int, dict[str, Any]] | None = None,
) -> list[VaultMediaItem]:
    """Aggregate media items from mirrored Fangate products.

    Args:
        products: Raw product dicts from db.fangate.list_fangate_products().
        delivered_ids: Set of fangate_media_id values already sent to the fan
                       (for delivery status overlay). None skips the overlay.
        delivered_map: Dict of {media_id: {sent_at, status}} for delivery state.
                       When provided, sent_at is overlaid on each delivered item.

    Returns:
        Deduplicated list of VaultMediaItem sorted by product_id DESC,
        then media_id DESC (newest products first).
    """
    by_media: dict[int, VaultMediaItem] = {}
    media_order: list[int] = []

    for product in products:
        product_id = int(product["id"])
        raw_media = product.get("raw", {})
        if isinstance(raw_media, str):
            import json

            try:
                raw_media = json.loads(raw_media)
            except (json.JSONDecodeError, TypeError):
                raw_media = {}

        media_list = raw_media.get("media") or []
        folder_id = product.get("folder_id")
        folder_name = product.get("folder_name")

        for m in media_list:
            if not isinstance(m, dict) or "id" not in m:
                continue
            media_id = int(m["id"])

            product_ref = VaultProductRef(
                product_id=product_id,
                title=product.get("title"),
                price=product.get("price_minor"),
            )

            if media_id in by_media:
                by_media[media_id].associated_products.append(product_ref)
            else:
                media_order.append(media_id)
                by_media[media_id] = VaultMediaItem(
                    media_id=media_id,
                    media_type=m.get("type"),
                    preview=m.get("preview"),
                    preview_blurred=m.get("preview_blurred"),
                    veriff_status=m.get("veriff_status"),
                    removal_description=m.get("removal_description"),
                    source=m.get("source"),
                    product_id=product_id,
                    product_title=product.get("title"),
                    product_price=product.get("price_minor"),
                    product_link=product.get("sales_url"),
                    folder_id=folder_id,
                    folder_name=folder_name,
                    in_collection=bool(product.get("in_collection")),
                    associated_products=[product_ref],
                )

    items = [by_media[mid] for mid in media_order]

    if delivered_map:
        for item in items:
            if item.media_id in delivered_map:
                info = delivered_map[item.media_id]
                item.delivery_count = 1
                item.sent_at = info.get("sent_at")
    elif delivered_ids:
        for item in items:
            if item.media_id in delivered_ids:
                item.delivery_count = 1

    return items


def products_to_vault_products(
    products: list[dict[str, Any]],
) -> list[VaultProduct]:
    """Convert raw product dicts to VaultProduct view models."""
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
        result.append(
            VaultProduct(
                id=int(p["id"]),
                title=p.get("title"),
                preview=p.get("preview_url"),
                price_minor=p.get("price_minor"),
                media_count=len([m for m in media_list if isinstance(m, dict) and "id" in m]),
                in_collection=bool(p.get("in_collection")),
                folder_id=p.get("folder_id"),
                folder_name=p.get("folder_name"),
                link=p.get("sales_url"),
                link_clicks=p.get("link_clicks"),
                unlocks=p.get("unlocks"),
                total_earnings=p.get("total_earnings"),
            )
        )
    return result
