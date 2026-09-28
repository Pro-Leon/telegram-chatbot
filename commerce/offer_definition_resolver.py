"""P3.3.7 — OfferDefinition resolver (pure catalog primitive, read-only).

Answers only::

    Which already-defined OfferDefinitions are structurally eligible
    catalog candidates for this creator?

It does NOT answer whether a fan should receive an offer now — that belongs
to the future Opportunity Candidate Engine (fan ownership via P3.3.2,
history, context, ranking).

Read-only catalog semantics:

- creator-scoped reads only (``db.offer_definitions`` DAO primitives);
- active definitions only — draft/retired are never candidates;
- structural eligibility re-checked defensively (valid type, 1–10 canonical
  Vault IDs, non-negative price, non-empty currency); malformed rows are
  excluded (fail closed, never repaired, never truncated);
- all creator-scoped mappings attached (``mapped_drop_ids``), never ranked
  or selected — a mapping is a candidate Drop reference, not liveness proof;
- explicit deterministic ordering ``(stable_key, version, definition_id)``;
- prices/currency/allow_download/family_id/offer_type returned verbatim;
- zero Dropfans calls, zero ownership calls, zero LLM calls, zero writes,
  zero caching. Database errors propagate (``[]`` means "no eligible
  definitions", never "catalog unavailable").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("commerce.offer_definition_resolver")


@dataclass(frozen=True)
class OfferDefinitionCandidate:
    """One structurally eligible catalog candidate (read-only data, not an opportunity)."""

    definition_id: int
    creator_id: int
    stable_key: str
    version: int
    offer_type: str
    canonical_vault_item_ids: tuple[str, ...]
    family_id: int | None
    price_minor: int
    currency: str
    allow_download: bool
    mapped_drop_ids: tuple[str, ...]


def _is_canonical_ids(value: Any) -> list[str] | None:
    """Return the ID list when it is already canonical (sorted unique, 1–10).

    Uses the P3.2 identity primitive for the canonical form; the stored row
    must already equal it — the resolver never repairs or truncates.
    Returns None for malformed rows (fail closed downstream).
    """
    from commerce.vault_sets import canonical_identity_ids

    if not isinstance(value, (list, tuple)) or not value:
        return None
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        cleaned.append(item.strip())
    if not cleaned or len(cleaned) > 10:
        return None
    try:
        canonical = canonical_identity_ids(cleaned)
    except ValueError:
        return None
    if canonical != cleaned:
        return None
    return canonical


def is_structurally_eligible(definition: dict[str, Any] | Any) -> bool:
    """Pure eligibility predicate for one definition row (no I/O)."""
    from db.offer_definitions import OFFER_TYPES

    get: Any
    if isinstance(definition, dict):
        get = definition.get
    else:
        def get(key: str, default: Any = None) -> Any:
            try:
                return definition[key]
            except Exception:
                return getattr(definition, key, default)

    if get("status") != "active":
        return False
    offer_type = get("offer_type")
    if not isinstance(offer_type, str) or offer_type.strip().upper() not in OFFER_TYPES:
        return False
    if _is_canonical_ids(get("canonical_vault_item_ids")) is None:
        return False
    price = get("price_minor")
    if isinstance(price, bool) or not isinstance(price, int) or price < 0:
        return False
    currency = get("currency")
    if not isinstance(currency, str) or not currency.strip():
        return False
    creator_id = get("creator_id")
    if not isinstance(creator_id, int) or isinstance(creator_id, bool) or creator_id <= 0:
        return False
    definition_id = get("id")
    if not isinstance(definition_id, int) or isinstance(definition_id, bool):
        return False
    version = get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return False
    stable_key = get("stable_key")
    if not isinstance(stable_key, str) or not stable_key.strip():
        return False
    return True


def _to_candidate(
    definition: dict[str, Any],
    mapped_drop_ids: tuple[str, ...],
) -> OfferDefinitionCandidate:
    canonical = _is_canonical_ids(definition.get("canonical_vault_item_ids")) or []
    return OfferDefinitionCandidate(
        definition_id=int(definition["id"]),
        creator_id=int(definition["creator_id"]),
        stable_key=str(definition["stable_key"]),
        version=int(definition["version"]),
        offer_type=str(definition["offer_type"]).strip().upper(),
        canonical_vault_item_ids=tuple(canonical),
        family_id=definition.get("family_id"),
        price_minor=int(definition["price_minor"]),
        currency=str(definition["currency"]),
        allow_download=bool(definition["allow_download"]),
        mapped_drop_ids=tuple(mapped_drop_ids),
    )


async def resolve_offer_definitions(
    creator_id: int,
    *,
    definitions: list[dict[str, Any]] | None = None,
    mappings: list[dict[str, Any]] | None = None,
) -> list[OfferDefinitionCandidate]:
    """Return ordered eligible catalog candidates for one creator.

    Production path reads active definitions plus the creator's mappings via
    the existing DAO primitives (both creator-scoped; mappings additionally
    matched on ``definition.creator_id == mapping.creator_id``). ``[]``
    means no eligible definitions. Database errors propagate unchanged —
    never converted to ``[]``. ``definitions``/``mappings`` are injectable
    rows for tests/inspection only.
    """
    if not isinstance(creator_id, int) or isinstance(creator_id, bool) or creator_id <= 0:
        raise ValueError("creator_id is required")
    from db import offer_definitions as _odb

    if definitions is None:
        definitions = await _odb.list_offer_definitions(creator_id, status="active")
    if mappings is None:
        mappings = await _odb.list_offer_definition_drops(creator_id)
    drops_by_definition: dict[Any, list[str]] = {}
    for mapping in mappings or []:
        try:
            if int(mapping.get("creator_id")) != int(creator_id):
                continue
            cuid = mapping.get("dropfans_product_id")
            if not isinstance(cuid, str) or not cuid.strip():
                continue
            drops_by_definition.setdefault(mapping.get("definition_id"), []).append(
                cuid.strip()
            )
        except Exception:
            continue
    candidates: list[OfferDefinitionCandidate] = []
    for definition in definitions or []:
        try:
            if int(definition.get("creator_id")) != int(creator_id):
                continue
        except Exception:
            continue
        if not is_structurally_eligible(definition):
            try:
                logger.debug(
                    "offer resolver: excluded ineligible definition creator=%s id=%s",
                    creator_id,
                    (definition or {}).get("id"),
                )
            except Exception:
                pass
            continue
        mapped = tuple(sorted(set(drops_by_definition.get(definition.get("id"), []))))
        candidates.append(_to_candidate(definition, mapped))
    candidates.sort(key=lambda c: (c.stable_key, c.version, c.definition_id))
    return candidates
