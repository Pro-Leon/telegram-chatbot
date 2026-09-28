"""Vault read model.

Media items are derived from Fangate product.media[] — not standalone
resources. The Vault provides an aggregated, deduplicated view with
CRM-specific delivery state.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VaultProductRef:
    """Lightweight product reference for associated-products list."""

    product_id: int
    title: str | None = None
    price: int | None = None


@dataclass
class VaultMediaItem:
    """Aggregated media item derived from product.media[].

    media_id is unique across all products. associated_products lists every
    product that contains this media item.
    """

    media_id: int
    media_type: str | None = None
    preview: str | None = None
    preview_blurred: str | None = None
    veriff_status: str | None = None
    removal_description: str | None = None
    source: str | None = None
    product_id: int = 0
    product_title: str | None = None
    product_price: int | None = None
    product_link: str | None = None
    folder_id: str | None = None
    folder_name: str | None = None
    in_collection: bool = False
    associated_products: list[VaultProductRef] = field(default_factory=list)
    delivery_count: int = 0
    sent_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "media_id": self.media_id,
            "media_type": self.media_type,
            "preview": self.preview,
            "preview_blurred": self.preview_blurred,
            "veriff_status": self.veriff_status,
            "removal_description": self.removal_description,
            "source": self.source,
            "product_id": self.product_id,
            "product_title": self.product_title,
            "product_price": self.product_price,
            "product_link": self.product_link,
            "folder_id": self.folder_id,
            "folder_name": self.folder_name,
            "in_collection": self.in_collection,
            "associated_products": [
                {"product_id": p.product_id, "title": p.title, "price": p.price}
                for p in self.associated_products
            ],
            "delivery_count": self.delivery_count,
            "sent_at": self.sent_at,
        }


@dataclass
class VaultProduct:
    """Fangate product with media count for the Vault view."""

    id: int
    title: str | None = None
    preview: str | None = None
    price_minor: int | None = None
    media_count: int = 0
    in_collection: bool = False
    folder_id: str | None = None
    folder_name: str | None = None
    link: str | None = None
    link_clicks: int | None = None
    unlocks: int | None = None
    total_earnings: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "preview": self.preview,
            "price_minor": self.price_minor,
            "media_count": self.media_count,
            "in_collection": self.in_collection,
            "folder_id": self.folder_id,
            "folder_name": self.folder_name,
            "link": self.link,
            "link_clicks": self.link_clicks,
            "unlocks": self.unlocks,
            "total_earnings": self.total_earnings,
        }


@dataclass
class VaultDeliveryRecord:
    """A recorded media delivery to a fan."""

    id: int
    creator_id: int
    user_id: int
    fangate_media_id: int
    product_id: int | None = None
    telegram_message_id: int | None = None
    sent_at: str | None = None
    status: str = "sent"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "creator_id": self.creator_id,
            "user_id": self.user_id,
            "fangate_media_id": self.fangate_media_id,
            "product_id": self.product_id,
            "telegram_message_id": self.telegram_message_id,
            "sent_at": self.sent_at,
            "status": self.status,
        }
