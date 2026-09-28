"""db.automation.py + db.families.py contract tests: surface + SQL shape.

Live round-trips proven on staging (operation lifecycle + claim race,
family CRUD + membership). These pin the suite-asserted substrings.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db.automation as adb
import db.families as fdb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTO_SRC = (REPO_ROOT / "db" / "automation.py").read_text(encoding="utf-8")
FAM_SRC = (REPO_ROOT / "db" / "families.py").read_text(encoding="utf-8")

AUTO_EXPECTED = [
    "cancel_operation",
    "claim_operation",
    "create_operation",
    "get_operation",
    "get_operation_by_idempotency_key",
    "get_pool",
    "list_operations",
    "mark_cancelled",
    "mark_failed",
    "mark_retrying",
    "mark_succeeded",
    "mark_unknown",
    "transition_operation",
]

# Id-keyed fns take operation_id first (pinned by persistence tests).
AUTO_NON_CREATOR_FIRST = {
    "get_pool",
    "get_operation",
    "claim_operation",
    "mark_succeeded",
    "mark_failed",
    "mark_retrying",
    "mark_unknown",
    "mark_cancelled",
    "transition_operation",
    "cancel_operation",
}

FAM_EXPECTED = [
    "add_content_family_member",
    "create_content_family",
    "get_content_family",
    "get_families_for_vault_item",
    "get_pool",
    "list_content_families",
    "list_family_members",
    "remove_content_family_member",
]


def test_automation_all_13_exist() -> None:
    assert len(AUTO_EXPECTED) == 13
    for name in AUTO_EXPECTED:
        assert callable(getattr(adb, name, None)), name
        assert name in adb.__all__, name


def test_automation_all_async() -> None:
    for name in AUTO_EXPECTED:
        if name == "get_pool":
            continue
        assert inspect.iscoroutinefunction(getattr(adb, name)), name


def test_automation_creator_first() -> None:
    for name in AUTO_EXPECTED:
        if name in AUTO_NON_CREATOR_FIRST:
            continue
        params = list(inspect.signature(getattr(adb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_automation_substrings() -> None:
    assert "ON CONFLICT (creator_id, idempotency_key)" in AUTO_SRC
    assert "WHERE idempotency_key <> ''" in AUTO_SRC
    assert "attempt_count = attempt_count + 1" in AUTO_SRC
    assert "AND status IN ('pending', 'retrying')" in AUTO_SRC
    assert "finished_at = NOW()" in AUTO_SRC
    assert "fangate" not in AUTO_SRC.lower()
    assert "dropfans" not in AUTO_SRC
    assert "DropFans" not in AUTO_SRC


def test_automation_no_provider_imports() -> None:
    tree = ast.parse(AUTO_SRC)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert not any(m.startswith("integrations.") for m in top_level)
    assert not any(m.startswith("automation.") for m in top_level)


def test_families_all_8_exist() -> None:
    assert len(FAM_EXPECTED) == 8
    for name in FAM_EXPECTED:
        assert callable(getattr(fdb, name, None)), name
        assert name in fdb.__all__, name


def test_families_all_async() -> None:
    for name in FAM_EXPECTED:
        if name == "get_pool":
            continue
        assert inspect.iscoroutinefunction(getattr(fdb, name)), name


def test_families_creator_first() -> None:
    for name in FAM_EXPECTED:
        if name == "get_pool":
            continue
        params = list(inspect.signature(getattr(fdb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_families_substrings() -> None:
    assert "INSERT INTO commerce_content_families" in FAM_SRC
    assert "INSERT INTO commerce_content_family_members" in FAM_SRC
    assert "DELETE FROM commerce_content_family_members" in FAM_SRC
    assert "is_primary" not in FAM_SRC


class _FakePoolConnCM:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *a: object) -> None:
        pass


def _pool(conn: Any) -> Any:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolConnCM(conn))
    return pool


class TestAutomationTransitions:
    @pytest.mark.asyncio
    async def test_mark_cancelled_kill_switch(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch("db.automation.get_pool", return_value=_pool(conn)):
            assert await adb.mark_cancelled(1, 1) is True
        sql, *params = conn.execute.call_args[0]
        assert "status = $3" in sql and params[2] == "cancelled"
        assert "'running'" in sql  # cancellable from running too

    @pytest.mark.asyncio
    async def test_transition_valid_target_hits_db(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch("db.automation.get_pool", return_value=_pool(conn)):
            assert await adb.transition_operation(1, 1, "failed") is True

    @pytest.mark.asyncio
    async def test_transition_invalid_target_no_io(self) -> None:
        with patch("db.automation.get_pool") as gp:
            assert await adb.transition_operation(1, 1, "bogus") is False
        gp.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_key_lookup_short_circuits(self) -> None:
        with patch("db.automation.get_pool") as gp:
            assert await adb.get_operation_by_idempotency_key(1, "") is None
        gp.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_insert_param_order(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 5})
        with patch("db.automation.get_pool", return_value=_pool(conn)):
            got = await adb.create_operation(creator_id=1, action="a", target="t", max_attempts=5)
        assert got == {"id": 5}
        sql, *params = conn.fetchrow.call_args[0]
        assert sql.startswith("INSERT")
        assert params[6] == 5


class TestFamiliesReads:
    @pytest.mark.asyncio
    async def test_list_members(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"vault_item_id": "V1"}])
        with patch("db.families.get_pool", return_value=_pool(conn)):
            assert await fdb.list_family_members(1, 7) == [{"vault_item_id": "V1"}]

    @pytest.mark.asyncio
    async def test_remove_missing_returns_false(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 7})
        conn.execute = AsyncMock(return_value="DELETE 0")
        with patch("db.families.get_pool", return_value=_pool(conn)):
            assert await fdb.remove_content_family_member(1, 7, "VX") is False
