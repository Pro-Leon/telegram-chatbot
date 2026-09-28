"""P3.3.10 — offer-event history reader tests (mocked DB, no live data).

Covers ``commerce.offer_history.get_offer_history`` with faked pool
connections: creator/user scoping, 24h UTC window on ``created_at``, all
six offer states counted, declined counts, last-offer timestamp, frozen
Vault snapshots (canonical, deduped), NULL/empty snapshot behavior,
active-duplicate detection, definition-identity limitation, DB failure
propagation, empty-result facts, input validation, determinism, and the
read-only/bounded production boundary.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from commerce.offer_history import OfferHistory, get_offer_history

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "offer_history.py"

T1 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


class FakeConn:
    """Canned creator-scoped reads dispatched by distinctive SQL fragments."""

    def __init__(self, aggregate=None, snapshots=None, active=None, fail_on=()):
        self.aggregate = aggregate or {
            "total": 0,
            "recent": 0,
            "last_at": None,
            "declined_total": 0,
            "declined_recent": 0,
            "active_total": 0,
            "pending_total": 0,
            "clicked_total": 0,
            "purchased_total": 0,
            "declined_check": 0,
            "expired_total": 0,
            "revoked_total": 0,
            "null_snapshots": 0,
        }
        self.snapshots = snapshots if snapshots is not None else []
        self.active = active if active is not None else []
        self.fail_on = set(fail_on)
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "COUNT(*) AS total" in sql:
            if "aggregate" in self.fail_on:
                raise RuntimeError("offer aggregate unavailable")
            return dict(self.aggregate)
        raise AssertionError(f"unexpected fetchrow: {sql[:80]}")

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "AND vault_item_ids IS NOT NULL" in sql:
            if "snapshots" in self.fail_on:
                raise RuntimeError("snapshot history unavailable")
            return list(self.snapshots)
        if "AND state IN ('pending', 'clicked')" in sql:
            if "active" in self.fail_on:
                raise RuntimeError("active history unavailable")
            return list(self.active)
        raise AssertionError(f"unexpected fetch: {sql[:80]}")


def _pool(conn):
    mock_pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    mock_pool.acquire = MagicMock(return_value=_Acquire())
    return mock_pool


def _install(monkeypatch, conn):
    import commerce.offer_history as mod

    monkeypatch.setattr(mod, "get_pool", AsyncMock(return_value=_pool(conn)))


def _agg(**over):
    base = {
        "total": 6,
        "recent": 2,
        "last_at": T2,
        "declined_total": 1,
        "declined_recent": 1,
        "active_total": 1,
        "pending_total": 1,
        "clicked_total": 0,
        "purchased_total": 2,
        "declined_check": 1,
        "expired_total": 1,
        "revoked_total": 1,
        "null_snapshots": 1,
    }
    base.update(over)
    return base


class TestCreatorUserScoping:
    @pytest.mark.asyncio
    async def test_every_query_carries_creator_and_user(self, monkeypatch):
        conn = FakeConn(aggregate=_agg())
        _install(monkeypatch, conn)
        history = await get_offer_history(2, 10)
        assert history.creator_id == 2 and history.user_id == 10
        assert len(conn.calls) == 3  # one aggregate + snapshots + active
        for _kind, _sql, args in conn.calls:
            assert args[:2] == (2, 10)

    @pytest.mark.asyncio
    async def test_same_user_across_two_creators_isolated(self, monkeypatch):
        conn = FakeConn(aggregate=_agg(total=1))
        _install(monkeypatch, conn)
        history = await get_offer_history(7, 10)
        assert history.creator_id == 7
        for _kind, _sql, args in conn.calls:
            assert args[0] == 7 and args[1] == 10

    def test_no_global_history_query(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "WHERE creator_id = $1 AND user_id = $2" in src
        assert src.count("WHERE creator_id = $1 AND user_id = $2") == 3


class TestTimeSemantics:
    @pytest.mark.asyncio
    async def test_recent_window_is_24h_on_created_at(self, monkeypatch):
        conn = FakeConn(aggregate=_agg())
        _install(monkeypatch, conn)
        await get_offer_history(1, 10)
        agg_sql = next(s for k, s, _ in conn.calls if k == "fetchrow")
        assert "created_at >= NOW() - INTERVAL '24 hours'" in agg_sql

    def test_no_other_windows(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "INTERVAL '7" not in src
        assert "INTERVAL '30" not in src
        assert "INTERVAL '90" not in src

    @pytest.mark.asyncio
    async def test_last_offer_at_is_max_created_at(self, monkeypatch):
        conn = FakeConn(aggregate=_agg(last_at=T2))
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.last_offer_at == T2
        agg_sql = next(s for k, s, _ in conn.calls if k == "fetchrow")
        assert "MAX(created_at)" in agg_sql


class TestStateSemantics:
    @pytest.mark.asyncio
    async def test_all_states_counted(self, monkeypatch):
        conn = FakeConn(aggregate=_agg())
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.total_offer_count == 6
        assert history.recent_offer_count == 2
        assert dict(history.state_counts) == {
            "pending": 1,
            "clicked": 0,
            "purchased": 2,
            "declined": 1,
            "expired": 1,
            "revoked": 1,
        }

    @pytest.mark.asyncio
    async def test_declined_rejection_counts(self, monkeypatch):
        conn = FakeConn(aggregate=_agg(declined_total=3, declined_recent=2))
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.declined_offer_count == 3
        assert history.recent_declined_offer_count == 2

    def test_no_accepted_state_invented(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "'accepted'" not in src
        assert '"accepted"' not in src

    @pytest.mark.asyncio
    async def test_purchased_counts_as_historical_offer(self, monkeypatch):
        conn = FakeConn(aggregate=_agg(total=2, purchased_total=2))
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.total_offer_count == 2
        assert dict(history.state_counts)["purchased"] == 2


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_frozen_snapshots_canonical_sorted(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(),
            snapshots=[{"vault_item_ids": ["V2", "V1"]}, {"vault_item_ids": ["V3"]}],
        )
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.offered_vault_sets == (("V1", "V2"), ("V3",))
        assert history.was_canonical_set_offered(["V1", "V2"]) is True
        assert history.was_canonical_set_offered(["V2", "V1"]) is True
        assert history.was_canonical_set_offered(["V9"]) is False

    @pytest.mark.asyncio
    async def test_duplicate_canonical_sets_deduped(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(),
            snapshots=[
                {"vault_item_ids": ["V1", "V2"]},
                {"vault_item_ids": ["V2", "V1"]},
                {"vault_item_ids": ["V1", "V2"]},
            ],
        )
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.offered_vault_sets == (("V1", "V2"),)

    @pytest.mark.asyncio
    async def test_null_and_empty_snapshots_excluded_but_counted(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(null_snapshots=2),
            snapshots=[{"vault_item_ids": None}, {"vault_item_ids": []}],
        )
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.null_snapshot_count == 2
        assert history.offered_vault_sets == ()
        assert history.was_canonical_set_offered(["V1"]) is False

    @pytest.mark.asyncio
    async def test_unusable_input_never_matches(self, monkeypatch):
        conn = FakeConn(aggregate=_agg())
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.was_canonical_set_offered(None) is False
        assert history.was_canonical_set_offered([]) is False
        assert history.has_active_canonical_set(None) is False


class TestActiveDuplicate:
    @pytest.mark.asyncio
    async def test_active_offer_facts(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(active_total=1, pending_total=1, clicked_total=0),
            active=[{"vault_item_ids": ["V1"]}],
        )
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.has_active_offer is True
        assert history.active_offer_count == 1
        assert history.active_vault_sets == (("V1",),)
        assert history.has_active_canonical_set(["V1"]) is True
        assert history.has_active_canonical_set(["V2"]) is False

    @pytest.mark.asyncio
    async def test_active_without_snapshot_identity_is_not_a_duplicate(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(active_total=1, pending_total=1),
            active=[{"vault_item_ids": None}],
        )
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.has_active_offer is True
        assert history.active_vault_sets == ()
        assert history.has_active_canonical_set(["V1"]) is False

    @pytest.mark.asyncio
    async def test_no_active_offer(self, monkeypatch):
        conn = FakeConn(aggregate=_agg(active_total=0, pending_total=0))
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.has_active_offer is False
        assert history.active_offer_count == 0
        assert history.active_vault_sets == ()


class TestDefinitionIdentityLimitation:
    @pytest.mark.asyncio
    async def test_definition_identity_unavailable(self, monkeypatch):
        conn = FakeConn(aggregate=_agg())
        _install(monkeypatch, conn)
        history = await get_offer_history(1, 10)
        assert history.definition_identity_available is False
        assert history.was_definition_offered(11, 1) is None
        assert history.was_definition_offered() is None

    def test_limitation_documented(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "definition_identity_available" in src
        assert "was_definition_offered" in src
        assert "NO OfferDefinition identity" in src or "no OfferDefinition identity" in src


class TestFailureAndEmpty:
    @pytest.mark.asyncio
    async def test_empty_successful_result_is_valid_empty_facts(self, monkeypatch):
        _install(monkeypatch, FakeConn())
        history = await get_offer_history(1, 10)
        assert history.total_offer_count == 0
        assert history.recent_offer_count == 0
        assert history.last_offer_at is None
        assert history.declined_offer_count == 0
        assert history.recent_declined_offer_count == 0
        assert history.has_active_offer is False
        assert history.active_offer_count == 0
        assert history.offered_vault_sets == ()
        assert history.active_vault_sets == ()
        assert history.null_snapshot_count == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["aggregate", "snapshots", "active"])
    async def test_db_failure_raises_never_empty(self, monkeypatch, failing):
        conn = FakeConn(fail_on=(failing,))
        _install(monkeypatch, conn)
        with pytest.raises(RuntimeError):
            await get_offer_history(1, 10)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("creator_id,user_id", [(0, 10), (-1, 10), (1, 0), (True, 10)])
    async def test_invalid_ids_raise(self, monkeypatch, creator_id, user_id):
        _install(monkeypatch, FakeConn())
        with pytest.raises(ValueError):
            await get_offer_history(creator_id, user_id)


class TestDeterminismAndBoundary:
    @pytest.mark.asyncio
    async def test_repeated_reads_identical(self, monkeypatch):
        conn = FakeConn(
            aggregate=_agg(),
            snapshots=[{"vault_item_ids": ["V2", "V1"]}],
            active=[{"vault_item_ids": ["V1"]}],
        )
        _install(monkeypatch, conn)
        first = await get_offer_history(1, 10)
        second = await get_offer_history(1, 10)
        assert first == second
        assert isinstance(first.offered_vault_sets, tuple)
        assert isinstance(first.state_counts, tuple)

    def test_frozen_immutable(self):
        assert OfferHistory.__dataclass_params__.frozen is True

    def test_read_only_bounded_no_foreign_sources(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "INSERT INTO" not in src
        assert "DELETE FROM" not in src
        # No Redis/cache/provider mechanics (prose may name them as exclusions).
        for token in (
            "import redis",
            "get_redis",
            "from db.redis",
            "from redis",
            "get_drop",
            "create_drop",
            "attach_drop",
            "check_drop_status",
        ):
            assert token not in src
        assert "JOIN" not in src
        assert "fangate_transactions" not in src
        assert "vault_media_deliveries" not in src
        assert "product_selection" not in src
        assert "content_matching" not in src
        assert src.count("FROM commerce_offers") == 3
        # No per-row follow-up queries: exactly one aggregate + two bounded reads.
        assert src.count("await conn.fetchrow") == 1
        assert src.count("await conn.fetch(") == 2
