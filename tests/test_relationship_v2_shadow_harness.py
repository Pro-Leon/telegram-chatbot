"""Stage F1 tests: shadow comparison records and window metrics.

Pure harness, no infra. Proves: end-to-end observe->compare->record->
summarize works on synthetic turns, match rates and char deltas compute,
empty windows summarize cleanly, invalid metadata rejected.
"""

from __future__ import annotations

import pytest

from relationship_v2.domain.shadow import ShadowDivergence, ShadowInputs
from relationship_v2.services.shadow import compare_with_legacy, observe_turn
from relationship_v2.services.shadow_harness import (
    ShadowMetrics,
    compare_turn,
)


def _inputs(**kw) -> ShadowInputs:
    base = {
        "generation_id": "gen-1",
        "creator_id": 1,
        "user_id": 2,
        "inbound_text": "hey, just got home",
        "inbound_text_length": 19,
        "current_relationship_lifecycle": "new",
        "relationship_version": 1,
        "current_escalation_stage": "relationship",
        "memory_lines": ["job=photographer"],
        "legacy_stage": "relationship",
        "legacy_memory_count": 1,
    }
    base.update(kw)
    return ShadowInputs(**base)


def test_full_compare_flow() -> None:
    inputs = _inputs()
    report = observe_turn(inputs)
    divs = compare_with_legacy(report, inputs)
    turn = compare_turn(
        report, divs, legacy_stage="relationship", legacy_memory_count=1,
        legacy_chars=900,
    )
    assert turn.stage_match is True
    assert turn.divergence_count == len(divs)
    assert turn.char_delta == report.assembly_chars - 900
    assert turn.v2_memory_lines == 1


def test_mismatch_records_divergence() -> None:
    inputs = _inputs(legacy_stage="present_offer", legacy_memory_count=5)
    report = observe_turn(inputs)
    divs = compare_with_legacy(report, inputs)
    assert any(d.dimension == "stage" for d in divs)
    turn = compare_turn(report, divs, legacy_stage="present_offer", legacy_chars=400)
    assert turn.stage_match is False


def test_metrics_window() -> None:
    m = ShadowMetrics()
    r1 = observe_turn(_inputs(generation_id="g1"))
    r2 = observe_turn(_inputs(generation_id="g2"))
    m.record(compare_turn(r1, [], legacy_stage=r1.selected_stage, legacy_chars=500))
    m.record(compare_turn(r2, [], legacy_stage="other", legacy_chars=700))
    s = m.summarize()
    assert s.turns == 2
    assert s.stage_comparisons == 2
    assert s.stage_matches == 1
    assert s.stage_match_rate == 0.5
    assert s.divergence_total == 0
    assert s.max_abs_char_delta is not None and s.max_abs_char_delta >= 0


def test_empty_window() -> None:
    s = ShadowMetrics().summarize()
    assert s.turns == 0
    assert s.stage_match_rate is None
    assert s.avg_char_delta is None


def test_no_legacy_metadata_ok() -> None:
    report = observe_turn(_inputs())
    turn = compare_turn(report, [])
    assert turn.stage_match is None
    assert turn.char_delta is None


def test_invalid_metadata_rejected() -> None:
    report = observe_turn(_inputs())
    with pytest.raises(ValueError):
        compare_turn(report, [], legacy_chars=-5)


def test_divergence_model_shape() -> None:
    d = ShadowDivergence(
        dimension="stage", v2_value="a", legacy_value="b", note="n"
    )
    assert d.dimension == "stage"


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "shadow_harness.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.persistence.repository" not in mods
