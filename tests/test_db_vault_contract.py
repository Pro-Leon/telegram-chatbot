"""db.vault.py contract tests: surface + SQL-shape static checks + reads.

Live round-trip proven on staging (reserve/finalize/release/reaper,
record/has, dropfans finalize/release, analytics). These pin the
suite-asserted substrings (m7 scoped-SQL, h4 reaper params, phase10
UNIQUE).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db.vault as vdb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO_ROOT / "db" / "vault.py").read_text(encoding="utf-8")

EXPECTED = [
    "count_deliveries",
    "finalize_delivery",
    "finalize_dropfans_delivery",
    "get_delivery",
    "get_delivery_stats",
    "get_delivered_media_map",
    "get_fan_delivery_count",
    "get_fan_delivery_history",
    "get_media_delivery_counts",
    "get_pool",
    "get_product_delivery_counts",
    "get_recent_deliveries",
    "get_top_fans",
    "get_unseen_media_ids",
    "has_user_received_media",
    "list_deliveries",
    "record_delivery",
    "release_delivery",
    "release_dropfans_delivery",
    "release_stale_reservations",
    "reserve_delivery",
]

# Fns whose first arg is intentionally not creator_id: id-keyed mutation
# by reservation id, and the global cross-creator stale reaper.
NON_CREATOR_FIRST = {
    "get_pool",
    "finalize_delivery",
    "release_delivery",
    "release_stale_reservations",
}


def test_all_21_functions_exist() -> None:
    assert len(EXPECTED) == 21
    for name in EXPECTED:
        assert callable(getattr(vdb, name, None)), name
        assert name in vdb.__all__, name


def test_all_async() -> None:
    for name in EXPECTED:
        if name == "get_pool":
            continue
        assert inspect.iscoroutinefunction(getattr(vdb, name)), name


def test_creator_first_signatures() -> None:
    for name in EXPECTED:
        if name in NON_CREATOR_FIRST:
            continue
        params = list(inspect.signature(getattr(vdb, name)).parameters)
        assert params[0] == "creator_id", (name, params)


def test_idempotency_substrings_verbatim() -> None:
    assert "ON CONFLICT (creator_id, user_id, fangate_media_id) DO NOTHING" in SRC
    assert "UNIQUE(creator_id, user_id, fangate_media_id)" in SRC
    assert "CAST($1 AS numeric) * INTERVAL '1 minute'" in SRC
    assert "NOT (id = ANY" in SRC
    assert "creator_id = $3" in SRC
    assert "creator_id = $2" in SRC
    assert "status = 'pending'" in SRC
    assert "make_interval" not in SRC


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


# ── Mock-backed behavior for fns without dedicated suites ──────────────


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


class TestRecordAndReads:
    @pytest.mark.asyncio
    async def test_record_delivery_inserted(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": 7})
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            assert await vdb.record_delivery(1, 2, 777, 101, 555) is True
        sql = conn.fetchrow.call_args[0][0]
        assert "'sent'" in sql

    @pytest.mark.asyncio
    async def test_record_delivery_conflict(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            assert await vdb.record_delivery(1, 2, 777) is False

    @pytest.mark.asyncio
    async def test_get_delivery_hit_and_miss(self) -> None:
        row = {"id": 7, "fangate_media_id": 777, "status": "sent"}
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=row)
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            got = await vdb.get_delivery(1, 2, 777)
        assert got == row

        conn2 = AsyncMock()
        conn2.fetchrow = AsyncMock(return_value=None)
        with patch("db.vault.get_pool", return_value=_pool(conn2)):
            assert await vdb.get_delivery(1, 2, 778) is None

    @pytest.mark.asyncio
    async def test_delivered_media_map(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                {"fangate_media_id": 201, "status": "sent", "sent_at": "t"},
                {"fangate_media_id": 202, "status": "sent", "sent_at": "t"},
            ]
        )
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            m = await vdb.get_delivered_media_map(1, 2)
        assert set(m.keys()) == {201, 202}
        assert m[201]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_unseen_media_ids(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"fangate_media_id": 201}])
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            unseen = await vdb.get_unseen_media_ids(1, 2, [201, 202, 203])
        assert unseen == [202, 203]

    @pytest.mark.asyncio
    async def test_unseen_empty_short_circuits(self) -> None:
        with patch("db.vault.get_pool") as gp:
            assert await vdb.get_unseen_media_ids(1, 2, []) == []
        gp.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_and_count_scoped(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            items = await vdb.list_deliveries(1, user_id=2, limit=10, offset=0)
        assert items == [{"id": 1}, {"id": 2}]
        sql = conn.fetch.call_args[0][0]
        assert "user_id = $2" in sql

        conn2 = AsyncMock()
        conn2.fetchrow = AsyncMock(return_value={"cnt": 3})
        with patch("db.vault.get_pool", return_value=_pool(conn2)):
            assert await vdb.count_deliveries(1, user_id=2) == 3


class TestDropfansVariants:
    @pytest.mark.asyncio
    async def test_finalize_dropfans(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            assert await vdb.finalize_dropfans_delivery(1, 2, "V1", 999) is True
        sql = conn.execute.call_args[0][0]
        assert "dropfans_vault_item_id = $3" in sql
        assert "status = 'pending'" in sql

        conn2 = AsyncMock()
        conn2.execute = AsyncMock(return_value="UPDATE 0")
        with patch("db.vault.get_pool", return_value=_pool(conn2)):
            assert await vdb.finalize_dropfans_delivery(1, 2, "V1") is False

    @pytest.mark.asyncio
    async def test_release_dropfans(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="DELETE 1")
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            assert await vdb.release_dropfans_delivery(1, 2, "V1") is True

        conn2 = AsyncMock()
        conn2.execute = AsyncMock(return_value="DELETE 0")
        with patch("db.vault.get_pool", return_value=_pool(conn2)):
            assert await vdb.release_dropfans_delivery(1, 2, "V1") is False


class TestReaperShape:
    @pytest.mark.asyncio
    async def test_no_skip_ids_two_params(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        with patch("db.vault.get_pool", return_value=_pool(conn)):
            assert await vdb.release_stale_reservations() == []
        sql, *params = conn.fetch.call_args[0]
        assert "NOT (id = ANY" not in sql
        assert params[0] == 5
        assert params[1] == 50
