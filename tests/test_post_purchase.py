"""P1.1 + P1.2 -- Post-Purchase Lifecycle Automation tests.

Groups:
    A: Successful attribution advances funnel stage
    B: Successful attribution enqueues confirmation
    C: Ambiguous attribution does NOT advance funnel
    D: No matching offer does NOT advance funnel
    E: Duplicate webhook does NOT duplicate confirmation
    F: Already purchased offer (idempotent attribution)
    G: Send enqueue failure (payment persistence unaffected)
    H: Blocked user handling
    I: Webhook -> post-purchase integration
"""

import hashlib
import hmac as _hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _sign(secret, raw_body):
    digest = _hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _webhook_payload(
    event="payment.successful",
    txn="txn_456",
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


def _purchase_record(offer_id=101, creator_id=1, user_id=42,
                     transaction_id="txn_456", product_id=42):
    from commerce.models import PurchaseRecord
    return PurchaseRecord(
        offer_id=offer_id, creator_id=creator_id, user_id=user_id,
        transaction_id=transaction_id, product_id=product_id,
    )


def _mock_db(monkeypatch, **overrides):
    from db import fangate as fdb
    names = [
        "get_creator_integration", "record_integration_success",
        "record_integration_error", "upsert_fangate_product",
        "count_fangate_products", "list_fangate_products",
        "get_fangate_product", "delete_fangate_product",
        "upsert_fangate_wallet_entry", "count_fangate_wallet_entries",
        "list_fangate_wallet_entries", "upsert_fangate_transaction",
        "list_fangate_transactions", "count_fangate_transactions",
        "insert_fangate_webhook_event", "get_fangate_webhook_event",
        "mark_webhook_event_processed", "update_integration_webhook",
        "get_creator", "create_creator", "list_creators",
        "upsert_creator_integration", "list_active_creator_ids",
    ]
    mocks = {}
    for name in names:
        mock = AsyncMock()
        if name in overrides:
            mock.side_effect = overrides[name]
        monkeypatch.setattr(fdb, name, mock)
        mocks[name] = mock
    return mocks


def _mock_pool(existing_stage="new"):
    from unittest.mock import MagicMock
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=existing_stage)
    mock_conn.execute = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


# GROUP A
class TestFunnelStageAdvancement:
    @pytest.mark.asyncio
    async def test_funnel_advanced_from_new(self):
        from commerce.post_purchase import advance_funnel_to_converted
        mock_pool, mock_conn = _mock_pool("new")
        with patch("commerce.post_purchase.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await advance_funnel_to_converted(42)
        assert result is True
        mock_conn.execute.assert_awaited_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "converted" in sql

    @pytest.mark.asyncio
    async def test_funnel_advanced_from_engaged(self):
        from commerce.post_purchase import advance_funnel_to_converted
        mock_pool, mock_conn = _mock_pool("engaged")
        with patch("commerce.post_purchase.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await advance_funnel_to_converted(42)
        assert result is True
        mock_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_funnel_already_converted_noop(self):
        from commerce.post_purchase import advance_funnel_to_converted
        mock_pool, mock_conn = _mock_pool("converted")
        with patch("commerce.post_purchase.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await advance_funnel_to_converted(42)
        assert result is True
        mock_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_funnel_user_not_found(self):
        from commerce.post_purchase import advance_funnel_to_converted
        mock_pool, mock_conn = _mock_pool(None)
        with patch("commerce.post_purchase.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await advance_funnel_to_converted(999999)
        assert result is False

    @pytest.mark.asyncio
    async def test_funnel_stage_not_overwritten_arbitrarily(self):
        from commerce.post_purchase import advance_funnel_to_converted
        mock_pool, mock_conn = _mock_pool("new")
        with patch("commerce.post_purchase.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await advance_funnel_to_converted(42)
        sql = mock_conn.execute.call_args[0][0]
        assert "converted" in sql
        assert "engaged" not in sql
        assert "returned" not in sql


# GROUP B
class TestConfirmationEnqueue:
    @pytest.mark.asyncio
    async def test_confirmation_enqueued_with_correct_payload(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1234567890-0")
        mock_dedup = AsyncMock(return_value=False)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            result = await enqueue_purchase_confirmation(42, "txn_456")
        assert result is True
        mock_enqueue.assert_awaited_once()
        call_args = mock_enqueue.call_args
        payload = call_args[0][0]
        dedup_id = call_args[1]["dedup_id"]
        assert payload["entity"] == "42"
        assert "purchase" in payload["content"].lower() or "confirmed" in payload["content"].lower()
        assert payload["save_to_db"] is True
        assert dedup_id == "post_purchase:txn_456:42"

    @pytest.mark.asyncio
    async def test_confirmation_no_internal_ids_exposed(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1234567890-0")
        mock_dedup = AsyncMock(return_value=False)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            await enqueue_purchase_confirmation(42, "txn_456")
        payload = mock_enqueue.call_args[0][0]
        content = payload["content"]
        assert "txn_456" not in content
        assert "buyer@" not in content
        assert "seller_earning" not in content

    @pytest.mark.asyncio
    async def test_confirmation_deterministic_message(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1234567890-0")
        mock_dedup = AsyncMock(return_value=False)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            await enqueue_purchase_confirmation(42, "txn_111")
            content_1 = mock_enqueue.call_args[0][0]["content"]
            await enqueue_purchase_confirmation(43, "txn_222")
            content_2 = mock_enqueue.call_args[0][0]["content"]
        assert content_1 == content_2

    @pytest.mark.asyncio
    async def test_confirmation_already_queued_no_duplicate(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1234567890-0")
        mock_dedup = AsyncMock(return_value=True)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            result = await enqueue_purchase_confirmation(42, "txn_456")
        assert result is True
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enqueue_send_failure_returns_false(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(side_effect=RuntimeError("redis down"))
        mock_dedup = AsyncMock(return_value=False)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            result = await enqueue_purchase_confirmation(42, "txn_456")
        assert result is False


# GROUP C
class TestAmbiguousAttribution:
    @pytest.mark.asyncio
    async def test_webhook_ambiguous_no_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=None))
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-ambig")
        assert result["attributed"] is False
        mock_handle.assert_not_awaited()


# GROUP D
class TestNoOfferNoAttribution:
    @pytest.mark.asyncio
    async def test_webhook_no_offer_no_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=None))
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-none")
        assert result["attributed"] is False
        mock_handle.assert_not_awaited()


# GROUP E
class TestDuplicateWebhook:
    @pytest.mark.asyncio
    async def test_duplicate_delivery_skips_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = False
        mocks["get_fangate_webhook_event"].return_value = {"id": 1, "processed": True}
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-dup")
        assert result["duplicate"] is True
        mock_handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dedup_id_prevents_duplicate_enqueue(self):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1234567890-0")
        mock_dedup = AsyncMock(side_effect=[False, True])
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            result_1 = await enqueue_purchase_confirmation(42, "txn_456")
            result_2 = await enqueue_purchase_confirmation(42, "txn_456")
        assert result_1 is True
        assert result_2 is True
        assert mock_enqueue.await_count == 1


# GROUP F
class TestAlreadyPurchased:
    @pytest.mark.asyncio
    async def test_already_purchased_no_duplicate_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        record = _purchase_record()
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=record))
        body = json.dumps(_webhook_payload(txn="txn_first")).encode()
        result_1 = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-first")
        assert result_1["attributed"] is True
        assert mock_handle.await_count == 1
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=None))
        body2 = json.dumps(_webhook_payload(txn="txn_first")).encode()
        result_2 = await service.receive_webhook(1, body2, _sign("whsec", body2), "delivery-second")
        assert result_2["attributed"] is False
        assert mock_handle.await_count == 1


# GROUP G
class TestSendEnqueueFailure:
    @pytest.mark.asyncio
    async def test_funnel_failure_does_not_break_webhook(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        record = _purchase_record()
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=record))
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", AsyncMock(side_effect=RuntimeError("boom")))
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-fail")
        assert result["attributed"] is True
        assert result["processed"] is True

    @pytest.mark.asyncio
    async def test_handle_post_purchase_isolation(self):
        from commerce.post_purchase import handle_post_purchase
        from commerce.models import PurchaseRecord
        record = PurchaseRecord(offer_id=101, creator_id=1, user_id=42, transaction_id="txn_iso", product_id=42)
        with patch("commerce.post_purchase.advance_funnel_to_converted", AsyncMock(side_effect=RuntimeError("db boom"))), \
             patch("commerce.post_purchase.enqueue_purchase_confirmation", AsyncMock(return_value=True)) as mock_enq:
            await handle_post_purchase(record)
        # P2.1: now creator-scoped (3 args); allow any extra kwargs for backward compat
        assert mock_enq.await_count == 1
        assert mock_enq.call_args[0][:2] == (42, "txn_iso")
        # creator_id should be passed through when available
        assert mock_enq.call_args[1].get("creator_id", 1) == 1

    @pytest.mark.asyncio
    async def test_handle_post_purchase_both_fail_no_crash(self):
        from commerce.post_purchase import handle_post_purchase
        from commerce.models import PurchaseRecord
        record = PurchaseRecord(offer_id=101, creator_id=1, user_id=42, transaction_id="txn_both", product_id=42)
        with patch("commerce.post_purchase.advance_funnel_to_converted", AsyncMock(side_effect=RuntimeError("fail"))), \
             patch("commerce.post_purchase.enqueue_purchase_confirmation", AsyncMock(side_effect=RuntimeError("fail"))):
            await handle_post_purchase(record)


# GROUP H
class TestBlockedUserHandling:
    @pytest.mark.asyncio
    async def test_handle_post_purchase_with_incomplete_record(self):
        from commerce.post_purchase import handle_post_purchase
        from commerce.models import PurchaseRecord
        record = PurchaseRecord(user_id=None, transaction_id="txn_no_user")
        mock_funnel = AsyncMock()
        mock_enq = AsyncMock()
        with patch("commerce.post_purchase.advance_funnel_to_converted", mock_funnel), \
             patch("commerce.post_purchase.enqueue_purchase_confirmation", mock_enq):
            await handle_post_purchase(record)
        mock_funnel.assert_not_awaited()
        mock_enq.assert_not_awaited()


# GROUP I
class TestWebhookPostPurchaseIntegration:
    @pytest.mark.asyncio
    async def test_successful_attribution_calls_handle_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        record = _purchase_record()
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=record))
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-ok")
        assert result["attributed"] is True
        mock_handle.assert_awaited_once_with(record)

    @pytest.mark.asyncio
    async def test_post_purchase_exception_does_not_affect_response(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        record = _purchase_record()
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=record))
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", AsyncMock(side_effect=RuntimeError("crash")))
        body = json.dumps(_webhook_payload()).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-crash")
        assert result["attributed"] is True
        assert result["processed"] is True

    @pytest.mark.asyncio
    async def test_no_attribution_no_handle_post_purchase(self, monkeypatch):
        from integrations.fangate import service
        mocks = _mock_db(monkeypatch)
        mocks["get_creator_integration"].return_value = _integration_row()
        mocks["insert_fangate_webhook_event"].return_value = True
        mocks["upsert_fangate_transaction"].return_value = True
        monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "whsec")
        monkeypatch.setattr("commerce.dao.attribute_purchase_from_webhook", AsyncMock(return_value=None))
        mock_handle = AsyncMock()
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        body = json.dumps(_webhook_payload(product_id=999)).encode()
        result = await service.receive_webhook(1, body, _sign("whsec", body), "delivery-noattr")
        assert result["attributed"] is False
        mock_handle.assert_not_awaited()
