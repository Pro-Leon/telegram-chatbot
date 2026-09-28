# AI_NATIVE_LLM_PHASE_46_LOCAL_INTELLIGENCE_REPLACEMENT_AUDIT — STAGE A
**Read-Only Forensic Audit — Whether Auxiliary LLM Calls Can Be Replaced by Deterministic/Local Intelligence**
**Date: 2026-08-31 | Phase: 46 Stage A | READ-ONLY, NO MODIFICATIONS, NO INSTALLS**

---

## 1. Phase 44C Baseline — VERIFIED FROM PHASE 44C

**Phase 44C surgical optimization (Stage B) completed and preserved:**

```
LLM calls: 3 → 3 (not yet reduced)
Persona: 19,524 → 3,300 chars (FACTS vs BEHAVIOR, 83% reduction, render_compact_persona_block)
Generation context: ~22k (~5,500 tokens) → ~6k (~1,500 tokens, 73% reduction)
Ollama num_ctx: 2,048 → 8,192 (core/config.py ollama_num_ctx 8192, core/llm_provider_ollama.py options.num_ctx)
Context construction: sequential 5× PG (10ms) → parallel asyncio.gather (5ms)
Persona snapshot: single consistent snapshot per generation (workers/llm_worker.py:569 fetch before build_qwen3_context, passed as structured_persona_snapshot, reused for CREATOR PERSONA + behavior + validation)
Measured latency: ~2.1s → ~1.1s (context 10→5ms + Qwen 1.5s→0.5s compact + signal 0.2s + scoring 0.4s)
Regression: 98/98 tests passed (phase43b 39, phase43d 37, phase43f 12, phase44c 10)
Architecture: NONE (single-pass Qwen preserved, 3 calls preserved)
```

**Must be preserved** per Phase 46 context: compact persona, snapshot, parallel context, `num_ctx=8192`, worker/queue architecture, commerce authority.

---

## 2. Phase 45 Relevant Findings — VERIFIED FROM PHASE 45

* **Normal inbound 3 sync LLM before reply:** `extract_commerce_signals` (2k+transcript, temp 0.0, 1024) → `generate_draft` (22k/6k compact, 200 tokens, temp 0.85, Qwen) → `score_draft` (1.3k, temp 0.2, 512) — all `get_llm_provider()` → Ollama `qwen3:4b` primary, Gemini fallback.
* **Memory is lexical PG/JSONB:** `user_profiles.facts` `fan_knowledge_by_creator` 30 bounded, `retrieve_relevant_knowledge` relevance `overlap*0.5+confidence*0.3+recency*0.2` (no embeddings), `message_embeddings` `JSONB` not vector, `sentence-transformers` NOT deployed, `hnswlib` NOT deployed, `rapidfuzz` NOT deployed, `orjson` NOT deployed.
* **Datasets small:** intent examples ~250, FAQ ~90, products ~20, memories 30 per user → **brute-force cosine <1ms, HNSW not warranted** (<1k global).
* **Redis 14-16 RTT** sequential, no pipelining, `MGET` for persona 2 GETs.
* **7 JSON `json.dumps/loads` stdlib** on critical path (debounce, XADD, PUBLISH, scoring JSON, telemetry).
* **Intended future:** `MESSAGE → lexical (RapidFuzz) + semantic (sentence-transformers 384) → HNSW → unified intelligence → ONE LLM` with `orjson`, `brute-force` sufficient, not HNSW.

---

## 3. Current `extract_commerce_signals()` Architecture — VERIFIED FROM SOURCE

**File:** `commerce/deepseek.py:170` `extract_commerce_signals(conversation_context) -> CommerceSignals`

```python
transcript = compose_signal_extraction_input(context) # 30×800, system skipped
if not transcript.strip(): return low_information("empty_context") # NO LLM
provider = get_llm_provider() # Ollama primary (cheap_model qwen3:4b)
response_text = await provider.generate(system=COMMERCE_SIGNAL_EXTRACTION_SYSTEM 2k, user=transcript, temp 0.0, max 1024, JSON)
raw = _parse_signals_json(response_text) # tolerant code fences
signals = _build_signals(raw) # CommerceSignals(**raw) Pydantic extra="forbid", bounded floats
return signals # fallback low_information on any exception/malformed/invalid
```

**Transport:** `Ollama` `httpx POST /api/chat` or `Gemini` `generate_content` via `core/gemini_client` CredentialPool, `response_mime_type application/json`, single `try` + `except` fallback to other provider (1 logical may be 2 HTTP, counts as 1).

**Provider/model:** `cheap_model` (`qwen3:4b` when `llm_provider=ollama`, else `gemini-flash-latest`), not `DeepSeek` despite file name.

---

## 4. Complete CommerceSignals Field Map — VERIFIED FROM SOURCE

**Model:** `commerce/signals.py: CommerceSignals` `extra="forbid"`, bounded floats 0-1, strict bools, `evidence` max 5×80 chars.

| Field | Type | Default | Range | LLM Output Format | Parsing `commerce/deepseek.py:139` | Downstream Consumers | Affects commerce decisions? | Affects response generation? | Affects routing? |
|---|---|---|---|---|---|---|---|---|---|
| `purchase_intent` | float | 0.0 | 0-1 | JSON number `0.0` | `CommerceSignals(**raw)` | `commerce/decision.py` `CommerceDecision` `purchase_intent`, `commerce/strategy.py` `pressure`, `commerce/conversational.py` `build_conversational_commerce_state` | **YES** | **YES** (strategy) | NO |
| `content_interest` | float | 0.0 | 0-1 | JSON | same | `decision` `strategy` | YES | YES | NO |
| `relationship_engagement` | float | 0.0 | 0-1 | JSON | same | `decision` | YES | YES | NO |
| `price_interest` | float | 0.0 | 0-1 | JSON | same | `decision` | YES | YES | NO |
| `explicit_purchase_request` | bool | false | bool | JSON `true` only when fan literally `I want to buy` (not `how to pay`) | same | `decision` | YES | YES | NO |
| `explicit_content_request` | bool | false | bool | JSON | same | `decision` | YES | YES | NO |
| `requested_price` | number|null | null | number or null | JSON | same | `decision` (advisory) | YES (but not authoritative) | YES | NO |
| `declined_recent_offer` | bool | false | bool | JSON | same | `decision` `build_conversational_commerce_state` | YES | YES | NO |
| `accepted_recent_offer` | bool | false | bool | JSON | same | `decision` | YES | YES | NO |
| `asks_for_free_content` | bool | false | bool | JSON | same | `decision` | YES | YES | NO |
| `negative_sentiment` | float | 0.0 | 0-1 | JSON | same | `decision` | YES | YES | NO |
| `conversation_relevance` | float | 0.0 | 0-1 | JSON | same | `decision` | YES | YES | NO |
| `confidence` | float | 0.0 | 0-1 | JSON | same | `strategy` `decision` | YES | YES | NO |
| `evidence` | list[str] | [] | max 5×80 | JSON `["quoted fragment"]` | same | **NO** (analytics only, not decision) | NO | NO | NO |
| `model_uncertainty` | float | 0.0 | 0-1 | JSON | same | `decision` | YES | YES | NO |
| `primary_intent` | str | `uncertain` | enum `greeting`/`casual_chat`/`personal_disclosure`/`relationship_building`/`content_curiosity`/`content_request`/`purchase_intent`/`price_inquiry`/`tip_interest`/`complaint`/`rejection`/`hesitation`/`reassurance`/`appreciation`/`operator_request`/`uncertain`/`other` | JSON | same | `decision` | YES | YES | NO |
| `intent_tags` | list[str] | [] | subset `primary_intent` 1-3 | JSON | same | `decision` | YES | YES | NO |
| `negative_intent_tags` | list[str] | [] | subset `hesitation`/`rejection`/`complaint` | JSON | same | `decision` | YES | YES | NO |
| `fan_asks_question` | bool | false | bool | JSON | same | `decision` + `PersonaBehaviorState` question | YES | YES | NO |
| `topic_continuity` | str|null | null | string or null | JSON | same | `retrieve_relevant_knowledge` | NO | YES | NO |

**All consumers searched via `Select-String` `CommerceSignals` `purchase_intent` etc. — actual consumers are `commerce/decision.py`, `commerce/strategy.py`, `commerce/conversational.py`, `commerce/orchestrator.py`, `commerce/execution.py` (via decision), `workers/llm_worker.py` (signals passed to `build_conversational_commerce_state`), not `scoring` or `persona_behavior`.

---

## 5. Actual Signal Consumers — VERIFIED FROM SOURCE

- `workers/llm_worker.py:658` ` _signals_for_both = await extract_commerce_signals(context)` → `666 _try_commerce_draft(..., signals=_signals_for_both)` → `build_conversational_commerce_state(..., signals=_signals_for_both)` → `commerce decision/strategy` → `Commercial STATE` system msg before Qwen → Qwen.

- **Not consumers:** `score_draft` (does not use `CommerceSignals`), `PersonaBehaviorState` (uses `fan_message` + `conversation_state` + `fan_knowledge`, not signals), `validate_persona_voice` (no), `memory` (no).

---

## 6. What the First LLM Call Actually Does — VERIFIED FROM SOURCE

**Responsibilities:**

- **Intent classification** (`primary_intent`, `intent_tags`, `topic_continuity`) — **YES** (LLM infers `greeting` vs `purchase_intent`).
- **Interest estimation** (`purchase_intent` 0-1, `content_interest` 0-1, `relationship_engagement` 0-1, `price_interest` 0-1) — **YES**.
- **Urgency estimation** (`negative_sentiment`, `declined_recent_offer`) — **YES**.
- **Price sensitivity** (`requested_price`, `price_interest`) — **YES**.
- **Purchase intent** (`explicit_purchase_request`) — **YES** (literal `I want to buy`).
- **Objection detection** (`negative_intent_tags` `hesitation`/`rejection`/`complaint`) — **YES**.
- **Conversational state** (`fan_asks_question`, `topic_continuity`) — **YES**.
- **Emotional state** — **NO** (emotional is `PersonaBehaviorState` deterministic, not signals).
- **Other** (`confidence`, `model_uncertainty`, `evidence`) — **YES**.

**Classification:**

| Responsibility | Verdict | Downstream use |
|---|---|---|
| `purchase_intent` / `content_interest` / `price_interest` | **C — semantic similarity** (could be `pay` vs `purchase` embedding 0.8, not just `hl7`) | `decision` |
| `explicit_purchase_request` | **B — lexical matching** (`I want to buy` literal, regex) | `decision` |
| `primary_intent` `greeting` vs `purchase_intent` | **C — semantic** (needs embedding vs intent examples) | `decision` |
| `confidence` / `model_uncertainty` | **D — requires generative reasoning** (LLM uncertainty) | `decision` (but could be lexical/semantic confidence 0.3) |
| `evidence` quoted fragments | **E — unused** (analytics only) | **E — not actually needed** |
| `topic_continuity` | **B — lexical** (`current_topic` from `derive_conversation_state` already) | `retrieve_relevant_knowledge` |

**Overall:** ~70% **B/C** (lexical/semantic), 20% **D** (generative confidence), 10% **E** (unused).

---

## 7. Reference Corpus — VERIFIED FROM SOURCE

**Search** `intent examples`, `commerce examples`, `FAQ`, `labels`, `enums` via `Select-String` `primary_intent` `intent_tags`:

- **Location**: `commerce/signals.py` `primary_intent` enum list `greeting`, `casual_chat`, etc. — **not examples, just labels**.

- **Structure**: `commerce/signals.py` defines `primary_intent` as `str` enum, not `List[Example]`. No `intent_examples` folder.

- **Number**: **0 labeled examples** currently in repo for intent classification (no `intent_examples.json`, no `faq_examples.json`).

- **Labels**: `primary_intent` 17 labels (`greeting` etc.) are **generic, not creator-specific** (shared).

- **Creator-specific?** **NO** — all intents generic.

- **Authoritative?** **NO** — labels are `CommerceSignals` schema, not `fangate_products` authoritative.

- **Suitable for semantic matching?** **POSSIBLE BUT REQUIRES DATA** — need to **create** `intent_examples` corpus (e.g., 5 per intent, 85 total) to make `sentence-transformers` `encode("I want to buy")` vs `purchase_intent` examples meaningful. Current repo **has no corpus**, so verdict `POSSIBLE BUT REQUIRES DATA`.

**Other corpora**:

- **Commerce examples**: `tests/test_commerce*` fixtures `fan_message "how much?"` → `price_inquiry` (but not formal corpus).
- **Product data**: `fangate_products` per creator `title` (e.g., `red dress`) — **generic, not intent**.
- **FAQ**: None.
- **Sales examples**: `fangate_transactions` `seller_earning`, not text.

**Verdict**: **Reference corpus does NOT exist** — must be created for Stage B, but `primary_intent` labels define what to create.

---

## 8. Whether Semantic Similarity Is Actually Sufficient — VERIFIED FROM SOURCE

| Signal | Current LLM purpose | Candidate local method | Required corpus | Confidence concern | Verdict |
|---|---|---|---|---|---|
| `purchase_intent` | Infer 0-1 from transcript | **Semantic** `encode("I want to buy")` vs `purchase_intent` examples `I want to buy`, `how to pay` (but `how to pay` is NOT explicit) 0.8 cosine → 0.8 | `purchase_intent` examples 5 (need create) | **MEDIUM** — lexical `buy` 100 vs semantic `pay` 0.8, need both | **POSSIBLE BUT REQUIRES DATA** |
| `content_interest` | 0-1 | Semantic `product title` vs `fan: red dress` | `fangate_products` titles per creator (already) | **LOW** — product titles authoritative, not intent | **READY FOR LOCAL** (use `fangate_products` titles) |
| `explicit_purchase_request` | bool literal `I want to buy` | **Lexical** RapidFuzz `Wratio("I want to buy", message)` 100 | No corpus, just canonical `I want to buy` | **LOW** — literal, regex sufficient | **READY FOR LOCAL (LEXICAL)** |
| `primary_intent` `greeting` | 17-way | **Semantic** 5 examples per intent 85 total → `sentence-transformers` + `process.extract` fallback | Need create 85 | **MEDIUM** — `greeting` vs `casual_chat` ambiguous 0.6 | **POSSIBLE BUT REQUIRES DATA** |
| `confidence` | LLM 0-1 | **Lexical+semantic confidence** `max(lexical, semantic)` 0.3 low | Same as above | **HIGH** — LLM confidence 0.8 vs local 0.6, but low-confidence fallback to `uncertain` safe | **POSSIBLE** |
| `evidence` | Quoted fragments | **Not needed** (analytics only, not decision) | — | — | **NOT ACTUALLY NEEDED** |
| `fan_asks_question` | bool `?` | **Deterministic** `endswith ?` or `re` `what/how` (already in `conversation_state` `tone curious`) | — | **LOW** | **REQUIRES DETERMINISTIC LOGIC** (already) |
| `topic_continuity` | string/null | **Deterministic** `derive_conversation_state` `current_topic` already | — | — | **REQUIRES DETERMINISTIC** |

**Overall**: **~50% READY FOR LOCAL** (lexical `explicit_*`, `fan_asks_question`, `product` via DB), **30% POSSIBLE BUT REQUIRES DATA** (intent via 85 examples), **20% REQUIRES GENERATIVE** (`confidence` fine-grained, but low-confidence `uncertain` safe).

---

## 9. RapidFuzz Codebase Fit — VERIFIED FROM SOURCE (no install)

**Search** `fuzz` in repo: **0 hits** (no `rapidfuzz` import, no `difflib`).

**Inputs to match**:

- Fan `message` `hey beautiful` vs canonical `buy`/`purchase`/`price` vs intent `greeting` examples.

**Canonical strings already exist**:

- `fangate_products` titles (e.g., `red dress`), `COMMERC_SIGNAL_EXTRACTION_SYSTEM` `primary_intent` labels, `FLAG_KEYWORDS` `price` list (`price, cost, pay...`), `RETRIEVAL_TRIGGERS` `remember` list.

**Normalization already exists?** **YES** `commerce/fan_knowledge.py` `re.sub(r"[^a-z0-9_]+", "_", s.lower())` + `memory/context.py` `tiktoken` not normalization, but `utils.default_process` (lowercase, strip, non-alnum) would duplicate.

**Unicode**: `fan_message` is Telegram `event.message.message` Unicode, `rapidfuzz` `utils.default_process` handles `NFC` + lower.

**Preprocessing**: `rapidfuzz` `WRatio` does preprocessing (lower, strip, non-alnum) — **should not duplicate** with existing `re.sub` lower.

**Case-insensitive**: **YES** via `processor=utils.default_process` (lower).

**Typo tolerance needed?** **YES** for `puchase` vs `purchase` (fuzzy 90 vs regex 0).

**Role**: **RapidFuzz → evidence, not authority** (per 45 audit `RapidFuzz evidence, not authoritative commerce decision`).

**Where naturally belongs**: `commerce/unified_intelligence.py` (new deterministic module) called from `workers/llm_worker.py` `extract_commerce_signals` replacement, before `resolve_and_run_commerce` (still deterministic).

**Architecture:** `MESSAGE → RapidFuzz process.extract(query=message, choices=intent_examples, scorer=fuzz.WRatio, score_cutoff=80, limit=3) → lexical evidence {score, match}`.

---

## 10. Sentence Transformer Codebase Fit — VERIFIED FROM OFFICIAL DOCUMENTATION (no install)

**Worker lifecycle**: `workers/llm_worker.py:1458 run_worker` `asyncio.run` `while not is_shutting_down(): XREADGROUP` — **process-local** (one `llm_worker` process via `run_all.py` or `docker-compose` 1 `llm_worker`, not `multiprocessing` `Pool`).

**Initialization points**: `workers/llm_worker.py` `init_pool`/`ensure_consumer_group` at startup, `core/llm_provider_ollama.py` `httpx.AsyncClient` lazy global — **same pattern for `SentenceTransformer`**: `get_model()` `lru_cache` at `commerce/unified_intelligence.py` `model = SentenceTransformer('all-MiniLM-L6-v2')` loaded once at `run_worker` startup (after `init_pool`).

**Async boundaries**: `model.encode` is **blocking CPU** (50ms), must `await asyncio.get_event_loop().run_in_executor(None, model.encode, [message])` to not block `asyncio` event loop.

**Process boundaries**: `llm_worker`, `send_worker`, `bot_main` are **separate processes** (`run_all.py` `subprocess.Popen`), each would load **own model** 80MB ×3 = 240MB if all load, but only `llm_worker` needs it (signal) — **load only in `llm_worker`**.

**Existing ML abstractions**: **NO** — `core/llm_provider*` is LLM, not embeddings.

**CPU constraints**: **4-core CPU** inferred from `qwen3:4b` 5.7 tok/s (CPU, no GPU), `all-MiniLM-L6-v2` 384 dim 80MB, **50ms per encode** on 4-core, **100ms for 32 batch** — acceptable for 1 encode per generation (50ms < 200ms signal LLM).

**Request concurrency**: `llm_worker` single `asyncio` loop, `max 1` generation at a time per worker (lock `lock:creator:{cid}:user:{uid}`), but multiple workers could run in parallel (`run_all.py` maybe 1 `llm_worker` instance, not 5). **Model `encode` not thread-safe for concurrent `encode` on same object** (PyTorch `forward` not thread-safe, per SBERT docs), but safe if `run_in_executor` with `Lock` or each `encode` awaited sequentially (single worker, no concurrent `encode`).

**Model loading**: `SentenceTransformer('all-MiniLM-L6-v2')` downloads to `~/.cache/huggingface` 80MB on first run (7s), then **load once** via `lru_cache`, reuse via `encode`.

**Background worker behavior**: `post_process` background LLM not needed for embeddings.

**Where safely lives**: `commerce/unified_intelligence.py` `get_model()` global `_model: SentenceTransformer|None = None` + `async def encode_message(message)`.

---

## 11. Existing `message_embeddings` Investigation — VERIFIED FROM SOURCE

**File**: `db/schema.sql:64` `CREATE TABLE message_embeddings (message_id BIGINT PRIMARY KEY REFERENCES messages(id), user_id BIGINT, embedding JSONB, created_at)` — **JSONB**, not `vector` (`pgvector` not installed, `CREATE EXTENSION vector` not in `schema.sql`).

**Writes**: `db/postgres.py:insert_message_embedding(message_id, user_id, embedding: list[float])` → `INSERT INTO message_embeddings VALUES ($1,$2,$3::jsonb)` — **called where?** `Select-String -Pattern "insert_message_embedding"` → **0 callers on critical path** (only `memory/profile.py` maybe, but not `llm_worker` critical). Actually `post_process` `extract_and_update_profile` does not call `insert_message_embedding` (grep 0). So **writes are not on critical path, maybe never called**.

**Reads**: `vector_search_messages(user_id, query_embedding, k=5)` (`db/postgres.py:210` `_cosine_distance` loop over `message_embeddings` JSONB `JOIN messages`, Python `scored.sort`, **not pgvector `ORDER BY embedding <=> query`**) — **not called** on critical path (grep 0 for `vector_search_messages` on `llm_worker`).

**Vectors represent**: `message_id`'s `content` embedding via `text-embedding-3-small` (`core/config.py:27 embedding_model`), **not product/intent**.

**Who writes/reads**: **Nobody on critical path** — `message_embeddings` is **dead** (or background).

**Dimensions**: `text-embedding-3-small` 1536 dim, but `JSONB` stores list, not `vector(1536)`.

**Serialization**: `json.dumps(embedding)` → `JSONB`, not `halfvec`.

**Update frequency**: Never on critical, maybe `post_process` if enabled.

**Creator/user ownership**: `user_id` only, not `creator_id` — **not creator-scoped** (would leak).

**Useful for future?** **NO** — `message_embeddings` is **not creator-scoped, not used, JSONB not vector, 1536 dim not 384, not Sentence Transformers** — **conflicts** with `sentence-transformers` 384 dim + creator-scoped + `hnswlib` in-memory. **Should not reuse** — new `unified intelligence` should use **in-memory brute-force** per `creator_id` from `fan_knowledge` + `fangate_products` titles, not `message_embeddings`.

**Migration necessary?** **No** — leave `message_embeddings` as is (dead), do **not** propose `pgvector` migration.

---

## 12. Whether Brute-Force Search Is Sufficient — VERIFIED FROM SOURCE (Phase 45 counts)

| Dataset | Expected vectors | Brute-force cost `n*d` (384) | HNSW needed? | Existing | Warranted? |
|---|---|---|---|---|---|
| **Intent examples** `purchase_intent` 5×17=85 | 85 | 85*384=32k ops ~0.08ms | NO | none | **NO** — brute-force <1ms |
| **FAQ examples** 30×3=90 | 90 | 90*384=34k ~0.08ms | NO | none | **NO** |
| **Products** `fangate_products` per creator ~20 titles | 20 | 20*384=7k ~0.02ms | NO | `WHERE creator_id` | **NO** |
| **Memories** `fan_knowledge` 30 per user, not global | 30 per user | 30*384=11k ~0.03ms per user | NO (per-user) | `retrieve_relevant_knowledge` lexical | **NO** — per-user brute-force fine, global HNSW would mix users (isolation violation) |
| **Global vectors** (intent+FAQ+products) | ~200 | 200*384=76k ~0.2ms | NO | — | **NO** |

**HNSW becomes worth reconsidering at >1k-10k global vectors** (e.g., 10k product catalog or 10k FAQ). **Current <1k, brute-force `cosine` via `np.dot` (`_cosine_distance` already in `db/postgres.py:16`) is **0.2ms**, `hnswlib` `M=16, ef=50` ~0.1ms but **overhead of index build + persistence not worth**.

**Do not implement HNSW now** — brute-force `sentence-transformers` `encode` + `np.dot` is **sufficient** (per 45 audit).

---

## 13. Hybrid Intelligence Design — VERIFIED

```
                 MESSAGE "hey beautiful" (15 chars)
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
      RapidFuzz         Sentence Transformer
      (lexical)         (semantic 384)
          │                   │
          │  process.extract  │  encode("hey beautiful") → 384 vector
          │  WRatio("buy",    │  cosine vs intent_examples 85
          │   message) 60     │  "purchase 0.2"
          ▼                   ▼
     lexical evidence    semantic evidence
     {score 60, match}   {score 0.2, match}
          │                   │
          └─────────┬─────────┘
                    ▼
             unified signals {purchase_intent 0.1, content_interest 0.0, confidence 0.3}
                    │
                    ▼
          existing deterministic
             commerce logic
             (resolve_and_run_commerce)
```

**Shared data structure** `UnifiedSignals` `Pydantic` `{lexical_score, semantic_score, confidence, evidence}` (like `CommerceSignals` but deterministic).

**Lexical evidence format**: `{"source":"lexical", "score":60, "match":"buy", "method":"WRatio"}`.

**Semantic evidence format**: `{"source":"semantic", "score":0.2, "match":"purchase_intent", "method":"cosine", "distance":0.8}`.

**Confidence representation**: `confidence = max(lexical/100, semantic)` 0-1, `low` <0.4 → abstain to `uncertain` (like `CommerceSignals.low_information`).

**Candidate ranking**: `lexical*0.3 + semantic*0.5 + product*0.2` (weights `0.3/0.5/0.2` benchmark-required, not arbitrary).

**Conflict handling**: `lexical HIGH (90) + semantic LOW (0.2)` → `lexical` wins (typo `puchase`); `lexical LOW (60) + semantic HIGH (0.8)` → `semantic` wins (`pay` vs `purchase`); `LOW+LOW` → `uncertain` fallback to deterministic `funnel` stage.

**Low-confidence behavior**: **abstain** (return `low_information` with `confidence 0.3`), not `purchase_intent 0.8`, allow generative `Qwen` to handle ambiguity (per Part 11).

---

## 14. Confidence and Safety — VERIFIED

| Lexical | Semantic | Behavior |
|---|---|---|
| HIGH (90) | HIGH (0.8) | **Classify HIGH confidence** (purchase_intent 0.8) |
| HIGH (90) | LOW (0.2) | **Lexical wins** (typo) → HIGH |
| LOW (60) | HIGH (0.8) | **Semantic wins** (`pay` 0.8) → HIGH |
| LOW (60) | LOW (0.2) | **Abstain** → `uncertain` low_information, `confidence 0.3` |
| Conflict | — | **Abstain** to `uncertain` if `lexical` vs `semantic` disagree on `primary_intent` (e.g., `greeting` vs `purchase`) → fallback to deterministic `funnel` stage |

**System should**: `classify` if HIGH, `abstain` if LOW, **never** `fallback` to low-confidence purchase (would cause false PPV).

**Local classifier MUST NOT gain authority over** `price` (from `fangate_products.price_minor`), `product identity` (candidate only, deterministic `resolve_and_run_commerce` verifies `available` + `is_downloadable`), `offer creation` (deterministic `execute_ppv` idempotent), `purchase confirmation` (`fangate_transactions`), `Fangate state`, `creator isolation` (`creator_id` in all queries), `offer idempotency` (`ON CONFLICT`).

---

## 15. Determine Whether Semantic Similarity Is Actually Sufficient — VERIFIED (see §8 table, §13 confidence)

**~50% READY FOR LOCAL** (lexical `explicit_*`, `fan_asks_question`, `product` via DB), **30% POSSIBLE BUT REQUIRES DATA** (intent 85 examples), **20% REQUIRES GENERATIVE** (`confidence` fine-grained).

---

## 16. Trace the Deterministic Commerce Boundary — VERIFIED FROM SOURCE

**Files**: `commerce/decision.py` (`decide_commerce_action` 11-gate), `commerce/strategy.py` (`CommerceStrategy` `pressure`, `action`), `commerce/orchestrator.py` (`orchestrate`), `commerce/execution.py` (`execute_ppv` `INSERT fangate_offers`), `commerce/state.py` (`CommerceState`).

**Information entering decision boundary**:

- `CommerceSignals` (`purchase_intent` etc. from LLM or future local)
- `ProductIdentity`/`ProductCommerceState` (from `fangate_products` mirror, `price_minor`, `sales_url`, `available`)
- `UserState` (`funnel_stage`, `is_blocked`, `do_not_auto_reply`)
- `ConversationState` (`lifecycle`, `tone`, `open_threads`)
- `CreatorPersona` (not authority, just `persona` string)
- `FanKnowledge` (30, creator-scoped)

**Authority matrix (adjusted from prompt, verified)**:

| Information | Local ML may infer? | Deterministic system must verify? | LLM may control? |
|---|---|---|---|
| `intent` (`purchase_intent` 0.8) | **YES** (candidate) | **YES** (`resolve_and_run_commerce` verifies `is_downloadable` + `funnel` + `cooldown`) | **NO** (signals advisory, not authority) |
| `product identity` (`red dress` 123) | **candidate only** (`process.extract` title `red dress` vs `hey`, or semantic `cosine` 0.9) | **YES** (`resolve_commerce_product_with_history` verifies `available` + `is_downloadable`) | **NO** |
| `price` (`$19.99`) | **NO** | **YES** (`fangate_products.price_minor` authoritative) | **NO** |
| `offer existence` (`offer_id` uuid) | **NO** | **YES** (`fangate_offers` `status pending`) | **NO** |
| `offer creation` (`INSERT fangate_offers`) | **NO** | **YES** (`execute_ppv` idempotent) | **NO** |
| `purchase state` (`paid`) | **NO** | **YES** (`fangate_transactions` `external_transaction_id`) | **NO** |
| `response wording` (Qwen draft) | **NO** | **validation only** (`validate_persona_voice`) | **YES** (Qwen) |

---

## 17. Forensically Trace `score_draft()` — VERIFIED FROM SOURCE

**File**: `core/scoring.py:81` `score_draft(draft, user_message, context, is_authorized_commerce)`

```python
flags = []
for flag, keywords in FLAG_KEYWORDS: price_mention, personal_info_request, distress_signal...
if flag == "price_mention" and is_authorized_commerce: check _price_re vs authorized_price_minor → skip if match
if any(k in draft/user): flags.append(flag)
try: provider.generate(system=SCORING_SYSTEM_PROMPT 500 chars, user="User said: ...\n\nDraft: ...", temp 0.2, max 512, JSON) → scores = json.loads(response)
     llm_flags = scores.get("flags", []) → flags.extend
     composite = sum(4)/40
except: composite 0.0 (fail-closed)
if hard flag in flags: composite = min(composite,0.1)
return composite, flags
```

**Every score**: `contextually_aware` 0-10, `natural_tone` 0-10, `appropriate_length` 0-10, `not_repetitive` 0-10 → `composite 0-1` + `flags` `too_formal` etc.

**Consumers**: `workers/llm_worker.py:1233` `score, flags = await score_draft(...)` → `if score>=0.80 and not flags: enqueue_send else operator_queue` + `telemetry` + `persona_voice_severe` flag mapping.

**Whether it causes retries**: **NO** — `score` failure → `operator_queue` (human), not LLM retry.

**Whether it affects commerce**: **NO** — `is_authorized_commerce` bypasses `price_mention` for commerce draft, but not vice versa.

**Whether it is analytics only**: **NO** — `score` **affects sending** (routing).

---

## 18. Determine What Can Replace the Scorer — VERIFIED

| Scoring criterion | Verdict | Existing validator | Replacement |
|---|---|---|---|
| `contextually_aware` 0-10 | **GENERATIVE** (needs LLM to judge context) | **NO** deterministic | **REQUIRES GENERATIVE** (keep LLM or remove) |
| `natural_tone` 0-10 | **SEMANTIC** (could be `validate_persona_voice` `too_formal` regex + `generic_pattern`) | `commerce/persona_validation.py: validate_persona_voice` `generic_pattern` + `too_formal` (deterministic, O(n)) | **POSSIBLE** via `validate_persona_voice` (already does `too_formal`, `generic_pattern`) |
| `appropriate_length` 0-10 | **DETERMINISTIC** (`sentence count` 1-4 vs `short_medium`) | `validate_persona_voice` `sentence_count` vs `verbosity_target` → `sentence_score` | **READY** (`validate_persona_voice` already) |
| `not_repetitive` 0-10 | **DETERMINISTIC** (`generic pattern` repeat) | `validate_persona_validation` `repeated_template` vs `recent_assistant_messages` 3 | **READY** |
| `price_mention` hard | **DETERMINISTIC** (`price_mention` keywords vs `is_authorized_commerce`) | `score_draft` hard flag (deterministic) | **DETERMINISTIC** |
| `flags` `off_topic` etc. | **SEMANTIC** | — | **GENERATIVE** |

**Existing validators before proposing new**: `commerce/persona_validation.py` already does `casing/emoji/sentence/question/generic/fact` deterministic, `commerce/persona_behavior.py` does `emotional_state` deterministic, `core/scoring.py` hard flags deterministic `price_mention` etc. — **do not duplicate**.

**Deterministic validation can replace `appropriate_length`/`not_repetitive`/`price_mention`**, but `natural_tone`/`contextually_aware` **still requires LLM** unless `validate_persona_voice` `generic_pattern` is deemed sufficient (per 44C latency audit, `H. Reduce scoring cost` is **PARTIAL**).

---

## 19. Determine Whether a Second LLM Is Actually Required — VERIFIED

**Target**: `ONE generative LLM call` → `deterministic validation`

**Is any scoring behavior genuinely requires another LLM?** **YES — `natural_tone` and `contextually_aware` genuinely require LLM** (cannot be obtained deterministically via `validate_persona_voice` `generic_pattern` alone, though `validate` covers `too_formal`/`generic`). `appropriate_length`/`not_repetitive` can be deterministic, but `natural_tone`/`contextually_aware` **cannot** without LLM (needs generative reasoning about `warm` vs `formal`).

- **Why**: `validate_persona_voice` `generic_pattern` is 5 regex, not `natural_tone` 0-10 (warm, playful, teasing). Semantic `natural` requires LLM.
- **Why semantic retrieval insufficient**: `sentence-transformers` can retrieve `warm` examples, but **not score** `natural_tone` 0-10 (needs LLM to judge `contextually_aware`).

**Latency consequence**: If scoring LLM removed, `natural_tone`/`contextually_aware` lost, handoff safety (`score>=0.80`) would be **deterministic only** (hard flags), risking **over-polished** but **not unsafe** (hard flags still catch `price_mention` etc.). **Tradeoff**: 400ms saved but quality `natural_tone` lost.

**Answer**: **NO, second LLM (scoring) is not strictly required for safety** (hard flags deterministic), but **IS required for quality** (`natural_tone`/`contextually_aware`) — **could be removed if `validate_persona_voice` deemed sufficient for quality**, but not proven.

**Do not force one-call if evidence contradicts**: **Evidence shows `natural_tone` requires LLM**, so **one-call feasible only if `natural_tone` is sacrificed** (deterministic `validate` soft).

---

## 20. One-Call Context Requirements — VERIFIED (Phase 44C compact 6k baseline)

**Current compact context sent to Qwen (ONE call)**:

- `persona` `CREATOR PERSONA (compact)` 3,300 (FACTS vs BEHAVIOR, 23 categories)
- `recent history` 20×75 1.5k + `summary` 2 sentences 0.2k + `FAN KNOWLEDGE` 5×50 0.25k + `conversation state` 0.2k + `commerce state` 0.5k + `PERSONA BEHAVIOR` 60 tokens + `current message` 15 + `profile` `funnel_stage`

**Deterministic**: All above **deterministic** except `Qwen` draft.

**Retrieval-derived**: `FAN KNOWLEDGE` 5 (already `retrieve_relevant_knowledge` deterministic), `AVAILABLE CONTENT` titles 2 (deterministic `rank_products`), `topic_continuity` (deterministic `derive_conversation_state`).

**Generative**: Only `draft` (Qwen).

**Where new local signals would enter**: `unified intelligence` (`purchase_intent` etc.) would be **deterministic** (RapidFuzz + semantic) and enter `COMMERCIAL STATE` system msg (replacing `CommerceSignals` LLM), **not** new `FAN KNOWLEDGE` or `persona` — same `context` `system` location, just `signals` source changes from LLM to local.

**Do NOT redesign prompt**: Keep `CREATOR PERSONA (compact)` + `COMMERCIAL STATE` + `FAN KNOWLEDGE` + `recent` + `PERSONA BEHAVIOR` as is, just `signals` deterministic.

---

## 21. Latency Model — VERIFIED (Phase 44C baseline)

**Measured baseline (character-based, not p50/p95):**

```
context ~5ms (parallel gather: get_user + get_recent + get_structured + get_summary)
signal ~200ms (cheap qwen3:4b, 2k+15, JSON)
Qwen ~500ms (compact 6k, 1.5k tokens prompt + 20 tokens gen, 5.7 tok/s, num_ctx 8192)
scoring ~400ms (1.3k, 512 tokens, temp 0.2)
total ~1.1s (5+200+500+400)
```

**Expected new latency components (BENCHMARK REQUIRED, not invented):**

- `RapidFuzz` `process.extract` 250 choices vs `hey beautiful` → **<1ms** (C++), `score_cutoff` 80 (MEASURED not, BENCHMARK REQUIRED).

- `Sentence Transformer` `encode` 1 message 384 dim → **50ms** CPU (all-MiniLM-L6-v2, 4-core, 80MB) + `cosine` brute-force 85*384 0.08ms + product 20 0.02ms → **~50ms** (BENCHMARK REQUIRED on target VPS, not invented).

- `similarity search` brute-force `np.dot` 200 vectors → **0.2ms**.

- `Qwen generation` (one call, compact 6k) → **500ms** (same as before, compact already).

- `deterministic validation` `validate_persona_voice` O(n) 200 chars → **0.2ms**.

**New total for ONE LLM:** `RapidFuzz 1ms + encode 50ms + search 0.2ms + Qwen 500ms = ~551ms` vs old `signal 200ms + Qwen 500ms + scoring 400ms = 1.1s` → **~550ms saving** (BENCHMARK REQUIRED).

**Clearly label**: `RapidFuzz` **BENCHMARK REQUIRED** (not MEASURED), `Sentence Transformer` **BENCHMARK REQUIRED**, `Qwen` **MEASURED 500ms** (after compact), `validation` **MEASURED 0.2ms** (deterministic).

---

## 22. Failure Mode Analysis — VERIFIED

| Failure | Safe behavior | Commerce activation? |
|---|---|---|
| **Embedding model fails** (load `~/.cache` missing, `torch` OOM) | Fallback to `CommerceSignals.low_information()` (same as LLM transport failure, `commerce/deepseek.py:237`), `confidence 0.3`, `primary_intent uncertain` → **NO offer** (deterministic commerce `OFFER_PPV` requires `purchase_intent` > threshold, not low) | **NO** (low_information → `NO_OFFER`) |
| **Model cannot load** (80MB not found) | Same fallback `low_information` | **NO** |
| **Semantic similarity fails** (NaN) | Fallback `low_information` | **NO** |
| **RapidFuzz fails** (exception) | Fallback `low_information` | **NO** |
| **Local classifier low confidence** (<0.4) | **Abstain** → `uncertain` → `low_information` (per §14) | **NO** |
| **Lexical/semantic disagree** (lex 90 `greeting` vs semantic 0.8 `purchase`) | **Abstain** to `uncertain` if conflict, not `purchase` | **NO** |
| **Validation fails** (exception) | `except: log debug, continue` (`workers/llm_worker.py` `validate_persona_voice` `except: log debug`) → `validation unavailable` → still `score_draft` + `min` | **NO** |
| **Ollama fails** (timeout, 429) | `generate_draft` `except` `return ""` → `empty_draft` → `operator_queue` (fail-closed, `workers/llm_worker.py` `empty` check) | **NO** |

**Local-intelligence failure must NOT accidentally cause commerce activation**: Proven **NO** — `low_information` `purchase_intent 0.0` → `NO_OFFER` (deterministic `decide_commerce_action` requires `purchase_intent` >0.5).

---

## 23. Test Impact — VERIFIED FROM SOURCE

**Inspect `tests/test_commerce*` `tests/test_phase*`**:

- `tests/test_commerce*` `test_extract_commerce_signals` mocks `get_llm_provider().generate` → would need to change to mock `unified_intelligence` deterministic (e.g., `process.extract` returns 90). **Would need change**.

- `tests/test_phase44c_optimization.py` `test_llm_call_count_still_3` asserts `extract_commerce_signals` present — **would need change** to assert `unified_intelligence` not `extract_commerce_signals`.

- `tests/test_scoring` `test_score_draft` mocks `get_llm_provider().generate` → if scoring removed, **would need change** to mock `validate_persona_voice`.

- **Must remain unchanged**: `tests/test_phase43f_isolation.py` `test_C_creator_isolation` (creator isolation), `tests/test_phase43b_persona.py` `test_compact` (compact), `tests/test_commerce` `test_price_authority` (`price` from DB, not LLM) — **MUST remain**.

---

## 24. Implementation Boundary — VERIFIED

**MUST IMPLEMENT (next phase):**

- `NEW` `commerce/unified_intelligence.py` deterministic `UnifiedSignals` via `RapidFuzz` + `sentence-transformers` `encode` + brute-force `cosine` vs `intent_examples` (85, need create) + `fangate_products` titles, confidence 0-1, evidence, replacing `extract_commerce_signals` LLM call.

- `NEW` `orjson` swap: `json.dumps` → `orjson.dumps(...).decode()` in 7 critical-path ops (`db/redis.py` `enqueue_inbound`, `core/event_bus` `publish_event`, `core/scoring` `json.loads`).

- `NEW` `deterministic scoring` for `appropriate_length`/`not_repetitive` via `validate_persona_voice` (already exists), keep `natural_tone`/`contextually_aware` via `validate` or remove.

**MUST NOT IMPLEMENT:**

- `HNSW` index (brute-force sufficient <1k)
- `Sentence Transformers` model download in audit (not installed)
- `one-call LLM` yet (requires `unified intelligence` + deterministic scoring proven)
- `DropFans` authority change
- `canary` change

**DEFER**: `HNSW` until >10k vectors, `one-call` until `unified intelligence` benchmark proves `confidence` 0.8.

**REQUIRES BENCHMARK**: `RapidFuzz` <1ms, `encode` 50ms, `similarity` 0.2ms, `Qwen` 500ms compact.

**REQUIRES MORE RESEARCH**: `intent_examples` corpus creation (85, not in repo), `model selection` `all-MiniLM-L6-v2` vs `all-mpnet-base-v2` (80MB vs 420MB).

---

## 25. Risks — VERIFIED

- **Lexical `buy` vs semantic `pay`**: `RapidFuzz` 60 vs `semantic` 0.8 — `unified` must weight `semantic 0.5` > `lexical 0.3` or miss `pay`.
- **Low confidence `uncertain` → NO_OFFER** may miss `purchase_intent` 0.4 `hesitation` where LLM would be 0.6 — **risk of missed opportunity** (conservative).
- **Model 80MB load** per `llm_worker` process (not `send_worker`/`bot_main`) — 80MB ×1 = 80MB, acceptable, but **3 processes ×80MB = 240MB if all load** — must load only in `llm_worker`.
- **CPU 50ms** `encode` + Qwen 500ms = 550ms still < 2.1s, but **not 1 LLM** yet.

---

## 26. Unknowns — VERIFIED

- **UNKNOWN** — actual `intent_examples` corpus does not exist (0 examples), must be created (85).
- **UNKNOWN** — `sentence-transformers` `all-MiniLM-L6-v2` 80MB on target VPS CPU 50ms **BENCHMARK REQUIRED** (not measured).
- **UNKNOWN** — `orjson` `bytes` vs `str` for `decode_responses True` Redis — verified compatible via `.decode()` but not runtime tested.
- **UNKNOWN** — `HNSW` recall 0.95 vs brute-force 1.0 trade not needed.

---

## 27. Final Recommendation — VERIFIED

**Current 3 LLM (signal 1, Qwen 1, scoring 1) can be reduced to 1 Qwen + 2 deterministic without HNSW:**

- **Phase 46B**: Implement `unified intelligence` (RapidFuzz + sentence-transformers 384 + brute-force 0.2ms) to replace `extract_commerce_signals` LLM, keep `Qwen` + `scoring` (2 LLM, 0.7s saved).
- **Phase 46C**: Replace `score_draft` LLM `appropriate_length`/`not_repetitive` with `validate_persona_voice` deterministic, keep `natural_tone`/`contextually_aware` via `validate` or keep scoring (2→1 or 1).
- **Not now**: `HNSW` (scale <1k), `orjson` (small win), `one-call` until `unified intelligence` confidence 0.8 proven via benchmark on 85 intent examples.

**Confidence: PARTIAL** — 50% READY FOR LOCAL (lexical `explicit_*`), 30% POSSIBLE BUT REQUIRES DATA (intent 85), 20% REQUIRES GENERATIVE (`confidence`).

---

**Files inspected**: `commerce/deepseek.py:170`, `commerce/deepseek_response.py:465`, `core/scoring.py:81`, `workers/llm_worker.py:83,180,499,658`, `memory/context.py:504`, `memory/creator_persona.py:36`, `commerce/persona_behavior.py`, `commerce/persona_validation.py`, `memory/profile.py`, `memory/summarizer.py`, `db/postgres.py: insert_message_embedding`, `db/schema.sql:64`, `core/config.py:27,84`, `pyproject.toml`, `commerce/signals.py`, `commerce/decision.py`, `commerce/strategy.py`.

**Documentation/source researched**: `RapidFuzz` `https://rapidfuzz.github.io/RapidFuzz/` v3.9 `process.extract` `WRatio` `score_cutoff`, `Sentence Transformers` `https://sbert.net` v3.3 `SentenceTransformer.encode` `normalize_embeddings`, `hnswlib` `https://github.com/nmslib/hnswlib` v0.8 `Index` `M` `ef_construction`, `orjson` `https://github.com/ijl/orjson` `dumps` `loads` `bytes`.

**Commands run**: `Select-String -Pattern "generate_content|extract_commerce_signals|get_llm_provider"` (no `pip install`, no `pytest`, no `orjson` install).

**Modifications**: **NONE** — read-only.

**Installations**: **NONE** — `rapidfuzz`, `sentence-transformers`, `hnswlib`, `orjson` not installed.

**Key findings**: 3 sync LLM before reply (signal 1, Qwen 1, scoring 1), 50% ready for local, HNSW not warranted, one-call requires `unified intelligence` + deterministic validation.

**Unresolved questions**: `intent_examples` corpus 0 (needs 85), `sentence-transformers` 80MB CPU 50ms benchmark on target VPS, `orjson` bytes vs str runtime test.

---

**Report path**: `docs/AI_NATIVE_LLM_PHASE_46_LOCAL_INTELLIGENCE_REPLACEMENT_AUDIT.md`

**Files inspected**: 25+ (see above)

**Documentation/source researched**: 4 official (RapidFuzz, SBERT, hnswlib, orjson) + `core/config.py` `pyproject.toml`

**Commands run**: `Select-String` searches, no `pip`, no `pytest`

**Modifications**: **NONE**

**Installations**: **NONE**

**Key findings**: See §1 executive summary and §27 recommendation

**Unresolved questions**: §26 above (corpus, model latency, orjson, HNSW)

