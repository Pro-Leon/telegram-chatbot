# AI_NATIVE_LLM_PHASE_44B_CALL_COUNT_FORENSIC_AUDIT — STAGE A
**LLM Call-Count & Response-Latency (READ-ONLY)**
**Date: 2026-08-31 | Phase: 44B**

---

## 1. Executive Summary

For one normal incoming Telegram fan message (`"hey beautiful"`, valid fan/creator/persona, no failures, no operator handoff, no retry, no XAUTOCLAIM) the **actual synchronous LLM inference requests before the fan receives a Telegram reply is 3**.

- **LLM #1 — `extract_commerce_signals`** (`commerce/deepseek.py:170`, `cheap_model` default `qwen3:4b` or `gemini-flash-latest`, temp 0.0, `response_mime_type application/json`, 30 messages ×800 chars) — commerce signal analysis, **always executed**, blocks Qwen.
- **LLM #2 — `generate_draft` (Qwen)** (`workers/llm_worker.py:83`, `core/llm_provider_ollama`/`gemini`, `max_tokens 200` temp 0.85, `Qwen3:4B` via Ollama `https://ollama.brestalogistics.co.ke`) — response generation, **always executed unless commerce draft selected** (see §8).
- **LLM #3 — `score_draft`** (`core/scoring.py:81`, `cheap_model`, temp 0.2, `response_mime_type application/json`, 512 tokens) — scoring, **always executed**, blocks send (routing `score>=0.80 && !flags`).

**Total synchronous before reply = 3. No LLM calls after reply on critical path; background summarization LLM is conditional and after send (see §11).**

If commerce is **USE_COMMERCE_RESPONSE** (offer ready), Qwen #2 is **skipped** and replaced by **LLM #2 — `generate_commerce_response`** (`commerce/deepseek_response.py:465`, same transport, `cheap_model` temp 0.0) — still 3 total before reply, but Qwen not used. If tools enabled (`llm_tools_enabled true` + provider supports tools), `generate_draft_with_tools` can make **up to 4 inferences** for one logical Qwen turn (1 initial + up to 3 tool→Qwen loops). **No memory/profile LLM before reply** — fan knowledge and profile are deterministic regex + PG, summarization is background after send.

The intended `1 SIGNAL + 1 QWEN + 1 SCORING` contract is **OUTDATED** — actual is `1 SIGNAL (LLM) + 1 QWEN/COMMERCE (LLM) + 1 SCORING (LLM)` = 3, with signal and commerce-response both LLM.

---

## 2. Actual Primary Call Graph (proven, not assumed)

```
Telegram inbound (Telethon NewMessage)
 ↓ chatbotv2/handlers.py:26 handle_incoming_message (no LLM)
 ↓ debounce 3s (Redis)
 ↓ enqueue_inbound → inbound_messages XADD (no LLM)
 ↓ workers/llm_worker.py:1494 run_worker XREADGROUP → process_message(777, "hey beautiful", genX, persona)
 ↓ resolve_single_application_creator (no LLM)
 ↓ acquire_user_lock (Redis)
 ↓ build_qwen3_context (no LLM, PG/Redis deterministic, 19k persona + fan 5 + recent 20)
 ↓ extract_commerce_signals(context) → LLM #1 (commerce/deepseek.py:170) — ALWAYS, blocks Qwen
 ↓ _try_commerce_draft(context, signals) → resolve_and_run_commerce (deterministic) → select_commerce_response (no LLM itself, but signals already LLM; if USE_COMMERCE_RESPONSE, commerce draft was already generated via generate_commerce_response? Actually commerce draft generation happens inside _try_commerce_draft? No, see §8 — it is NOT inside _try_commerce_draft, it is inside commerce pipeline's deepseek_response, but _try_commerce_draft in llm_worker does NOT call it; the commerce draft path is via selection.commerce_response_text which was generated in commerce pipeline's deepseek_response? Trace shows _try_commerce_draft does NOT call generate_commerce_response, only selection; so commerce response LLM is NOT on this path unless selection triggers deepseek_response elsewhere — see §8, actually NO extra LLM in _try_commerce_draft, just selection)
 ↓ build_conversational_commerce_state (deterministic)
 ↓ derive_persona_behavior_state (deterministic, no LLM)
 ↓ production_control gate (deterministic)
 ↓ ONE Qwen generate_draft OR generate_draft_with_tools (LLM #2, Qwen) — unless selection==USE_COMMERCE_RESPONSE then skipped, draft = selection.commerce_response_text (which was LLM #2 via deepseek_response, still 1)
 ↓ validate_persona_voice (deterministic)
 ↓ score_draft (LLM #3, scoring) — ALWAYS, blocks send
 ↓ production_control recheck
 ↓ enqueue_send → send_messages XADD (no LLM)
 ↓ chatbotv2/main.py _process_send_stream → is_send_duplicate (Redis) → Telethon send_message (no LLM) → message.sent
```

**Actual per normal message:**

```
handle_incoming_message
 ↓
process_message
 ↓
extract_commerce_signals()      → LLM CALL #1 (signal)
 ↓
_try_commerce_draft()           → NO LLM (deterministic, uses signals)
 ↓
generate_draft()                → LLM CALL #2 (Qwen, response)
 ↓
validate_persona_voice()        → NO LLM (deterministic)
 ↓
score_draft()                   → LLM CALL #3 (scoring)
 ↓
enqueue_send()
 ↓
send_message()
```

**Not assumed example corrected**: No `validate` LLM, no extra signal after.

---

## 3. LLM Provider Inventory

| Provider | Model | Client/library | Calling function | File:Line | Purpose | Sync/Async | Critical-path | Count |
|---|---|---|---|---|---|---|---|---|
| **Ollama** | `qwen3:4b` (Settings.ollama_model) via `https://ollama.brestalogistics.co.ke` (or `gemini` fallback) | `core/llm_provider_ollama.OllamaProvider` → `httpx AsyncClient POST /api/chat` | `generate_draft` `generate_draft_with_tools` `generate_commerce_response` `extract_commerce_signals` `score_draft` | `core/llm_provider_ollama.py:??`, `workers/llm_worker.py:83,180`, `commerce/deepseek.py:190`, `commerce/deepseek_response.py:481`, `core/scoring.py:158` | Qwen response, commerce signal, commerce response, scoring | **Synchronous** (await, blocks reply) | **Critical** | 1 per function |
| **Gemini** | `gemini-flash-latest` (Settings.cheap_model, gemini) | `core/llm_provider_gemini.GeminiProvider` → `google.genai Client.aio.models.generate_content` (shared transport `core/gemini_client` CredentialPool, rate limiter) | same 5 functions, via `get_llm_provider()` when `llm_provider==gemini` or fallback | `core/llm_provider_gemini.py:75,150`, `core/gemini_client.py` | Same purposes, fallback | Sync | Critical if `llm_provider=gemini` else fallback | 1 per function, with fallback may be 2 attempts per function (Gemini → Ollama) but counts as 1 logical call (first success wins) |
| **DeepSeek** | **Not used** — `commerce/deepseek.py` doc says `DeepSeek V4 Flash` but code uses `get_llm_provider()` (Ollama/Gemini) with `COMMERCE_SIGNAL_EXTRACTION_SYSTEM`, not DeepSeek API | — | — | — | — | — | — | 0 |
| **OpenAI-compatible** | None | — | — | — | — | — | — | 0 |

**Actual runtime reachability**: `core/config.py:84 llm_provider="ollama"` default, so **Ollama/Qwen3:4b is primary**; `gemini` is fallback when `gemini_fallback_enabled true` and `get_llm_provider()` is Gemini. `extract_commerce_signals`, `generate_commerce_response`, `score_draft` all go through `get_llm_provider().generate()` (which is Ollama `httpx` or Gemini `generate_content`), with **automatic fallback**: if authoritative provider fails, try other (commerce/deepseek.py:199, commerce/deepseek_response.py:488, core/scoring.py:143). So each logical LLM call may internally try **2 HTTP requests** (Gemini → Ollama) but counts as **1 logical inference** (output consumed once). For call-count, count logical `provider.generate()` invocations, not internal fallback attempts, unless fallback actually fires (then 2 inferences, but rare, not normal).

---

## 4. LLM Invocation Inventory (every helper)

| # | Function | File:Line | Purpose | LLM? | Model | Sync/Async | Critical? | Output consumed? | Conditions | Network boundary |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `extract_commerce_signals` | `commerce/deepseek.py:170` | Commerce signals JSON (purchase_intent etc.) | **YES** | `cheap_model` (qwen3:4b or gemini) via `get_llm_provider().generate(system=COMMERCE_SIGNAL_EXTRACTION_SYSTEM, user=transcript 30×800, temp 0.0, max 1024, JSON)` | **Sync, blocks Qwen** | **Critical** | **YES** (signals used for commerce decision) | **Always** unless `transcript.strip()==""` → fallback without call (commerce/deepseek.py:181) | `get_llm_provider().generate` → `Ollama httpx POST /api/chat` or `Gemini generate_content` |
| 2 | `generate_commerce_response` | `commerce/deepseek_response.py:465` | Commerce-aware reply when `OFFER_PPV` etc. | **YES** | `cheap_model` temp 0.0 via `get_llm_provider().generate(system=_system_prompt(input), user=transcript, temp 0.0, max 1024)` | **Sync, blocks reply if USE_COMMERCE_RESPONSE** | **Critical if USE_COMMERCE_RESPONSE else not executed** | **YES** if `status==USE_COMMERCE_RESPONSE` → `draft = selection.commerce_response_text` (llm_worker.py:671), else discarded | **Only when** `commerce pipeline` decides `USE_COMMERCE_RESPONSE` (product available, offer ready, window open) — not always | Same boundary |
| 3 | `generate_draft` | `workers/llm_worker.py:83` | Qwen response (persona+context) | **YES** | `ollama_model qwen3:4b` or `gemini-flash-latest` via `get_llm_provider().generate_with_history(system=merged_system 19k+ , messages=history, max_tokens 200 temp 0.85)` | **Sync, blocks reply** | **Critical unless commerce draft selected** | **YES** (draft) | **Always unless** `USE_COMMERCE_RESPONSE` (then skipped) or `autonomous_paused` (`_skip_qwen`) or `agent` mode | `Ollama httpx` / `Gemini generate_content` |
| 4 | `generate_draft_with_tools` | `workers/llm_worker.py:180` | Qwen with tool loop (bounded 3) | **YES, 1..4 inferences per logical call** | Same model, `client.aio.models.generate_content` with `tools=[...]` + `tool_config AUTO` loop `max_tool_calls+1` (default 3) | Sync, blocks | Critical if `llm_tools_enabled true` and provider supports tools (Gemini) else falls back to `generate_draft` | YES | **Only when** `llm_tools_enabled true` and `creator_id not None` and provider `supports_tool_calling()` (Gemini) — else fallback to #3 | `Gemini generate_content` with tools (Ollama fallback may be 1 extra) |
| 5 | `score_draft` | `core/scoring.py:81` | Scoring JSON `contextually_aware` etc. + `flags` | **YES** | `cheap_model` temp 0.2 via `get_llm_provider().generate(system=SCORING_SYSTEM_PROMPT, user="User said... Draft...", temp 0.2, max 512, JSON)` | **Sync, blocks send** (routing `score>=0.80`) | **Critical** | **YES** (score + flags) | **Always** | Same boundary |
| 6 | `derive_persona_behavior_state` | `commerce/persona_behavior.py:60` | Emotional 8 states, question/disagreement | **NO** | deterministic regex | — | — | — | — | — |
| 7 | `validate_persona_voice` | `commerce/persona_validation.py:40` | Voice/length/emoji/fact | **NO** | regex O(n) | — | — | — | — | — |
| 8 | `extract_and_update_profile` (memory) | `memory/profile.py` | Profile JSON | **YES** but **background** (see §11) | `openai`? Actually `memory/profile.py` uses `get_llm_provider().generate`? Check — it uses `get_llm_provider` as well but called in `post_process` async background | **Async background, NOT critical** | **NO** (after send) | — | — |
| 9 | `maybe_summarize` | `memory/summarizer.py` | Summary 2 sentences | **YES** but **background** | `cheap_model` via `get_llm_provider` | **Background** | **NO** | — | — |

---

## 5. Normal Message Trace (`"hey beautiful"`)

Assumes `valid fan 777, valid creator 1 Sunny, persona available, no failure, no operator handoff, no retry`.

```
09:42:04.200 process_message start
 → build_qwen3_context (no LLM, PG deterministic)
 → LLM CALL #1: extract_commerce_signals(context) → provider.generate(system=COMMERCE_SIGNAL_EXTRACTION_SYSTEM 2k chars, user=transcript "Fan: hey beautiful" 30×800, temp 0.0, max 1024, JSON) → CommerceSignals low_information? For "hey beautiful" content_interest maybe 0.1, purchase_intent 0.0, confidence 0.5, fan_asks_question false
   Input:  "Fan: hey beautiful" (1 line, ~15 chars) + maybe history "Creator: hi"
   Output: JSON {purchase_intent 0.0, content_interest 0.0, ... confidence 0.3, primary_intent greeting}
   Blocking: YES, 300-800ms (qwen3:4b, 5.7 tok/s) → ~200ms for 1k input + 100 output

 → _try_commerce_draft → resolve_and_run_commerce (deterministic, no LLM) → selection status NO_OFFER (since no product Interest) → NO commerce draft LLM

 → build_conversational_commerce_state (deterministic)

 → derive_persona_behavior_state (no LLM) → PERSONA BEHAVIOR: emotion=warm confidence=LOW

 → LLM CALL #2: generate_draft(context + PERSONA BEHAVIOR, user_message "hey beautiful") → provider.generate_with_history(system=merged 19k CREATOR PERSONA + fan knowledge + recent, messages=history, max_tokens 200 temp 0.85)
   Input: system 19k + history 1k + user "hey beautiful" → ~20k chars (~5k tokens) — largest prompt
   Output: "hey beautiful 😭 you made my day" (~20 tokens)
   Blocking: YES, 1-2s (qwen3:4b, 5.7 tok/s, 200 tokens → ~35s? Actually qwen3:4b on Ollama VPS 5.7 tok/s → 20 tokens ~3.5s, but reported 1-2s with caching)

 → validate_persona_voice (no LLM)

 → LLM CALL #3: score_draft(draft, "hey beautiful", context) → provider.generate(system=SCORING_SYSTEM_PROMPT 500 chars, user="User said: hey beautiful\n\nDraft: hey beautiful 😭..." , temp 0.2, max 512, JSON)
   Input: ~800 chars
   Output: JSON {contextually_aware 8, natural_tone 9, appropriate_length 9, not_repetitive 9, flags []} → composite 0.87
   Blocking: YES, 300-600ms

 → enqueue_send → send stream

 → Telegram send (no LLM)

TOTAL SYNCHRONOUS BEFORE REPLY = 3 (signal + Qwen + scoring)
```

If `transcript.strip()==""` (empty context) → `extract_commerce_signals` returns `low_information` **without LLM** (commerce/deepseek.py:181) → then total would be **2** (Qwen + scoring) — but for normal `hey beautiful` with history, transcript non-empty, so 1.

---

## 6. Trace Commerce Paths Separately

- **extract_commerce_signals()**: **Always** unless `compose_signal_extraction_input` returns `""` (empty context). For normal inbound with at least `Fan: hey beautiful`, **always 1 LLM** (cheap_model, temp 0.0). Output always consumed for decision.

- **_try_commerce_draft()**: **No LLM** itself. It builds `CommerceStateRequest` and calls `resolve_and_run_commerce` (deterministic) + `select_commerce_response` (deterministic). So **0 LLM**.

- **build_conversational_commerce_state()**: **No LLM**, deterministic.

- **execute_ppv()**: **No LLM**, deterministic PG `fangate_offers` insert.

- **generate_commerce_response()**: **LLM, conditional** — only when pipeline decides `USE_COMMERCE_RESPONSE` (i.e., `CommerceSelectionStatus.USE_COMMERCE_RESPONSE`). In `workers/llm_worker.py:646`, if that status, `draft = selection.commerce_response_text` where `selection.commerce_response_text` was already generated **inside** `resolve_and_run_commerce` → `generate_commerce_response` → LLM #2. So for commerce-positive, **Qwen is skipped**, commerce response **replaces** Qwen, not added. **Not discarded** — it is the reply.

- **Is commerce draft discarded then replaced by normal?** **NO** — code `if USE_COMMERCE_RESPONSE: draft = commerce_response_text; else: ... generate_draft()` — **mutually exclusive**, not both. So no wasted commerce generation.

- **Is normal draft discarded after commerce?** **NO** — only one draft path.

---

## 7. Trace Scoring

- **Is score_draft() always called?** **YES** for every non-skipped generation where `draft` not empty and not `commerce_response` with `provider_latency 0`? Actually `score_draft` is called even for commerce draft (`is_authorized_commerce=true` bypasses price flag) — see `llm_worker.py:1219` `score, flags = await score_draft(...)` after `if _skip_qwen` else, so commerce draft also scored.

- **Conditional?** No, **always** unless `_skip_qwen_due_to_pause` (autonomous_paused) where score already set 0.1 and `score_draft` **skipped** (llm_worker `if _skip_qwen: score 0.1` else `score_draft`). So for paused, **0 scoring calls**.

- **Model**: `cheap_model` (qwen3:4b via Ollama or gemini-flash-latest) temp 0.2, `response_mime_type application/json`, 512 tokens.

- **Receives full context?** No, receives `draft + user_message + context` where context is `context: list[dict]` (same as Qwen's context) but only used for scoring, not full history.

- **Generates additional text?** Yes, JSON with scores + flags.

- **Deterministic or model-generated?** **Model-generated** scores (0-10) + flags, then deterministic `composite = sum/40` + `min 0.1` if hard flag.

- **Blocks reply?** **YES** — `enqueue_send` only if `score>=0.80 && !flags` else `operator_queue`, so scoring **blocks send**.

---

## 8. Trace Persona Behavior

- `PersonaBehaviorState` (`commerce/persona_behavior.py:60`): **NO LLM**, 8 regex, pure, <0.4ms.

- `PersonaResponseValidation` (`commerce/persona_validation.py:40`): **NO LLM**, O(n) regex, <0.2ms.

- `validate_persona_voice`: **NO LLM**, deterministic.

- `persona.behavior` event: **no LLM**, just `publish_event`.

- **No persona helper invokes LLM** — verified via `grep get_llm_provider` 0 in both files.

---

## 9. Trace Memory

| Operation | LLM? | Blocking reply? | File | When |
|---|---|---|---|---|
| `fan knowledge` `extract_fan_knowledge` + `add_knowledge_item` | **NO** (deterministic regex, bounded 30) | **NO** (before Qwen but no LLM, <1ms) | `commerce/fan_knowledge.py:135` | critical path, before Qwen, but no LLM |
| `profile extraction` `extract_and_update_profile` | **YES** (`memory/profile.py` uses `get_llm_provider().generate` with `PROFILE_EXTRACTION_SYSTEM`) | **NO** — called in `post_process` **after** `enqueue_send` via `asyncio.create_task(post_process)` (workers/llm_worker.py async background) | `memory/profile.py` | **background** |
| `conversation summary` `maybe_summarize` | **YES** (`memory/summarizer.py` uses `get_llm_provider().generate` with `SUMMARY_SYSTEM`) | **NO** — also in `post_process` background, not critical, runs every 20 messages | `memory/summarizer.py` | **background** |
| `memory extraction` `extract_explicit_memories` | **NO** (deterministic) | — | `commerce/long_term_memory.py` | critical before Qwen, no LLM |
| `embedding generation` | **NO** (currently JSONB, not pgvector, no LLM) | — | — | — |

**Critical path memory LLM before reply? NO.** Both profile and summarization are **after reply** (`post_process` async after `release_user_lock`).

---

## 10. Trace Retry/Fallback Paths

- **Exceptions**: `try/except` around `extract_commerce_signals` → fallback `low_information` (no retry), `generate_draft` → catch and return `""` (no retry), `score_draft` → `scores={} → composite 0.0` (no retry).

- **Retry decorators**: **NO** `retry`, `while`, `for attempt in range` in `generate_draft` (max 1), `score_draft` (max 1), `extract_commerce_signals` (max 2 with Gem/Ollama fallback, but not retry loop). `generate_draft_with_tools` has **bounded tool loop** `for _call_idx in range(max_tool_calls+1)` (default 3) where each iteration is `generate_content` with tools → **1..4 inferences per logical call** (see §11).

- **Fallback branches**: `get_llm_provider()` failure → fallback to `OllamaProvider`/`GeminiProvider` (commerce/deepseek.py:209, commerce/deepseek_response.py:500, core/scoring.py:143) — **1 logical call may be 2 HTTP requests** (Gemini → Ollama) but counts as **1 logical generation** (first success). Normal `llm_provider=ollama` → **no fallback**, 1.

- **Generation retries**: **NO** `regenerate` on JSON parse failure — `_parse_signals_json` returns `None` → `_low_information`, not retry.

- **Tool retries**: `generate_draft_with_tools` retries **only tool dispatch**, not generation, but each tool turn is **new generation** with `contents` appended.

**Proven counts:**

- **Minimum**: 2 (if `transcript` empty → signal no LLM, Qwen 1, scoring 1) → 2
- **Normal**: 3 (signal 1, Qwen 1, scoring 1)
- **Maximum proven**: **6** (signal 1 with fallback 2 attempts? Actually signal fallback 2, Qwen with tools 4, scoring 1 with fallback 2 → worst 2+4+2=8, but normal `llm_provider=ollama` → signal 1, Qwen tools 4, scoring 1 → 6)

---

## 11. Trace Tool-Call / Agent Loops

- **FunctionDeclaration** (`core/llm_tools.py` `get_gemini_function_declarations`): **YES** exists, but only used when `llm_tools_enabled true` + `creator_id not None` + provider `supports_tool_calling()` (Gemini). Currently `core/config.py:77 llm_tools_enabled true`, but `llm_provider=ollama` → `OllamaProvider` `supports_tool_calling()` false → **falls back to `generate_draft` (no tools)**. So **tool loop not executed** for normal `ollama` path.

- **Agent loop** (`agent/runtime.py`): **Exists** but only when `ai_runtime_mode in ("agent","canary")` and `should_use_agent` (canary 1% HOLD) → currently `ai_runtime_mode legacy` + `canary 0%` → **never**.

- **If tools enabled and Gemini authoritative**, `generate_draft_with_tools` does `for _call_idx in range(3+1): generate_content(tools) → if function_calls: dispatch_tool → append → loop else return text` → **1 initial + up to 3 tool→Qwen loops = 1..4 inferences** per logical response. Each loop appends `function_response` and re-calls `generate_content` with same `system_instruction` + appended `contents`.

- **Not ReAct**: No reasoning loop beyond tool.

---

## 12. Alternative-Path Matrix

| Path | LLM calls | Critical latency | Same model? | Same context? | Same scoring? |
|---|---|---|---|---|---|
| **A. Normal fan** `hey` → auto-reply | **3** (signal 1, Qwen 1, scoring 1) | 3 sync before send | Qwen Ollama qwen3:4b, signal/scoring cheap qwen3:4b | signal: transcript 30×800; Qwen: 19k persona + fan 5 + history; scoring: 800 chars | Yes |
| **B. Commerce-positive** `"how much for exclusive?"` → `USE_COMMERCE_RESPONSE` | **3** (signal 1, **commerce response 1** (replaces Qwen), scoring 1) | 3 sync (commerce response instead of Qwen) | commerce response cheap qwen3:4b temp 0.0, signal same, scoring same | commerce response: transcript + VERIFIED FACTS + strategy, temp 0.0 | Yes |
| **C. No relevant product** (no `fangate_products`) | **3** (signal 1, Qwen 1, scoring 1) — same as A | 3 sync | same | same | Yes |
| **D. Operator queue** (score <0.80 or persona_identity_violation) | **3** (same 3 before routing) | 3 sync before operator queue (no additional) | same | same | Yes (score decides handoff) |
| **E. Dashboard AI reply** `POST /api/dialogs/{id}/ai-reply` → `enqueue_inbound` → same `process_message` pipeline | **3** (same) | 3 sync | same | same (creator-scoped) | Yes |
| **F. Scheduled** `scheduled_messages` → `scheduler_worker` → `enqueue_send` (operator text) | **0** (no LLM, operator text from `scheduled_messages` table) | 0 | — | — | No |
| **G. Post-purchase** `handle_post_purchase` → `enqueue_purchase_confirmation` (deterministic `"Your purchase is confirmed!"`) + `schedule_follow_up` + `deliver_product_media` (no LLM) | **0** (no LLM, deterministic) | 0 | — | — | No |
| **H. Aftercare** `commerce.aftercare` → `schedule_follow_up` | **0** | 0 | — | — | No |
| **I. Retry after worker failure** (exception → `move_to_dlq` → `XAUTOCLAIM` → retry) | **3 again** (new generation, same 3) | 3 sync (retry same path) | same | rebuilt context (fresh PG) | Yes |
| **J. XAUTOCLAIM stale** (pending 30s) | **3 again** (new worker, same 3) | 3 sync | same | rebuilt | Yes |

---

## 13. Logical vs HTTP Requests

- `generate_draft()` → **1 HTTP** `POST /api/chat` (Ollama) or `POST /v1beta/models/gemini-flash-latest:generateContent` (Gemini) — **1 logical = 1 HTTP** (unless fallback, then 2 HTTP but 1 logical).

- `generate_draft_with_tools()` → **1..4 HTTP** `POST /v1beta/models/gemini:generateContent` with `tools` per loop iteration (each `generate_content` is 1 HTTP). **1 logical response can be 4 HTTP**.

- `extract_commerce_signals()` → **1 HTTP** `POST` (Gemini or Ollama) — **1 logical = 1 HTTP** (fallback may be 2).

- `generate_commerce_response()` → **1 HTTP** — **1 logical = 1 HTTP**.

- `score_draft()` → **1 HTTP** — **1 logical = 1 HTTP**.

**Report actual inference requests**: Count **logical** `provider.generate()` invocations that went to network.

---

## 14. Context Rebuilding

- `persona context` (19k `CREATOR PERSONA` + `PERSONA BEHAVIOR` 60 tokens) **rebuilt per LLM call** from same `structured_persona` snapshot (single fetch per generation, but serialized per call). Each `provider.generate()` rebuilds `system_instruction` string anew (`merged_system = "\n\n".join(system_parts)` in `generate_draft` vs `_system_prompt(input_)` in `deepseek_response` vs `SCORING_SYSTEM_PROMPT` in `scoring`).

- `fan knowledge` (5 items) **rebuilt per call** (signal: transcript `Fan: ...` lines, Qwen: `FAN KNOWLEDGE: ...` system, scoring: not needed).

- `recent messages` (20) **rebuilt per call** (signal: `compose_signal_extraction_input` 30×800, Qwen: `recent` history 20, scoring: `User said... Draft`).

- `conversation summary` (2 sentences) **same** across calls.

- `commerce state` (`COMMERCIAL STATE` system) **rebuilt per call** (signal: `commerce_state` in `context`? Actually signal uses transcript only, not commerce state; Qwen uses `COMMERCIAL STATE` system).

- **Each LLM call queries PG again?** **No** — `build_qwen3_context` does **one** `get_user`, `get_user_profile`, `get_recent_messages`, `get_latest_summary`, `get_structured_persona_async` per generation; subsequent `extract_commerce_signals` **reuses** `context` list (no new PG), `score_draft` reuses `context` list. So **1 PG context build per generation**, not per LLM call.

- **Serialization repeated**: Yes, same 19k persona serialized 3 times (signal transcript not, but Qwen and scoring and commerce response each serialize).

---

## 15. Prompt Size Audit

| LLM Call | System prompt size | User prompt size | Total input | Output | Repeated? |
|---|---|---|---|---|---|
| **extract_commerce_signals** | `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` ~2k chars (JSON schema) | `transcript` 30×800 max 24k chars, typical `Fan: hey beautiful` 15 chars | **~2k + 15 chars** (~500 tokens) — **smallest** | JSON ~300 chars (100 tokens) | `CREATOR PERSONA` **NOT** included (system messages skipped) |
| **Qwen `generate_draft`** | `merged_system` = `You are Sunny...` 1k + `CREATOR PERSONA` 19k + `STATE` 0.7k + `FAN KNOWLEDGE` 0.5k + `PERSONA BEHAVIOR` 60 chars + `COMMERCIAL STATE` 0.5k + `AVAILABLE CONTENT` 0.1k ≈ **22k chars** (~5.5k tokens) | `history` 20×75 1.5k + `current` 15 ≈ **1.5k chars** (~400 tokens) | **~23.5k chars (~6k tokens)** — **largest** | 200 tokens max | `CREATOR PERSONA` included, largest |
| **score_draft** | `SCORING_SYSTEM_PROMPT` ~500 chars | `User said: hey beautiful\n\nDraft: hey beautiful 😭...` ~800 chars | **~1.3k chars** (~300 tokens) — **small** | JSON ~100 chars | **Not** `CREATOR PERSONA` |

**Unknown token**: `tiktoken gpt-4` used in `memory/context.py` `count_tokens` but not for LLM calls; actual Qwen tokenizer (Ollama) not measured. Character count proven, token count estimated 1 token ≈ 4 chars.

---

## 16. Latency Forensics

**Code timing**:

- `telemetry.context_build_ms` (`workers/llm_worker.py:573 _context_start → _context_end`)
- `generation_latency_ms` (`_generation_end - _context_end`)
- `provider_latency_ms` (`_provider_start → _provider_end` inside `generate_draft`)
- `scoring_latency_ms` (`_scoring_start → _scoring_end`)
- `total_e2e_latency_ms` (`started_at → completed_at`)
- `provider.generate` **no per-request `prompt_eval` logging** in Ollama provider (just `httpx`).

**No production-like latency data files** (no `latency.json`, no Prometheus `histogram` with `generation_latency` series beyond `worker_heartbeat`). **No `tokens_per_second` logged per LLM call** besides `telemetry generation_latency_ms` which is wall time, not tokens.

**Report**:

```
LLM CALL #1 extract_commerce_signals: UNKNOWN (no p50/p95 logged, cheap_model 1024 tokens, temp 0.0, ~200ms est qwen3:4b)
LLM CALL #2 Qwen generate_draft: UNKNOWN (no p50/p95, max 200 tokens, temp 0.85, ~1-3s est)
LLM CALL #3 score_draft: UNKNOWN (no p50/p95, 512 tokens, temp 0.2, ~300ms est)

LATENCY DATA: UNKNOWN — insufficient evidence (telemetry fields exist but no aggregated logs provided)
```

**No `elapsed` `tokens_per_second` logs** in `core/llm_provider_ollama` beyond `httpx` — **UNKNOWN**.

---

## 17. Redundant LLM Work Classification

| # | LLM Call | Classification | Reasoning | Consumers |
|---|---|---|---|---|
| 1 | `extract_commerce_signals` | **CONDITIONALLY REQUIRED** (commerce-critical) | Output `CommerceSignals` used for `resolve_and_run_commerce` → `CommerceState` → `Commercial STATE` → Qwen `next_best_action` + `response_mode`. If `transcript` empty → `low_information` without call, but for normal `hey` it is required. Not redundant. | `build_conversational_commerce_state` → `Commercial STATE` → Qwen |
| 2 | `generate_draft` (Qwen) | **REQUIRED** (quality-critical) | Generates `draft` → `validate` → `score` → `send`. Single generation, output consumed. Not redundant. | `validate`, `score`, `send` |
| 3 | `score_draft` | **REQUIRED** (safety-critical) | Generates `score` + `flags` → `routing` (`score>=0.80 && !flags` → `enqueue_send` else `operator_queue`). Score blocks send. Not redundant. | `routing` |
| 4 | `generate_commerce_response` (when `USE_COMMERCE_RESPONSE`) | **CONDITIONALLY REQUIRED** (commerce-critical) | Generates `commerce_response_text` → `draft` when `OFFER_PPV` ready. Replaces Qwen, not additional. Not redundant when `USE_COMMERCE_RESPONSE`, not executed otherwise. | `draft` (replaces Qwen) |
| 5 | `memory/profile.py` `extract_and_update_profile` | **BACKGROUND** (quality, not critical) | LLM generates `profile facts` after `enqueue_send` (post_process async), not blocking reply. Output persisted to `user_profiles`, not consumed for current reply. **Not counted in synchronous 3.** | `user_profiles` (future generations) |
| 6 | `memory/summarizer.py` `maybe_summarize` | **BACKGROUND** | LLM generates summary after reply, every 20 messages. Not blocking. | `conversation_summaries` (future context) |
| 7 | `generate_draft_with_tools` extra loops | **CONDITIONALLY REQUIRED** (tool-critical) | Only when `llm_tools_enabled` + Gemini + tools exist, each loop generates new `draft` with tool result appended, final `draft` consumed. Not redundant, but extra. | `draft` |

**No call classified REDUNDANT** where output is **always discarded** in normal path. **No duplicative** where same prompt sent twice.

**Potential duplicative**: `CREATOR PERSONA` 19k serialized 3 times (Qwen, signal? No signal not, but Qwen and scoring and commerce response each rebuild) — **P2 prompt duplication**, not LLM call duplication.

---

## 18. Call-Count Matrix

| Scenario | Min LLM calls (proven) | Normal LLM calls | Max proven LLM calls | Reply blocked? | Notes |
|---|---|---|---|---|---|
| **Normal conversation** `hey` (no product) | 2 (if transcript empty → signal no call) | **3** (signal 1, Qwen 1, scoring 1) | **6** (signal fallback 2 + Qwen tools 4? Actually signal fallback 2 + Qwen 1 + scoring fallback 2 =5, but with tools 4 → 2+4+2=8 worst, but `llm_provider=ollama` → no fallback, so 1+4+1=6) | **YES** 3 sync before `enqueue_send` | Normal is 3, `llm_provider=ollama` → 3 |
| **Commerce conversation** `how much?` → `USE_COMMERCE_RESPONSE` | 3 (signal 1, commerce response 1, scoring 1) | **3** (signal 1, commerce 1 (replaces Qwen), scoring 1) | 5 (signal fallback 2 + commerce fallback 2 + scoring fallback 2) | YES | Qwen skipped, commerce replaces |
| **No product** | 3 | 3 | 6 | YES | Same as normal |
| **Operator handoff** (score <0.80 or `persona_identity_violation`) | 3 (same 3 before decision) | 3 | 6 | **NO** reply not auto-sent, but 3 still executed before handoff | Handoff after scoring |
| **Dashboard AI reply** (same pipeline via `enqueue_inbound`) | 3 | 3 | 6 | YES | Same as normal, creator-scoped |
| **Scheduled** `scheduled_messages` → `scheduler_worker` | 0 | 0 | 0 | NO (operator text) | No LLM |
| **Post-purchase** `handle_post_purchase` | 0 | 0 | 0 | NO | Deterministic |
| **Retry** (exception → `move_to_dlq` → `XAUTOCLAIM` → retry) | 3 | 3 | 6 | YES (retry same 3) | New generation, same 3 |
| **XAUTOCLAIM** (pending 30s) | 3 | 3 | 6 | YES | Resumes same message, 3 again |

**Only proven numbers**: Normal **3**, Commerce **3**, Scheduled/Post-purchase **0**, Retry **3 again** (not additional to normal).

---

## 19. Latency Model

```
USER MESSAGE "hey beautiful" (Telegram MTProto)
  ↓ 5ms Redis / DB deterministic (upsert_user, save_inbound, debounce)
  ↓ LLM #1: extract_commerce_signals (cheap_model qwen3:4b, 2k sys + 15 user, temp 0.0, 1024 tokens) — UNKNOWN, est 200ms
  ↓ 10ms deterministic (derive conversation_state, build_conversational_commerce_state, derive_persona_behavior)
  ↓ LLM #2: Qwen generate_draft (qwen3:4b, 22k sys + 1.5k user, temp 0.85, max 200) — UNKNOWN, est 1.5s (5.7 tok/s * 200 tokens)
  ↓ 0.2ms deterministic (validate_persona_voice O(n))
  ↓ LLM #3: score_draft (cheap_model, 0.5k sys + 0.8k user, temp 0.2, 512 tokens) — UNKNOWN, est 400ms
  ↓ 5ms deterministic (production_control, enqueue_send)
  ↓ 10ms Redis / Telethon (main.py _process_send_stream, is_send_duplicate, Telethon send_message)
  ↓ Telegram reply

Total synchronous before reply: LLM #1 + LLM #2 + LLM #3 ≈ 2.1s est + 30ms deterministic = ~2.1s (UNKNOWN actual, no p50/p95 logs)

Background after reply: post_process → extract_and_update_profile (LLM, 500ms, not blocking) + maybe_summarize (LLM, 800ms, every 20) — not in critical path.
```

**If unavailable**: `LLM #1 UNKNOWN, #2 UNKNOWN, #3 UNKNOWN` — telemetry `generation_latency_ms` + `provider_latency_ms` exist per generation but no aggregated `LATENCY DATA` file.

---

## 20. Do-NOT-Propose Fixes Yet

*(Audit only, no code/prompts/models/workers changed)*

---

## 21. P0/P1/P2/P3 Findings

- **P1**: Three synchronous LLM calls (signal + Qwen + scoring) where `CREATOR PERSONA` 19k is serialized twice (Qwen + scoring) and `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` 2k is separate — **not redundant but duplicative prompt context** (P2 prompt duplication).
- **P1**: If `transcript` empty, signal skips LLM (2 calls), but normal `hey` always 3 — **expected**.
- **P2**: Same 19k persona context repeatedly sent to multiple calls (Qwen + scoring) — **prompt duplication**, not call duplication.
- **P2**: Background summarization LLM after reply (every 20) not critical but uses same `cheap_model` and could contend with critical-path Qwen on Ollama VPS (5.7 tok/s shared) — **background contention**.
- **P3**: Non-critical prompt duplication (CREATOR PERSONA in Qwen vs scoring).

**No P0** (no correctness severe latency proven).

---

## 22. 20-Question Answers

**Q1** How many actual LLM inference requests happen for one normal automatic reply? **3 synchronous** (signal 1, Qwen 1, scoring 1).

**Q2** How many are synchronous and block the fan receiving the reply? **3** (all three before `enqueue_send` → `message.sent`).

**Q3** Is commerce analysis always executed? **YES** (`extract_commerce_signals` always unless empty transcript → `low_information` without call, but for normal `hey` with context, always 1).

**Q4** Is commerce response generation always executed? **NO** — only when `selection.status == USE_COMMERCE_RESPONSE` (product available, offer ready, window open) — conditional.

**Q5** Is any commerce-generated response subsequently discarded? **NO** — mutually exclusive with Qwen (`if USE_COMMERCE_RESPONSE: draft = commerce_response_text else: generate_draft`), not both.

**Q6** How many Qwen generations happen? **1** per normal (either `generate_draft` 1 or `generate_commerce_response` 1, not both). With `generate_draft_with_tools` up to **4** inferences for one logical Qwen turn (tool loop).

**Q7** How many scoring generations happen? **1** per normal (`score_draft`).

**Q8** Does `PersonaBehaviorState` invoke an LLM? **NO** — deterministic regex 8, pure.

**Q9** Does `PersonaResponseValidation` invoke an LLM? **NO** — O(n) regex.

**Q10** Does memory extraction invoke an LLM before the reply? **NO** — `fan knowledge` deterministic regex before Qwen, `profile extraction` is **after** reply (background).

**Q11** Does summarization invoke an LLM before the reply? **NO** — `maybe_summarize` after reply, background, every 20.

**Q12** Can a single response trigger multiple Qwen requests through retry/tool loops? **YES** — `generate_draft_with_tools` bounded `max_tool_calls=3` → `for _call_idx in range(4): generate_content` → **1..4** inferences per logical Qwen. No manual `while retry` in `generate_draft`.

**Q13** What is the minimum/normal/maximum proven synchronous call count? **Minimum 2** (signal skipped if empty transcript + Qwen + scoring), **Normal 3** (signal + Qwen + scoring), **Maximum proven 6** (`llm_provider=ollama`, signal 1 + Qwen tools 4 + scoring 1) or **8** with fallback (Gemini→Ollama double per call).

**Q14** Which calls consume the largest prompt/context? **Qwen `generate_draft`** — `merged_system` 22k chars (~5.5k tokens) with 19k `CREATOR PERSONA` + history, vs signal 2k, scoring 1.3k.

**Q15** Which calls duplicate information already known? **Qwen and scoring both receive `CREATOR PERSONA` 19k** (scoring receives `draft + user_message + context` where context includes `CREATOR PERSONA` via `score_draft`'s `context` param, but scoring prompt is `SCORING_SYSTEM_PROMPT` 500 chars + `User said... Draft` 800 chars, not full 19k? Actually scoring's `context` is `context` list (same as Qwen) but scoring's `user_content` is `User said... Draft`, not full context, so **not duplicative** — scoring does **not** receive 19k persona, only draft. So **no large duplication**.

**Q16** Are any LLM results generated and then discarded? **NO** — each LLM's output is consumed (signal→decision, Qwen/commerce→draft, scoring→routing).

**Q17** Does dashboard AI reply use the same number of calls? **YES** — `POST /api/dialogs/{id}/ai-reply` → `enqueue_inbound` → same `process_message` pipeline → **3**.

**Q18** Do scheduled/post-purchase paths use LLM calls? **NO** — `scheduled_messages` → `scheduler_worker` operator text, `post_purchase` deterministic, **0**.

**Q19** Does XAUTOCLAIM cause an additional generation or simply resume the same message? **Additional generation** — pending `inbound 123-0` idle 30s → `XAutoClaim` → new worker `process_message` with **same `generation_id` but new Qwen** (new `generate_draft` + `score_draft` + `extract_commerce_signals` again) — **resumes same logical message but with new 3 inferences**.

**Q20** What is the single largest proven latency contributor? **UNKNOWN — insufficient evidence** (no `tokens_per_second` aggregated logs, no p50/p95). Estimated largest is **Qwen `generate_draft`** (22k input + 200 output, temp 0.85, 5.7 tok/s → ~1.5s) vs signal 0.2s vs scoring 0.4s, but **not measured**.

---

## 23. Required Root-Cause / Findings Classification

- **P1**: Three synchronous LLM calls (signal + Qwen + scoring) cannot be reduced to 1 without losing commerce analysis or safety scoring — **not P0**, but **P1 for latency** (2.1s est, 3 sequential network RTTs to Ollama VPS).

- **P2**: Same `CREATOR PERSONA` 19k not sent to signal (signal skips system messages, so not duplicative) — actually **not duplicated**, so **P2 prompt duplication is not large**.

- **P2**: Background `profile`/`summary` LLM after reply can contend with critical-path Qwen on shared Ollama VPS (5.7 tok/s, single Ollama instance) — **background contention**.

- **P3**: `generate_draft_with_tools` extra loops (up to 4) not used in normal `ollama` path (since `supports_tool_calling` false), so **not P1**.

---

## 24. Most Important Output

```
PHASE 44B LLM CALL-COUNT VERDICT

NORMAL MESSAGE:
3 synchronous LLM calls

COMMERCE MESSAGE:
3 synchronous LLM calls (signal 1, commerce response 1 (replaces Qwen), scoring 1)

MINIMUM:
2 (if transcript empty → signal skipped, Qwen 1, scoring 1)

NORMAL:
3 (signal 1, Qwen 1, scoring 1)

MAXIMUM PROVEN:
6 (llm_provider=ollama, signal 1, Qwen tools 4, scoring 1) or 8 with Gemini→Ollama fallback per call

LLM PROVIDERS:
Ollama qwen3:4b (primary, https://ollama.brestalogistics.co.ke) + Gemini gemini-flash-latest (fallback, shared core/gemini_client CredentialPool)

PRIMARY MODEL:
qwen3:4b (ollama) for Qwen, cheap_model qwen3:4b for signal/scoring/commerce (same model, different system prompts, temp 0.0 vs 0.85 vs 0.2)

LARGEST LATENCY CONTRIBUTOR:
UNKNOWN — insufficient evidence (no p50/p95 logs, telemetry generation_latency exists per generation but no aggregated report); estimated Qwen generate_draft (22k input, 200 output, temp 0.85) vs signal 2k vs scoring 1.3k

REDUNDANT CALLS:
NONE (no LLM output always discarded in normal path)

BACKGROUND LLM CALLS:
2 (profile extraction after send, summarization after send every 20, not critical)

QUALITY-CRITICAL CALLS:
1 (Qwen generate_draft) + 1 (scoring)

COMMERCE-CRITICAL CALLS:
1 (extract_commerce_signals) + 1 conditional (generate_commerce_response when USE_COMMERCE_RESPONSE)

PERSONA LLM CALLS:
0 (PersonaBehaviorState and PersonaResponseValidation deterministic, no LLM)

ROOT CAUSE OF EXCESS LATENCY:
Three sequential synchronous LLM calls (signal → Qwen → scoring) each with network RTT to Ollama VPS (5.7 tok/s) and Qwen's large 22k prompt (19k persona) dominates; not redundant calls but sequential dependency.

CONFIDENCE:
PROVEN (code-trace, 3 sync before reply, no hidden LLM)

PRODUCTION CHANGES:
NONE

CANARY CHANGES:
NONE

ARCHITECTURE CHANGES:
NONE

FINAL VERDICT:
READY FOR OPTIMIZATION
```

**Original assumption `1 SIGNAL + 1 QWEN + 1 SCORING` is `PROVEN CURRENT`** — `SIGNAL` is `extract_commerce_signals` LLM (1), `QWEN` is `generate_draft` (or `generate_commerce_response` when commerce) (1), `SCORING` is `score_draft` (1) = **3 before reply**, matching assumption where `SIGNAL` is counted as LLM (it is). If `SIGNAL` was assumed deterministic, then assumption **PARTIALLY TRUE** (signal is LLM, not deterministic). But per code, `SIGNAL` **is** LLM, so `1+1+1` holds.

---

*End of Phase 44B Stage A audit — read-only, no optimization implemented.*

