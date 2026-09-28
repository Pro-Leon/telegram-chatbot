"""Phase 11 unit tests: flagged, scoped, reversible activation.

Pure policy. Covers MIGRATION_AND_CUTOVER.md activation rules.
"""

from __future__ import annotations

import pathlib

import pytest

from relationship_v2.domain.activation import ActivationPreconditions, ActivationScope
from relationship_v2.services.activation import (
    activate,
    check_preconditions,
    deactivate,
    dependencies_ok,
    is_active_for_scope,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pre(**kw) -> ActivationPreconditions:
    base: dict = {
        "v1_disabled": True,
        "shadow_passed": True,
        "commerce_verified": True,
        "dependencies_healthy": True,
    }
    base.update(kw)
    return ActivationPreconditions(**base)


def test_disabled_flag_admits_nothing() -> None:
    scope = ActivationScope(enabled=False, allowed_creator_ids=(1, 2))
    assert is_active_for_scope(scope, 1) is False


def test_empty_allowlist_admits_nothing() -> None:
    scope = ActivationScope(enabled=True, allowed_creator_ids=())
    assert is_active_for_scope(scope, 1) is False


def test_unknown_creator_fail_closed() -> None:
    scope = ActivationScope(enabled=True, allowed_creator_ids=(1,))
    assert is_active_for_scope(scope, 2) is False
    assert is_active_for_scope(scope, 1) is True
    assert is_active_for_scope(scope, 0) is False


def test_v1_enabled_blocks_dual_authority() -> None:
    ok, blockers = check_preconditions(_pre(v1_disabled=False))
    assert ok is False
    assert "v1_still_enabled_dual_authority" in blockers


def test_shadow_and_commerce_gate() -> None:
    ok, blockers = check_preconditions(_pre(shadow_passed=False))
    assert ok is False and "shadow_not_passed" in blockers
    ok2, blockers2 = check_preconditions(_pre(commerce_verified=False))
    assert ok2 is False and "commerce_regressed" in blockers2


def test_health_gate_requires_both_layers() -> None:
    assert dependencies_ok(True, True) is True
    assert dependencies_ok(True, False) is False
    assert dependencies_ok(False, True) is False


def test_activate_audited_and_reversible() -> None:
    scope = ActivationScope(enabled=True, allowed_creator_ids=(1,))
    dec = activate(scope, _pre(), "operator:7")
    assert dec.action == "activate"
    assert dec.actor == "operator:7"
    off = deactivate("operator:7")
    assert off.action == "deactivate"
    assert "data_preserved" in off.reasons[0]


def test_activate_blocked_raises_with_reasons() -> None:
    scope = ActivationScope(enabled=True, allowed_creator_ids=(1,))
    with pytest.raises(ValueError, match="v1_still_enabled"):
        activate(scope, _pre(v1_disabled=False), "operator:7")
    with pytest.raises(ValueError, match="allowlist"):
        activate(ActivationScope(enabled=True), _pre(), "operator:7")
    with pytest.raises(ValueError, match="actor"):
        activate(scope, _pre(), "")


def test_phase11_no_config_router_or_commerce_imports() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "activation.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2"):
                continue
            assert mod != "core.config", mod
            assert mod != "core.architecture_router", mod
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
