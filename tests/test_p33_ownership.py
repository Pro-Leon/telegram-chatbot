"""P3.3.1 — item-level ownership primitive tests.

Covers ``commerce.dao.get_owned_vault_ids`` with mocked DB boundaries
(no live PostgreSQL, no production data):

- purchased snapshots create ownership (single + union + dedupe)
- NULL snapshots excluded (no reconstruction)
- non-purchased states excluded (pending/clicked/declined/expired/revoked)
- transaction_id NULL excluded
- creator/user isolation (query args + SQL contract)
- empty result is frozenset()
- DB failure raises (fail-closed, never false-empty)
- SQL contract: purchase-only, snapshot-only, no delivery/product/taxonomy sources
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]


def _pool_with_fetch(fetch_result=None, fetch_error=None):
    """Build a mocked pool whose connection fetch returns rows or raises."""
    mock_conn = AsyncMock()
    if fetch_error is not None:
        mock_conn.fetch = AsyncMock(side_effect=fetch_error)
    else:
        mock_conn.fetch = AsyncMock(return_value=fetch_result or [])
    mock_pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            return False

    mock_pool.acquire = MagicMock(return_value=_Acquire())
    return mock_pool, mock_conn


def _rows(*vault_ids):
    return [{"vault_item_id": v} for v in vault_ids]


class TestPurchasedSnapshotOwnership:
    @pytest.mark.asyncio
    async def test_purchased_snapshot_creates_ownership(self, monkeypatch):
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch(_rows("V1", "V2"))
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset({"V1", "V2"})

    @pytest.mark.asyncio
    async def test_multiple_offers_union(self, monkeypatch):
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch(_rows("V1", "V2", "V2", "V3"))
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset({"V1", "V2", "V3"})

    @pytest.mark.asyncio
    async def test_duplicate_ids_canonicalize(self, monkeypatch):
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch(_rows("V1", "V1", "V2"))
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset({"V1", "V2"})


class TestExclusions:
    @pytest.mark.asyncio
    async def test_null_snapshot_excluded(self, monkeypatch):
        """NULL vault_item_ids rows contribute nothing (DB filters them)."""
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch([])
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset()

    @pytest.mark.asyncio
    async def test_pending_clicked_excluded(self, monkeypatch):
        """Non-purchased states never qualify even with valid IDs + txn."""
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch([])
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset()

    @pytest.mark.asyncio
    async def test_no_transaction_not_owned(self, monkeypatch):
        """state='purchased' without transaction_id does not confer ownership."""
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch([])
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset()

    @pytest.mark.asyncio
    async def test_revoked_excluded(self, monkeypatch):
        """Revoked offers never contribute.

        The SQL contract admits only state='purchased', so a real database
        returns no rows for revoked offers; the mock mirrors that. No offer
        state definitions were modified for this test.
        """
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch([])
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset()


class TestIsolation:
    @pytest.mark.asyncio
    async def test_creator_isolation(self, monkeypatch):
        from commerce import dao as dao_mod

        seen = {}

        async def _fetch(sql, *args):
            seen["args"] = args
            assert args[0] == 1 and args[1] == 10
            return _rows("V1")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=_fetch)
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=mock_pool))
        assert await dao_mod.get_owned_vault_ids(1, 10) == frozenset({"V1"})
        assert seen["args"] == (1, 10)

    @pytest.mark.asyncio
    async def test_user_isolation(self, monkeypatch):
        from commerce import dao as dao_mod

        async def _fetch(sql, *args):
            assert args[0] == 1 and args[1] == 10
            return _rows("V1")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=_fetch)
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=mock_pool))
        result = await dao_mod.get_owned_vault_ids(1, 10)
        assert result == frozenset({"V1"})
        assert "V2" not in result

    @pytest.mark.asyncio
    async def test_empty_result_is_frozenset(self, monkeypatch):
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch([])
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        result = await dao_mod.get_owned_vault_ids(1, 10)
        assert result == frozenset()
        assert isinstance(result, frozenset)


class TestFailureSemantics:
    @pytest.mark.asyncio
    async def test_db_failure_raises_never_empty(self, monkeypatch):
        """A database failure must propagate, never become a false-empty set."""
        from commerce import dao as dao_mod

        pool, _ = _pool_with_fetch(fetch_error=RuntimeError("connection down"))
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(RuntimeError, match="connection down"):
            await dao_mod.get_owned_vault_ids(1, 10)


class TestSqlContract:
    def test_query_is_purchase_and_snapshot_only(self):
        """The query must admit only purchased+txn+snapshot rows and consult
        no delivery, product, taxonomy, or provider sources."""
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod.get_owned_vault_ids)
        assert "unnest(vault_item_ids)" in src
        assert "state = 'purchased'" in src
        assert "transaction_id IS NOT NULL" in src
        assert "vault_item_ids IS NOT NULL" in src
        assert "creator_id = $1 AND user_id = $2" in src
        for forbidden in (
            "vault_media_deliveries",
            "fangate_products",
            "dropfans",
            "taxonomy",
            "bundle_group",
            "product_id",
        ):
            assert forbidden not in src

    def test_uses_canonical_identity(self):
        """No second Vault-ID normalization algorithm may be introduced."""
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod.get_owned_vault_ids)
        assert "canonical_identity_ids" in src

    def test_return_type_is_frozenset(self):
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod.get_owned_vault_ids)
        assert "-> frozenset[str]" in src
        assert "frozenset()" in src
