# Ollama Qwen3 Think-Mode A/B Forensic Benchmark

**Date:** 2026-08-28  
**Status:** COMPLETE  
**Endpoint:** `https://ollama.brestalogistics.co.ke/api/chat`  
**Model:** `qwen3:4b`  
**Test:** 10 CRM scenarios × 2 modes × 1 rep = 20 requests  

---

## Executive Summary

Controlled A/B benchmark comparing `think=true` vs `think=false` on the remote Ollama endpoint with the CRM conversational workload. **think=false wins on raw success rate and latency, but produces unusable output** — all 10/10 responses contain thinking/reasoning text in the visible content field. **think=true has the correct separation** (thinking in `message.thinking`, content in `message.content`) but suffers from 40% empty-content failures.

**Blunt verdict: Neither mode works well out of the box for CRM. think=true is architecturally correct but unreliable. think=false is reliable but architecturally wrong. The system needs a different approach.**

---

## Test Configuration

- **Endpoint:** `/api/chat` (native, not OpenAI-compatible)
- **Authentication:** Basic Auth (ollama:OLLAMA_API_KEY)
- **Model:** qwen3:4b (loaded, 3.18GB)
- **num_predict:** 300 (retry@500 on empty content for think=true)
- **Qwen3 non-thinking params:** temperature=0.7, top_p=0.8, top_k=20, min_p=0, presence_penalty=1.5
- **System prompt:** Full CRM prompt with Sarah persona, Alex fan context, rapport stage guidance
- **VPS:** CPU-only, ~5.5 tok/s, no GPU

### Scenarios (10 total)

| ID | Category | Name | Message |
|----|----------|------|---------|
| 1 | rapport | simple_greeting | "hey" |
| 2 | emotional | fan_misses_creator | "i really missed you today" |
| 3 | emotional | fan_compliment | "you're honestly one of the most beautiful people i've ever seen" |
| 4 | commercial | explicit_buying_intent | "i want to buy your content, how do i do that?" |
| 5 | commercial | asking_price | "how much does it cost?" |
| 6 | rejection | stop_asking | "please stop asking me to buy things" |
| 7 | rejection | asks_if_bot | "are you a bot? be honest with me" |
| 8 | tips | tip_opportunity | "you deserve a tip for being so sweet to me" |
| 9 | ambiguous | short_message | "k" |
| 10 | emotional | vulnerable | "i've been having a really hard time lately and talking to you helps" |

---

## Results

### think=true (with retry@500)

| Scenario | Latency | Eval Tokens | Content | Status | Retried |
|----------|---------|-------------|---------|--------|---------|
| simple_greeting | 173.8s | 500 | EMPTY | FAIL | yes |
| fan_misses_creator | 173.9s | 500 | EMPTY | FAIL | yes |
| fan_compliment | 169.9s | 487 | 67 | OK | yes |
| explicit_buying_intent | 141.5s | 369 | 76 | OK | yes |
| asking_price | 64.2s | 300 | 34 | OK | no |
| stop_asking | 64.1s | 300 | 20 | OK | no |
| asks_if_bot | 146.8s | 394 | 46 | OK | yes |
| tip_opportunity | 152.5s | 419 | 68 | OK | yes |
| short_message | 172.7s | 500 | EMPTY | FAIL | yes |
| vulnerable | 175.6s | 500 | EMPTY | FAIL | yes |

**Summary:** 6/10 success (60%), 4/10 empty even at num_predict=500, avg latency 143.5s, 2/10 leakage

### think=false (no retry needed)

| Scenario | Latency | Eval Tokens | Content | Status | Leakage |
|----------|---------|-------------|---------|--------|---------|
| simple_greeting | 63.7s | 300 | 316 | OK | 1 |
| fan_misses_creator | 64.5s | 300 | 335 | OK | 6 |
| fan_compliment | 63.2s | 300 | 352 | OK | 5 |
| explicit_buying_intent | 63.8s | 300 | 340 | OK | 4 |
| asking_price | 63.4s | 300 | 335 | OK | 6 |
| stop_asking | 63.2s | 300 | 351 | OK | 4 |
| asks_if_bot | 63.1s | 300 | 314 | OK | 4 |
| tip_opportunity | 63.5s | 300 | 340 | OK | 4 |
| short_message | 63.7s | 300 | 325 | OK | 3 |
| vulnerable | 64.8s | 300 | 347 | OK | 4 |

**Summary:** 10/10 success (100%), 0/10 empty, avg latency 63.7s, 10/10 leakage

### Per-Scenario Winner

| Scenario | think=true | think=false | Winner |
|----------|-----------|-------------|--------|
| simple_greeting | FAIL 174s | OK 64s | think=false |
| fan_misses_creator | FAIL 174s | OK 64s | think=false |
| fan_compliment | OK 170s | OK 63s | think=true (no leak) |
| explicit_buying_intent | OK 142s | OK 64s | tie |
| asking_price | OK 64s | OK 63s | think=true (no leak) |
| stop_asking | OK 64s | OK 63s | think=true (no leak) |
| asks_if_bot | OK 147s | OK 64s | tie |
| tip_opportunity | OK 152s | OK 63s | think=true (no leak) |
| short_message | FAIL 173s | OK 63s | think=false |
| vulnerable | FAIL 176s | OK 65s | think=false |

**Tally:** think=true=4, think=false=4, tie=2

---

## Thinking Leakage Analysis

### think=false — ALL responses contain thinking text

Sample content from think=false mode:

```
simple_greeting: "Okay, let me craft a warm greeting that builds rapport. Sarah is cheerful..."
  → Fan sees: 316 chars, ~280 are internal reasoning
  
fan_misses_creator: "I notice the user expressed missing me. As Sarah, I should respond warmly...
  → Fan sees: 335 chars, ~280 are internal reasoning
  
asking_price: "The user is asking about pricing. I should be transparent and helpful...
  → Fan sees: 335 chars, ~280 are internal reasoning"
```

**Verdict: think=false output is unusable for CRM.** Fans would see internal AI reasoning, breaking the human illusion.

### think=true — Correct separation, but 40% failure rate

When think=true succeeds, the separation is clean:
- `message.thinking`: Internal reasoning (e.g., "The user is asking about pricing. I should be transparent...")
- `message.content`: Fan-facing response (e.g., "It's $9.99/month for full access! Want me to walk you through it?")

But 4/10 scenarios produce empty `message.content` even at num_predict=500.

---

## Root Cause: Why think=true Fails

The model has a fixed token budget. With `num_predict=300`:
- Thinking phase: consumes 300-500 tokens (varies by complexity)
- Content phase: starts only after thinking completes
- If thinking consumes all tokens before content starts → empty content

Even at `num_predict=500`:
- Some scenarios (simple_greeting, fan_misses_creator, short_message, vulnerable) have thinking that exceeds 500 tokens
- The model allocates ALL tokens to thinking, never reaches content generation

**This is a fundamental limitation of Qwen3 think mode on small models:** short/simple prompts trigger extensive internal reasoning that exhausts the token budget.

---

## Latency Comparison

| Metric | think=true | think=false |
|--------|-----------|-------------|
| Average | 143.5s | 63.7s |
| P50 | 169.9s | 63.7s |
| P95 | 175.6s | 64.8s |
| Ratio | 2.25x | 1x |

think=true is 2.25x slower because:
1. Thinking phase adds 60-110s of computation
2. Retry mechanism doubles latency for 8/10 scenarios
3. Each request = 2 HTTP calls (initial + retry)

---

## Implications

### For CRM Conversational Use

1. **think=false is NOT viable** — fans see internal reasoning, breaking the human persona
2. **think=true is architecturally correct** but unreliable (40% failure rate)
3. **Current num_predict budget is insufficient** — need 800-1200 tokens for consistent content generation with think=true
4. **Latency is unacceptable** — 143s avg for think=true means fans wait 2+ minutes per response

### For Production Deployment

1. **Do NOT deploy think=true as-is** — 40% failure rate is production-breaking
2. **Do NOT deploy think=false** — thinking leakage breaks CRM persona
3. **Options to consider:**
   - Increase num_predict to 800+ (higher latency, higher cost)
   - Use a larger model (Qwen3:8B or Qwen3:14B) with more efficient thinking
   - Use a non-thinking model for CRM generation
   - Implement think=false with post-processing to strip thinking text

---

## Recommendation

**Do not use Qwen3:4B think mode for CRM conversational generation.** The model is too small to reliably separate thinking from content production within reasonable token budgets.

**Recommended alternatives:**
1. **Non-thinking Qwen3:4B** (current approach) — reliable, fast, but no internal reasoning
2. **Larger model** (Qwen3:8B or Qwen3:14B) — better thinking separation, higher latency
3. **Hybrid approach** — use think=false for generation, implement post-processing to clean output

---

## Files Generated

- `docs/OLLAMA_QWEN3_THINK_MODE_AB_FORENSIC_BENCHMARK.md` — This report
- `tests/benchmarks/test_qwen3_ab_fast.py` — Fast A/B benchmark harness
- `tests/benchmarks/test_qwen3_think_mode_ab.py` — Full A/B benchmark harness (30 scenarios)
- `docs/ab_benchmark_raw.json` — Raw results data

---

## Test Commands

```bash
# Run fast A/B benchmark (10 scenarios, ~20-30 min)
python -m tests.benchmarks.test_qwen3_ab_fast

# Run full A/B benchmark (30 scenarios, ~2-6 hours)
python -m tests.benchmarks.test_qwen3_think_mode_ab
```
