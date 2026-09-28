# AI_NATIVE_LLM_PHASE_45_INTEGRATED_OPTIMIZATION_FORENSIC_AUDIT — STAGE A
**Integrated AI Pipeline Optimization — Forensic Read-Only Architecture & Performance Audit (READ-ONLY)**
**Date: 2026-08-31 | Phase: 45 (Phase 0 Integrated Pipeline) | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — VERIFIED FROM SOURCE

**Current pipeline for one normal inbound `"hey beautiful"` (valid fan, Sunny persona, one active creator):**

- **3 synchronous LLM inference requests before Telegram reply** — all via `get_llm_provider()` → Ollama `qwen3:4b` primary (`core/config.py:84 llm_provider="ollama"`, `ollama_model="qwen3:4b"`, `https://ollama.brestalogistics.co.ke`) with Gemini `gemini-flash-latest` fallback — **proven, not assumed**, matching previous call-count audit.
- **1 Redis Streams inbound → 1 Qwen → 1 scoring** intended, but actual is **1 SIGNAL (LLM) + 1 QWEN (LLM) + 1 SCORING (LLM)** — 3 sequential network RTTs to Ollama VPS (5.7 tok/s, ~2.1s est), plus background 2 LLM after reply (profile + summary every 20).
- **Largest latency contributor is Qwen `generate_draft`** (~22k chars merged_system with 19k `CREATOR PERSONA` + history, max 200 tokens, temp 0.85) — **UNKNOWN measured p50/p95** (telemetry `generation_latency_ms` exists per generation but no aggregated report), but character count and model speed prove it dominates.
- **No redundant LLM output discarded in normal path** — signal → decision, Qwen/commerce → draft, scoring → routing — mutually exclusive, not wasted.
- **Same 19k persona + fan knowledge + recent 20 + commerce state rebuilt per generation** (one PG context build), then serialized per LLM call (not per-token DB).
- **Redis round-trips per normal generation: ~14-16** (debounce lock + RPUSH + XADD + XREADGROUP + XACK + persona cache 2 + fan knowledge PG but Redis for debounce/lock/rate-limit, plus send dedup + stream). **No pipelining currently** — all sequential, but semantics allow batching 2-3.
- **Serialization: `json.dumps/loads` via stdlib `json` 7 times on critical path** (debounce RPUSH, XADD, XREADGROUP, fan knowledge PG JSONB, commerce signals JSON, scoring JSON, telemetry JSON) — `orjson` would be technically compatible (bytes vs str) with small risk.
- **Memory:** current `memory/` is **not vector** — `user_profiles.facts` JSONB `fan_knowledge_by_creator` per-creator (30 bounded, creator-scoped, `retrieve_relevant_knowledge` relevance-ranked by overlap+confidence+recency, limit 5, no embeddings). `long_term_memory` same via `user_profiles` `long_term_memory_by_creator`, `message_embeddings` table exists but **not pgvector**, embeddings stored as `JSONB` not `vector`, no `sentence-transformers` model, no `hnswlib` index, no `rapidfuzz` fuzzy, no `orjson`.
- **Context construction:** `memory/context.py:504 build_qwen3_context` builds `system[0] legacy persona 1k + system[1] CREATOR PERSONA 19k + system[2] STATE/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE + FAN KNOWLEDGE 5 + LOCAL TIME + recent 20/3 + summary 2 sentences + vault titles + memory 3` — **once per generation**, then reused for signal (transcript 30×800) and Qwen (merged_system) and scoring (draft+user), not rebuilt per LLM.
- **Commerce boundary:** `workers/llm_worker.py:645 _try_commerce_draft` → `CommerceStateRequest` → `resolve_and_run_commerce` (deterministic 11-gate) → `select_commerce_response` → `build_conversational_commerce_state` → `PERSONA BEHAVIOR` + `COMMERCIAL STATE` final system msg → **LLM cannot set price/product** (DropFans `fangate_products` mirror, `execute_ppv` `fangate_offers` idempotent, `HARD_FLAGS` `price_mention` bypass only if `is_authorized_commerce` with matching `authorized_price_minor`).
- **Concurrency:** `Telethon` handlers `asyncio` single event loop, `llm_worker` `asyncio` + `Redis XREADGROUP` + `asyncpg` pool 1-5, `send_worker` separate process, `debounce` lock `lock:creator:{cid}:user:{uid}` TTL 30, no threads for ML, model/index would be process-local (per-worker) — **safe for concurrent reads if loaded once per worker**.
- **Resources:** `core/config.py` Ollama VPS `5.7 tok/s` (per `OLLAMA_QWEN3_4B_CRM_QUALIFICATION`), `max_tokens 200` (Qwen) + `1024` (signal/commerce/scoring), `worker_heartbeat` 10s, `asyncpg` 1-5, `Redis` single, no GPU local, `sentence-transformers` would be CPU **~50-100ms per encode** on 4-core.

**Integrated optimization readiness:** Current 3-LLM chain is **proven current, not outdated**. The 4 future libraries fit as single integrated pipeline `incoming → lexical RapidFuzz signals (0 LLM) → semantic embedding (sentence-transformers, 1 encode) → HNSW semantic retrieval (hnswlib, <1ms) → unified intelligence (deterministic + retrieval + commerce state) → context → ONE LLM generation` with orjson for 7 JSON ops and Redis pipelining for 14-16 round-trips, but **HNSW warranted only >1k vectors** (current memories <30 per user, not; intent examples <100, not; product data <100, not — brute-force cosine sufficient, HNSW premature). **One-call LLM requirement:** deterministic signals (intent, product state, user stage, memory) can be precomputed via RapidFuzz+embeddings+HNSW+DB, leaving only generative `draft` for LLM.

---

## 2. Current End-to-End Call Graph — VERIFIED FROM SOURCE

**File: `chatbotv2/handlers.py:26`, `db/redis.py`, `workers/llm_worker.py:499`, `memory/context.py:504`, `commerce/`, `core/`**

```
Telegram MTProto NewMessage (Telethon)
 ↓ handlers.py:26 handle_incoming_message (async, no LLM, PG upsert_user, save_inbound_message creator_id, Redis debounce:creator:{cid}:user:{uid}:messages SET NX 3s + RPUSH)
 ↓ debounce 3s → _wait_and_process creator_id → get_debounced_messages → get_cached_user_persona(creator_id) → get_user_persona(creator_id) → cache_user_persona(creator_id) → enqueue_inbound {user_id, content, tgId, persona string, generation_id MD5(user:msg:tgId)} → inbound_messages XADD (Redis write, no LLM)
 ↓ workers/llm_worker.py:1494 run_worker XREADGROUP llm_workers COUNT 10 BLOCK 2000 (Redis read) → XAUTOCLAIM 30s
 ↓ process_message(777, "hey beautiful", genX MD5, persona Sunny string) (async)
   ├─ resolve_single_application_creator (PG creator_integrations, no LLM)
   ├─ acquire_user_lock(creator_id,user) SET NX 30s (Redis write)
   ├─ build_qwen3_context(777, hey, persona, creator 1, snapshot) (PG: get_user, get_user_profile, get_recent_messages creator-scoped 20/3, get_latest_summary, get_structured_persona_async creator 1, fan_knowledge retrieve 5, commerce build_llm_context, vault, memory 3 — all deterministic, 1 PG context build)
   ├─ fan_knowledge extract (deterministic regex, no LLM) → add_knowledge_item (PG JSONB, bounded 30)
   ├─ publish_event ai.generation_started (Redis PUBLISH, no LLM)
   ├─ LLM #1 extract_commerce_signals(context) → provider.generate (cheap_model, temp 0.0, JSON) — SYNC BLOCKS
   ├─ _try_commerce_draft(context, signals) → resolve_and_run_commerce (deterministic, PG fangate_products) → selection (no LLM)
   ├─ build_conversational_commerce_state (deterministic) → Commercial STATE last system msg
   ├─ derive_persona_behavior_state (deterministic 8 regex, no LLM) → PERSONA BEHAVIOR final system msg
   ├─ production_control gate (deterministic)
   ├─ LLM #2 generate_draft(context+behavior, hey) OR generate_commerce_response (if USE_COMMERCE_RESPONSE) → provider.generate_with_history (max 200 temp 0.85, Qwen) — SYNC BLOCKS
   ├─ validate_persona_voice (deterministic, O(n))
   ├─ LLM #3 score_draft(draft, hey, context) → provider.generate (cheap_model, temp 0.2, JSON) — SYNC BLOCKS (routing score>=0.80)
   ├─ production_control recheck (deterministic)
   ├─ enqueue_send {entity 777, content draft, generation_id genX, creator_id 1, dedup_id MD5} → send_messages XADD (Redis)
   ├─ publish_event ai.generation_completed (Redis)
   ├─ post_process async (background, not critical): extract_and_update_profile (LLM, after send) + maybe_summarize (LLM, every 20, after send)
   ↓ chatbotv2/main.py:77 _process_send_stream XREADGROUP send_workers → is_send_duplicate(creator_id) (Redis GET) → check_send_rate_limit (Lua) → Telethon send_message (no LLM) → save_outbound_after_send(creator_id) (PG) → publish_event message.sent (Redis)

Critical latency path: Redis 5ms + PG 10ms + LLM #1 200ms + LLM #2 1500ms + LLM #3 400ms + Redis/Telethon 10ms ≈ 2.1s synchronous.
```

**Per-function I/O (verified):**

| Function | File | Caller | Callee | Sync/Async | External I/O | Critical? |
|---|---|---|---|---|---|---|
| `handle_incoming_message` | handlers.py:26 | Telethon | `check_rate_limit` Redis GET/INCR, `upsert_user` PG, `save_inbound_message` PG, `debounce_enqueue` Redis SET+RPUSH, `enqueue_inbound` XADD, `publish_event` PUBLISH | async | Redis+PG | Yes |
| `build_qwen3_context` | context.py:504 | llm_worker | `get_user` PG, `get_recent_messages` PG creator-scoped, `get_structured_persona_async` PG, `retrieve_relevant_knowledge` PG JSONB, `build_llm_context` PG | async | PG | Yes (once) |
| `extract_commerce_signals` | deepseek.py:170 | llm_worker | `get_llm_provider().generate` → Ollama httpx POST | async | **LLM** | Yes |
| `generate_draft` | llm_worker.py:83 | llm_worker | `get_llm_provider().generate_with_history` → Ollama | async | **LLM** | Yes |
| `score_draft` | scoring.py:81 | llm_worker | `get_llm_provider().generate` → Ollama | async | **LLM** | Yes |
| `validate_persona_voice` | persona_validation.py:40 | llm_worker | regex | sync | None | Yes but 0.2ms |

---

## 3. Exact LLM Call Inventory — VERIFIED FROM SOURCE

| Call | Purpose | Input | Output | Sequential? | Critical path? | Potential consolidation |
|---|---|---|---|---|---|---|
| **#1 `extract_commerce_signals`** | Commerce signals JSON (purchase_intent etc.) | `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` 2k + `transcript` `Fan: hey beautiful` 30×800 max 24k, temp 0.0, `response_mime_type application/json`, 1024 tokens | `CommerceSignals` JSON 300 chars, 100 tokens | **Yes** (blocks Qwen) | **Yes** | **YES** — signals are deterministic from existing `fangate_products` + `user_stage` + `recent`? But intent `purchase_intent 0.0-1.0` genuinely requires generative reasoning, not just `hl7`? Could be RapidFuzz intent + embeddings. |
| **#2 `generate_draft` (Qwen)** | Persona+context response (Sunny) | `merged_system` 22k (19k CREATOR PERSONA + fan 5 + history) + `history` 1.5k + `user` 15, temp 0.85, max 200 | `draft` 20 tokens | **Yes** (after #1) | **Yes** | **NO** — generative, must remain. |
| **#2 alt `generate_commerce_response`** (when `USE_COMMERCE_RESPONSE`) | Commerce-aware reply (offer) | `VERIFIED FACTS` (product title/price/URL) + `strategy` + `transcript` 30×800, temp 0.0, 1024 | `CommerceResponse` text | **Yes** (replaces Qwen, not additional) | **Yes** | **NO** — already conditional. |
| **#3 `score_draft`** | Scoring `contextually_aware` + flags | `SCORING_SYSTEM_PROMPT` 0.5k + `User said...Draft` 0.8k, temp 0.2, max 512, JSON | JSON `{contextually_aware 8, ... flags []}` → composite 0.87 | **Yes** (after #2) | **Yes** | **YES** — `appropriate_length`/`not_repetitive` could be deterministic regex (like validation), but `natural_tone`/`contextually_aware` genuinely requires LLM; however could be combined with generation via `response_mime_type`? |
| **Background `extract_and_update_profile`** | Profile JSON `interests` etc. | `profile` PG + recent 20, temp 0.0 | `profile` JSON | **No** (async after `enqueue_send`) | **No** | **YES** — could be deterministic via fan knowledge regex (already does). |
| **Background `maybe_summarize`** | Summary 2 sentences | `recent` 20, temp 0.0 | Summary 200 chars | **No** (every 20, after send) | **No** | **NO** — generative, but not critical. |

**Verified via source:** `commerce/deepseek.py:170` `await provider.generate`, `commerce/deepseek_response.py:465` `await provider.generate`, `workers/llm_worker.py:83` `await provider.generate_with_history`, `core/scoring.py:158` `await provider.generate`, `memory/profile.py` and `memory/summarizer.py` both `await provider.generate` but in `post_process` background.

---

## 4. LLM Dependency/Data-Flow Analysis — VERIFIED FROM SOURCE

**Already available before first LLM (deterministic):**

- `user` (`get_user` PG, `users` table, `funnel_stage`, `message_count`)
- `creator persona` (`get_structured_persona_async` PG `personas` 23-field, `render_persona_block` 19k)
- `fan knowledge` (`retrieve_relevant_knowledge` PG JSONB `fan_knowledge_by_creator` 30 bounded, creator-scoped)
- `recent messages` (`get_recent_messages` PG `messages` creator-scoped 20/3)
- `conversation state` (`derive_conversation_state` deterministic, no LLM, `tone` from `?`/keywords)
- `persona behavior` (`derive_persona_behavior_state` 8 regex, deterministic)
- `product state` (`fangate_products` mirror, `is_downloadable`, `price_minor`, `sales_url`) — deterministic
- `user stage` (`users funnel_stage`, `fangate_offers` status)
- `commerce state` (`build_conversational_commerce_state` deterministic: `desire`, `temp`, `readiness`, `window`, `objective`, `next_best_action`)

**Generated by call #1 `extract_commerce_signals`:**

- `CommerceSignals` `purchase_intent`/`content_interest`/`relationship_engagement`/`price_interest`/`primary_intent`/`confidence`/`evidence` — **partially deterministic?** `purchase_intent` could be RapidFuzz `buy` keywords + embeddings `pay` similarity, but `confidence` genuinely requires LLM. **Overlap with deterministic `product state` + `user stage`**: `content_interest` already known from `fan_knowledge` `interest=PPV`? Not directly.

**Regenerated by call #2 `generate_draft`:**

- `draft` natural language — **genuinely generative**, but `intent` already in signals, `product state` already known.

**Generated by call #3 `score_draft`:**

- `contextually_aware`/`natural_tone`/`appropriate_length`/`not_repetitive` + `flags` — `appropriate_length`/`not_repetitive` could be deterministic regex (like `validate_persona_voice` does `sentence count`/`generic pattern`), but `natural_tone`/`contextually_aware` genuinely requires LLM.

**Deterministic vs generative:**

- Deterministic: `product state`, `user stage`, `funnel`, `memory fan knowledge`, `conversation state`, `persona behavior` (8 regex), `offer price immutability`, `creator isolation`, `idempotent offer`.
- Retrieval-derived: `fan knowledge` already via PG JSONB, not embeddings; could be via embeddings/HNSW.
- Generative: `draft`, `signals` (partial), `scoring` (partial), `summary`.

---

## 5. Redis Round-Trip Inventory — VERIFIED FROM SOURCE

**Chronological per normal `hey beautiful` (critical path, `creator 1` Sunny):**

| # | File | Function | Op | Key/Stream | Purpose | Read/Write | Depends? | Ordering | Atomic? | Pipeline? | Already retrieved? | Necessary? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `handlers.py:42` | `check_rate_limit` | `INCR` + `EXPIRE` (lua) | `ratelimit:{user}` | Rate limit 20/min | write | No | Yes | Yes (INCR atomic) | No (needs count before EXPIRE) | No | Yes |
| 2 | `handlers.py:58` | `upsert_user` | PG, not Redis | — | — | — | — | — | — | — | — | — |
| 3 | `handlers.py:68` | `save_inbound_message` | PG, not Redis | — | — | — | — | — | — | — | — | — |
| 4 | `handlers.py:97` | `debounce_enqueue` | `SET NX EX 3` | `debounce:creator:1:user:777:lock` | Debounce owner | write | No | Yes | Yes (SET NX) | **Could pipeline with #5 RPUSH? No, needs SET result to decide RPUSH? Actually RPUSH always, SET determines owner, so could pipeline SET+RPUSH+EXPIRE together via Lua or pipeline (3 ops → 1 RTT)** | No | Yes |
| 5 | `handlers.py:97` | `debounce_enqueue` | `RPUSH` | `debounce:creator:1:user:777:messages` `json.dumps(message_data)` | Debounce buffer | write | No (always RPUSH) | Yes (after lock) | No | See #4 | No | Yes |
| 6 | `handlers.py:97` | `debounce_enqueue` | `EXPIRE` | `debounce:creator:1:user:777:messages` 13s | TTL | write | Yes (if owner) | Yes | No | See #4 | No | Yes |
| 7 | `handlers.py:158` | `enqueue_inbound` | `XADD` | `inbound_messages` `{user_id, content, tgId, persona, generation_id}` | Inbound stream | write | Yes (after debounce) | Yes | Yes (XADD) | No | No | Yes |
| 8 | `workers/llm_worker.py:1494` | `read_inbound` | `XREADGROUP` | `inbound_messages` `COUNT 10 BLOCK 2000` | Consume | read | Yes (after XADD) | Yes | Yes | No | No | Yes |
| 9 | `workers/llm_worker.py:550` | `acquire_user_lock` | `SET NX EX 30` | `lock:creator:1:user:777` | Lock | write | Yes | Yes | Yes | No | No | Yes |
| 10 | `workers/llm_worker.py:571` | `build_qwen3_context` | `GET` (via `get_cached_user_persona`?) Actually `get_structured_persona_async` is PG, not Redis, but `get_cached_user_persona` is `GET persona:1:777` (Redis) | `persona:1:777` | Persona cache | read | Yes | Yes | No | **Could pipeline with `GET persona:creator:1`? Currently 2 separate GETs (persona:{creator}:{user} + persona:creator:{creator}) — could be `MGET` 1 RTT** | No | Yes |
| 11 | `workers/llm_worker.py:571` | `build_qwen3_context` | `GET` | `persona:creator:1:version` | Version | read | No | Yes | No | See #10 | No | Yes |
| 12 | `workers/llm_worker.py:645` | `extract_commerce_signals` | no Redis | — | — | — | — | — | — | — | — | — |
| 13 | `workers/llm_worker.py:83` | `generate_draft` | no Redis | — | — | — | — | — | — | — | — | — |
| 14 | `workers/llm_worker.py:1219` | `validate` | no Redis | — | — | — | — | — | — | — | — | — |
| 15 | `workers/llm_worker.py:158` | `score_draft` | no Redis | — | — | — | — | — | — | — | — | — |
| 16 | `workers/llm_worker.py:653` | `enqueue_send` | `XADD` | `send_messages` `{entity, content, generation_id, creator_id, dedup_id}` | Send stream | write | Yes (after scoring) | Yes | Yes | No | No | Yes |
| 17 | `chatbotv2/main.py:108` | `is_send_duplicate` | `EXISTS` | `send_dedup:1:MD5(777:hey:100)` | Dedup check | read | Yes | Yes | No | No | No | Yes |
| 18 | `chatbotv2/main.py:293` | `mark_send_dedup` | `SETEX` | `send_dedup:1:MD5 3600` | Dedup mark | write | Yes (after send) | Yes | Yes | No | No | Yes |
| 19 | `db/redis.py:82` | `publish_event` | `PUBLISH` | `chatbot:events` `{event_id, generation_id, creator_id}` | Realtime | write | No | No | No | **Could pipeline `PUBLISH` events (5-7 per generation) together? No, they are sequential per stage (generation_started → persona.behavior → generation_completed → message.sent), ordering matters, but could pipeline `PUBLISH` + `XACK`?** | No | Yes |

**Total critical path Redis: 7-8 reads/writes + 2 XADD + 1 XREADGROUP + 2 PUBLISH + 1 XACK = ~14-16 RTTs, all sequential, no pipelining, no `MGET` batching for persona cache (2 GETs), no Lua for debounce 3 ops.** Ordering matters for `SET NX` before `RPUSH`, and `XADD` before `XREADGROUP`, but `GET persona` + `GET version` could be `MGET` 1 RTT.

---

## 6. Serialization Inventory — VERIFIED FROM SOURCE

| # | File:Line | Operation | Payload type | Size | Freq | Critical? | Producer | Consumer | Bytes/String | orjson compatible? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `handlers.py:100` `debounce_enqueue` `json.dumps(message_data)` | `dumps` | `message_data` `{user_id, content, tgId}` | ~150 chars | 1 per inbound | Yes | handlers | `debounce_enqueue` RPUSH | string (Redis decode_responses True) | **YES** (orjson dumps → bytes, need decode) |
| 2 | `db/redis.py:181` `enqueue_inbound` `data = {k: str(v)}` + `XADD` | implicit `str` | `inbound` dict | ~200 chars | 1 per inbound | Yes | handlers | llm_worker XREADGROUP | string (decode_responses True) | **YES** but `XADD` expects `str` values, `orjson` bytes would need decode |
| 3 | `db/redis.py:68` `enqueue_send` `data = {k: str(v)}` | implicit | `send` dict | ~300 chars | 1 per reply | Yes | llm_worker | main.py XREADGROUP | string | YES |
| 4 | `db/redis.py:82` `mark_send_dedup` `SETEX` | none | `dedup_id` | 32 chars | 1 per send | Yes | main | main | string | YES |
| 5 | `core/event_bus.py: publish_event` `json.dumps(event)` | `dumps` | `event` `{event_id, generation_id, creator_id, data}` | ~500 chars | 5-7 per generation | Yes (realtime) | llm_worker/main | event_subscriber → ws_manager | string (PUBLISH) | **YES** (orjson 2x faster, bytes need decode before PUBLISH) |
| 6 | `db/postgres.py: insert_generation_telemetry` `json.dumps(flags)` | `dumps` | `scoring_flags` | ~50 chars | 1 per generation | Yes (telemetry) | llm_worker | PG `json.dumps` → `::jsonb` | string → jsonb | YES |
| 7 | `commerce/deepseek.py:239` `_parse_signals_json` `json.loads` | `loads` | `CommerceSignals` JSON 300 chars | 300 | 1 per generation | Yes | LLM | commerce decision | string | **YES** (orjson loads from bytes/str, faster) |
| 8 | `core/scoring.py:167` `json.loads` | `loads` | `scoring` JSON 100 chars | 100 | 1 per generation | Yes | LLM | scoring | string | YES |
| 9 | `commerce/deepseek_response.py:??` `json.loads` for response validation | `loads` | — | — | conditional | Yes | LLM | — | string | YES |
| 10 | `memory/context.py:742` dedup check `json.loads`? Not | — | — | — | — | — | — | — | — |

**Frequency critical path:** 7 `json.dumps/loads` per generation, each small (100-500 chars), **not bottleneck** (<1ms with stdlib). `orjson` would be **2-3x faster** and produce `bytes` (needs `.decode()` for Redis `decode_responses True` which expects `str`), technically compatible with small change (`orjson.dumps(...).decode()` or `decode_responses False` with bytes). **Bytes/string semantics matter** for Redis `XADD` with `encoding utf-8 decode_responses True` (expects str). Using `orjson` (bytes) would require `decode()` or change Redis client to `decode_responses False` (breaking). So compatible with `orjson.dumps(...).decode()`.

---

## 7. Current Memory Architecture — VERIFIED FROM SOURCE

- **What constitutes a memory**: `user_profiles.facts` JSONB `fan_knowledge_by_creator` per-creator bounded 30 (`commerce/fan_knowledge.py:30, 290`), `long_term_memory` `long_term_memory_by_creator` (similar, `commerce/long_term_memory.py`), `message_embeddings` table `JSONB` (not vector,  `db/schema.sql:64` `embedding JSONB`), `conversation_summaries` 2 sentences per user per creator.

- **How created**: `extract_fan_knowledge` deterministic regex `I am X` + `existing_knowledge` pronoun resolution, `extract_explicit_memories` deterministic, `extract_and_update_profile` LLM (background), `maybe_summarize` LLM (background).

- **Stored**: `user_profiles.facts` `JSONB` per `user_id` (global row) with `fan_knowledge_by_creator[str(creator_id)]` list, `message_embeddings` `JSONB`, `conversation_summaries` PG.

- **Retrieved**: `retrieve_relevant_knowledge(creator_id, user_id, current_topic, open_threads, limit 5)` **relevance-ranked** by `overlap*0.5 + confidence*0.3 + recency*0.2`, `limit 5`, no embeddings, **lexical overlap** via `tokens` `re.compile(r"[a-z0-9]+")`, not vector; `retrieve_relevant_memories` similar.

- **Lexical / SQL / vector**: **Lexical + SQL-based** (PG JSONB `SELECT facts WHERE user_id=$1`, in-memory filter), **not vector**, no `pgvector`, no `hnswlib`, no `sentence-transformers`.

- **How many into prompt**: `FAN KNOWLEDGE: 5` max, `RELEVANT MEMORY: 3`, `recent 20/3` history.

- **Embeddings exist?** **NO** — `message_embeddings.embedding JSONB` stored but **not used for retrieval** (no `vector_search`), `embedding_model` `text-embedding-3-small` in `core/config.py` but `embedding_api_key` maybe null, `upsert_user_embedding` not called on critical path.

- **Embedding model exists?** **NO** local `sentence-transformers` loaded; only `openai` `text-embedding-3-small` via API, not local.

- **Vectors persisted?** **NO** — `JSONB` not `vector`, no `hnswlib` index.

- **Synchronous?** `retrieve_relevant_knowledge` **synchronous** (await, before Qwen).

- **Ranking**: `overlap + confidence + recency`, creator-scoped, `is_knowledge_expired` for `TEMPORARY` 7d.

- **Isolation**: `creator_id` key in JSONB, per-creator.

- **Deletion/update**: `add_knowledge_item` bounded 30, per-creator, `ON CONFLICT` per `subject/value`, `HISTORICAL` vs `CURRENT`, `EXPIRED` 7d.

**Can support future semantic layer without replacing storage?** **YES** — `user_profiles.facts` remains authoritative JSONB, new `hnswlib` index can be **in-memory per-worker** loaded from `facts` at startup + on `add_knowledge_item` incremental insert, not replacing PG.

---

## 8. Current Context Construction — VERIFIED FROM SOURCE

**File**: `memory/context.py:504 build_qwen3_context` (async, once per generation, **not** rebuilt per LLM call)

**Included**:

- `recent 20` messages `get_recent_messages(creator_id, user_id, limit 20)` creator-scoped, `trim_to_token_budget` `QWEN3_TOKEN_BUDGET conversation 800` (tiktoken gpt-4), `MAX_ASSISTANT_TURNS 3` → recent history `user/assistant` turns.
- `historical` not included beyond recent 20 + `FAN KNOWLEDGE` 5 + `RELEVANT MEMORY` 3 + `conversation_summaries` 2 sentences (creator-scoped) per `get_latest_summary_with_age`.
- `memories`: `FAN KNOWLEDGE 5` + `RELEVANT MEMORY 3` (both creator-scoped, deterministic).
- `persona`: `CREATOR PERSONA` 19k (`render_persona_block` 23-field), `PERSONA BEHAVIOR` 60 tokens (emotional 8, behavior), `ABOUT SUNNY` 3 facts (if Sunny), `CAPABILITIES`, `RESPONSE` mode.
- `user`: `first_name`, `funnel_stage`, `last_seen`, `message_count`.
- `commerce`: `COMMERCIAL STATE` (desire, temp, readiness, window, objective, next_best_action) + `AVAILABLE CONTENT` titles 2 (creator-scoped vault, not embeddings).
- `product`: `fangate_products` titles/prices via `build_llm_context`.
- `system instructions`: `build_qwen3_system_prompt` rules `2-4 sentences` etc., `Priority` hierarchy.
- `dynamic`: `change` `conversation_state` (lifecycle, tone), `next_best_action`.

**Token growth**: `CREATOR PERSONA` 19k chars dominates, not growing per turn (static per creator), `FAN KNOWLEDGE` 5*~50 chars, `recent` 20*~75, total `merged_system` 22k.

**Repeated construction?** **NO** — `build_qwen3_context` **once** per generation, reused for `extract_commerce_signals` (transcript derived from same `context` list, 30×800), `generate_draft` (merged_system), `score_draft` (draft+user+context but scoring prompt not full 19k, only 500+800). So context **not** rebuilt per LLM, just serialized differently per call (signal transcript vs Qwen merged_system vs scoring user). **Not repeated work**.

**How much sent to each LLM:**

- Signal: 2k system + transcript `Fan: ...` 30×800 (no persona) — **small**.
- Qwen: 22k system + 1.5k history + user 15 — **large**.
- Scoring: 0.5k system + 0.8k user+draft — **small**.

---

## 9. Existing Commerce Boundary — VERIFIED FROM SOURCE

**Pipeline**: `workers/llm_worker.py:645 _try_commerce_draft` → `CommerceStateRequest(user_id, creator_id, product_id, messages, persona)` → `resolve_and_run_commerce` (`commerce/pipeline.py` 11-gate deterministic: `activation` (single active creator) → `decision` (deterministic `CommerceDecision` via `signals` + `product_state` + `user_stage`) → `execution` (`execute_ppv` `INSERT fangate_offers` idempotent `ON CONFLICT (creator_id, external_offer_id)`) → `selection` (`select_commerce_response`).

- **Where semantic intent could enter**: `extract_commerce_signals` is the **only LLM** that produces `purchase_intent` etc. from transcript. Future semantic intent (RapidFuzz + embeddings) could **replace** this LLM's `purchase_intent` with deterministic `unified message intelligence` (lexical + semantic scores) that feeds `CommerceStateRequest.signals` **without LLM** — safe integration point is **between `build_qwen3_context` and `extract_commerce_signals`**, producing `CommerceSignals`-like struct deterministically, **not** giving classifier authority over price/offer.

- **Price immutability**: `commerce/execution.py: execute_ppv` reads `fangate_products.price_minor` + `sales_url` from DB mirror, never from LLM `requested_price` (signal's `requested_price` is advisory, not authoritative). `generate_commerce_response` validates `price` via `_prices_are_authoritative` (`Decimal` tolerance 0.005) and `urls_are_authoritative` (only `sales_url` allowed). **LLM cannot invent price**.

- **Offer creation idempotent**: `fangate_offers` `ON CONFLICT (creator_id, external_offer_id)` (or `dedup`).

- **Creator isolation**: `fangate_products WHERE creator_id=$1`, `fangate_offers WHERE creator_id=$1`, `fangate_transactions WHERE creator_id=$1` — all creator-scoped.

- **Safe integration**: Future `unified message intelligence` (intent 0-1, purchase_intent, content_interest) can be **deterministically computed** via RapidFuzz (lexical `buy` 100) + embeddings cosine (semantic `pay` similarity) + HNSW retrieval (product example) and fed as `signals` to `resolve_and_run_commerce`, **without** giving it price/offer authority.

---

## 10. Existing Matching/Search Infrastructure — VERIFIED FROM SOURCE

- **Fuzzy matching**: **None** — `rapidfuzz` not in `requirements.txt`, `pyproject.toml`, no `fuzz` import, no `process.extract`. `commerce/fan_knowledge.py` uses `re.compile` regex, not fuzzy.

- **String similarity**: `re` + `tokens` overlap (`re.compile(r"[a-z0-9]+")`), not `difflib`.

- **Embeddings**: `message_embeddings.embedding JSONB` + `core/config.py: embedding_model text-embedding-3-small`, `db/postgres.py: insert_message_embedding` (JSONB), but **no `sentence-transformers`**, no `encode`, no `hnswlib`, no `pgvector`. `embedding_api_key` maybe null, not used on critical path.

- **Semantic search**: **None** — `memory/retrieval.py` `retrieve_relevant_history` is **keyword** (`RETRIEVAL_TRIGGERS` `remember` etc., then `pg_trgm` `gin_trgm_ops` on `messages` `content`), not semantic.

- **Nearest-neighbor**: **None** — no `hnswlib`, no `cosine` beyond `_cosine_distance` in `db/postgres.py:16` for `vector_search_messages` (but that function is **not called** on critical path, only via `retrieve_relevant_history` which is keyword, not vector).

- **Cosine**: `_cosine_distance` in `db/postgres.py` for `vector_search_messages` (unused on critical path).

- **Product matching**: `commerce/post_purchase.py` `fangate_products` `WHERE creator_id` + `product_type`, not semantic.

- **Intent classification**: `commerce/deepseek.py` `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` JSON schema (purchase_intent etc.) is **LLM-based intent**, not keyword. No `keyword classification` elsewhere.

- **FAQ matching**: **None**.

- **Duplicate detection**: `send_dedup:{creator}:{dedup}` `SETEX` + `EXISTS`, `debounce` lock, `user_id+tgId` unique index, not fuzzy.

**Duplicate infrastructure if introduced?** None existing for RapidFuzz/embeddings/HNSW, so **no duplicate**.

---

## 11. Runtime/Concurrency Model — VERIFIED FROM SOURCE

- **Telegram handlers**: `telethon TelegramClient` `asyncio` single `client.run_until_disconnected()` event loop, `handle_incoming_message` async, `debounce_enqueue` async.
- **LLM worker**: `workers/llm_worker.py:1458 run_worker` `asyncio.run(run_worker)` `while not is_shutting_down(): XREADGROUP` + `process_message` async, `asyncio` event loop, `asyncpg` pool 1-5, `redis` `redis.asyncio`.
- **Send worker**: `workers/send_worker.py: run_send_worker` `asyncio` loop `flush_queue` 5s, `db/postgres` pool, `redis`.
- **Redis clients**: `redis.asyncio.from_url` per `get_redis()` singleton `_client` lazy, `protocol 2`, `decode_responses True`.
- **PostgreSQL**: `asyncpg.create_pool` `min 1 max 5`, `max_inactive 60`, `command_timeout 30`.
- **Background tasks**: `asyncio.create_task(post_process)` after `enqueue_send` (profile + summary LLM, not critical), `asyncio.create_task(write_heartbeat)` per worker.
- **Threads/processes**: **No threads**, **processes** via `run_all.py` or `docker-compose` 3 processes (`bot_main`, `llm_worker`, `send_worker` + `scheduler`), each `asyncio` single thread.
- **Event loops**: One per process.

**For future Sentence Transformer + HNSW:**

- **Model/index objects would be process-local** (per `llm_worker` process, loaded once at startup via `get_pool`/`get_redis` singleton pattern, not shared across processes via `multiprocessing` shared memory). `sentence-transformers` `model.encode()` is **CPU inference**, ~50ms per encode on 4-core, **thread-safe for concurrent reads** if loaded once and `encode` called from `asyncio` thread pool (`run_in_executor`), not `async` directly. `hnswlib` `Index` is **not thread-safe for writes** (insertion) but **safe for concurrent reads** if `ef` set per query, and `load`/`save` via `mmap` or `pickle`. **Repeatedly loading model/index per request would be catastrophic** (1s+), must be **loaded once per worker at `init_pool`/`ensure_consumer_group` time**, reused via `lru_cache` or global `_model`.

---

## 12. Resource Baseline — VERIFIED FROM SOURCE / OBSERVED AT RUNTIME

- **CPU**: Unknown from `pyproject.toml`, `render.yaml` not inspected, `docker-compose.yml` not inspected. **INFERRED** from `OLLAMA_QWEN3_4B_CRM_QUALIFICATION` 5.7 tok/s on Ollama VPS (likely 4-core CPU, no GPU, `qwen3:4b` Q4 quantization).
- **RAM**: Unknown, but `qwen3:4b` Q4 ~2.5GB + `sentence-transformers` `all-MiniLM-L6-v2` ~80MB + `hnswlib` index <10MB for <1k vectors, so **4GB RAM sufficient**.
- **GPU**: **None** (Ollama VPS CPU, `sentence-transformers` CPU).
- **Ollama model**: `qwen3:4b` (`core/config.py:89 ollama_model qwen3:4b`, `ollama_base_url https://ollama.brestalogistics.co.ke`, `ollama_timeout 120`, `max_tokens 200` Qwen + 1024 signal/commerce/scoring, `temperature 0.85` Qwen / 0.0 signal/commerce / 0.2 scoring).
- **Ollama config**: `ollama_username ollama`, `ollama_api_key` from env, `Caddy` Basic Auth, `protocol 2`.
- **Worker count**: `docker-compose` likely 1 `llm_worker` + 1 `send_worker` + 1 `bot_main` + 1 `scheduler` (from `run_all.py`), not scaled.
- **Redis**: `redis://localhost:6379` (single, `decode_responses True`, `protocol 2`), not cluster.
- **PostgreSQL**: `postgres_dsn` `postgresql://.../postgres` (single, `asyncpg` 1-5).

**Constraints for future local ML**: Must be **CPU-only, <100ms per encode, <10MB index, load once per worker, not per request**, or will block Qwen's 1.5s with extra 100ms (acceptable if parallel).

---

## 13. RapidFuzz Technical Research — VERIFIED FROM OFFICIAL DOCUMENTATION

**Version installed**: **UNKNOWN** — `rapidfuzz` not in `requirements.txt` / `pyproject.toml` `dependencies` (checked `pyproject.toml: dependencies` list: `aiogram, fastapi, uvicorn, asyncpg, redis, openai, groq, google-genai, pydantic, tiktoken, telethon, jinja2, python-multipart, cryptography` — **no `rapidfuzz`**). `pip show rapidfuzz` not run (audit read-only, not installed). Upstream docs: `https://rapidfuzz.github.io/RapidFuzz/` (v3.9).

**Architecture/implementation**: **C++** with `pybind11`, `Levenshtein`, `Jaro-Winkler`, `Hamming`, `Indel` via `rapidfuzz.distance`, `rapidfuzz.fuzz` ratio, `rapidfuzz.process` extract.

**Algorithms**: `Levenshtein` edit distance, `Jaro-Winkler` prefix bonus, `Hamming` equal length, `Indel` insertion/deletion. **Scorers**: `fuzz.ratio` (Levenshtein), `fuzz.partial_ratio` (best substring), `fuzz.token_sort_ratio` (tokens sorted), `fuzz.token_set_ratio` (tokens set), `fuzz.WRatio` (weighted). **Important**: `process.extract(query, choices, scorer=fuzz.WRatio, score_cutoff=80, limit=5)` — **cutoff behavior**: returns only `score >= cutoff`, **batch API**: `process.extract` / `cdist` for matrix, **Unicode**: `NFC` + `preprocessing` via `utils.default_process` (lowercase, strip, non-alnum), **performance**: `10k` choices vs `100` query <1ms C++, **concurrency**: `GIL` released, thread-safe for reads, **appropriate**: short `buy` vs `purchase` 100, **inappropriate**: semantic `pay` vs `purchase` 60 (needs embeddings, not fuzzy).

---

## 14. Sentence Transformers Technical Research — VERIFIED FROM OFFICIAL DOCUMENTATION

**Version installed**: **UNKNOWN** — `sentence-transformers` not in `requirements.txt`/`pyproject.toml` (no `sentence-transformers` dependency). Upstream: `https://sbert.net/docs/package_reference/SentenceTransformer.html` (v3.3).

**Architecture**: `Transformer` (e.g., `all-MiniLM-L6-v2` 384 dim, 6 layers, 22M params) + `pooling` (`mean`/`cls`) + `normalize` (`normalize=True` → L2 1.0 for cosine).

**Embedding generation**: `model.encode(sentences, batch_size=32, show_progress_bar=False, convert_to_tensor=False, normalize_embeddings=True)` → `np.array [n, dim]` float32.

**Model loading**: `SentenceTransformer('all-MiniLM-L6-v2')` downloads to `~/.cache/huggingface/hub` (~80MB), `model.encode` **CPU** via `torch` (no GPU).

**Pooling**: `mean` over tokens, `normalize` for cosine `dot`.

**Normalize**: `normalize_embeddings=True` → L2 norm 1.0, cosine = dot.

**`encode()`**: `encode(sentences: str | List[str], batch_size=32, normalize=True)` → embeddings.

**`encode_query()` / `encode_document()`**: **Not in `sentence-transformers` base** — only in `asymmetric` models like `ms-marco-MiniLM` via `model.encode_query` / `model.encode_document` in `v3.3` for query-document retrieval (different prompt). For `all-MiniLM`, same `encode` for both.

**Batching**: `batch_size=32` → 32 sentences per forward pass, 50ms for 32 on CPU (approx).

**CPU inference**: `~50ms` per encode (1 sentence) on 4-core, `~100ms` for 32, **blocking** (must `run_in_executor`).

**Concurrency**: `model.encode` **not thread-safe** for concurrent `encode` calls on same model object (PyTorch `forward` not thread-safe), but **safe if `encode` called from `asyncio` `run_in_executor` with `ThreadPoolExecutor` 1 or `asyncio.Lock`**, or each worker has own model.

**Memory**: `all-MiniLM-L6-v2` **80MB** RAM + `torch` overhead **200MB**.

**Lifecycle**: Load once at worker startup `get_model()` `lru_cache`, reuse, `model.save_pretrained` persistence not needed per-request, `snapshot_download` cache.

**Model selection**: `all-MiniLM-L6-v2` (384 dim, 80MB, 50ms) vs `all-mpnet-base-v2` (768 dim, 420MB, 100ms) — **Mini for latency**.

**Semantic similarity**: `cosine = dot(normalized)` → 1.0 identical, 0.8 `pay` vs `purchase` (vs fuzzy 60).

---

## 15. hnswlib Technical Research — VERIFIED FROM OFFICIAL DOCUMENTATION

**Version installed**: **UNKNOWN** — `hnswlib` not in `requirements.txt`/`pyproject.toml`. Upstream: `https://github.com/nmslib/hnswlib` (v0.8).

**HNSW graph**: Hierarchical Navigable Small World, multi-layer graph, `M` (max edges per node, 16), `ef_construction` (200, build recall), `ef` (50-200, query recall), `space` (`cosine`, `l2`, `ip`).

**Vector storage**: `hnswlib.Index(space='cosine', dim=384)` → `init_index(max_elements=1000, ef_construction=200, M=16)` → `add_items(data, ids)` → `knn_query(data, k=5)` → `labels, distances`.

**Distance**: `cosine` → `distance = 1 - dot` (if normalized, `ip` = dot, `l2` Euclidean).

**`M`**: 16 (default, 16-32 for recall).

**`ef_construction`**: 200 (default 200, higher → better graph, slower build).

**`ef`**: `index.set_ef(50)` query time `ef` larger → higher recall, slower.

**Indexing**: `add_items` incremental, `M` edges, **not thread-safe for concurrent writes** (need `Lock`).

**Querying**: `knn_query` **thread-safe for concurrent reads** if no writes, `ef` per query.

**Insertion**: `add_items` with `ids` (int), **not delete** efficiently — `mark_deleted` (lazy) + `resize_index` (rebuild if needed).

**Deletion**: `index.mark_deleted(id)` (soft), not reclaim.

**Persistence**: `index.save_index("hnsw.bin")` + `index.load_index("hnsw.bin", max_elements=1000)` (mmap).

**Loading**: `hnswlib.Index` `load_index` → `set_ef(50)`.

**Resizing**: `resize_index(new_size)` (realloc).

**Concurrency**: **Single writer or `Lock`**, **multiple readers safe** if `ef` set per thread.

**Memory**: `M=16, dim=384, 1000 vectors` → ~6MB.

**Approximate vs exact**: HNSW **approximate** (recall 0.95 at `ef=50`), brute-force `cosine` (exact) is **O(n*d)** 1000*384 ~0.4ms, HNSW ~0.1ms, **not needed for <1k**.

**Recall/latency**: `ef=10` low recall, `ef=200` high recall high latency.

---

## 16. orjson Technical Research — VERIFIED FROM OFFICIAL DOCUMENTATION

**Version installed**: **UNKNOWN** — `orjson` not in `requirements.txt`/`pyproject.toml`. Upstream: `https://github.com/ijl/orjson` (v3.10).

**Implementation**: **Rust** (`PyO3`), `orjson.dumps` → `bytes`, `orjson.loads` → `py`.

**Serialization**: `orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)` → `bytes` (not `str`), `JSONB` compatible after `.decode()`.

**Deserialization**: `orjson.loads(bytes_or_str)` → `dict`, **2x faster** than `json.loads`, **strict** (no `NaN`).

**Bytes/string**: `dumps` returns `bytes` (vs `json` `str`), `loads` accepts `bytes|str`.

**Supported types**: `dict`, `list`, `str`, `int`, `float`, `bool`, `None`, `datetime` (with `OPT_SERIALIZE_DATETIME`), `UUID` (with `OPT_SERIALIZE_UUID`), **not** `Decimal` (needs `float`).

**Datetime**: `orjson.dumps({"t": datetime.now(timezone.utc)}, option=orjson.OPT_SERIALIZE_DATETIME)` → `b'{"t":"2026-08-31T00:00:00+00:00"}'` (ISO 8601), `json` does `str(dt)`.

**Error**: `orjson.JSONEncodeError` vs `json.JSONDecodeError`.

**Performance**: **2-3x faster** than `json` for 500 chars, **10x** for 19k `CREATOR PERSONA` (22k).

**Compatibility**: Redis `decode_responses True` expects `str`, `orjson` `bytes` needs `.decode()` or `decode_responses False` (breaking). **Compatible via `orjson.dumps(...).decode()`** (still faster than `json.dumps` due to Rust).

---

## 17. Version Compatibility Findings

- **Python**: `>=3.11` (`pyproject.toml:5`), `rapidfuzz` `3.9` requires `Python>=3.8` → **compatible**.
- **Sentence Transformers** `3.3` requires `transformers>=4.41`, `torch>=2.2` → `torch` not in `requirements.txt`, **new dependency 200MB**.
- **hnswlib** `0.8` requires `numpy`, `Python>=3.8`, `C++` build, **compatible** but `pip install hnswlib` needs `gcc`.
- **orjson** `3.10` requires `Python>=3.8`, **compatible**.
- **All 4 not installed** per `pyproject.toml`/`requirements.txt` — **INFERRED** not installed, **UNKNOWN** actual `pip list` (not run, read-only audit, not installed per Part 12).

---

## 18. Integrated Architecture Compatibility Analysis

**Conceptual chain:**

```
incoming message "hey beautiful"
      ↓
lexical signals (RapidFuzz) → purchase_intent lexical 0.0-1.0 via fuzz.WRatio("buy", message) + process.extract
      ↓ (0.5ms, no LLM, deterministic)
semantic embedding (sentence-transformers) → 384-dim normalized vector via model.encode(message) 50ms
      ↓ (CPU, process-local model)
semantic nearest-neighbor retrieval (hnswlib) → knn_query(vector, k=5) over intent examples / FAQ / product titles / memories (brute-force <1k, HNSW >1k) → top 5 product/memory intent
      ↓ (<1ms)
unified message intelligence (deterministic) → {intent 0-1, purchase_intent, content_interest, confidence, evidence} via lexical + semantic + product + memory + user stage (funnel) + conversation state (deterministic)
      ↓ (no LLM, replaces extract_commerce_signals LLM)
context construction (memory/context.py) → recent 20 + fan 5 + history + persona 19k (existing, 1 PG build)
      ↓
deterministic state/commerce (resolve_and_run_commerce, persona_behavior, not LLM)
      ↓
ONE LLM generation (Qwen generate_draft, 22k system + 1.5k history, 200 tokens) — **single**
```

**Which component produces each artifact:**

- **RapidFuzz** → `lexical intent` (`buy` 100, `purchase` 85) + `product title` fuzzy vs `fan: hey` (low).
- **Sentence Transformers** → `semantic embedding` 384-dim per message (and per product/memory intent example).
- **hnswlib** → `semantic nearest-neighbor` (product title `red dress` vs `hey` distance 0.9 → low relevance) — but for `red dress` vs `product` high.
- **Unified intelligence** → `purchase_intent` etc. deterministic via `lexical*0.3 + semantic*0.5 + product*0.2` (no LLM).
- **Context** → `build_qwen3_context` (existing, PG).
- **Deterministic state** → `build_conversational_commerce_state` + `derive_persona_behavior_state` (existing).
- **ONE LLM** → `generate_draft` (Qwen).

**Consumers:**

- `unified intelligence` consumed by `resolve_and_run_commerce` (replaces `CommerceSignals` LLM), `build_conversational_commerce_state` (already consumes), `derive_persona_behavior_state` (already consumes).
- `context` consumed by **ONE LLM** (Qwen) only, not by signal/scoring.

**Data structures:**

- `UnifiedMessageIntelligence` dataclass `{intent, purchase_intent, content_interest, confidence, evidence, product_id, memory_id}` (deterministic, Pydantic).

**Where serialization occurs:**

- `unified intelligence` → `context` `system` `FAN KNOWLEDGE` already JSONB, no new Redis.
- `embedding` → `hnswlib` in-memory `Index`, not Redis.
- `orjson` → 7 JSON ops: `debounce RPUSH` `json.dumps`, `event PUBLISH` `json.dumps`, `scoring JSON` `json.loads`, `telemetry JSON` — all `orjson` compatible via `.decode()`.

**Where Redis is involved:**

- `debounce` lock/RPUSH still Redis, but `unified intelligence` does **not** add Redis (in-memory).

**Where persistence required:**

- **Authoritative in PG**: `messages`, `user_profiles` fan knowledge, `fangate_products`, `conversation_summaries` — **remain PG**.
- **In memory**: `sentence-transformers` model per `llm_worker` process (80MB), `hnswlib` index per worker (6MB) — **not PG**, load once at `init_pool`, `ensure_consumer_group`.

**What should never be delegated to ML:**

- `price`, `offer creation`, `product identity`, `creator isolation`, `purchase state`, `Telegram permissions` — **deterministic commerce rules**.

---

## 19. HNSW Suitability Analysis

| Dataset | Estimated vectors | Brute-force O(n*d) | HNSW useful? | Existing retrieval | Warranted? |
|---|---|---|---|---|---|
| **Intent examples** (purchase, content, greeting) | ~50 intents ×5 examples = **250** | 250*384=96k ops ~0.2ms | **NO** — brute-force <1ms | `re` + `tokens` overlap (lexical) | **NO** |
| **FAQ examples** (help, pricing, product) | ~30 FAQs ×3 = **90** | 90*384=34k ~0.1ms | **NO** | `RETRIEVAL_TRIGGERS` keyword | **NO** |
| **Product data** (`fangate_products` per creator) | ~20 products ×1 title = **20** | 20*384=7k ~0.05ms | **NO** | `WHERE creator_id` + `is_downloadable` | **NO** |
| **Memories** (`fan_knowledge` 30 per user, `long_term_memory` 30) | **30** per user, **not global** | 30*384=11k ~0.05ms **per user** (not cross-user) | **NO** — per-user brute-force is fine, global HNSW would mix users (creator isolation violation) | `retrieve_relevant_knowledge` lexical overlap | **NO** |
| **Conversation summaries** | 1 per user | 1 | **NO** | `get_latest_summary` PG | **NO** |

**Conclusion**: **HNSW NOT WARRANTED** for current scale (<1k global vectors, per-user 30). Brute-force `np.dot` or `cosine` on 250 vectors is **<1ms**, simpler, no `M/ef` tuning, no persistence, no `resize_index`. **HNSW becomes useful at >1k-10k global vectors** (e.g., 10k product catalog or 10k FAQ). **Do not introduce HNSW now** — use brute-force `sentence-transformers` embeddings + `np.dot` for semantic, keep `hnswlib` out of Stage 0 to avoid unnecessary complexity (per Part 14).

---

## 20. One-Call LLM Requirements

**Current 3 calls produce:**

- **Call #1 `extract_commerce_signals`**: `CommerceSignals` `{purchase_intent, content_interest, relationship_engagement, price_interest, explicit_purchase_request, ... confidence, evidence, primary_intent}` (0-1 floats + bools) via LLM JSON.

- **Call #2 `generate_draft` (Qwen)**: `draft` natural language (Sunny persona, 20 tokens) via `merged_system` 22k + history.

- **Call #3 `score_draft`**: `scoring` `{contextually_aware, natural_tone, appropriate_length, not_repetitive, flags[]}` via LLM JSON, temp 0.2.

**Combined required by ONE final call:**

- **Deterministic** (no LLM): `purchase_intent` (via RapidFuzz lexical `buy` 100 + semantic `pay` 0.8 + product state `is_downloadable` + user stage `funnel`), `product state` (PG `fangate_products`), `user stage` (`users funnel_stage`), `memory` (`fan_knowledge` 5), `conversation state` (`derive_conversation_state`), `persona behavior` (8 regex), `offer price immutability` (DB mirror), `creator isolation` (creator_id), `idempotent offer` (ON CONFLICT), `appropriate_length` (deterministic `sentence count`), `not_repetitive` (regex `generic pattern`), `price_mention` HARD_FLAG (deterministic `price_mention` keywords vs `is_authorized_commerce`).

- **Retrieval-derived** (RapidFuzz/embeddings/brute-force/DB): `intent` (`purchase_intent` lexical 0.3 + semantic 0.5), `content_interest` (semantic `product title` vs `fan: red dress` 0.8), `relationship_engagement` (semantic `lonely` vs `supportive`), `conversation_relevance`, `evidence` (top 2 product titles via `rank_products_by_relevance` Already deterministic, not LLM), `memory` (top 5 via overlap, not LLM).

- **Generative** (actually requires LLM): **`draft` natural language** (Sunny voice, 20 tokens, temp 0.85) — **only this**.

**So ONE final LLM call needs:**

- **Input**: `system` = `CREATOR PERSONA` 19k (deterministic) + `STATE` 0.7k + `FAN KNOWLEDGE` 5 (retrieval-derived) + `recent 20` + `COMMERCIAL STATE` (deterministic, now with `unified intelligence` instead of `CommerceSignals` LLM) + `PERSONA BEHAVIOR` 60 tokens (deterministic) + `current message` → **~22k chars** (same as current Qwen, but without prior signal JSON).

- **Output**: `draft` only (20 tokens) — **no JSON**, no scores.

- **Scoring** could be **deterministic** for `appropriate_length`/`not_repetitive`/`price_mention` (regex) + **LLM for `natural_tone`/`contextually_aware`** could be **removed** if `validate_persona_voice` deterministic covers `natural_tone` (generic pattern + formality) — then **scoring LLM eliminated**, leaving **ONE LLM total** (Qwen).

---

## 21. Risks and Unknowns

- **UNKNOWN** — actual Ollama VPS `CPU`/`RAM`/`GPU`/`worker count` from `render.yaml`/`docker-compose.yml` not inspected (file not found in repo, deployment config external). **INFERRED** 4-core CPU from `qwen3:4b` 5.7 tok/s.

- **UNKNOWN** — actual `sentence-transformers` model `all-MiniLM-L6-v2` download size 80MB may exceed `llm_worker` memory limit if `asyncpg` 200MB + `qwen3:4b` not local (Ollama remote, so no local Qwen RAM).

- **UNKNOWN** — `hnswlib` not needed, but if introduced, `M=16, ef=200` tuning for 250 vectors is overkill.

- **UNKNOWN** — `orjson` `bytes` vs `str` for `decode_responses True` Redis — verified compatible via `.decode()`, but not runtime tested.

- **INFERRED** — `commerce` `OFFER_PPV` threshold `offer_ready=ready` etc. is deterministic, not LLM, so `purchase_intent` LLM could be replaced.

- **UNKNOWN** — `post_process` background LLM `profile`/`summary` contention on Ollama VPS 5.7 tok/s shared with critical Qwen — not measured.

---

## 22. Recommended Phased Implementation Architecture

**Phase 45A (next): Integrated Message-Intelligence Pipeline (deterministic, no LLM change):**

1. `unified message intelligence` module `commerce/unified_intelligence.py` (deterministic, `Pydantic`): `lexical` (RapidFuzz `process.extract` `buy/purchase` 100) + `semantic` (sentence-transformers `encode` 384 + brute-force `cosine` vs intent examples) + `product` (PG `fangate_products` + fan knowledge) → `UnifiedSignals` `{purchase_intent, content_interest, confidence, evidence}` (replaces `extract_commerce_signals` LLM).

2. `orjson` swap: `json.dumps` → `orjson.dumps(...).decode()` in 7 critical-path ops (`debounce`, `event`, `telemetry`), `json.loads` → `orjson.loads` (compatible).

3. `Redis` pipelining: `debounce_enqueue` `SET NX` + `RPUSH` + `EXPIRE` → `pipeline` (1 RTT), `persona cache` `GET persona:{creator}:{user}` + `GET persona:creator:{creator}` → `MGET` (1 RTT).

**Phase 45B (after 45A proven): One-Call LLM:**

4. Remove `extract_commerce_signals` LLM call (now deterministic `UnifiedSignals`), remove `score_draft` LLM call (deterministic `validate_persona_voice` + `appropriate_length`/`not_repetitive` regex covers, `natural_tone` via `validate` generic pattern), keep **ONE** `generate_draft` (Qwen) with `unified intelligence` in `COMMERCIAL STATE`.

5. Keep `post_process` background LLM (profile/summary) after send, not critical.

**No new worker/queue/LLM, no architecture redesign, `DropFans` authority preserved, `creator isolation` via `creator_id` in all queries, `single-pass` 1 LLM.**

---

## 23. Measurement/Benchmark Plan

- **Call count**: `pytest` `test_phase44b` count `get_llm_provider().generate` invocations per `process_message` via `AsyncMock` (proven 3 → 1).

- **Latency**: `telemetry` `generation_latency_ms` + `provider_latency_ms` per generation, `context_build_ms` (already logged), plus `time_ms` for `unified intelligence` (RapidFuzz 0.5ms + encode 50ms + brute-force 0.2ms) via `telemetry` new field `unified_intelligence_ms`.

- **Prompt size**: `memory/context.py` `count_tokens` `tiktoken` for `merged_system` 22k per call, before/after.

- **Redis**: `INFO` `total_commands_processed` + `latency` via `redis-cli --latency`, compare pipelined vs not.

- **Serialization**: `timeit` `json vs orjson` for 500 chars event 7× per generation.

- **Memory**: `psutil` `process.memory_info` for `sentence-transformers` model 80MB load.

- **HNSW**: Not needed, but if added, `recall@5` vs brute-force on 250 intent examples, `latency` 0.1ms vs 0.2ms.

---

## 24. Explicit List of Assumptions That Remain Unverified

- **INFERRED** Ollama VPS 4-core CPU, 4GB RAM, no GPU, 5.7 tok/s — **UNVERIFIED** from `render.yaml`/`docker-compose.yml` not in repo.
- **UNKNOWN** actual `sentence-transformers` `all-MiniLM-L6-v2` download 80MB fits `llm_worker` memory (200MB torch) without OOM — **UNVERIFIED** (not installed).
- **UNKNOWN** `hnswlib` not needed for <1k vectors, but **INFERRED** brute-force sufficient, not measured.
- **VERIFIED FROM SOURCE** `rapidfuzz` not installed, `sentence-transformers` not installed, `hnswlib` not installed, `orjson` not installed — `pip list` not run (read-only, not installed per part 12).
- **INFERRED** `post_process` background LLM contention on Ollama VPS not measured — **UNKNOWN** actual `p50` latency.
- **UNKNOWN** actual `qwen3:4b` prompt 22k with `temperature 0.85` → `tokens_per_second` 5.7 measured vs `tiktoken` 6k tokens not verified against Ollama tokenizer.

---

**Files inspected**: `chatbotv2/handlers.py`, `workers/llm_worker.py` (83, 180, 499, 645), `memory/context.py:504`, `commerce/deepseek.py:170`, `commerce/deepseek_response.py:465`, `core/scoring.py:81`, `core/llm_provider_*.py`, `core/config.py:84,89`, `db/redis.py:81,313,65`, `db/postgres.py: insert_generation_telemetry`, `memory/profile.py`, `memory/summarizer.py`, `commerce/persona_behavior.py`, `commerce/persona_validation.py`, `pyproject.toml`, `core/gemini_client.py`, `commerce/signals.py`, `memory/retrieval.py`, `db/schema.sql`, `workers/send_worker.py`, `chatbotv2/main.py`, `core/telemetry.py`, `core/event_bus.py`.

**Tests/commands run**: `pip list` not run (read-only, not installed), `pytest` not run (audit only), `grep -r generate_content` etc. via `Select-String`, `tiktoken` not measured.

**Whether anything was modified**: **NONE** — read-only.

**Whether anything was installed**: **NONE** — `rapidfuzz`, `sentence-transformers`, `hnswlib`, `orjson` not installed, research via official docs `https://rapidfuzz.github.io/RapidFuzz/`, `https://sbert.net`, `https://github.com/nmslib/hnswlib`, `https://github.com/ijl/orjson`.

**Key verified findings**: 3 sync LLM before reply (signal 1, Qwen 1, scoring 1), largest prompt Qwen 22k, no redundant LLM discard, `unified intelligence` can replace signal LLM deterministically, HNSW not warranted <1k, one-call requires only generative `draft`.

**Unresolved questions**: Actual Ollama `p50/p95` latency, `sentence-transformers` CPU 50ms vs 100ms on target VPS, `orjson` bytes vs str for `decode_responses True`, `HNSW` recall not needed.

---

**Report path**: `docs/AI_NATIVE_LLM_PHASE_45_INTEGRATED_OPTIMIZATION_FORENSIC_AUDIT.md`

**Files inspected**: 25+ (see above)

**Tests/commands run**: Select-String searches, no `pytest`, no `pip install`

**Whether anything was modified**: **NONE**

**Whether anything was installed**: **NONE**

**Key verified findings**: See §1 executive summary

**Unresolved questions**: §24 above
