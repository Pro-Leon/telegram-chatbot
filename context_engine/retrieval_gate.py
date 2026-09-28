"""Retrieval Gating — Pass 2 Deterministic (no LLM, no DB, no embeddings).

Decision: should retrieval run lexical+semantic branches for this turn?

Fail-open: any error -> allow retrieval (conservative). Strictly deterministic
given same message + conversation_state. No I/O.
"""

from __future__ import annotations

import re
from typing import Any

# Exact short greetings that never need knowledge retrieval
_EXACT_NO_RETRIEVE = {
    "hey",
    "hi",
    "hello",
    "heya",
    "yo",
    "yes",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "lol",
    "haha",
    "good morning",
    "good night",
    "good afternoon",
    "good evening",
    "how are you",
    "how are you?",
    "miss you",
    "miss u",
}

# Retrieve when these substrings appear (memory/context.py:58 RETRIEVAL_TRIGGERS)
# Reused verbatim to avoid drift; imported lazily to keep module light.
_RETRIEVAL_TRIGGERS = (
    "remember",
    "told you",
    "said",
    "mentioned",
    "last time",
    "before",
    "earlier",
    "used to",
    "what was",
    "you said",
    "previous",
)

# Cheap product/topic lexicon (static, no DB — catalog lookups are async DB
# and forbidden in this pure gate). Catches catalog-topic turns like
# "show me the red lace collection" that carry no ?/trigger/topic signal.
_PRODUCT_TOPIC_LEXICON = (
    "collection",
    "video",
    "videos",
    "catalog",
    "lace",
    "product",
    "photoshoot",
    "bundle",
)

_STOPWORDS = {
    "what", "when", "where", "why", "how", "who",
    "your", "you", "are", "is", "the", "and", "for",
    "with", "about", "that", "this", "have", "has",
    "had", "will", "would", "could", "should", "miss",
    "are", "you", "a", "an", "to", "of", "in", "on",
    "it", "i", "me", "my", "we", "our", "us",
}

def _has_meaningful_token(lower: str) -> bool:
    """True if there is at least one word >4 chars not in stopwords."""
    words = re.findall(r"[a-z0-9]+", lower)
    for w in words:
        if w in _STOPWORDS:
            continue
        if len(w) > 4:
            return True
    return False

def should_retrieve_knowledge(message: str, conversation_state: dict[str, Any] | None) -> bool:
    """Deterministic gating for MemorySource lexical+semantic retrieval.

    Returns True when retrieval is required, False when the turn is simple
    and can safely skip expensive branches. No DB, no embeddings, no LLM.

    Rules (in order):
    1. Empty/whitespace -> False
    2. Exact greeting blocklist -> False
    3. len==1 -> False
    4. len<8 and not _is_question -> False  (covers "hey" len3 audit 27ms)
    5. Memory triggers ("remember", "told you", ...) -> True
    6. conversation_state.current_topic or open_threads substring in message lower -> True
    6b. Commerce intent (is_explicit_purchase_request/is_price_inquiry, text-only) -> True
    6c. conversation_state fan_asks_question is True -> True
    6d. Product/topic lexicon (static, no DB) -> True
    7. _is_question and len>12 -> True
    8. No meaningful token (>4 chars non-stopword) -> False
    9. Default -> False (conservative: only explicit signals retrieve)
    """
    try:
        if not message or not message.strip():
            return False
        stripped = message.strip()
        lower = stripped.lower()

        # 2. exact blocklist
        if lower in _EXACT_NO_RETRIEVE:
            return False

        # 3. single char
        if len(stripped) == 1:
            return False

        # Lazy import to avoid circular and reuse canonical logic
        try:
            from core.conversation_contract import _is_question as _is_q  # core/conversation_contract.py:34
        except Exception:
            # Fallback local (should not happen)
            def _is_q(t: str) -> bool:  # type: ignore
                t = t.strip()
                if not t:
                    return False
                if t.endswith("?"):
                    return True
                return bool(re.match(r"^\s*(what|how|when|where|who|why|can you|could you|do you|are you|have you|will you|would you|did you|is |are |who's|what's)\b", t.lower()))

        is_question = _is_q(stripped)

        # 4. len<8 and not question -> false
        if len(stripped) < 8 and not is_question:
            return False

        # 5. memory triggers (case-insensitive substring)
        # Import canonical triggers to avoid drift, but keep local fallback
        try:
            from memory.context import RETRIEVAL_TRIGGERS as _CANON_TRIG  # memory/context.py:58
            triggers = _CANON_TRIG
        except Exception:
            triggers = _RETRIEVAL_TRIGGERS
        for trig in triggers:
            if trig.lower() in lower:
                return True

        # 6. conversation_state topic/thread continuity
        if isinstance(conversation_state, dict):
            topic = conversation_state.get("current_topic")
            if isinstance(topic, str) and topic.strip():
                if topic.lower().strip() in lower:
                    return True
            threads = conversation_state.get("open_threads")
            if isinstance(threads, (list, tuple)):
                for t in threads:
                    if isinstance(t, str) and t.strip() and t.lower().strip() in lower:
                        return True

        # 6b. Commerce intent (deterministic text-only verifiers, no signals
        # arg — signals do not exist yet at retrieval time). Catches
        # "i want to buy ..." / "i will pay $30" as audited in Pass 2L.
        # Lazy import to avoid circulars; fail-closed to next rule.
        try:
            from commerce.purchase_intent import (
                is_explicit_purchase_request as _is_buy,
            )
            from commerce.purchase_intent import is_price_inquiry as _is_price

            if bool(_is_buy(message)) or bool(_is_price(message)):
                return True
        except Exception:
            pass

        # 6c. Fan-asks flag (off-contract today: ConversationState has no such
        # field, but honor it if a caller ever passes it — no DB).
        try:
            if isinstance(conversation_state, dict) and conversation_state.get("fan_asks_question") is True:
                return True
        except Exception:
            pass

        # 6d. Product/topic lexicon (static, no DB). Catches catalog-topic
        # turns like "show me the red lace collection".
        try:
            for _lex in _PRODUCT_TOPIC_LEXICON:
                if _lex in lower:
                    return True
        except Exception:
            pass

        # 7. question + >12 chars -> true
        if is_question and len(stripped) > 12:
            return True

        # 8. no meaningful token -> false
        if not _has_meaningful_token(lower):
            return False

        # 9. default conservative false
        return False
    except Exception:
        # Fail-open: on any error, allow retrieval (conservative)
        return True
