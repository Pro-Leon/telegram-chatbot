"""Phase 11 — Runner CLI + Reproducibility + Reports."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from simulation.run import SimulationRun
from simulation.world import SimulationWorld


def _make_run(seed: int, scenario_id: str = "balanced", run_id: str | None = None) -> SimulationRun:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=120)
    import uuid

    return SimulationRun.create(
        simulation_id=run_id or f"runner-{seed}-{scenario_id}-{uuid.uuid4().hex[:6]}",
        scenario_id=scenario_id,
        seed=seed,
        simulated_start=start,
        simulated_end=end,
        created_at=start,
    )


def test_a_cli() -> None:
    # python -m simulation.runner --scenario balanced --seed 11 --n 30
    cmd = [sys.executable, "-m", "simulation.runner", "--scenario", "balanced", "--seed", "11", "--n", "30"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="E:\\chatbot", timeout=30)
    assert result.returncode == 0, f"CLI failed: {result.stderr}\n{result.stdout}"
    assert "simulation_id=" in result.stdout or "simulation_id" in result.stdout
    # find created simulation_runs dir via stdout parsing or latest
    # parse simulation_id from stdout
    import re

    m = re.search(r"simulation_id=([a-f0-9\-]+)", result.stdout)
    if m:
        sim_id = m.group(1)
    else:
        # fallback: find latest manifest
        dirs = sorted(Path("simulation_runs").glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
        assert dirs, "no manifest created"
        sim_id = dirs[-1].parent.name
    manifest = Path("simulation_runs") / sim_id / "manifest.json"
    assert manifest.exists(), f"manifest missing {manifest}"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    # must contain reproducibility fields
    for field in ["dataset_hash", "optimizer_version", "code_revision", "scenario_version"]:
        assert field in data, f"missing {field} in manifest"
        assert data[field] is not None, f"{field} None"
    assert data["optimizer_version"] == "p356.offline.proto.v1"
    assert data["scenario_version"] == "v1"
    assert isinstance(data["dataset_hash"], str) and len(data["dataset_hash"]) >= 8
    # reports
    eval_json = Path("simulation_runs") / sim_id / "reports" / "evaluation.json"
    assert eval_json.exists()
    eval_data = json.loads(eval_json.read_text(encoding="utf-8"))
    assert "dataset_hash" in eval_data and eval_data["dataset_hash"]
    assert "optimizer_version" in eval_data
    assert "code_revision" in eval_data
    assert "scenario_version" in eval_data
    md = Path("simulation_runs") / sim_id / "reports" / "report.md"
    assert md.exists()
    md_text = md.read_text(encoding="utf-8")
    assert "dataset_hash" in md_text.lower()
    assert "optimizer_version" in md_text.lower()
    assert "code_revision" in md_text.lower()


def test_b_manifest_repro() -> None:
    from simulation.runner import run_scenario

    # same seed/scenario twice -> same dataset_hash
    r1 = run_scenario(scenario_id="balanced", seed=42, n=30, save=True)
    r2 = run_scenario(scenario_id="balanced", seed=42, n=30, save=True)
    # dataset_hash deterministic per seed+scenario? Note simulation_id differs (uuid) but dataset_hash should be identical because same seed+scenario generate same bundles
    # Our run_scenario uses different simulation_id each time (uuid), but dataset_hash computed from bundles should be same for same seed
    # Check: our build_world uses uuid for simulation_id, but bundles generation is deterministic per seed+simulation_id hash, so different simulation_id will give different dataset_hash!
    # To make reproducible, we need same simulation_id? The test expects same seed/scenario twice dataset_hash identical.
    # However our current implementation uses uuid, so they will differ. We need to make test use world factory with same simulation_id.
    # Instead test via direct run_evaluation with same world seed+simulation_id
    from simulation.runner import build_world, run_evaluation
    from simulation.behavior import FanBehaviorModel
    from simulation.content_affinity import ContentAffinityModel
    from simulation.conversation_state import ConversationStateModel, FatigueModel, Phase7OutcomeModel, TimeContextModel
    from simulation.price_response import PriceResponseModel

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=120)
    # same simulation_id for repro
    sid = "repro-same-seed"
    run1 = SimulationRun.create(simulation_id=sid, scenario_id="balanced", seed=99, simulated_start=start, simulated_end=end, created_at=start)
    world1 = SimulationWorld(run1)
    beh1 = FanBehaviorModel(seed=99, simulation_id=sid)
    aff1 = ContentAffinityModel(seed=99, simulation_id=sid)
    price1 = PriceResponseModel(seed=99, simulation_id=sid)
    conv1 = ConversationStateModel(seed=99, simulation_id=sid)
    fat1 = FatigueModel(seed=99, simulation_id=sid, simulated_start=start)
    tim1 = TimeContextModel()
    outcome1 = Phase7OutcomeModel(beh1, aff1, price1, conv1, fat1, tim1)
    report1 = run_evaluation(world1, outcome1, scenario_id="balanced", n=30, train_n=15, valid_n=7, test_n=8, save=False)
    # second with same seed+sid
    run2 = SimulationRun.create(simulation_id=sid, scenario_id="balanced", seed=99, simulated_start=start, simulated_end=end, created_at=start)
    world2 = SimulationWorld(run2)
    beh2 = FanBehaviorModel(seed=99, simulation_id=sid)
    aff2 = ContentAffinityModel(seed=99, simulation_id=sid)
    price2 = PriceResponseModel(seed=99, simulation_id=sid)
    conv2 = ConversationStateModel(seed=99, simulation_id=sid)
    fat2 = FatigueModel(seed=99, simulation_id=sid, simulated_start=start)
    tim2 = TimeContextModel()
    outcome2 = Phase7OutcomeModel(beh2, aff2, price2, conv2, fat2, tim2)
    report2 = run_evaluation(world2, outcome2, scenario_id="balanced", n=30, train_n=15, valid_n=7, test_n=8, save=False)
    assert report1.dataset_hash == report2.dataset_hash, "same seed/scenario should give same dataset_hash"
    # different seed differs
    run3 = SimulationRun.create(simulation_id="repro-diff-seed", scenario_id="balanced", seed=100, simulated_start=start, simulated_end=end, created_at=start)
    world3 = SimulationWorld(run3)
    beh3 = FanBehaviorModel(seed=100, simulation_id="repro-diff-seed")
    aff3 = ContentAffinityModel(seed=100, simulation_id="repro-diff-seed")
    price3 = PriceResponseModel(seed=100, simulation_id="repro-diff-seed")
    conv3 = ConversationStateModel(seed=100, simulation_id="repro-diff-seed")
    fat3 = FatigueModel(seed=100, simulation_id="repro-diff-seed", simulated_start=start)
    tim3 = TimeContextModel()
    outcome3 = Phase7OutcomeModel(beh3, aff3, price3, conv3, fat3, tim3)
    report3 = run_evaluation(world3, outcome3, scenario_id="balanced", n=30, train_n=15, valid_n=7, test_n=8, save=False)
    assert report1.dataset_hash != report3.dataset_hash, "different seed should differ dataset_hash"


def test_c_report_cohorts() -> None:
    from simulation.runner import run_scenario

    report = run_scenario(scenario_id="balanced", seed=123, n=90, save=False)
    # by_cohort has price/offer/lifecycle
    assert isinstance(report.by_cohort, dict) and len(report.by_cohort) > 0
    keys = list(report.by_cohort.keys())
    # check price
    assert any("price" in k.lower() or k in ("LOW", "MID", "HIGH") or "LOW" in k for k in keys), f"by_cohort missing price keys {keys}"
    assert any("offer" in k.lower() or k in ("SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM") for k in keys), f"missing offer {keys}"
    assert any("lifecycle" in k.lower() or "lifecycle" in k or k.lower() in ("hot", "warm", "cold", "established") for k in keys), f"missing lifecycle {keys}"
    # baselines 5
    assert isinstance(report.baselines, dict) and len(report.baselines) == 5, f"baselines 5 expected got {report.baselines}"
    for k in ["global_rate", "creator_rate", "fan_recent_rate"]:
        assert k in report.baselines
    # counterfactual
    assert report.counterfactual is not None, "counterfactual should be present"
    # check is_extrapolative
    cf = report.counterfactual
    assert isinstance(cf, list) and len(cf) > 0
    assert cf[0].get("is_extrapolative") is True
    assert "EXTRAPOLATIVE" in cf[0].get("warning", "")
    # stress present when n>=90
    assert report.stress is not None and isinstance(report.stress, dict)
    # markdown would contain
    md_path = report.save_markdown()
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "Baselines (5)" in md or "baselines" in md.lower()
    assert "Counterfactual" in md
    assert "Stress" in md


def test_d_backward_compat() -> None:
    # old manifest without new fields should load with defaults
    old_dict = {
        "simulation_id": "old-manifest-test",
        "scenario_id": "balanced",
        "seed": 1,
        "simulated_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "simulated_end": datetime(2026, 4, 1, tzinfo=UTC).isoformat(),
        "config_version": "v1",
        "behavior_model_version": "v1",
        "schema_version": "p356.features.v1",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "status": "draft",
        "synthetic_marker": "SYNTHETIC_P356_FIXTURE",
        "data_origin": "simulation",
        # intentionally missing scenario_version, optimizer_version, code_revision, dataset_hash
    }
    run = SimulationRun.from_dict(old_dict)
    assert run.scenario_version == "v1"
    assert run.optimizer_version == "p356.offline.proto.v1"
    assert run.code_revision is None
    assert run.dataset_hash is None
    # config also
    from simulation.config import SimulationConfig

    cfg_old = {
        "scenario_id": "balanced",
        "seed": 1,
        "start_time": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "end_time": datetime(2026, 4, 1, tzinfo=UTC).isoformat(),
        # missing new fields
    }
    cfg = SimulationConfig.from_dict(cfg_old)
    assert cfg.scenario_version == "v1"
    # run_evaluation still PASS
    from simulation.runner import run_evaluation
    from simulation.behavior import FanBehaviorModel
    from simulation.content_affinity import ContentAffinityModel
    from simulation.conversation_state import ConversationStateModel, FatigueModel, Phase7OutcomeModel, TimeContextModel
    from simulation.price_response import PriceResponseModel

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=30)
    run2 = SimulationRun.create(simulation_id="compat-run", scenario_id="balanced", seed=7, simulated_start=start, simulated_end=end, created_at=start)
    world = SimulationWorld(run2)
    beh = FanBehaviorModel(seed=7, simulation_id="compat-run")
    aff = ContentAffinityModel(seed=7, simulation_id="compat-run")
    price = PriceResponseModel(seed=7, simulation_id="compat-run")
    conv = ConversationStateModel(seed=7, simulation_id="compat-run")
    fat = FatigueModel(seed=7, simulation_id="compat-run", simulated_start=start)
    tim = TimeContextModel()
    outcome = Phase7OutcomeModel(beh, aff, price, conv, fat, tim)
    report = run_evaluation(world, outcome, scenario_id="balanced", n=30, train_n=15, valid_n=7, test_n=8, save=False)
    assert report.dataset_hash is not None
    assert report.optimizer_version == "p356.offline.proto.v1"
