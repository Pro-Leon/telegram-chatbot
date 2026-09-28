"""Deterministic Persona Behavioral State — Phase 43D.

Generic, creator-scoped, bounded, no LLM, no DB, no network.
Derives per-turn behavioral constraints from already-fetched:
  - structured persona (23-field JSONB, creator-scoped)
  - conversation_state (lifecycle, tone, open_threads, last_question)
  - fan_knowledge relevant 5 + fan message text
  - recent assistant turns (3)
  - commerce objective (optional)

Same inputs → same state (deterministic, hash-stable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Compiled patterns — bounded, no catastrophic backtracking
# ---------------------------------------------------------------------------
_EXCITED_RE = re.compile(
    r"(!{2,}|finally|got the job|yay\b|amazing\b|excited\b|\bwon\b|\bwin\b)", re.I
)
_PLAYFUL_RE = re.compile(
    r"(ridiculous lol|you are ridiculous|haha|lol|you'?re funny|teas\w*|playful)", re.I
)
_SERIOUS_RE = re.compile(
    r"(messed|overwhelmed|stressed|depressed|family.*problem|relationship.*concern|important|vulnerable|anxious|worried|not doing enough|everything up)",
    re.I,
)
_EMBARRASSED_RE = re.compile(r"(sorry|embarrassed|awkward|self-conscious|cringe)", re.I)
_ANNOYED_RE = re.compile(
    r"(ignoring me|annoying|frustrating|angry|upset with you|why are you ignoring)", re.I
)
_NERVOUS_RE = re.compile(
    r"(nervous|unsure|difficult social|\buncertain\b|not sure what to do)", re.I
)
_CURIOUS_RE = re.compile(r"\?\s*$")
_OPINION_RE = re.compile(
    r"(better than|obviously|is overrated|is the best|should be|i think (?:brooklyn|manhattan|nyc|pineapple))",
    re.I,
)
_SUBJECTIVE_CLAIM_RE = re.compile(
    r"(brooklyn|manhattan|nyc|pineapple on pizza).*(better|overrated|best|worst)", re.I
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PersonaBehaviorState:
    emotional_state: str  # excited | playful | warm | curious | embarrassed | annoyed | serious | nervous | neutral
    confidence: str  # HIGH | MEDIUM | LOW
    conversation_mode: str  # react | explore | share | tease | callback | clarify | answer
    question_allowed: bool
    question_policy: str  # NO_QUESTION | ONE_NATURAL_QUESTION | OPTIONAL_QUESTION
    disagreement_available: bool
    teasing_allowed: bool
    sincerity_required: bool
    verbosity_target: str  # short | short_medium | medium
    emoji_policy: str  # none | occasional | allow_one
    lowercase_policy: str  # neutral | allow_lowercase
    naturalness_mode: str  # normal | avoid_generic_ack
    persona_version: int | None
    creator_id: int | None
    generation_id: str | None
    tease_reason: str | None = (
        None  # commercial | social | None (Phase 3 provenance, additive only)
    )


def _get_persona_value(persona: dict[str, Any] | None, path: str, default: Any = None) -> Any:
    if not persona:
        return default
    cur: Any = persona
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _derive_emotional_state(
    fan_message: str,
    conversation_state: Any | None,
    structured: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return (emotional_state, confidence). Deterministic, no LLM."""
    if not fan_message:
        return "warm", "LOW"
    low = fan_message.lower()
    # Priority hierarchy: serious > annoyed > embarrassed > excited > playful > curious > nervous > warm
    # Serious sensitive topics override playful
    if _SERIOUS_RE.search(fan_message):
        # Also check tone supportive
        try:
            tone = getattr(conversation_state, "tone", None) if conversation_state else None
            if tone == "supportive":
                return "serious", "HIGH"
        except Exception:
            pass
        return "serious", "HIGH"
    if _ANNOYED_RE.search(fan_message):
        return "annoyed", "HIGH"
    if _EMBARRASSED_RE.search(fan_message):
        return "embarrassed", "MEDIUM"
    if _EXCITED_RE.search(fan_message):
        # require at least 2 signals for HIGH, else MEDIUM
        # count exclamation or strong words
        count = 0
        if "!!!" in fan_message or "!!" in fan_message:
            count += 1
        if re.search(r"\b(finally|amazing|excited|got the job)\b", low):
            count += 1
        if count >= 2:
            return "excited", "HIGH"
        return "excited", "MEDIUM"
    if _PLAYFUL_RE.search(fan_message):
        try:
            tone = getattr(conversation_state, "tone", None) if conversation_state else None
            if tone == "flirty":
                return "playful", "HIGH"
        except Exception:
            pass
        return "playful", "MEDIUM"
    if _NERVOUS_RE.search(fan_message):
        return "nervous", "MEDIUM"
    # Curious if fan asks question and tone curious
    try:
        tone = getattr(conversation_state, "tone", None) if conversation_state else None
        if tone == "curious" or _CURIOUS_RE.search(fan_message.strip()):
            # Only high if open_threads or interesting topic; else medium
            if conversation_state and getattr(conversation_state, "current_topic", None):
                return "curious", "MEDIUM"
            return "curious", "LOW"
    except Exception:
        pass
    # Default warm
    return "warm", "LOW"


def derive_persona_behavior_state(
    *,
    structured_persona: dict[str, Any] | None,
    conversation_state: Any | None,
    fan_message: str,
    fan_knowledge: list[dict[str, Any]] | None = None,
    recent_assistant_messages: list[dict[str, Any]] | None = None,
    commerce_objective: str | None = None,
    next_best_action: str | None = None,
    creator_id: int | None = None,
    generation_id: str | None = None,
) -> PersonaBehaviorState:
    """Derive deterministic behavioral state. Generic over persona.

    Never raises — returns neutral fallback on exception.
    O(recent 3 + fan_message len).
    """
    try:
        structured = structured_persona or {}
        # Emotional
        emotional_state, confidence = _derive_emotional_state(
            fan_message, conversation_state, structured
        )

        # Persona version
        pv = structured.get("persona_version")
        if pv is None:
            pv = structured.get("_db_version")
        try:
            pv_int = int(pv) if pv is not None else None
        except Exception:
            pv_int = None

        # Conversation mode — reuse commerce next_best_action if present, else map from emotional + topic
        # Precedence: commerce next_best_action > emotional
        conv_mode = "react"
        if next_best_action:
            nba = str(next_best_action).lower()
            if nba in ("follow_up_open_loop", "re_engage"):
                conv_mode = "callback"
            elif nba in ("explore_interest", "qualify"):
                conv_mode = "explore"
            elif nba in ("deepen_desire",):
                conv_mode = "tease"
            elif nba in ("present_offer",):
                conv_mode = "tease"
            elif nba in ("handle_objection", "aftercare"):
                conv_mode = "react"
            else:
                conv_mode = "react"
        else:
            # Fallback from emotional
            if emotional_state == "curious":
                conv_mode = "explore"
            elif emotional_state == "playful":
                conv_mode = "tease"
            elif emotional_state == "excited":
                conv_mode = "share"
            elif emotional_state in ("serious", "nervous"):
                conv_mode = "react"

        # Phase 3 TEASE provenance (additive metadata only, token unchanged).
        # Derived from already-in-scope inputs; default no-suffix when
        # indeterminate so all existing callers/tests stay identical.
        _tease_reason: str | None = None
        try:
            if conv_mode == "tease":
                if next_best_action:
                    try:
                        _nba_l = str(next_best_action).lower().strip()
                    except Exception:
                        _nba_l = ""
                    if _nba_l in ("deepen_desire", "present_offer"):
                        _tease_reason = "commercial"
                    else:
                        _tease_reason = None
                else:
                    if emotional_state == "playful":
                        _tease_reason = "social"
                    else:
                        _tease_reason = None
        except Exception:
            _tease_reason = None

        # Question allowed — respect existing budget
        # Use core/question_policy if available, else simple
        question_allowed = False
        question_policy = "NO_QUESTION"
        try:
            from core.question_policy import evaluate_question_budget

            # derive needed fields from conversation_state
            last_q = (
                getattr(conversation_state, "last_question", None) if conversation_state else None
            )
            answered = (
                bool(getattr(conversation_state, "last_question_answered", True))
                if conversation_state
                else True
            )
            consecutive = int(getattr(conversation_state, "consecutive_questions", 0) or 0)
            q_in_3 = (
                getattr(conversation_state, "questions_in_last_3", None)
                if conversation_state
                else None
            )
            # Map conv_mode to proposed_mode for budget
            proposed = (
                "explore"
                if conv_mode in ("explore",)
                else "clarify"
                if conv_mode == "clarify"
                else "react"
            )
            # But for excited/playful, optional question may be allowed
            if emotional_state in ("excited", "playful") and proposed == "react":
                proposed = "explore"  # allow optional
            qb = evaluate_question_budget(
                last_q, answered, consecutive, proposed, questions_in_last_3=q_in_3
            )
            question_allowed = bool(qb.allowed)
            if question_allowed:
                question_policy = "ONE_NATURAL_QUESTION"
            else:
                # For excited/playful with optional, allow OPTIONAL
                if (
                    emotional_state in ("excited", "playful")
                    and not qb.allowed
                    and "limit" not in qb.reason
                ):
                    # keep NO_QUESTION for now; validator will allow optional
                    pass
                question_policy = "NO_QUESTION"
        except Exception:
            # Fallback simple: allow if no recent question
            try:
                consecutive = (
                    int(getattr(conversation_state, "consecutive_questions", 0) or 0)
                    if conversation_state
                    else 0
                )
                question_allowed = consecutive == 0
                question_policy = "ONE_NATURAL_QUESTION" if question_allowed else "NO_QUESTION"
            except Exception:
                question_allowed = False
                question_policy = "NO_QUESTION"

        # Emotional overrides for question
        if emotional_state == "serious":
            # serious → only if genuinely useful, default false
            question_allowed = False
            question_policy = "NO_QUESTION"
        elif emotional_state == "annoyed":
            question_allowed = False
            question_policy = "NO_QUESTION"

        # Disagreement available — generic, not Sunny-hardcoded
        can_disagree = False
        try:
            can_disagree = bool(
                _get_persona_value(structured, "behavioral_rules.can_disagree", False)
                or _get_persona_value(
                    structured, "behavioral_rules.disagreement.can_disagree", False
                )
            )
        except Exception:
            can_disagree = False
        disagreement_available = False
        if can_disagree:
            # Detect subjective claim/opinion
            if _OPINION_RE.search(fan_message) or _SUBJECTIVE_CLAIM_RE.search(fan_message):
                # Not if serious/sensitive
                if emotional_state not in ("serious", "nervous", "embarrassed"):
                    disagreement_available = True
            # Also if fan says you're wrong
            if re.search(r"\byou'?re wrong\b", fan_message, re.I):
                disagreement_available = True
                if emotional_state == "serious":
                    # Still allow playful disagreement unless serious
                    # For now, block playful disagreement when serious
                    disagreement_available = False

        # Teasing allowed
        teasing_allowed = False
        try:
            # If persona has playful trait and not serious/annoyed that requires sincerity
            # Check behavioral_rules not overly restrictive
            # For generic, teasing allowed when playful or warm and not serious
            if emotional_state in (
                "playful",
                "warm",
                "excited",
                "curious",
            ) and emotional_state not in ("serious", "annoyed"):
                # Also check if persona is playful (has teasing trait)
                # For generic, assume true unless boundaries say not constantly teasing
                # We interpret: teasing allowed when not sincerity_required
                teasing_allowed = True
            if emotional_state in ("serious", "nervous"):
                teasing_allowed = False
            if emotional_state == "annoyed":
                # annoyed → mild sarcasm not teasing
                teasing_allowed = False
        except Exception:
            teasing_allowed = False

        # Sincerity required
        sincerity_required = False
        if emotional_state in ("serious", "nervous"):
            sincerity_required = True
        # Also if fan disclosed vulnerable personal matter (serious RE match)
        if _SERIOUS_RE.search(fan_message):
            sincerity_required = True

        # Verbosity target from persona
        verbosity_target = "short_medium"
        try:
            vl = _get_persona_value(
                structured, "communication.message_length", None
            ) or _get_persona_value(structured, "behavioral_rules.message_length.casual", None)
            if vl:
                vl_low = str(vl).lower()
                if "short" in vl_low and "medium" in vl_low:
                    verbosity_target = "short_medium"
                elif "short" in vl_low:
                    verbosity_target = "short"
                elif "medium" in vl_low:
                    verbosity_target = "medium"
        except Exception:
            pass

        # Emoji policy generic
        emoji_policy = "occasional"
        try:
            ef = (
                _get_persona_value(structured, "communication.emoji_style", None)
                or _get_persona_value(structured, "behavioral_rules.emojis.frequency", None)
                or _get_persona_value(structured, "communication.emoji", None)
            )
            if ef:
                ef_low = str(ef).lower()
                if "occasional" in ef_low:
                    emoji_policy = "occasional"
                elif "none" in ef_low or "never" in ef_low:
                    emoji_policy = "none"
                elif "frequent" in ef_low or "always" in ef_low:
                    emoji_policy = "allow_one"
                else:
                    emoji_policy = "occasional"
            else:
                emoji_policy = "occasional"
        except Exception:
            emoji_policy = "occasional"

        # Lowercase policy
        lowercase_policy = "neutral"
        try:
            casing = _get_persona_value(
                structured, "communication.casing", ""
            ) or _get_persona_value(structured, "communication.casing", "")
            if casing and "lowercase" in str(casing).lower():
                lowercase_policy = "allow_lowercase"
            else:
                # Also check if persona has lowercase in representative patterns
                # Generic fallback: if persona has lowercase in communication, allow
                lowercase_policy = "neutral"
        except Exception:
            lowercase_policy = "neutral"

        # Naturalness mode
        naturalness_mode = "normal"
        try:
            # If recent assistant had generic ack, flag avoid
            if recent_assistant_messages:
                for m in recent_assistant_messages[-3:]:
                    content = (m.get("content") or "").lower()
                    if any(
                        p in content
                        for p in ["that sounds amazing", "i totally get that", "that must be"]
                    ):
                        naturalness_mode = "avoid_generic_ack"
                        break
        except Exception:
            pass

        return PersonaBehaviorState(
            emotional_state=emotional_state,
            confidence=confidence,
            conversation_mode=conv_mode,
            question_allowed=question_allowed,
            question_policy=question_policy,
            disagreement_available=disagreement_available,
            teasing_allowed=teasing_allowed,
            sincerity_required=sincerity_required,
            verbosity_target=verbosity_target,
            emoji_policy=emoji_policy,
            lowercase_policy=lowercase_policy,
            naturalness_mode=naturalness_mode,
            persona_version=pv_int,
            creator_id=creator_id,
            generation_id=str(generation_id) if generation_id else None,
            tease_reason=_tease_reason,
        )
    except Exception:
        # Fail-safe neutral
        return PersonaBehaviorState(
            emotional_state="warm",
            confidence="LOW",
            conversation_mode="react",
            question_allowed=False,
            question_policy="NO_QUESTION",
            disagreement_available=False,
            teasing_allowed=False,
            sincerity_required=False,
            verbosity_target="short_medium",
            emoji_policy="occasional",
            lowercase_policy="neutral",
            naturalness_mode="normal",
            persona_version=None,
            creator_id=creator_id,
            generation_id=str(generation_id) if generation_id else None,
        )


def render_persona_behavior_block(state: PersonaBehaviorState) -> str:
    """Render concise behavioral instruction for Qwen (~60 tokens, 3-5 lines)."""
    lines: list[str] = []
    # Line 1: emotion + confidence + mode (+ additive TEASE reason; token unchanged)
    try:
        _reason = getattr(state, "tease_reason", None)
    except Exception:
        _reason = None
    _suffix = ""
    try:
        if getattr(state, "conversation_mode", None) == "tease" and _reason in (
            "commercial",
            "social",
        ):
            _suffix = f", reason={_reason}"
    except Exception:
        _suffix = ""
    lines.append(
        f"PERSONA BEHAVIOR: emotion={state.emotional_state} confidence={state.confidence} mode={state.conversation_mode}{_suffix}"
    )
    # Line 2: voice / verbosity / question
    voice_parts: list[str] = []
    if state.lowercase_policy == "allow_lowercase":
        voice_parts.append("lowercase allowed (not required)")
    else:
        voice_parts.append("standard casing")
    if state.emoji_policy == "occasional":
        voice_parts.append("occasional emoji max 1 (zero ok)")
    elif state.emoji_policy == "none":
        voice_parts.append("no emoji")
    else:
        voice_parts.append("emoji allowed max 1")
    voice_parts.append(f"length={state.verbosity_target}")
    if state.question_allowed:
        voice_parts.append(f"question={state.question_policy.lower()}")
    else:
        voice_parts.append("question=none")
    lines.append("Voice: " + "; ".join(voice_parts))
    # Line 3: behavioral rules
    behavior_parts: list[str] = []
    if state.sincerity_required:
        behavior_parts.append("sincerity required — drop slang/joke, be direct")
    elif state.teasing_allowed:
        behavior_parts.append("light teasing allowed")
    else:
        behavior_parts.append("teasing off")
    if state.disagreement_available:
        behavior_parts.append("disagreement available (playful if fits, do not auto-agree)")
    else:
        behavior_parts.append("do not force disagreement")
    if state.naturalness_mode == "avoid_generic_ack":
        behavior_parts.append("avoid generic ack templates")
    # Emotional-specific
    if state.emotional_state == "excited":
        behavior_parts.append("expressive, energetic, exclamation ok")
    elif state.emotional_state == "annoyed":
        behavior_parts.append("shorter, mild sarcasm ok, not hostile")
    elif state.emotional_state == "serious":
        behavior_parts.append("supportive, less slang")
    elif state.emotional_state == "nervous":
        behavior_parts.append("gentle, reassuring")
    elif state.emotional_state == "embarrassed":
        behavior_parts.append("self-deprecating humor light")
    lines.append("Behavior: " + "; ".join(behavior_parts))
    return "\n".join(lines)
