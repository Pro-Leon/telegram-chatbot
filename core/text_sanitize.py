"""Stored-text sanitization for prompt-bound memory text.

Profile facts, long-term memories, and fan-knowledge values are rendered
back into generation prompts verbatim. Without content checks, fan text
laundered through extraction (or model-misread junk) persists as trusted
system context — including instruction-like text ("ignore previous
instructions …") that the model then obeys.

This module is a pure leaf (``re`` only, no intra-repo imports) so every
writer and renderer can use it without import cycles:

- :func:`sanitize_stored_text` — write-time: normalize, bound length,
  drop instruction-like values entirely (returns ``""``).
- :func:`contains_instruction` / :func:`contains_markup` — read helpers.
- :func:`is_render_safe` — render-time gate for subject/value pairs.

Fail-open direction is always *drop the line*, never repair it. Never
raises on any input.
"""

from __future__ import annotations

import re
from typing import Any

#: Bound per stored value (interests/names/cities never legitimately
#: exceed this; greedy regex captures get cut here instead of persisting
#: whole smuggled sentences).
MAX_STORED_CHARS = 120

#: Instruction-like constructions that must never persist as memory or
#: render into a prompt. Word-boundaried; matched case-insensitively.
_INSTRUCTION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bignore\s+(all\s+)?((previous|prior|above|your)\s+)?instructions?\b", re.IGNORECASE
    ),
    re.compile(
        r"\bdisregard\s+(all\s+)?((previous|prior|above|your)\s+)?(instructions?|rules?|prompt)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bforget\s+(all|everything|your)\s+(instructions?|rules?|prompt|training)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdo\s+not\s+(follow|obey|listen to)\b.{0,30}\b(instructions?|rules?|system)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bnew\s+persona\b", re.IGNORECASE),
    re.compile(r"\breveal\s+your\s+(system\s+)?prompt\b", re.IGNORECASE),
    re.compile(r"\bprint\s+your\s+instructions\b", re.IGNORECASE),
    re.compile(
        r"\b(show|tell)\s+me\s+your\s+(system\s+)?(instructions?|prompt|rules)\b", re.IGNORECASE
    ),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\b(system|developer)\s+override\b", re.IGNORECASE),
    re.compile(r"\bforget\s+you\s+are\b", re.IGNORECASE),
)

#: Internal prompt scaffolding that must never render as memory content
#: or echo back in a draft.
_MARKUP_RE = re.compile(
    r"\[(PLAYER MESSAGE|RETRIEVED|CURRENT AUTHORITATIVE|HISTORICAL|ADVISORY|"
    r"INTIMACY CONTEXT|RELATIONSHIP CONTEXT|BOUNDARY|CONTENT TRANSITION|"
    r"CONVERSATION STRATEGY|PERSONA BEHAVIOR|COMMERCE|STATE|MEMORY|TEMPORAL|"
    r"CONTENT|SUMMARY|IDENTITY|CONVERSATION|RESPONSE|QUESTION|AVAILABLE CONTENT|"
    r"FAN KNOWLEDGE|LOCAL TIME|CREATOR PERSONA|CREATOR LOCAL TIME)[^\]]*\]",
    re.IGNORECASE,
)

_CARD_RE = re.compile(r"\b\d{13,16}\b")


def _normalize(text: Any) -> str:
    try:
        if not isinstance(text, str):
            return ""
        # Collapse all whitespace (incl. newlines) — no multi-line
        # smuggling of prompt-shaped blocks through stored values.
        return " ".join(text.split())
    except Exception:
        return ""


def contains_instruction(text: Any) -> bool:
    """True when text carries instruction-like constructions."""
    try:
        normalized = _normalize(text)
        if not normalized:
            return False
        return any(pat.search(normalized) for pat in _INSTRUCTION_RES)
    except Exception:
        return False


def contains_markup(text: Any) -> bool:
    """True when text carries internal prompt scaffolding."""
    try:
        normalized = _normalize(text)
        if not normalized:
            return False
        return bool(_MARKUP_RE.search(normalized))
    except Exception:
        return False


def sanitize_stored_text(value: Any) -> str:
    """Normalize + bound a value for persistence (pure, never raises).

    Returns the cleaned string, or ``""`` when the value is unusable or
    instruction-like (callers drop empties instead of persisting them).
    """
    try:
        normalized = _normalize(value)
        if not normalized:
            return ""
        if contains_instruction(normalized):
            return ""
        if contains_markup(normalized):
            return ""
        if len(normalized) > MAX_STORED_CHARS:
            normalized = normalized[:MAX_STORED_CHARS].rstrip()
        return normalized
    except Exception:
        return ""


def is_render_safe(subject: Any, value: Any) -> bool:
    """Render-time gate for one subject/value pair (pure, never raises).

    False (drop the line) on instruction text, prompt markup, or
    card-like digit runs in either field. Defense-in-depth alongside
    write-time :func:`sanitize_stored_text` for rows predating it.
    """
    try:
        for field in (subject, value):
            normalized = _normalize(field)
            if not normalized:
                continue
            if contains_instruction(normalized):
                return False
            if contains_markup(normalized):
                return False
            if _CARD_RE.search(normalized):
                return False
        return True
    except Exception:
        return False
