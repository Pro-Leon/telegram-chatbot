"""Stage B3 tests: episode planning from extraction candidates.

Pure classification, no infra. Proves: life events flag follow-up, facts
become discovery episodes, signals are not events, summaries bounded.
"""

from __future__ import annotations

from relationship_v2.domain.memory import MemoryEpisodeType
from relationship_v2.services.episode_manager import plan_candidates, plan_episode
from relationship_v2.services.memory_extraction import extract_candidates


def _cands(text: str):
    return extract_candidates(text, source_event_id="ev-1", provenance="test")


def test_soccer_game_flags_follow_up() -> None:
    cands = _cands("My daughter has a soccer game Saturday.")
    plans = plan_candidates(cands)
    life = [p for p in plans if p.follow_up_candidate]
    assert life, "life event must flag follow-up"
    assert life[0].episode_type == MemoryEpisodeType.IMPORTANT_EVENT
    assert life[0].follow_up_hint is not None
    assert life[0].salience >= 0.5


def test_occupation_fact_becomes_discovery() -> None:
    cands = _cands("I work as a photographer.")
    plans = plan_candidates(cands)
    assert plans
    assert plans[0].episode_type == MemoryEpisodeType.PERSONAL_DISCOVERY
    assert plans[0].follow_up_candidate is False


def test_birthday_flags_follow_up() -> None:
    cands = _cands("My birthday is June 4th.")
    plans = plan_candidates(cands)
    assert any(p.follow_up_candidate for p in plans)


def test_signals_are_not_events() -> None:
    cands = _cands("I love football and music at work.")
    signals = [c for c in cands if c.kind.value == "signal"]
    assert signals
    for s in signals:
        assert plan_episode(s) is None


def test_summary_bounded_and_salience_banded() -> None:
    cands = _cands("I work as a photographer.")
    plans = plan_candidates(cands)
    for p in plans:
        assert len(p.summary) <= 280
        assert 0.0 <= p.salience <= 1.0


def test_empty_value_returns_none() -> None:
    cands = _cands("   ")
    assert plan_candidates(cands) == []


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "episode_manager.py"
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
