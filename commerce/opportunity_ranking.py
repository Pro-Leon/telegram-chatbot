"""P3.3.12 — deterministic opportunity ranking (pure, in-memory).

Architecture position::

    OfferDefinition -> OpportunityCandidate -> hard eligibility
        -> ranking inputs -> deterministic ranking -> ranking result
        -> [future] live provider sealing

This module provides the ranking layer only: a frozen ranking input, a
pure assembler, an explicit lexicographic policy, and an immutable
in-memory result. No provider verification, no Commerce Offer creation,
no persistence, no network, no clock reads, no LLM, no Redis.

Authority (P3.3.11 audit): ranking consumes ONLY already-established
facts — the eligible ``OpportunityCandidate``, its ``EligibilityVerdict``,
``FanCommercialState`` observed facts, ``OfferHistory`` facts, and the
deterministic subset of ``ConversationState`` (lifecycle, current topic,
recent topics, open threads). Excluded: LLM commerce-signal floats
(``purchase_intent``, ``price_interest``, ``content_interest``),
model-generated relationship/temperature/desire/readiness scores,
pressure/fatigue scores, raw user prose (``last_user_fact``), question
counters, segments, analytics, earnings, taxonomy bundle keys, and any
live provider state.

Policy v1 (lexicographic, ascending = ranked first):

1. ``novelty`` — canonical Vault set never previously offered ranks
   before a previously offered set (observed set history only; no taste
   or preference inference).
2. ``recent-item avoidance`` — fewer candidate Vault IDs present in the
   fan's recently offered Vault IDs ranks first (observed fact only;
   no penalty constant, no cooldown, no rejection threshold).
3. ``offer-type order`` — explicit value-neutral alphabetical catalog
   order (``CORE_BUNDLE, PREMIUM, SINGLE, SMALL_BUNDLE``) breaks
   remaining ties; it never prices, gates, or infers anything.
4. ``stable identity`` — ``(stable_key, version, definition_id)`` final
   tie-break (resolver convention), so identical inputs always produce
   identical output.

Deliberate non-factors in v1 (recorded as context, never ordered on):
fan-level commercial facts (purchase count, spend, AOV, highest, recent
spend) are identical across a fan's candidates and therefore cannot
order them — any use would be gating (forbidden) or financial-capacity
inference (forbidden). Price is read-only and unordered (no
cheapest-first default). Family is descriptive only. Delivered IDs are
fulfillment facts, never ownership. Conversation facts are carried for
future policy versions and audit context; v1 assigns them no weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Ranking policy version, stamped on every result so future policy
#: changes are distinguishable in explainability output.
RANKING_POLICY_VERSION = "v1"

#: Explicit value-neutral tie-break order for offer types (alphabetical).
#: A fixed catalog order, not a value judgement; unknown types sort last.
OFFER_TYPE_ORDER = ("CORE_BUNDLE", "PREMIUM", "SINGLE", "SMALL_BUNDLE")

#: Closed explainability vocabulary. Only factors genuinely participating
#: in the v1 ordering may be emitted (see policy docstring).
NOVEL_CANONICAL_SET = "NOVEL_CANONICAL_SET"
PREVIOUSLY_OFFERED_SET = "PREVIOUSLY_OFFERED_SET"
NOT_RECENTLY_OFFERED = "NOT_RECENTLY_OFFERED"
RECENTLY_OFFERED_ITEMS = "RECENTLY_OFFERED_ITEMS"
OFFER_TYPE_DIVERSITY = "OFFER_TYPE_DIVERSITY"
STABLE_ID_TIEBREAK = "STABLE_ID_TIEBREAK"

RANKING_FACTORS = frozenset(
    {
        NOVEL_CANONICAL_SET,
        PREVIOUSLY_OFFERED_SET,
        NOT_RECENTLY_OFFERED,
        RECENTLY_OFFERED_ITEMS,
        OFFER_TYPE_DIVERSITY,
        STABLE_ID_TIEBREAK,
    }
)

#: Neutral provider placeholder some callers may carry; ranking never
#: verifies and rejects any non-neutral provider claim (fail closed).
PROVIDER_UNVERIFIED = "unverified"

#: Admissible deterministic ConversationState fields (P3.3.11 audit).
#: Everything else (tone affect label, raw user prose, question counters,
#: LLM signal floats, model-generated scores) is excluded.
CONVERSATION_FIELDS = ("lifecycle", "current_topic", "recent_topics", "open_threads")


@dataclass(frozen=True)
class RankingConversationContext:
    """Admissible deterministic conversation facts (immutable).

    Recorded context for future policy versions and audit; v1 assigns no
    ordering weight to any conversation fact.
    """

    lifecycle: str | None = None
    current_topic: str | None = None
    recent_topics: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankingKeys:
    """Precomputed per-candidate ordering discriminators (immutable).

    Derived once, purely, by the assembler from history facts:

    - ``novel_set``: candidate canonical set absent from historical
      offered Vault sets.
    - ``recent_item_overlap``: count of candidate Vault IDs present in
      the fan's recently offered Vault IDs.
    - ``type_order``: index of the candidate offer type in
      ``OFFER_TYPE_ORDER`` (unknown types sort last).
    """

    novel_set: bool
    recent_item_overlap: int
    type_order: int


@dataclass(frozen=True)
class OpportunityRankingInput:
    """One eligible candidate plus its ranking facts (immutable).

    Holds references to the authoritative frozen facts (candidate,
    eligibility verdict, fan commercial state, offer history) without
    duplicating them, plus the derived ordering keys, the admissible
    conversation context, and the explicit evaluation timestamp.
    Candidate price and Vault IDs are preserved exactly (by reference;
    never copied, normalized, or repaired here).
    """

    creator_id: int
    user_id: int
    candidate: Any
    eligibility_verdict: Any
    fan_commercial_state: Any
    offer_history: Any
    keys: RankingKeys
    conversation: RankingConversationContext
    evaluated_at: datetime


@dataclass(frozen=True)
class RankedCandidate:
    """One ranked candidate identity plus its rationale (immutable)."""

    definition_id: int
    stable_key: str
    version: int
    factors: tuple[str, ...]
    novel_set: bool
    recent_item_overlap: int


@dataclass(frozen=True)
class OpportunityRankingResult:
    """Immutable in-memory ranking outcome (never persisted).

    ``ranked`` is ordered best-first; ``selected`` is the winner (first)
    or None when nothing was eligible to rank. ``policy_version`` pins
    the ordering semantics for explainability.
    """

    evaluated_at: datetime
    creator_id: int
    user_id: int
    ranked: tuple[RankedCandidate, ...]
    selected: RankedCandidate | None
    policy_version: str = RANKING_POLICY_VERSION


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dataclass/object or a mapping."""
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _require_scope_id(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return value


def _require_evaluated_at(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("evaluated_at must be a timezone-aware datetime")
    return value


def _conversation_context(conversation: Any) -> RankingConversationContext:
    """Extract only the admissible deterministic conversation fields."""
    if conversation is None:
        return RankingConversationContext()
    lifecycle = _get(conversation, "lifecycle")
    current_topic = _get(conversation, "current_topic")
    recent = _get(conversation, "recent_topics", ())
    threads = _get(conversation, "open_threads", ())

    def _str_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(v for v in value if isinstance(v, str) and v.strip())

    return RankingConversationContext(
        lifecycle=lifecycle if isinstance(lifecycle, str) and lifecycle.strip() else None,
        current_topic=current_topic
        if isinstance(current_topic, str) and current_topic.strip()
        else None,
        recent_topics=_str_tuple(recent),
        open_threads=_str_tuple(threads),
    )


def _ranking_keys(candidate: Any, offer_history: Any, fan_state: Any) -> RankingKeys:
    """Derive ordering discriminators purely from history facts."""
    raw_ids = _get(candidate, "canonical_vault_item_ids")
    if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
        raise ValueError("candidate canonical_vault_item_ids is required for ranking")
    cleaned: list[str] = []
    for item in raw_ids:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("candidate canonical_vault_item_ids is invalid for ranking")
        cleaned.append(item.strip())
    wanted = tuple(cleaned)

    offered_sets = _get(offer_history, "offered_vault_sets", ()) or ()
    try:
        offered = {tuple(s) for s in offered_sets}
    except Exception:
        offered = set()
    novel = wanted not in offered

    recent_ids = _get(fan_state, "recent_offered_vault_ids", ()) or ()
    try:
        recent = {v for v in recent_ids if isinstance(v, str)}
    except Exception:
        recent = set()
    overlap = len(set(cleaned) & recent)

    offer_type = _get(candidate, "offer_type")
    normalized = offer_type.strip().upper() if isinstance(offer_type, str) else ""
    try:
        type_order = OFFER_TYPE_ORDER.index(normalized)
    except ValueError:
        type_order = len(OFFER_TYPE_ORDER)
    return RankingKeys(novel_set=novel, recent_item_overlap=overlap, type_order=type_order)


def assemble_ranking_input(
    candidate: Any,
    *,
    eligibility_verdict: Any,
    fan_commercial_state: Any,
    offer_history: Any,
    conversation: Any = None,
    evaluated_at: datetime | None = None,
) -> OpportunityRankingInput:
    """Assemble one frozen ranking input from already-read facts (pure).

    All facts are caller-supplied (dependency injection); nothing is
    fetched, no clock is read, no provider is contacted, no LLM runs,
    and no input is mutated. Fail-closed: scope mismatch, ineligible
    verdict, non-neutral provider claim, or missing timestamp raises
    instead of producing a rankable input.
    """
    if candidate is None:
        raise TypeError("candidate is required")
    if eligibility_verdict is None:
        raise TypeError("eligibility_verdict is required")
    if fan_commercial_state is None:
        raise TypeError("fan_commercial_state is required")
    if offer_history is None:
        raise TypeError("offer_history is required")
    stamp = _require_evaluated_at(evaluated_at)

    creator_id = _require_scope_id("candidate.creator_id", _get(candidate, "creator_id"))
    user_id = _require_scope_id("candidate.user_id", _get(candidate, "user_id"))
    for label, source in (
        ("fan_commercial_state", fan_commercial_state),
        ("offer_history", offer_history),
    ):
        if _get(source, "creator_id") != creator_id:
            raise ValueError(f"{label} creator scope mismatch")
        if _get(source, "user_id") != user_id:
            raise ValueError(f"{label} user scope mismatch")

    if _get(eligibility_verdict, "eligible") is not True:
        raise ValueError("candidate is not eligible for ranking")
    if tuple(_get(eligibility_verdict, "denial_reasons", ()) or ()) != ():
        raise ValueError("candidate carries denial reasons and cannot be ranked")

    provider_state = _get(candidate, "provider_verification", PROVIDER_UNVERIFIED)
    if provider_state != PROVIDER_UNVERIFIED:
        raise ValueError("candidate provider state is not unverified")

    keys = _ranking_keys(candidate, offer_history, fan_commercial_state)
    return OpportunityRankingInput(
        creator_id=creator_id,
        user_id=user_id,
        candidate=candidate,
        eligibility_verdict=eligibility_verdict,
        fan_commercial_state=fan_commercial_state,
        offer_history=offer_history,
        keys=keys,
        conversation=_conversation_context(conversation),
        evaluated_at=stamp,
    )


def _sort_key(entry: OpportunityRankingInput) -> tuple[Any, ...]:
    candidate = entry.candidate
    stable_key = _get(candidate, "stable_key", "")
    version = _get(candidate, "version", 0)
    definition_id = _get(candidate, "definition_id", 0)
    return (
        0 if entry.keys.novel_set else 1,
        entry.keys.recent_item_overlap,
        entry.keys.type_order,
        str(stable_key),
        int(version) if isinstance(version, int) and not isinstance(version, bool) else 0,
        int(definition_id)
        if isinstance(definition_id, int) and not isinstance(definition_id, bool)
        else 0,
    )


def rank_candidates(
    entries: Any,
    *,
    evaluated_at: datetime | None = None,
) -> OpportunityRankingResult:
    """Rank assembled eligible candidates deterministically (pure).

    Single-batch semantics: every entry must share the call's creator,
    user, and evaluation timestamp. Ordering follows policy v1
    (novelty, recent-item avoidance, offer-type order, stable identity).
    Empty input yields an empty result with ``selected=None``.
    """
    stamp = _require_evaluated_at(evaluated_at)
    items = list(entries or ())
    for entry in items:
        if not isinstance(entry, OpportunityRankingInput):
            raise TypeError("entries must be OpportunityRankingInput instances")
    if not items:
        return OpportunityRankingResult(
            evaluated_at=stamp,
            creator_id=0,
            user_id=0,
            ranked=(),
            selected=None,
        )
    creator_id = items[0].creator_id
    user_id = items[0].user_id
    for entry in items:
        if entry.creator_id != creator_id or entry.user_id != user_id:
            raise ValueError("ranking batch mixes creator/user scope")
        if entry.evaluated_at != stamp:
            raise ValueError("ranking batch mixes evaluation timestamps")

    distinct_types = {str(_get(e.candidate, "offer_type", "")) for e in items}
    use_type_factor = len(distinct_types) > 1

    ordered = sorted(items, key=_sort_key)
    ranked: list[RankedCandidate] = []
    for entry in ordered:
        candidate = entry.candidate
        factors: list[str] = [
            NOVEL_CANONICAL_SET if entry.keys.novel_set else PREVIOUSLY_OFFERED_SET,
            NOT_RECENTLY_OFFERED if entry.keys.recent_item_overlap == 0 else RECENTLY_OFFERED_ITEMS,
        ]
        if use_type_factor:
            factors.append(OFFER_TYPE_DIVERSITY)
        factors.append(STABLE_ID_TIEBREAK)
        ranked.append(
            RankedCandidate(
                definition_id=int(_get(candidate, "definition_id")),
                stable_key=str(_get(candidate, "stable_key")),
                version=int(_get(candidate, "version")),
                factors=tuple(factors),
                novel_set=entry.keys.novel_set,
                recent_item_overlap=entry.keys.recent_item_overlap,
            )
        )
    ranked_tuple = tuple(ranked)
    return OpportunityRankingResult(
        evaluated_at=stamp,
        creator_id=creator_id,
        user_id=user_id,
        ranked=ranked_tuple,
        selected=ranked_tuple[0],
    )
