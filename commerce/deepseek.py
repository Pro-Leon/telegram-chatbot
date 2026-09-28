"""DeepSeek V4 Flash commerce signal adapter (Phase 5.3B).

Runs on the sole LLM provider (llama.cpp via :func:`core.llm_provider.get_llm_provider`),
exactly like ``core/scoring.py`` and ``memory/profile.py``.

The model is the provider default (``LLAMA_MODEL``); callers must not pass
a stale provider-specific model name.

Contract:

- ``extract_commerce_signals(conversation_context) -> CommerceSignals``
  NEVER raises for model/transport failures and NEVER returns an
  interest-bearing signal set on failure: any failure yields the
  deterministic ``CommerceSignals.low_information()`` fallback.
- Output is strict JSON (``response_mime_type="application/json"``),
  parsed by ``_parse_signals_json`` and validated by
  ``CommerceSignals`` (extra="forbid", bounded floats, strict bools).
- Temperature is fixed at 0.0: no model randomness, no clock, no
  randomness in code — deterministic for identical inputs.
- Empty or absent conversation input short-circuits to the fallback
  WITHOUT calling the model.
- No action, authorization, eligibility, or decision logic lives here;
  the deterministic engine (:func:`commerce.decision.decide_commerce_action`)
  is the only source of commerce actions.
"""

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from commerce.signals import (
    MAX_EVIDENCE_ITEM_LENGTH,
    MAX_EVIDENCE_ITEMS,
    CommerceSignals,
)
from core.config import get_settings
# Kept for backward-compat with tests that patch commerce.deepseek.get_llm_provider
from core.llm_provider import get_llm_provider  # noqa: F401

logger = logging.getLogger("commerce_deepseek")
_settings = get_settings()

# Bounded input/output knobs (deterministic, documented).
SIGNAL_MAX_TRANSCRIPT_MESSAGES = 30
SIGNAL_MAX_MESSAGE_CHARS = 800
SIGNAL_MAX_OUTPUT_TOKENS = 1024
SIGNAL_TEMPERATURE = 0.0

COMMERCE_SIGNAL_EXTRACTION_SYSTEM = (
    """You are a commerce signal analyzer for a fan-management system.

Read the conversation transcript and return ONE JSON object with exactly these fields:
{
  "purchase_intent": float 0.0-1.0,
  "content_interest": float 0.0-1.0,
  "relationship_engagement": float 0.0-1.0,
  "price_interest": float 0.0-1.0,
  "explicit_purchase_request": boolean,
  "explicit_content_request": boolean,
  "requested_price": number or null,
  "declined_recent_offer": boolean,
  "accepted_recent_offer": boolean,
  "asks_for_free_content": boolean,
  "negative_sentiment": float 0.0-1.0,
  "conversation_relevance": float 0.0-1.0,
  "confidence": float 0.0-1.0,
  "evidence": [string, max """
    + str(MAX_EVIDENCE_ITEMS)
    + """ items, each <= """
    + str(MAX_EVIDENCE_ITEM_LENGTH)
    + """ chars],
  "model_uncertainty": float 0.0-1.0,
  "primary_intent": string, one of: greeting, casual_chat, personal_disclosure, relationship_building, content_curiosity, content_request, purchase_intent, price_inquiry, tip_interest, complaint, rejection, hesitation, reassurance, appreciation, operator_request, uncertain, other,
  "intent_tags": [string, subset of primary_intent values that also apply],
  "negative_intent_tags": [string, subset of: hesitation, rejection, complaint],
  "fan_asks_question": boolean,
  "topic_continuity": string or null, the conversation topic if steady, null if shifting
}

Rules:
- Every float MUST be between 0.0 and 1.0 inclusive.
- Do NOT invent signals. If a signal is absent, use 0.0 or false.
- "explicit_purchase_request" is true ONLY when the fan literally asks to buy something (for example "I want to buy", "how do I pay" is NOT explicit).
- "explicit_content_request" is true ONLY when the fan literally asks for the content (for example "send me the video", "I want the pics").
- "requested_price" is the exact amount the fan quoted, otherwise null. If the fan asks for FREE content, set "asks_for_free_content" to true and "requested_price" to null.
- "declined_recent_offer" / "accepted_recent_offer": true ONLY when the fan explicitly discusses an earlier offer made in this conversation.
- "evidence" holds short quoted fragments that justify the scores. NEVER include card numbers, CVV, PAN, or any payment data in evidence. NEVER invent quotes.
- "primary_intent" is the single most dominant intent. Use "uncertain" when the message is ambiguous or low-information.
- "intent_tags" captures ALL applicable intents (may overlap). Keep to 1-3 tags.
- "negative_intent_tags" captures ONLY hesitation, rejection, or complaint signals. Leave empty if none.
- "fan_asks_question" is true when the fan's message is a question (ends with ? or seeks information).
- "topic_continuity" names the current topic if the conversation is steady (e.g. "gym_routine", "vacation"); null if the topic shifts or is unclear.
- Output ONLY the JSON object. No commentary, no markdown fences."""
)


def compose_signal_extraction_input(conversation_context: list[dict[str, Any]] | None) -> str:
    """Build a bounded transcript for signal extraction.

    System messages are skipped (persona boilerplate carries no signals).
    At most ``SIGNAL_MAX_TRANSCRIPT_MESSAGES`` messages are kept and each
    message is truncated to ``SIGNAL_MAX_MESSAGE_CHARS`` characters.
    Returns "" for missing/empty context.
    """
    if not conversation_context:
        return ""
    lines: list[str] = []
    for msg in conversation_context[-SIGNAL_MAX_TRANSCRIPT_MESSAGES:]:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system" or not isinstance(content, str):
            continue
        content = content[:SIGNAL_MAX_MESSAGE_CHARS]
        if role in ("user", "customer", "fan"):
            lines.append(f"Fan: {content}")
        else:
            lines.append(f"Creator: {content}")
    return "\n".join(lines)


def _parse_signals_json(text: str | None) -> dict[str, Any] | None:
    """Parse strict JSON (tolerating only surrounding code fences).

    Returns None for anything that is not a JSON object.
    """
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _build_signals(raw: dict[str, Any]) -> CommerceSignals | None:
    """Validate raw model output into CommerceSignals.

    Returns None on ANY validation failure (bounds, types, extras,
    evidence bounds, payment data, missing fields).
    """
    try:
        return CommerceSignals(**raw)
    except (ValidationError, TypeError, ValueError):
        return None


def _low_information(kind: str) -> CommerceSignals:
    """Log a bounded, redacted failure notice and return the safe fallback."""
    logger.warning(
        "commerce signal extraction failed (%s) — using low-information fallback (model=%s)",
        kind,
        getattr(_settings, "llama_model", "default"),
    )
    return CommerceSignals.low_information()


async def extract_commerce_signals(
    conversation_context: list[dict[str, Any]] | None,
) -> CommerceSignals:
    """Extract validated commerce signals from a conversation transcript.

    Safe-by-construction: transport errors, malformed output, invalid
    values, and empty input all produce ``CommerceSignals.low_information()``.
    Failure NEVER looks like interest, and this function never decides a
    commerce action.
    """
    transcript = compose_signal_extraction_input(conversation_context)
    if not transcript.strip():
        return _low_information("empty_context")

    try:
        # Sole provider (llama.cpp): single attempt, generic JSON.
        # Failure taxonomy preserved: any transport failure -> low_information().
        provider = get_llm_provider()
        response_text = await provider.generate(
            system_instruction=COMMERCE_SIGNAL_EXTRACTION_SYSTEM,
            user_content=transcript,
            response_mime_type="application/json",
            max_output_tokens=SIGNAL_MAX_OUTPUT_TOKENS,
            temperature=SIGNAL_TEMPERATURE,
        )
    except Exception:
        logger.warning(
            "commerce signal extraction transport failure (model=%s) — using low-information fallback",
            getattr(_settings, "llama_model", "default"),
            exc_info=True,
        )
        return CommerceSignals.low_information()

    raw = _parse_signals_json(response_text)
    if raw is None:
        return _low_information("malformed_json")

    signals = _build_signals(raw)
    if signals is None:
        return _low_information("invalid_payload")

    return signals
