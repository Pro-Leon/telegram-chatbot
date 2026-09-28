"""Descriptive intimacy trajectory -- Phase 6 deterministic domain.

Describes the *observed conversational intimacy* trajectory between one
creator and one fan (``creator_id + user_id`` grain). It is conceptually
separate from, and architecturally parallel to, the Phase 1 relationship
trajectory (``commerce/relationship_trajectory.py``):

* relationship trajectory -- interaction history, participation,
  reciprocity, continuity (never affection/permission/commerce).
* intimacy trajectory (this module) -- observed romantic / playful /
  emotional / sexual-conversation / continuity signals per processed turn.

What this domain IS:

* Descriptive state only. Bands record that intimate conversation
  happened, never that intimate conversation is permitted, authorized,
  consented to, or safe.
* Durable counters + deterministic band reads, creator-scoped, bounded,
  fail-open -- the same proven architecture as Phase 1.

What this domain is NOT (explicit non-goals, enforced by absence):

* permission / consent / authorization / escalation-allowance state.
  No field resembling ``sexual_allowed`` / ``consent_level`` /
  ``escalation_allowed`` exists here or may be added here.
* safety policy. No band value is a safety decision and nothing here
  bypasses or weakens safety/routing/capability controls.
* commerce authority. This module never imports ``commerce/desire.py``,
  ``commerce/temperature.py``, ``commerce/readiness.py``,
  ``commerce/offer_readiness.py``, or ``commerce/relationship.py``.
  Intimacy state never flows into desire/temperature/readiness/ranking/
  sealing/execution.
* adult status. No ``age_verified`` / ``adult`` / ``is_adult`` field
  exists here. Phase 6 implements no adult gate.
* refusal / boundary / consent state. Phase 7 owns that architecture.

Design rules enforced by this module (mirrors Phase 1 contracts):

* Pure deterministic derivation: no network, no LLM, no DB/Redis access,
  no randomness, no credentials, no commerce logic.
* Durable state holds counters/timestamps/bands/provenance only -- never
  raw message text, sexual text, prompt fragments, or permission
  decisions.
* Provenance vocabulary mirrors ``commerce/long_term_memory.py``
  (``EXPLICIT 1.0 / SYSTEM_EVENT 0.9 / STRONG_INFERENCE 0.8 /
  WEAK_INFERENCE 0.5``). Local constants are defined here (rather than
  imported) to keep this module free of commerce imports.
* A single LLM observation can never promote durable state; durable
  promotion requires repeated deterministic behavioral evidence.
  Unknown/neutral is preferred over fabricated conclusions.
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

logger = logging.getLogger("commerce.intimacy_trajectory")

# ---------------------------------------------------------------------------
# Namespace / versioning (persistence contract)
# ---------------------------------------------------------------------------

#: Creator-namespaced JSONB key inside ``user_profiles.facts``. Follows the
#: established ``*_by_creator`` pattern. Deliberately separate from
#: ``relationship_trajectory_by_creator``: the two trajectories are
#: related but independent and must never become one state machine.
INTIMACY_TRAJECTORY_KEY = "intimacy_trajectory_by_creator"

#: Schema version stamped on every persisted anchor block. Readers tolerate
#: unknown future fields (see ``intimacy_anchors_from_dict``); writers
#: always stamp the current version.
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Provenance vocabulary (mirrors commerce/long_term_memory.py values)
# ---------------------------------------------------------------------------

#: Fan explicitly stated / directly observed intimate content.
PROVENANCE_EXPLICIT = 1.0
#: Deterministic system event (e.g. observed turn bookkeeping).
PROVENANCE_SYSTEM_EVENT = 0.9
#: Strong deterministic inference from corroborated behavioral evidence.
PROVENANCE_STRONG_INFERENCE = 0.8
#: Weak single-observation inference; never promotes durable bands alone.
PROVENANCE_WEAK_INFERENCE = 0.5

# ---------------------------------------------------------------------------
# Dimensions (exactly five; descriptive conversational trajectory only)
# ---------------------------------------------------------------------------

#: Closed dimension vocabulary. Values are part of the Phase 6 contract.
#: These describe observed conversation only -- never permission, consent,
#: authorization, readiness, or commerce interest.
INTIMACY_DIMENSIONS = (
    "romantic",
    "playful",
    "emotional",
    "sexual_conversation",
    "intimate_continuity",
)


class IntimacyBand(str, enum.Enum):
    """Accumulated conversational evidence for one intimacy dimension.

    UNKNOWN means "insufficient evidence accumulated" -- it does NOT mean
    rejection, disinterest, permission denied, or commercial coldness.
    """

    UNKNOWN = "unknown"
    LOW = "low"
    STEADY = "steady"
    DEEP = "deep"


#: Shared band order for every dimension (single-step clamp + decay).
_INTIMACY_ORDER = (
    IntimacyBand.UNKNOWN,
    IntimacyBand.LOW,
    IntimacyBand.STEADY,
    IntimacyBand.DEEP,
)

# ---------------------------------------------------------------------------
# Thresholds (explicit constants, conservative absolute counts)
# ---------------------------------------------------------------------------
# Deliberately NOT copied from relationship thresholds: intimacy evidence
# is sparser and higher-stakes to misread, so entry requires repeated
# corroborated observations. Tune only with a dedicated calibration
# review. These count corroborated per-turn dimension hits (at most one
# per dimension per processed turn), never raw word counts.

#: Corroborated observations required to leave UNKNOWN.
INTIMACY_LOW_MIN_OBSERVATIONS = 2
#: Corroborated observations required to reach STEADY.
INTIMACY_STEADY_MIN_OBSERVATIONS = 6
#: Corroborated observations required to reach DEEP.
INTIMACY_DEEP_MIN_OBSERVATIONS = 12

#: Dormancy horizon after which the *readout* decays one step. Historical
#: anchors are never destroyed by decay (see
#: ``derive_intimacy_snapshot``). Same conservative horizon as Phase 1.
DECAY_DORMANT_DAYS = 30.0

#: LLM hint value at/above which an observation counts as corroborated for
#: informational purposes. Corroboration only increments an informational
#: counter; it never promotes bands (see ``accumulate_intimacy_turn``).
LLM_INTIMACY_HIGH = 0.8

# Fixed vocabulary for transition reasons (bounded; no raw text stored).
_EVIDENCE_FIELD_NAMES = (
    "romantic_signal",
    "playful_signal",
    "emotional_signal",
    "sexual_conversation_signal",
    "intimate_continuity_signal",
    "user_initiated_intimacy",
    "assistant_intimacy_continuation",
    "current_intimate_topic",
    "prior_intimate_context_reference",
)

# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntimacyTurnEvidence:
    """Narrow typed input: intimacy observations for exactly one turn.

    Every boolean must be derivable from existing deterministic sources
    (current message, history roles, topic/thread trackers). The
    ``llm_content_interest`` / ``llm_explicit_content`` fields are the
    only LLM-derived inputs: optional advisory values, bounded
    informational use only (corroboration counter), never band promotion.
    """

    romantic_signal: bool = False
    playful_signal: bool = False
    emotional_signal: bool = False
    sexual_conversation_signal: bool = False
    intimate_continuity_signal: bool = False
    user_initiated_intimacy: bool = False
    assistant_intimacy_continuation: bool = False
    current_intimate_topic: bool = False
    prior_intimate_context_reference: bool = False
    llm_content_interest: float | None = None
    llm_explicit_content: bool = False

    def dimension_signals(self) -> int:
        """Count dimension-level signals in this turn (max 5)."""
        return sum(
            1
            for name in (
                "romantic_signal",
                "playful_signal",
                "emotional_signal",
                "sexual_conversation_signal",
                "intimate_continuity_signal",
            )
            if getattr(self, name, False) is True
        )

    def substantive_signals(self) -> int:
        """Dimension signals excluding bare user initiation.

        Used for LLM-corroboration gating: a high LLM hint only
        corroborates when the same turn carries substantive deterministic
        intimacy evidence -- never on mere message presence.
        """
        return self.dimension_signals()

    def reason_token(self) -> str:
        """Deterministic bounded reason token for transitions."""
        active = sorted(n for n in _EVIDENCE_FIELD_NAMES if getattr(self, n, False) is True)
        return "accumulated:" + "+".join(active) if active else "accumulated:turn"


@dataclass
class IntimacyAnchors:
    """Durable per-(creator, fan) intimacy evidence.

    Counters/timestamps/bands/provenance only -- never raw text, sexual
    text, permission decisions, or commerce state.
    """

    schema_version: int = SCHEMA_VERSION
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    observation_count: int = 0
    romantic_count: int = 0
    playful_count: int = 0
    emotional_count: int = 0
    sexual_conversation_count: int = 0
    intimate_continuity_count: int = 0
    romantic_last_observed_at: str | None = None
    playful_last_observed_at: str | None = None
    emotional_last_observed_at: str | None = None
    sexual_conversation_last_observed_at: str | None = None
    intimate_continuity_last_observed_at: str | None = None
    corroborated_observations: int = 0
    last_provenance: float = PROVENANCE_SYSTEM_EVENT
    last_source: str = "cold_start"
    # Committed band levels ({dimension: value}). Advance at most one
    # step per accumulation. Unknown/missing entries read as UNKNOWN.
    bands: dict[str, str] = field(default_factory=dict)
    # Last transition per dimension: {dimension: {from, to, at, reason}}.
    transitions: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class IntimacyBands:
    """Derived band view (raw, before dormancy-decayed readout)."""

    romantic: IntimacyBand = IntimacyBand.UNKNOWN
    playful: IntimacyBand = IntimacyBand.UNKNOWN
    emotional: IntimacyBand = IntimacyBand.UNKNOWN
    sexual_conversation: IntimacyBand = IntimacyBand.UNKNOWN
    intimate_continuity: IntimacyBand = IntimacyBand.UNKNOWN


@dataclass(frozen=True)
class IntimacySnapshot:
    """Deterministic per-turn derived view over durable anchors.

    Bands reflect dormancy decay for *readout* purposes; anchors are
    untouched by decay. Exposes bands + current-turn evidence summary
    only -- never counters, timestamps, provenance, generation IDs,
    commerce state, permission state, adult status, or raw text.
    """

    romantic: IntimacyBand
    playful: IntimacyBand
    emotional: IntimacyBand
    sexual_conversation: IntimacyBand
    intimate_continuity: IntimacyBand
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


def neutral_intimacy_anchors(now: datetime | None = None) -> IntimacyAnchors:
    """Explicit neutral cold start: insufficient intimacy history yet.

    Cold start means "this subsystem has no intimacy history yet" -- it
    does NOT mean rejection, disinterest, permission denied, or
    commercial coldness. No backfill is performed.
    """
    _ = _coerce_now(now)
    return IntimacyAnchors()


def neutral_intimacy_snapshot(now: datetime | None = None) -> IntimacySnapshot:
    """Neutral derived view over absent anchors (all UNKNOWN, no decay)."""
    return IntimacySnapshot(
        romantic=IntimacyBand.UNKNOWN,
        playful=IntimacyBand.UNKNOWN,
        emotional=IntimacyBand.UNKNOWN,
        sexual_conversation=IntimacyBand.UNKNOWN,
        intimate_continuity=IntimacyBand.UNKNOWN,
        days_since_last_seen=None,
        decay_applied=False,
        active_signals_this_turn=0,
        computed_at=_iso(_coerce_now(now)),
    )


# ---------------------------------------------------------------------------
# Raw band derivation (pure; operates on counters only)
# ---------------------------------------------------------------------------


def _derive_dimension_band(count: int) -> IntimacyBand:
    """One dimension's band from its corroborated observation counter."""
    try:
        total = int(count)
    except Exception:
        return IntimacyBand.UNKNOWN
    if total >= INTIMACY_DEEP_MIN_OBSERVATIONS:
        return IntimacyBand.DEEP
    if total >= INTIMACY_STEADY_MIN_OBSERVATIONS:
        return IntimacyBand.STEADY
    if total >= INTIMACY_LOW_MIN_OBSERVATIONS:
        return IntimacyBand.LOW
    return IntimacyBand.UNKNOWN


def _derive_raw_bands(anchors: IntimacyAnchors) -> IntimacyBands:
    return IntimacyBands(
        romantic=_derive_dimension_band(anchors.romantic_count),
        playful=_derive_dimension_band(anchors.playful_count),
        emotional=_derive_dimension_band(anchors.emotional_count),
        sexual_conversation=_derive_dimension_band(anchors.sexual_conversation_count),
        intimate_continuity=_derive_dimension_band(anchors.intimate_continuity_count),
    )


def _clamp_upward(
    previous: enum.Enum, candidate: enum.Enum, order: tuple[enum.Enum, ...]
) -> enum.Enum:
    """Limit upward band movement to a single step per accumulation.

    Structural single-turn protection: no single turn may jump multiple
    intimacy levels, regardless of how many signals it carries.
    Downward movement is never clamped here (candidates derive from
    cumulative counters, which do not regress; dormancy decay applies
    separately at readout).
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


def apply_intimacy_transition_policy(
    base: IntimacyAnchors,
    updated: IntimacyAnchors,
    evidence: IntimacyTurnEvidence,
    now: datetime | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Explicit deterministic transition policy: previous state + evidence.

    The single place where durable intimacy band transitions are
    decided. ``accumulate_intimacy_turn`` is the sole committer and
    calls this exactly once per accumulation.

    Policy:

    * Independent dimensions: each dimension transitions independently.
      One dimension reaching a candidate threshold never modifies
      another; shared turn evidence contributes only through the
      per-dimension counter mapping in ``accumulate_intimacy_turn``.
    * Counter-based candidates: candidate bands derive from counters
      (via ``_derive_raw_bands``), never from the committed band. The
      committed band is the baseline/clamp state only.
    * One-step upward promotion: a persisted band advances at most one
      level per accumulation, however many signals the turn carries.
    * No invented durable decline: counters never regress here, so
      candidates never regress; dormancy decay is readout-only.
    * LLM promotion-inert: this function reads no LLM-derived input.
      The ``llm_content_interest`` / ``llm_explicit_content`` values
      influence only the informational corroboration counter folded
      earlier in ``accumulate_intimacy_turn``.

    Pure and deterministic. Reason tokens use the bounded
    evidence-field vocabulary only -- never raw text.
    """
    moment = _coerce_now(now)
    stamp = _iso(moment)
    # NOTE: the clamp baseline is the committed bands (what was actually
    # stored), while candidates always derive from updated counters.
    previous_bands = IntimacyBands(
        **{name: _stored_band(base, name) for name in INTIMACY_DIMENSIONS}  # type: ignore[arg-type]
    )
    candidate_bands = _derive_raw_bands(updated)
    clamped = IntimacyBands(
        **{
            name: _clamp_upward(
                getattr(previous_bands, name),
                getattr(candidate_bands, name),
                _INTIMACY_ORDER,
            )
            for name in INTIMACY_DIMENSIONS
        }  # type: ignore[arg-type]
    )
    reason = evidence.reason_token()
    transitions = dict(updated.transitions)
    committed_bands = dict(base.bands or {})
    for band_name in INTIMACY_DIMENSIONS:
        before = getattr(previous_bands, band_name).value
        after = getattr(clamped, band_name).value
        if before != after:
            transitions[band_name] = {"from": before, "to": after, "at": stamp, "reason": reason}
    for band_name in INTIMACY_DIMENSIONS:
        committed_bands[band_name] = getattr(clamped, band_name).value
    return committed_bands, transitions


def _provenance_for_evidence(evidence: IntimacyTurnEvidence) -> float:
    """Deterministic provenance for one turn's update (no LLM trust)."""
    if evidence.sexual_conversation_signal or evidence.romantic_signal:
        # Directly observed intimate conversational content.
        return PROVENANCE_EXPLICIT
    if evidence.dimension_signals() >= 2:
        return PROVENANCE_STRONG_INFERENCE
    if evidence.user_initiated_intimacy:
        return PROVENANCE_SYSTEM_EVENT
    return PROVENANCE_WEAK_INFERENCE


def accumulate_intimacy_turn(
    anchors: IntimacyAnchors | None,
    evidence: IntimacyTurnEvidence,
    now: datetime | None = None,
    *,
    source: str = "turn",
) -> IntimacyAnchors:
    """Fold one turn of intimacy evidence into durable anchors (pure).

    * Deterministic: same anchors + evidence + timestamp -> identical.
    * No I/O, no LLM, no commerce access. All state is passed in.
    * Each dimension counter advances at most once per call (boolean
      evidence), so repeated lexical hits in one turn cannot manufacture
      momentum.
    * LLM hints (``llm_content_interest`` / ``llm_explicit_content``)
      only increment the informational ``corroborated_observations``
      counter when a high hint coincides with >=1 substantive
      deterministic signal in the same turn; they never promote bands.
    * Upward band movement is clamped to a single step per call.
    * Neutral turns (no signals) still count as an observation but move
      nothing and erase nothing.
    """
    moment = _coerce_now(now)
    base = anchors if anchors is not None else neutral_intimacy_anchors(moment)
    stamp = _iso(moment)

    user_turn = bool(evidence.user_initiated_intimacy)

    corroborated = base.corroborated_observations
    hint = evidence.llm_content_interest
    if isinstance(hint, bool) or (hint is not None and not isinstance(hint, (int, float))):
        hint = None
    if (
        isinstance(hint, (int, float))
        and hint >= LLM_INTIMACY_HIGH
        and evidence.substantive_signals() >= 1
    ):
        corroborated += 1
    # An explicit-content commerce flag never creates intimacy evidence,
    # but when deterministic sexual-conversation evidence is present in
    # the same turn it corroborates (informational counter only).
    if bool(evidence.llm_explicit_content) and bool(evidence.sexual_conversation_signal):
        corroborated += 1

    provenance = _provenance_for_evidence(evidence)
    updated = IntimacyAnchors(
        schema_version=SCHEMA_VERSION,
        first_seen_at=base.first_seen_at or stamp,
        last_seen_at=stamp if user_turn or base.last_seen_at is None else base.last_seen_at,
        observation_count=base.observation_count + 1,
        romantic_count=base.romantic_count + (1 if evidence.romantic_signal else 0),
        playful_count=base.playful_count + (1 if evidence.playful_signal else 0),
        emotional_count=base.emotional_count + (1 if evidence.emotional_signal else 0),
        sexual_conversation_count=base.sexual_conversation_count
        + (1 if evidence.sexual_conversation_signal else 0),
        intimate_continuity_count=base.intimate_continuity_count
        + (1 if evidence.intimate_continuity_signal else 0),
        romantic_last_observed_at=stamp
        if evidence.romantic_signal
        else base.romantic_last_observed_at,
        playful_last_observed_at=stamp
        if evidence.playful_signal
        else base.playful_last_observed_at,
        emotional_last_observed_at=stamp
        if evidence.emotional_signal
        else base.emotional_last_observed_at,
        sexual_conversation_last_observed_at=stamp
        if evidence.sexual_conversation_signal
        else base.sexual_conversation_last_observed_at,
        intimate_continuity_last_observed_at=stamp
        if evidence.intimate_continuity_signal
        else base.intimate_continuity_last_observed_at,
        corroborated_observations=corroborated,
        last_provenance=provenance,
        last_source=source,
        transitions=dict(base.transitions),
    )

    committed_bands, transitions = apply_intimacy_transition_policy(base, updated, evidence, moment)
    return IntimacyAnchors(
        schema_version=updated.schema_version,
        first_seen_at=updated.first_seen_at,
        last_seen_at=updated.last_seen_at,
        observation_count=updated.observation_count,
        romantic_count=updated.romantic_count,
        playful_count=updated.playful_count,
        emotional_count=updated.emotional_count,
        sexual_conversation_count=updated.sexual_conversation_count,
        intimate_continuity_count=updated.intimate_continuity_count,
        romantic_last_observed_at=updated.romantic_last_observed_at,
        playful_last_observed_at=updated.playful_last_observed_at,
        emotional_last_observed_at=updated.emotional_last_observed_at,
        sexual_conversation_last_observed_at=updated.sexual_conversation_last_observed_at,
        intimate_continuity_last_observed_at=updated.intimate_continuity_last_observed_at,
        corroborated_observations=updated.corroborated_observations,
        last_provenance=updated.last_provenance,
        last_source=updated.last_source,
        bands=committed_bands,
        transitions=transitions,
    )


# ---------------------------------------------------------------------------
# Snapshot derivation (pure): anchors [+ optional turn context] -> view
# ---------------------------------------------------------------------------


def _days_since_last_seen(anchors: IntimacyAnchors, now: datetime) -> float | None:
    last = _parse_ts(anchors.last_seen_at)
    if last is None:
        return None
    return max(0.0, (now - last).total_seconds() / 86400.0)


def _decay_one_step(band: IntimacyBand) -> IntimacyBand:
    try:
        idx = _INTIMACY_ORDER.index(band)
    except ValueError:
        return band
    return _INTIMACY_ORDER[max(0, idx - 1)]


def derive_intimacy_snapshot(
    durable_anchors: IntimacyAnchors | None,
    turn_evidence: IntimacyTurnEvidence | None = None,
    now: datetime | None = None,
) -> IntimacySnapshot:
    """Derive the deterministic per-turn intimacy view (pure).

    Bands come from the durable counters. Dormancy decay
    (``DECAY_DORMANT_DAYS``) downgrades the *readout* by one documented
    step per dimension. Anchors are never mutated here -- history
    survives decay and renewed intimate conversation re-warms the
    readout through new accumulation.
    """
    moment = _coerce_now(now)
    anchors = durable_anchors if durable_anchors is not None else neutral_intimacy_anchors(moment)
    raw = _derive_raw_bands(anchors)
    days_since = _days_since_last_seen(anchors, moment)

    decay_applied = bool(
        days_since is not None
        and days_since >= DECAY_DORMANT_DAYS
        and anchors.observation_count > 0
    )
    if decay_applied:
        bands = IntimacyBands(
            **{name: _decay_one_step(getattr(raw, name)) for name in INTIMACY_DIMENSIONS}  # type: ignore[arg-type]
        )
    else:
        bands = raw

    active = 0
    if turn_evidence is not None:
        active = turn_evidence.dimension_signals()

    return IntimacySnapshot(
        romantic=bands.romantic,
        playful=bands.playful,
        emotional=bands.emotional,
        sexual_conversation=bands.sexual_conversation,
        intimate_continuity=bands.intimate_continuity,
        days_since_last_seen=days_since,
        decay_applied=decay_applied,
        active_signals_this_turn=active,
        computed_at=_iso(moment),
    )


# ---------------------------------------------------------------------------
# Serialization (deterministic, tolerant, bounded)
# ---------------------------------------------------------------------------

_ANCHOR_INT_FIELDS = (
    "observation_count",
    "romantic_count",
    "playful_count",
    "emotional_count",
    "sexual_conversation_count",
    "intimate_continuity_count",
    "corroborated_observations",
)

_ANCHOR_TS_FIELDS = (
    "romantic_last_observed_at",
    "playful_last_observed_at",
    "emotional_last_observed_at",
    "sexual_conversation_last_observed_at",
    "intimate_continuity_last_observed_at",
)


def _stored_band(anchors: IntimacyAnchors, band_name: str) -> IntimacyBand:
    """Read the committed level for one dimension; unknown/missing -> UNKNOWN."""
    raw = (anchors.bands or {}).get(band_name)
    for member in _INTIMACY_ORDER:
        if member.value == raw:
            return member
    return IntimacyBand.UNKNOWN


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


def intimacy_anchors_to_dict(anchors: IntimacyAnchors) -> dict[str, Any]:
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
    for name in _ANCHOR_TS_FIELDS:
        value = getattr(anchors, name, None)
        payload[name] = value if isinstance(value, str) else None
    # Committed bands: only known dimensions with valid member values.
    bands: dict[str, str] = {}
    for band_name in INTIMACY_DIMENSIONS:
        raw = (anchors.bands or {}).get(band_name)
        if any(member.value == raw for member in _INTIMACY_ORDER):
            bands[band_name] = str(raw)
    payload["bands"] = bands
    # Transitions: bounded to known dimensions, fixed vocabulary only.
    transitions: dict[str, dict[str, str]] = {}
    for band_name in INTIMACY_DIMENSIONS:
        entry = (anchors.transitions or {}).get(band_name)
        if isinstance(entry, dict):
            transitions[band_name] = {
                key: str(entry.get(key, ""))[:96] for key in ("from", "to", "at", "reason")
            }
    # Bounded history: keep at most one record per dimension (the
    # transitions mapping above); no separate unbounded log exists.
    payload["transitions"] = transitions
    return payload


def intimacy_anchors_from_dict(data: Any, now: datetime | None = None) -> IntimacyAnchors:
    """Tolerant load: malformed/unknown input fails safe to neutral anchors.

    Unknown future fields are ignored (forward compatibility). Wrong
    types coerce to neutral defaults. Never raises on untrusted stored
    JSON. Never yields permission/commerce/adult semantics: unknown
    fields are dropped, never interpreted.
    """
    _ = _coerce_now(now)
    if not isinstance(data, dict):
        return neutral_intimacy_anchors()
    try:
        transitions: dict[str, dict[str, str]] = {}
        raw_transitions = data.get("transitions")
        if isinstance(raw_transitions, dict):
            for band_name in INTIMACY_DIMENSIONS:
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
            for band_name in INTIMACY_DIMENSIONS:
                raw = raw_bands.get(band_name)
                if any(member.value == raw for member in _INTIMACY_ORDER):
                    bands[band_name] = str(raw)
        timestamps: dict[str, str | None] = {}
        for name in _ANCHOR_TS_FIELDS:
            raw_ts = data.get(name)
            timestamps[name] = raw_ts if isinstance(raw_ts, str) else None
        return IntimacyAnchors(
            schema_version=SCHEMA_VERSION,
            first_seen_at=first_seen if isinstance(first_seen, str) else None,
            last_seen_at=last_seen if isinstance(last_seen, str) else None,
            last_provenance=_coerce_provenance(data.get("last_provenance")),
            last_source=str(data.get("last_source", "loaded"))[:64],
            bands=bands,
            transitions=transitions,
            **timestamps,  # type: ignore[arg-type]
            **{name: _coerce_non_negative_int(data.get(name)) for name in _ANCHOR_INT_FIELDS},  # type: ignore[arg-type]
        )
    except Exception:
        logger.warning("intimacy_anchors_from_dict failed; returning neutral", exc_info=True)
        return neutral_intimacy_anchors()


def serialize_intimacy_anchors(anchors: IntimacyAnchors) -> str:
    """Deterministic JSON serialization (sorted keys) for logging/tests."""
    return json.dumps(intimacy_anchors_to_dict(anchors), sort_keys=True)


# ---------------------------------------------------------------------------
# Persistence adapter (creator-scoped JSONB namespace, fail-open)
# ---------------------------------------------------------------------------


def get_intimacy_anchors(profile: dict[str, Any] | None, creator_id: int) -> IntimacyAnchors:
    """Load this creator's intimacy anchors from an already-fetched profile.

    Pure read over the supplied ``profile`` mapping (this creator's
    namespace only); performs zero DB/Redis access and zero writes.
    Fails safe to neutral anchors on any malformed input. Never raises
    for missing namespaces; other creators' namespaces are never
    touched.
    """
    try:
        if not isinstance(profile, dict):
            return neutral_intimacy_anchors()
        by_creator = profile.get(INTIMACY_TRAJECTORY_KEY, {})
        if not isinstance(by_creator, dict):
            return neutral_intimacy_anchors()
        # str keys for JSON; tolerate int keys in hand-built profiles
        # (same fallback order as relationship trajectory / LTM / fan
        # knowledge).
        block = by_creator.get(str(creator_id), {}) or by_creator.get(creator_id, {})
        if not isinstance(block, dict) or not block:
            return neutral_intimacy_anchors()
        return intimacy_anchors_from_dict(block)
    except Exception:
        logger.warning("get_intimacy_anchors failed; returning neutral", exc_info=True)
        return neutral_intimacy_anchors()


async def store_intimacy_anchors(user_id: int, creator_id: int, anchors: IntimacyAnchors) -> bool:
    """Persist this creator's intimacy anchors atomically; fail-open.

    Uses the existing ``mutate_user_profile_atomically`` row-locked
    helper and writes only ``facts[KEY][str(creator_id)]``. Unrelated
    namespaces (relationship trajectory, fan knowledge, commercial
    preferences, other creators) are preserved by construction.
    Returns False (with a warning log) when persistence is
    unavailable -- callers must continue the conversation with the
    in-memory snapshot. Intimacy state never blocks sending.
    """
    try:
        from db.postgres import mutate_user_profile_atomically

        block = intimacy_anchors_to_dict(anchors)

        def _mutate(facts: dict[str, Any]) -> bool:
            by_creator = facts.get(INTIMACY_TRAJECTORY_KEY, {})
            if not isinstance(by_creator, dict):
                by_creator = {}
            # str keys for JSON; only this creator's entry is assigned.
            by_creator[str(creator_id)] = block
            facts[INTIMACY_TRAJECTORY_KEY] = by_creator
            return True

        return bool(await mutate_user_profile_atomically(user_id, _mutate))
    except Exception:
        logger.warning(
            "store_intimacy_anchors failed for %s:%s (fail-open)",
            creator_id,
            user_id,
            exc_info=True,
        )
        return False


__all__ = [
    "DECAY_DORMANT_DAYS",
    "INTIMACY_DEEP_MIN_OBSERVATIONS",
    "INTIMACY_DIMENSIONS",
    "INTIMACY_LOW_MIN_OBSERVATIONS",
    "INTIMACY_STEADY_MIN_OBSERVATIONS",
    "INTIMACY_TRAJECTORY_KEY",
    "LLM_INTIMACY_HIGH",
    "PROVENANCE_EXPLICIT",
    "PROVENANCE_STRONG_INFERENCE",
    "PROVENANCE_SYSTEM_EVENT",
    "PROVENANCE_WEAK_INFERENCE",
    "SCHEMA_VERSION",
    "IntimacyAnchors",
    "IntimacyBand",
    "IntimacyBands",
    "IntimacySnapshot",
    "IntimacyTurnEvidence",
    "accumulate_intimacy_turn",
    "apply_intimacy_transition_policy",
    "derive_intimacy_snapshot",
    "get_intimacy_anchors",
    "intimacy_anchors_from_dict",
    "intimacy_anchors_to_dict",
    "neutral_intimacy_anchors",
    "neutral_intimacy_snapshot",
    "serialize_intimacy_anchors",
    "store_intimacy_anchors",
]
