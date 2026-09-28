"""P3.5.4B — Historical deterministic validator (pure, advisory only).

Answers, for frozen decision-time facts: would a hypothetical advisory
recommendation have passed the same structural decision-time constraints,
and does it agree with deterministic v1?

This validator is HISTORICAL-ONLY. It never answers "would this pass right
now" — live preflight remains the existing sealing path, and no second live
mode exists here. It performs no I/O, no provider calls, no sealing, no
execution, no attribution, and no commerce mutation of any kind.

Mandatory gate order (ownership evidence precedes eligibility because
``evaluate_opportunity_eligibility`` consumes ``owned_vault_ids``):

    creator_scope → candidate_identity → ownership → eligibility
        → single_drop_rule → governance → provider_truth

Gate semantics:

- Structural advisory defects (wrong creator/opportunity/policy, unknown or
  novel candidate, duplicates, malformed refs) → ADVISORY_INVALID.
- References that exist but fail hard commercial rules (ownership overlap,
  eligibility denial) → ADVISORY_NOT_ELIGIBLE.
- Structural sealing-rule failure (Drop mapping count) → VALIDATOR_REJECTED.
- Governance is always UNEVALUABLE_FROM_HISTORY here: pressure, fatigue,
  cooldown, risk, and operator pause state are not frozen in decision
  snapshots, so historical mode records the gap instead of inventing a pass
  (or a failure). Full governance re-check belongs to live preflight, i.e.
  the existing sealing-time path.
- Provider truth is established by frozen sealing evidence (seal record or
  recorded send); it is never re-verified live. Absent seal evidence where
  none is required stays unevaluable, never a failure.

Scores are opaque: magnitudes, thresholds, and calibration never influence
validity. Ties resolve deterministically (v1 rank order when supplied, else
lowest candidate identity) as a measurement convention only — v1 order
remains authoritative for commerce.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from commerce.opportunity_optimization import (
    AdvisoryOptimizationResult,
    OptimizationInput,
)

# ---------------------------------------------------------------------------
# Gate names (mandatory order) and classifications (closed vocabularies).
# ---------------------------------------------------------------------------

GATE_CREATOR_SCOPE = "creator_scope"
GATE_CANDIDATE_IDENTITY = "candidate_identity"
GATE_OWNERSHIP = "ownership"
GATE_ELIGIBILITY = "eligibility"
GATE_SINGLE_DROP = "single_drop_rule"
GATE_GOVERNANCE = "governance"
GATE_PROVIDER_TRUTH = "provider_truth"

GATE_ORDER: tuple[str, ...] = (
    GATE_CREATOR_SCOPE,
    GATE_CANDIDATE_IDENTITY,
    GATE_OWNERSHIP,
    GATE_ELIGIBILITY,
    GATE_SINGLE_DROP,
    GATE_GOVERNANCE,
    GATE_PROVIDER_TRUTH,
)

#: Advisory advice is structurally and commercially sound.
VALID = "VALID"
#: Malformed, forbidden, unknown, novel, mismatched, or duplicate advice.
ADVISORY_INVALID = "ADVISORY_INVALID"
#: Known frozen candidate that fails hard historical commercial rules.
ADVISORY_NOT_ELIGIBLE = "ADVISORY_NOT_ELIGIBLE"
#: Structurally sound advice rejected by a validator sealing/authority rule.
VALIDATOR_REJECTED = "VALIDATOR_REJECTED"

#: Advisory abstained (or supplied no scores): nothing to validate.
ADVISORY_ABSTAIN = "ADVISORY_ABSTAIN"
#: Advisory matches the deterministic v1 selection.
AGREEMENT = "AGREEMENT"
#: Advisory names a different valid eligible candidate than v1.
DIVERGENCE = "DIVERGENCE"
#: v1 selected nothing; not disagreement.
NO_V1_SELECTION = "NO_V1_SELECTION"
#: Input could not be reconstructed; never agreement or negative.
INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
#: Recovered inputs form a separate cohort, never pooled with FULL rows.
RECOVERED_INPUT = "RECOVERED_INPUT"
#: Unexpected processing failure; never agreement, divergence, or negative.
VALIDATION_ERROR = "VALIDATION_ERROR"

#: Historical governance verdict when required facts are not frozen.
GOVERNANCE_UNEVALUABLE = "UNEVALUABLE_FROM_HISTORY"

_NO_MAPPED_DROP = "NO_MAPPED_DROP"
_MULTIPLE_DROPS = "MULTIPLE_DROPS"


@dataclass(frozen=True)
class GateResult:
    """One gate outcome. ``passed=None`` means unevaluable (recorded, never a pass)."""

    gate: str
    passed: bool | None
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    """Frozen historical-validation outcome (advisory only, no authority)."""

    valid: bool
    classification: str
    first_failed_gate: str | None
    gate_results: tuple[GateResult, ...]
    governance_state: str
    denial_reasons: tuple[str, ...]
    historical: bool
    advisory_candidate: tuple[int, int] | None
    input_policy_version: str | None


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def select_advisory_candidate(
    advisory: AdvisoryOptimizationResult,
    *,
    ranked_order: tuple[int, ...] | list[int] | None = None,
) -> tuple[int, int] | None:
    """Resolve advisory scores to one candidate identity (pure, deterministic).

    Highest score wins. Ties resolve by v1 rank order when a frozen
    ``ranked_order`` is supplied, else by lowest ``(definition_id, version)``.
    Abstention (or empty scores) resolves to None. Magnitudes are never
    interpreted — this is a measurement convention, not a ranking policy.
    """
    if advisory.abstain or not advisory.candidate_scores:
        return None
    best_score: float | None = None
    tied: list[tuple[int, int]] = []
    for did, ver, score in advisory.candidate_scores:
        if best_score is None or score > best_score:
            best_score = score
            tied = [(int(did), int(ver))]
        elif score == best_score:
            tied.append((int(did), int(ver)))
    if len(tied) == 1:
        return tied[0]
    if ranked_order:
        position = {int(did): idx for idx, did in enumerate(ranked_order)}
        return min(tied, key=lambda ref: (position.get(ref[0], len(position)), ref[0], ref[1]))
    return min(tied)


def _eligible_identities(input: OptimizationInput) -> dict[tuple[int, int], Any]:
    return {c.identity(): c for c in input.frozen_candidates}


def _fail(
    *,
    classification: str,
    first_failed_gate: str | None,
    gates: list[GateResult],
    advisory_candidate: tuple[int, int] | None,
    input: OptimizationInput,
    denial_reasons: tuple[str, ...] = (),
) -> ValidationResult:
    return ValidationResult(
        valid=False,
        classification=classification,
        first_failed_gate=first_failed_gate,
        gate_results=tuple(gates),
        governance_state=GOVERNANCE_UNEVALUABLE,
        denial_reasons=tuple(denial_reasons),
        historical=True,
        advisory_candidate=advisory_candidate,
        input_policy_version=input.policy_version,
    )


def validate_advisory(
    input: OptimizationInput,
    advisory: AdvisoryOptimizationResult,
    *,
    sealed_offer_id: int | None = None,
    ranked_order: tuple[int, ...] | list[int] | None = None,
) -> ValidationResult:
    """Validate hypothetical advice against frozen decision-time facts (pure).

    Runs every gate in mandatory order and records each outcome; the first
    failing gate determines the classification. ``sealed_offer_id`` (from the
    harness's ledger-row read, never from the advisory) and ``ranked_order``
    (frozen v1 order for tie measurement) are optional context. Never raises
    on advisory content — malformed advice classifies ADVISORY_INVALID.
    Raises only on wrong input/advisory types.
    """
    if not isinstance(input, OptimizationInput):
        raise TypeError("input must be an OptimizationInput")
    if not isinstance(advisory, AdvisoryOptimizationResult):
        raise TypeError("advisory must be an AdvisoryOptimizationResult")
    if advisory.abstain or not advisory.candidate_scores:
        return ValidationResult(
            valid=True,
            classification=ADVISORY_ABSTAIN,
            first_failed_gate=None,
            gate_results=(),
            governance_state=GOVERNANCE_UNEVALUABLE,
            denial_reasons=(),
            historical=True,
            advisory_candidate=None,
            input_policy_version=input.policy_version,
        )
    gates: list[GateResult] = []

    # Gate 1 — creator scope (advisory must belong to this exact decision).
    scope_ok = (
        advisory.creator_id == input.creator_id
        and advisory.opportunity_id == input.opportunity_id
        and advisory.input_policy_version == input.policy_version
    )
    gates.append(
        GateResult(
            gate=GATE_CREATOR_SCOPE,
            passed=bool(scope_ok),
            reason="scope_match" if scope_ok else "scope_mismatch",
        )
    )
    if not scope_ok:
        return _fail(
            classification=ADVISORY_INVALID,
            first_failed_gate=GATE_CREATOR_SCOPE,
            gates=gates,
            advisory_candidate=None,
            input=input,
        )

    # Structural reference checks (duplicates, malformed entries).
    refs: list[tuple[int, int]] = []
    refs_ok = True
    seen: set[tuple[int, int]] = set()
    for entry in advisory.candidate_scores:
        try:
            did, ver, _score = entry
            ref = (int(did), int(ver))
        except Exception:
            refs_ok = False
            break
        if not isinstance(did, int) or isinstance(did, bool) or did <= 0:
            refs_ok = False
            break
        if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
            refs_ok = False
            break
        if ref in seen:
            refs_ok = False
            break
        seen.add(ref)
        refs.append(ref)
    eligible = _eligible_identities(input)
    known = refs_ok and all(ref in eligible for ref in refs)
    gates.append(
        GateResult(
            gate=GATE_CANDIDATE_IDENTITY,
            passed=bool(known),
            reason="known_frozen_identities" if known else "unknown_or_novel_candidate",
        )
    )
    if not known:
        return _fail(
            classification=ADVISORY_INVALID,
            first_failed_gate=GATE_CANDIDATE_IDENTITY,
            gates=gates,
            advisory_candidate=None,
            input=input,
        )
    advisory_candidate = select_advisory_candidate(advisory, ranked_order=ranked_order)
    frozen = eligible[advisory_candidate] if advisory_candidate in eligible else None
    if frozen is None:  # Defensive; unreachable given the membership check above.
        return _fail(
            classification=ADVISORY_INVALID,
            first_failed_gate=GATE_CANDIDATE_IDENTITY,
            gates=gates,
            advisory_candidate=None,
            input=input,
        )

    # Gate 3 — ownership (frozen set, production overlap semantics).
    from commerce.ownership import classify_vault_overlap

    ownership_ok = False
    ownership_reason = "overlap_check_failed"
    try:
        owned = set(input.ownership_context.owned_vault_ids) if input.ownership_context else set()
        overlap = classify_vault_overlap(list(frozen.canonical_vault_ids), owned)
        ownership_ok = overlap is not None and getattr(overlap, "name", "") == "ZERO"
        ownership_reason = f"overlap_{getattr(overlap, 'name', 'unknown')}"
    except Exception:
        ownership_ok = False
        ownership_reason = "overlap_check_failed"
    gates.append(
        GateResult(gate=GATE_OWNERSHIP, passed=bool(ownership_ok), reason=ownership_reason)
    )
    if not ownership_ok:
        return _fail(
            classification=ADVISORY_NOT_ELIGIBLE,
            first_failed_gate=GATE_OWNERSHIP,
            gates=gates,
            advisory_candidate=advisory_candidate,
            input=input,
            denial_reasons=("ownership_overlap",),
        )

    # Gate 4 — eligibility re-check through the production primitive.
    from commerce.opportunity_eligibility import evaluate_opportunity_eligibility

    denial_reasons: tuple[str, ...] = ()
    eligibility_ok = False
    try:
        candidate_view = {
            "creator_id": input.creator_id,
            "user_id": input.user_id,
            # Historical entailment, documented: the advisory candidate is a
            # member of the frozen eligible set, so its definition was active
            # at decision time. Live preflight re-resolves status separately
            # (existing sealing path); this validator never consults it.
            "definition_status": "active",
            "offer_type": frozen.offer_type,
            "canonical_vault_item_ids": list(frozen.canonical_vault_ids),
            "price_minor": frozen.price_minor,
            "currency": frozen.currency,
        }
        history_view = {
            "creator_id": input.creator_id,
            "user_id": input.user_id,
            "active_vault_sets": [list(s) for s in input.offer_history_summary.active_vault_sets]
            if input.offer_history_summary
            else [],
            "offered_vault_sets": [list(s) for s in input.offer_history_summary.offered_vault_sets]
            if input.offer_history_summary
            else [],
        }
        owned = set(input.ownership_context.owned_vault_ids) if input.ownership_context else set()
        verdict = evaluate_opportunity_eligibility(
            candidate_view,
            owned,
            history_view,
            creator_id=input.creator_id,
            user_id=input.user_id,
        )
        denial_reasons = tuple(verdict.denial_reasons or ())
        eligibility_ok = bool(verdict.eligible)
    except Exception:
        eligibility_ok = False
        denial_reasons = ("eligibility_adapter_failed",)
    gates.append(
        GateResult(
            gate=GATE_ELIGIBILITY,
            passed=bool(eligibility_ok),
            reason="eligible" if eligibility_ok else f"denied:{'|'.join(denial_reasons) or 'unknown'}",
        )
    )
    if not eligibility_ok:
        return _fail(
            classification=ADVISORY_NOT_ELIGIBLE,
            first_failed_gate=GATE_ELIGIBILITY,
            gates=gates,
            advisory_candidate=advisory_candidate,
            input=input,
            denial_reasons=denial_reasons,
        )

    # Gate 5 — single-Drop rule (audited pure mirror; no shared helper exists).
    mapped = list(frozen.mapped_drop_ids)
    if len(mapped) == 0:
        drop_ok, drop_reason = False, _NO_MAPPED_DROP
    elif len(mapped) > 1:
        drop_ok, drop_reason = False, _MULTIPLE_DROPS
    else:
        drop_ok, drop_reason = True, "single_mapped_drop"
    gates.append(GateResult(gate=GATE_SINGLE_DROP, passed=drop_ok, reason=drop_reason))
    if not drop_ok:
        return _fail(
            classification=VALIDATOR_REJECTED,
            first_failed_gate=GATE_SINGLE_DROP,
            gates=gates,
            advisory_candidate=advisory_candidate,
            input=input,
        )

    # Gate 6 — governance: structurally unevaluable from frozen snapshots.
    gates.append(
        GateResult(
            gate=GATE_GOVERNANCE,
            passed=None,
            reason=GOVERNANCE_UNEVALUABLE,
        )
    )

    # Gate 7 — provider truth from frozen sealing evidence only (zero calls).
    exposed = input.evidence_context.exposure_state if input.evidence_context else "UNAVAILABLE"
    if sealed_offer_id is not None:
        provider_ok: bool | None = True
        provider_reason = "sealed_record_present"
    elif exposed == "SENT":
        provider_ok, provider_reason = True, "sent_record_present"
    else:
        provider_ok, provider_reason = None, "no_seal_record"
    gates.append(
        GateResult(gate=GATE_PROVIDER_TRUTH, passed=provider_ok, reason=provider_reason)
    )

    return ValidationResult(
        valid=True,
        classification=VALID,
        first_failed_gate=None,
        gate_results=tuple(gates),
        governance_state=GOVERNANCE_UNEVALUABLE,
        denial_reasons=(),
        historical=True,
        advisory_candidate=advisory_candidate,
        input_policy_version=input.policy_version,
    )


def compare_with_v1(
    input: OptimizationInput,
    advisory_candidate: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None]:
    """Compare advisory identity against the deterministic v1 selection (pure).

    Returns ``(classification, v1_candidate)`` where classification is one of
    AGREEMENT, DIVERGENCE, or NO_V1_SELECTION. A missing v1 selection is
    never disagreement; historical catalog changes must never rewrite this
    comparison (both sides are frozen).
    """
    v1: tuple[int, int] | None = None
    if isinstance(input.selected_definition_id, int) and not isinstance(
        input.selected_definition_id, bool
    ):
        if isinstance(input.selected_definition_version, int) and not isinstance(
            input.selected_definition_version, bool
        ):
            v1 = (int(input.selected_definition_id), int(input.selected_definition_version))
    if v1 is None or advisory_candidate is None:
        return NO_V1_SELECTION, v1
    if tuple(advisory_candidate) == tuple(v1):
        return AGREEMENT, v1
    return DIVERGENCE, v1


__all__ = [
    "GATE_CREATOR_SCOPE",
    "GATE_CANDIDATE_IDENTITY",
    "GATE_OWNERSHIP",
    "GATE_ELIGIBILITY",
    "GATE_SINGLE_DROP",
    "GATE_GOVERNANCE",
    "GATE_PROVIDER_TRUTH",
    "GATE_ORDER",
    "VALID",
    "ADVISORY_INVALID",
    "ADVISORY_NOT_ELIGIBLE",
    "VALIDATOR_REJECTED",
    "ADVISORY_ABSTAIN",
    "AGREEMENT",
    "DIVERGENCE",
    "NO_V1_SELECTION",
    "INPUT_UNAVAILABLE",
    "RECOVERED_INPUT",
    "VALIDATION_ERROR",
    "GOVERNANCE_UNEVALUABLE",
    "GateResult",
    "ValidationResult",
    "select_advisory_candidate",
    "validate_advisory",
    "compare_with_v1",
]
