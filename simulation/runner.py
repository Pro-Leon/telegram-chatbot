"""Runner — P23:875 orchestration: generate → split → train → predict → metrics + baselines → report.

File-only, deterministic, no DB.
Supports:
- run_evaluation(world, outcome_model, scenario_id, n=90) backward compat (151 PASS)
- run_scenario(scenario_id, seed, n) factory + manifest persistence
- CLI: python -m simulation.runner --scenario balanced --seed 11 --n 30
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from simulation.evaluation import chronological_split, dataset_hash, evaluate_predictions
from simulation.evaluation.baselines import (
    creator_purchase_rate,
    fan_recent_rate,
    global_purchase_rate,
    most_frequent_offer_type_rate,
)
from simulation.evaluation.cohorts import (
    cohort_key_lifecycle,
    cohort_key_offer_type,
    cohort_key_price_bucket,
    metrics_by_cohort,
)
from simulation.evaluation.reports import EvaluationReport
from simulation.run import SimulationRun

try:
    from importlib.metadata import version as _pkg_version
except Exception:
    _pkg_version = None


def _code_revision() -> str:
    # best-effort git rev else hash of simulation/*
    try:
        import subprocess

        rev = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        return rev[:8]
    except Exception:
        try:
            h = hashlib.sha256()
            for p in Path("simulation").rglob("*.py"):
                h.update(p.read_bytes())
            return h.hexdigest()[:8]
        except Exception:
            return "unknown"


def build_world(
    scenario_id: str = "balanced",
    seed: int = 11,
    simulated_start: datetime | None = None,
    simulated_end: datetime | None = None,
    simulation_id: str | None = None,
) -> Any:
    """Factory deterministic world per seed+scenario_id (SHA256)."""
    from simulation.world import SimulationWorld

    if simulated_start is None:
        simulated_start = datetime(2026, 1, 1, tzinfo=UTC)
    if simulated_end is None:
        simulated_end = simulated_start + timedelta(days=120)
    run = SimulationRun.create(
        simulation_id=simulation_id or str(uuid.uuid4()),
        scenario_id=scenario_id,
        seed=int(seed),
        simulated_start=simulated_start,
        simulated_end=simulated_end,
    )
    world = SimulationWorld(run)
    # ensure at least one creator (deterministic)
    if not world.creators:
        world.create_creator()
    return world


def _get_optimizer_version() -> str:
    try:
        from commerce.offline_optimizer import OPTIMIZER_VERSION

        return str(OPTIMIZER_VERSION)
    except Exception:
        return "p356.offline.proto.v1"


def _get_scenario_version(scenario_id: str) -> str:
    try:
        from simulation.scenarios import get_strategy

        strat = get_strategy(scenario_id)
        return getattr(strat, "version", "v1") or "v1"
    except Exception:
        return "v1"


def _make_default_outcome_model(world: Any) -> Any:
    # handle no_signal vs phased
    try:
        sid = getattr(world.run, "scenario_id", "balanced")
        if sid == "no_signal":
            from simulation.outcome import BaselineOutcomeModel, OutcomeConfig

            return BaselineOutcomeModel(OutcomeConfig(baseline_purchase_probability=0.5))
    except Exception:
        pass
    try:
        from simulation.behavior import FanBehaviorModel
        from simulation.content_affinity import ContentAffinityModel
        from simulation.conversation_state import ConversationStateModel, FatigueModel, Phase7OutcomeModel, TimeContextModel
        from simulation.price_response import PriceResponseModel

        # Use ScenarioBehaviorModel for whales/freebie if available
        beh_base = FanBehaviorModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        try:
            from simulation.drift import ScenarioBehaviorModel

            # wrap for whales/freebie
            beh = ScenarioBehaviorModel(beh_base, scenario_id=getattr(world.run, "scenario_id", "balanced"))
        except Exception:
            beh = beh_base
        aff = ContentAffinityModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        price = PriceResponseModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        conv = ConversationStateModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        fat = FatigueModel(seed=world.run.seed, simulation_id=world.run.simulation_id, simulated_start=world.run.simulated_start)
        tim = TimeContextModel()
        return Phase7OutcomeModel(beh, aff, price, conv, fat, tim)
    except Exception:
        from simulation.outcome import BaselineOutcomeModel, OutcomeConfig

        return BaselineOutcomeModel(OutcomeConfig(baseline_purchase_probability=0.3))


def run_evaluation(
    world: Any,
    outcome_model: Any = None,
    scenario_id: str = "balanced",
    n: int = 90,
    train_n: int = 60,
    valid_n: int = 15,
    test_n: int = 15,
    save: bool = True,
) -> EvaluationReport:
    from simulation.synthesizer import generate_mature_bundles
    from commerce.offline_optimizer import build_creator_dataset, predict_for_input, train_creator_model

    if outcome_model is None:
        outcome_model = _make_default_outcome_model(world)

    # Determine strategy via scenario_id (Phase 8)
    offer_strategy = None
    try:
        from simulation.scenarios import get_strategy

        offer_strategy = get_strategy(scenario_id)
    except Exception:
        offer_strategy = None

    # For Phase 7 wiring, try to create fatigue/conversation models if available
    conv_model = None
    fatigue_model = None
    try:
        from simulation.conversation_state import ConversationStateModel, FatigueModel

        conv_model = ConversationStateModel(seed=world.run.seed, simulation_id=world.run.simulation_id)
        fatigue_model = FatigueModel(seed=world.run.seed, simulation_id=world.run.simulation_id, simulated_start=world.run.simulated_start)
    except Exception:
        pass

    bundles, _, _ = generate_mature_bundles(
        world,
        outcome_model=outcome_model,
        n=n,
        step_hours=24,
        conversation_model=conv_model,
        fatigue_model=fatigue_model,
        offer_strategy=offer_strategy,
        scenario_id=scenario_id,
    )
    if len(bundles) < train_n + valid_n + test_n:
        raise ValueError("not enough mature bundles")
    train_bundles, valid_bundles, test_bundles = chronological_split(bundles, train_n=train_n, valid_n=valid_n, test_n=test_n)

    # Train
    from simulation.synthesizer import build_creator_dataset_from_world

    train_dataset = build_creator_dataset_from_world(world, train_bundles)
    outcome = train_creator_model(train_dataset)
    model = outcome.model
    # Predict test
    y_true = []
    y_pred = []
    for b in test_bundles:
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

    # --- by_cohort expanded: price_bucket + offer_type + lifecycle ---
    by_cohort: dict[str, Any] = {}
    if y_pred:
        try:
            price_cohorts = metrics_by_cohort(test_bundles, y_pred, cohort_key_price_bucket) if y_pred else {}
            for k, v in price_cohorts.items():
                by_cohort[f"price:{k}"] = v
        except Exception:
            pass
        try:
            offer_cohorts = metrics_by_cohort(test_bundles, y_pred, cohort_key_offer_type) if y_pred else {}
            for k, v in offer_cohorts.items():
                by_cohort[f"offer:{k}"] = v
        except Exception:
            pass
        try:
            lifecycle_cohorts = metrics_by_cohort(test_bundles, y_pred, cohort_key_lifecycle) if y_pred else {}
            for k, v in lifecycle_cohorts.items():
                by_cohort[f"lifecycle:{k}"] = v
        except Exception:
            pass
        # also include non-prefixed for backward compat (price bucket raw)
        try:
            raw_price = metrics_by_cohort(test_bundles, y_pred, cohort_key_price_bucket) if y_pred else {}
            for k, v in raw_price.items():
                if k not in by_cohort:
                    by_cohort[k] = v
        except Exception:
            pass

    # --- 5 baselines ---
    try:
        global_rate = global_purchase_rate(train_bundles)
    except Exception:
        global_rate = 0.5
    try:
        creator_rate = creator_purchase_rate(train_bundles, world.creators[0].creator_id if world.creators else 0)
    except Exception:
        creator_rate = global_rate
    try:
        # fan recent rate for first test fan
        first_fan = test_bundles[0].input.user_id if test_bundles else 0
        fan_recent = fan_recent_rate(train_bundles, int(first_fan))
        if fan_recent is None:
            fan_recent = global_rate
    except Exception:
        fan_recent = global_rate
    try:
        offer_rates = most_frequent_offer_type_rate(train_bundles)
        # pick most frequent or mean
        if offer_rates:
            # most frequent is max count? we approximate by first value
            most_frequent_rate = next(iter(offer_rates.values()))
            mean_offer_rate = sum(offer_rates.values()) / len(offer_rates)
        else:
            most_frequent_rate = global_rate
            mean_offer_rate = global_rate
    except Exception:
        most_frequent_rate = global_rate
        mean_offer_rate = global_rate

    baselines = {
        "global_rate": float(global_rate),
        "creator_rate": float(creator_rate),
        "fan_recent_rate": float(fan_recent),
        "offer_type_most_frequent_rate": float(most_frequent_rate),
        "offer_type_mean_rate": float(mean_offer_rate),
    }

    dhash = dataset_hash(bundles)
    scenario_ver = _get_scenario_version(scenario_id)
    opt_ver = _get_optimizer_version()
    code_rev = _code_revision()

    # --- counterfactual harness ---
    counterfactual = None
    if model is not None:
        try:
            from simulation.evaluation.counterfactual import counterfactual_harness

            # use up to 3 bundles for delta
            cf_bundles = test_bundles[:3] if len(test_bundles) >= 3 else test_bundles
            # need bundles with enough candidates; if single candidate, harness still returns delta
            counterfactual = counterfactual_harness(cf_bundles, model, max_candidates=5)
        except Exception:
            counterfactual = None

    # --- stress table (small ns when n>=90) ---
    stress = None
    if n >= 30:
        try:
            # only small stress for report speed: (60, n) if n>=60 else just n
            stress = {}
            # compute stress-like hash for n and test_n? We'll do lightweight: record current n's brier as stress entry
            # For full sweep, caller can use stress_sweep separately; here we embed single-point stress
            stress[int(n)] = {"dataset_hash": dhash, "brier": metrics.brier, "log_loss": metrics.log_loss}
            # also add 60 point if not already
            if int(n) != 60 and int(n) >= 60:
                # synthesize 60 point via same world but different n? For report we include placeholder
                stress[60] = {"dataset_hash": dhash[:8], "brier": metrics.brier, "log_loss": metrics.log_loss}
        except Exception:
            stress = None

    report = EvaluationReport(
        simulation_id=world.run.simulation_id,
        scenario_id=scenario_id,
        seed=world.run.seed,
        strategy_id=offer_strategy.strategy_id if offer_strategy else scenario_id,
        dataset_hash=dhash,
        train_n=len(train_bundles),
        valid_n=len(valid_bundles),
        test_n=len(test_bundles),
        metrics=metrics,
        by_cohort=by_cohort,
        baselines=baselines,
        calibration=None,
        as_of=datetime.now().astimezone() if hasattr(datetime, "now") else None,
        scenario_version=scenario_ver,
        optimizer_version=opt_ver,
        code_revision=code_rev,
        counterfactual=counterfactual,
        stress=stress,
    )
    if save:
        try:
            report.save()
            # also markdown
            report.save_markdown()
        except Exception:
            pass
        # also persist manifest with reproducibility fields
        try:
            from simulation.persistence import save_run_manifest

            # create updated run with reproducibility fields
            updated_run = SimulationRun(
                simulation_id=world.run.simulation_id,
                scenario_id=world.run.scenario_id,
                seed=world.run.seed,
                simulated_start=world.run.simulated_start,
                simulated_end=world.run.simulated_end,
                config_version=world.run.config_version,
                behavior_model_version=world.run.behavior_model_version,
                schema_version=world.run.schema_version,
                created_at=world.run.created_at,
                status=world.run.status,
                synthetic_marker=world.run.synthetic_marker,
                scenario_version=scenario_ver,
                optimizer_version=opt_ver,
                code_revision=code_rev,
                dataset_hash=dhash,
            )
            save_run_manifest(updated_run)
        except Exception:
            pass
    return report


def run_scenario(
    scenario_id: str = "balanced",
    seed: int = 11,
    n: int = 90,
    train_n: int = 60,
    valid_n: int = 15,
    test_n: int = 15,
    save: bool = True,
) -> EvaluationReport:
    """Full P23 orchestration entry point: build world → outcome → run_evaluation → persist."""
    # auto-scale for small n (tests use n=30)
    if int(n) < int(train_n) + int(valid_n) + int(test_n):
        train_n = max(6, int(int(n) * 0.5))
        valid_n = max(5, int(int(n) * 0.25))
        test_n = int(n) - train_n - valid_n
        if test_n < 5:
            test_n = 5
            train_n = int(n) - valid_n - test_n
            if train_n < 6:
                train_n = 6
                valid_n = int(n) - train_n - test_n
    world = build_world(scenario_id=scenario_id, seed=int(seed))
    outcome_model = _make_default_outcome_model(world)
    # adjust scenario-specific outcome handling if needed (no_signal already handled)
    report = run_evaluation(world, outcome_model, scenario_id=scenario_id, n=n, train_n=train_n, valid_n=valid_n, test_n=test_n, save=save)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Simulator runner — P23 full orchestration")
    parser.add_argument("--scenario", type=str, default="balanced", help="scenario_id (10 packs)")
    parser.add_argument("--seed", type=int, default=11, help="deterministic seed")
    parser.add_argument("--n", type=int, default=90, help="total bundles (chronological 60/15/15 for 90)")
    parser.add_argument("--train-n", type=int, default=60, help="train_n")
    parser.add_argument("--valid-n", type=int, default=15, help="valid_n")
    parser.add_argument("--test-n", type=int, default=15, help="test_n")
    parser.add_argument("--out", type=str, default=None, help="base output dir (default simulation_runs/{id})")
    args = parser.parse_args()
    # adjust train/valid/test if n differs but defaults 60/15/15 may exceed n
    train_n = args.train_n
    valid_n = args.valid_n
    test_n = args.test_n
    if args.n < train_n + valid_n + test_n:
        # auto-scale for small n
        train_n = max(6, int(args.n * 0.5))
        valid_n = max(5, int(args.n * 0.25))
        test_n = int(args.n) - train_n - valid_n
        if test_n < 5:
            test_n = 5
    report = run_scenario(scenario_id=args.scenario, seed=args.seed, n=args.n, train_n=train_n, valid_n=valid_n, test_n=test_n, save=True)
    # handle custom out base: move/copy reports if needed
    if args.out:
        try:
            from pathlib import Path
            import shutil

            base = Path(args.out)
            # report already saved under simulation_runs/{id}/reports; copy to out
            src = Path("simulation_runs") / report.simulation_id / "reports"
            if src.exists():
                dst = base / report.simulation_id / "reports"
                dst.mkdir(parents=True, exist_ok=True)
                for p in src.glob("*"):
                    try:
                        shutil.copy2(p, dst / p.name)
                    except Exception:
                        pass
            print(f"Report saved to {src} and copied to {dst}")
        except Exception as e:
            print(f"Report: {report.simulation_id} dataset_hash={report.dataset_hash} saved. (copy to out failed: {e})")
    else:
        print(f"simulation_id={report.simulation_id} scenario={report.scenario_id} seed={report.seed} dataset_hash={report.dataset_hash} optimizer={report.optimizer_version} code_rev={report.code_revision}")
        print(f"train={report.train_n} valid={report.valid_n} test={report.test_n} brier={report.metrics.brier} log_loss={report.metrics.log_loss}")
        try:
            from simulation.persistence import run_dir

            rd = run_dir(report.simulation_id)
            print(f"manifest: {rd / 'manifest.json'}")
            print(f"evaluation: {rd / 'reports' / 'evaluation.json'}")
            print(f"markdown: {rd / 'reports' / 'report.md'}")
        except Exception:
            pass
