"""Autonomous operation persistence and domain models.

Provider-neutral automation layer. Contains no provider calls,
no API keys, no credentials.
"""

from automation.models import (
    AutomationAction,
    AutomationErrorClass,
    AutomationOperation,
    AutomationResult,
    AutomationStatus,
)

__all__ = [
    "AutomationAction",
    "AutomationErrorClass",
    "AutomationOperation",
    "AutomationResult",
    "AutomationStatus",
]
