"""Unauthorized soft-sell CTA detection — Phase 4.

Pure, deterministic, side-effect free: no DB, no Redis, no network, no
LLM, no clock, no persistence, no global mutable state. Only ``re`` and
``typing`` are imported (mirrors ``commerce/boundary_validation.py``
``_COMMERCE_RE`` style: compiled word-boundaried patterns).

Contract:
    detect_unauthorized_commercial_cta(reply, is_authorized=False)
        -> (hit, matched_pattern)

* ``hit`` is True when ``reply`` carries an unauthorized commercial
  call-to-action and ``is_authorized`` is False.
* ``matched_pattern`` is the name of the first matching pattern group
  (for logging/tests), else None.
* ``is_authorized=True`` short-circuits to ``(False, None)`` — the
  caller computes authority from already-in-scope deterministic state
  (USE_COMMERCE_RESPONSE selection, sealed-PPV handled flag, free-photo
  authorization); this module never decides authority itself.
* Empty / non-string input yields ``(False, None)``. Never raises.

Scope note: price/CTA keywords already covered elsewhere
(``price_mention``/``photo_promise`` in ``core/scoring.py``,
``_COMMERCE_RE`` under STOP_CONVERSATION, ``_FORBIDDEN_VOCABULARY`` /
``_OFFER_CLAIM_PHRASES`` in commerce-draft copy) are intentionally NOT
re-implemented here except where a soft-sell phrasing would otherwise
pass all of them clean (verified gap table in the Phase 4 audit).
Overlapping patterns (e.g. ``buy it here``) are included for
defense-in-depth; they already fire ``price_mention`` via ``buy``.
"""

from __future__ import annotations

import re
from typing import Any

#: Flag appended to the worker ``flags`` list on a hit. Flows through the
#: existing ``has_blocking_flags -> QUEUE (blocking_flags)`` routing path.
#: Distinct from the ``unauthorized_price_attempt`` ledger key
#: (``commerce/adaptive_optimization.py``) — never reuse that key.
CTA_FLAG = "unauthorized_commercial_cta"

#: Content nouns that mark a CTA as commercial rather than ordinary chat.
#: Bounded allowlist — "exclusive interview" and "unlock your potential"
#: carry no noun from this set and must keep passing.
_CONTENT_NOUN_ALT = (
    r"pics?|photos?|selfies?|content|set|bundle|vault|drops?|ppvs?|"
    r"items?|collection|videos?|photoshoot|link"
)
_CONTENT_NOUNS = r"(?:" + _CONTENT_NOUN_ALT + r")"

#: (pattern name, compiled regex). Word-boundaried; case-insensitive.
#: Ordered most-specific first; the first hit names ``matched_pattern``.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "want_to_see",
        re.compile(
            r"\bwant\s+to\s+see\b.{0,40}\b(?:my\s+)?" + _CONTENT_NOUNS,
            re.IGNORECASE,
        ),
    ),
    (
        "send_you_something",
        re.compile(
            r"\bsend(?:ing)?\s+(?:you|u|ya)\s+"
            r"(?:something\s+special|a\s+(?:pic|photo|selfie)|"
            r"something\s+exclusive)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unlock_content",
        re.compile(
            r"\bunlock\b.{0,30}\b(?:my\s+|the\s+)?" + _CONTENT_NOUNS,
            re.IGNORECASE,
        ),
    ),
    (
        "check_out_content",
        re.compile(
            r"\bcheck\s+out\s+my\b.{0,30}\b(?:new\s+set\b|" + _CONTENT_NOUN_ALT + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "exclusive_content",
        re.compile(
            r"\bexclusive\b.{0,30}\b" + _CONTENT_NOUNS,
            re.IGNORECASE,
        ),
    ),
    (
        "special_for_you",
        re.compile(
            r"\bsomething\s+special\s+for\s+you\b",
            re.IGNORECASE,
        ),
    ),
    (
        "offer_created",
        re.compile(
            r"\b(?:offer\s+(?:was|has\s+been)\s+created|"
            r"payment\s+link\s+is\s+ready|"
            r"buy\s+it\s+here|"
            r"grab\s+it\s+here|"
            r"link\s+in\s+bio)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "promo_language",
        re.compile(
            r"\b(?:limited\s+offer|discount|only\s+today|"
            r"special\s+price|half\s+price|50%\s*off|"
            r"50\s*percent\s+off|on\s+sale)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bare_amount",
        re.compile(
            r"\b\d+(?:\.\d{1,2})?\s*dollars?\b",
            re.IGNORECASE,
        ),
    ),
    # H1 draft-promise pin (bot promises, NOT fan asks): a de-escalation
    # draft promising a refund, account credit, or free content must queue
    # like any other unauthorized CTA. Fan asking for free content is a
    # separate signal (asks_for_free_content) and is untouched here.
    (
        "refund_promise",
        re.compile(
            r"\bi\s*(?:will|'ll)?\s*(?:give\s+you\s+(?:a\s+)?)?refund\b"
            r"|\bgive\s+you\s+(?:a\s+)?refund\b"
            r"|\brefund\s+(?:you|your\s+money)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "credit_promise",
        re.compile(
            # FP: "credit card" is a payment method, never a promise.
            r"\bgive\s+you\s+(?:a\s+)?credit\b(?!\s+card\b)"
            r"|\bcredit\s+(?:your\s+account|back\b)"
            r"|\baccount\s+credited\b",
            re.IGNORECASE,
        ),
    ),
    (
        "free_content_promise",
        re.compile(
            r"\bfree\s+(?:video|videos|pic|pics|photo|photos|selfie|selfies|content|bundle|set)\s+on\s+me\b"
            r"|\bi\s*(?:will|'ll)?\s*(?:send|give)\s+you\b.{0,30}\bfree\b"
            r"|\bon\s+the\s+house\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_unauthorized_commercial_cta(
    reply: Any,
    is_authorized: bool = False,
) -> tuple[bool, str | None]:
    """Detect an unauthorized commercial CTA in a draft reply (pure).

    Args:
        reply: Draft reply text (untrusted). Non-string or blank input
            yields ``(False, None)``.
        is_authorized: Caller-computed authority for this turn. True
            short-circuits to ``(False, None)``.

    Returns:
        ``(hit, matched_pattern)`` where ``matched_pattern`` is the
        first matching group name or None. Never raises.
    """
    try:
        if bool(is_authorized):
            return False, None
        if not isinstance(reply, str) or not reply.strip():
            return False, None
        for name, pattern in _PATTERNS:
            try:
                if pattern.search(reply):
                    return True, name
            except Exception:
                continue
        return False, None
    except Exception:
        return False, None


__all__ = [
    "CTA_FLAG",
    "detect_unauthorized_commercial_cta",
]
