"""P1.4 — Media Sending Integration Tests.

Comprehensive test suite covering:
A. Text regression (backward compatibility)
B. Media send (photo/video/document)
C. Stream serialization
D. Rate limiting (shared with text)
E. Deduplication
F. Failure handling
G. Persistence
H. Events
I. Security (path validation)
J. Backward compatibility
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _text_data(**overrides):
    base = {
        "entity": "12345",
        "content": "Hello world",
        "draft_content": "Hello world",
        "was_edited": "",
        "was_auto_approved": "",
        "confidence_score": "0",
        "operator_id": "",
        "save_to_db": "",
        "media_type": "",
        "media_path": "",
        # P1.4: creator_id is REQUIRED on the send stream (fail-closed DLQ
        # without it). Phases 1-4: generation_id preserved when present.
        "creator_id": "42",
    }
    base.update(overrides)
    return base


def _media_data(media_type="photo", **overrides):
    return _text_data(media_type=media_type, **overrides)


def _make_result(msg_id=99001):
    r = MagicMock()
    r.id = msg_id
    return r


def _send_stream_patches(**overrides):
    """Create context-manager patches for all _process_send_stream dependencies.

    Pass overrides as raw AsyncMock instances (e.g. read_send_messages=AsyncMock(return_value=[...])).
    """
    defaults = {
        "read_send_messages": AsyncMock(return_value=[]),
        "is_send_duplicate": AsyncMock(return_value=False),
        "get_send_rate_limit_wait": AsyncMock(return_value=0),
        "check_send_rate_limit": AsyncMock(return_value=True),
        "mark_send_dedup": AsyncMock(),
        "ack_send": AsyncMock(),
        "save_outbound_after_send": AsyncMock(),
        "publish_event": AsyncMock(),
        "requeue_stalled_send_messages": AsyncMock(return_value=(0, [])),
        "move_send_to_dlq": AsyncMock(),
        "release_stale_reservations": AsyncMock(return_value=[]),
        # H4 Batch 2/3 hermeticity: never touch live Redis from these tests.
        "get_or_create_send_random_id": AsyncMock(return_value=b"0123456789abcdef"),
        "try_reserve_send_dedup": AsyncMock(return_value="reserved:test-tok"),
        "confirm_send_dedup": AsyncMock(return_value=True),
        "get_send_dedup_value": AsyncMock(return_value=None),
        "record_unknown_send_attempt": AsyncMock(return_value=True),
        "record_send_repair_needed": AsyncMock(return_value=True),
        "clear_unknown_send_attempt": AsyncMock(return_value=True),
        "clear_send_repair_needed": AsyncMock(return_value=True),
        "list_unknown_vault_reservation_ids": AsyncMock(return_value=[]),
    }
    defaults.update(overrides)
    ctx = {}
    for name, mock_obj in defaults.items():
        ctx[name] = patch(f"chatbotv2.main.{name}", new=mock_obj)
    return ctx


async def _run_one_message(data, extra_patches=None):
    from chatbotv2.main import _process_send_stream

    mock_client = AsyncMock()
    # Configure send_message to return a result with a real .id
    msg_result = MagicMock()
    msg_result.id = 99001
    mock_client.send_message.return_value = msg_result
    mock_client.get_input_entity.return_value = "resolved"

    patches = _send_stream_patches(
        read_send_messages=AsyncMock(return_value=[("s1", [("m1", data)])]),
        **(extra_patches or {}),
    )
    # Start all patches and collect the active mocks
    active = {}
    for key, p in patches.items():
        active[key] = p.start()
    try:
        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]):
            await _process_send_stream(mock_client)
    finally:
        for p in patches.values():
            p.stop()
    return mock_client, active


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Text Regression
# ═══════════════════════════════════════════════════════════════════════════════


class TestTextRegression:
    """Existing text send behavior must remain unchanged."""

    @pytest.mark.asyncio
    async def test_text_send_still_works(self):
        """Text-only payload invokes send_message, not send_file."""
        _make_result(99001)
        client, _ = await _run_one_message(
            _text_data(entity="12345", content="Hello world"),
            extra_patches={
                "send_file": AsyncMock(side_effect=AssertionError("send_file should not be called")),
            },
        )
        client.send_message.assert_called_once()
        # send_file should not exist on mock_client since we didn't patch it
        # (the side_effect would have triggered if it was called)

    @pytest.mark.asyncio
    async def test_text_dedup_suppresses(self):
        """Duplicate text sends are suppressed."""
        client, patches = await _run_one_message(
            _text_data(dedup_id="dup-abc-123"),
            extra_patches={"is_send_duplicate": AsyncMock(return_value=True)},
        )
        client.send_message.assert_not_called()
        patches["ack_send"].assert_called_once()

    @pytest.mark.asyncio
    async def test_text_persistence_correct(self):
        """Text sends are persisted with correct args including media_type=None."""
        _client, patches = await _run_one_message(
            _text_data(
                entity="555",
                content="persist test",
                draft_content="original draft",
                was_edited="true",
                was_auto_approved="true",
                confidence_score="0.88",
                operator_id="42",
                save_to_db="true",
            ),
        )
        patches["save_outbound_after_send"].assert_called_once_with(
            user_id=555,
            content="persist test",
            draft_content="original draft",
            was_edited=True,
            was_auto_approved=True,
            confidence_score=0.88,
            operator_id=42,
            telegram_message_id=99001,
            media_type=None,
            media_path=None,
            fangate_media_id=None,
            creator_id=42,
            generation_id=None,
            dedup_id=None,
        )

    @pytest.mark.asyncio
    async def test_text_event_no_media_type(self):
        """Text sends publish message.sent without media_type in payload."""
        _, patches = await _run_one_message(
            _text_data(entity="777", was_auto_approved="true", confidence_score="0.9"),
        )
        patches["publish_event"].assert_called_once()
        call_args = patches["publish_event"].call_args
        assert call_args[0][0] == "message.sent"
        assert "media_type" not in call_args[0][1]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — Media Send
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaSend:
    """Photo, video, and document sends through the send stream."""

    @pytest.mark.asyncio
    async def test_photo_send_invokes_send_file(self):
        """Photo media payload invokes send_file with force_document=False."""
        mock_result = _make_result(88001)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake jpg data")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        client, _patches = await _run_one_message(
            _media_data(entity="222", content="Check this out", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        mock_file.assert_called_once()
        call_args = mock_file.call_args
        assert call_args[0][0] == "resolved"
        assert call_args[0][1] == temp_path
        assert call_args[1]["caption"] == "Check this out"
        assert call_args[1]["force_document"] is False
        client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_send_invokes_send_file(self):
        """Video media payload invokes send_file with force_document=False."""
        mock_result = _make_result(88002)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake mp4 data")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        await _run_one_message(
            _media_data(entity="333", media_type="video", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        mock_file.assert_called_once()
        assert mock_file.call_args[1]["force_document"] is False

    @pytest.mark.asyncio
    async def test_document_send_invokes_send_file_force_document(self):
        """Document media payload invokes send_file with force_document=True."""
        mock_result = _make_result(88003)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf data")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        await _run_one_message(
            _media_data(entity="444", media_type="document", content="Here is the file", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        mock_file.assert_called_once()
        assert mock_file.call_args[1]["force_document"] is True
        assert mock_file.call_args[1]["caption"] == "Here is the file"

    @pytest.mark.asyncio
    async def test_media_only_no_caption(self):
        """Media send with empty content sends empty caption."""
        mock_result = _make_result(88004)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake png")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        await _run_one_message(
            _media_data(entity="555", content="", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        assert mock_file.call_args[1]["caption"] == ""

    @pytest.mark.asyncio
    async def test_text_payload_does_not_invoke_send_file(self):
        """Empty media_type does NOT invoke send_file."""
        mock_result = _make_result(88005)
        mock_file = AsyncMock(return_value=mock_result)
        client, _ = await _run_one_message(
            _text_data(entity="666", content="plain text"),
            extra_patches={"send_file": mock_file},
        )
        mock_file.assert_not_called()
        client.send_message.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — Stream Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamSerialization:
    """Media payload survives Redis enqueue/dequeue serialization."""

    @pytest.mark.asyncio
    async def test_media_payload_redis_roundtrip(self):
        """Media fields survive stringification through enqueue_send."""
        from db.redis import enqueue_send

        mock_r = AsyncMock()
        mock_r.xadd.return_value = "msg-1"

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await enqueue_send(
                {
                    "entity": "123",
                    "content": "caption text",
                    "media_type": "photo",
                    "media_path": "/tmp/img.jpg",
                    "creator_id": "42",
                },
                dedup_id="media-dedup-1",
            )

        call_data = mock_r.xadd.call_args[0][1]
        assert call_data["media_type"] == "photo"
        assert call_data["media_path"] == "/tmp/img.jpg"
        assert call_data["content"] == "caption text"
        assert call_data["dedup_id"] == "media-dedup-1"

    @pytest.mark.asyncio
    async def test_consumer_interprets_media_payload(self):
        """_process_send_stream correctly reads media fields from stream data."""
        mock_result = _make_result(88010)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="888", media_path=temp_path, save_to_db="true"),
            extra_patches={"send_file": mock_file},
        )
        patches["save_outbound_after_send"].assert_called_once()
        call_kwargs = patches["save_outbound_after_send"].call_args[1]
        assert call_kwargs["media_type"] == "photo"
        assert call_kwargs["media_path"] == temp_path

    @pytest.mark.asyncio
    async def test_unsupported_media_type_falls_through_to_text(self):
        """Invalid media_type (e.g. 'audio') is treated as text send."""
        client, patches = await _run_one_message(
            _text_data(entity="999", content="fallback", media_type="audio", media_path="/tmp/x.mp3", save_to_db="true"),
        )
        client.send_message.assert_called_once_with("resolved", "fallback")
        patches["save_outbound_after_send"].assert_called_once()
        call_kwargs = patches["save_outbound_after_send"].call_args[1]
        assert call_kwargs["media_type"] is None

    @pytest.mark.asyncio
    async def test_empty_media_path_with_valid_type_falls_through(self):
        """media_type set but empty media_path falls through to text send."""
        client, _ = await _run_one_message(
            _text_data(entity="101", content="text fallback", media_type="photo", media_path=""),
        )
        client.send_message.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateLimiting:
    """Media sends use the same rate-limit path as text."""

    @pytest.mark.asyncio
    async def test_media_uses_same_rate_limiter(self):
        """Media send applies per-peer rate limiting."""
        mock_result = _make_result(88020)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="333", media_path=temp_path),
            extra_patches={
                "send_file": mock_file,
                "get_send_rate_limit_wait": AsyncMock(return_value=0.2),
            },
        )
        patches["get_send_rate_limit_wait"].assert_called_once_with("333")
        patches["check_send_rate_limit"].assert_called_once_with("333")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Dedup
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaDedup:
    """Duplicate media sends are suppressed."""

    @pytest.mark.asyncio
    async def test_duplicate_media_send_suppressed(self):
        """Duplicate media dedup_id prevents re-send."""
        mock_file = AsyncMock()
        client, patches = await _run_one_message(
            _media_data(entity="444", dedup_id="media-dup-1", media_path="/tmp/x.jpg"),
            extra_patches={
                "send_file": mock_file,
                "is_send_duplicate": AsyncMock(return_value=True),
            },
        )
        mock_file.assert_not_called()
        client.send_message.assert_not_called()
        patches["ack_send"].assert_called_once()

    @pytest.mark.asyncio
    async def test_different_dedup_ids_not_deduplicated(self):
        """Different media sends with different logical IDs are not incorrectly deduped."""
        mock_result = _make_result(88030)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="555", media_path=temp_path, dedup_id="unique-id-abc"),
            extra_patches={"send_file": mock_file},
        )
        mock_file.assert_called_once()
        patches["mark_send_dedup"].assert_called_once_with("unique-id-abc")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — Failure Handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaFailure:
    """Media send failures follow existing DLQ semantics."""

    @pytest.mark.asyncio
    async def test_user_blocked_ack_and_event(self):
        """UserIsBlockedError during media send ACKs and publishes send_failed."""
        from telethon.errors import UserIsBlockedError

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(side_effect=UserIsBlockedError("Blocked"))
        _, patches = await _run_one_message(
            _media_data(entity="666", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        patches["ack_send"].assert_called_once()
        patches["publish_event"].assert_called_once()
        event_args = patches["publish_event"].call_args
        assert event_args[0][0] == "message.send_failed"
        assert event_args[0][1]["error"] == "UserIsBlockedError"

    @pytest.mark.asyncio
    async def test_generic_send_timeout_goes_unknown(self):
        """H4 Batch 3 (D2): ambiguous transport failure (e.g. timeout) is
        UNKNOWN — never certified failed. DLQ reason is unknown_send_result,
        no message.send_failed, no vault release, no delivered mark."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(side_effect=RuntimeError("Telegram timeout"))
        _, patches = await _run_one_message(
            _media_data(entity="777", media_path=temp_path, dedup_id="fail-dedup"),
            extra_patches={"send_file": mock_file},
        )
        patches["move_send_to_dlq"].assert_called_once()
        dlq_args = patches["move_send_to_dlq"].call_args
        assert dlq_args[0][0] == "m1"
        assert dlq_args[0][1] == "unknown_send_result"
        patches["publish_event"].assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_does_not_persist(self):
        """Failed media send does not call save_outbound_after_send."""
        from telethon.errors import UserIsBlockedError

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(side_effect=UserIsBlockedError("Blocked"))
        _, patches = await _run_one_message(
            _media_data(entity="888", media_path=temp_path, save_to_db="true"),
            extra_patches={"send_file": mock_file},
        )
        patches["save_outbound_after_send"].assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — Persistence
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaPersistence:
    """Successful media sends are persisted with correct metadata."""

    @pytest.mark.asyncio
    async def test_media_persist_includes_type_and_path(self):
        """Successful media send persists media_type and media_path."""
        mock_result = _make_result(88040)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="999", content="Photo caption", draft_content="Photo caption", media_path=temp_path, save_to_db="true"),
            extra_patches={"send_file": mock_file},
        )
        patches["save_outbound_after_send"].assert_called_once_with(
            user_id=999,
            content="Photo caption",
            draft_content="Photo caption",
            was_edited=False,
            was_auto_approved=False,
            confidence_score=0.0,
            operator_id=None,
            telegram_message_id=88040,
            media_type="photo",
            media_path=temp_path,
            fangate_media_id=None,
            creator_id=42,
            generation_id=None,
            dedup_id=None,
        )

    @pytest.mark.asyncio
    async def test_telegram_message_id_captured(self):
        """Telegram message ID from send_file result is persisted."""
        mock_result = _make_result(88041)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="111", media_type="video", media_path=temp_path, save_to_db="true"),
            extra_patches={"send_file": mock_file},
        )
        call_kwargs = patches["save_outbound_after_send"].call_args[1]
        assert call_kwargs["telegram_message_id"] == 88041


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — Events
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaEvents:
    """Successful and failed media sends publish correct events."""

    @pytest.mark.asyncio
    async def test_media_sent_includes_media_type(self):
        """Successful media send publishes message.sent with media_type."""
        mock_result = _make_result(88050)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(return_value=mock_result)
        _, patches = await _run_one_message(
            _media_data(entity="222", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        patches["publish_event"].assert_called_once()
        call_args = patches["publish_event"].call_args
        assert call_args[0][0] == "message.sent"
        assert call_args[0][1]["media_type"] == "photo"
        assert call_args[0][1]["telegram_message_id"] == 88050

    @pytest.mark.asyncio
    async def test_media_failure_publishes_send_failed(self):
        """Failed media send publishes message.send_failed."""
        from telethon.errors import UserIsBlockedError

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        mock_file = AsyncMock(side_effect=UserIsBlockedError("Blocked"))
        _, patches = await _run_one_message(
            _media_data(entity="333", media_path=temp_path),
            extra_patches={"send_file": mock_file},
        )
        event_args = patches["publish_event"].call_args
        assert event_args[0][0] == "message.send_failed"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Security (Path Validation)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaSecurity:
    """Media path validation prevents unsafe access."""

    def test_nonexistent_path_rejected(self):
        """Non-existent file path is rejected."""
        from chatbotv2.main import _validate_media_path

        result = _validate_media_path("/tmp/does_not_exist_abc123.jpg")
        assert result is None

    def test_empty_path_rejected(self):
        """Empty path is rejected."""
        from chatbotv2.main import _validate_media_path

        assert _validate_media_path("") is None
        assert _validate_media_path("   ") is None

    def test_directory_rejected(self):
        """Directory path (not a file) is rejected."""
        from chatbotv2.main import _validate_media_path

        result = _validate_media_path(tempfile.gettempdir())
        assert result is None

    def test_valid_file_accepted(self):
        """Valid existing file is accepted."""
        from chatbotv2.main import _validate_media_path

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"test data")
            temp_path = f.name

        result = _validate_media_path(temp_path)
        assert result is not None
        assert result.endswith(".jpg")

    def test_oversized_file_rejected(self):
        """File exceeding max size is rejected."""
        from chatbotv2.main import _MAX_MEDIA_SIZE_BYTES, _validate_media_path

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            # Write slightly over the limit
            f.write(b"x" * (_MAX_MEDIA_SIZE_BYTES + 1))
            temp_path = f.name

        result = _validate_media_path(temp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_media_send_goes_to_dlq(self):
        """Invalid media path during send goes to DLQ."""
        from chatbotv2.main import _process_send_stream

        mock_client = AsyncMock()
        data = _media_data(entity="444", media_path="/nonexistent/fake.jpg", save_to_db="true")
        patches = _send_stream_patches(
            read_send_messages=AsyncMock(return_value=[("s1", [("m1", data)])]),
        )
        active = {}
        for key, p in patches.items():
            active[key] = p.start()
        try:
            with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]):
                await _process_send_stream(mock_client)
        finally:
            for p in patches.values():
                p.stop()
        active["move_send_to_dlq"].assert_called_once()
        assert active["move_send_to_dlq"].call_args[0][1] == "invalid_media_path"

    @pytest.mark.asyncio
    async def test_no_raw_binary_in_redis(self):
        """Media files are referenced by path, not embedded in Redis."""
        from db.redis import enqueue_send

        mock_r = AsyncMock()
        mock_r.xadd.return_value = "msg-1"

        binary_data = b"binary content that should NOT be in redis"
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                f.write(binary_data)
                temp_path = f.name

            await enqueue_send(
                {
                    "entity": "123",
                    "content": "caption",
                    "media_type": "photo",
                    "media_path": temp_path,
                    "creator_id": "42",
                },
                dedup_id="test-no-binary",
            )

        call_data = mock_r.xadd.call_args[0][1]
        # The media_path is a string path, not binary data
        assert call_data["media_path"] == temp_path
        assert len(call_data["media_path"]) < 1000  # Path is short, not binary blob
        assert binary_data not in str(call_data).encode()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP J — Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Existing send-stream tests continue passing; callers unaffected."""

    @pytest.mark.asyncio
    async def test_enqueue_send_accepts_old_payload(self):
        """Existing callers that don't send media fields still work."""
        from db.redis import enqueue_send

        mock_r = AsyncMock()
        mock_r.xadd.return_value = "msg-1"

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await enqueue_send(
                {"entity": "100", "content": "old style", "creator_id": "42"},
                dedup_id="old-dedup",
            )

        call_data = mock_r.xadd.call_args[0][1]
        assert call_data["entity"] == "100"
        assert call_data["content"] == "old style"
        assert call_data["dedup_id"] == "old-dedup"
        # Old callers don't set media_type/media_path
        assert "media_type" not in call_data or call_data.get("media_type", "") == ""
        assert "media_path" not in call_data or call_data.get("media_path", "") == ""

    @pytest.mark.asyncio
    async def test_text_send_path_unchanged_for_existing_callers(self):
        """Existing text payloads (no media fields) use send_message path."""
        client, _ = await _run_one_message(
            _text_data(entity="200", content="existing caller text"),
        )
        client.send_message.assert_called_once_with("resolved", "existing caller text")

    @pytest.mark.asyncio
    async def test_llm_worker_payload_compatible(self):
        """LLM worker auto-approve payload works with media-aware processor."""
        payload = {
            "entity": "12345",
            "content": "AI generated response",
            "draft_content": "AI generated response",
            "was_edited": "false",
            "was_auto_approved": "true",
            "confidence_score": "0.92",
            "operator_id": "",
            "save_to_db": "true",
            "creator_id": "42",
        }
        client, patches = await _run_one_message(payload)
        client.send_message.assert_called_once()
        patches["save_outbound_after_send"].assert_called_once()
        call_kwargs = patches["save_outbound_after_send"].call_args[1]
        assert call_kwargs["media_type"] is None
        assert call_kwargs["media_path"] is None
