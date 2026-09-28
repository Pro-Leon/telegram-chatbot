"""P3.2 Commerce Safety Foundation regression tests.

Covers every new invariant with mocked DB/provider boundaries (no live
credentials, no real PostgreSQL):

- canonical Vault-set identity/presentation/validation
- deterministic Drop content key
- offer snapshot written/immutable/fail-closed + legacy NULL
- execution-time verified price persisted (stale mirror never persisted)
- synthetic-ID → external-CUID resolution + fail-closed + creator isolation
- creator-scoped transaction uniqueness (migration contract)
- vault delivery uniqueness (migration + reservation contract)
- drop creation intent convergence (reuse active, refuse pending duplicate)
- snapshot/provider drift handling
- caption commercial values match sealed offer facts
- schema verification distinguishes present vs unknown
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "db"
    / "migrations"
    / "20260916000000_p32_safety_foundation.sql"
)


# ── Canonical Vault sets ──────────────────────────────────────────────


class TestCanonicalVaultSets:
    def test_identity_sorts_unique(self):
        from commerce.vault_sets import canonical_identity_ids

        assert canonical_identity_ids(["b", "a"]) == ["a", "b"]

    def test_identity_dedupes(self):
        from commerce.vault_sets import canonical_identity_ids

        assert canonical_identity_ids(["a", "b", "a"]) == ["a", "b"]

    def test_presentation_preserves_order_first_wins(self):
        from commerce.vault_sets import presentation_ids

        assert presentation_ids(["a", "b", "a"]) == ["a", "b"]
        assert presentation_ids(["b", "a"]) == ["b", "a"]

    def test_empty_rejected(self):
        from commerce.vault_sets import canonical_identity_ids, validate_vault_set

        # Identity of an empty set is defined (empty list); rejection happens
        # wherever a Drop/offer requires at least one item.
        assert canonical_identity_ids([]) == []
        with pytest.raises(ValueError):
            validate_vault_set([])

    def test_over_limit_rejected(self):
        from commerce.vault_sets import validate_vault_set

        with pytest.raises(ValueError):
            validate_vault_set([f"v{i}" for i in range(11)])

    def test_duplicates_rejected_by_validate(self):
        from commerce.vault_sets import validate_vault_set

        with pytest.raises(ValueError):
            validate_vault_set(["a", "a"])

    def test_identity_ignores_order_but_presentation_does_not(self):
        from commerce.vault_sets import canonical_identity_ids, presentation_ids

        assert canonical_identity_ids(["b", "a"]) == canonical_identity_ids(["a", "b"])
        assert presentation_ids(["b", "a"]) != presentation_ids(["a", "b"])


# ── Drop content key ──────────────────────────────────────────────────


class TestDropContentKey:
    def test_same_inputs_converge(self):
        from commerce.vault_sets import drop_content_key

        k1 = drop_content_key(
            creator_id=1, vault_item_ids=["b", "a"], price_minor=2500,
            currency="USD", allow_download=True,
        )
        k2 = drop_content_key(
            creator_id=1, vault_item_ids=["a", "b"], price_minor=2500,
            currency="usd", allow_download=True,
        )
        assert k1 == k2

    def test_different_creator_distinct(self):
        from commerce.vault_sets import drop_content_key

        k1 = drop_content_key(creator_id=1, vault_item_ids=["a"], price_minor=1000)
        k2 = drop_content_key(creator_id=2, vault_item_ids=["a"], price_minor=1000)
        assert k1 != k2

    def test_different_price_distinct(self):
        from commerce.vault_sets import drop_content_key

        k1 = drop_content_key(creator_id=1, vault_item_ids=["a"], price_minor=1000)
        k2 = drop_content_key(creator_id=1, vault_item_ids=["a"], price_minor=2500)
        assert k1 != k2

    def test_different_config_distinct(self):
        from commerce.vault_sets import drop_content_key

        k1 = drop_content_key(creator_id=1, vault_item_ids=["a"], price_minor=1000, allow_download=True)
        k2 = drop_content_key(creator_id=1, vault_item_ids=["a"], price_minor=1000, allow_download=False)
        assert k1 != k2

    def test_different_set_distinct(self):
        from commerce.vault_sets import drop_content_key

        k1 = drop_content_key(creator_id=1, vault_item_ids=["a", "b"], price_minor=1000)
        k2 = drop_content_key(creator_id=1, vault_item_ids=["a", "c"], price_minor=1000)
        assert k1 != k2

    def test_empty_set_rejected(self):
        from commerce.vault_sets import drop_content_key

        with pytest.raises(ValueError):
            drop_content_key(creator_id=1, vault_item_ids=[], price_minor=1000)


# ── Offer snapshot: DAO contract ──────────────────────────────────────


class TestOfferSnapshotContract:
    def test_snapshot_columns_in_insert(self):
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod.create_offer_serialized)
        assert "vault_item_ids" in src
        assert "dropfans_product_id" in src
        assert "drop_content_hash" in src

    def test_snapshot_columns_never_updated(self):
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod)
        for stmt in (
            "UPDATE commerce_offers SET price_minor",
            "UPDATE commerce_offers SET link",
            "UPDATE commerce_offers SET vault_item_ids",
            "UPDATE commerce_offers SET dropfans_product_id",
            "UPDATE commerce_offers SET product_id",
        ):
            assert stmt not in src

    def test_model_round_trips_snapshot(self):
        from commerce.models import PpvOffer

        row = {
            "id": 1, "creator_id": 1, "user_id": 5, "product_id": 5155,
            "link": "https://www.dropfans.io/buy/x", "price_minor": 2500,
            "currency": "USD", "state": "pending", "reason": "t",
            "created_by": "t", "created_at": None, "expires_at": None,
            "clicked_at": None, "purchased_at": None, "transaction_id": None,
            "dropfans_product_id": "df_x", "vault_item_ids": ["v1", "v2"],
            "media_count": 2, "drop_content_hash": "abc",
        }
        offer = PpvOffer.from_row(row)
        assert offer.vault_item_ids == ["v1", "v2"]
        assert offer.dropfans_product_id == "df_x"
        assert offer.media_count == 2
        assert offer.drop_content_hash == "abc"

    def test_model_legacy_null_snapshot(self):
        from commerce.models import PpvOffer

        row = {
            "id": 2, "creator_id": 1, "user_id": 5, "product_id": 5155,
            "link": "https://x", "price_minor": 2500,
            "currency": "USD", "state": "purchased", "reason": None,
            "created_by": "t", "created_at": None, "expires_at": None,
            "clicked_at": None, "purchased_at": None, "transaction_id": "t1",
        }
        offer = PpvOffer.from_row(row)
        assert offer.vault_item_ids is None
        assert offer.dropfans_product_id is None


# ── Execution: snapshot + verified price ──────────────────────────────


def _decision():
    from commerce.decision import CommerceDecision, CommerceReason
    from commerce.models import CommerceAction

    return CommerceDecision(
        action=CommerceAction.OFFER_PPV,
        reason_code=CommerceReason.STRONG_BUYING_SIGNAL,
        allowed=True,
        confidence=1.0,
    )


def _product_row(price_minor=2500, vault_ids=None):
    return {
        "id": 5155, "creator_id": 1, "is_accessible": True,
        "sales_url": "https://www.dropfans.io/buy/df_prod_abc123",
        "price_minor": price_minor, "is_verif_age": False,
        "raw": {
            "dropfans_product_id": "df_prod_abc123",
            "vaultItemIds": ["v1"] if vault_ids is None else vault_ids,
        },
    }


async def _run_execute(monkeypatch, *, local_price=2500, live_dollars=25.0,
                       vault_ids=None, live_side_effect=None):
    from commerce import execution as ex
    from db import dropfans as ddb
    from integrations.dropfans import service as dservice

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=_product_row(local_price, vault_ids))
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")
    if live_side_effect is not None:
        mock_get_drop = AsyncMock(side_effect=live_side_effect)
    else:
        async def _ok(cid, pid):
            assert pid == "df_prod_abc123", f"CUID must be external, got {pid!r}"
            return {"price": live_dollars, "currency": "USD",
                    "buyUrl": "https://www.dropfans.io/buy/df_prod_abc123",
                    "mediaCount": 1, "media": []}
        mock_get_drop = AsyncMock(side_effect=_ok)
    captured = {}

    async def _fake_create(**kwargs):
        captured.update(kwargs)
        return ({"id": 42, "state": "pending", **kwargs}, True)

    monkeypatch.setattr(ddb, "get_dropfans_integration",
                        AsyncMock(return_value={"status": "active", "encrypted_api_key": "x"}))
    monkeypatch.setattr(ex, "get_user",
                        AsyncMock(return_value={"id": 5, "is_blocked": False, "do_not_auto_reply": False}))
    monkeypatch.setattr(ex, "find_pending_offer_for_product", AsyncMock(return_value=None))
    monkeypatch.setattr(ex, "has_purchased_product", AsyncMock(return_value=False))
    monkeypatch.setattr(ex, "create_offer_serialized", _fake_create)
    monkeypatch.setattr(ex, "record_offer_transition", AsyncMock(return_value=None))
    monkeypatch.setattr(ex, "decrypt_secret", lambda t: "key")
    # P3.2C F1: tests declare the P3.2 safety schema PRESENT (no live DB).
    monkeypatch.setattr(
        ex,
        "check_commerce_schema_ready",
        AsyncMock(return_value=(True, "commerce_schema_present")),
    )
    monkeypatch.setattr("db.postgres.get_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(dservice, "get_drop", mock_get_drop)
    result = await ex.execute_ppv(
        creator_id=1, user_id=5, product_id=5155,
        decision=_decision(), created_by="test",
    )
    return result, captured, mock_get_drop


class TestExecutionSnapshotAndPrice:
    @pytest.mark.asyncio
    async def test_snapshot_written_on_creation(self, monkeypatch):
        result, captured, _ = await _run_execute(monkeypatch)
        assert result.status.value == "executed"
        assert captured["dropfans_product_id"] == "df_prod_abc123"
        assert captured["vault_item_ids"] == ["v1"]
        assert captured["media_count"] == 1
        assert captured["drop_content_hash"]

    @pytest.mark.asyncio
    async def test_stale_mirror_never_persisted(self, monkeypatch):
        # Local 2500 vs live 35.00 → persisted offer must equal verified 3500.
        result, captured, _ = await _run_execute(
            monkeypatch, local_price=2500, live_dollars=35.0)
        assert result.status.value == "executed"
        assert captured["price_minor"] == 3500
        assert result.metadata["verified_price_minor"] == 3500

    @pytest.mark.asyncio
    async def test_live_failure_fails_closed(self, monkeypatch):
        from integrations.dropfans.errors import DropfansTimeoutError

        async def _boom(cid, pid):
            raise DropfansTimeoutError("get_drop", "timeout")
        result, _, _ = await _run_execute(monkeypatch, live_side_effect=_boom)
        assert result.status.value == "provider_error"
        assert result.denial_reason == "price_verification_failed"

    @pytest.mark.asyncio
    async def test_missing_snapshot_fails_closed(self, monkeypatch):
        result, _, _ = await _run_execute(monkeypatch, vault_ids=[])
        assert result.status.value == "product_unavailable"
        assert result.denial_reason == "no_vault_snapshot"

    @pytest.mark.asyncio
    async def test_provider_receives_external_cuid(self, monkeypatch):
        _, _, mock_get_drop = await _run_execute(monkeypatch)
        for call in mock_get_drop.call_args_list:
            args = call[0] if call[0] else ()
            kwargs = call[1] if len(call) > 1 else {}
            pid = args[1] if len(args) > 1 else kwargs.get("drop_id", "")
            assert pid == "df_prod_abc123"
            assert pid != "5155"


# ── CUID resolution ───────────────────────────────────────────────────


class TestCuidResolution:
    @pytest.mark.asyncio
    async def test_canonical_column_preferred(self, monkeypatch):
        from db import dropfans as ddb

        row = {"dropfans_product_id": "df_canon", "raw": {"dropfans_product_id": "df_raw"}}
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))
        assert await ddb.resolve_dropfans_cuid(1, 5155) == "df_canon"

    @pytest.mark.asyncio
    async def test_raw_fallback(self, monkeypatch):
        from db import dropfans as ddb

        row = {"dropfans_product_id": None, "raw": {"dropfans_product_id": "df_raw"}}
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))
        assert await ddb.resolve_dropfans_cuid(1, 5155) == "df_raw"

    @pytest.mark.asyncio
    async def test_missing_fails_closed(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))
        assert await ddb.resolve_dropfans_cuid(1, 5155) is None

    @pytest.mark.asyncio
    async def test_cross_creator_isolated(self, monkeypatch):
        from db import dropfans as ddb

        seen = {}

        async def _fetchrow(sql, *args):
            seen["args"] = args
            return {"dropfans_product_id": "df_a", "raw": {}}

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))
        assert await ddb.resolve_dropfans_cuid(7, 5155) == "df_a"
        assert seen["args"][0] == 7

    @pytest.mark.asyncio
    async def test_verify_uses_cuid_not_synthetic(self, monkeypatch):
        from commerce import post_purchase as pp

        monkeypatch.setattr(
            "db.dropfans.resolve_dropfans_cuid", AsyncMock(return_value="df_real"))
        with patch("integrations.dropfans.service.get_drop",
                   new=AsyncMock(return_value={"price": 25.0, "currency": "USD"})) as mock_get:
            ok, drop, _ = await pp._verify_dropfans_drop_binding(1, 5155)
        assert ok is True
        args = mock_get.call_args[0]
        assert args == (1, "df_real")

    @pytest.mark.asyncio
    async def test_verify_missing_cuid_fails_closed(self, monkeypatch):
        from commerce import post_purchase as pp

        monkeypatch.setattr(
            "db.dropfans.resolve_dropfans_cuid", AsyncMock(return_value=None))
        with patch("integrations.dropfans.service.get_drop",
                   new=AsyncMock()) as mock_get:
            ok, drop, reason = await pp._verify_dropfans_drop_binding(1, 5155)
        assert ok is False
        assert reason == "missing_dropfans_product_id"
        mock_get.assert_not_called()


# ── Migration contracts ───────────────────────────────────────────────


class TestMigrationContracts:
    def _sql(self):
        assert MIGRATION_PATH.exists(), "P3.2 migration file must exist"
        return MIGRATION_PATH.read_text(encoding="utf-8")

    def test_offer_snapshot_columns(self):
        sql = self._sql()
        assert "vault_item_ids TEXT[]" in sql
        assert "media_count INTEGER" in sql
        assert "drop_content_hash" in sql

    def test_creator_scoped_txn_uniqueness(self):
        sql = self._sql()
        assert "idx_commerce_offers_creator_txn" in sql
        assert "DROP INDEX IF EXISTS idx_commerce_offers_transaction_id" in sql
        # No new global uniqueness on transaction_id alone.
        assert "UNIQUE (transaction_id)" not in sql

    def test_mirror_creator_scoped_uniqueness_not_global(self):
        sql = self._sql()
        assert "idx_fangate_products_creator_dropfans" in sql
        assert "(creator_id, dropfans_product_id)" in sql
        assert "UNIQUE (dropfans_product_id)" not in sql

    def test_delivery_uniqueness_with_dedup(self):
        sql = self._sql()
        assert "idx_vault_deliveries_creator_user_vault" in sql
        assert "(creator_id, user_id, dropfans_vault_item_id)" in sql
        assert "WHERE dropfans_vault_item_id IS NOT NULL" in sql
        # Deterministic duplicate cleanup preserving earliest row.
        assert "MIN(id)" in sql

    def test_intent_table(self):
        sql = self._sql()
        assert "dropfans_drop_intents" in sql
        assert "content_key" in sql
        assert "UNIQUE (creator_id, content_key)" in sql
        assert "'pending'" in sql and "'active'" in sql and "'failed'" in sql

    def test_reservation_matches_constraint(self):
        from commerce import post_purchase as pp
        import inspect

        src = inspect.getsource(pp.deliver_product_media)
        assert "ON CONFLICT (creator_id, user_id, dropfans_vault_item_id)" in src


# ── Drop creation intents ─────────────────────────────────────────────


class TestDropIntentFlow:
    @pytest.mark.asyncio
    async def test_active_intent_reused_without_post(self, monkeypatch):
        from integrations.dropfans import service as svc

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=(
                {"status": "active", "dropfans_product_id": "df_known",
                 "content_key": "k"}, False)),
        )
        monkeypatch.setattr(
            "db.dropfans.find_dropfans_product",
            AsyncMock(return_value={"sales_url": "https://www.dropfans.io/buy/df_known"}),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=AssertionError("must not reach provider"))):
            result = await svc.create_drop(
                1, name="t", price=25.0, vault_item_ids=["v1"])
        assert result["product_id"] == "df_known"

    @pytest.mark.asyncio
    async def test_pending_intent_refuses_duplicate_post(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=(
                {"status": "pending", "dropfans_product_id": None,
                 "content_key": "k"}, False)),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=AssertionError("must not POST"))):
            with pytest.raises(DropfansError):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])

    @pytest.mark.asyncio
    async def test_failed_intent_rearms_and_posts_once(self, monkeypatch):
        from integrations.dropfans import service as svc

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=(
                {"status": "failed", "dropfans_product_id": None,
                 "content_key": "k"}, False)),
        )
        monkeypatch.setattr(
            "db.drop_intents.rearm_failed_intent",
            AsyncMock(return_value={"status": "pending", "content_key": "k"}),
        )

        class _Result:
            product_id = "df_new"
            buy_url = "https://www.dropfans.io/buy/df_new"
            media_count = 1

        async def _fake_run_scoped(creator_id, op, fn):
            client = MagicMock()
            client.create_drop = AsyncMock(return_value=_Result())
            created = await fn(client)
            return created

        monkeypatch.setattr(
            "db.dropfans.upsert_dropfans_product", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "db.drop_intents.mark_intent_active",
            AsyncMock(return_value={"status": "active"}),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=_fake_run_scoped)):
            result = await svc.create_drop(
                1, name="t", price=25.0, vault_item_ids=["v1"])
        assert result["product_id"] == "df_new"

    @pytest.mark.asyncio
    async def test_failed_intent_rearm_failure_refuses_post(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=(
                {"status": "failed", "dropfans_product_id": None,
                 "content_key": "k"}, False)),
        )
        monkeypatch.setattr(
            "db.drop_intents.rearm_failed_intent",
            AsyncMock(return_value=None),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=AssertionError("must not POST"))):
            with pytest.raises(DropfansError):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])

    @pytest.mark.asyncio
    async def test_same_set_same_config_converges_key(self):
        from commerce.vault_sets import drop_content_key

        base = dict(creator_id=1, vault_item_ids=["v1", "v2"],
                    price_minor=2500, currency="USD", allow_download=True)
        assert drop_content_key(**base) == drop_content_key(**base)

    @pytest.mark.asyncio
    async def test_rearm_only_touches_failed(self, monkeypatch):
        from db import drop_intents as di

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(di, "get_pool", AsyncMock(return_value=mock_pool))
        assert await di.rearm_failed_intent(1, "k") is None
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "AND status = 'failed'" in sql
        assert "SET status = 'pending'" in sql

    @pytest.mark.asyncio
    async def test_intent_dao_converges(self, monkeypatch):
        from db import drop_intents as di

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            None,  # INSERT ... ON CONFLICT DO NOTHING → no row (conflict)
            {"content_key": "k", "status": "pending"},  # existing
        ])

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(di, "get_pool", AsyncMock(return_value=mock_pool))
        row, created = await di.get_or_create_intent(
            1, content_key="k", canonical_vault_item_ids=["v1"],
            price_minor=2500)
        assert created is False
        assert row["content_key"] == "k"


# ── Delivery snapshot / drift ─────────────────────────────────────────


class TestDeliverySnapshotContract:
    @pytest.mark.asyncio
    async def test_snapshot_preferred_over_drifted_live(self, monkeypatch):
        """Snapshot [v1,v2] + live [v1,v2,v3] → effective [v1,v2], drift logged."""
        from commerce import post_purchase as pp

        product = {
            "product_type": "dropfans",
            "raw": {"vaultItemIds": ["v1", "v2"]},
            "sales_url": "https://www.dropfans.io/buy/d",
        }
        offer_row = {
            "vault_item_ids": ["v1", "v2"], "dropfans_product_id": "df_d",
            "media_count": 2, "transaction_id": "t", "purchased_at": None,
        }
        async def _fetchrow(sql, *args):
            if "FROM commerce_offers" in sql:
                return offer_row
            if "FROM vault_media_deliveries" in sql:
                return None
            return None

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow)

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_Acquire())
        mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
        monkeypatch.setattr("db.postgres.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("db.fangate.get_fangate_product", AsyncMock(return_value=product))
        monkeypatch.setattr(pp, "_verify_dropfans_drop_binding",
                            AsyncMock(return_value=(True, {
                                "media": [{"vaultItemId": "v1"}, {"vaultItemId": "v2"},
                                          {"vaultItemId": "v3"}],
                                "mediaCount": 3, "buyUrl": "https://www.dropfans.io/buy/d",
                            }, None)))
        monkeypatch.setattr(pp, "_fetch_dropfans_vault_map", AsyncMock(return_value={}))
        monkeypatch.setattr("commerce.dao.has_purchased_product", AsyncMock(return_value=True))

        with patch.object(pp.logger, "warning") as mock_warn:
            await pp.deliver_product_media(1, 5, 5155, "t")
        drift_logs = [c for c in mock_warn.call_args_list
                      if "snapshot/provider drift" in str(c)]
        assert drift_logs, "drift must be logged, snapshot preserved"

    @pytest.mark.asyncio
    async def test_duplicate_delivery_single_row(self):
        """Reservation SQL targets the same (creator,user,CUID) uniqueness."""
        from commerce import post_purchase as pp
        import inspect

        src = inspect.getsource(pp.deliver_product_media)
        assert "dropfans_vault_item_id=$3" in src
        assert "WHERE dropfans_vault_item_id IS NOT NULL" in src


# ── Caption grounding ─────────────────────────────────────────────────


class TestCaptionGrounding:
    @pytest.mark.asyncio
    async def test_caption_uses_persisted_offer_price(self, monkeypatch):
        from commerce.decision import CommerceDecision, CommerceReason
        from commerce.models import CommerceAction
        from commerce.pipeline import _sealed_caption_facts
        from commerce.execution import ExecutionResult, ExecutionStatus

        request = MagicMock()
        request.creator_id = 1
        request.product_state = MagicMock()
        request.product_state.price_minor = 2500  # stale mirror cache
        request.currency = "USD"
        exec_res = ExecutionResult(status=ExecutionStatus.EXECUTED, offer_id=42)
        monkeypatch.setattr(
            "commerce.dao.get_offer",
            AsyncMock(return_value={
                "price_minor": 3500, "currency": "USD",
                "link": "https://www.dropfans.io/buy/d",
            }),
        )
        sealed_state, sealed_currency = await _sealed_caption_facts(request, exec_res)
        assert sealed_state.price_minor == 3500
        assert sealed_currency == "USD"

    @pytest.mark.asyncio
    async def test_caption_falls_back_without_offer(self, monkeypatch):
        from commerce.pipeline import _sealed_caption_facts

        request = MagicMock()
        request.product_state = MagicMock()
        sealed_state, _ = await _sealed_caption_facts(request, None)
        assert sealed_state is request.product_state


# ── Transaction creator-scoping (behavioral) ──────────────────────────


class TestTransactionCreatorScoping:
    @pytest.mark.asyncio
    async def test_same_txn_different_creators_both_attributed(self, monkeypatch):
        """Same transaction_id under two creators must not collide.

        DAO attribution is creator-scoped (all SQL carries creator_id and the
        conditional claim), so creator 2's offer can still be purchased with
        the same textual transaction id creator 1 already used.
        """
        from commerce import dao as dao_mod

        for creator in (1, 2):
            mock_conn = AsyncMock()
            mock_conn.fetch = AsyncMock(return_value=[
                {"id": 100 + creator, "user_id": 50 + creator,
                 "product_id": 5155, "state": "pending"},
            ])
            updated = {"id": 100 + creator, "user_id": 50 + creator,
                       "product_id": 5155, "price_minor": 2500,
                       "currency": "USD", "purchased_at": None}

            async def _fetchrow(sql, *args, _u=updated):
                if sql.strip().startswith("UPDATE commerce_offers"):
                    assert args[0] in (1, 2)
                    return _u
                if "FROM users" in sql:
                    return {"funnel_stage": "converted",
                            "first_purchase_at": "x",
                            "first_offer_id": 1}
                return None

            mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow)
            mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

            class _Acquire:
                async def __aenter__(self):
                    return mock_conn

                async def __aexit__(self, *a):
                    return False

            class _Tx:
                async def __aenter__(self):
                    return mock_conn

                async def __aexit__(self, *a):
                    return False

            mock_conn.transaction = MagicMock(return_value=_Tx())
            mock_pool = MagicMock()
            mock_pool.acquire = MagicMock(return_value=_Acquire())
            monkeypatch.setattr(dao_mod, "get_pool",
                                AsyncMock(return_value=mock_pool))
            # NOTE: dao.attribute_purchase_from_webhook resolves get_pool at
            # call time via db.postgres.get_pool; patch both paths.
            import db.postgres as pg

            monkeypatch.setattr(pg, "get_pool",
                                AsyncMock(return_value=mock_pool))
            record = await dao_mod.attribute_purchase_from_webhook(
                creator, 5155, "shared-txn-1")
            assert record is not None
            assert record.creator_id == creator
            assert record.transaction_id == "shared-txn-1"

    def test_offer_claim_sql_is_creator_scoped(self):
        import inspect

        from commerce import dao as dao_mod

        src = inspect.getsource(dao_mod.attribute_purchase_from_webhook)
        assert "WHERE creator_id = $1 AND id = $2" in src
        assert "AND (transaction_id IS NULL OR transaction_id = $3)" in src
        src2 = inspect.getsource(dao_mod.mark_offer_purchased)
        assert "WHERE creator_id = $1 AND id = $2" in src2


# ── Mirror upsert creator isolation ─────────────────────────────────────


class TestMirrorUpsertIsolation:
    def test_upsert_uses_creator_scoped_conflict(self):
        import inspect

        from db import dropfans as ddb

        src = inspect.getsource(ddb.upsert_dropfans_product)
        assert "ON CONFLICT (creator_id, dropfans_product_id)" in src
        # Legacy creator-blind upsert must not silently overwrite another
        # creator: the only id-targeted upsert is the pre-migration fallback.
        assert "WHERE fangate_products.creator_id = $2" in inspect.getsource(
            __import__("db.fangate", fromlist=["upsert_fangate_product"])
            .upsert_fangate_product)

    @pytest.mark.asyncio
    async def test_same_cuid_two_creators_distinct_params(self, monkeypatch):
        from db import dropfans as ddb

        seen = []

        async def _execute(sql, *args):
            seen.append((sql, args))
            return "INSERT 0 1"

        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock(side_effect=_execute)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))
        await ddb.upsert_dropfans_product(1, dropfans_product_id="df_shared")
        await ddb.upsert_dropfans_product(2, dropfans_product_id="df_shared")
        assert len(seen) == 2
        assert seen[0][1][1] == 1
        assert seen[1][1][1] == 2
        assert seen[0][1][7] == seen[1][1][7] == "df_shared"
        assert "ON CONFLICT (creator_id, dropfans_product_id)" in seen[1][0]


# ── Schema verification ───────────────────────────────────────────────


class TestSchemaVerification:
    @pytest.mark.asyncio
    async def test_unknown_not_healthy_on_db_failure(self, monkeypatch):
        from db import postgres as pg

        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr(pg, "get_pool", _boom)
        status = await pg.check_migrations_pending()
        assert status.get("up_to_date") is not True
        assert status.get("unknown") is True

        safety = await pg.verify_commerce_safety_schema()
        assert safety.get("present") is not True
        assert safety.get("unknown") is True

    def test_verify_schema_covers_intents(self):
        import inspect

        from db.postgres import verify_schema

        assert "dropfans_drop_intents" in inspect.getsource(verify_schema)


# ── P3.2C F1: commerce schema gate ──────────────────────────────────────


class TestCommerceSchemaGate:
    @pytest.mark.asyncio
    async def test_present_means_ready(self, monkeypatch):
        from commerce import execution as ex

        monkeypatch.setattr(
            "db.postgres.verify_commerce_safety_schema",
            AsyncMock(return_value={"present": True, "missing": {}}),
        )
        ready, reason = await ex.check_commerce_schema_ready(force=True)
        assert ready is True
        assert reason == "commerce_schema_present"

    @pytest.mark.asyncio
    async def test_absent_refuses(self, monkeypatch):
        from commerce import execution as ex

        monkeypatch.setattr(
            "db.postgres.verify_commerce_safety_schema",
            AsyncMock(return_value={"present": False, "missing": {"tables": []}}),
        )
        ready, reason = await ex.check_commerce_schema_ready(force=True)
        assert ready is False
        assert reason == "commerce_schema_absent"

    @pytest.mark.asyncio
    async def test_unknown_refuses_never_healthy(self, monkeypatch):
        from commerce import execution as ex

        async def _boom():
            raise ConnectionError("db down")

        monkeypatch.setattr("db.postgres.get_pool", _boom)
        ready, reason = await ex.check_commerce_schema_ready(force=True)
        assert ready is False
        assert reason == "commerce_schema_unknown"

    @pytest.mark.asyncio
    async def test_execute_ppv_blocked_when_schema_absent(self, monkeypatch):
        from commerce import execution as ex

        monkeypatch.setattr(
            ex, "check_commerce_schema_ready",
            AsyncMock(return_value=(False, "commerce_schema_absent")),
        )
        result = await ex.execute_ppv(
            creator_id=1, user_id=5, product_id=5155,
            decision=_decision(), created_by="test",
        )
        assert result.status.value == "denied"
        assert result.denial_reason == "commerce_schema_absent"
        assert result.offer_id is None

    @pytest.mark.asyncio
    async def test_execute_ppv_blocked_when_schema_unknown(self, monkeypatch):
        from commerce import execution as ex

        monkeypatch.setattr(
            ex, "check_commerce_schema_ready",
            AsyncMock(return_value=(False, "commerce_schema_unknown")),
        )
        result = await ex.execute_ppv(
            creator_id=1, user_id=5, product_id=5155,
            decision=_decision(), created_by="test",
        )
        assert result.status.value == "denied"
        assert result.denial_reason == "commerce_schema_unknown"

    def test_worker_startup_wires_readiness_gate(self):
        import inspect

        from workers.llm_worker import run_worker

        src = inspect.getsource(run_worker)
        assert "check_commerce_schema_ready" in src
        # Unknown/absent must disable commerce, never continue degraded.
        assert "commerce_schema_unknown" in src


# ── P3.2C F1: DAO snapshot fail-closed ──────────────────────────────────


class TestDaoSnapshotFailClosed:
    @pytest.mark.asyncio
    async def test_missing_columns_propagate_no_legacy_insert(self, monkeypatch):
        """A schema error must propagate — exactly one INSERT attempted."""
        from commerce import dao as dao_mod

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            None,  # no existing redeemable offer
            Exception('column "vault_item_ids" does not exist'),  # INSERT fails
        ])
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        class _Tx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_conn.transaction = MagicMock(return_value=_Tx())
        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(Exception, match="vault_item_ids"):
            await dao_mod.create_offer_serialized(
                creator_id=1, user_id=5, product_id=5155, link="https://x",
                price_minor=2500, currency="USD", created_by="t",
                dropfans_product_id="df_x", vault_item_ids=["v1"],
            )
        # Existing-check + exactly one failing INSERT; no silent legacy retry.
        assert mock_conn.fetchrow.await_count == 2

    @pytest.mark.asyncio
    async def test_missing_snapshot_raises_before_sql(self, monkeypatch):
        from commerce import dao as dao_mod

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        monkeypatch.setattr(dao_mod, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(ValueError, match="commerce_snapshot_required"):
            await dao_mod.create_offer_serialized(
                creator_id=1, user_id=5, product_id=5155, link="https://x",
                created_by="t", dropfans_product_id="df_x", vault_item_ids=None,
            )
        mock_pool.acquire.assert_not_called()
        assert mock_conn.fetchrow.await_count == 0


# ── P3.2C F2: failure classification ────────────────────────────────────


class TestFailureClassification:
    def test_definitive_rejections(self):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import (
            DropfansAuthenticationError,
            DropfansAuthorizationError,
            DropfansNotFoundError,
            DropfansRateLimitError,
            DropfansValidationError,
        )

        assert svc._is_definitive_rejection(DropfansValidationError("create_drop", "bad", 422)) is True
        assert svc._is_definitive_rejection(DropfansAuthenticationError("create_drop", "x", 401)) is True
        assert svc._is_definitive_rejection(DropfansAuthorizationError("create_drop", "x", 403)) is True
        assert svc._is_definitive_rejection(DropfansNotFoundError("create_drop", "x", 404)) is True
        assert svc._is_definitive_rejection(DropfansRateLimitError("create_drop", "x")) is True

    def test_ambiguous_results(self):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import (
            DropfansResponseError,
            DropfansServerError,
            DropfansTimeoutError,
            DropfansTransportError,
        )

        assert svc._is_definitive_rejection(DropfansTimeoutError("create_drop", "t")) is False
        assert svc._is_definitive_rejection(DropfansTransportError("create_drop", "reset")) is False
        assert svc._is_definitive_rejection(DropfansServerError("create_drop", "boom", 500)) is False
        assert svc._is_definitive_rejection(DropfansResponseError("create_drop", "bad body")) is False
        assert svc._is_definitive_rejection(RuntimeError("bug")) is False

    @pytest.mark.asyncio
    async def test_definitive_rejection_marks_failed(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansValidationError

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=({"status": "pending", "content_key": "k"}, True)),
        )
        marked = {}

        async def _mark_failed(cid, key, reason):
            marked.update(creator=cid, key=key, reason=reason)
            return {"status": "failed"}

        monkeypatch.setattr("db.drop_intents.mark_intent_failed", _mark_failed)

        class _Client:
            async def create_drop(self, **kwargs):
                raise DropfansValidationError("create_drop", "invalid vault item", 422)

        async def _fake_scoped(cid, op, fn):
            return await fn(_Client())

        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=_fake_scoped)):
            with pytest.raises(DropfansValidationError):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])
        assert marked["reason"].startswith("drop_provider_rejection:")

    @pytest.mark.asyncio
    async def test_timeout_stays_pending_no_repost(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError, DropfansTimeoutError

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=({"status": "pending", "content_key": "k"}, True)),
        )
        monkeypatch.setattr(
            "db.drop_intents.mark_intent_failed",
            AsyncMock(side_effect=AssertionError("ambiguous must not mark failed")),
        )
        posts = []

        class _Client:
            async def create_drop(self, **kwargs):
                posts.append(1)
                raise DropfansTimeoutError("create_drop", "timeout")

        async def _fake_scoped(cid, op, fn):
            return await fn(_Client())

        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=_fake_scoped)):
            with pytest.raises(DropfansTimeoutError):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])
        assert len(posts) == 1

        # A follow-up attempt for the same content must NOT POST again.
        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=({"status": "pending", "content_key": "k"}, False)),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=AssertionError("must not POST"))):
            with pytest.raises(DropfansError, match="pending"):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])

    @pytest.mark.asyncio
    async def test_mirror_failure_stays_pending_preserves_cuid(self, monkeypatch):
        from integrations.dropfans import service as svc

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=({"status": "pending", "content_key": "k"}, True)),
        )
        monkeypatch.setattr(
            "db.drop_intents.mark_intent_failed",
            AsyncMock(side_effect=AssertionError("persistence failure must not mark failed")),
        )
        noted = {}

        async def _note(cid, key, cuid):
            noted.update(creator=cid, key=key, cuid=cuid)
            return {"status": "pending", "dropfans_product_id": cuid}

        monkeypatch.setattr("db.drop_intents.note_intent_provider_cuid", _note)

        class _Result:
            product_id = "df_orphan"
            buy_url = "https://www.dropfans.io/buy/df_orphan"
            media_count = 1

        class _Client:
            async def create_drop(self, **kwargs):
                return _Result()

        async def _fake_scoped(cid, op, fn):
            return await fn(_Client())

        monkeypatch.setattr(
            "db.dropfans.upsert_dropfans_product",
            AsyncMock(side_effect=RuntimeError("mirror down")),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=_fake_scoped)):
            with pytest.raises(RuntimeError, match="mirror down"):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])
        assert noted["cuid"] == "df_orphan"

    @pytest.mark.asyncio
    async def test_pending_refusal_names_reconciliation(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError

        monkeypatch.setattr(
            "db.drop_intents.get_or_create_intent",
            AsyncMock(return_value=({"status": "pending", "content_key": "k"}, False)),
        )
        with patch("integrations.dropfans.service._run_scoped",
                   new=AsyncMock(side_effect=AssertionError("must not POST"))):
            with pytest.raises(DropfansError, match="drop_intent_pending_reconciliation"):
                await svc.create_drop(1, name="t", price=25.0, vault_item_ids=["v1"])

    @pytest.mark.asyncio
    async def test_note_cuid_only_touches_pending(self, monkeypatch):
        from db import drop_intents as di

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(di, "get_pool", AsyncMock(return_value=mock_pool))
        assert await di.note_intent_provider_cuid(1, "k", "df_x") is None
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "AND status = 'pending'" in sql
        assert "dropfans_product_id" in sql


# ── P3.2C F2: adoption ──────────────────────────────────────────────────


def _adopt_intent_row():
    return {
        "creator_id": 1,
        "content_key": None,  # filled by caller via real key fn
        "canonical_vault_item_ids": ["v1", "v2"],
        "price_minor": 2500,
        "currency": "USD",
        "allow_download": True,
        "status": "pending",
        "dropfans_product_id": None,
    }


def _adopt_live_drop():
    return {
        "id": "df_adopt_me",
        "name": "t",
        "price": 25.0,
        "currency": "USD",
        "allow_download": True,
        "buy_url": "https://www.dropfans.io/buy/df_adopt_me",
        "media_count": 2,
        "media": [
            {"vault_item_id": "v1", "order": 0},
            {"vault_item_id": "v2", "order": 1},
        ],
    }


class TestIntentAdoption:
    def _key(self):
        from commerce.vault_sets import drop_content_key

        return drop_content_key(
            creator_id=1, vault_item_ids=["v1", "v2"],
            price_minor=2500, currency="USD", allow_download=True,
        )

    @pytest.mark.asyncio
    async def test_valid_adoption_activates_without_post(self, monkeypatch):
        from integrations.dropfans import service as svc

        key = self._key()
        row = _adopt_intent_row()
        row["content_key"] = key
        monkeypatch.setattr("db.drop_intents.get_intent", AsyncMock(return_value=row))
        monkeypatch.setattr(
            "integrations.dropfans.service.get_drop",
            AsyncMock(return_value=_adopt_live_drop()),
        )
        monkeypatch.setattr(
            "db.dropfans.upsert_dropfans_product", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "db.drop_intents.mark_intent_active",
            AsyncMock(return_value={"status": "active"}),
        )
        with patch("integrations.dropfans.client.DropfansClient.create_drop",
                   new=AsyncMock(side_effect=AssertionError("adoption must not POST"))):
            result = await svc.adopt_drop(1, content_key=key, dropfans_product_id="df_adopt_me")
        assert result["adopted"] is True
        assert result["product_id"] == "df_adopt_me"

    @pytest.mark.asyncio
    async def test_creator_mismatch_rejected(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError

        monkeypatch.setattr("db.drop_intents.get_intent", AsyncMock(return_value=None))
        with patch("integrations.dropfans.service.get_drop",
                   new=AsyncMock(side_effect=AssertionError("must not reach provider"))):
            with pytest.raises(DropfansError):
                await svc.adopt_drop(2, content_key="k", dropfans_product_id="df_x")

    @pytest.mark.asyncio
    async def test_invalid_cuid_rejected(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansValidationError

        with pytest.raises(DropfansValidationError):
            await svc.adopt_drop(1, content_key="k", dropfans_product_id="   ")

    @pytest.mark.asyncio
    async def test_synthetic_id_rejected(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansValidationError

        key = self._key()
        row = _adopt_intent_row()
        row["content_key"] = key
        monkeypatch.setattr("db.drop_intents.get_intent", AsyncMock(return_value=row))
        monkeypatch.setattr(
            "db.dropfans.find_synthetic_product",
            AsyncMock(return_value={"id": 5155, "creator_id": 1}),
        )
        with pytest.raises(DropfansValidationError, match="synthetic"):
            await svc.adopt_drop(1, content_key=key, dropfans_product_id="5155")

    @pytest.mark.asyncio
    async def test_mismatched_drop_rejected(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError

        key = self._key()
        row = _adopt_intent_row()
        row["content_key"] = key
        monkeypatch.setattr("db.drop_intents.get_intent", AsyncMock(return_value=row))
        live = _adopt_live_drop()
        live["price"] = 99.0  # wrong price
        monkeypatch.setattr(
            "integrations.dropfans.service.get_drop", AsyncMock(return_value=live))
        monkeypatch.setattr(
            "db.drop_intents.mark_intent_active",
            AsyncMock(side_effect=AssertionError("mismatch must not activate")),
        )
        with pytest.raises(DropfansError, match="price"):
            await svc.adopt_drop(1, content_key=key, dropfans_product_id="df_adopt_me")

    @pytest.mark.asyncio
    async def test_unverifiable_drop_rejected(self, monkeypatch):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import DropfansError, DropfansNotFoundError

        key = self._key()
        row = _adopt_intent_row()
        row["content_key"] = key
        monkeypatch.setattr("db.drop_intents.get_intent", AsyncMock(return_value=row))

        async def _missing(cid, cuid):
            raise DropfansNotFoundError("get_drop", "not found", 404)

        monkeypatch.setattr(
            "integrations.dropfans.service.get_drop", AsyncMock(side_effect=_missing))
        with pytest.raises(DropfansError, match="not verifiable"):
            await svc.adopt_drop(1, content_key=key, dropfans_product_id="df_ghost")


# ── P3.2C F3: dashboard offer routes ────────────────────────────────────


class TestDashboardOfferRoutes:
    def _auth(self):
        return {"username": "operator"}

    @pytest.mark.asyncio
    async def test_create_offer_never_fakes_success(self, monkeypatch):
        from chatbotv2.dashboard.routes import fangate as routes

        monkeypatch.setattr(
            routes, "_require_creator", AsyncMock(return_value=None))
        request = MagicMock()
        request.json = AsyncMock(return_value={"user_id": 5, "price": 2500})
        response = await routes.api_fangate_create_offer(1, 5155, request, self._auth())
        assert response.status_code == 501
        body = __import__("json").loads(response.body.decode())
        assert "id" not in body or body.get("id") != 0
        assert body.get("error") == "NotImplemented"

    @pytest.mark.asyncio
    async def test_update_offer_never_fakes_success(self, monkeypatch):
        from chatbotv2.dashboard.routes import fangate as routes

        monkeypatch.setattr(
            routes, "_require_creator", AsyncMock(return_value=None))
        request = MagicMock()
        request.json = AsyncMock(return_value={"price": 999})
        response = await routes.api_fangate_update_offer(1, 5155, 42, request, self._auth())
        assert response.status_code == 501
        body = __import__("json").loads(response.body.decode())
        assert body.get("ok") is not True

    @pytest.mark.asyncio
    async def test_delete_offer_never_fakes_success(self, monkeypatch):
        from chatbotv2.dashboard.routes import fangate as routes

        monkeypatch.setattr(
            routes, "_require_creator", AsyncMock(return_value=None))
        response = await routes.api_fangate_delete_offer(1, 5155, 42, self._auth())
        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_send_offer_never_fakes_success(self, monkeypatch):
        from chatbotv2.dashboard.routes import fangate as routes

        monkeypatch.setattr(
            routes, "_require_creator", AsyncMock(return_value=None))
        response = await routes.api_fangate_send_offer(1, 5155, 42, self._auth())
        assert response.status_code == 501

    def test_no_route_mutates_immutable_facts(self):
        import inspect

        from chatbotv2.dashboard.routes import fangate as routes

        for fn_name in (
            "api_fangate_create_offer",
            "api_fangate_update_offer",
            "api_fangate_delete_offer",
            "api_fangate_send_offer",
        ):
            src = inspect.getsource(getattr(routes, fn_name))
            assert "cdao.create_offer" not in src
            assert "cdao.update_offer" not in src
            assert "UPDATE commerce_offers" not in src
            assert "501" in src

    @pytest.mark.asyncio
    async def test_adopt_route_requires_confirmation(self, monkeypatch):
        from chatbotv2.dashboard.routes import fangate as routes

        monkeypatch.setattr(
            routes, "_require_creator", AsyncMock(return_value=None))
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "content_key": "k", "dropfans_product_id": "df_x"})
        response = await routes.api_dropfans_adopt_intent(1, request, self._auth())
        assert response.status_code == 400
