"""F13 code-slice tests: debug dossier rendering from context + rows.

Pure composer, no infra. Proves: all eight sections present, lines
bounded, empty stores render (never raise), provenance required,
deterministic output.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.domain.context import RelationshipContextSnapshot
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.services.context_assembly import assemble
from relationship_v2.services.dossier import build_dossier
from relationship_v2.services.turn_context import TurnContext

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def _ctx(**kw) -> TurnContext:
    snap = RelationshipContextSnapshot(
        relationship_id=uuid4(), creator_id=1, user_id=2,
        lifecycle=RelationshipLifecycle.WARMING, familiarity="familiar",
        comfort="moderate", version=3, as_of=NOW, absence_days=5,
        has_boundaries=True, buyer_class="repeat", evidence_hash="x",
        provenance="t",
    )
    base = {
        "generation_id": "gen-1", "creator_id": 1, "user_id": 2,
        "relationship_id": snap.relationship_id, "snapshot": snap,
        "commerce": None, "commerce_unavailable": True,
        "recall_refs": ["loop:x"],
        "assembled": assemble("gen-1", 1, 2, {}, now=NOW),
        "provenance": "t",
    }
    base.update(kw)
    return TurnContext(**base)


def test_all_sections_present() -> None:
    d = build_dossier(
        _ctx(),
        facts=[{"memory_key": "job", "value": "photographer"}],
        episodes=[{"episode_type": "important_event", "summary": "soccer game"}],
        loops=[{"status": "open", "description": "ask about game"}],
        signals=[{"topic": "teasing", "behavior": "x", "polarity": "positive",
                  "evidence_count": 4}],
        intimate=[{"kind": "boundary", "signal": "stated boundary re: ex"}],
    )
    names = [s.name for s in d.sections]
    assert names == ["relationship", "person", "open_loops", "episodes",
                     "patterns", "intimate", "commerce", "recall"]
    assert any("warming" in line for line in d.sections[0].lines)
    assert any("job=photographer" in line for line in d.sections[1].lines)
    assert d.sections[6].lines == ["commerce unavailable (stale)"]


def test_lines_bounded() -> None:
    d = build_dossier(
        _ctx(), facts=[{"memory_key": "k", "value": "v" * 500}],
        episodes=[{"episode_type": "t", "summary": "s" * 500}],
    )
    for section in d.sections:
        for line in section.lines:
            assert len(line) <= 280, (section.name, line[:40])


def test_empty_stores_render() -> None:
    d = build_dossier(_ctx())
    assert len(d.sections) == 8
    assert all(isinstance(s.lines, list) for s in d.sections)


def test_deterministic() -> None:
    rows = {"facts": [{"memory_key": "job", "value": "photographer"}]}
    a = build_dossier(_ctx(), **rows)
    b = build_dossier(_ctx(), **rows)
    assert [s.lines for s in a.sections] == [s.lines for s in b.sections]


def test_provenance_required() -> None:
    with pytest.raises(ValueError):
        build_dossier(_ctx(), provenance="")


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "dossier.py"
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
