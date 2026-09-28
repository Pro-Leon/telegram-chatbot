"""Phase 75E: Deterministic scoring replacement.

Replaces LLM #3 (score_draft) with deterministic heuristics.
Provides quality scoring without LLM involvement.

The scoring is based on:
1. Length heuristics (appropriate_length)
2. Formality detection (too_formal)
3. Generic pattern detection (too_generic)
4. Repetition detection (repetitive)

Safety flags remain in core/scoring.py (FLAG_KEYWORDS).
This module only handles quality scoring.
"""

import logging
import re
from typing import Any

logger = logging.getLogger("scoring_deterministic")


#: Canonical hollow-template openers — single source of truth.
#: Shared by the quality validator (flag) and the generation prompt
#: (never-list) so the two can never drift apart. Matching is
#: opener-position only (normalized draft start): mid-draft politeness
#: after specific content ("Sounds good! I appreciate it") is human and
#: stays clean; LEADING with one of these is the template reflex.
TEMPLATE_OPENER_PHRASES = (
    "i understand",
    "i appreciate",
    "thank you for",
    "how can i help",
    "how may i",
    "thanks for reaching",
    "let me know",
    "feel free",
    "i'm here for you",
    "i'm here to help",
    "that sounds amazing",
    "i totally get that",
    "that must be",
    "tell me more",
    "what about you",
    "that makes sense",
    "i get it",
)


_PAIR_QUESTION_RES = (
    re.compile(r"\?\s*$"),
    re.compile(
        r"^\s*(what|how|when|where|who|why|can you|could you|do you|are you|have you|is|are|do|does|did|will|would|should)\b",
        re.IGNORECASE,
    ),
)

_PAIR_STOPWORDS = frozenset(
    {
        "i", "me", "my", "you", "your", "yours", "we", "us", "our",
        "to", "the", "a", "an", "and", "or", "of", "in", "on", "at",
        "is", "are", "was", "were", "be", "it", "its", "that", "this",
        "so", "too", "just", "very", "really", "with", "for", "as",
        "do", "does", "did", "have", "has", "had", "will", "would",
        "can", "could", "should", "what", "how", "when", "where", "who",
        "why", "s", "t", "m", "re", "ll", "ve", "d",
    }
)

_NAME_REQUEST_RES = (
    re.compile(r"\bwhat'?s your name\b", re.IGNORECASE),
    re.compile(r"\bwhat is your name\b", re.IGNORECASE),
    re.compile(r"\bremind me your name\b", re.IGNORECASE),
    re.compile(r"\b(your|ur) name again\b", re.IGNORECASE),
)

_NAME_TALK_RE = re.compile(
    r"\b(my name|your name|call (you|me))\b", re.IGNORECASE
)
_WHATS_YOURS_RE = re.compile(
    r"\b(what'?s|what is|whats) yours\b", re.IGNORECASE
)


def _is_fan_question(text: Any) -> bool:
    try:
        if not isinstance(text, str) or not text.strip():
            return False
        return any(pat.search(text) for pat in _PAIR_QUESTION_RES)
    except Exception:
        return False


def _substantive_tokens(text: Any) -> set[str]:
    try:
        if not isinstance(text, str) or not text:
            return set()
        return set(
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if token and token not in _PAIR_STOPWORDS
        )
    except Exception:
        return set()


def _pair_completion_flags(
    draft: str,
    user_message: str,
    fan_name: str | None = None,
) -> list[str]:
    """Pair-completion grounding flags (pure, conservative, never raises).

    - ``no_grounding``: fan asked a question, the draft shares zero
      substantive tokens with it, asks nothing back, and is short
      (<=12 words). Long replies get the benefit of the doubt
      (answers paraphrase); drafts ending in a question read as
      engaged follow-ups, not ignores.
    - ``name_request_known``: the fan's first name is known (real name,
      never a placeholder) and the draft asks for it — either directly
      ("what's your name") or via "what's yours" framed as name-talk
      ("that's my name... what's yours?"). Preference-style "what's
      yours" without name-talk stays clean.
    """
    found: list[str] = []
    try:
        if not isinstance(draft, str) or not draft.strip():
            return found
        if isinstance(user_message, str) and user_message.strip():
            if _is_fan_question(user_message):
                fan_toks = _substantive_tokens(user_message)
                draft_toks = _substantive_tokens(draft)
                words = len(draft.split())
                if (
                    fan_toks
                    and not (fan_toks & draft_toks)
                    and "?" not in draft
                    and words <= 12
                ):
                    found.append("no_grounding")
        name = fan_name.strip() if isinstance(fan_name, str) else ""
        if name and name.lower() not in ("", "there", "fan", "user"):
            if any(pat.search(draft) for pat in _NAME_REQUEST_RES):
                found.append("name_request_known")
            elif _WHATS_YOURS_RE.search(draft) and _NAME_TALK_RE.search(draft):
                found.append("name_request_known")
    except Exception:
        pass
    return found


def score_draft_deterministic(
    draft: str,
    user_message: str,
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
    fan_name: str | None = None,
) -> tuple[float, list[str]]:
    """Deterministic quality scoring for draft responses.

    Replaces LLM-based scoring with heuristics. Returns composite
    score (0.0-1.0) and list of quality flags.

    Args:
        draft: Generated response text
        user_message: Original fan message
        is_authorized_commerce: Whether this is authorized commerce response
        authorized_price_minor: Authorized price in cents (if applicable)
        fan_name: Fan's known first name (for the name-request check;
            placeholder-y names like Fan/there never count as known)

    Returns:
        Tuple of (composite_score, quality_flags)
    """
    flags: list[str] = []

    # 1. Length check (0-10) — human-shaped: brief-but-clean replies are
    # legitimate (affiliative continuers, reactions). Only degenerate
    # (0-1 words) is flagged generic; 2-word replies get an informational
    # short_reply flag for queue triage; 3-4 word clean replies may send.
    word_count = len(draft.split())
    if 10 <= word_count <= 100:
        length_score = 10.0
    elif 5 <= word_count < 10 or 100 < word_count <= 150:
        length_score = 7.0
    elif 3 <= word_count < 5:
        length_score = 8.0
    elif 150 < word_count <= 200:
        length_score = 5.0
    elif word_count == 2:
        length_score = 5.0
        flags.append("short_reply")
    else:
        length_score = 1.0
        flags.append("too_generic")

    # 2. Formality check (0-10)
    formal_indicators = [
        "dear", "sincerely", "regards", "respectfully",
        "therefore", "furthermore", "consequently", "moreover",
        "in conclusion", "as per", "pursuant to",
    ]
    formal_count = sum(1 for f in formal_indicators if f in draft.lower())
    if formal_count == 0:
        tone_score = 9.0
    elif formal_count <= 2:
        tone_score = 6.0
        flags.append("too_formal")
    else:
        tone_score = 3.0
        flags.append("too_formal")

    # 3. Template openers (0-10) — opener-position only: leading with a
    # hollow template is the reflex; the same words mid-draft after
    # specific content are human politeness and stay clean.
    _draft_open = draft.lower().strip()
    generic_count = sum(1 for g in TEMPLATE_OPENER_PHRASES if _draft_open.startswith(g))
    if generic_count == 0:
        awareness_score = 9.0
    elif generic_count <= 1:
        awareness_score = 6.0
        flags.append("too_generic")
    else:
        awareness_score = 3.0
        flags.append("too_generic")

    # 4. Repetition check (0-10)
    words = draft.lower().split()
    if len(words) > 5:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio >= 0.7:
            repetition_score = 10.0
        elif unique_ratio >= 0.5:
            repetition_score = 6.0
            flags.append("repetitive")
        else:
            repetition_score = 3.0
            flags.append("repetitive")
    else:
        repetition_score = 8.0

    # 5. Pair completion (deterministic grounding proxies — research-backed:
    # a reply reads human when it completes the open pair and grounds
    # specifically; word count never proved either).
    for _pf in _pair_completion_flags(draft, user_message, fan_name=fan_name):
        if _pf not in flags:
            flags.append(_pf)

    # Composite score (0.0 to 1.0)
    composite = (length_score + tone_score + awareness_score + repetition_score) / (4 * 10)

    # Override: degenerate replies (0-1 words) capped at 0.5
    if word_count <= 1:
        composite = min(composite, 0.5)
        if "too_generic" not in flags:
            flags.append("too_generic")

    # 6. Markup echo (internal prompt scaffolding in the draft).
    # Stage directions ([PLAYER MESSAGE], [RETRIEVED ...], phase headers)
    # and internal ids must never reach the fan. Flag + soft cap so the
    # turn goes to review with a documented reason.
    try:
        from core.text_sanitize import contains_markup as _has_markup

        _has_internal_id = bool(re.search(r"\bid:\d+", draft))
        if _has_markup(draft) or _has_internal_id:
            if "markup_echo" not in flags:
                flags.append("markup_echo")
            composite = min(composite, 0.5)
    except Exception:
        pass

    return float(composite), flags


def compute_safety_flags(
    draft: str,
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
) -> list[str]:
    """Compute deterministic safety flags from draft text.

    Replicates the safety-critical keyword detection from core/scoring.py
    without LLM involvement.

    Args:
        draft: Generated response text
        is_authorized_commerce: Whether this is authorized commerce response
        authorized_price_minor: Authorized price in cents (if applicable)

    Returns:
        List of safety flags
    """
    from core.scoring import FLAG_KEYWORDS, HARD_FLAGS

    flags: list[str] = []
    draft_lower = draft.lower()

    for flag, keywords in FLAG_KEYWORDS.items():
        if flag not in HARD_FLAGS:
            continue

        # Skip price mentions for authorized commerce
        if flag == "price_mention" and is_authorized_commerce:
            if authorized_price_minor is not None:
                _price_re = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")
                _authorized_dollars = authorized_price_minor / 100.0
                _found_prices: list[float] = []
                for m in _price_re.finditer(draft):
                    try:
                        _found_prices.append(float(m.group(1)))
                    except Exception:
                        continue
                if _found_prices:
                    _matches = any(abs(p - _authorized_dollars) <= 0.005 for p in _found_prices)
                    if _matches:
                        continue
                else:
                    if f"${_authorized_dollars:.2f}" in draft or f"${int(_authorized_dollars)}" in draft:
                        continue
                    if "$" not in draft and "price" not in draft_lower:
                        continue
                    continue
            else:
                continue

        if any(k in draft_lower for k in keywords):
            flags.append(flag)

    return flags


def validate_draft_quality(
    draft: str,
    user_message: str,
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
    fan_name: str | None = None,
) -> tuple[bool, float, list[str], list[str]]:
    """Validate draft quality deterministically.

    Returns:
        Tuple of (is_approved, quality_score, quality_flags, safety_flags)
    """
    # Quality scoring
    quality_score, quality_flags = score_draft_deterministic(
        draft, user_message,
        is_authorized_commerce=is_authorized_commerce,
        authorized_price_minor=authorized_price_minor,
        fan_name=fan_name,
    )

    # Safety flags
    safety_flags = compute_safety_flags(
        draft,
        is_authorized_commerce=is_authorized_commerce,
        authorized_price_minor=authorized_price_minor,
    )

    # Approval decision
    # Auto-approve if:
    # 1. Quality score >= 0.80
    # 2. No safety flags
    # 3. No critical quality flags (too_formal, too_generic, repetitive,
    #    plus the pair-completion flags: short ungrounded replies and
    #    name-requests need human eyes)
    critical_quality_flags = {"too_formal", "too_generic", "repetitive", "short_reply", "no_grounding", "name_request_known"}
    has_critical_flags = bool(set(quality_flags) & critical_quality_flags)

    is_approved = (
        quality_score >= 0.80
        and not safety_flags
        and not has_critical_flags
    )

    return is_approved, quality_score, quality_flags, safety_flags
