"""P3.6 — Production Evidence Accrual & Optimizer Readiness (observability only).

Read-only readiness assessment answering: when a real commercial
opportunity occurs, can we reconstruct what was known at decision time,
what was selected, whether it was exposed, what happened afterward, and
whether that observation is eligible for future offline training?

This module is OBSERVABILITY ONLY. It changes no production behavior:

- Deterministic v1 ranking remains the sole commerce authority for
  eligibility, ownership, provider truth, governance, ranking, sealing,
  sending, execution, and attribution.
- The offline advisory prototype remains outside production authority.
  This module never imports, invokes, scores, persists, or flags it, and
  creates no production import of it (the P3.5.6 barrier test scans every
  ``commerce/*.py`` file, so this file deliberately contains no reference
  to that module — mirrored constants below are restated with provenance
  and parity-checked by tests, which are allowed to reference both sides).
- No price logic, no elasticity, no causal/uplift/counterfactual claims,
  no conversation-quality optimization, no experiments, no optimizer
  weights, no ranking overrides, no feature flags for selection.
- No persistence: no tables, no model registry, no prediction storage.
  The only database access is bounded read-only ``SELECT`` statements in
  the explicitly marked async collectors; every other function is pure.

Evidence path consumed (all pre-existing P3.5 primitives, unmodified):

    decision (ledger frozen snapshot)
        -> frozen advisory input (P3.5.4A builder, pure)
        -> exposure (P3.5.3B send writer, monotonic)
        -> maturity/censoring (P3.5.3B policy p353b.v1, 168h)
        -> outcome (single-winner P3.5.2 attribution)
        -> primary eligibility (mirror of the prototype label rules)

Primary eligibility mirror (must match the prototype label semantics;
parity is asserted by tests, which may import both sides):

- eligible positive: mature SENT FULL non-recovered attributed PURCHASED
  with transaction, non-child, non-synthetic.
- eligible negative: mature SENT FULL non-recovered COMMERCIAL_NEGATIVE
  (DECLINED/EXPIRED), non-child, non-synthetic.
- everything else (PROCESS_NEGATIVE, CENSORED, UNAVAILABLE,
  NO_OPPORTUNITY/NO_SELECTION, RECOVERED, PARTIAL, UNATTRIBUTED,
  re-engagement child, synthetic fixture, unreconstructable input) is
  excluded from the primary cohort but stays visible in diagnostics.

Known snapshot gap (documented, not fabricated): production decision
snapshots do not capture ``family_id`` (``candidate_identity_snapshot``
projects definition/version/stable_key/offer_type/vault/price/currency/
drops only), so the frozen family signal is always absent and the
corresponding bucket is constant. See ``FEATURE_READINESS_NOTES``. No
value is invented for it.

Fail-closed rule: any observation that cannot be reconstructed safely
classifies unavailable/input-unavailable, keeps its provenance, is
excluded from the primary cohort, and surfaces in diagnostics. Malformed
evidence never defaults to PURCHASED/DECLINED/EXPIRED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commerce.opportunity_evidence import classify_opportunity_evidence
from commerce.opportunity_optimization import build_optimization_input

# ---------------------------------------------------------------------------
# Versions and prototype-floor mirrors (restated, never imported).
# ---------------------------------------------------------------------------
#
# The offline prototype module is intentionally not imported here so that
# no production file references it (P3.5.6 barrier). These values mirror it;
# tests assert parity. If the prototype ever changes them, update here
# explicitly — never silently.

#: Readiness report identity for this implementation.
READINESS_VERSION = "p36.readiness.v1"

#: Expected frozen feature-schema identity (mirror; parity-tested).
EXPECTED_FEATURE_SCHEMA = "p356.features.v1"

#: Authoritative maturity policy consumed from P3.5.3B (not redefined).
EXPECTED_MATURITY_POLICY = "p353b.v1"

#: Prototype training floors (mirrors; prototype diagnostics, not claims of
#: statistical sufficiency). Never lowered to make a creator "ready".
MIN_TRAIN_TOTAL = 6
MIN_TRAIN_POSITIVE = 2
MIN_TRAIN_NEGATIVE = 2

#: Minimum test-side primary rows for a chronological holdout to be feasible.
MIN_HOLDOUT_TEST = 1

#: Default bound per readiness sweep (repository 25/50 convention).
READINESS_LIMIT = 500

#: Hard cap per readiness sweep (bounded even when callers ask for more).
READINESS_MAX = 2000

#: Frozen feature names whose completeness is checked without importing the
#: prototype (mirror; parity-tested).
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

#: Per-feature source/timestamp/frozen assessment for the readiness contract.
#: ``status`` is "suppliable" (frozen at decision time), "degraded"
#: (suppliable but constant — see note), or "excluded" (intentionally not a
#: feature). Nothing here is read from live state at evaluation time.
FEATURE_READINESS_NOTES: tuple[tuple[str, str, str], ...] = (
    ("offer_type", "suppliable", "frozen selected candidate, snapshot"),
    ("price_bucket", "suppliable", "frozen pinned price fact only, bucketed"),
    ("currency", "suppliable", "frozen selected candidate, snapshot"),
    ("family_presence", "degraded", "snapshot never captures family_id; always NO_FAMILY, never fabricated"),
    ("vault_count_bucket", "suppliable", "frozen selected candidate, snapshot"),
    ("drop_mapping", "suppliable", "frozen selected candidate, snapshot"),
    ("fan_purchase_bucket", "suppliable", "frozen fan snapshot at evaluated_at"),
    ("fan_spend_bucket", "suppliable", "frozen fan snapshot at evaluated_at"),
    ("fan_recent_offer_bucket", "suppliable", "frozen fan snapshot at evaluated_at"),
    ("fan_rejected_bucket", "suppliable", "frozen fan snapshot at evaluated_at"),
    ("history_total_bucket", "suppliable", "frozen history snapshot at evaluated_at"),
    ("history_declined_bucket", "suppliable", "frozen history snapshot at evaluated_at"),
    ("has_active_offer", "suppliable", "frozen history snapshot at evaluated_at"),
    ("lifecycle", "suppliable", "frozen conversation snapshot at evaluated_at"),
    ("topic_presence", "suppliable", "frozen conversation snapshot at evaluated_at"),
    ("owned_count_bucket", "suppliable", "frozen ownership snapshot (fan purchased set at decision)"),
)

#: Synthetic namespace prefixes that must never enter the primary cohort.
SYNTHETIC_GENERATION_PREFIX = "synthetic:"
RECOVERY_GENERATION_PREFIX = "recovered:"

# ---------------------------------------------------------------------------
# Small pure helpers (no I/O).
# ---------------------------------------------------------------------------


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _require_aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _is_synthetic_row(row: Any) -> bool:
    """Detect synthetic fixture rows (test-only, never production evidence)."""
    try:
        if isinstance(row, dict):
            if row.get("synthetic"):
                return True
            generation = row.get("generation_id")
        else:
            generation = getattr(row, "generation_id", None)
    except Exception:
        return False
    return isinstance(generation, str) and generation.startswith(SYNTHETIC_GENERATION_PREFIX)


def _is_child_row(row: Any) -> bool:
    try:
        parent = row.get("reengagement_of") if isinstance(row, dict) else getattr(row, "reengagement_of", None)
    except Exception:
        return False
    return isinstance(parent, int) and not isinstance(parent, bool) and parent > 0


# ---------------------------------------------------------------------------
# Immutable readiness structures (in memory, never persisted).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowDiagnosis:
    """Per-row readiness diagnosis (ephemeral, fail-closed)."""

    opportunity_id: int
    creator_id: int
    evaluated_at: datetime | None
    evidence_label: str
    maturity_state: str
    exposure_state: str
    evidence_quality: str
    recovered: bool
    is_child: bool
    synthetic: bool
    attribution_status: str | None
    has_transaction: bool
    outcome_state: str | None
    input_status: str
    missing_features: tuple[str, ...]
    policy_version: str | None
    definition: tuple[int, int] | None
    eligible_primary: bool
    binary: int | None
    exclusion: str
    anomalies: tuple[str, ...]


@dataclass(frozen=True)
class CreatorReadiness:
    """Per-creator readiness (never pooled across creators)."""

    creator_id: int
    n_rows: int
    n_primary: int
    n_pos: int
    n_neg: int
    n_process_negative: int
    n_censored: int
    n_unavailable: int
    n_recovered: int
    n_partial: int
    n_unattributed: int
    n_children: int
    n_synthetic: int
    n_input_unavailable: int
    earliest_evaluated_at: datetime | None
    latest_evaluated_at: datetime | None
    span_hours: float | None
    floors_met: bool
    floors_detail: str
    holdout_feasible: bool
    holdout_detail: str
    policy_breakdown: tuple[tuple[str, int], ...]
    definition_breakdown: tuple[tuple[str, int], ...]
    anomaly_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class GlobalReadiness:
    """Readiness sweep summary (ephemeral, descriptive only)."""

    readiness_version: str
    feature_schema: str
    maturity_policy: str
    as_of: datetime
    n_rows: int
    n_creators: int
    n_sent: int
    n_full_mature_sent: int
    n_primary: int
    n_pos: int
    n_neg: int
    n_process_negative: int
    n_censored: int
    n_unavailable: int
    n_recovered: int
    n_partial: int
    n_unattributed: int
    n_children: int
    n_synthetic: int
    n_input_unavailable: int
    n_no_opportunity: int
    n_no_selection: int
    multiple_purchase_offers: tuple[tuple[int, int], ...]
    creators: tuple[CreatorReadiness, ...]
    rows: tuple[RowDiagnosis, ...]


# ---------------------------------------------------------------------------
# Primary eligibility mirror (parity-tested against the prototype rules).
# ---------------------------------------------------------------------------


def _primary_eligibility(
    evidence: dict[str, Any],
    *,
    is_child: bool,
    synthetic: bool,
    input_ok: bool,
) -> tuple[bool, int | None, str]:
    """Mirror the prototype primary-cohort rules (pure).

    Returns ``(eligible, binary, exclusion)``. Malformed evidence never
    defaults to a supervised label. The malformed COMMERCIAL_NEGATIVE
    fallback intentionally mirrors the prototype (non-EXPIRED maps to
    DECLINED) so readiness counts match prototype training counts; see
    the P3.5.6 audit finding F1, deliberately left un-diverged here.
    """
    if synthetic:
        return False, None, "synthetic_excluded"
    if not input_ok:
        return False, None, "input_unavailable"
    if not isinstance(evidence, dict):
        return False, None, "unavailable"
    if bool(evidence.get("recovered", False)):
        return False, None, "recovered"
    if is_child:
        return False, None, "reengagement_child"
    quality = str(evidence.get("evidence_quality") or "UNAVAILABLE")
    if quality == "PARTIAL":
        return False, None, "partial"
    maturity = str(evidence.get("maturity_state") or "IMMATURE")
    if maturity != "MATURE":
        return False, None, "censored"
    label = str(evidence.get("label") or "UNAVAILABLE")
    if label in ("NO_OPPORTUNITY", "NO_SELECTION"):
        return False, None, "no_selection" if label == "NO_SELECTION" else "no_opportunity"
    if label == "PROCESS_NEGATIVE":
        return False, None, "process_negative"
    if label in ("CENSORED", "UNAVAILABLE"):
        return False, None, "censored" if label == "CENSORED" else "unavailable"
    exposure = str(evidence.get("exposure_state") or "UNAVAILABLE")
    if exposure != "SENT":
        return False, None, "no_sent_exposure"
    if quality != "FULL":
        if quality == "UNATTRIBUTED":
            return False, None, "unattributed"
        return False, None, "unavailable"
    if label == "POSITIVE":
        attribution = evidence.get("attribution_status")
        txn = evidence.get("transaction_id")
        has_txn = isinstance(txn, str) and bool(txn.strip())
        if attribution != "attributed" or not has_txn:
            return False, None, "unattributed"
        return True, 1, "eligible"
    if label == "COMMERCIAL_NEGATIVE":
        outcome = evidence.get("outcome_state") or evidence.get("observed_outcome_state")
        if outcome == "EXPIRED":
            return True, 0, "eligible"
        return True, 0, "eligible"
    return False, None, "censored"


def _check_input_features(ledger_row: Any, evidence: Any) -> tuple[str, tuple[str, ...]]:
    """Check frozen-input reconstructability without live reads (pure).

    Returns ``(status, missing)`` where status is ``"OK"`` or an
    ``"INPUT_UNAVAILABLE:<reason>"`` string. Never raises on bad data.
    """
    try:
        built = build_optimization_input(ledger_row=ledger_row, evidence=evidence)
    except Exception as exc:
        return f"INPUT_UNAVAILABLE:{type(exc).__name__}", ()
    missing: list[str] = []
    try:
        selected = None
        want = (int(built.selected_definition_id), int(built.selected_definition_version))  # type: ignore[arg-type]
        for cand in built.frozen_candidates:
            try:
                if cand.identity() == want:
                    selected = cand
                    break
            except Exception:
                continue
        if selected is None:
            missing.append("selected_candidate")
        if built.fan_commercial_summary is None:
            missing.append("fan_commercial_summary")
        if built.offer_history_summary is None:
            missing.append("offer_history_summary")
        if built.ownership_context is None:
            missing.append("ownership_context")
        if built.conversation_context is None:
            missing.append("conversation_context")
    except Exception:
        return "INPUT_UNAVAILABLE:check_failed", ()
    if missing:
        return f"INPUT_UNAVAILABLE:{'|'.join(missing)}", tuple(missing)
    return "OK", ()


def diagnose_row(row: Any, *, as_of: datetime) -> RowDiagnosis:
    """Diagnose one ledger row for optimizer-training eligibility (pure).

    Fail-closed: unparseable rows diagnose unavailable with provenance
    preserved; never a supervised label.
    """
    _require_aware("as_of", as_of)
    try:
        evidence = classify_opportunity_evidence(row, as_of=as_of)
    except Exception:
        evidence = {
            "label": "UNAVAILABLE",
            "maturity_state": "IMMATURE",
            "exposure_state": "UNAVAILABLE",
            "evidence_quality": "UNAVAILABLE",
            "recovered": False,
            "attribution_status": None,
            "transaction_id": None,
            "outcome_state": None,
            "maturity_policy_version": EXPECTED_MATURITY_POLICY,
        }
    synthetic = _is_synthetic_row(row)
    child = _is_child_row(row)
    try:
        opportunity_id = int(row.get("opportunity_id")) if isinstance(row, dict) else int(getattr(row, "opportunity_id"))
    except Exception:
        opportunity_id = -1
    try:
        creator_id = int(row.get("creator_id")) if isinstance(row, dict) else int(getattr(row, "creator_id"))
    except Exception:
        creator_id = -1
    try:
        evaluated_raw = row.get("evaluated_at") if isinstance(row, dict) else getattr(row, "evaluated_at", None)
        evaluated_at = evaluated_raw if isinstance(evaluated_raw, datetime) and evaluated_raw.tzinfo is not None else None
    except Exception:
        evaluated_at = None
    input_status, missing = _check_input_features(row, evidence)
    eligible, binary, exclusion = _primary_eligibility(
        evidence, is_child=child, synthetic=synthetic, input_ok=(input_status == "OK")
    )
    anomalies: list[str] = []
    try:
        if str(evidence.get("maturity_policy_version") or "") != EXPECTED_MATURITY_POLICY:
            anomalies.append("maturity_policy_mismatch")
    except Exception:
        anomalies.append("maturity_policy_mismatch")
    try:
        exposure = str(evidence.get("exposure_state") or "UNAVAILABLE")
        sealed = row.get("sealed_offer_id") if isinstance(row, dict) else getattr(row, "sealed_offer_id", None)
        outcome_state = evidence.get("outcome_state")
        if exposure == "SENT" and sealed is None and outcome_state != "PURCHASED":
            anomalies.append("sent_without_linkage")
    except Exception:
        pass
    try:
        raw_outcome = row.get("outcome_state") if isinstance(row, dict) else getattr(row, "outcome_state", None)
        raw_attribution = row.get("attribution_status") if isinstance(row, dict) else getattr(row, "attribution_status", None)
        raw_txn = row.get("transaction_id") if isinstance(row, dict) else getattr(row, "transaction_id", None)
        if raw_outcome == "PURCHASED" and (
            raw_attribution != "attributed" or not (isinstance(raw_txn, str) and raw_txn.strip())
        ):
            anomalies.append("purchase_without_attribution")
    except Exception:
        pass
    try:
        if input_status != "OK":
            if "scope_mismatch" in input_status or creator_id == -1:
                anomalies.append("creator_mismatch")
            else:
                anomalies.append("missing_frozen_input")
    except Exception:
        pass
    try:
        txn = evidence.get("transaction_id")
        has_txn = isinstance(txn, str) and bool(txn.strip())
    except Exception:
        has_txn = False
    try:
        raw_did = row.get("selected_definition_id") if isinstance(row, dict) else getattr(row, "selected_definition_id", None)
        raw_ver = row.get("selected_version") if isinstance(row, dict) else getattr(row, "selected_version", None)
        definition = (
            (int(raw_did), int(raw_ver))
            if isinstance(raw_did, int)
            and not isinstance(raw_did, bool)
            and isinstance(raw_ver, int)
            and not isinstance(raw_ver, bool)
            else None
        )
    except Exception:
        definition = None
    try:
        import json as _json

        raw_snapshot = row.get("decision_snapshot") if isinstance(row, dict) else getattr(row, "decision_snapshot", None)
        parsed = _json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
        ranking = parsed.get("ranking") if isinstance(parsed, dict) else None
        policy_version = ranking.get("policy_version") if isinstance(ranking, dict) else None
        if not isinstance(policy_version, str) or not policy_version.strip():
            policy_version = None
    except Exception:
        policy_version = None
    return RowDiagnosis(
        opportunity_id=opportunity_id,
        creator_id=creator_id,
        evaluated_at=evaluated_at,
        evidence_label=str(evidence.get("label") or "UNAVAILABLE"),
        maturity_state=str(evidence.get("maturity_state") or "IMMATURE"),
        exposure_state=str(evidence.get("exposure_state") or "UNAVAILABLE"),
        evidence_quality=str(evidence.get("evidence_quality") or "UNAVAILABLE"),
        recovered=bool(evidence.get("recovered", False)),
        is_child=child,
        synthetic=synthetic,
        attribution_status=evidence.get("attribution_status"),
        has_transaction=has_txn,
        outcome_state=evidence.get("outcome_state"),
        input_status=input_status,
        missing_features=tuple(missing),
        policy_version=policy_version,
        definition=definition,
        eligible_primary=bool(eligible),
        binary=binary,
        exclusion=exclusion,
        anomalies=tuple(anomalies),
    )


def _holdout_feasibility(primary: list[RowDiagnosis]) -> tuple[bool, str]:
    """Determine chronological-holdout feasibility for one creator (pure).

    Tries every midpoint cutoff between consecutive distinct evaluation
    times: the train side (strictly before the cutoff) must meet the
    prototype floors, and the test side must hold at least
    ``MIN_HOLDOUT_TEST`` primary rows. No random splitting. Rows without
    timestamps cannot support a holdout.
    """
    timed = sorted(
        [r for r in primary if r.evaluated_at is not None],
        key=lambda r: (r.evaluated_at, r.opportunity_id),
    )
    if len(timed) < MIN_TRAIN_TOTAL + MIN_HOLDOUT_TEST:
        return False, "insufficient_primary_rows"
    stamps: list[datetime] = []
    for row in timed:
        assert row.evaluated_at is not None
        if not stamps or row.evaluated_at > stamps[-1]:
            stamps.append(row.evaluated_at)
    if len(stamps) < 2:
        return False, "insufficient_chronological_span"
    for cutoff in stamps[1:]:
        train = [r for r in timed if r.evaluated_at < cutoff]  # type: ignore[operator]
        test = [r for r in timed if r.evaluated_at >= cutoff]  # type: ignore[operator]
        n_pos = sum(1 for r in train if r.binary == 1)
        n_neg = sum(1 for r in train if r.binary == 0)
        if len(train) >= MIN_TRAIN_TOTAL and n_pos >= MIN_TRAIN_POSITIVE and n_neg >= MIN_TRAIN_NEGATIVE and len(test) >= MIN_HOLDOUT_TEST:
            return True, f"cutoff={cutoff.isoformat()} train={len(train)} test={len(test)}"
    return False, "no_cutoff_meets_floors_with_holdout"


def summarize_rows(rows: Any, *, as_of: datetime) -> GlobalReadiness:
    """Summarize ledger rows into a creator-local readiness report (pure)."""
    _require_aware("as_of", as_of)
    rows = list(rows or [])
    diagnosed = [diagnose_row(r, as_of=as_of) for r in rows]
    creators: dict[int, list[RowDiagnosis]] = {}
    for diag in diagnosed:
        creators.setdefault(int(diag.creator_id), []).append(diag)
    # Duplicate-revenue guard: one sealed offer must yield at most one
    # raw PURCHASED ledger row (single-winner P3.5.2 semantics).
    winners: dict[tuple[int, int], int] = {}
    for raw in rows:
        try:
            state = raw.get("outcome_state") if isinstance(raw, dict) else getattr(raw, "outcome_state", None)
            if state != "PURCHASED":
                continue
            creator = int(raw.get("creator_id")) if isinstance(raw, dict) else int(getattr(raw, "creator_id"))
            offer = int(raw.get("sealed_offer_id")) if isinstance(raw, dict) else int(getattr(raw, "sealed_offer_id"))
            winners[(creator, offer)] = winners.get((creator, offer), 0) + 1
        except Exception:
            continue
    multiple = tuple(sorted((creator, offer) for (creator, offer), count in winners.items() if count > 1))
    creator_reports: list[CreatorReadiness] = []
    for creator_id in sorted(creators):
        diags = sorted(
            creators[creator_id],
            key=lambda d: (d.evaluated_at or datetime.min.replace(tzinfo=UTC), d.opportunity_id),
        )
        primary = [d for d in diags if d.eligible_primary]
        pos = sum(1 for d in primary if d.binary == 1)
        neg = sum(1 for d in primary if d.binary == 0)
        times = [d.evaluated_at for d in primary if d.evaluated_at is not None]
        earliest = min(times) if times else None
        latest = max(times) if times else None
        span = (latest - earliest).total_seconds() / 3600.0 if earliest and latest else None
        floors_ok = len(primary) >= MIN_TRAIN_TOTAL and pos >= MIN_TRAIN_POSITIVE and neg >= MIN_TRAIN_NEGATIVE
        floors_detail = f"primary={len(primary)} pos={pos} neg={neg} floors=({MIN_TRAIN_TOTAL},{MIN_TRAIN_POSITIVE},{MIN_TRAIN_NEGATIVE})"
        feasible, holdout_detail = _holdout_feasibility(primary)
        policies: dict[str, int] = {}
        definitions: dict[str, int] = {}
        for d in primary:
            policies[d.policy_version or "unknown"] = policies.get(d.policy_version or "unknown", 0) + 1
            key = f"{d.definition[0]}v{d.definition[1]}" if d.definition else "unknown"
            definitions[key] = definitions.get(key, 0) + 1
        anomaly_counts: dict[str, int] = {}
        for d in diags:
            for flag in d.anomalies:
                anomaly_counts[flag] = anomaly_counts.get(flag, 0) + 1

        def _count(pred: Any) -> int:
            return sum(1 for d in diags if pred(d))

        creator_reports.append(
            CreatorReadiness(
                creator_id=creator_id,
                n_rows=len(diags),
                n_primary=len(primary),
                n_pos=pos,
                n_neg=neg,
                n_process_negative=_count(lambda d: d.exclusion == "process_negative"),
                n_censored=_count(lambda d: d.exclusion == "censored" or d.exclusion == "no_sent_exposure"),
                n_unavailable=_count(lambda d: d.exclusion in ("unavailable", "input_unavailable")),
                n_recovered=_count(lambda d: d.exclusion == "recovered"),
                n_partial=_count(lambda d: d.exclusion == "partial"),
                n_unattributed=_count(lambda d: d.exclusion == "unattributed"),
                n_children=_count(lambda d: d.is_child),
                n_synthetic=_count(lambda d: d.synthetic),
                n_input_unavailable=_count(lambda d: d.input_status != "OK"),
                earliest_evaluated_at=earliest,
                latest_evaluated_at=latest,
                span_hours=span,
                floors_met=floors_ok,
                floors_detail=floors_detail,
                holdout_feasible=feasible,
                holdout_detail=holdout_detail,
                policy_breakdown=tuple(sorted(policies.items())),
                definition_breakdown=tuple(sorted(definitions.items())),
                anomaly_counts=tuple(sorted(anomaly_counts.items())),
            )
        )
    by_exclusion = [d.exclusion for d in diagnosed]

    def _n(*names: str) -> int:
        return sum(1 for e in by_exclusion if e in names)

    return GlobalReadiness(
        readiness_version=READINESS_VERSION,
        feature_schema=EXPECTED_FEATURE_SCHEMA,
        maturity_policy=EXPECTED_MATURITY_POLICY,
        as_of=as_of,
        n_rows=len(diagnosed),
        n_creators=len(creator_reports),
        n_sent=sum(1 for d in diagnosed if d.exposure_state == "SENT"),
        n_full_mature_sent=sum(
            1
            for d in diagnosed
            if d.exposure_state == "SENT" and d.maturity_state == "MATURE" and d.evidence_quality == "FULL"
        ),
        n_primary=sum(1 for d in diagnosed if d.eligible_primary),
        n_pos=sum(1 for d in diagnosed if d.eligible_primary and d.binary == 1),
        n_neg=sum(1 for d in diagnosed if d.eligible_primary and d.binary == 0),
        n_process_negative=_n("process_negative"),
        n_censored=_n("censored", "no_sent_exposure"),
        n_unavailable=_n("unavailable", "input_unavailable"),
        n_recovered=_n("recovered"),
        n_partial=_n("partial"),
        n_unattributed=_n("unattributed"),
        n_children=sum(1 for d in diagnosed if d.is_child),
        n_synthetic=sum(1 for d in diagnosed if d.synthetic),
        n_input_unavailable=sum(1 for d in diagnosed if d.input_status != "OK"),
        n_no_opportunity=_n("no_opportunity"),
        n_no_selection=_n("no_selection"),
        multiple_purchase_offers=multiple,
        creators=tuple(creator_reports),
        rows=tuple(diagnosed),
    )


# ---------------------------------------------------------------------------
# Bounded read-only collectors (SELECT only; no writes of any kind).
# ---------------------------------------------------------------------------

_READINESS_COLUMNS = (
    "opportunity_id, creator_id, user_id, generation_id, evaluated_at, "
    "decision_snapshot, selected_definition_id, selected_version, "
    "selected_stable_key, no_selection_reason, decision_status, "
    "seal_subreason, sealed_offer_id, drop_cuid, outcome_state, outcome_at, "
    "transaction_id, purchased_price_minor, purchased_currency, "
    "attribution_status, attribution_confidence, reengagement_of, "
    "exposure_state, exposure_at, exposure_source, created_at, updated_at"
)


async def fetch_opportunity_rows(
    *,
    creator_id: int | None = None,
    limit: int = READINESS_LIMIT,
    evaluated_after: datetime | None = None,
    evaluated_before: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch bounded ledger rows for readiness (read-only SELECT).

    Creator-scoped when ``creator_id`` is given; otherwise global with the
    same hard cap (per-row creator accounting still applies downstream —
    creators are never pooled). Raises on invalid scope or naive datetimes.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = READINESS_LIMIT
    limit = max(1, min(limit, READINESS_MAX))
    if creator_id is not None:
        _require_scope("creator_id", creator_id)
    if evaluated_after is not None:
        _require_aware("evaluated_after", evaluated_after)
    if evaluated_before is not None:
        _require_aware("evaluated_before", evaluated_before)
    from db.postgres import get_pool

    clauses: list[str] = []
    params: list[Any] = []
    if creator_id is not None:
        params.append(int(creator_id))
        clauses.append(f"creator_id = ${len(params)}")
    if evaluated_after is not None:
        params.append(evaluated_after)
        clauses.append(f"evaluated_at >= ${len(params)}")
    if evaluated_before is not None:
        params.append(evaluated_before)
        clauses.append(f"evaluated_at < ${len(params)}")
    params.append(limit)
    query = (
        f"SELECT {_READINESS_COLUMNS} FROM commerce_opportunity_decisions "
        + (f"WHERE {' AND '.join(clauses)} " if clauses else "")
        + "ORDER BY creator_id ASC, opportunity_id ASC "
        + f"LIMIT ${len(params)}"
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        fetched = await conn.fetch(query, *params)
    return [dict(r) for r in (fetched or [])]


async def assess_readiness(
    *,
    creator_id: int | None = None,
    limit: int = READINESS_LIMIT,
    as_of: datetime | None = None,
    evaluated_after: datetime | None = None,
    evaluated_before: datetime | None = None,
) -> GlobalReadiness:
    """Collect bounded rows and summarize readiness (read-only).

    Never writes, never calls providers, never seals/sends/attributes,
    never invokes any optimizer. Naive ``as_of`` raises immediately.
    """
    now = _require_aware("as_of", as_of) if as_of is not None else datetime.now(UTC)
    rows = await fetch_opportunity_rows(
        creator_id=creator_id,
        limit=limit,
        evaluated_after=evaluated_after,
        evaluated_before=evaluated_before,
    )
    return summarize_rows(rows, as_of=now)


__all__ = [
    "CreatorReadiness",
    "FEATURE_NAMES",
    "FEATURE_READINESS_NOTES",
    "GlobalReadiness",
    "MIN_HOLDOUT_TEST",
    "MIN_TRAIN_NEGATIVE",
    "MIN_TRAIN_POSITIVE",
    "MIN_TRAIN_TOTAL",
    "EXPECTED_FEATURE_SCHEMA",
    "EXPECTED_MATURITY_POLICY",
    "READINESS_LIMIT",
    "READINESS_MAX",
    "READINESS_VERSION",
    "RECOVERY_GENERATION_PREFIX",
    "RowDiagnosis",
    "SYNTHETIC_GENERATION_PREFIX",
    "_holdout_feasibility",
    "_primary_eligibility",
    "assess_readiness",
    "diagnose_row",
    "fetch_opportunity_rows",
    "summarize_rows",
]
