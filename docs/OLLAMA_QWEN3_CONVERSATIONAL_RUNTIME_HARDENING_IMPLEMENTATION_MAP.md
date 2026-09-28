# Ollama Qwen3 Conversational Runtime Hardening — Implementation Map

**Date:** 2026-08-28  
**Status:** IN PROGRESS  

---

## Priority Classification

### P0 — Must Fix (Production Blockers)

| # | Issue | File | Fix | Test |
|---|-------|------|-----|------|
| 1 | Worker uses verbose context (3550 tokens) instead of compact (1600) | `workers/llm_worker.py:408` | Switch to `build_qwen3_context()` | Unit test: context token count |
| 2 | Ollama adapter hardcodes think=True | `core/llm_provider_ollama.py:134` | Add think_mode parameter, default False | Unit test: payload inspection |
| 3 | Empty content retry only works for think=True | `core/llm_provider_ollama.py:177` | Simplify for think=False (no retry needed) | Unit test: retry behavior |

### P1 — Should Fix (Performance)

| # | Issue | File | Fix | Test |
|---|-------|------|-----|------|
| 4 | No timing instrumentation | `core/llm_provider_ollama.py` | Extract eval_count/eval_duration from response | Unit test: timing fields present |
| 5 | Scoring uses separate LLM call | `core/scoring.py:88` | Make scoring deterministic for Ollama (keyword-only) | Unit test: scoring without LLM |
| 6 | Commerce context loaded on every message | `memory/context.py:316` | Lazy-load with failure isolation (already done) | Verify existing isolation |

### P2 — Nice to Have (Quality)

| # | Issue | File | Fix | Test |
|---|-------|------|-----|------|
| 7 | No thinking leakage detection | `core/llm_provider_ollama.py` | Add content validation for think=True mode | Unit test: leakage detection |
| 8 | No response length validation | `core/llm_provider_ollama.py` | Add max content length check | Unit test: length validation |

### P3 — Future (VPS-dependent)

| # | Issue | File | Fix | Test |
|---|-------|------|-----|------|
| 9 | think=true unreliable at current VPS speed | N/A | Revisit when VPS inference >10 tok/s | Live benchmark |
| 10 | Dynamic thinking mode routing | `core/llm_provider_ollama.py` | Route by message complexity | A/B benchmark |

---

## Implementation Plan

### Step 1: Add think_mode parameter to Ollama adapter

**File:** `core/llm_provider_ollama.py`

**Changes:**
- Add `_think_mode: bool = False` to `__init__`
- Update `_build_payload` to use `self._think_mode`
- Simplify `_generate_native` retry logic (no retry needed for think=False)
- Add timing extraction from response
- Add thinking leakage detection for think=True mode

**Invariants preserved:**
- No fallback to Gemini
- No change to Gemini adapter
- Credentials never logged
- Empty content still raises LLMProviderError

### Step 2: Switch worker to compact Qwen3 context

**File:** `workers/llm_worker.py`

**Changes:**
- Line 408: Import and call `build_qwen3_context()` instead of `build_context()`
- This reduces input tokens from ~3550 to ~1600

**Invariants preserved:**
- Same DB queries (user, profile, summary, recent messages)
- Same commerce context (failure-isolated)
- Same retrieval triggers
- Same token budget enforcement

### Step 3: Add timing instrumentation

**File:** `core/llm_provider_ollama.py`

**Changes:**
- Extract `prompt_eval_count`, `prompt_eval_duration`, `eval_count`, `eval_duration` from response
- Return as part of a richer response object (or log them)
- This enables measuring T_prefill vs T_generation separately

**Invariants preserved:**
- No change to response content
- No change to error handling

### Step 4: Add deterministic scoring for Ollama

**File:** `core/scoring.py`

**Changes:**
- When provider is Ollama, skip LLM scoring call
- Use keyword-based scoring only (hard flags + basic heuristics)
- This eliminates one LLM call per message

**Invariants preserved:**
- Hard flags still detected
- Score range still 0.0-1.0
- Operator review still catches quality issues

### Step 5: Tests

**New test file:** `tests/test_qwen3_runtime_hardening.py`

Tests:
1. Ollama adapter uses think=False by default
2. Ollama adapter respects think_mode parameter
3. Compact context builder produces fewer tokens
4. Worker calls compact context builder
5. Timing fields extracted from response
6. Deterministic scoring works without LLM
7. Empty content handling for think=False
8. Thinking leakage detection for think=True
