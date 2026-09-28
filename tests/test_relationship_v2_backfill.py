"""Stage F8 tests: legacy flattening and dry-run/apply backfill.

No live DB: persistence faked. Proves: namespaced profile dicts flatten,
knowledge items flatten, dry-run persists nothing, apply persists accepted
only with dedupe, scope fail-closed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from relationship_v2.services.backfill import (
    flatten_knowledge_items,
    flatten_profile_facts,
    run_backfill,
)
from relationship_v2.services.migration_validator import LegacyFact


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_flatten_profile_namespaces() -> None:
    facts = {
        "occupation": "architect",
        "_confidence": {"occupation": 0.9},
        "by_creator": {"city": "Chicago", "_x": 1},
        "count": 3,
        "empty": "   ",
    }
    out = flatten_profile_facts(facts)
    keys = {f.key for f in out}
    assert "occupation" in keys
    assert "by_creator.city" in keys
    assert "count" in keys
    assert not any(k.startswith("_") or "empty" in k for k in keys)
    assert all(f.source_table == "user_profiles" for f in out)


def test_flatten_knowledge_items() -> None:
    items = [
        {"subject": "occupation", "value": "nurse", "category": "WORK"},
        {"subject": "", "value": "x"},
        {"subject": "city", "value": ""},
        "not-a-dict",
    ]
    out = flatten_knowledge_items(items)
    assert len(out) == 1
    assert out[0].key == "occupation" and out[0].category == "WORK"


def test_dry_run_persists_nothing() -> None:
    async def _never(*a, **k):
        raise AssertionError("no writes in dry-run")

    entries = flatten_profile_facts({"occupation": "architect", "price": "$5"})
    rep = _run(
        run_backfill(entries, 1, 2, uuid4(), create_fact=_never)
    )
    assert rep.dry_run is True
    assert rep.scanned == 2 and rep.accepted == 1 and rep.rejected == 1
    assert rep.persisted == 0


def test_apply_persists_accepted_deduped() -> None:
    persisted: list[tuple] = []

    async def _create(c, u, rel, cat, key, val, conf, **k):
        persisted.append((key, val, cat, conf, k.get("provenance")))
        return {"id": uuid4()}

    entries = [
        LegacyFact(source_table="t", key="job", value="Architect"),
        LegacyFact(source_table="t", key="job", value="architect "),
        LegacyFact(source_table="t", key="price", value="$5"),
    ]
    rep = _run(
        run_backfill(entries, 1, 2, uuid4(), dry_run=False, create_fact=_create)
    )
    assert rep.persisted == 1
    key, _val, _cat, conf, prov = persisted[0]
    assert key == "job" and conf == 0.5
    assert prov is not None and "backfill" in prov


def test_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        _run(run_backfill([], 0, 2, uuid4(), dry_run=True))


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "backfill.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any(m.startswith(("chatbotv2.", "workers.", "memory.")) for m in mods)
    # Repository binds lazily inside the default port only (fake-injectable).
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert "relationship_v2.persistence.repository" not in top_level
