"""P3.3.10 — pure opportunity eligibility evaluator (hard gates only).

Deterministic verdict for one :class:`OpportunityCandidate
<commerce.opportunity.OpportunityCandidate>` given caller-supplied facts.
Pure: no database, no Redis, no Dropfans calls, no LLM, no clock, no
ranking, no scoring, no pricing, no persistence.

Architecture position::

    OfferDefinition -> deterministic candidate -> hard eligibility
        -> future ranking -> future provider verification/sealing

Hard gates only (fail closed, never repaired):

- creator/user scope match against the evaluation context;
- definition validity: active status, known offer type, canonical Vault
  set (1-10 sorted unique IDs), non-negative integer price, USD currency;
- ownership overlap via the authoritative P3.3.2 primitive
  (``classify_vault_overlap`` / ``is_overlap_eligible``): only ZERO
  overlap is eligible — PARTIAL/FULL/INVALID reject whole, never sliced;
- active duplicate: a currently active (pending/clicked) offer for the
  same fan carrying the exact same canonical Vault set rejects, but only
  where that identity is deterministically established from frozen
  snapshots.

Explicitly NOT gates (facts for future ranking/suppression, never
denials here):

- historical (non-active) prior offers — no P3.3.9 audit establishes
  recency/frequency as eligibility policy, so ``was_canonical_set_offered``
  is reported, never rejected on;
- ContentFamily membership (descriptive identity only);
- delivered content (delivery is not ownership);
- provider verification (always ``"unverified"`` here; live verification
  belongs to the later sealing phase).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Canonical offer-type vocabulary (mirrors ``db.offer_definitions.OFFER_TYPES``
#: / ``commerce.models.OFFER_DEFINITION_TYPES``; restated here so this pure
#: module never imports the DAO/DB layer).
OFFER_TYPES = frozenset({"SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"})

#: Maximum Vault items per candidate (mirrors ``commerce.vault_sets.MAX_VAULT_ITEMS``).
MAX_VAULT_ITEMS = 10

#: Neutral provider slot value (mirrors
#: ``commerce.opportunity.PROVIDER_VERIFICATION_UNVERIFIED``; restated so this
#: module stays import-light — the candidate test asserts equality).
PROVIDER_VERIFICATION_UNVERIFIED = "unverified"

CREATOR_SCOPE_MISMATCH = "CREATOR_SCOPE_MISMATCH"
USER_SCOPE_MISMATCH = "USER_SCOPE_MISMATCH"
DEFINITION_NOT_ACTIVE = "DEFINITION_NOT_ACTIVE"
INVALID_OFFER_TYPE = "INVALID_OFFER_TYPE"
INVALID_VAULT_SET = "INVALID_VAULT_SET"
OWNERSHIP_FULL_OVERLAP = "OWNERSHIP_FULL_OVERLAP"
OWNERSHIP_PARTIAL_OVERLAP = "OWNERSHIP_PARTIAL_OVERLAP"
OWNERSHIP_INVALID = "OWNERSHIP_INVALID"
INVALID_PRICE = "INVALID_PRICE"
INVALID_CURRENCY = "INVALID_CURRENCY"
ACTIVE_DUPLICATE_OFFER = "ACTIVE_DUPLICATE_OFFER"

DENIAL_REASONS = frozenset(
    {
        CREATOR_SCOPE_MISMATCH,
        USER_SCOPE_MISMATCH,
        DEFINITION_NOT_ACTIVE,
        INVALID_OFFER_TYPE,
        INVALID_VAULT_SET,
        OWNERSHIP_FULL_OVERLAP,
        OWNERSHIP_PARTIAL_OVERLAP,
        OWNERSHIP_INVALID,
        INVALID_PRICE,
        INVALID_CURRENCY,
        ACTIVE_DUPLICATE_OFFER,
    }
)


@dataclass(frozen=True)
class EligibilityVerdict:
    """Deterministic hard-eligibility verdict for one candidate (immutable).

    ``eligible`` is True only when ``denial_reasons`` is empty. Reasons are
    collected in a fixed gate order so repeated evaluation of identical
    inputs yields an identical verdict. ``was_canonical_set_offered`` is a
    reported fact for future ranking/suppression — never a denial.
    ``provider_verification`` is always ``"unverified"`` in this phase.
    """

    eligible: bool
    denial_reasons: tuple[str, ...]
    overlap_result: str
    provider_verification: str = PROVIDER_VERIFICATION_UNVERIFIED
    was_canonical_set_offered: bool = False
    has_active_duplicate: bool = False


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dataclass/object or a mapping (codebase convention)."""
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _require_context_id(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return value


def _is_canonical_vault_set(value: Any) -> bool:
    """Return True only when ``value`` is already canonical (sorted unique, 1-10)."""
    from commerce.vault_sets import canonical_identity_ids

    if not isinstance(value, (list, tuple)) or not value:
        return False
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return False
        cleaned.append(item.strip())
    if not cleaned or len(cleaned) > MAX_VAULT_ITEMS:
        return False
    if len(set(cleaned)) != len(cleaned):
        return False
    try:
        canonical = canonical_identity_ids(cleaned)
    except ValueError:
        return False
    return canonical == cleaned


def _is_valid_price(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= 0


def _is_valid_currency(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "USD"


def _history_flag(history: Any, method: str, fallback_sets: Any, ids: Any) -> bool:
    """Evaluate a set-identity history predicate without ever raising on content.

    Prefers the history primitive's own pure helper; falls back to direct
    membership over the exposed snapshot tuples. Unusable input yields False
    (no deterministically established fact), never True.
    """
    candidate_method = getattr(history, method, None)
    if callable(candidate_method):
        try:
            return bool(candidate_method(ids))
        except Exception:
            return False
    try:
        from commerce.vault_sets import canonical_identity_ids

        if not isinstance(ids, (list, tuple)) or not ids:
            return False
        wanted = tuple(canonical_identity_ids(list(ids)))
        if not wanted:
            return False
        return wanted in {tuple(s) for s in (fallback_sets or ())}
    except Exception:
        return False


def evaluate_opportunity_eligibility(
    candidate: Any,
    owned_vault_ids: Any,
    offer_history: Any,
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
) -> EligibilityVerdict:
    """Evaluate hard eligibility for one candidate (pure, deterministic).

    Args:
        candidate: an :class:`OpportunityCandidate
            <commerce.opportunity.OpportunityCandidate>` (attribute or
            mapping access). Malformed domain content yields denials, never
            silent repair.
        owned_vault_ids: caller-supplied owned Vault IDs (e.g. from the
            P3.3.1 ``get_owned_vault_ids`` primitive). Passed verbatim to
            the authoritative overlap classifier.
        offer_history: caller-supplied :class:`OfferHistory
            <commerce.offer_history.OfferHistory>` for the same fan.
        creator_id/user_id: evaluation context. When omitted, the history's
            own creator/user scope is the context. Mismatches between the
            candidate and the context deny; a history scoped to a different
            fan than the context also denies (fail closed).

    Raises:
        TypeError: when ``candidate`` or ``offer_history`` is missing.
        ValueError: when the evaluation context IDs are not positive ints.

    Returns:
        An immutable :class:`EligibilityVerdict`. No ranking, no scores, no
        probabilities, no provider calls, no I/O of any kind.
    """
    from commerce.ownership import OverlapResult, classify_vault_overlap

    if candidate is None:
        raise TypeError("candidate is required")
    if offer_history is None:
        raise TypeError("offer_history is required")

    context_creator = (
        _require_context_id("creator_id", creator_id)
        if creator_id is not None
        else _require_context_id("creator_id", _get(offer_history, "creator_id"))
    )
    context_user = (
        _require_context_id("user_id", user_id)
        if user_id is not None
        else _require_context_id("user_id", _get(offer_history, "user_id"))
    )

    reasons: list[str] = []

    cand_creator = _get(candidate, "creator_id")
    cand_user = _get(candidate, "user_id")
    if cand_creator != context_creator:
        reasons.append(CREATOR_SCOPE_MISMATCH)
    if cand_user != context_user:
        reasons.append(USER_SCOPE_MISMATCH)

    hist_creator = _get(offer_history, "creator_id")
    hist_user = _get(offer_history, "user_id")
    if hist_creator != context_creator and CREATOR_SCOPE_MISMATCH not in reasons:
        reasons.append(CREATOR_SCOPE_MISMATCH)
    if hist_user != context_user and USER_SCOPE_MISMATCH not in reasons:
        reasons.append(USER_SCOPE_MISMATCH)

    status = _get(candidate, "definition_status", "active")
    if not isinstance(status, str) or status.strip().lower() != "active":
        reasons.append(DEFINITION_NOT_ACTIVE)

    offer_type = _get(candidate, "offer_type")
    if not isinstance(offer_type, str) or offer_type.strip().upper() not in OFFER_TYPES:
        reasons.append(INVALID_OFFER_TYPE)

    vault_ids = _get(candidate, "canonical_vault_item_ids")
    if not _is_canonical_vault_set(vault_ids):
        reasons.append(INVALID_VAULT_SET)

    if not _is_valid_price(_get(candidate, "price_minor")):
        reasons.append(INVALID_PRICE)
    if not _is_valid_currency(_get(candidate, "currency")):
        reasons.append(INVALID_CURRENCY)

    try:
        overlap = classify_vault_overlap(vault_ids, owned_vault_ids)
    except Exception:
        overlap = OverlapResult.INVALID
    if overlap is OverlapResult.ZERO:
        pass
    elif overlap is OverlapResult.PARTIAL:
        reasons.append(OWNERSHIP_PARTIAL_OVERLAP)
    elif overlap is OverlapResult.FULL:
        reasons.append(OWNERSHIP_FULL_OVERLAP)
    else:
        reasons.append(OWNERSHIP_INVALID)

    has_active_duplicate = _history_flag(
        offer_history,
        "has_active_canonical_set",
        _get(offer_history, "active_vault_sets"),
        vault_ids,
    )
    if has_active_duplicate:
        reasons.append(ACTIVE_DUPLICATE_OFFER)

    # Reported fact only: a historical (non-active) prior offer for the same
    # canonical set never denies in this phase — no audit establishes it as
    # eligibility policy. Reserved for future ranking/suppression.
    was_offered = _history_flag(
        offer_history,
        "was_canonical_set_offered",
        _get(offer_history, "offered_vault_sets"),
        vault_ids,
    )

    return EligibilityVerdict(
        eligible=not reasons,
        denial_reasons=tuple(reasons),
        overlap_result=str(overlap.value),
        provider_verification=PROVIDER_VERIFICATION_UNVERIFIED,
        was_canonical_set_offered=was_offered,
        has_active_duplicate=has_active_duplicate,
    )
