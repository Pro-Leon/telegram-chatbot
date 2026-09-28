"""Response-mode planner (C.1-F Phase 3).

Planner decides WHAT kind of conversational move; Qwen decides HOW to phrase.

Modes: REACT, ANSWER, SHARE, EXPLORE, TEASE, CALLBACK, CLARIFY, CLOSE
Deterministic, no LLM, bounded.
"""

from __future__ import annotations

import re


class ResponseMode(str):
    REACT = "react"       # acknowledge / empathize
    ANSWER = "answer"     # direct answer to fan question
    SHARE = "share"        # persona self-fact
    EXPLORE = "explore"    # ask new relevant question
    TEASE = "tease"        # flirty/playful escalation
    CALLBACK = "callback"  # reference earlier thread/fact
    CLARIFY = "clarify"    # capability boundary / confusion
    CLOSE = "close"        # soft close, no question


# Word-boundaried sexual/media vocabulary ("send" deliberately absent:
# resend/link requests must never escalate; "pic" must not fire inside
# "topic").
_SEXUAL_MEDIA_RE = re.compile(r"\b(horny|naughty|sexy|pic|picture|nude|photo|selfie)\b")


def _is_question(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return t.endswith("?") or t.lower().startswith(("what ","how ","when ","where ","who ","why ","can you ","are you ","have you ","do you "))


def plan_response_mode(
    fan_message: str,
    conversation_state,  # ConversationState
    *,
    has_unanswered_question: bool = False,
    can_share_persona: bool = True,
    capability_needs_clarify: bool = False,
) -> str:
    """Deterministic planner.

    Inputs are pre-derived. Returns one ResponseMode value.
    Rule order is intentional — earlier rules dominate.
    """
    fan = fan_message.strip()
    low = fan.lower()

    if capability_needs_clarify:
        return ResponseMode.CLARIFY

    # fan asked about sunny herself
    if any(p in low for p in ["what are you", "what do you do", "what are you upto", "what are you up to", "how about you", "and you?"]):
        # limit share to not every turn: prefer callback if we have threads
        if conversation_state.open_threads:
            return ResponseMode.CALLBACK
        if can_share_persona:
            return ResponseMode.SHARE
        return ResponseMode.REACT

    if _is_question(fan):
        # fan asked us something — answer it
        # Word-boundaried: bare substrings misfire ("pic" in "topic",
        # "send" in any resend request). "send" is dropped entirely: a
        # verb, never intrinsically sexual — "send that link again"
        # must not escalate to TEASE.
        if _SEXUAL_MEDIA_RE.search(low):
            # sexual/media pressure — don't tease into media false promise
            return ResponseMode.CLARIFY if capability_needs_clarify else ResponseMode.TEASE
        return ResponseMode.ANSWER

    # short prompt that invites expansion (e.g. "Nothing much, work majorly")
    if len(fan.split()) <= 6 and not _is_question(fan):
        # they volunteered a fact — explore it or callback
        if conversation_state.last_question and not conversation_state.last_question_answered:
            # don't ask again, answer/react instead
            return ResponseMode.REACT
        return ResponseMode.EXPLORE

    # has something we can callback — requires open threads and matching topic
    if conversation_state.open_threads and any(k in low for k in ("saturday", "netflix", "popcorn")):
        return ResponseMode.CALLBACK

    # default gentle exploration — but not mandatory question mode
    if conversation_state.tone == "flirty":
        return ResponseMode.TEASE
    return ResponseMode.REACT
