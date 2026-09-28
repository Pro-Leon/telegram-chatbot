"""P3.4 — scheduler re-engagement eligibility convergence.

Decides whether one already-existing stale pending commerce offer may be
referenced by a governed re-engagement message. The stale offer row is the
sole candidate identity; nothing is invented from catalogs, mirrors,
providers, models, or audience groupings.

Verdict is allow-or-skip only. This module never creates commerce offers,
never sets prices, never selects Drops or Vault sets, never ranks, never
seals, never sends, and never calls the legacy commerce chain.

Authority reused verbatim (no scheduler-specific copies):

* ``commerce.dao.get_offer`` — fresh subject re-read (still active?)
* ``commerce.vault_sets.canonical_identity_ids`` — frozen snapshot identity
* ``db.offer_definitions.get_offer_definition`` — catalog validity mapping
* ``commerce.ownership.fetch_owned_vault_ids`` + ``classify_vault_overlap``
  — ownership policy (ZERO overlap only; partial/full never sliced)
* ``commerce.dao.list_offers_for_user`` — other-active duplicate check
  (same canonical Vault set, excluding the subject itself)

Every store failure fails closed (skip). Every unmappable or invalid
candidate fails closed (skip).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("commerce.reengagement_eligibility")

ELIGIBLE = "eligible"
INVALID_SCOPE = "invalid_scope"
INVALID_OFFER = "invalid_offer"
OFFER_NOT_ACTIVE = "offer_not_active"
STORE_UNAVAILABLE = "store_unavailable"
INVALID_SNAPSHOT = "invalid_snapshot"
DEFINITION_UNRESOLVABLE = "definition_unresolvable"
DEFINITION_NOT_ACTIVE = "definition_not_active"
OWNERSHIP_UNAVAILABLE = "ownership_unavailable"
OWNED_FULL = "owned_full"
OWNED_PARTIAL = "owned_partial"
ACTIVE_DUPLICATE = "active_duplicate"

_ACTIVE_STATES = ("pending", "clicked")


def _scope(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return int(value)


def _offer_id(offer: Any) -> int | None:
    try:
        raw = offer.get("id") if isinstance(offer, dict) else getattr(offer, "id", None)
        parsed = int(raw)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _provenance(reason: Any) -> tuple[int | None, int | None, str | None]:
    """Extract (definition_id, version, stable_key) from a sealed reason envelope."""
    try:
        if reason is None:
            return None, None, None
        parsed = json.loads(reason) if isinstance(reason, str) else reason
        if not isinstance(parsed, dict):
            return None, None, None
        try:
            definition_id = parsed.get("definition_id")
            definition_id = int(definition_id) if definition_id is not None else None
            if definition_id is not None and definition_id <= 0:
                definition_id = None
        except Exception:
            definition_id = None
        try:
            version = parsed.get("definition_version")
            version = int(version) if version is not None else None
            if version is not None and version < 1:
                version = None
        except Exception:
            version = None
        stable = parsed.get("stable_key")
        stable_key = str(stable) if stable is not None and str(stable).strip() else None
        if definition_id is None or version is None:
            return None, None, None
        return definition_id, version, stable_key
    except Exception:
        return None, None, None


def _canonical(ids: Any) -> tuple[str, ...] | None:
    try:
        from commerce.vault_sets import canonical_identity_ids

        if not isinstance(ids, (list, tuple)) or not ids:
            return None
        cleaned = canonical_identity_ids([str(x) for x in list(ids)])
        return tuple(cleaned) if cleaned else None
    except Exception:
        return None


async def is_stale_offer_reengageable(
    *,
    creator_id: int,
    user_id: int,
    offer: Any,
) -> tuple[bool, str]:
    """Decide whether a stale pending offer may back a re-engagement message.

    Args:
        creator_id/user_id: scheduler sweep scope (authoritative).
        offer: the stale ``commerce_offers`` row from the sweep (identity
            source only; liveness is re-established by fresh reads below).

    Returns:
        ``(True, "eligible")`` only when the subject row is still active,
        its catalog definition is still active, the fan owns none of its
        Vault set, and no *other* active offer carries the same canonical
        Vault set. Anything else returns ``(False, reason)``.
    """
    scope_creator = _scope(creator_id)
    scope_user = _scope(user_id)
    if scope_creator is None or scope_user is None:
        return False, INVALID_SCOPE
    if not isinstance(offer, dict):
        return False, INVALID_OFFER
    try:
        offer_creator = offer.get("creator_id")
        offer_user = offer.get("user_id")
    except Exception:
        return False, INVALID_OFFER
    if offer_creator != scope_creator or offer_user != scope_user:
        return False, INVALID_OFFER
    subject_id = _offer_id(offer)
    if subject_id is None:
        return False, INVALID_OFFER

    # 1. Subject must still be active (fresh read; the sweep row may be stale).
    try:
        from commerce.dao import get_offer

        fresh = await get_offer(scope_creator, subject_id)
    except Exception as exc:
        logger.debug("re-engagement subject re-read failed: %s", exc)
        return False, STORE_UNAVAILABLE
    if fresh is None or fresh.get("state") not in _ACTIVE_STATES:
        return False, OFFER_NOT_ACTIVE

    # 2. Frozen snapshot identity comes from the live row, never the mirror.
    canonical = _canonical(fresh.get("vault_item_ids"))
    if canonical is None:
        return False, INVALID_SNAPSHOT

    # 3. Catalog mapping must resolve to a still-active definition version.
    definition_id, version, stable_key = _provenance(fresh.get("reason"))
    if definition_id is None or version is None:
        return False, DEFINITION_UNRESOLVABLE
    try:
        from db.offer_definitions import get_offer_definition

        definition = await get_offer_definition(scope_creator, definition_id, version)
    except Exception as exc:
        logger.debug("re-engagement definition lookup failed: %s", exc)
        return False, STORE_UNAVAILABLE
    if definition is None:
        return False, DEFINITION_UNRESOLVABLE
    try:
        def_status = definition.get("status")
        def_stable = definition.get("stable_key")
    except Exception:
        return False, DEFINITION_UNRESOLVABLE
    if not isinstance(def_status, str) or def_status.strip().lower() != "active":
        return False, DEFINITION_NOT_ACTIVE
    if stable_key is not None and def_stable != stable_key:
        return False, DEFINITION_UNRESOLVABLE

    # 4. Ownership policy: ZERO overlap only (partial/full never sliced).
    try:
        from commerce.ownership import (
            OverlapResult,
            classify_vault_overlap,
            fetch_owned_vault_ids,
        )

        owned = await fetch_owned_vault_ids(scope_creator, scope_user)
        overlap = classify_vault_overlap(list(canonical), owned)
    except Exception as exc:
        logger.debug("re-engagement ownership check failed: %s", exc)
        return False, OWNERSHIP_UNAVAILABLE
    if overlap is OverlapResult.FULL:
        return False, OWNED_FULL
    if overlap is OverlapResult.PARTIAL:
        return False, OWNED_PARTIAL
    if overlap is not OverlapResult.ZERO:
        return False, INVALID_SNAPSHOT

    # 5. No *other* active offer may carry the same canonical Vault set.
    try:
        from commerce.dao import list_offers_for_user

        actives = await list_offers_for_user(
            scope_user, creator_id=scope_creator, limit=100
        )
    except Exception as exc:
        logger.debug("re-engagement active-offer check failed: %s", exc)
        return False, STORE_UNAVAILABLE
    try:
        for row in actives or []:
            try:
                if row.get("state") not in _ACTIVE_STATES:
                    continue
                try:
                    row_id = int(row.get("id"))
                except Exception:
                    continue
                if row_id == subject_id:
                    continue
                if _canonical(row.get("vault_item_ids")) == canonical:
                    return False, ACTIVE_DUPLICATE
            except Exception:
                continue
    except Exception:
        return False, STORE_UNAVAILABLE

    return True, ELIGIBLE
