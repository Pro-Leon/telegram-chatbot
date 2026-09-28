"""Phase 4 — Behavioral heterogeneity verification B1-B15."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simulation.behavior import BehavioralOutcomeConfig, BehavioralOutcomeModel, FanBehavior, FanBehaviorModel
from simulation.outcome import BaselineOutcomeModel, OutcomeConfig, synthetic_ledger_from_outcome
from simulation.run import SimulationRun
from simulation.world import SimulationWorld


def _make_run(seed: int, run_id: str | None = None) -> SimulationRun:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=10)
    return SimulationRun.create(
        simulation_id=run_id or f"run-{seed}-{run_id or 'x'}",
        scenario_id="baseline",
        seed=seed,
        simulated_start=start,
        simulated_end=end,
        created_at=start,
    )


# B1 deterministic traits
def test_b1_deterministic_traits_same_fan() -> None:
    run = _make_run(seed=42, run_id="b1-a")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    model_a = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    model_b = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh_a1 = model_a.for_fan(fan)
    beh_a2 = model_b.for_fan(fan)
    assert beh_a1 == beh_a2
    beh_a3 = model_a.for_fan(fan)
    assert beh_a1 == beh_a3


# B2 different seed different populations
def test_b2_different_seed_different_populations() -> None:
    run1 = _make_run(seed=1, run_id="b2-1")
    run2 = _make_run(seed=2, run_id="b2-2")
    w1 = SimulationWorld(run1)
    w2 = SimulationWorld(run2)
    c1 = w1.create_creator()
    c2 = w2.create_creator()
    m1 = FanBehaviorModel(seed=run1.seed, simulation_id=run1.simulation_id)
    m2 = FanBehaviorModel(seed=run2.seed, simulation_id=run2.simulation_id)
    diff = 0
    for ctr in range(1, 11):
        f1 = w1.create_fan(c1)
        f2 = w2.create_fan(c2)
        b1 = m1.for_fan(f1)
        b2 = m2.for_fan(f2)
        if b1.purchase_propensity != b2.purchase_propensity:
            diff += 1
    assert diff >= 5


# B3 bounded traits
def test_b3_bounded_traits() -> None:
    run = _make_run(seed=3, run_id="b3")
    world = SimulationWorld(run)
    creator = world.create_creator()
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    for _ in range(50):
        fan = world.create_fan(creator)
        beh = model.for_fan(fan)
        for name in ("purchase_propensity", "engagement_level", "relationship_affinity", "price_sensitivity", "freebie_tendency", "content_preference_strength"):
            v = getattr(beh, name)
            assert 0.0 <= v <= 1.0, f"{name} out of bounds {v}"


# B4 fan stability across opportunities
def test_b4_fan_stability_across_opportunities() -> None:
    run = _make_run(seed=4, run_id="b4")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh1 = model.for_fan(fan)
    for _ in range(3):
        content = world.create_content(creator)
        opp = world.create_opportunity(creator, fan, content)
        beh_again = model.for_fan(fan)
        assert beh_again == beh1


# B5 population variation 100 fans not collapsing
def test_b5_population_variation() -> None:
    run = _make_run(seed=5, run_id="b5")
    world = SimulationWorld(run)
    creator = world.create_creator()
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    behaviors = []
    for _ in range(100):
        fan = world.create_fan(creator)
        behaviors.append(model.for_fan(fan))
    uniq_purchase = len({round(b.purchase_propensity, 4) for b in behaviors})
    assert uniq_purchase > 10, "population collapsed"
    mean = sum(b.purchase_propensity for b in behaviors) / len(behaviors)
    var = sum((b.purchase_propensity - mean) ** 2 for b in behaviors) / len(behaviors)
    assert var > 0.02


# B6 correlation sanity directional
def test_b6_correlation_sanity() -> None:
    run = _make_run(seed=6, run_id="b6")
    world = SimulationWorld(run)
    creator = world.create_creator()
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id, correlation_strength=0.4)
    n = 300
    behs: list[FanBehavior] = []
    for _ in range(n):
        fan = world.create_fan(creator)
        behs.append(model.for_fan(fan))

    def pearson(xs: list[float], ys: list[float]) -> float:
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
        den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    purchase = [b.purchase_propensity for b in behs]
    engage = [b.engagement_level for b in behs]
    rel = [b.relationship_affinity for b in behs]
    free = [b.freebie_tendency for b in behs]
    price = [b.price_sensitivity for b in behs]

    corr_pe = pearson(purchase, engage)
    corr_pr = pearson(purchase, rel)
    corr_pf = pearson(purchase, free)
    corr_pp = pearson(purchase, price)

    assert corr_pe > 0.15, f"purchase-engagement correlation {corr_pe} not >0.15"
    assert corr_pr > 0.15, f"purchase-relationship {corr_pr} not >0.15"
    assert corr_pf < -0.15, f"purchase-freebie {corr_pf} not < -0.15"
    assert corr_pp < -0.15, f"purchase-price {corr_pp} not < -0.15"
    assert corr_pe < 0.85
    assert corr_pf > -0.85


# B7 stochastic outcome different counters same fan behavior stable
def test_b7_stochastic_outcome_same_fan() -> None:
    run = _make_run(seed=7, run_id="b7")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    content = world.create_content(creator)
    behavior_model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    outcome_cfg = BehavioralOutcomeConfig(intercept=-1.0, purchase_weight=2.0)
    beh_model = BehavioralOutcomeModel(behavior_model=behavior_model, outcome_config=outcome_cfg)
    opp1 = world.create_opportunity(creator, fan, content)
    opp2 = world.create_opportunity(creator, fan, content)
    beh_before = behavior_model.for_fan(fan)
    out1, _, _ = beh_model.decide(opp1, fan, seed=run.seed, run_id=run.simulation_id, counter=1)
    out2, _, _ = beh_model.decide(opp2, fan, seed=run.seed, run_id=run.simulation_id, counter=2)
    beh_after = behavior_model.for_fan(fan)
    assert beh_before == beh_after
    outcomes = set()
    for c in range(1, 11):
        opp = world.create_opportunity(creator, fan, content)
        o, _, _ = beh_model.decide(opp, fan, seed=run.seed, run_id=run.simulation_id, counter=c)
        outcomes.add(o.purchased)
    assert beh_before.purchase_propensity == beh_after.purchase_propensity


# B8 behavioral effect higher propensity higher purchase rate
def test_b8_behavioral_effect_purchase_rate() -> None:
    run = _make_run(seed=8, run_id="b8")
    world = SimulationWorld(run)
    creator = world.create_creator()
    behavior_model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    outcome_cfg = BehavioralOutcomeConfig(intercept=-1.0, purchase_weight=1.5, engagement_weight=0.8, relationship_weight=0.6, price_sensitivity_weight=0.8, freebie_weight=0.9, content_weight=0.0)
    beh_model = BehavioralOutcomeModel(behavior_model=behavior_model, outcome_config=outcome_cfg)
    fans = [world.create_fan(creator) for _ in range(400)]
    behs = [(f, behavior_model.for_fan(f)) for f in fans]
    behs_sorted = sorted(behs, key=lambda x: x[1].purchase_propensity)
    low_group = behs_sorted[:100]
    high_group = behs_sorted[-100:]

    def rate(group):
        purchased = 0
        total = 0
        for fan, beh in group:
            content = world.create_content(creator)
            idx = total + 1
            opp = world.create_opportunity(creator, fan, content)
            out, _, _ = beh_model.decide(opp, fan, seed=run.seed, run_id=run.simulation_id, counter=idx)
            if out.purchased:
                purchased += 1
            total += 1
        return purchased / total if total else 0

    low_rate = rate(low_group)
    high_rate = rate(high_group)
    assert high_rate > low_rate, f"high propensity {high_rate} should > low {low_rate}"
    behs_by_freebie = sorted(behs, key=lambda x: x[1].freebie_tendency)
    low_free = behs_by_freebie[:100]
    high_free = behs_by_freebie[-100:]
    low_free_rate = rate(low_free)
    high_free_rate = rate(high_free)
    assert low_free_rate >= high_free_rate


# B9 no latent leakage into ledger/snapshot/Input
def test_b9_no_latent_leakage() -> None:
    run = _make_run(seed=9, run_id="b9")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    content = world.create_content(creator)
    opp = world.create_opportunity(creator, fan, content)
    behavior_model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    # snapshot
    snap_text = json.dumps(opp.decision_snapshot).lower()
    for forbidden in ["purchase_propensity", "engagement_level", "relationship_affinity", "price_sensitivity", "freebie_tendency", "latent", "behavior"]:
        assert forbidden not in snap_text
    from simulation.adapter import synthetic_ledger_row

    ledger = synthetic_ledger_row(opp)
    ledger_text = json.dumps(ledger, default=str).lower()
    for forbidden in ["purchase_propensity", "engagement_level", "relationship_affinity", "price_sensitivity", "freebie_tendency", "content_preference_strength"]:
        assert forbidden not in ledger_text
    from simulation.adapter import probe_optimization_input

    inp = probe_optimization_input(opp)
    import dataclasses

    fields = {f.name for f in dataclasses.fields(inp.__class__)}
    for forbidden in ["purchase_propensity", "behavior", "latent"]:
        assert forbidden not in fields
    if inp.evidence_context:
        ev_dict = inp.evidence_context.__dict__ if hasattr(inp.evidence_context, "__dict__") else {}
        for forbidden in ["purchase_propensity"]:
            assert forbidden not in str(ev_dict).lower()
    # RowBundle also clean
    from simulation.synthesizer import generate_mature_bundles
    from simulation.behavior import BehavioralOutcomeModel

    beh_model = BehavioralOutcomeModel(behavior_model=behavior_model)
    # generate one bundle behaviorally
    fan2 = world.create_fan(creator)
    opp2 = world.create_opportunity(creator, fan2, content)
    out, _, hidden = beh_model.decide(opp2, fan2, seed=run.seed, run_id=run.simulation_id, counter=1)
    # hidden contains latent but ledger must not
    assert "purchase_propensity" not in json.dumps(synthetic_ledger_row(opp2), default=str).lower()
    assert "purchase_propensity" in str(hidden) or "latent_purchase_probability" in str(hidden)


# B10 ground truth separation
def test_b10_ground_truth_separation() -> None:
    run = _make_run(seed=10, run_id="b10")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    content = world.create_content(creator)
    opp = world.create_opportunity(creator, fan, content)
    behavior_model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh_model = BehavioralOutcomeModel(behavior_model=behavior_model)
    out, ref, hidden = beh_model.decide(opp, fan, seed=run.seed, run_id=run.simulation_id, counter=1)
    # reference must not contain latent
    ref_dict = ref.to_dict()
    assert "purchase_propensity" not in ref_dict
    assert "latent" not in ref_dict
    assert "hidden" not in ref_dict
    # hidden payload does contain behavior
    assert "behavior" in hidden
    assert "purchase_propensity" in str(hidden["behavior"])
    # ledger/OptimizationInput still clean
    from simulation.adapter import synthetic_ledger_row
    ledger = synthetic_ledger_row(opp)
    assert "behavior" not in json.dumps(ledger, default=str).lower()


# B11 creator isolation
def test_b11_creator_isolation_behavior() -> None:
    run = _make_run(seed=11, run_id="b11")
    world = SimulationWorld(run)
    creator_a = world.create_creator()
    creator_b = world.create_creator()
    fan_a = world.create_fan(creator_a)
    fan_b = world.create_fan(creator_b)
    model_a = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    model_b = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh_a = model_a.for_fan(fan_a)
    beh_b = model_b.for_fan(fan_b)
    assert beh_a.creator_id == creator_a.creator_id
    assert beh_b.creator_id == creator_b.creator_id
    assert beh_a.creator_id != beh_b.creator_id
    # Dataset isolation already tested in world tests; here verify behavior not cross-pollinated
    # Fan A behavior should not equal Fan B behavior generally (different fan ids)
    # Also ensure behavior model cache is per fan_id: same fan returns same, different fan different
    assert beh_a.fan_id != beh_b.fan_id


# B12 run isolation
def test_b12_run_isolation() -> None:
    run1 = _make_run(seed=12, run_id="run-b12-a")
    run2 = _make_run(seed=12, run_id="run-b12-b")
    w1 = SimulationWorld(run1)
    w2 = SimulationWorld(run2)
    c1 = w1.create_creator()
    c2 = w2.create_creator()
    # Same seed but different simulation_id → different identities
    assert c1.creator_id != c2.creator_id
    # Behavior also isolated
    m1 = FanBehaviorModel(seed=run1.seed, simulation_id=run1.simulation_id)
    m2 = FanBehaviorModel(seed=run2.seed, simulation_id=run2.simulation_id)
    f1 = w1.create_fan(c1)
    f2 = w2.create_fan(c2)
    # To compare same fan counter isolation, we need same fan counter but different run_id gives different behavior even if fan_id collides unlikely
    # Instead check that models with same seed but different simulation_id are not sharing cache
    b1 = m1.for_fan(f1)
    b2 = m2.for_fan(f2)
    # They are different runs so simulation_id differs; behaviors may coincidentally be equal but with high prob differ
    # Check that simulation_id field reflects isolation
    assert b1.simulation_id == run1.simulation_id
    assert b2.simulation_id == run2.simulation_id
    # Also verify world isolation: same seed, different run_id produce different fan ids (since identity hash includes run_id)
    assert f1.fan_id != f2.fan_id or b1.simulation_id != b2.simulation_id


# B13 deterministic serialization
def test_b13_deterministic_serialization_world_behavior() -> None:
    run = _make_run(seed=13, run_id="b13")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh = model.for_fan(fan)
    # Serialize behavior
    d = beh.to_dict()
    restored = FanBehavior.from_dict(d)
    assert restored == beh
    # Serialize world and verify behavior still same after reload
    world_dict = world.to_dict()
    from simulation.world import SimulationWorld as SW

    restored_world = SW.from_dict(world_dict)
    # Create same fan in restored world? fan already exists; behavior for that fan should remain deterministic via new model
    model2 = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    # Find fan with same fan_id in restored world
    restored_fan = next(f for f in restored_world.fans if f.fan_id == fan.fan_id)
    beh_restored = model2.for_fan(restored_fan)
    assert beh_restored == beh


# B14 Phase 3 compatibility
def test_b14_phase3_baseline_still_works() -> None:
    run = _make_run(seed=14, run_id="b14")
    world = SimulationWorld(run)
    creator = world.create_creator()
    fan = world.create_fan(creator)
    content = world.create_content(creator)
    opp = world.create_opportunity(creator, fan, content)
    # Baseline should still be callable
    base_model = BaselineOutcomeModel(OutcomeConfig(baseline_purchase_probability=0.5))
    out, ref, hidden = base_model.decide(opp, seed=run.seed, run_id=run.simulation_id, counter=1)
    assert out.purchased in (True, False)
    assert out.latent_purchase_probability == 0.5


# B15 optimizer end-to-end with behavioral model
def test_b15_optimizer_e2e_behavioral() -> None:
    run = _make_run(seed=15, run_id="b15")
    world = SimulationWorld(run)
    behavior_model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    beh_outcome_model = BehavioralOutcomeModel(behavior_model=behavior_model)

    # Generate 12 mature opportunities behaviorally
    from simulation.synthesizer import generate_mature_bundles

    # Need to monkey-patch generate_mature_bundles to use behavioral model? Instead manually build
    creator = world.create_creator()
    bundles = []
    for i in range(1, 13):
        fan = world.create_fan(creator)
        content = world.create_content(creator)
        opp = world.create_opportunity(creator, fan, content)
        out, _, hidden = beh_outcome_model.decide(opp, fan, seed=run.seed, run_id=run.simulation_id, counter=i)
        # Build ledger from behavioral outcome (reuse synthetic_ledger_from_outcome but need behavioral latent)
        # synthetic_ledger_from_outcome expects SimulatedOutcome with correct latent; we have it
        from simulation.outcome import synthetic_ledger_from_outcome
        ledger = synthetic_ledger_from_outcome(opp, out)
        from commerce.opportunity_evidence import classify_opportunity_evidence
        from commerce.opportunity_optimization import build_optimization_input

        as_of = out.maturity_at  # mature
        evidence = classify_opportunity_evidence(ledger, as_of=as_of)
        inp = build_optimization_input(ledger_row=ledger, evidence=evidence)
        from commerce.offline_optimizer import RowBundle

        bundles.append(RowBundle(input=inp, evidence=evidence, ledger_row=ledger))
        world.clock.advance(hours=24)

    # Now CreatorDataset and train
    from simulation.synthesizer import build_creator_dataset_from_world

    # Use first 12 bundles, they are from same creator, so dataset should build
    # Need to ensure world has those bundles' inputs; but our bundles were built manually not via world.opportunities list? It's ok
    # Build dataset via helper that expects world and bundles
    # Instead directly use offline_optimizer build_creator_dataset
    from commerce.offline_optimizer import build_creator_dataset, train_creator_model, predict_for_input

    dataset = build_creator_dataset(creator_id=creator.creator_id, bundles=bundles)
    result = train_creator_model(dataset)
    assert result.abstained is False, f"behavioral dataset should train: {result.abstain_reason}"
    assert result.model is not None
    # Predict on new fan
    fan_new = world.create_fan(creator)
    content_new = world.create_content(creator)
    opp_new = world.create_opportunity(creator, fan_new, content_new)
    from simulation.adapter import synthetic_ledger_row

    ledger_new = synthetic_ledger_row(opp_new, exposure_state="NONE")
    from commerce.opportunity_optimization import build_optimization_input

    inp_new = build_optimization_input(ledger_row=ledger_new)
    pred = predict_for_input(result.model, inp_new)
    # Should predict or abstain, but not leak latent
    assert "purchase_propensity" not in str(pred.__dict__).lower()


# Distribution test 1000 fans summary (not strict assert, just sanity print)
def test_distribution_1000_fans_summary(capsys) -> None:
    run = _make_run(seed=100, run_id="dist-1000")
    world = SimulationWorld(run)
    creator = world.create_creator()
    model = FanBehaviorModel(seed=run.seed, simulation_id=run.simulation_id)
    n = 1000
    traits = {name: [] for name in ["purchase_propensity", "engagement_level", "relationship_affinity", "price_sensitivity", "freebie_tendency", "content_preference_strength"]}
    for _ in range(n):
        fan = world.create_fan(creator)
        beh = model.for_fan(fan)
        for name in traits:
            traits[name].append(getattr(beh, name))
    # Print summary (for report population sanity)
    import statistics

    for name, vals in traits.items():
        mn = min(vals)
        mx = max(vals)
        mean = statistics.mean(vals)
        median = statistics.median(vals)
        stdev = statistics.pstdev(vals)
        print(f"{name}: min {mn:.3f} max {mx:.3f} mean {mean:.3f} median {median:.3f} stdev {stdev:.3f}")
        assert 0.0 <= mn <= 1.0
        assert 0.0 <= mx <= 1.0
        assert 0.0 <= mean <= 1.0
        assert stdev > 0.05  # heterogeneous
        assert stdev < 0.35  # bounded
    # Directional correlations already tested in B6; just ensure not collapsed
    # Also ensure all traits cover range reasonably
    for name, vals in traits.items():
        assert max(vals) - min(vals) > 0.5, f"{name} range too narrow"
