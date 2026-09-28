"""Sunny architecture router — V1 cutover boundary (Phase 3).

Single choke point answering: which architecture owns this event?

    Incoming event
         |
         v
    Architecture Router
         |
         +---- V1 -> DISABLED (frozen legacy, must not process conversations)
         |
         +---- V2 -> NOT ACTIVE YET (specification only, no runtime)
         |
         +---- Commerce -> PRESERVED (deterministic authority, untouched)

Rules:
- V1 paths consult ``is_v1_conversational_enabled()`` and fail closed when
  disabled. The flag defaults to False and cannot be re-enabled by
  WebSocket, browser, API, or queue payload parameters — only by explicit
  server-side configuration (env ``SUNNY_V1_ENABLED=true``).
- V2 has no runtime yet; ``is_v2_enabled()`` exists so future activation
  does not require resurrecting V1.
- Commerce is independent of both flags; this module never gates commerce.

CURRENT status markers used across sunny_v2 docs:
CURRENT / FUTURE / PRESERVED / DEPRECATED / DISABLED / UNKNOWN
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sunny.architecture_router")

# Canonical architecture identifiers.
V1 = "v1_legacy_conversational"
V2 = "v2_relationship"
COMMERCE = "deterministic_commerce"

# V1 runtime status. DISABLED is the frozen post-cutover state.
V1_STATUS = "DISABLED"
# V2 implementation status. SPECIFICATION means documented, not implemented.
V2_STATUS = "SPECIFICATION / NOT IMPLEMENTED"
# Commerce status. PRESERVED means code/config/schema/provider path intact.
COMMERCE_STATUS = "PRESERVED"


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_v1_conversational_enabled() -> bool:
    """Return True only when legacy V1 conversational processing is enabled.

    Default False (disabled). Reads ``SUNNY_V1_ENABLED`` env directly so the
    gate works even when settings objects are cached at import time, then
    falls back to ``core.config.Settings.sunny_v1_enabled`` when available.
    Server-side only — callers must never accept this value from request
    bodies, query params, or stream payloads.
    """
    try:
        if _env_true("SUNNY_V1_ENABLED"):
            return True
    except Exception:
        pass
    try:
        from core.config import get_settings as _get_settings

        return bool(_get_settings().sunny_v1_enabled)
    except Exception:
        return False


def is_v2_enabled() -> bool:
    """Return True when the future V2 runtime is active. Always False today."""
    try:
        if _env_true("SUNNY_V2_ENABLED") or _env_true("RELATIONSHIP_V2_ENABLED"):
            return True
    except Exception:
        pass
    try:
        from core.config import get_settings as _get_settings

        s = _get_settings()
        return bool(s.sunny_v2_enabled or s.relationship_v2_enabled)
    except Exception:
        return False


def _v2_flag(env_name: str, attr: str) -> bool:
    try:
        if _env_true(env_name):
            return True
    except Exception:
        pass
    try:
        from core.config import get_settings as _get_settings

        return bool(getattr(_get_settings(), attr))
    except Exception:
        return False


def is_v2_read_enabled() -> bool:
    """V2 may read/build context (shadow or live). Default False."""
    return _v2_flag("RELATIONSHIP_V2_READ_ENABLED", "relationship_v2_read_enabled")


def is_v2_write_enabled() -> bool:
    """V2 may persist relationship state. Default False."""
    return _v2_flag("RELATIONSHIP_V2_WRITE_ENABLED", "relationship_v2_write_enabled")


def is_v2_shadow_mode() -> bool:
    """V2 observe-only: process/log diffs, never control replies. Default False."""
    return _v2_flag("RELATIONSHIP_V2_SHADOW_MODE", "relationship_v2_shadow_mode")


def route_inbound(kind: str) -> str:
    """Classify an inbound event kind to its owning architecture.

    Pure function, no I/O. ``kind`` is a caller-supplied label such as
    ``"conversation"``, ``"commerce_webhook"``, or ``"schedule"``.
    Returns one of ``V1`` / ``V2`` / ``COMMERCE`` / ``"unknown"``.
    """
    k = (kind or "").strip().lower()
    if k in ("conversation", "inbound_message", "llm_generation", "operator_flush"):
        return V1
    if k in ("v2_conversation", "v2_relationship", "v2_memory"):
        return V2
    if k in (
        "commerce_webhook",
        "commerce_reconciliation",
        "commerce_scheduled",
        "post_purchase",
        "send_delivery",
    ):
        return COMMERCE
    return "unknown"


def log_v1_suppressed(location: str, *, user_id: int | None = None) -> None:
    """Best-effort observability for a suppressed V1 path. Never raises."""
    try:
        logger.warning(
            "V1 conversational path suppressed at %s (V1_STATUS=%s) user=%s",
            location,
            V1_STATUS,
            user_id,
        )
    except Exception:
        pass
