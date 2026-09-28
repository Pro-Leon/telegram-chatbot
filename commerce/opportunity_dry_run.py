"""P3.5.4B — Historical advisory dry-run harness (bounded, read-only).

Measures hypothetical optimizer agreement/divergence against deterministic v1
decisions without affecting live commerce. For each historical ledger row the
harness reconstructs the frozen ``OptimizationInput``, runs a caller-supplied
advisor (or records abstention when none is supplied), validates the advice
through the historical validator, and compares it with the frozen v1
selection.

Read-only means truly read-only: the only database access is bounded
``SELECT`` statements against ``commerce_opportunity_decisions``; evidence
classification is pure (no per-row queries — no N+1); there are no provider,
LLM, Redis, sealing, execution, attribution, send, or ownership-mutation
calls anywhere in this module. Results are returned ephemerally and never
persisted. One malformed row classifies INPUT_UNAVAILABLE/VALIDATION_ERROR
and never aborts the sweep.

Grain: agreement is measured per opportunity ledger row; revenue is never
counted here at all (no revenue metrics exist in this module — re-engagement
children are distinct agreement rows sharing selection context, and purchase
counting stays with single-winner attribution).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from db.postgres import get_pool

from commerce.opportunity_evidence import classify_opportunity_evidence
from commerce.opportunity_optimization import (
    AdvisoryOptimizationResult,
    OptimizationInput,
    build_optimization_input,
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

logger = logging.getLogger("commerce.opportunity_dry_run")

#: Default bound per sweep (repository 25/50 convention).
DRY_RUN_LIMIT = 25

#: Hard cap per sweep (bounded even when callers ask for more).
DRY_RUN_MAX = 200


@dataclass(frozen=True)
class DryRunRowResult:
    """Per-row dry-run outcome (inspectable, never persisted)."""

    opportunity_id: int
    creator_id: int
    classification: str
    first_failed_gate: str | None
    v1_candidate: tuple[int, int] | None
    advisory_candidate: tuple[int, int] | None
    policy_version: str | None
    recovered: bool
    censored: bool
    governance_state: str | None
    denial_reasons: tuple[str, ...]
    error: str | None


@dataclass(frozen=True)
class DryRunSummary:
    """Frozen sweep summary (ephemeral; contract-validity counts only)."""

    total: int
    valid_inputs: int
    unavailable_inputs: int
    v1_selections: int
    recommendations: int
    abstentions: int
    agreements: int
    divergences: int
    advisory_invalid: int
    advisory_not_eligible: int
    validator_rejections_by_gate: tuple[tuple[str, int], ...]
    no_v1_selection: int
    recovered_count: int
    censored_count: int
    policy_version_breakdown: tuple[tuple[str, int], ...]
    rows: tuple[DryRunRowResult, ...]


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _coerce_aware(value: Any) -> datetime | None:
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


def _v1_of(input: OptimizationInput) -> tuple[int, int] | None:
    if isinstance(input.selected_definition_id, int) and not isinstance(
        input.selected_definition_id, bool
    ):
        if isinstance(input.selected_definition_version, int) and not isinstance(
            input.selected_definition_version, bool
        ):
            return (int(input.selected_definition_id), int(input.selected_definition_version))
    return None


def _ranked_order_of(row: dict[str, Any]) -> tuple[int, ...] | None:
    """Extract frozen v1 rank order for deterministic tie measurement (pure)."""
    import json as _json

    try:
        snapshot = row.get("decision_snapshot")
        parsed = _json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        if not isinstance(parsed, dict):
            return None
        ranking = parsed.get("ranking")
        if not isinstance(ranking, dict):
            return None
        order = ranking.get("ranked_order")
        if not isinstance(order, (list, tuple)) or not order:
            return None
        return tuple(int(did) for did in order if isinstance(did, int) and not isinstance(did, bool))
    except Exception:
        return None


async def run_dry_run(
    *,
    creator_id: int,
    limit: int = DRY_RUN_LIMIT,
    as_of: datetime | None = None,
    advisor: Callable[[OptimizationInput], Any] | None = None,
    evaluated_after: datetime | None = None,
    evaluated_before: datetime | None = None,
) -> DryRunSummary:
    """Run a bounded historical advisory dry-run for one creator (read-only).

    Scans at most ``limit`` ledger rows (deterministic ``ORDER BY creator_id,
    opportunity_id``) with an optional ``evaluated_at`` half-open range, then
    classifies each row with per-row isolation. ``advisor`` maps a frozen
    input to a hypothetical advisory result (sync or async); ``None`` records
    abstention for every row. ``as_of`` gates outcome-side evidence; naive
    values raise immediately. Never writes, never calls providers/LLM/Redis,
    never seals, executes, sends, or attributes.
    """
    creator_id = _require_scope("creator_id", creator_id)
    try:
        limit = int(limit)
    except Exception:
        limit = DRY_RUN_LIMIT
    limit = max(1, min(limit, DRY_RUN_MAX))
    now = datetime.now(UTC)
    if as_of is not None:
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be a timezone-aware datetime or None")
        now = as_of
    after = _coerce_aware(evaluated_after) if evaluated_after is not None else None
    before = _coerce_aware(evaluated_before) if evaluated_before is not None else None
    if evaluated_after is not None and after is None:
        raise ValueError("evaluated_after must be a datetime or None")
    if evaluated_before is not None and before is None:
        raise ValueError("evaluated_before must be a datetime or None")

    clauses = ["creator_id = $1"]
    params: list[Any] = [creator_id]
    if after is not None:
        params.append(after)
        clauses.append(f"evaluated_at >= ${len(params)}")
    if before is not None:
        params.append(before)
        clauses.append(f"evaluated_at < ${len(params)}")
    params.append(limit)
    query = (
        "SELECT * FROM commerce_opportunity_decisions "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY creator_id ASC, opportunity_id ASC "
        f"LIMIT ${len(params)}"
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    results: list[DryRunRowResult] = []
    for raw in rows or []:
        try:
            row = dict(raw)
        except Exception:
            continue
        try:
            result = await _with_advisor(row=row, as_of=now, advisor=advisor)
        except Exception as exc:
            result = DryRunRowResult(
                opportunity_id=int(row.get("opportunity_id", -1))
                if isinstance(row.get("opportunity_id"), int)
                else -1,
                creator_id=creator_id,
                classification=VALIDATION_ERROR,
                first_failed_gate=None,
                v1_candidate=None,
                advisory_candidate=None,
                policy_version=None,
                recovered=False,
                censored=False,
                governance_state=None,
                denial_reasons=(),
                error=f"row_failed:{type(exc).__name__}",
            )
        results.append(result)
    return _summarize(results)


async def _with_advisor(
    *,
    row: dict[str, Any],
    as_of: datetime,
    advisor: Callable[[OptimizationInput], Any] | None,
) -> DryRunRowResult:
    """Build the input, resolve advisor output (sync or async), classify."""
    from commerce.opportunity_optimization import build_optimization_input as _build

    try:
        input = _build(ledger_row=row)
    except Exception as exc:
        opportunity_id = row.get("opportunity_id")
        creator_id = row.get("creator_id")
        return DryRunRowResult(
            opportunity_id=int(opportunity_id) if isinstance(opportunity_id, int) else -1,
            creator_id=int(creator_id) if isinstance(creator_id, int) else -1,
            classification=INPUT_UNAVAILABLE,
            first_failed_gate=None,
            v1_candidate=None,
            advisory_candidate=None,
            policy_version=None,
            recovered=False,
            censored=False,
            governance_state=None,
            denial_reasons=(),
            error=f"input_unavailable:{type(exc).__name__}",
        )
    from commerce.opportunity_evidence import classify_opportunity_evidence as _classify

    try:
        evidence = _classify(row, as_of=as_of)
    except Exception:
        evidence = None
    recovered = bool(evidence.get("recovered", False)) if evidence else False
    censored = bool(evidence and evidence.get("label") == "CENSORED")
    try:
        advisory = advisor(input) if advisor is not None else None
        if isinstance(advisory, Awaitable):
            advisory = await advisory
    except Exception as exc:
        return DryRunRowResult(
            opportunity_id=int(input.opportunity_id),
            creator_id=int(input.creator_id),
            classification=VALIDATION_ERROR,
            first_failed_gate=None,
            v1_candidate=_v1_of(input),
            advisory_candidate=None,
            policy_version=input.policy_version,
            recovered=recovered,
            censored=censored,
            governance_state="UNEVALUABLE_FROM_HISTORY",
            denial_reasons=(),
            error=f"advisor_failed:{type(exc).__name__}",
        )
    if advisory is None or advisory.abstain or not advisory.candidate_scores:
        return DryRunRowResult(
            opportunity_id=int(input.opportunity_id),
            creator_id=int(input.creator_id),
            classification=ADVISORY_ABSTAIN,
            first_failed_gate=None,
            v1_candidate=_v1_of(input),
            advisory_candidate=None,
            policy_version=input.policy_version,
            recovered=recovered,
            censored=censored,
            governance_state="UNEVALUABLE_FROM_HISTORY",
            denial_reasons=(),
            error=None,
        )
    try:
        sealed_offer_id = row.get("sealed_offer_id")
        if sealed_offer_id is not None and (
            not isinstance(sealed_offer_id, int) or isinstance(sealed_offer_id, bool)
        ):
            sealed_offer_id = None
        result = validate_advisory(
            input,
            advisory,
            sealed_offer_id=sealed_offer_id,
            ranked_order=_ranked_order_of(row),
        )
    except Exception as exc:
        return DryRunRowResult(
            opportunity_id=int(input.opportunity_id),
            creator_id=int(input.creator_id),
            classification=VALIDATION_ERROR,
            first_failed_gate=None,
            v1_candidate=_v1_of(input),
            advisory_candidate=None,
            policy_version=input.policy_version,
            recovered=recovered,
            censored=censored,
            governance_state="UNEVALUABLE_FROM_HISTORY",
            denial_reasons=(),
            error=f"validation_failed:{type(exc).__name__}",
        )
    if recovered:
        # Separate cohort: validation still ran (gate detail preserved below
        # and counted in aggregate), but agreement is never pooled with FULL
        # rows — recovered inputs lack ranking/selection provenance parity.
        return DryRunRowResult(
            opportunity_id=int(input.opportunity_id),
            creator_id=int(input.creator_id),
            classification=RECOVERED_INPUT,
            first_failed_gate=result.first_failed_gate,
            v1_candidate=_v1_of(input),
            advisory_candidate=result.advisory_candidate,
            policy_version=input.policy_version,
            recovered=True,
            censored=censored,
            governance_state=result.governance_state,
            denial_reasons=tuple(result.denial_reasons),
            error=None,
        )
    if result.classification != VALID:
        return DryRunRowResult(
            opportunity_id=int(input.opportunity_id),
            creator_id=int(input.creator_id),
            classification=result.classification,
            first_failed_gate=result.first_failed_gate,
            v1_candidate=_v1_of(input),
            advisory_candidate=result.advisory_candidate,
            policy_version=input.policy_version,
            recovered=False,
            censored=censored,
            governance_state=result.governance_state,
            denial_reasons=tuple(result.denial_reasons),
            error=None,
        )
    comparison, v1 = compare_with_v1(input, result.advisory_candidate)
    return DryRunRowResult(
        opportunity_id=int(input.opportunity_id),
        creator_id=int(input.creator_id),
        classification=comparison,
        first_failed_gate=None,
        v1_candidate=v1,
        advisory_candidate=result.advisory_candidate,
        policy_version=input.policy_version,
        recovered=False,
        censored=censored,
        governance_state=result.governance_state,
        denial_reasons=(),
        error=None,
    )


def _summarize(results: list[DryRunRowResult]) -> DryRunSummary:
    gate_counts: dict[str, int] = {}
    policy_counts: dict[str, int] = {}
    counts = {
        "valid_inputs": 0,
        "unavailable_inputs": 0,
        "v1_selections": 0,
        "recommendations": 0,
        "abstentions": 0,
        "agreements": 0,
        "divergences": 0,
        "advisory_invalid": 0,
        "advisory_not_eligible": 0,
        "no_v1_selection": 0,
        "recovered_count": 0,
        "censored_count": 0,
    }
    for r in results:
        policy_counts[r.policy_version or "unknown"] = policy_counts.get(r.policy_version or "unknown", 0) + 1
        if r.recovered:
            counts["recovered_count"] += 1
        if r.censored:
            counts["censored_count"] += 1
        if r.v1_candidate is not None:
            counts["v1_selections"] += 1
        if r.first_failed_gate:
            gate_counts[r.first_failed_gate] = gate_counts.get(r.first_failed_gate, 0) + 1
        cls = r.classification
        if cls == INPUT_UNAVAILABLE:
            counts["unavailable_inputs"] += 1
        elif cls == VALIDATION_ERROR:
            counts["unavailable_inputs"] += 1
        else:
            counts["valid_inputs"] += 1
        if cls == ADVISORY_ABSTAIN:
            counts["abstentions"] += 1
        elif cls in (AGREEMENT, DIVERGENCE):
            counts["recommendations"] += 1
            counts["agreements" if cls == AGREEMENT else "divergences"] += 1
        elif cls == ADVISORY_INVALID:
            counts["recommendations"] += 1
            counts["advisory_invalid"] += 1
        elif cls == ADVISORY_NOT_ELIGIBLE:
            counts["recommendations"] += 1
            counts["advisory_not_eligible"] += 1
        elif cls == VALIDATOR_REJECTED:
            counts["recommendations"] += 1
        elif cls == NO_V1_SELECTION:
            counts["recommendations"] += 1
            counts["no_v1_selection"] += 1
        elif cls == RECOVERED_INPUT:
            counts["recommendations"] += 1
    return DryRunSummary(
        total=len(results),
        valid_inputs=counts["valid_inputs"],
        unavailable_inputs=counts["unavailable_inputs"],
        v1_selections=counts["v1_selections"],
        recommendations=counts["recommendations"],
        abstentions=counts["abstentions"],
        agreements=counts["agreements"],
        divergences=counts["divergences"],
        advisory_invalid=counts["advisory_invalid"],
        advisory_not_eligible=counts["advisory_not_eligible"],
        validator_rejections_by_gate=tuple(sorted(gate_counts.items())),
        no_v1_selection=counts["no_v1_selection"],
        recovered_count=counts["recovered_count"],
        censored_count=counts["censored_count"],
        policy_version_breakdown=tuple(sorted(policy_counts.items())),
        rows=tuple(results),
    )


__all__ = [
    "DRY_RUN_LIMIT",
    "DRY_RUN_MAX",
    "DryRunRowResult",
    "DryRunSummary",
    "run_dry_run",
]
