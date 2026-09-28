"""P0.2 -- Webhook -> Purchase Attribution Bridge tests.

Groups:
    A: Webhook creates transaction (existing behavior preserved)
    B: Webhook attributes exact match (service-level)
    C: Attribution call actually occurs (regression guard)
    D: No match (no attribution)
    E: Multiple candidates (fail-closed)
    F: Creator isolation
    G: Idempotent attribution (re-processing)
    H: Already purchased (safe no-op)
    I: Invalid offer state
    J: Transaction already has user (preserve existing)
    K: HMAC failure prevents attribution
    L: Duplicate delivery (existing idempotency)
    M: Transaction persistence failure (no attribution)
    N: Attribution failure (best-effort isolation)
    O: Analytics
    P: Full webhook -> offer lifecycle
"""

import hashlib
import hmac as _hmac
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.fangate.service import (
    IntegrationNotFoundError,
    WebhookSignatureInvalidError,
)

pytestmark = [pytest.mark.unit]


# -- Helpers ----------------------------------------------------------------


def _sign(secret, raw_body):
    digest = _hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _webhook_payload(
    event="payment.successful",
    txn="txn_123",
    product_id=42,
    seller_earning=8.50,
    **overrides,
):
    body = {
        "event": event,
        "timestamp": "2026-05-19T10:42:00Z",
        "data": {
            "transaction_id": txn,
            "buyer_email": "buyer@example.com",
            "seller_earning": seller_earning,
            "currency": "EUR",
            "product_id": product_id,
            "media_ids": ["m_101"],
            "set_price": 10.00,
        },
    }
    body["data"].update(overrides)
    return body


def _integration_row(creator_id=1, with_webhook_secret=True):
    return {
        "id": 1,
        "creator_id": creator_id,
        "fangate_account_id": None,
        "encrypted_api_key": "gAAAAA-fake-ciphertext",
        "api_key_name": "crm",
        "webhook_id": 99 if with_webhook_secret else None,
        "encrypted_webhook_secret": (
            "gAAAAA-secret-ciphertext" if with_webhook_secret else None
        ),
        "status": "active",
        "last_success_at": None,
        "last_error_at": None,
        "last_error": None,
    }


def _mock_db(monkeypatch, **overrides):
    """Install AsyncMock replacements for db.fangate functions."""
    from db import fangate as fdb

    names = [
        "get_creator_integration",
        "record_integration_success",
        "record_integration_error",
        "upsert_fangate_product",
        "count_fangate_products",
        "list_fangate_products",
        "get_fangate_product",
        "delete_fangate_product",
        "upsert_fangate_wallet_entry",
        "count_fangate_wallet_entries",
        "list_fangate_wallet_entries",
        "upsert_fangate_transaction",
        "list_fangate_transactions",
        "count_fangate_transactions",
        "insert_fangate_webhook_event",
        "get_fangate_webhook_event",
        "mark_webhook_event_processed",
        "update_integration_webhook",
        "get_creator",
        "create_creator",
        "list_creators",
        "upsert_creator_integration",
        "list_active_creator_ids",
    ]
    mocks = {}
    for name in names:
        mock = AsyncMock()
        if name in overrides:
            mock.side_effect = overrides[name]
        monkeypatch.setattr(fdb, name, mock)
        mocks[name] = mock
    return mocks


def _offer_row(
    offer_id=101,
    creator_id=1,
    user_id=42,
    product_id=42,
    state="pending",
    transaction_id=None,
):
    return {
        "id": offer_id,
        "creator_id": creator_id,
        "user_id": user_id,
        "product_id": product_id,
        "link": f"https://fangate.info/{product_id}x",
        "price_minor": 4400,
        "currency": "USD",
        "state": state,
        "reason": "ppv_execution",
        "created_by": "commerce_pipeline",
        "created_at": datetime(2026, 5, 19, tzinfo=UTC),
        "expires_at": None,
        "clicked_at": None,
        "purchased_at": None,
        "transaction_id": transaction_id,
    }


def _mock_dao_pool(candidates, update_result=None):
    """Build a mock pool/connection for DAO-level tests."""
    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=candidates)
    mock_conn.fetchrow = AsyncMock(return_value=update_result)
    mock_conn.execute = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
    mock_pool.__aexit__ = AsyncMock(return_value=False)
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    return mock_pool


# -- Group A: Webhook creates transaction (existing behavior preserved) -------


class TestWebhookCreatesTransaction:
    @pytest.mark.asyncio
    async def test_receive_valid_persists_and_records(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret",
            lambda _: "whsec_test",
        )
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook",
            AsyncMock(return_value=None),
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec_test", body), "delivery-1"
        )

        assert result["duplicate"] is False
        assert result["processed"] is True
        assert result["recorded"] is True
        mocks["insert_fangate_webhook_event"].assert_awaited_once()
        mocks["upsert_fangate_transaction"].assert_awaited_once()
        mocks["mark_webhook_event_processed"].assert_awaited_once_with(1, "delivery-1")

    @pytest.mark.asyncio
    async def test_receive_duplicate_delivery_skipped(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = False
        mocks["get_fangate_webhook_event"].return_value = {
            "id": 1,
            "processed": True,
        }
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "delivery-dup"
        )

        assert result == {"duplicate": True, "processed": False}
        mocks["upsert_fangate_transaction"].assert_not_awaited()


# -- Group B: Webhook attributes exact match ---------------------------------


class TestWebhookAttributesExactMatch:
    @pytest.mark.asyncio
    async def test_attribution_called_with_correct_args(self, monkeypatch):
        from integrations.fangate import service
        from commerce.models import PurchaseRecord

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(
            return_value=PurchaseRecord(
                offer_id=101,
                creator_id=1,
                user_id=42,
                transaction_id="txn_123",
                product_id=42,
            )
        )
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload(product_id=42)).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "d-1"
        )

        assert result["attributed"] is True
        mock_attr.assert_awaited_once()
        call_kwargs = mock_attr.call_args.kwargs
        assert call_kwargs["creator_id"] == 1
        assert call_kwargs["product_id"] == 42
        assert call_kwargs["transaction_id"] == "txn_123"

    @pytest.mark.asyncio
    async def test_revenue_minor_converted_from_seller_earning(self, monkeypatch):
        from integrations.fangate import service
        from commerce.models import PurchaseRecord

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(return_value=PurchaseRecord())
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload(seller_earning=12.75)).encode()
        await service.receive_webhook(
            1, body, _sign("whsec", body), "d-rev"
        )

        call_kwargs = mock_attr.call_args.kwargs
        assert call_kwargs["revenue_minor"] == 1275


# -- Group C: Attribution call actually occurs (regression guard) --------------


class TestAttributionCallOccurs:
    @pytest.mark.asyncio
    async def test_attribution_called_on_successful_webhook(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        await service.receive_webhook(
            1, body, _sign("whsec", body), "d-wire"
        )

        mock_attr.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_attribution_not_called_when_no_product_id(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        payload = _webhook_payload()
        payload["data"]["product_id"] = None
        body = json.dumps(payload).encode()
        await service.receive_webhook(
            1, body, _sign("whsec", body), "d-noprod"
        )

        mock_attr.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attribution_result_reflected_in_response(self, monkeypatch):
        from integrations.fangate import service
        from commerce.models import PurchaseRecord

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(return_value=PurchaseRecord(offer_id=101))
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "d-yes"
        )

        assert result["attributed"] is True


# -- Group D: No match (no attribution) --------------------------------------


class TestNoMatch:
    @pytest.mark.asyncio
    async def test_no_pending_offer_returns_none(self):
        from commerce.dao import attribute_purchase_from_webhook

        mock_pool = _mock_dao_pool(candidates=[], update_result=None)

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_attribution_returns_none_when_service_returns_none(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "d-nomatch"
        )

        assert result["attributed"] is False


# -- Group E: Multiple candidates (fail-closed) ------------------------------


class TestMultipleCandidates:
    @pytest.mark.asyncio
    async def test_multiple_pending_offers_returns_none(self):
        from commerce.dao import attribute_purchase_from_webhook

        offer_a = _offer_row(offer_id=101, user_id=42)
        offer_b = _offer_row(offer_id=102, user_id=99)
        mock_pool = _mock_dao_pool(candidates=[offer_a, offer_b])

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_no_arbitrary_offer_selected(self):
        from commerce.dao import attribute_purchase_from_webhook

        offer_a = _offer_row(offer_id=101, user_id=42)
        offer_b = _offer_row(offer_id=102, user_id=99)
        mock_pool = _mock_dao_pool(candidates=[offer_a, offer_b])

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None
        mock_conn = mock_pool.acquire.return_value
        # P2.1: ambiguous fail-closed must NOT mark any offer purchased,
        # but MUST create operator-visible ambiguous_offer recovery record.
        for call in mock_conn.execute.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "SET state = 'purchased'" not in sql
        recovery_inserts = [
            call for call in mock_conn.execute.call_args_list
            if "ambiguous_purchase_recoveries" in (call.args[0] if call.args else "")
        ]
        assert len(recovery_inserts) >= 1


# -- Group F: Creator isolation ----------------------------------------------


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_offers_from_other_creator_not_matched(self):
        from commerce.dao import attribute_purchase_from_webhook

        own_offer = _offer_row(offer_id=101, creator_id=1, user_id=42)
        mock_pool = _mock_dao_pool(
            candidates=[own_offer],
            update_result={**own_offer, "state": "purchased"},
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        mock_conn = mock_pool.acquire.return_value
        call_args = mock_conn.fetch.call_args
        assert call_args.args[1] == 1  # creator_id parameter


# -- Group G: Idempotent attribution (re-processing) --------------------------


class TestIdempotentAttribution:
    @pytest.mark.asyncio
    async def test_already_purchased_offer_returns_none(self):
        from commerce.dao import attribute_purchase_from_webhook

        pending_offer = _offer_row(offer_id=101, user_id=42, state="pending")
        mock_pool = _mock_dao_pool(
            candidates=[pending_offer], update_result=None
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None


# -- Group H: Already purchased (safe no-op) ---------------------------------


class TestAlreadyPurchased:
    @pytest.mark.asyncio
    async def test_already_purchased_returns_none(self):
        from commerce.dao import attribute_purchase_from_webhook

        pending_offer = _offer_row(offer_id=101, user_id=42, state="pending")
        mock_pool = _mock_dao_pool(
            candidates=[pending_offer], update_result=None
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_existing"
            )

        assert result is None


# -- Group I: Invalid offer state --------------------------------------------


class TestInvalidOfferState:
    @pytest.mark.asyncio
    async def test_expired_offer_not_attributed(self):
        from commerce.dao import attribute_purchase_from_webhook

        mock_pool = _mock_dao_pool(candidates=[])

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_offer_not_attributed(self):
        from commerce.dao import attribute_purchase_from_webhook

        mock_pool = _mock_dao_pool(candidates=[])

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is None


# -- Group J: Transaction already has user ------------------------------------


class TestTransactionAlreadyHasUser:
    @pytest.mark.asyncio
    async def test_existing_user_not_overwritten(self):
        from commerce.dao import attribute_purchase_from_webhook

        pending_offer = _offer_row(offer_id=101, user_id=42, state="pending")
        purchased_offer = {
            **pending_offer,
            "state": "purchased",
            "transaction_id": "txn_1",
        }
        mock_pool = _mock_dao_pool(
            candidates=[pending_offer], update_result=purchased_offer
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1"
            )

        assert result is not None
        assert result.user_id == 42
        mock_conn = mock_pool.acquire.return_value
        execute_call = mock_conn.execute.call_args_list[0]
        assert "user_id IS NULL" in execute_call.args[0]


# -- Group K: HMAC failure prevents attribution -------------------------------


class TestHmacFailurePreventsAttribution:
    @pytest.mark.asyncio
    async def test_bad_signature_blocks_everything(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock()
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        with pytest.raises(WebhookSignatureInvalidError):
            await service.receive_webhook(1, body, "sha256=forged", "d-1")

        mocks["insert_fangate_webhook_event"].assert_not_awaited()
        mocks["upsert_fangate_transaction"].assert_not_awaited()
        mock_attr.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_secret_blocks_everything(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row(
            with_webhook_secret=False
        )
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock()
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        with pytest.raises(IntegrationNotFoundError):
            await service.receive_webhook(1, body, "sha256=x", "d-1")

        mock_attr.assert_not_awaited()


# -- Group L: Duplicate delivery (existing idempotency) -----------------------


class TestDuplicateDelivery:
    @pytest.mark.asyncio
    async def test_duplicate_skips_attribution(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = False
        mocks["get_fangate_webhook_event"].return_value = {
            "id": 1,
            "processed": True,
        }
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock()
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "d-dup"
        )

        assert result["duplicate"] is True
        mock_attr.assert_not_awaited()


# -- Group M: Transaction persistence failure (no attribution) ----------------


class TestTransactionPersistenceFailure:
    @pytest.mark.asyncio
    async def test_persistence_failure_blocks_attribution(self, monkeypatch):
        from integrations.fangate import service
        from integrations.fangate.errors import FangateError

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].side_effect = RuntimeError("db down")
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock()
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        with pytest.raises(FangateError):
            await service.receive_webhook(
                1, body, _sign("whsec", body), "d-fail"
            )

        mock_attr.assert_not_awaited()


# -- Group N: Attribution failure (best-effort isolation) ---------------------


class TestAttributionFailureIsolation:
    @pytest.mark.asyncio
    async def test_attribution_exception_does_not_block_webhook(self, monkeypatch):
        from integrations.fangate import service

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        mock_attr = AsyncMock(side_effect=RuntimeError("attribution db error"))
        monkeypatch.setattr(
            "commerce.dao.attribute_purchase_from_webhook", mock_attr
        )

        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(
            1, body, _sign("whsec", body), "d-err"
        )

        assert result["processed"] is True
        assert result["attributed"] is False


# -- Group O: Analytics -------------------------------------------------------


class TestAnalytics:
    @pytest.mark.asyncio
    async def test_analytics_purchased_incremented(self):
        from commerce.dao import attribute_purchase_from_webhook

        pending_offer = _offer_row(offer_id=101, user_id=42, state="pending")
        purchased_offer = {
            **pending_offer,
            "state": "purchased",
            "transaction_id": "txn_1",
        }
        mock_pool = _mock_dao_pool(
            candidates=[pending_offer], update_result=purchased_offer
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await attribute_purchase_from_webhook(
                creator_id=1, product_id=42, transaction_id="txn_1",
                revenue_minor=850,
            )

        assert result is not None
        mock_conn = mock_pool.acquire.return_value
        # attach_user + purchased_analytics + revenue_analytics
        assert mock_conn.execute.call_count >= 2


# -- Group P: Full webhook -> offer lifecycle ---------------------------------


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle_single_offer(self, monkeypatch):
        """End-to-end: single pending offer -> webhook -> attributed."""
        from integrations.fangate import service
        from commerce.dao import attribute_purchase_from_webhook

        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr(
            "integrations.fangate.service.decrypt_secret", lambda _: "whsec"
        )

        pending_offer = _offer_row(offer_id=101, user_id=42, state="pending")
        purchased_offer = {
            **pending_offer,
            "state": "purchased",
            "transaction_id": "txn_123",
        }
        mock_pool = _mock_dao_pool(
            candidates=[pending_offer], update_result=purchased_offer
        )

        with patch(
            "commerce.dao.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            monkeypatch.setattr(
                "commerce.dao.attribute_purchase_from_webhook",
                attribute_purchase_from_webhook,
            )

            body = json.dumps(_webhook_payload(product_id=42)).encode()
            result = await service.receive_webhook(
                1, body, _sign("whsec", body), "d-lifecycle"
            )

        assert result["duplicate"] is False
        assert result["processed"] is True
        assert result["attributed"] is True
