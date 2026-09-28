"""Execution stage derivation -- deterministic resolver from existing evidence.

Derives the current execution stage for a generation without new DB table.
Consumes generation events, telemetry, messages, and send state. Returns UNKNOWN if evidence absent.
"""

from __future__ import annotations
from enum import Enum
from typing import Any

class ExecutionStage(str, Enum):
    RECEIVED = "RECEIVED"
    DEBOUNCING = "DEBOUNCING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    CONTEXT = "CONTEXT"
    SIGNAL = "SIGNAL"
    DECISION = "DECISION"
    SAFETY_GATE = "SAFETY_GATE"
    QWEN = "QWEN"
    SCORING = "SCORING"
    AUTHORITY = "AUTHORITY"
    SEND_QUEUED = "SEND_QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    OUTCOME = "OUTCOME"
    FAILED = "FAILED"
    WAITING = "WAITING"
    UNKNOWN = "UNKNOWN"

# Stage order for UI
STAGE_ORDER = [
    ExecutionStage.RECEIVED,
    ExecutionStage.DEBOUNCING,
    ExecutionStage.QUEUED,
    ExecutionStage.PROCESSING,
    ExecutionStage.CONTEXT,
    ExecutionStage.SIGNAL,
    ExecutionStage.DECISION,
    ExecutionStage.SAFETY_GATE,
    ExecutionStage.QWEN,
    ExecutionStage.SCORING,
    ExecutionStage.AUTHORITY,
    ExecutionStage.SEND_QUEUED,
    ExecutionStage.SENDING,
    ExecutionStage.SENT,
    ExecutionStage.OUTCOME,
]

def derive_stage(
    *,
    has_message_created: bool = False,
    is_debouncing: bool = False,
    is_queued: bool = False,
    has_generation_started: bool = False,
    has_context: bool = False,
    has_signal: bool = False,
    has_decision: bool = False,
    has_safety_gate: bool = False,
    has_qwen: bool = False,
    has_scoring: bool = False,
    has_authority: bool = False,
    is_send_queued: bool = False,
    is_sending: bool = False,
    has_sent: bool = False,
    has_outcome: bool = False,
    has_failed: bool = False,
) -> ExecutionStage:
    """Deterministic stage resolver. Latest known stage wins."""
    if has_failed:
        return ExecutionStage.FAILED
    if has_outcome:
        return ExecutionStage.OUTCOME
    if has_sent:
        return ExecutionStage.SENT
    if is_sending:
        return ExecutionStage.SENDING
    if is_send_queued:
        return ExecutionStage.SEND_QUEUED
    if has_authority:
        return ExecutionStage.AUTHORITY
    if has_scoring:
        return ExecutionStage.SCORING
    if has_qwen:
        return ExecutionStage.QWEN
    if has_safety_gate:
        return ExecutionStage.SAFETY_GATE
    if has_decision:
        return ExecutionStage.DECISION
    if has_signal:
        return ExecutionStage.SIGNAL
    if has_context:
        return ExecutionStage.CONTEXT
    if has_generation_started:
        return ExecutionStage.PROCESSING
    if is_queued:
        return ExecutionStage.QUEUED
    if is_debouncing:
        return ExecutionStage.DEBOUNCING
    if has_message_created:
        return ExecutionStage.RECEIVED
    return ExecutionStage.UNKNOWN

def derive_from_generation(
    generation_events: list[dict[str, Any]],
    telemetry: dict[str, Any] | None = None,
    is_queued: bool = False,
    is_sending: bool = False,
    has_sent: bool = False,
) -> ExecutionStage:
    """Higher-level helper that inspects generation event list."""
    has_started = any(e.get("event_type") == "ai.generation_started" for e in generation_events)
    has_completed = any(e.get("event_type") == "ai.generation_completed" for e in generation_events)
    has_failed = any(e.get("event_type") in ("ai.generation_failed", "message.send_failed") for e in generation_events)
    # Telemetry hints
    has_context = bool(telemetry and telemetry.get("context_build_ms"))
    has_signal = bool(telemetry and telemetry.get("commercial_objective") is not None)
    has_decision = bool(telemetry and telemetry.get("decision_trace"))
    has_safety = bool(telemetry and telemetry.get("operation_allowed") is not None)
    has_qwen = has_started  # QWEN runs after started
    has_scoring = bool(telemetry and telemetry.get("scoring_score") is not None)
    has_authority = bool(telemetry and telemetry.get("routing_decision"))
    is_send_queued = has_completed and any(e.get("data", {}).get("was_auto_approved") for e in generation_events if e.get("event_type") == "ai.generation_completed")
    has_outcome = bool(telemetry and telemetry.get("outcome"))
    return derive_stage(
        has_message_created=True,
        has_generation_started=has_started,
        has_context=has_context,
        has_signal=has_signal,
        has_decision=has_decision,
        has_safety_gate=has_safety,
        has_qwen=has_qwen,
        has_scoring=has_scoring,
        has_authority=has_authority,
        is_send_queued=is_send_queued,
        is_sending=is_sending,
        has_sent=has_sent,
        has_outcome=has_outcome,
        has_failed=has_failed,
        is_queued=is_queued,
    )
