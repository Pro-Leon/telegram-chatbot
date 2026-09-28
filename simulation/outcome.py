"""Baseline Outcome Model — Phase 3 minimal deterministic purchase generator.

Does NOT implement engagement/affinity/price sensitivity etc.
Just baseline_purchase_probability with seeded RNG, no global random.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from simulation.ground_truth import GroundTruthReference
from simulation.world import SyntheticOpportunity


def _hash_float(seed: int, run_id: str, domain: str, counter: int) -> float:
    """Deterministic float in [0,1) from seed+run_id+domain+counter."""
    payload = f"{seed}:{run_id}:{domain}:{counter}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    # 32-bit -> float
    v = int(digest[:8], 16)
    return v / 4294967296.0


def _hash_int(seed: int, run_id: str, domain: str, counter: int, modulo: int) -> int:
    payload = f"{seed}:{run_id}:{domain}:{counter}:{modulo}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest[:8], 16) % modulo


@dataclass(frozen=True)
class OutcomeConfig:
    """Minimal baseline outcome config."""

    baseline_purchase_probability: float = 0.1
    purchase_delay_hours_min: int = 1
    purchase_delay_hours_max: int = 48
    non_purchase_outcome: str = "DECLINED"  # DECLINED or EXPIRED

    def __post_init__(self) -> None:
        if not 0.0 <= self.baseline_purchase_probability <= 1.0:
            raise ValueError("baseline_purchase_probability must be in [0,1]")
        if (
            self.purchase_delay_hours_min < 0
            or self.purchase_delay_hours_max < self.purchase_delay_hours_min
        ):
            raise ValueError("invalid purchase_delay range")
        if self.non_purchase_outcome not in ("DECLINED", "EXPIRED"):
            raise ValueError("non_purchase_outcome must be DECLINED or EXPIRED")


@dataclass(frozen=True)
class SimulatedOutcome:
    """Result of simulating one opportunity's exposure+outcome."""

    opportunity_id: int
    generation_id: str
    purchased: bool
    latent_purchase_probability: float
    outcome_state: str  # PURCHASED or DECLINED/EXPIRED (for evidence)
    exposure_state: str = "SENT"
    sent_at: datetime | None = None
    purchase_at: datetime | None = None
    maturity_at: datetime | None = None
    transaction_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "generation_id": self.generation_id,
            "purchased": self.purchased,
            "latent_purchase_probability": self.latent_purchase_probability,
            "outcome_state": self.outcome_state,
            "exposure_state": self.exposure_state,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "purchase_at": self.purchase_at.isoformat() if self.purchase_at else None,
            "maturity_at": self.maturity_at.isoformat() if self.maturity_at else None,
            "transaction_id": self.transaction_id,
        }


class BaselineOutcomeModel:
    """Minimal deterministic outcome model.

    Interface: given simulation state + opportunity → simulated outcome

    Usage:
        model = BaselineOutcomeModel(config)
        outcome = model.decide(opportunity, run_seed, run_id, counter)
    """

    def __init__(self, config: OutcomeConfig | None = None) -> None:
        self.config = config or OutcomeConfig()

    def decide(
        self,
        opportunity: SyntheticOpportunity,
        *,
        seed: int,
        run_id: str,
        counter: int,
    ) -> tuple[SimulatedOutcome, GroundTruthReference, dict[str, Any]]:
        """Deterministically decide outcome for one opportunity.

        Returns (outcome, ground_truth_reference, hidden_truth_payload)
        Hidden payload contains latent_purchase_probability and must NOT enter ledger/snapshot.
        """
        if not isinstance(opportunity, SyntheticOpportunity):
            raise ValueError("opportunity must be SyntheticOpportunity")
        # latent probability is just config baseline (simple, no hidden feature)
        latent_p = float(self.config.baseline_purchase_probability)
        # deterministic random in [0,1)
        r = _hash_float(seed, run_id, "outcome", counter)
        purchased = r < latent_p
        # SENT exposure is immediate at evaluated_at + small offset (1 minute) to ensure sent_at > evaluated_at
        sent_at = opportunity.evaluated_at + timedelta(minutes=1)
        purchase_at: datetime | None = None
        transaction_id: str | None = None
        outcome_state = "DECLINED" if not purchased else "PURCHASED"
        if not purchased:
            outcome_state = self.config.non_purchase_outcome
            # need outcome_at for evidence: for DECLINED/EXPIRED, outcome_at is at maturity threshold? Actually classified via outcome_state + outcome_at.
            # For non-purchase, we set outcome_at = sent_at + small? But maturity calculation uses outcome_at for terminal? For DECLINED/EXPIRED, maturity returns outcome_at directly (terminal). So set outcome_at = sent_at + 1h synthetic? But then maturity_at = outcome_at.
            # Simpler: outcome_at = sent_at + 2h
            outcome_at = sent_at + timedelta(hours=2)
        else:
            # purchase delay deterministic  min..max
            delay_range = (
                self.config.purchase_delay_hours_max - self.config.purchase_delay_hours_min + 1
            )
            delay_h = _hash_int(
                seed, run_id, f"purchase_delay:{opportunity.opportunity_id}", counter, delay_range
            )
            delay_h = delay_h + self.config.purchase_delay_hours_min
            # add minutes jitter deterministic
            delay_m = _hash_int(
                seed, run_id, f"purchase_min:{opportunity.opportunity_id}", counter + 1000, 60
            )
            purchase_at = sent_at + timedelta(hours=int(delay_h), minutes=int(delay_m))
            outcome_at = purchase_at
            transaction_id = f"txn:sym:{run_id}:{opportunity.opportunity_id}"

        # Maturity: max(sent_at, outcome_at) + 168h if open, but terminal outcomes mature once outcome_at. For simulation we compute maturity_at as expected from production logic:
        # Use same as _maturity: for terminal PURCHASED/DECLINED/EXPIRED -> mature once outcome_at. For open not applicable here.
        from commerce.opportunity_evidence import MATURITY_WINDOW_HOURS

        if purchased:
            maturity_at = outcome_at + timedelta(hours=0)  # terminal: mature at outcome_at
        else:
            # terminal DECLINED/EXPIRED also mature at outcome_at
            maturity_at = outcome_at

        # But evidence classifier for open would need 168h. Here we are terminal, so maturity = outcome_at.
        # However to test CENSORED vs mature, we need to set as_of after maturity_at.
        # For purchase lifecycle, we keep as above.

        outcome = SimulatedOutcome(
            opportunity_id=int(opportunity.opportunity_id),
            generation_id=str(opportunity.generation_id),
            purchased=bool(purchased),
            latent_purchase_probability=float(latent_p),
            outcome_state=outcome_state,
            exposure_state="SENT",
            sent_at=sent_at,
            purchase_at=purchase_at,
            maturity_at=maturity_at,
            transaction_id=transaction_id,
        )
        # Ground truth reference (identifier only)
        ref = GroundTruthReference.create(
            simulation_id=run_id,
            opportunity_id=int(opportunity.opportunity_id),
            generation_id=str(opportunity.generation_id),
            created_at=datetime.now(UTC),
        )
        hidden_payload = {
            "latent_purchase_probability": float(latent_p),
            "rng_value": float(r),
            "outcome": outcome_state,
            "purchased": bool(purchased),
            "purchase_at": purchase_at.isoformat() if purchase_at else None,
            "sent_at": sent_at.isoformat(),
        }
        return outcome, ref, hidden_payload


def synthetic_ledger_from_outcome(
    opportunity: SyntheticOpportunity,
    outcome: SimulatedOutcome,
) -> dict[str, Any]:
    """Build synthetic ledger row dict that will produce correct evidence classification.

    Preserves creator_id, fan_id, opportunity_id, generation_id, decision_snapshot, SENT exposure.
    """
    from simulation.adapter import synthetic_ledger_row

    # For mature classification, we need exposure_state SENT and outcome_state terminal
    # Sealed_offer_id must be non-None to support SENT without purchase? But purchase-entailed also works.
    # Set sealed_offer_id to deterministic synthetic offer id
    sealed_offer_id = 800000 + (int(opportunity.opportunity_id) % 100000)
    ledger = synthetic_ledger_row(
        opportunity,
        sealed_offer_id=sealed_offer_id,
        outcome_state=outcome.outcome_state,
        exposure_state=outcome.exposure_state,
        exposure_at=outcome.sent_at,
        outcome_at=outcome.purchase_at
        if outcome.purchased
        else outcome.maturity_at,  # for DECLINED use maturity_at which equals outcome_at
        transaction_id=outcome.transaction_id,
    )
    # Fix attribution for purchased
    if outcome.purchased:
        ledger["attribution_status"] = "attributed"
        ledger["attribution_confidence"] = "full"
        ledger["purchased_price_minor"] = opportunity.decision_snapshot.get("selected", {}).get(
            "price_minor", 1999
        )
        ledger["purchased_currency"] = "USD"
    else:
        ledger["attribution_status"] = "unattributed"
        ledger["attribution_confidence"] = "full"
    # Ensure synthetic marker stays
    from simulation.data_origin import SYNTHETIC_MARKER

    ledger["synthetic"] = SYNTHETIC_MARKER
    return ledger


def maturing_as_of(outcome: SimulatedOutcome) -> datetime:
    """Compute as_of that makes outcome mature (terminal: outcome_at itself)."""
    if outcome.purchased:
        # purchase terminal mature at purchase_at
        assert outcome.purchase_at is not None
        return outcome.purchase_at  # as_of >= outcome_at → mature
    # non-purchase DECLINED/EXPIRED terminal mature at maturity_at (which equals outcome_at)
    assert outcome.maturity_at is not None
    return outcome.maturity_at


__all__ = [
    "OutcomeConfig",
    "SimulatedOutcome",
    "BaselineOutcomeModel",
    "synthetic_ledger_from_outcome",
    "maturing_as_of",
]
