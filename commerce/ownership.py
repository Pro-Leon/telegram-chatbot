"""P3.3.2 — centralized Vault-item ownership overlap policy.

Single authority for answering: "may this candidate Vault set be offered to
this fan?" Ownership itself comes exclusively from the P3.3.1 primitive
``commerce.dao.get_owned_vault_ids`` (purchased snapshots only). This module
adds no second ownership query, no delivery/product/title/taxonomy signals.

Policy (frozen):

- ZERO overlap    -> ELIGIBLE (may continue to ranking/selection)
- PARTIAL overlap -> REJECT_PARTIAL_OVERLAP (never sliced, repriced, or freed)
- FULL overlap    -> REJECT_FULL_OVERLAP (never offered again)
- INVALID         -> rejected (empty/invalid candidate set; an empty set must
  never read as eligible merely because ``set() <= owned``)

The candidate price always represents the complete commercial object, so a
partially owned candidate is rejected as priced — never reduced to its
unowned subset.
"""

from __future__ import annotations

import enum
from typing import Any, Iterable


class OverlapResult(str, enum.Enum):
    """Closed overlap verdict for one candidate Vault set."""

    ZERO = "zero"
    PARTIAL = "partial"
    FULL = "full"
    INVALID = "invalid"


_DENIAL_REASONS = {
    OverlapResult.PARTIAL: "reject_partial_overlap",
    OverlapResult.FULL: "reject_full_overlap",
    OverlapResult.INVALID: "invalid_candidate_vault_set",
}


def classify_vault_overlap(
    candidate_vault_ids: Any,
    owned_vault_ids: Iterable[str] | None,
) -> OverlapResult:
    """Classify overlap between a candidate set and an owned set.

    Both inputs are canonicalized with the existing
    ``canonical_identity_ids`` utility (no second normalization algorithm).
    Classification is purely set-based — never titles, families, Drop IDs,
    or product IDs. Invalid/empty candidates yield INVALID (rejected), never
    a false ZERO.
    """
    from commerce.vault_sets import canonical_identity_ids

    try:
        candidate = canonical_identity_ids(candidate_vault_ids or [])
    except Exception:
        return OverlapResult.INVALID
    if not candidate:
        return OverlapResult.INVALID
    try:
        owned = set(canonical_identity_ids(list(owned_vault_ids or [])))
    except Exception:
        # Unusable ownership input must fail closed at the caller, but the
        # classifier itself is pure: treat as no verifiable ownership claim
        # is possible here is wrong — instead refuse to decide eligible.
        # Callers fetch ownership via get_owned_vault_ids (which raises on
        # DB failure); reaching this branch means a programming error, so
        # return INVALID to force rejection rather than eligibility.
        return OverlapResult.INVALID
    overlap = set(candidate) & owned
    if not overlap:
        return OverlapResult.ZERO
    if len(overlap) == len(set(candidate)):
        return OverlapResult.FULL
    return OverlapResult.PARTIAL


def is_overlap_eligible(result: OverlapResult) -> bool:
    """Return True only for ZERO overlap."""
    return result is OverlapResult.ZERO


def overlap_denial_reason(result: OverlapResult) -> str | None:
    """Return the stable denial code for rejected verdicts, else None."""
    return _DENIAL_REASONS.get(result)


def product_vault_ids(product: dict[str, Any]) -> list[str] | None:
    """Extract the authoritative Vault-item set for a mirror product.

    Uses the same ``raw.vaultItemIds`` source as execution-time snapshots.
    Returns None when membership is unknown (missing/unparseable/empty raw)
    so callers preserve legacy handling instead of fabricating identity.
    """
    try:
        raw = (product or {}).get("raw", {}) or {}
        if isinstance(raw, str):
            import json as _json

            try:
                raw = _json.loads(raw)
            except Exception:
                return None
        if not isinstance(raw, dict):
            return None
        ids = raw.get("vaultItemIds") or raw.get("vault_item_ids") or []
        if not ids:
            return None
        from commerce.vault_sets import presentation_ids

        cleaned = presentation_ids(ids)
        return cleaned or None
    except Exception:
        return None


async def fetch_owned_vault_ids(creator_id: int, user_id: int) -> frozenset[str]:
    """Fetch the fan's owned Vault set via the P3.3.1 primitive.

    Thin pass-through preserving the single ownership authority. Database
    failures propagate (fail-closed) and are never converted to empty.
    """
    from commerce.dao import get_owned_vault_ids

    return await get_owned_vault_ids(creator_id, user_id)


async def filter_products_by_ownership(
    creator_id: int,
    user_id: int,
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove PARTIAL/FULL/INVALID-overlap products before ranking.

    Products with unknown Vault membership (``product_vault_ids`` is None)
    are preserved for legacy handling — no identity is fabricated for them.
    When no candidate carries authoritative Vault identity, the owned set is
    not fetched at all (nothing to gate). Otherwise the owned set is fetched
    exactly once; a fetch failure propagates so the caller can fail closed
    (never an empty ownership set).
    """
    known: list[tuple[dict[str, Any], list[str]]] = []
    legacy: list[dict[str, Any]] = []
    for product in products:
        ids = product_vault_ids(product)
        if ids is None:
            legacy.append(product)
        else:
            known.append((product, ids))
    if not known:
        return list(products)
    owned = await fetch_owned_vault_ids(creator_id, user_id)
    eligible: list[dict[str, Any]] = list(legacy)
    for product, ids in known:
        if is_overlap_eligible(classify_vault_overlap(ids, owned)):
            eligible.append(product)
    # Preserve input order: legacy and eligible-known interleaved as received.
    order = {id(p): i for i, p in enumerate(products)}
    eligible.sort(key=lambda p: order[id(p)])
    return eligible
