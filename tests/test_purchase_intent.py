"""Tests for deterministic purchase/price intent verifiers — P0 authorization gates.

These are security gates: false negatives (missed buy) are safe,
false positives (hallucinated buy) are unsafe. Tests prefer conservative.
"""

import pytest

from commerce.purchase_intent import is_explicit_purchase_request, is_price_inquiry


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Purchase positive — MUST be True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg",
    [
        "I want to buy",
        "I wanna buy the pics",
        "I would like to purchase this",
        "I'll pay",
        "I'll pay $30",
        "I’m ready to buy",
        "I'm ready to purchase",
        "can I buy this?",
        "can I pay?",
        "how do I pay?",
        "where can I buy this?",
        "take my money",
        "send me a pic and I'll pay",
        "send me a pic and I’ll pay $30",
        "unlock it and I'll pay",
        "ready to pay for it",
        "ready to purchase",
        "ready to buy",
        "I will pay",
        "I want to purchase",
    ],
)
def test_purchase_positive(msg):
    assert is_explicit_purchase_request(msg) is True, f"expected True for {msg!r}"


# ---------------------------------------------------------------------------
# Purchase negative — MUST be False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg",
    [
        "don't buy it",
        "dont buy it",
        "do not buy it",
        "you should buy this",
        "my friend wants to buy",
        "she wants to purchase it",
        "I wouldn't purchase that",
        "I won't buy it",
        "I'm not buying",
        "not interested in buying",
        "I just bought it",
        "I already bought it",
        "what do people pay?",
        "what does everyone buy?",
        "you should buy this for yourself",
        "he wants to buy it",
        "they want to buy",
        "I just paid $20 yesterday",  # paid recount, not request
    ],
)
def test_purchase_negative(msg):
    assert is_explicit_purchase_request(msg) is False, f"expected False for {msg!r}"


# ---------------------------------------------------------------------------
# Price positive — MUST be True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg",
    [
        "how much?",
        "how much for pics?",
        "how much does it cost?",
        "what's the price?",
        "what is the price?",
        "price?",
        "price list",
        "do you charge?",
        "do you have a price?",
        "how much to unlock everything?",
        "is it $20?",
        "is this $30?",
        "send me a pic, how much?",
        "$20",
        "$ 20",
        "$20.00",
        "$ 20.00",
        "how much does it cost to unlock everything?",
        "whats the price",
    ],
)
def test_price_positive(msg):
    assert is_price_inquiry(msg) is True, f"expected True for {msg!r}"


# ---------------------------------------------------------------------------
# Price negative — MUST be False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg",
    [
        "hey",
        "hello",
        "that's priceless",
        "I just paid $20 yesterday",
        "I already paid $20",
        "what do people pay?",
        "I love your content",
        "just chilling tonight, you?",
        "your price is priceless haha",  # priceless not inquiry
    ],
)
def test_price_negative(msg):
    assert is_price_inquiry(msg) is False, f"expected False for {msg!r}"


# ---------------------------------------------------------------------------
# Boundary / robustness
# ---------------------------------------------------------------------------

def test_empty_and_none_like():
    assert is_explicit_purchase_request("") is False
    assert is_explicit_purchase_request("   ") is False
    assert is_price_inquiry("") is False
    assert is_price_inquiry("   ") is False

def test_bounded_length():
    long_msg = "a" * 10000 + " I want to buy"
    # Should still be True but bounded to 500, so long prefix truncated -> may be False
    # Our implementation truncates to 500, so this tests boundedness doesn't crash
    result = is_explicit_purchase_request(long_msg)
    assert isinstance(result, bool)

def test_case_insensitive():
    assert is_explicit_purchase_request("I WANT TO BUY") is True
    assert is_price_inquiry("HOW MUCH?") is True

def test_punctuation_tolerant():
    assert is_explicit_purchase_request("I want to buy!!!") is True
    assert is_price_inquiry("how much for pics???") is True

def test_purchase_allows_apostrophe_variants():
    assert is_explicit_purchase_request("I’ll pay") is True
    assert is_explicit_purchase_request("I'll pay") is True
    assert is_explicit_purchase_request("I’m ready to buy") is True
