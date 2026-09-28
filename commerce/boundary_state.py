"""Durable boundary state -- Phase 7 deterministic domain.

Describes the *active conversational constraints* between one creator and
one fan (``creator_id + user_id`` grain). It is conceptually separate
from, and architecturally parallel to, the Phase 1 relationship
trajectory and the Phase 6 intimacy trajectory:

* relationship trajectory -- interaction history (descriptive).
* intimacy trajectory -- observed intimacy signals (descriptive).
* boundary state (this module) -- user-established behavioral-scope
  constraints (prescriptive, deterministic enforcement downstream).

What this domain IS:

* Durable constraints + deterministic reads, creator-scoped, bounded,
  idempotent -- the same proven architecture as Phases 1/2/6.
* Scope-explicit: every constraint carries TURN / TOPIC / MODE /
  CONVERSATION / CONTACT scope. Narrow stays narrow.
* Revocable: explicit relaxation evidence clears a constraint.
  Ordinary positive conversation, intimacy growth, and later engagement
  NEVER clear a constraint (enforced by absence of such rules).

What this domain is NOT (explicit non-goals, enforced by absence):

* permission / consent / sexual-readiness state. No field resembling
  ``permission_score`` / ``consent_score`` / ``sexual_readiness`` /
  ``allowed`` exists here or may be added here.
* safety policy. This module never imports ``core/scoring.py`` and its
  output never weakens safety/routing/capability controls.
* commerce authority. This module never imports ``commerce/desire.py``,
  ``commerce/temperature.py``, ``commerce/readiness.py``,
  ``commerce/offer_readiness.py``, or ``commerce/relationship.py``.
  Boundary state never flows INTO those modules; workers consult
  :func:`boundary_blocks_commerce` as a downstream veto only.
* adult status. No ``age_verified`` / ``adult`` field exists here.
* LLM-derived state. Only deterministic
  :class:`commerce.boundary_evidence.BoundaryTurnEvidence` moves state.

Design rules (mirror Phase 1/2/6 contracts):

* Pure deterministic derivation: no network, no LLM, no randomness, no
  credentials, no commerce logic. The async accumulator performs DB
  writes only through ``mutate_user_profile_atomically``.
* Durable state holds constraint records only -- never raw message
  text, sexual text, prompt fragments, or scores.
* Persistence reuses ``user_profiles.facts`` JSONB
  ``boundary_state_by_creator`` with atomic mutation; write failures are
  reported via the return value (callers keep current-turn enforcement
  from in-memory evidence) and read failures degrade deterministically
  (``BoundarySnapshot.degraded`` forces operator review downstream).
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from commerce.boundary_evidence import (
    BOUNDARY_SCOPES,
    BOUNDARY_TYPES,
    BoundaryTurnEvidence,
)

logger = logging.getLogger("commerce.boundary_state")


# ---------------------------------------------------------------------------
# Namespace / versioning (persistence contract)
# ---------------------------------------------------------------------------

#: Creator-namespaced JSONB key inside ``user_profiles.facts``. Follows the
#: established ``*_by_creator`` pattern. Deliberately separate from flat
#: profile keys, LTM, fan knowledge, and both trajectories.
BOUNDARY_STATE_KEY = "boundary_state_by_creator"

#: Schema version stamped on every persisted creator block.
SCHEMA_VERSION = 1

#: Bounded retention for the generation-keyed processed marker list.
BOUNDARY_PROCESSED_IDS_MAX = 20

#: Field name for the bounded processed-marker list inside this creator's
#: boundary block. Stored alongside (never inside) constraint records.
BOUNDARY_PROCESSED_IDS_FIELD = "processed_generation_ids"

#: Maximum constraint records per creator block. There are exactly seven
#: boundary types, so the dict is naturally bounded; this pins the bound.
BOUNDARY_CONSTRAINTS_MAX = 7


# ---------------------------------------------------------------------------
# Expiry / recovery policy (explicit, documented, bounded)
# ---------------------------------------------------------------------------
# Persistent by default. Only TOPIC-scoped constraints with an explicit
# temporary qualifier ("right now", "for now", ...) expire, plus
# CHANGE_TOPIC which is a turn/topic move by nature. Contact and manner
# constraints never auto-expire: "don't call me babe" and "don't message
# me again" remain until explicit relaxation.

#: Hours after which a temporary TOPIC constraint expires.
TEMPORARY_TOPIC_EXPIRY_HOURS = 24.0

#: CHANGE_TOPIC is a conversational move, not a standing rule: it guides
#: the next turns, then expires.
CHANGE_TOPIC_EXPIRY_HOURS = 24.0

#: A temporary constraint inside this window before expiry reports
#: RECOVERING (observable recovery without a second state machine).
RECOVERING_WINDOW_HOURS = 6.0


class BoundaryStatus(str, enum.Enum):
    """Lifecycle of one boundary constraint."""

    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"
    EXPIRED = "EXPIRED"
    CLEARED = "CLEARED"


#: Commerce veto set: active constraints of these types independently veto
#: an offer transition (regardless of LLM commerce signals). Manner
#: constraints (NO_FLIRTING / NO_PET_NAME / NO_PERSONAL_QUESTION) constrain
#: realization (output validation) but do not veto a neutrally-presented
#: commercial action.
COMMERCE_VETO_TYPES = frozenset(
    {
        "NO_SEXUAL_TOPIC",
        "CHANGE_TOPIC",
        "STOP_CONVERSATION",
        "DO_NOT_CONTACT",
    }
)


# ---------------------------------------------------------------------------
# Records (durable, minimal, no raw text)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryConstraint:
    """One durable constraint (immutable value object)."""

    boundary_type: str
    scope: str
    status: str = BoundaryStatus.ACTIVE.value
    first_observed: str | None = None
    last_reaffirmed: str | None = None
    expires_at: str | None = None
    provenance: str = "explicit:user_assertion"


@dataclass(frozen=True)
class BoundarySnapshot:
    """Compact current-turn view for downstream consumers.

    Exposes only what enforcement needs: active constraint types plus the
    minimum recovery/contact metadata. No raw text, no counters, no
    provenance floats, no generation IDs, no timestamps, no scores.
    """

    active: tuple[str, ...] = ()
    recovering: tuple[str, ...] = ()
    expired: tuple[str, ...] = ()
    #: True when durable state was unreadable; downstream must NOT trust
    #: "no active boundary" and must route to operator review instead.
    degraded: bool = False

    def is_active(self, boundary_type: str) -> bool:
        return boundary_type in self.active

    def blocks_contact(self) -> bool:
        return "DO_NOT_CONTACT" in self.active

    def wants_close(self) -> bool:
        return "STOP_CONVERSATION" in self.active

    def blocks_offer(self) -> bool:
        return any(t in COMMERCE_VETO_TYPES for t in self.active)


# ---------------------------------------------------------------------------
# Time helpers (pure, bounded)
# ---------------------------------------------------------------------------


def _coerce_now(now: datetime | None) -> datetime:
    try:
        if isinstance(now, datetime):
            if now.tzinfo is None:
                return now.replace(tzinfo=UTC)
            return now.astimezone(UTC)
    except Exception:
        pass
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    try:
        if isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.strip())
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    except Exception:
        return None
    return None


def _is_expired(constraint: BoundaryConstraint, now: datetime) -> bool:
    try:
        if constraint.status == BoundaryStatus.CLEARED.value:
            return False
        expiry = _parse_time(constraint.expires_at)
        if expiry is None:
            return False
        return now >= expiry
    except Exception:
        return False


def _is_recovering(constraint: BoundaryConstraint, now: datetime) -> bool:
    try:
        expiry = _parse_time(constraint.expires_at)
        if expiry is None:
            return False
        remaining = (expiry - now).total_seconds() / 3600.0
        return 0.0 <= remaining <= RECOVERING_WINDOW_HOURS
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pure transitions (no I/O, never raise)
# ---------------------------------------------------------------------------


def _expiry_for(boundary_type: str, temporary_qualifier: bool, now: datetime) -> str | None:
    """Return an ISO expiry or None (persistent). Explicit and bounded."""
    try:
        if boundary_type == "CHANGE_TOPIC":
            return (now + timedelta(hours=CHANGE_TOPIC_EXPIRY_HOURS)).isoformat()
        if boundary_type == "NO_SEXUAL_TOPIC" and temporary_qualifier:
            return (now + timedelta(hours=TEMPORARY_TOPIC_EXPIRY_HOURS)).isoformat()
    except Exception:
        return None
    return None


def apply_evidence(
    constraints: Mapping[str, BoundaryConstraint] | None,
    evidence: BoundaryTurnEvidence,
    now: datetime | None = None,
) -> dict[str, BoundaryConstraint]:
    """Apply turn evidence to constraint records (pure).

    Rules (deterministic, documented):

    * Each asserted type activates (new record) or reaffirms (refreshes
      ``last_reaffirmed``; a CLEARED/EXPIRED record reactivates with a new
      ``first_observed``). Reaffirmation never extends a temporary expiry
      unless the new turn also carries a temporary qualifier.
    * Each explicitly relaxed type moves to CLEARED (record retained for
      auditability within the bounded dict). CHANGE_TOPIC has no
      relaxation vocabulary; expiry ends it.
    * Same-turn assertion wins over relaxation per type (decided in the
      evidence extractor; re-enforced here by applying relaxations first).
    * Ordinary conversation (empty evidence) changes nothing: positive
      engagement, intimacy growth, and later messages NEVER clear state.
    """
    try:
        moment = _coerce_now(now)
        current: dict[str, BoundaryConstraint] = (
            dict(constraints) if isinstance(constraints, Mapping) else {}
        )
        if not isinstance(evidence, BoundaryTurnEvidence):
            return current

        # Relaxations first (assertions re-win below when both present).
        for btype in evidence.relaxed_types():
            try:
                existing = current.get(btype)
                if existing is None:
                    continue
                if existing.status == BoundaryStatus.CLEARED.value:
                    continue
                current[btype] = BoundaryConstraint(
                    boundary_type=btype,
                    scope=existing.scope,
                    status=BoundaryStatus.CLEARED.value,
                    first_observed=existing.first_observed,
                    last_reaffirmed=moment.isoformat(),
                    expires_at=None,
                    provenance="explicit:user_relaxation",
                )
            except Exception:
                continue

        for btype in evidence.asserted_types():
            try:
                scope = BOUNDARY_SCOPES.get(btype, "CONVERSATION")
                existing = current.get(btype)
                if existing is not None and existing.status not in (
                    BoundaryStatus.CLEARED.value,
                    BoundaryStatus.EXPIRED.value,
                ):
                    current[btype] = BoundaryConstraint(
                        boundary_type=btype,
                        scope=existing.scope,
                        status=BoundaryStatus.ACTIVE.value,
                        first_observed=existing.first_observed,
                        last_reaffirmed=moment.isoformat(),
                        expires_at=(
                            _expiry_for(btype, evidence.temporary_qualifier, moment)
                            or existing.expires_at
                        ),
                        provenance=existing.provenance,
                    )
                else:
                    current[btype] = BoundaryConstraint(
                        boundary_type=btype,
                        scope=scope,
                        status=BoundaryStatus.ACTIVE.value,
                        first_observed=moment.isoformat(),
                        last_reaffirmed=moment.isoformat(),
                        expires_at=_expiry_for(btype, evidence.temporary_qualifier, moment),
                        provenance="explicit:user_assertion",
                    )
            except Exception:
                continue

        # Bound: at most one record per type (dict naturally bounded).
        while len(current) > BOUNDARY_CONSTRAINTS_MAX:
            try:
                oldest = min(
                    current,
                    key=lambda k: str(
                        current[k].last_reaffirmed or current[k].first_observed or ""
                    ),
                )
                del current[oldest]
            except Exception:
                break
        return current
    except Exception:
        logger.debug("boundary apply_evidence failed (fail-open)", exc_info=True)
        try:
            return dict(constraints) if isinstance(constraints, Mapping) else {}
        except Exception:
            return {}


def derive_boundary_snapshot(
    constraints: Mapping[str, BoundaryConstraint] | None,
    now: datetime | None = None,
    *,
    degraded: bool = False,
) -> BoundarySnapshot:
    """Project records to the current-turn snapshot (pure).

    EXPIRED is computed at read time from ``expires_at`` (lazy; the next
    write prunes it). RECOVERING marks temporary constraints inside the
    recovery window. Unknown types are ignored (forward tolerance).
    """
    try:
        moment = _coerce_now(now)
        active: list[str] = []
        recovering: list[str] = []
        expired: list[str] = []
        if isinstance(constraints, Mapping):
            for btype in BOUNDARY_TYPES:
                try:
                    record = constraints.get(btype)
                    if not isinstance(record, BoundaryConstraint):
                        continue
                    if record.status == BoundaryStatus.CLEARED.value:
                        continue
                    if _is_expired(record, moment):
                        expired.append(btype)
                        continue
                    active.append(btype)
                    if _is_recovering(record, moment):
                        recovering.append(btype)
                except Exception:
                    continue
        return BoundarySnapshot(
            active=tuple(active),
            recovering=tuple(recovering),
            expired=tuple(expired),
            degraded=bool(degraded),
        )
    except Exception:
        logger.debug("boundary snapshot derivation failed", exc_info=True)
        return BoundarySnapshot(degraded=True)


def snapshot_with_current_evidence(
    constraints: Mapping[str, BoundaryConstraint] | None,
    evidence: BoundaryTurnEvidence,
    now: datetime | None = None,
) -> BoundarySnapshot:
    """Effective snapshot: durable records + current-turn evidence (pure).

    Current explicit evidence outranks history: a type asserted this turn
    is active even if the durable write has not completed (or failed),
    and a type relaxed this turn is inactive even if still durable.
    This is what output validation and commerce veto must use.
    """
    try:
        moment = _coerce_now(now)
        merged = apply_evidence(constraints, evidence, moment)
        return derive_boundary_snapshot(merged, moment)
    except Exception:
        logger.debug("boundary effective snapshot failed", exc_info=True)
        return BoundarySnapshot(degraded=True)


def boundary_blocks_commerce(snapshot: BoundarySnapshot | None) -> tuple[bool, str | None]:
    """Downstream commerce veto (pure, deterministic).

    Returns (blocked, reason). Blocked for NO_SEXUAL_TOPIC (sexual-topic
    content transition), CHANGE_TOPIC (no offer pushing the constrained
    topic), STOP_CONVERSATION (no offer on a closing turn), DO_NOT_CONTACT
    (no autonomous contact). Manner constraints (NO_FLIRTING /
    NO_PET_NAME / NO_PERSONAL_QUESTION) constrain realization via output
    validation, not the commercial action itself. Independent of LLM
    ``content_interest`` / ``negative_sentiment`` / ``commercial_paused``.
    """
    try:
        if snapshot is None or not isinstance(snapshot, BoundarySnapshot):
            return False, None
        if snapshot.degraded:
            # Unknown boundary state must not silently authorize commerce.
            return True, "boundary_state_unknown"
        for btype in ("DO_NOT_CONTACT", "STOP_CONVERSATION", "NO_SEXUAL_TOPIC", "CHANGE_TOPIC"):
            if btype in snapshot.active:
                return True, btype.lower()
        return False, None
    except Exception:
        return True, "boundary_state_unknown"


# ---------------------------------------------------------------------------
# Persistence (creator-scoped JSONB, atomic, idempotent, bounded)
# ---------------------------------------------------------------------------


def _constraint_to_dict(record: BoundaryConstraint) -> dict[str, Any]:
    return {
        "boundary_type": record.boundary_type,
        "scope": record.scope,
        "status": record.status,
        "first_observed": record.first_observed,
        "last_reaffirmed": record.last_reaffirmed,
        "expires_at": record.expires_at,
        "provenance": record.provenance,
        "schema_version": SCHEMA_VERSION,
    }


def _constraint_from_dict(raw: Any) -> BoundaryConstraint | None:
    try:
        if not isinstance(raw, dict):
            return None
        btype = str(raw.get("boundary_type", "")).strip()
        if btype not in BOUNDARY_TYPES:
            return None
        scope = str(raw.get("scope", "")).strip() or BOUNDARY_SCOPES.get(btype, "CONVERSATION")
        status = str(raw.get("status", BoundaryStatus.ACTIVE.value)).strip()
        if status not in (
            BoundaryStatus.ACTIVE.value,
            BoundaryStatus.RECOVERING.value,
            BoundaryStatus.EXPIRED.value,
            BoundaryStatus.CLEARED.value,
        ):
            status = BoundaryStatus.ACTIVE.value
        if status == BoundaryStatus.RECOVERING.value:
            # RECOVERING is read-time derived; persisted records stay ACTIVE.
            status = BoundaryStatus.ACTIVE.value

        def _opt_str(value: Any) -> str | None:
            try:
                text = str(value).strip() if value is not None else ""
                return text[:64] if text else None
            except Exception:
                return None

        return BoundaryConstraint(
            boundary_type=btype,
            scope=scope,
            status=status,
            first_observed=_opt_str(raw.get("first_observed")),
            last_reaffirmed=_opt_str(raw.get("last_reaffirmed")),
            expires_at=_opt_str(raw.get("expires_at")),
            provenance=str(raw.get("provenance", "explicit:user_assertion"))[:64],
        )
    except Exception:
        return None


def _read_processed_ids(block: Any) -> list[str]:
    try:
        if not isinstance(block, dict):
            return []
        raw = block.get(BOUNDARY_PROCESSED_IDS_FIELD, [])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip()[:128])
        return out
    except Exception:
        return []


def get_boundary_constraints(profile: Any, creator_id: int | None) -> dict[str, BoundaryConstraint]:
    """Read this creator's durable constraints from a profile mapping (pure)."""
    try:
        if not isinstance(profile, Mapping) or creator_id is None:
            return {}
        try:
            cid = str(int(creator_id))
        except Exception:
            return {}
        by_creator = profile.get(BOUNDARY_STATE_KEY, {})
        if not isinstance(by_creator, Mapping):
            return {}
        block = by_creator.get(cid)
        if not isinstance(block, dict):
            return {}
        records = block.get("constraints", {})
        if not isinstance(records, dict):
            return {}
        out: dict[str, BoundaryConstraint] = {}
        for raw in records.values():
            record = _constraint_from_dict(raw)
            if record is not None:
                out[record.boundary_type] = record
        return out
    except Exception:
        return {}


async def accumulate_boundary_turn_idempotent(
    *,
    user_id: int,
    creator_id: int | None,
    generation_id: str,
    evidence: BoundaryTurnEvidence,
    now: datetime | None = None,
    profile: Any | None = None,
    max_processed_ids: int = BOUNDARY_PROCESSED_IDS_MAX,
) -> tuple[BoundarySnapshot | None, bool]:
    """Accumulate one turn of boundary evidence exactly once (fail-safe).

    * Empty evidence (no assertion, no relaxation): no write; returns the
      current snapshot read from ``profile`` (or an empty snapshot when
      no profile is available). Never fails.
    * Non-empty evidence: marker check + constraint mutation happen
      atomically inside the row-locked ``mutate_user_profile_atomically``
      closure in the ``boundary_state_by_creator`` namespace. Repeated
      ``generation_id`` (redelivery/retry/XAUTOCLAIM) does not re-apply.
    * Persistence failure: returns the effective in-memory snapshot
      (durable records + current evidence) with ``applied=False`` so the
      caller still enforces the current turn.
    * Durable state unreadable AND evidence present: returns an
      evidence-only snapshot with ``degraded=True`` (caller must route to
      operator review, never auto-send).
    * Never raises: catastrophic failure returns ``(None, False)``.
    """
    try:
        moment = _coerce_now(now)
        gen = generation_id.strip() if isinstance(generation_id, str) else ""
        if not gen:
            logger.warning("boundary idempotent accumulate skipped: empty generation_id")
            return None, False
        gen = gen[:128]
        try:
            uid = int(user_id)
        except Exception:
            logger.warning("boundary idempotent accumulate skipped: bad ids")
            return None, False
        if creator_id is None:
            logger.warning("boundary idempotent accumulate skipped: missing creator")
            return None, False
        try:
            cid = int(creator_id)
        except Exception:
            logger.warning("boundary idempotent accumulate skipped: bad ids")
            return None, False
        if not isinstance(evidence, BoundaryTurnEvidence):
            logger.warning("boundary idempotent accumulate skipped: bad evidence")
            return None, False
        try:
            cap = max(1, min(int(max_processed_ids), 100))
        except Exception:
            cap = BOUNDARY_PROCESSED_IDS_MAX

        # Baseline for the write-failure fallback only: a caller-supplied
        # profile mapping is authoritative enough for current-turn
        # enforcement. When no profile is supplied, the mutation closure
        # below reads the baseline from the locked row itself (no extra DB
        # read on the hot path, mirroring intimacy/relationship).
        baseline: dict[str, BoundaryConstraint] = {}
        had_baseline = False
        try:
            if isinstance(profile, Mapping):
                baseline = get_boundary_constraints(profile, cid)
                had_baseline = True
        except Exception:
            baseline = {}
            had_baseline = False

        if evidence.is_empty():
            # No durable effect and nothing to enforce: skip the write
            # entirely (no marker, no row creation).
            try:
                return derive_boundary_snapshot(baseline, moment), False
            except Exception:
                return BoundarySnapshot(), False

        try:
            from db.postgres import mutate_user_profile_atomically
        except Exception:
            logger.warning("boundary accumulate: atomic helper unavailable", exc_info=True)
            try:
                fallback = snapshot_with_current_evidence(baseline, evidence, moment)
                if not had_baseline:
                    fallback = BoundarySnapshot(
                        active=fallback.active,
                        recovering=fallback.recovering,
                        expired=fallback.expired,
                        degraded=True,
                    )
                return fallback, False
            except Exception:
                return BoundarySnapshot(degraded=True), False

        result: dict[str, Any] = {"duplicate": False}

        def _mutate(facts: dict[str, Any]) -> bool:
            try:
                by_creator = facts.get(BOUNDARY_STATE_KEY)
                if not isinstance(by_creator, dict):
                    by_creator = {}
                    facts[BOUNDARY_STATE_KEY] = by_creator
                key = str(cid)
                block = by_creator.get(key)
                if not isinstance(block, dict):
                    block = {}
                    by_creator[key] = block
                processed = _read_processed_ids(block)
                stored_raw = block.get("constraints", {})
                stored: dict[str, BoundaryConstraint] = {}
                if isinstance(stored_raw, dict):
                    for raw in stored_raw.values():
                        record = _constraint_from_dict(raw)
                        if record is not None:
                            stored[record.boundary_type] = record
                if gen in processed:
                    result["duplicate"] = True
                    result["stored_snapshot"] = derive_boundary_snapshot(stored, moment)
                    return False
                updated = apply_evidence(stored, evidence, moment)
                # Lazily prune expired actives on write.
                pruned: dict[str, BoundaryConstraint] = {}
                for btype, record in updated.items():
                    if _is_expired(record, moment):
                        pruned[btype] = BoundaryConstraint(
                            boundary_type=record.boundary_type,
                            scope=record.scope,
                            status=BoundaryStatus.EXPIRED.value,
                            first_observed=record.first_observed,
                            last_reaffirmed=record.last_reaffirmed,
                            expires_at=record.expires_at,
                            provenance=record.provenance,
                        )
                    else:
                        pruned[btype] = record
                block["constraints"] = {
                    btype: _constraint_to_dict(record) for btype, record in pruned.items()
                }
                block["schema_version"] = SCHEMA_VERSION
                processed = ([gen] + processed)[:cap]
                block[BOUNDARY_PROCESSED_IDS_FIELD] = processed
                by_creator[key] = block
                facts[BOUNDARY_STATE_KEY] = by_creator
                result["snapshot"] = derive_boundary_snapshot(pruned, moment)
                return True
            except Exception:
                logger.warning("boundary mutate failed (fail-safe)", exc_info=True)
                return False

        try:
            persisted = bool(await mutate_user_profile_atomically(uid, _mutate))
        except Exception:
            logger.warning("boundary accumulate mutate raised (fail-safe)", exc_info=True)
            persisted = False
        if persisted:
            snapshot = result.get("snapshot")
            if isinstance(snapshot, BoundarySnapshot):
                return snapshot, True
            return derive_boundary_snapshot(
                apply_evidence(baseline, evidence, moment), moment
            ), True
        if result.get("duplicate"):
            # Redelivery: report stored state without re-applying.
            stored_snapshot = result.get("stored_snapshot")
            if isinstance(stored_snapshot, BoundarySnapshot):
                return stored_snapshot, False
            return derive_boundary_snapshot(baseline, moment), False
        # Write failed: enforce this turn from the best baseline available.
        # Without a caller-supplied profile the durable state is unknown:
        # degrade so the worker routes to operator review (fail-closed).
        try:
            fallback = snapshot_with_current_evidence(baseline, evidence, moment)
            if not had_baseline:
                fallback = BoundarySnapshot(
                    active=fallback.active,
                    recovering=fallback.recovering,
                    expired=fallback.expired,
                    degraded=True,
                )
            return fallback, False
        except Exception:
            return BoundarySnapshot(degraded=True), False
    except Exception:
        logger.warning("accumulate_boundary_turn_idempotent failed", exc_info=True)
        return None, False


__all__ = [
    "BOUNDARY_CONSTRAINTS_MAX",
    "BOUNDARY_PROCESSED_IDS_FIELD",
    "BOUNDARY_PROCESSED_IDS_MAX",
    "BOUNDARY_STATE_KEY",
    "COMMERCE_VETO_TYPES",
    "SCHEMA_VERSION",
    "BoundaryConstraint",
    "BoundarySnapshot",
    "BoundaryStatus",
    "accumulate_boundary_turn_idempotent",
    "apply_evidence",
    "boundary_blocks_commerce",
    "derive_boundary_snapshot",
    "get_boundary_constraints",
    "snapshot_with_current_evidence",
]
