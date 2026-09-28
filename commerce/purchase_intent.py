"""Deterministic purchase/price intent verifiers — authorization gates.

These functions are **security/authorization gates**, not general NLP classifiers.

They are intentionally:

* deterministic (no randomness, no LLM, no DB, no clock)
* pure (only the supplied ``user_message`` matters)
* conservative (prefer false negatives over false positives)
* bounded (input truncated, regex work limited)
* independent of any LLM signal or application state

Purpose: ``LLM explicit_purchase_request == True`` MUST NOT authorize PPV
unless ``deterministic verifier(last raw user turn) == True``.

Same for price.

Fail-closed: any unexpected input → False, never True.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_LEN = 500  # bound pathological input

def _norm(text: str) -> str:
    if not isinstance(text, str):
        return ""
    # normalize curly apostrophes to straight, collapse whitespace, lower
    t = text.strip()[:_MAX_LEN]
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    # lower for matching but keep original length for negation windows
    return t.lower()

# ---------------------------------------------------------------------------
# Purchase intent — first-person, non-negated, non-past, non-third-person
# ---------------------------------------------------------------------------

# Negation terms that within a short window before the buy trigger invalidate it.
_NEGATION_RE = re.compile(
    r"\b(don't|dont|do not|doesn't|doesnt|does not|didn't|didnt|did not"
    r"|wouldn't|wouldnt|would not|won't|wont|will not"
    r"|can't|cant|cannot|shouldn't|shouldnt|should not"
    r"|couldn't|couldnt|could not|not|never|no)\b",
    re.IGNORECASE,
)

# Past purchase / already-purchased phrasing that must NOT be treated as
# a new purchase request.
_PAST_PURCHASE_RE = re.compile(
    r"\b(just\s+bought|already\s+bought|just\s+paid|already\s+paid|bought\s+it|paid\s+already|bought\s+already)\b",
    re.IGNORECASE,
)

# Third-person / non-self subjects that must not authorize.
_THIRD_PERSON_RE = re.compile(
    r"\b(my\s+friend|my\s+buddy|she\s+wants|he\s+wants|they\s+want|people\s+pay|everyone\s+buy)\b",
    re.IGNORECASE,
)

# Allowlist of explicit, first-person purchase constructions.
# Each pattern is anchored to first-person / direct-user intent.
_PURCHASE_ALLOWLIST: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bi\s+want\s+to\s+buy\b", re.IGNORECASE),
    re.compile(r"\bi\s+wanna\s+buy\b", re.IGNORECASE),
    re.compile(r"\bi\s+wanna\s+pay\b", re.IGNORECASE),
    re.compile(r"\bi\s+would\s+like\s+to\s+(?:buy|purchase)\b", re.IGNORECASE),
    re.compile(r"\bi\s+want\s+to\s+purchase\b", re.IGNORECASE),
    re.compile(r"\bi\s*'?m\s+ready\s+to\s+(?:buy|purchase|pay)\b", re.IGNORECASE),
    re.compile(r"\bim\s+ready\s+to\s+(?:buy|purchase|pay)\b", re.IGNORECASE),
    re.compile(r"\bready\s+to\s+(?:buy|pay|purchase)\b", re.IGNORECASE),
    re.compile(r"\bi(?:'ll|\s+will)\s+pay\b", re.IGNORECASE),
    re.compile(r"\bi\s+will\s+purchase\b", re.IGNORECASE),
    re.compile(r"\bcan\s+i\s+(?:buy|pay|purchase)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+do\s+i\s+pay\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+can\s+i\s+buy\b", re.IGNORECASE),
    re.compile(r"\btake\s+my\s+money\b", re.IGNORECASE),
    re.compile(r"\bunlock\b.{0,20}\bi(?:'ll|\s+will)\s+pay\b", re.IGNORECASE),
)

def _has_negation_before(text_lower: str, match_start: int, window: int = 40) -> bool:
    """Check whether a negation term appears shortly before the match."""
    snippet = text_lower[max(0, match_start - window): match_start]
    return bool(_NEGATION_RE.search(snippet))

def _is_past_or_third_person(text_lower: str) -> bool:
    """Return True for phrasing that is past, third-person, or generic."""
    if _PAST_PURCHASE_RE.search(text_lower):
        return True
    if _THIRD_PERSON_RE.search(text_lower):
        return True
    # "not interested in buying" etc.
    if re.search(r"\bnot\s+interested\b", text_lower):
        return True
    if re.search(r"\bnot\s+buying\b", text_lower):
        return True
    return False


def is_explicit_purchase_request(user_message: str) -> bool:
    """Return True only for conservative, first-person explicit purchase requests.

    Security gate: favors false negatives (missed buy) over false positives
    (hallucinated buy). Bounded to current user message only.

    Positive:  "I want to buy", "I'll pay $30", "can I buy this?", etc.
    Negative:  "don't buy it", "you should buy", "my friend wants to buy",
               "I just bought it", "what do people pay?"
    """
    norm = _norm(user_message)
    if not norm or len(norm.strip()) < 2:
        return False

    # Quick past / third-person exclusion before allowlist
    if _is_past_or_third_person(norm):
        # Still allow if the message ALSO contains a clear future request
        # like "I just bought it but I want to buy again" — but be conservative:
        # require an allowlist hit that is not the past phrase itself.
        pass  # fall through to allowlist + negation check; past flag alone not decisive if also future
        # Actually for "I just bought it" with no future phrase, allowlist won't hit -> False
        # For "I just bought it and I want to buy again" we should be True? Conservative: require future phrase.
        # We keep check but don't early return; negation/allowlist will decide.
    # Check for quoted speech that might embed second-person advice? We treat entire string;
    # conservative: if message contains "you should buy" without first-person buy, it won't match allowlist anyway.

    for pat in _PURCHASE_ALLOWLIST:
        m = pat.search(norm)
        if m:
            # Negation window check
            if _has_negation_before(norm, m.start()):
                continue
            # If past phrase exists and the match is the past phrase overlap? e.g., "I just bought it"
            # Already not matching allowlist (bought vs buy) so fine.
            # For "I wouldn't purchase that" -> "wouldn't" is negation before "purchase" -> covered by window.
            # Need explicit check for "wouldn't" inside the match span as well (e.g., "I wouldn't purchase")
            span = norm[m.start(): m.end()]
            if _NEGATION_RE.search(span):
                continue
            # Prevent "what do people pay?" style generic
            if "people pay" in norm or "everyone buy" in norm:
                continue
            return True

    return False


# ---------------------------------------------------------------------------
# Price inquiry
# ---------------------------------------------------------------------------

_PRICE_PHRASE_RE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow\s+much\b", re.IGNORECASE),
    re.compile(r"\bhow\s+much\s+does\s+it\s+cost\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is)\s+the\s+price\b", re.IGNORECASE),
    re.compile(r"\bwhats\s+the\s+price\b", re.IGNORECASE),
    re.compile(r"\bprice\s*\?", re.IGNORECASE),
    re.compile(r"\bprice\s+list\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\s+charge\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\s+have\s+a\s+price\b", re.IGNORECASE),
    re.compile(r"\bhow\s+much\s+to\s+unlock\b", re.IGNORECASE),
    re.compile(r"\bis\s+it\s+\$\s*\d", re.IGNORECASE),
    re.compile(r"\bis\s+this\s+\$\s*\d", re.IGNORECASE),
)

# Dollar amount like $20, $ 20, $20.00, $ 20.00
_DOLLAR_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")

# Past paid phrasing that invalidates a dollar inquiry
_PAST_PAID_RE = re.compile(
    r"\b(just\s+paid|already\s+paid|paid\s+yesterday|paid\s+already|paid\s+\$\s*\d+.*yesterday|paid\s+\$\s*\d+.*already)\b",
    re.IGNORECASE,
)

# Generic money discussion without asking creator
_GENERIC_MONEY_RE = re.compile(r"\bwhat\s+do\s+people\s+pay\b", re.IGNORECASE)


def is_price_inquiry(user_message: str) -> bool:
    """Return True only for conservative, direct price inquiries to the creator.

    Positive: "how much?", "what's the price?", "is it $20?", "$20" (standalone), etc.
    Negative: "hey", "that's priceless", "I just paid $20 yesterday", generic chat.
    """
    norm = _norm(user_message)
    if not norm or len(norm.strip()) < 2:
        return False

    # Exclude generic/people-pay without creator-directed inquiry
    if _GENERIC_MONEY_RE.search(norm):
        return False

    # Exclude "priceless" false positive (contains price substring)
    # Word boundary in phrase regex already excludes it; but be explicit:
    # If the only price-like word is priceless and no other signal, stay False.
    has_phrase = False
    for pat in _PRICE_PHRASE_RE:
        if pat.search(norm):
            # Negation check for price? "don't charge" etc not in positives.
            has_phrase = True
            break
    if has_phrase:
        # Still exclude past-paid + dollar combos that also contain how much?
        # e.g., "I just paid $20 yesterday how much?" unlikely; but be conservative: if past paid present, don't treat as inquiry
        if _PAST_PAID_RE.search(norm):
            return False
        return True

    # Dollar-based inquiry: require dollar + question-like context or standalone
    dollar_m = _DOLLAR_RE.search(norm)
    if dollar_m:
        # Exclude past-paid recounts
        if _PAST_PAID_RE.search(norm):
            return False
        # If dollar appears with "paid ... yesterday/already" -> not inquiry
        if re.search(r"\bpaid\b", norm) and re.search(r"\b(yesterday|already|just)\b", norm):
            return False
        # Standalone dollar like "$20" or "$20?" -> inquiry
        stripped = norm.strip()
        if re.fullmatch(r"\$?\s*\d+(?:\.\d{1,2})?\s*\??", stripped) and "$" in stripped:
            # Must contain $ to be dollar inquiry
            return True
        # Dollar + question mark -> likely asking
        if "?" in norm:
            return True
        # Dollar with "is it" / "is this" already captured above; else be conservative: dollar alone in sentence without question is not enough
        # So require question for non-standalone dollar
        return False

    # Bare "price" without question? "price?" already captured; "price" alone without ? not inquiry
    return False
