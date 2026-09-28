"""Phase 12 unit tests: retirement readiness + isolation evidence.

Removal itself needs separate approval; this phase proves the gates work
and that V2 is isolated from legacy. No deletions performed.
"""

from __future__ import annotations

import pathlib

import pytest

from relationship_v2.domain.retirement import RetirementReadiness
from relationship_v2.services.retirement import REMOVAL_STEPS, check_readiness, plan_removal

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _ready(**kw) -> RetirementReadiness:
    base: dict = {
        "v2_stable": True,
        "v2_primary": True,
        "legacy_writes_disabled": True,
        "legacy_reads_disabled": True,
        "commerce_independent": True,
        "rollback_tested": True,
        "approval_granted": True,
    }
    base.update(kw)
    return RetirementReadiness(**base)


def test_each_gate_blocks() -> None:
    for field in (
        "v2_stable",
        "v2_primary",
        "legacy_writes_disabled",
        "legacy_reads_disabled",
        "commerce_independent",
        "rollback_tested",
        "approval_granted",
    ):
        ok, blockers = check_readiness(_ready(**{field: False}))
        assert ok is False
        assert blockers, field


def test_all_gates_pass() -> None:
    ok, blockers = check_readiness(_ready())
    assert ok is True and blockers == []


def test_removal_blocked_without_approval() -> None:
    dec = plan_removal(_ready(approval_granted=False), "operator:7")
    assert dec.action == "blocked"
    assert "approval_missing" in dec.blockers
    assert dec.steps == []


def test_removal_plan_ordered_writes_before_reads() -> None:
    dec = plan_removal(_ready(), "operator:7")
    assert dec.action == "remove_per_plan"
    steps = dec.steps
    assert steps == list(REMOVAL_STEPS)
    assert steps.index("disable_legacy_writes") < steps.index("disable_legacy_reads")
    assert steps.index("disable_legacy_reads") < steps.index("remove_legacy_modules")
    assert "verify_commerce_intact" in steps
    assert "verify_rollback_intact" in steps


def test_removal_requires_actor() -> None:
    with pytest.raises(ValueError, match="actor"):
        plan_removal(_ready(), "")


def test_v2_isolated_no_legacy_imports() -> None:
    import ast

    legacy_prefixes = ("commerce", "memory", "context_engine")
    for f in (REPO_ROOT / "relationship_v2").rglob("*.py"):
        # Exempt: relationship_v2/integration/ is the sanctioned commerce
        # boundary (COMMERCE_CONTRACT + 10_LEGACY_ISOLATION allowed
        # dependency). Allow-listed there by test_relationship_v2_commerce_ports.
        if "integration" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                if mod.startswith("relationship_v2") or mod in ("db.postgres", "db", "db.redis"):
                    continue
                for prefix in legacy_prefixes:
                    assert not (mod == prefix or mod.startswith(prefix + ".")), f"{f}: {mod}"


def test_v1_quarantine_gates_present() -> None:
    router = (REPO_ROOT / "core" / "architecture_router.py").read_text(encoding="utf-8")
    assert 'V1_STATUS = "DISABLED"' in router
    for path in (
        "chatbotv2/handlers.py",
        "workers/llm_worker.py",
        "workers/send_worker.py",
    ):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert "is_v1_conversational_enabled" in text, path
