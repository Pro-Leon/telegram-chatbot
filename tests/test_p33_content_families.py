"""P3.3.3 — ContentFamily foundation tests (mocked DB, no live data).

Covers ``db.families`` creator isolation, membership uniqueness, and
multi-family semantics, plus migration structural checks and the
no-commercial-callers / no-is_primary / no-taxonomy-inference contracts.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "db"
    / "migrations"
    / "20260917000000_p33_content_families.sql"
)


def _pool(fetchrow=None, fetch=None, execute=None):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow)
    mock_conn.fetch = AsyncMock(return_value=fetch or [])
    if execute is not None:
        mock_conn.execute = AsyncMock(return_value=execute)
    else:
        mock_conn.execute = AsyncMock(return_value="DELETE 0")
    mock_pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            return False

    mock_pool.acquire = MagicMock(return_value=_Acquire())
    return mock_pool, mock_conn


def _family_row(fid=7, creator=1, slug="black-lingerie-01", label="Black Lingerie Set"):
    return {
        "id": fid, "creator_id": creator, "slug": slug, "label": label,
        "created_at": None, "updated_at": None,
    }


class TestFamilyCrud:
    @pytest.mark.asyncio
    async def test_create_family(self, monkeypatch):
        from db import families as fdb

        pool, conn = _pool(fetchrow=_family_row())
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        row = await fdb.create_content_family(1, "black-lingerie-01", "Black Lingerie Set")
        assert row["slug"] == "black-lingerie-01"
        sql = conn.fetchrow.call_args[0][0]
        assert "INSERT INTO commerce_content_families" in sql

    @pytest.mark.asyncio
    async def test_creator_scoped_slug_uniqueness(self, monkeypatch):
        """Duplicate slug for the same creator surfaces a DB uniqueness error."""
        import asyncpg

        from db import families as fdb

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            side_effect=asyncpg.exceptions.UniqueViolationError("duplicate key value")
        )
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await fdb.create_content_family(1, "set-01", "Set")

    @pytest.mark.asyncio
    async def test_same_slug_across_creators_allowed_by_contract(self, monkeypatch):
        """UNIQUE(creator_id, slug) permits the same slug per creator."""
        from db import families as fdb

        pool, conn = _pool(fetchrow=_family_row(creator=2, slug="set-01", label="Set"))
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        row = await fdb.create_content_family(2, "set-01", "Set")
        assert row["creator_id"] == 2 and row["slug"] == "set-01"
        args = conn.fetchrow.call_args[0][1:]
        assert args[0] == 2  # creator first

    def test_migration_enforces_creator_scoped_slug(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "UNIQUE (creator_id, slug)" in sql
        assert "UNIQUE (slug)" not in sql.replace("UNIQUE (creator_id, slug)", "")


class TestMembership:
    @pytest.mark.asyncio
    async def test_add_member(self, monkeypatch):
        from db import families as fdb

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": 7},  # family ownership check
                {"family_id": 7, "creator_id": 1, "vault_item_id": "V1", "created_at": None},
            ]
        )
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=mock_pool))
        row = await fdb.add_content_family_member(1, 7, "V1")
        assert row["vault_item_id"] == "V1"
        insert_sql = mock_conn.fetchrow.call_args_list[1][0][0]
        assert "INSERT INTO commerce_content_family_members" in insert_sql

    @pytest.mark.asyncio
    async def test_duplicate_membership_rejected(self, monkeypatch):
        import asyncpg

        from db import families as fdb

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": 7},
                asyncpg.exceptions.UniqueViolationError("duplicate key value"),
            ]
        )
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await fdb.add_content_family_member(1, 7, "V1")

    @pytest.mark.asyncio
    async def test_multiple_families_per_item(self, monkeypatch):
        from db import families as fdb

        pool, _ = _pool(
            fetch=[
                {"id": 1, "creator_id": 1, "slug": "a", "label": "A",
                 "created_at": None, "updated_at": None},
                {"id": 2, "creator_id": 1, "slug": "b", "label": "B",
                 "created_at": None, "updated_at": None},
            ]
        )
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        rows = await fdb.get_families_for_vault_item(1, "V1")
        assert {r["slug"] for r in rows} == {"a", "b"}

    def test_migration_allows_multi_family_membership(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "PRIMARY KEY (family_id, vault_item_id)" in sql
        assert "UNIQUE (vault_item_id)" not in sql

    @pytest.mark.asyncio
    async def test_remove_member_keeps_family(self, monkeypatch):
        from db import families as fdb

        pool, conn = _pool(
            fetchrow={"id": 7},
            execute="DELETE 1",
        )
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        assert await fdb.remove_content_family_member(1, 7, "V1") is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM commerce_content_family_members" in sql
        assert "commerce_content_families" not in sql  # family itself untouched

    @pytest.mark.asyncio
    async def test_empty_family_valid(self, monkeypatch):
        from db import families as fdb

        pool, _ = _pool(fetch=[])
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        assert await fdb.list_family_members(1, 7) == []


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_membership_requires_family_ownership(self, monkeypatch):
        """Creator 2 cannot add to creator 1's family: deterministic rejection."""
        from db import families as fdb

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)  # no family for creator 2
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(ValueError, match="not found for creator"):
            await fdb.add_content_family_member(2, 7, "V1")

    @pytest.mark.asyncio
    async def test_cannot_read_other_creator_family(self, monkeypatch):
        from db import families as fdb

        pool, conn = _pool(fetchrow=None)
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        assert await fdb.get_content_family(1, 999) is None
        sql, creator, _fid = conn.fetchrow.call_args[0][0], *conn.fetchrow.call_args[0][1:]
        assert "creator_id = $1" in sql and creator == 1

    @pytest.mark.asyncio
    async def test_listing_is_creator_scoped(self, monkeypatch):
        from db import families as fdb

        pool, conn = _pool(fetch=[_family_row()])
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool))
        rows = await fdb.list_content_families(1)
        assert len(rows) == 1
        sql = conn.fetch.call_args[0][0]
        assert "WHERE creator_id = $1" in sql
        assert conn.fetch.call_args[0][1] == 1

    @pytest.mark.asyncio
    async def test_vault_lookup_is_creator_scoped(self, monkeypatch):
        """Same CUID text under two creators resolves to per-creator families."""
        from db import families as fdb

        pool_a, _ = _pool(fetch=[_family_row(fid=1, creator=1, slug="A", label="A")])
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool_a))
        assert [r["slug"] for r in await fdb.get_families_for_vault_item(1, "V1")] == ["A"]

        pool_b, _ = _pool(fetch=[_family_row(fid=2, creator=2, slug="B", label="B")])
        monkeypatch.setattr(fdb, "get_pool", AsyncMock(return_value=pool_b))
        assert [r["slug"] for r in await fdb.get_families_for_vault_item(2, "V1")] == ["B"]

    def test_migration_enforces_cross_creator_integrity(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "FOREIGN KEY (family_id, creator_id)" in sql
        assert "REFERENCES commerce_content_families (id, creator_id)" in sql


class TestFoundationContracts:
    def test_no_is_primary(self):
        dao_src = (Path(__file__).parent.parent / "db" / "families.py").read_text(encoding="utf-8")
        mig_src = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "is_primary" not in dao_src
        assert "is_primary" not in mig_src

    def _ddl(self):
        lines = [
            line for line in MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("--")
        ]
        return "\n".join(lines)

    def test_no_commercial_fields(self):
        ddl = self._ddl().lower()
        for field in (
            "price", "currency", "offer_type", "bundle_type", "eligibility",
            "suppression", "fatigue", "ranking", "sales_count", "product_id",
            "dropfans",
        ):
            assert field not in ddl

    def test_no_backfill_or_taxonomy_inference(self):
        ddl = self._ddl()
        for stmt in ("INSERT INTO", "UPDATE ", "DELETE FROM", "bundle_group", "taxonomy", "folder", "tags"):
            assert stmt not in ddl

    def test_migration_idempotent_conventions(self):
        mig_src = MIGRATION_PATH.read_text(encoding="utf-8")
        assert mig_src.count("CREATE TABLE IF NOT EXISTS") == 2
        assert "CREATE INDEX IF NOT EXISTS" in mig_src

    def test_no_commercial_callers(self):
        import commerce.content_matching
        import commerce.execution
        import commerce.product_selection
        import commerce.vault_ranking
        import inspect

        for mod in (
            commerce.product_selection,
            commerce.content_matching,
            commerce.vault_ranking,
            commerce.execution,
        ):
            src = inspect.getsource(mod)
            assert "commerce_content_famil" not in src
            assert "db.families" not in src and "db import families" not in src
            assert "content_family" not in src
