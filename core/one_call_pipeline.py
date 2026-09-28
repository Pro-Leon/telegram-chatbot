"""Phase 75F: Qwen2.5 one-call pipeline implementation.

Integrates all previous phases into a single pipeline:
1. Context compaction (Phase 75C)
2. Commerce signal hints (Phase 75D)
3. One-call generation (Phase 75B)
4. Deterministic scoring (Phase 75E)
5. Routing decision

This pipeline replaces the 3-LLM pipeline with a single Qwen2.5
generation call that outputs structured JSON containing reply,
commerce signals, confidence, and handoff flag.
"""

import json
import logging
from typing import Any

from core.config import get_settings
from core.llm_provider import get_llm_provider
from core.one_call import (
    ONE_CALL_SYSTEM_PROMPT,
    OneCallResult,
    detect_prompt_echo,
    format_one_call_prompt,
    validate_one_call_response,
)
from core.output_rails import check as output_rails_check
from core.context_compact import build_one_call_context, build_one_call_from_snapshot, validate_one_call_context
from core.commerce_prompt import build_commerce_signal_hints, COMMERCE_SIGNAL_INSTRUCTIONS
from core.commerce_context_gate import commerce_context_required
from core.scoring_deterministic import validate_draft_quality

logger = logging.getLogger("one_call_pipeline")

_settings = get_settings()

# Phase 5.4: greeting fast-path (reply-only, no JSON schema).
# Cold greetings ("hi", fresh user, no history) fail under the full OneCall
# contract (placeholder echo, truncation at 400-600 caps, role inversion),
# so they take a plain-text generation validated by the same Step 6 rails.
# Conservative trigger: short known-greeting text + new funnel + no history.
# Anything else falls through to the canonical JSON path untouched.
_GREETING_TEXTS = frozenset({
    "hi", "hello", "hey", "hii", "hiii", "heyy", "heyyy",
    "hi!", "hello!", "hey!", "hey there", "hi there", "hello there",
    "yo", "sup", "hiya",
})

GREETING_SYSTEM_PROMPT = """You are Sunny, chatting directly with one fan. You are always Sunny; the fan is never Sunny and never shares your name. Reply with one short message only, plain text, no JSON, no labels.

EXAMPLES:
Fan: hi
Sunny: heyy, how are you
Fan: good, just abit busy. how are you?
Sunny: busy girlieee i feel that, im good just chilling. what are you up to today

Rules: short (1-2 sentences), lowercase texting ok, occasional emoji ok. Never call the fan Sunny. Never invent shared history, love, friendship, or past events."""


def is_cold_greeting(
    user_message: Any,
    user: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
) -> bool:
    """True when a turn qualifies for the reply-only greeting fast-path."""
    try:
        if not isinstance(user_message, str):
            return False
        if user_message.strip().lower() not in _GREETING_TEXTS:
            return False
        u = user if isinstance(user, dict) else {}
        if str(u.get("funnel_stage", "new")) != "new":
            return False
        try:
            if int(u.get("message_count", 0) or 0) > 2:
                return False
        except Exception:
            pass
        # Recent includes the just-saved current inbound in production
        # (handlers persist before enqueue), so turn 1 arrives with one row.
        # Allow that; anything more means real history.
        try:
            if recent_messages and len(recent_messages) > 1:
                return False
        except Exception:
            return False
        return True
    except Exception:
        return False


async def _greeting_fastpath_generation(
    user_id: int,
    creator_id: int,
    user_message: str,
    user: dict[str, Any] | None,
    generation_id: str | None = None,
    system_prompt: str | None = None,
    kind: str = "greeting_fastpath",
    history_lines: tuple[str, ...] | list[str] | None = None,
    max_output_tokens: int = 100,
    recent_outbound: tuple[str, ...] | list[str] | None = None,
) -> OneCallResult:
    """Plain-text generation with Step 6 validation. Never raises."""
    from commerce.signals import CommerceSignals

    _provider_name = ""
    _model_name = ""
    _prompt_tokens = None
    _output_tokens = None
    _gen_latency_ms = None
    try:
        provider = get_llm_provider()
        try:
            _provider_name = getattr(provider, "provider_name", "") or getattr(_settings, "llm_provider", "llamacpp")
            _model_name = getattr(provider, "_model", None) or getattr(_settings, "llama_model", "default")
        except Exception:
            pass
        import time as _t_greet
        _gen_start = _t_greet.monotonic()
        first = ""
        try:
            first = str((user or {}).get("first_name", "") or "").strip()
        except Exception:
            first = ""
        _wire_model = getattr(provider, "_model", None) or getattr(_settings, "llama_model", "default")
        _uc = f"Fan ({first or 'there'}) says: {user_message}"
        try:
            if history_lines:
                _hist = "\n".join(str(x) for x in list(history_lines)[-6:] if str(x).strip())
                if _hist:
                    _uc = f"Recent conversation:\n{_hist}\n\n{_uc}"
        except Exception:
            pass
        text = await provider.generate(
            system_instruction=system_prompt or GREETING_SYSTEM_PROMPT,
            user_content=_uc,
            model=_wire_model,
            response_mime_type=None,
            onecall_json_schema=False,
            max_output_tokens=max_output_tokens,
            temperature=0.7,
        )
        _gen_latency_ms = int((_t_greet.monotonic() - _gen_start) * 1000)
        try:
            _prompt_tokens = getattr(provider, "last_prompt_tokens", None)
            _output_tokens = getattr(provider, "last_generation_tokens", None)
        except Exception:
            pass
        reply = text.strip() if isinstance(text, str) else ""
        if not reply:
            raise ValueError("empty greeting reply")
        signals = CommerceSignals.low_information()
        try:
            signals.primary_intent = "greeting"
        except Exception:
            pass
        result = OneCallResult(
            reply=reply,
            signals=signals,
            confidence=0.75,
            needs_handoff=False,
            is_valid=True,
            provider_name=_provider_name or "llamacpp",
            model_name=_model_name or "default",
            input_tokens=_prompt_tokens,
            output_tokens=_output_tokens,
            latency_ms=_gen_latency_ms,
            generation_kind=kind,
            call_index=1,
        )
    except Exception as exc:
        from commerce.signals import CommerceSignals as _CS

        return OneCallResult(
            reply="",
            signals=_CS.low_information(),
            confidence=0.0,
            needs_handoff=True,
            is_valid=False,
            validation_error=f"Greeting fast-path failed: {exc}",
            provider_name=_provider_name or "llamacpp",
            model_name=_model_name or "default",
            input_tokens=_prompt_tokens,
            output_tokens=_output_tokens,
            latency_ms=_gen_latency_ms,
            generation_kind=kind,
            call_index=1,
        )
    # Step 6 equivalent: deterministic scoring + echo + rails on plain reply.
    try:
        _fan_name = None
        try:
            _fan_name = user.get("first_name") if isinstance(user, dict) else None
        except Exception:
            _fan_name = None
        is_approved, quality_score, quality_flags, safety_flags = validate_draft_quality(
            result.reply, user_message, fan_name=_fan_name,
        )
        try:
            if detect_prompt_echo(result.reply) and "prompt_echo" not in quality_flags:
                quality_flags.append("prompt_echo")
                quality_score = min(quality_score, 0.29)
        except Exception:
            pass
        try:
            _ro: list[str] = []
            try:
                if recent_outbound:
                    _ro = [str(x) for x in list(recent_outbound)[-3:] if str(x).strip()]
                else:
                    _ro = [
                        str(x).split(":", 1)[1].strip()
                        for x in list(history_lines or [])
                        if str(x).strip().lower().startswith("sunny:")
                    ][-3:]
            except Exception:
                _ro = []
            _verdict, _r_flags, _r_cap = output_rails_check(result.reply, recent_outbound=_ro, embed=None)
            for _f in _r_flags or []:
                if _f not in quality_flags:
                    quality_flags.append(_f)
            if _r_cap is not None:
                quality_score = min(quality_score, _r_cap)
            if _verdict == "review":
                result.needs_handoff = True
        except Exception:
            pass
        result.quality_score = quality_score
        result.quality_flags = quality_flags
        result.safety_flags = safety_flags
        if not is_approved:
            result.needs_handoff = True
    except Exception:
        pass
    return result



async def one_call_generation(
    user_id: int,
    creator_id: int,
    user_message: str,
    persona: str,
    profile: dict[str, Any],
    user: dict[str, Any],
    commerce_text: str = "",
    summary: str | None = None,
    summary_age_days: int | None = None,
    conversation_state: Any | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    persona_name: str | None = None,
    identity_established: bool | None = None,
    relationship_state: str | None = None,
    recent_offer_count: int = 0,
    recent_purchase_count: int = 0,
    has_active_offer: bool = False,
    has_relevant_product: bool = True,
    retrieved_context: str = "",
    authoritative_state: Any | None = None,
    pipeline_result: Any | None = None,
    generation_id: str | None = None,
    commerce_desire: Any | None = None,
    commerce_window: Any | None = None,
    commerce_objective: Any | None = None,
) -> OneCallResult:
    """Execute one-call generation pipeline.

    Args:
        user_id: Fan user ID
        creator_id: Creator ID
        user_message: Fan's message
        persona: Persona instruction
        profile: User profile dict
        user: User dict
        commerce_text: Commerce context text
        summary: Conversation summary
        summary_age_days: Age of summary in days
        conversation_state: Derived conversation state
        recent_messages: Recent conversation messages
        persona_name: Creator persona name
        identity_established: Whether identity is established
        relationship_state: Current relationship state
        recent_offer_count: Number of recent offers
        recent_purchase_count: Number of recent purchases
        has_active_offer: Whether fan has an active offer
        has_relevant_product: Whether relevant product exists
        retrieved_context: Pre-retrieved context from Context Engine
        commerce_desire: Deterministic desire stage (Pass 7L enrichment;
            DesireStage value or DesireState; None = unknown)
        commerce_window: Deterministic sales window (Pass 7L; None = unknown)
        commerce_objective: Deterministic commercial objective (Pass 7L;
            None = unknown)

    Returns:
        OneCallResult with validated reply, signals, and routing info
    """
    # Pass 7L: commerce enrichment for the hints gate. Explicit kwargs win;
    # otherwise derive from the authoritative conversation_state (dict or
    # object, tolerating both "window"/"sales_window" and
    # "objective"/"commercial_objective" key spellings).
    if commerce_desire is None or commerce_window is None or commerce_objective is None:
        try:
            _cs_enrich: Any = None
            if authoritative_state is not None:
                _cs_enrich = getattr(authoritative_state, "conversation_state", None)
            if _cs_enrich is None:
                _cs_enrich = conversation_state
            if _cs_enrich is not None:
                def _pick(obj: Any, *names: str) -> Any:
                    for _n in names:
                        try:
                            if isinstance(obj, dict):
                                _v = obj.get(_n)
                            else:
                                _v = getattr(obj, _n, None)
                            if _v is not None:
                                return _v
                        except Exception:
                            continue
                    return None
                if commerce_desire is None:
                    commerce_desire = _pick(_cs_enrich, "desire", "desire_stage")
                if commerce_window is None:
                    commerce_window = _pick(_cs_enrich, "window", "sales_window")
                if commerce_objective is None:
                    commerce_objective = _pick(_cs_enrich, "objective", "commercial_objective")
        except Exception:
            pass
    # Phase 5.4: cold greetings take the reply-only fast-path (no JSON
    # schema). Anything else falls through to the canonical contract.
    try:
        if is_cold_greeting(user_message, user, recent_messages):
            return await _greeting_fastpath_generation(
                user_id=user_id,
                creator_id=creator_id,
                user_message=user_message,
                user=user if isinstance(user, dict) else None,
                generation_id=generation_id,
            )
    except Exception:
        pass
    # Phase 2: if authoritative snapshot + pipeline_result supplied, use snapshot path (single source)
    if authoritative_state is not None or pipeline_result is not None:
        messages = build_one_call_from_snapshot(
            snapshot=getattr(pipeline_result, "snapshot", None) if pipeline_result is not None else None,
            authoritative_state=authoritative_state,
            pipeline_result=pipeline_result,
        )
        # Pass 7 gated commerce hints: deterministic, no LLM, no DB.
        # Replaces overbroad any("commerce" in ...) substring guard.
        # Normal chat skips the builder entirely (saves wire); product/
        # price/purchase turns keep bounded hints.
        _required = commerce_context_required(
            user_message,
            relationship_state,
            has_active_offer,
            has_relevant_product,
            commerce_desire,
            commerce_window,
            commerce_objective,
        )
        if not _required:
            logger.debug("one_call: commerce hints suppressed (gate=False)")
        if _required:
            _ct = commerce_text or (getattr(authoritative_state, "commerce_context_text", "") if authoritative_state else "")
            _rel = relationship_state or ""
            commerce_hints = build_commerce_signal_hints(
                commerce_text=_ct,
                relationship_state=_rel,
                recent_offer_count=recent_offer_count,
                recent_purchase_count=recent_purchase_count,
                has_active_offer=has_active_offer,
                has_relevant_product=has_relevant_product,
            )
            if commerce_hints:
                messages.append({"role": "system", "content": f"[ADVISORY / DERIVED - COMMERCE HINTS]\n{commerce_hints}"})
            else:
                logger.debug("one_call: commerce hints empty despite gate=True")
        # Reuse validation / generation below (shared)
    else:
        # Step 1: Build compact context (legacy)
        messages = build_one_call_context(
            user=user,
            profile=profile,
            persona=persona,
            persona_name=persona_name,
            identity_established=identity_established,
            commerce_text=commerce_text,
            summary=summary,
            summary_age_days=summary_age_days,
            conversation_state=conversation_state,
            recent_messages=recent_messages,
            retrieved_context=retrieved_context,
        )

    # Step 2: Add commerce signal hints (snapshot path already handled above)
    # Pass 7: same deterministic gate on legacy path (was unguarded).
    if authoritative_state is None and pipeline_result is None:
        _legacy_required = commerce_context_required(
            user_message,
            relationship_state,
            has_active_offer,
            has_relevant_product,
            commerce_desire,
            commerce_window,
            commerce_objective,
        )
        if not _legacy_required:
            logger.debug("one_call: legacy commerce hints suppressed (gate=False)")
        else:
            _h2 = build_commerce_signal_hints(
                commerce_text=commerce_text,
                relationship_state=relationship_state,
                recent_offer_count=recent_offer_count,
                recent_purchase_count=recent_purchase_count,
                has_active_offer=has_active_offer,
                has_relevant_product=has_relevant_product,
            )
            if _h2:
                messages.append({"role": "system", "content": _h2})

    # Step 3: Validate context (fail-soft: shed oldest history on wire
    # overrun instead of failing the whole turn into an empty draft).
    is_valid, error = validate_one_call_context(messages)
    if not is_valid and "Wire prompt too large" in (error or ""):
        try:
            from core.context_compact import trim_wire_to_budget

            _before = len(messages)
            messages = trim_wire_to_budget(messages, user_message)
            _dropped = _before - len(messages)
            is_valid, error = validate_one_call_context(messages)
            if _dropped > 0:
                logger.info(
                    "one_call: wire overrun trimmed %d oldest turn(s), revalidation=%s",
                    _dropped,
                    "pass" if is_valid else f"still failing: {error}",
                )
        except Exception:
            logger.debug("one_call: wire trim failed (fail-open)", exc_info=True)
    if not is_valid:
        logger.warning("one_call: Context validation failed: %s", error)
        return OneCallResult(
            reply="",
            signals=__import__("commerce.signals", fromlist=["CommerceSignals"]).CommerceSignals.low_information(),
            confidence=0.0,
            needs_handoff=True,
            is_valid=False,
            validation_error=f"Context validation failed: {error}",
        )

    # Step 4: Generate one-call response (Phase 87: capture provider/model/tokens fail-open)
    _provider_name = ""
    _model_name = ""
    _prompt_tokens = None
    _output_tokens = None
    _gen_latency_ms = None
    try:
        provider = get_llm_provider()
        try:
            _provider_name = getattr(provider, "provider_name", "") or getattr(_settings, "llm_provider", "llamacpp")
            _model_name = getattr(provider, "_model", None) or getattr(_settings, "llama_model", "default")
        except Exception:
            _provider_name = getattr(_settings, "llm_provider", "llamacpp")
            _model_name = getattr(_settings, "llama_model", "default")
        import time as _t_one
        _gen_start = _t_one.monotonic()
        # Wire model comes from the active provider instance so request metadata
        # identifies the configured model. Falls back to the llama default
        # for mock providers in tests.
        _provider_model = getattr(provider, "_model", None)
        _wire_model = (
            _provider_model
            if isinstance(_provider_model, str) and _provider_model
            else getattr(_settings, "llama_model", "default")
        )
        raw_response = await provider.generate(
            system_instruction=ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS,
            user_content=json.dumps(messages),
            model=_wire_model,
            response_mime_type="application/json",
            # Explicit OneCall opt-in: only this canonical path requests the
            # OneCallReply json_schema constraint. All other application/json
            # consumers leave the flag False and keep generic JSON behavior.
            onecall_json_schema=True,
            # 600 fits reply + 18 commerce signals; 400 truncated valid JSON
            # mid-string on cold greetings (2026-09-25 Luna probe: 2/6 cut).
            max_output_tokens=600,
            temperature=0.7,
        )
        _gen_latency_ms = int((_t_one.monotonic() - _gen_start) * 1000)
        try:
            _prompt_tokens = getattr(provider, "last_prompt_tokens", None)
            _output_tokens = getattr(provider, "last_generation_tokens", None)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("one_call: Generation failed: %s", exc)
        # Phase 87 taxonomy
        err_str = str(exc).lower()
        if "timeout" in err_str or "timed out" in err_str:
            _validation_tax = "provider_timeout"
        elif "auth" in err_str or "401" in err_str or "403" in err_str:
            _validation_tax = "provider_exception"
        else:
            _validation_tax = "provider_exception"
        return OneCallResult(
            reply="",
            signals=__import__("commerce.signals", fromlist=["CommerceSignals"]).CommerceSignals.low_information(),
            confidence=0.0,
            needs_handoff=True,
            is_valid=False,
            validation_error=f"Generation failed: {exc}",
            provider_name=_provider_name or "llamacpp",
            model_name=_model_name or "default",
            input_tokens=_prompt_tokens,
            output_tokens=_output_tokens,
            latency_ms=_gen_latency_ms,
            generation_kind="one_call",
            call_index=1,
        )

    # Debug: log raw response for troubleshooting (remove after)
    try:
        if not raw_response or not raw_response.strip():
            logger.warning(f"one_call raw_response empty len={len(raw_response) if raw_response else 0}")
        else:
            logger.info(f"one_call raw_response len={len(raw_response)} preview={raw_response[:300]!r}")
    except Exception:
        pass
    # Step 5: Validate and score (Phase 87: capture taxonomy + Phase 89 grounding)
    _participants = None
    _contract = None
    try:
        if authoritative_state is not None:
            _participants = getattr(authoritative_state, "participants", None)
            _contract = getattr(authoritative_state, "conversation_contract", None)
        elif pipeline_result is not None and hasattr(pipeline_result, "snapshot") and getattr(pipeline_result, "snapshot", None) is not None:
            snap = getattr(pipeline_result, "snapshot", None)
            # pipeline_result.snapshot may have participants via authoritative_state originally, but snapshot itself doesn't carry them
            pass
    except Exception:
        pass
    result = validate_one_call_response(raw_response, participants=_participants, contract=_contract)

    # Attach provider identity/tokens to result (even if invalid, preserve for observability)
    try:
        result.provider_name = _provider_name or "llamacpp"
        result.model_name = _model_name or "default"
        result.input_tokens = _prompt_tokens
        result.output_tokens = _output_tokens
        result.latency_ms = _gen_latency_ms
        result.total_tokens = (int(_prompt_tokens or 0) + int(_output_tokens or 0)) if (_prompt_tokens or _output_tokens) else None
        result.generation_kind = "one_call"
        result.call_index = 1
    except Exception:
        pass

    # Determine validation outcome taxonomy (fail-open)
    try:
        if not result.is_valid:
            ve = (result.validation_error or "").lower()
            if "json parse" in ve or "malformed" in ve:
                result.validation_error = result.validation_error  # keep original
                # taxonomy for telemetry is derived in llm_worker, but keep hint
            pass
    except Exception:
        pass

    # Step 6: Quality validation
    # Preserve conversational flags before overwrite (Phase 89R hardened set)
    _conv_flags_preserve = list(getattr(result, "quality_flags", []) or [])
    _roleplay_flags_set = ("speaker_inversion", "character_as_player_inversion", "player_as_character_inversion", "unauthorized_player_speech", "player_agency_violation", "out_of_character", "unanswered_question", "topic_pivot", "generic_deflection", "speaker_prefix_leak", "prompt_echo", "markup_echo", "no_grounding")
    _conv_flags_only = [f for f in _conv_flags_preserve if f in _roleplay_flags_set]
    if result.is_valid and result.reply:
        _fan_name = None
        try:
            _fan_name = user.get("first_name") if isinstance(user, dict) else None
        except Exception:
            _fan_name = None
        is_approved, quality_score, quality_flags, safety_flags = validate_draft_quality(
            result.reply,
            user_message,
            fan_name=_fan_name,
        )
        # Merge conversational flags back (do not lose them)
        for cf in _conv_flags_only:
            if cf not in quality_flags:
                quality_flags.append(cf)
        # If conversational failure, slightly penalize but not handoff by itself
        if _conv_flags_only:
            if any(f in _conv_flags_only for f in ("speaker_inversion", "character_as_player_inversion", "player_as_character_inversion", "unauthorized_player_speech", "out_of_character", "speaker_prefix_leak")):
                quality_score = min(quality_score, 0.5)
            elif "unanswered_question" in _conv_flags_only or "topic_pivot" in _conv_flags_only:
                quality_score = min(quality_score, 0.6)
        # Phase 0.1 Echo preserve: re-run prompt-echo after overwrite.
        # validate_draft_quality never emits prompt_echo, so without this
        # the Layer 4b verdict from validate_one_call_response is erased
        # and echo scores ~0.875 clean. Fail-open.
        try:
            if detect_prompt_echo(result.reply):
                if "prompt_echo" not in quality_flags:
                    quality_flags.append("prompt_echo")
                quality_score = min(quality_score, 0.29)
                result.needs_handoff = True
                try:
                    if result.confidence is not None:
                        result.confidence = min(result.confidence, 0.3)
                except Exception:
                    pass
        except Exception:
            pass
        # Phase 1.2: output-rails choke point (fail-open, idempotent).
        # Runs on the final draft after all score/flag merges; unions rails
        # flags, applies min-cap, re-asserts handoff. No new I/O: last-3
        # outbound comes from the already-threaded recent_messages.
        try:
            _rails_prior: list[str] = []
            try:
                _rails_prior = [
                    m.get("content", "")
                    for m in list(recent_messages or [])
                    if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content")
                ][-3:]
            except Exception:
                _rails_prior = []
            _rails_verdict, _rails_flags, _rails_cap = output_rails_check(
                result.reply, recent_outbound=_rails_prior, embed=None
            )
            for _rf in _rails_flags:
                if _rf not in quality_flags:
                    quality_flags.append(_rf)
            if _rails_cap is not None:
                quality_score = min(quality_score, _rails_cap)
            if _rails_verdict == "review":
                result.needs_handoff = True
                try:
                    if result.confidence is not None:
                        result.confidence = min(result.confidence, 0.3)
                except Exception:
                    pass
        except Exception:
            pass
        result.quality_score = quality_score
        result.quality_flags = quality_flags
        result.safety_flags = safety_flags

        # Override handoff if not approved
        if not is_approved:
            result.needs_handoff = True

    # Phase 5.5: single plain-text retry (reply-only, no JSON schema).
    # JSON debris (truncation/early-EOS) or weak-but-clean drafts get one
    # second chance; safety verdicts never retry. Fail-open, best-score-wins.
    # Without this, truncation like '{"reply": "hello...}, " }' queues an
    # empty/fragment draft and the fan hears silence.
    try:
        _retry_eligible = False
        try:
            _rq_flags = list(getattr(result, "quality_flags", []) or [])
            _rq_safety = list(getattr(result, "safety_flags", []) or [])
            if not getattr(result, "is_valid", False):
                _ve = str(getattr(result, "validation_error", "") or "").lower()
                if ("json parse" in _ve or "malformed" in _ve or "schema validation" in _ve
                        or "empty" in _ve or "truncat" in _ve or "generation failed" in _ve):
                    _retry_eligible = True
            elif float(getattr(result, "quality_score", 0.0) or 0.0) < 0.80 and not _rq_safety:
                _retry_eligible = True
        except Exception:
            _retry_eligible = False
        if _retry_eligible and getattr(result, "generation_kind", "") not in ("plain_retry", "greeting_fastpath"):
            _hist_lines: list[str] = []
            try:
                _fn = ""
                try:
                    _fn = str((user or {}).get("first_name", "") or "").strip() or "Fan"
                except Exception:
                    _fn = "Fan"
                for _m in list(recent_messages or [])[-4:]:
                    if not isinstance(_m, dict):
                        continue
                    _c = str(_m.get("content", "") or "").strip()
                    if not _c:
                        continue
                    _who = "Sunny" if str(_m.get("role", "")) == "assistant" else _fn
                    _hist_lines.append(f"{_who}: {_c[:200]}")
            except Exception:
                _hist_lines = []
            _retry = await _greeting_fastpath_generation(
                user_id=user_id,
                creator_id=creator_id,
                user_message=user_message,
                user=user if isinstance(user, dict) else None,
                generation_id=generation_id,
                kind="plain_retry",
                history_lines=_hist_lines,
                max_output_tokens=150,
                recent_outbound=[
                    str(_m.get("content", ""))
                    for _m in list(recent_messages or [])[-3:]
                    if isinstance(_m, dict) and str(_m.get("role", "")) == "assistant"
                    and str(_m.get("content", "") or "").strip()
                ],
            )
            try:
                _r_ok = bool(getattr(_retry, "is_valid", False) and (_retry.reply or "").strip())
                _r_safe = list(getattr(_retry, "safety_flags", []) or [])
                _r_score = float(getattr(_retry, "quality_score", 0.0) or 0.0)
                _o_score = float(getattr(result, "quality_score", 0.0) or 0.0)
                if _r_ok and not _r_safe and _r_score > _o_score:
                    _retry.call_index = 2
                    result = _retry
            except Exception:
                pass
    except Exception:
        pass

    return result


async def one_call_pipeline_with_fallback(
    user_id: int,
    creator_id: int,
    user_message: str,
    persona: str,
    profile: dict[str, Any],
    user: dict[str, Any],
    **kwargs: Any,
) -> OneCallResult:
    """One-call pipeline with fallback to existing 3-LLM pipeline.

    This function provides a safe migration path by falling back
    to the existing pipeline if one-call fails.
    """
    try:
        result = await one_call_generation(
            user_id=user_id,
            creator_id=creator_id,
            user_message=user_message,
            persona=persona,
            profile=profile,
            user=user,
            **kwargs,
        )
        if result.is_valid:
            return result
        else:
            logger.info("one_call: One-call failed, falling back to 3-LLM pipeline")
            return await _fallback_3llm_pipeline(
                user_id=user_id,
                creator_id=creator_id,
                user_message=user_message,
                persona=persona,
                profile=profile,
                user=user,
                **kwargs,
            )
    except Exception as exc:
        logger.warning("one_call: Pipeline failed, falling back: %s", exc)
        return await _fallback_3llm_pipeline(
            user_id=user_id,
            creator_id=creator_id,
            user_message=user_message,
            persona=persona,
            profile=profile,
            user=user,
            **kwargs,
        )


async def _fallback_3llm_pipeline(
    user_id: int,
    creator_id: int,
    user_message: str,
    persona: str,
    profile: dict[str, Any],
    user: dict[str, Any],
    **kwargs: Any,
) -> OneCallResult:
    """Fallback to existing 3-LLM pipeline."""
    # Import existing pipeline
    from commerce.signals import CommerceSignals

    # For now, return low_information fallback
    # TODO: Integrate with existing llm_worker.py pipeline
    return OneCallResult(
        reply="",
        signals=CommerceSignals.low_information(),
        confidence=0.0,
        needs_handoff=True,
        is_valid=True,
        validation_error="Fallback to 3-LLM pipeline",
    )
