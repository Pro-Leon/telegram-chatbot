"""Vault media management tests — P2.1 audit coverage.

Covers:
A. Media aggregation from products (dedup, multi-product, empty)
B. Vault models serialization
C. URL validation in send pipeline (HTTPS enforcement)
D. API route behavior (dependency override)
E. Delivery recording integration (send pipeline → vault)
F. Send-media API endpoint
G. Creator isolation
H. Already-sent detection
I. Edge cases (missing media, empty products, concurrent delivery idempotency)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Test data ────────────────────────────────────────────────────────────────


def _raw_product(product_id=101, media=None, **overrides):
    """Build a raw product dict matching db.fangate.list_fangate_products output."""
    if media is None:
        media = [
            {"id": 201, "type": "image", "preview": "https://fangate.s3.amazonaws.com/img1.jpg"},
            {"id": 202, "type": "video", "preview": "https://fangate.s3.amazonaws.com/vid1.mp4"},
        ]
    base = {
        "id": product_id,
        "product_type": "digital",
        "title": f"Product {product_id}",
        "preview_url": f"https://fangate.s3.amazonaws.com/preview_{product_id}.jpg",
        "preview_blurred_url": None,
        "price_minor": 500,
        "in_collection": True,
        "sales_url": f"https://fangate.dev/link/{product_id}",
        "link_clicks": 10,
        "unlocks": 5,
        "total_earnings": 250,
        "folder_id": "folder_1",
        "folder_name": "Main Folder",
        "is_adult_content": False,
        "is_verif_age": False,
        "is_epoch_enabled": False,
        "is_should_consent": False,
        "is_downloadable": False,
        "is_accessible": True,
        "private_description": None,
        "public_description": None,
        "raw": {"media": media},
        "synced_at": "2026-08-23T00:00:00Z",
    }
    base.update(overrides)
    return base


# ── A. Media aggregation ────────────────────────────────────────────────────


class TestMediaAggregation:
    def test_basic_aggregation(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101)]
        items = aggregate_media_from_products(products)
        assert len(items) == 2
        assert items[0].media_id == 201
        assert items[0].media_type == "image"
        assert items[0].product_id == 101
        assert len(items[0].associated_products) == 1

    def test_deduplication_across_products(self):
        from vault.aggregate import aggregate_media_from_products

        shared_media = [
            {"id": 999, "type": "image", "preview": "https://example.com/shared.jpg"}
        ]
        products = [
            _raw_product(101, media=shared_media),
            _raw_product(102, media=shared_media),
        ]
        items = aggregate_media_from_products(products)
        assert len(items) == 1
        assert items[0].media_id == 999
        assert len(items[0].associated_products) == 2
        product_ids = {p.product_id for p in items[0].associated_products}
        assert product_ids == {101, 102}

    def test_empty_products(self):
        from vault.aggregate import aggregate_media_from_products

        items = aggregate_media_from_products([])
        assert items == []

    def test_product_with_no_media(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101, media=[])]
        items = aggregate_media_from_products(products)
        assert items == []

    def test_delivery_overlay(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101)]
        delivered = {201}
        items = aggregate_media_from_products(products, delivered_ids=delivered)
        img_item = next(i for i in items if i.media_id == 201)
        vid_item = next(i for i in items if i.media_id == 202)
        assert img_item.delivery_count == 1
        assert vid_item.delivery_count == 0

    def test_delivery_overlay_none_skips(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101)]
        items = aggregate_media_from_products(products, delivered_ids=None)
        for item in items:
            assert item.delivery_count == 0

    def test_folder_and_collection_preserved(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101, folder_id="f2", folder_name="Special")]
        items = aggregate_media_from_products(products)
        assert items[0].folder_id == "f2"
        assert items[0].folder_name == "Special"
        assert items[0].in_collection is True

    def test_invalid_media_entry_skipped(self):
        from vault.aggregate import aggregate_media_from_products

        media = [
            {"id": 1, "type": "image", "preview": "https://example.com/a.jpg"},
            {"no_id": True, "type": "image"},
            "not_a_dict",
            {"id": 2, "type": "video", "preview": "https://example.com/b.mp4"},
        ]
        products = [_raw_product(101, media=media)]
        items = aggregate_media_from_products(products)
        assert len(items) == 2
        assert [i.media_id for i in items] == [1, 2]

    def test_media_from_raw_json_string(self):
        """raw field can be a JSON string (from DB serialization)."""
        import json

        from vault.aggregate import aggregate_media_from_products

        media = [{"id": 50, "type": "image", "preview": "https://example.com/x.jpg"}]
        product = _raw_product(101, media=media)
        product["raw"] = json.dumps({"media": media})
        items = aggregate_media_from_products([product])
        assert len(items) == 1
        assert items[0].media_id == 50


# ── B. Model serialization ──────────────────────────────────────────────────


class TestVaultModels:
    def test_vault_media_item_to_dict(self):
        from vault.models import VaultMediaItem, VaultProductRef

        item = VaultMediaItem(
            media_id=1,
            media_type="image",
            preview="https://example.com/img.jpg",
            product_id=10,
            associated_products=[VaultProductRef(10, "P10", 500)],
        )
        d = item.to_dict()
        assert d["media_id"] == 1
        assert d["media_type"] == "image"
        assert len(d["associated_products"]) == 1
        assert d["associated_products"][0]["product_id"] == 10

    def test_vault_product_to_dict(self):
        from vault.models import VaultProduct

        p = VaultProduct(id=1, title="Test", media_count=3)
        d = p.to_dict()
        assert d["id"] == 1
        assert d["media_count"] == 3

    def test_vault_delivery_record_to_dict(self):
        from vault.models import VaultDeliveryRecord

        r = VaultDeliveryRecord(id=1, creator_id=10, user_id=20, fangate_media_id=30)
        d = r.to_dict()
        assert d["fangate_media_id"] == 30


# ── C. URL validation in send pipeline ──────────────────────────────────────


class TestUrlMediaValidation:
    def test_https_url_accepted(self):
        from chatbotv2.main import _validate_media_path

        url = "https://fangate.s3.amazonaws.com/media/123.jpg"
        assert _validate_media_path(url) == url

    def test_http_url_rejected(self):
        from chatbotv2.main import _validate_media_path

        url = "http://fangate.s3.amazonaws.com/media/123.jpg"
        assert _validate_media_path(url) is None

    def test_empty_string_rejected(self):
        from chatbotv2.main import _validate_media_path

        assert _validate_media_path("") is None
        assert _validate_media_path("   ") is None

    def test_invalid_local_path_rejected(self):
        from chatbotv2.main import _validate_media_path

        assert _validate_media_path("/nonexistent/file.jpg") is None

    def test_local_file_accepted(self, tmp_path):
        from chatbotv2.main import _validate_media_path

        f = tmp_path / "test.jpg"
        f.write_bytes(b"fake image data")
        assert _validate_media_path(str(f)) == str(f)

    def test_path_traversal_rejected(self):
        from chatbotv2.main import _validate_media_path

        assert _validate_media_path("../../../etc/passwd") is None

    def test_ftp_url_rejected(self):
        from chatbotv2.main import _validate_media_path

        assert _validate_media_path("ftp://example.com/file.jpg") is None


# ── D. API routes (dependency override) ─────────────────────────────────────


class TestVaultRoutes:
    def _make_app(self):
        from fastapi import FastAPI

        from chatbotv2.dashboard.auth import require_auth
        from chatbotv2.dashboard.routes.vault import router

        app = FastAPI()
        app.include_router(router)
        self._auth_func = require_auth
        return app

    @pytest.mark.asyncio
    async def test_list_media_route(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        mock_result = {
            "items": [{"media_id": 1, "media_type": "image"}],
            "total_products": 1,
            "limit": 50,
            "offset": 0,
        }

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.vault_svc") as mock_svc:
            mock_svc.list_media = AsyncMock(return_value=mock_result)
            client = TestClient(app)
            resp = client.get("/api/vault/media")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 1

    @pytest.mark.asyncio
    async def test_get_media_not_found(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.vault_svc") as mock_svc:
            mock_svc.get_media = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/vault/media/999")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_check_delivery_route(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.vault_svc") as mock_svc:
            mock_svc.has_fan_received_media = AsyncMock(return_value=True)
            client = TestClient(app)
            resp = client.get(
                "/api/vault/deliveries/check",
                params={"user_id": 123, "fangate_media_id": 456},
            )
            assert resp.status_code == 200
            assert resp.json()["received"] is True

    @pytest.mark.asyncio
    async def test_list_products_route(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        mock_result = {"items": [{"id": 1, "title": "P1"}], "total": 1, "limit": 100, "offset": 0}
        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.vault_svc") as mock_svc:
            mock_svc.list_products = AsyncMock(return_value=mock_result)
            client = TestClient(app)
            resp = client.get("/api/vault/products")
            assert resp.status_code == 200
            assert resp.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_list_folders_route(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.vault_svc") as mock_svc:
            mock_svc.list_folders = AsyncMock(return_value=[{"id": "f1", "name": "Folder 1"}])
            client = TestClient(app)
            resp = client.get("/api/vault/folders")
            assert resp.status_code == 200
            assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_send_media_validates_required_fields(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={"user_id": 123})
            assert resp.status_code == 400
            assert "required" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_send_media_validates_https(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
                "media_type": "photo",
                "media_path": "http://insecure.com/img.jpg",
                "fangate_media_id": 456,
            })
            assert resp.status_code == 400
            assert "HTTPS" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_send_media_validates_media_type(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
                "media_type": "invalid",
                "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
                "fangate_media_id": 456,
            })
            assert resp.status_code == 400
            assert "photo" in resp.json()["error"]


# ── E. Delivery recording in send pipeline ──────────────────────────────────


class TestDeliveryRecording:
    @pytest.mark.asyncio
    async def test_vault_record_called_when_fangate_media_id_present(self):
        """When send stream data includes fangate_media_id, vault delivery is recorded via reserve+finalize."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99001
        mock_client.send_message = AsyncMock(return_value=msg_result)
        mock_client.get_input_entity = AsyncMock(return_value=MagicMock())

        data = {
            "entity": "12345",
            "content": "Hello",
            "draft_content": "Hello",
            "was_edited": "",
            "was_auto_approved": "",
            "confidence_score": "0",
            "operator_id": "",
            "save_to_db": "true",
            "media_type": "photo",
            "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
            "fangate_media_id": "777",
            "product_id": "101",
            "creator_id": "1",
        }

        read_send = AsyncMock(return_value=[("send_main", [("msg_001", data)])])
        reserve_mock = AsyncMock(return_value=42)
        finalize_mock = AsyncMock(return_value=True)

        with patch("chatbotv2.main.read_send_messages", read_send), \
             patch("chatbotv2.main.ack_send", AsyncMock()), \
             patch("chatbotv2.main.is_send_duplicate", AsyncMock(return_value=False)), \
             patch("chatbotv2.main.get_send_rate_limit_wait", AsyncMock(return_value=0)), \
             patch("chatbotv2.main.check_send_rate_limit", AsyncMock(return_value=True)), \
             patch("chatbotv2.main.requeue_stalled_send_messages", AsyncMock(return_value=(0, []))), \
             patch("chatbotv2.main.save_outbound_after_send", AsyncMock()), \
             patch("chatbotv2.main.publish_event", AsyncMock()), \
             patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.move_send_to_dlq", AsyncMock()), \
             patch("chatbotv2.main.send_file", AsyncMock(return_value=msg_result)), \
             patch("chatbotv2.main._validate_media_path", return_value="https://fangate.s3.amazonaws.com/img.jpg"), \
             patch("chatbotv2.main.release_stale_reservations", AsyncMock(return_value=[])), \
             patch("db.vault.reserve_delivery", reserve_mock), \
             patch("db.vault.finalize_delivery", finalize_mock):
            await _process_send_stream(mock_client)

        reserve_mock.assert_called_once_with(1, 12345, 777, product_id=101)
        # M7 (B8): finalize is ownership-scoped with the stream creator.
        finalize_mock.assert_called_once_with(42, telegram_message_id=99001, creator_id=1)

    @pytest.mark.asyncio
    async def test_no_vault_record_when_no_fangate_media_id(self):
        """Without fangate_media_id, vault delivery is NOT recorded."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99002
        mock_client.send_message = AsyncMock(return_value=msg_result)

        data = {
            "entity": "12345",
            "content": "Hello",
            "draft_content": "Hello",
            "was_edited": "",
            "was_auto_approved": "",
            "confidence_score": "0",
            "operator_id": "",
            "save_to_db": "true",
            "media_type": "",
            "media_path": "",
        }

        read_send = AsyncMock(return_value=[("send_main", [("msg_002", data)])])
        vault_record = AsyncMock()

        with patch("chatbotv2.main.read_send_messages", read_send), \
             patch("chatbotv2.main.ack_send", AsyncMock()), \
             patch("chatbotv2.main.is_send_duplicate", AsyncMock(return_value=False)), \
             patch("chatbotv2.main.get_send_rate_limit_wait", AsyncMock(return_value=0)), \
             patch("chatbotv2.main.check_send_rate_limit", AsyncMock(return_value=True)), \
             patch("chatbotv2.main.requeue_stalled_send_messages", AsyncMock(return_value=(0, []))), \
             patch("chatbotv2.main.save_outbound_after_send", AsyncMock()), \
             patch("chatbotv2.main.publish_event", AsyncMock()), \
             patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.move_send_to_dlq", AsyncMock()), \
             patch("chatbotv2.main.send_file", AsyncMock(return_value=msg_result)), \
             patch("chatbotv2.main.release_stale_reservations", AsyncMock(return_value=[])), \
             patch("db.vault.record_delivery", vault_record):
            await _process_send_stream(mock_client)

        vault_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_vault_record_failure_does_not_break_send(self):
        """If vault delivery recording fails, the send still succeeds."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99003
        mock_client.send_message = AsyncMock(return_value=msg_result)
        mock_client.get_input_entity = AsyncMock(return_value=MagicMock())

        data = {
            "entity": "12345",
            "content": "Hello",
            "draft_content": "Hello",
            "was_edited": "",
            "was_auto_approved": "",
            "confidence_score": "0",
            "operator_id": "",
            "save_to_db": "true",
            "media_type": "photo",
            "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
            "fangate_media_id": "777",
            "product_id": "101",
            "creator_id": "1",
        }

        read_send = AsyncMock(return_value=[("send_main", [("msg_003", data)])])
        vault_record = AsyncMock(side_effect=Exception("DB connection lost"))

        with patch("chatbotv2.main.read_send_messages", read_send), \
             patch("chatbotv2.main.ack_send", AsyncMock()) as mock_ack, \
             patch("chatbotv2.main.is_send_duplicate", AsyncMock(return_value=False)), \
             patch("chatbotv2.main.get_send_rate_limit_wait", AsyncMock(return_value=0)), \
             patch("chatbotv2.main.check_send_rate_limit", AsyncMock(return_value=True)), \
             patch("chatbotv2.main.requeue_stalled_send_messages", AsyncMock(return_value=(0, []))), \
             patch("chatbotv2.main.save_outbound_after_send", AsyncMock()), \
             patch("chatbotv2.main.publish_event", AsyncMock()), \
             patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.move_send_to_dlq", AsyncMock()), \
             patch("chatbotv2.main.send_file", AsyncMock(return_value=msg_result)), \
             patch("chatbotv2.main._validate_media_path", return_value="https://fangate.s3.amazonaws.com/img.jpg"), \
             patch("chatbotv2.main.release_stale_reservations", AsyncMock(return_value=[])), \
             patch("db.vault.reserve_delivery", AsyncMock(return_value=123)), \
             patch("db.vault.finalize_delivery", AsyncMock(return_value=True)), \
             patch("db.vault.record_delivery", vault_record):
            await _process_send_stream(mock_client)

        # Send should still be acked even if vault recording fails
        mock_ack.assert_called()


# ── F. Send-media endpoint ──────────────────────────────────────────────────


class TestSendMediaEndpoint:
    def _make_app(self):
        from fastapi import FastAPI

        from chatbotv2.dashboard.auth import require_auth
        from chatbotv2.dashboard.routes.vault import router

        app = FastAPI()
        app.include_router(router)
        self._auth_func = require_auth
        return app

    @pytest.mark.asyncio
    async def test_send_media_enqueues(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        auth_data = {"username": "admin"}
        app.dependency_overrides[self._auth_func] = lambda: auth_data

        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.enqueue_send", new_callable=AsyncMock) as mock_enqueue:
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
                "media_type": "photo",
                "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
                "fangate_media_id": 456,
                "product_id": 789,
                "caption": "Check this out",
            })
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            mock_enqueue.assert_called_once()
            call_args = mock_enqueue.call_args
            data = call_args[0][0]
            assert data["entity"] == "123"
            assert data["media_type"] == "photo"
            assert data["media_path"] == "https://fangate.s3.amazonaws.com/img.jpg"
            assert data["fangate_media_id"] == "456"
            assert data["creator_id"] == "1"


# ── G. Creator isolation ────────────────────────────────────────────────────


class TestCreatorIsolation:
    def test_require_creator_id_resolves_from_integration(self):
        """_require_creator_id resolves creator_id from the integration table."""
        import asyncio

        from chatbotv2.dashboard.routes.vault import _require_creator_id

        with (
            patch("db.fangate.list_active_creator_ids", new_callable=AsyncMock, return_value=[]),
            patch("db.fangate.get_any_creator_id_with_integration", new_callable=AsyncMock, return_value=42),
        ):
            result = asyncio.run(
                _require_creator_id({"username": "admin"})
            )
            assert result == 42

    def test_require_creator_id_prefers_active_integration(self):
        """M6: an active integration wins over the legacy any-status lookup."""
        import asyncio

        from chatbotv2.dashboard.routes.vault import _require_creator_id

        with (
            patch("db.fangate.list_active_creator_ids", new_callable=AsyncMock, return_value=[2]),
            patch("db.fangate.get_any_creator_id_with_integration", new_callable=AsyncMock, return_value=1),
        ):
            result = asyncio.run(
                _require_creator_id({"username": "admin"})
            )
            assert result == 2

    def test_require_creator_id_raises_when_no_integration(self):
        """_require_creator_id raises ValueError when no integration exists."""
        import asyncio

        from chatbotv2.dashboard.routes.vault import _require_creator_id

        with (
            patch("db.fangate.list_active_creator_ids", new_callable=AsyncMock, return_value=[]),
            patch("db.fangate.get_any_creator_id_with_integration", new_callable=AsyncMock, return_value=None),
        ):
            with pytest.raises(ValueError, match="No Fangate integration"):
                asyncio.run(
                    _require_creator_id({"username": "admin"})
                )


# ── H. Already-sent detection ───────────────────────────────────────────────


class TestAlreadySentDetection:
    def test_delivery_overlay_marks_delivered(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101)]
        delivered = {201, 202}
        items = aggregate_media_from_products(products, delivered_ids=delivered)
        for item in items:
            assert item.delivery_count == 1

    def test_delivery_overlay_empty_set(self):
        from vault.aggregate import aggregate_media_from_products

        products = [_raw_product(101)]
        items = aggregate_media_from_products(products, delivered_ids=set())
        for item in items:
            assert item.delivery_count == 0


# ── I. Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_media_preserves_all_product_refs(self):
        """A media item belonging to 3 products has 3 associated_products."""
        from vault.aggregate import aggregate_media_from_products

        shared = [{"id": 100, "type": "image", "preview": "https://example.com/x.jpg"}]
        products = [
            _raw_product(1, media=shared, title="P1"),
            _raw_product(2, media=shared, title="P2"),
            _raw_product(3, media=shared, title="P3"),
        ]
        items = aggregate_media_from_products(products)
        assert len(items) == 1
        assert len(items[0].associated_products) == 3

    def test_vault_product_ref_fields(self):
        from vault.models import VaultProductRef

        ref = VaultProductRef(product_id=42, title="Test", price=999)
        assert ref.product_id == 42
        assert ref.title == "Test"
        assert ref.price == 999

    def test_vault_media_item_defaults(self):
        from vault.models import VaultMediaItem

        item = VaultMediaItem(media_id=1)
        assert item.delivery_count == 0
        assert item.associated_products == []
        assert item.in_collection is False


# ── J. _Msg wrapper telegram_message_id ────────────────────────────────────


class TestMsgWrapper:
    def test_msg_has_id_attribute(self):
        """_Msg wrapper exposes .id for telegram_message_id persistence."""
        from unittest.mock import MagicMock
        from chatbotv2.client import send_message, send_file
        import asyncio

        telethon_msg = MagicMock()
        telethon_msg.id = 12345

        # Test send_message wrapper
        with patch("chatbotv2.client.get_client", new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.send_message = AsyncMock(return_value=telethon_msg)
            mock_get.return_value = mock_client
            result = asyncio.run(send_message("12345", "test"))
            assert result.id == 12345
            assert result.message_id == 12345

    def test_msg_wrapper_send_file(self):
        """send_file _Msg wrapper also exposes .id."""
        from unittest.mock import MagicMock
        from chatbotv2.client import send_file
        import asyncio

        telethon_msg = MagicMock()
        telethon_msg.id = 67890

        with patch("chatbotv2.client.get_client", new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.send_file = AsyncMock(return_value=telethon_msg)
            mock_get.return_value = mock_client
            result = asyncio.run(send_file("12345", "file.jpg"))
            assert result.id == 67890
            assert result.message_id == 67890


# ── K. vault.media_sent event publishing ────────────────────────────────────


class TestVaultMediaSentEvent:
    @pytest.mark.asyncio
    async def test_vault_media_sent_published_after_delivery(self):
        """When fangate_media_id present, vault.media_sent event is published."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99010
        mock_client.send_message = AsyncMock(return_value=msg_result)
        mock_client.get_input_entity = AsyncMock(return_value=MagicMock())

        data = {
            "entity": "55555",
            "content": "Enjoy!",
            "draft_content": "Enjoy!",
            "was_edited": "",
            "was_auto_approved": "",
            "confidence_score": "0",
            "operator_id": "",
            "save_to_db": "true",
            "media_type": "photo",
            "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
            "fangate_media_id": "888",
            "product_id": "202",
            "creator_id": "1",
        }

        read_send = AsyncMock(return_value=[("send_main", [("msg_010", data)])])
        publish_calls = []

        async def mock_publish(event_type, evt_data, **kwargs):
            publish_calls.append((event_type, evt_data, kwargs))

        with patch("chatbotv2.main.read_send_messages", read_send), \
             patch("chatbotv2.main.ack_send", AsyncMock()), \
             patch("chatbotv2.main.is_send_duplicate", AsyncMock(return_value=False)), \
             patch("chatbotv2.main.get_send_rate_limit_wait", AsyncMock(return_value=0)), \
             patch("chatbotv2.main.check_send_rate_limit", AsyncMock(return_value=True)), \
             patch("chatbotv2.main.requeue_stalled_send_messages", AsyncMock(return_value=(0, []))), \
             patch("chatbotv2.main.save_outbound_after_send", AsyncMock()), \
             patch("chatbotv2.main.publish_event", side_effect=mock_publish), \
             patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.move_send_to_dlq", AsyncMock()), \
             patch("chatbotv2.main.send_file", AsyncMock(return_value=msg_result)), \
             patch("chatbotv2.main._validate_media_path", return_value="https://fangate.s3.amazonaws.com/img.jpg"), \
             patch("chatbotv2.main.release_stale_reservations", AsyncMock(return_value=[])), \
             patch("db.vault.reserve_delivery", AsyncMock(return_value=999)), \
             patch("db.vault.finalize_delivery", AsyncMock(return_value=True)), \
             patch("db.vault.record_delivery", AsyncMock()), \
             patch("db.vault.has_user_received_media", new_callable=AsyncMock, return_value=False):
            await _process_send_stream(mock_client)

        event_types = [c[0] for c in publish_calls]
        assert "message.sent" in event_types
        assert "vault.media_sent" in event_types

        vault_evt = next(c for c in publish_calls if c[0] == "vault.media_sent")
        assert vault_evt[1]["fangate_media_id"] == "888"
        assert vault_evt[1]["product_id"] == "202"
        assert vault_evt[1]["user_id"] == 55555

    @pytest.mark.asyncio
    async def test_vault_media_sent_not_published_without_fangate_id(self):
        """Without fangate_media_id, vault.media_sent is NOT published."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99011
        mock_client.send_message = AsyncMock(return_value=msg_result)

        data = {
            "entity": "55555",
            "content": "Hello",
            "draft_content": "Hello",
            "was_edited": "",
            "was_auto_approved": "",
            "confidence_score": "0",
            "operator_id": "",
            "save_to_db": "true",
            "media_type": "",
            "media_path": "",
        }

        read_send = AsyncMock(return_value=[("send_main", [("msg_011", data)])])
        publish_calls = []

        async def mock_publish(event_type, evt_data, **kwargs):
            publish_calls.append((event_type, evt_data, kwargs))

        with patch("chatbotv2.main.read_send_messages", read_send), \
             patch("chatbotv2.main.ack_send", AsyncMock()), \
             patch("chatbotv2.main.is_send_duplicate", AsyncMock(return_value=False)), \
             patch("chatbotv2.main.get_send_rate_limit_wait", AsyncMock(return_value=0)), \
             patch("chatbotv2.main.check_send_rate_limit", AsyncMock(return_value=True)), \
             patch("chatbotv2.main.requeue_stalled_send_messages", AsyncMock(return_value=(0, []))), \
             patch("chatbotv2.main.save_outbound_after_send", AsyncMock()), \
             patch("chatbotv2.main.publish_event", side_effect=mock_publish), \
             patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.move_send_to_dlq", AsyncMock()), \
             patch("chatbotv2.main.send_file", AsyncMock(return_value=msg_result)), \
             patch("chatbotv2.main.release_stale_reservations", AsyncMock(return_value=[])), \
             patch("db.vault.record_delivery", AsyncMock()):
            await _process_send_stream(mock_client)

        event_types = [c[0] for c in publish_calls]
        assert "vault.media_sent" not in event_types


# ── L. Service filtering ────────────────────────────────────────────────────


class TestServiceFiltering:
    @pytest.mark.asyncio
    async def test_list_media_filters_by_product_id(self):
        """list_media correctly filters by product_id."""
        from vault.service import list_media

        products = [_raw_product(101), _raw_product(102)]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=2):
            result = await list_media(1, product_id=101)
            items = result["items"]
            assert all(i["product_id"] == 101 for i in items)

    @pytest.mark.asyncio
    async def test_list_media_filters_by_media_type(self):
        """list_media correctly filters by media_type."""
        from vault.service import list_media

        products = [_raw_product(101)]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=1):
            result = await list_media(1, media_type="video")
            items = result["items"]
            assert all(i["media_type"] == "video" for i in items)

    @pytest.mark.asyncio
    async def test_list_media_filters_by_folder_id(self):
        """list_media correctly filters by folder_id."""
        from vault.service import list_media

        products = [_raw_product(101, folder_id="f1"), _raw_product(102, folder_id="f2")]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=2):
            result = await list_media(1, folder_id="f1")
            items = result["items"]
            assert all(i["folder_id"] == "f1" for i in items)

    @pytest.mark.asyncio
    async def test_list_media_filters_collection_only(self):
        """list_media correctly filters collection_only."""
        from vault.service import list_media

        products = [_raw_product(101, in_collection=True), _raw_product(102, in_collection=False)]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=2):
            result = await list_media(1, collection_only=True)
            items = result["items"]
            assert all(i["in_collection"] for i in items)

    @pytest.mark.asyncio
    async def test_list_media_combined_filters(self):
        """list_media works with multiple filters."""
        from vault.service import list_media

        products = [_raw_product(101, folder_id="f1"), _raw_product(102, folder_id="f2")]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=2):
            result = await list_media(1, folder_id="f1", media_type="video")
            items = result["items"]
            assert all(i["folder_id"] == "f1" and i["media_type"] == "video" for i in items)


# ── M. Bulk delivery state ──────────────────────────────────────────────────


class TestBulkDeliveryState:
    @pytest.mark.asyncio
    async def test_list_media_with_user_shows_delivery_count(self):
        """When user_id is provided, delivery_count reflects sent status."""
        from vault.service import list_media

        products = [_raw_product(101)]
        delivered_map = {201: {"sent_at": "2026-08-23T00:00:00Z", "status": "sent"}}
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=1), \
             patch("db.vault.get_delivered_media_map", new_callable=AsyncMock, return_value=delivered_map):
            result = await list_media(1, user_id=123)
            items = result["items"]
            sent_item = next(i for i in items if i["media_id"] == 201)
            unsent_item = next(i for i in items if i["media_id"] == 202)
            assert sent_item["delivery_count"] == 1
            assert sent_item["sent_at"] == "2026-08-23T00:00:00Z"
            assert unsent_item["delivery_count"] == 0

    @pytest.mark.asyncio
    async def test_list_media_without_user_no_delivery_query(self):
        """When user_id is None, no delivery query is made."""
        from vault.service import list_media

        products = [_raw_product(101)]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.fangate.count_fangate_products", new_callable=AsyncMock, return_value=1), \
             patch("db.vault.get_delivered_media_map", new_callable=AsyncMock) as mock_del:
            result = await list_media(1, user_id=None)
            mock_del.assert_not_called()
            items = result["items"]
            for item in items:
                assert item["delivery_count"] == 0


# ── N. send_media endpoint edge cases ───────────────────────────────────────


class TestSendMediaEdgeCases:
    def _make_app(self):
        from fastapi import FastAPI
        from chatbotv2.dashboard.auth import require_auth
        from chatbotv2.dashboard.routes.vault import router
        app = FastAPI()
        app.include_router(router)
        self._auth_func = require_auth
        return app

    @pytest.mark.asyncio
    async def test_send_media_rejects_http_url(self):
        """send-media rejects non-HTTPS URLs."""
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.dependency_overrides[self._auth_func] = lambda: {"username": "admin"}
        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
                "media_type": "photo",
                "media_path": "http://insecure.com/img.jpg",
                "fangate_media_id": 456,
            })
            assert resp.status_code == 400
            assert "HTTPS" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_send_media_rejects_invalid_type(self):
        """send-media rejects unknown media_type."""
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.dependency_overrides[self._auth_func] = lambda: {"username": "admin"}
        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
                "media_type": "image",
                "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
                "fangate_media_id": 456,
            })
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_send_media_rejects_empty_fields(self):
        """send-media rejects missing required fields."""
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.dependency_overrides[self._auth_func] = lambda: {"username": "admin"}
        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1):
            client = TestClient(app)
            resp = client.post("/api/vault/send-media", json={
                "user_id": 123,
            })
            assert resp.status_code == 400
            assert "required" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_send_media_dedup_id_stable(self):
        """Same user+media+caption produces same dedup_id (M6: caption-bound)."""
        from fastapi.testclient import TestClient
        import hashlib
        app = self._make_app()
        app.dependency_overrides[self._auth_func] = lambda: {"username": "admin"}
        with patch("chatbotv2.dashboard.routes.vault._require_creator_id", new_callable=AsyncMock, return_value=1), \
             patch("chatbotv2.dashboard.routes.vault.enqueue_send", new_callable=AsyncMock) as mock_enqueue:
            client = TestClient(app)
            payload = {
                "user_id": 123,
                "media_type": "photo",
                "media_path": "https://fangate.s3.amazonaws.com/img.jpg",
                "fangate_media_id": 456,
            }
            resp = client.post("/api/vault/send-media", json=payload)
            assert resp.status_code == 200
            first_dedup = mock_enqueue.call_args[1]["dedup_id"]

            resp2 = client.post("/api/vault/send-media", json=payload)
            assert resp2.status_code == 200
            second_dedup = mock_enqueue.call_args[1]["dedup_id"]

            assert first_dedup == second_dedup
            expected = hashlib.md5("123:456:".encode()).hexdigest()
            assert first_dedup == expected
