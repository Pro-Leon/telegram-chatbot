"""Context Engine Path Selector (Phase 79).

Centralized runtime path selection between the new Context Engine +
one-generation Qwen path and the legacy 3-LLM pipeline.

SAFETY:
- Path selection is evaluated before generation
- The selector is centralized and explicit
- Invalid configurations are rejected safely
- Default path is "new" (Context Engine)
- Rollback is configuration-driven (no code changes)
"""

from __future__ import annotations

import enum
import logging

from core.config import get_settings

logger = logging.getLogger("context_engine.path_selector")


class LLMPath(str, enum.Enum):
    """Runtime LLM path selection.

    NEW: Context Engine + one Qwen generation (active production path)
    LEGACY: existing 3-LLM pipeline (fallback/rollback)
    """

    NEW = "new"
    LEGACY = "legacy"


def get_active_path() -> LLMPath:
    """Get the currently configured active LLM path.

    Returns LLMPath.NEW or LLMPath.LEGACY based on settings.llm_path.
    Invalid values default to LLMPath.LEGACY for safety.
    """
    settings = get_settings()
    raw = getattr(settings, "llm_path", "new")

    try:
        path = LLMPath(raw)
    except ValueError:
        logger.warning(
            "invalid llm_path=%r, falling back to legacy for safety", raw
        )
        path = LLMPath.LEGACY

    return path


def is_new_path_active() -> bool:
    """Check if the new Context Engine path is the active production path."""
    return get_active_path() == LLMPath.NEW


def log_path_selection(generation_id: str) -> None:
    """Log the active path for operational visibility."""
    path = get_active_path()
    logger.info(
        "llm_path_select generation=%s path=%s",
        generation_id,
        path.value,
    )
