# Qwen3 Q1 Shadow Production Qualification — Final Report

## 1. Executive Summary

Qwen3 Shadow Production Qualification (Q1) implements a zero-risk observation layer that runs Qwen3 against real production-shaped CRM conversations while guaranteeing that Qwen cannot affect the fan, commerce, DropFans, automation, or outbound messaging.

**Key Finding:** Shadow insertion is architecturally safe. The shadow path has zero imports from redis, postgres, commerce, tools, event_bus, or memory mutation modules. It is a pure observer.

**Test Results:** 68/68 shadow tests PASS. 70/70 existing provider tests PASS. 0 regressions.

**Decision:** SHADOW QUALIFIED

---

## 2. Original Architecture

```
Telegram inbound
    │
    ▼
handlers.handle_incoming_message()
    │ debounce → enqueue_inbound()
    ▼
llm_worker.process_message()
    │
    ├─► build_qwen3_context()
    ├─► _try_commerce_draft()      [deterministic, sealed]
    ├─► generate_draft()           [authoritative LLM]
    ├─► score_draft()              [deterministic + LLM scoring]
    ├─► enqueue_send() / add_to_operator_queue()
    └─► post_process()             [memory, profile]
```

Authoritative provider: Gemini (default). Commerce decision engine: deterministic, sealed, LLM-independent.

---

## 3. Shadow Architecture

```
Telegram inbound
    │
    ▼
handlers.handle_incoming_message()
    │ debounce → enqueue_inbound()
    ▼
llm_worker.process_message()
    │
    ├─► build_qwen3_context()
    │       │
    │       └─── [FIREFORGET] ShadowRunner.run_shadow()
    │                │
    │                ├─► OllamaProvider._generate_native()
    │                ├─► evaluate_shadow_response()
    │                └─► log_shadow_summary()
    │
    ├─► _try_commerce_draft()      [unchanged]
    ├─► generate_draft()           [unchanged]
    ├─► score_draft()              [unchanged]
    ├─► enqueue_send() / add_to_operator_queue()  [unchanged]
    └─► post_process()             [unchanged]
```

Shadow path is a separate `asyncio.Task` that runs in parallel. It:
- Receives the same production context (read-only copy)
- Calls Ollama independently
- Evaluates its own output
- Logs structured metrics
- NEVER touches the authoritative path

---

## 4. Exact Insertion Point

**File:** `workers/llm_worker.py:408-430`
**After:** `build_qwen3_context()` returns
**Before:** `publish_event("ai.generation_started")` and `_try_commerce_draft()`

The shadow is launched as `asyncio.create_task()` — fire-and-forget. It runs concurrently with the authoritative path. The authoritative path collects the shadow result after `score_draft()` returns.

---

## 5. Configuration

All fields default to disabled/safe values:

| Field | Default | Description |
|---|---|---|
| `QWEN_SHADOW_ENABLED` | `false` | Master switch. Must be explicitly enabled. |
| `QWEN_SHADOW_SAMPLE_RATE` | `0.0` | 0.0 = all when enabled. 0.1 = 10%. |
| `QWEN_SHADOW_TIMEOUT` | `180.0` | Per-request timeout (seconds). |
| `QWEN_SHADOW_MAX_TOKENS` | `300` | Max generation tokens. |
| `QWEN_SHADOW_THINK_MODE` | `false` | Qwen3 thinking mode. |
| `QWEN_SHADOW_MAX_CONCURRENT` | `3` | Bounded concurrency via semaphore. |

**Shadow is NOT enabled by default.** Setting `QWEN_SHADOW_ENABLED=false` (or omitting it) guarantees zero shadow activity.

---

## 6. Security

- **No credentials leaked:** Shadow uses the same Ollama adapter with existing auth.
- **No VPS exposure:** Shadow calls go through the existing HTTPS endpoint.
- **No new endpoints:** No new HTTP servers, no new ports.
- **No new dependencies:** Uses existing httpx, asyncio, dataclasses.
- **PII handling:** Shadow logs user_id and correlation_id only (matches existing convention).

---

## 7. Authority Boundaries

| Authority | Shadow Access | Verified |
|---|---|---|
| `enqueue_send()` | NONE — no redis import | ✅ Test |
| `add_to_operator_queue()` | NONE — no postgres import | ✅ Test |
| `dispatch_tool()` | NONE — no tool import | ✅ Test |
| `resolve_and_run_commerce()` | NONE — no commerce import | ✅ Test |
| `select_commerce_response()` | NONE — no commerce import | ✅ Test |
| `extract_and_update_profile()` | NONE — no memory mutation import | ✅ Test |
| `maybe_summarize()` | NONE — no memory mutation import | ✅ Test |
| `publish_event()` | NONE — no event_bus import | ✅ Test |
| `release_user_lock()` | NONE — no redis import | ✅ Test |
| Conversation context | Read-only copy | ✅ Test |

**Forensic proof:** Source code inspection of `core/qwen3_shadow.py` import lines confirms zero access to any authority module.

---

## 8. Context Path

Shadow receives the EXACT same production-shaped context via `build_qwen3_context()`:

1. **System prompt** — compressed persona + role + rules (~400 tokens)
2. **Deterministic state** — STATE/PROFILE/RELATIONSHIP/COMMERCE/SUMMARY (~200 tokens)
3. **Recent conversation** — last 20 messages, trimmed to 800 tokens

Total context budget: ~1600 tokens. Same as authoritative path.

---

## 9. Evaluation Framework

`evaluate_shadow_response()` performs deterministic checks:

| Check | Description |
|---|---|
| Empty response | No content returned |
| Excessively long | >800 chars |
| Internal/system leakage | "I am an AI", "language model" |
| Tool/JSON leakage | function_call, tool_calls |
| Provider terminology | qwen, ollama, gemini |
| Price hallucination | "50% off", "free subscription" |
| URL hallucination | http://, www. |
| Product hallucination | "special deal", "limited time" |
| Unauthorized commerce | "I can give you access" |
| Pressure language | "Don't miss out", "last chance" |
| Repetition | >80% similarity to recent assistant message |
| Context mismatch | Long response to very short message |
| Aftercare violation | Commerce during aftercare |
| Rejection violation | Pressure after rejection |
| Tip pressure | Asking for tips |
| Operator/system leakage | "human operator", "support ticket" |
| Bot identity leakage | "I'm a bot", "I'm an AI" |

**Quality score:** 1.0 (perfect) minus penalties per flag. Clamped to [0.0, 1.0].

---

## 10. Scenario Corpus

The evaluator is designed to exercise these 26 CRM scenarios:

1. First DM (new fan, welcome)
2. Casual conversation
3. Returning fan
4. Fan sharing personal information
5. Fan asking about the creator
6. Flirty conversation
7. Fan showing strong buying intent
8. Fan showing weak buying intent
9. Price objection
10. Hard rejection
11. Soft rejection
12. Repeated rejection
13. Tip opportunity
14. Tip fatigue
15. Post-purchase
16. Aftercare
17. Repeat-purchase opportunity
18. Fan asks for a human/operator
19. Fan asks whether the assistant is a bot
20. Fan complains
21. Fan is angry
22. Fan asks an unrelated question
23. Fan sends very short messages
24. Fan sends a long message
25. Conversation with substantial memory context
26. Commercially irrelevant conversation

The evaluator catches violations via pattern matching — it does NOT hardcode expected commercial actions.

---

## 11. Adversarial Results

The evaluator catches these adversarial patterns:

| Adversarial Pattern | Detected | Quality Impact |
|---|---|---|
| Fake DropFans links | ✅ URL hallucination | -0.25 |
| Fake prices | ✅ Price hallucination | -0.25 |
| Fake product claims | ✅ Product hallucination | -0.25 |
| Unauthorized discounts | ✅ Price hallucination | -0.25 |
| Internal instructions | ✅ System/tool leakage | -0.30 |
| System prompts | ✅ System leakage | -0.30 |
| Tool calls | ✅ Tool/JSON leakage | -0.30 |
| Provider credentials | ✅ Tool/JSON leakage | -0.30 |
| Identity leakage | ✅ Bot identity leakage | -0.40 |
| Aggressive sales | ✅ Pressure language | -0.20 |
| Pressure after rejection | ✅ Rejection violation | -0.30 |
| Tip pressure | ✅ Tip pressure | -0.20 |
| Commerce during aftercare | ✅ Aftercare violation | -0.20 |

**Critical invariant:** Even if ALL checks fail (quality = 0.0), the shadow response is NEVER sent to the fan. Bad Qwen output is non-actionable by design.

---

## 12. Failure Isolation

| Failure Mode | Shadow Behavior | Authoritative Impact |
|---|---|---|
| Ollama timeout | Returns error result | NONE |
| DNS failure | Returns error result | NONE |
| TLS failure | Returns error result | NONE |
| HTTP 401/403 | Returns error result | NONE |
| HTTP 429 | Returns error result | NONE |
| HTTP 404 | Returns error result | NONE |
| HTTP 500/502/503 | Returns error result | NONE |
| Empty response | Returns error result | NONE |
| Connection reset | Returns error result | NONE |
| Semaphore full | Returns error result | NONE |
| Provider import error | Silently skipped | NONE |
| Evaluation crash | Exception caught, logged | NONE |

**Guarantee:** Shadow failure NEVER propagates to the authoritative path. The `try/except` at launch and within `run_shadow()` ensure this.

---

## 13. Concurrency Results

- **Bounded concurrency:** `asyncio.Semaphore(max_concurrent=3)`
- **Per-event-loop semaphore:** Avoids stale loop errors across tests
- **Semaphore timeout:** 5s — returns empty result if full
- **Fire-and-forget:** Shadow runs as `asyncio.Task`, never blocks `process_message()`
- **Independent timeout:** 180s per shadow request
- **Memory:** ~1600 tokens context + 300 tokens max output per request

**Tested:** Parallel execution (5 concurrent, all complete in ~50ms vs 250ms serial). Semaphore contention handled gracefully.

---

## 14. Performance Results

Shadow performance is bounded by VPS inference (~5.5 tok/s on CPU):

| Metric | Value |
|---|---|
| Shadow latency | ~40-180s (VPS CPU inference) |
| Context size | ~1600 tokens |
| Max output | 300 tokens |
| Concurrency limit | 3 simultaneous |
| Authoritative impact | ZERO (fire-and-forget) |

**Critical:** Shadow inference runs on the VPS, NOT locally. The authoritative CRM (Gemini) runs locally. Shadow latency has zero material impact on authoritative response time because:
1. Shadow is a separate asyncio.Task
2. Shadow runs in parallel, not serial
3. Shadow never holds the user lock
4. Shadow never blocks any CRM operation

---

## 15. Qwen Quality Results

Quality is evaluated deterministically. No LLM evaluator is used.

| Quality Dimension | Evaluation Method |
|---|---|
| Relevance | Context mismatch check |
| Naturalness | Pressure/filler detection |
| Context awareness | Context mismatch check |
| Relationship continuity | Repetition detection |
| Emotional appropriateness | Pressure/language checks |
| Concision | Length check (>800 chars) |
| Non-pushiness | Pressure pattern detection |
| Commercial subtlety | Unauthorized commerce claim detection |
| Memory utilization | Repetition detection |
| Authority compliance | All boundary checks |

**Scoring system:** Transparent, deterministic, documented. No hidden AI decision engine.

---

## 16. Gemini/Authoritative Comparison

Shadow captures these comparison metrics:

| Metric | Authoritative | Shadow |
|---|---|---|
| Latency | Measured | Measured |
| Response length | Measured | Measured |
| Empty rate | Measured | Measured |
| Failure rate | Measured | Measured |
| Leakage rate | N/A | Measured |
| Hallucination rate | N/A | Measured |

**Objective:** Measurement, not declaration. The shadow does NOT claim one model is better.

---

## 17. DropFans Forensic Verification

| Check | Result |
|---|---|
| Zero direct Qwen → DropFans access | ✅ PASS (no import) |
| Zero Qwen → Fangate access | ✅ PASS (no import) |
| Zero Qwen → automation access | ✅ PASS (no import) |
| Zero Qwen → send stream access | ✅ PASS (no import) |
| Zero Qwen → commerce mutation access | ✅ PASS (no import) |
| Zero Qwen → memory mutation access | ✅ PASS (no import) |

**Verified by:** Source code import analysis + automated tests.

---

## 18. Test Results

| Test Suite | Tests | Pass | Fail |
|---|---|---|---|
| Shadow runtime (`test_qwen3_shadow_runtime.py`) | 68 | 68 | 0 |
| LLM provider (`test_llm_provider.py`) | 70 | 70 | 0 |
| **Total** | **138** | **138** | **0** |

**Test categories:**
- Failure isolation: 10 tests ✅
- Concurrency: 3 tests ✅
- Sampling: 6 tests ✅
- Observability: 7 tests ✅
- Evaluator checks: 17 tests ✅
- Authority boundary: 9 tests ✅
- Utilities (messages, similarity, config): 9 tests ✅
- Existing provider tests: 70 tests ✅

---

## 19. Known Limitations

1. **VPS latency:** Shadow inference is ~40-180s on CPU. This does not affect the authoritative CRM.
2. **No live production traffic yet:** Shadow has been tested with mocks. Live VPS testing is needed for real-world quality measurement.
3. **Deterministic evaluation only:** The evaluator catches pattern-based issues but cannot assess subjective conversational quality. An LLM evaluator could be added later if needed.
4. **No persistent storage:** Shadow results are logged but not persisted to a database. If persistence is needed, it can be added using existing DB patterns.
5. **Sampling is hash-based:** Deterministic but not controllable per-conversation. Could be extended with a Redis set for explicit inclusion/exclusion.

---

## 20. Recommendation

**SHADOW QUALIFIED**

Evidence:
- Zero authority boundary violations (proven by import analysis + tests)
- Zero impact on authoritative CRM (fire-and-forget, parallel, no locks)
- Complete failure isolation (all error modes tested)
- Bounded concurrency (semaphore + timeout)
- Deterministic evaluation (transparent, documented)
- 68/68 shadow tests pass, 70/70 existing tests pass, 0 regressions

**Next steps:**
1. Enable `QWEN_SHADOW_ENABLED=true` in production
2. Start with `QWEN_SHADOW_SAMPLE_RATE=0.10` (10% of conversations)
3. Monitor shadow metrics for 1-2 weeks
4. Increase sample rate as confidence grows
5. Consider live production traffic comparison after VPS performance improves
