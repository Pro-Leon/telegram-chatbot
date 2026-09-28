# Qwen3 Q1 Shadow Field Evaluation — Final Report

## 1. Executive Summary

Q1 Shadow Field Evaluation implements a CRM-specific evaluation system to determine whether Qwen3 4B is actually useful for THIS CRM's conversational objective. The system evaluates 50+ scenarios across 16 CRM-specific dimensions, with deterministic automatic evaluators and structured shadow pair capture.

**Status: IMPLEMENTATION COMPLETE** — All components implemented, tested (317/317 tests pass), and documented. **No real-world field data exists yet** — the system is instrumented but shadow is disabled by default (`QWEN_SHADOW_ENABLED=false`).

## 2. Objective

The core question: "Would this response make the fan more likely to continue the conversation and eventually become commercially receptive, while still feeling natural and non-pushy?"

This is NOT a generic benchmark. The evaluation measures CRM-specific success: relationship → trust → natural conversation → opportunity → subtle monetization.

## 3. Current Architecture

```
Telegram inbound
      │
      ▼
existing CRM processing (debounce, context build)
      │
      ├──────────────────────┐
      │                      │
      ▼                      ▼
authoritative provider    Qwen shadow
(Gemini, current prod)         │
      │                      │
      ▼                      ▼
normal CRM decision      evaluation only
      │
      ▼
normal outbound path
```

**Key invariant:** Shadow output MUST NOT affect the fan, commerce, DropFans, automation, or outbound messaging.

## 4. Shadow Call Graph

```
process_message()
  │
  ├─ (1) build_qwen3_context()
  │       → system prompt + profile + state + commerce + recent messages
  │
  ├─ (2) ShadowRunner.run_shadow(context_messages, user_message, ...)
  │       ├─ ShadowConfig.should_sample(user_id)
  │       ├─ acquire semaphore (max 3 concurrent)
  │       ├─ _build_ollama_messages(context_messages, user_message)
  │       ├─ OllamaProvider._generate_native(ollama_messages, num_predict=300)
  │       └─ return ShadowResult
  │
  ├─ (3) Gemini generate_draft() / generate_draft_with_tools()
  │
  ├─ (4) score_draft()
  │
  └─ (5) Shadow result collection (if not timed out)
          ├─ set authoritative_latency_ms
          ├─ set authoritative_response_length
          ├─ evaluate_shadow_response()
          └─ log_shadow_summary()
```

## 5. Data Flow

### Gemini (Authoritative) Path
- System: `build_qwen3_system_prompt()` + `TOOL_AUTHORITY_PROMPT`
- Context: state context + commerce facts + summary + recent messages (800 tokens, max 3 assistant turns)
- Output: max_tokens=200, temperature=0.85
- Latency: full pipeline (context build + generation + scoring)

### Qwen3 (Shadow) Path
- System: same `context_messages` merged via `_build_ollama_messages()`
- Context: identical context_messages list
- Output: num_predict=300, temperature=0.7, top_p=0.8, top_k=20
- Latency: Ollama HTTP call only

### Discrepancies Found
1. **Output token disparity**: Gemini 200 vs Qwen 300 (Qwen has 50% more budget)
2. **Latency measurement**: Gemini measures full pipeline, Qwen measures HTTP only
3. **No persistent storage**: Results logged but not persisted
4. **Collection timeout**: 2s timeout means most Qwen results are dropped (avg 63.7s latency)

## 6. Scenario Corpus

**50+ scenarios across 10 categories:**

| Category | Count | Description |
|----------|-------|-------------|
| A. Casual Relationship | 6 | Greetings, daily life, emotional conversation |
| B. Memory | 5 | Context references, profile utilization |
| C. Commercial Curiosity | 5 | Content questions, price inquiries |
| D. Buying Intent | 5 | Explicit purchase requests |
| E. Rejection | 5 | Soft/hard rejection, price objection |
| F. Tip | 5 | Appreciation, tip opportunities |
| G. Aftercare | 5 | Post-purchase behavior |
| H. Repair | 4 | Misunderstandings, topic changes |
| I. Human/Handoff | 5 | Bot detection, complaint escalation |
| J. Adversarial | 5 | Prompt injection, authority attacks |

## 7. Evaluation Rubric (16 Dimensions)

| # | Dimension | Score | Description |
|---|-----------|-------|-------------|
| 1 | NATURALNESS | 0-5 | Does it sound like a real human? |
| 2 | RELATIONSHIP_BUILDING | 0-5 | Does it build genuine rapport? |
| 3 | CONVERSATIONAL_RELEVANCE | 0-5 | Does it answer what the fan said? |
| 4 | PERSONA_ADHERENCE | 0-5 | Does it sound like the configured creator? |
| 5 | COMMERCIAL_TIMING | 0-5 | Does it correctly distinguish conversation vs intent? |
| 6 | SALES_SUBTLETY | 0-5 | Is commercial language naturally introduced? |
| 7 | TIP_NATURALNESS | 0-5 | Is tip timing appropriate and subtle? |
| 8 | MEMORY_UTILIZATION | 0-5 | Does it use known facts without awkward recitation? |
| 9 | REPAIR_QUALITY | 0-5 | Does it handle misunderstandings naturally? |
| 10 | REJECTION_HANDLING | 0-5 | Does it respect rejection without pushing? |
| 11 | AFTERCARE | 0-5 | Does it avoid selling after purchase? |
| 12 | HANDOFF_BEHAVIOR | 0-5 | Does it recognize when human help is needed? |
| 13 | AUTHORITY_COMPLIANCE | PASS/FAIL | No invented prices, URLs, or products |
| 14 | LATENCY | 0-5 | Is response time acceptable? |
| 15 | FAILURE_RATE | 0-5 | Did the response succeed? |
| 16 | GEMINI_VS_QWEN_PREFERENCE | - | Which model would a human prefer? |

## 8. Automatic Evaluators

### AuthorityEvaluator (PASS/FAIL)
- Fabricated price detection
- Fabricated URL detection
- Fabricated product detection
- Unauthorized offer detection
- DropFans/Fangate detection
- Provider action detection
- Internal state leakage detection

### CommercialEvaluator (0-5)
- Premature offer detection
- Repeated offer detection
- Post-purchase selling detection
- Tip repetition detection
- Pressure language detection

### ConversationalEvaluator (0-5)
- Empty response detection
- Excessive length detection
- Repeated phrase detection
- Template repetition detection
- Irrelevant answer detection
- System leakage detection

## 9. Gemini Results

**NOT YET MEASURED** — No real-world field data exists. Shadow is disabled by default.

## 10. Qwen Results

**NOT YET MEASURED** — No real-world field data exists. Shadow is disabled by default.

## 11. Latency Comparison

**NOT YET MEASURED** — No real-world field data exists.

Known from prior benchmark:
- Gemini: ~2-5s (production)
- Qwen3: ~60-180s (CPU-only VPS, ~5.5 tok/s)

## 12. Quality Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 13. Relationship Comparison

**NOT YET MEASURED** — Requires human evaluation or carefully controlled secondary evaluator.

## 14. Commercial Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 15. Tip Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 16. Rejection Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 17. Aftercare Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 18. Memory Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 19. Repair Comparison

**NOT YET MEASURED** — Requires real-world shadow pairs.

## 20. Authority Forensics

### Invariants Verified (by test)
1. ✅ Shadow module has no Redis imports
2. ✅ Shadow module has no Postgres imports
3. ✅ Shadow module has no commerce imports
4. ✅ Shadow module has no tool imports
5. ✅ Shadow module has no event_bus imports
6. ✅ Shadow module has no memory mutation imports
7. ✅ Shadow result is read-only (no authority actions)
8. ✅ No Gemini fallback paths
9. ✅ No Qwen-to-Gemini fallback
10. ✅ Authority evaluator detects all violation types

### Authority Violation Types Detected
| Type | Pattern |
|------|---------|
| fabricated_price | `\$\d+` |
| fabricated_url | `https?://[^\s]+` |
| fabricated_product | `discount\|special offer\|limited time` |
| unauthorized_offer | `\d{1,3}%\s*off` |
| unauthorized_dropfans | `dropfans\.io\|fangate\.info` |
| commerce_override | `I (have\|can\|will) (give\|send\|provide\|offer)` |
| provider_action | `tool_call\|function_call\|tool_calls` |
| internal_state_leakage | `system prompt\|api[_\-]?key\|webhook` |

## 21. Failure Analysis

### Failure Classification Types (10)
| Type | Description |
|------|-------------|
| empty_response | No content returned |
| timeout | Request timed out |
| auth_failure | Authentication failed (401/403) |
| rate_limit | Rate limited (429) |
| model_unavailable | Model not found (404) |
| connection_error | Network connection failure |
| content_policy | Content policy violation |
| hallucination | Fabricated information |
| authority_violation | Boundary violation detected |
| quality_failure | Quality score below threshold |

## 22. Infrastructure Limitations

1. **CPU-only VPS**: Qwen3 runs at ~5.5 tok/s, making full benchmarks take hours
2. **No GPU**: Cannot achieve production-competitive latency
3. **Single model**: Only qwen3:4b available, no larger models for comparison
4. **Remote Ollama**: Additional network latency vs local inference

## 23. Sample-Size Limitations

**FIELD SAMPLE INSUFFICIENT**

No real-world shadow pairs have been collected. The system is instrumented and ready, but:
- Shadow is disabled by default (`QWEN_SHADOW_ENABLED=false`)
- No production traffic has been shadow-evaluated
- All evaluation results are from synthetic test scenarios

## 24. Statistical Limitations

- Cannot draw statistical conclusions without real-world data
- Synthetic scenarios may not reflect actual fan behavior
- Human evaluation requirements not yet fulfilled
- Preference judgments require human review

## 25. Human-Review Requirements

The following dimensions REQUIRE human evaluation:
- NATURALNESS (0-5)
- RELATIONSHIP_BUILDING (0-5)
- PERSONA_ADHERENCE (0-5)
- GEMINI_VS_QWEN_PREFERENCE

Automatic heuristics cannot perfectly judge human-likeness and relationship quality.

## 26. Production-Readiness Assessment

### Ready
- ✅ Evaluation framework implemented
- ✅ 50+ scenario corpus built
- ✅ 16-dimension rubric defined
- ✅ Automatic evaluators functional
- ✅ Authority compliance verified
- ✅ 317 tests passing
- ✅ Shadow safety invariants enforced

### Not Ready
- ❌ No real-world field data
- ❌ No human evaluation completed
- ❌ No statistical significance
- ❌ Qwen3 latency not competitive (60-180s vs 2-5s)
- ❌ No persistent shadow result storage

## 27. Recommended Next Phase

1. **Enable shadow sampling** at low rate (0.01 = 1%) for real-world data collection
2. **Add persistent storage** for shadow results (Postgres table or Redis)
3. **Run live shadow evaluation** for 2-4 weeks to collect meaningful sample
4. **Human evaluation** of collected shadow pairs
5. **Statistical analysis** of collected data
6. **If Qwen3 shows promise**: Consider GPU upgrade for competitive latency
7. **If Qwen3 does not show promise**: Document findings and close Q1

## Files Created

| File | Lines | Description |
|------|-------|-------------|
| `core/qwen3_field_evaluation.py` | ~900 | Field evaluation module (rubric, evaluators, scenarios) |
| `tests/test_qwen3_field_evaluation.py` | ~600 | 67 tests for field evaluation |
| `docs/QWEN3_Q1_SHADOW_FIELD_EVALUATION_FINAL_REPORT.md` | This file | Final report |

## Files Modified

| File | Change |
|------|--------|
| `core/qwen3_q1_intelligence.py` | Added FailureType, classify_failure(), LatencyStats, compute_latency_stats(), BatchComparisonResult |

## Test Results

| Test Suite | Count | Status |
|------------|-------|--------|
| test_qwen3_field_evaluation.py | 67 | ✅ ALL PASS |
| test_qwen3_q1_intelligence.py | 112 | ✅ ALL PASS |
| test_qwen3_shadow_runtime.py | 68 | ✅ ALL PASS |
| test_llm_provider.py | 70 | ✅ ALL PASS |
| **Total** | **317** | **✅ ALL PASS** |

## Forensic Findings

1. **No hidden fallback paths**: Qwen failure does not trigger Gemini fallback
2. **No DropFans access**: Shadow module has no DropFans imports
3. **No Fangate paths**: Shadow module has no Fangate imports
4. **No credential leakage**: Ollama credentials not exposed in shadow
5. **No unbounded concurrency**: Semaphore limits parallel requests to 3
6. **No raw conversation logging**: Only structured ShadowResult logged
7. **Creator isolation enforced**: Shadow results scoped to creator_id

## Remaining Limitations

1. **No real-world data**: All results are synthetic
2. **No human evaluation**: Automatic heuristics only
3. **Latency gap**: Qwen3 at 60-180s vs Gemini at 2-5s
4. **Output token disparity**: Gemini 200 vs Qwen 300
5. **No persistent storage**: Results only in logs

## Conclusion

The Q1 Shadow Field Evaluation system is fully implemented with:
- 50+ CRM scenarios across 10 categories
- 16-dimension evaluation rubric
- 3 automatic evaluators (authority, commercial, conversational)
- Structured shadow pair capture
- Gemini vs Qwen comparison infrastructure
- Latency analysis with P50/P95/P99
- 317 passing tests

**However, no real-world field data exists.** The system is instrumented and ready for production shadow evaluation when enabled. The actual determination of whether Qwen3 is useful for THIS CRM requires collecting real shadow pairs and human evaluation.
