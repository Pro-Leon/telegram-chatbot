"""P3.1 — Bounded remediation for audit findings F-01..F-04 + F-07.

Covers:
  F-04: sale insert-vs-duplicate semantics (event/count only on insert).
  F-01/F-03: attribution-divergence naming, metric, bounded production caller.
  F-02: operator-gated vault sync + selection-config routes (auth/scope/bounds).
  F-07: stale cleanup only on complete sync, unknown-moderation exclusion,
        unindexed fail-open to existing selection.

All external/DB interaction is mocked — no live Dropfans writes, no migrations
applied, no production data touched.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _pool_with_conn(conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ── F-04: sale idempotency ────────────────────────────────────────────────────


class TestF04SaleIdempotency:
    @pytest.mark.asyncio
    async def test_first_observation_inserts_returns_true_emits_once(self):
        from db import dropfans as ddb

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"transaction_id": "dropfans:s1"})
        with patch.object(ddb, "get_pool", AsyncMock(return_value=pool)):
            with patch("core.event_bus.publish_event", AsyncMock()) as publish:
                result = await ddb.record_dropfans_sale(
                    1, dropfans_product_id="df_p1", sale_amount_cents=500
                )
        assert result is True
        publish.assert_awaited_once()
        assert publish.call_args[0][0] == "commerce.sale_recorded"

    @pytest.mark.asyncio
    async def test_second_observation_returns_false_emits_nothing(self):
        from db import dropfans as ddb

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)  # ON CONFLICT DO NOTHING
        with patch.object(ddb, "get_pool", AsyncMock(return_value=pool)):
            with patch("core.event_bus.publish_event", AsyncMock()) as publish:
                result = await ddb.record_dropfans_sale(
                    1, dropfans_product_id="df_p1", sale_amount_cents=500
                )
        assert result is False
        publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconcile_sales_counts_only_inserts(self):
        from integrations.dropfans import service as svc

        sales = [
            {"product_id": "p1", "paid": True, "sale_amount_cents": 500,
             "buyer_email": "a@x.io", "sale_id": None, "paid_at": None},
            {"product_id": "p1", "paid": True, "sale_amount_cents": 500,
             "buyer_email": "a@x.io", "sale_id": None, "paid_at": None},
        ]
        with patch.object(svc, "poll_sales", AsyncMock(return_value=sales)):
            with patch.object(
                svc.ddb, "record_dropfans_sale", AsyncMock(side_effect=[True, False])
            ):
                with patch.object(
                    svc, "get_earnings", AsyncMock(return_value={"transactions": []})
                ):
                    result = await svc.reconcile_sales(1)
        assert result["newly_recorded"] == 1
        assert result["already_recorded"] == 1

    def test_insert_preserves_conflict_target_and_fields(self):
        import pathlib

        src = pathlib.Path("db/dropfans.py").read_text(encoding="utf-8")
        fn_src = src[src.index("async def record_dropfans_sale"):]
        fn_src = fn_src[: fn_src.index("\n\n\nasync def")]
        assert "ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING" in fn_src
        assert "RETURNING transaction_id" in fn_src
        assert "product_id" in fn_src
        assert "buyer_email" in fn_src


# ── F-01/F-03: attribution divergence ─────────────────────────────────────────


class TestF01AttributionDivergence:
    def _pool(self, gross, net):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[gross, net, 0, gross, net, 0])
        return _pool_with_conn(conn)

    @pytest.mark.asyncio
    async def test_metric_name_is_attribution_not_refund(self):
        from commerce import reconciliation as rec

        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=self._pool(4, 2))):
            with patch("commerce.production_control.record_metric", return_value={}) as metric:
                result = await rec.compute_attribution_divergence(1, product_id=5, day="2026-01-01")
        assert result["divergence"] == 2
        assert metric.call_args[1]["name"] == "commerce.attribution_divergence"
        assert "refund" not in metric.call_args[1]["name"]

    @pytest.mark.asyncio
    async def test_wrapper_delegates_without_new_semantics(self):
        from commerce import reconciliation as rec

        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=self._pool(2, 2))):
            with patch("commerce.production_control.record_metric", return_value={}) as metric:
                wrapped = await rec.compute_refund_divergence(1, product_id=5, day="2026-01-01")
                direct = await rec.compute_attribution_divergence(1, product_id=5, day="2026-01-01")
        assert wrapped == direct
        assert metric.call_args[1]["name"] == "commerce.attribution_divergence"

    @pytest.mark.asyncio
    async def test_dimensions_creator_product_day(self):
        from commerce import reconciliation as rec

        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=self._pool(2, 1))):
            with patch("commerce.production_control.record_metric", return_value={}) as metric:
                await rec.compute_attribution_divergence(7, product_id=9, day="2026-02-02")
        kwargs = metric.call_args[1]
        assert kwargs["creator_id"] == 7
        assert kwargs["product_family"] == "product:9"
        assert kwargs["topic"] == "2026-02-02"

    @pytest.mark.asyncio
    async def test_helper_is_read_only(self):
        import pathlib

        src = pathlib.Path("commerce/reconciliation.py").read_text(encoding="utf-8")
        fn_src = src[src.index("async def compute_attribution_divergence"):]
        fn_src = fn_src[: fn_src.index("\n\n\nasync def")]
        assert "UPDATE " not in fn_src
        assert "DELETE " not in fn_src
        assert "INSERT " not in fn_src
        assert "funnel_stage" not in fn_src
        assert "first_purchase_at" not in fn_src

    def test_semantics_documented_as_not_refunds(self):
        import commerce.reconciliation as rec

        doc = rec.compute_attribution_divergence.__doc__ or ""
        assert "NOT a refund" in doc
        assert "chargeback" in doc

    @pytest.mark.asyncio
    async def test_snapshot_bounded_distinct_scopes(self):
        from commerce import reconciliation as rec

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"creator_id": 1, "product_id": 5, "day": "2026-01-01"},
            {"creator_id": 1, "product_id": 5, "day": "2026-01-01"},  # dup scope
            {"creator_id": 1, "product_id": 6, "day": "2026-01-01"},
        ])
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=_pool_with_conn(conn))):
            with patch.object(rec, "compute_attribution_divergence", AsyncMock(return_value={})) as helper:
                emitted = await rec.emit_attribution_divergence_snapshot(limit=5)
        assert emitted == 2
        assert helper.await_count == 2

    @pytest.mark.asyncio
    async def test_snapshot_respects_limit(self):
        from commerce import reconciliation as rec

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"creator_id": 1, "product_id": i, "day": "2026-01-01"} for i in range(10)
        ])
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=_pool_with_conn(conn))):
            with patch.object(rec, "compute_attribution_divergence", AsyncMock(return_value={})) as helper:
                emitted = await rec.emit_attribution_divergence_snapshot(limit=2)
        assert emitted == 2
        assert helper.await_count == 2

    @pytest.mark.asyncio
    async def test_snapshot_db_failure_returns_zero(self):
        from commerce import reconciliation as rec

        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=Exception("db down"))
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=_pool_with_conn(conn))):
            emitted = await rec.emit_attribution_divergence_snapshot(limit=5)
        assert emitted == 0

    def test_snapshot_makes_no_dropfans_calls(self):
        import pathlib

        src = pathlib.Path("commerce/reconciliation.py").read_text(encoding="utf-8")
        fn_src = src[src.index("async def emit_attribution_divergence_snapshot"):]
        fn_src = fn_src[: fn_src.index("\n\n\nasync def")]
        assert "get_earnings" not in fn_src
        assert "check_drop_status" not in fn_src
        assert "check_sale_now" not in fn_src
        assert "service" not in fn_src

    @pytest.mark.asyncio
    async def test_reconcile_all_calls_snapshot(self):
        from commerce import reconciliation as rec

        with patch.object(rec, "reconcile_dropfans_sales", AsyncMock(return_value=2)):
            with patch.object(rec, "reconcile_unattributed_purchases", AsyncMock(return_value=3)):
                with patch.object(rec, "recover_incomplete_post_purchases", AsyncMock(return_value=1)):
                    with patch.object(rec, "emit_attribution_divergence_snapshot", AsyncMock(return_value=2)) as snap:
                        total = await rec.reconcile_all()
        assert total == 6
        snap.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snapshot_failure_does_not_fail_reconciliation(self):
        from commerce import reconciliation as rec

        with patch.object(rec, "reconcile_dropfans_sales", AsyncMock(return_value=1)):
            with patch.object(rec, "reconcile_unattributed_purchases", AsyncMock(return_value=1)):
                with patch.object(rec, "recover_incomplete_post_purchases", AsyncMock(return_value=0)):
                    with patch.object(
                        rec, "emit_attribution_divergence_snapshot",
                        AsyncMock(side_effect=Exception("metric down")),
                    ):
                        total = await rec.reconcile_all()
        assert total == 2


# ── F-02: operator-gated vault controls ───────────────────────────────────────


@pytest.fixture
def app_client():
    from chatbotv2.dashboard.app import app
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def mock_auth():
    with patch("chatbotv2.dashboard.auth.verify_session") as mock:
        mock.return_value = {"username": "admin"}
        yield mock


@pytest.fixture
def mock_creator():
    with patch("chatbotv2.dashboard.routes.fangate._require_creator") as mock:
        mock.return_value = None
        yield mock


class TestF02VaultSyncRoute:
    @pytest.mark.asyncio
    async def test_sync_requires_auth(self, app_client):
        resp = await app_client.post(
            "/api/fangate/creators/1/dropfans-vault-sync", json={"max_pages": 2}
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_sync_invokes_bounded_sync(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.sync_vault_index", AsyncMock()) as mock_sync:
            mock_sync.return_value = {"synced": 4, "pages": 1, "complete": True, "pruned": 0}
            resp = await app_client.post(
                "/api/fangate/creators/7/dropfans-vault-sync",
                json={"max_pages": 3},
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 200
        mock_sync.assert_awaited_once_with(7, max_pages=3)
        assert resp.json()["sync"]["synced"] == 4
        mock_creator.assert_called_once()
        assert mock_creator.call_args[0][0] == 7

    @pytest.mark.asyncio
    async def test_sync_max_pages_clamped(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.sync_vault_index", AsyncMock(return_value={})) as mock_sync:
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-vault-sync",
                json={"max_pages": 500},
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 200
        assert mock_sync.call_args[1]["max_pages"] == 20

    @pytest.mark.asyncio
    async def test_sync_failure_returns_500_without_leak(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.sync_vault_index", AsyncMock(side_effect=Exception("boom"))):
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-vault-sync",
                json={},
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 500
        assert "api_key" not in resp.text.lower()


class TestF02SelectionConfigRoute:
    @pytest.mark.asyncio
    async def test_config_requires_auth(self, app_client):
        assert (await app_client.get("/api/fangate/creators/1/dropfans-selection-config")).status_code == 401
        assert (await app_client.post(
            "/api/fangate/creators/1/dropfans-selection-config", json={}
        )).status_code == 401

    @pytest.mark.asyncio
    async def test_config_get_returns_current(self, app_client, mock_auth, mock_creator):
        cfg = {"creator_id": 1, "allowed_folders": [], "allowed_tags": [], "hard_mode": False}
        with patch("db.dropfans.get_selection_config", AsyncMock(return_value=cfg)):
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-selection-config",
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 200
        assert resp.json()["config"] == cfg

    @pytest.mark.asyncio
    async def test_config_set_validates_and_persists(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.set_selection_config", AsyncMock()) as mock_set:
            mock_set.return_value = {
                "creator_id": 3, "allowed_folders": ["ok"], "allowed_tags": ["beach"], "hard_mode": True,
            }
            resp = await app_client.post(
                "/api/fangate/creators/3/dropfans-selection-config",
                json={"allowed_folders": ["ok"], "allowed_tags": ["Beach"], "hard_mode": True},
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 200
        mock_set.assert_awaited_once()
        assert mock_set.call_args[0][0] == 3
        assert mock_set.call_args[1]["allowed_folders"] == ["ok"]

    @pytest.mark.asyncio
    async def test_config_rejects_oversized_allowlists(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.set_selection_config", AsyncMock()) as mock_set:
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-selection-config",
                json={"allowed_folders": [f"f{i}" for i in range(51)]},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 400
            resp2 = await app_client.post(
                "/api/fangate/creators/1/dropfans-selection-config",
                json={"allowed_tags": ["x" * 65]},
                cookies={"session": "valid_token"},
            )
            assert resp2.status_code == 400
        mock_set.assert_not_awaited()

    def test_validate_allowlist_rejects_wrong_types(self):
        from chatbotv2.dashboard.routes.fangate import _validate_allowlist

        with pytest.raises(TypeError):
            _validate_allowlist("notalist", name="allowed_folders", max_items=50, max_len=64)
        with pytest.raises(TypeError):
            _validate_allowlist([123], name="allowed_tags", max_items=100, max_len=64)
        with pytest.raises(ValueError):
            _validate_allowlist(["x" * 65], name="allowed_tags", max_items=100, max_len=64)
        assert _validate_allowlist(None, name="allowed_tags", max_items=100, max_len=64) == []

    @pytest.mark.asyncio
    async def test_config_response_carries_no_secrets(self, app_client, mock_auth, mock_creator):
        with patch("db.dropfans.set_selection_config", AsyncMock(return_value={
            "creator_id": 1, "allowed_folders": [], "allowed_tags": [], "hard_mode": False,
        })):
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-selection-config",
                json={},
                cookies={"session": "valid_token"},
            )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "api_key" not in body
        assert "secret" not in body


# ── F-07: vault safety hardening ──────────────────────────────────────────────


class TestF07ModerationFailClosed:
    def test_unknown_moderation_excluded(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "mystery", "tags": ["beach"], "folder_id": "f"},
            {"vault_item_id": "known", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_tags=["beach"])
        assert [c.vault_item_id for c, _ in ranked] == ["known"]

    def test_explicit_none_moderation_excluded(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "x", "tags": [], "folder_id": "f", "moderation_status": None},
        ]
        assert rank_vault_candidates(cands) == []

    def test_lowercase_approved_still_selectable(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "x", "tags": [], "folder_id": "f", "moderation_status": "approved"},
        ]
        assert [c.vault_item_id for c, _ in rank_vault_candidates(cands)] == ["x"]

    def test_pending_and_rejected_excluded(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "p", "tags": [], "folder_id": "f", "moderation_status": "PENDING"},
            {"vault_item_id": "r", "tags": [], "folder_id": "f", "moderation_status": "REJECTED"},
            {"vault_item_id": "a", "tags": [], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        assert [c.vault_item_id for c, _ in rank_vault_candidates(cands)] == ["a"]


class TestF07SyncCleanup:
    def _item(self, vid):
        item = MagicMock()
        item.id = vid
        item.content_tags = ["beach"]
        item.folder_id = "f"
        item.folder_name = "F"
        item.moderation_status = "APPROVED"
        item.file_type = "image"
        return item

    def _result(self, items, has_more=False):
        res = MagicMock()
        res.items = items
        res.has_more = has_more
        return res

    @pytest.mark.asyncio
    async def test_complete_sync_prunes_missing_rows(self):
        from db import dropfans as ddb

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 2")
        with patch.object(ddb, "get_pool", AsyncMock(return_value=pool)):
            with patch.object(ddb, "upsert_vault_index", AsyncMock()):
                with patch(
                    "integrations.dropfans.service.list_vault_items",
                    AsyncMock(return_value=self._result([self._item("a"), self._item("b")])),
                ):
                    out = await ddb.sync_vault_index(1, max_pages=5)
        assert out["complete"] is True
        assert out["synced"] == 2
        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        assert args[0].strip().upper().startswith("DELETE FROM DROPFANS_VAULT_INDEX")
        assert args[1] == 1
        assert sorted(args[2]) == ["a", "b"]
        assert out["pruned"] == 2

    @pytest.mark.asyncio
    async def test_partial_sync_never_deletes(self):
        from db import dropfans as ddb

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        with patch.object(ddb, "get_pool", AsyncMock(return_value=pool)):
            with patch.object(ddb, "upsert_vault_index", AsyncMock()):
                with patch(
                    "integrations.dropfans.service.list_vault_items",
                    AsyncMock(return_value=self._result([self._item("a")], has_more=True)),
                ):
                    out = await ddb.sync_vault_index(1, max_pages=1)
        assert out["complete"] is False
        pool.execute.assert_not_awaited()
        assert out["pruned"] == 0


class TestF07UnindexedFailOpen:
    def _product(self, pid, vault_ids, price=1000):
        return {
            "id": pid,
            "title": f"Product {pid}",
            "price_minor": price,
            "is_accessible": True,
            "sales_url": f"https://www.dropfans.io/buy/p{pid}",
            "raw": {"vaultItemIds": list(vault_ids)},
        }

    @pytest.mark.asyncio
    async def test_unindexed_products_follow_existing_rules(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v9"], price=500)]
        index = [
            {"vault_item_id": "v1", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={
                    "allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": False,
                })):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        with patch("commerce.ownership.fetch_owned_vault_ids", AsyncMock(return_value=frozenset())):
                            result = await ps.resolve_commerce_product_with_history(1, 42)
        # v9 has no index row: must not vanish; cheapest existing rule governs.
        assert result == 2

    @pytest.mark.asyncio
    async def test_indexed_unsatisfying_still_excluded_in_soft_union(self):
        from commerce import product_selection as ps

        products = [
            self._product(1, ["v1"], price=1000),
            self._product(2, ["v2"], price=500),
            self._product(3, ["v9"], price=100),
        ]
        index = [
            {"vault_item_id": "v1", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "v2", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={
                    "allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": False,
                })):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        with patch("commerce.ownership.fetch_owned_vault_ids", AsyncMock(return_value=frozenset())):
                            result = await ps.resolve_commerce_product_with_history(1, 42)
        # v2 is indexed-but-unsatisfying (excluded); v1 matched, v3 unindexed.
        # Cheapest of {v1, v3} under existing rules wins.
        assert result == 3

    @pytest.mark.asyncio
    async def test_hard_mode_uses_only_matched(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v9"], price=100)]
        index = [
            {"vault_item_id": "v1", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={
                    "allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": True,
                })):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        with patch("commerce.ownership.fetch_owned_vault_ids", AsyncMock(return_value=frozenset())):
                            result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 1

    @pytest.mark.asyncio
    async def test_index_rows_alone_never_filter_without_config(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v2"], price=500)]
        index = [
            {"vault_item_id": "v1", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={
                    "allowed_folders": [], "allowed_tags": [], "hard_mode": False,
                })):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 2
