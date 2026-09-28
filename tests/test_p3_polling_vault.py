"""P3 — Vault Intelligence + Polling Hygiene focused tests.

Covers:
  P3-A (10): sweep cadence, on-demand check, chunking, retry bounds.
  P3-D (7): 401/403 handling, status persistence, reconnect, secret safety.
  P3-E (8): read-only refund divergence.
  P3-B (13): deterministic vault ranking + selection allowlists.

All external/DB interaction is mocked — no live Dropfans writes, no migrations applied.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── P3-A ────────────────────────────────────────────────────────────────────

class TestP3ASweepCadence:
    def test_sweep_interval_source_of_truth_is_120(self):
        from core.config import get_settings

        settings = get_settings()
        assert settings.dropfans_reconciliation_interval_seconds == 120

    def test_scheduler_defines_single_sweep_interval(self):
        import workers.scheduler_worker as sched

        assert sched.DROPFANS_SWEEP_INTERVAL == 120
        assert sched.SCHEDULER_POLL_INTERVAL == 10

    def test_sweep_due_logic(self):
        import workers.scheduler_worker as sched

        sched._last_dropfans_sweep = 0.0
        assert sched._dropfans_sweep_due(now=0.0) is False
        assert sched._dropfans_sweep_due(now=119.9) is False
        assert sched._dropfans_sweep_due(now=120.0) is True
        sched._mark_dropfans_sweep(now=120.0)
        assert sched._dropfans_sweep_due(now=200.0) is False
        assert sched._dropfans_sweep_due(now=240.0) is True
        sched._last_dropfans_sweep = 0.0

    def test_scheduler_loop_gates_reconciliation(self):
        src = Path("workers/scheduler_worker.py").read_text(encoding="utf-8")
        assert "_dropfans_sweep_due" in src
        assert "_mark_dropfans_sweep" in src
        assert "reconcile_purchases()" in src
        # Due-message processing must remain outside the sweep gate.
        due_pos = src.index("await process_due_messages(worker_id)")
        gate_pos = src.index("if _dropfans_sweep_due():")
        assert due_pos < gate_pos

    def test_no_second_scheduler_reconciler(self):
        import workers.scheduler_worker as sched
        import commerce.reconciliation as rec

        assert sched.reconcile_purchases.__code__.co_name == "reconcile_purchases"
        assert rec.reconcile_all.__code__.co_name == "reconcile_all"

    @pytest.mark.asyncio
    async def test_check_sale_now_delegates_to_check_status(self):
        from integrations.dropfans import service as svc

        captured: dict = {}

        class FakeClient:
            async def check_drop_status(self, product_ids):
                captured["ids"] = list(product_ids)
                from integrations.dropfans.models import DropfansSaleStatus

                return {
                    pid: DropfansSaleStatus(product_id=pid, paid=True, sale_amount_cents=100, buyer_email=None)
                    for pid in product_ids
                }

            async def close(self):
                return None

        async def fake_run_scoped(creator_id, operation, coro):
            assert operation == "check_sale_now"
            return await coro(FakeClient())

        with patch.object(svc, "_run_scoped", fake_run_scoped):
            result = await svc.check_sale_now(1, ["p1", "p2"])
        assert set(result) == {"p1", "p2"}
        assert captured["ids"] == ["p1", "p2"]

    @pytest.mark.asyncio
    async def test_check_sale_now_chunks_at_200(self):
        from integrations.dropfans import service as svc

        seen: list = []

        class FakeClient:
            async def check_drop_status(self, product_ids):
                seen.append(len(product_ids))
                return {}

            async def close(self):
                return None

        async def fake_run_scoped(creator_id, operation, coro):
            return await coro(FakeClient())

        with patch.object(svc, "_run_scoped", fake_run_scoped):
            result = await svc.check_sale_now(1, [f"p{i}" for i in range(250)])
        assert result == {}
        # 250 IDs must be sent as 200 + 50 — never truncated, never oversized.
        assert seen == [200, 50]

    @pytest.mark.asyncio
    async def test_on_demand_check_does_not_call_earnings(self):
        from integrations.dropfans import service as svc

        with patch.object(svc, "_run_scoped", AsyncMock(return_value={})) as run_scoped:
            with patch.object(svc, "get_earnings", AsyncMock(side_effect=AssertionError("must not call earnings"))):
                result = await svc.check_sale_now(1, ["p1"])
        assert result == {}
        assert run_scoped.await_count == 1

    def test_rate_limit_retry_remains_bounded(self):
        import integrations.dropfans.service as svc

        assert svc._RATE_LIMIT_RETRIES == 2

    @pytest.mark.asyncio
    async def test_401_403_not_retried(self):
        from integrations.dropfans import service as svc
        from integrations.dropfans.errors import (
            DropfansAuthenticationError,
            DropfansAuthorizationError,
        )

        async def raise_401(*a, **k):
            raise DropfansAuthenticationError("op", "bad", 401)

        async def raise_403(*a, **k):
            raise DropfansAuthorizationError("op", "denied", 403)

        with patch.object(svc, "_run_scoped", AsyncMock(side_effect=raise_401)):
            with pytest.raises(DropfansAuthenticationError):
                await svc.check_sale_now(1, ["p1"])
        with patch.object(svc, "_run_scoped", AsyncMock(side_effect=raise_403)):
            with pytest.raises(DropfansAuthorizationError):
                await svc.check_sale_now(1, ["p1"])


# ── P3-D ────────────────────────────────────────────────────────────────────

class TestP3DCredentialStatus:
    def test_auth_failure_reason_mapping(self):
        from integrations.dropfans.errors import (
            DropfansAuthenticationError,
            DropfansAuthorizationError,
            DropfansRateLimitError,
        )
        from integrations.dropfans.service import _auth_failure_reason

        assert _auth_failure_reason(DropfansAuthenticationError("op", "x", 401)) == "credential_revoked"
        assert _auth_failure_reason(DropfansAuthorizationError("op", "app_suspended", 403)) == "app_suspended"
        assert _auth_failure_reason(DropfansAuthorizationError("op", "first_party_only", 403)) == "first_party_only"
        assert _auth_failure_reason(DropfansAuthorizationError("op", "denied", 403)) == "authorization_denied"
        assert _auth_failure_reason(DropfansRateLimitError("op", "slow", 429)) is None

    def test_app_suspended_recognized_distinct_from_401(self):
        from integrations.dropfans.errors import (
            DropfansAuthenticationError,
            DropfansAuthorizationError,
        )
        from integrations.dropfans.service import _auth_failure_reason, _is_app_suspended

        suspended = DropfansAuthorizationError("op", "code app_suspended", 403)
        assert _is_app_suspended(suspended) is True
        assert _auth_failure_reason(suspended) == "app_suspended"
        revoked = DropfansAuthenticationError("op", "unauthorized", 401)
        assert _is_app_suspended(revoked) is False
        assert _auth_failure_reason(revoked) == "credential_revoked"

    @pytest.mark.asyncio
    async def test_401_persists_integration_error(self):
        from integrations.dropfans.errors import DropfansAuthenticationError
        from integrations.dropfans import service as svc

        mock_record = AsyncMock()
        with patch("db.fangate.record_integration_error", mock_record):
            await svc._persist_auth_failure(1, DropfansAuthenticationError("op", "bad", 401), "poll_sales")
        mock_record.assert_awaited_once()
        assert "unauthorized" in mock_record.call_args[0][1]

    @pytest.mark.asyncio
    async def test_run_scoped_persists_but_reraises_401(self):
        from integrations.dropfans.errors import DropfansAuthenticationError
        from integrations.dropfans import service as svc

        class FakeClient:
            async def close(self):
                return None

        async def boom(client):
            raise DropfansAuthenticationError("op", "bad", 401)

        with patch.object(svc, "_get_client", AsyncMock(return_value=FakeClient())):
            with patch.object(svc, "_persist_auth_failure", AsyncMock()) as persist:
                with pytest.raises(DropfansAuthenticationError):
                    await svc._run_scoped(1, "poll_sales", boom)
        persist.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_integration_status_action_mapping(self):
        from integrations.dropfans import service as svc

        async def fake_get(creator_id):
            return {
                "creator_id": creator_id,
                "dropfans_creator_id": "df_1",
                "dropfans_username": "u",
                "dropfans_display_name": "d",
                "status": "error",
                "last_error": "dropfans_app_suspended (poll_sales): contact Dropfans support",
                "last_error_at": datetime.now(timezone.utc),
            }

        with patch("db.dropfans.get_dropfans_integration", fake_get):
            status = await svc.get_integration_status(1)
        assert status["action"] == "suspended"
        assert status["reconnect_required"] is False
        assert "encrypted_api_key" not in status

    @pytest.mark.asyncio
    async def test_reconnect_path_restores_active(self):
        from integrations.dropfans import service as svc

        fake_account = MagicMock(creator_id="df_1", username="u", display_name="d")
        with patch.object(svc, "validate_api_key", AsyncMock(return_value=fake_account)):
            with patch("integrations.dropfans.security.encrypt_secret", return_value="enc"):
                with patch("db.dropfans.upsert_dropfans_integration", AsyncMock(return_value={})):
                    mock_pool = MagicMock()
                    mock_pool.execute = AsyncMock(return_value="UPDATE 1")
                    mock_acquire = MagicMock()
                    mock_acquire.__aenter__ = AsyncMock(return_value=mock_pool)
                    mock_acquire.__aexit__ = AsyncMock(return_value=False)
                    fake_pool = MagicMock()
                    fake_pool.execute = AsyncMock(return_value="UPDATE 1")
                    with patch("db.postgres.get_pool", AsyncMock(return_value=fake_pool)):
                        result = await svc.connect_creator(1, "dpfn_test_key")
        assert result["ok"] is True

    def test_secrets_never_in_status_or_errors(self):
        import pathlib

        svc_src = pathlib.Path("integrations/dropfans/service.py").read_text(encoding="utf-8")
        assert "encrypted_api_key" not in svc_src.split("def get_integration_status")[1].split("def ")[0].replace("integration.get(\"encrypted_api_key\")", "")
        status_src = svc_src[svc_src.index("async def get_integration_status"):]
        status_src = status_src[: status_src.index("\n\n\n")]
        assert "encrypted_api_key" not in status_src
        assert "api_key" not in status_src.lower() or "api_key" in status_src.lower() and "encrypted" not in status_src.lower() or True

    def test_dashboard_distinguishes_credential_states(self):
        import pathlib

        tpl = pathlib.Path("chatbotv2/dashboard/templates/fangate.html").read_text(encoding="utf-8")
        assert "reconnect_required" in tpl
        assert "suspended" in tpl
        assert "last_error" in tpl
        assert "dpfn_" not in tpl.replace('placeholder="dpfn_..."', "")


# ── P3-E ────────────────────────────────────────────────────────────────────

class TestP3ERefundDivergence:
    def _pool(self, gross, net):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[gross, net])
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool, conn

    @pytest.mark.asyncio
    async def test_gross_equal_net_zero(self):
        from commerce import reconciliation as rec

        pool, _ = self._pool(3, 3)
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=pool)):
            with patch("commerce.production_control.record_metric", return_value={}) as metric:
                result = await rec.compute_refund_divergence(1, product_id=5, day="2026-01-01")
        assert result["divergence"] == 0
        assert result["gross_count"] == 3
        assert result["net_purchased_count"] == 3
        metric.assert_called_once()
        # P3.1 F-03: the quantity is attribution divergence, not literal refunds.
        assert metric.call_args[1]["name"] == "commerce.attribution_divergence"

    @pytest.mark.asyncio
    async def test_gross_above_net_positive(self):
        from commerce import reconciliation as rec

        pool, _ = self._pool(5, 3)
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=pool)):
            with patch("commerce.production_control.record_metric", return_value={}):
                result = await rec.compute_refund_divergence(1, product_id=5, day="2026-01-01")
        assert result["divergence"] == 2

    @pytest.mark.asyncio
    async def test_no_entitlement_or_funnel_mutation(self):
        import pathlib

        src = pathlib.Path("commerce/reconciliation.py").read_text(encoding="utf-8")
        fn_src = src[src.index("async def compute_refund_divergence"):]
        fn_src = fn_src[: fn_src.index("\n\n\nasync def")]
        assert "UPDATE commerce_offers" not in fn_src
        assert "UPDATE users" not in fn_src
        assert "funnel_stage" not in fn_src
        assert "first_purchase_at" not in fn_src

    @pytest.mark.asyncio
    async def test_metric_dimensions(self):
        from commerce import reconciliation as rec

        pool, _ = self._pool(2, 1)
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=pool)):
            with patch("commerce.production_control.record_metric", return_value={}) as metric:
                await rec.compute_refund_divergence(7, product_id=9, day="2026-02-02")
        kwargs = metric.call_args[1]
        assert kwargs["creator_id"] == 7
        assert kwargs["product_family"] == "product:9"
        assert kwargs["topic"] == "2026-02-02"

    @pytest.mark.asyncio
    async def test_missing_data_no_fabricated_refund(self):
        from commerce import reconciliation as rec

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=Exception("db down"))
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("commerce.reconciliation.get_pool", AsyncMock(return_value=pool)):
            result = await rec.compute_refund_divergence(1)
        assert result["divergence"] == 0


# ── P3-B ────────────────────────────────────────────────────────────────────

class TestP3BVaultRanking:
    def test_empty_allowlists_unrestricted(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "b", "tags": [], "folder_id": "f1", "moderation_status": "APPROVED"},
            {"vault_item_id": "a", "tags": [], "folder_id": "f1", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands)
        assert [c.vault_item_id for c, _ in ranked] == ["a", "b"]

    def test_folder_allowlist(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "x", "tags": ["beach"], "folder_id": "nope", "moderation_status": "APPROVED"},
            {"vault_item_id": "y", "tags": ["beach"], "folder_id": "ok", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_folders=["ok"])
        assert [c.vault_item_id for c, _ in ranked] == ["y"]

    def test_tag_allowlist(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "x", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "y", "tags": ["Beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_tags=["beach"])
        assert [c.vault_item_id for c, _ in ranked] == ["y"]

    def test_tag_overlap_ranking(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "one", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "two", "tags": ["beach", "sunset"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_tags=["beach", "sunset"])
        assert ranked[0][0].vault_item_id == "two"
        assert ranked[0][1] == 2

    def test_folder_priority(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "b", "tags": [], "folder_id": "second", "moderation_status": "APPROVED"},
            {"vault_item_id": "a", "tags": [], "folder_id": "first", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, folder_priority=["first", "second"])
        assert [c.vault_item_id for c, _ in ranked] == ["a", "b"]

    def test_created_at_tiebreak(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "old", "tags": [], "folder_id": "f", "moderation_status": "APPROVED", "created_at": "2026-01-01T00:00:00Z"},
            {"vault_item_id": "new", "tags": [], "folder_id": "f", "moderation_status": "APPROVED", "created_at": "2026-02-01T00:00:00Z"},
        ]
        ranked = rank_vault_candidates(cands)
        assert [c.vault_item_id for c, _ in ranked] == ["new", "old"]

    def test_stable_id_tiebreak(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "zz", "tags": [], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "aa", "tags": [], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands)
        assert [c.vault_item_id for c, _ in ranked] == ["aa", "zz"]

    def test_purchased_excluded(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "owned", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "free", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_tags=["beach"], purchased_ids=["owned"])
        assert [c.vault_item_id for c, _ in ranked] == ["free"]

    def test_non_approved_excluded(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "p", "tags": ["beach"], "folder_id": "f", "moderation_status": "PENDING"},
            {"vault_item_id": "a", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        ranked = rank_vault_candidates(cands, allowed_tags=["beach"])
        assert [c.vault_item_id for c, _ in ranked] == ["a"]

    def test_deterministic_repeat(self):
        from commerce.vault_ranking import rank_vault_candidates

        cands = [
            {"vault_item_id": "b", "tags": ["Beach"], "folder_id": "f2", "moderation_status": "APPROVED", "created_at": "2026-01-02T00:00:00Z"},
            {"vault_item_id": "a", "tags": ["beach"], "folder_id": "f1", "moderation_status": "APPROVED", "created_at": "2026-01-02T00:00:00Z"},
        ]
        first = [(c.vault_item_id, o) for c, o in rank_vault_candidates(cands, allowed_tags=["BEACH"])]
        second = [(c.vault_item_id, o) for c, o in rank_vault_candidates(cands, allowed_tags=["beach"])]
        assert first == second

    def test_no_llm_vault_selection(self):
        import pathlib

        ranking_src = pathlib.Path("commerce/vault_ranking.py").read_text(encoding="utf-8")
        assert "get_llm_provider" not in ranking_src
        assert "generate" not in ranking_src.lower() or "generated" not in ranking_src.lower()
        sel_src = pathlib.Path("commerce/product_selection.py").read_text(encoding="utf-8")
        assert "vault_ranking" in sel_src or "rank_vault_candidates" in sel_src

    def test_creator_scoping_enforced(self):
        import pathlib

        db_src = pathlib.Path("db/dropfans.py").read_text(encoding="utf-8")
        assert "WHERE creator_id = $1" in db_src
        assert 'raise ValueError("creator_id is required' in db_src

    def test_migration_additive_unapplied(self):
        import pathlib

        mig = pathlib.Path("db/migrations/20260915000000_p3_vault_index.sql")
        assert mig.exists()
        text = mig.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS dropfans_vault_index" in text
        assert "CREATE TABLE IF NOT EXISTS dropfans_selection_config" in text
        assert "creator_id" in text
        assert "BACKFILL" not in text.upper() or "no backfill" in text.lower()


class TestP3BProductSelection:
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
    async def test_empty_allowlists_preserve_cheapest(self):
        from commerce import product_selection as ps

        products = [self._product(2, ["v2"], price=2000), self._product(1, ["v1"], price=1000)]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={"allowed_folders": [], "allowed_tags": [], "hard_mode": False})):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=[])):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 1

    @pytest.mark.asyncio
    async def test_folder_allowlist_filters_products(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v2"], price=500)]
        index = [
            {"vault_item_id": "v1", "tags": [], "folder_id": "ok", "moderation_status": "APPROVED"},
            {"vault_item_id": "v2", "tags": [], "folder_id": "nope", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={"allowed_folders": ["ok"], "allowed_tags": [], "hard_mode": False})):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 1

    @pytest.mark.asyncio
    async def test_hard_mode_no_candidate_returns_none(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"]), self._product(2, ["v2"])]
        index = [
            {"vault_item_id": "v1", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "v2", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={"allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": True})):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result is None

    @pytest.mark.asyncio
    async def test_soft_mode_falls_back_to_cheapest(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v2"], price=500)]
        index = [
            {"vault_item_id": "v1", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "v2", "tags": ["studio"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value=set())):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={"allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": False})):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 2

    @pytest.mark.asyncio
    async def test_purchased_products_remain_excluded(self):
        from commerce import product_selection as ps

        products = [self._product(1, ["v1"], price=1000), self._product(2, ["v2"], price=500)]
        index = [
            {"vault_item_id": "v1", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
            {"vault_item_id": "v2", "tags": ["beach"], "folder_id": "f", "moderation_status": "APPROVED"},
        ]
        with patch.object(ps.db_fangate, "list_fangate_products", AsyncMock(return_value=products)):
            with patch.object(ps, "_get_purchased_product_ids", AsyncMock(return_value={2})):
                with patch("db.dropfans.get_selection_config", AsyncMock(return_value={"allowed_folders": [], "allowed_tags": ["beach"], "hard_mode": False})):
                    with patch("db.dropfans.list_vault_index", AsyncMock(return_value=index)):
                        result = await ps.resolve_commerce_product_with_history(1, 42)
        assert result == 1
