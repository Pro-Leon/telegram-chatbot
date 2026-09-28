"""P3.5.4A — Sanitized Optimization Input Contract (advisory boundary only).

Minimum safe contract letting a *future* advisory optimizer consume
trustworthy, point-in-time commercial evidence without becoming commercial
authority. This module implements NO optimizer, NO scoring weights, NO
learning, NO experiments, and changes NO live decision path. Building an
``OptimizationInput`` is observational infrastructure: it never changes which
offer is selected, sealed, sent, or attributed.

Point-in-time invariant (the core guarantee of this module):

    Every feature is projected from data frozen at ``evaluated_at`` —
    the persisted decision snapshot (``decision_snapshot``), immutable
    ledger-row columns, and ``as_of``-gated evidence classifications.

No constructor here reads current catalog, provider, Vault, analytics,
telemetry, or message state. There is therefore no current-state reader to
leak post-decision information: cutoff safety holds by construction, not by
query discipline. Live current-state readers (``FanCommercialState``,
``OfferHistory`` without cutoff, ``OfferDefinition`` resolver) must NEVER be
substituted as inputs to these builders.

Authority boundary (Model A — advisory scores only):

    deterministic v1 ranking (authoritative)
        → OptimizationInput (this module, observational)
        → future advisory optimizer (not implemented)
        → AdvisoryOptimizationResult / abstain (typed here, never produced here)
        → deterministic validator (boundary typed here, implemented later)
        → existing sealing/execution (unchanged)

The deterministic validator must eventually re-check, in order: creator
scope, eligibility, ownership, candidate definition/version identity,
single-Drop rule, pressure/fatigue/cooldown governance, provider truth, and
sealing. See :func:`validation_checklist`. Abstention always means "use
existing deterministic v1 behavior", never "no sale".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Small pure helpers (no I/O, no clock reads except explicit now() default).
# ---------------------------------------------------------------------------


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _coerce_aware(value: Any) -> datetime | None:
    """Coerce timestamps to tz-aware UTC (provider-skew rule, as in ledger)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(i for i in (str(v) for v in value if isinstance(v, str) and v.strip()) if i)


def _frozen_str_set(value: Any) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        return frozenset()
    try:
        items = list(value or ())
    except Exception:
        return frozenset()
    return frozenset(i.strip() for i in items if isinstance(i, str) and i.strip())


# ---------------------------------------------------------------------------
# Frozen contract types (all immutable; tuples/frozensets only, no ORM).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenCandidate:
    """One eligible candidate frozen at decision time (advisory comparison only).

    Projected from the frozen decision snapshot — never re-resolved against
    the current catalog. Identity is creator × definition_id × version;
    ``stable_key`` alone is never sufficient and Drop IDs are carried only as
    frozen provider mappings (the optimizer must never choose among them).
    Purchased price is outcome evidence and is never a candidate feature.
    """

    definition_id: int
    version: int
    stable_key: str
    offer_type: str | None = None
    price_minor: int | None = None
    currency: str | None = None
    family_id: int | None = None
    canonical_vault_ids: tuple[str, ...] = ()
    mapped_drop_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_scope("definition_id", self.definition_id)
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("version is required")
        _require_text("stable_key", self.stable_key)
        if self.price_minor is not None and (
            isinstance(self.price_minor, bool) or not isinstance(self.price_minor, int)
        ):
            raise ValueError("price_minor must be an integer or None")
        if self.family_id is not None and (
            not isinstance(self.family_id, int) or isinstance(self.family_id, bool)
        ):
            raise ValueError("family_id must be an integer or None")

    def identity(self) -> tuple[int, int]:
        """Immutable commercial identity: (definition_id, version)."""
        return (int(self.definition_id), int(self.version))


@dataclass(frozen=True)
class OwnershipContext:
    """Ownership as decision-time context (never a decision).

    Ownership remains hard authority outside the optimizer; the future
    validator must re-run authoritative ownership checks regardless of this
    context. ``source`` records whether the set came from the live engine
    result or the frozen snapshot fan facts — both decision-time frozen.
    """

    creator_id: int
    user_id: int
    owned_vault_ids: frozenset[str] = frozenset()
    source: str = "snapshot"

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("user_id", self.user_id)
        if self.source not in ("engine", "snapshot"):
            raise ValueError("source must be 'engine' or 'snapshot'")


@dataclass(frozen=True)
class FanCommercialSummary:
    """Cutoff-safe fan commercial facts projected from the frozen snapshot.

    Every value was frozen at ``evaluated_at`` by the decision snapshot, so
    no purchase/offer after the decision can appear here. No financial
    capacity is inferred; no sensitive attributes are carried.
    """

    creator_id: int
    user_id: int
    purchase_count: int = 0
    total_spend_minor: int = 0
    average_order_value_minor: int | None = None
    highest_purchase_minor: int | None = None
    last_purchase_at: str | None = None
    recent_purchase_count: int = 0
    recent_spend_minor: int = 0
    owned_vault_ids: frozenset[str] = frozenset()
    delivered_vault_ids: tuple[str, ...] = ()
    recent_offer_count: int = 0
    recent_rejected_offer_count: int = 0
    last_offer_at: str | None = None
    recent_offered_vault_ids: tuple[str, ...] = ()
    currency: str | None = None

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("user_id", self.user_id)


@dataclass(frozen=True)
class OfferHistorySummary:
    """Sanitized offer-history facts projected from the frozen snapshot.

    Counts/sets only — no raw rows, messages, PII, or transaction IDs.
    ``definition_identity_available`` is preserved as False where the source
    cannot establish it; unavailable identity is never fabricated.
    """

    creator_id: int
    user_id: int
    total_offer_count: int = 0
    recent_offer_count: int = 0
    last_offer_at: str | None = None
    declined_offer_count: int = 0
    recent_declined_offer_count: int = 0
    state_counts: tuple[tuple[str, int], ...] = ()
    has_active_offer: bool = False
    active_offer_count: int = 0
    offered_vault_sets: tuple[tuple[str, ...], ...] = ()
    active_vault_sets: tuple[tuple[str, ...], ...] = ()
    null_snapshot_count: int = 0
    definition_identity_available: bool = False

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("user_id", self.user_id)


@dataclass(frozen=True)
class ConversationContext:
    """Deterministic conversation subset frozen at decision time.

    Only the four ranking-admissible fields. No LLM floats, prose, scores,
    counters, or post-decision turns.
    """

    lifecycle: str | None = None
    current_topic: str | None = None
    recent_topics: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceContext:
    """Outcome-side evidence context with explicit cutoff semantics.

    Carries the ``as_of``-gated classification only — never future outcomes
    as features, never transaction IDs, never purchased price (price-learning
    labels come from the evidence API directly, not from this input).
    Censored/unavailable evidence is preserved as such, never as negative.
    """

    exposure_state: str = "UNAVAILABLE"
    exposure_at: str | None = None
    label: str = "UNAVAILABLE"
    maturity_state: str = "IMMATURE"
    maturity_policy_version: str | None = None
    evidence_quality: str = "UNAVAILABLE"
    attribution_status: str | None = None
    recovered: bool = False
    evidence_as_of: str | None = None

    def __post_init__(self) -> None:
        if self.exposure_state not in (
            "UNAVAILABLE",
            "DECISION",
            "SEALED",
            "SEND_ATTEMPTED",
            "SENT",
        ):
            raise ValueError("unknown exposure_state")
        if self.label not in (
            "POSITIVE",
            "COMMERCIAL_NEGATIVE",
            "PROCESS_NEGATIVE",
            "CENSORED",
            "NO_OPPORTUNITY",
            "NO_SELECTION",
            "UNAVAILABLE",
        ):
            raise ValueError("unknown label")
        if self.maturity_state not in ("IMMATURE", "MATURE"):
            raise ValueError("unknown maturity_state")
        if self.evidence_quality not in ("FULL", "PARTIAL", "UNATTRIBUTED", "UNAVAILABLE"):
            raise ValueError("unknown evidence_quality")


@dataclass(frozen=True)
class ReengagementContext:
    """Parent/child linkage for exposure-vs-revenue accounting.

    Touches are individually countable exposures; revenue stays single
    (see ``purchase_winner_opportunity_id`` / ``revenue_events`` mirroring
    single-winner attribution). Unknown sibling state is None, never zero-
    filled as fact.
    """

    opportunity_id: int
    reengagement_of: int | None = None
    is_child: bool = False
    sibling_touch_count: int | None = None
    purchase_winner_opportunity_id: int | None = None
    revenue_events: int | None = None

    def __post_init__(self) -> None:
        _require_scope("opportunity_id", self.opportunity_id)


@dataclass(frozen=True)
class OptimizationInput:
    """Sanitized, point-in-time, immutable advisory input (no authority).

    Constructed ONLY via :func:`build_optimization_input` from frozen
    decision data. Carries no ORM objects, no mutable records, no current
    catalog/provider/analytics state, no transaction IDs, no purchased
    prices, no raw messages. ``experiment_id``/``variant_id`` are reserved
    nullable metadata for future reconstructability; this phase never assigns
    them (the builder accepts no such parameters).
    """

    creator_id: int
    opportunity_id: int
    user_id: int
    evaluated_at: datetime
    policy_version: str | None
    frozen_candidates: tuple[FrozenCandidate, ...] = ()
    selected_definition_id: int | None = None
    selected_definition_version: int | None = None
    ownership_context: OwnershipContext | None = None
    fan_commercial_summary: FanCommercialSummary | None = None
    offer_history_summary: OfferHistorySummary | None = None
    conversation_context: ConversationContext | None = None
    evidence_context: EvidenceContext | None = None
    reengagement_context: ReengagementContext | None = None
    experiment_id: str | None = None
    variant_id: str | None = None

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("opportunity_id", self.opportunity_id)
        _require_scope("user_id", self.user_id)
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be a timezone-aware datetime")
        if self.policy_version is not None:
            _require_text("policy_version", self.policy_version)


@dataclass(frozen=True)
class AdvisoryOptimizationResult:
    """Future advisory output type (typed here, never produced here).

    Scores reference eligible candidates by immutable (definition_id,
    version) identity only. The type cannot carry price, currency, Vault or
    Drop IDs, URLs, eligibility/ownership verdicts, creator selection,
    sealing/send commands, or novel candidates — such authority is
    structurally inexpressible. Abstention means "use deterministic v1
    behavior", never "no sale"; an abstaining result carries no scores.
    """

    creator_id: int
    opportunity_id: int
    candidate_scores: tuple[tuple[int, int, float], ...] = ()
    abstain: bool = False
    optimizer_version: str = "unassigned"
    input_policy_version: str | None = None

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("opportunity_id", self.opportunity_id)
        _require_text("optimizer_version", self.optimizer_version)
        for ref in self.candidate_scores:
            try:
                did, ver, score = ref
            except Exception:
                raise ValueError("candidate_scores entries must be (definition_id, version, score)")
            _require_scope("candidate definition_id", did)
            if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
                raise ValueError("candidate version is required")
            if not isinstance(score, float) or not math.isfinite(score):
                raise ValueError("candidate score must be a finite float")
        if self.abstain and self.candidate_scores:
            raise ValueError("an abstaining result must not carry scores")

    def referenced_identities(self) -> frozenset[tuple[int, int]]:
        """Immutable candidate identities referenced by this advice."""
        return frozenset((int(did), int(ver)) for did, ver, _ in self.candidate_scores)


def abstain_result(
    *,
    creator_id: int,
    opportunity_id: int,
    optimizer_version: str = "unassigned",
    input_policy_version: str | None = None,
) -> AdvisoryOptimizationResult:
    """Build an abstaining advisory result (v1 behavior preserved)."""
    return AdvisoryOptimizationResult(
        creator_id=creator_id,
        opportunity_id=opportunity_id,
        candidate_scores=(),
        abstain=True,
        optimizer_version=optimizer_version,
        input_policy_version=input_policy_version,
    )


# ---------------------------------------------------------------------------
# Future validator boundary (typed, not implemented; reuses existing checks).
# ---------------------------------------------------------------------------

#: Ordered validator checks for the future advisory path. Each entry names an
#: existing authoritative check to reuse — no new authority is introduced.
VALIDATOR_CHECKS: tuple[tuple[str, str], ...] = (
    ("creator_scope", "creator_id/user_id/opportunity scope match (ledger identity)"),
    ("eligibility", "commerce.opportunity_eligibility hard eligibility re-check"),
    ("ownership", "commerce.ownership authoritative ownership re-check"),
    ("candidate_identity", "db.offer_definitions pinned definition_id/version re-check"),
    ("single_drop_rule", "exactly-one mapped Drop provider representation rule"),
    ("governance", "pressure/fatigue/cooldown governance re-check"),
    ("provider_truth", "live sealing verification before any offer exists"),
)


def validation_checklist() -> tuple[tuple[str, str], ...]:
    """Return the ordered future-validator check boundary (pure data)."""
    return VALIDATOR_CHECKS


# ---------------------------------------------------------------------------
# Pure builders (snapshot projection only — no reads of any kind).
# ---------------------------------------------------------------------------


def build_frozen_candidate(entry: Any) -> FrozenCandidate:
    """Project one snapshot eligible entry to a frozen candidate (pure).

    Fails closed when commercial identity is incomplete: an optimizer must
    never advise on an unidentified candidate. ``family_id`` passes through
    only when the snapshot carries it (older snapshots omit it) — it is
    never backfilled from the live catalog.
    """
    if not isinstance(entry, dict):
        raise ValueError("candidate entry must be a mapping")
    try:
        definition_id = entry.get("definition_id")
        version = entry.get("version")
        if isinstance(definition_id, bool) or not isinstance(definition_id, int):
            raise ValueError("candidate definition_id is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("candidate version is required")
        stable_key = entry.get("stable_key")
        if not isinstance(stable_key, str) or not stable_key.strip():
            raise ValueError("candidate stable_key is required")
    except ValueError:
        raise
    except Exception:
        raise ValueError("candidate entry is invalid")
    price = entry.get("price_minor")
    if isinstance(price, bool) or (price is not None and not isinstance(price, int)):
        raise ValueError("candidate price_minor must be an integer or None")
    family_id = entry.get("family_id")
    if family_id is not None and (not isinstance(family_id, int) or isinstance(family_id, bool)):
        raise ValueError("candidate family_id must be an integer or None")
    vault_ids = entry.get("vault_ids")
    if vault_ids is None:
        # Older snapshots may use the engine field name; accept without rewrite.
        vault_ids = entry.get("canonical_vault_item_ids")
    raw_drops = entry.get("mapped_drop_ids")
    drop_seq = (
        list(raw_drops)
        if isinstance(raw_drops, (list, tuple, set, frozenset))
        else []
    )
    return FrozenCandidate(
        definition_id=int(definition_id),
        version=int(version),
        stable_key=stable_key.strip(),
        offer_type=str(entry.get("offer_type")).strip() if entry.get("offer_type") else None,
        price_minor=price,
        currency=str(entry.get("currency")).strip() if entry.get("currency") else None,
        family_id=family_id,
        canonical_vault_ids=tuple(
            v
            for v in (list(vault_ids) if isinstance(vault_ids, (list, tuple)) else [])
            if isinstance(v, str)
        ),
        mapped_drop_ids=tuple(sorted({d for d in drop_seq if isinstance(d, str)})),
    )


def build_fan_summary(fan: Any, *, creator_id: int, user_id: int) -> FanCommercialSummary:
    """Project frozen snapshot fan facts to a cutoff-safe summary (pure).

    Cross-checks embedded creator/user scope when present. Missing keys fall
    back to neutral defaults — absence is never filled from live readers.
    """
    creator_id = _require_scope("creator_id", creator_id)
    user_id = _require_scope("user_id", user_id)
    fan = fan if isinstance(fan, dict) else {}
    for key, expected in (("creator_id", creator_id), ("user_id", user_id)):
        actual = fan.get(key)
        if actual is None:
            continue
        try:
            matches = int(actual) == int(expected)
        except Exception:
            raise ValueError(f"fan {key} scope mismatch")
        if not matches:
            raise ValueError(f"fan {key} scope mismatch")

    def _int(key: str, default: int = 0) -> int:
        value = fan.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    def _opt_int(key: str) -> int | None:
        value = fan.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _iso(key: str) -> str | None:
        value = fan.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, datetime):
            coerced = _coerce_aware(value)
            return coerced.isoformat() if coerced else None
        return None

    return FanCommercialSummary(
        creator_id=creator_id,
        user_id=user_id,
        purchase_count=_int("purchase_count"),
        total_spend_minor=_int("total_spend_minor"),
        average_order_value_minor=_opt_int("average_order_value_minor"),
        highest_purchase_minor=_opt_int("highest_purchase_minor"),
        last_purchase_at=_iso("last_purchase_at"),
        recent_purchase_count=_int("recent_purchase_count"),
        recent_spend_minor=_int("recent_spend_minor"),
        owned_vault_ids=_frozen_str_set(fan.get("purchased_vault_ids")),
        delivered_vault_ids=tuple(_str_list(fan.get("delivered_vault_ids"))),
        recent_offer_count=_int("recent_offer_count"),
        recent_rejected_offer_count=_int("recent_rejected_offer_count"),
        last_offer_at=_iso("last_offer_at"),
        recent_offered_vault_ids=tuple(_str_list(fan.get("recent_offered_vault_ids"))),
        currency=str(fan.get("currency")).strip() if fan.get("currency") else None,
    )


def build_history_summary(history: Any, *, creator_id: int, user_id: int) -> OfferHistorySummary:
    """Project frozen snapshot history to a sanitized summary (pure).

    Preserves ``definition_identity_available = False`` where the source
    cannot establish it. State counts and vault sets are frozen as sorted
    tuples; no transaction IDs exist in the source and none are added.
    """
    creator_id = _require_scope("creator_id", creator_id)
    user_id = _require_scope("user_id", user_id)
    history = history if isinstance(history, dict) else {}
    for key, expected in (("creator_id", creator_id), ("user_id", user_id)):
        actual = history.get(key)
        if actual is None:
            continue
        try:
            matches = int(actual) == int(expected)
        except Exception:
            raise ValueError(f"history {key} scope mismatch")
        if not matches:
            raise ValueError(f"history {key} scope mismatch")

    def _int(key: str, default: int = 0) -> int:
        value = history.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    def _iso(key: str) -> str | None:
        value = history.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, datetime):
            coerced = _coerce_aware(value)
            return coerced.isoformat() if coerced else None
        return None

    raw_counts = history.get("state_counts")
    state_counts: tuple[tuple[str, int], ...] = ()
    if isinstance(raw_counts, dict):
        pairs = [
            (str(k), v)
            for k, v in raw_counts.items()
            if isinstance(v, int) and not isinstance(v, bool)
        ]
        state_counts = tuple(sorted(pairs, key=lambda p: p[0]))

    def _vault_sets(key: str) -> tuple[tuple[str, ...], ...]:
        raw = history.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        out: list[tuple[str, ...]] = []
        for entry in raw:
            if isinstance(entry, (list, tuple)):
                out.append(tuple(v for v in entry if isinstance(v, str)))
        return tuple(out)

    return OfferHistorySummary(
        creator_id=creator_id,
        user_id=user_id,
        total_offer_count=_int("total_offer_count"),
        recent_offer_count=_int("recent_offer_count"),
        last_offer_at=_iso("last_offer_at"),
        declined_offer_count=_int("declined_offer_count"),
        recent_declined_offer_count=_int("recent_declined_offer_count"),
        state_counts=state_counts,
        has_active_offer=bool(history.get("has_active_offer", False)),
        active_offer_count=_int("active_offer_count"),
        offered_vault_sets=_vault_sets("offered_vault_sets"),
        active_vault_sets=_vault_sets("active_vault_sets"),
        null_snapshot_count=_int("null_snapshot_count"),
        definition_identity_available=bool(history.get("definition_identity_available", False)),
    )


def build_conversation_context(conversation: Any) -> ConversationContext:
    """Project the four ranking-admissible conversation fields (pure)."""
    conversation = conversation if isinstance(conversation, dict) else {}
    lifecycle = conversation.get("lifecycle")
    current_topic = conversation.get("current_topic")
    return ConversationContext(
        lifecycle=str(lifecycle).strip() if isinstance(lifecycle, str) and lifecycle.strip() else None,
        current_topic=str(current_topic).strip() if isinstance(current_topic, str) and current_topic.strip() else None,
        recent_topics=tuple(_str_list(conversation.get("recent_topics"))),
        open_threads=tuple(_str_list(conversation.get("open_threads"))),
    )


def _iso_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        coerced = _coerce_aware(value)
        return coerced.isoformat() if coerced else None
    return None


def build_evidence_context(
    evidence: Any, *, creator_id: int, opportunity_id: int
) -> EvidenceContext:
    """Project an evidence classification to outcome-side context (pure).

    ``None`` evidence yields an explicit UNAVAILABLE context (never a
    negative). Creator/opportunity mismatches raise — cross-creator evidence
    must never enter an input. Transaction IDs and purchased prices are
    deliberately dropped (data minimization; price-learning labels come from
    the evidence API, not this input).
    """
    creator_id = _require_scope("creator_id", creator_id)
    opportunity_id = _require_scope("opportunity_id", opportunity_id)
    if evidence is None:
        return EvidenceContext()
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be a mapping or None")
    for key, expected in (("creator_id", creator_id), ("opportunity_id", opportunity_id)):
        actual = evidence.get(key)
        if actual is not None:
            try:
                if int(actual) != int(expected):
                    raise ValueError(f"evidence {key} scope mismatch")
            except ValueError:
                raise
            except Exception:
                raise ValueError(f"evidence {key} scope mismatch")
    return EvidenceContext(
        exposure_state=str(evidence.get("exposure_state") or "UNAVAILABLE"),
        exposure_at=_iso_or_none(evidence.get("exposure_at")),
        label=str(evidence.get("label") or "UNAVAILABLE"),
        maturity_state=str(evidence.get("maturity_state") or "IMMATURE"),
        maturity_policy_version=evidence.get("maturity_policy_version"),
        evidence_quality=str(evidence.get("evidence_quality") or "UNAVAILABLE"),
        attribution_status=evidence.get("attribution_status"),
        recovered=bool(evidence.get("recovered", False)),
        evidence_as_of=_iso_or_none(evidence.get("label_as_of")),
    )


def build_reengagement_context(
    ledger_row: Any,
    offer_exposures: Any = None,
    *,
    opportunity_id: int,
) -> ReengagementContext:
    """Project parent/child linkage for exposure-vs-revenue accounting (pure).

    Unknown sibling state stays None (never zero-filled as fact). Revenue
    semantics mirror single-winner attribution via the exposures listing.
    """
    opportunity_id = _require_scope("opportunity_id", opportunity_id)
    parent = _get(ledger_row, "reengagement_of")
    if parent is not None and (not isinstance(parent, int) or isinstance(parent, bool) or parent <= 0):
        raise ValueError("reengagement_of must be an opportunity id or None")
    if offer_exposures is None:
        return ReengagementContext(
            opportunity_id=opportunity_id,
            reengagement_of=parent,
            is_child=parent is not None,
        )
    if not isinstance(offer_exposures, dict):
        raise ValueError("offer_exposures must be a mapping or None")
    touches = offer_exposures.get("exposures")
    sibling_touch_count: int | None = None
    if isinstance(touches, (list, tuple)):
        sibling_touch_count = max(0, len(touches) - 1)
    winner = offer_exposures.get("purchase_winner_opportunity_id")
    if winner is not None and (not isinstance(winner, int) or isinstance(winner, bool) or winner <= 0):
        raise ValueError("purchase_winner_opportunity_id is invalid")
    revenue = offer_exposures.get("revenue_events")
    if revenue is not None and revenue not in (0, 1):
        raise ValueError("revenue_events must be 0, 1, or None")
    return ReengagementContext(
        opportunity_id=opportunity_id,
        reengagement_of=parent,
        is_child=parent is not None,
        sibling_touch_count=sibling_touch_count,
        purchase_winner_opportunity_id=winner,
        revenue_events=revenue,
    )


def _parse_snapshot(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return snapshot
    if isinstance(snapshot, str) and snapshot.strip():
        try:
            parsed = json.loads(snapshot)
        except Exception:
            raise ValueError("decision_snapshot is not valid JSON")
        if not isinstance(parsed, dict):
            raise ValueError("decision_snapshot must decode to a mapping")
        return parsed
    raise ValueError("decision_snapshot is required")


def build_optimization_input(
    *,
    ledger_row: Any,
    snapshot: Any = None,
    evidence: Any = None,
    offer_exposures: Any = None,
) -> OptimizationInput:
    """Build a sanitized advisory input from frozen decision data (pure, no I/O).

    Sources, in order: the immutable ledger-row columns (identity, scope,
    timestamps, selection projection, seal/re-engagement linkage) and the
    frozen ``decision_snapshot`` mapping (candidates, fan, history,
    conversation, ranking policy). Optional ``evidence`` (an
    ``opportunity_evidence`` classification) and ``offer_exposures`` (a
    ``list_offer_exposures`` listing) supply outcome-side context only.

    Fails closed on scope mismatch, missing identity, unidentified
    candidates, or absent snapshot. Never reads catalogs, providers,
    analytics, telemetry, or messages — point-in-time safety holds because
    every feature originates from data frozen at ``evaluated_at``.
    """
    if not isinstance(ledger_row, dict):
        raise ValueError("ledger_row must be a mapping")
    creator_id = _require_scope("creator_id", ledger_row.get("creator_id"))
    opportunity_id = _require_scope("opportunity_id", ledger_row.get("opportunity_id"))
    user_id = _require_scope("user_id", ledger_row.get("user_id"))
    evaluated_at = _coerce_aware(ledger_row.get("evaluated_at"))
    if evaluated_at is None:
        raise ValueError("ledger_row.evaluated_at must be a timezone-aware datetime")
    raw_snapshot = snapshot if snapshot is not None else ledger_row.get("decision_snapshot")
    frozen = _parse_snapshot(raw_snapshot)
    eligible = frozen.get("eligible")
    if not isinstance(eligible, (list, tuple)):
        raise ValueError("snapshot eligible candidates are required")
    candidates = tuple(build_frozen_candidate(e) for e in eligible)
    ranking = frozen.get("ranking") if isinstance(frozen.get("ranking"), dict) else {}
    policy_version = ranking.get("policy_version")
    if policy_version is not None and (not isinstance(policy_version, str) or not policy_version.strip()):
        raise ValueError("snapshot ranking policy_version is invalid")
    selected = frozen.get("selected") if isinstance(frozen.get("selected"), dict) else None
    selected_id = selected.get("definition_id") if selected else None
    selected_version = selected.get("version") if selected else None
    if selected_id is None:
        selected_id = ledger_row.get("selected_definition_id")
    if selected_version is None:
        selected_version = ledger_row.get("selected_version")
    fan_section = frozen.get("fan") if isinstance(frozen.get("fan"), dict) else {}
    history_section = frozen.get("history") if isinstance(frozen.get("history"), dict) else {}
    fan_summary = build_fan_summary(fan_section, creator_id=creator_id, user_id=user_id)
    history_summary = build_history_summary(history_section, creator_id=creator_id, user_id=user_id)
    conversation = build_conversation_context(
        frozen.get("conversation") if isinstance(frozen.get("conversation"), dict) else {}
    )
    ownership = OwnershipContext(
        creator_id=creator_id,
        user_id=user_id,
        owned_vault_ids=fan_summary.owned_vault_ids,
        source="snapshot",
    )
    evidence_context = build_evidence_context(
        evidence, creator_id=creator_id, opportunity_id=opportunity_id
    )
    reengagement = build_reengagement_context(
        ledger_row, offer_exposures, opportunity_id=opportunity_id
    )
    return OptimizationInput(
        creator_id=creator_id,
        opportunity_id=opportunity_id,
        user_id=user_id,
        evaluated_at=evaluated_at,
        policy_version=policy_version.strip() if isinstance(policy_version, str) else None,
        frozen_candidates=candidates,
        selected_definition_id=int(selected_id) if isinstance(selected_id, int) and not isinstance(selected_id, bool) else None,
        selected_definition_version=int(selected_version) if isinstance(selected_version, int) and not isinstance(selected_version, bool) else None,
        ownership_context=ownership,
        fan_commercial_summary=fan_summary,
        offer_history_summary=history_summary,
        conversation_context=conversation,
        evidence_context=evidence_context,
        reengagement_context=reengagement,
    )


#: Fields that must never appear on the advisory output type. Enforced by
#: construction (the dataclass has no such fields) and by test.
FORBIDDEN_OUTPUT_FIELDS = frozenset(
    {
        "price_minor",
        "currency",
        "vault_ids",
        "drop_ids",
        "checkout_url",
        "eligibility",
        "ownership",
        "seal",
        "send",
        "transaction_id",
    }
)


__all__ = [
    "FrozenCandidate",
    "OwnershipContext",
    "FanCommercialSummary",
    "OfferHistorySummary",
    "ConversationContext",
    "EvidenceContext",
    "ReengagementContext",
    "OptimizationInput",
    "AdvisoryOptimizationResult",
    "abstain_result",
    "VALIDATOR_CHECKS",
    "validation_checklist",
    "FORBIDDEN_OUTPUT_FIELDS",
    "build_optimization_input",
    "build_frozen_candidate",
    "build_fan_summary",
    "build_history_summary",
    "build_conversation_context",
    "build_evidence_context",
    "build_reengagement_context",
]
