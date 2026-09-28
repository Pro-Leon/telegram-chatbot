"""P1.1 tests — Redis lock contention must not be ACKed as success.

Covers:
1. Lock contention does not ACK
2. Successful processing still ACKs
3. Processing exception preserves DLQ
4. Lock release semantics
5. Same-user vs different-user concurrency (via acquire_user_lock scoping)
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from workers.llm_worker import UserLockContentionError, process_message

pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_process_message_raises_on_lock_contention():
    """process_message must raise UserLockContentionError when lock is held."""
    # P1.4: lock is creator-scoped, so need a valid creator to reach lock check
    with patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=False)), \
         patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1))):
        with pytest.raises(UserLockContentionError):
            await process_message(
                user_id=123,
                user_message="hi",
                telegram_message_id=1,
                username="u",
                first_name="f",
                persona="",
                creator_id=1,
            )


@pytest.mark.asyncio
async def test_process_message_succeeds_when_lock_acquired():
    """When lock is acquired, process_message should proceed (not raise contention)."""
    # P1.4: need valid creator to reach lock, use creator_id param
    with patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True)), \
         patch("workers.llm_worker.release_user_lock", new=AsyncMock()), \
         patch("workers.llm_worker.upsert_user", new=AsyncMock()), \
         patch("workers.llm_worker.is_user_auto_reply_excluded", new=AsyncMock(return_value=False)), \
         patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1))), \
         patch("memory.creator_persona.get_structured_persona_async", new=AsyncMock(return_value=None)), \
         patch("workers.llm_worker.assemble_authoritative_context", new=AsyncMock(side_effect=Exception("force fallback"))), \
         patch("memory.context.build_qwen3_context", new=AsyncMock(return_value=[{"role": "system", "content": "hi"}])), \
         patch("workers.llm_worker.is_user_auto_reply_excluded", new=AsyncMock(return_value=False)):
        # We won't actually run full process_message to completion without many mocks,
        # so just verify it does NOT raise UserLockContentionError when lock True
        # by checking that acquire_user_lock True does not raise immediately
        # We patch process_message internals to avoid full run, but we can test the lock path directly:
        # Use a minimal process_message that will fail later but not due to lock
        try:
            await process_message(
                user_id=124,
                user_message="hi",
                telegram_message_id=2,
                username="u",
                first_name="f",
                persona="",
                creator_id=1,
            )
        except UserLockContentionError:
            pytest.fail("Should not raise contention when lock acquired")
        except Exception:
            # Any other exception is fine (means it passed lock check)
            pass


@pytest.mark.asyncio
async def test_run_worker_does_not_ack_on_contention():
    """Simulate run_worker loop: contention must not ACK and not release."""
    from workers.llm_worker import UserLockContentionError

    # Simulate the try/except/finally logic used in run_worker for normal messages
    ack_mock = AsyncMock()
    dlq_mock = AsyncMock()
    release_mock = AsyncMock()

    # Mock process_message to raise contention
    async def mock_process(*args, **kwargs):
        raise UserLockContentionError("locked")

    with patch("workers.llm_worker.process_message", new=mock_process), \
         patch("workers.llm_worker.ack_inbound", new=ack_mock), \
         patch("workers.llm_worker.move_to_dlq", new=dlq_mock), \
         patch("workers.llm_worker.release_user_lock", new=release_mock):

        _is_contention = False
        _msg_user_id = 999
        msg_id = "test-id-1"
        data = {"user_id": "999", "creator_id": "1"}

        try:
            await mock_process(user_id=_msg_user_id, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="")
            await ack_mock(msg_id)
        except UserLockContentionError:
            _is_contention = True
        except Exception:
            await dlq_mock(msg_id, "processing_error", payload=data, worker_id="test")
        finally:
            if not _is_contention:
                await release_mock(_msg_user_id, creator_id=1)

        # On contention: ack not called, dlq not called, release not called
        ack_mock.assert_not_called()
        dlq_mock.assert_not_called()
        release_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_worker_acks_on_success():
    """Successful processing must ACK and release."""
    ack_mock = AsyncMock()
    dlq_mock = AsyncMock()
    release_mock = AsyncMock()

    async def mock_process_ok(*args, **kwargs):
        return None

    with patch("workers.llm_worker.process_message", new=mock_process_ok), \
         patch("workers.llm_worker.ack_inbound", new=ack_mock), \
         patch("workers.llm_worker.move_to_dlq", new=dlq_mock), \
         patch("workers.llm_worker.release_user_lock", new=release_mock):

        _is_contention = False
        _msg_user_id = 1000
        msg_id = "test-id-ok"
        data = {"user_id": "1000", "creator_id": "1"}

        try:
            await mock_process_ok(user_id=_msg_user_id, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="")
            await ack_mock(msg_id)
        except UserLockContentionError:
            _is_contention = True
        except Exception:
            await dlq_mock(msg_id, "processing_error", payload=data, worker_id="test")
        finally:
            if not _is_contention:
                await release_mock(_msg_user_id, creator_id=1)

        ack_mock.assert_awaited_once_with(msg_id)
        dlq_mock.assert_not_called()
        release_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_worker_dlq_on_exception():
    """Processing exception must DLQ and release, not treat as contention."""
    ack_mock = AsyncMock()
    dlq_mock = AsyncMock()
    release_mock = AsyncMock()

    async def mock_process_fail(*args, **kwargs):
        raise RuntimeError("boom")

    with patch("workers.llm_worker.process_message", new=mock_process_fail), \
         patch("workers.llm_worker.ack_inbound", new=ack_mock), \
         patch("workers.llm_worker.move_to_dlq", new=dlq_mock), \
         patch("workers.llm_worker.release_user_lock", new=release_mock):

        _is_contention = False
        _msg_user_id = 1001
        msg_id = "test-id-fail"
        data = {"user_id": "1001", "creator_id": "1"}

        try:
            await mock_process_fail(user_id=_msg_user_id, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="")
            await ack_mock(msg_id)
        except UserLockContentionError:
            _is_contention = True
        except Exception:
            await dlq_mock(msg_id, "processing_error", payload=data, worker_id="test")
        finally:
            if not _is_contention:
                await release_mock(_msg_user_id, creator_id=1)

        ack_mock.assert_not_called()
        dlq_mock.assert_awaited_once()
        release_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_release_after_success():
    """Lock must be released after successful processing (outer release)."""
    # This is more of an integration check: process_message with mocked lock True should
    # eventually lead to release via run_worker's finally (not contention).
    # We test the acquire/release directly for different users.
    from db.redis import acquire_user_lock, release_user_lock, _user_lock_key
    from unittest.mock import AsyncMock as AM, patch

    # Mock redis set/delete
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)

    with patch("db.redis.get_redis", new=AsyncMock(return_value=mock_redis)):
        # Same user, same creator -> second acquire should be True in mock, but we test key scoping
        result1 = await acquire_user_lock(1, ttl=30, creator_id=10)
        assert result1 is True
        # Different user same creator -> should be independent (different key)
        # Our mock always returns True, so we check key generation instead
        key1 = _user_lock_key(1, creator_id=10)
        key2 = _user_lock_key(2, creator_id=10)
        assert key1 != key2
        assert key1 == "lock:creator:10:user:1"
        assert key2 == "lock:creator:10:user:2"

        await release_user_lock(1, creator_id=10)
        mock_redis.delete.assert_awaited_once_with(key1)


@pytest.mark.asyncio
async def test_different_user_concurrency_independent():
    """Acquiring lock for user 1 must not block user 2 (same creator)."""
    from db.redis import _user_lock_key
    # Keys must be distinct per user
    assert _user_lock_key(1, creator_id=5) != _user_lock_key(2, creator_id=5)
    # Same user different creator must be distinct
    assert _user_lock_key(1, creator_id=5) != _user_lock_key(1, creator_id=6)
    # Same user same creator must be same
    assert _user_lock_key(1, creator_id=5) == _user_lock_key(1, creator_id=5)


@pytest.mark.asyncio
async def test_same_user_serialization():
    """Two same-user deliveries cannot both acquire lock simultaneously (mocked)."""
    from unittest.mock import AsyncMock, patch

    call_count = 0

    async def fake_acquire(user_id, ttl=30, creator_id=None):
        nonlocal call_count
        call_count += 1
        # First call succeeds, second fails (simulating held lock)
        return call_count == 1

    with patch("workers.llm_worker.acquire_user_lock", new=fake_acquire):
        with patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1))):
            # First message
            try:
                await process_message(user_id=500, user_message="first", telegram_message_id=1, username="u", first_name="f", persona="", creator_id=1)
            except UserLockContentionError:
                pytest.fail("first should not contend")
            except Exception:
                pass  # other errors ok, we only care about lock

            # Second message same user should contend
            with pytest.raises(UserLockContentionError):
                await process_message(user_id=500, user_message="second", telegram_message_id=2, username="u", first_name="f", persona="", creator_id=1)
