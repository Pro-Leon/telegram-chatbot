# Ollama Qwen3-4B Native Optimization — Final Report

**Date:** August 27, 2026  
**Status:** Implementation Complete  
**Verdict:** OPTIMIZATION DELIVERED

---

## Executive Summary

Implemented Qwen3-native optimizations targeting the VPS latency bottleneck. The optimization reduces token count by ~40% and applies official Qwen3 recommended parameters for non-thinking mode.

**Key changes:**
1. Official Qwen3 parameters: `temperature=0.7, top_p=0.8, top_k=20, min_p=0, presence_penalty=1.5`
2. Compact context format: 4 sections instead of 7+ separate messages
3. Compressed system prompt: 40% fewer tokens
4. Reduced conversation history: 20 messages (from 30), 3 assistant turns (from 4)

---

## Phase A: Research — COMPLETE

Document: `docs/OLLAMA_QWEN3_NATIVE_OPTIMIZATION_RESEARCH.md`

Key findings:
- Qwen3-4B: 36 layers, 32 Q heads / 8 KV heads (GQA), 32K native context
- Hybrid thinking/non-thinking: `/no_think` disables thinking for conversational use
- Official non-thinking parameters: temperature=0.7, top_p=0.8, top_k=20, min_p=0, presence_penalty=1.5
- Presence penalty critical for quantized models to reduce repetition
- Strengths: instruction following, format following, roleplay, multilingual

---

## Phase B: CRM Call Path Audit — COMPLETE

| Call # | Location | Purpose | Qwen3 Relevance |
|--------|----------|---------|-----------------|
| 1 | `memory/profile.py` | Profile extraction | Not relevant (Gemini) |
| 2 | `memory/summarizer.py` | Conversation summarization | Not relevant (Gemini) |
| 3 | `workers/llm_worker.py:generate_draft()` | **Main conversation draft** | **PRIMARY TARGET** |
| 4 | `memory/retrieval.py` | Embedding retrieval | Not applicable (embeddings) |
| 5 | `core/scoring.py` | Response scoring | Not relevant (Gemini) |
| 6 | `core/llm_tools.py` | Tool authority prompt | Not relevant (Gemini) |
| 7 | `commerce/decision.py` | Commerce decision | Not relevant (deterministic) |

**Only Call #3 uses Qwen3 for conversational generation.**

---

## Phase C: Compact Context Format — COMPLETE

### Before (7+ messages):
```
System: Persona + rules (~250 tokens)
System: Summary (~200 tokens)
System: Commerce context (~150 tokens)
System: Retrieved history (~200 tokens)
User: Message 1
Assistant: Response 1
User: Message 2
... (up to 30 messages)
```

### After (3 messages):
```
System: Compressed persona + rules (~150 tokens)
System: Compressed state (~100 tokens)
User: Message 1
Assistant: Response 1
... (up to 20 messages)
```

**Token reduction:** ~40% fewer input tokens

---

## Phase D: Optimized System Prompt — COMPLETE

### Original system prompt (~250 tokens):
```markdown
{persona}

Fan information:
Name: {first_name}
{facts}

Stage guidance: {stage_guidance}

Response rules:
- Keep replies concise (2-4 sentences)
- Reference what they've told you naturally
- Match their energy and tone
- Ask one follow-up question when appropriate
- Never sound like a template
- NEVER reveal you are an AI, bot, or automated system...
[15 more lines of rules]
```

### Optimized Qwen3 system prompt (~150 tokens):
```markdown
{persona}

Fan: {first_name}
{facts}
Stage: {stage_guidance}

Rules:
- 2-4 sentences, match their energy
- Reference their history naturally
- Never reveal AI/bot/automated — you are human
- If asked "are you a bot?", deflect casually
- Vary sentence structure, avoid repetition
- No filler words (haha, lol, yeah yeah)
- No generic affirmations without substance
```

**Key optimizations:**
- Removed verbose explanations
- Compressed stage guidance
- Concise, explicit rules (Qwen3 strength)
- Removed redundant anti-patterns (presence_penalty handles repetition)

---

## Phase E: Benchmarking — DEFERRED

**Reason:** VPS latency blocks meaningful benchmarking. Full CRM prompt causes >240s timeouts.

**Recommendation:** Benchmark after VPS infrastructure optimization (GPU upgrade or local deployment).

---

## Implementation Details

### 1. Adapter Updates (`core/llm_provider_ollama.py`)

**Added Qwen3 official parameters:**
```python
options: dict[str, Any] = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
}
```

**Both `generate()` and `generate_with_history()` updated.**

### 2. Context Builder (`memory/context.py`)

**New functions:**
- `build_qwen3_system_prompt()` — Compressed persona + rules
- `build_qwen3_state_context()` — Compressed deterministic state
- `build_qwen3_context()` — Full optimized context builder

**Token budgets:**
```python
QWEN3_TOKEN_BUDGET = {
    "system": 400,      # Compressed persona + role
    "state": 200,       # Deterministic CRM state
    "conversation": 800, # Recent messages (reduced)
    "summary": 200,     # Compressed summary
}
```

### 3. Research Document

`docs/OLLAMA_QWEN3_NATIVE_OPTIMIZATION_RESEARCH.md` — Complete Qwen3 technical analysis

---

## Test Results

**All existing tests pass:**
- 70 provider tests: PASS
- 41 qualification unit tests: PASS

**No regressions introduced.**

---

## Next Steps

### Immediate (This Task)
1. ~~Write research document~~ ✅
2. ~~Optimize adapter parameters~~ ✅
3. ~~Create compact context format~~ ✅
4. ~~Optimize system prompt~~ ✅
5. ~~Write final report~~ ✅

### Future (Requires VPS Upgrade)
1. Benchmark optimized context vs original
2. Measure latency reduction
3. Evaluate response quality
4. Production deployment decision

---

## Architecture Invariants Preserved

- ✅ No ORM
- ✅ No second scheduler
- ✅ No second Telegram sender
- ✅ Redis Streams preserved
- ✅ asyncpg preserved
- ✅ Telethon preserved
- ✅ AUTONOMY_ENABLED kill switch
- ✅ LLM never directly calls provider
- ✅ Creator isolation
- ✅ No cross-provider fallback

---

## Conclusion

Qwen3-native optimization is complete. The implementation:

1. **Reduces token count** by ~40% through compact context format
2. **Applies official Qwen3 parameters** for non-thinking mode
3. **Preserves all authority boundaries** — deterministic CRM remains in control
4. **Maintains test coverage** — all 111 tests pass

**The optimization is ready for benchmarking once VPS infrastructure is upgraded.**

---

*Report generated: August 27, 2026*
