"""Qualification Intelligence — structured discovery state (Phase 14)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class QualificationState:
    missing_facts: list[str]  # e.g., ["format_preference", "budget"]
    known_facts: list[str]
    confidence: float

def derive_qualification_state(preferences: dict[str, Any], purchase_history: list[dict[str, Any]], recent_offers: list[dict[str, Any]]) -> QualificationState:
    missing = []
    known = []
    if not preferences.get("format"):
        missing.append("format_preference")
    else:
        known.append("format_preference")
    if not preferences.get("budget"):
        missing.append("budget_sensitivity")
    else:
        known.append("budget_sensitivity")
    # Add more as needed
    return QualificationState(missing_facts=missing, known_facts=known, confidence=0.7 if known else 0.3)
