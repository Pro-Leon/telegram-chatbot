"""Phase 10 — Drift / Whale-Freebie / Adversarial / Counterfactual / Stress / Failure Suite."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from simulation.run import SimulationRun
from simulation.world import SimulationWorld


def _make_run(seed: int, scenario_id: str = "balanced", run_id: str | None = None) -> SimulationRun:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=120)
    return SimulationRun.create(
        simulation_id=run_id or f"p10-{seed}-{scenario_id}-{uuid.uuid4().hex[:6]}",
        scenario_id=scenario_id,
        seed=seed,
        simulated_start=start,
        simulated_end=end,
        created_at=start,
    )


def test_a_whale_freebie_packs() -> None:
    from simulation.behavior import FanBehaviorModel
    from simulation.drift import ScenarioBehaviorModel, freebie_biased_behavior, whale_biased_behavior
    from simulation.scenarios import SCENARIO_PACKS, get_strategy

    # registry 10
    assert len(SCENARIO_PACKS) == 10, f"expected 10 packs got {len(SCENARIO_PACKS)}"
    for sid in ["whales", "freebie_heavy", "temporal_drift", "no_signal", "adversarial"]:
        assert sid in SCENARIO_PACKS, f"missing {sid}"
        strat = get_strategy(sid)
        assert strat.strategy_id == sid
        assert strat.currency == "USD"
    # existing preserved
    assert get_strategy("baseline").content_mix == {"SMALL_BUNDLE": 1.0}
    # price heterogeneity: whales higher than cheap_single
    whales = get_strategy("whales")
    cheap = get_strategy("cheap_single")
    prices_w = [whales.sample_content_params(seed=42, simulation_id="a-whale", counter=i)[1] for i in range(100)]
    prices_c = [cheap.sample_content_params(seed=42, simulation_id="a-whale", counter=i)[1] for i in range(100)]
    assert sum(prices_w) / len(prices_w) > sum(prices_c) / len(prices_c) + 200
    # whale vs freebie behavior means
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=10)
    run = _make_run(42, "baseline", "a-bh")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fans = [world.create_fan(creator) for _ in range(24)]
    base_model = FanBehaviorModel(seed=42, simulation_id="a-bh")
    base_behs = [base_model.for_fan(f) for f in fans]
    base_pp = sum(b.purchase_propensity for b in base_behs) / len(base_behs)
    base_freebie = sum(b.freebie_tendency for b in base_behs) / len(base_behs)
    # whales bias
    whales_behs = [whale_biased_behavior(b, f, 42, "a-whale") for b, f in zip(base_behs, fans)]
    whales_pp = sum(b.purchase_propensity for b in whales_behs) / len(whales_behs)
    whales_ps = sum(b.price_sensitivity for b in whales_behs) / len(whales_behs)
    base_ps = sum(b.price_sensitivity for b in base_behs) / len(base_behs)
    assert whales_pp > base_pp + 0.1, f"whales pp {whales_pp:.3f} should > base {base_pp:.3f}"
    assert whales_ps < base_ps - 0.1, f"whales ps {whales_ps:.3f} should < base {base_ps:.3f}"
    # freebie heavy
    freebie_behs = [freebie_biased_behavior(b, f, 42, "a-free") for b, f in zip(base_behs, fans)]
    freebie_mean = sum(b.freebie_tendency for b in freebie_behs) / len(freebie_behs)
    freebie_pp = sum(b.purchase_propensity for b in freebie_behs) / len(freebie_behs)
    assert 0.7 <= freebie_mean <= 0.9, f"freebie mean {freebie_mean:.3f} out of 0.7-0.9"
    assert freebie_mean > base_freebie + 0.1
    assert freebie_pp < base_pp
    # deterministic replay same seed same pack same sequence
    strat_bal = get_strategy("whales")
    seq1 = [strat_bal.sample_content_params(seed=123, simulation_id="replay-a", counter=i) for i in range(12)]
    seq2 = [strat_bal.sample_content_params(seed=123, simulation_id="replay-a", counter=i) for i in range(12)]
    assert seq1 == seq2
    # scenario wrapper deterministic
    wrap1 = ScenarioBehaviorModel(base_model, scenario_id="whales", seed=42, simulation_id="a-bh")
    wrap2 = ScenarioBehaviorModel(FanBehaviorModel(seed=42, simulation_id="a-bh"), scenario_id="whales", seed=42, simulation_id="a-bh")
    for f in fans[:5]:
        assert wrap1.for_fan(f).purchase_propensity == wrap2.for_fan(f).purchase_propensity


def test_b_drift() -> None:
    from simulation.drift import DRIFT_RATE, DriftModel

    start = datetime(2026, 1, 1, tzinfo=UTC)
    run = _make_run(7, "temporal_drift", "b-drift")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    dm = DriftModel(seed=run.seed, simulation_id=run.simulation_id, simulated_start=run.simulated_start, drift_rate=DRIFT_RATE)
    early_at = start + timedelta(days=10)
    late_at = start + timedelta(days=80)
    d_early = dm.drift_for(fan, early_at)
    d_late = dm.drift_for(fan, late_at)
    assert d_early != d_late, "drift early vs late must differ"
    # sign consistent
    sign = dm.sign_for(fan)
    assert (d_early > 0 and d_late > 0) if sign > 0 else (d_early < 0 and d_late < 0)
    # magnitude approx drift_rate*days
    assert abs(abs(d_late) - abs(d_early) - DRIFT_RATE * 70) < 1e-9
    # deterministic per evaluated_at
    assert dm.drift_for(fan, early_at) == dm.drift_for(fan, early_at)
    assert dm.drift_for(fan, late_at) == dm.drift_for(fan, late_at)
    # drift formula sanity: 90d ~0.27
    ninety = dm.drift_for(fan, start + timedelta(days=90))
    assert abs(abs(ninety) - 0.27) < 0.02


def test_c_adversarial_flip() -> None:
    from simulation.behavior import FanBehaviorModel
    from simulation.content_affinity import ContentAffinityModel
    from simulation.drift import adversarial_content_weight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    run = _make_run(9, "adversarial", "c-adv")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    content = world.create_content(creator, offer_type="SMALL_BUNDLE", price_minor=1999)
    beh = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id).for_fan(fan)
    aff_model = ContentAffinityModel(seed=run.seed, simulation_id=run.simulation_id)
    aff = aff_model.affinity_for(fan, content, beh)
    assert 0.0 <= aff.score <= 1.0
    # adversarial weights
    w30 = adversarial_content_weight(start + timedelta(days=30), start)
    w60 = adversarial_content_weight(start + timedelta(days=60), start)
    assert w30 == 0.8, f"day30 weight {w30} should be 0.8"
    assert w60 == -0.8, f"day60 weight {w60} should be -0.8"
    c30 = w30 * aff.score
    c60 = w60 * aff.score
    # if affinity >0, contributions flip sign
    if aff.score > 0.01:
        assert c30 > 0
        assert c60 < 0
        assert c30 > c60
    else:
        # even if tiny affinity, weights still flip
        assert w30 > 0 and w60 < 0
    # deterministic
    assert adversarial_content_weight(start + timedelta(days=30), start) == 0.8
    assert adversarial_content_weight(start + timedelta(days=44), start) == 0.8
    assert adversarial_content_weight(start + timedelta(days=45), start) == -0.8


def test_d_counterfactual() -> None:
    from commerce.offline_optimizer import (
        EXTRAPOLATIVE_WARNING,
        build_creator_dataset,
        train_creator_model,
    )
    from commerce.opportunity_optimization import (
        ConversationContext,
        FanCommercialSummary,
        FrozenCandidate,
        OfferHistorySummary,
        OptimizationInput,
        OwnershipContext,
    )
    from commerce.offline_optimizer import RowBundle
    from simulation.evaluation.counterfactual import counterfactual_harness
    from simulation.synthesizer import generate_mature_bundles

    # train a model on balanced data
    run = _make_run(11, "balanced", "d-cf-train")
    world = SimulationWorld(run)
    bundles, _, _ = generate_mature_bundles(world, n=90, step_hours=24, scenario_id="balanced")
    from simulation.synthesizer import build_creator_dataset_from_world
    ds = build_creator_dataset_from_world(world, bundles[:60])
    res = train_creator_model(ds)
    model = res.model
    assert model is not None, "model should not abstain with 60"
    # craft input with multiple candidates (same fan×same context×different offer)
    # use first bundle's context but create 3 candidates
    base_inp = bundles[0].input
    # create 3 distinct candidates
    cands = []
    for i, otype in enumerate(["SINGLE", "SMALL_BUNDLE", "PREMIUM"]):
        cands.append(
            FrozenCandidate(
                definition_id=9000 + i,
                version=1,
                stable_key=f"sim_test_{i}",
                offer_type=otype,
                price_minor=[500, 2000, 4000][i],
                currency="USD",
                family_id=None,
                canonical_vault_ids=(f"V{i}",),
                mapped_drop_ids=(f"drop_{i}",),
            )
        )
    # build custom OptimizationInput with same fan/context but new candidates
    custom_input = OptimizationInput(
        creator_id=base_inp.creator_id,
        opportunity_id=base_inp.opportunity_id,
        user_id=base_inp.user_id,
        evaluated_at=base_inp.evaluated_at,
        policy_version=base_inp.policy_version,
        frozen_candidates=tuple(cands),
        selected_definition_id=cands[0].definition_id,
        selected_definition_version=cands[0].version,
        ownership_context=base_inp.ownership_context,
        fan_commercial_summary=base_inp.fan_commercial_summary,
        offer_history_summary=base_inp.offer_history_summary,
        conversation_context=base_inp.conversation_context,
        evidence_context=base_inp.evidence_context,
        reengagement_context=base_inp.reengagement_context,
    )
    bundle_custom = RowBundle(input=custom_input, evidence=bundles[0].evidence, ledger_row=bundles[0].ledger_row)
    harness = counterfactual_harness([bundle_custom], model, max_candidates=5)
    assert len(harness) == 1
    entry = harness[0]
    assert entry["is_extrapolative"] is True
    assert "EXTRAPOLATIVE" in entry["warning"] and "OFF-POLICY" in entry["warning"]
    assert entry["warning"] == EXTRAPOLATIVE_WARNING
    assert len(entry["candidate_scores"]) == 3, f"should score all 3 candidates, got {entry['candidate_scores']}"
    assert entry["primary_definition"] == (9000, 1)
    assert entry["primary_prob"] is not None
    assert len(entry["deltas"]) == 3
    # primary delta 0
    prim_deltas = [d for d in entry["deltas"] if d["is_primary"]]
    assert len(prim_deltas) == 1 and abs(prim_deltas[0]["delta_vs_primary"]) < 1e-9
    # alternative delta reported
    alt = [d for d in entry["deltas"] if not d["is_primary"]][0]
    assert alt["delta_vs_primary"] is not None
    # never leaks latent
    assert "latent" not in json.dumps(entry, default=str).lower()
    assert "ground_truth" not in json.dumps(entry, default=str).lower()
    # also test multiple bundles
    harness2 = counterfactual_harness([bundle_custom, bundle_custom], model, max_candidates=2)
    assert len(harness2) == 2


def test_e_stress() -> None:
    from simulation.evaluation.stress import stress_sweep

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=120)

    counter = {"i": 0}

    def factory():
        counter["i"] += 1
        sid = f"stress-e-{counter['i']}-{uuid.uuid4().hex[:4]}"
        run = SimulationRun.create(
            simulation_id=sid,
            scenario_id="balanced",
            seed=42 + counter["i"],
            simulated_start=start,
            simulated_end=end,
            created_at=start,
        )
        return SimulationWorld(run)

    reports = stress_sweep(factory, scenario_id="balanced", ns=[60, 120])
    assert len(reports) == 2
    assert 60 in reports and 120 in reports
    r60 = reports[60]
    r120 = reports[120]
    assert r60.dataset_hash != r120.dataset_hash
    assert r60.metrics.brier is not None
    assert r120.metrics.brier is not None
    assert r60.metrics.log_loss is not None
    assert r60.train_n > 0 and r60.test_n > 0
    # small scaling docs: brier recorded vs n


def test_f_leakage() -> None:
    from simulation.evaluation.failures import leakage_injection_check
    from simulation.event import SimulationEvent

    # event guard
    with pytest.raises(ValueError):
        SimulationEvent.create(
            simulation_id="f-leak-ev",
            event_type="test",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            payload={"ground_truth": 1},
        )
    with pytest.raises(ValueError):
        SimulationEvent.create(
            simulation_id="f-leak-ev2",
            event_type="test",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            payload={"latent_probability": 0.5},
        )
    # helper must detect
    res = leakage_injection_check()
    assert res["event_leak_detected"] is True
    assert res["event_oracle_detected"] is True
    assert res["censored_ok"] is True
    assert res["snapshot_not_leaked"] is True
    assert res["overall_detected"] is True
    # also test build_supervised_label CENSORED for NONE exposure
    from commerce.offline_optimizer import build_supervised_label

    ev = {"exposure_state": "NONE", "maturity_state": "MATURE", "label": "CENSORED", "evidence_quality": "FULL", "recovered": False}
    out = build_supervised_label(evidence=ev, ledger_row={"reengagement_of": None})
    assert out.kind == "CENSORED" and out.binary is None


def test_g_contamination() -> None:
    from commerce.offline_optimizer import build_creator_dataset, build_supervised_label
    from simulation.evaluation.failures import creator_contamination_check
    from simulation.run import SimulationRun
    from simulation.scenarios import get_strategy
    from simulation.synthesizer import generate_mature_bundles
    from simulation.world import SimulationWorld

    res = creator_contamination_check()
    assert res["contamination_raised"] is True
    assert res["isolated"] is True
    assert res["overall_ok"] is True
    # direct isolation test
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=30)
    run = SimulationRun.create(simulation_id="g-direct", scenario_id="balanced", seed=10, simulated_start=start, simulated_end=end, created_at=start)
    world = SimulationWorld(run)
    creator_a = world.create_creator()
    creator_b = world.create_creator()
    strat_a = get_strategy("whales")
    strat_b = get_strategy("freebie_heavy")
    bundles_a, _, _ = generate_mature_bundles(world, n=12, creator=creator_a, offer_strategy=strat_a)
    bundles_b, _, _ = generate_mature_bundles(world, n=12, creator=creator_b, offer_strategy=strat_b)
    with pytest.raises(ValueError, match="cross-creator"):
        build_creator_dataset(creator_id=creator_a.creator_id, bundles=bundles_a + bundles_b)


def test_h_temporal() -> None:
    from commerce.offline_optimizer import build_supervised_label
    from commerce.opportunity_evidence import classify_opportunity_evidence
    from simulation.evaluation.failures import temporal_leakage_check
    from simulation.run import SimulationRun
    from simulation.synthesizer import generate_mature_bundles
    from simulation.world import SimulationWorld

    res = temporal_leakage_check()
    assert res["temporal_ok"] is True
    assert res["censored_via_label"] is True
    assert res["overall_ok"] is True
    # direct: future leak must be CENSORED when as_of before maturity
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=30)
    run = SimulationRun.create(simulation_id="h-direct", scenario_id="balanced", seed=20, simulated_start=start, simulated_end=end, created_at=start)
    world = SimulationWorld(run)
    bundles, ledgers, _ = generate_mature_bundles(world, n=5, step_hours=24)
    ledger = ledgers[0]
    # early as_of = simulated_start -> should be IMMATURE
    early_ev = classify_opportunity_evidence(ledger, as_of=start)
    assert early_ev.get("maturity_state") != "MATURE" or early_ev.get("label") in ("CENSORED", "UNAVAILABLE")
    out = build_supervised_label(evidence=early_ev, ledger_row=ledger)
    assert out.binary is None  # not PRIMARY
    assert out.kind in ("CENSORED", "UNAVAILABLE", "REENGAGEMENT_CHILD", "RECOVERED", "PARTIAL")
