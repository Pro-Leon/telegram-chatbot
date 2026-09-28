# Phase 75E: Deterministic Scoring Replacement

**Date:** September 2, 2026  
**Status:** COMPLETE  
**Worker:** `core/scoring_deterministic.py` (new file)  
**Tests:** `tests/test_phase75e_scoring_deterministic.py` (19 tests, all pass)

---

## 1. Executive Summary

Phase 75E implements deterministic scoring replacement for LLM #3 (`score_draft`). The module provides quality scoring without LLM involvement, using heuristics based on:

1. Length heuristics (appropriate_length)
2. Formality detection (too_formal)
3. Generic pattern detection (too_generic)
4. Repetition detection (repetitive)

**Key achievement:** LLM #3 is now optional. Quality scoring can be done deterministically.

---

## 2. Design Principles

### Deterministic Scoring
- No LLM involvement for quality assessment
- Based on measurable text properties
- Consistent and reproducible
- Fast (no network RTT)

### Fail-Safe
- Low quality → operator queue
- Safety flags → operator queue
- Scoring failure → operator queue

### Preserved Safety
- Safety flags remain from `core/scoring.py` (FLAG_KEYWORDS)
- Authorized commerce price exemptions preserved
- Hard flags still route to operator queue

---

## 3. Module Structure

### score_draft_deterministic()
Quality scoring based on heuristics:
```python
score, flags = score_draft_deterministic(
    draft="Hey! How's your day?",
    user_message="Hello!",
)
```

### compute_safety_flags()
Safety flag detection from keywords:
```python
safety_flags = compute_safety_flags(
    draft="The price is $50",
    is_authorized_commerce=True,
    authorized_price_minor=5000,
)
```

### validate_draft_quality()
Combined quality + safety validation:
```python
is_approved, score, quality_flags, safety_flags = validate_draft_quality(
    draft="Hey! How's your day?",
    user_message="Hello!",
)
```

---

## 4. Quality Scoring Heuristics

### Length Check (0-10)
| Word Count | Score | Flags |
|------------|-------|-------|
| 10-100 | 10.0 | — |
| 5-9 or 101-150 | 7.0 | — |
| 3-4 or 151-200 | 5.0 | — |
| 2 | 3.0 | too_generic |
| 0-1 | 1.0 | too_generic |

### Formality Check (0-10)
| Formal Indicators | Score | Flags |
|-------------------|-------|-------|
| 0 | 9.0 | — |
| 1-2 | 6.0 | too_formal |
| 3+ | 3.0 | too_formal |

### Generic Pattern Check (0-10)
| Generic Patterns | Score | Flags |
|------------------|-------|-------|
| 0 | 9.0 | — |
| 1 | 6.0 | too_generic |
| 2+ | 3.0 | too_generic |

### Repetition Check (0-10)
| Unique Ratio | Score | Flags |
|--------------|-------|-------|
| ≥70% | 10.0 | — |
| 50-70% | 6.0 | repetitive |
| <50% | 3.0 | repetitive |

### Composite Score
- Average of 4 scores / 10 → 0.0-1.0
- Override: Replies < 5 words capped at 0.5

---

## 5. Safety Flags

### Hard Flags (from core/scoring.py)
- `price_mention`: Price keywords (authorized commerce exempt)
- `personal_info_request`: Personal info keywords
- `distress_signal`: Distress keywords
- `legal_mention`: Legal keywords
- `photo_promise`: Photo promise keywords

### Authorized Commerce Exemption
- If `is_authorized_commerce=True` and price matches authorized price → not flagged
- Tolerance: $0.005

---

## 6. Approval Decision

### Auto-Approve Conditions
1. Quality score ≥ 0.80
2. No safety flags
3. No critical quality flags (too_formal, too_generic, repetitive)

### Operator Queue Conditions
- Quality score < 0.80
- Any safety flag
- Any critical quality flag
- Scoring failure

---

## 7. Comparison: LLM vs Deterministic

| Aspect | LLM #3 | Deterministic |
|--------|--------|---------------|
| Network RTT | Yes | No |
| Consistency | Variable | Deterministic |
| Cost | Token usage | Free |
| Speed | ~200-500ms | <1ms |
| Nuance | Higher | Lower |
| Safety flags | Same | Same |

---

## 8. Test Coverage

19 tests covering:
- Quality scoring (7 tests)
- Safety flag detection (6 tests)
- Combined validation (6 tests)

**All tests pass.**

---

## 9. Integration Points

### With Phase 75B (One-Call Contract)
```python
from core.scoring_deterministic import validate_draft_quality

is_approved, score, quality_flags, safety_flags = validate_draft_quality(
    draft=result.reply,
    user_message=user_message,
)
```

### With Phase 75F (One-Call Pipeline)
```python
from core.scoring_deterministic import score_draft_deterministic, compute_safety_flags

# Quality scoring
quality_score, quality_flags = score_draft_deterministic(result.reply, user_message)

# Safety flags
safety_flags = compute_safety_flags(result.reply)

# Routing decision
if quality_score >= 0.80 and not safety_flags:
    # Auto-approve
    pass
else:
    # Operator queue
    pass
```

### With Existing Pipeline (Fallback)
```python
# Can replace LLM scoring in existing pipeline
from core.scoring_deterministic import score_draft_deterministic

# Instead of: score, flags = await score_draft(draft, user_message, context)
score, flags = score_draft_deterministic(draft, user_message)
```

---

## 10. Files Modified/Created

### Created
- `core/scoring_deterministic.py` — Deterministic scoring module
- `tests/test_phase75e_scoring_deterministic.py` — 19 regression tests
- `docs/AI_NATIVE_LLM_PHASE_75E_SCORING_REPLACEMENT.md` — This report

### Modified
- None

---

## 11. Next Steps

1. **Phase 75F:** Qwen2.5 one-call pipeline implementation
2. **Phase 75G:** Benchmark / shadow comparison
3. **Phase 75H:** Controlled activation (feature flag)

---

## PHASE 75E VERDICT

```
STATUS: COMPLETE

FILES CREATED:
- core/scoring_deterministic.py (200 lines)
- tests/test_phase75e_scoring_deterministic.py (200 lines)
- docs/AI_NATIVE_LLM_PHASE_75E_SCORING_REPLACEMENT.md (this file)

TESTS: 19/19 pass

SCORING:
- Quality: Length, formality, generic patterns, repetition
- Safety: Keyword detection (from core/scoring.py)
- Approval: Score ≥ 0.80 + no flags

BENEFITS:
- No LLM involvement for scoring
- Deterministic and reproducible
- Fast (<1ms vs 200-500ms)
- Free (no token usage)

SAFETY:
- Safety flags preserved from core/scoring.py
- Authorized commerce exemptions preserved
- Fail-safe to operator queue

NEXT PHASE:
75F — Qwen2.5 one-call pipeline implementation
```
