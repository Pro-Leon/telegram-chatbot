"""Phase 102 — Deterministic product/menu metadata layer.

Creator-scoped, authoritative, deterministic, fail-closed.
Reuses existing `fangate_products` (Dropfans/Fangate mirror) as the
authoritative table — no new table needed. `fangate_products` already
has `id` (stable BIGINT), `creator_id` FK, `title`, `price_minor`,
`currency` via `creator_integrations.currency_code` / `offers.currency`,
`is_accessible` as active flag, plus ordering via `title`/`id`.

This module is the sole Phase 102 entry for product/menu metadata.
It never invents products, never parses LLM output, never authorizes
purchase/entitlement/PPV, never touches free-photo ledger.

All lookups are `WHERE creator_id=$1` — no global cache.
Price/currency are DB state, never LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db.postgres import get_pool


@dataclass(frozen=True)
class ProductMetadata:
    creator_id: int
    product_id: int
    title: str
    description: str | None
    price_minor: int | None
    currency: str | None
    is_active: bool
    product_type: str | None
    folder_id: str | None
    folder_name: str | None


def _row_to_metadata(row: dict[str, Any], currency: str | None = None) -> ProductMetadata:
    return ProductMetadata(
        creator_id=int(row["creator_id"]),
        product_id=int(row["id"]),
        title=str(row.get("title") or ""),
        description=row.get("public_description") or row.get("private_description"),
        price_minor=row.get("price_minor"),
        currency=currency or row.get("currency") or "USD",
        is_active=bool(row.get("is_accessible")),
        product_type=row.get("product_type"),
        folder_id=row.get("folder_id"),
        folder_name=row.get("folder_name"),
    )


async def get_product(creator_id: int, product_id: int) -> ProductMetadata | None:
    """Deterministic creator-scoped lookup. Fail-closed.

    Returns None if product missing, creator-mismatched, or inactive
    (is_accessible false). Never invents product from LLM/user text.
    """
    if not isinstance(creator_id, int) or creator_id <= 0 or not isinstance(product_id, int) or product_id <= 0:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, creator_id, title, public_description, private_description,
                   price_minor, product_type, folder_id, folder_name, is_accessible
            FROM fangate_products
            WHERE creator_id=$1 AND id=$2
            """,
            creator_id,
            product_id,
        )
        if not row:
            return None
        # Availability check — creator-scoped, deterministic
        if not row["is_accessible"]:
            return None
        # Currency from creator_integrations (authoritative) — fallback USD
        cur = None
        try:
            cur_row = await conn.fetchrow("SELECT currency_code FROM creator_integrations WHERE creator_id=$1", creator_id)
            if cur_row and cur_row["currency_code"]:
                cur = cur_row["currency_code"]
        except Exception:
            pass
        return _row_to_metadata(dict(row), currency=cur)


async def list_active_products(creator_id: int, limit: int = 100) -> list[ProductMetadata]:
    """Creator-scoped active catalog. Deterministic ordering.

    Returns active products (`is_accessible = true`) for creator ordered by
    `title ASC, id ASC` (stable). No global cache, no LLM.
    """
    if not isinstance(creator_id, int) or creator_id <= 0:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, creator_id, title, public_description, private_description,
                   price_minor, product_type, folder_id, folder_name, is_accessible
            FROM fangate_products
            WHERE creator_id=$1 AND is_accessible = true
            ORDER BY title ASC, id ASC
            LIMIT $2
            """,
            creator_id,
            limit,
        )
        # Currency per creator (single query, still creator-scoped)
        cur = None
        try:
            cur_row = await conn.fetchrow("SELECT currency_code FROM creator_integrations WHERE creator_id=$1", creator_id)
            if cur_row:
                cur = cur_row["currency_code"]
        except Exception:
            pass
        return [_row_to_metadata(dict(r), currency=cur) for r in rows]


async def list_menu(creator_id: int) -> list[ProductMetadata]:
    """Menu is the active catalog for a creator (deterministic)."""
    return await list_active_products(creator_id, limit=100)


# For context integration: authoritative menu context string
async def get_menu_context(creator_id: int, max_items: int = 5) -> str:
    """Return authoritative menu context for inclusion in LLM context.

    Uses DB state, creator-scoped, deterministic. Truncated to max_items.
    Format is factual, not model-generated.
    """
    items = await list_active_products(creator_id, limit=max_items)
    if not items:
        return ""
    # Leak hardening: titles only. Internal ids, prices, and creator ids
    # never reach prompt prose — selection/ranking use structured data
    # elsewhere, and authorized commerce carries its own sealed state.
    # (Price slips were caught by price_mention; internal ids had NO
    # validator — removing them at the source closes both.)
    lines = []
    for p in items:
        title = (p.title or "").strip()
        if title:
            lines.append(f"- {title}")
    if not lines:
        return ""
    return "Authoritative menu titles (reference only — do not mention or offer unless the fan explicitly asks about content):\n" + "\n".join(lines)
