"""Phase 3a contract tests: offer_definitions + drop_intents surface + DDL.

Live round-trips proven on staging separately; these pin suite-asserted
signatures, SQL substrings, and migration texts without live PG.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import db.drop_intents as di
import db.offer_definitions as odb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ODB_SRC = (REPO_ROOT / "db" / "offer_definitions.py").read_text(encoding="utf-8")
DI_SRC = (REPO_ROOT / "db" / "drop_intents.py").read_text(encoding="utf-8")


def test_offer_types_frozen() -> None:
    assert odb.OFFER_TYPES == frozenset({"SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"})


def test_offer_definitions_surface() -> None:
    expected = [
        "activate_offer_definition", "create_offer_definition",
        "get_offer_definition", "get_offer_definition_by_key",
        "list_offer_definition_drops", "list_offer_definitions",
        "map_offer_definition_drop", "retire_offer_definition",
    ]
    for name in expected:
        assert inspect.iscoroutinefunction(getattr(odb, name)), name
        assert name in odb.__all__, name
    sig = inspect.signature(odb.create_offer_definition)
    params = list(sig.parameters)
    assert params[:7] == ["creator_id", "stable_key", "offer_type",
                          "vault_item_ids", "price_minor", "currency",
                          "allow_download"]
    assert sig.parameters["version"].default == 1
    assert sig.parameters["status"].default == "draft"


def test_offer_sql_shapes() -> None:
    import ast as _ast

    assert "INSERT INTO commerce_offer_definitions" in ODB_SRC
    assert "INSERT INTO commerce_offer_definition_drops" in ODB_SRC
    assert "SET status = 'retired'" in ODB_SRC
    assert "creator_id = $1" in ODB_SRC
    assert "version = $3" in ODB_SRC
    tree = _ast.parse(ODB_SRC)
    body = tree.body
    if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant):
        body = body[1:]
    code = _ast.unparse(_ast.Module(body=body, type_ignores=[]))
    for banned in ("fangate_products", "backfill", "auto_seed"):
        assert banned not in code, banned


def test_offer_migration_text() -> None:
    mig = (REPO_ROOT / "db" / "migrations"
           / "20260917010000_p33_offer_definitions.sql").read_text(encoding="utf-8")
    assert mig.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "UNIQUE (creator_id, stable_key, version)" in mig
    assert "PRIMARY KEY (creator_id, dropfans_product_id)" in mig
    assert "cardinality(canonical_vault_item_ids) BETWEEN 1 AND 10" in mig
    assert "price_minor INTEGER NOT NULL CHECK (price_minor >= 0)" in mig
    assert "'SINGLE', 'SMALL_BUNDLE', 'CORE_BUNDLE', 'PREMIUM'" in mig
    assert "FOREIGN KEY (family_id, creator_id)" in mig
    assert "REFERENCES commerce_content_families (id, creator_id)" in mig


def test_families_migration_text() -> None:
    mig = (REPO_ROOT / "db" / "migrations"
           / "20260917000000_p33_content_families.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS commerce_content_families" in mig
    assert "UNIQUE (creator_id, slug)" in mig
    assert "PRIMARY KEY (family_id, vault_item_id)" in mig
    assert "REFERENCES commerce_content_families (id, creator_id)" in mig


def test_intents_surface() -> None:
    for name in ("get_or_create_intent", "get_intent", "rearm_failed_intent",
                 "mark_intent_failed", "mark_intent_active",
                 "note_intent_provider_cuid"):
        assert inspect.iscoroutinefunction(getattr(di, name)), name
        assert name in di.__all__, name
    sig = inspect.signature(di.get_or_create_intent)
    params = list(sig.parameters)
    assert params[0] == "creator_id" and params[1] == "content_key"


def test_intents_sql_shapes() -> None:
    assert "ON CONFLICT (creator_id, content_key) DO NOTHING" in DI_SRC
    assert "AND status = 'failed'" in DI_SRC
    assert "AND status = 'pending'" in DI_SRC


def test_intents_migration_text() -> None:
    mig = (REPO_ROOT / "db" / "migrations"
           / "20260916000000_p32_safety_foundation.sql").read_text(encoding="utf-8")
    assert "dropfans_drop_intents" in mig
    assert "content_key" in mig
    assert "UNIQUE (creator_id, content_key)" in mig
    for state in ("'pending'", "'active'", "'failed'"):
        assert state in mig


def test_no_provider_imports() -> None:
    for rel in ("db/offer_definitions.py", "db/drop_intents.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        assert not any(m.startswith("integrations.") for m in mods)
        assert "commerce.vault_sets" not in mods
