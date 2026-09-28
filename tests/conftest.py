"""Shared fixtures for Phase 4.6 integration tests.

Provides mock infrastructure for external boundaries (Gemini, Redis, PostgreSQL)
while exercising real application logic between them.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Test Player Identity — Pre-Phase 91 Cleanup
# ---------------------------------------------------------------------------
# Production player/fan identity comes from runtime user state (user.first_name)
# or neutral fallback "Fan". Tests MUST NOT hard-code the former test identity.
# Use this fixture-driven constant for CHARACTER=Sunny Skye / PLAYER=test fan contract.
TEST_PLAYER_NAME = "Alex"
TEST_PLAYER_NAME_HASH = __import__("hashlib").sha256(TEST_PLAYER_NAME.lower().encode()).hexdigest()[:16]
CHARACTER_NAME = "Sunny Skye"


@pytest.fixture
def test_player_name() -> str:
    return TEST_PLAYER_NAME


@pytest.fixture
def test_character_name() -> str:
    return CHARACTER_NAME

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings():
    """Settings object with test defaults."""
    from core.config import Settings

    return Settings(
        OPENAI_API_KEY="test-openai-key",
        POSTGRES_DSN="postgresql://localhost:5432/test",
        REDIS_URL="redis://localhost:6379",
        GEMINI_API_KEYS="test-key-1,test-key-2,test-key-3",
        AUTO_APPROVE_THRESHOLD=0.80,
        MODEL_NAME="gemini-flash-latest",
        CHEAP_MODEL="gemini-flash-latest",
        MAX_TOKENS=200,
        TEMPERATURE=0.85,
        SUMMARIZE_EVERY_N=20,
        DEBOUNCE_WINDOW_SECONDS=3,
        RATE_LIMIT_PER_MINUTE=20,
        USER_LOCK_TTL=60,
        DASHBOARD_ADMIN_PASSWORD="admin123",
        ENABLE_WEBSOCKET=True,
        GEMINI_RPM_LIMIT=10,
        GEMINI_RPM_SAFETY_MARGIN=0.8,
        REDIS_PENDING_IDLE_MS=60000,
        DLQ_MAX_REPLAY_ATTEMPTS=3,
        DLQ_RETENTION_SECONDS=604800,
        STRUCTURED_LOGGING=False,
        WORKER_HEARTBEAT_INTERVAL=10,
        WORKER_HEARTBEAT_TTL=30,
    )


# ---------------------------------------------------------------------------
# Gemini Mocks
# ---------------------------------------------------------------------------


class FakeGeminiCredential:
    """Minimal fake Gemini credential for testing."""

    def __init__(self, key="test-key-1"):
        self.key = key
        self._client = None

    def get_client(self):
        if self._client is None:
            self._client = MagicMock()
            self._client.aio = MagicMock()
            self._client.aio.models = MagicMock()
        return self._client


@pytest.fixture
def fake_credential():
    return FakeGeminiCredential("test-key-1")


@pytest.fixture
def fake_credential_2():
    return FakeGeminiCredential("test-key-2")


def _make_gemini_response(text: str) -> MagicMock:
    """Create a fake Gemini generate_content response."""
    resp = MagicMock()
    resp.text = text
    resp.candidates = []
    return resp


def _make_gemini_json_response(data: dict) -> MagicMock:
    """Create a fake Gemini response that returns JSON text."""
    return _make_gemini_response(json.dumps(data))


@pytest.fixture
def mock_gemini_pool(fake_credential, fake_credential_2):
    """Mock the Gemini CredentialPool."""
    pool = MagicMock()
    pool.size = 3
    pool.get_credential.return_value = fake_credential
    pool.get_client.return_value = fake_credential.get_client()
    pool.get_available_key_count.return_value = 3
    pool.mark_cooldown = MagicMock()
    return pool


@pytest.fixture
def mock_gemini_limiter():
    """Mock the Gemini RateLimiter."""
    limiter = MagicMock()
    limiter.can_request.return_value = True
    limiter.record_request = MagicMock()
    limiter.set_cooldown = MagicMock()
    limiter._cooldowns = {}
    limiter.is_in_cooldown.return_value = False
    return limiter


# ---------------------------------------------------------------------------
# Redis Mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Mock async Redis client.

    Provides realistic mock implementations for stream operations,
    locks, rate limits, and simple key-value operations.
    """
    r = AsyncMock()

    # Track state for key-value operations
    _kv_store = {}
    _stream_counter = [0]

    async def fake_set(key, value, nx=False, ex=None, **kwargs):
        if nx and key in _kv_store:
            return None
        _kv_store[key] = value
        return True

    async def fake_get(key):
        return _kv_store.get(key)

    async def fake_delete(*keys):
        count = 0
        for k in keys:
            if k in _kv_store:
                del _kv_store[k]
                count += 1
        return count

    async def fake_exists(key):
        return 1 if key in _kv_store else 0

    async def fake_setex(key, ttl, value):
        _kv_store[key] = value
        return True

    async def fake_incr(key):
        current = int(_kv_store.get(key, 0)) + 1
        _kv_store[key] = str(current)
        return current

    async def fake_expire(key, ttl):
        return True

    async def fake_xadd(stream, data, id="*", **kwargs):
        _stream_counter[0] += 1
        entry_id = f"{int(time.time() * 1000)}-{_stream_counter[0]}"
        return entry_id

    async def fake_xreadgroup(group, consumer, streams, count=10, block=2000):
        return []

    async def fake_xack(stream, group, *message_ids):
        return len(message_ids)

    async def fake_xautoclaim(name, groupname, consumername, min_idle_time, start_id, count=10):
        return (0, [])

    async def fake_xrange(stream, min="-", max="+", count=500):
        return []

    async def fake_xrevrange(stream, count=50):
        return []

    async def fake_xdel(stream, *ids):
        return len(ids)

    async def fake_xinfo_stream(stream):
        return {"length": 0}

    async def fake_publish(channel, message):
        return 1

    async def fake_ping():
        return True

    async def fake_lrange(key, start, end):
        return []

    async def fake_eval(script, numkeys, *args):
        return 1

    async def fake_scan_iter(match):
        return []

    async def fake_rpush(key, *values):
        return len(values)

    r.set = AsyncMock(side_effect=fake_set)
    r.get = AsyncMock(side_effect=fake_get)
    r.delete = AsyncMock(side_effect=fake_delete)
    r.exists = AsyncMock(side_effect=fake_exists)
    r.setex = AsyncMock(side_effect=fake_setex)
    r.incr = AsyncMock(side_effect=fake_incr)
    r.expire = AsyncMock(side_effect=fake_expire)
    r.xadd = AsyncMock(side_effect=fake_xadd)
    r.xreadgroup = AsyncMock(side_effect=fake_xreadgroup)
    r.xack = AsyncMock(side_effect=fake_xack)
    r.xautoclaim = AsyncMock(side_effect=fake_xautoclaim)
    r.xrange = AsyncMock(side_effect=fake_xrange)
    r.xrevrange = AsyncMock(side_effect=fake_xrevrange)
    r.xdel = AsyncMock(side_effect=fake_xdel)
    r.xinfo_stream = AsyncMock(side_effect=fake_xinfo_stream)
    r.publish = AsyncMock(side_effect=fake_publish)
    r.ping = AsyncMock(side_effect=fake_ping)
    r.lrange = AsyncMock(side_effect=fake_lrange)
    r.eval = AsyncMock(side_effect=fake_eval)
    r.scan_iter = AsyncMock(side_effect=fake_scan_iter)
    r.rpush = AsyncMock(side_effect=fake_rpush)
    r.close = AsyncMock()

    r._kv = _kv_store
    r._streams = {}
    return r


# ---------------------------------------------------------------------------
# PostgreSQL Mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pg_conn():
    """Mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def mock_pg_pool(mock_pg_conn):
    """Mock asyncpg pool with acquire context manager."""
    pool = MagicMock()

    class FakeAcquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *args):
            pass

    pool.acquire = MagicMock(return_value=FakeAcquire(mock_pg_conn))
    pool.close = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Entity blacklist isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_entity_blacklist():
    """Ensure entity blacklist does not leak between tests."""
    from unittest.mock import patch, AsyncMock
    from core.entity_blacklist import reset_for_testing
    reset_for_testing()
    # Patch is_blacklisted to return False by default for tests (unless test explicitly mocks)
    # This prevents flaky failures when Redis has leftover blacklist entries
    with patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False)):
        yield
    reset_for_testing()


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_event_bus():
    """Mock event bus that records published events."""
    published = []

    async def fake_publish(
        event_type, data, *, user_id=None, dialog_id=None, generation_id=None, scope="global"
    ):
        event_id = f"evt-{len(published)}"
        published.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "data": data,
                "user_id": user_id,
                "dialog_id": dialog_id,
                "generation_id": generation_id,
                "scope": scope,
            }
        )
        return event_id

    return fake_publish, published


# ---------------------------------------------------------------------------
# FastAPI Test Client
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app():
    """Create a fresh FastAPI test app for isolated testing."""
    from httpx import ASGITransport

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
    transport = ASGITransport(app=app)
    yield app, transport
    app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_app):
    """Async HTTP client for testing dashboard routes."""
    from httpx import AsyncClient

    _app, transport = test_app
    return AsyncClient(transport=transport, base_url="http://test")
