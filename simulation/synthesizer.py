"""Synthesizer — chronological generation of mature synthetic observations.

Produces:
  Synthetic Opportunity → Exposure (SENT) → Outcome (PURCHASED/DECLINED) → Maturity → Ledger Row → RowBundle

Uses real evidence classifier + supervised label; no DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from commerce.offline_optimizer import RowBundle

from simulation.adapter import synthetic_ledger_row
from simulation.outcome import (
    BaselineOutcomeModel,
    OutcomeConfig,
    maturing_as_of,
    synthetic_ledger_from_outcome,
)
from simulation.world import SimulationWorld, SyntheticCreator, SyntheticFan


def generate_mature_bundles(
    world: SimulationWorld,
    *,
    outcome_model: BaselineOutcomeModel | None = None,
    n: int = 12,
    step_hours: int = 24,
    creator: SyntheticCreator | None = None,
    fan_pool: list[SyntheticFan] | None = None,
    conversation_model: Any | None = None,
    fatigue_model: Any | None = None,
    offer_strategy: Any | None = None,
    scenario_id: str | None = None,
) -> tuple[list[RowBundle], list[dict[str, Any]], list[Any]]:
    """Generate n chronological mature RowBundles for one creator.

    World clock is advanced step_hours between opportunities to ensure chronological ordering.
    Returns (bundles, ledger_rows, outcomes) where outcomes are SimulatedOutcome.

    No DB/TG. Deterministic per run seed + outcome_model config.
    When conversation_model/fatigue_model provided, DecisionSnapshot is wired with
    observable fan/history/conversation counts deterministically (Phase 7.1), while
    latent scores remain hidden. When None, zero defaults kept for backward compat.
    When offer_strategy or scenario_id provided, content per opportunity is sampled
    deterministically via OfferStrategy (Phase 8) to create heterogeneous feature mix;
    otherwise single content reused (baseline replay identical).
    """
    if outcome_model is None:
        outcome_model = BaselineOutcomeModel(OutcomeConfig(baseline_purchase_probability=0.5))
    if creator is None:
        creator = world.create_creator() if not world.creators else world.creators[0]
    # Resolve offer strategy (Phase 8) — scenario_id maps via scenarios.get_strategy
    resolved_strategy = offer_strategy
    if resolved_strategy is None and scenario_id is not None:
        try:
            from simulation.scenarios import get_strategy

            resolved_strategy = get_strategy(scenario_id)
        except Exception:
            resolved_strategy = None
    # Fallback to world.run.scenario_id if still None and world run has scenario
    if resolved_strategy is None and scenario_id is None:
        try:
            from simulation.scenarios import get_strategy

            # try world.run.scenario_id if not baseline
            sid = getattr(world.run, "scenario_id", None)
            if isinstance(sid, str) and sid.strip() and sid.strip() != "baseline":
                # only auto-resolve non-baseline to avoid changing baseline replay
                pass
            else:
                resolved_strategy = None
        except Exception:
            resolved_strategy = None
    # Ensure we have content for baseline single; for strategy we sample per opp
    base_content = None
    if resolved_strategy is None:
        base_content = world.create_content(creator) if not world.contents else world.contents[0]

    bundles: list[RowBundle] = []
    ledgers: list[dict[str, Any]] = []
    outcomes: list[Any] = []

    # For fan pool: create n fans deterministically if not provided
    fans: list[SyntheticFan] = []
    if fan_pool is not None:
        fans = fan_pool
    else:
        for _ in range(n):
            fans.append(world.create_fan(creator))

    for i in range(n):
        # Advance clock for chronological spacing if not first
        if i > 0:
            world.clock.advance(hours=step_hours)
        fan = fans[i % len(fans)]
        evaluated_at = world.clock.current_time()
        # Determine content per opportunity (Phase 8 heterogeneous)
        if resolved_strategy is not None:
            try:
                otype, price_minor, vault_ids, mapped = resolved_strategy.sample_content_params(
                    seed=world.run.seed, simulation_id=world.run.simulation_id, counter=i
                )
                content = world.create_content(
                    creator,
                    offer_type=otype,
                    price_minor=price_minor,
                    currency="USD",
                    vault_ids=vault_ids,
                    mapped_drop_ids=mapped,
                )
            except Exception:
                content = base_content
        else:
            content = base_content
        # Wire observable snapshot deterministically if models provided (Phase 7.1)
        history_state = None
        conversation_state = None
        if fatigue_model is not None:
            try:
                history_state = fatigue_model.history_state_for(fan, evaluated_at)
            except Exception:
                history_state = None
        if conversation_model is not None:
            try:
                conversation_state = conversation_model.state_for(fan, evaluated_at)
            except Exception:
                conversation_state = None
        opp = world.create_opportunity(
            creator,
            fan,
            content,
            evaluated_at=evaluated_at,
            history_state=history_state,
            conversation_state=conversation_state,
        )
        # Decide outcome deterministically: counter = i+1 ensures per-opp variation
        # Support Baseline (opp), Behavioral (opp, fan), Content/Price/Phase7 (opp, fan, content) signatures
        outcome = None
        ref = None
        hidden = None
        # try most specific first
        for attempt in ("fan_content", "fan", "baseline"):
            try:
                if attempt == "fan_content":
                    outcome, ref, hidden = outcome_model.decide(  # type: ignore[call-arg]
                        opp, fan, content, seed=world.run.seed, run_id=world.run.simulation_id, counter=i + 1
                    )
                elif attempt == "fan":
                    outcome, ref, hidden = outcome_model.decide(  # type: ignore[call-arg]
                        opp, fan, seed=world.run.seed, run_id=world.run.simulation_id, counter=i + 1
                    )
                else:
                    outcome, ref, hidden = outcome_model.decide(
                        opp, seed=world.run.seed, run_id=world.run.simulation_id, counter=i + 1
                    )
                break
            except TypeError:
                continue
        if outcome is None:
            raise TypeError("outcome_model.decide signature not matched")
        outcomes.append(outcome)
        # Build mature ledger row
        ledger = synthetic_ledger_from_outcome(opp, outcome)
        ledgers.append(ledger)
        # Build bundle via real pipeline: evidence at maturity
        from commerce.opportunity_evidence import classify_opportunity_evidence
        from commerce.opportunity_optimization import build_optimization_input

        # as_of = maturity (for terminal, maturity_at == outcome_at)
        as_of = maturing_as_of(outcome)
        # Also need to ensure as_of >= outcome_at, which it is. For purchased, maturity immediate, so SENT is mature.
        evidence = classify_opportunity_evidence(ledger, as_of=as_of)
        # Build input: ledger row -> OptimizationInput (pure)
        inp = build_optimization_input(ledger_row=ledger, evidence=evidence)
        bundle = RowBundle(input=inp, evidence=evidence, ledger_row=ledger)
        bundles.append(bundle)
    return bundles, ledgers, outcomes


def build_creator_dataset_from_world(
    world: SimulationWorld,
    bundles: list[RowBundle],
    as_of: datetime | None = None,
) -> Any:
    """Build CreatorDataset from bundles (creator-local, chronological).

    Reuses commerce/offline_optimizer.build_creator_dataset (pure).
    """
    from commerce.offline_optimizer import build_creator_dataset

    if not bundles:
        raise ValueError("bundles empty")
    creator_id = bundles[0].input.creator_id
    # Ensure all same creator
    for b in bundles:
        if b.input.creator_id != creator_id:
            raise ValueError("CreatorDataset must be creator-local (mixed creators)")
    if as_of is None:
        # Use latest evaluated_at + 169h to ensure maturity for probe
        latest = max(b.input.evaluated_at for b in bundles)
        as_of = latest + timedelta(hours=169)
    return build_creator_dataset(creator_id=creator_id, bundles=bundles, as_of=as_of)
