"""db.segments.py contract tests: surface + SQL-shape static checks + writes.

Live round-trip proven on staging (create/upsert, list filter, get,
partial update, toggle, duplicate, member count, last-evaluated, delete).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db.segments as sdb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO_ROOT / "db" / "segments.py").read_text(encoding="utf-8")

EXPECTED = [
    "create_segment",
    "delete_segment",
    "duplicate_segment",
    "get_pool",
    "get_segment",
    "list_segments",
    "toggle_segment",
    "update_last_evaluated",
    "update_member_count",
    "update_segment",
]


def test_all_10_functions_exist() -> None:
    assert len(EXPECTED) == 10
    for name in EXPECTED:
        assert callable(getattr(sdb, name, None)), name
        assert name in sdb.__all__, name


def test_all_async() -> None:
    for name in EXPECTED:
        if name == "get_pool":
            continue
        assert inspect.iscoroutinefunction(getattr(sdb, name)), name


def test_creator_first_signatures() -> None:
    for name in EXPECTED:
        if name == "get_pool":
            continue
        params = list(inspect.signature(getattr(sdb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_idempotency_substrings_verbatim() -> None:
    assert "ON CONFLICT (creator_id, LOWER(name)) DO UPDATE" in SRC
    assert "WHERE creator_id = $1 AND id = $2" in SRC
    assert "enabled = TRUE" in SRC
    assert "RETURNING" in SRC


def test_no_provider_imports() -> None:
    tree = ast.parse(SRC)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert not any(m.startswith("integrations.") for m in top_level)
    assert "db.fangate" not in top_level
    assert "db.dropfans" not in top_level


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


class TestWrites:
    @pytest.mark.asyncio
    async def test_update_segment_partial(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 1, "name": "N2"})
        with patch("db.segments.get_pool", return_value=_pool(conn)):
            got = await sdb.update_segment(1, 1, name="N2")
        assert got == {"id": 1, "name": "N2"}
        sql = conn.fetchrow.call_args[0][0]
        assert "name = $3" in sql
        assert "rules = $" not in sql

    @pytest.mark.asyncio
    async def test_update_segment_rules_jsonb(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 1})
        with patch("db.segments.get_pool", return_value=_pool(conn)):
            await sdb.update_segment(1, 1, rules={"type": "group"})
        sql, *params = conn.fetchrow.call_args[0]
        assert "::jsonb" in sql
        assert params[-1] == '{"type": "group"}'

    @pytest.mark.asyncio
    async def test_update_segment_empty_returns_none(self) -> None:
        with patch("db.segments.get_pool") as gp:
            assert await sdb.update_segment(1, 1) is None
        gp.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_copies_row(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 2, "name": "S (Copy)"})
        with patch("db.segments.get_pool", return_value=_pool(conn)):
            got = await sdb.duplicate_segment(1, 1, "S (Copy)")
        assert got["name"] == "S (Copy)"
        sql = conn.fetchrow.call_args[0][0]
        assert "SELECT creator_id, $3, description, rules, enabled" in sql

    @pytest.mark.asyncio
    async def test_member_count_and_evaluated(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch("db.segments.get_pool", return_value=_pool(conn)):
            assert await sdb.update_member_count(1, 1, 42) is True
            assert await sdb.update_last_evaluated(1, 1) is True
        assert "member_count = $3" in conn.execute.call_args_list[0][0][0]
        assert "last_evaluated_at = NOW()" in conn.execute.call_args_list[1][0][0]

    @pytest.mark.asyncio
    async def test_rules_str_decoded_on_read(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 1, "rules": '{"type": "group"}'})
        with patch("db.segments.get_pool", return_value=_pool(conn)):
            got = await sdb.get_segment(1, 1)
        assert got["rules"] == {"type": "group"}
