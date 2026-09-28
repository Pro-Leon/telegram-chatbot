# Phase 75D: Commerce Signal Integration

**Date:** September 2, 2026  
**Status:** COMPLETE  
**Worker:** `core/commerce_prompt.py` (new file)  
**Tests:** `tests/test_phase75d_commerce_prompt.py` (18 tests, all pass)

---

## 1. Executive Summary

Phase 75D implements commerce signal integration for the one-call prompt. The module provides Qwen2.5 with commerce-relevant context so it can output accurate advisory signals alongside the reply.

**Key principle:** All signals remain advisory. No field directly authorizes price, payment, or access.

---

## 2. Design Principles

### Advisory Signals Only
- Commerce signals are observations about fan intent
- No signal can authorize a purchase or payment
- All commerce authority remains in deterministic engine

### Compact Context
- Key facts extracted from commerce text
- Relationship state included
- Offer/purchase context included
- No verbose formatting

### Safety Boundary
- Qwen cannot authorize price, payment, or access
- Qwen cannot bypass hard-deny rules
- Qwen cannot create offers or transactions

---

## 3. Module Structure

### build_commerce_signal_hints()
Builds compact commerce context for one-call prompt:
```python
hints = build_commerce_signal_hints(
    commerce_text=commerce_text,
    relationship_state="warm",
    recent_offer_count=2,
    has_active_offer=True,
)
```

### _extract_key_commerce_facts()
Extracts key facts from commerce text:
- Filters by keywords (purchase, tip, offer, etc.)
- Excludes formatting lines
- Limits to 3 facts

### build_commerce_signal_schema()
Returns schema definition for commerce_signals field.

### COMMERCE_SIGNAL_INSTRUCTIONS
System prompt instructions for generating commerce signals.

---

## 4. Commerce Context Hints

| Hint | Description | Example |
|------|-------------|---------|
| COMMERCE FACTS | Key facts from commerce text | "Purchase: 2 purchases" |
| RELATIONSHIP | Current relationship state | "RELATIONSHIP: warm" |
| ACTIVE OFFER | Whether fan has active offer | "ACTIVE OFFER: yes" |
| RECENT OFFERS | Number of recent offers | "RECENT OFFERS: 2" |
| RECENT PURCHASES | Number of recent purchases | "RECENT PURCHASES: 1" |
| RELEVANT PRODUCT | Whether relevant product exists | "RELEVANT PRODUCT: yes" |

---

## 5. Signal Schema

The one-call JSON output includes commerce_signals with 18 fields:

| Field | Type | Description |
|-------|------|-------------|
| purchase_intent | float 0.0-1.0 | How likely fan wants to buy |
| content_interest | float 0.0-1.0 | How interested in content |
| relationship_engagement | float 0.0-1.0 | How engaged in conversation |
| price_interest | float 0.0-1.0 | How much asking about price |
| explicit_purchase_request | boolean | Fan explicitly wants to buy |
| explicit_content_request | boolean | Fan explicitly asked for content |
| requested_price | null or number | Price fan mentioned |
| declined_recent_offer | boolean | Fan declined recent offer |
| asks_for_free_content | boolean | Fan asking for free content |
| negative_sentiment | float 0.0-1.0 | How negative fan is |
| confidence | float 0.0-1.0 | Confidence in signals |
| evidence | list[str] | Supporting quotes (max 5) |
| model_uncertainty | float 0.0-1.0 | Uncertainty in signals |
| primary_intent | str | Primary intent category |
| intent_tags | list[str] | Intent tags (max 5) |
| negative_intent_tags | list[str] | Negative intents |
| fan_asks_question | boolean | Whether fan asked a question |

---

## 6. Safety Properties

### PPV Authority Preserved
- Qwen outputs `purchase_intent` (advisory)
- Deterministic engine decides whether to offer
- `execute_ppv()` re-validates all conditions
- Price, product, URL from DB only

### Persona Enforcement Preserved
- Behavior block in context
- `validate_persona_voice()` post-generation
- Identity lifecycle maintained

### Handoff Safety Preserved
- Confidence threshold routing
- Deterministic keyword flags
- Operator queue for safety flags

---

## 7. Test Coverage

18 tests covering:
- Commerce signal hints (7 tests)
- Key fact extraction (5 tests)
- Signal schema (3 tests)
- Instructions (3 tests)

**All tests pass.**

---

## 8. Integration Points

### With Phase 75B (One-Call Contract)
```python
from core.commerce_prompt import build_commerce_signal_hints

hints = build_commerce_signal_hints(
    commerce_text=commerce_text,
    relationship_state=relationship_state,
)
# Add hints to context
```

### With Phase 75F (One-Call Pipeline)
```python
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS

# Include in system prompt
system_prompt = f"""{persona}

{COMMERCE_SIGNAL_INSTRUCTIONS}
"""
```

---

## 9. Files Modified/Created

### Created
- `core/commerce_prompt.py` — Commerce signal integration module
- `tests/test_phase75d_commerce_prompt.py` — 18 regression tests
- `docs/AI_NATIVE_LLM_PHASE_75D_COMMERCE_SIGNAL_INTEGRATION.md` — This report

### Modified
- None

---

## 10. Next Steps

1. **Phase 75E:** Scoring replacement (deterministic heuristics)
2. **Phase 75F:** Qwen2.5 one-call pipeline implementation
3. **Phase 75G:** Benchmark / shadow comparison
4. **Phase 75H:** Controlled activation (feature flag)

---

## PHASE 75D VERDICT

```
STATUS: COMPLETE

FILES CREATED:
- core/commerce_prompt.py (150 lines)
- tests/test_phase75d_commerce_prompt.py (200 lines)
- docs/AI_NATIVE_LLM_PHASE_75D_COMMERCE_SIGNAL_INTEGRATION.md (this file)

TESTS: 18/18 pass

SIGNALS:
- 18 advisory fields for commerce intent
- All signals are observations only
- No field authorizes price, payment, or access

SAFETY:
- PPV authority preserved (deterministic engine unchanged)
- Persona enforcement preserved (behavior block + validation)
- Handoff safety preserved (confidence + keyword flags)

NEXT PHASE:
75E — Scoring replacement (deterministic heuristics)
```
