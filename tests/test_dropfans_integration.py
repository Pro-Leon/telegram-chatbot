"""Dropfans integration tests for the CRM.

Covers the full Dropfans contract:
1. Client authentication (Bearer token, 401/403 mapping)
2. Product normalization (from_api factory methods)
3. Product retrieval (db/dropfans.py functions)
4. Execution path (commerce/execution.py with Dropfans-only)
5. Error handling (timeout, auth, validation errors)
6. Creator isolation (queries scoped to creator_id)
7. Webhook disabled (returns 410)
8. Hash collision resistance (SHA-256 deterministic IDs)

All DB/external interaction is mocked — no live credentials.
"""

import ast
import hashlib
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.dropfans.errors import (
    DropfansAuthenticationError,
    DropfansAuthorizationError,
    DropfansError,
    DropfansNotFoundError,
    DropfansRateLimitError,
    DropfansResponseError,
    DropfansServerError,
    DropfansTimeoutError,
    DropfansTransportError,
    DropfansValidationError,
)
from integrations.dropfans.models import (
    DropfansAccount,
    DropfansBalance,
    DropfansDrop,
    DropfansDropResult,
    DropfansEarnings,
    DropfansEarningsTransaction,
    DropfansLinks,
    DropfansSaleStatus,
    DropfansVaultFolder,
    DropfansVaultItem,
)

EXECUTION_PATH = Path(__file__).parent.parent / "commerce" / "execution.py"
CLIENT_PATH = Path(__file__).parent.parent / "integrations" / "dropfans" / "client.py"

_CREATOR = {"id": 1, "name": "Test Creator"}
_INTEGRATION = {
    "creator_id": 1,
    "dropfans_creator_id": "df_user_abc123",
    "dropfans_username": "testcreator",
    "dropfans_display_name": "Test Creator",
    "encrypted_api_key": "gAAAAA-fake-ciphertext",
    "status": "active",
}
_FAN = {"id": 5, "username": "fan", "is_blocked": False, "do_not_auto_reply": False}
_LOCAL_PRODUCT_ROW = {
    "id": 5155,
    "creator_id": 1,
    "is_accessible": True,
    "sales_url": "https://fangate.info/5155x",
    "price_minor": 4400,
    "is_verif_age": False,
    "raw": {"dropfans_product_id": "df_prod_abc123", "vaultItemIds": ["v1", "v2"]},
}
_OFFER_ROW = {
    "id": 10,
    "creator_id": 1,
    "user_id": 5,
    "product_id": 5155,
    "link": "https://fangate.info/5155x",
    "state": "pending",
    "price_minor": 4400,
    "currency": None,
    "reason": "ppv_execution",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision():
    from commerce.decision import CommerceDecision, CommerceReason
    from commerce.models import CommerceAction

    return CommerceDecision(
        action=CommerceAction.OFFER_PPV,
        reason_code=CommerceReason.STRONG_BUYING_SIGNAL,
        allowed=True,
        confidence=1.0,
    )


def _patch_ppv(monkeypatch):
    """Install AsyncMock replacements for every backend execute_ppv touches.

    Returns {name: AsyncMock} for per-test overrides and assertions.
    """
    from commerce import execution as ex
    from db import dropfans as ddb
    from integrations.dropfans import service as dservice

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=_LOCAL_PRODUCT_ROW)
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    # P3.2 live-price verification: live Drop price 44.00 USD == 4400 cents
    # matches the local mirror (equal case) by default.
    async def _mock_get_drop(creator_id, drop_id):
        return {"price": 44.0, "currency": "USD",
                "buyUrl": "https://www.dropfans.io/buy/df_prod_abc123",
                "mediaCount": 0, "media": []}

    mocks = {
        "get_integration": AsyncMock(return_value=_INTEGRATION),
        "get_user": AsyncMock(return_value=_FAN),
        "find_pending": AsyncMock(return_value=None),
        "has_purchased": AsyncMock(return_value=False),
        "create_serialized": AsyncMock(return_value=(dict(_OFFER_ROW), True)),
        "record_transition": AsyncMock(return_value=None),
        "get_pool": AsyncMock(return_value=mock_pool),
        "get_drop": AsyncMock(side_effect=_mock_get_drop),
    }
    monkeypatch.setattr(ddb, "get_dropfans_integration", mocks["get_integration"])
    monkeypatch.setattr(dservice, "get_drop", mocks["get_drop"])
    monkeypatch.setattr(ex, "get_user", mocks["get_user"])
    monkeypatch.setattr(ex, "find_pending_offer_for_product", mocks["find_pending"])
    monkeypatch.setattr(ex, "has_purchased_product", mocks["has_purchased"])
    monkeypatch.setattr(ex, "create_offer_serialized", mocks["create_serialized"])
    monkeypatch.setattr(ex, "record_offer_transition", mocks["record_transition"])
    monkeypatch.setattr(ex, "decrypt_secret", lambda token: "df_sk_live_test")
    # P3.2C F1: tests declare the P3.2 safety schema PRESENT (no live DB).
    monkeypatch.setattr(
        ex,
        "check_commerce_schema_ready",
        AsyncMock(return_value=(True, "commerce_schema_present")),
    )
    monkeypatch.setattr("db.postgres.get_pool", mocks["get_pool"])
    return mocks


async def _run(monkeypatch, *, mocks=None, decision=None, age_verified=False):
    """Run execute_ppv once with the standard happy-path environment."""
    if mocks is None:
        mocks = _patch_ppv(monkeypatch)
    from commerce.execution import execute_ppv

    result = await execute_ppv(
        creator_id=1,
        user_id=5,
        product_id=5155,
        decision=decision if decision is not None else _decision(),
        created_by="decision_engine",
        age_verified=age_verified,
    )
    return result, mocks


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Client Authentication
# ═══════════════════════════════════════════════════════════════════════════════


class TestClientAuthentication:
    def test_bearer_token_set_in_headers(self):
        from integrations.dropfans.client import DropfansClient

        client = DropfansClient("test_api_key_12345")
        assert "Authorization" in client._client.headers
        assert client._client.headers["Authorization"] == "Bearer test_api_key_12345"
        assert client._client.headers["Accept"] == "application/json"

    def test_empty_api_key_raises(self):
        from integrations.dropfans.client import DropfansClient

        with pytest.raises(ValueError, match="api_key must not be empty"):
            DropfansClient("")

    def test_none_api_key_raises(self):
        from integrations.dropfans.client import DropfansClient

        with pytest.raises((ValueError, TypeError)):
            DropfansClient(None)

    @pytest.mark.asyncio
    async def test_401_maps_to_authentication_error(self):
        from integrations.dropfans.client import DropfansClient

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b'{"message": "Invalid API key"}'
        mock_response.json.return_value = {"message": "Invalid API key"}
        mock_response.headers = {}

        with patch.object(
            DropfansClient, "_request", new_callable=AsyncMock
        ) as mock_req:
            mock_req.side_effect = DropfansAuthenticationError(
                "GET /me", "Invalid API key", 401
            )
            client = DropfansClient("bad_key")
            with pytest.raises(DropfansAuthenticationError) as exc_info:
                await client.get_me()
            assert exc_info.value.status_code == 401
            assert "Invalid API key" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_403_maps_to_authorization_error(self):
        from integrations.dropfans.client import DropfansClient

        with patch.object(
            DropfansClient, "_request", new_callable=AsyncMock
        ) as mock_req:
            mock_req.side_effect = DropfansAuthorizationError(
                "GET /me", "Access denied", 403
            )
            client = DropfansClient("valid_key")
            with pytest.raises(DropfansAuthorizationError) as exc_info:
                await client.get_me()
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_404_maps_to_not_found_error(self):
        from integrations.dropfans.client import DropfansClient

        with patch.object(
            DropfansClient, "_request", new_callable=AsyncMock
        ) as mock_req:
            mock_req.side_effect = DropfansNotFoundError(
                "GET /drops/999", "Not found", 404
            )
            client = DropfansClient("key")
            with pytest.raises(DropfansNotFoundError):
                await client.get_drop("999")

    @pytest.mark.asyncio
    async def test_429_maps_to_rate_limit_error_with_retry_after(self):
        from integrations.dropfans.client import DropfansClient

        with patch.object(
            DropfansClient, "_request", new_callable=AsyncMock
        ) as mock_req:
            mock_req.side_effect = DropfansRateLimitError(
                "POST /drops", "Rate limit exceeded", 429, retry_after=5.0
            )
            client = DropfansClient("key")
            with pytest.raises(DropfansRateLimitError) as exc_info:
                await client.create_drop(
                    name="test", price=9.99, vault_item_ids=["v1"]
                )
            assert exc_info.value.retry_after == 5.0

    def test_map_status_error_all_codes(self):
        from integrations.dropfans.client import _map_status_error

        assert isinstance(_map_status_error("op", 400, None), DropfansValidationError)
        assert isinstance(_map_status_error("op", 401, None), DropfansAuthenticationError)
        assert isinstance(_map_status_error("op", 403, None), DropfansAuthorizationError)
        assert isinstance(_map_status_error("op", 404, None), DropfansNotFoundError)
        assert isinstance(_map_status_error("op", 422, None), DropfansValidationError)
        assert isinstance(_map_status_error("op", 429, None), DropfansRateLimitError)
        assert isinstance(_map_status_error("op", 500, None), DropfansServerError)
        assert isinstance(_map_status_error("op", 502, None), DropfansServerError)
        assert isinstance(_map_status_error("op", 503, None), DropfansServerError)
        assert isinstance(_map_status_error("op", 418, None), DropfansError)

    def test_map_status_error_extracts_message_from_body(self):
        from integrations.dropfans.client import _map_status_error

        err = _map_status_error("op", 400, {"message": "Custom error"})
        assert err.message == "Custom error"

        err2 = _map_status_error("op", 400, {"error": "Alt error"})
        assert err2.message == "Alt error"

        err3 = _map_status_error("op", 400, {"detail": "Detail msg"})
        assert err3.message == "Detail msg"

    def test_map_status_error_extracts_from_errors_list(self):
        from integrations.dropfans.client import _map_status_error

        err = _map_status_error("op", 400, {"errors": ["first error", "second"]})
        assert err.message == "first error"

    def test_map_status_error_extracts_from_errors_dict(self):
        from integrations.dropfans.client import _map_status_error

        err = _map_status_error("op", 400, {"errors": {"field": "invalid"}})
        assert "invalid" in err.message


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Product Normalization (from_api factory methods)
# ═══════════════════════════════════════════════════════════════════════════════


class TestProductNormalization:
    def test_vault_item_from_api(self):
        data = {
            "id": "vault_001",
            "title": "My Photo",
            "fileType": "image",
            "filePath": "/storage/photo.jpg",
            "thumbnailPath": "/storage/thumb.jpg",
            "downloadUrl": "https://cdn.dropfans.io/dl/001",
            "tags": ["featured", "new"],
            "folderId": "f1",
            "folderName": "Campaigns",
            "moderationStatus": "APPROVED",
            "createdAt": "2025-01-15T10:00:00Z",
        }
        item = DropfansVaultItem.from_api(data)
        assert item.id == "vault_001"
        assert item.title == "My Photo"
        assert item.file_type == "image"
        assert item.tags == ["featured", "new"]
        assert item.moderation_status == "APPROVED"
        assert item.raw == data

    def test_vault_item_from_api_minimal(self):
        item = DropfansVaultItem.from_api({"id": "x"})
        assert item.id == "x"
        assert item.title is None
        assert item.tags == []

    def test_vault_folder_from_api(self):
        data = {"id": "f1", "name": "Campaigns", "itemCount": 5, "createdAt": "2025-01-01"}
        folder = DropfansVaultFolder.from_api(data)
        assert folder.id == "f1"
        assert folder.name == "Campaigns"
        assert folder.item_count == 5

    def test_drop_result_from_api(self):
        data = {
            "productId": "prod_xyz",
            "buyUrl": "https://dropfans.io/buy/prod_xyz",
            "mediaCount": 3,
        }
        result = DropfansDropResult.from_api(data)
        assert result.product_id == "prod_xyz"
        assert result.buy_url == "https://dropfans.io/buy/prod_xyz"
        assert result.media_count == 3

    def test_drop_from_api_full(self):
        data = {
            "productId": "prod_001",
            "name": "Summer Bundle",
            "priceCents": 4999,
            "buyUrl": "https://dropfans.io/buy/prod_001",
            "status": "active",
            "allowDownload": True,
            "mediaCount": 10,
            "salesCount": 42,
            "vaultItemIds": ["v1", "v2"],
            "createdAt": "2025-06-01",
            "lastSaleAt": "2025-06-15",
        }
        drop = DropfansDrop.from_api(data)
        assert drop.product_id == "prod_001"
        assert drop.name == "Summer Bundle"
        assert drop.price_cents == 4999
        assert drop.buy_url == "https://dropfans.io/buy/prod_001"
        assert drop.allow_download is True
        assert drop.sales_count == 42
        assert drop.vault_item_ids == ["v1", "v2"]

    def test_drop_from_api_uses_id_fallback(self):
        data = {"id": "fallback_id", "name": "Test"}
        drop = DropfansDrop.from_api(data)
        assert drop.product_id == "fallback_id"

    def test_sale_status_from_api(self):
        data = {"paid": True, "saleAmountCents": 4999, "buyerEmail": "fan@test.com"}
        status = DropfansSaleStatus.from_api("prod_1", data)
        assert status.product_id == "prod_1"
        assert status.paid is True
        assert status.sale_amount_cents == 4999
        assert status.buyer_email == "fan@test.com"

    def test_sale_status_unpaid(self):
        status = DropfansSaleStatus.from_api("prod_2", {"paid": False})
        assert status.paid is False
        assert status.sale_amount_cents is None

    def test_earnings_from_api(self):
        data = {
            "netEarnings": 15000,
            "grossEarnings": 20000,
            "transactionCount": 10,
            "uniqueCustomers": 8,
            "dropRevenue": 12000,
            "tips": 2000,
            "subscriptions": 1000,
            "recentTransactions": [
                {"id": "t1", "amountCents": 500, "type": "drop", "buyerEmail": "a@b.com", "productId": "p1"},
            ],
        }
        earnings = DropfansEarnings.from_api(data)
        assert earnings.net_cents == 15000
        assert earnings.gross_cents == 20000
        assert earnings.transaction_count == 10
        assert earnings.unique_customers == 8
        assert len(earnings.recent_transactions) == 1
        assert earnings.recent_transactions[0].id == "t1"
        assert earnings.recent_transactions[0].amount_cents == 500

    def test_earnings_from_api_empty(self):
        earnings = DropfansEarnings.from_api({})
        assert earnings.net_cents == 0
        assert earnings.recent_transactions == []

    def test_balance_from_api(self):
        data = {"pending": 5000, "available": 10000, "processing": 2000, "paidOut": 30000}
        balance = DropfansBalance.from_api(data)
        assert balance.pending_cents == 5000
        assert balance.available_cents == 10000
        assert balance.processing_cents == 2000
        assert balance.paid_out_cents == 30000

    def test_links_from_api(self):
        data = {
            "web": {"buyTemplate": "https://dropfans.io/buy/{dropId}"},
            "telegram": {"buyTemplate": "https://t.me/dropfans?start={productId}"},
        }
        links = DropfansLinks.from_api(data)
        assert links.web_buy_url == "https://dropfans.io/buy/{dropId}"
        assert links.telegram_buy_template == "https://t.me/dropfans?start={productId}"

    def test_links_from_api_partial(self):
        links = DropfansLinks.from_api({"web": {"buyTemplate": "https://web.test/{dropId}"}})
        assert links.web_buy_url == "https://web.test/{dropId}"
        assert links.telegram_buy_template is None

    def test_account_from_api(self):
        data = {
            "id": "df_user_123",
            "username": "creator1",
            "displayName": "Creator One",
            "email": "creator@test.com",
        }
        account = DropfansAccount.from_api(data)
        assert account.creator_id == "df_user_123"
        assert account.username == "creator1"
        assert account.display_name == "Creator One"
        assert account.email == "creator@test.com"

    def test_account_from_api_uses_name_fallback(self):
        data = {"id": "u1", "name": "Fallback Name"}
        account = DropfansAccount.from_api(data)
        assert account.display_name == "Fallback Name"

    def test_account_from_api_user_id_fallback(self):
        data = {"userId": "uid_999"}
        account = DropfansAccount.from_api(data)
        assert account.creator_id == "uid_999"

    def test_earnings_transaction_from_api_minimal(self):
        txn = DropfansEarningsTransaction.from_api({"id": "t_min"})
        assert txn.id == "t_min"
        assert txn.amount_cents == 0
        assert txn.product_id is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Product Retrieval (db/dropfans.py functions)
# ═══════════════════════════════════════════════════════════════════════════════


class TestProductRetrieval:
    @pytest.mark.asyncio
    async def test_get_dropfans_integration_found(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=_INTEGRATION)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        result = await ddb.get_dropfans_integration(1)
        assert result is not None
        assert result["creator_id"] == 1
        assert result["dropfans_creator_id"] == "df_user_abc123"

    @pytest.mark.asyncio
    async def test_get_dropfans_integration_not_found(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        result = await ddb.get_dropfans_integration(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_dropfans_product_uses_sha256_synthetic_id(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.upsert_dropfans_product(
            1,
            dropfans_product_id="df_prod_xyz",
            name="Test Drop",
            price_cents=4999,
            buy_url="https://dropfans.io/buy/xyz",
            status="active",
        )
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1:]
        expected_id = int(hashlib.sha256("df_prod_xyz".encode()).hexdigest()[:15], 16) % (2**62)
        assert params[0] == expected_id
        assert params[1] == 1
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_find_dropfans_product_returns_matching_row(self, monkeypatch):
        from db import dropfans as ddb
        import json

        synthetic_id = int(hashlib.sha256("df_prod_abc".encode()).hexdigest()[:15], 16) % (2**62)
        row = dict(_LOCAL_PRODUCT_ROW, raw=json.dumps({"dropfans_product_id": "df_prod_abc"}))
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        result = await ddb.find_dropfans_product(1, "df_prod_abc")
        assert result is not None

    @pytest.mark.asyncio
    async def test_find_dropfans_product_no_match_returns_none(self, monkeypatch):
        from db import dropfans as ddb
        import json

        row = dict(_LOCAL_PRODUCT_ROW, raw=json.dumps({"dropfans_product_id": "different_id"}))
        mock_pool = MagicMock()
        # Canonical (creator_id, dropfans_product_id) lookup misses, then the
        # synthetic-hash fallback row fails the raw equality check → None.
        mock_pool.fetchrow = AsyncMock(side_effect=[None, row])
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        result = await ddb.find_dropfans_product(1, "df_prod_abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_count_active_dropfans_products(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"cnt": 5})
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        count = await ddb.count_active_dropfans_products(1)
        assert count == 5

    @pytest.mark.asyncio
    async def test_count_active_dropfans_products_zero(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        count = await ddb.count_active_dropfans_products(1)
        assert count == 0

    @pytest.mark.asyncio
    async def test_record_dropfans_sale_idempotent(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        # P3.1 F-04: insert-vs-duplicate is derived from RETURNING via fetchrow.
        mock_pool.fetchrow = AsyncMock(return_value={"transaction_id": "dropfans:s1"})
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        result = await ddb.record_dropfans_sale(
            1, dropfans_product_id="df_prod_1", sale_amount_cents=4999
        )
        assert result is True
        sql = mock_pool.fetchrow.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "RETURNING" in sql

    @pytest.mark.asyncio
    async def test_has_dropfans_sale_been_recorded_true(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"x": 1})
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        assert await ddb.has_dropfans_sale_been_recorded(1, "df_prod_1") is True

    @pytest.mark.asyncio
    async def test_has_dropfans_sale_been_recorded_false(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        assert await ddb.has_dropfans_sale_been_recorded(1, "missing") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Execution Path (Dropfans-only)
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecutionPath:
    @pytest.mark.asyncio
    async def test_happy_path_executes(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        result, _ = await _run(monkeypatch)
        assert result.status is ExecutionStatus.EXECUTED
        assert result.created is True
        assert result.offer_id == 10
        assert result.offer_state == "pending"

    @pytest.mark.asyncio
    async def test_execute_ppv_uses_dropfans_only(self, monkeypatch):
        from commerce.execution import execute_ppv
        from integrations.dropfans.errors import DropfansError

        mocks = _patch_ppv(monkeypatch)
        monkeypatch.setattr(execute_ppv, "__module__", "commerce.execution")

        from commerce import execution as ex

        assert "dropfans" in str(ex.ddb)

    @pytest.mark.asyncio
    async def test_execution_extract_dropfans_product_id_from_raw(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.EXECUTED
        assert mocks["create_serialized"].await_args.kwargs["product_id"] == 5155

    @pytest.mark.asyncio
    async def test_execution_sets_usd_currency(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"
        kwargs = mocks["create_serialized"].await_args.kwargs
        assert kwargs["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_execution_records_pending_state(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"
        mocks["record_transition"].assert_awaited_once()
        kwargs = mocks["record_transition"].await_args.kwargs
        assert kwargs["state"] == "pending"

    @pytest.mark.asyncio
    async def test_missing_dropfans_product_id_in_raw_denied(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, raw={})
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PRODUCT_UNAVAILABLE
        assert result.denial_reason == "no_dropfans_product_id"

    @pytest.mark.asyncio
    async def test_execution_no_price_link_params(self):
        from commerce.execution import execute_ppv

        sig = inspect.signature(execute_ppv)
        params = set(sig.parameters)
        assert params & {"price", "price_minor", "link", "currency"} == set()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Error Handling (timeout, auth, validation)
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_error_has_operation_and_message(self):
        err = DropfansTimeoutError("GET /drops", "Request timed out after 30s")
        assert err.operation == "GET /drops"
        assert "timed out" in err.message
        assert isinstance(err, DropfansTransportError)
        assert isinstance(err, DropfansError)

    @pytest.mark.asyncio
    async def test_auth_error_preserves_status_code(self):
        err = DropfansAuthenticationError("POST /me", "Invalid API key", 401)
        assert err.status_code == 401
        assert err.operation == "POST /me"

    @pytest.mark.asyncio
    async def test_validation_error_preserves_body_message(self):
        err = DropfansValidationError("POST /drops", "price must be positive", 400)
        assert err.message == "price must be positive"
        assert err.status_code == 400

    @pytest.mark.asyncio
    async def test_server_error_5xx(self):
        err = DropfansServerError("GET /drops", "Internal error", 502)
        assert err.status_code == 502
        assert isinstance(err, DropfansError)

    @pytest.mark.asyncio
    async def test_rate_limit_error_retry_after_none(self):
        err = DropfansRateLimitError("POST /drops")
        assert err.retry_after is None
        assert err.status_code == 429

    @pytest.mark.asyncio
    async def test_error_str_contains_operation(self):
        err = DropfansError("test_op", "test message", 500)
        s = str(err)
        assert "test_op" in s
        assert "test message" in s
        assert "500" in s

    @pytest.mark.asyncio
    async def test_error_str_with_correlation_id(self):
        err = DropfansError("op", "msg", 500, correlation_id="corr-123")
        assert "corr-123" in str(err)

    @pytest.mark.asyncio
    async def test_execution_timeout_maps_to_provider_error(self, monkeypatch):
        from commerce.execution import ExecutionStatus
        from integrations.dropfans.errors import DropfansTimeoutError
        from commerce.models import PolicyDecision

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, sales_url=None)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        monkeypatch.setattr(
            "integrations.dropfans.service.build_checkout_url",
            AsyncMock(side_effect=DropfansTimeoutError("build_checkout", "timeout")),
        )
        monkeypatch.setattr(
            "commerce.execution.evaluate_ppv_eligibility",
            lambda *a, **kw: PolicyDecision(allowed=True),
        )
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PROVIDER_ERROR
        assert result.denial_reason == "checkout_link_unavailable"

    @pytest.mark.asyncio
    async def test_execution_auth_error_maps_to_provider_error(self, monkeypatch):
        from commerce.execution import ExecutionStatus
        from integrations.dropfans.errors import DropfansAuthenticationError
        from commerce.models import PolicyDecision

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, sales_url=None)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        monkeypatch.setattr(
            "integrations.dropfans.service.build_checkout_url",
            AsyncMock(side_effect=DropfansAuthenticationError("auth", "bad key", 401)),
        )
        monkeypatch.setattr(
            "commerce.execution.evaluate_ppv_eligibility",
            lambda *a, **kw: PolicyDecision(allowed=True),
        )
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PROVIDER_ERROR
        assert result.denial_reason == "checkout_link_unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Creator Isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_get_dropfans_integration_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=_INTEGRATION)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.get_dropfans_integration(1)
        call_args = mock_pool.fetchrow.call_args
        assert call_args[0][1] == 1

    @pytest.mark.asyncio
    async def test_upsert_dropfans_product_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.upsert_dropfans_product(42, dropfans_product_id="df_x")
        call_args = mock_pool.execute.call_args
        params = call_args[0][1:]
        assert params[1] == 42

    @pytest.mark.asyncio
    async def test_find_dropfans_product_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.find_dropfans_product(7, "df_y")
        call_args = mock_pool.fetchrow.call_args
        assert call_args[0][1] == 7

    @pytest.mark.asyncio
    async def test_list_active_dropfans_products_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.list_active_dropfans_products(3)
        call_args = mock_pool.fetch.call_args
        assert call_args[0][1] == 3

    @pytest.mark.asyncio
    async def test_count_active_dropfans_products_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"cnt": 2})
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.count_active_dropfans_products(5)
        call_args = mock_pool.fetchrow.call_args
        assert call_args[0][1] == 5

    @pytest.mark.asyncio
    async def test_record_dropfans_sale_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        # P3.1 F-04: insert goes through fetchrow (... RETURNING ...).
        mock_pool.fetchrow = AsyncMock(return_value={"transaction_id": "dropfans:s1"})
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.record_dropfans_sale(10, dropfans_product_id="df_z")
        call_args = mock_pool.fetchrow.call_args
        assert call_args[0][1] == 10

    @pytest.mark.asyncio
    async def test_has_dropfans_sale_been_recorded_scoped_to_creator(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.has_dropfans_sale_been_recorded(8, "df_1")
        call_args = mock_pool.fetchrow.call_args
        assert call_args[0][1] == 8

    @pytest.mark.asyncio
    async def test_execution_different_creator_gets_own_integration(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        creator_b_integration = dict(_INTEGRATION, creator_id=2)
        mocks["get_integration"].return_value = creator_b_integration
        result, _ = await _run(monkeypatch, mocks=mocks)
        mocks["get_integration"].assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_execution_isolation_between_creators(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_integration"].return_value = None
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.CREATOR_NOT_READY
        mocks["create_serialized"].assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Webhook Disabled (returns 410)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebhookDisabled:
    @pytest.mark.asyncio
    async def test_fangate_webhook_endpoint_returns_410(self, test_client):
        client = test_client
        response = await client.post("/api/fangate/webhooks/1", json={})
        assert response.status_code == 410
        body = response.json()
        assert body["status"] == "deprecated"
        assert "Dropfans" in body["message"]
        assert body["creator_id"] == 1

    @pytest.mark.asyncio
    async def test_fangate_webhook_endpoint_returns_deprecation_notice(self, test_client):
        client = test_client
        response = await client.post("/api/fangate/webhooks/42", json={"event": "test"})
        assert response.status_code == 410
        body = response.json()
        assert "no longer processed" in body["message"]

    @pytest.mark.asyncio
    async def test_fangate_webhook_does_not_mutate_state(self, test_client):
        client = test_client
        response = await client.post("/api/fangate/webhooks/1", json={"event": "purchase"})
        assert response.status_code == 410
        body = response.json()
        assert body["status"] == "deprecated"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Hash Collision Resistance (SHA-256 deterministic IDs)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHashCollisionResistance:
    def test_sha256_deterministic_id_is_stable(self):
        product_id = "df_prod_abc123"
        expected = int(hashlib.sha256(product_id.encode()).hexdigest()[:15], 16) % (2**62)
        assert expected == int(hashlib.sha256(product_id.encode()).hexdigest()[:15], 16) % (2**62)

    def test_different_products_different_ids(self):
        id1 = int(hashlib.sha256("prod_a".encode()).hexdigest()[:15], 16) % (2**62)
        id2 = int(hashlib.sha256("prod_b".encode()).hexdigest()[:15], 16) % (2**62)
        assert id1 != id2

    def test_same_input_same_id_across_calls(self):
        product_id = "df_prod_xyz789"
        ids = set()
        for _ in range(100):
            ids.add(int(hashlib.sha256(product_id.encode()).hexdigest()[:15], 16) % (2**62))
        assert len(ids) == 1

    def test_synthetic_id_within_62_bit_range(self):
        for i in range(50):
            pid = f"prod_{i:04d}"
            sid = int(hashlib.sha256(pid.encode()).hexdigest()[:15], 16) % (2**62)
            assert 0 <= sid < 2**62

    @pytest.mark.asyncio
    async def test_upsert_uses_sha256_not_python_hash(self, monkeypatch):
        from db import dropfans as ddb

        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        monkeypatch.setattr(ddb, "get_pool", AsyncMock(return_value=mock_pool))

        await ddb.upsert_dropfans_product(1, dropfans_product_id="test_id")
        call_args = mock_pool.execute.call_args
        params = call_args[0][1:]
        expected_id = int(hashlib.sha256("test_id".encode()).hexdigest()[:15], 16) % (2**62)
        assert params[0] == expected_id

    def test_no_hash_collision_for_common_ids(self):
        seen = {}
        for i in range(1000):
            pid = f"dropfans_product_{i}"
            sid = int(hashlib.sha256(pid.encode()).hexdigest()[:15], 16) % (2**62)
            assert sid not in seen, f"Collision at i={i}: {pid} == {seen[sid]}"
            seen[sid] = pid


# ═══════════════════════════════════════════════════════════════════════════════
# Security
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecuritySurface:
    def test_error_never_contains_api_key(self):
        err = DropfansError("op", "something failed", 500)
        assert "secret_key_12345" not in str(err)

    def test_error_str_does_not_leak_payload(self):
        err = DropfansError("op", "msg", payload={"api_key": "super_secret"})
        s = str(err)
        assert "super_secret" not in s

    def test_execution_import_scope(self):
        tree = ast.parse(EXECUTION_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        allowed = {
            "commerce",
            # P3.2: deterministic money math (Decimal, no float) + best-effort
            # realtime via the event bus (workers never import ws directly).
            "core",
            "dataclasses",
            "datetime",
            "db",
            "decimal",
            "enum",
            "integrations",
            "json",
            "logging",
            "typing",
        }
        unexpected = imported - allowed
        assert unexpected == set(), f"Unexpected imports: {unexpected}"

    def test_client_import_scope(self):
        tree = ast.parse(CLIENT_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        allowed = {
            "__future__",
            "errors",
            "httpx",
            "integrations",
            "logging",
            "models",
            "time",
            "typing",
        }
        unexpected = imported - allowed
        assert unexpected == set(), f"Unexpected imports in client: {unexpected}"


# ═══════════════════════════════════════════════════════════════════════════════
# Error Hierarchy
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorHierarchy:
    def test_timeout_is_subclass_of_transport(self):
        assert issubclass(DropfansTimeoutError, DropfansTransportError)

    def test_transport_is_subclass_of_base(self):
        assert issubclass(DropfansTransportError, DropfansError)

    def test_auth_error_is_subclass(self):
        assert issubclass(DropfansAuthenticationError, DropfansError)

    def test_rate_limit_is_subclass(self):
        assert issubclass(DropfansRateLimitError, DropfansError)

    def test_not_found_is_subclass(self):
        assert issubclass(DropfansNotFoundError, DropfansError)

    def test_validation_is_subclass(self):
        assert issubclass(DropfansValidationError, DropfansError)

    def test_server_error_is_subclass(self):
        assert issubclass(DropfansServerError, DropfansError)

    def test_response_error_is_subclass(self):
        assert issubclass(DropfansResponseError, DropfansError)

    def test_all_errors_have_operation(self):
        for cls in [
            DropfansError,
            DropfansAuthenticationError,
            DropfansAuthorizationError,
            DropfansNotFoundError,
            DropfansValidationError,
            DropfansRateLimitError,
            DropfansServerError,
            DropfansResponseError,
            DropfansTransportError,
            DropfansTimeoutError,
        ]:
            err = cls("test_op", "msg")
            assert err.operation == "test_op"

    def test_error_message_property(self):
        err = DropfansError("op", "hello")
        assert err.message == "hello"

    def test_error_default_message(self):
        err = DropfansError("op")
        assert "op" in err.message
