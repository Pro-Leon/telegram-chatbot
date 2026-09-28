"""Phase 75C: Deterministic context compaction for one-call pipeline.

Optimizes the context sent to Qwen2.5 by:
1. Combining system prompt + state + conversation into fewer messages
2. Removing redundant context elements
3. Optimizing token usage within num_ctx=8192 budget

This module provides a compact context specifically for the one-call
pipeline that replaces the 3-LLM pipeline.
"""

import logging
from typing import Any

from memory.context import (
    QWEN3_TOKEN_BUDGET,
    QWEN3_STAGE_GUIDANCE,
    _render_value,
    count_tokens,
    current_message_in_history,
    trim_to_token_budget,
)

logger = logging.getLogger("context_compact")

# One-call token budget — optimized for structured JSON output
# Component entries are SOFT diagnostics under Option A (Pass 8): only the
# final wire (ONE_CALL_MAX_PROMPT_TOKENS) and the n_ctx hard ceiling gate
# generation. Kept for observability, never invalid on their own.
ONE_CALL_TOKEN_BUDGET: dict[str, int] = {
    "system": 350,      # Compressed persona + rules (reduced from 400)
    "state": 150,       # Deterministic CRM state (reduced from 200)
    "conversation": 600, # Recent messages (reduced from 800)
    "signals_hint": 50,  # Commerce signal hints (new)
}

# Pass 8 Option A: final-wire budget against the ACTUAL provider payload:
# wire = count(ONE_CALL_SYSTEM_PROMPT + COMMERCE_SIGNAL_INSTRUCTIONS)
#      + count(json.dumps(messages)). Plan forensic 1533 EST / 1548 reported
# passes; future growth fails with a distinct wire error (not the 8192 code).
# Well below n_ctx 8192 (hard ceiling kept separately).
ONE_CALL_MAX_PROMPT_TOKENS = 2300

# Maximum context messages for one-call pipeline
ONE_CALL_MAX_MESSAGES = 8


def phase5_snapshot_blocks(authoritative_state: Any | None) -> list[dict[str, str]]:
    """Phase 5 snapshot-carried blocks for the production OneCall path.

    The worker attaches the deterministically selected relationship
    context text, the Phase 6 intimacy context text, the Phase 7
    boundary constraint text, the Phase 8 content-transition guidance
    text, the Phase 4 strategy block text, and the
    persona-behavior realization-guidance block text to the frozen
    ``AuthoritativeState`` (``relationship_context_text`` /
    ``intimacy_context_text`` / ``boundary_context_text`` /
    ``content_transition_context_text`` / ``strategy_block_text`` /
    ``behavior_block_text``) because the
    production path rebuilds OneCall messages from this snapshot and
    ignores the legacy ``context`` list (where Phase 4 previously
    appended strategy, leaving it dead in production). Empty strings
    render nothing, preserving byte-identical prompts when selection
    abstains. Ordering is relationship context → intimacy context →
    boundary context → content transition → conversation strategy →
    persona behavior,
    mirroring the legacy path where the strategy block is a trailing
    system block and persona behavior is subordinate realization
    guidance. The intimacy block is descriptive conversational state
    only (never permission, consent, safety policy, or commerce
    authority) and never precedes authoritative participants/contract
    grounding. The boundary block lists active user-established
    behavioral constraints only (never raw wording, never scores) with
    explicit precedence over persona style; it is guidance only, never
    the enforcement mechanism (enforcement is output validation plus
    routing/commerce veto). The content-transition block holds bounded
    categorical labels only (never raw wording, never scores, never a
    specific paid item, never an offer command); it is guidance only,
    never commerce authority, and renders nothing when the selector
    abstains (NONE). The persona-behavior block never precedes
    authoritative participants/contract grounding and never becomes
    commerce strategy.
    Fail-open: never raises.
    """
    blocks: list[dict[str, str]] = []
    try:
        if authoritative_state is None:
            return blocks
        rel = getattr(authoritative_state, "relationship_context_text", "") or ""
        if isinstance(rel, str) and rel.strip():
            blocks.append({"role": "system", "content": rel})
        intim = getattr(authoritative_state, "intimacy_context_text", "") or ""
        if isinstance(intim, str) and intim.strip():
            blocks.append({"role": "system", "content": intim})
        bound = getattr(authoritative_state, "boundary_context_text", "") or ""
        if isinstance(bound, str) and bound.strip():
            blocks.append({"role": "system", "content": bound})
        trans = getattr(authoritative_state, "content_transition_context_text", "") or ""
        if isinstance(trans, str) and trans.strip():
            blocks.append({"role": "system", "content": trans})
        strat = getattr(authoritative_state, "strategy_block_text", "") or ""
        if isinstance(strat, str) and strat.strip():
            blocks.append({"role": "system", "content": strat})
        beh = getattr(authoritative_state, "behavior_block_text", "") or ""
        if isinstance(beh, str) and beh.strip():
            blocks.append({"role": "system", "content": beh})
    except Exception:
        return blocks
    return blocks


def build_one_call_context(
    user: dict[str, Any],
    profile: dict[str, Any],
    persona: str,
    persona_name: str | None = None,
    identity_established: bool | None = None,
    commerce_text: str = "",
    summary: str | None = None,
    summary_age_days: int | None = None,
    conversation_state: Any | None = None,
    response_mode: str | None = None,
    question_allowed: bool | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    retrieved_context: str = "",
) -> list[dict[str, str]]:
    """Build compact context for one-call Qwen2.5 generation.

    Combines system prompt, state, and recent messages into a minimal
    context optimized for structured JSON output.

    Args:
        user: User dict with first_name, funnel_stage, etc.
        profile: User profile dict
        persona: Persona instruction text
        persona_name: Creator persona name (if known)
        identity_established: Whether identity is already established
        commerce_text: Commerce context text
        summary: Conversation summary
        summary_age_days: Age of summary in days
        conversation_state: Derived conversation state
        response_mode: Response mode (if known)
        question_allowed: Question budget (if known)
        recent_messages: Recent conversation messages
        retrieved_context: Pre-retrieved context from Context Engine (memory, knowledge, temporal)

    Returns:
        List of message dicts for Qwen2.5
    """
    messages: list[dict[str, str]] = []

    # Step 1: Compact system prompt
    system_content = _build_compact_system_prompt(
        persona=persona,
        user=user,
        profile=profile,
        persona_name=persona_name,
        identity_established=identity_established,
    )
    messages.append({"role": "system", "content": system_content})

    # Step 2: Compact state context
    state_context = _build_compact_state_context(
        user=user,
        profile=profile,
        commerce_text=commerce_text,
        summary=summary,
        summary_age_days=summary_age_days,
        conversation_state=conversation_state,
        response_mode=response_mode,
        question_allowed=question_allowed,
        persona_name=persona_name,
    )
    if state_context.strip():
        messages.append({"role": "system", "content": state_context})

    # Step 2b: Retrieved context from Context Engine (memory, knowledge, temporal)
    if retrieved_context and retrieved_context.strip():
        messages.append({"role": "system", "content": retrieved_context})

    # Step 3: Recent conversation (limited) — roleplay labels (Phase 89R)
    if recent_messages:
        trimmed = trim_to_token_budget(recent_messages, ONE_CALL_TOKEN_BUDGET["conversation"])
        trimmed = trimmed[-ONE_CALL_MAX_MESSAGES:]

        MAX_ASSISTANT_TURNS = 3
        assistant_count = 0
        for msg in reversed(trimmed):
            if msg.get("direction") != "inbound":
                assistant_count += 1
            if assistant_count > MAX_ASSISTANT_TURNS:
                trimmed = trimmed[1:]

        # Derive roleplay labels for history
        _spk_l = persona_name or "Sunny Skye"
        try:
            _lst_l = (user.get("first_name") or "Fan").strip() if isinstance(user, dict) else "Fan"
        except Exception:
            _lst_l = "Fan"
        for msg in trimmed:
            direction = msg.get("direction") or "user"
            raw = msg.get("content", "") or ""
            if direction == "inbound" or direction == "user":
                labeled = f"{_lst_l}: {raw}" if _lst_l else raw
                messages.append({"role": "user", "content": labeled})
            else:
                labeled = f"{_spk_l}: {raw}" if _spk_l else raw
                messages.append({"role": "assistant", "content": labeled})

    return messages


def _build_compact_system_prompt(
    persona: str,
    user: dict[str, Any],
    profile: dict[str, Any],
    persona_name: str | None = None,
    identity_established: bool | None = None,
) -> str:
    """Build compact system prompt for one-call generation.

    Combines persona, fan info, and rules into a minimal format.
    """
    first_name = user.get("first_name", "there")
    funnel_stage = user.get("funnel_stage", "new")
    stage_guidance = QWEN3_STAGE_GUIDANCE.get(funnel_stage, "")

    # Format profile compactly
    profile_parts: list[str] = []
    for key in ("age", "location", "occupation", "interests"):
        value = profile.get(key)
        if value:
            rendered = _render_value(value)
            if rendered:
                profile_parts.append(rendered)
    profile_text = ", ".join(profile_parts) if profile_parts else "No profile data yet."

    # Identity lifecycle
    persona_block = persona
    if identity_established and persona_block and persona_name:
        first = persona_name.split()[0].lower() if persona_name else ""
        if first:
            persona_block = persona_block.replace(f"You are {persona_name}", f"You are {first}")
            persona_block = persona_block.replace(f"{persona_name} here!", f"{first}")

    return f"""{persona_block}

Fan: {first_name} | {profile_text}
Stage: {stage_guidance}

Rules:
- 2-4 sentences, match energy, reference history naturally
- NEVER reveal AI/bot/automated — you are human
- Vary sentence structure, no filler words
- No generic affirmations without substance"""


def _build_compact_state_context(
    user: dict[str, Any],
    profile: dict[str, Any],
    commerce_text: str = "",
    summary: str | None = None,
    summary_age_days: int | None = None,
    conversation_state: Any | None = None,
    response_mode: str | None = None,
    question_allowed: bool | None = None,
    persona_name: str | None = None,
) -> str:
    """Build compact deterministic state context.

    Minimal format with only essential facts.
    """
    parts: list[str] = []

    first_name = user.get("first_name", "there")
    funnel_stage = user.get("funnel_stage", "new")

    # Compressed state header
    parts.append(f"STATE: {first_name} | {funnel_stage}")

    # Compressed relationship state
    relationship = user.get("relationship_state")
    if relationship:
        parts.append(f"RELATIONSHIP: {relationship}")

    # Compressed summary (first sentence only)
    if summary:
        sentences = summary.split(". ")
        compressed = sentences[0] if sentences else summary
        if not compressed.endswith("."):
            compressed += "."
        parts.append(f"SUMMARY: {compressed}")

    # Identity lifecycle
    if conversation_state is not None:
        ident = getattr(conversation_state, "identity_already_established", None)
        if ident is not None:
            parts.append(f"IDENTITY: established={str(bool(ident)).lower()}")
            if ident and persona_name:
                first = persona_name.split()[0].lower() if persona_name else "you"
                parts.append(f"RULE: Do NOT re-introduce as {persona_name}; you are already known as {first}.")

    # Response mode + question budget
    if response_mode:
        parts.append(f"RESPONSE: mode={response_mode}")
    if question_allowed is not None:
        parts.append(f"QUESTION: allowed={str(bool(question_allowed)).lower()}")

    return "\n".join(parts)


def build_one_call_from_snapshot(
    snapshot: Any = None,
    authoritative_state: Any = None,
    pipeline_result: Any = None,
    max_conversation_messages: int = ONE_CALL_MAX_MESSAGES,
) -> list[dict[str, str]]:
    """Build OneCall messages from authoritative snapshot + CE pipeline result.

    Phase 2 canonical: instead of independently fetching user/profile/commerce
    and rebuilding system/state via build_one_call_context, this consumes the
    single AuthoritativeState + ContextSnapshot already assembled by
    Authoritative Context Assembly.

    It uses the renderer's authority-labelled blocks (SYSTEM/STATE/COMMERCE etc)
    and conversation turns from the snapshot, ensuring:
      - ONE TURN = ONE SNAPSHOT reuse (no refetch)
      - authority markers preserved
      - budget already enforced by CE (TOTAL 2600) and OneCall check (8192)
      - fallback to minimal system+conversation if snapshot missing
    """
    messages: list[dict[str, str]] = []

    # Helper to label conversation turns with roleplay identities
    def _label_conv(conv_list: list[dict[str, str]], speaker_label: str | None, listener_label: str | None) -> list[dict[str, str]]:
        labeled: list[dict[str, str]] = []
        for m in conv_list:
            role = m.get("role", "user")
            content = m.get("content", "") or ""
            if role == "user":
                if listener_label and content.strip() and not content.lstrip().startswith(f"{listener_label}:"):
                    # avoid double-label if already prefixed
                    content = f"{listener_label}: {content}"
                labeled.append({"role": "user", "content": content})
            else:
                # assistant/model -> character
                if speaker_label and content.strip() and not content.lstrip().startswith(f"{speaker_label}:"):
                    content = f"{speaker_label}: {content}"
                labeled.append({"role": "assistant", "content": content})
        return labeled

    # Try to use pipeline_result's rendered messages if available
    if pipeline_result is not None:
        try:
            from context_engine.renderer import CompactRenderer
            # pipeline_result.messages already in Qwen format with labels
            msgs = getattr(pipeline_result, "messages", None)
            if msgs and isinstance(msgs, list) and len(msgs) > 0:
                # Validate count: limit conversation turns to max_conversation_messages
                # Keep all system blocks, trim only conversation tail
                system_msgs = [m for m in msgs if m.get("role") == "system"]
                conv_msgs = [m for m in msgs if m.get("role") != "system"]
                if len(conv_msgs) > max_conversation_messages:
                    conv_msgs = conv_msgs[-max_conversation_messages:]
                # Phase 89R: label history with roleplay identities (never rely on user/assistant alone)
                if authoritative_state is not None:
                    try:
                        from core.conversation_contract import render_participants_block, render_contract_block

                        participants = getattr(authoritative_state, "participants", None)
                        contract = getattr(authoritative_state, "conversation_contract", None)
                        # derive labels
                        _spk_l = None
                        _lst_l = None
                        try:
                            if participants is not None:
                                _spk_l = getattr(participants, "speaker_name", None) or getattr(participants, "character_name", None)
                                _lst_l = getattr(participants, "listener_name", None) or getattr(participants, "player_name", None)
                        except Exception:
                            pass
                        if not _spk_l:
                            try:
                                _spk_l = getattr(authoritative_state, "persona_name", None) or "Sunny Skye"
                            except Exception:
                                _spk_l = "Sunny Skye"
                        if not _lst_l:
                            try:
                                _u = getattr(authoritative_state, "user", {}) or {}
                                _lst_l = (_u.get("first_name") or "Fan").strip() if isinstance(_u, dict) else "Fan"
                            except Exception:
                                _lst_l = "Fan"
                        # label history
                        conv_msgs = _label_conv(conv_msgs, _spk_l, _lst_l)
                        # inject current fan message as authoritative [PLAYER MESSAGE] (single representation, not duplicated)
                        # M3 exactly-once: skip when the current turn is already visible anywhere
                        # in the conversation window (tail in the normal case, non-tail for a
                        # reprocessed/stale turn) — labels are stripped for the comparison.
                        try:
                            _cur = str(getattr(authoritative_state, "current_message", "") or "").strip()
                            if _cur:
                                if not current_message_in_history(conv_msgs, _cur, speaker_labels=(_lst_l,) if _lst_l else ()):
                                    conv_msgs.append({"role": "user", "content": f"[PLAYER MESSAGE]\n\n{_lst_l}:\n{_cur}"})
                        except Exception:
                            pass

                        if participants is not None:
                            system_msgs.append({"role": "system", "content": render_participants_block(participants)})
                        if contract is not None:
                            system_msgs.append({"role": "system", "content": render_contract_block(contract)})
                    except Exception:
                        pass
                # Phase 5/6/7/8: snapshot-carried relationship context +
                # intimacy context + boundary context + content-transition
                # guidance + strategy + persona
                # behavior. Appended after grounding blocks and
                # before conversation, mirroring the legacy path where the
                # strategy block is the trailing system block and persona
                # behavior is subordinate realization guidance. Advisory text
                # never precedes the authoritative participants/contract
                # blocks. No-op when all are empty (abstention keeps prompts
                # byte-identical).
                try:
                    _phase5_blocks = phase5_snapshot_blocks(authoritative_state)
                    if _phase5_blocks:
                        system_msgs = system_msgs + _phase5_blocks
                except Exception:
                    pass
                messages = system_msgs + conv_msgs
                return messages
            # Fallback to rendering from snapshot
            snapshot_obj = getattr(pipeline_result, "snapshot", snapshot)
            if snapshot_obj is not None:
                renderer = CompactRenderer()
                try:
                    # Use snapshot snapshot
                    rendered_msgs = renderer.render_to_messages(snapshot_obj)
                    # render_to_messages already labels STATE/MEMORY etc
                    # Trim conversation tail if needed
                    sys_msgs = [m for m in rendered_msgs if m.get("role") == "system"]
                    conv = [m for m in rendered_msgs if m.get("role") != "system"]
                    if len(conv) > max_conversation_messages:
                        conv = conv[-max_conversation_messages:]
                    # Phase 89R: inject grounding + roleplay labels + current message
                    if authoritative_state is not None:
                        try:
                            from core.conversation_contract import render_participants_block, render_contract_block

                            participants = getattr(authoritative_state, "participants", None)
                            contract = getattr(authoritative_state, "conversation_contract", None)
                            # labels
                            _spk_l = None
                            _lst_l = None
                            try:
                                if participants is not None:
                                    _spk_l = getattr(participants, "speaker_name", None) or getattr(participants, "character_name", None)
                                    _lst_l = getattr(participants, "listener_name", None) or getattr(participants, "player_name", None)
                            except Exception:
                                pass
                            if not _spk_l:
                                try:
                                    _spk_l = getattr(authoritative_state, "persona_name", None) or "Sunny Skye"
                                except Exception:
                                    _spk_l = "Sunny Skye"
                            if not _lst_l:
                                try:
                                    _u = getattr(authoritative_state, "user", {}) or {}
                                    _lst_l = (_u.get("first_name") or "Fan").strip() if isinstance(_u, dict) else "Fan"
                                except Exception:
                                    _lst_l = "Fan"
                            conv = _label_conv(conv, _spk_l, _lst_l)
                            try:
                                _cur = str(getattr(authoritative_state, "current_message", "") or "").strip()
                                if _cur:
                                    # M3 exactly-once (see above): in-window match suppresses injection.
                                    if not current_message_in_history(conv, _cur, speaker_labels=(_lst_l,) if _lst_l else ()):
                                        conv.append({"role": "user", "content": f"[PLAYER MESSAGE]\n\n{_lst_l}:\n{_cur}"})
                            except Exception:
                                pass
                            if participants is not None:
                                sys_msgs.append({"role": "system", "content": render_participants_block(participants)})
                            if contract is not None:
                                sys_msgs.append({"role": "system", "content": render_contract_block(contract)})
                        except Exception:
                            pass
                    # Phase 5/6/7/8: snapshot-carried relationship context +
                    # intimacy context + boundary context +
                    # content-transition guidance + strategy + persona
                    # behavior (same ordering as the
                    # pipeline-messages branch above).
                    try:
                        _phase5_blocks = phase5_snapshot_blocks(authoritative_state)
                        if _phase5_blocks:
                            sys_msgs = sys_msgs + _phase5_blocks
                    except Exception:
                        pass
                    messages = sys_msgs + conv
                    if messages:
                        return messages
                except Exception:
                    pass
        except Exception:
            pass

    # Fallback: build from authoritative_state alone (minimum safe context)
    if authoritative_state is not None:
        try:
            # Use authoritative_state's user/profile/persona to build minimal system
            user = getattr(authoritative_state, "user", {}) or {}
            profile = getattr(authoritative_state, "profile", {}) or {}
            persona = getattr(authoritative_state, "persona", "") or ""
            persona_name = getattr(authoritative_state, "persona_name", None)
            conversation_state = getattr(authoritative_state, "conversation_state", None)
            recent = getattr(authoritative_state, "recent_messages", None)
            # Minimal system via existing helper
            system_content = _build_compact_system_prompt(
                persona=persona,
                user=user,
                profile=profile,
                persona_name=persona_name,
                identity_established=getattr(conversation_state, "identity_already_established", None) if conversation_state else None,
            )
            messages.append({"role": "system", "content": f"[CURRENT AUTHORITATIVE STATE - SYSTEM]\n{system_content}"})
            # Minimal state
            commerce_text = getattr(authoritative_state, "commerce_context_text", "") or ""
            summary = getattr(authoritative_state, "summary", None)
            summary_age = getattr(authoritative_state, "summary_age_days", None)
            state_context = _build_compact_state_context(
                user=user,
                profile=profile,
                commerce_text=commerce_text,
                summary=summary,
                summary_age_days=summary_age,
                conversation_state=conversation_state,
                persona_name=persona_name,
            )
            if state_context.strip():
                messages.append({"role": "system", "content": f"[CURRENT AUTHORITATIVE STATE]\n{state_context}"})
            # Phase 89 grounding: participants + contract (authoritative, compact, ~80 tokens)
            try:
                from core.conversation_contract import render_participants_block, render_contract_block

                participants = getattr(authoritative_state, "participants", None)
                contract = getattr(authoritative_state, "conversation_contract", None)
                if participants is not None:
                    messages.append({"role": "system", "content": render_participants_block(participants)})
                if contract is not None:
                    messages.append({"role": "system", "content": render_contract_block(contract)})
            except Exception:
                pass
            # Phase 5/6/7/8: snapshot-carried relationship context + intimacy
            # context + boundary context + content-transition guidance +
            # strategy + persona behavior (same
            # ordering as the
            # branches above; no-op when empty).
            try:
                _phase5_blocks = phase5_snapshot_blocks(authoritative_state)
                if _phase5_blocks:
                    messages = messages + _phase5_blocks
            except Exception:
                pass
            # Conversation (bounded) — history grounding with explicit speaker labels
            # need labels for both history and current message
            speaker_label = None
            listener_label = None
            try:
                part = getattr(authoritative_state, "participants", None)
                if part is not None:
                    speaker_label = getattr(part, "speaker_name", None) or getattr(part, "character_name", None)
                    listener_label = getattr(part, "listener_name", None) or getattr(part, "player_name", None)
            except Exception:
                pass
            if not speaker_label:
                speaker_label = persona_name or "Sunny Skye"
            if not listener_label:
                try:
                    listener_label = (user.get("first_name") or "Fan").strip() if isinstance(user, dict) else "Fan"
                except Exception:
                    listener_label = "Fan"
            if recent:
                recent_list = list(recent) if isinstance(recent, (tuple, list)) else []
                trimmed = trim_to_token_budget(recent_list, ONE_CALL_TOKEN_BUDGET["conversation"])
                trimmed = trimmed[-ONE_CALL_MAX_MESSAGES:]
                for msg in trimmed:
                    direction = msg.get("direction") or msg.get("role") or ""
                    raw = msg.get("content", "") or ""
                    if direction == "inbound" or direction == "user":
                        labeled = f"{listener_label}: {raw}" if listener_label else raw
                        messages.append({"role": "user", "content": labeled})
                    else:
                        labeled = f"{speaker_label}: {raw}" if speaker_label else raw
                        messages.append({"role": "assistant", "content": labeled})
            # Inject current fan message as authoritative [PLAYER MESSAGE] (single representation)
            try:
                _cur = str(getattr(authoritative_state, "current_message", "") or "").strip()
                if _cur:
                    # M3 exactly-once (see above): skip when already visible in-window.
                    if not current_message_in_history(messages, _cur, speaker_labels=(listener_label,) if listener_label else ()):
                        messages.append({"role": "user", "content": f"[PLAYER MESSAGE]\n\n{listener_label}:\n{_cur}"})
            except Exception:
                pass
            if messages:
                return messages
        except Exception:
            pass

    # Ultimate fallback: empty but valid
    return [{"role": "system", "content": "[CURRENT AUTHORITATIVE STATE] Fallback minimal context"}]


def estimate_one_call_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate token count for one-call context."""
    return sum(count_tokens(m.get("content", "")) for m in messages)


def trim_wire_to_budget(
    messages: list[dict[str, str]],
    current_message: str = "",
    min_conv_turns: int = 2,
) -> list[dict[str, str]]:
    """Fail-soft wire-budget trim: drop oldest conversation turns until fit.

    A wire overrun previously failed the ENTIRE turn (empty draft queued,
    fan silence). This instead sheds the cheapest context first:

    * system blocks are NEVER dropped (persona, grounding, boundaries,
      strategy, behavior, hints — safety and realization guidance stay);
    * the turn carrying the current inbound message is NEVER dropped;
    * at least ``min_conv_turns`` conversation turns are kept when
      possible.

    Pure over plain data, bounded (at most len(messages) iterations),
    never raises — unusable input returns the input unchanged so the
    existing failure path still owns genuinely unfixable turns.
    """
    try:
        if not isinstance(messages, list) or not messages:
            return messages
        cur = current_message.strip() if isinstance(current_message, str) else ""
        system = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        conv = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
        if not conv:
            return messages

        def _carries_current(m: Any) -> bool:
            try:
                return bool(cur) and cur in str(m.get("content", ""))
            except Exception:
                return False

        for _ in range(len(conv)):
            ok, _ = validate_one_call_context(system + conv)
            if ok:
                break
            # Oldest droppable turn: outside the recent window and not
            # the current turn. Recent turns, the current turn, and all
            # system blocks are protected.
            keep_from = max(0, len(conv) - max(0, min_conv_turns))
            victim = None
            for index in range(len(conv)):
                if index >= keep_from:
                    continue
                if _carries_current(conv[index]):
                    continue
                victim = index
                break
            if victim is None:
                break
            del conv[victim]
        return system + conv
    except Exception:
        logger.debug("trim_wire_to_budget failed (fail-open unchanged)", exc_info=True)
        return messages


def validate_one_call_context(messages: list[dict[str, str]]) -> tuple[bool, str]:
    """Validate one-call context fits within token budget.

    Option A (Pass 8): the FINAL WIRE gates generation
    (``ONE_CALL_MAX_PROMPT_TOKENS``); the n_ctx 8192 ceiling is kept as a
    distinct hard check. Component budgets (system/state/conv/signals) are
    soft diagnostics only — overruns log a warning and still pass, so the
    previously invisible participants+contract tokens can no longer hide
    behind the ``Fan:`` filter (now honest all-system count, warning-only).

    Returns:
        Tuple of (is_valid, error_message)
    """
    total_tokens = estimate_one_call_tokens(messages)

    if total_tokens > 8192:  # num_ctx hard ceiling (distinct code)
        return False, f"Context too large: {total_tokens} tokens (max 8192)"

    # Soft diagnostics: honest all-system count (no Fan: filter, so
    # participants+contract are included). Warning-only, never invalid.
    try:
        system_all = sum(
            count_tokens(m.get("content", ""))
            for m in messages
            if m.get("role") == "system"
        )
        if system_all > ONE_CALL_TOKEN_BUDGET["system"]:
            logger.warning(
                "one_call: system_all %s tokens over soft budget %s (diagnostic only)",
                system_all,
                ONE_CALL_TOKEN_BUDGET["system"],
            )
    except Exception:
        pass

    # Option A wire check: fixed provider system + serialized messages.
    # Same gpt-4 estimator as everywhere (accepted ~1% drift vs reported).
    # Lazy import avoids a core.one_call <-> core.context_compact cycle.
    try:
        import json as _json

        try:
            from core.one_call import ONE_CALL_SYSTEM_PROMPT as _SYS
            from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS as _CI

            _fixed = count_tokens(_SYS + "\n\n" + _CI)
        except Exception:
            _fixed = 831  # measured fallback (556 + 276)
        wire = _fixed + count_tokens(_json.dumps(messages))
        if wire > ONE_CALL_MAX_PROMPT_TOKENS:
            return False, f"Wire prompt too large: {wire} tokens (max wire {ONE_CALL_MAX_PROMPT_TOKENS})"
    except Exception:
        pass

    return True, ""
