"""Stage D1 tests: commerce read-port contract shapes + boundary hygiene.

No live PostgreSQL/providers: scope guards verified directly; shapes and
hygiene verified statically. Live read verification belongs to Stage F
shadow (real DB, real commerce tables).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from relationship_v2.integration.commerce_ports import (
    eligibility_port,
    opportunity_port,
    purchase_port,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PORTS = REPO_ROOT / "relationship_v2" / "integration" / "commerce_ports.py"


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_scope_fail_closed_without_io() -> None:
    for port in (eligibility_port, opportunity_port, purchase_port):
        with pytest.raises(ValueError):
            _run(port(0, 2))
        with pytest.raises(ValueError):
            _run(port(1, -1))


def test_contract_shapes_present() -> None:
    src = PORTS.read_text(encoding="utf-8")
    for key in (
        '"eligible"',
        '"reason"',
        '"active_offer"',
        '"opportunities"',
        '"cooldowns"',
        '"status"',
        '"count"',
        '"last_at"',
        '"owned_refs"',
        '"aftercare"',
    ):
        assert key in src, key


def test_read_only_no_execution_surface() -> None:
    tree = ast.parse(PORTS.read_text(encoding="utf-8"))
    mods: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for mod in mods:
        assert "provider" not in mod and "dropfans" not in mod and "fangate" not in mod
    for forbidden in ("execute_ppv", "mark_aftercare_pending", "deliver_product_media"):
        assert forbidden not in names, forbidden


def test_only_preserved_commerce_imports() -> None:
    tree = ast.parse(PORTS.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    allowed = {
        "db.postgres",
        "commerce.dao",
        "commerce.fan_commercial_state",
        "commerce.offer_history",
        "commerce.opportunity_engine",
    }
    for mod in mods:
        assert mod in allowed or mod in {
            "__future__",
            "logging",
            "datetime",
            "typing",
        }, mod
    for legacy in (
        "commerce.relationship",
        "commerce.relationship_trajectory",
        "commerce.intimacy_trajectory",
        "commerce.long_term_memory",
        "commerce.fan_knowledge",
        "memory.profile",
        "memory.summarizer",
        "context_engine",
    ):
        assert legacy not in mods, legacy
