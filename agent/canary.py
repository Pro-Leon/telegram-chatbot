"""Canary routing module.

Deterministic canary routing for agent runtime activation.
Uses stable hashing to ensure consistent behavior per user/conversation.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agent.canary")


@dataclass(frozen=True)
class CanaryConfig:
    """Canary configuration."""
    enabled: bool = False
    sample_rate: float = 0.0
    creator_ids: tuple[int, ...] = ()
    
    @classmethod
    def from_settings(cls) -> CanaryConfig:
        """Load canary config from settings."""
        try:
            from core.config import Settings
            settings = Settings()
            
            # Parse creator IDs
            creator_ids_str = settings.ai_agent_canary_creator_ids
            creator_ids = ()
            if creator_ids_str:
                creator_ids = tuple(
                    int(x.strip()) 
                    for x in creator_ids_str.split(",") 
                    if x.strip()
                )
            
            return cls(
                enabled=settings.ai_agent_canary_enabled,
                sample_rate=settings.ai_agent_canary_sample_rate,
                creator_ids=creator_ids,
            )
        except Exception as e:
            logger.warning(f"Failed to load canary config: {e}")
            return cls()


def _stable_hash(user_id: int, salt: str = "agent_canary") -> float:
    """Generate stable hash for user_id.
    
    Returns float in [0.0, 1.0) for deterministic canary routing.
    Same user_id always produces same hash.
    """
    hash_input = f"{user_id}:{salt}"
    hash_bytes = hashlib.sha256(hash_input.encode()).digest()
    # Convert first 8 bytes to float in [0.0, 1.0)
    hash_int = int.from_bytes(hash_bytes[:8], byteorder="big")
    return hash_int / (2**64)


def should_use_agent(
    user_id: int,
    creator_id: int | None = None,
    config: CanaryConfig | None = None,
) -> bool:
    """Determine if this user should use agent runtime.
    
    Deterministic: same user_id always gets same decision.
    Stable: decision doesn't change between messages.
    """
    if config is None:
        config = CanaryConfig.from_settings()
    
    # Canary disabled → legacy
    if not config.enabled:
        return False
    
    # Sample rate 0 → legacy
    if config.sample_rate <= 0.0:
        return False
    
    # Sample rate 1.0 → agent for all
    if config.sample_rate >= 1.0:
        # Check creator filter
        if config.creator_ids and creator_id not in config.creator_ids:
            return False
        return True
    
    # Deterministic sampling based on user_id hash
    hash_value = _stable_hash(user_id)
    
    # Check creator filter
    if config.creator_ids and creator_id not in config.creator_ids:
        return False
    
    return hash_value < config.sample_rate


def get_canary_info(user_id: int, creator_id: int | None = None) -> dict[str, Any]:
    """Get canary routing info for observability."""
    config = CanaryConfig.from_settings()
    hash_value = _stable_hash(user_id)
    use_agent = should_use_agent(user_id, creator_id, config)
    
    return {
        "canary_enabled": config.enabled,
        "canary_sample_rate": config.sample_rate,
        "canary_creator_filter": bool(config.creator_ids),
        "user_hash": hash_value,
        "use_agent": use_agent,
    }
