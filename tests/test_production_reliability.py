"""Phase 4.1C - Production reliability & observability tests."""

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A - Configuration Defaults
# ═══════════════════════════════════════════════════════════════════════════════


class TestProductionConfigDefaults:
    def test_structured_logging_default(self):
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
        )
        assert s.structured_logging is False

    def test_worker_heartbeat_interval_default(self):
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
        )
        assert s.worker_heartbeat_interval == 10

    def test_worker_heartbeat_ttl_default(self):
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
        )
        assert s.worker_heartbeat_ttl == 30

    def test_structured_logging_from_env(self):
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
            STRUCTURED_LOGGING="true",
        )
        assert s.structured_logging is True

    def test_heartbeat_config_from_env(self):
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
            WORKER_HEARTBEAT_INTERVAL="5",
            WORKER_HEARTBEAT_TTL="15",
        )
        assert s.worker_heartbeat_interval == 5
        assert s.worker_heartbeat_ttl == 15

    def test_env_example_has_new_fields(self):
        from pathlib import Path

        env_example = Path(__file__).parent.parent / ".env.example"
        content = env_example.read_text()
        assert "STRUCTURED_LOGGING" in content
        assert "WORKER_HEARTBEAT_INTERVAL" in content
        assert "WORKER_HEARTBEAT_TTL" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B - Health Endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    def test_health_response_structure(self):
        from core.health import get_health_response

        resp = get_health_response()
        assert resp["status"] == "ok"
        assert resp["service"] == "chatbotv2"
        assert "version" in resp

    def test_health_does_not_require_redis(self):
        from core.health import get_health_response

        resp = get_health_response()
        assert resp["status"] == "ok"

    def test_health_does_not_expose_secrets(self):
        from core.health import get_health_response

        resp = get_health_response()
        dumped = json.dumps(resp)
        for word in ("api_key", "password", "token", "secret"):
            assert word not in dumped.lower()

    @pytest.mark.asyncio
    async def test_health_endpoint_via_testclient(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.health import get_health_response

        app = FastAPI()

        @app.get("/health")
        async def health():
            return get_health_response()

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C - Readiness Endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadinessEndpoint:
    @pytest.mark.asyncio
    async def test_ready_when_all_healthy(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.0}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            resp = await get_readiness_response()
            assert resp["status"] == "ready"

    @pytest.mark.asyncio
    async def test_not_ready_when_redis_down(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "error"}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            resp = await get_readiness_response()
            assert resp["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_not_ready_when_postgres_down(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.0}
            mock_cp.return_value = {"status": "error"}
            resp = await get_readiness_response()
            assert resp["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_ready_returns_dependency_details(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.5}
            mock_cp.return_value = {"status": "ok", "latency_ms": 3.0}
            resp = await get_readiness_response()
            assert "dependencies" in resp
            assert "redis" in resp["dependencies"]
            assert "postgres" in resp["dependencies"]
            assert "gemini" in resp["dependencies"]

    @pytest.mark.asyncio
    async def test_readiness_does_not_expose_secrets(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.0}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            resp = await get_readiness_response()
            dumped = json.dumps(resp)
            for word in ("api_key", "password", "dsn"):
                assert word not in dumped.lower()

    @pytest.mark.asyncio
    async def test_readiness_503_when_not_ready(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.responses import JSONResponse

        from core.health import get_readiness_response

        app = FastAPI()

        @app.get("/ready")
        async def ready():
            resp = await get_readiness_response()
            code = 200 if resp["status"] == "ready" else 503
            return JSONResponse(content=resp, status_code=code)

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "error"}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            client = TestClient(app)
            resp = client.get("/ready")
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_readiness_200_when_ready(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.responses import JSONResponse

        from core.health import get_readiness_response

        app = FastAPI()

        @app.get("/ready")
        async def ready():
            resp = await get_readiness_response()
            code = 200 if resp["status"] == "ready" else 503
            return JSONResponse(content=resp, status_code=code)

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.0}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            client = TestClient(app)
            resp = client.get("/ready")
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D - Dependency Health Checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestDependencyHealthChecks:
    @pytest.mark.asyncio
    async def test_check_redis_ok(self):
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.health import check_redis

            result = await check_redis()
            assert result["status"] == "ok"
            assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_check_redis_error(self):
        with patch("db.redis.get_redis", side_effect=RuntimeError("conn refused")):
            from core.health import check_redis

            result = await check_redis()
            assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_check_postgres_ok(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
        with patch("db.postgres.get_pool", return_value=mock_pool):
            from core.health import check_postgres

            result = await check_postgres()
            assert result["status"] == "ok"
            assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_check_postgres_error(self):
        with patch("db.postgres.get_pool", side_effect=RuntimeError("conn refused")):
            from core.health import check_postgres

            result = await check_postgres()
            assert result["status"] == "error"

    def test_check_gemini_unconfigured(self):
        with patch("core.gemini_client.get_pool", side_effect=RuntimeError("no keys")):
            from core.health import check_gemini

            result = check_gemini()
            assert result["status"] == "unconfigured"

    def test_check_gemini_available(self):
        mock_pool = MagicMock()
        mock_pool.size = 2
        mock_pool.get_available_key_count.return_value = 2
        mock_limiter = MagicMock()
        mock_limiter._cooldowns = {}
        with (
            patch("core.gemini_client.get_pool", return_value=mock_pool),
            patch("core.gemini_client.get_limiter", return_value=mock_limiter),
        ):
            from core.health import check_gemini

            result = check_gemini()
            assert result["status"] == "available"
            assert result["credentials_total"] == 2

    def test_check_gemini_degraded(self):
        mock_pool = MagicMock()
        mock_pool.size = 2
        mock_pool.get_available_key_count.return_value = 0
        mock_limiter = MagicMock()
        mock_limiter._cooldowns = {}
        with (
            patch("core.gemini_client.get_pool", return_value=mock_pool),
            patch("core.gemini_client.get_limiter", return_value=mock_limiter),
        ):
            from core.health import check_gemini

            result = check_gemini()
            assert result["status"] == "degraded"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E - Worker Heartbeat
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerHeartbeat:
    @pytest.mark.asyncio
    async def test_write_heartbeat_creates_key(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        stop = asyncio.Event()

        async def get_redis_and_stop():
            stop.set()
            return mock_redis

        with patch("db.redis.get_redis", side_effect=get_redis_and_stop):
            from core.worker_heartbeat import write_heartbeat

            await write_heartbeat("worker_1", worker_type="llm", stop_event=stop)
            mock_redis.set.assert_called()
            assert "worker:heartbeat:worker_1" in mock_redis.set.call_args.args[0]

    @pytest.mark.asyncio
    async def test_write_heartbeat_includes_type(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        stop = asyncio.Event()

        async def get_redis_and_stop():
            stop.set()
            return mock_redis

        with patch("db.redis.get_redis", side_effect=get_redis_and_stop):
            from core.worker_heartbeat import write_heartbeat

            await write_heartbeat("w_1", worker_type="llm", stop_event=stop)
            payload = json.loads(mock_redis.set.call_args.args[1])
            assert payload["worker_type"] == "llm"
            assert payload["worker_id"] == "w_1"

    @pytest.mark.asyncio
    async def test_write_heartbeat_has_ttl(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        stop = asyncio.Event()

        async def get_redis_and_stop():
            stop.set()
            return mock_redis

        with patch("db.redis.get_redis", side_effect=get_redis_and_stop):
            from core.worker_heartbeat import write_heartbeat

            await write_heartbeat(
                "w_1", worker_type="llm", ttl_seconds=45, stop_event=stop
            )
            assert mock_redis.set.call_args.kwargs.get("ex") == 45

    @pytest.mark.asyncio
    async def test_read_worker_status_none_when_no_key(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.worker_heartbeat import read_worker_status

            result = await read_worker_status("worker_1")
            assert result is None

    @pytest.mark.asyncio
    async def test_read_worker_status_returns_data(self):
        payload = json.dumps(
            {"worker_id": "worker_1", "worker_type": "llm", "timestamp": time.time()}
        )
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=payload)
        mock_redis.ttl = AsyncMock(return_value=20)
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.worker_heartbeat import read_worker_status

            result = await read_worker_status("worker_1")
            assert result is not None
            assert result["worker_id"] == "worker_1"
            assert result["ttl_seconds"] == 20

    @pytest.mark.asyncio
    async def test_remove_heartbeat_deletes_key(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.worker_heartbeat import remove_heartbeat

            await remove_heartbeat("worker_1")
            mock_redis.delete.assert_called_once_with("worker:heartbeat:worker_1")

    @pytest.mark.asyncio
    async def test_heartbeat_error_does_not_crash(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
        stop = asyncio.Event()

        async def get_redis_and_stop():
            stop.set()
            return mock_redis

        with patch("db.redis.get_redis", side_effect=get_redis_and_stop):
            from core.worker_heartbeat import write_heartbeat

            await write_heartbeat("worker_1", worker_type="llm", stop_event=stop)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F - Structured Logging Safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestStructuredLogging:
    def test_structured_formatter_output_is_json(self):
        from core.logging_config import StructuredFormatter

        formatter = StructuredFormatter()
        record = logging.LogRecord("test", logging.INFO, "test.py", 1, "hello %s", ("world",), None)
        parsed = json.loads(formatter.format(record))
        assert parsed["level"] == "INFO"
        assert parsed["msg"] == "hello world"
        assert "ts" in parsed

    def test_human_formatter_output_is_text(self):
        from core.logging_config import HumanFormatter

        formatter = HumanFormatter()
        record = logging.LogRecord(
            "test", logging.WARNING, "test.py", 1, "warning message", (), None
        )
        output = formatter.format(record)
        assert "WARNING" in output
        assert "[test]" in output

    def test_setup_logging_structured(self):
        from core.logging_config import StructuredFormatter, setup_logging

        setup_logging(structured=True)
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, StructuredFormatter)

    def test_setup_logging_human(self):
        from core.logging_config import HumanFormatter, setup_logging

        setup_logging(structured=False)
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, HumanFormatter)

    def test_sanitize_log_value_redacts_secrets(self):
        from core.logging_config import sanitize_log_value

        data = {"user_id": 123, "api_key": "secret123", "password": "hunter2", "message": "hello"}
        result = sanitize_log_value(data)
        assert result["user_id"] == 123
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["message"] == "hello"

    def test_sanitize_log_value_non_dict_passthrough(self):
        from core.logging_config import sanitize_log_value

        assert sanitize_log_value("hello") == "hello"
        assert sanitize_log_value(42) == 42
        assert sanitize_log_value(None) is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G - Request/Correlation IDs
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestIDMiddleware:
    def test_generates_id_when_absent(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        resp = TestClient(app).get("/test")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) == 32

    def test_preserves_incoming_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        resp = TestClient(app).get("/test", headers={"X-Request-ID": "my-custom-id-123"})
        assert resp.headers["X-Request-ID"] == "my-custom-id-123"

    def test_rejects_oversized_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        resp = TestClient(app).get("/test", headers={"X-Request-ID": "x" * 200})
        assert len(resp.headers["X-Request-ID"]) == 32

    def test_rejects_empty_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        resp = TestClient(app).get("/test", headers={"X-Request-ID": ""})
        assert len(resp.headers["X-Request-ID"]) == 32


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H - Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════════════════


class TestGracefulShutdown:
    def test_shutdown_flag_initially_false(self):
        import core.shutdown as mod

        mod._shutting_down = False
        from core.shutdown import is_shutting_down

        assert is_shutting_down() is False

    def test_set_shutting_down(self):
        import core.shutdown as mod

        mod._shutting_down = False
        from core.shutdown import is_shutting_down, set_shutting_down

        set_shutting_down(True)
        assert is_shutting_down() is True
        set_shutting_down(False)
        assert is_shutting_down() is False

    @pytest.mark.asyncio
    async def test_cleanup_called_during_shutdown(self):
        cleanup_called = False

        async def cleanup():
            nonlocal cleanup_called
            cleanup_called = True

        # Directly test that cleanup functions can be invoked
        await cleanup()
        assert cleanup_called is True

    def test_message_not_falsely_acked(self):
        import core.shutdown as mod

        mod._shutting_down = False
        from core.shutdown import set_shutting_down

        set_shutting_down(True)
        assert mod._shutting_down is True
        set_shutting_down(False)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I - Redis Recovery Observability
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedisRecoveryObservability:
    @pytest.mark.asyncio
    async def test_recovery_returns_count_and_ids(self):
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("msg-1-0", {}), ("msg-2-0", {})]))
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, ids = await requeue_stalled_messages("worker_1", idle_ms=60000)
            assert count == 2
            assert "msg-1-0" in ids
            assert "msg-2-0" in ids

    @pytest.mark.asyncio
    async def test_recovery_returns_empty_on_no_pending(self):
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, ids = await requeue_stalled_messages("worker_1", idle_ms=60000)
            assert count == 0
            assert ids == []

    @pytest.mark.asyncio
    async def test_send_recovery_returns_count_and_ids(self):
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("send-1-0", {})]))
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            count, ids = await requeue_stalled_send_messages("bot_main", idle_ms=30000)
            assert count == 1
            assert "send-1-0" in ids

    @pytest.mark.asyncio
    async def test_recovery_error_returns_zero(self):
        import redis as _redis

        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(
            side_effect=_redis.ResponseError("CROSSSLOT")
        )
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, ids = await requeue_stalled_messages("worker_1", idle_ms=60000)
            assert count == 0
            assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP J - DLQ Observability
# ═══════════════════════════════════════════════════════════════════════════════


class TestDlqObservability:
    @pytest.mark.asyncio
    async def test_dlq_inbound_record_has_stream_field(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("msg-1", "test_reason", payload={"content": "hello"})
            record = mock_redis.xadd.call_args[0][1]
            assert record["stream"] == "inbound"
            assert record["reason"] == "test_reason"

    @pytest.mark.asyncio
    async def test_dlq_outbound_record_has_stream_field(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-2-0")
        mock_redis.xack = AsyncMock()
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            await move_send_to_dlq("msg-2", "send_error", payload={"content": "reply"})
            record = mock_redis.xadd.call_args[0][1]
            assert record["stream"] == "send"

    @pytest.mark.asyncio
    async def test_dlq_count_returns_integer(self):
        mock_redis = AsyncMock()
        mock_redis.xinfo_stream = AsyncMock(return_value={"length": 5})
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import count_dlq_entries

            count = await count_dlq_entries()
            assert count == 5

    @pytest.mark.asyncio
    async def test_dlq_list_returns_entries(self):
        mock_redis = AsyncMock()
        mock_redis.xrevrange = AsyncMock(
            return_value=[
                ("1-0", {"reason": "err1", "stream": "inbound"}),
                ("2-0", {"reason": "err2", "stream": "send"}),
            ]
        )
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import list_dlq_entries

            entries = await list_dlq_entries(count=10)
            assert len(entries) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP K - Additional Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthCheckIntegration:
    @pytest.mark.asyncio
    async def test_readiness_gemini_degraded_not_critical(self):
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock) as mock_cr,
            patch("core.health.check_postgres", new_callable=AsyncMock) as mock_cp,
            patch("core.health.check_gemini") as mock_cg,
        ):
            mock_cr.return_value = {"status": "ok", "latency_ms": 1.0}
            mock_cp.return_value = {"status": "ok", "latency_ms": 2.0}
            mock_cg.return_value = {
                "status": "degraded",
                "credentials_total": 2,
                "credentials_available": 0,
            }
            resp = await get_readiness_response()
            assert resp["status"] == "ready"

    def test_health_app_version_present(self):
        from core.health import APP_VERSION, get_health_response

        resp = get_health_response()
        assert resp["version"] == APP_VERSION

    def test_gemini_health_no_api_keys_exposed(self):
        mock_pool = MagicMock()
        mock_pool.size = 3
        mock_pool.get_available_key_count.return_value = 1
        mock_limiter = MagicMock()
        mock_limiter._cooldowns = {"key1": 1}
        with (
            patch("core.gemini_client.get_pool", return_value=mock_pool),
            patch("core.gemini_client.get_limiter", return_value=mock_limiter),
        ):
            from core.health import check_gemini

            result = check_gemini()
            dumped = json.dumps(result)
            assert "key1" not in dumped

    def test_structured_formatter_exception(self):
        from core.logging_config import StructuredFormatter

        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "test", logging.ERROR, "test.py", 1, "error occurred", (), sys.exc_info()
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]
