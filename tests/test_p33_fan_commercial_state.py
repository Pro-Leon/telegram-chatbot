"""P3.3.8 — FanCommercialState tests (mocked DB, no live data).

Covers ``commerce.fan_commercial_state`` with faked pool connections and a
faked P3.3.1 ownership primitive: creator isolation, strict purchase
semantics, buyer-price spend, AOV/highest/last-purchase derivation, 24h
recency, ownership reuse, sent-only delivery, snapshot-only offer history,
deferred fields, USD discipline, raise-on-failure, determinism, and
production boundaries (no LLM/Dropfans/pricing/selection/taxonomy/Redis).
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from commerce.fan_commercial_state import (
    FanCommercialState,
    get_fan_commercial_state,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "fan_commercial_state.py"

T1 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


class FakeConn:
    """Canned creator-scoped reads dispatched by distinctive SQL fragments."""

    def __init__(self, purchase=None, recent=None, offers=None,
                 snapshots=None, delivered=None, fail_on=()):
        self.purchase = purchase or {"n": 0, "spend": 0, "highest": None,
                                     "last_at": None, "non_usd": 0}
        self.recent = recent or {"n": 0, "spend": 0}
        self.offers = offers or {"recent": 0, "rejected": 0, "last_at": None}
        self.snapshots = snapshots or []
        self.delivered = delivered or []
        self.fail_on = set(fail_on)
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "MAX(purchased_at)" in sql:
            if "purchase" in self.fail_on:
                raise RuntimeError("purchase aggregate unavailable")
            return dict(self.purchase)
        if "purchased_at >= NOW()" in sql:
            if "recent_purchase" in self.fail_on:
                raise RuntimeError("recent purchase aggregate unavailable")
            return dict(self.recent)
        if "MAX(created_at)" in sql:
            if "offers" in self.fail_on:
                raise RuntimeError("offer aggregate unavailable")
            return dict(self.offers)
        raise AssertionError(f"unexpected fetchrow: {sql[:80]}")

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "unnest(vault_item_ids)" in sql:
            if "snapshots" in self.fail_on:
                raise RuntimeError("snapshot history unavailable")
            return list(self.snapshots)
        if "vault_media_deliveries" in sql:
            if "delivered" in self.fail_on:
                raise RuntimeError("delivery history unavailable")
            return list(self.delivered)
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


def _install(monkeypatch, conn, owned=frozenset()):
    import commerce.fan_commercial_state as mod

    monkeypatch.setattr(mod, "get_pool", AsyncMock(return_value=_pool(conn)))
    calls = []

    async def _owned(creator_id, user_id):
        calls.append((creator_id, user_id))
        if isinstance(owned, BaseException):
            raise owned
        return owned

    monkeypatch.setattr("commerce.ownership.fetch_owned_vault_ids", _owned)
    return calls


def _rich_conn(**over):
    purchase = {"n": 2, "spend": 5000, "highest": 3000, "last_at": T2, "non_usd": 0}
    purchase.update(over.get("purchase", {}))
    recent = {"n": 1, "spend": 3000}
    recent.update(over.get("recent", {}))
    offers = {"recent": 4, "rejected": 1, "last_at": T2}
    offers.update(over.get("offers", {}))
    return FakeConn(
        purchase=purchase, recent=recent, offers=offers,
        snapshots=over.get("snapshots", [{"vault_item_id": "V2"}, {"vault_item_id": "V1"}]),
        delivered=over.get("delivered", [{"vault_item_id": "V1"}]),
        fail_on=over.get("fail_on", ()),
    )


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_same_user_across_two_creators(self, monkeypatch):
        conn = _rich_conn()
        owned_calls = _install(monkeypatch, conn, frozenset({"V1"}))
        state = await get_fan_commercial_state(2, 10)
        assert state.creator_id == 2 and state.user_id == 10
        for _sql, args in conn.calls:
            assert args[0] == 2 and args[1] == 10
        assert owned_calls == [(2, 10)]

    @pytest.mark.asyncio
    async def test_every_query_carries_creator_and_user(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn)
        await get_fan_commercial_state(1, 10)
        assert len(conn.calls) == 5
        for _sql, args in conn.calls:
            assert args[:2] == (1, 10)

    def test_no_global_user_lookup(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "first_purchase_at" not in src
        assert "first_offer_id" not in src
        assert "first_transaction_id" not in src


class TestPurchaseSemantics:
    @pytest.mark.asyncio
    async def test_strict_predicate_in_sql(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn)
        await get_fan_commercial_state(1, 10)
        purchase_sql = next(s for s, _ in conn.calls if "MAX(purchased_at)" in s)
        assert "state = 'purchased'" in purchase_sql
        assert "transaction_id IS NOT NULL" in purchase_sql

    @pytest.mark.asyncio
    async def test_legacy_null_snapshot_still_counted(self, monkeypatch):
        conn = _rich_conn(purchase={"n": 1, "spend": 2000, "highest": 2000,
                                    "last_at": T1, "non_usd": 0})
        _install(monkeypatch, conn, frozenset())
        state = await get_fan_commercial_state(1, 10)
        assert state.purchase_count == 1
        assert state.total_spend_minor == 2000
        assert state.purchased_vault_ids == frozenset()

    @pytest.mark.asyncio
    async def test_empty_state_is_zero_not_failure(self, monkeypatch):
        _install(monkeypatch, FakeConn(), frozenset())
        state = await get_fan_commercial_state(1, 10)
        assert state.purchase_count == 0
        assert state.total_spend_minor == 0
        assert state.average_order_value_minor is None
        assert state.highest_purchase_minor is None
        assert state.last_purchase_at is None
        assert state.purchased_vault_ids == frozenset()
        assert state.delivered_vault_ids == ()
        assert state.recent_offered_vault_ids == ()


class TestSpend:
    def test_no_earning_or_alternate_sources(self):
        # The "never use" prohibition is documented in prose; here assert no
        # spend mechanics other than SUM(price_minor) exist.
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("SUM(seller_earning)", "SUM(set_price)", "segments.",
                      "from segments", "revenue_minor"):
            assert token not in src
        assert "SUM(price_minor)" in src

    @pytest.mark.asyncio
    async def test_null_price_keeps_count_not_spend(self, monkeypatch):
        # Two strict purchases; one has NULL price: SUM sees 2000, COUNT sees 2.
        conn = _rich_conn(purchase={"n": 2, "spend": 2000, "highest": 2000,
                                    "last_at": T1, "non_usd": 0})
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.purchase_count == 2
        assert state.total_spend_minor == 2000
        assert state.average_order_value_minor == 1000

    @pytest.mark.asyncio
    async def test_sum_correct(self, monkeypatch):
        _install(monkeypatch, _rich_conn(), frozenset({"V1", "V2", "V3"}))
        state = await get_fan_commercial_state(1, 10)
        assert state.total_spend_minor == 5000


class TestAOVHighestLast:
    @pytest.mark.asyncio
    async def test_aov_floor_semantics(self, monkeypatch):
        conn = _rich_conn(purchase={"n": 2, "spend": 3001, "highest": 3000,
                                    "last_at": T2, "non_usd": 0})
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.average_order_value_minor == 1500

    @pytest.mark.asyncio
    async def test_aov_single_purchase(self, monkeypatch):
        conn = _rich_conn(purchase={"n": 1, "spend": 1999, "highest": 1999,
                                    "last_at": T1, "non_usd": 0})
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.average_order_value_minor == 1999
        assert state.highest_purchase_minor == 1999
        assert state.last_purchase_at == T1

    @pytest.mark.asyncio
    async def test_highest_none_without_priced_purchase(self, monkeypatch):
        conn = _rich_conn(purchase={"n": 1, "spend": 0, "highest": None,
                                    "last_at": T1, "non_usd": 0})
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.highest_purchase_minor is None
        assert state.last_purchase_at == T1


class TestRecentPurchase:
    @pytest.mark.asyncio
    async def test_window_uses_purchased_at(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn)
        await get_fan_commercial_state(1, 10)
        recent_sql = next(s for s, _ in conn.calls if "purchased_at >= NOW()" in s)
        assert "INTERVAL '24 hours'" in recent_sql
        assert "created_at >=" not in recent_sql

    @pytest.mark.asyncio
    async def test_recent_values(self, monkeypatch):
        _install(monkeypatch, _rich_conn())
        state = await get_fan_commercial_state(1, 10)
        assert state.recent_purchase_count == 1
        assert state.recent_spend_minor == 3000

    def test_no_other_windows(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "INTERVAL '7" not in src
        assert "INTERVAL '30" not in src
        assert "INTERVAL '90" not in src


class TestOwnership:
    @pytest.mark.asyncio
    async def test_exact_reuse_of_primitive(self, monkeypatch):
        conn = _rich_conn()
        owned_calls = _install(monkeypatch, conn, frozenset({"V3", "V1"}))
        state = await get_fan_commercial_state(1, 10)
        assert owned_calls == [(1, 10)]
        assert state.purchased_vault_ids == frozenset({"V1", "V3"})

    def test_no_duplicated_ownership_sql(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "fetch_owned_vault_ids" in src
        assert src.count("unnest(") == 1  # only recent-snapshot history

    @pytest.mark.asyncio
    async def test_ownership_failure_propagates(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn, owned=RuntimeError("ownership unavailable"))
        with pytest.raises(RuntimeError, match="ownership unavailable"):
            await get_fan_commercial_state(1, 10)


class TestDelivery:
    @pytest.mark.asyncio
    async def test_sent_only_scoping(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn)
        await get_fan_commercial_state(1, 10)
        sql = next(s for s, _ in conn.calls if "vault_media_deliveries" in s)
        assert "status = 'sent'" in sql
        assert "dropfans_vault_item_id IS NOT NULL" in sql
        assert "creator_id = $1 AND user_id = $2" in sql

    @pytest.mark.asyncio
    async def test_distinct_deterministic(self, monkeypatch):
        conn = _rich_conn(delivered=[{"vault_item_id": "V2"},
                                     {"vault_item_id": "V1"},
                                     {"vault_item_id": "V2"}])
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.delivered_vault_ids == ("V1", "V2")

    @pytest.mark.asyncio
    async def test_delivery_does_not_create_ownership(self, monkeypatch):
        conn = _rich_conn(delivered=[{"vault_item_id": "V9"}])
        _install(monkeypatch, conn, frozenset())
        state = await get_fan_commercial_state(1, 10)
        assert state.delivered_vault_ids == ("V9",)
        assert state.purchased_vault_ids == frozenset()

    @pytest.mark.asyncio
    async def test_unowned_but_undelivered_purchase(self, monkeypatch):
        conn = _rich_conn(delivered=[])
        _install(monkeypatch, conn, frozenset({"V1", "V2"}))
        state = await get_fan_commercial_state(1, 10)
        assert state.purchased_vault_ids == frozenset({"V1", "V2"})
        assert state.delivered_vault_ids == ()

    def test_no_legacy_media_ids(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "fangate_media_id" not in src


class TestOfferHistory:
    @pytest.mark.asyncio
    async def test_counts_and_last_offer(self, monkeypatch):
        _install(monkeypatch, _rich_conn())
        state = await get_fan_commercial_state(1, 10)
        assert state.recent_offer_count == 4
        assert state.recent_rejected_offer_count == 1
        assert state.last_offer_at == T2

    @pytest.mark.asyncio
    async def test_window_bound(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn)
        await get_fan_commercial_state(1, 10)
        sql = next(s for s, _ in conn.calls if "MAX(created_at)" in s)
        assert "INTERVAL '24 hours'" in sql

    @pytest.mark.asyncio
    async def test_null_snapshot_counts_without_vault_ids(self, monkeypatch):
        conn = _rich_conn(
            offers={"recent": 2, "rejected": 0, "last_at": T1}, snapshots=[])
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.recent_offer_count == 2
        assert state.recent_offered_vault_ids == ()

    @pytest.mark.asyncio
    async def test_snapshot_history_canonical_sorted(self, monkeypatch):
        conn = _rich_conn(snapshots=[{"vault_item_id": "V3"},
                                     {"vault_item_id": "V1"},
                                     {"vault_item_id": "V1"}])
        _install(monkeypatch, conn)
        state = await get_fan_commercial_state(1, 10)
        assert state.recent_offered_vault_ids == ("V1", "V3")

    def test_no_live_or_mirror_sources(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("get_drop", "live price", "live Drop", "live Vault",
                      "provider", "mirror", "fangate_products",
                      "dropfans_product_id"):
            assert token not in src


class TestDeferred:
    def test_contract_has_no_deferred_fields(self):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(FanCommercialState)}
        assert fields == {
            "creator_id", "user_id", "purchase_count", "total_spend_minor",
            "average_order_value_minor", "highest_purchase_minor",
            "last_purchase_at", "recent_purchase_count", "recent_spend_minor",
            "purchased_vault_ids", "delivered_vault_ids", "recent_offer_count",
            "recent_rejected_offer_count", "last_offer_at",
            "recent_offered_vault_ids", "currency",
        }

    def test_no_type_or_acceptance_inference(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("recent_accepted", "single_offer_count",
                      "bundle_offer_count", "premium_offer_count",
                      "OFFER_TYPES", "offer_type"):
            assert token not in src

    def test_frozen_immutable(self):
        assert FanCommercialState.__dataclass_params__.frozen is True


class TestCurrency:
    @pytest.mark.asyncio
    async def test_usd_state(self, monkeypatch):
        _install(monkeypatch, _rich_conn())
        state = await get_fan_commercial_state(1, 10)
        assert state.currency == "USD"

    @pytest.mark.asyncio
    async def test_non_usd_excluded_with_warning(self, monkeypatch, caplog):
        conn = _rich_conn(purchase={"n": 2, "spend": 2000, "highest": 2000,
                                    "last_at": T1, "non_usd": 1})
        _install(monkeypatch, conn)
        with caplog.at_level("WARNING", logger="commerce.fan_commercial_state"):
            state = await get_fan_commercial_state(1, 10)
        assert state.total_spend_minor == 2000
        assert state.purchase_count == 2
        assert any("non-USD" in r.message for r in caplog.records)

    def test_no_fx(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("exchange_rate", "fx_rate", "forex", "FOREX"):
            assert token not in src


class TestFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["purchase", "recent_purchase", "offers",
                                         "snapshots", "delivered"])
    async def test_source_failure_raises(self, monkeypatch, failing):
        conn = _rich_conn(fail_on=(failing,))
        _install(monkeypatch, conn)
        with pytest.raises(RuntimeError):
            await get_fan_commercial_state(1, 10)


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_repeated_reads_identical(self, monkeypatch):
        conn = _rich_conn()
        _install(monkeypatch, conn, frozenset({"V2", "V1"}))
        first = await get_fan_commercial_state(1, 10)
        second = await get_fan_commercial_state(1, 10)
        assert first == second
        assert isinstance(first.purchased_vault_ids, frozenset)
        assert isinstance(first.delivered_vault_ids, tuple)
        assert isinstance(first.recent_offered_vault_ids, tuple)


class TestBoundary:
    def test_no_forbidden_dependencies(self):
        # Boundary prose ("never an opportunity ...") is documentation; assert
        # no corresponding mechanics exist.
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("llm_worker", "prompt_build", "generation", "caption",
                      "inference", "get_drop", "create_drop", "attach_drop",
                      "check_drop_status", "product_selection",
                      "content_matching", "vault_ranking", "parse_taxonomy",
                      "bundle_group", "conversational", "INSERT INTO",
                      "UPDATE ", "opportunity_id", "seal_opportunity",
                      "create_offer", "create_opportunity", "sales_pressure",
                      "pressure_score", "affordability_score", "can_afford",
                      "relationship_score", "conversion_probability",
                      "bundle_preference", "fan_segment", "wealth",
                      "redis", "Redis", "materializ", "backfill"):
            assert token not in src

    def test_quarantine_markers_intact(self):
        base = Path(__file__).parent.parent
        for rel in ("commerce/content_matching.py", "commerce/product_selection.py",
                    "commerce/vault_taxonomy.py", "commerce/dao.py",
                    "commerce/conversational.py", "commerce/operational_execution.py"):
            assert "P3.3.4 QUARANTINE" in (base / rel).read_text(encoding="utf-8")

    def test_no_new_migration(self):
        migs = sorted((Path(__file__).parent.parent / "db" / "migrations").glob("*p33*"))
        assert [p.name for p in migs] == [
            "20260917000000_p33_content_families.sql",
            "20260917010000_p33_offer_definitions.sql",
        ]
