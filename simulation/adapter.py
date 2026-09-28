"""OpportunityAdapter — transforms SyntheticOpportunity to optimizer contracts.

No send/execute/purchase, no production writes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from simulation.data_origin import SYNTHETIC_MARKER
from simulation.snapshot import ledger_row_from_opportunity
from simulation.world import SyntheticOpportunity


def synthetic_ledger_row(
    opportunity: SyntheticOpportunity,
    *,
    sealed_offer_id: int | None = None,
    outcome_state: str = "PENDING",
    exposure_state: str = "NONE",
    exposure_at: datetime | None = None,
    outcome_at: datetime | None = None,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    """Build ledger dict suitable for build_optimization_input + classify_opportunity_evidence.

    Pure. Generates minimal columns plus optional exposure/outcome for evidence probe.
    """
    base = ledger_row_from_opportunity(
        opportunity,
        sealed_offer_id=sealed_offer_id,
        outcome_state=outcome_state,
        exposure_state=exposure_state,
    )
    # Add extra evidence-related columns
    if exposure_at is not None:
        if not isinstance(exposure_at, datetime) or exposure_at.tzinfo is None:
            raise ValueError("exposure_at must be tz-aware")
        base["exposure_at"] = exposure_at
    if outcome_at is not None:
        if not isinstance(outcome_at, datetime) or outcome_at.tzinfo is None:
            raise ValueError("outcome_at must be tz-aware")
        base["outcome_at"] = outcome_at
    if transaction_id is not None:
        base["transaction_id"] = str(transaction_id)
    # Markers for quarantine
    base["synthetic"] = SYNTHETIC_MARKER
    base["attribution_status"] = "unattributed"
    base["reengagement_of"] = None
    base["exposure_source"] = "sealed_execution" if exposure_state == "SENT" else None
    return base


def probe_optimization_input(
    opportunity: SyntheticOpportunity, evidence: dict[str, Any] | None = None
) -> Any:
    """Attempt real build_optimization_input on synthetic opportunity.

    Returns OptimizationInput on success, raises on failure (caller should assert no exception).
    """
    from commerce.opportunity_optimization import build_optimization_input

    row = synthetic_ledger_row(opportunity)
    # evidence can be supplied for reengagement etc., else synthetic minimal
    if evidence is None:
        # Use minimal synthetic evidence classification shape? But build_optimization_input does not require evidence; it builds from ledger + optional evidence param.
        # For Phase 2 we call without evidence to prove basic path succeeds.
        return build_optimization_input(ledger_row=row)
    return build_optimization_input(ledger_row=row, evidence=evidence)


def probe_evidence_classification(ledger_row: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    """Call real classify_opportunity_evidence on synthetic ledger row."""
    from commerce.opportunity_evidence import classify_opportunity_evidence

    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("as_of must be tz-aware")
    return classify_opportunity_evidence(ledger_row, as_of=as_of)


def probe_supervised_label(
    ledger_row: dict[str, Any], evidence: dict[str, Any] | None = None
) -> Any:
    """Call real build_supervised_label on synthetic evidence+row."""
    from commerce.offline_optimizer import build_supervised_label

    if evidence is None:
        # Call evidence classifier to get synthetic-compatible shape?
        # For Phase 2 probe we allow None to see UNAVAILABLE path
        pass
    return build_supervised_label(evidence=evidence, ledger_row=ledger_row)


__all__ = [
    "probe_evidence_classification",
    "probe_optimization_input",
    "probe_supervised_label",
    "synthetic_ledger_row",
]
