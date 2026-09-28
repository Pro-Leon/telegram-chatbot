"""Canonical relationship trajectory — Phase 1 deterministic domain (AUDIT → DESIGN → IMPLEMENT).

Describes the *human* relationship trajectory between one creator and one fan
(``creator_id + user_id`` grain). It is conceptually separate from:

* ``commerce.relationship.RelationshipState`` — a COMMERCIAL lifecycle
  classifier (funnel + purchase + recency). That module is frozen and is
  never imported, renamed, redefined, or extended here.
* purchase desire (``commerce/desire.py``), commercial temperature
  (``commerce/temperature.py``), readiness/warming/sales-window, response
  modes, conversation objectives, tone, and LLM observations.

What this domain means:

* ``familiarity`` — accumulated interaction history and continuity. It does
  NOT mean affection, attraction, trust, or permission.
* ``engagement`` — behavioral participation depth over multiple
  interactions (message volume, questions, shared information, topic
  continuation). It is NOT psychological interpretation and NOT the LLM
  ``relationship_engagement`` float (which is never persisted here).
* ``reciprocity`` — balance of conversational participation between fan and
  creator-side turns. It does NOT mean emotional commitment or attachment.
* ``continuity`` — whether interaction has persistent conversational
  anchors (returns, open loops, prior-context references, recurring topics).
* ``trend`` — recent movement direction of the above evidence
  (declining/stable/growing). A single turn never creates a durable
  positive trend.

What this domain does NOT contain (deferred to later phases with separate
safety/boundary design): sexual intensity/tension, intimacy scores,
consent/permission/boundary state, escalation state, or any commerce
authority (products, prices, eligibility, windows, offers).

Design rules enforced by this module:

* Pure deterministic derivation: no network, no LLM, no DB/Redis access,
  no randomness, no credentials, no commerce execution/price/offer logic.
* Durable state holds counters/timestamps/bands/provenance only — never
  raw message text or sensitive conversational content.
* Provenance vocabulary mirrors ``commerce/long_term_memory.py``
  (``EXPLICIT 1.0 / SYSTEM_EVENT 0.9 / STRONG_INFERENCE 0.8 /
  WEAK_INFERENCE 0.5``) so no second incompatible confidence language is
  introduced. Local constants are defined here (rather than imported) to
  keep this module free of commerce imports.
* A single LLM observation can never promote durable state; durable
  promotion requires repeated behavioral evidence. Unknown/neutral is
  preferred over fabricated conclusions (cold start).
* Persistence reuses the existing ``user_profiles.facts`` JSONB
  ``*_by_creator`` namespace pattern with atomic mutation; failures are
  fail-open and never block message processing.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("commerce.relationship_trajectory")

# ---------------------------------------------------------------------------
# Namespace / versioning (persistence contract)
# ---------------------------------------------------------------------------

#: Creator-namespaced JSONB key inside ``user_profiles.facts``. Follows the
#: established ``*_by_creator`` pattern (cf. ``long_term_memory_by_creator``,
#: ``fan_knowledge_by_creator``, ``commercial_preferences_by_creator``).
RELATIONSHIP_TRAJECTORY_KEY = "relationship_trajectory_by_creator"

#: Schema version stamped on every persisted anchor block. Readers tolerate
#: unknown future fields (see ``anchors_from_dict``); writers always stamp
#: the current version.
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Provenance vocabulary (mirrors commerce/long_term_memory.py values)
# ---------------------------------------------------------------------------

#: Fan explicitly stated / directly observed fact.
PROVENANCE_EXPLICIT = 1.0
#: Deterministic system event (e.g. session return derived from counters).
PROVENANCE_SYSTEM_EVENT = 0.9
#: Strong deterministic inference from repeated behavioral evidence.
PROVENANCE_STRONG_INFERENCE = 0.8
#: Weak single-observation inference; never promotes durable bands alone.
PROVENANCE_WEAK_INFERENCE = 0.5

# ---------------------------------------------------------------------------
# Bands (small closed vocabularies; string values are part of the contract)
# ---------------------------------------------------------------------------


class FamiliarityBand(str, enum.Enum):
    """Accumulated interaction history. NOT affection/trust/permission."""

    UNKNOWN = "unknown"  # insufficient history accumulated
    NEW = "new"  # first contact(s), no established continuity yet
    FAMILIAR = "familiar"  # repeated interaction with some span/returns
    ESTABLISHED = "established"  # sustained interaction over time + returns


class EngagementBand(str, enum.Enum):
    """Behavioral participation depth over multiple interactions."""

    UNKNOWN = "unknown"  # no interactions observed
    LOW = "low"  # sparse fan participation
    STEADY = "steady"  # regular fan participation (messages + signals)
    DEEP = "deep"  # sustained rich fan participation


class ReciprocityBand(str, enum.Enum):
    """Balance of fan vs creator-side conversational participation."""

    UNKNOWN = "unknown"  # no exchanges observed
    LOW = "low"  # creator-side turns carry the conversation
    BALANCED = "balanced"  # mutual participation
    HIGH = "high"  # fan drives the conversation


class ContinuityBand(str, enum.Enum):
    """Presence of persistent conversational anchors."""

    UNKNOWN = "unknown"  # no interactions observed
    SPARSE = "sparse"  # interaction without durable anchors
    ANCHORED = "anchored"  # at least one anchor kind present
    RICH = "rich"  # multiple anchor kinds / repeated returns


class TrendDirection(str, enum.Enum):
    """Recent movement direction of relationship-relevant evidence."""

    UNKNOWN = "unknown"  # insufficient history
    DECLINING = "declining"  # dormancy or disengagement pattern
    STABLE = "stable"  # no significant recent movement
    GROWING = "growing"  # corroborated positive streak (never single-turn)


# Band orders used for single-step promotion clamps and decay downgrades.
_FAMILIARITY_ORDER = (
    FamiliarityBand.UNKNOWN,
    FamiliarityBand.NEW,
    FamiliarityBand.FAMILIAR,
    FamiliarityBand.ESTABLISHED,
)
_ENGAGEMENT_ORDER = (
    EngagementBand.UNKNOWN,
    EngagementBand.LOW,
    EngagementBand.STEADY,
    EngagementBand.DEEP,
)
_RECIPROCITY_ORDER = (
    ReciprocityBand.UNKNOWN,
    ReciprocityBand.LOW,
    ReciprocityBand.BALANCED,
    ReciprocityBand.HIGH,
)
_CONTINUITY_ORDER = (
    ContinuityBand.UNKNOWN,
    ContinuityBand.SPARSE,
    ContinuityBand.ANCHORED,
    ContinuityBand.RICH,
)

# ---------------------------------------------------------------------------
# Provisional thresholds (explicit constants, conservative defaults)
# ---------------------------------------------------------------------------
# These are documented as provisional: they are chosen to require repeated
# evidence (never single-turn promotion beyond the entry level) and to be
# deterministic/testable. They do NOT reuse commercial desire-decay
# semantics. Tune only with a dedicated calibration review.

FAMILIAR_MIN_INTERACTIONS = 5
FAMILIAR_MIN_USER_MESSAGES = 3
ESTABLISHED_MIN_INTERACTIONS = 20
ESTABLISHED_MIN_RETURNS = 3
ESTABLISHED_MIN_SPAN_DAYS = 7.0

ENGAGEMENT_STEADY_MIN_USER_MESSAGES = 5
ENGAGEMENT_STEADY_MIN_SIGNALS = 3
ENGAGEMENT_DEEP_MIN_USER_MESSAGES = 15
ENGAGEMENT_DEEP_MIN_SHARED = 5
ENGAGEMENT_DEEP_MIN_CONTINUATIONS = 3

RECIPROCITY_LOW_MAX_SHARE = 0.25
RECIPROCITY_HIGH_MIN_SHARE = 0.75
RECIPROCITY_HIGH_MIN_USER_MESSAGES = 5

CONTINUITY_RICH_MIN_RETURNS = 3

#: Dormancy horizon after which the *readout* decays one step. Historical
#: anchors are never destroyed by decay (see ``derive_relationship_snapshot``).
DECAY_DORMANT_DAYS = 30.0

#: LLM engagement value at/above which an observation counts as "high" for
#: corroboration purposes. Corroboration only increments an informational
#: counter; it never promotes bands (see ``accumulate_turn``).
LLM_ENGAGEMENT_HIGH = 0.8

#: Positive streak length required before trend may read GROWING. Each
#: streak step requires >=2 behavioral signals in one turn, so GROWING
#: always reflects at least three corroborated turns — never one message.
TREND_GROWING_MIN_STREAK = 3

#: Behavioral signals per turn counting toward the positive streak.
POSITIVE_SIGNALS_PER_TURN = 2

# Fixed vocabulary for transition reasons (bounded; no raw text stored).
_EVIDENCE_FIELD_NAMES = (
    "user_sent_message",
    "user_asked_question",
    "user_answered_question",
    "user_shared_information",
    "user_continued_topic",
    "user_referenced_previous_context",
    "assistant_asked_question",
    "assistant_shared_information",
    "session_returned",
    "open_loop_continued",
    "open_loop_resolved",
)

# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationshipTurnEvidence:
    """Narrow typed input: behavioral observations for exactly one turn.

    Every boolean must be derivable from existing deterministic sources
    (message roles, question detection, topic/thread trackers, open-loop
    memory, session counters). ``llm_relationship_engagement`` is the only
    LLM-derived field: optional advisory input, bounded informational use
    only (corroboration counter), never band promotion.
    """

    user_sent_message: bool = False
    user_asked_question: bool = False
    user_answered_question: bool = False
    user_shared_information: bool = False
    user_continued_topic: bool = False
    user_referenced_previous_context: bool = False
    assistant_asked_question: bool = False
    assistant_shared_information: bool = False
    session_returned: bool = False
    open_loop_continued: bool = False
    open_loop_resolved: bool = False
    llm_relationship_engagement: float | None = None

    def positive_behavioral_signals(self) -> int:
        """Count behavioral (non-LLM) positive signals in this turn."""
        return sum(
            1
            for name in _EVIDENCE_FIELD_NAMES
            if name != "assistant_asked_question"
            and name != "assistant_shared_information"
            and getattr(self, name, False) is True
        )

    def substantive_behavioral_signals(self) -> int:
        """Behavioral signals excluding bare contact (``user_sent_message``).

        Used for LLM-corroboration gating: a high LLM engagement value only
        corroborates when the same turn carries substantive deterministic
        evidence (question, answer, disclosure, continuation, reference,
        return, or open-loop activity) — never on mere message presence.
        """
        return sum(
            1
            for name in _EVIDENCE_FIELD_NAMES
            if name
            not in (
                "user_sent_message",
                "assistant_asked_question",
                "assistant_shared_information",
            )
            and getattr(self, name, False) is True
        )

    def reason_token(self) -> str:
        """Deterministic bounded reason token for transitions (no raw text)."""
        active = sorted(n for n in _EVIDENCE_FIELD_NAMES if getattr(self, n, False) is True)
        return "accumulated:" + "+".join(active) if active else "accumulated:turn"


@dataclass(frozen=True)
class RelationshipAnchors:
    """Durable per-(creator, fan) evidence. Counters/timestamps/bands only."""

    schema_version: int = SCHEMA_VERSION
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    interaction_count: int = 0
    user_message_count: int = 0
    user_question_count: int = 0
    user_answer_count: int = 0
    user_shared_info_count: int = 0
    user_topic_continuation_count: int = 0
    user_context_reference_count: int = 0
    assistant_question_count: int = 0
    assistant_shared_info_count: int = 0
    session_return_count: int = 0
    open_loop_observed_count: int = 0
    open_loop_resolved_count: int = 0
    corroborated_engagement_observations: int = 0
    positive_streak: int = 0
    observation_count: int = 0
    last_provenance: float = PROVENANCE_SYSTEM_EVENT
    last_source: str = "cold_start"
    # Committed band levels ({band_name: value}). Bands advance at most one
    # step per accumulation, so no single turn can jump multiple levels.
    # Unknown/missing entries read as UNKNOWN (forward compatible).
    bands: dict[str, str] = field(default_factory=dict)
    # Last transition per band: {band_name: {from, to, at, reason}}. Bounded
    # to the five known bands; fixed vocabulary values only.
    transitions: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationshipBands:
    """Derived band view (raw, before dormancy-decayed readout)."""

    familiarity: FamiliarityBand = FamiliarityBand.UNKNOWN
    engagement: EngagementBand = EngagementBand.UNKNOWN
    reciprocity: ReciprocityBand = ReciprocityBand.UNKNOWN
    continuity: ContinuityBand = ContinuityBand.UNKNOWN
    trend: TrendDirection = TrendDirection.UNKNOWN


@dataclass(frozen=True)
class RelationshipSnapshot:
    """Deterministic per-turn derived view over durable anchors.

    Bands reflect dormancy decay for *readout* purposes; anchors are
    untouched by decay. ``active_signals_this_turn`` is informational only
    (validates turn evidence without promoting durable state).
    """

    familiarity: FamiliarityBand
    engagement: EngagementBand
    reciprocity: ReciprocityBand
    continuity: ContinuityBand
    trend: TrendDirection
    days_since_last_seen: float | None = None
    decay_applied: bool = False
    active_signals_this_turn: int = 0
    computed_at: str | None = None


# ---------------------------------------------------------------------------
# Time helpers (pure)
# ---------------------------------------------------------------------------


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


def neutral_anchors(now: datetime | None = None) -> RelationshipAnchors:
    """Explicit neutral cold start: insufficient history accumulated.

    Cold start means "this subsystem has no relationship history yet" — it
    does NOT mean rejection, low attraction/interest, poor relationship,
    permission denied, or commercial coldness. No backfill is performed.
    """
    _ = _coerce_now(now)
    return RelationshipAnchors()


def neutral_snapshot(now: datetime | None = None) -> RelationshipSnapshot:
    """Neutral derived view over absent anchors (all UNKNOWN, no decay)."""
    return RelationshipSnapshot(
        familiarity=FamiliarityBand.UNKNOWN,
        engagement=EngagementBand.UNKNOWN,
        reciprocity=ReciprocityBand.UNKNOWN,
        continuity=ContinuityBand.UNKNOWN,
        trend=TrendDirection.UNKNOWN,
        days_since_last_seen=None,
        decay_applied=False,
        active_signals_this_turn=0,
        computed_at=_iso(_coerce_now(now)),
    )


# ---------------------------------------------------------------------------
# Raw band derivation (pure; operates on counters only)
# ---------------------------------------------------------------------------


def _span_days(anchors: RelationshipAnchors, now: datetime) -> float | None:
    first = _parse_ts(anchors.first_seen_at)
    last = _parse_ts(anchors.last_seen_at) or now
    if first is None:
        return None
    return max(0.0, (last - first).total_seconds() / 86400.0)


def _derive_familiarity(anchors: RelationshipAnchors, now: datetime) -> FamiliarityBand:
    if anchors.interaction_count <= 0:
        return FamiliarityBand.UNKNOWN
    span = _span_days(anchors, now)
    if (
        anchors.interaction_count >= ESTABLISHED_MIN_INTERACTIONS
        and anchors.session_return_count >= ESTABLISHED_MIN_RETURNS
        and span is not None
        and span >= ESTABLISHED_MIN_SPAN_DAYS
    ):
        return FamiliarityBand.ESTABLISHED
    if (
        anchors.interaction_count >= FAMILIAR_MIN_INTERACTIONS
        and anchors.user_message_count >= FAMILIAR_MIN_USER_MESSAGES
        and (span is not None and span >= 1.0 or anchors.session_return_count >= 1)
    ):
        return FamiliarityBand.FAMILIAR
    return FamiliarityBand.NEW


def _participation_signals(anchors: RelationshipAnchors) -> int:
    return (
        anchors.user_shared_info_count
        + anchors.user_question_count
        + anchors.user_answer_count
        + anchors.user_topic_continuation_count
        + anchors.user_context_reference_count
    )


def _derive_engagement(anchors: RelationshipAnchors) -> EngagementBand:
    if anchors.interaction_count <= 0:
        return EngagementBand.UNKNOWN
    if (
        anchors.user_message_count >= ENGAGEMENT_DEEP_MIN_USER_MESSAGES
        and anchors.user_shared_info_count >= ENGAGEMENT_DEEP_MIN_SHARED
        and anchors.user_topic_continuation_count >= ENGAGEMENT_DEEP_MIN_CONTINUATIONS
    ):
        return EngagementBand.DEEP
    if (
        anchors.user_message_count >= ENGAGEMENT_STEADY_MIN_USER_MESSAGES
        and _participation_signals(anchors) >= ENGAGEMENT_STEADY_MIN_SIGNALS
    ):
        return EngagementBand.STEADY
    return EngagementBand.LOW


def _derive_reciprocity(anchors: RelationshipAnchors) -> ReciprocityBand:
    user_moves = (
        anchors.user_question_count + anchors.user_answer_count + anchors.user_shared_info_count
    )
    assistant_moves = anchors.assistant_question_count + anchors.assistant_shared_info_count
    total = user_moves + assistant_moves
    if anchors.interaction_count <= 0 or total <= 0:
        return ReciprocityBand.UNKNOWN
    if anchors.user_message_count <= 0:
        # Assistant-only turns: the creator side carries the conversation.
        return ReciprocityBand.LOW
    share = user_moves / total
    if share < RECIPROCITY_LOW_MAX_SHARE:
        return ReciprocityBand.LOW
    if (
        share > RECIPROCITY_HIGH_MIN_SHARE
        and anchors.user_message_count >= RECIPROCITY_HIGH_MIN_USER_MESSAGES
    ):
        return ReciprocityBand.HIGH
    return ReciprocityBand.BALANCED


def _continuity_kinds(anchors: RelationshipAnchors) -> int:
    kinds = 0
    if anchors.session_return_count >= 1:
        kinds += 1
    if anchors.open_loop_observed_count + anchors.open_loop_resolved_count >= 1:
        kinds += 1
    if anchors.user_context_reference_count >= 1:
        kinds += 1
    if anchors.user_topic_continuation_count >= 2:
        kinds += 1
    return kinds


def _derive_continuity(anchors: RelationshipAnchors) -> ContinuityBand:
    if anchors.interaction_count <= 0:
        return ContinuityBand.UNKNOWN
    kinds = _continuity_kinds(anchors)
    if kinds >= 3 or (anchors.session_return_count >= CONTINUITY_RICH_MIN_RETURNS and kinds >= 2):
        return ContinuityBand.RICH
    if kinds >= 1:
        return ContinuityBand.ANCHORED
    return ContinuityBand.SPARSE


def _days_since_last_seen(anchors: RelationshipAnchors, now: datetime) -> float | None:
    last = _parse_ts(anchors.last_seen_at)
    if last is None:
        return None
    return max(0.0, (now - last).total_seconds() / 86400.0)


def _derive_trend(anchors: RelationshipAnchors, now: datetime) -> TrendDirection:
    if anchors.interaction_count <= 0:
        return TrendDirection.UNKNOWN
    days_since = _days_since_last_seen(anchors, now)
    if days_since is not None and days_since >= DECAY_DORMANT_DAYS:
        return TrendDirection.DECLINING
    if anchors.positive_streak >= TREND_GROWING_MIN_STREAK:
        return TrendDirection.GROWING
    return TrendDirection.STABLE


def _derive_raw_bands(anchors: RelationshipAnchors, now: datetime) -> RelationshipBands:
    return RelationshipBands(
        familiarity=_derive_familiarity(anchors, now),
        engagement=_derive_engagement(anchors),
        reciprocity=_derive_reciprocity(anchors),
        continuity=_derive_continuity(anchors),
        trend=_derive_trend(anchors, now),
    )


def _clamp_upward(
    previous: enum.Enum, candidate: enum.Enum, order: tuple[enum.Enum, ...]
) -> enum.Enum:
    """Limit upward band movement to a single step per accumulation.

    Structural single-turn protection: no single turn may jump multiple
    relationship levels, regardless of how many signals it carries.
    Downward movement is never clamped (evidence of disengagement applies
    immediately; dormancy decay is handled separately at readout).
    """
    try:
        prev_idx = order.index(previous)
        cand_idx = order.index(candidate)
    except ValueError:
        return candidate
    if cand_idx > prev_idx + 1:
        return order[prev_idx + 1]
    return candidate


# ---------------------------------------------------------------------------
# Accumulation (pure): turn evidence -> updated durable anchors
# ---------------------------------------------------------------------------


def apply_transition_policy(
    base: RelationshipAnchors,
    updated: RelationshipAnchors,
    evidence: RelationshipTurnEvidence,
    now: datetime | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Explicit deterministic transition policy: previous state + new evidence.

    This is the single place where durable band transitions are decided.
    ``accumulate_turn`` is the sole committer and calls this exactly once
    per accumulation; no second transition engine exists.

    Policy (Phase 3, pinned by ``tests/test_relationship_transitions_phase3.py``):

    * Independent dimensions: each persisted band (familiarity, engagement,
      reciprocity, continuity) transitions independently. One dimension
      reaching a candidate threshold never directly modifies another;
      shared raw evidence contributes only through the existing per-dimension
      counter mapping.
    * Counter-based candidates: candidate bands derive from counters (via
      ``_derive_raw_bands``), never from the committed band. The committed
      band is the baseline/clamp state only.
    * One-step upward promotion: a persisted band advances at most one level
      per accumulation, however many signals the turn carries. This makes
      single-turn protection structural: stored bands — and therefore every
      snapshot derived from them — can never jump multiple levels in one
      accumulation.
    * No invented durable decline: downward movement is never clamped.
      Evidence of disengagement applies immediately through the existing
      candidate calculation (in practice only reciprocity ratios and the
      derived trend can regress, because all other candidates derive from
      cumulative counters). No decline evidence model, hysteresis band, or
      cooling/uncertain vocabulary is introduced here.
    * Trend stays derived: the trend label is computed, recorded in the
      transition record for explainability, but never committed as an
      authoritative band.
    * LLM promotion-inert: this function reads no LLM-derived input. The
      evidence ``llm_relationship_engagement`` float influences only the
      informational corroboration counter folded earlier in
      ``accumulate_turn``; it cannot create, satisfy, or alter a transition.

    Pure and deterministic: same base + updated counters + evidence +
    timestamp → identical ``(committed_bands, transitions)``. Reason tokens
    use the bounded evidence-field vocabulary only — never raw text.
    """
    moment = _coerce_now(now)
    stamp = _iso(moment)
    previous_bands = RelationshipBands(
        familiarity=_stored_band(base, "familiarity"),
        engagement=_stored_band(base, "engagement"),
        reciprocity=_stored_band(base, "reciprocity"),
        continuity=_stored_band(base, "continuity"),
        trend=_derive_trend(base, moment),
    )
    candidate_bands = _derive_raw_bands(updated, moment)
    clamped = RelationshipBands(
        familiarity=_clamp_upward(
            previous_bands.familiarity, candidate_bands.familiarity, _FAMILIARITY_ORDER
        ),
        engagement=_clamp_upward(
            previous_bands.engagement, candidate_bands.engagement, _ENGAGEMENT_ORDER
        ),
        reciprocity=_clamp_upward(
            previous_bands.reciprocity, candidate_bands.reciprocity, _RECIPROCITY_ORDER
        ),
        continuity=_clamp_upward(
            previous_bands.continuity, candidate_bands.continuity, _CONTINUITY_ORDER
        ),
        trend=candidate_bands.trend,
    )
    reason = evidence.reason_token()
    transitions = dict(updated.transitions)
    committed_bands = dict(base.bands or {})
    for band_name in ("familiarity", "engagement", "reciprocity", "continuity", "trend"):
        before = getattr(previous_bands, band_name).value
        after = getattr(clamped, band_name).value
        if before != after:
            transitions[band_name] = {"from": before, "to": after, "at": stamp, "reason": reason}
    for band_name in ("familiarity", "engagement", "reciprocity", "continuity"):
        committed_bands[band_name] = getattr(clamped, band_name).value
    return committed_bands, transitions


def _provenance_for_evidence(evidence: RelationshipTurnEvidence) -> float:
    """Deterministic provenance for one turn's update (no LLM trust)."""
    if evidence.session_returned or evidence.user_shared_information:
        # Directly observed behavior / explicit fan action.
        return PROVENANCE_EXPLICIT
    if evidence.positive_behavioral_signals() >= POSITIVE_SIGNALS_PER_TURN:
        return PROVENANCE_STRONG_INFERENCE
    if evidence.user_sent_message:
        return PROVENANCE_SYSTEM_EVENT
    return PROVENANCE_WEAK_INFERENCE


def accumulate_turn(
    anchors: RelationshipAnchors | None,
    evidence: RelationshipTurnEvidence,
    now: datetime | None = None,
    *,
    source: str = "turn",
) -> RelationshipAnchors:
    """Fold one turn of behavioral evidence into durable anchors (pure).

    * Deterministic: same anchors + evidence + timestamp → identical result.
    * No I/O, no LLM, no commerce access. All state is passed in.
    * LLM engagement (``llm_relationship_engagement``) only increments the
      informational ``corroborated_engagement_observations`` counter when a
      high value coincides with >=1 behavioral signal in the same turn; it
      never promotes bands (see module docstring inference rule).
    * Upward band movement is clamped to a single step per call, so one
      turn — however flirtatious, enthusiastic, or complimentary — cannot
      jump multiple levels.
    """
    moment = _coerce_now(now)
    base = anchors if anchors is not None else neutral_anchors(moment)
    stamp = _iso(moment)

    positive = evidence.positive_behavioral_signals()
    if positive >= POSITIVE_SIGNALS_PER_TURN:
        streak = base.positive_streak + 1
    elif evidence.user_sent_message:
        streak = 0
    else:
        # Non-user turns (e.g. assistant-only bookkeeping) leave the streak.
        streak = base.positive_streak

    corroborated = base.corroborated_engagement_observations
    llm_value = evidence.llm_relationship_engagement
    if isinstance(llm_value, bool) or (
        llm_value is not None and not isinstance(llm_value, (int, float))
    ):
        llm_value = None
    if (
        isinstance(llm_value, (int, float))
        and llm_value >= LLM_ENGAGEMENT_HIGH
        and evidence.substantive_behavioral_signals() >= 1
    ):
        corroborated += 1

    provenance = _provenance_for_evidence(evidence)
    updated = RelationshipAnchors(
        schema_version=SCHEMA_VERSION,
        first_seen_at=base.first_seen_at or stamp,
        last_seen_at=stamp
        if evidence.user_sent_message or base.last_seen_at is None
        else base.last_seen_at,
        interaction_count=base.interaction_count + (1 if evidence.user_sent_message else 0),
        user_message_count=base.user_message_count + (1 if evidence.user_sent_message else 0),
        user_question_count=base.user_question_count + (1 if evidence.user_asked_question else 0),
        user_answer_count=base.user_answer_count + (1 if evidence.user_answered_question else 0),
        user_shared_info_count=base.user_shared_info_count
        + (1 if evidence.user_shared_information else 0),
        user_topic_continuation_count=base.user_topic_continuation_count
        + (1 if evidence.user_continued_topic else 0),
        user_context_reference_count=base.user_context_reference_count
        + (1 if evidence.user_referenced_previous_context else 0),
        assistant_question_count=base.assistant_question_count
        + (1 if evidence.assistant_asked_question else 0),
        assistant_shared_info_count=base.assistant_shared_info_count
        + (1 if evidence.assistant_shared_information else 0),
        session_return_count=base.session_return_count + (1 if evidence.session_returned else 0),
        open_loop_observed_count=base.open_loop_observed_count
        + (1 if evidence.open_loop_continued else 0),
        open_loop_resolved_count=base.open_loop_resolved_count
        + (1 if evidence.open_loop_resolved else 0),
        corroborated_engagement_observations=corroborated,
        positive_streak=streak,
        observation_count=base.observation_count + 1,
        last_provenance=provenance,
        last_source=source,
        transitions=dict(base.transitions),
    )

    # Explicit transition policy (single committer): candidate bands derive
    # from updated counters, committed bands advance at most one upward step,
    # trend stays derived, and LLM input is promotion-inert. See
    # ``apply_transition_policy`` for the pinned policy.
    committed_bands, transitions = apply_transition_policy(base, updated, evidence, moment)
    # Rebuild with the committed bands + transition record (counters final).
    return RelationshipAnchors(
        schema_version=updated.schema_version,
        first_seen_at=updated.first_seen_at,
        last_seen_at=updated.last_seen_at,
        interaction_count=updated.interaction_count,
        user_message_count=updated.user_message_count,
        user_question_count=updated.user_question_count,
        user_answer_count=updated.user_answer_count,
        user_shared_info_count=updated.user_shared_info_count,
        user_topic_continuation_count=updated.user_topic_continuation_count,
        user_context_reference_count=updated.user_context_reference_count,
        assistant_question_count=updated.assistant_question_count,
        assistant_shared_info_count=updated.assistant_shared_info_count,
        session_return_count=updated.session_return_count,
        open_loop_observed_count=updated.open_loop_observed_count,
        open_loop_resolved_count=updated.open_loop_resolved_count,
        corroborated_engagement_observations=updated.corroborated_engagement_observations,
        positive_streak=updated.positive_streak,
        observation_count=updated.observation_count,
        last_provenance=updated.last_provenance,
        last_source=updated.last_source,
        bands=committed_bands,
        transitions=transitions,
    )


# ---------------------------------------------------------------------------
# Snapshot derivation (pure): anchors [+ optional turn context] -> view
# ---------------------------------------------------------------------------


def derive_relationship_snapshot(
    durable_anchors: RelationshipAnchors | None,
    turn_evidence: RelationshipTurnEvidence | None = None,
    now: datetime | None = None,
) -> RelationshipSnapshot:
    """Derive the deterministic per-turn relationship view (pure).

    Bands come from the durable committed levels (which advance at most one
    step per accumulation); ``turn_evidence`` contributes solely the
    informational ``active_signals_this_turn`` count and never promotes
    bands (single-turn protection holds for the snapshot too).

    Dormancy decay (``DECAY_DORMANT_DAYS``) downgrades the familiarity and
    engagement *readout* by one documented step. Anchors are never mutated
    here — history survives decay and a returning fan re-warms the readout
    through new accumulation.
    """
    moment = _coerce_now(now)
    anchors = durable_anchors if durable_anchors is not None else neutral_anchors(moment)
    raw = RelationshipBands(
        familiarity=_stored_band(anchors, "familiarity"),
        engagement=_stored_band(anchors, "engagement"),
        reciprocity=_stored_band(anchors, "reciprocity"),
        continuity=_stored_band(anchors, "continuity"),
        trend=_derive_trend(anchors, moment),
    )
    days_since = _days_since_last_seen(anchors, moment)

    decay_applied = bool(
        days_since is not None
        and days_since >= DECAY_DORMANT_DAYS
        and anchors.interaction_count > 0
    )
    familiarity = raw.familiarity
    engagement = raw.engagement
    if decay_applied:
        familiarity = _decay_one_step(familiarity, _FAMILIARITY_ORDER, FamiliarityBand.NEW)
        engagement = _decay_one_step(engagement, _ENGAGEMENT_ORDER, EngagementBand.LOW)

    active = 0
    if turn_evidence is not None:
        active = sum(
            1 for name in _EVIDENCE_FIELD_NAMES if getattr(turn_evidence, name, False) is True
        )

    return RelationshipSnapshot(
        familiarity=familiarity,
        engagement=engagement,
        reciprocity=raw.reciprocity,
        continuity=raw.continuity,
        trend=raw.trend,
        days_since_last_seen=days_since,
        decay_applied=decay_applied,
        active_signals_this_turn=active,
        computed_at=_iso(moment),
    )


def _decay_one_step(band: enum.Enum, order: tuple[enum.Enum, ...], floor: enum.Enum) -> enum.Enum:
    try:
        idx = order.index(band)
    except ValueError:
        return band
    floor_idx = order.index(floor)
    return order[max(floor_idx, idx - 1)]


# ---------------------------------------------------------------------------
# Serialization (deterministic, tolerant, bounded)
# ---------------------------------------------------------------------------

_ANCHOR_INT_FIELDS = (
    "interaction_count",
    "user_message_count",
    "user_question_count",
    "user_answer_count",
    "user_shared_info_count",
    "user_topic_continuation_count",
    "user_context_reference_count",
    "assistant_question_count",
    "assistant_shared_info_count",
    "session_return_count",
    "open_loop_observed_count",
    "open_loop_resolved_count",
    "corroborated_engagement_observations",
    "positive_streak",
    "observation_count",
)

_KNOWN_BAND_NAMES = ("familiarity", "engagement", "reciprocity", "continuity", "trend")

_BAND_ENUMS: dict[str, tuple[enum.Enum, ...]] = {
    "familiarity": _FAMILIARITY_ORDER,
    "engagement": _ENGAGEMENT_ORDER,
    "reciprocity": _RECIPROCITY_ORDER,
    "continuity": _CONTINUITY_ORDER,
}


def _stored_band(anchors: RelationshipAnchors, band_name: str) -> enum.Enum:
    """Read the committed level for one band; unknown/missing → UNKNOWN."""
    order = _BAND_ENUMS[band_name]
    raw = (anchors.bands or {}).get(band_name)
    for member in order:
        if member.value == raw:
            return member
    return order[0]


def _coerce_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return 0


def _coerce_provenance(value: Any) -> float:
    if isinstance(value, bool):
        return PROVENANCE_SYSTEM_EVENT
    if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
        return float(value)
    return PROVENANCE_SYSTEM_EVENT


def anchors_to_dict(anchors: RelationshipAnchors) -> dict[str, Any]:
    """Deterministic fixed-key serialization (no raw text by construction)."""
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "first_seen_at": anchors.first_seen_at,
        "last_seen_at": anchors.last_seen_at,
        "last_provenance": anchors.last_provenance,
        "last_source": str(anchors.last_source)[:64],
    }
    for name in _ANCHOR_INT_FIELDS:
        payload[name] = getattr(anchors, name, 0)
    # Committed bands: only known band names with valid member values.
    bands: dict[str, str] = {}
    for band_name, order in _BAND_ENUMS.items():
        raw = (anchors.bands or {}).get(band_name)
        if any(member.value == raw for member in order):
            bands[band_name] = str(raw)
    payload["bands"] = bands
    # Transitions: bounded to known bands, fixed vocabulary values only.
    transitions: dict[str, dict[str, str]] = {}
    for band_name in _KNOWN_BAND_NAMES:
        entry = (anchors.transitions or {}).get(band_name)
        if isinstance(entry, dict):
            transitions[band_name] = {
                key: str(entry.get(key, ""))[:96] for key in ("from", "to", "at", "reason")
            }
    payload["transitions"] = transitions
    return payload


def anchors_from_dict(data: Any, now: datetime | None = None) -> RelationshipAnchors:
    """Tolerant load: malformed/unknown input fails safe to neutral anchors.

    Unknown future fields are ignored (forward compatibility). Wrong types
    coerce to neutral defaults. Never raises on untrusted stored JSON.
    """
    _ = _coerce_now(now)
    if not isinstance(data, dict):
        return neutral_anchors()
    try:
        transitions: dict[str, dict[str, str]] = {}
        raw_transitions = data.get("transitions")
        if isinstance(raw_transitions, dict):
            for band_name in _KNOWN_BAND_NAMES:
                entry = raw_transitions.get(band_name)
                if isinstance(entry, dict):
                    transitions[band_name] = {
                        key: str(entry.get(key, ""))[:96] for key in ("from", "to", "at", "reason")
                    }
        first_seen = data.get("first_seen_at")
        last_seen = data.get("last_seen_at")
        bands: dict[str, str] = {}
        raw_bands = data.get("bands")
        if isinstance(raw_bands, dict):
            for band_name, order in _BAND_ENUMS.items():
                raw = raw_bands.get(band_name)
                if any(member.value == raw for member in order):
                    bands[band_name] = str(raw)
        return RelationshipAnchors(
            schema_version=SCHEMA_VERSION,
            first_seen_at=first_seen if isinstance(first_seen, str) else None,
            last_seen_at=last_seen if isinstance(last_seen, str) else None,
            last_provenance=_coerce_provenance(data.get("last_provenance")),
            last_source=str(data.get("last_source", "loaded"))[:64],
            bands=bands,
            transitions=transitions,
            **{name: _coerce_non_negative_int(data.get(name)) for name in _ANCHOR_INT_FIELDS},  # type: ignore[arg-type]
        )
    except Exception:
        logger.warning("anchors_from_dict failed; returning neutral anchors", exc_info=True)
        return neutral_anchors()


def serialize_relationship_anchors(anchors: RelationshipAnchors) -> str:
    """Deterministic JSON serialization (sorted keys) for logging/tests."""
    return json.dumps(anchors_to_dict(anchors), sort_keys=True)


# ---------------------------------------------------------------------------
# Persistence adapter (creator-scoped JSONB namespace, fail-open)
# ---------------------------------------------------------------------------


def get_relationship_anchors(
    profile: dict[str, Any] | None, creator_id: int
) -> RelationshipAnchors:
    """Load this creator's anchors from an already-fetched profile (pure read).

    Fails safe to neutral anchors on any malformed input. Never raises for
    missing namespaces; other creators' namespaces are never touched.
    """
    try:
        if not isinstance(profile, dict):
            return neutral_anchors()
        by_creator = profile.get(RELATIONSHIP_TRAJECTORY_KEY, {})
        if not isinstance(by_creator, dict):
            return neutral_anchors()
        # str keys for JSON; tolerate int keys in hand-built profiles (same
        # fallback order as long_term_memory / commercial preferences).
        block = by_creator.get(str(creator_id), {}) or by_creator.get(creator_id, {})
        if not isinstance(block, dict) or not block:
            return neutral_anchors()
        return anchors_from_dict(block)
    except Exception:
        logger.warning("get_relationship_anchors failed; returning neutral", exc_info=True)
        return neutral_anchors()


async def store_relationship_anchors(
    user_id: int, creator_id: int, anchors: RelationshipAnchors
) -> bool:
    """Persist this creator's anchors atomically; fail-open, never raises.

    Uses the existing ``mutate_user_profile_atomically`` row-locked helper
    and writes only ``facts[KEY][str(creator_id)]``. Unrelated namespaces
    (fan knowledge, commercial preferences, other creators) are preserved
    by construction. Returns False (with a warning log) when persistence is
    unavailable — callers must continue the conversation with the in-memory
    snapshot. Relationship state never blocks sending.
    """
    try:
        from db.postgres import mutate_user_profile_atomically

        block = anchors_to_dict(anchors)

        def _mutate(facts: dict[str, Any]) -> bool:
            by_creator = facts.get(RELATIONSHIP_TRAJECTORY_KEY, {})
            if not isinstance(by_creator, dict):
                by_creator = {}
            # str keys for JSON; only this creator's entry is assigned.
            by_creator[str(creator_id)] = block
            facts[RELATIONSHIP_TRAJECTORY_KEY] = by_creator
            return True

        return bool(await mutate_user_profile_atomically(user_id, _mutate))
    except Exception:
        logger.warning(
            "store_relationship_anchors failed for %s:%s (fail-open)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return False


__all__ = [
    "DECAY_DORMANT_DAYS",
    "ESTABLISHED_MIN_INTERACTIONS",
    "FAMILIAR_MIN_INTERACTIONS",
    "LLM_ENGAGEMENT_HIGH",
    "PROVENANCE_EXPLICIT",
    "PROVENANCE_STRONG_INFERENCE",
    "PROVENANCE_SYSTEM_EVENT",
    "PROVENANCE_WEAK_INFERENCE",
    "RELATIONSHIP_TRAJECTORY_KEY",
    "SCHEMA_VERSION",
    "TREND_GROWING_MIN_STREAK",
    "ContinuityBand",
    "EngagementBand",
    "FamiliarityBand",
    "ReciprocityBand",
    "RelationshipAnchors",
    "RelationshipBands",
    "RelationshipSnapshot",
    "RelationshipTurnEvidence",
    "TrendDirection",
    "accumulate_turn",
    "anchors_from_dict",
    "anchors_to_dict",
    "apply_transition_policy",
    "derive_relationship_snapshot",
    "get_relationship_anchors",
    "neutral_anchors",
    "neutral_snapshot",
    "serialize_relationship_anchors",
    "store_relationship_anchors",
]
