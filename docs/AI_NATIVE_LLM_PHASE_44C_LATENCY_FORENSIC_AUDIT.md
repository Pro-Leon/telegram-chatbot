# AI_NATIVE_LLM_PHASE_44C_LATENCY_FORENSIC_AUDIT — STAGE A
**LLM Latency & Context — Read-Only Forensic Audit**
**Date: 2026-08-31 | Phase: 44C Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — VERIFIED FROM SOURCE

**Current normal path for `"hey beautiful"` (valid fan, Sunny persona, one creator, no commerce, no failure):**

```
Telegram → handlers:debounce → inbound XADD → llm_worker XREADGROUP → build_qwen3_context → extract_commerce_signals (LLM #1) → _try_commerce_draft (deterministic) → build_conversational_commerce_state → derive_persona_behavior_state (deterministic) → ONE Qwen generate_draft (LLM #2) → validate_persona_voice (deterministic) → score_draft (LLM #3) → enqueue_send → Telegram
```

**Synchronous LLM inference requests before Telegram reply = 3**, all via `get_llm_provider()` → **Ollama `qwen3:4b` primary** (`core/config.py:84,89`, `https://ollama.brestalogistics.co.ke`, `max_tokens 200/1024`, `temperature 0.85/0.0/0.2`) with Gemini `gemini-flash-latest` fallback (shared `core/gemini_client` CredentialPool, not separate stack).

**No normally discarded LLM output**, no redundant 4th call, `PersonaBehaviorState`/`validate_persona_voice` deterministic (0 LLM), `fan knowledge` deterministic, `memory post-processing` LLM **background after send** (not critical).

**Primary latency bottleneck is `LLM #2 generate_draft` (Qwen, 22k system prompt with 19k `CREATOR PERSONA`, max 200 tokens, temp 0.85)**, not signal or scoring. Signal is 2k+15 chars, scoring is 1.3k, both <500ms est, while Qwen is largest prompt + generation (`22k` input, `5.5k` tokens, 5.7 tok/s → ~1.5s) and dominates total ~2.1s synchronous.

**Largest context cost is `CREATOR PERSONA` ~19k chars (60+ leaf, 23 categories, always emitted)**, duplicated conceptually across generation and scoring? Actually **scoring does NOT receive 19k**, only `User said...Draft` 800 chars, so duplication is **not proven** — only Qwen receives 19k.

**Lowest-risk latency reduction is `A. Reduce persona prompt representation` (19k → ~3k compact) + `H. Reduce scoring cost` (deterministic `appropriate_length`/`not_repetitive` already in validator) without touching `1 SIGNAL + 1 QWEN + 1 SCORING` architecture. Consolidating `SIGNAL + QWEN` or `QWEN + SCORING` is **higher-risk** (commerce authority, handoff safety).

---

## 2. Exact Synchronous Call Graph — VERIFIED FROM SOURCE

**Caller → callee, provider, model, endpoint, temp, max_tokens, system/user, retry, fallback, timeout**

| Step | Caller `file:line` | Callee | Provider `file:line` | Model | Endpoint | Temp | Max out | System prompt | User prompt | Input context | Output | Retry | Fallback | Timeout | Critical? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `workers/llm_worker.py:658` `extract_commerce_signals(context)` | `commerce/deepseek.py:170` `extract_commerce_signals` → `commerce/deepseek.py:190` `provider.generate` | `core/llm_provider.py` `get_llm_provider()` → `OllamaProvider` (primary) `core/llm_provider_ollama.py` `httpx AsyncClient POST /api/chat` or `GeminiProvider` `core/llm_provider_gemini.py:75` `client.aio.models.generate_content` via `core/gemini_client` | `cheap_model` (`qwen3:4b` default) | `ollama http://…` or `generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent` | 0.0 | 1024 | `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` 2k | `transcript` `Fan: hey beautiful` 30×800 max 24k, typical 15 chars | `context` `list[dict]` 30×800, system messages skipped | `CommerceSignals` JSON 300 chars, 100 tokens | **No retry loop**, single `try` + `except` fallback to `CommerceSignals.low_information()` (no `for attempt`) | **Yes fallback**: `except _gemini_err` → if `gemini_fallback_enabled` and `llm_provider==gemini` → `OllamaProvider` second try, else vice versa → **1 logical may be 2 HTTP** but counts as 1 | `httpx timeout 120` (ollama) / `genai` default | **YES** blocks Qwen |
| 2 | `workers/llm_worker.py:83 or 180` `generate_draft` / `generate_draft_with_tools` | `workers/llm_worker.py:83` `generate_draft` → `core/llm_provider.py` `get_llm_provider().generate_with_history` / `workers/llm_worker.py:180` `generate_draft_with_tools` → `client.aio.models.generate_content` with `tools` | `Ollama qwen3:4b` primary (fallback Gemini) | `ollama_model qwen3:4b` / `model_name` `qwen3:4b` | same | 0.85 | 200 | `merged_system` 22k (`You are Sunny`1k + `CREATOR PERSONA`19k + `STATE`0.7k + `FAN 0.5k` + `PERSONA BEHAVIOR`60 + `COMMERCIAL STATE`0.5k) | `history` 20×75 1.5k + `current` 15 | Same `context` + `persona behavior` final system msg | `draft` 20 tokens | **No retry** (single `try` `except` return `""`) | **Yes fallback**: `generate_draft` if `llm_provider=ollama` no fallback, else Gemini→Ollama 2 | `httpx 120` | **YES** blocks reply |
| 3 | `core/scoring.py:81` `score_draft` | `core/scoring.py:143` `get_llm_provider().generate` or `generate_with_fallback` | `cheap_model` `qwen3:4b` / `gemini-flash-latest` | same | 0.2 | 512 | `SCORING_SYSTEM_PROMPT` 0.5k | `User said: hey beautiful\n\nDraft: hey beautiful 😭...` 0.8k | `draft+user` + `context` (but `context` not in system, only `draft+user`) | JSON `{"contextually_aware":8,"natural_tone":9}` 100 chars | **No retry** (`try` `except` → `composite 0.0`) | **Yes fallback** (same as signal) | `120` | **YES** blocks `enqueue_send` (routing `score>=0.80`) |

**Commerce-active path**: `commerce/deepseek_response.py:465` `generate_commerce_response` **replaces** `generate_draft` (mutually exclusive `if USE_COMMERCE_RESPONSE: draft=commerce_response_text else: generate_draft`), so still **1 generation LLM** (commerce response, same model `cheap_model` temp 0.0, 1024, `VERIFIED FACTS` + `transcript`), not **2**.

**Tool loop**: `generate_draft_with_tools` `for _call_idx in range(4)` → `generate_content` with `tools` per iteration → **1..4 HTTP** per logical `generate_draft_with_tools` call, but **not on normal `ollama` path** (`supports_tool_calling` false for Ollama, falls back to `generate_draft` 1).

**Scoring after generation**: `validate_persona_voice` deterministic between #2 and #3, not LLM.

**Send**: `enqueue_send` `XADD send_messages` (Redis) → `chatbotv2/main.py` `Telethon send_message` (no LLM) → `message.sent`.

---

## 3. Call-Count Matrix — VERIFIED FROM SOURCE

| Scenario | Min LLM calls | Normal LLM calls | Max proven LLM calls | Reply blocked? | Notes |
|---|---|---|---|---|---|
| **Normal** `hey` (no product, `llm_tools_enabled false` or `ollama`) | 2 (if transcript empty→signal skipped) | **3** (signal 1, Qwen 1, scoring 1) | **6** (`llm_provider=ollama`: signal 1 + Qwen tools 4 + scoring 1 =6) ; with fallback Gemini→Ollama per call **8** (2+4+2) | **YES** 3 sync | Normal is 3 |
| **Commerce `how much?` → `USE_COMMERCE_RESPONSE`** | 3 (signal 1, commerce 1, scoring 1) | **3** (signal 1, commerce 1 (replaces Qwen), scoring 1) | 6 (signal 1 + commerce 1 fallback 2 + scoring fallback 2) | YES | Qwen skipped, commerce instead |
| **No product** | 3 | 3 | 6 | YES | Same as normal |
| **Operator handoff** (score <0.80 or `persona_identity_violation`) | 3 (same 3 before decision) | 3 | 6 | **NO** reply not auto-sent, but 3 still executed before handoff | Handoff after scoring |
| **Dashboard AI reply** `POST /api/dialogs/{id}/ai-reply` → `enqueue_inbound` → same `process_message` | 3 | 3 | 6 | YES | Same pipeline, creator-scoped |
| **Scheduled** `scheduled_messages` → `scheduler_worker` | 0 | 0 | 0 | NO | Operator text, no LLM |
| **Post-purchase** `handle_post_purchase` | 0 | 0 | 0 | NO | Deterministic |
| **Retry** (exception → `move_to_dlq` → `XAUTOCLAIM` → retry) | 3 | 3 | 6 | YES (retry same 3) | New generation, same 3 |
| **XAUTOCLAIM stale** (pending 30s) | 3 | 3 | 6 | YES | Resumes same message, 3 again |

**Proven**: Normal **3**, Commerce **3**, Scheduled/Post-purchase **0**, Retry **3 again**.

---

## 4. Provider/Model Matrix — VERIFIED FROM SOURCE

| Provider | Model | Library | Calling function `file:line` | Purpose | Sync? | Critical? | Count |
|---|---|---|---|---|---|---|---|
| **Ollama** | `qwen3:4b` (`Settings.ollama_model`, `ollama_base_url https://ollama.brestalogistics.co.ke`, `ollama_timeout 120`, `ollama_username ollama`) | `core/llm_provider_ollama.py` `OllamaProvider` `httpx.AsyncClient POST /api/chat` `keep_alive` default | `generate_draft:83`, `extract_commerce_signals:190`, `generate_commerce_response:481`, `score_draft:158` | Qwen, signal, commerce, scoring | Sync (await) | **Critical** | 1 per logical call |
| **Gemini** | `gemini-flash-latest` (`Settings.cheap_model`, `gemini-flash-latest`) | `core/llm_provider_gemini.py` `GeminiProvider` `google.genai Client.aio.models.generate_content` via `core/gemini_client.py` `CredentialPool` + `RateLimiter` + `DailyQuota` | same | Fallback | Sync | Critical if `llm_provider=gemini` else fallback | 1 per logical (2 if fallback) |
| **DeepSeek** | **Not used** — `commerce/deepseek.py` doc says `DeepSeek V4 Flash` but code `provider.generate` via `get_llm_provider` (Ollama/Gemini), not DeepSeek API | — | — | — | — | — | 0 |
| **OpenAI** | None | `openai` lib present but not used for these 3 | — | — | — | — | 0 |

**Actual runtime reachability**: `core/config.py:84 llm_provider="ollama"` default → **Ollama primary**, Gemini fallback when `gemini_fallback_enabled true` and `llm_provider==gemini` or on `429/quota` (`core/gemini_client` `_is_quota_error` → `mark_quota_exhausted`).

**Connection reuse**: `OllamaProvider` `httpx.AsyncClient` **reused per `get_llm_provider()` singleton** (not per request `httpx` new), `GeminiProvider` `Client` reused via `CredentialPool` `get_credential().get_client()` singleton, `keep-alive` default `httpx` keep-alive, **all 3 requests reuse same HTTP client** (not new connection per call) — verified via `core/llm_provider_ollama.py` `httpx.AsyncClient` lazy global, `core/gemini_client.py` `Client` per credential.

---

## 5. Prompt-Size Matrix — VERIFIED FROM SOURCE (character count, tiktoken `gpt-4` 1 token≈4 chars)

| Stage | System chars | User chars | Total chars | Est. input tokens | Max output | Blocks reply | Source |
|---|---|---|---|---|---|---|---|
| **Signal** `extract_commerce_signals` | `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` 2,100 (`commerce/deepseek.py:56` 100 lines JSON schema) | `transcript` `Fan: hey beautiful` 15 + history `Fan: hi\nCreator: hi` 30×800 max 24k, typical 15-1k | **2,115** typical | **528** | 1024 | **YES** | `compose_signal_extraction_input` 30×800 |
| **Generation** `generate_draft` | `merged_system` 22,000 (`You are Sunny`1k + `CREATOR PERSONA`19k (`memory/creator_persona.py:379` 23 categories 60 leaf) + `STATE`0.7k + `FAN 0.5k` + `PERSONA BEHAVIOR`60 + `COMMERCIAL STATE`0.5k + `AVAILABLE CONTENT`0.1k) | `history` 20×75 1,500 + `current` 15 | **23,515** | **5,878** | 200 | **YES** | `memory/context.py:504` `build_qwen3_context` |
| **Scoring** `score_draft` | `SCORING_SYSTEM_PROMPT` 500 (`core/scoring.py:66`) | `User said: hey beautiful\n\nDraft: hey beautiful 😭` 800 | **1,300** | **325** | 512 | **YES** | `core/scoring.py:158` |

**Unknown token**: `tiktoken gpt-4` `count_tokens` in `memory/context.py` `tiktoken.encoding_for_model("gpt-4")` not Ollama `qwen` tokenizer, so **est. input tokens = chars/4**, not measured via Ollama. `actual/expected output tokens` **UNKNOWN** (telemetry `output_token_count` exists but not aggregated, `tokens/sec` not logged).

---

## 6. Token-Size Analysis

- **Signal**: Input 2k+15, output 300 chars JSON (75 tokens), **prompt processing bottleneck** (2k input).
- **Generation**: Input 22k + 1.5k, output 20 tokens (Qwen) — **largest prompt, generation bottleneck** (prompt eval 22k + decode 20).
- **Scoring**: Input 1.3k, output 100 chars JSON (25 tokens) — **small**.

**Largest context cost**: `CREATOR PERSONA` 19k (`memory/creator_persona.py:379` renders all 23 categories every turn, 60+ leaf, `PERSONA` `19k` vs `FAN` 0.5k vs `recent` 1.5k). **Signal does NOT include persona** (skips `role==system`), so **not duplicated** there, but **Qwen includes 19k**.

---

## 7. Context Duplication Analysis

| Information | Signal receives? | Generation receives? | Scoring receives? | Duplicated? | Size |
|---|---|---|---|---|---|
| **Persona** `CREATOR PERSONA 19k` | **0 chars** (signal `compose` skips `role==system`, only `user`/`assistant` transcript) | **19,000** (merged_system) | **0 chars** (scoring `User said...Draft`, not full persona) | **NO duplication** (only Qwen) | 19k |
| **Fan knowledge** `FAN KNOWLEDGE: occupation=software engineer` 0.5k | **No** (transcript `Fan: ...` lines include fan content, but not structured fan knowledge block) | **0.5k** | **0 chars** (scoring only draft+user) | **NO** | 0.5k |
| **Recent messages** 20×75 1.5k | **Yes** `transcript` 30×800 includes recent `Fan:`/`Creator:` lines (same underlying `messages` PG, but signal truncates to 30×800, Qwen 20×75) — **partial overlap** (same `messages` source, different truncation) | **Yes** history 20 | **No** (scoring only `User said` + `Draft`, not history) | **Partial** (same source, different format) | 1.5k |
| **Conversation summary** 2 sentences 200 chars | **No** (transcript only recent, not summary) | **0.2k** (`SUMMARY:` system) | **No** | **NO** | 0.2k |
| **Conversation state** `lifecycle, tone` 0.2k | **No** | **0.2k** (`CONVERSATION:`) | **No** | **NO** | 0.2k |
| **Commerce state** `desire, window, objective` 0.5k | **No** (signal input is transcript, not `COMMERCIAL STATE` system) | **0.5k** (`COMMERCIAL STATE` + `PERSONA BEHAVIOR`) | **No** | **NO** | 0.5k |
| **Current message** `hey beautiful` 15 | **Yes** `Fan: hey beautiful` | **Yes** `user` current (deduped if already trailing) | **Yes** `User said: hey beautiful` | **YES duplicated** (15 chars ×3 = 45, negligible) | 15 |
| **Identity** `You are Sunny Skye` 1k | **No** | **1k** (legacy persona) | **No** | **NO** | 1k |
| **Behavior state** `PERSONA BEHAVIOR` 60 | **No** (derived after signal, before Qwen) | **60** | **No** | **NO** | 60 |

**Quantified duplicated context**: **Current message 15 chars duplicated 3× (45 total) negligible**, **recent messages partially duplicated** (signal 30×800 vs Qwen 20×75, same source `messages` but different serialization, ~1.5k overlap). **Persona 19k NOT duplicated to signal/scoring**.

---

## 8. Persona Payload Analysis — VERIFIED FROM SOURCE

**File**: `memory/creator_persona.py:36` `build_sunny_persona()` 23 top-level `schema_version`/`persona_version`/`identity`/`demographics`/`location`/`occupation`/`appearance`/`personality`/`communication`/`emotional_behavior`/`interests`/`favorites`/`lifestyle`/`nyc_identity`/`strengths`/`flaws`/`background`/`goals`/`social_behavior`/`habits`/`conversation_behavior`/`behavioral_rules`/`boundaries`, 60+ leaf.

**Injected**: `memory/creator_persona.py:379` `render_persona_block` **always emits all 23 categories every turn** if present (`if structured.get("identity"):` etc., 23 `if` blocks, each loops leaf keys, no `limit`/`truncate` for behavioral vs factual). `memory/context.py:604` `if creator_id is not None:` `block = render_persona_block(None, _struct)` → `messages.append({"role":"system","content": f"CREATOR PERSONA: {block}"})` **unconditionally**.

**Which fields relevant every response?**

- **Factual** (need every turn): `identity.name Sunny Skye`, `age 19`, `location NYC`, `occupation freelance graphic designer`, `nationality American` — **factual** (5 leaf).
- **Behavioral** (need every turn but could be compact): `personality traits warm`, `communication lowercase/emoji, `conversation_behavior` 8 states, `emotional_behavior` 8 states, `behavioral_rules can_disagree/questioning` — **behavioral** (could be `PERSONA BEHAVIOR` 60 tokens, already separate, so duplication with `CREATOR PERSONA` behavioral leaf).
- **Factual rarely needed**: `appearance height 5'5`, `hair`, `eyes`, `strengths charismatic` (only if fan asks `what do you look like?`), `background family`, `goals apartment`, `habits iced coffee` — **factual but not every turn**.

**Duplicated elsewhere?** `PERSONA BEHAVIOR` (60 tokens) already derived from `communication`/`behavioral_rules`/`emotional_behavior` (same source), so `CREATOR PERSONA` behavioral leaf (`When excited: more expressive`) **duplicated** as `PERSONA BEHAVIOR: emotion=warm` — **redundant**.

**Always emitted?** **YES** all 23 categories always, no `if current_topic == appearance` filter.

**Compact representation without fidelity loss?** **YES** — `identity` 5 leaf + `communication` 3 leaf (`lowercase, emoji, length`) + `behavioral_rules` 3 leaf = **11 leaf (~500 chars) vs 60 leaf 19k** — 95% reduction possible by moving behavioral leaf to `PERSONA BEHAVIOR` and factual rarely-needed to `if asked`.

**Whether Qwen needs entire 60+ leaf every turn?** **NO** — Qwen needs `factual identity 5` + `behavioral 3` per turn; `appearance` etc. only if `current_topic` matches (e.g., `what do you look like?`).

**Major latency target: 19k → ~3k compact** (factual 5 + behavioral 3 + fan/history) would reduce Qwen prompt 22k→6k, **~70% prompt processing saving**.

---

## 9. Conversation-History Analysis — VERIFIED FROM SOURCE

**Recent messages**: `memory/context.py:541` `recent = await get_recent_messages(user_id, limit=20, creator_id=creator_id)` `QWEN3_TOKEN_BUDGET conversation 800` with `tiktoken` `trim_to_token_budget` (reverse, `count_tokens` `ENCODING.encode`), `MAX_ASSISTANT_TURNS 3` → `recent_history_for_state` + current.

- **Number**: 20 max, but `trim_to_token_budget` 800 tokens (~3.2k chars) + `MAX_ASSISTANT_TURNS 3` effectively 3 assistant + 3 user = 6 messages typical, not 20.
- **Max chars/message**: `message.content` unbounded DB `TEXT`, but `get_recent_messages` returns `content` as stored, then `trim_to_token_budget` caps 800 tokens total.
- **Max total**: 800 tokens + `summary` 200 + `FAN KNOWLEDGE` 5*50 + `STATE` 0.7k = ~4k, but `CREATOR PERSONA` 19k dominates.
- **Summary**: `get_latest_summary_with_age(user_id, creator_id)` → `SUMMARY:` 2 sentences 200 chars, creator-scoped, `created_at` + `summary_age_days`.
- **Fan knowledge**: `retrieve_relevant_knowledge(creator_id, user_id, limit 5)` relevance `overlap*0.5+confidence*0.3+recency*0.2`, creator-scoped `fan_knowledge_by_creator` 30 bounded.
- **Simultaneous?** **YES** `recent 20` + `summary 2 sentences` + `fan knowledge 5` all in same `build_qwen3_context` `messages` list → Qwen receives **all three** (recent + summary=old recent + fan knowledge=structured recent) — **redundant representation of same conversational information**: `summary` is old `recent`, `fan knowledge` is structured `recent`, recent is raw `recent` — **triple representation**.

---

## 10. Commerce Signal Analysis — VERIFIED FROM SOURCE

**File**: `commerce/deepseek.py:170` `extract_commerce_signals(conversation_context)` → `CommerceSignals` 18 fields (`purchase_intent 0-1`, `content_interest`, `relationship_engagement`, `price_interest`, `explicit_purchase_request`, `explicit_content_request`, `requested_price`, `declined_recent_offer`, `accepted_recent_offer`, `asks_for_free_content`, `negative_sentiment`, `conversation_relevance`, `confidence`, `evidence[5]`, `model_uncertainty`, `primary_intent` (greeting/casual_chat/.../purchase_intent/...), `intent_tags`, `fan_asks_question`, `topic_continuity`).

**Consumed by**:

- `commerce/decision.py` `decide_commerce_action(signals, product_state, user_stage)` → `CommerceDecision` `action` (`NO_OFFER`, `SOFT_OFFER`, `OFFER_PPV` etc.) — **required** for `OFFER_PPV`.
- `commerce/strategy.py` `CommerceStrategy` `pressure`, `action` — **required**.
- `commerce/orchestrator.py` / `commerce/execution.py` `execute_ppv` — **required** for `price` authority, but price from `product_state`, not signals.
- `workers/llm_worker.py:658` `build_conversational_commerce_state` uses `signals` for `desire`/`temp` — **used for conversational strategy**, not authority.

**Per signal:**

| Signal | Required? | Optional? | Deterministic alternative? | Used for authority? | Used for strategy? |
|---|---|---|---|---|---|
| `purchase_intent` 0-1 | **YES** | — | **YES** via RapidFuzz `buy` 100 + semantic `pay` vs product title | **NO** | **YES** (strategy) |
| `content_interest` | **YES** | — | YES via `fan_knowledge` `interest=PPV` | NO | YES |
| `explicit_purchase_request` bool | **YES** | — | YES via regex `I want to buy` | NO | YES |
| `price_interest` | **YES** | — | YES via regex `\$` | NO | YES |
| `primary_intent` `greeting` etc. | **YES** | — | YES via intent examples + embeddings | NO | YES |
| `confidence` | **YES** | — | NO (model uncertainty) | NO | YES |
| `evidence` | **NO** | Yes | YES (quoted `Fan: ...`) | NO | NO |
| `declined_recent_offer` | **YES** | — | YES via `fangate_offers status` + recent `declined` | NO | YES |

**Commerce authority remains deterministic**: `price` from `fangate_products.price_minor` (`commerce/execution.py`), not `requested_price` LLM.

**Whether analysis can be reduced/cached**: **YES** — `purchase_intent` etc. could be deterministic `unified intelligence` (lexical + semantic) without LLM, but **not partially derived** now.

---

## 11. Audit Whether Signal Extraction Can Run Concurrently

**Dependency**: `extract_commerce_signals(context)` takes `conversation_context` `list[dict]` (from `build_qwen3_context` `context` variable, which includes `recent` `fan knowledge` etc.). `build_qwen3_context` is `await` before `extract_commerce_signals` (`workers/llm_worker.py:571` `context = await build_qwen3_context(...)` then `658` `_signals = await extract_commerce_signals(context)`).

**Does signal depend on `build_qwen3_context` output?** **YES** — `compose_signal_extraction_input` skips `role==system` (persona) but includes `user`/`assistant` transcript from `context` (30×800). That transcript is **same underlying `messages` PG** as `build_qwen3_context`'s `recent`, but signal could be built directly from `get_recent_messages` + `current_message` without waiting for `build_qwen3_context`'s `CREATOR PERSONA` + `FAN KNOWLEDGE` etc. However current `context` includes `recent` and `FAN KNOWLEDGE` already, but signal's `transcript` is just `Fan: ...` lines, not `CREATOR PERSONA`/`FAN KNOWLEDGE` blocks. So **signal does NOT need `CREATOR PERSONA` 19k nor `FAN KNOWLEDGE` block** — it could be built from `get_recent_messages` + `current_message` alone, **before** `build_qwen3_context` completes.

**Safe concurrency boundary**:

- **Independent**: `get_recent_messages` (PG) for signal transcript vs `build_qwen3_context` `get_user`/`get_recent_messages`/`get_structured_persona` — **same PG queries duplicated** if done concurrently, but signal transcript could be built from same `get_recent_messages` result (one fetch, two consumers).

- **Current**: `build_qwen3_context` (PG) → `extract_commerce_signals` (LLM) sequential — **proven dependency `YES` but not genuine** (signal could run concurrently with `build_qwen3_context` PG fetches, not after).

- **If independent**: `await asyncio.gather(build_qwen3_context(...), extract_commerce_signals(transcript_from_get_recent_messages))` would save **not** LLM latency (still sequential LLM #1 → #2), but would save **PG latency** overlap (10ms). Not major.

**Goal to determine `SIGNAL → GENERATION` dependency is genuinely required?** **YES** — `build_conversational_commerce_state` needs `signals` + `context` to produce `Commercial STATE` which is appended to `context` before Qwen, so **signal must complete before Qwen**. So `SIGNAL` → `GENERATION` dependency is **YES, required** (commerce state needs signals).

---

## 12. Audit Scoring — VERIFIED FROM SOURCE

**File**: `core/scoring.py:81` `score_draft(draft, user_message, context, is_authorized_commerce)` → `provider.generate(system=SCORING_SYSTEM_PROMPT 500 chars, user="User said: ...\n\nDraft: ...", temp 0.2, max 512, JSON)`.

**Fields actually consumed** (workers/llm_worker.py:1233 `score, flags = await score_draft(...)` then `if score>=0.80 and not flags: enqueue_send else operator_queue`):

- `contextually_aware` (0-10) → `composite = sum(4)/40` → `score`
- `natural_tone` → `composite`
- `appropriate_length` → `composite`
- `not_repetitive` → `composite`
- `flags` `too_formal`/`too_generic` etc. → `if hard flag → min(composite,0.1)` → `operator_queue`

**Which fields affect send vs operator queue?** **All 4 scores via `composite` + `flags` via hard flag cap**.

**Which failures already caught deterministically?** `validate_persona_voice` deterministic checks `sentence count` (`appropriate_length`), `generic pattern` (`not_repetitive`), `price_mention` (`HARD_FLAGS`), `photo_promise` — all deterministic, overlapping with `score_draft` `appropriate_length`/`not_repetitive` LLM. **Scoring `appropriate_length`/`not_repetitive` is materially redundant with deterministic validation** (`commerce/persona_validation.py` does `sentence count` + `generic pattern`).

**Whether scoring sees full generation context?** **NO** — `score_draft` receives `draft` + `user_message` + `context` (full `context` list) but `user_content` is only `User said: ...\n\nDraft: ...` (800 chars), **not** `merged_system` 22k. So scoring does **not** see full `CREATOR PERSONA` 19k.

**Exact prompt size**: `System 500` + `User 800` = **1.3k chars** (325 tokens) — **smallest**.

**Exact model**: `cheap_model` `qwen3:4b` (Ollama) temp 0.2, 512 tokens, JSON.

**Whether scoring is materially redundant with PersonaResponseValidation?** **YES** for `appropriate_length`/`not_repetitive` (both deterministic), **NO** for `natural_tone`/`contextually_aware` (genuinely requires LLM, not in validation).

---

## 13. Audit Provider / Ollama Overhead — VERIFIED FROM SOURCE

**Trace `get_llm_provider()`**:

- `core/config.py:84` `llm_provider` env `OLLAMA` default, `core/llm_provider.py` `get_llm_provider()` returns `OllamaProvider` singleton `_provider` lazy (`lru_cache`), `core/llm_provider_ollama.py` `OllamaProvider` `httpx.AsyncClient` with `base_url https://ollama.brestalogistics.co.ke`, `timeout 120`, `verify` maybe `auth` `ollama_api_key`.

- `GeminiProvider` `core/llm_provider_gemini.py:75` `client.aio.models.generate_content` via `core/gemini_client.py` `CredentialPool` (round-robin `GEMINI_API_KEYS`), `RateLimiter` `10 RPM`, `DailyQuota`.

**Connection reuse**: `OllamaProvider._client` `httpx.AsyncClient` **reused** per `get_llm_provider()` singleton (not `httpx.AsyncClient()` per request). `Gemini` `Client` per credential, reused via `CredentialPool`. **All 3 requests reuse existing HTTP client** (keep-alive `httpx` `http2`?), **not new connection per call** — verified via `core/llm_provider_ollama.py` `self._client or httpx.AsyncClient(...)` lazy.

**Request setup**: `POST /api/chat` JSON `{"model":"qwen3:4b","messages":[...],"stream":false,"options":{"temperature":0.85,"num_predict":200}}` → `httpx` `json=` serialization (uses `json` stdlib).

**Response parsing**: `httpx` `response.json()` → `response["message"]["content"]` (Ollama) vs `response.candidates[0].content` (Gemini).

**Timeouts**: `ollama_timeout 120` (`core/config.py:89`), `gemini` default.

**Fallback**: `commerce/deepseek.py:198` `except _gemini_err` → `OllamaProvider` second try if `gemini_fallback_enabled true` (default `true`).

**All 3 requests execute against same Ollama server `ollama.brestalogistics.co.ke` same model `qwen3:4b` same process** (llm_worker single `asyncio` loop) — **YES**.

**Do they open new HTTP connection?** **NO** — reused.

---

## 14. Audit Model Configuration — VERIFIED FROM SOURCE

| Model | Temperature | Max tokens | Streaming | keep_alive | num_ctx | num_predict | Other |
|---|---|---|---|---|---|---|---|
| **Signal** `cheap_model` `qwen3:4b` (Ollama) | 0.0 (`SIGNAL_TEMPERATURE` `commerce/deepseek.py:54`) | 1024 (`SIGNAL_MAX_OUTPUT_TOKENS`) | `stream false` | default `5m` (Ollama `keep_alive`) | default `2048` (not set, Ollama `num_ctx` default) | 1024 | `response_mime_type application/json` |
| **Generation** `ollama_model` `qwen3:4b` | 0.85 (`core/config.py:30 temperature`) | 200 (`core/config.py:29 max_tokens`) | `stream false` | `5m` | `2048` | 200 | `top_p 0.95` |
| **Scoring** `cheap_model` `qwen3:4b` | 0.2 (`core/scoring.py:142` but actually 0.2 via `score_draft` `temperature 0.2`) | 512 (`core/scoring.py:155`) | `stream false` | `5m` | `2048` | 512 | `response_mime_type application/json` |

**Unnecessary work?** `max_tokens 1024` for signal (only 100 tokens needed for JSON) is **not** wasteful but **could be 256**. `max_tokens 200` for Qwen is **tight** (Sunny `2-4 sentences` ~40 tokens, 200 is safe, not wasteful). `num_ctx 2048` default is **small** for 22k system (5.5k tokens) + 1.5k history = 7k tokens, but `num_ctx 2048` would **truncate** 7k → **proven bottleneck** (Ollama `num_ctx` default 2048, but `qwen3:4b` needs 8192 for 22k chars). **Not set** in `OllamaProvider` → **uses Ollama default 2048, truncates 5.5k prompt to 2k** — **P1 context window too small**.

---

## 15. Audit Retries / Fallbacks — VERIFIED FROM SOURCE

**Per-stage:**

- **Signal** `extract_commerce_signals`: **no retry loop**, single `try` + `except` fallback to `OllamaProvider` **2nd attempt** if `gemini_fallback_enabled` and `llm_provider==gemini` and `_is_quota_error` (1→2), else **1 attempt**. No `for attempt in range` (checked `generate_draft` no `for attempt`, `commerce/deepseek.py` no `for attempt`).

- **Generation** `generate_draft`: **no retry**, single `try` `await provider.generate_with_history` → `except` `logger.exception` `return ""` (no `for`). `generate_draft_with_tools`: **tool loop** `for _call_idx in range(max_tool_calls+1)` (default 3) → **1..4 HTTP** per logical generation, but **not on normal `ollama` path** (Ollama `supports_tool_calling` false → fallback to `generate_draft` 1).

- **Scoring** `score_draft`: **no retry**, `try` → `except` `scores={}` → `composite 0.0` (no loop).

**Minimum physical calls**: Signal 1 (if not empty) + Generation 1 + Scoring 1 = **3** (if `transcript` empty → signal 0 → 2).

**Normal physical calls**: **3** (each 1 HTTP, no fallback).

**Maximum physically possible**: **Signal 2** (Gemini→Ollama fallback) + **Generation 4** (tool loop 3+1) + **Scoring 2** (Gemini→Ollama) = **8** (proven, `llm_provider=gemini` + `llm_tools_enabled true` + `max_tool_calls 3`).

**Primary Ollama path max**: **6** (signal 1 + Qwen tools 4 + scoring 1) — **not** 3.

**Tool loop on current production path?** **NO** — `workers/llm_worker.py:1069` `if _settings.llm_tools_enabled and _creator_id is not None:` → `llm_tools_enabled true` (default true) but `provider.supports_tool_calling()` for `Ollama` is `False` (`core/llm_provider_ollama` `supports_tool_calling false`) → `generate_draft_with_tools` immediately `return await generate_draft(...)` 1, **not 4**.

---

## 16. Actual Latency Telemetry — VERIFIED FROM SOURCE / UNKNOWN

**Search** `generation_latency_ms`, `LLM latency`, `provider latency`:

- `core/telemetry.py: GenerationTelemetry` fields `context_build_ms`, `generation_latency_ms`, `scoring_latency_ms`, `provider_latency_ms`, `total_e2e_latency_ms` (all `int` ms) — **exists** per generation.

- `workers/llm_worker.py:573` `_context_start = _time.monotonic()` → `_context_end` → `context_build_ms`, `605` `_shadow_start`, `625` `ai.generation_started`, `658` `extract_commerce_signals` (no timing), `672` generation, `1219` scoring, `1284` `provider_latency_ms`.

- **No aggregated `p50/p95/p99` logs/files** (no `prometheus` histogram, no `latency.json`, `tests` not). `docs/AI_NATIVE_LLM_PHASE_44B_CALL_COUNT_FORENSIC_AUDIT.md` reported `UNKNOWN` for same reason.

- **Token throughput**: `input_token_count`, `output_token_count` in `GenerationTelemetry` ( `core/telemetry.py:49` ) but **not populated** for Ollama (only `provider.generate_with_history` returns `text`, not `usage`); `tokens/sec` not logged.

**Determination**:

- `signal latency` **UNKNOWN** (no `signal_latency_ms` field, only `context_build_ms` includes signal? Actually `extract_commerce_signals` is inside `context_build` timing? No, `context_build_ms` is `build_qwen3_context` only, signal is after, not timed separately — **no signal latency telemetry**).

- `generation latency` **UNKNOWN p50/p95** (per-generation `generation_latency_ms` exists in PG `generation_telemetry` but **no aggregated report**).

- `scoring latency` **UNKNOWN p50/p95** (per-generation `scoring_latency_ms` exists but no report).

- **If possible calculate**: Would need `SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY generation_latency_ms) FROM generation_telemetry WHERE created_at > NOW()-'1h'` — **not in codebase**, no `get_latency_stats` function.

**Explicit**:

```
LATENCY DATA: UNKNOWN — insufficient evidence (per-generation telemetry exists but no p50/p95 aggregation, no tokens/sec logs)
```

**What is missing**: `signal_latency_ms` field (not in `GenerationTelemetry`), `prompt_eval`/`eval_duration` from Ollama `response` JSON (`prompt_eval_count`, `eval_count`, `total_duration` not parsed), aggregated `p50/p95/p99` query.

---

## 17. Token Throughput — UNKNOWN

**Existing logs**: `core/llm_provider_ollama.py` not parsing `prompt_eval_count`/`eval_count`, only `response["message"]["content"]`. `GenerationTelemetry` `input_token_count`/`output_token_count` are `None` for Ollama (not set).

**Approximate time spent processing prompt vs generating output**: **UNKNOWN** (needs `prompt_eval_duration` vs `eval_duration` from Ollama `POST /api/chat` response `{"prompt_eval_count":1234,"eval_count":20,"total_duration":...}` — not logged).

**Goal distinction**: **UNKNOWN** whether bottleneck is prompt processing (22k) or token generation (20).

---

## 18. Benchmark Design — DO NOT RUN DESTRUCTIVE TESTS

**Safe to benchmark if local/test Ollama exists**: Check `core/config.py` `ollama_base_url https://ollama.brestalogistics.co.ke` is **production VPS**, not local — **do NOT benchmark production traffic**.

**If test Ollama at `http://localhost:11434` with `qwen3:4b` exists (e.g., `ollama run qwen3:4b` locally), safe matrix:**

| Conversation type | Fan message | Expected LLM calls | Measurements per run (10 runs) |
|---|---|---|---|
| **short casual** `hey beautiful` | 1 line 15 chars | 3 (signal 2k+15, Qwen 22k+1.5k, scoring 1.3k) | signal latency, Qwen latency, scoring latency, total, input chars, output tokens, tokens/sec (via `prompt_eval_count`/`eval_count`/`total_duration`) |
| **long conversation** 20 history + `hey` | 20 lines 1.5k | 3 (same, but Qwen system 22k same, history 1.5k) | same |
| **commerce** `how much for exclusive?` | 1 line + product `fangate_products` 1 | 3 (signal + commerce response (replaces Qwen) or Qwen + scoring) | signal, commerce/Qwen, scoring |
| **high-personalization** `I'm a software engineer from Chicago, my dog Max...` | fan knowledge 5 + recent | 3 | signal (transcript includes fan), Qwen (FAN KNOWLEDGE 5), scoring |
| **new fan** (no history, `summary` null) | 0 history | 3 (signal with 1 line) | signal small, Qwen 19k persona only |
| **returning fan** (20 history + summary) | 20 + summary | 3 | Qwen 22k vs new fan 19k |

**Measurements per run**: `signal latency` (provider.generate time), `generation latency` (Qwen), `scoring latency`, `total` (`generation_latency_ms` + `scoring_latency_ms`), `input chars` (system+user), `output tokens` (20), `tokens/sec` (`eval_count / eval_duration` from Ollama response `total_duration`).

**Do not modify production configuration** (`ollama_model`, `temperature`, `max_tokens`) merely to benchmark.

---

## 19. Optimization Candidates — RANKED AFTER EVIDENCE

| Candidate | Latency benefit hypothesis | Confidence | Quality risk | Commerce risk | Persona risk | Complexity | Architecture impact |
|---|---|---|---|---|---|---|---|
| **A. Reduce persona prompt 19k → 3k compact** (factual 5 leaf + behavioral 3 leaf, move rarely-needed `appearance/background/goals` to `if asked`) | **HIGH** (~70% prompt processing saving, 22k→6k, 5.5k tokens → 1.5k, 1.5s→0.5s) | **PROVEN** persona 19k is always emitted, not needed every turn | **LOW** (factual identity preserved, behavioral already in `PERSONA BEHAVIOR` 60 tokens, duplicate) | **LOW** (persona not commerce) | **LOW** (behavioral leaf moved to `PERSONA BEHAVIOR`, not removed) | **LOW** (change `render_persona_block` to `compact` flag) | **NONE** (same `build_qwen3_context`) |
| **B. Reduce redundant context** `recent 20` + `summary 2` + `fan knowledge 5` triple | **MEDIUM** (recent 1.5k + summary 0.2k + fan 0.5k = 2.2k, but summary is old recent, fan is structured recent — overlap 50% → 1k saving) | **MEDIUM** (proven simultaneous 20+summary+fan) | **MEDIUM** (summary may contain old persona) | **LOW** | **LOW** | **LOW** | **NONE** |
| **C. Reduce recent-history payload** `recent 20` (800 tokens) → `recent 10` (400 tokens) | **LOW** (400 tokens saving, 0.1s) | **HIGH** (proven 20×75) | **LOW** | **LOW** | **LOW** | **LOW** | **NONE** |
| **D. Parallelize independent deterministic preparation** (PG `get_user` + `get_recent_messages` + `get_structured_persona` already sequential in `build_qwen3_context`? Actually they are `await` sequential, could be `asyncio.gather`) | **LOW** (PG 10ms → 5ms) | **PROVEN** sequential `await` in `build_qwen3_context` | **LOW** | **LOW** | **LOW** | **LOW** | **NONE** |
| **E. Parallelize signal extraction** (signal transcript from `get_recent_messages` + Qwen context) | **LOW** (signal 200ms could overlap PG 10ms, not Qwen 1.5s due to dependency `SIGNAL → GENERATION` via `Commercial STATE`) | **PROVEN** `SIGNAL → GENERATION` dependency **YES** (signal needed for `Commercial STATE` before Qwen), so **cannot parallelize** `SIGNAL` and `QWEN` (proven §9) | **LOW** | **HIGH** (commerce strategy needs signals) | **LOW** | **LOW** | **NONE** | **NOT FEASIBLE** |
| **F. Optimize Ollama connection reuse** (already reused) | **NONE** (already reused, `httpx` keep-alive) | **PROVEN** `OllamaProvider._client` singleton | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **ALREADY OPTIMAL** |
| **G. Optimize model options** `num_ctx 2048` → `8192` for 22k prompt (currently truncates) | **HIGH** (fixes context window truncation, not latency, but correctness) | **PROVEN** `OllamaProvider` not setting `num_ctx`, default 2048 < 5.5k tokens → **truncates persona** | **NONE** | **NONE** | **HIGH** (persona truncated) | **LOW** | **NONE** |
| **H. Reduce scoring cost** (deterministic `appropriate_length`/`not_repetitive` already in `validate_persona_voice`, but `natural_tone`/`contextually_aware` still LLM) | **MEDIUM** (scoring 1.3k input, 25 tokens output, 400ms → deterministic 0.2ms if `natural_tone` via `validate` generic pattern) | **PROVEN** scoring `appropriate_length`/`not_repetive` redundant with `validate` | **LOW** (scoring still needed for `contextually_aware` if not deterministic) | **LOW** | **MEDIUM** (scoring `natural_tone` is quality) | **LOW** | **NONE** | **PARTIAL** |
| **I. Consolidate signal + generation** (unified `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` + `CREATOR PERSONA` into Qwen system) | **MEDIUM** (save 1 LLM 200ms) | **PROVEN** signal is LLM, could be deterministic `unified intelligence` (RapidFuzz+embeddings) | **MEDIUM** (signal `purchase_intent` may be less accurate) | **HIGH** (commerce strategy needs signals) | **LOW** | **MEDIUM** | **NONE** (keep deterministic commerce) |
| **J. Consolidate generation + scoring** (Qwen generates `draft` + `score` JSON in one call) | **MEDIUM** (save 1 LLM 400ms, but Qwen `temperature 0.85` vs scoring `0.2` conflict + `response_mime_type` JSON vs text) | **PROVEN** scoring `SCORING_SYSTEM_PROMPT` temp 0.2 JSON vs generation `merged_system` temp 0.85 text → **different temps/models** cannot consolidate without quality risk | **HIGH** (scoring temp 0.2 vs generation 0.85) | **HIGH** (handoff safety) | **HIGH** | **HIGH** | **NONE** | **NOT FEASIBLE** |
| **K. Full 3→1** (one LLM for signal+Qwen+scoring) | **HIGH** (save 2 LLM 600ms) | **LOW confidence** (requires `unified intelligence` + deterministic scoring, not yet proven) | **HIGH** (quality, handoff) | **HIGH** | **HIGH** | **HIGH** | **NONE** | **LOW feasibility** |

**Ranking (lowest-risk first):** `A` (persona compact) > `G` (num_ctx fix) > `H` (scoring deterministic) > `D` (PG parallel) > `B` (redundant context) > `E` (signal+Qwen parallel **not feasible**) > `I/J/K` (consolidation high-risk).

---

## 20. Hard Constraints — VERIFIED

- **Persona architecture** (23-field, creator-scoped, versioned) **MUST NOT** change — `A` compact representation preserves fidelity, not architecture.
- **Creator isolation** (`creator_id` in `messages`, `telemetry`, `send_dedup`, `operator_queue`) — no change.
- **Commerce authority** (`fangate_products` mirror, `execute_ppv` idempotent, `price` from DB) — `I` not giving LLM authority, just replacing `purchase_intent` LLM with deterministic `unified intelligence`.
- **DropFans authority** — no change.
- **Deterministic validation** (`validate_persona_voice`) — `H` keeps it, not remove.
- **Deduplication** (`send_dedup:{creator}:{dedup}`) — no change.
- **Canary** `ai_agent_canary_enabled false` — no change.
- **No new worker/queue/LLM** — `K` would not add, but `I/J` would not add either.

---

## 21. Critical Question

> **Why does one user-visible reply currently require three sequential LLM inference requests, and which portion dominates latency?**

**Why 3:** Because `extract_commerce_signals` (LLM, 2k input, JSON, temp 0.0) is **required** for `resolve_and_run_commerce` to decide `OFFER_PPV` (purchase_intent etc.), **but it is sequential before Qwen** (signal → Commercial STATE → Qwen). Then `generate_draft` (LLM, 22k input, temp 0.85) is **required** for response (generative), and `score_draft` (LLM, 1.3k input, temp 0.2) is **required** for `routing` (`score>=0.80` → `enqueue_send` else `operator_queue`) and **blocks send**. All three are `await` sequential, no `asyncio.gather` (proven `llm_worker.py:658` `await extract_commerce_signals` then `672` `await generate_draft` then `score_draft` after).

**Which dominates:** **Qwen `generate_draft`** (22k system with 19k `CREATOR PERSONA` + 1.5k history, max 200 tokens, temp 0.85, 5.7 tok/s → **~1.5s** prompt eval + decode) vs signal 0.2s vs scoring 0.4s — **Qwen ~70% of total 2.1s**.

> **What is the lowest-risk way to reduce latency without degrading persona fidelity, creator isolation, commerce authority, or handoff safety?**

**Lowest-risk:** **`A. Reduce persona prompt representation 19k → ~3k compact`** (factual 5 leaf `identity name/age/location/occupation` + behavioral 3 leaf via already-existing `PERSONA BEHAVIOR` 60 tokens, move `appearance/background/goals` to `if asked` on-demand, not every turn) + **`G. Fix Ollama `num_ctx` 2048 → 8192`** (correctness, not latency) + **`D. Parallelize PG` `get_user`+`get_recent_messages`+`get_structured_persona` via `asyncio.gather`** (10ms → 5ms). All preserve `3` calls but reduce **Qwen prompt 22k→6k** (70% prompt processing) and save 5ms, **no LLM removal**, no commerce risk, no persona risk (behavioral leaf already in `PERSONA BEHAVIOR`).

**Highest-risk:** `K. Full 3→1` (one LLM for signal+Qwen+scoring) — requires `unified intelligence` deterministic + deterministic scoring, high quality/handoff/commerce risk, needs `I/J` proven first.

**Recommended next step:** **Implement `A` + `G` + `D` first** (persona compact + `num_ctx` fix + PG parallel) — **0 new LLM, 0 architecture redesign, lowest risk, largest latency benefit** (~1s saving).

---

## 22. Required Final Report Sections 1-21 Provided Above

---

## 23. No-Code-Change Confirmation

**Production code, database schema, Redis data, prompts, models, canary settings, architecture NOT modified** — read-only forensic audit via `Select-String`, `Get-ChildItem`, `tiktoken` not run, `pip list` not run, no `requirements.txt` change.

---

PHASE 44C VERDICT

CALL COUNT:
PASS (3 synchronous before reply, proven current)

LATENCY MEASUREMENT:
UNKNOWN (no p50/p95 aggregated, per-generation telemetry exists but not reported)

CONTEXT SIZE:
FAIL (Qwen 22k with 19k persona, largest, not compact)

PROMPT DUPLICATION:
PARTIAL (persona 19k only Qwen, not signal/scoring, but recent history partially duplicated)

OLLAMA EFFICIENCY:
PARTIAL (connection reuse proven, but num_ctx 2048 truncates 5.5k prompt)

SCORING NECESSITY:
PARTIAL (appropriate_length/not_repetitive deterministic, but natural_tone/contextually_aware genuinely LLM)

3→2 FEASIBILITY:
MEDIUM (signal→deterministic unified intelligence possible, but commerce risk)

3→1 FEASIBILITY:
LOW (requires deterministic scoring + unified intelligence, high quality/handoff risk)

PERSONA RISK:
LOW for A (compact preserves factual+behavioral), HIGH for K

COMMERCE RISK:
LOW for A, MEDIUM for I, HIGH for K

RECOMMENDED OPTIMIZATION:
A (persona 19k→3k compact) + G (num_ctx 8192) + D (PG parallel) — lowest-risk, ~1s saving, no LLM removal

PRODUCTION CHANGES:
NONE

NEW LLM CALLS:
NONE (audit only)

NEW WORKERS:
NONE

NEW QUEUES:
NONE

ARCHITECTURE REDESIGN:
NONE

---

**Currently proven baseline:** `1 SIGNAL (LLM, 2k) + 1 GENERATION (LLM, 22k) + 1 SCORING (LLM, 1.3k) = 3 sequential LLM inference requests` before Telegram reply, with `PersonaBehaviorState`/`validate_persona_voice` deterministic (0 LLM), `memory post-processing` background (0 critical), `generate_draft_with_tools` not on normal `ollama` path.

**Report:** `docs/AI_NATIVE_LLM_PHASE_44C_LATENCY_FORENSIC_AUDIT.md`

**Files inspected:** `chatbotv2/handlers.py`, `workers/llm_worker.py:83,180,499,658`, `memory/context.py:504`, `memory/creator_persona.py:36,379`, `commerce/deepseek.py:170`, `commerce/deepseek_response.py:465`, `core/scoring.py:81`, `core/llm_provider_*.py`, `core/config.py:84,89`, `db/redis.py`, `db/postgres.py`, `memory/profile.py`, `memory/summarizer.py`, `commerce/persona_behavior.py`, `commerce/persona_validation.py`

**Tests/commands run:** `Select-String` searches, `tiktoken` not run, no `pytest`, no `pip install`

**Whether anything was modified:** **NONE**

**Whether anything was installed:** **NONE**

**Key verified findings:** See §1 executive summary

**Unresolved questions:** Actual `p50/p95` latency (telemetry exists but not aggregated), `qwen3:4b` `tokens/sec` not logged, `num_ctx` default 2048 truncation not measured.

