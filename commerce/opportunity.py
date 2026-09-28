"""P3.3.10 — immutable opportunity candidate (deterministic, pure).

A candidate is the deterministic projection of one active OfferDefinition
onto one fan: definition identity, verbatim offer facts, catalog Drop
references, and neutral eligibility/provider slots. No ranking, no scoring,
no probabilities, no pressure/fatigue signals, no conversation prose, no
LLM output, no provider verification, no persistence.

Architecture position::

    OfferDefinition -> deterministic candidate -> hard eligibility
        -> future ranking -> future provider verification/sealing

Construction is pure (no I/O, no clock, no network). Eligibility is NOT
decided here; :mod:`commerce.opportunity_eligibility` decides it from the
candidate plus caller-supplied facts. The ``overlap_result`` /
``denial_reason`` / ``prior_offer_facts`` slots start neutral (unknown)
so a freshly built candidate never claims an eligibility verdict it has
not computed.

Provider boundary: ``provider_verification`` is always ``"unverified"``
in this phase. It is an immutable neutral placeholder, not provider
truth. No verification timestamp or live price is stored anywhere here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

PROVIDER_VERIFICATION_UNVERIFIED = "unverified"

OVERLAP_UNKNOWN = "unknown"

#: Closed set of overlap slot values (mirrors commerce.ownership verdicts
#: plus the neutral pre-evaluation state).
OVERLAP_STATES = frozenset({"unknown", "zero", "partial", "full", "invalid"})


@dataclass(frozen=True)
class CandidatePriorOfferFacts:
    """Deterministic prior-offer facts attached to one candidate (immutable).

    ``definition_history_available`` is always False in this phase:
    historical ``commerce_offers`` rows carry no OfferDefinition identity,
    so definition-level reoffer state cannot be established. Set-identity
    facts (``was_canonical_set_offered`` / ``has_active_duplicate``) are
    derived from frozen Vault snapshots only.
    """

    was_canonical_set_offered: bool
    has_active_duplicate: bool
    definition_history_available: bool = False


@dataclass(frozen=True)
class OpportunityCandidate:
    """One deterministic opportunity candidate (immutable)."""

    # Identity.
    creator_id: int
    user_id: int
    definition_id: int
    stable_key: str
    version: int

    # Offer facts (verbatim from the definition; never repaired here).
    offer_type: str
    canonical_vault_item_ids: tuple[str, ...]
    family_id: int | None
    price_minor: int
    currency: str
    allow_download: bool
    mapped_drop_ids: tuple[str, ...] = field(default_factory=tuple)

    # Lifecycle slot: the evaluator rejects anything but "active".
    definition_status: str = "active"

    # Eligibility slots (neutral until the evaluator decides).
    overlap_result: str = OVERLAP_UNKNOWN
    denial_reason: str | None = None
    prior_offer_facts: CandidatePriorOfferFacts | None = None

    # Provider slot (neutral placeholder, never provider truth).
    provider_verification: str = PROVIDER_VERIFICATION_UNVERIFIED


def _require_positive_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return value


def candidate_from_definition(
    creator_id: int,
    user_id: int,
    definition: dict[str, Any] | Any,
    mapped_drop_ids: Iterable[str] | None = None,
) -> OpportunityCandidate:
    """Build the deterministic candidate for one fan + one definition row.

    Pure: no I/O, no clock. The definition row must already be canonical
    (sorted unique Vault IDs, valid type/price/currency, active status is
    NOT required here — it is carried verbatim so the evaluator can gate
    on it). Malformed rows raise ``ValueError`` (fail closed, never
    repaired, never truncated). ``family_id`` is carried as descriptive
    identity only.
    """
    from typing import Iterable as _Iterable  # local to keep module import-light

    _require_positive_int("creator_id", creator_id)
    _require_positive_int("user_id", user_id)

    get: Any
    if isinstance(definition, dict):
        get = definition.get
    else:

        def get(key: str, default: Any = None) -> Any:
            try:
                return definition[key]
            except Exception:
                return getattr(definition, key, default)

    definition_creator = get("creator_id")
    _require_positive_int("definition.creator_id", definition_creator)
    if int(definition_creator) != int(creator_id):
        raise ValueError("definition creator does not match candidate creator")

    definition_id = get("id")
    if not isinstance(definition_id, int) or isinstance(definition_id, bool):
        raise ValueError("definition id is required")

    stable_key = get("stable_key")
    if not isinstance(stable_key, str) or not stable_key.strip():
        raise ValueError("definition stable_key is required")

    version = get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("definition version is required")

    offer_type = get("offer_type")
    if not isinstance(offer_type, str) or not offer_type.strip():
        raise ValueError("definition offer_type is required")
    offer_type = offer_type.strip().upper()

    raw_ids = get("canonical_vault_item_ids")
    if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
        raise ValueError("definition canonical_vault_item_ids is required")
    cleaned: list[str] = []
    for item in raw_ids:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("definition canonical_vault_item_ids is invalid")
        cleaned.append(item.strip())
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("definition canonical_vault_item_ids contains duplicates")
    from commerce.vault_sets import MAX_VAULT_ITEMS, canonical_identity_ids

    try:
        canonical = canonical_identity_ids(cleaned)
    except ValueError:
        raise ValueError("definition canonical_vault_item_ids is invalid") from None
    if not canonical or canonical != cleaned:
        raise ValueError(
            "definition canonical_vault_item_ids must already be canonical "
            "(sorted unique, 1-10 items)"
        )
    if len(canonical) > MAX_VAULT_ITEMS:
        raise ValueError("definition canonical_vault_item_ids exceeds limit of 10")

    price = get("price_minor")
    if isinstance(price, bool) or not isinstance(price, int):
        raise ValueError("definition price_minor is required")
    # Price negativity is an eligibility matter; carry verbatim so the
    # evaluator reports INVALID_PRICE instead of construction failing.
    # Non-integer prices, however, are a malformed row.

    currency = get("currency")
    if not isinstance(currency, str):
        raise ValueError("definition currency is required")
    # Empty/non-USD currency is carried verbatim for the evaluator gate.

    family_id = get("family_id")
    if family_id is not None and (
        not isinstance(family_id, int) or isinstance(family_id, bool)
    ):
        raise ValueError("definition family_id must be an integer or None")

    status = get("status", "active")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("definition status is required")
    status = status.strip().lower()

    drops: tuple[str, ...] = ()
    if mapped_drop_ids is not None:
        if not isinstance(mapped_drop_ids, (list, tuple, set, frozenset)):
            raise ValueError("mapped_drop_ids must be a sequence of strings")
        seen: set[str] = set()
        ordered: list[str] = []
        for cuid in mapped_drop_ids:
            if not isinstance(cuid, str) or not cuid.strip():
                raise ValueError("mapped_drop_ids must contain non-empty strings")
            token = cuid.strip()
            if token not in seen:
                seen.add(token)
                ordered.append(token)
        drops = tuple(sorted(seen))

    return OpportunityCandidate(
        creator_id=int(creator_id),
        user_id=int(user_id),
        definition_id=int(definition_id),
        stable_key=stable_key.strip(),
        version=int(version),
        offer_type=offer_type,
        canonical_vault_item_ids=tuple(canonical),
        family_id=family_id,
        price_minor=price,
        currency=currency,
        allow_download=bool(get("allow_download")),
        mapped_drop_ids=drops,
        definition_status=status,
    )
