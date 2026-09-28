"""Product Knowledge Layer — grounded product representation (Phase 14)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class ProductKnowledge:
    product_id: int
    creator_id: int
    title: str
    price_minor: int | None
    currency: str | None
    media_count: int | None
    format: str | None
    subject: str | None
    setting: str | None
    bundle_group: str
    availability: bool
    purchase_status: str  # not_purchased / purchased
    sales_url: str | None

    @property
    def semantic_tokens(self) -> set[str]:
        import re
        return set(re.compile(r"[a-z0-9]+").findall(self.title.lower()))

def build_product_knowledge(product: dict[str, Any], creator_id: int, purchased_ids: set[int]) -> ProductKnowledge:
    from commerce.vault_taxonomy import parse_taxonomy
    title = product.get("title") or ""
    tax = parse_taxonomy(title)
    pid = int(product.get("id") or product.get("product_id"))
    return ProductKnowledge(
        product_id=pid,
        creator_id=creator_id,
        title=title,
        price_minor=product.get("price_minor"),
        currency=product.get("currency"),
        media_count=tax.media_count,
        format=tax.format,
        subject=tax.subject,
        setting=tax.setting,
        bundle_group=tax.bundle_group,
        availability=bool(product.get("is_accessible", True)),
        purchase_status="purchased" if pid in purchased_ids else "not_purchased",
        sales_url=product.get("sales_url"),
    )
