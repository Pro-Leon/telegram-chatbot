"""Stage D3 tests: package entrypoint + flag-gated worker observation.

No live DB/commerce: observation uses a fake assembler; flag off is the
default. Proves: disabled -> None without I/O, enabled -> metadata only,
failures -> None, get_context lazy with no import-time service cost.
"""

from __future__ import annotations

import ast
import pathlib

import relationship_v2
from relationship_v2.worker_hook import observe_turn_context

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_package_entrypoint_lazy() -> None:
    assert relationship_v2.V2_STATUS == "STAGE_D3_SHADOW_OBSERVE"
    assert relationship_v2.V1_DEPENDENCY is False
    assert "get_context" in relationship_v2.__all__
    tree = ast.parse((REPO_ROOT / "relationship_v2" / "__init__.py").read_text(
        encoding="utf-8"
    ))
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert not any(m.startswith("relationship_v2.services") for m in top_level)
    assert hasattr(relationship_v2, "get_context")


def test_disabled_returns_none_without_io() -> None:
    async def _never(*a, **k):
        raise AssertionError("no I/O while flag is off")

    obs = _run(
        observe_turn_context(1, 2, "gen-1", assemble=_never),
    )
    assert obs is None


def test_enabled_returns_metadata_only(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_READ_ENABLED", "true")

    async def _fake(*a, **k):
        from types import SimpleNamespace

        return SimpleNamespace(
            assembled=SimpleNamespace(total_chars=1234, sections=[1, 2, 3]),
            recall_refs=["loop:x"],
            commerce_unavailable=False,
        )

    obs = _run(observe_turn_context(1, 2, "gen-2", assemble=_fake))
    assert obs is not None
    assert obs.total_chars == 1234
    assert obs.section_count == 3
    assert obs.recall_count == 1
    assert obs.commerce_ok is True
    assert obs.elapsed_ms >= 0.0


def test_failure_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_READ_ENABLED", "true")

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    assert _run(observe_turn_context(1, 2, "gen-3", assemble=_boom)) is None


def test_scope_guarded(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_READ_ENABLED", "true")

    async def _never(*a, **k):
        raise AssertionError("no I/O on bad scope")

    assert _run(observe_turn_context(0, 2, "gen-4", assemble=_never)) is None


def test_worker_hook_present_and_guarded() -> None:
    src = (REPO_ROOT / "workers" / "llm_worker.py").read_text(encoding="utf-8")
    assert "observe_turn_context as _observe_v2_turn" in src
    assert "_v2_turn_observation" in src
    # Hook observes only: no reply/send/state influence.
    hook = src.split("_observe_v2_turn", 1)[1].split("except Exception:", 1)[0]
    for verb in ("send", "enqueue", "approve", "generation_completed"):
        assert verb not in hook, verb


def test_no_provider_or_realtime_imports() -> None:
    path = REPO_ROOT / "relationship_v2" / "worker_hook.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any("ws_manager" in m or "event_subscriber" in m for m in mods)
