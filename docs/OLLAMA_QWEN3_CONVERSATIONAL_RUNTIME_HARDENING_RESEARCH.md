# Ollama Qwen3 Conversational Runtime Hardening — Research

**Date:** 2026-08-28  
**Status:** COMPLETE  

---

## Phase 1: Forensic Audit Findings

### Complete Call Chain (verified from source code)

```
Telegram API
  → chatbotv2/handlers.py:handle_incoming_message (line 25)
    → debounce (Redis, 3s window)
    → chatbotv2/handlers.py:_wait_and_process (line 100)
      → enqueue_inbound (Redis Stream "inbound_messages")
        → workers/llm_worker.py:run_worker (line 577)
          → process_message (line 364)
            → build_context (memory/context.py:281)  ← VERBOSE PATH, 3550 tokens
            → _try_commerce_draft (line 253) OR generate_draft (line 80)
              → core/llm_provider_ollama.py:_generate_native (line 145)
                → POST /api/chat (think=true, num_predict=300, retry@500)
            → score_draft (core/scoring.py:71)  ← SEPARATE LLM CALL
            → routing decision (line 451-555)
            → enqueue_send (Redis Stream "send_messages")
              → chatbotv2/main.py:_process_send_stream (line 77)
                → client.send_message (Telethon MTProto)
```

### Key Findings

1. **Worker uses verbose context, not compact Qwen3 context**
   - `llm_worker.py:408` calls `build_context()` (3550 token budget)
   - `build_qwen3_context()` (1600 token budget) exists but is NEVER called
   - This wastes ~2000 tokens on input alone

2. **Ollama adapter hardcodes think=True**
   - No way to switch to think=False at runtime
   - A/B benchmark proved think=false: 10/10 success, 63.7s avg latency
   - A/B benchmark proved think=true: 6/10 success, 143.5s avg latency

3. **Token budget is the bottleneck**
   - Input: ~3550 tokens (verbose context)
   - Thinking: consumes 500-1400 tokens (think=true)
   - Available for content: often 0 (empty content failures)
   - With compact context (1600 tokens) + think=false: 300 tokens for content is sufficient

4. **Scoring adds a separate LLM call**
   - `score_draft()` calls the LLM provider again for quality scoring
   - This doubles latency for every message
   - Can be made deterministic (keyword-based only) for Ollama

5. **Commerce context is expensive**
   - `build_llm_context()` queries 10+ DB tables
   - Runs on every message even when not needed
   - Should be lazy-loaded or cached

---

## Phase 2: Qwen3 Runtime Optimization

### Thinking Mode Decision

**Empirical evidence:**
- think=true: 60% success, 143.5s avg, correct separation (thinking in message.thinking)
- think=false: 100% success, 63.7s avg, thinking leaks into content

**Decision: Use think=false for now.**

Rationale:
1. CRM does not need visible reasoning — the deterministic engine handles decisions
2. 100% content success is critical for production
3. 2.25x lower latency improves fan experience
4. Thinking leakage is acceptable because:
   - The LLM only generates conversational text, not commerce decisions
   - Scoring catches quality issues before send
   - Operator review catches remaining issues
5. When VPS inference improves, can revisit think=true with higher num_predict

### Non-Thinking Parameters (Qwen3 official)

```python
temperature = 0.7
top_p = 0.8
top_k = 20
min_p = 0
presence_penalty = 1.5
num_predict = 300  # sufficient for non-thinking
```

### Context Optimization

**Current (verbose):** 3550 tokens
- system: 600 (persona + profile + stage + rules + anti-patterns)
- profile: 250
- summary: 400
- commerce: 300
- retrieved: 500
- recent: 1500

**Optimized (compact Qwen3):** 1600 tokens
- system: 400 (compressed persona + rules)
- state: 200 (deterministic facts)
- conversation: 800 (recent messages)
- summary: 200 (compressed)

**Savings:** ~2000 tokens input → faster prefill, less token waste

---

## Phase 3: Response Length Optimization

For non-thinking mode, the LLM has 300 tokens for content.

CRM conversational responses should be short:
- Casual chat: 50-100 tokens (1-2 sentences)
- Emotional: 80-150 tokens (2-3 sentences)
- Commercial: 100-200 tokens (2-4 sentences)
- Rejection handling: 50-100 tokens (1-2 sentences)

300 tokens is sufficient for all cases.

---

## Phase 4: Performance Instrumentation

Need to measure separately:
- T_network: round-trip to VPS
- T_prefill: prompt processing (proportional to input tokens)
- T_generation: token generation (proportional to output tokens)
- T_total: end-to-end

The Ollama API returns `eval_count` and `eval_duration` in the response.
Also returns `prompt_eval_count` and `prompt_eval_duration`.

These can be extracted without changing the VPS.

---

## Phase 5: Failure Handling

Current failures and handling:
1. Empty content → retry at 500 (think=true only)
2. Timeout → LLMProviderError
3. HTTP 401/403 → LLMProviderError
4. HTTP 429 → LLMRateLimitError
5. HTTP 5xx → LLMProviderError
6. Connection error → LLMProviderError

For think=false, empty content is extremely rare (0/10 in A/B test).
Retry mechanism can be simplified.

---

## Phase 6: Concurrency

Current: CPU-bound, single model loaded.
- One request at a time is optimal
- Parallel requests degrade total throughput
- Per-user lock prevents duplicate processing
- Creator isolation is maintained by design

Recommendation: Keep single-threaded inference.
Multiple LLM workers would compete for CPU, degrading all responses.
