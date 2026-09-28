"""Gap 2 — Post-purchase Vault media delivery tests.

Covers:
    A: Purchased product with one media item
    B: Purchased product with multiple media items
    C: Product with no media
    D: Product not found
    E: Invalid/non-HTTPS media skipped
    F: Reservation succeeds -> message is enqueued
    G: Reservation conflict -> no duplicate enqueue
    H: Multiple media items have independent reservations
    I: Correct creator_id
    J: Correct user_id
    K: Correct fangate_media_id
    L: Delivery failure does not roll back purchase handling
    M: Queue failure is handled according to existing semantics
    N: Existing send worker remains responsible for finalization
"""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _media_item(media_id, media_type="image", preview="https://fangate.s3.amazonaws.com/file.jpg"):
    return {"id": media_id, "type": media_type, "preview": preview}


def _product_with_media(product_id=101, media=None):
    if media is None:
        media = [_media_item(201)]
    return {
        "id": product_id,
        "title": f"Product {product_id}",
        "raw": {"media": media},
    }


def _purchase_record(creator_id=1, user_id=42, transaction_id="txn_del_001", product_id=101):
    from commerce.models import PurchaseRecord

    return PurchaseRecord(
        offer_id=101,
        creator_id=creator_id,
        user_id=user_id,
        transaction_id=transaction_id,
        product_id=product_id,
    )


# ── A. Purchased product with one media item ─────────────────────────────────


class TestSingleMediaDelivery:
    @pytest.mark.asyncio
    async def test_one_media_reserved_and_enqueued(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_001")

        mock_reserve.assert_awaited_once_with(1, 42, 201, 101)
        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.call_args
        payload = call_kwargs[0][0]
        assert payload["fangate_media_id"] == "201"
        assert payload["media_type"] == "photo"
        assert payload["media_path"] == "https://fangate.s3.amazonaws.com/file.jpg"
        assert payload["creator_id"] == "1"
        assert payload["entity"] == "42"
        assert payload["product_id"] == "101"


# ── B. Purchased product with multiple media items ───────────────────────────


class TestMultipleMediaDelivery:
    @pytest.mark.asyncio
    async def test_multiple_media_each_reserved(self):
        from commerce.post_purchase import deliver_product_media

        media = [
            _media_item(201, "image", "https://fangate.s3.amazonaws.com/img1.jpg"),
            _media_item(202, "video", "https://fangate.s3.amazonaws.com/vid1.mp4"),
        ]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_multi")

        assert mock_enqueue.await_count == 2
        ids = {call[0][0]["fangate_media_id"] for call in mock_enqueue.call_args_list}
        assert ids == {"201", "202"}


# ── C. Product with no media ─────────────────────────────────────────────────


class TestNoMediaDelivery:
    @pytest.mark.asyncio
    async def test_empty_media_list(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_empty")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_media_list(self):
        from commerce.post_purchase import deliver_product_media

        product = {"id": 101, "title": "P", "raw": {"media": None}}

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_none")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()


# ── D. Product not found ─────────────────────────────────────────────────────


class TestProductNotFound:
    @pytest.mark.asyncio
    async def test_product_not_found_skips(self):
        from commerce.post_purchase import deliver_product_media

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=None)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 999, "txn_del_nf")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()


# ── E. Invalid/non-HTTPS media skipped ───────────────────────────────────────


class TestInvalidMediaSkipped:
    @pytest.mark.asyncio
    async def test_http_url_skipped(self):
        from commerce.post_purchase import deliver_product_media

        media = [_media_item(201, "image", "http://insecure.com/file.jpg")]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_http")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_preview_skipped(self):
        from commerce.post_purchase import deliver_product_media

        media = [_media_item(201, "image", "")]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_empty_url")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsupported_media_type_skipped(self):
        from commerce.post_purchase import deliver_product_media

        media = [_media_item(201, "animation", "https://fangate.s3.amazonaws.com/anim.gif")]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_bad_type")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_maps_to_photo(self):
        from commerce.post_purchase import deliver_product_media

        media = [_media_item(201, "image", "https://fangate.s3.amazonaws.com/img.jpg")]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_img")

        payload = mock_enqueue.call_args[0][0]
        assert payload["media_type"] == "photo"


# ── F. Reservation succeeds -> message is enqueued ───────────────────────────


class TestReservationEnqueueSuccess:
    @pytest.mark.asyncio
    async def test_reserve_returns_id_then_enqueued(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=42)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_rsv")

        mock_enqueue.assert_awaited_once()


# ── G. Reservation conflict -> no duplicate enqueue ──────────────────────────


class TestReservationConflict:
    @pytest.mark.asyncio
    async def test_reserve_returns_none_skips(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=None)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_conflict")

        mock_enqueue.assert_not_awaited()


# ── H. Multiple media items have independent reservations ────────────────────


class TestIndependentReservations:
    @pytest.mark.asyncio
    async def test_each_media_independent_reserve(self):
        from commerce.post_purchase import deliver_product_media

        media = [
            _media_item(201, "image", "https://fangate.s3.amazonaws.com/img1.jpg"),
            _media_item(202, "video", "https://fangate.s3.amazonaws.com/vid1.mp4"),
            _media_item(203, "image", "https://fangate.s3.amazonaws.com/img2.jpg"),
        ]
        product = _product_with_media(101, media)

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(side_effect=[1, 2, 3])),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_ind")

        assert mock_enqueue.await_count == 3
        fangate_ids = [call[0][0]["fangate_media_id"] for call in mock_enqueue.call_args_list]
        assert fangate_ids == ["201", "202", "203"]


# ── I. Correct creator_id ────────────────────────────────────────────────────


class TestCorrectCreatorId:
    @pytest.mark.asyncio
    async def test_creator_id_in_payload(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(77, 42, 101, "txn_del_cid")

        payload = mock_enqueue.call_args[0][0]
        assert payload["creator_id"] == "77"


# ── J. Correct user_id ───────────────────────────────────────────────────────


class TestCorrectUserId:
    @pytest.mark.asyncio
    async def test_user_id_in_entity(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 888, 101, "txn_del_uid")

        payload = mock_enqueue.call_args[0][0]
        assert payload["entity"] == "888"


# ── K. Correct fangate_media_id ──────────────────────────────────────────────


class TestCorrectFangateMediaId:
    @pytest.mark.asyncio
    async def test_fangate_media_id_in_payload(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(999)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_fmid")

        payload = mock_enqueue.call_args[0][0]
        assert payload["fangate_media_id"] == "999"


# ── L. Delivery failure does not roll back purchase handling ─────────────────


class TestDeliveryFailureIsolation:
    @pytest.mark.asyncio
    async def test_deliver_product_media_exception_does_not_propagate(self):
        from commerce.post_purchase import deliver_product_media

        with patch("db.fangate.get_fangate_product", AsyncMock(side_effect=RuntimeError("db boom"))):
            # Should not raise
            await deliver_product_media(1, 42, 101, "txn_del_fail")

    @pytest.mark.asyncio
    async def test_handle_post_purchase_continues_when_delivery_fails(self):
        from commerce.post_purchase import handle_post_purchase

        record = _purchase_record()

        with (
            patch("commerce.post_purchase.advance_funnel_to_converted", AsyncMock(return_value=True)),
            patch("commerce.post_purchase.enqueue_purchase_confirmation", AsyncMock(return_value=True)),
            patch("commerce.post_purchase.schedule_follow_up", AsyncMock(return_value=True)),
            patch("commerce.post_purchase.deliver_product_media", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            # Should not raise
            await handle_post_purchase(record)

    @pytest.mark.asyncio
    async def test_deliver_product_media_db_failure_does_not_propagate(self):
        from commerce.post_purchase import deliver_product_media

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_dbfail")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()


# ── M. Queue failure is handled according to existing semantics ──────────────


class TestQueueFailureHandling:
    @pytest.mark.asyncio
    async def test_enqueue_failure_releases_reservation(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=5)),
            patch("db.redis.enqueue_send", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch("db.vault.release_delivery", AsyncMock()) as mock_release,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_qfail")

        mock_release.assert_awaited_once_with(5, creator_id=1)

    @pytest.mark.asyncio
    async def test_enqueue_failure_release_also_fails_does_not_propagate(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=5)),
            patch("db.redis.enqueue_send", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch("db.vault.release_delivery", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            # Should not raise even if release also fails
            await deliver_product_media(1, 42, 101, "txn_del_qfail2")


# ── N. Existing send worker remains responsible for finalization ─────────────


class TestSendWorkerFinalization:
    @pytest.mark.asyncio
    async def test_deliver_only_enqueues_does_not_finalize(self):
        """Post-purchase delivery only enqueues; the send worker finalizes."""
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=False)),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.finalize_delivery", AsyncMock()) as mock_finalize,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_nofinalize")

        mock_enqueue.assert_awaited_once()
        mock_finalize.assert_not_awaited()


# ── Already-delivered media is skipped ────────────────────────────────────────


class TestAlreadyDeliveredMedia:
    @pytest.mark.asyncio
    async def test_already_delivered_skips_reserve(self):
        from commerce.post_purchase import deliver_product_media

        product = _product_with_media(101, [_media_item(201)])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", AsyncMock(return_value=True)),
            patch("db.vault.reserve_delivery", AsyncMock()) as mock_reserve,
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
        ):
            await deliver_product_media(1, 42, 101, "txn_del_already")

        mock_reserve.assert_not_awaited()
        mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_delivered_and_undelivered(self):
        from commerce.post_purchase import deliver_product_media

        media = [
            _media_item(201, "image", "https://fangate.s3.amazonaws.com/img1.jpg"),
            _media_item(202, "video", "https://fangate.s3.amazonaws.com/vid1.mp4"),
        ]
        product = _product_with_media(101, media)

        # First media already delivered, second not
        delivered_check = AsyncMock(side_effect=[True, False])

        with (
            patch("db.fangate.get_fangate_product", AsyncMock(return_value=product)),
            patch("db.vault.has_user_received_media", delivered_check),
            patch("db.vault.reserve_delivery", AsyncMock(return_value=1)),
            patch("db.redis.enqueue_send", AsyncMock()) as mock_enqueue,
            patch("db.vault.release_delivery", AsyncMock()),
        ):
            await deliver_product_media(1, 42, 101, "txn_del_mixed")

        # Only the undelivered media should be enqueued
        assert mock_enqueue.await_count == 1
        payload = mock_enqueue.call_args[0][0]
        assert payload["fangate_media_id"] == "202"
