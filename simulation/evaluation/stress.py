"""Sample-size stress sweep — Phase 10 P21:830.

Loop caller synthesizing n mature bundles per n, chronological split,
train/predict, record Brier/log_loss vs n.

Small ns used in unit tests (60/120/240) for speed; same API scales
to 1k-500k (documented, not executed in unit tests).
File-only, SHA256 determinism, no DB.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from simulation.evaluation import chronological_split, dataset_hash, evaluate_predictions
from simulation.evaluation.reports import EvaluationReport
from simulation.run import SimulationRun
from simulation.world import SimulationWorld


def _make_phase7_outcome(world: SimulationWorld) -> Any:
    """Create Phase7OutcomeModel for world (behavior+content+price+conversation+fatigue+time)."""
    try:
        from simulation.behavior import FanBehaviorModel
        from simulation.content_affinity import ContentAffinityModel
        from simulation.conversation_state import ConversationStateModel, FatigueModel, TimeContextModel, Phase7OutcomeModel
        from simulation.price_response import PriceResponseModel

        beh = FanBehaviorModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        aff = ContentAffinityModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        price = PriceResponseModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        conv = ConversationStateModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        fat = FatigueModel(seed=world.run.seed, simulation_id=world.run.simulation_id, simulated_start=world.run.simulated_start)
        tim = TimeContextModel()
        return Phase7OutcomeModel(beh, aff, price, conv, fat, tim)
    except Exception:
        from simulation.outcome import BaselineOutcomeModel, OutcomeConfig

        return BaselineOutcomeModel(OutcomeConfig(baseline_purchase_probability=0.3))


def stress_sweep(
    world_factory: Callable[..., SimulationWorld],
    scenario_id: str = "balanced",
    ns: list[int] | tuple[int, ...] = (60, 120, 240),
) -> dict[int, EvaluationReport]:
    """Run stress sweep for each n in ns.

    world_factory: callable returning fresh SimulationWorld per call.
        Signature may be () -> SimulationWorld or (seed, scenario_id, n) etc.
        We try zero-arg first, else with scenario_id/n.
    Returns dict n -> EvaluationReport.
    """
    if not callable(world_factory):
        raise ValueError("world_factory must be callable")
    if not isinstance(ns, (list, tuple)) or not ns:
        raise ValueError("ns must be non-empty list of ints")
    for n in ns:
        if not isinstance(n, int) or n <= 0:
            raise ValueError("ns entries must be positive ints")
    results: dict[int, EvaluationReport] = {}
    for n in ns:
        # create world
        world: SimulationWorld | None = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if attempt == 0:
                    world = world_factory()  # type: ignore
                elif attempt == 1:
                    world = world_factory(scenario_id=scenario_id, n=n)  # type: ignore
                else:
                    world = world_factory(seed=100 + n, scenario_id=scenario_id)  # type: ignore
                if isinstance(world, SimulationWorld):
                    break
            except TypeError as e:
                last_exc = e
                continue
            except Exception as e:
                last_exc = e
                continue
        if not isinstance(world, SimulationWorld):
            raise ValueError(f"world_factory failed for n={n}: {last_exc}")
        # choose outcome model: try phase7 else baseline
        outcome_model = _make_phase7_outcome(world)
        # resolve offer strategy
        try:
            from simulation.scenarios import get_strategy

            strat = get_strategy(scenario_id)
        except Exception:
            strat = None
        # need fatigue/conversation wiring for realistic features
        conv_model = None
        fatigue_model = None
        try:
            from simulation.conversation_state import ConversationStateModel, FatigueModel

            conv_model = ConversationStateModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
            fatigue_model = FatigueModel(seed=world.run.seed, simulation_id=world.run.simulation_id, simulated_start=world.run.simulated_start)
        except Exception:
            pass
        from simulation.synthesizer import generate_mature_bundles

        bundles, _, _ = generate_mature_bundles(
            world,
            outcome_model=outcome_model,
            n=int(n),
            step_hours=24,
            conversation_model=conv_model,
            fatigue_model=fatigue_model,
            offer_strategy=strat,
            scenario_id=scenario_id,
        )
        if len(bundles) < 3:
            raise ValueError(f"not enough bundles for n={n}")
        # chronological split adapted to n
        # for n=60 -> 30/15/15 ; for n=120 -> 60/30/30 ; generally train 50%, valid 25%, test remainder
        train_n = max(6, int(n * 0.5))
        valid_n = max(5, int(n * 0.25))
        test_n = int(n) - train_n - valid_n
        if test_n < 5:
            test_n = 5
            train_n = int(n) - valid_n - test_n
            if train_n < 6:
                train_n = 6
                valid_n = int(n) - train_n - test_n
        # clamp to available
        total_needed = train_n + valid_n + test_n
        if total_needed > len(bundles):
            # trim proportionally
            train_n = max(6, len(bundles) // 2)
            valid_n = max(5, len(bundles) // 4)
            test_n = len(bundles) - train_n - valid_n
        train_b, valid_b, test_b = chronological_split(bundles, train_n=train_n, valid_n=valid_n, test_n=test_n)
        from commerce.offline_optimizer import build_creator_dataset, predict_for_input, train_creator_model
        from simulation.synthesizer import build_creator_dataset_from_world

        train_ds = build_creator_dataset_from_world(world, train_b)
        train_out = train_creator_model(train_ds)
        model = train_out.model
        y_true: list[int] = []
        y_pred: list[float] = []
        for b in test_b:
            from commerce.offline_optimizer import make_training_example

            ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
            if ex is None:
                continue
            y_true.append(int(ex.binary))
            pred = predict_for_input(model, b.input)
            if pred.probability is None:
                y_pred.append(0.5)
            else:
                y_pred.append(float(pred.probability))
        metrics = evaluate_predictions(y_true=y_true, y_pred=y_pred)
        # baselines and cohorts
        from simulation.evaluation.baselines import global_purchase_rate, creator_purchase_rate
        from simulation.evaluation.cohorts import cohort_key_price_bucket, metrics_by_cohort

        by_cohort = metrics_by_cohort(test_b, y_pred, cohort_key_price_bucket) if y_pred else {}
        baselines = {
            "global_rate": global_purchase_rate(train_b) if train_b else 0.5,
            "creator_rate": creator_purchase_rate(train_b, world.creators[0].creator_id if world.creators else 0) if train_b else 0.5,
        }
        dhash = dataset_hash(bundles)
        report = EvaluationReport(
            simulation_id=world.run.simulation_id,
            scenario_id=scenario_id,
            seed=world.run.seed,
            strategy_id=strat.strategy_id if strat else scenario_id,
            dataset_hash=dhash,
            train_n=len(train_b),
            valid_n=len(valid_b),
            test_n=len(test_b),
            metrics=metrics,
            by_cohort=by_cohort,
            baselines=baselines,
            calibration=None,
            as_of=datetime.now(UTC),
        )
        results[int(n)] = report
    return results


__all__ = ["stress_sweep"]
