# Ollama Qwen3-4B Native Conversational Transport: Final Decision Report

**Date:** 2026-08-26  
**Status:** COMPLETE  
**Decision:** Use OpenAI-compatible endpoint (`/v1/chat/completions`)

## Executive Summary

After exhaustive testing of both the native Ollama API (`/api/chat`) and the OpenAI-compatible endpoint (`/v1/chat/completions`) with Qwen3-4B, the decision is to use the OpenAI-compatible endpoint.

**Why:** The native API cannot produce content with Qwen3's thinking mode. The `message.content` field is always empty when `think=true` (default). The `think=false` parameter does not disable thinking — it moves thinking text into the `content` field, which is not the actual response. The OpenAI-compatible endpoint correctly handles thinking mode and returns proper content.

## Test Results Summary

| Test | Result |
|------|--------|
| Provider tests (unit) | 70/70 PASS |
| CRM qualification tests (unit) | 41/41 PASS |
| Live VPS tests | 52 (skipped — require VPS) |
| Native API content parsing | FAIL |
| OpenAI endpoint content parsing | PASS |

## Native API: Broken Content

```
think=true, num_predict=10 → content='' (thinking_len=43)
think=true, num_predict=50 → content='' (thinking_len=208)
think=false, num_predict=50 → content='Hmm... user ping... responding... <english>I should respond...</english>' (thinking text, not response)
```

No parameter combination produces clean response content via the native API.

## OpenAI Endpoint: Correct Content, num_predict Ignored

```
OpenAI /no_think, num_predict=10 → content='OK' (258 completion tokens)
```

The content is correct, but the model generates 200-500 tokens regardless of `num_predict`.

## Performance Characteristics

| Scenario | Latency | Notes |
|----------|---------|-------|
| Minimal prompt (/no_think) | 34-52s | 287 completion tokens |
| CRM-style prompt | 48-173s | 500+ tokens, still correct |
| Native API (num_predict works) | 9.6s/50tok | But content is broken |

## Key Findings

1. **Qwen3 thinking mode is aggressive:** Generates ~200-500 tokens for internal reasoning even with `num_predict=10`
2. **OpenAI endpoint correctly strips thinking:** Returns clean content via `choices[0].message.content`
3. **Native API cannot disable thinking:** `think=false` puts thinking in `content`, `/no_think` prefix is not respected
4. **VPS is CPU-only:** ~5.5 tok/s, so 200-500 tokens = 36-91s minimum generation time
5. **Network RTT:** ~355ms median (not significant)

## Recommendation

**Use OpenAI-compatible endpoint (`/v1/chat/completions`)**

- ✅ Correct content (critical for CRM)
- ✅ Thinking mode properly handled
- ⚠️ num_predict ignored (200-500 tokens, ~36-91s generation)
- ✅ 70 unit tests pass

**Alternative (NOT recommended):** Native API (`/api/chat`)
- ❌ Content always empty (broken)
- ⚠️ num_predict respected
- ❌ 41 unit tests fail (empty response)

## Files Modified

- `core/llm_provider_ollama.py` — Uses `/v1/chat/completions` endpoint
- `tests/test_llm_provider.py` — 70 tests, all pass

## Next Steps

1. ~~Adapter migration~~ — COMPLETE
2. ~~Tests updated~~ — COMPLETE
3. Write final decision report — IN PROGRESS
4. Clean up debug scripts — COMPLETE
