"""db.dropfans.py contract tests: surface + SQL-shape static checks.

Live round-trip proven on staging (sync, CUID resolution, sales dedupe,
selection config, vault index). These pin the suite-asserted substrings.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import db.dropfans as ddb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO_ROOT / "db" / "dropfans.py").read_text(encoding="utf-8")

EXPECTED = [
    "count_active_dropfans_products",
    "count_recorded_sales",
    "find_dropfans_product",
    "find_synthetic_product",
    "get_dropfans_integration",
    "get_pool",
    "get_selection_config",
    "has_dropfans_sale_been_recorded",
    "list_active_dropfans_creator_ids",
    "list_active_dropfans_products",
    "list_recorded_sales",
    "list_vault_index",
    "record_dropfans_sale",
    "resolve_dropfans_cuid",
    "set_selection_config",
    "sum_recorded_sales_cents",
    "sync_vault_index",
    "upsert_dropfans_integration",
    "upsert_dropfans_product",
    "upsert_vault_index",
]


def test_all_20_functions_exist() -> None:
    assert len(EXPECTED) == 20
    for name in EXPECTED:
        assert callable(getattr(ddb, name, None)), name
        assert name in ddb.__all__, name


def test_all_async() -> None:
    for name in EXPECTED:
        if name == "get_pool":
            continue
        assert inspect.iscoroutinefunction(getattr(ddb, name)), name


def test_creator_first_signatures() -> None:
    for name in EXPECTED:
        if name in ("get_pool", "list_active_dropfans_creator_ids"):
            continue
        params = list(inspect.signature(getattr(ddb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_idempotency_substrings_verbatim() -> None:
    assert "ON CONFLICT (creator_id, dropfans_product_id)" in SRC
    assert "ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING" in SRC
    assert "ON CONFLICT (creator_id, vault_item_id) DO UPDATE" in SRC
    assert "DELETE FROM dropfans_vault_index" in SRC
    assert "status = 'active' AND dropfans_creator_id IS NOT NULL" in SRC


def test_no_provider_imports() -> None:
    tree = ast.parse(SRC)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    # sync_vault_index lazily imports the provider lister inside the
    # function (dashboard suite patches it there); top level stays clean.
    assert not any(m.startswith("integrations.") for m in top_level)
    assert "db.fangate" not in top_level
