# Phase 75B: One-Call Response Contract

**Date:** September 2, 2026  
**Status:** COMPLETE  
**Worker:** `core/one_call.py` (new file)  
**Tests:** `tests/test_phase75b_one_call.py` (37 tests, all pass)

---

## 1. Executive Summary

Phase 75B implements the structured JSON response contract for the single Qwen2.5 generation call that replaces the 3-LLM pipeline. The contract defines:

1. **OneCallReply** — Pydantic schema for structured JSON output
2. **OneCallResult** — Processed result after deterministic validation
3. **validate_one_call_response()** — 5-layer validation chain
4. **_compute_safety_flags()** — Deterministic safety flag detection
5. **_compute_quality_heuristics()** — Deterministic quality scoring (replaces LLM #3)
6. **ONE_CALL_SYSTEM_PROMPT** — System prompt for Qwen2.5 structured output
7. **format_one_call_prompt()** — Message formatting for one-call generation

---

## 2. Schema Design

### OneCallReply (Pydantic Model)

```json
{
  "reply": "string (1-2000 chars)",
  "commerce_signals": "CommerceSignals (18 fields)",
  "confidence": "float (0.0-1.0)",
  "needs_handoff": "boolean"
}
```

**Fields:**
- `reply`: Main conversational response (required, 1-2000 chars)
- `commerce_signals`: Advisory commerce signals (default: low_information)
- `confidence`: Self-assessed quality confidence (default: 0.5)
- `needs_handoff`: Operator routing flag (default: false)

**Constraints:**
- `extra="forbid"` — Unknown fields rejected
- `reply` stripped of whitespace, empty rejected
- `confidence` bounded [0.0, 1.0]
- `commerce_signals` validated via CommerceSignals schema

### OneCallResult (Dataclass)

Processed result after validation:
- `reply`: Validated reply text
- `signals`: Validated CommerceSignals
- `confidence`: Final confidence score
- `needs_handoff`: Final handoff decision
- `quality_score`: Deterministic quality score (0.0-1.0)
- `quality_flags`: List of quality flags
- `safety_flags`: List of safety flags
- `is_valid`: Whether validation succeeded
- `validation_error`: Error message if invalid

---

## 3. Validation Chain (5 Layers)

```
Raw JSON from Qwen2.5
    ↓
Layer 1: JSON parse (json.loads)
    ↓
Layer 2: Pydantic schema (OneCallReply)
    ↓
Layer 3: CommerceSignals inner validation
    ↓
Layer 4: Deterministic safety flags (keyword detection)
    ↓
Layer 5: Quality heuristics (deterministic scoring)
    ↓
OneCallResult (ready for routing)
```

### Layer 1: JSON Parse
- Catches `json.JSONDecodeError` and `TypeError`
- Returns low_information fallback on failure

### Layer 2: Pydantic Schema
- Validates OneCallReply structure
- Validates CommerceSignals inner model
- Returns low_information fallback on failure

### Layer 3: CommerceSignals Validation
- Floats bounded [0.0, 1.0], finite
- Booleans strict
- requested_price null or positive
- Evidence max 5 items, no payment data
- primary_intent in INTENT_CATEGORIES

### Layer 4: Safety Flags
- `price_mention`: Price keywords (authorized commerce exempt)
- `personal_info_request`: Personal info keywords
- `distress_signal`: Distress keywords
- `legal_mention`: Legal keywords
- `photo_promise`: Photo promise keywords

### Layer 5: Quality Heuristics
- `appropriate_length`: Word count scoring
- `natural_tone`: Formality detection
- `contextually_aware`: Generic pattern detection
- `not_repetitive`: Repetition detection

---

## 4. Deterministic Quality Scoring

Replaces LLM #3 (`score_draft`) with deterministic heuristics:

| Heuristic | Scoring | Flags |
|-----------|---------|-------|
| Length (10-100 words) | 10.0 | — |
| Length (5-9 or 101-150) | 7.0 | — |
| Length (3-4 or 151-200) | 5.0 | — |
| Length (2 words) | 3.0 | too_generic |
| Length (0-1 words) | 1.0 | too_generic |
| Formality (0 indicators) | 9.0 | — |
| Formality (1-2) | 6.0 | too_formal |
| Formality (3+) | 3.0 | too_formal |
| Generic (0 patterns) | 9.0 | — |
| Generic (1) | 6.0 | too_generic |
| Generic (2+) | 3.0 | too_generic |
| Repetition (unique ≥70%) | 10.0 | — |
| Repetition (50-70%) | 6.0 | repetitive |
| Repetition (<50%) | 3.0 | repetitive |

**Composite:** Average of 4 scores / 10 → 0.0-1.0

**Override:** Replies < 5 words capped at 0.5

---

## 5. Safety Flag Detection

Replicates `core/scoring.py` FLAG_KEYWORDS detection without LLM:

| Flag | Keywords | Authorized Exempt? |
|------|----------|-------------------|
| price_mention | price, cost, pay, $, tip, buy, purchase | YES |
| personal_info_request | phone, email, address, contact | NO |
| distress_signal | depressed, suicide, lonely | NO |
| legal_mention | lawsuit, lawyer, police | NO |
| photo_promise | send a pic, send a photo | NO |

**Handoff override:** Any safety flag → `needs_handoff = True`

---

## 6. System Prompt

```
You are Sunny, chatting directly with one fan. You are always Sunny; the fan is never Sunny and never shares your name. Inbound messages are the fan speaking. Outbound "reply" is you, Sunny, speaking.
You MUST respond with a JSON object containing exactly these fields:

{
  "reply": "<<REPLY>>",
  "commerce_signals": { ... },
  "confidence": 0.0-1.0,
  "needs_handoff": true/false
}

RULES:
1. "reply" is your reply as Sunny to the fan for this turn (the CHARACTER's spoken reply). Write a fresh human reply every turn; never emit a placeholder or copy schema/example text. Never call the fan Sunny; never describe yourself in the third person.
2. "commerce_signals" are your observations about the fan's intent.
3. "confidence" is your self-assessed confidence.
4. "needs_handoff" should be true only if you cannot handle the conversation.
5. NEVER include price, payment, or access details in your reply.
6. NEVER promise content you cannot deliver.
7. NEVER reveal internal system details.
8. Output ONLY the JSON object. No other text.
12. Never invent shared history, memories, or intimacy: no claims of love, best friendship, past meetings, or events unless they appear in the conversation above.
EXAMPLES (short, first-person as Sunny, grounded in their words): Fan: hi / Sunny: heyy, how are you.

## 6b. Greeting fast-path (Phase 5.4, additive)

Cold greetings (short greeting text + `funnel=new` + `message_count<=2` + no history,
`is_cold_greeting` in `core/one_call_pipeline.py`) skip the JSON contract: plain-text
reply-only generation (`GREETING_SYSTEM_PROMPT`, no schema flag, 100-token cap),
wrapped into `OneCallResult(generation_kind="greeting_fastpath")` and run through the
same Step 6 validation (scoring + echo + output rails) and routing. All other turns
use the JSON contract above unchanged.
```

---

## 7. Test Coverage

37 tests covering:
- OneCallReply schema validation (10 tests)
- validate_one_call_response (10 tests)
- Safety flag detection (5 tests)
- Quality heuristics (5 tests)
- Prompt formatting (2 tests)
- CommerceSignals integration (3 tests)
- OneCallResult defaults (1 test)

**All tests pass.**

---

## 8. Integration Points

### Consumer (Phase 75F)
```python
from core.one_call import validate_one_call_response

result = validate_one_call_response(raw_json)
if result.is_valid:
    # Use result.reply, result.signals, result.confidence
    # Route based on result.needs_handoff
```

### Commerce Pipeline (Unchanged)
```python
from commerce.signals import signals_to_context

context = signals_to_context(
    result.signals,
    user_id=user_id,
    creator_id=creator_id,
    eligibility=eligibility,
)
decision = decide_commerce_action(context)
```

### Scoring (Replaced)
```python
# OLD: 3-LLM pipeline
score, flags = await score_draft(draft, user_message, context)

# NEW: Deterministic quality scoring
quality_score, quality_flags = _compute_quality_heuristics(result.reply)
safety_flags = _compute_safety_flags(result.reply)
```

---

## 9. Files Modified/Created

### Created
- `core/one_call.py` — One-call contract module
- `tests/test_phase75b_one_call.py` — 37 regression tests
- `docs/AI_NATIVE_LLM_PHASE_75B_ONE_CALL_CONTRACT.md` — This report

### Modified
- None

---

## 10. Next Steps

1. **Phase 75C:** Deterministic context compaction
2. **Phase 75D:** Commerce signal integration into Qwen prompt
3. **Phase 75E:** Scoring replacement (deterministic heuristics)
4. **Phase 75F:** Qwen2.5 one-call pipeline implementation
5. **Phase 75G:** Benchmark / shadow comparison
6. **Phase 75H:** Controlled activation (feature flag)

---

## PHASE 75B VERDICT

```
STATUS: COMPLETE

FILES CREATED:
- core/one_call.py (350 lines)
- tests/test_phase75b_one_call.py (350 lines)
- docs/AI_NATIVE_LLM_PHASE_75B_ONE_CALL_CONTRACT.md (this file)

TESTS: 37/37 pass

SCHEMA:
- OneCallReply: Pydantic model for structured JSON output
- OneCallResult: Processed result with validation
- validate_one_call_response(): 5-layer validation chain
- _compute_safety_flags(): Deterministic safety detection
- _compute_quality_heuristics(): Deterministic quality scoring

SAFETY:
- PPV authority preserved (deterministic engine unchanged)
- Persona enforcement preserved (behavior block + validation)
- Handoff safety preserved (safety flags + quality threshold)

NEXT PHASE:
75C — Deterministic context compaction
```
