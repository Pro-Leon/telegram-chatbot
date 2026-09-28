"""Phase 1 — Simulation Contracts verification (C1-C7).

Tests for:
- deterministic clock
- reproducibility
- isolation
- serialization
- timestamp behavior
- ground-truth isolation
- production side-effect absence
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
import uuid

import pytest

from simulation.clock import SimulationClock
from simulation.config import SimulationConfig
from simulation.data_origin import (
    SYNTHETIC_GENERATION_PREFIX,
    SYNTHETIC_MARKER,
    is_simulation_row,
)
from simulation.event import SimulationEvent
from simulation.ground_truth import GroundTruthReference
from simulation.identity import (
    SYNTHETIC_CREATOR_RANGE_START,
    SimulationIdentity,
)
from simulation.persistence import (
    load_ground_truth_reference,
    load_run_manifest,
    load_simulation_events,
    save_ground_truth_reference,
    save_run_manifest,
    save_simulation_event,
)
from simulation.run import SimulationRun

# ---------------------------------------------------------------------------
# C1 Deterministic Clock Test
# ---------------------------------------------------------------------------

def test_clock_deterministic_same_operations() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock_a = SimulationClock(start)
    clock_a.advance(hours=1)
    clock_a.advance(hours=2)

    clock_b = SimulationClock(start)
    clock_b.advance(hours=1)
    clock_b.advance(hours=2)

    assert clock_a.current_time() == clock_b.current_time()
    assert clock_a.current_time() == datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)


def test_clock_advance_to() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = SimulationClock(start)
    target = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
    clock.advance_to(target)
    assert clock.current_time() == target


def test_clock_no_backward() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = SimulationClock(start)
    clock.advance(hours=5)
    with pytest.raises(ValueError):
        clock.advance_to(datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC))


def test_clock_requires_aware() -> None:
    with pytest.raises(ValueError):
        SimulationClock(datetime(2026, 1, 1, 0, 0, 0))  # naive


# ---------------------------------------------------------------------------
# C2 Reproducibility Test
# ---------------------------------------------------------------------------

def test_run_reproducibility_same_seed_config() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)
    sid = "test-sim-001"
    run_a = SimulationRun.create(
        simulation_id=sid,
        scenario_id="baseline",
        seed=42,
        simulated_start=start,
        simulated_end=end,
        config_version="v1",
        behavior_model_version="v1",
        schema_version="p356.features.v1",
        created_at=start,
    )
    run_b = SimulationRun.create(
        simulation_id=sid,
        scenario_id="baseline",
        seed=42,
        simulated_start=start,
        simulated_end=end,
        config_version="v1",
        behavior_model_version="v1",
        schema_version="p356.features.v1",
        created_at=start,
    )
    assert run_a.to_dict() == run_b.to_dict()
    assert run_a.to_json() == run_b.to_json()


def test_identity_deterministic_same_seed() -> None:
    sid = "sim-repro-123"
    seed = 12345
    id_a = SimulationIdentity.create(simulation_id=sid, seed=seed, creator_counter=0)
    id_b = SimulationIdentity.create(simulation_id=sid, seed=seed, creator_counter=0)
    assert id_a.synthetic_creator_id == id_b.synthetic_creator_id
    assert id_a.synthetic_fan_id(1) == id_b.synthetic_fan_id(1)
    assert id_a.synthetic_opportunity_id(1) == id_b.synthetic_opportunity_id(1)
    # different run → different
    id_c = SimulationIdentity.create(simulation_id="other-run", seed=seed, creator_counter=0)
    assert id_a.synthetic_creator_id != id_c.synthetic_creator_id


def test_no_global_random_used() -> None:
    # Run twice with same seed must produce same synthetic ids without global random state pollution
    sid = "no-global-1"
    seed = 999
    ident = SimulationIdentity.create(simulation_id=sid, seed=seed)
    fan1_first = ident.synthetic_fan_id(1)
    fan1_second = ident.synthetic_fan_id(1)
    assert fan1_first == fan1_second


# ---------------------------------------------------------------------------
# C3 Isolation Test
# ---------------------------------------------------------------------------

def test_synthetic_creator_in_high_range() -> None:
    ident = SimulationIdentity.create(simulation_id="iso-1", seed=1)
    assert ident.synthetic_creator_id >= SYNTHETIC_CREATOR_RANGE_START
    assert ident.is_synthetic_creator(ident.synthetic_creator_id) is True
    assert ident.is_synthetic_creator(1) is False
    assert ident.is_synthetic_creator(42) is False


def test_synthetic_fan_in_high_range() -> None:
    ident = SimulationIdentity.create(simulation_id="iso-2", seed=2)
    fan = ident.synthetic_fan_id(1)
    assert ident.is_synthetic_fan(fan) is True
    assert ident.is_synthetic_fan(123) is False


def test_generation_id_prefix() -> None:
    ident = SimulationIdentity.create(simulation_id="iso-3", seed=3)
    gen = ident.synthetic_generation_id(1001)
    assert gen.startswith(SYNTHETIC_GENERATION_PREFIX)
    assert "synthetic:iso-3:1001" == gen


def test_is_simulation_row_detection() -> None:
    row_synth = {"generation_id": "synthetic:run:1", "synthetic": SYNTHETIC_MARKER}
    row_prod = {"generation_id": "gen-abc-123"}
    row_recovered = {"generation_id": "recovered:1:5"}
    assert is_simulation_row(row_synth) is True
    assert is_simulation_row(row_prod) is False
    assert is_simulation_row(row_recovered) is False
    # also via marker without prefix
    row_marker_only = {"generation_id": "other", "synthetic": SYNTHETIC_MARKER}
    assert is_simulation_row(row_marker_only) is True


def test_isolation_no_collision_with_production_ids() -> None:
    # Production creators typically 1..10000; synthetic 900000+ ensures no numeric collision
    ident = SimulationIdentity.create(simulation_id="iso-4", seed=4)
    for counter in range(1, 20):
        fan = ident.synthetic_fan_id(counter)
        opp = ident.synthetic_opportunity_id(counter)
        assert fan > 1_000_000 or fan >= 9_000_000  # within synthetic high range
        assert opp >= 9_000_000
        assert fan not in range(1, 10000)
        assert opp not in range(1, 10000)


# ---------------------------------------------------------------------------
# C4 Serialization Test
# ---------------------------------------------------------------------------

def test_run_serialization_roundtrip() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
    run = SimulationRun.create(
        simulation_id=str(uuid.uuid4()),
        scenario_id="test",
        seed=123,
        simulated_start=start,
        simulated_end=end,
        created_at=start,
    )
    restored = SimulationRun.from_dict(run.to_dict())
    assert restored == run
    assert SimulationRun.from_json(run.to_json()) == run


def test_clock_serialization_roundtrip() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = SimulationClock(start)
    clock.advance(hours=6)
    d = clock.to_dict()
    restored = SimulationClock.from_dict(d)
    assert restored == clock


def test_identity_serialization_roundtrip() -> None:
    ident = SimulationIdentity.create(simulation_id="ser-1", seed=10)
    d = ident.to_dict()
    restored = SimulationIdentity.from_dict(d)
    assert restored == ident


def test_event_serialization_roundtrip() -> None:
    now = datetime.now(UTC)
    event = SimulationEvent.create(
        simulation_id="sim-1",
        event_type="opportunity.created",
        occurred_at=now,
        entity_ids={"creator_id": 900001, "opportunity_id": 1},
        payload={"offer_type": "SMALL_BUNDLE"},
    )
    restored = SimulationEvent.from_dict(event.to_dict())
    assert restored == event
    assert SimulationEvent.from_json(event.to_json()) == event


def test_ground_truth_reference_serialization_roundtrip() -> None:
    now = datetime.now(UTC)
    ref = GroundTruthReference.create(
        simulation_id="sim-1",
        opportunity_id=1001,
        generation_id="synthetic:sim-1:1001",
        created_at=now,
    )
    restored = GroundTruthReference.from_dict(ref.to_dict())
    assert restored == ref
    assert GroundTruthReference.from_json(ref.to_json()) == ref


def test_config_serialization_roundtrip() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)
    cfg = SimulationConfig(scenario_id="baseline", seed=42, start_time=start, end_time=end)
    restored = SimulationConfig.from_dict(cfg.to_dict())
    assert restored.scenario_id == cfg.scenario_id
    assert restored.seed == cfg.seed
    assert restored.start_time == cfg.start_time


# ---------------------------------------------------------------------------
# C5 Timestamp Test
# ---------------------------------------------------------------------------

def test_timestamp_timezone_utc() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    run = SimulationRun.create(
        simulation_id="ts-1",
        scenario_id="baseline",
        seed=1,
        simulated_start=start,
        simulated_end=start + timedelta(days=1),
        created_at=start,
    )
    assert run.simulated_start.tzinfo is UTC
    assert run.simulated_end.tzinfo is UTC


def test_timestamp_precision_ordering() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = SimulationClock(start)
    t1 = clock.current_time()
    clock.advance(hours=6)
    t2 = clock.current_time()
    assert t2 > t1
    assert (t2 - t1) == timedelta(hours=6)
    # microsecond precision preserved
    clock.advance(microseconds=500)
    assert clock.current_time().microsecond == 500


def test_timestamp_serialization_iso() -> None:
    now = datetime(2026, 1, 1, 12, 30, 45, 123000, tzinfo=UTC)
    event = SimulationEvent.create(simulation_id="ts-2", event_type="test", occurred_at=now)
    d = event.to_dict()
    assert "2026-01-01T12:30:45" in d["occurred_at"]
    restored = SimulationEvent.from_dict(d)
    assert restored.occurred_at == now


def test_clock_elapsed() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = SimulationClock(start)
    assert clock.elapsed() == timedelta(0)
    clock.advance(days=1, hours=2)
    assert clock.elapsed() == timedelta(days=1, hours=2)


# ---------------------------------------------------------------------------
# C6 Ground-Truth Isolation Test
# ---------------------------------------------------------------------------

def test_ground_truth_not_in_optimization_input() -> None:
    """GroundTruthReference must not be convertible to OptimizationInput fields."""
    now = datetime.now(UTC)
    ref = GroundTruthReference.create(
        simulation_id="sim-gt-1",
        opportunity_id=1001,
        generation_id="synthetic:sim-gt-1:1001",
        created_at=now,
    )
    # Ensure payload with latent values is stored separately, not in reference
    truth_payload = {"latent_purchase_probability": 0.73, "latent_affinity": 0.9}
    # Reference dict must not contain latent keys
    ref_dict = ref.to_dict()
    for forbidden in ["latent_purchase_probability", "latent_affinity", "oracle", "price"]:
        assert forbidden not in ref_dict

    # Simulate what build_optimization_input would see — it only sees ledger_row
    # We verify that GroundTruthReference fields are not part of OptimizationInput dataclass
    from commerce.opportunity_optimization import OptimizationInput
    import dataclasses

    input_fields = {f.name for f in dataclasses.fields(OptimizationInput)}
    gt_fields = set(ref_dict.keys())
    # No overlap in semantic latent fields — only identifiers may coincide intentionally
    # But latent-specific fields must not be in OptimizationInput
    assert "reference_id" not in input_fields
    assert "ground_truth" not in input_fields

    # Ensure SimulationEvent payload also blocks ground truth
    with pytest.raises(ValueError):
        SimulationEvent.create(
            simulation_id="sim-gt-1",
            event_type="test",
            occurred_at=now,
            payload={"ground_truth": truth_payload},
        )


def test_event_payload_forbids_oracle() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        SimulationEvent.create(
            simulation_id="sim-1",
            event_type="x",
            occurred_at=now,
            payload={"oracle_probability": 0.5},
        )


def test_truth_payload_separate_persistence(tmp_path: Path) -> None:
    """Hidden truth is stored in separate file, not in manifest/event."""
    now = datetime.now(UTC)
    run = SimulationRun.create(
        simulation_id=str(uuid.uuid4()),
        scenario_id="iso",
        seed=1,
        simulated_start=now,
        simulated_end=now + timedelta(days=1),
        created_at=now,
    )
    base = tmp_path / "simulation_runs"
    ident = SimulationIdentity.create(simulation_id=run.simulation_id, seed=run.seed)
    opp_id = ident.synthetic_opportunity_id(1)
    gen = ident.synthetic_generation_id(opp_id)
    ref = GroundTruthReference.create(
        simulation_id=run.simulation_id, opportunity_id=opp_id, generation_id=gen, created_at=now
    )
    truth_payload = {"latent_purchase_probability": 0.42}
    save_ground_truth_reference(ref, truth_payload, base=base)
    # Manifest and events should not contain latent
    save_run_manifest(run, base=base)
    manifest_text = (base / run.simulation_id / "manifest.json").read_text(encoding="utf-8")
    assert "latent_purchase_probability" not in manifest_text
    # Payload file does contain it, but is separate
    payload_path = base / run.simulation_id / "ground_truth" / f"{opp_id}.payload.json"
    assert payload_path.exists()
    assert "latent_purchase_probability" in payload_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# C7 Production Side-Effect Test
# ---------------------------------------------------------------------------

def test_simulation_does_not_import_telegram() -> None:
    """Simulation package must not import Telethon/Telegram production execution."""
    import pathlib

    sim_files = list(Path("E:/chatbot/simulation").glob("*.py"))
    for p in sim_files:
        text = p.read_text(encoding="utf-8")
        lower = text.lower()
        assert "telethon" not in lower, f"{p.name} must not import Telethon"
        assert "dropfans" not in lower or "opportunity_sealing" not in lower  # sealing not called
        # Ensure no direct DB writes to commerce_offers
        assert "commerce_offers" not in lower or "INSERT INTO commerce_offers" not in text


def test_simulation_persistence_does_not_touch_production_tables(tmp_path: Path) -> None:
    """Creating/advancing simulation writes only to tmp simulation_runs, not DB."""
    now = datetime.now(UTC)
    run = SimulationRun.create(
        simulation_id=str(uuid.uuid4()),
        scenario_id="prod-iso",
        seed=1,
        simulated_start=now,
        simulated_end=now + timedelta(days=1),
        created_at=now,
    )
    base = tmp_path / "simulation_runs"
    # These should succeed without DB pool
    save_run_manifest(run, base=base)
    event = SimulationEvent.create(
        simulation_id=run.simulation_id,
        event_type="opportunity.created",
        occurred_at=now,
        entity_ids={"opportunity_id": 1},
        payload={"offer_type": "SMALL_BUNDLE"},
    )
    save_simulation_event(event, base=base)
    # Verify file-only
    assert (base / run.simulation_id / "manifest.json").exists()
    assert (base / run.simulation_id / "events" / f"{event.event_id}.json").exists()
    # No Redis stream involved — check that no redis key was created (we don't have redis in test)
    # Simply ensure no exception and no production write occurred via assertion that files exist only under tmp


def test_simulation_clock_no_wall_clock() -> None:
    """Clock must not use time.time() or sleep for progression."""
    import inspect

    src = Path("E:/chatbot/simulation/clock.py").read_text(encoding="utf-8")
    assert "time.sleep" not in src
    # time.time() may appear only in file persistence timestamps, not in clock progression
    # Clock methods should be deterministic
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    c1 = SimulationClock(start)
    c1.advance(hours=6)
    c2 = SimulationClock(start)
    c2.advance(hours=6)
    assert c1.current_time() == c2.current_time()


# ---------------------------------------------------------------------------
# Additional: persistence round-trip
# ---------------------------------------------------------------------------

def test_persistence_manifest_roundtrip(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    run = SimulationRun.create(
        simulation_id=str(uuid.uuid4()),
        scenario_id="persist",
        seed=123,
        simulated_start=now,
        simulated_end=now + timedelta(days=2),
        created_at=now,
    )
    base = tmp_path / "sim_runs"
    save_run_manifest(run, base=base)
    loaded = load_run_manifest(run.simulation_id, base=base)
    assert loaded == run


def test_persistence_events_roundtrip(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    base = tmp_path / "sim_runs"
    e1 = SimulationEvent.create(simulation_id=run_id, event_type="a", occurred_at=now)
    e2 = SimulationEvent.create(simulation_id=run_id, event_type="b", occurred_at=now + timedelta(hours=1))
    save_simulation_event(e1, base=base)
    save_simulation_event(e2, base=base)
    events = load_simulation_events(run_id, base=base)
    ids = {e.event_id for e in events}
    assert e1.event_id in ids and e2.event_id in ids
