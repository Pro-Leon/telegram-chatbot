"""Deterministic product resolution for autonomous commerce.

Resolves which product the autonomous commerce pipeline may operate on.
Dropfans is the sole active commerce provider. The product mirror
(fangate_products table) stores both legacy Fangate and active Dropfans
products with product_type = 'dropfans' for Dropfans entries.

This is deterministic infrastructure logic — the LLM never participates in
product selection, but may provide structured observations (fan seems
interested in X) that the deterministic layer resolves to a real product.

Selection policy (strict priority):

    resolve_commerce_product (single-product path):
        1. Exactly one valid product for the creator  -> return its id
        2. Otherwise                                   -> return None

    resolve_commerce_product_with_history (multi-product path):
        1. Zero valid products            -> None
        2. One valid product              -> return its id
        3. Two+ valid products:
           a. Exclude already-purchased products
           b. One unpurchased valid       -> return its id
           c. Zero unpurchased            -> None (all purchased)
           d. Two+ unpurchased            -> deterministic ranking:
              cheapest price first, then lowest id (explainable fallback)

A "valid" product satisfies ALL of:

    - belongs to the creator (enforced by creator-scoped DB query)
    - exists in the local product mirror
    - is_accessible = True
    - sales_url is not None / not empty

Invariants:

    DETERMINISTIC: identical DB snapshots yield identical resolutions.
    CREATOR-SCOPED: the query filters by creator_id; cross-creator leakage
        is impossible.
    READ-ONLY: only SELECT queries; no writes.
    FAILURE-ISOLATED: DB failures degrade to None; never raises.
    RANKED: multi-product ambiguity resolved by price→id, not None.
"""

import logging
from typing import Any

from db import fangate as db_fangate

logger = logging.getLogger("commerce.product_selection")


def _is_valid_product(product: dict[str, Any]) -> bool:
    """Return True if the product is a valid candidate for autonomous commerce.

    A product is valid when it is accessible AND has a sales URL.
    Both conditions are required by the existing eligibility and strategy
    layers (see commerce/eligibility.py and commerce/strategy.py).
    """
    if not product.get("is_accessible", False):
        return False
    sales_url = product.get("sales_url")
    return bool(sales_url and str(sales_url).strip())


async def _get_purchased_product_ids(creator_id: int, user_id: int) -> set[int] | None:
    """Return the set of product_ids the user has already purchased.

    Creator-scoped. Returns None on DB failure (fail-closed) so caller can
    block product selection rather than treating purchased as unpurchased.
    """
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT product_id
                FROM commerce_offers
                WHERE creator_id = $1 AND user_id = $2
                  AND state = 'purchased' AND transaction_id IS NOT NULL
                """,
                creator_id,
                user_id,
            )
            return {int(r["product_id"]) for r in rows}
    except Exception:
        logger.warning(
            "Purchase history query failed (creator=%s user=%s)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return None


async def resolve_commerce_product(creator_id: int) -> int | None:
    """Resolve the product the autonomous commerce pipeline may operate on.

    Returns the product ID (internal mirror ID) when exactly one valid product
    exists for the creator. Returns None when:

    - zero valid products exist
    - two or more valid products exist (ambiguous — fail-closed)
    - the DB query fails

    The caller must pass the result into ``CommerceStateRequest.product_id``.
    A None result means the commerce pipeline should fall back to the standard
    LLM path (no autonomous offer creation).
    """
    try:
        products = await db_fangate.list_fangate_products(creator_id, limit=200, offset=0)
    except Exception:
        logger.warning(
            "Product resolution DB failure for creator %s — commerce unavailable",
            creator_id,
            exc_info=True,
        )
        return None

    valid = [p for p in products if _is_valid_product(p)]

    if len(valid) == 1:
        product_id = int(valid[0]["id"])
        logger.debug(
            "Product resolution: creator=%s single valid product=%s",
            creator_id,
            product_id,
        )
        return product_id

    if len(valid) == 0:
        logger.debug(
            "Product resolution: creator=%s no valid products — commerce unavailable",
            creator_id,
        )
        return None

    # 2+ valid products: fail-closed ambiguity
    logger.info(
        "Product resolution: creator=%s has %s valid products — ambiguous, "
        "autonomous commerce skipped (fail-closed)",
        creator_id,
        len(valid),
    )
    return None


async def resolve_commerce_product_with_history(
    creator_id: int,
    user_id: int,
    *,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
    preferences: list[str] | None = None,
    allowed_folders: list[str] | None = None,
    allowed_tags: list[str] | None = None,
    folder_priority: list[str] | None = None,
    hard_mode: bool | None = None,
) -> int | None:
    """Resolve product considering purchase history and relevance for multi-product creators.

    Unified ranking (Phase 10 Fix #1):
        1. Zero valid products             -> None
        2. One valid product               -> return its id
        3. Two+ valid products:
           a. Exclude already-purchased (creator+user)
           b. One unpurchased valid        -> return its id
           c. Zero unpurchased             -> None (all purchased)
            d. Two+ unpurchased             -> deterministic relevance-aware ranking:
               - meaningful relevance evidence required (rel>=0.15) else no offer
               - rank by content affinity (title-token overlap with current_topic/open_threads/preferences)
               - P3.3.4: no bundle preference — relevance, then price, then id.
                 Title similarity never implies bundle composition.
               - price as deterministic tie-breaker
               - product_id as final tie-breaker

    When relevance signals are absent (no topic/preferences), falls back to
    cheapest -> lowest id (explainable). Conversation text never directly
    selects product; it only provides relevance evidence via deterministic
    title-token matching.

    Invariants:

        DETERMINISTIC: identical DB snapshots + identical relevance inputs yield identical resolutions.
        CREATOR-SCOPED: all queries filtered by creator_id.
        READ-ONLY: only SELECT queries; no writes.
        FAILURE-ISOLATED: DB failures degrade to None; never raises.
        FAIL-CLOSED: ambiguous or low-relevance situations return None.
    """
    try:
        products = await db_fangate.list_fangate_products(creator_id, limit=200, offset=0)
    except Exception:
        logger.warning(
            "Product resolution DB failure for creator %s — commerce unavailable",
            creator_id,
            exc_info=True,
        )
        return None

    valid = [p for p in products if _is_valid_product(p)]

    # P3.3.2: item-level ownership overlap gate — applied BEFORE ranking and
    # before every fast path below (single-valid, single-unpurchased,
    # relevance winner, allowlist winner, cheapest fallback). Candidates with
    # PARTIAL/FULL overlap are removed as priced (never sliced/repriced);
    # products with unknown Vault membership keep legacy handling.
    try:
        from commerce import ownership as _ownership

        valid = await _ownership.filter_products_by_ownership(creator_id, user_id, valid)
    except Exception:
        logger.warning(
            "Product resolution: ownership check failed for creator=%s user=%s — fail-closed no product",
            creator_id,
            user_id,
            exc_info=True,
        )
        return None
    if not valid:
        logger.info(
            "Product resolution: creator=%s user=%s all candidates overlap owned Vault items — commerce unavailable",
            creator_id,
            user_id,
        )
        return None

    # P3.3.4 QUARANTINE: title-derived family suppression removed. A shared
    # subject/setting parse is descriptive similarity, not proof of a shared
    # commercial relationship, prior purchase, or bundle membership. Operator
    # family suppression metrics remain recorded but no longer filter
    # candidates here; explicit ContentFamily gating arrives in a later phase.

    if len(valid) == 0:
        logger.debug(
            "Product resolution: creator=%s no valid products — commerce unavailable",
            creator_id,
        )
        return None

    if len(valid) == 1:
        product_id = int(valid[0]["id"])
        logger.debug(
            "Product resolution: creator=%s single valid product=%s",
            creator_id,
            product_id,
        )
        return product_id

    # 2+ valid products: use purchase history to disambiguate
    # P0 fail-closed: None means purchase history unavailable -> no product selection
    purchased_ids = await _get_purchased_product_ids(creator_id, user_id)
    if purchased_ids is None:
        logger.warning(
            "Product resolution: purchase history unavailable for creator=%s user=%s — fail-closed no product",
            creator_id,
            user_id,
        )
        return None
    unpurchased = [p for p in valid if int(p["id"]) not in purchased_ids]

    if len(unpurchased) == 0:
        # All valid products have been purchased
        logger.info(
            "Product resolution: creator=%s user=%s all %s valid products "
            "already purchased — commerce unavailable",
            creator_id,
            user_id,
            len(valid),
        )
        return None

    # P3-B: Vault allowlist pre-filter (deterministic, AND semantics).
    # Empty allowlists preserve existing behavior exactly. Purchased exclusion
    # above always wins; authorization/price/serialization downstream untouched.
    # Applies to any unpurchased set size (including single) so hard mode is
    # meaningful; soft mode falls through to existing logic when nothing matches.
    try:
        from commerce.vault_ranking import rank_vault_candidates

        _cfg_folders = allowed_folders
        _cfg_tags = allowed_tags
        _cfg_priority = folder_priority
        _cfg_hard = hard_mode
        if _cfg_folders is None or _cfg_tags is None or _cfg_hard is None:
            try:
                from db import dropfans as _ddb_cfg

                _cfg = await _ddb_cfg.get_selection_config(creator_id)
                if _cfg_folders is None:
                    _cfg_folders = _cfg.get("allowed_folders", [])
                if _cfg_tags is None:
                    _cfg_tags = _cfg.get("allowed_tags", [])
                if _cfg_hard is None:
                    _cfg_hard = bool(_cfg.get("hard_mode", False))
            except Exception:
                if _cfg_folders is None:
                    _cfg_folders = []
                if _cfg_tags is None:
                    _cfg_tags = []
                if _cfg_hard is None:
                    _cfg_hard = False
        _folders = [str(f) for f in (_cfg_folders or []) if str(f).strip()]
        _tags = [str(t) for t in (_cfg_tags or []) if str(t).strip()]
        _vault_filter_active = bool(_folders or _tags)
        if _vault_filter_active:
            _index_rows: list[dict[str, Any]] = []
            try:
                from db import dropfans as _ddb_idx

                _index_rows = await _ddb_idx.list_vault_index(creator_id)
            except Exception:
                _index_rows = []
            _by_vault: dict[str, dict[str, Any]] = {}
            for _row in _index_rows:
                try:
                    _by_vault[str(_row.get("vault_item_id"))] = _row
                except Exception:
                    continue

            def _product_vault_ids(product: dict[str, Any]) -> list[str]:
                try:
                    raw = product.get("raw", {}) or {}
                    if isinstance(raw, str):
                        import json as _json

                        try:
                            raw = _json.loads(raw)
                        except Exception:
                            raw = {}
                    ids = raw.get("vaultItemIds", []) or []
                    return [str(v) for v in ids if str(v).strip()]
                except Exception:
                    return []

            _vault_matched: list[tuple[int, dict[str, Any]]] = []
            _vault_unindexed: list[dict[str, Any]] = []
            for _product in unpurchased:
                _vids = _product_vault_ids(_product)
                _candidates: list[dict[str, Any]] = []
                for _vid in _vids:
                    _row = _by_vault.get(_vid)
                    if _row is None:
                        continue
                    _candidates.append(
                        {
                            "vault_item_id": _vid,
                            "tags": list(_row.get("tags") or []),
                            "folder_id": _row.get("folder_id"),
                            "moderation_status": _row.get("moderation_status"),
                            "file_type": _row.get("file_type"),
                            "created_at": None,
                        }
                    )
                if not _candidates:
                    # P3.1 F-07c: no indexed signal for this product. An absent
                    # index row must NOT imply "unsafe" — the index may simply
                    # be incomplete. Such products stay governed by the
                    # existing selection rules below (validity, purchased
                    # exclusion and commerce authority still apply).
                    _vault_unindexed.append(_product)
                    continue
                _ranked = rank_vault_candidates(
                    _candidates,
                    allowed_folders=_folders,
                    allowed_tags=_tags,
                    folder_priority=_cfg_priority or _folders,
                    purchased_ids=set(),
                )
                if _ranked:
                    _best, _overlap = _ranked[0]
                    _vault_matched.append((_overlap, _product))
                # Indexed but allowlist-unsatisfying products are excluded.
            if _vault_matched:
                # Allowlist-satisfying products first (overlap desc, id asc),
                # then unindexed products under existing rules — unless hard
                # mode explicitly demands constraint satisfaction.
                _vault_matched.sort(key=lambda t: (-t[0], int(t[1]["id"])))
                if _cfg_hard:
                    unpurchased = [p for _, p in _vault_matched]
                else:
                    _unindexed_ids = {int(p["id"]) for p in _vault_unindexed}
                    unpurchased = [p for _, p in _vault_matched] + [
                        p for p in unpurchased if int(p["id"]) in _unindexed_ids
                    ]
                if len(unpurchased) == 1:
                    product_id = int(unpurchased[0]["id"])
                    logger.info(
                        "Product resolution: creator=%s user=%s vault-allowlist single candidate product=%s",
                        creator_id,
                        user_id,
                        product_id,
                    )
                    return product_id
            elif _cfg_hard:
                logger.info(
                    "Product resolution: creator=%s user=%s vault allowlists hard-mode no candidate — no offer",
                    creator_id,
                    user_id,
                )
                return None
            # Soft mode with no satisfying candidate falls through to existing logic.
    except Exception:
        logger.debug("Vault allowlist pre-filter failed, using existing ranking", exc_info=True)

    if len(unpurchased) == 1:
        product_id = int(unpurchased[0]["id"])
        logger.debug(
            "Product resolution: creator=%s user=%s single unpurchased "
            "valid product=%s (from %s valid, %s purchased)",
            creator_id,
            user_id,
            product_id,
            len(valid),
            len(purchased_ids),
        )
        return product_id

    # 2+ unpurchased valid products: unified deterministic ranking (Phase 10 Fix #1)
    # Relevance-aware when conversational evidence available, else cheapest fallback.
    # This ensures tease (AVAILABLE CONTENT) and offer (deterministic) use same ranking.
    try:
        # Use relevance-aware ranking when we have conversational evidence
        if current_topic or open_threads or preferences:
            from commerce.content_matching import rank_products_by_relevance

            # rank_products_by_relevance expects full product dicts with id/title/price_minor/raw
            # unpurchased already contains those; purchased exclusion already done
            ranked_with_rel = rank_products_by_relevance(
                unpurchased,
                current_topic,
                tuple(open_threads) if open_threads else (),
                preferences or [],
                purchased_ids=set(),  # already excluded
            )
            if ranked_with_rel:
                top_product, top_rel = ranked_with_rel[0]
                # Require meaningful relevance evidence (rel>=0.15) else no confident match
                if top_rel >= 0.15:
                    product_id = int(top_product["id"])
                    logger.info(
                        "Product resolution: creator=%s user=%s has %s unpurchased valid "
                        "products — relevance-ranked selection product=%s rel=%.2f topic=%s",
                        creator_id,
                        user_id,
                        len(unpurchased),
                        product_id,
                        top_rel,
                        current_topic,
                    )
                    return product_id
                else:
                    logger.info(
                        "Product resolution: creator=%s user=%s no confident relevance match (top rel=%.2f) — no offer",
                        creator_id,
                        user_id,
                        top_rel,
                    )
                    return None
    except Exception:
        logger.warning("Relevance ranking failed, falling back to cheapest", exc_info=True)

    # Fallback: cheapest -> lowest id (when no relevance evidence or ranking failed)
    def _rank_key(p: dict[str, Any]) -> tuple:
        price = p.get("price_minor")
        price_key = price if price is not None else 10**12
        return (price_key, int(p["id"]))

    ranked = sorted(unpurchased, key=_rank_key)
    chosen = ranked[0]
    product_id = int(chosen["id"])
    logger.info(
        "Product resolution: creator=%s user=%s has %s unpurchased valid "
        "products — ranked selection product=%s (cheapest/lowest-id fallback)",
        creator_id,
        user_id,
        len(unpurchased),
        product_id,
    )
    return product_id


async def list_valid_products(creator_id: int) -> list[dict[str, Any]]:
    """Return all valid products for a creator (for the list_products tool).

    Returns a list of dicts with keys: product_id, title, price_minor, currency.
    Each product is valid (is_accessible=True, sales_url present).
    Creator-scoped. Returns empty list on failure.
    """
    try:
        products = await db_fangate.list_fangate_products(creator_id, limit=200, offset=0)
    except Exception:
        logger.warning(
            "list_valid_products DB failure for creator %s", creator_id, exc_info=True
        )
        return []

    valid = [p for p in products if _is_valid_product(p)]

    # Get currency from creator integration
    currency = None
    try:
        integration = await db_fangate.get_creator_integration(creator_id)
        if integration:
            currency = integration.get("currency_code")
    except Exception:  # noqa: BLE001,S110 — currency lookup is best-effort
        pass

    return [
        {
            "product_id": int(p["id"]),
            "title": p.get("title") or "",
            "price_minor": p.get("price_minor"),
            "currency": currency,
        }
        for p in valid
    ]
