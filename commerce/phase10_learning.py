"""Phase 10 — Learning & Optimization (optimization layer, NOT authority layer).

Architecture (must remain)::

    Observation -> deterministic interpretation -> outcome attribution
        -> offline aggregation -> recommendation -> validation
        -> human approval -> versioned configuration
        -> deterministic runtime behavior

NOT::

    Observation -> optimizer -> autonomous runtime action

What Phase 10 learns (bounded):
- deterministic threshold deltas (e.g. fatigue/pressure bounds where approved)
- strategy weighting / bounded strategy-selection parameters
- fatigue/pressure thresholds where explicitly approved

What Phase 10 CANNOT control (all remain NO, enforced by tests):
- execute commerce / send offers / select products / select prices
- override eligibility / ranking authority / sealing / execution
- override safety / boundaries / clear constraints
- select unauthorized content / modify feature flags
- activate its own recommendations (optimizer never self-activates)

Key invariants:
- Attribution key is (creator_id, generation_id). Never approximate with
  timestamps when a generation ID exists. Never attribute to "most recent
  strategy in profile state".
- Missing evidence is missing/unavailable/censored/unattributed — never a
  negative merely because the user did not respond.
- Relationship/conversation outcomes and commerce outcomes are separate
  namespaces. Purchase MUST NOT become relationship success; engagement
  MUST NOT become commercial authorization.
- Every runtime-affecting change is a new immutable versioned configuration.
  Never mutate an activated version.
- Creator isolation: Creator A's outcomes never change Creator B's state.
- Telemetry failure / attribution failure / optimizer failure never blocks
  conversation or commerce (fail-open, fail-safe).
- No online reinforcement learning. Bounded offline learning + controlled
  activation only.

Reuses (never duplicates):
- commerce.adaptive_optimization (CanonicalOutcome, OUTCOME_WEIGHTS,
  outcome_strength, exposures, experiment contract, fatigue, evidence floors)
- commerce.strategy_learning (evidence persistence)
- commerce.conversation_outcomes (legacy compat)
- commerce.opportunity_evidence (maturity p353b.v1, 168h window)
- commerce.opportunity_ledger (durable decision snapshots)
- commerce.opportunity_validation (historical validator)
- P3.5.6 offline advisory prototype (purchase-given-sent-exposure baseline;
  intentionally not imported here to preserve the production import barrier —
  floors below mirror it with provenance, parity-checked by tests)
- commerce.optimizer_readiness (readiness floors)
- commerce.production_control (canary/health/rollback mechanics)
- commerce.opportunity_ranking.RANKING_POLICY_VERSION (ranking policy only)
- core.telemetry.GenerationTelemetry (observational only)
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Reuse canonical taxonomy + weights (do not redefine).
from commerce.adaptive_optimization import (
    CanonicalOutcome,
)

# Reuse maturity policy (do not create a second maturity clock).
from commerce.opportunity_evidence import (
    MATURITY_POLICY_VERSION,
    MATURITY_WINDOW_HOURS,
    QUALITY_FULL,
)

# Reuse ranking policy version (strategy version is separate).
try:
    from commerce.opportunity_ranking import RANKING_POLICY_VERSION
except Exception:  # pragma: no cover - fail-safe default
    RANKING_POLICY_VERSION = "v1"

# Reuse evidence floors (do not optimize on tiny samples).
try:
    from commerce.adaptive_optimization import (
        MIN_EVIDENCE_CREATOR,
        MIN_EVIDENCE_CREATOR_TOPIC,
        MIN_EVIDENCE_FAN,
        MIN_EVIDENCE_FAN_TOPIC,
    )
except Exception:  # pragma: no cover
    MIN_EVIDENCE_FAN = 5
    MIN_EVIDENCE_FAN_TOPIC = 5
    MIN_EVIDENCE_CREATOR = 10
    MIN_EVIDENCE_CREATOR_TOPIC = 10

# Prototype-floor mirrors (restated, never imported, to preserve the
# P3.5.6 production import barrier which forbids any commerce/*.py file
# from referencing that prototype module; tests assert parity).
# Mirrors P3.5.6 MIN_TRAIN_TOTAL/POSITIVE/NEGATIVE (6/2/2). If the prototype
# ever changes them, update here explicitly — never silently.
_OFFLINE_MIN_TOTAL = 6
_OFFLINE_MIN_POS = 2
_OFFLINE_MIN_NEG = 2


# ── Version identities ────────────────────────────────────────────────────

#: Strategy version is distinct from experiment ID, variant ID, config
#: version, and ranking policy version. Bump only with an explicit tested
#: strategy-selection change.
STRATEGY_VERSION = "strategy.v1"

#: Config identity for historical records that predate version stamping.
#: Forward events should carry a real computed version; this value exists
#: only so legacy rows are explicitly marked rather than silently invented.
LEGACY_UNVERSIONED = "unversioned-legacy"
UNKNOWN_CONFIG_VERSION = "unknown"

#: Phase 10 feature/artifact identity.
PHASE10_VERSION = "phase10.v1"
RECOMMENDATION_SCHEMA_VERSION = "phase10.recommendation.v1"
AGGREGATE_SCHEMA_VERSION = "phase10.aggregate.v1"

#: Optimizer identity used to prove the optimizer never approves itself.
OPTIMIZER_IDENTITY = "phase10-offline-optimizer"


# ── Outcome namespaces (explicit separation) ──────────────────────────────


class RelationshipOutcome(str, enum.Enum):
    """Relationship / conversation outcomes. Commerce-agnostic by design.

    A purchase may be a commerce success without being a relationship
    success; a positive relationship outcome may occur without commerce.
    """

    CONTINUED = "continued"
    DEEPENED = "deepened"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    WITHDREW = "withdrew"
    BOUNDARY = "boundary"
    RETURNED = "returned"
    TOPIC_CONTINUED = "topic_continued"
    CONTENT_INTEREST_CONTINUED = "content_interest_continued"
    NO_SIGNAL = "no_signal"


class CommerceOutcome(str, enum.Enum):
    """Commerce outcomes. Relationship-agnostic by design."""

    OPPORTUNITY_PRESENTED = "opportunity_presented"
    CLICKED = "clicked"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PURCHASED = "purchased"
    NO_PURCHASE = "no_purchase"
    COMMERCIAL_NEGATIVE = "commercial_negative"
    PROCESS_NEGATIVE = "process_negative"
    CENSORED = "censored"
    UNAVAILABLE = "unavailable"


class AttributionStatus(str, enum.Enum):
    ATTRIBUTED = "attributed"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    CENSORED = "censored"
    UNATTRIBUTED = "unattributed"
    IMMATURE = "immature"


# Canonical -> namespace mapping. Deliberately conservative: purchase-derived
# labels NEVER map to relationship success (Phase 10 fence §20).
_CANONICAL_TO_RELATIONSHIP: dict[str, RelationshipOutcome | None] = {
    CanonicalOutcome.POSITIVE_ENGAGEMENT.value: RelationshipOutcome.CONTINUED,
    CanonicalOutcome.TOPIC_CONTINUATION.value: RelationshipOutcome.TOPIC_CONTINUED,
    CanonicalOutcome.INTEREST_INCREASE.value: RelationshipOutcome.DEEPENED,
    CanonicalOutcome.DESIRE_INCREASE.value: RelationshipOutcome.DEEPENED,
    CanonicalOutcome.DESIRE_DECREASE.value: RelationshipOutcome.NEGATIVE,
    CanonicalOutcome.QUESTION_ANSWERED.value: RelationshipOutcome.CONTINUED,
    CanonicalOutcome.OPEN_LOOP_RESOLVED.value: RelationshipOutcome.CONTINUED,
    CanonicalOutcome.PREFERENCE_LEARNED.value: RelationshipOutcome.DEEPENED,
    CanonicalOutcome.OBJECTION.value: RelationshipOutcome.NEGATIVE,
    CanonicalOutcome.OBJECTION_RESOLVED.value: RelationshipOutcome.DEEPENED,
    CanonicalOutcome.OFFER_REQUEST.value: None,  # commerce signal, not relationship
    CanonicalOutcome.PURCHASE.value: None,  # FENCE: purchase is NOT relationship success
    CanonicalOutcome.AFTERCARE_ENGAGEMENT.value: RelationshipOutcome.RETURNED,
    CanonicalOutcome.REPEAT_PURCHASE.value: None,  # FENCE: same as purchase
    CanonicalOutcome.REJECTION.value: RelationshipOutcome.WITHDREW,
    CanonicalOutcome.COOLDOWN.value: RelationshipOutcome.WITHDREW,
    CanonicalOutcome.HANDOFF.value: RelationshipOutcome.BOUNDARY,
    CanonicalOutcome.CONVERSATION_END.value: RelationshipOutcome.NEUTRAL,
    CanonicalOutcome.NO_SIGNAL.value: RelationshipOutcome.NO_SIGNAL,
}

_CANONICAL_TO_COMMERCE: dict[str, CommerceOutcome | None] = {
    CanonicalOutcome.OFFER_REQUEST.value: CommerceOutcome.OPPORTUNITY_PRESENTED,
    CanonicalOutcome.PURCHASE.value: CommerceOutcome.PURCHASED,
    CanonicalOutcome.REPEAT_PURCHASE.value: CommerceOutcome.PURCHASED,
    CanonicalOutcome.REJECTION.value: CommerceOutcome.DECLINED,
    CanonicalOutcome.OBJECTION.value: CommerceOutcome.NO_PURCHASE,
    CanonicalOutcome.OBJECTION_RESOLVED.value: CommerceOutcome.NO_PURCHASE,
    CanonicalOutcome.COOLDOWN.value: CommerceOutcome.CENSORED,
    CanonicalOutcome.HANDOFF.value: CommerceOutcome.CENSORED,
    CanonicalOutcome.CONVERSATION_END.value: CommerceOutcome.NO_PURCHASE,
    CanonicalOutcome.NO_SIGNAL.value: CommerceOutcome.CENSORED,
    CanonicalOutcome.POSITIVE_ENGAGEMENT.value: None,  # FENCE: engagement != conversion
    CanonicalOutcome.TOPIC_CONTINUATION.value: None,
    CanonicalOutcome.INTEREST_INCREASE.value: None,
    CanonicalOutcome.DESIRE_INCREASE.value: None,
    CanonicalOutcome.AFTERCARE_ENGAGEMENT.value: None,
}


def canonical_to_relationship(outcome: str | CanonicalOutcome) -> RelationshipOutcome | None:
    """Map canonical outcome to relationship namespace.

    Returns None when the canonical outcome carries no relationship meaning
    (notably PURCHASE / REPEAT_PURCHASE / OFFER_REQUEST). Callers must treat
    None as "no relationship label", never as neutral/negative.
    """
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    return _CANONICAL_TO_RELATIONSHIP.get(key)


def canonical_to_commerce(outcome: str | CanonicalOutcome) -> CommerceOutcome | None:
    """Map canonical outcome to commerce namespace.

    Returns None when the canonical outcome carries no commerce meaning
    (notably pure engagement signals). Callers must treat None as
    "no commerce label", never as conversion.
    """
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    return _CANONICAL_TO_COMMERCE.get(key)


def purchase_is_not_relationship_success(outcome: str | CanonicalOutcome) -> bool:
    """Fence helper (§20): purchase must never count as relationship success."""
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    return key in (CanonicalOutcome.PURCHASE.value, CanonicalOutcome.REPEAT_PURCHASE.value)


def engagement_is_not_commerce(outcome: str | CanonicalOutcome) -> bool:
    """Fence helper (§21): engagement alone must never authorize commerce."""
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    return key in (
        CanonicalOutcome.POSITIVE_ENGAGEMENT.value,
        CanonicalOutcome.TOPIC_CONTINUATION.value,
        CanonicalOutcome.INTEREST_INCREASE.value,
        CanonicalOutcome.DESIRE_INCREASE.value,
        CanonicalOutcome.AFTERCARE_ENGAGEMENT.value,
    )


def classify_relationship_from_behavior(
    *,
    user_continued_topic: bool = False,
    user_returned: bool = False,
    positive_signal: bool = False,
    negative_signal: bool = False,
    withdrew: bool = False,
    boundary: bool = False,
    content_interest_continued: bool = False,
    no_signal: bool = False,
) -> RelationshipOutcome:
    """Deterministic relationship outcome from observable behavior only.

    Priority: boundary > withdrew > negative > deepened/continued > neutral.
    No LLM prose, no purchase input, no intimacy inference.
    """
    if boundary:
        return RelationshipOutcome.BOUNDARY
    if withdrew:
        return RelationshipOutcome.WITHDREW
    if negative_signal:
        return RelationshipOutcome.NEGATIVE
    if user_returned:
        return RelationshipOutcome.RETURNED
    if content_interest_continued:
        return RelationshipOutcome.CONTENT_INTEREST_CONTINUED
    if user_continued_topic:
        return RelationshipOutcome.TOPIC_CONTINUED
    if positive_signal:
        return RelationshipOutcome.DEEPENED
    if no_signal:
        return RelationshipOutcome.NO_SIGNAL
    return RelationshipOutcome.NEUTRAL


def classify_commerce_from_evidence(
    *,
    opportunity_presented: bool = False,
    clicked: bool = False,
    purchased: bool = False,
    declined: bool = False,
    expired: bool = False,
    revoked: bool = False,
    process_failed: bool = False,
    censored: bool = False,
    has_transaction_evidence: bool = False,
) -> CommerceOutcome:
    """Deterministic commerce outcome from durable evidence only.

    Purchase requires transaction evidence (DropFans authority). Without it,
    a purchase claim is UNAVAILABLE, never assumed.
    """
    if censored:
        return CommerceOutcome.CENSORED
    if purchased:
        if has_transaction_evidence:
            return CommerceOutcome.PURCHASED
        return CommerceOutcome.UNAVAILABLE
    if declined:
        return CommerceOutcome.DECLINED
    if expired:
        return CommerceOutcome.EXPIRED
    if revoked:
        return CommerceOutcome.REVOKED
    if process_failed:
        return CommerceOutcome.PROCESS_NEGATIVE
    if clicked:
        return CommerceOutcome.CLICKED
    if opportunity_presented:
        return CommerceOutcome.OPPORTUNITY_PRESENTED
    return CommerceOutcome.NO_PURCHASE


# ── Configuration versioning (immutable, deterministic) ───────────────────


def _canonical_json(payload: dict[str, Any]) -> str:
    def _clean(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _clean(v[k]) for k in sorted(v.keys())}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        if isinstance(v, enum.Enum):
            return v.value
        return v

    return json.dumps(_clean(payload), sort_keys=True, separators=(",", ":"))


def compute_config_version(payload: dict[str, Any]) -> str:
    """Deterministic config version from effective deterministic config only.

    Hashes canonical serialization of the payload. Callers must NOT include
    timestamps, random values, raw messages, or ephemeral state.
    """
    raw = _canonical_json(payload).encode("utf-8")
    return "cfg_" + hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class Phase10Config:
    """Immutable versioned configuration (append-only history)."""

    config_version: str
    payload: tuple[tuple[str, str], ...]
    effective_from: str
    approval_identity: str
    created_at: str
    creator_scope: int | None = None
    parent_version: str | None = None
    status: str = "active"

    def payload_dict(self) -> dict[str, Any]:
        return dict(self.payload)


# In-memory append-only history (bounded). Durable persistence, if needed,
# lives in user_profiles JSONB via helpers below; runtime never mutates.
_CONFIG_HISTORY: dict[str, Phase10Config] = {}
_CONFIG_ACTIVE: dict[str, str] = {}  # scope_key -> config_version
_CONFIG_MAX = 100


def _scope_key(creator_scope: int | None) -> str:
    return f"creator:{int(creator_scope)}" if creator_scope is not None else "global"


def register_config(
    payload: dict[str, Any],
    *,
    approval_identity: str,
    creator_scope: int | None = None,
    effective_from: str | None = None,
    parent_version: str | None = None,
    status: str = "active",
) -> Phase10Config:
    """Create a NEW immutable config version (never mutates an existing one)."""
    if not approval_identity or not str(approval_identity).strip():
        raise ValueError("approval_identity is required")
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload is required")
    version = compute_config_version(payload)
    now = datetime.now(UTC).isoformat()
    cfg = Phase10Config(
        config_version=version,
        payload=tuple(sorted((str(k), str(v)) for k, v in payload.items())),
        effective_from=effective_from or now,
        approval_identity=str(approval_identity),
        created_at=now,
        creator_scope=creator_scope,
        parent_version=parent_version,
        status=status,
    )
    _CONFIG_HISTORY[version] = cfg
    if len(_CONFIG_HISTORY) > _CONFIG_MAX:
        # Prune oldest by created_at (history is audit; in-memory bound only).
        oldest = sorted(_CONFIG_HISTORY.values(), key=lambda c: c.created_at)[:-_CONFIG_MAX]
        for c in oldest:
            _CONFIG_HISTORY.pop(c.config_version, None)
    return cfg


def get_config(version: str) -> Phase10Config | None:
    return _CONFIG_HISTORY.get(version)


def set_active_config(version: str, *, creator_scope: int | None = None) -> None:
    """Point activation at an existing version (approval required upstream).

    This helper records activation intent in-memory; real production
    activation flows through production_control canary/health mechanics
    (see request_activation / rollback_to). The optimizer must never call
    this directly — only an approved process may.
    """
    if version not in _CONFIG_HISTORY:
        raise ValueError("unknown config version")
    _CONFIG_ACTIVE[_scope_key(creator_scope)] = version


def get_active_config_version(*, creator_scope: int | None = None) -> str:
    return _CONFIG_ACTIVE.get(_scope_key(creator_scope), LEGACY_UNVERSIONED)


def resolve_runtime_config(*, creator_scope: int | None = None) -> Phase10Config | None:
    """Stable per-turn snapshot. Fail-safe: None means use safe defaults."""
    try:
        version = get_active_config_version(creator_scope=creator_scope)
        if version in (LEGACY_UNVERSIONED, UNKNOWN_CONFIG_VERSION):
            return None
        return _CONFIG_HISTORY.get(version)
    except Exception:
        return None


def list_config_history() -> list[Phase10Config]:
    return sorted(_CONFIG_HISTORY.values(), key=lambda c: c.created_at)


def clear_config_history() -> None:
    _CONFIG_HISTORY.clear()
    _CONFIG_ACTIVE.clear()


def stamp_decision_with_version(
    decision: Any, *, config_version: str | None = None, creator_scope: int | None = None
) -> Any:
    """Stamp a CommerceDecision (or mapping) with its config version.

    Additive only: sets metadata['config_version'] and metadata
    ['strategy_version'] when absent. Never overwrites an existing stamp.
    Fail-open: returns the original object on any error.
    """
    try:
        version = config_version or get_active_config_version(creator_scope=creator_scope)
        if not version:
            version = LEGACY_UNVERSIONED
        meta = getattr(decision, "metadata", None)
        if isinstance(meta, dict):
            if "config_version" not in meta:
                try:
                    object.__setattr__  # noqa: B018 - touch for frozen check
                    meta["config_version"] = version
                except Exception:
                    meta["config_version"] = version
            if "strategy_version" not in meta:
                meta["strategy_version"] = STRATEGY_VERSION
            if "ranking_policy_version" not in meta:
                meta["ranking_policy_version"] = RANKING_POLICY_VERSION
            return decision
        if isinstance(decision, dict):
            out = dict(decision)
            out.setdefault("config_version", version)
            out.setdefault("strategy_version", STRATEGY_VERSION)
            out.setdefault("ranking_policy_version", RANKING_POLICY_VERSION)
            return out
        return decision
    except Exception:
        return decision


def build_strategy_attribution(
    *,
    strategy_family: str | None,
    strategy_version: str = STRATEGY_VERSION,
    source: str | None = None,
    config_version: str | None = None,
    experiment_id: str | None = None,
    variant: str | None = None,
    creator_scope: int | None = None,
) -> dict[str, Any]:
    """Attributable strategy selection record (separate concepts preserved)."""
    return {
        "strategy_family": strategy_family,
        "strategy_version": strategy_version or STRATEGY_VERSION,
        "source": source,
        "config_version": config_version or get_active_config_version(creator_scope=creator_scope),
        "experiment_id": experiment_id,
        "variant": variant,
        "ranking_policy_version": RANKING_POLICY_VERSION,
    }


# Experiment guard: reuse adaptive contract + Phase 10 forbidden fields.
_PHASE10_FORBIDDEN_SUBSTRINGS = (
    "price",
    "purchase",
    "eligib",
    "seal",
    "execut",
    "safety",
    "boundary",
    "consent",
    "flag",
    "rollout",
    "auth",
)


def validate_experiment_change(proposed_change: dict[str, Any]) -> tuple[bool, str]:
    """Reject experiments that mutate forbidden commerce/safety fields."""
    try:
        from commerce.adaptive_optimization import Experiment as _Exp
        from commerce.adaptive_optimization import experiment_safe_to_apply

        ok, reason = experiment_safe_to_apply(
            _Exp(experiment_id="phase10-check", creator_id=1, strategy_family="check"),
            dict(proposed_change or {}),
        )
        if not ok:
            return False, reason
    except Exception:
        pass
    for key in proposed_change or {}:
        low = str(key).lower()
        if any(sub in low for sub in _PHASE10_FORBIDDEN_SUBSTRINGS):
            return False, f"forbidden_change:{key}"
    return True, "safe"


# ── Generation-level attribution (conservative, joinable) ─────────────────


@dataclass(frozen=True)
class ExposureRef:
    creator_id: int
    user_id: int
    generation_id: str
    strategy_family: str | None = None
    strategy_version: str = STRATEGY_VERSION
    config_version: str = LEGACY_UNVERSIONED
    experiment_id: str | None = None
    variant: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class AttributionResult:
    attributed: bool
    status: str  # AttributionStatus value
    creator_id: int | None
    user_id: int | None
    generation_id: str | None
    exposure: dict[str, Any] | None
    relationship_outcome: str | None
    commerce_outcome: str | None
    reason: str


def _require_positive_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def attribute_generation_outcome(
    *,
    creator_id: Any,
    user_id: Any,
    generation_id: str | None,
    exposure: dict[str, Any] | None,
    relationship_outcome: str | RelationshipOutcome | None = None,
    commerce_outcome: str | CommerceOutcome | None = None,
    maturity_state: str | None = None,
    has_transaction_evidence: bool = False,
) -> AttributionResult:
    """Conservative exposure -> outcome join on (creator_id, generation_id).

    Rules: attributable to a known generation/exposure where required,
    creator-scoped, user-scoped, deterministic, maturity-aware. If
    attribution cannot be established, DO NOT GUESS — return missing /
    unavailable / censored / unattributed. Missing evidence never becomes
    a negative outcome. Purchase without transaction evidence is
    UNAVAILABLE, never a positive.
    """
    try:
        cid = _require_positive_int("creator_id", creator_id)
        uid = _require_positive_int("user_id", user_id)
    except ValueError:
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.MISSING.value,
            creator_id=None,
            user_id=None,
            generation_id=generation_id,
            exposure=None,
            relationship_outcome=None,
            commerce_outcome=None,
            reason="missing_creator_or_user_scope",
        )
    if not generation_id or not str(generation_id).strip():
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.MISSING.value,
            creator_id=cid,
            user_id=uid,
            generation_id=None,
            exposure=None,
            relationship_outcome=None,
            commerce_outcome=None,
            reason="missing_generation_id",
        )
    gid = str(generation_id)
    if not isinstance(exposure, dict) or not exposure:
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.UNAVAILABLE.value,
            creator_id=cid,
            user_id=uid,
            generation_id=gid,
            exposure=None,
            relationship_outcome=None,
            commerce_outcome=None,
            reason="no_exposure_for_generation",
        )
    # Scope check: exposure must belong to the same creator+user.
    try:
        exp_cid = exposure.get("creator_id")
        exp_uid = exposure.get("user_id")
        exp_gid = exposure.get("generation_id")
        if exp_cid is not None and int(exp_cid) != int(cid):
            return AttributionResult(
                attributed=False,
                status=AttributionStatus.UNATTRIBUTED.value,
                creator_id=cid,
                user_id=uid,
                generation_id=gid,
                exposure=None,
                relationship_outcome=None,
                commerce_outcome=None,
                reason="creator_scope_mismatch",
            )
        if exp_uid is not None and int(exp_uid) != int(uid):
            return AttributionResult(
                attributed=False,
                status=AttributionStatus.UNATTRIBUTED.value,
                creator_id=cid,
                user_id=uid,
                generation_id=gid,
                exposure=None,
                relationship_outcome=None,
                commerce_outcome=None,
                reason="user_scope_mismatch",
            )
        if exp_gid is not None and str(exp_gid) != gid:
            return AttributionResult(
                attributed=False,
                status=AttributionStatus.UNATTRIBUTED.value,
                creator_id=cid,
                user_id=uid,
                generation_id=gid,
                exposure=None,
                relationship_outcome=None,
                commerce_outcome=None,
                reason="generation_id_mismatch",
            )
    except (ValueError, TypeError):
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.UNATTRIBUTED.value,
            creator_id=cid,
            user_id=uid,
            generation_id=gid,
            exposure=None,
            relationship_outcome=None,
            commerce_outcome=None,
            reason="scope_parse_error",
        )
    # Maturity: immature observations are never final labels.
    if maturity_state is not None and str(maturity_state).upper() == "IMMATURE":
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.IMMATURE.value,
            creator_id=cid,
            user_id=uid,
            generation_id=gid,
            exposure=dict(exposure),
            relationship_outcome=None,
            commerce_outcome=None,
            reason="immature_outcome_censored",
        )
    rel = (
        relationship_outcome.value
        if isinstance(relationship_outcome, enum.Enum)
        else (str(relationship_outcome) if relationship_outcome is not None else None)
    )
    com = (
        commerce_outcome.value
        if isinstance(commerce_outcome, enum.Enum)
        else (str(commerce_outcome) if commerce_outcome is not None else None)
    )
    # Fence: purchase commerce label without transaction evidence is unavailable.
    if com == CommerceOutcome.PURCHASED.value and not has_transaction_evidence:
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.UNAVAILABLE.value,
            creator_id=cid,
            user_id=uid,
            generation_id=gid,
            exposure=dict(exposure),
            relationship_outcome=rel,
            commerce_outcome=None,
            reason="purchase_without_transaction_evidence",
        )
    if rel is None and com is None:
        return AttributionResult(
            attributed=False,
            status=AttributionStatus.CENSORED.value,
            creator_id=cid,
            user_id=uid,
            generation_id=gid,
            exposure=dict(exposure),
            relationship_outcome=None,
            commerce_outcome=None,
            reason="no_mature_outcome",
        )
    return AttributionResult(
        attributed=True,
        status=AttributionStatus.ATTRIBUTED.value,
        creator_id=cid,
        user_id=uid,
        generation_id=gid,
        exposure=dict(exposure),
        relationship_outcome=rel,
        commerce_outcome=com,
        reason="attributed_by_generation_id",
    )


def find_exposure_for_generation(
    exposures: list[dict[str, Any]] | None,
    *,
    creator_id: int,
    user_id: int,
    generation_id: str,
) -> dict[str, Any] | None:
    """Exact join on (creator_id, user_id, generation_id). No fuzzy fallback."""
    if not exposures or not generation_id:
        return None
    for exp in exposures:
        try:
            if not isinstance(exp, dict):
                continue
            if str(exp.get("generation_id")) != str(generation_id):
                continue
            if exp.get("creator_id") is not None and int(exp.get("creator_id")) != int(creator_id):
                continue
            if exp.get("user_id") is not None and int(exp.get("user_id")) != int(user_id):
                continue
            return exp
        except (ValueError, TypeError):
            continue
    return None


# ── Post-response outcome capture (observable behavior, PII-minimized) ─────


@dataclass(frozen=True)
class ObservedOutcome:
    relationship: str | None
    commerce: str | None
    reason_codes: tuple[str, ...]
    has_transaction_evidence: bool = False


def capture_post_response_outcome(
    *,
    user_continued_topic: bool = False,
    user_returned: bool = False,
    positive_signal: bool = False,
    negative_signal: bool = False,
    withdrew: bool = False,
    boundary: bool = False,
    content_interest_continued: bool = False,
    purchase_confirmed_by_webhook: bool = False,
    transaction_id: str | None = None,
    offer_presented: bool = False,
    clicked: bool = False,
    declined: bool = False,
    expired: bool = False,
    revoked: bool = False,
) -> ObservedOutcome:
    """Smallest required post-response capture from observable behavior.

    Uses subsequent observable behavior, not LLM interpretation. No durable
    intimacy facts from a single signal; no raw prose as labels.
    """
    rel = classify_relationship_from_behavior(
        user_continued_topic=user_continued_topic,
        user_returned=user_returned,
        positive_signal=positive_signal,
        negative_signal=negative_signal,
        withdrew=withdrew,
        boundary=boundary,
        content_interest_continued=content_interest_continued,
        no_signal=not any(
            [
                user_continued_topic,
                user_returned,
                positive_signal,
                negative_signal,
                withdrew,
                boundary,
                content_interest_continued,
            ]
        ),
    )
    has_txn = bool(purchase_confirmed_by_webhook and transaction_id and str(transaction_id).strip())
    com = classify_commerce_from_evidence(
        opportunity_presented=offer_presented,
        clicked=clicked,
        purchased=purchase_confirmed_by_webhook,
        declined=declined,
        expired=expired,
        revoked=revoked,
        has_transaction_evidence=has_txn,
    )
    codes: list[str] = []
    if boundary:
        codes.append("boundary_observed")
    if withdrew:
        codes.append("withdrawal_observed")
    if purchase_confirmed_by_webhook:
        codes.append("purchase_webhook" if has_txn else "purchase_claim_without_transaction")
    if user_continued_topic:
        codes.append("topic_continued")
    if user_returned:
        codes.append("user_returned")
    return ObservedOutcome(
        relationship=rel.value,
        commerce=com.value,
        reason_codes=tuple(codes),
        has_transaction_evidence=has_txn,
    )


# ── Telemetry helpers (PII-minimized, fail-open) ───────────────────────────

PHASE10_TELEMETRY_FIELDS = (
    "config_version",
    "strategy_version",
    "ranking_policy_version",
    "experiment_id",
    "experiment_variant",
    "relationship_outcome",
    "commerce_outcome",
    "attribution_status",
    "maturity_policy_version",
    "maturity_state",
    "generation_creator_scope",
    "calibration_available",
)

_BOUNDED_CATEGORICAL_ALLOW = frozenset(
    {
        "continued",
        "deepened",
        "neutral",
        "negative",
        "withdrew",
        "boundary",
        "returned",
        "topic_continued",
        "content_interest_continued",
        "no_signal",
        "opportunity_presented",
        "clicked",
        "declined",
        "expired",
        "revoked",
        "purchased",
        "no_purchase",
        "commercial_negative",
        "process_negative",
        "censored",
        "unavailable",
        "attributed",
        "missing",
        "unattributed",
        "immature",
        "mature",
        "CONTROL",
        "EXPERIMENT",
    }
)


def _bounded(value: Any, *, max_len: int = 64) -> Any:
    if value is None:
        return None
    s = value.value if isinstance(value, enum.Enum) else str(value)
    s = s.strip()[:max_len]
    return s or None


def apply_phase10_telemetry(
    telemetry: Any,
    *,
    config_version: str | None = None,
    strategy_version: str = STRATEGY_VERSION,
    experiment_id: str | None = None,
    experiment_variant: str | None = None,
    relationship_outcome: str | None = None,
    commerce_outcome: str | None = None,
    attribution_status: str | None = None,
    maturity_state: str | None = None,
    creator_scope: int | None = None,
) -> None:
    """Stamp the minimum Phase 10 attribution fields. Fail-open (never raises).

    Bounded categorical values only. No raw prose, no message text.
    """
    try:
        mapping = {
            "config_version": _bounded(
                config_version or get_active_config_version(creator_scope=creator_scope)
            ),
            "strategy_version": _bounded(strategy_version or STRATEGY_VERSION),
            "ranking_policy_version": _bounded(RANKING_POLICY_VERSION),
            "experiment_id": _bounded(experiment_id),
            "experiment_variant": _bounded(experiment_variant),
            "relationship_outcome": _bounded(relationship_outcome),
            "commerce_outcome": _bounded(commerce_outcome),
            "attribution_status": _bounded(attribution_status),
            "maturity_policy_version": _bounded(MATURITY_POLICY_VERSION),
            "maturity_state": _bounded(maturity_state),
            "generation_creator_scope": int(creator_scope) if creator_scope is not None else None,
            "calibration_available": False,
        }
        for key, value in mapping.items():
            try:
                setattr(telemetry, key, value)
            except Exception:
                continue
        # Also mirror into dict-style telemetry when supported.
        try:
            as_dict = getattr(telemetry, "__dict__", None)
            if isinstance(as_dict, dict):
                for key, value in mapping.items():
                    as_dict.setdefault(key, value)
        except Exception:
            pass
    except Exception:
        return


def phase10_telemetry_dict(
    *,
    config_version: str | None = None,
    strategy_version: str = STRATEGY_VERSION,
    experiment_id: str | None = None,
    experiment_variant: str | None = None,
    relationship_outcome: str | None = None,
    commerce_outcome: str | None = None,
    attribution_status: str | None = None,
    maturity_state: str | None = None,
    creator_scope: int | None = None,
) -> dict[str, Any]:
    """Pure dict form for persistence layers (PII-minimized)."""
    return {
        "config_version": _bounded(config_version or LEGACY_UNVERSIONED),
        "strategy_version": _bounded(strategy_version or STRATEGY_VERSION),
        "ranking_policy_version": _bounded(RANKING_POLICY_VERSION),
        "experiment_id": _bounded(experiment_id),
        "experiment_variant": _bounded(experiment_variant),
        "relationship_outcome": _bounded(relationship_outcome),
        "commerce_outcome": _bounded(commerce_outcome),
        "attribution_status": _bounded(attribution_status),
        "maturity_policy_version": _bounded(MATURITY_POLICY_VERSION),
        "maturity_state": _bounded(maturity_state),
        "generation_creator_scope": int(creator_scope) if creator_scope is not None else None,
    }


# ── Offline aggregation (read-only, maturity-aware, creator-scoped) ────────


@dataclass(frozen=True)
class Phase10EvidenceRow:
    """One frozen historical observation (in-memory, never persisted here)."""

    creator_id: int
    user_id: int
    generation_id: str
    strategy_family: str | None
    strategy_version: str
    config_version: str
    experiment_id: str | None
    variant: str | None
    relationship_outcome: str | None
    commerce_outcome: str | None
    maturity_state: str  # MATURE | IMMATURE
    evidence_quality: str  # FULL | PARTIAL | UNATTRIBUTED | UNAVAILABLE
    has_transaction_evidence: bool = False
    evaluated_at: str | None = None


@dataclass(frozen=True)
class Phase10Aggregate:
    creator_id: int
    schema_version: str
    maturity_policy_version: str
    n_exposures: int
    n_mature: int
    n_attributed: int
    excluded_counts: tuple[tuple[str, int], ...]
    relationship_metrics: tuple[tuple[str, float], ...]
    commerce_metrics: tuple[tuple[str, float], ...]
    strategy_breakdown: tuple[tuple[str, int], ...]
    calibration: tuple[tuple[str, float | int | None], ...]
    sample_sufficient: bool
    abstain_reason: str | None


def _brier(probabilities: list[float], labels: list[int]) -> float | None:
    if not probabilities or len(probabilities) != len(labels):
        return None
    try:
        return sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(labels)
    except Exception:
        return None


def aggregate_offline(
    *,
    creator_id: int,
    rows: list[Phase10EvidenceRow] | tuple[Phase10EvidenceRow, ...],
    min_total: int = _OFFLINE_MIN_TOTAL,
    min_positive: int = 1,
    min_negative: int = 1,
    predicted_probabilities: list[float] | None = None,
) -> Phase10Aggregate:
    """Offline aggregation: read-only, creator-scoped, maturity-enforcing.

    - Enforces creator isolation (cross-creator rows raise, fail closed).
    - Trains/aggregates only on mature, attributed rows with an outcome in
      the relevant namespace. Immature / censored / unavailable rows are
      counted in excluded_counts, never pooled as negatives.
    - Produces SEPARATE relationship and commerce metrics (never a single
      reward). Purchase never enters relationship metrics.
    - Abstains (sample_sufficient=False) when floors are not met.
    - Never modifies runtime config, flags, commerce, prices, eligibility.
    """
    cid = _require_positive_int("creator_id", creator_id)
    rows = list(rows or [])
    excluded: dict[str, int] = {}
    mature_attributed: list[Phase10EvidenceRow] = []
    for row in rows:
        try:
            if int(row.creator_id) != int(cid):
                raise ValueError("cross-creator bundle: creator isolation cannot be guaranteed")
        except ValueError:
            raise
        except Exception:
            excluded["UNAVAILABLE"] = excluded.get("UNAVAILABLE", 0) + 1
            continue
        if str(getattr(row, "maturity_state", "")).upper() != "MATURE":
            excluded["IMMATURE_CENSORED"] = excluded.get("IMMATURE_CENSORED", 0) + 1
            continue
        if str(getattr(row, "evidence_quality", "")) != QUALITY_FULL:
            key = str(getattr(row, "evidence_quality", "UNAVAILABLE") or "UNAVAILABLE")
            excluded[key] = excluded.get(key, 0) + 1
            continue
        if not row.generation_id:
            excluded["MISSING_GENERATION"] = excluded.get("MISSING_GENERATION", 0) + 1
            continue
        mature_attributed.append(row)

    # Separate namespaces.
    rel_events = [
        {"outcome": (r.relationship_outcome or "no_signal").lower()}
        for r in mature_attributed
        if r.relationship_outcome
    ]
    com_purchased = sum(
        1 for r in mature_attributed if r.commerce_outcome == CommerceOutcome.PURCHASED.value
    )
    com_declined = sum(
        1
        for r in mature_attributed
        if r.commerce_outcome
        in (
            CommerceOutcome.DECLINED.value,
            CommerceOutcome.EXPIRED.value,
            CommerceOutcome.COMMERCIAL_NEGATIVE.value,
        )
    )
    com_total = len([r for r in mature_attributed if r.commerce_outcome])
    # FENCE: relationship metrics never consume purchase labels.
    try:
        from commerce.adaptive_optimization import compute_relationship_metrics

        rel_metrics = (
            compute_relationship_metrics(rel_events)
            if rel_events
            else {"reply_rate": 0.0, "continuation": 0.0, "positive_rate": 0.0, "return_rate": 0.0}
        )
    except Exception:
        total = max(1, len(rel_events))
        rel_metrics = {
            "reply_rate": round(
                sum(1 for e in rel_events if e.get("outcome") not in ("no_signal",)) / total, 3
            ),
            "continuation": 0.0,
            "positive_rate": 0.0,
            "return_rate": 0.0,
        }
    com_metrics = {
        "purchase_rate": round(com_purchased / max(1, com_total), 4) if com_total else 0.0,
        "decline_rate": round(com_declined / max(1, com_total), 4) if com_total else 0.0,
        "n_commerce_labeled": float(com_total),
    }
    strat_counts: dict[str, int] = {}
    for r in mature_attributed:
        key = str(r.strategy_family or "unknown")
        strat_counts[key] = strat_counts.get(key, 0) + 1

    # Sample floors: abstain-first.
    n_total = len(mature_attributed)
    n_pos = com_purchased + sum(
        1
        for r in mature_attributed
        if (r.relationship_outcome or "")
        in (
            RelationshipOutcome.CONTINUED.value,
            RelationshipOutcome.DEEPENED.value,
            RelationshipOutcome.TOPIC_CONTINUED.value,
            RelationshipOutcome.RETURNED.value,
        )
    )
    n_neg = (
        sum(
            1
            for r in mature_attributed
            if (r.relationship_outcome or "")
            in (RelationshipOutcome.NEGATIVE.value, RelationshipOutcome.WITHDREW.value)
        )
        + com_declined
    )
    sufficient = not (n_total < min_total or n_pos < min_positive or n_neg < min_negative)
    abstain_reason = None if sufficient else "insufficient_eligible_training_evidence"

    # Calibration (diagnostic only, never runtime authority).
    labels = [
        1 if r.commerce_outcome == CommerceOutcome.PURCHASED.value else 0
        for r in mature_attributed
        if r.commerce_outcome
    ]
    brier = (
        _brier(list(predicted_probabilities or [])[: len(labels)], labels)
        if predicted_probabilities and len(predicted_probabilities) >= len(labels) and labels
        else None
    )
    mean_pred = (
        (sum(list(predicted_probabilities or [])[: len(labels)]) / max(1, len(labels)))
        if predicted_probabilities and labels
        else None
    )
    observed = (sum(labels) / max(1, len(labels))) if labels else None
    gap = abs(mean_pred - observed) if (mean_pred is not None and observed is not None) else None

    return Phase10Aggregate(
        creator_id=cid,
        schema_version=AGGREGATE_SCHEMA_VERSION,
        maturity_policy_version=MATURITY_POLICY_VERSION,
        n_exposures=len(rows),
        n_mature=len(mature_attributed),
        n_attributed=len(mature_attributed),
        excluded_counts=tuple(sorted(excluded.items())),
        relationship_metrics=tuple(sorted((k, float(v)) for k, v in rel_metrics.items())),
        commerce_metrics=tuple(sorted((k, float(v)) for k, v in com_metrics.items())),
        strategy_breakdown=tuple(sorted(strat_counts.items())),
        calibration=(
            ("brier", brier),
            ("mean_predicted", mean_pred),
            ("observed_rate", observed),
            ("calibration_gap", gap),
            ("n_scored", len(labels)),
        ),
        sample_sufficient=sufficient,
        abstain_reason=abstain_reason,
    )


# ── Recommendation artifact (versioned, bounded, non-executable) ──────────

FORBIDDEN_RECOMMENDATION_FIELDS = frozenset(
    {
        "price",
        "price_minor",
        "product_id",
        "product_selection",
        "eligibility_override",
        "sealing",
        "execution",
        "safety_override",
        "boundary_override",
        "content_selection",
        "feature_flag",
        "rollout_percent",
        "purchase_url",
    }
)

ALLOWED_OBJECTIVES = frozenset(
    {
        "relationship_continuation",
        "commerce_efficiency",
        "operational_health",
        "fatigue_reduction",
        "calibration_improvement",
    }
)


@dataclass(frozen=True)
class RecommendationArtifact:
    source_dataset_version: str
    config_version_evaluated: str
    strategy_version_evaluated: str
    recommendation_version: str
    creator_scope: int
    metric_objective: str
    proposed_threshold_delta: tuple[tuple[str, float], ...] = ()
    proposed_priority_delta: tuple[tuple[str, float], ...] = ()
    proposed_strategy_weight_delta: tuple[tuple[str, float], ...] = ()
    sample_size: int = 0
    maturity_status: str = "unknown"
    holdout_result: str | None = None
    confidence: float | None = None
    calibration_gap: float | None = None
    generated_at: str = ""
    rationale: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def _recommendation_version(*, creator_scope: int, objective: str, payload: dict[str, Any]) -> str:
    raw = _canonical_json(
        {
            "creator": int(creator_scope),
            "objective": str(objective),
            "payload": payload,
            "schema": RECOMMENDATION_SCHEMA_VERSION,
        }
    ).encode()
    return "rec_" + hashlib.sha256(raw).hexdigest()[:16]


def build_recommendation(
    *,
    creator_scope: int,
    metric_objective: str,
    aggregate: Phase10Aggregate,
    config_version_evaluated: str,
    proposed_threshold_delta: dict[str, float] | None = None,
    proposed_priority_delta: dict[str, float] | None = None,
    proposed_strategy_weight_delta: dict[str, float] | None = None,
    holdout_result: str | None = None,
    confidence: float | None = None,
    rationale: list[str] | tuple[str, ...] = (),
    reason_codes: list[str] | tuple[str, ...] = (),
) -> RecommendationArtifact:
    """Build a bounded, non-executable recommendation (never self-activating)."""
    cid = _require_positive_int("creator_scope", creator_scope)
    objective = str(metric_objective or "").strip()
    if objective not in ALLOWED_OBJECTIVES:
        raise ValueError(f"unknown metric_objective: {objective!r}")
    if int(aggregate.creator_id) != int(cid):
        raise ValueError("aggregate creator scope mismatch")
    for mapping in (
        proposed_threshold_delta,
        proposed_priority_delta,
        proposed_strategy_weight_delta,
    ):
        for key in mapping or {}:
            if str(key).lower() in FORBIDDEN_RECOMMENDATION_FIELDS or any(
                sub in str(key).lower() for sub in _PHASE10_FORBIDDEN_SUBSTRINGS
            ):
                raise ValueError(f"forbidden recommendation field: {key!r}")

    # Bound deltas to small deterministic ranges (no runaway optimization).
    def _bound(m: dict[str, float] | None) -> tuple[tuple[str, float], ...]:
        out: list[tuple[str, float]] = []
        for k, v in sorted((m or {}).items()):
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"non-numeric delta: {k!r}")
            if not math.isfinite(f):
                raise ValueError(f"non-finite delta: {k!r}")
            if abs(f) > 0.25:
                raise ValueError(f"delta out of bounds (|delta|<=0.25): {k!r}")
            out.append((str(k), f))
        return tuple(out)

    payload = {
        "threshold": dict(sorted((proposed_threshold_delta or {}).items())),
        "priority": dict(sorted((proposed_priority_delta or {}).items())),
        "weights": dict(sorted((proposed_strategy_weight_delta or {}).items())),
    }
    version = _recommendation_version(creator_scope=cid, objective=objective, payload=payload)
    cal_gap = None
    try:
        cal_gap = dict(aggregate.calibration).get("calibration_gap")
    except Exception:
        cal_gap = None
    return RecommendationArtifact(
        source_dataset_version=aggregate.schema_version,
        config_version_evaluated=config_version_evaluated,
        strategy_version_evaluated=STRATEGY_VERSION,
        recommendation_version=version,
        creator_scope=cid,
        metric_objective=objective,
        proposed_threshold_delta=_bound(proposed_threshold_delta),
        proposed_priority_delta=_bound(proposed_priority_delta),
        proposed_strategy_weight_delta=_bound(proposed_strategy_weight_delta),
        sample_size=int(aggregate.n_mature),
        maturity_status=f"policy={aggregate.maturity_policy_version} mature={aggregate.n_mature}",
        holdout_result=holdout_result,
        confidence=confidence,
        calibration_gap=float(cal_gap) if cal_gap is not None else None,
        generated_at=datetime.now(UTC).isoformat(),
        rationale=tuple(rationale or ()),
        reason_codes=tuple(reason_codes or ()),
    )


def validate_recommendation(
    recommendation: RecommendationArtifact,
    *,
    aggregate: Phase10Aggregate,
    require_holdout: bool = True,
) -> tuple[bool, str]:
    """Validate before approval: sufficient sample, mature, scoped, bounded.

    Returns (ok, reason). Failure means ABSTAIN — never approve.
    """
    try:
        if int(recommendation.creator_scope) != int(aggregate.creator_id):
            return False, "creator_scope_mismatch"
        if not aggregate.sample_sufficient:
            return False, f"insufficient_sample:{aggregate.abstain_reason}"
        if aggregate.n_mature <= 0:
            return False, "no_mature_outcomes"
        if aggregate.maturity_policy_version != MATURITY_POLICY_VERSION:
            return False, "maturity_policy_mismatch"
        if recommendation.metric_objective not in ALLOWED_OBJECTIVES:
            return False, "unknown_objective"
        for group in (
            recommendation.proposed_threshold_delta,
            recommendation.proposed_priority_delta,
            recommendation.proposed_strategy_weight_delta,
        ):
            for key, value in group:
                if str(key).lower() in FORBIDDEN_RECOMMENDATION_FIELDS or any(
                    sub in str(key).lower() for sub in _PHASE10_FORBIDDEN_SUBSTRINGS
                ):
                    return False, f"forbidden_field:{key}"
                if not math.isfinite(float(value)) or abs(float(value)) > 0.25:
                    return False, f"delta_out_of_bounds:{key}"
        if not recommendation.config_version_evaluated:
            return False, "missing_config_version_evaluated"
        if require_holdout and not recommendation.holdout_result:
            return False, "missing_holdout_result"
        if not recommendation.recommendation_version.startswith("rec_"):
            return False, "bad_recommendation_version"
        return True, "valid"
    except Exception as exc:
        return False, f"validation_error:{type(exc).__name__}"


# ── Human approval (optimizer can recommend, never approve itself) ─────────


@dataclass(frozen=True)
class ApprovalRecord:
    recommendation_version: str
    approved_config_version: str
    approver: str
    timestamp: str
    previous_version: str
    reason: str
    status: str = "approved"
    creator_scope: int | None = None


_APPROVALS: dict[str, ApprovalRecord] = {}


def approve_recommendation(
    recommendation: RecommendationArtifact,
    *,
    approver: str,
    reason: str,
    aggregate: Phase10Aggregate,
    new_payload: dict[str, Any],
    creator_scope: int | None = None,
) -> ApprovalRecord:
    """Human approval boundary: recommendation -> ACTIVE config version.

    The optimizer must never approve itself: approver == OPTIMIZER_IDENTITY
    is rejected. Validation must pass or approval is refused (abstain).
    Creates a NEW immutable config version; never mutates the old one.
    """
    if not approver or not str(approver).strip():
        raise ValueError("approver is required")
    if str(approver) == OPTIMIZER_IDENTITY:
        raise ValueError("optimizer cannot approve its own recommendation")
    ok, why = validate_recommendation(recommendation, aggregate=aggregate)
    if not ok:
        raise ValueError(f"recommendation invalid, abstaining: {why}")
    scope = int(creator_scope) if creator_scope is not None else int(recommendation.creator_scope)
    if int(recommendation.creator_scope) != int(scope):
        raise ValueError("approval creator scope mismatch")
    previous = get_active_config_version(creator_scope=scope)
    cfg = register_config(
        dict(new_payload or {}),
        approval_identity=str(approver),
        creator_scope=scope,
        parent_version=previous
        if previous not in (LEGACY_UNVERSIONED, UNKNOWN_CONFIG_VERSION)
        else None,
    )
    record = ApprovalRecord(
        recommendation_version=recommendation.recommendation_version,
        approved_config_version=cfg.config_version,
        approver=str(approver),
        timestamp=datetime.now(UTC).isoformat(),
        previous_version=previous,
        reason=str(reason or ""),
        status="approved",
        creator_scope=scope,
    )
    _APPROVALS[recommendation.recommendation_version] = record
    return record


def get_approval(recommendation_version: str) -> ApprovalRecord | None:
    return _APPROVALS.get(recommendation_version)


def clear_approvals() -> None:
    _APPROVALS.clear()


# ── Controlled activation (canary -> health -> expansion -> rollback) ───────


@dataclass(frozen=True)
class ActivationPlan:
    approval_config_version: str
    previous_version: str
    creator_scope: int | None
    stages: tuple[str, ...]
    created_at: str


def request_activation(approval: ApprovalRecord) -> ActivationPlan:
    """Build an activation plan for an APPROVED config (no side effects).

    The optimizer itself must not call activation, change rollout
    percentages, modify flags, or advance a rollout. An approved operational
    process executes the plan through production_control canary/health
    mechanics. Stages mirror production_control rollout scopes.
    """
    if not isinstance(approval, ApprovalRecord) or approval.status != "approved":
        raise ValueError("activation requires an approved record")
    if approval.approved_config_version not in _CONFIG_HISTORY:
        raise ValueError("approved config version unknown")
    return ActivationPlan(
        approval_config_version=approval.approved_config_version,
        previous_version=approval.previous_version,
        creator_scope=approval.creator_scope,
        stages=("canary-1pct", "canary-10pct", "expand-50pct", "full"),
        created_at=datetime.now(UTC).isoformat(),
    )


def execute_activation_stage(
    plan: ActivationPlan, stage: str, *, operator: str = "approved-operator"
) -> dict[str, Any]:
    """Execute one already-approved operational step via production control.

    This function is the approved operational control path (not the
    optimizer). It records activation intent and health evidence hooks;
    real traffic shaping stays inside production_control. Unknown stages
    raise (fail closed).
    """
    if stage not in plan.stages:
        raise ValueError(f"unknown activation stage: {stage!r}")
    # Point the in-memory active pointer only after explicit approval exists.
    # Rollback restores the prior version without mutating history.
    _CONFIG_ACTIVE[_scope_key(plan.creator_scope)] = plan.approval_config_version
    try:
        from commerce import production_control as _pc

        if hasattr(_pc, "record_metric"):
            _pc.record_metric(
                name="phase10_activation",
                creator_id=plan.creator_scope,
                outcome=stage,
                strategy="phase10",
            )
    except Exception:
        pass
    return {
        "stage": stage,
        "active_config_version": plan.approval_config_version,
        "previous_version": plan.previous_version,
        "operator": operator,
        "health_required": True,
    }


def rollback_to(
    previous_version: str, *, creator_scope: int | None = None, reason: str = ""
) -> dict[str, Any]:
    """Rollback restores the prior known-good version (history preserved).

    Never mutates or deletes the failed version. Records a rollback event
    with health evidence hooks. Runtime becomes attributable to the restored
    version.
    """
    scope_key = _scope_key(creator_scope)
    current = _CONFIG_ACTIVE.get(scope_key, LEGACY_UNVERSIONED)
    if previous_version not in _CONFIG_HISTORY and previous_version not in (
        LEGACY_UNVERSIONED,
        UNKNOWN_CONFIG_VERSION,
    ):
        raise ValueError("unknown rollback target")
    _CONFIG_ACTIVE[scope_key] = previous_version
    try:
        from commerce import production_control as _pc

        if hasattr(_pc, "record_metric"):
            _pc.record_metric(
                name="phase10_rollback",
                creator_id=creator_scope,
                outcome=f"{current}->{previous_version}:{reason[:80]}",
                strategy="phase10",
            )
    except Exception:
        pass
    return {
        "restored_config_version": previous_version,
        "failed_config_version": current,
        "reason": reason,
        "history_preserved": True,
    }


# ── Runtime determinism + modes ────────────────────────────────────────────


class Phase10Mode(str, enum.Enum):
    OBSERVE = "observe"
    ANALYTICS = "analytics"
    RECOMMEND = "recommend"
    APPROVED = "approved"


_PHASE10_MODE: str = Phase10Mode.OBSERVE.value


def set_phase10_mode(mode: str) -> str:
    if mode not in (m.value for m in Phase10Mode):
        raise ValueError(f"unknown Phase10 mode: {mode!r}")
    global _PHASE10_MODE
    _PHASE10_MODE = mode
    return _PHASE10_MODE


def get_phase10_mode() -> str:
    return _PHASE10_MODE


def runtime_snapshot(*, creator_scope: int | None = None) -> dict[str, Any]:
    """Stable per-turn snapshot: identical inputs produce identical decisions.

    The optimizer never mutates configuration during a turn. If lookup
    fails, fail to safe defaults (never an unversioned autonomous change).
    """
    try:
        cfg = resolve_runtime_config(creator_scope=creator_scope)
        if cfg is None:
            return {
                "config_version": LEGACY_UNVERSIONED,
                "strategy_version": STRATEGY_VERSION,
                "ranking_policy_version": RANKING_POLICY_VERSION,
                "mode": get_phase10_mode(),
            }
        return {
            "config_version": cfg.config_version,
            "strategy_version": STRATEGY_VERSION,
            "ranking_policy_version": RANKING_POLICY_VERSION,
            "mode": get_phase10_mode(),
            "effective_from": cfg.effective_from,
        }
    except Exception:
        return {
            "config_version": LEGACY_UNVERSIONED,
            "strategy_version": STRATEGY_VERSION,
            "ranking_policy_version": RANKING_POLICY_VERSION,
            "mode": Phase10Mode.OBSERVE.value,
        }


# ── Authority barrier (optimizer is never commerce authority) ──────────────


def optimizer_authority_check() -> dict[str, bool]:
    """Explicit NO for every authority the optimizer must never hold."""
    return {
        "can_execute_commerce": False,
        "can_override_eligibility": False,
        "can_alter_price": False,
        "can_alter_sealing": False,
        "can_alter_execution": False,
        "can_override_safety": False,
        "can_override_boundaries": False,
        "can_authorize_content": False,
        "can_self_activate": False,
        "can_modify_feature_flags": False,
    }


def assert_no_authority_bypass(proposed: dict[str, Any]) -> tuple[bool, str]:
    """Reject any recommendation payload that touches authority fields."""
    for key in proposed or {}:
        low = str(key).lower()
        if low in FORBIDDEN_RECOMMENDATION_FIELDS or any(
            sub in low for sub in _PHASE10_FORBIDDEN_SUBSTRINGS
        ):
            return False, f"authority_bypass_rejected:{key}"
    return True, "no_authority_bypass"


# ── LLM authority fence ────────────────────────────────────────────────────

_LLM_RAW_PREFIXES = ("llm_", "prompt_", "summary_", "prose_", "emotion_", "intimacy_")


def validate_offline_features(features: dict[str, Any]) -> tuple[bool, str]:
    """Offline optimizer operates on approved frozen typed features only.

    Rejects raw LLM prose/scores unless an explicit deterministic feature
    contract presents them as bounded categorical reason codes.
    """
    for key, value in (features or {}).items():
        low = str(key).lower()
        if low.startswith(_LLM_RAW_PREFIXES) and isinstance(value, str) and len(value) > 120:
            return False, f"raw_llm_feature_rejected:{key}"
        if "prose" in low or "raw_message" in low or "intimate" in low:
            return False, f"pii_prose_feature_rejected:{key}"
    return True, "features_approved"


# ── Documentation hook ─────────────────────────────────────────────────────

PHASE10_DOC = """Phase 10 learns bounded threshold/weight deltas from mature,
creator-scoped, generation-attributed evidence. It cannot execute commerce,
override eligibility/sealing/execution/safety/boundaries, change prices,
select content, modify flags, or self-activate. Relationship and commerce
outcomes are separate namespaces. Attribution key is (creator_id,
generation_id). Maturity policy p353b.v1 (168h). Recommendation ->
validation -> human approval -> versioned config -> canary -> health ->
activation -> rollback. Creator-isolated, fail-safe, PII-minimized."""


__all__ = [
    "AGGREGATE_SCHEMA_VERSION",
    "ALLOWED_OBJECTIVES",
    "FORBIDDEN_RECOMMENDATION_FIELDS",
    "LEGACY_UNVERSIONED",
    "MATURITY_POLICY_VERSION",
    "MATURITY_WINDOW_HOURS",
    "OPTIMIZER_IDENTITY",
    "PHASE10_DOC",
    "PHASE10_TELEMETRY_FIELDS",
    "PHASE10_VERSION",
    "RANKING_POLICY_VERSION",
    "RECOMMENDATION_SCHEMA_VERSION",
    "STRATEGY_VERSION",
    "UNKNOWN_CONFIG_VERSION",
    "ActivationPlan",
    "ApprovalRecord",
    "AttributionResult",
    "AttributionStatus",
    "CommerceOutcome",
    "ExposureRef",
    "ObservedOutcome",
    "Phase10Aggregate",
    "Phase10Config",
    "Phase10EvidenceRow",
    "Phase10Mode",
    "RecommendationArtifact",
    "RelationshipOutcome",
    "aggregate_offline",
    "apply_phase10_telemetry",
    "approve_recommendation",
    "assert_no_authority_bypass",
    "attribute_generation_outcome",
    "build_recommendation",
    "build_strategy_attribution",
    "canonical_to_commerce",
    "canonical_to_relationship",
    "capture_post_response_outcome",
    "classify_commerce_from_evidence",
    "classify_relationship_from_behavior",
    "clear_approvals",
    "clear_config_history",
    "compute_config_version",
    "engagement_is_not_commerce",
    "execute_activation_stage",
    "find_exposure_for_generation",
    "get_active_config_version",
    "get_approval",
    "get_config",
    "get_phase10_mode",
    "list_config_history",
    "optimizer_authority_check",
    "phase10_telemetry_dict",
    "purchase_is_not_relationship_success",
    "register_config",
    "request_activation",
    "resolve_runtime_config",
    "rollback_to",
    "runtime_snapshot",
    "set_active_config",
    "set_phase10_mode",
    "stamp_decision_with_version",
    "validate_experiment_change",
    "validate_offline_features",
    "validate_recommendation",
]
