"""Stage C5 tests: proactive recall ranking without topic overlap.

Pure ranker, no infra. Proves: important unresolved callbacks surface on
"hey", due loops outrank, recent recalls penalized, budget bounded,
ordering deterministic.
"""

from __future__ import annotations

import pytest

from relationship_v2.services.proactive_recall import (
    RecallCandidate,
    rank_candidates,
    recency_decay,
    score_candidate,
)


def _cand(ref_id: str, **kw) -> RecallCandidate:
    base = {"ref_id": ref_id, "kind": "loop"}
    base.update(kw)
    return RecallCandidate(**base)


def test_callback_without_overlap_surfaces() -> None:
    interview = _cand(
        "interview", importance="high", salience=0.7, age_days=14.0,
        unresolved=True, relevance=0.0,
    )
    trivial = _cand(
        "lunch", importance="low", salience=0.2, age_days=1.0,
        unresolved=False, relevance=0.9,
    )
    sel = rank_candidates([trivial, interview], budget=2)
    assert sel.items[0].ref_id == "interview"
    assert sel.items[0].reason == "high+unresolved+callback"
    assert sel.dropped == 0


def test_due_outranks_ordinary() -> None:
    due = _cand("rent", importance="high", salience=0.6, unresolved=True, due=True)
    plain = _cand("hobby", importance="high", salience=0.6, unresolved=True)
    sel = rank_candidates([plain, due], budget=2)
    assert sel.items[0].ref_id == "rent"


def test_recent_recall_penalized() -> None:
    fresh = _cand("a", importance="high", salience=0.8, unresolved=True)
    nagged = _cand(
        "b", importance="high", salience=0.8, unresolved=True, recalled_days_ago=0.5
    )
    assert score_candidate(nagged) < score_candidate(fresh)
    sel = rank_candidates([nagged, fresh], budget=2)
    assert sel.items[0].ref_id == "a"


def test_budget_bounded_with_dropped_count() -> None:
    cands = [_cand(f"c{i}", importance="normal") for i in range(6)]
    sel = rank_candidates(cands, budget=3)
    assert len(sel.items) == 3
    assert sel.dropped == 3


def test_deterministic_tiebreak() -> None:
    a = _cand("b-id", importance="normal")
    b = _cand("a-id", importance="normal")
    first = rank_candidates([a, b], budget=2)
    second = rank_candidates([b, a], budget=2)
    assert [i.ref_id for i in first.items] == [i.ref_id for i in second.items]


def test_budget_rejected() -> None:
    with pytest.raises(ValueError):
        rank_candidates([], budget=0)


def test_recency_decay_helper() -> None:
    assert recency_decay(0.0) == pytest.approx(1.0)
    assert 0.0 < recency_decay(14.0) < 1.0
    with pytest.raises(ValueError):
        recency_decay(-1.0)


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "proactive_recall.py"
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
