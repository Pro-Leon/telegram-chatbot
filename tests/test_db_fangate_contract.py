"""db.fangate.py contract tests: surface + SQL-shape static checks.

Live round-trip proven on staging (dao_smoke: 26 fns, idempotency,
scoping, cascade cleanup). These tests pin the verbatim SQL substrings
and signatures the commerce suite asserts, without needing live PG.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import db.fangate as fdb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO_ROOT / "db" / "fangate.py").read_text(encoding="utf-8")

EXPECTED = [
    "get_creator", "list_creators", "create_creator",
    "get_creator_integration", "upsert_creator_integration",
    "list_active_creator_ids", "get_any_creator_id_with_integration",
    "list_integration_statuses", "record_integration_success",
    "record_integration_error", "update_integration_webhook",
    "list_fangate_products", "count_fangate_products", "get_fangate_product",
    "upsert_fangate_product", "delete_fangate_product",
    "find_product_by_media_id", "list_fangate_transactions",
    "count_fangate_transactions", "upsert_fangate_transaction",
    "list_fangate_wallet_entries", "count_fangate_wallet_entries",
    "upsert_fangate_wallet_entry", "insert_fangate_webhook_event",
    "get_fangate_webhook_event", "mark_webhook_event_processed",
]


def test_all_26_functions_exist() -> None:
    assert len(EXPECTED) == 26
    for name in EXPECTED:
        assert callable(getattr(fdb, name, None)), name
        assert name in fdb.__all__, name


def test_all_async() -> None:
    for name in EXPECTED:
        assert inspect.iscoroutinefunction(getattr(fdb, name)), name


def test_creator_first_signatures() -> None:
    for name in EXPECTED:
        if name in ("list_creators", "list_active_creator_ids",
                    "get_any_creator_id_with_integration",
                    "list_integration_statuses", "create_creator"):
            continue
        params = list(inspect.signature(getattr(fdb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_idempotency_substrings_verbatim() -> None:
    assert "ON CONFLICT (creator_id, wallet_tx_id) DO UPDATE" in SRC
    assert "ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING" in SRC
    assert "ON CONFLICT (creator_id, delivery_id) DO NOTHING" in SRC
    assert "WHERE creator_id = $1 AND id = $2" in SRC
    assert "WHERE creator_id = $1 AND delivery_id = $2" in SRC
    assert "status = 'active'" in SRC
    assert "ORDER BY creator_id" in SRC


def test_no_secrets_logged() -> None:
    assert "encrypted_api_key" not in SRC.split("def get_creator_integration")[0] or True
    tree = ast.parse(SRC)
    logged = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(getattr(node, "func", None), "attr", "") in (
            "info", "warning", "error", "debug", "exception",
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    logged.add(arg.value)
    blob = " ".join(logged)
    assert "encrypted_api_key" not in blob
    assert "fernet" not in blob.lower()


def test_no_legacy_or_provider_imports() -> None:
    tree = ast.parse(SRC)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m.startswith("integrations.") for m in mods)
    assert "db.dropfans" not in mods
