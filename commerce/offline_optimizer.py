"""P3.5.6 — Offline Advisory Optimizer Prototype (offline only, no authority).

OFFLINE ONLY. This module is an isolated prototype for the defensible
learning target H — probability of mature purchase conditional on sent
exposure, at opportunity-row grain, mature labels only — plus the
auxiliary descriptive target G (agreement with deterministic v1).

What this module IS:
- A deterministic, dependency-light (stdlib-only) empirical log-odds
  baseline that estimates P(mature purchase | sent exposure) for the
  v1-selected/exposed opportunity.
- A creator-isolated, chronological, abstention-first offline harness that
  routes every recommendation through the P3.5.4B historical validator as
  an advisory diagnostic.

What this module IS NOT (explicit non-goals, never to be added here):
- NOT commerce authority. Production v1 remains authoritative for
  eligibility, ownership, provider truth, governance, deterministic
  ranking, sending/sealing/execution. This module cannot change any of
  those and MUST NOT be imported by production commerce code.
- NOT price optimization. Frozen price appears only as a historical
  pinned-offer fact (bucketed); the model never modifies, generates,
  recommends, or interpolates price or elasticity.
- NOT causal/uplift/counterfactual ranking. Only v1-selected/exposed
  candidates have observed outcomes. The model answers only:
  "Given v1 exposed this offer under these frozen conditions, what was
  the probability of mature purchase?" It never claims candidate B would
  have converted better than candidate A.
- NOT revenue/acceptance/click/conversation-quality/fatigue prediction.
- NOT an experiment system. No experiment_id, no variant_id, no
  activation of experiment infrastructure.
- NOT a persistent store. No model registry table, no prediction table,
  no feature store, no DB writes of any kind. Models live in memory.

Score semantics (the ONLY valid reading of ``probability``):
    "estimated probability of mature purchase conditional on sent exposure,
    under the historical training population."
It does NOT mean causal probability, probability under an alternative
offer, probability if price changed, expected revenue, conversation
quality, or retention. Canonical short form: conditional on sent exposure.

Counterfactual limit:
- Primary training/evaluation uses ONLY exposed v1-selected
  opportunities. Unselected candidates are never labeled negative.
- ``score_candidates_extrapolative`` exists solely as an explicitly
  labeled extrapolative/off-policy diagnostic and MUST NOT be interpreted
  as validated conversion probability for unexposed candidates.

Re-engagement (Option A — safer, adopted here):
- Opportunity rows remain the observation grain.
- Re-engagement children (``reengagement_of`` set / ``is_child``) are
  EXCLUDED from primary supervised training and from primary predictive
  metrics. They remain separate observations for descriptive/agreement
  analysis with their linkage preserved, and they never create duplicate
  revenue positives (revenue stays single-winner upstream).

Evidence quality:
- Primary cohort: FULL, mature, non-recovered, attributed, sent
  exposure, non-child.
- Recovered rows form a separate quarantined cohort (never pooled).
- PARTIAL is never pooled into FULL. UNATTRIBUTED purchases are never
  supervised positives. CENSORED/UNAVAILABLE are never negatives.
- No numerical evidence weights exist anywhere in this module.

Maturity:
- Consumes P3.5.3B semantics (``MATURITY_POLICY_VERSION == "p353b.v1"``,
  168h window). No second maturity clock is defined here.

Temporal methodology (chronological holdout; expanding-window helper
provided):
- Examples are ordered by frozen ``evaluated_at``. The trainer sees only
  rows with ``evaluated_at < train_end``. The test cohort is
  ``test_start <= evaluated_at < test_end`` with ``train_end <=
  test_start`` enforced (no future training example leaks backward).
- Test features come exclusively from the frozen ``OptimizationInput``
  (decision-time snapshot); labels come from ``as_of``-gated evidence.
- Prototype diagnostic threshold 0.5 (``PROTOTYPE_THRESHOLD``) is used
  ONLY for offline accuracy/precision/recall diagnostics. It is explicitly
  prototype-only and must never enter production authority.

Feature schema:
- ``FEATURE_SCHEMA_VERSION`` is explicit, deterministic, and immutable
  for this implementation. It is separate from ``ranking_policy_version``
  (deterministic v1 policy, segmentation only, never a learned feature)
  and from ``OPTIMIZER_VERSION`` (this prototype instance).
- ``user_id`` exists only as a join/scoping key and is NEVER a feature.
- ``creator_id`` is required for isolation and is NEVER a feature and
  NEVER pooled across creators.
- Future purchase/outcome/``outcome_at``/purchased price/transaction ID,
  current ownership/catalog/Vault/Dropfans state, salesCount, dashboard
  aggregates, LLM scores, message prose, post-decision conversation,
  provider responses, and send results never enter the feature vector.

Synthetic data:
- Because commerce_offers / fangate_transactions are empty and the
  opportunity-ledger migration is not applied in this environment, this
  module ships deterministic synthetic fixture builders (``SYNTHETIC_*``)
  clearly marked synthetic. Synthetic outcomes are NEVER production
  evidence and must never be presented as such.

Production import barrier:
- This module imports ONLY stdlib plus the advisory-safe
  ``commerce.opportunity_optimization`` (frozen input contract) and
  ``commerce.opportunity_validation`` (historical validator). It performs
  no I/O, no DB/Redis/provider/LLM calls, no sealing/execution/sending,
  and no persistence. Production commerce/worker/scheduler/execution
  paths MUST NOT import this module (enforced by test).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commerce.opportunity_optimization import (
    AdvisoryOptimizationResult,
    OptimizationInput,
    abstain_result,
)
from commerce.opportunity_validation import (
    ADVISORY_ABSTAIN,
    ADVISORY_INVALID,
    ADVISORY_NOT_ELIGIBLE,
    AGREEMENT,
    DIVERGENCE,
    INPUT_UNAVAILABLE,
    NO_V1_SELECTION,
    RECOVERED_INPUT,
    VALID,
    VALIDATION_ERROR,
    VALIDATOR_REJECTED,
    compare_with_v1,
    validate_advisory,
)

# ---------------------------------------------------------------------------
# Versions and semantics (explicit, separate concepts).
# ---------------------------------------------------------------------------

#: Immutable feature-schema identity for THIS prototype implementation.
#: Bump only with an explicit, tested schema change — never silently.
FEATURE_SCHEMA_VERSION = "p356.features.v1"

#: Actual prototype instance version. Used ONLY because this prototype
#: instance genuinely exists (offline, advisory, no authority).
OPTIMIZER_VERSION = "p356.offline.proto.v1"

#: Consumed maturity policy (defined by P3.5.3B, not redefined here).
EXPECTED_MATURITY_POLICY_VERSION = "p353b.v1"

#: The ONLY valid reading of the prototype probability.
SCORE_SEMANTICS = (
    "estimated probability of mature purchase conditional on sent exposure, "
    "under the historical training population"
)

#: Warning attached (by name/docs) to any extrapolative candidate scoring.
EXTRAPOLATIVE_WARNING = (
    "EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exposed; "
    "scores for unexposed candidates are NOT validated conversion "
    "probabilities and MUST NOT be interpreted as causal or ranking claims."
)

#: Synthetic marker for fixture builders (never production evidence).
SYNTHETIC_MARKER = "SYNTHETIC_P356_FIXTURE"

# ---------------------------------------------------------------------------
# Prototype-only configuration (diagnostics, never production gates).
# ---------------------------------------------------------------------------

#: Minimum supervised examples to train (prototype-only diagnostic floor).
#: Explicitly NOT a production gate; v1 remains authoritative regardless.
MIN_TRAIN_TOTAL = 6
MIN_TRAIN_POSITIVE = 2
MIN_TRAIN_NEGATIVE = 2

#: Diagnostic decision threshold for accuracy/precision/recall only.
#: Prototype-only; never enters production authority.
PROTOTYPE_THRESHOLD = 0.5

#: Laplace smoothing strength for the empirical log-odds baseline.
_SMOOTHING_ALPHA = 1.0

# ---------------------------------------------------------------------------
# Label taxonomy (mirrors P3.5.3B evidence semantics; consumed, not redefined).
# ---------------------------------------------------------------------------

TRAIN_LABEL_PURCHASED = "PURCHASED"
TRAIN_LABEL_DECLINED = "DECLINED"
TRAIN_LABEL_EXPIRED = "EXPIRED"

CLASS_CENSORED = "CENSORED"
CLASS_UNAVAILABLE = "UNAVAILABLE"
CLASS_PROCESS_NEGATIVE = "PROCESS_NEGATIVE"
CLASS_NO_SELECTION = "NO_SELECTION"
CLASS_NO_OPPORTUNITY = "NO_OPPORTUNITY"
CLASS_RECOVERED = "RECOVERED"
CLASS_UNATTRIBUTED = "UNATTRIBUTED"
CLASS_PARTIAL = "PARTIAL"
CLASS_REENGAGEMENT_CHILD = "REENGAGEMENT_CHILD"

PRIMARY_TRAIN_LABELS = frozenset(
    {TRAIN_LABEL_PURCHASED, TRAIN_LABEL_DECLINED, TRAIN_LABEL_EXPIRED}
)

# ---------------------------------------------------------------------------
# Feature contract (frozen-input only; see module docstring for exclusions).
# ---------------------------------------------------------------------------

FEATURE_NAMES: tuple[str, ...] = (
    "offer_type",
    "price_bucket",
    "currency",
    "family_presence",
    "vault_count_bucket",
    "drop_mapping",
    "fan_purchase_bucket",
    "fan_spend_bucket",
    "fan_recent_offer_bucket",
    "fan_rejected_bucket",
    "history_total_bucket",
    "history_declined_bucket",
    "has_active_offer",
    "lifecycle",
    "topic_presence",
    "owned_count_bucket",
)


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _require_aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _price_bucket(price_minor: int | None) -> str:
    """Bucket the FROZEN pinned offer price (historical fact only).

    Thresholds are prototype-only bucketing for the offline baseline; they
    carry no pricing authority and never recommend or interpolate price.
    """
    if price_minor is None:
        return "MISSING"
    if not isinstance(price_minor, int) or isinstance(price_minor, bool):
        return "MISSING"
    if price_minor < 1000:
        return "LOW"
    if price_minor < 3000:
        return "MID"
    return "HIGH"


# ---------------------------------------------------------------------------
# Immutable offline structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelOutcome:
    """Label-builder outcome for one opportunity row (pure classification)."""

    kind: str
    binary: int | None
    reason: str
    mature: bool
    sent_exposure: bool
    evidence_quality: str
    recovered: bool
    is_child: bool


@dataclass(frozen=True)
class TrainingExample:
    """One primary supervised observation (opportunity-row grain)."""

    creator_id: int
    opportunity_id: int
    evaluated_at: datetime
    policy_version: str | None
    definition_id: int
    definition_version: int
    features: tuple[tuple[str, str], ...]
    label: str
    binary: int
    evidence_quality: str
    recovered: bool
    reengagement_of: int | None
    is_child: bool
    feature_schema_version: str

    def __post_init__(self) -> None:
        _require_scope("creator_id", self.creator_id)
        _require_scope("opportunity_id", self.opportunity_id)
        _require_aware("evaluated_at", self.evaluated_at)
        if self.label not in PRIMARY_TRAIN_LABELS:
            raise ValueError("TrainingExample label must be primary supervised")
        if self.binary not in (0, 1):
            raise ValueError("TrainingExample binary must be 0 or 1")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("unsupported feature schema version")
        names = [name for name, _ in self.features]
        if tuple(names) != FEATURE_NAMES:
            raise ValueError("features must follow FEATURE_NAMES order exactly")


@dataclass(frozen=True)
class RowBundle:
    """One offline row: frozen input + gated evidence + ledger facts."""

    input: OptimizationInput
    evidence: dict[str, Any]
    ledger_row: dict[str, Any]


@dataclass(frozen=True)
class CreatorDataset:
    """Creator-scoped primary supervised dataset (in memory, never persisted)."""

    creator_id: int
    feature_schema_version: str
    examples: tuple[TrainingExample, ...]
    n_bundles: int
    excluded_counts: tuple[tuple[str, int], ...]
    as_of: datetime | None = None


@dataclass(frozen=True)
class OfflineModel:
    """Deterministic empirical log-odds baseline (in memory only)."""

    creator_id: int
    feature_schema_version: str
    optimizer_version: str
    ranking_policy_versions: tuple[str, ...]
    n_train: int
    n_pos: int
    n_neg: int
    prior_log_odds: float
    feature_llr: tuple[tuple[tuple[str, str], float], ...]
    train_min_evaluated_at: datetime
    train_max_evaluated_at: datetime


@dataclass(frozen=True)
class TrainingOutcome:
    """Trainer result: a model or a mandatory abstention."""

    creator_id: int
    model: OfflineModel | None
    abstained: bool
    abstain_reason: str | None
    feature_schema_version: str
    optimizer_version: str


@dataclass(frozen=True)
class OfflinePrediction:
    """Purchase-given-sent-exposure prediction for the exposed opportunity."""

    creator_id: int
    opportunity_id: int
    probability: float | None
    abstain: bool
    abstain_reason: str | None
    feature_schema_version: str
    optimizer_version: str
    ranking_policy_version: str | None
    score_semantics: str
    is_extrapolative: bool = False


@dataclass(frozen=True)
class EvalRow:
    """Per-test-row offline evaluation outcome (ephemeral)."""

    opportunity_id: int
    creator_id: int
    true_label: str
    binary: int | None
    is_primary: bool
    probability: float | None
    abstained: bool
    abstain_reason: str | None
    advisory_candidate: tuple[int, int] | None
    v1_candidate: tuple[int, int] | None
    classification: str
    first_failed_gate: str | None
    policy_version: str | None
    definition: tuple[int, int] | None


@dataclass(frozen=True)
class EvaluationReport:
    """Chronological holdout report (predictive metrics on primary only)."""

    creator_id: int
    feature_schema_version: str
    optimizer_version: str
    train_end: datetime
    test_start: datetime
    test_end: datetime
    n_train_bundles: int
    n_train_primary: int
    n_train_pos: int
    n_train_neg: int
    n_test_bundles: int
    n_test_primary: int
    n_test_pos: int
    n_test_neg: int
    n_predicted_primary: int
    coverage: float
    abstention_rate: float
    accuracy: float | None
    precision: float | None
    recall: float | None
    brier: float | None
    mean_predicted: float | None
    observed_rate: float | None
    calibration_gap: float | None
    agreements: int
    divergences: int
    abstentions: int
    advisory_invalid: int
    advisory_not_eligible: int
    validator_rejected: int
    no_v1_selection: int
    recovered_count: int
    input_unavailable: int
    validation_error: int
    policy_breakdown: tuple[tuple[str, int], ...]
    definition_breakdown: tuple[tuple[str, int], ...]
    rows: tuple[EvalRow, ...]
    abstained_training: bool
    training_abstain_reason: str | None


# ---------------------------------------------------------------------------
# Label builder (mature sent FULL attributed only; everything else excluded).
# ---------------------------------------------------------------------------


def build_supervised_label(
    *,
    evidence: dict[str, Any] | None,
    ledger_row: dict[str, Any] | None = None,
) -> LabelOutcome:
    """Classify one row into primary supervised or excluded cohorts (pure).

    Consumes the P3.5.3B evidence shape (``label``, ``maturity_state``,
    ``exposure_state``, ``evidence_quality``, ``recovered``,
    ``attribution_status``, ``transaction_id``, ``outcome_state``) plus the
    ledger linkage (``reengagement_of``). Never raises on bad data: bad
    data classifies UNAVAILABLE/CENSORED (fail closed). Never forces
    non-primary evidence into binary labels.
    """
    ledger_row = ledger_row if isinstance(ledger_row, dict) else {}
    raw_parent = ledger_row.get("reengagement_of")
    is_child = (
        isinstance(raw_parent, int) and not isinstance(raw_parent, bool) and raw_parent > 0
    )
    if not isinstance(evidence, dict):
        return LabelOutcome(
            kind=CLASS_UNAVAILABLE,
            binary=None,
            reason="no_evidence",
            mature=False,
            sent_exposure=False,
            evidence_quality="UNAVAILABLE",
            recovered=False,
            is_child=is_child,
        )
    recovered = bool(evidence.get("recovered", False))
    quality = str(evidence.get("evidence_quality") or "UNAVAILABLE")
    maturity = str(evidence.get("maturity_state") or "IMMATURE")
    exposure = str(evidence.get("exposure_state") or "UNAVAILABLE")
    label = str(evidence.get("label") or "UNAVAILABLE")
    mature = maturity == "MATURE"
    sent = exposure == "SENT"
    if recovered:
        return LabelOutcome(
            kind=CLASS_RECOVERED,
            binary=None,
            reason="recovered_quarantined",
            mature=mature,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=True,
            is_child=is_child,
        )
    if is_child:
        # Option A (safer): children are separate descriptive observations,
        # never primary supervised rows, never duplicate revenue positives.
        return LabelOutcome(
            kind=CLASS_REENGAGEMENT_CHILD,
            binary=None,
            reason="reengagement_child_excluded_from_primary",
            mature=mature,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=True,
        )
    if quality == "PARTIAL":
        return LabelOutcome(
            kind=CLASS_PARTIAL,
            binary=None,
            reason="partial_not_pooled_into_full",
            mature=mature,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if not mature:
        return LabelOutcome(
            kind=CLASS_CENSORED,
            binary=None,
            reason="immature_censored",
            mature=False,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if label in ("NO_OPPORTUNITY", "NO_SELECTION"):
        kind = CLASS_NO_OPPORTUNITY if label == "NO_OPPORTUNITY" else CLASS_NO_SELECTION
        return LabelOutcome(
            kind=kind,
            binary=None,
            reason="decision_system_state",
            mature=True,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if label == "PROCESS_NEGATIVE":
        # Separate process-negative population (SEND_FAILED/SEAL_FAILED):
        # system/process failure, never fan rejection, never merged into
        # the purchase label.
        return LabelOutcome(
            kind=CLASS_PROCESS_NEGATIVE,
            binary=None,
            reason="process_negative_separate_population",
            mature=True,
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if label in (CLASS_CENSORED, CLASS_UNAVAILABLE):
        kind = CLASS_CENSORED if label == CLASS_CENSORED else CLASS_UNAVAILABLE
        return LabelOutcome(
            kind=kind,
            binary=None,
            reason="evidence_excluded",
            mature=(label != CLASS_UNAVAILABLE),
            sent_exposure=sent,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if not sent:
        # Primary population requires sent exposure; anything else is out
        # of the supervised cohort (never manufactured into a negative).
        return LabelOutcome(
            kind=CLASS_CENSORED,
            binary=None,
            reason="no_sent_exposure",
            mature=True,
            sent_exposure=False,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if quality != "FULL":
        if quality == "UNATTRIBUTED":
            return LabelOutcome(
                kind=CLASS_UNATTRIBUTED,
                binary=None,
                reason="unattributed_purchase_not_supervised_positive",
                mature=True,
                sent_exposure=True,
                evidence_quality=quality,
                recovered=False,
                is_child=False,
            )
        return LabelOutcome(
            kind=CLASS_UNAVAILABLE,
            binary=None,
            reason="non_full_quality_excluded",
            mature=True,
            sent_exposure=True,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if label == "POSITIVE":
        attribution = evidence.get("attribution_status")
        txn = evidence.get("transaction_id")
        has_txn = isinstance(txn, str) and bool(txn.strip())
        if attribution != "attributed" or not has_txn:
            return LabelOutcome(
                kind=CLASS_UNATTRIBUTED,
                binary=None,
                reason="purchase_without_transaction_or_unattributed",
                mature=True,
                sent_exposure=True,
                evidence_quality=quality,
                recovered=False,
                is_child=False,
            )
        return LabelOutcome(
            kind=TRAIN_LABEL_PURCHASED,
            binary=1,
            reason="mature_attributed_purchase_on_sent_exposure",
            mature=True,
            sent_exposure=True,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    if label == "COMMERCIAL_NEGATIVE":
        outcome = evidence.get("outcome_state") or evidence.get("observed_outcome_state")
        if outcome == "EXPIRED":
            return LabelOutcome(
                kind=TRAIN_LABEL_EXPIRED,
                binary=0,
                reason="mature_expired_on_sent_exposure",
                mature=True,
                sent_exposure=True,
                evidence_quality=quality,
                recovered=False,
                is_child=False,
            )
        # DECLINED is the canonical fan-rejection negative; any other
        # commercial-negative encoding maps here conservatively with the
        # outcome preserved in the reason path (both are binary 0).
        return LabelOutcome(
            kind=TRAIN_LABEL_DECLINED,
            binary=0,
            reason="mature_declined_on_sent_exposure",
            mature=True,
            sent_exposure=True,
            evidence_quality=quality,
            recovered=False,
            is_child=False,
        )
    return LabelOutcome(
        kind=CLASS_CENSORED,
        binary=None,
        reason="unrecognized_conservative_censored",
        mature=True,
        sent_exposure=sent,
        evidence_quality=quality,
        recovered=False,
        is_child=False,
    )


# ---------------------------------------------------------------------------
# Feature extraction (frozen input only).
# ---------------------------------------------------------------------------


def _selected_frozen_candidate(inp: OptimizationInput) -> Any:
    if inp.selected_definition_id is None or inp.selected_definition_version is None:
        raise ValueError("selected candidate identity is required")
    want = (int(inp.selected_definition_id), int(inp.selected_definition_version))
    for cand in inp.frozen_candidates:
        try:
            if cand.identity() == want:
                return cand
        except Exception:
            continue
    raise ValueError("selected candidate not in frozen set")


def extract_features(inp: OptimizationInput) -> dict[str, str]:
    """Project a frozen OptimizationInput to categorical features (pure).

    Uses ONLY decision-time frozen facts. Raises ValueError on missing
    required sections (callers map this to ABSTAIN). Never reads
    ``user_id`` (join key only), ``creator_id`` (isolation only),
    ``evaluated_at`` (ordering only), ``policy_version`` (segmentation
    only), outcome/evidence, transaction, price-as-signal, or any current
    catalog/provider/Vault/Dropfans/LLM state.
    """
    if not isinstance(inp, OptimizationInput):
        raise ValueError("input must be an OptimizationInput")
    selected = _selected_frozen_candidate(inp)
    if inp.fan_commercial_summary is None:
        raise ValueError("fan_commercial_summary is required")
    if inp.offer_history_summary is None:
        raise ValueError("offer_history_summary is required")
    if inp.ownership_context is None:
        raise ValueError("ownership_context is required")
    if inp.conversation_context is None:
        raise ValueError("conversation_context is required")
    fan = inp.fan_commercial_summary
    hist = inp.offer_history_summary
    conv = inp.conversation_context
    owned = inp.ownership_context.owned_vault_ids or frozenset()

    offer_type = selected.offer_type.strip() if selected.offer_type else "UNKNOWN"
    currency = selected.currency.strip() if selected.currency else "MISSING"
    family = "HAS_FAMILY" if selected.family_id is not None else "NO_FAMILY"
    vault_n = len(tuple(selected.canonical_vault_ids or ()))
    vault_bucket = "V0" if vault_n == 0 else ("V1" if vault_n == 1 else ("V2" if vault_n == 2 else "V3P"))
    drop_n = len(tuple(selected.mapped_drop_ids or ()))
    drop_bucket = "SINGLE" if drop_n == 1 else "OTHER"

    pc = fan.purchase_count if isinstance(fan.purchase_count, int) else 0
    fan_purchase = "P0" if pc <= 0 else ("P1_2" if pc <= 2 else "P3P")
    spend = fan.total_spend_minor if isinstance(fan.total_spend_minor, int) else 0
    fan_spend = "S0" if spend <= 0 else ("S_LOW" if spend < 5000 else "S_HIGH")
    ro = fan.recent_offer_count if isinstance(fan.recent_offer_count, int) else 0
    fan_recent = "R0" if ro <= 0 else ("R1" if ro == 1 else "R2P")
    rj = fan.recent_rejected_offer_count if isinstance(fan.recent_rejected_offer_count, int) else 0
    fan_rejected = "J0" if rj <= 0 else "J1P"

    total = hist.total_offer_count if isinstance(hist.total_offer_count, int) else 0
    hist_total = "H0_2" if total <= 2 else ("H3_5" if total <= 5 else "H6P")
    declined = hist.declined_offer_count if isinstance(hist.declined_offer_count, int) else 0
    hist_declined = "D0" if declined <= 0 else "D1P"
    active = "ACTIVE" if bool(hist.has_active_offer) else "NO_ACTIVE"

    lifecycle = conv.lifecycle.strip() if conv.lifecycle else "MISSING"
    topic = "HAS_TOPIC" if conv.current_topic else "NO_TOPIC"

    owned_n = len(set(owned))
    owned_bucket = "O0" if owned_n <= 0 else ("O1_2" if owned_n <= 2 else "O3P")

    return {
        "offer_type": offer_type,
        "price_bucket": _price_bucket(selected.price_minor),
        "currency": currency,
        "family_presence": family,
        "vault_count_bucket": vault_bucket,
        "drop_mapping": drop_bucket,
        "fan_purchase_bucket": fan_purchase,
        "fan_spend_bucket": fan_spend,
        "fan_recent_offer_bucket": fan_recent,
        "fan_rejected_bucket": fan_rejected,
        "history_total_bucket": hist_total,
        "history_declined_bucket": hist_declined,
        "has_active_offer": active,
        "lifecycle": lifecycle,
        "topic_presence": topic,
        "owned_count_bucket": owned_bucket,
    }


def _features_tuple(feats: dict[str, str]) -> tuple[tuple[str, str], ...]:
    try:
        return tuple((name, str(feats[name])) for name in FEATURE_NAMES)
    except KeyError as exc:
        raise ValueError(f"missing required feature: {exc}") from exc


# ---------------------------------------------------------------------------
# Example + creator-scoped dataset builders.
# ---------------------------------------------------------------------------


def make_training_example(
    *,
    input: OptimizationInput,
    evidence: dict[str, Any] | None,
    ledger_row: dict[str, Any] | None = None,
) -> tuple[TrainingExample | None, LabelOutcome]:
    """Build a primary TrainingExample or return the exclusion outcome.

    Enforces creator isolation: ``input.creator_id`` must match the
    evidence/ledger creator scope when those scopes are present, else
    raises ValueError (fail closed, never silently pool).
    """
    if not isinstance(input, OptimizationInput):
        raise ValueError("input must be an OptimizationInput")
    ledger_row = ledger_row if isinstance(ledger_row, dict) else {}
    if isinstance(evidence, dict):
        for key in ("creator_id", "opportunity_id"):
            actual = evidence.get(key)
            if actual is None:
                continue
            expected = input.creator_id if key == "creator_id" else input.opportunity_id
            try:
                if int(actual) != int(expected):
                    raise ValueError(f"evidence {key} scope mismatch")
            except ValueError:
                raise
            except Exception:
                raise ValueError(f"evidence {key} scope mismatch")
    if isinstance(ledger_row, dict) and ledger_row:
        for key in ("creator_id", "opportunity_id"):
            actual = ledger_row.get(key)
            if actual is None:
                continue
            expected = input.creator_id if key == "creator_id" else input.opportunity_id
            try:
                if int(actual) != int(expected):
                    raise ValueError(f"ledger_row {key} scope mismatch")
            except ValueError:
                raise
            except Exception:
                raise ValueError(f"ledger_row {key} scope mismatch")
    outcome = build_supervised_label(evidence=evidence, ledger_row=ledger_row)
    if outcome.kind not in PRIMARY_TRAIN_LABELS or outcome.binary is None:
        return None, outcome
    feats = _features_tuple(extract_features(input))
    parent = ledger_row.get("reengagement_of") if isinstance(ledger_row, dict) else None
    parent_id = int(parent) if isinstance(parent, int) and not isinstance(parent, bool) and parent > 0 else None
    example = TrainingExample(
        creator_id=int(input.creator_id),
        opportunity_id=int(input.opportunity_id),
        evaluated_at=input.evaluated_at,
        policy_version=input.policy_version,
        definition_id=int(input.selected_definition_id),  # type: ignore[arg-type]
        definition_version=int(input.selected_definition_version),  # type: ignore[arg-type]
        features=feats,
        label=outcome.kind,
        binary=int(outcome.binary),
        evidence_quality=outcome.evidence_quality,
        recovered=False,
        reengagement_of=parent_id,
        is_child=False,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )
    return example, outcome


def build_creator_dataset(
    *,
    creator_id: int,
    bundles: list[RowBundle] | tuple[RowBundle, ...],
    as_of: datetime | None = None,
) -> CreatorDataset:
    """Build a creator-scoped primary dataset (pure, in memory).

    Every bundle's input MUST belong to ``creator_id``; any cross-creator
    bundle raises ValueError (fail closed). Non-primary rows are counted
    in ``excluded_counts`` and never pooled into ``examples``.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if as_of is not None:
        _require_aware("as_of", as_of)
    bundles = list(bundles or [])
    examples: list[TrainingExample] = []
    excluded: dict[str, int] = {}
    for bundle in bundles:
        if not isinstance(bundle, RowBundle) or not isinstance(bundle.input, OptimizationInput):
            excluded["INPUT_UNAVAILABLE"] = excluded.get("INPUT_UNAVAILABLE", 0) + 1
            continue
        if int(bundle.input.creator_id) != int(creator_id):
            raise ValueError("cross-creator bundle: creator isolation cannot be guaranteed")
        example, outcome = make_training_example(
            input=bundle.input, evidence=bundle.evidence, ledger_row=bundle.ledger_row
        )
        if example is None:
            excluded[outcome.kind] = excluded.get(outcome.kind, 0) + 1
        else:
            examples.append(example)
    examples.sort(key=lambda e: (e.evaluated_at, e.opportunity_id))
    return CreatorDataset(
        creator_id=creator_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        examples=tuple(examples),
        n_bundles=len(bundles),
        excluded_counts=tuple(sorted(excluded.items())),
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Deterministic empirical log-odds baseline (stdlib only).
# ---------------------------------------------------------------------------


def _sigmoid(logit: float) -> float:
    if not math.isfinite(logit):
        raise ValueError("non-finite logit")
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    emitted = math.exp(logit)
    return emitted / (1.0 + emitted)


def train_creator_model(dataset: CreatorDataset) -> TrainingOutcome:
    """Train the per-creator baseline or abstain (deterministic, pure).

    Abstains (never a global/shared model) when the creator has
    insufficient eligible training evidence per the prototype-only floors.
    """
    if not isinstance(dataset, CreatorDataset):
        raise ValueError("dataset must be a CreatorDataset")
    if dataset.feature_schema_version != FEATURE_SCHEMA_VERSION:
        return TrainingOutcome(
            creator_id=dataset.creator_id,
            model=None,
            abstained=True,
            abstain_reason="unsupported_feature_schema",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            optimizer_version=OPTIMIZER_VERSION,
        )
    examples = list(dataset.examples)
    # Defense in depth: dataset builder already scopes, but the trainer
    # re-asserts single-creator purity (no silent aggregation).
    creators = {int(e.creator_id) for e in examples}
    if len(creators) > 1:
        return TrainingOutcome(
            creator_id=dataset.creator_id,
            model=None,
            abstained=True,
            abstain_reason="creator_isolation_violated",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            optimizer_version=OPTIMIZER_VERSION,
        )
    n_total = len(examples)
    n_pos = sum(1 for e in examples if e.binary == 1)
    n_neg = n_total - n_pos
    if (
        n_total < MIN_TRAIN_TOTAL
        or n_pos < MIN_TRAIN_POSITIVE
        or n_neg < MIN_TRAIN_NEGATIVE
    ):
        return TrainingOutcome(
            creator_id=dataset.creator_id,
            model=None,
            abstained=True,
            abstain_reason="insufficient_eligible_training_evidence",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            optimizer_version=OPTIMIZER_VERSION,
        )
    alpha = _SMOOTHING_ALPHA
    prior_p = (n_pos + alpha) / (n_total + 2.0 * alpha)
    prior_log_odds = math.log(prior_p / (1.0 - prior_p))
    # Distinct values per feature for cardinality-aware smoothing.
    distinct: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
    for example in examples:
        for name, value in example.features:
            distinct[name].add(value)
    llr: dict[tuple[str, str], float] = {}
    # Deterministic iteration: sorted feature names, sorted values.
    for name in FEATURE_NAMES:
        values = sorted(distinct[name])
        cardinality = max(1, len(values))
        for value in values:
            c_pos = sum(1 for e in examples if e.binary == 1 and dict(e.features).get(name) == value)
            c_neg = sum(1 for e in examples if e.binary == 0 and dict(e.features).get(name) == value)
            p_given_pos = (c_pos + alpha) / (n_pos + alpha * cardinality)
            p_given_neg = (c_neg + alpha) / (n_neg + alpha * cardinality)
            llr[(name, value)] = math.log(p_given_pos / p_given_neg)
    ordered_llr = tuple(sorted(llr.items(), key=lambda kv: (kv[0][0], kv[0][1])))
    policies = sorted({e.policy_version or "unknown" for e in examples})
    times = sorted(e.evaluated_at for e in examples)
    model = OfflineModel(
        creator_id=int(dataset.creator_id),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        optimizer_version=OPTIMIZER_VERSION,
        ranking_policy_versions=tuple(policies),
        n_train=n_total,
        n_pos=n_pos,
        n_neg=n_neg,
        prior_log_odds=float(prior_log_odds),
        feature_llr=ordered_llr,
        train_min_evaluated_at=times[0],
        train_max_evaluated_at=times[-1],
    )
    return TrainingOutcome(
        creator_id=int(dataset.creator_id),
        model=model,
        abstained=False,
        abstain_reason=None,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        optimizer_version=OPTIMIZER_VERSION,
    )


def predict_for_input(
    model: OfflineModel | None,
    input: OptimizationInput,
    *,
    abstain_reason_if_no_model: str = "no_trained_model",
) -> OfflinePrediction:
    """Predict purchase-given-sent-exposure for the exposed opportunity.

    Abstains (v1 remains authoritative, never suppression) on: no model,
    creator mismatch, unsupported schema, missing required features,
    recovered/quarantined input, re-engagement child (Option A: primary
    model is out-of-population for children), or non-finite results.
    Evidence outcome fields are NEVER features; only the recovered/child
    quality guards are consulted.
    """
    if not isinstance(input, OptimizationInput):
        return OfflinePrediction(
            creator_id=-1,
            opportunity_id=-1,
            probability=None,
            abstain=True,
            abstain_reason="malformed_input",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            optimizer_version=OPTIMIZER_VERSION,
            ranking_policy_version=None,
            score_semantics=SCORE_SEMANTICS,
            is_extrapolative=False,
        )
    base = dict(
        creator_id=int(input.creator_id),
        opportunity_id=int(input.opportunity_id),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        optimizer_version=model.optimizer_version if model is not None else OPTIMIZER_VERSION,
        ranking_policy_version=input.policy_version,
        score_semantics=SCORE_SEMANTICS,
        is_extrapolative=False,
    )
    if model is None:
        return OfflinePrediction(probability=None, abstain=True, abstain_reason=abstain_reason_if_no_model, **base)  # type: ignore[arg-type]
    if int(input.creator_id) != int(model.creator_id):
        return OfflinePrediction(probability=None, abstain=True, abstain_reason="creator_isolation", **base)  # type: ignore[arg-type]
    if model.feature_schema_version != FEATURE_SCHEMA_VERSION:
        return OfflinePrediction(probability=None, abstain=True, abstain_reason="unsupported_feature_schema", **base)  # type: ignore[arg-type]
    try:
        if input.evidence_context is not None and bool(input.evidence_context.recovered):
            return OfflinePrediction(probability=None, abstain=True, abstain_reason="recovered_input", **base)  # type: ignore[arg-type]
        if input.reengagement_context is not None and bool(input.reengagement_context.is_child):
            return OfflinePrediction(probability=None, abstain=True, abstain_reason="reengagement_child_excluded_from_primary", **base)  # type: ignore[arg-type]
        feats = extract_features(input)
    except ValueError as exc:
        return OfflinePrediction(probability=None, abstain=True, abstain_reason=f"missing_required_features:{type(exc).__name__}", **base)  # type: ignore[arg-type]
    table = dict(model.feature_llr)
    try:
        logit = float(model.prior_log_odds)
        for name in FEATURE_NAMES:
            delta = table.get((name, feats[name]))
            if delta is not None:
                logit += float(delta)
        prob = _sigmoid(logit)
        if not isinstance(prob, float) or not math.isfinite(prob):
            raise ValueError("non-finite probability")
        prob = min(max(prob, 1e-6), 1.0 - 1e-6)
    except (ValueError, OverflowError, ZeroDivisionError):
        return OfflinePrediction(probability=None, abstain=True, abstain_reason="invalid_prediction", **base)  # type: ignore[arg-type]
    return OfflinePrediction(probability=prob, abstain=False, abstain_reason=None, **base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Advisory conversion (primary: exposed opportunity only, never ranking).
# ---------------------------------------------------------------------------


def prediction_to_advisory(
    input: OptimizationInput,
    prediction: OfflinePrediction,
) -> AdvisoryOptimizationResult:
    """Convert a primary prediction to validator-compatible advice (pure).

    A non-abstained prediction scores ONLY the v1-selected/exposed
    identity with the purchase probability. The historical validator then
    measures AGREEMENT (valid) vs validator rejections; DIVERGENCE is
    zero by design in this safest prototype because alternatives are
    never ranked from observational data. Abstention maps to an
    abstaining result (v1 behavior preserved, never suppression).
    """
    if not isinstance(input, OptimizationInput):
        raise ValueError("input must be an OptimizationInput")
    if not isinstance(prediction, OfflinePrediction):
        raise ValueError("prediction must be an OfflinePrediction")
    if int(prediction.creator_id) != int(input.creator_id) or int(prediction.opportunity_id) != int(
        input.opportunity_id
    ):
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=OPTIMIZER_VERSION,
            input_policy_version=input.policy_version,
        )
    if prediction.abstain or prediction.probability is None:
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=prediction.optimizer_version,
            input_policy_version=input.policy_version,
        )
    if input.selected_definition_id is None or input.selected_definition_version is None:
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=prediction.optimizer_version,
            input_policy_version=input.policy_version,
        )
    prob = float(prediction.probability)
    if not math.isfinite(prob):
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=prediction.optimizer_version,
            input_policy_version=input.policy_version,
        )
    return AdvisoryOptimizationResult(
        creator_id=int(input.creator_id),
        opportunity_id=int(input.opportunity_id),
        candidate_scores=(
            (int(input.selected_definition_id), int(input.selected_definition_version), prob),
        ),
        abstain=False,
        optimizer_version=prediction.optimizer_version,
        input_policy_version=input.policy_version,
    )


def score_candidates_extrapolative(
    input: OptimizationInput,
    model: OfflineModel | None,
) -> AdvisoryOptimizationResult:
    """Score ALL frozen candidates with the primary model (DIAGNOSTIC ONLY).

    EXTRAPOLATIVE / OFF-POLICY ADVISORY: alternatives were not exposed, so
    these scores are extrapolative/off-policy advisory values, NOT validated
    conversion probabilities. Never use for ranking claims, causal claims, or
    production decisions. The primary prototype path
    (prediction_to_advisory) never calls this function.
    """
    if not isinstance(input, OptimizationInput):
        raise ValueError("input must be an OptimizationInput")
    if model is None or int(input.creator_id) != int(model.creator_id):
        return abstain_result(
            creator_id=int(input.creator_id) if isinstance(input.creator_id, int) else -1,
            opportunity_id=int(input.opportunity_id),
            optimizer_version=OPTIMIZER_VERSION,
            input_policy_version=input.policy_version,
        )
    table = dict(model.feature_llr)
    fan = input.fan_commercial_summary
    hist = input.offer_history_summary
    conv = input.conversation_context
    owned = input.ownership_context.owned_vault_ids if input.ownership_context else frozenset()
    if fan is None or hist is None or conv is None:
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=model.optimizer_version,
            input_policy_version=input.policy_version,
        )
    scores: list[tuple[int, int, float]] = []
    for cand in input.frozen_candidates:
        try:
            offer_type = cand.offer_type.strip() if cand.offer_type else "UNKNOWN"
            currency = cand.currency.strip() if cand.currency else "MISSING"
            family = "HAS_FAMILY" if cand.family_id is not None else "NO_FAMILY"
            vault_n = len(tuple(cand.canonical_vault_ids or ()))
            vault_bucket = "V0" if vault_n == 0 else ("V1" if vault_n == 1 else ("V2" if vault_n == 2 else "V3P"))
            drop_n = len(tuple(cand.mapped_drop_ids or ()))
            drop_bucket = "SINGLE" if drop_n == 1 else "OTHER"
            pc = fan.purchase_count if isinstance(fan.purchase_count, int) else 0
            fan_purchase = "P0" if pc <= 0 else ("P1_2" if pc <= 2 else "P3P")
            spend = fan.total_spend_minor if isinstance(fan.total_spend_minor, int) else 0
            fan_spend = "S0" if spend <= 0 else ("S_LOW" if spend < 5000 else "S_HIGH")
            ro = fan.recent_offer_count if isinstance(fan.recent_offer_count, int) else 0
            fan_recent = "R0" if ro <= 0 else ("R1" if ro == 1 else "R2P")
            rj = fan.recent_rejected_offer_count if isinstance(fan.recent_rejected_offer_count, int) else 0
            fan_rejected = "J0" if rj <= 0 else "J1P"
            total = hist.total_offer_count if isinstance(hist.total_offer_count, int) else 0
            hist_total = "H0_2" if total <= 2 else ("H3_5" if total <= 5 else "H6P")
            declined = hist.declined_offer_count if isinstance(hist.declined_offer_count, int) else 0
            hist_declined = "D0" if declined <= 0 else "D1P"
            active = "ACTIVE" if bool(hist.has_active_offer) else "NO_ACTIVE"
            lifecycle = conv.lifecycle.strip() if conv.lifecycle else "MISSING"
            topic = "HAS_TOPIC" if conv.current_topic else "NO_TOPIC"
            owned_n = len(set(owned or frozenset()))
            owned_bucket = "O0" if owned_n <= 0 else ("O1_2" if owned_n <= 2 else "O3P")
            feats = {
                "offer_type": offer_type,
                "price_bucket": _price_bucket(cand.price_minor),
                "currency": currency,
                "family_presence": family,
                "vault_count_bucket": vault_bucket,
                "drop_mapping": drop_bucket,
                "fan_purchase_bucket": fan_purchase,
                "fan_spend_bucket": fan_spend,
                "fan_recent_offer_bucket": fan_recent,
                "fan_rejected_bucket": fan_rejected,
                "history_total_bucket": hist_total,
                "history_declined_bucket": hist_declined,
                "has_active_offer": active,
                "lifecycle": lifecycle,
                "topic_presence": topic,
                "owned_count_bucket": owned_bucket,
            }
            logit = float(model.prior_log_odds)
            for name in FEATURE_NAMES:
                delta = table.get((name, feats[name]))
                if delta is not None:
                    logit += float(delta)
            prob = _sigmoid(logit)
            scores.append((int(cand.definition_id), int(cand.version), float(prob)))
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
    if not scores:
        return abstain_result(
            creator_id=int(input.creator_id),
            opportunity_id=int(input.opportunity_id),
            optimizer_version=model.optimizer_version,
            input_policy_version=input.policy_version,
        )
    scores.sort(key=lambda s: (s[0], s[1]))
    return AdvisoryOptimizationResult(
        creator_id=int(input.creator_id),
        opportunity_id=int(input.opportunity_id),
        candidate_scores=tuple(scores),
        abstain=False,
        optimizer_version=model.optimizer_version,
        input_policy_version=input.policy_version,
    )


# ---------------------------------------------------------------------------
# Chronological evaluation (holdout primary; expanding-window helper).
# ---------------------------------------------------------------------------


def _ranked_order_from_ledger_row(ledger_row: dict[str, Any]) -> tuple[int, ...] | None:
    try:
        import json as _json

        snapshot = ledger_row.get("decision_snapshot")
        parsed = _json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        if not isinstance(parsed, dict):
            return None
        ranking = parsed.get("ranking")
        if not isinstance(ranking, dict):
            return None
        order = ranking.get("ranked_order")
        if not isinstance(order, (list, tuple)) or not order:
            return None
        cleaned = tuple(int(d) for d in order if isinstance(d, int) and not isinstance(d, bool))
        return cleaned or None
    except Exception:
        return None


def _sealed_offer_from_ledger_row(ledger_row: dict[str, Any]) -> int | None:
    sealed = ledger_row.get("sealed_offer_id")
    if isinstance(sealed, int) and not isinstance(sealed, bool) and sealed > 0:
        return sealed
    return None


def _v1_of(input: OptimizationInput) -> tuple[int, int] | None:
    if isinstance(input.selected_definition_id, int) and not isinstance(
        input.selected_definition_id, bool
    ):
        if isinstance(input.selected_definition_version, int) and not isinstance(
            input.selected_definition_version, bool
        ):
            return (int(input.selected_definition_id), int(input.selected_definition_version))
    return None


def chronological_holdout_evaluate(
    *,
    creator_id: int,
    bundles: list[RowBundle] | tuple[RowBundle, ...],
    train_end: datetime,
    test_start: datetime,
    test_end: datetime,
) -> EvaluationReport:
    """Run a chronological holdout evaluation for one creator (pure).

    - Train bundles: ``evaluated_at < train_end`` (primary labels only).
    - Test bundles: ``test_start <= evaluated_at < test_end``.
    - Requires ``train_end <= test_start`` (no backward leakage), else
      raises. All datetimes must be timezone-aware.
    - Predictive metrics (accuracy/precision/recall/Brier/calibration)
      cover the PRIMARY test cohort with non-abstained predictions only,
      using the prototype-only threshold for class decisions.
    - Every non-abstained test prediction is routed through the P3.5.4B
      historical validator; agreement/divergence taxonomy follows
      :mod:`commerce.opportunity_validation`.
    """
    creator_id = _require_scope("creator_id", creator_id)
    _require_aware("train_end", train_end)
    _require_aware("test_start", test_start)
    _require_aware("test_end", test_end)
    if not (train_end <= test_start <= test_end):
        raise ValueError("chronology requires train_end <= test_start <= test_end")
    bundles = list(bundles or [])
    for bundle in bundles:
        if not isinstance(bundle, RowBundle) or not isinstance(bundle.input, OptimizationInput):
            raise ValueError("bundles must be RowBundle with OptimizationInput")
        if int(bundle.input.creator_id) != int(creator_id):
            raise ValueError("cross-creator bundle: creator isolation cannot be guaranteed")

    train_bundles = [b for b in bundles if b.input.evaluated_at < train_end]
    test_bundles = [b for b in bundles if test_start <= b.input.evaluated_at < test_end]

    train_dataset = build_creator_dataset(creator_id=creator_id, bundles=train_bundles)
    outcome = train_creator_model(train_dataset)
    model = outcome.model

    rows: list[EvalRow] = []
    policy_counts: dict[str, int] = {}
    defn_counts: dict[str, int] = {}
    n_test_pos = 0
    n_test_neg = 0
    n_test_primary = 0
    n_predicted_primary = 0
    correct = 0
    true_pos = 0
    pred_pos = 0
    actual_pos = 0
    brier_sum = 0.0
    prob_sum = 0.0
    agreements = divergences = abstentions = 0
    advisory_invalid = advisory_not_eligible = validator_rejected = 0
    no_v1 = recovered_count = input_unavailable = validation_error = 0

    for bundle in sorted(test_bundles, key=lambda b: (b.input.evaluated_at, b.input.opportunity_id)):
        inp = bundle.input
        policy_counts[inp.policy_version or "unknown"] = (
            policy_counts.get(inp.policy_version or "unknown", 0) + 1
        )
        example, label_outcome = make_training_example(
            input=inp, evidence=bundle.evidence, ledger_row=bundle.ledger_row
        )
        is_primary = example is not None
        binary = example.binary if example is not None else None
        true_label = example.label if example is not None else label_outcome.kind
        if is_primary:
            n_test_primary += 1
            if binary == 1:
                n_test_pos += 1
            else:
                n_test_neg += 1
            defn_key = f"{example.definition_id}v{example.definition_version}"
            defn_counts[defn_key] = defn_counts.get(defn_key, 0) + 1
        # Recovered inputs form a quarantined cohort (never pooled).
        if label_outcome.kind == CLASS_RECOVERED or (
            isinstance(bundle.evidence, dict) and bool(bundle.evidence.get("recovered", False))
        ):
            recovered_count += 1
            rows.append(
                EvalRow(
                    opportunity_id=int(inp.opportunity_id),
                    creator_id=int(inp.creator_id),
                    true_label=true_label,
                    binary=binary,
                    is_primary=False,
                    probability=None,
                    abstained=True,
                    abstain_reason="recovered_input",
                    advisory_candidate=None,
                    v1_candidate=_v1_of(inp),
                    classification=RECOVERED_INPUT,
                    first_failed_gate=None,
                    policy_version=inp.policy_version,
                    definition=(example.definition_id, example.definition_version)
                    if example is not None
                    else None,
                )
            )
            # Recovered abstentions count as abstentions for coverage honesty.
            abstentions += 1
            continue
        prediction = predict_for_input(model, inp) if model is not None else predict_for_input(
            None, inp, abstain_reason_if_no_model=outcome.abstain_reason or "no_trained_model"
        )
        if prediction.abstain or prediction.probability is None:
            abstentions += 1
            rows.append(
                EvalRow(
                    opportunity_id=int(inp.opportunity_id),
                    creator_id=int(inp.creator_id),
                    true_label=true_label,
                    binary=binary,
                    is_primary=is_primary,
                    probability=None,
                    abstained=True,
                    abstain_reason=prediction.abstain_reason,
                    advisory_candidate=None,
                    v1_candidate=_v1_of(inp),
                    classification=ADVISORY_ABSTAIN,
                    first_failed_gate=None,
                    policy_version=inp.policy_version,
                    definition=(example.definition_id, example.definition_version)
                    if example is not None
                    else None,
                )
            )
            continue
        # Non-abstained: route through the historical validator.
        advisory = prediction_to_advisory(inp, prediction)
        try:
            result = validate_advisory(
                inp,
                advisory,
                sealed_offer_id=_sealed_offer_from_ledger_row(bundle.ledger_row),
                ranked_order=_ranked_order_from_ledger_row(bundle.ledger_row),
            )
        except Exception:
            validation_error += 1
            rows.append(
                EvalRow(
                    opportunity_id=int(inp.opportunity_id),
                    creator_id=int(inp.creator_id),
                    true_label=true_label,
                    binary=binary,
                    is_primary=is_primary,
                    probability=float(prediction.probability),
                    abstained=False,
                    abstain_reason=None,
                    advisory_candidate=None,
                    v1_candidate=_v1_of(inp),
                    classification=VALIDATION_ERROR,
                    first_failed_gate=None,
                    policy_version=inp.policy_version,
                    definition=(example.definition_id, example.definition_version)
                    if example is not None
                    else None,
                )
            )
            continue
        if result.classification != VALID:
            if result.classification == ADVISORY_INVALID:
                advisory_invalid += 1
            elif result.classification == ADVISORY_NOT_ELIGIBLE:
                advisory_not_eligible += 1
            elif result.classification == VALIDATOR_REJECTED:
                validator_rejected += 1
            rows.append(
                EvalRow(
                    opportunity_id=int(inp.opportunity_id),
                    creator_id=int(inp.creator_id),
                    true_label=true_label,
                    binary=binary,
                    is_primary=is_primary,
                    probability=float(prediction.probability),
                    abstained=False,
                    abstain_reason=None,
                    advisory_candidate=result.advisory_candidate,
                    v1_candidate=_v1_of(inp),
                    classification=result.classification,
                    first_failed_gate=result.first_failed_gate,
                    policy_version=inp.policy_version,
                    definition=(example.definition_id, example.definition_version)
                    if example is not None
                    else None,
                )
            )
            # Validator rejections still count as emitted recommendations;
            # predictive metrics below only cover primary rows, and only
            # VALID advisories contribute to agreement accounting. For
            # class metrics on primary rows with rejected advice, the row
            # is excluded from accuracy denominators (no trustworthy
            # recommendation), documented here.
            continue
        comparison, v1 = compare_with_v1(inp, result.advisory_candidate)
        if comparison == AGREEMENT:
            agreements += 1
        elif comparison == DIVERGENCE:
            divergences += 1
        elif comparison == NO_V1_SELECTION:
            no_v1 += 1
        rows.append(
            EvalRow(
                opportunity_id=int(inp.opportunity_id),
                creator_id=int(inp.creator_id),
                true_label=true_label,
                binary=binary,
                is_primary=is_primary,
                probability=float(prediction.probability),
                abstained=False,
                abstain_reason=None,
                advisory_candidate=result.advisory_candidate,
                v1_candidate=v1,
                classification=comparison,
                first_failed_gate=None,
                policy_version=inp.policy_version,
                definition=(example.definition_id, example.definition_version)
                if example is not None
                else None,
            )
        )
        if is_primary and binary is not None:
            n_predicted_primary += 1
            prob = float(prediction.probability)
            predicted_label = 1 if prob >= PROTOTYPE_THRESHOLD else 0
            if predicted_label == binary:
                correct += 1
            if predicted_label == 1:
                pred_pos += 1
                if binary == 1:
                    true_pos += 1
            if binary == 1:
                actual_pos += 1
            brier_sum += (prob - binary) ** 2
            prob_sum += prob

    coverage = (n_predicted_primary / n_test_primary) if n_test_primary else 0.0
    abstention_rate = 1.0 - coverage
    if n_predicted_primary:
        accuracy: float | None = correct / n_predicted_primary
        precision: float | None = (true_pos / pred_pos) if pred_pos else None
        recall: float | None = (true_pos / actual_pos) if actual_pos else None
        brier: float | None = brier_sum / n_predicted_primary
        mean_predicted: float | None = prob_sum / n_predicted_primary
        observed = sum(1 for r in rows if r.is_primary and r.binary == 1 and not r.abstained and r.classification in (AGREEMENT, DIVERGENCE)) / n_predicted_primary if n_predicted_primary else None
        observed_rate: float | None = float(observed) if observed is not None else None
        calibration_gap: float | None = (
            abs(mean_predicted - observed_rate)
            if mean_predicted is not None and observed_rate is not None
            else None
        )
    else:
        accuracy = precision = recall = brier = None
        mean_predicted = observed_rate = calibration_gap = None

    return EvaluationReport(
        creator_id=creator_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        optimizer_version=OPTIMIZER_VERSION,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        n_train_bundles=len(train_bundles),
        n_train_primary=len(train_dataset.examples),
        n_train_pos=sum(1 for e in train_dataset.examples if e.binary == 1),
        n_train_neg=sum(1 for e in train_dataset.examples if e.binary == 0),
        n_test_bundles=len(test_bundles),
        n_test_primary=n_test_primary,
        n_test_pos=n_test_pos,
        n_test_neg=n_test_neg,
        n_predicted_primary=n_predicted_primary,
        coverage=float(coverage),
        abstention_rate=float(abstention_rate),
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        brier=brier,
        mean_predicted=mean_predicted,
        observed_rate=observed_rate,
        calibration_gap=calibration_gap,
        agreements=agreements,
        divergences=divergences,
        abstentions=abstentions,
        advisory_invalid=advisory_invalid,
        advisory_not_eligible=advisory_not_eligible,
        validator_rejected=validator_rejected,
        no_v1_selection=no_v1,
        recovered_count=recovered_count,
        input_unavailable=input_unavailable,
        validation_error=validation_error,
        policy_breakdown=tuple(sorted(policy_counts.items())),
        definition_breakdown=tuple(sorted(defn_counts.items())),
        rows=tuple(rows),
        abstained_training=outcome.abstained,
        training_abstain_reason=outcome.abstain_reason,
    )


def expanding_window_evaluate(
    *,
    creator_id: int,
    bundles: list[RowBundle] | tuple[RowBundle, ...],
    cutoffs: list[tuple[datetime, datetime, datetime]],
) -> tuple[EvaluationReport, ...]:
    """Run expanding-window chronological evaluation (pure).

    Each cutoff is ``(train_end, test_start, test_end)`` and is evaluated
    as an independent holdout via :func:`chronological_holdout_evaluate`.
    Windows must individually satisfy ``train_end <= test_start``.
    """
    reports: list[EvaluationReport] = []
    for train_end, test_start, test_end in cutoffs:
        reports.append(
            chronological_holdout_evaluate(
                creator_id=creator_id,
                bundles=list(bundles or []),
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return tuple(reports)


def model_metadata(model: OfflineModel) -> dict[str, Any]:
    """Expose version metadata with the three version concepts separated."""
    return {
        "feature_schema_version": model.feature_schema_version,
        "ranking_policy_versions": list(model.ranking_policy_versions),
        "optimizer_version": model.optimizer_version,
        "creator_id": model.creator_id,
        "n_train": model.n_train,
        "n_pos": model.n_pos,
        "n_neg": model.n_neg,
        "score_semantics": SCORE_SEMANTICS,
    }


# ---------------------------------------------------------------------------
# Deterministic synthetic fixtures (clearly marked synthetic, never evidence).
# ---------------------------------------------------------------------------


def make_synthetic_ledger_row(
    *,
    creator_id: int,
    opportunity_id: int,
    user_id: int = 10,
    evaluated_at: datetime,
    definition_id: int = 11,
    version: int = 2,
    stable_key: str = "alpha",
    offer_type: str = "SMALL_BUNDLE",
    price_minor: int = 1999,
    currency: str = "USD",
    family_id: int | None = None,
    vault_ids: tuple[str, ...] | list[str] = ("V1", "V2"),
    mapped_drop_ids: tuple[str, ...] | list[str] = ("drop_x",),
    policy_version: str | None = "v1",
    sealed_offer_id: int | None = None,
    reengagement_of: int | None = None,
    outcome_state: str = "PENDING",
    purchase_count: int = 0,
) -> dict[str, Any]:
    """Build a deterministic SYNTHETIC ledger row (test-only, never evidence).

    The returned mapping carries ``"synthetic": SYNTHETIC_MARKER`` and a
    ``synthetic:`` generation namespace so it can never be mistaken for
    production evidence. No DB access, no provider calls.
    """
    import json as _json

    creator_id = _require_scope("creator_id", creator_id)
    opportunity_id = _require_scope("opportunity_id", opportunity_id)
    _require_aware("evaluated_at", evaluated_at)
    eligible = [
        {
            "definition_id": int(definition_id),
            "version": int(version),
            "stable_key": stable_key,
            "offer_type": offer_type,
            "vault_ids": list(vault_ids),
            "price_minor": int(price_minor),
            "currency": currency,
            "mapped_drop_ids": list(mapped_drop_ids),
        }
    ]
    if family_id is not None:
        eligible[0]["family_id"] = int(family_id)
    snapshot = {
        "eligible": eligible,
        "ineligible": [],
        "selected": dict(eligible[0]),
        "ranking": {
            "policy_version": policy_version,
            "ranked_order": [int(definition_id)],
            "factors": {},
        },
        "conversation": {
            "lifecycle": "established",
            "current_topic": "movie",
            "recent_topics": [],
            "open_threads": [],
        },
        "fan": {
            "creator_id": creator_id,
            "user_id": user_id,
            "purchase_count": int(purchase_count),
            "total_spend_minor": int(purchase_count) * 2000,
            "purchased_vault_ids": [],
            "delivered_vault_ids": [],
            "recent_offer_count": 0,
            "recent_rejected_offer_count": 0,
            "recent_offered_vault_ids": [],
            "recent_purchase_count": 0,
            "recent_spend_minor": 0,
        },
        "history": {
            "creator_id": creator_id,
            "user_id": user_id,
            "total_offer_count": 1,
            "recent_offer_count": 0,
            "declined_offer_count": 0,
            "recent_declined_offer_count": 0,
            "state_counts": {},
            "has_active_offer": False,
            "active_offer_count": 0,
            "offered_vault_sets": [],
            "active_vault_sets": [],
            "null_snapshot_count": 0,
            "definition_identity_available": False,
        },
    }
    return {
        "opportunity_id": opportunity_id,
        "creator_id": creator_id,
        "user_id": user_id,
        "generation_id": f"synthetic:{creator_id}:{opportunity_id}",
        "evaluated_at": evaluated_at,
        "decision_snapshot": _json.dumps(snapshot, sort_keys=True),
        "selected_definition_id": int(definition_id),
        "selected_version": int(version),
        "selected_stable_key": stable_key,
        "decision_status": "SEALED" if sealed_offer_id is not None else "DECIDED",
        "sealed_offer_id": sealed_offer_id,
        "reengagement_of": reengagement_of,
        "outcome_state": outcome_state,
        "synthetic": SYNTHETIC_MARKER,
    }


def make_synthetic_evidence(
    *,
    creator_id: int,
    opportunity_id: int,
    exposure_state: str = "SENT",
    label: str = "POSITIVE",
    maturity_state: str = "MATURE",
    evidence_quality: str = "FULL",
    attribution_status: str = "attributed",
    transaction_id: str | None = "txn-synth-1",
    outcome_state: str | None = "PURCHASED",
    recovered: bool = False,
) -> dict[str, Any]:
    """Build a deterministic SYNTHETIC evidence classification (test-only).

    Shape-mirrors :func:`commerce.opportunity_evidence.
    classify_opportunity_evidence` output fields consumed by
    :func:`build_supervised_label`. Carries ``"synthetic"`` marker; never
    production evidence.
    """
    return {
        "creator_id": creator_id,
        "opportunity_id": opportunity_id,
        "exposure_state": exposure_state,
        "exposure_at": None,
        "label": label,
        "maturity_state": maturity_state,
        "maturity_policy_version": EXPECTED_MATURITY_POLICY_VERSION,
        "evidence_quality": evidence_quality,
        "attribution_status": attribution_status,
        "transaction_id": transaction_id,
        "outcome_state": outcome_state,
        "observed_outcome_state": outcome_state,
        "recovered": bool(recovered),
        "reengagement_of": None,
        "synthetic": SYNTHETIC_MARKER,
    }


def make_synthetic_bundle(
    *,
    creator_id: int,
    opportunity_id: int,
    evaluated_at: datetime,
    label: str = "POSITIVE",
    outcome_state: str | None = "PURCHASED",
    offer_type: str = "SMALL_BUNDLE",
    price_minor: int = 1999,
    user_id: int = 10,
    sealed_offer_id: int | None = None,
    reengagement_of: int | None = None,
    evidence_quality: str = "FULL",
    recovered: bool = False,
    transaction_id: str | None = "txn-synth-1",
) -> RowBundle:
    """Build one synthetic (input, evidence, ledger_row) bundle (test-only)."""
    from commerce.opportunity_optimization import build_optimization_input

    ledger_row = make_synthetic_ledger_row(
        creator_id=creator_id,
        opportunity_id=opportunity_id,
        user_id=user_id,
        evaluated_at=evaluated_at,
        offer_type=offer_type,
        price_minor=price_minor,
        sealed_offer_id=sealed_offer_id if sealed_offer_id is not None else 1000 + opportunity_id,
        reengagement_of=reengagement_of,
    )
    if label == "POSITIVE":
        exposure, maturity, quality, attribution = "SENT", "MATURE", evidence_quality, "attributed"
        txn = transaction_id
    elif label == "COMMERCIAL_NEGATIVE":
        exposure, maturity, quality, attribution = "SENT", "MATURE", "FULL", "unattributed"
        txn = None
    elif label == "PROCESS_NEGATIVE":
        exposure, maturity, quality, attribution = "SEND_ATTEMPTED", "MATURE", "FULL", "unattributed"
        txn = None
    elif label == "CENSORED":
        exposure, maturity, quality, attribution = "SENT", "IMMATURE", "FULL", "unattributed"
        txn = None
    else:
        exposure, maturity, quality, attribution = "UNAVAILABLE", "IMMATURE", "UNAVAILABLE", None
        txn = None
    evidence = make_synthetic_evidence(
        creator_id=creator_id,
        opportunity_id=opportunity_id,
        exposure_state=exposure,
        label=label,
        maturity_state=maturity,
        evidence_quality=quality,
        attribution_status=attribution or "unattributed",
        transaction_id=txn,
        outcome_state=outcome_state,
        recovered=recovered,
    )
    if reengagement_of is not None:
        evidence = dict(evidence)
        evidence["reengagement_of"] = reengagement_of
    inp = build_optimization_input(ledger_row=ledger_row, evidence=evidence)
    return RowBundle(input=inp, evidence=evidence, ledger_row=ledger_row)


def build_synthetic_cohort(
    *,
    creator_id: int,
    start: datetime,
    n: int = 12,
    step_hours: int = 24,
    user_base: int = 100,
) -> list[RowBundle]:
    """Build a deterministic synthetic cohort with learnable signal (test-only).

    Even indices model mature attributed purchases on a SMALL_BUNDLE/MID
    offer; odd indices model mature commercial negatives (alternating
    DECLINED/EXPIRED) on a SINGLE/LOW offer. The correlation is a
    synthetic fixture convenience for calibration tests — never a causal
    or production claim.
    """
    from datetime import timedelta as _timedelta

    _require_scope("creator_id", creator_id)
    _require_aware("start", start)
    bundles: list[RowBundle] = []
    for i in range(int(n)):
        evaluated_at = start + _timedelta(hours=int(step_hours) * i)
        oid = 1000 + i
        if i % 2 == 0:
            bundles.append(
                make_synthetic_bundle(
                    creator_id=creator_id,
                    opportunity_id=oid,
                    evaluated_at=evaluated_at,
                    label="POSITIVE",
                    outcome_state="PURCHASED",
                    offer_type="SMALL_BUNDLE",
                    price_minor=1999,
                    user_id=int(user_base) + i,
                    transaction_id=f"txn-synth-{creator_id}-{oid}",
                )
            )
        else:
            outcome = "DECLINED" if i % 4 == 1 else "EXPIRED"
            bundles.append(
                make_synthetic_bundle(
                    creator_id=creator_id,
                    opportunity_id=oid,
                    evaluated_at=evaluated_at,
                    label="COMMERCIAL_NEGATIVE",
                    outcome_state=outcome,
                    offer_type="SINGLE",
                    price_minor=499,
                    user_id=int(user_base) + i,
                )
            )
    return bundles


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "OPTIMIZER_VERSION",
    "EXPECTED_MATURITY_POLICY_VERSION",
    "SCORE_SEMANTICS",
    "EXTRAPOLATIVE_WARNING",
    "SYNTHETIC_MARKER",
    "MIN_TRAIN_TOTAL",
    "MIN_TRAIN_POSITIVE",
    "MIN_TRAIN_NEGATIVE",
    "PROTOTYPE_THRESHOLD",
    "FEATURE_NAMES",
    "TRAIN_LABEL_PURCHASED",
    "TRAIN_LABEL_DECLINED",
    "TRAIN_LABEL_EXPIRED",
    "CLASS_CENSORED",
    "CLASS_UNAVAILABLE",
    "CLASS_PROCESS_NEGATIVE",
    "CLASS_NO_SELECTION",
    "CLASS_NO_OPPORTUNITY",
    "CLASS_RECOVERED",
    "CLASS_UNATTRIBUTED",
    "CLASS_PARTIAL",
    "CLASS_REENGAGEMENT_CHILD",
    "PRIMARY_TRAIN_LABELS",
    "LabelOutcome",
    "TrainingExample",
    "RowBundle",
    "CreatorDataset",
    "OfflineModel",
    "TrainingOutcome",
    "OfflinePrediction",
    "EvalRow",
    "EvaluationReport",
    "build_supervised_label",
    "extract_features",
    "make_training_example",
    "build_creator_dataset",
    "train_creator_model",
    "predict_for_input",
    "prediction_to_advisory",
    "score_candidates_extrapolative",
    "chronological_holdout_evaluate",
    "expanding_window_evaluate",
    "model_metadata",
    "make_synthetic_ledger_row",
    "make_synthetic_evidence",
    "make_synthetic_bundle",
    "build_synthetic_cohort",
]
