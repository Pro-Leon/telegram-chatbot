"""P3.3.14.1 — Opportunity Engine orchestration adapter (read-only, deterministic).

Composes the accepted P3.3 primitives into one production-quality callable
boundary without provider, persistence, LLM, or legacy fallback:

    ConversationState
        ↓  FanCommercialState + OfferHistory + Ownership (single ownership fetch via FanCommercialState)
        ↓  OfferDefinition resolver (active only)
        ↓  OpportunityCandidate (candidate_from_definition, pure)
        ↓  Eligibility (evaluate_opportunity_eligibility, pure, hard gates only)
        ↓  Ranking inputs (assemble_ranking_input, pure) + deterministic rank_candidates
        ↓  OpportunityEngineResult (immutable, in-memory)

Stops before sealing/commerce_offers/execute_ppv/send queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("commerce.opportunity_engine")


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return value


def _require_now(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value


def _conversation_to_ranking_context(conversation_state: Any) -> Any:
    """Map deterministic ConversationState → RankingConversationContext via ranking helper.

    Only lifecycle/current_topic/recent_topics/open_threads are forwarded; tone,
    last_user_fact, question counters, etc. are dropped. If conversation_state
    is None, returns None (ranking assembler will produce empty context).
    """
    if conversation_state is None:
        return None
    # Reuse ranking module's internal mapping indirectly by passing the raw
    # state through; assemble_ranking_input will call _conversation_context
    # which extracts only CONVERSATION_FIELDS.  We do not duplicate that
    # logic here — we just pass the object through.  For test injection we
    # also accept a plain dict with those keys.
    return conversation_state


def _status_for_result(candidates: int, eligible: int, selected: Any | None) -> str:
    if candidates == 0:
        return "NO_CANDIDATES"
    if eligible == 0:
        return "NO_ELIGIBLE_CANDIDATES"
    if selected is None:
        return "NO_SELECTION"
    return "RANKED"


@dataclass(frozen=True)
class OpportunityEngineResult:
    """Immutable orchestration result (evaluation only, never persisted)."""

    creator_id: int
    user_id: int
    evaluated_at: datetime

    # Authoritative observed facts (by reference, not copied)
    fan_commercial_state: Any
    offer_history: Any
    owned_vault_ids: frozenset[str]

    # Candidate layer
    candidates: tuple[Any, ...]
    eligible_candidates: tuple[Any, ...]
    ineligible: tuple[tuple[Any, Any], ...]  # (candidate, verdict)

    # Ranking layer
    ranking_inputs: tuple[Any, ...]
    ranking_result: Any | None
    selected_candidate: Any | None

    # Derived convenience
    has_opportunity: bool
    status: str  # NO_CANDIDATES | NO_ELIGIBLE_CANDIDATES | NO_SELECTION | RANKED

    # Conversation context that was actually forwarded to ranking
    ranking_conversation: Any | None = None


async def evaluate_opportunity(
    creator_id: int,
    user_id: int,
    conversation_state: Any | None,
    now: datetime,
    *,
    # Dependency injection for tests (when None, live primitives are called)
    fan_commercial_state: Any | None = None,
    offer_history: Any | None = None,
    owned_vault_ids: frozenset[str] | None = None,
    definitions: list[dict[str, Any]] | None = None,
    mappings: list[dict[str, Any]] | None = None,
) -> OpportunityEngineResult:
    """Evaluate one fan's opportunity (read-only, deterministic).

    Injection params are for tests only; production callers supply only the
    four positional args.  The function never fakes a missing primitive
    result as empty opportunity — DB/data-access failures propagate.
    """
    creator_id = _require_scope("creator_id", creator_id)
    user_id = _require_scope("user_id", user_id)
    now = _require_now(now)

    # 1. Obtain FanCommercialState (authoritative; owns the single ownership fetch)
    if fan_commercial_state is None:
        from commerce.fan_commercial_state import get_fan_commercial_state

        fan_commercial_state = await get_fan_commercial_state(creator_id, user_id)
    else:
        # Validate injected scope (fail closed, not silent)
        if (
            getattr(fan_commercial_state, "creator_id", None) != creator_id
            or getattr(fan_commercial_state, "user_id", None) != user_id
        ):
            raise ValueError("fan_commercial_state scope mismatch")

    # 2. Obtain OfferHistory
    if offer_history is None:
        from commerce.offer_history import get_offer_history

        offer_history = await get_offer_history(creator_id, user_id)
    else:
        if (
            getattr(offer_history, "creator_id", None) != creator_id
            or getattr(offer_history, "user_id", None) != user_id
        ):
            raise ValueError("offer_history scope mismatch")

    # 3. Ownership — reuse FanCommercialState's purchased set (single fetch)
    # If caller injected an explicit owned set, honour it (test injection);
    # otherwise derive from fan state (no second query).
    if owned_vault_ids is None:
        # FanCommercialState.purchased_vault_ids is the authoritative owned set
        owned_raw = getattr(fan_commercial_state, "purchased_vault_ids", None)
        if owned_raw is None:
            # Fallback to ownership primitive only if fan state has no field
            # (should not happen with current FanCommercialState shape)
            from commerce.ownership import fetch_owned_vault_ids

            owned_vault_ids = await fetch_owned_vault_ids(creator_id, user_id)
        else:
            owned_vault_ids = (
                frozenset(owned_raw) if not isinstance(owned_raw, frozenset) else owned_raw
            )
    # validate that injected owned set is a set-like
    if not isinstance(owned_vault_ids, (set, frozenset)):
        # allow list/tuple for test convenience, normalize
        try:
            owned_vault_ids = frozenset(owned_vault_ids)  # type: ignore[arg-type]
        except Exception:
            raise ValueError("owned_vault_ids must be a set of Vault IDs")

    # 4. Resolve active OfferDefinitions (resolver is the only source)
    from commerce.offer_definition_resolver import resolve_offer_definitions

    resolved = await resolve_offer_definitions(
        creator_id, definitions=definitions, mappings=mappings
    )

    # 5. Create OpportunityCandidates
    from commerce.opportunity import candidate_from_definition

    candidates: list[Any] = []
    for rc in resolved:
        # Adapt resolver candidate → candidate_from_definition input
        definition_dict = {
            "id": rc.definition_id,
            "creator_id": rc.creator_id,
            "stable_key": rc.stable_key,
            "version": rc.version,
            "offer_type": rc.offer_type,
            "canonical_vault_item_ids": list(rc.canonical_vault_item_ids),
            "family_id": rc.family_id,
            "price_minor": rc.price_minor,
            "currency": rc.currency,
            "allow_download": rc.allow_download,
            "status": "active",
        }
        try:
            cand = candidate_from_definition(
                creator_id, user_id, definition_dict, mapped_drop_ids=rc.mapped_drop_ids
            )
        except ValueError:
            # Malformed definition (should have been filtered by resolver, but
            # fail closed if it slips through)
            logger.warning(
                "opportunity engine: candidate construction failed creator=%s def=%s",
                creator_id,
                rc.definition_id,
            )
            continue
        candidates.append(cand)

    if not candidates:
        # No valid candidates → explicit no-opportunity (no ranking, no provider)
        return OpportunityEngineResult(
            creator_id=creator_id,
            user_id=user_id,
            evaluated_at=now,
            fan_commercial_state=fan_commercial_state,
            offer_history=offer_history,
            owned_vault_ids=owned_vault_ids,
            candidates=(),
            eligible_candidates=(),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_CANDIDATES",
            ranking_conversation=_conversation_to_ranking_context(conversation_state),
        )

    # 6. Evaluate eligibility (hard gates only, pure)
    from commerce.opportunity_eligibility import evaluate_opportunity_eligibility

    eligible: list[Any] = []
    ineligible: list[tuple[Any, Any]] = []
    for cand in candidates:
        verdict = evaluate_opportunity_eligibility(cand, owned_vault_ids, offer_history)
        if verdict.eligible:
            eligible.append(cand)
        else:
            ineligible.append((cand, verdict))

    if not eligible:
        return OpportunityEngineResult(
            creator_id=creator_id,
            user_id=user_id,
            evaluated_at=now,
            fan_commercial_state=fan_commercial_state,
            offer_history=offer_history,
            owned_vault_ids=owned_vault_ids,
            candidates=tuple(candidates),
            eligible_candidates=(),
            ineligible=tuple(ineligible),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_ELIGIBLE_CANDIDATES",
            ranking_conversation=_conversation_to_ranking_context(conversation_state),
        )

    # 7. Assemble ranking inputs (pure, scope+eligibility verified, no provider)
    from commerce.opportunity_ranking import assemble_ranking_input, rank_candidates

    ranking_inputs: list[Any] = []
    # Re-evaluate verdicts to pass into assembler (assembler requires the same verdict object)
    # Build a map candidate -> verdict for eligible only
    verdict_map: dict[int, Any] = {}
    for cand in eligible:
        v = evaluate_opportunity_eligibility(cand, owned_vault_ids, offer_history)
        verdict_map[id(cand)] = v

    for cand in eligible:
        verdict = verdict_map[id(cand)]
        try:
            ri = assemble_ranking_input(
                cand,
                eligibility_verdict=verdict,
                fan_commercial_state=fan_commercial_state,
                offer_history=offer_history,
                conversation=conversation_state,
                evaluated_at=now,
            )
        except ValueError as exc:
            # Assembler fail-closed (scope/provider state) — treat as ineligible for ranking
            logger.warning(
                "opportunity engine: ranking input assembly failed creator=%s def=%s: %s",
                creator_id,
                getattr(cand, "definition_id", "?"),
                exc,
            )
            continue
        ranking_inputs.append(ri)

    if not ranking_inputs:
        return OpportunityEngineResult(
            creator_id=creator_id,
            user_id=user_id,
            evaluated_at=now,
            fan_commercial_state=fan_commercial_state,
            offer_history=offer_history,
            owned_vault_ids=owned_vault_ids,
            candidates=tuple(candidates),
            eligible_candidates=tuple(eligible),
            ineligible=tuple(ineligible),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_SELECTION",
            ranking_conversation=_conversation_to_ranking_context(conversation_state),
        )

    # 8. Deterministically rank
    ranking_result = rank_candidates(ranking_inputs, evaluated_at=now)

    # Map selected RankedCandidate back to OpportunityCandidate via definition_id/version
    selected_candidate: Any | None = None
    if ranking_result.selected is not None:
        sel = ranking_result.selected
        for cand in eligible:
            if int(getattr(cand, "definition_id", -1)) == int(sel.definition_id) and int(
                getattr(cand, "version", -1)
            ) == int(sel.version):
                selected_candidate = cand
                break

    has_opportunity = selected_candidate is not None
    status = _status_for_result(len(candidates), len(eligible), selected_candidate)

    return OpportunityEngineResult(
        creator_id=creator_id,
        user_id=user_id,
        evaluated_at=now,
        fan_commercial_state=fan_commercial_state,
        offer_history=offer_history,
        owned_vault_ids=owned_vault_ids,
        candidates=tuple(candidates),
        eligible_candidates=tuple(eligible),
        ineligible=tuple(ineligible),
        ranking_inputs=tuple(ranking_inputs),
        ranking_result=ranking_result,
        selected_candidate=selected_candidate,
        has_opportunity=has_opportunity,
        status=status,
        ranking_conversation=_conversation_to_ranking_context(conversation_state),
    )
