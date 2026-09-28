"""Context Engine A/B Canary Configuration (Phase 77).

Controls the observational A/B canary path that compares the Context Engine +
one-generation Qwen path against the existing authoritative 3-LLM pipeline.

SAFETY:
- Default mode: DISABLED
- The canary path NEVER sends messages
- The canary path NEVER creates offers
- The canary path NEVER mutates commerce state
- The canary path NEVER mutates Redis or PostgreSQL
- Any failure in the canary path does not affect the production path
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("context_engine.canary")


class CanaryMode(str, enum.Enum):
    """Canary operation mode.

    DISABLED: No canary execution. Default and safe.
    OBSERVE: Canary runs observationally, output recorded but never acted upon.
    """

    DISABLED = "disabled"
    OBSERVE = "observe"


@dataclass(frozen=True)
class CanaryConfig:
    """Configuration for the A/B canary observation path.

    All fields are immutable after construction.
    """

    mode: CanaryMode = CanaryMode.DISABLED
    sample_rate: float = 0.0
    timeout_seconds: float = 30.0
    max_output_tokens: int = 500
    model: str = ""  # empty = use production llama_model (sole provider default)

    @classmethod
    def from_settings(cls, settings: Any = None) -> CanaryConfig:
        """Build config from application settings.

        Args:
            settings: Application settings object. If None, uses core.config.settings.

        Returns:
            CanaryConfig with values from settings.
        """
        if settings is None:
            from core.config import settings as _settings

            settings = _settings

        mode_str = getattr(settings, "context_engine_canary_mode", "disabled")
        try:
            mode = CanaryMode(mode_str)
        except ValueError:
            logger.warning(
                "Invalid canary mode %r, falling back to DISABLED", mode_str
            )
            mode = CanaryMode.DISABLED

        return cls(
            mode=mode,
            sample_rate=getattr(settings, "context_engine_canary_sample_rate", 0.0),
            timeout_seconds=getattr(
                settings, "context_engine_canary_timeout", 30.0
            ),
            max_output_tokens=getattr(
                settings, "context_engine_canary_max_tokens", 500
            ),
            model=getattr(settings, "context_engine_canary_model", ""),
        )

    def should_run(self, user_id: int) -> bool:
        """Determine if canary should run for this user.

        Uses deterministic hashing for consistent sampling.
        When mode is DISABLED, always returns False.
        """
        if self.mode == CanaryMode.DISABLED:
            return False

        if self.sample_rate >= 1.0:
            return True

        if self.sample_rate <= 0.0:
            return False

        # Deterministic hash-based sampling
        h = hash(f"canary:{user_id}") % 10000
        return h < (self.sample_rate * 10000)

    @property
    def is_enabled(self) -> bool:
        """True if canary mode is not DISABLED."""
        return self.mode != CanaryMode.DISABLED
