"""Health check functions for Redis, PostgreSQL, llama.cpp, and Dropfans status.

Provides lightweight dependency checks used by /health and /ready endpoints.
Does NOT make live Dropfans API calls — exposes locally-known state only.
Dropfans is the sole active commerce provider. Fangate is legacy-only.
"""

import logging
import time
from typing import Any

logger = logging.getLogger("health")

APP_VERSION = "5.0a"


async def check_redis() -> dict[str, Any]:
    """Ping Redis and return status."""
    try:
        from db.redis import get_redis

        r = await get_redis()
        start = time.monotonic()
        await r.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception:
        logger.debug("Redis health check failed", exc_info=True)
        return {"status": "error"}


async def check_postgres() -> dict[str, Any]:
    """Execute SELECT 1 and return status."""
    try:
        from db.postgres import get_pool

        pool = await get_pool()
        start = time.monotonic()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception:
        logger.debug("Postgres health check failed", exc_info=True)
        return {"status": "error"}


async def check_llamacpp() -> dict[str, Any]:
    """Check llama.cpp availability via lightweight GET /v1/models.

    No generation is performed. Returns provider health for the sole LLM.
    """
    try:
        from core.llm_provider import get_llm_provider

        provider = get_llm_provider()
        healthy = await provider.health_check()
        return {"status": "available" if healthy else "degraded"}
    except Exception:
        logger.debug("llama.cpp health check failed", exc_info=True)
        return {"status": "unconfigured"}


def check_gemini() -> dict[str, Any]:
    """Legacy alias retained for backward-compat; delegates to llama.cpp.

    The Gemini stack was removed; health callers should migrate to
    check_llamacpp(). This wrapper preserves the old import path.
    """
    # Synchronous wrapper cannot await; report unconfigured so callers that
    # still reference Gemini do not mistake it for healthy.
    return {"status": "unconfigured", "note": "gemini_removed_use_llamacpp"}


_dropfans_cache: dict[str, Any] = {}
_DROPFANS_CACHE_TTL_SECONDS = 10.0


async def check_dropfans() -> dict[str, Any]:
    """Return CACHED Dropfans integration state. Never calls the Dropfans API
    and never affects readiness — Dropfans downtime must not degrade the CRM."""
    now = time.monotonic()
    cached = _dropfans_cache.get("value")
    if cached is not None and now - _dropfans_cache.get("ts", 0) < _DROPFANS_CACHE_TTL_SECONDS:
        return cached

    try:
        from db import dropfans as ddb
        from db import fangate as fdb

        # Check Dropfans integrations
        dropfans_integrations = 0
        active_integrations = await fdb.list_active_creator_ids()
        for cid in (active_integrations or []):
            df_int = await ddb.get_dropfans_integration(cid)
            if df_int and df_int.get("dropfans_creator_id"):
                dropfans_integrations += 1

        if dropfans_integrations == 0:
            result = {"status": "unconfigured", "integrations": 0}
        else:
            result = {"status": "active", "integrations": dropfans_integrations}
    except Exception:  # noqa: BLE001 — health checks must never raise
        result = {"status": "unavailable"}

    _dropfans_cache.update({"value": result, "ts": now})
    return result


async def check_fangate() -> dict[str, Any]:
    """Return CACHED Fangate integration state (legacy)."""
    now = time.monotonic()
    cached_key = "fangate_value"
    cached = _dropfans_cache.get(cached_key)
    if cached is not None and now - _dropfans_cache.get("fangate_ts", 0) < _DROPFANS_CACHE_TTL_SECONDS:
        return cached

    try:
        from db import fangate as fdb

        rows = await fdb.list_integration_statuses()
    except Exception:  # noqa: BLE001
        result = {"status": "unavailable"}
        _dropfans_cache.update({cached_key: result, "fangate_ts": now})
        return result

    if not rows:
        result = {"status": "unconfigured", "integrations": 0}
    else:
        successes = [r["last_success_at"] for r in rows if r["last_success_at"] is not None]
        errors = [r for r in rows if r["last_error_at"] is not None]
        last_error = errors[-1]["last_error"] if errors else None
        status = "active" if successes else "degraded"
        result = {
            "status": status,
            "integrations": len(rows),
            "reachable": bool(successes),
            "last_success_at": max(successes).isoformat() if successes else None,
            "last_error_at": errors[-1]["last_error_at"].isoformat() if errors else None,
            "last_error": last_error,
        }
    _dropfans_cache.update({cached_key: result, "fangate_ts": now})
    return result


def get_health_response() -> dict[str, Any]:
    """Build the /health response body. No dependency checks."""
    return {
        "status": "ok",
        "service": "chatbotv2",
        "version": APP_VERSION,
    }


async def get_readiness_response() -> dict[str, Any]:
    """Build the /ready response body with dependency checks."""
    redis_check = await check_redis()
    postgres_check = await check_postgres()
    llamacpp_check = await check_llamacpp()
    dropfans_check = await check_dropfans()

    all_deps = {
        "redis": redis_check,
        "postgres": postgres_check,
        "llamacpp": llamacpp_check,
        "dropfans": dropfans_check,
    }

    critical_ok = redis_check["status"] == "ok" and postgres_check["status"] == "ok"

    return {
        "status": "ready" if critical_ok else "not_ready",
        "dependencies": all_deps,
    }
