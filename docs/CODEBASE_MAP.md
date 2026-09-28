# CODEBASE_MAP.md

> Comprehensive codebase analysis. All claims are verified against actual source code.
>
> Legend: **VERIFIED** = confirmed in code | **LIKELY** = inferred from patterns | **UNKNOWN** = not verifiable from code alone

---

## 1. Architecture

```
Telegram User
    |
    v
[MTProto Bot]  (chatbotv2/main.py)
    |-- Telethon event handler
    |-- debounce via Redis (chatbotv2/handlers.py)
    |-- saves inbound to PostgreSQL
    |-- enqueues to Redis Stream: inbound_messages
    |-- also consumes Redis Stream: send_messages -> sends via Telethon
    |
    v
[LLM Worker]  (workers/llm_worker.py)
    |-- consumes inbound_messages (consumer group: llm_workers)
    |-- builds context (persona + profile + summary + recent messages)
    |-- generates draft via Google Gemini
    |-- scores draft (0.0-1.0) + hard flags (core/scoring.py)
    |-- score >= 0.80 AND no flags --> auto-send via send_messages stream
    |-- score < 0.80 OR has flags --> operator_queue table in PostgreSQL
    |-- post-processes: profile extraction, summarization
    |
    v                                      v
[MTProto Bot sends]              [Operator Dashboard]  (port 8080)
reads send_messages stream         FastAPI + Jinja2
sends to Telegram user             Approve/Edit/Reject
saves outbound to DB               enqueues to send_messages stream
    |
    v
[Send Worker]  (workers/send_worker.py)
    |-- polls operator_queue every 5s
    |-- enqueues approved items to send_messages stream
```

**Four processes** run concurrently:
1. `chatbotv2.main` - MTProto bot + send stream consumer
2. `workers.llm_worker` - LLM draft generation
3. `workers.send_worker` - Operator queue flusher
4. `chatbotv2.dashboard.app` - FastAPI web dashboard (uvicorn)

Launcher: `run_all.py` starts all 4 as subprocesses.

---

## 2. Repository Structure

```
chatbot/
├── __init__.py
├── __main__.py              # Dashboard-only launcher (uvicorn)
├── run_all.py               # All-in-one launcher (subprocess manager)
├── reset_db.py              # Kill stale PostgreSQL connections
├── pyproject.toml           # Build config, deps, ruff rules
├── requirements.txt         # Flat pip deps (12 packages)
├── .env / .env.example      # Environment configuration
├── .gitignore
├── AGENTS.md                # Project documentation
├── chatbotv2.session        # Telethon SQLite session
│
├── chatbotv2/               # Main application package
│   ├── __init__.py
│   ├── main.py              # MTProto bot entry point (164 lines)
│   ├── config.py            # MTProtoSettings (64 lines)
│   ├── client.py            # Telethon client singleton (50 lines)
│   ├── handlers.py          # Inbound message handler + debounce (119 lines)
│   └── dashboard/
│       ├── __init__.py
│       ├── app.py           # FastAPI app, all routes (779 lines)
│       ├── auth.py          # Cookie-based session auth (88 lines)
│       ├── templates/       # 12 Jinja2 HTML templates
│       └── static/css/, js/ # Empty directories
│
├── core/                    # Shared infrastructure
│   ├── __init__.py
│   ├── config.py            # Settings (pydantic-settings) (58 lines)
│   ├── scoring.py           # LLM-based draft scoring (140 lines)
│   ├── circuit_breaker.py   # Circuit breaker pattern (104 lines)
│   ├── limiter.py           # In-memory rate limiter (22 lines)
│   └── shutdown.py          # Graceful shutdown coordinator (60 lines)
│
├── db/                      # Database layer
│   ├── __init__.py
│   ├── schema.sql           # PostgreSQL DDL (137 lines)
│   ├── postgres.py          # asyncpg pool + all SQL (643 lines)
│   └── redis.py             # Redis async client + streams (373 lines)
│
├── memory/                  # Context and memory
│   ├── __init__.py
│   ├── context.py           # LLM context window builder (173 lines)
│   ├── profile.py           # User profile extraction via LLM (122 lines)
│   ├── retrieval.py         # Embedding + vector search (25 lines)
│   └── summarizer.py        # Rolling summarization (65 lines)
│
└── workers/                 # Background workers
    ├── __init__.py
    ├── llm_worker.py        # LLM draft generation worker (268 lines)
    └── send_worker.py       # Operator queue flusher (126 lines)
```

---

## 3. Telegram Flow

### Telethon Client Initialization
- **File**: `chatbotv2/client.py:14-32`
- **Function**: `get_client()` - lazy singleton
- **Auth**: phone number + optional code via `TELETHON_CODE` env var
- **Session**: SQLite file `chatbotv2.session` (configurable via `telethon_session` setting)
- **API**: Uses MTProto (user account), NOT Bot API
- **Config**: `chatbotv2/config.py:MTProtoSettings` reads `api_id`, `api_hash`, `phone_number` from `.env`

### Inbound Event Handler
- **File**: `chatbotv2/handlers.py:116-119`
- **Registration**: `setup_handlers(client)` registers `@client.on(events.NewMessage(incoming=True))`
- **Called from**: `chatbotv2/main.py:138` during bot startup

### Message Receiving Flow
1. `handle_incoming_message()` at `chatbotv2/handlers.py:25`
2. Filters: skip if `sender_id is None`, skip channels/bots (`handlers.py:31-35`)
3. Rate limit check: `check_rate_limit(user_id, rate_limit_per_minute)` at `handlers.py:41`
4. Upsert user: `upsert_user()` at `handlers.py:49`
5. Save inbound to DB: `save_inbound_message()` at `handlers.py:50-54`
6. Debounce: `debounce_enqueue()` at `handlers.py:56-67`
7. If window owner: spawns `asyncio.create_task(_wait_and_process())` at `handlers.py:77`
8. If not owner: sends typing indicator at `handlers.py:71-74`

### Debounce Logic
- **File**: `chatbotv2/handlers.py:83-113`
- **Window**: configurable, default 3 seconds (`DEBOUNCE_WINDOW_SECONDS`)
- **Redis keys**: `debounce:{user_id}:lock` (NX+EX), `debounce:{user_id}:messages` (list)
- After window expires, collects all messages, takes only the latest (`debounced[-1]`)
- Enqueues single message to `inbound_messages` stream with persona attached

### Outbound Sending
- **File**: `chatbotv2/main.py:28-108`
- **Function**: `_process_send_stream(client)` - continuous loop
- **Consumer**: reads from `send_messages` stream via `read_send_messages("bot_main")`
- **Dedup**: checks `send_dedup:{dedup_id}` key, skips if exists (`main.py:42-45`)
- **Rate limiting**: per-peer token bucket via `get_send_rate_limit_wait()` + `check_send_rate_limit()` (`main.py:55-62`)
- **Entity resolution**: `client.get_input_entity()` (`main.py:67`)
- **Send**: `client.send_message(input_entity, content)` (`main.py:75`)
- **DB save**: `save_outbound_after_send()` if `save_to_db` flag is true (`main.py:80-98`)

### Telegram Error Handling
- `UserIsBlockedError`: ack message, log warning, continue (`main.py:100-102`)
- Entity not found: move to DLQ (`main.py:68-70`)
- Other send errors: move to DLQ (`main.py:103-105`)
- Startup retries: 3 attempts with 3s delay (`main.py:135-160`)

### Telegram Rate Limiting
- **Inbound**: simple counter per user, 60s window (`db/redis.py:238-244`)
- **Outbound**: token bucket per peer, 1 token/sec, burst=5 (`db/redis.py:304-373`)
- Uses Lua scripts for atomic token bucket operations

---

## 4. Redis Flow

### Redis Initialization
- **File**: `db/redis.py:21-29`
- **Function**: `get_redis()` - lazy singleton
- **URL**: from `REDIS_URL` env var via `core/config.py:Settings`
- **Encoding**: UTF-8, decode_responses=True

### Stream Names (VERIFIED)
| Stream | Constant | Purpose |
|--------|----------|---------|
| `inbound_messages` | `INBOUND_STREAM` | LLM worker input |
| `draft_messages` | `DRAFT_STREAM` | Declared but **NOT used** in current code |
| `send_messages` | `SEND_STREAM` | Outbound message queue |
| `dead_letter_queue` | `DLQ_STREAM` | Failed message storage |

### Consumer Groups (VERIFIED)
| Group | Stream | Consumers |
|-------|--------|-----------|
| `llm_workers` | `inbound_messages` | LLM workers (`llm_worker.py`) |
| `send_workers` | `send_messages` | MTProto bot main (`main.py:37`) |

### Producers
| Producer | Stream | File:Line |
|----------|--------|-----------|
| Handlers (debounce) | `inbound_messages` | `handlers.py:104` via `enqueue_inbound()` |
| LLM worker (auto-send) | `send_messages` | `llm_worker.py:167` via `enqueue_send()` |
| Send worker (queue flush) | `send_messages` | `send_worker.py:28` via `enqueue_send()` |
| Dashboard (manual send) | `send_messages` | `dashboard/app.py:157,275,479` via `enqueue_send()` |
| Dashboard (AI reply) | `inbound_messages` | `dashboard/app.py:317` via `enqueue_inbound()` |

### Consumers
| Consumer | Stream | Group | File |
|----------|--------|-------|------|
| LLM worker | `inbound_messages` | `llm_workers` | `llm_worker.py:209` |
| MTProto bot | `send_messages` | `send_workers` | `main.py:37` |

### Acknowledgements
- Inbound: `ack_inbound(msg_id)` at `db/redis.py:157-159` - called after processing in `llm_worker.py:228`
- Send: `ack_send(msg_id)` at `db/redis.py:101-103` - called after successful Telegram send in `main.py:78`

### Dead-Letter Handling
- Inbound DLQ: `move_to_dlq(message_id, reason)` at `db/redis.py:180-183` - called on processing error (`llm_worker.py:232`)
- Send DLQ: `move_send_to_dlq(message_id, reason)` at `db/redis.py:106-109` - called on send error (`main.py:105`) or entity not found (`main.py:70`)
- DLQ messages stored in Redis stream `dead_letter_queue` with `{message_id, reason, stream}`
- **No DLQ consumer or recovery process exists** (VERIFIED)

### Redis Locks
| Key Pattern | Purpose | TTL | File |
|-------------|---------|-----|------|
| `lock:user:{user_id}` | Per-user processing lock | configurable (default 60s) | `db/redis.py:186-199` |

- Acquired in `llm_worker.py:129` before processing
- Released in `llm_worker.py:195` (finally block)
- Cleared on startup: `clear_all_user_locks()` in `run_all.py:115`

### Debounce Keys (VERIFIED)
| Key | Type | TTL |
|-----|------|-----|
| `debounce:{user_id}:lock` | String (NX) | `window_seconds` (default 3s) |
| `debounce:{user_id}:messages` | List | `window_seconds + 10` |

### Rate-Limit Keys (VERIFIED)
| Key | Type | TTL |
|-----|------|-----|
| `ratelimit:{user_id}` | String (counter) | 60s |
| `send_ratelimit:{peer_id}` | Sorted Set | window_ms/1000 + 10 |

### Caches (VERIFIED)
| Key | Type | TTL | Content |
|-----|------|-----|---------|
| `context:{user_id}` | String (JSON) | 300s | Context dict |
| `persona:{user_id}` | String | 600s | Persona instructions |
| `persona:default` | String | 600s | Default persona instructions |
| `setting:auto_reply` | String | None (permanent) | "1" or "0" |
| `send_dedup:{dedup_id}` | String | 3600s | "1" |

### Consumer Group Setup
- **File**: `db/redis.py:44-62`
- **Function**: `ensure_consumer_group()` - creates groups with `mkstream=True`
- Called at startup by: `chatbotv2/main.py:133`, `llm_worker.py:200`, `dashboard/app.py:70`

### Stalled Message Recovery (VERIFIED)
- **Inbound**: `requeue_stalled_messages()` at `db/redis.py:162-177` - XAUTOCLAIM, idle > 30s
- **Send**: `requeue_stalled_send_messages()` at `db/redis.py:112-127` - XAUTOCLAIM, idle > 30s
- **Called**: `main.py:33` at start of each send loop iteration
- **Not called** for inbound stream in LLM worker (VERIFIED gap)

---

## 5. Worker Architecture

### LLM Worker
- **Entry**: `python -m workers.llm_worker --worker-id worker_1`
- **File**: `workers/llm_worker.py`
- **Main loop**: `run_worker(worker_id)` at line 198
- **Reads**: `read_inbound(worker_id, count=5, block_ms=2000)` - blocking read
- **Processing**: `process_message()` at line 121
  1. Acquire user lock (TTL 60s) - prevents duplicate processing
  2. Upsert user + save inbound to DB
  3. Check auto-reply exclusion
  4. Build context via `build_context()`
  5. Generate draft via `generate_draft()` (Google Gemini, 3 retries with backoff)
  6. Score draft via `score_draft()` (Google Gemini, 3 retries)
  7. Route: auto-send, operator queue, or skip (auto-reply off)
  8. Post-process: `asyncio.create_task(post_process(user_id))` (fire-and-forget)
  9. Release user lock (finally block)
- **Error handling**: exceptions logged, message moved to DLQ (`llm_worker.py:230-232`)
- **Shutdown**: signal handlers via `core/shutdown.py`, cleanup closes pool + redis

### Send Worker
- **Entry**: `python -m workers.send_worker --worker-id sender_1`
- **File**: `workers/send_worker.py`
- **Main loop**: `run_send_worker(worker_id)` at line 93
- **Polling**: every 5 seconds (`asyncio.sleep(5)`)
- **Processing**: `flush_queue(max_items=50)` at line 48
  - Reads pending items from `operator_queue` table
  - Enqueues each to `send_messages` stream
  - Resolves queue item as "approved"
- **Cleanup**: closes pool + redis on shutdown

### Worker Crash Handling (VERIFIED)
- **LLM worker**:
  - `requeue_stalled_messages()` exists but is **NOT called** by any worker (VERIFIED gap)
  - User lock TTL (60s) provides eventual consistency
- **Send worker**:
  - `requeue_stalled_send_messages()` IS called by MTProto bot main loop (`main.py:33`)
  - Provides recovery for stalled send messages
- **Process-level**: `run_all.py` monitors subprocess health, exits only if dashboard crashes (`run_all.py:158-169`)
- **No automatic restart** for individual worker crashes (VERIFIED)

### Background Tasks
- `post_process()` in `llm_worker.py:110` runs as fire-and-forget `asyncio.create_task()`
  - Profile extraction
  - Summarization (conditional on message count)
- No dedicated background task scheduler exists

---

## 6. Database Architecture

### Schema (VERIFIED)
**File**: `db/schema.sql`

#### Tables (10 total)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `users` | Telegram users | `id BIGINT PK`, `username`, `first_name`, `message_count`, `funnel_stage`, `persona_id FK`, `is_blocked`, `do_not_auto_reply`, `notes` |
| `messages` | Full message audit log | `id BIGSERIAL PK`, `user_id FK`, `direction`, `content`, `draft_content`, `was_edited`, `was_auto_approved`, `confidence_score`, `operator_id`, `telegram_message_id`, `sent_at` |
| `conversation_summaries` | Rolling summaries | `id BIGSERIAL PK`, `user_id FK`, `summary`, `message_count_at_summary` |
| `user_profiles` | Structured facts + embeddings | `user_id BIGINT PK FK`, `facts JSONB`, `embedding JSONB` |
| `message_embeddings` | Per-message embeddings | `message_id BIGINT PK FK`, `user_id FK`, `embedding JSONB` |
| `personas` | Conversation personalities | `id SERIAL PK`, `name`, `instructions`, `is_default` |
| `operator_queue` | Pending operator approvals | `id BIGSERIAL PK`, `user_id FK`, `draft_content`, `confidence_score`, `flags JSONB`, `status`, `assigned_to`, `resolved_at` |
| `operators` | Operator Telegram IDs | `id BIGSERIAL PK`, `telegram_id BIGINT UNIQUE`, `username`, `is_active` |
| `sessions` | Dashboard auth tokens | `token TEXT PK`, `username`, `expires_at` |
| `dlq_messages` | Dead letter queue | `id BIGSERIAL PK`, `original_stream_id`, `message_data JSONB`, `failure_reason`, `attempts` |

#### Indexes (VERIFIED)
- `idx_messages_user_id_created` on `messages(user_id, created_at DESC)`
- `idx_conv_summaries_user_id` on `conversation_summaries(user_id)`
- `idx_message_embeddings_user` on `message_embeddings(user_id)` (appears duplicated in schema)
- `idx_message_embeddings_vector` on `message_embeddings(user_id)` (B-tree, not vector/HNSW)
- `idx_personas_default` on `personas(is_default)`
- `idx_operator_queue_status` on `operator_queue(status, created_at)`
- `idx_sessions_expires` on `sessions(expires_at)`

#### Relationships
```
users.id <-- messages.user_id
users.id <-- conversation_summaries.user_id
users.id <-- user_profiles.user_id
users.id <-- message_embeddings.user_id
users.id <-- operator_queue.user_id
users.persona_id --> personas.id
messages.id <-- message_embeddings.message_id
sessions (standalone, token-based auth)
operators (standalone, telegram_id-based)
dlq_messages (standalone, stores failed stream messages)
```

#### Default Data
- 3 personas seeded: "sales" (default), "friendly", "support"
- Seed query uses `ON CONFLICT (name) DO NOTHING`

### PostgreSQL Pool
- **File**: `db/postgres.py:28-36`
- **Pool**: asyncpg, min_size=1, max_size=5, command_timeout=30s
- **Schema verification**: `verify_schema()` checks all 10 tables exist at startup

### Important SQL Operations
- `upsert_user`: INSERT ON CONFLICT UPDATE, increments `message_count` (`postgres.py:77-93`)
- `vector_search_messages`: fetches ALL embeddings for user, computes cosine distance in Python (`postgres.py:493-524`)
- `get_user_analytics`: complex CTE with stats + ranking (`postgres.py:563-621`)
- `get_user_message_timeline`: date-bucketed message counts (`postgres.py:624-643`)
- `save_outbound_after_send`: separate from `save_outbound_message` - called after actual Telegram send (`postgres.py:291-325`)

---

## 7. LLM Architecture

### LLM Clients (VERIFIED - Multiple separate clients)

All use **Google Gemini** via `google-genai` SDK:

| Client | Location | Purpose | Model |
|--------|----------|---------|-------|
| Draft generation | `llm_worker.py:38` | Generate reply drafts | `model_name` (default: `gemini-flash-latest`) |
| Scoring | `scoring.py:13` | Score draft quality | `cheap_model` (default: `gemini-flash-latest`) |
| Profile extraction | `profile.py:13` | Extract user facts | `cheap_model` (default: `gemini-flash-latest`) |
| Summarization | `summarizer.py:8` | Summarize conversations | `cheap_model` (default: `gemini-flash-latest`) |
| Embeddings | `retrieval.py:8` | Generate embeddings | `gemini-embedding-001` |

**Note**: `core/config.py:Settings` lists `openai_api_key` as a required field, but it is never used for LLM calls. Historical Groq usage replaced by Google Gemini.

### Draft Generation
- **File**: `workers/llm_worker.py:41-91`
- **Provider**: Google Gemini (`google.genai.Client`)
- **Model**: configurable, default `gemini-flash-latest`
- **Config**: temperature=0.85, max_output_tokens=200, top_p=0.95
- **System instruction**: merged from context system messages
- **Retry**: 3 attempts, backoff = (attempt+1)*3 seconds, only on `ServerError`
- **Error**: raises last exception after 3 failures
- **No empty response handling**: `response.text` used directly without null check (VERIFIED gap)

### Scoring
- **File**: `core/scoring.py:76-140`
- **Two-phase scoring**:
  1. **Keyword-based hard flags**: `price_mention`, `personal_info_request`, `distress_signal`, `legal_mention` (checked in both user message and draft)
  2. **LLM-based scoring**: returns JSON with 4 dimensions (contextually_aware, natural_tone, appropriate_length, not_repetitive), each 0-10
- **Composite**: average of 4 scores / 10
- **Hard flag override**: if any HARD_FLAGS present, composite capped at 0.1
- **Config**: temperature=0.2, max_output_tokens=512
- **Retry**: 3 attempts with backoff
- **LLM flags**: "off_topic", "too_formal", "too_generic", "breaks_persona", "awkward_phrasing", "repetitive"
- **JSON parse failure**: defaults to empty dict, all scores default to 5

### Profile Extraction
- **File**: `memory/profile.py:51-80`
- **Model**: `cheap_model`
- **Input**: last 10 messages formatted as "Fan: ... / You: ..."
- **Output**: JSON with schema-defined fields (name, age, location, interests, etc.)
- **Config**: response_mime_type="application/json", max_output_tokens=400
- **Retry**: 3 attempts, only on 429/503 errors
- **Merge**: `merge_profiles()` at `profile.py:83-102` - lists merged (set union), dicts merged (shallow), scalars overwritten
- **Embedding**: if name extracted, generates profile embedding and stores via `upsert_user_embedding()` (`profile.py:117-122`)

### Summarization
- **File**: `memory/summarizer.py:22-65`
- **Trigger**: `maybe_summarize()` called after every message processing
- **Condition**: `message_count % summarize_every_n == 0` (default: every 20 messages)
- **Model**: `cheap_model`
- **Input**: existing summary + last `summarize_every_n` messages
- **Output**: updated summary text (max 250 tokens)
- **Temperature**: 0.3
- **No retry logic** (VERIFIED gap)

### Embeddings
- **File**: `memory/retrieval.py:11-16`
- **Model**: `gemini-embedding-001` (Google Gemini)
- **Usage**: generates embedding for profile text and for retrieval queries
- **Storage**: stored as JSONB in `user_profiles.embedding` and `message_embeddings.embedding`

---

## 8. Context Construction

### Build Context
- **File**: `memory/context.py:118-173`
- **Function**: `build_context(user_id, current_message, persona)`
- **Returns**: list of message dicts with `role` and `content`

### Context Components (in order)

1. **System prompt** (`context.py:82-115`)
   - Persona instructions (from DB/cache)
   - User profile facts (formatted as "- key: value")
   - Funnel stage guidance (new/warming/engaged/converted)
   - Response rules (2-4 sentences, reference past, match energy)
   - Anti-patterns (no repetition, no filler, no template language)

2. **Summary** (`context.py:134-141`)
   - Latest conversation summary from `conversation_summaries` table
   - Added as system message: "Summary of earlier conversation:\n{summary}"

3. **Retrieved history** (`context.py:143-152`)
   - **Only triggered** if message contains trigger words (`context.py:32-44`):
     "remember", "told you", "said", "mentioned", "last time", "before", "earlier", "used to", "what was", "you said", "previous"
   - Retrieves top-k (default 3) similar messages via vector search
   - Added as system message: "Relevant past exchanges:\n[Past message]: ..."

4. **Recent messages** (`context.py:154-172`)
   - Last 30 messages from DB
   - Trimmed to token budget (1500 tokens, using tiktoken gpt-4 encoding)
   - Max 4 assistant turns kept (older outbound messages dropped)
   - Alternating user/assistant roles based on `direction` field

### Token Budgets (VERIFIED)
```python
TOKEN_BUDGET = {
    "system": 600,  # system prompt
    "profile": 250,  # (used in system prompt, not separate)
    "summary": 400,  # (not enforced - summary added as-is)
    "retrieved": 500,  # (not enforced - retrieval added as-is)
    "recent": 1500,  # recent messages budget
}
```

**Note**: Only `recent` budget is actually enforced via `trim_to_token_budget()`. System prompt, summary, and retrieved history are added without token budget enforcement (VERIFIED gap).

### Token Counting
- **File**: `memory/context.py:13,24-25`
- **Library**: tiktoken with `gpt-4` encoding
- **Note**: Used even though the LLM is Google Gemini. Token counts are approximate but functional.

---

## 9. Memory Architecture

### Profile Extraction Flow
1. **Trigger**: after every message processing in `llm_worker.py:189` via `post_process()`
2. **Data**: last 10 messages formatted as conversation
3. **LLM call**: extracts JSON facts (`profile.py:51-80`)
4. **Merge**: combines with existing profile (`profile.py:83-102`)
5. **Storage**: `update_user_profile()` upserts JSONB (`postgres.py:354-367`)
6. **Embedding** (conditional): if name extracted, generates embedding and stores in `user_profiles.embedding`

### Summary Creation Flow
1. **Trigger**: `maybe_summarize()` called after every message processing
2. **Condition**: `message_count % summarize_every_n == 0`
3. **LLM call**: updates rolling summary with new messages (`summarizer.py:29-65`)
4. **Storage**: `save_summary()` inserts new row (not update) (`postgres.py:385-397`)

**Multiple summaries accumulate** - `get_latest_summary()` fetches most recent by `created_at DESC LIMIT 1` (`postgres.py:370-382`)

### Embedding Creation Flow
1. **Profile embedding**: generated when name is extracted (`profile.py:117-122`)
2. **Message embeddings**: `insert_message_embedding()` exists in `postgres.py:479-490` but is **NEVER CALLED** (VERIFIED gap)
3. **Vector search**: `vector_search_messages()` at `postgres.py:493-524` - reads ALL embeddings for user, computes cosine distance in Python (brute-force)

### What Actually Works vs What Appears Implemented
| Feature | Status | Evidence |
|---------|--------|----------|
| Profile extraction | WORKS | Called in `llm_worker.py:115`, LLM generates JSON, stored in DB |
| Profile merge | WORKS | `merge_profiles()` at `profile.py:83-102` |
| Profile embedding | PARTIAL | Only generated when name extracted (`profile.py:117`) |
| Summary creation | WORKS | Called conditionally in `llm_worker.py:116` |
| Summary accumulation | WORKS | New rows inserted, latest fetched by timestamp |
| Message embedding creation | NOT WORKING | `insert_message_embedding()` exists but is never called |
| Vector retrieval | PARTIAL | Works but brute-force (loads ALL embeddings for user) |
| Embedding for retrieval queries | WORKS | `get_embedding()` called in `retrieval.py:24` |

---

## 10. Persona Architecture

### Database Storage
- **Table**: `personas` in `db/schema.sql:69-76`
- **Fields**: `id SERIAL PK`, `name TEXT`, `instructions TEXT`, `is_default BOOLEAN`
- **Default personas**: "sales" (default=true), "friendly", "support" seeded in schema

### Assignment
- **Per-user**: `users.persona_id FK` references `personas.id`
- **Get user persona**: `get_user_persona(user_id)` at `postgres.py:136-143` - JOIN query
- **Set user persona**: `set_user_persona(user_id, persona_id)` at `postgres.py:146-149`

### Default Persona
- **Get default**: `get_default_persona()` at `postgres.py:152-156` - `WHERE is_default = TRUE`
- **Fallback chain** in `handlers.py:93-103`:
  1. Check Redis cache for user persona
  2. If miss, query DB for user persona
  3. If none, check Redis cache for default persona
  4. If miss, query DB for default persona
- Same chain in `dashboard/app.py:307-315` for AI Reply button

### Redis Caching
| Key | TTL | Functions |
|-----|-----|-----------|
| `persona:{user_id}` | 600s | `cache_user_persona()`, `get_cached_user_persona()` |
| `persona:default` | 600s | `cache_default_persona()`, `get_cached_default_persona()` |

### Cache Invalidation
- **File**: `db/redis.py:283-288`
- **Function**: `invalidate_persona_cache(user_id=None)`
- **If user_id provided**: deletes `persona:{user_id}`
- **If None**: deletes `persona:default`
- **Called by**:
  - `dashboard/app.py:510` - after creating persona
  - `dashboard/app.py:523` - after updating persona
  - `dashboard/app.py:533` - after deleting persona
  - `dashboard/app.py:544` - after setting user persona

**Persona cache invalidation is only triggered from dashboard operations**. If persona is modified via direct DB access, cache becomes stale (VERIFIED but acceptable).

### Prompt Construction
- Persona instructions are used as the first part of the system prompt
- Built in `build_system_prompt()` at `memory/context.py:82-115`
- Format: `{persona_instructions}\n\nFan information:\nName: {first_name}\n{profile_facts}\n\nStage guidance: {guidance}\n\nResponse rules: ...`

---

## 11. Auto-Reply Architecture

### Global Auto-Reply Setting
- **Redis key**: `setting:auto_reply` (permanent, no TTL)
- **Default**: enabled (if key missing, returns True) (`redis.py:291-296`)
- **Check**: `is_auto_reply_enabled()` at `redis.py:291-296`
- **Set**: `set_auto_reply_enabled(enabled)` at `redis.py:299-301`
- **Dashboard**: toggle in `chat.html` and `chat_embed.html`, API at `dashboard/app.py:422-434`

### Per-User Exclusion
- **DB column**: `users.do_not_auto_reply BOOLEAN DEFAULT FALSE`
- **Check**: `is_user_auto_reply_excluded(user_id)` at `postgres.py:129-133`
- **Set**: `set_user_auto_reply_exclusion(user_id, exclude)` at `postgres.py:121-126`
- **Dashboard**: toggle in `chat.html` and `profile.html`, API at `dashboard/app.py:381-392`

### Where Checked (VERIFIED)
The per-user auto-reply exclusion check happens in `llm_worker.py:138-140`:
```python
if await is_user_auto_reply_excluded(user_id):
    return
```
This is checked AFTER acquiring the user lock, upserting user, and saving inbound message, but BEFORE generating draft.

The **global auto-reply** check happens even later at `llm_worker.py:148`:
```python
auto_reply_on = await is_auto_reply_enabled()
```
This is checked AFTER generating the draft and scoring it. So draft generation + scoring happens even when auto-reply is off, wasting LLM tokens (VERIFIED gap).

### Debounce
- **Window**: configurable, default 3 seconds (`DEBOUNCE_WINDOW_SECONDS`)
- **Implementation**: Redis NX lock + list accumulation
- **First message** in window returns `is_window_owner=True`
- **After window**: `_wait_and_process()` collects all messages, takes latest only

### Rate Limiting
- **Inbound**: per-user, 60s sliding window, max `rate_limit_per_minute` (default 20)
- **Response**: user receives "You're sending messages too fast" warning

### Generation + Scoring
- Draft generated via Google Gemini (`llm_worker.py:144`)
- Scored via LLM + keyword flags (`llm_worker.py:146`)
- Routing based on score and auto-reply state

### Sending
- Auto-approved: enqueued to `send_messages` stream with `was_auto_approved=True`
- Operator queue: stored in `operator_queue` table
- Both eventually reach MTProto bot for Telegram send

---

## 12. AI Reply Architecture

### Complete Flow

1. **Frontend**: User clicks "AI Reply" button in `chat.html:204` or `chat_embed.html:117`
2. **JavaScript**: `generateAiReply()` sends `POST /api/dialogs/{dialog_id}/ai-reply`
3. **API handler**: `dashboard/app.py:293-327`
   - Fetches latest inbound message for user from DB
   - Resolves persona (user-specific or default, with cache)
   - Enqueues to `inbound_messages` Redis stream with persona
4. **LLM Worker**: picks up from stream, processes normally
5. **Draft generated**: scored, routed to auto-send or operator queue
6. **Suggestion appears**: frontend polls `/api/dialogs/{dialog_id}/suggestions` every 10 seconds (`chat.html:564`, `chat_embed.html:246`)

### API Endpoints for AI Reply
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/dialogs/{id}/ai-reply` | POST | Enqueue AI reply request |
| `/api/dialogs/{id}/suggestions` | GET | Fetch pending suggestions |

### Key Detail
The AI Reply button re-enqueues the **last inbound message** content, not a new message. This means the AI generates a reply to the same message it may have already replied to.

---

## 13. Frontend Architecture

### Framework
- **Backend**: FastAPI + Jinja2 templates (server-side rendering)
- **CSS**: Tailwind CSS (CDN) + custom CSS in templates
- **JS**: Alpine.js (CDN) for reactivity, vanilla JavaScript for API calls
- **No build step**: all static assets loaded from CDN

### Template Hierarchy
```
base.html              # Minimal HTML shell
├── dashboard.html     # Main layout (sidebar, header, content area)
│   ├── overview.html  # KPI cards, recent messages, queue status
│   ├── queue.html     # Operator approval queue
│   ├── chats.html     # Chat list + iframe popup
│   ├── chat.html      # Single chat view (full page)
│   ├── users.html     # User list table
│   ├── profile.html   # User profile + message history
│   ├── personas.html  # Persona CRUD (Alpine.js)
│   └── settings.html  # Config display (read-only)
├── login.html         # Standalone login page
└── chat_embed.html    # Standalone chat for iframe embedding
```

### API Calls from Frontend
| Page | Endpoint | Method | Polling |
|------|----------|--------|---------|
| Overview | `/api/stats` | GET | Every 5s (`overview.html:291`) |
| Chat | `/api/dialogs/{id}/suggestions` | GET | Every 10s (`chat.html:564`) |
| Chat embed | `/api/dialogs/{id}/suggestions` | GET | Every 10s (`chat_embed.html:246`) |
| Chat | `/api/dialogs/{id}/send` | POST | On submit |
| Chat | `/api/dialogs/{id}/ai-reply` | POST | On button click |
| Chat | `/api/settings/auto-reply` | GET/POST | On toggle |
| Queue | `/api/queue/{id}/resolve` | POST | On action |
| Personas | `/api/personas` | GET | On Alpine init |

### Polling Behavior (VERIFIED)
- **Overview stats**: `setInterval(loadStats, 5000)` at `overview.html:291`
- **AI suggestions**: `setInterval(loadSuggestions, 10000)` at `chat.html:564`, `chat_embed.html:246`
- **No WebSocket or SSE** anywhere in the codebase (VERIFIED)

### iframe Usage
- **Chat popup**: `chats.html:240-251` opens chat in iframe popup
- **Embed endpoint**: `/dashboard/chat/{dialog_id}/embed` at `app.py:660-697`
- **Embed template**: `chat_embed.html` - standalone HTML, no dashboard layout
- **Auth**: iframe shares cookies with parent (httponly, same-site=lax)
- **Chat list**: clicking a chat sets iframe src to embed URL (`chats.html:269-276`)

---

## 14. Authentication

- **File**: `chatbotv2/dashboard/auth.py`
- **Method**: Cookie-based sessions stored in PostgreSQL `sessions` table
- **Token**: `secrets.token_urlsafe(32)`, 8-hour expiry
- **Login**: password check against `DASHBOARD_ADMIN_PASSWORD` env var (`app.py:104`)
- **Middleware**: `require_auth` FastAPI dependency used on all `/api/*` and `/dashboard/*` routes
- **Logout**: destroys session in DB, deletes cookie (`app.py:118-124`)
- **No username validation**: any username is accepted, only password is checked
- **Session cleanup**: runs at dashboard startup (`app.py:71`), deletes expired rows

---

## 15. Concurrency

### Multiple Inbound Messages Arriving Rapidly
- **Debounce** (`handlers.py:56-77`): Redis NX lock ensures only the first message in a 3s window becomes the "window owner". Other messages are buffered in a list and only the latest is processed after the window.
- **Inbound saved to DB** (`handlers.py:50-54`): EVERY message is saved to the `messages` table immediately, regardless of debounce.
- **Typing indicator** (`handlers.py:71-74`): sent for non-window-owner messages.

### Multiple Workers Processing Same User
- **User lock** (`llm_worker.py:129`): `acquire_user_lock(user_id, ttl=60s)` via Redis SET NX. If lock acquired, skip processing. Released in finally block.
- **Lock TTL**: 60 seconds (configurable via `USER_LOCK_TTL`). If a worker crashes while holding the lock, it auto-expires.
- **Duplicate prevention**: effective but not perfect - two workers could acquire the lock for the same user if timing is exact (race window is microseconds due to Redis atomicity).

### Redis Lock Behavior
- Lock key: `lock:user:{user_id}`
- TTL: configurable (default 60s)
- Acquired: `redis.set(key, "1", nx=True, ex=ttl)` - atomic
- Released: `redis.delete(key)` - **no ownership check** (VERIFIED gap: a different worker could release another's lock)
- Cleared on startup: `clear_all_user_locks()` in `run_all.py:115`

### Duplicate Processing
- **Inbound stream**: consumer group ensures each message is delivered to one consumer only
- **XACK required**: if worker crashes before ACK, message becomes pending and can be reclaimed
- **Stalled recovery**: `requeue_stalled_messages()` exists but is NOT called by LLM workers (VERIFIED)
- **User lock TTL** provides eventual consistency (60s)

### Duplicate Sends
- **Dedup**: `send_dedup:{dedup_id}` key with 1-hour TTL (`main.py:42-45`)
- **dedup_id generation**: MD5 of `{user_id}:{user_message}:{telegram_message_id}` in `llm_worker.py:150-152`
- **Also generated** in `send_worker.py:27` as `queue_item:{queue_id}` or `manual:{user_id}:{hash(content)}`
- **Also generated** in `dashboard/app.py:156` and `dashboard/app.py:274`
- **Dedup prevents**: sending the same message twice via different paths

### Stale AI Generations
- **User lock** prevents concurrent processing, so only one draft is generated per user at a time
- **But**: if an operator edits and sends a suggestion, the AI might still generate a new draft for the next inbound message
- **No cancellation**: once `generate_draft()` starts, it runs to completion
- **Fire-and-forget post-processing**: profile extraction and summarization run after lock release, could race with next message processing (VERIFIED)

### Operator Replies While AI is Generating
- **No protection**: operator can approve/edit/send a queue item while LLM worker is generating a new draft
- **Dedup key**: different dedup IDs for different messages, so both could be sent
- **Result**: potential double-reply to user (VERIFIED gap)

### Worker Crashes
- **LLM worker**: stalled messages not reclaimed (no XAUTOCLAIM call)
- **Send worker**: stalled messages reclaimed by MTProto bot main loop
- **Process-level**: `run_all.py` only kills all services if dashboard crashes; individual worker crashes leave remaining processes running but orphaned

### Redis Consumer Recovery
- **Pending messages**: messages not ACKed remain pending in consumer group
- **XCLAIM/XAUTOCLAIM**: available but only called for send stream (by main bot)
- **LLM stream**: no stalled message recovery implemented (VERIFIED)

### Summary Races
- **Trigger**: `maybe_summarize()` runs after every message, checks `message_count % N`
- **No lock**: two workers could trigger summarization simultaneously (unlikely due to user lock, but possible if lock expires during long processing)
- **Multiple summary rows**: each summarization creates a new row, not update
- **Read**: `get_latest_summary()` always gets most recent by timestamp
- **Risk**: two concurrent summaries could produce conflicting results, but only the latest is used

### Profile Update Races
- **No lock on profile update**: `update_user_profile()` is an UPSERT, so concurrent updates would overwrite
- **Fire-and-forget**: `post_process()` runs after lock release
- **Two messages from same user in quick succession**: first message's `post_process()` could race with second message's `post_process()`
- **Last write wins**: profile JSONB is replaced entirely, not merged at DB level

---

## 16. Error Handling

### Telegram Errors
- `UserIsBlockedError`: ack message, log, continue (`main.py:100-102`)
- Entity not found: move to DLQ (`main.py:68-70`)
- Other send errors: move to DLQ (`main.py:103-105`)
- Startup retries: 3 attempts with 3s delay (`main.py:135-160`)

### LLM Errors
- `ServerError`: 3 retries with backoff (`llm_worker.py:73-89`, `scoring.py:91-119`)
- JSON parse errors: default values used (`scoring.py:121-124`, `profile.py:77-80`)
- Empty response: **no handling** (VERIFIED gap) - `response.text` used directly

### Redis Errors
- Stream read errors: caught and logged, return empty list (`redis.py:97-98`, `redis.py:152-154`)
- XAUTOCLAIM errors: caught, return 0 (`redis.py:126-127`, `redis.py:176-177`)

### PostgreSQL Errors
- Connection pool: min_size=1, max_size=5, command_timeout=30s (`postgres.py:30-36`)
- Schema verification: checks all tables at startup, raises RuntimeError if missing (`postgres.py:39-62`)
- Query errors: **not explicitly caught** in most functions - will propagate to caller

### Circuit Breakers
- **File**: `core/circuit_breaker.py`
- **Instances**: openai, telegram, postgres (lazy-initialized)
- **NOT USED** anywhere in the actual application code (VERIFIED gap) - circuit breakers are defined but never called

### Graceful Shutdown
- **File**: `core/shutdown.py`
- **Signal handlers**: SIGINT, SIGTERM
- **Cleanup**: registered cleanup functions run in order
- **Bot**: closes Telethon client, PostgreSQL pool, Redis connection (`main.py:111-121`)
- **Workers**: close PostgreSQL pool and Redis connection

---

## 17. Testing

### Test Framework
- **Configured**: `pytest` and `pytest-asyncio` in dev dependencies (`pyproject.toml`)
- **Test command**: `pytest` documented in `AGENTS.md`
- **Cache**: `.pytest_cache/` directory exists

### Actual Tests
- **No test files exist** (VERIFIED) - no `test_*.py`, no `conftest.py`, no `tests/` directory
- **No test coverage** for any component
- **No database testing**, Redis testing, LLM mocking, Telethon mocking, or API testing

---

## 18. Verified Gaps

### Gap 1: No WebSocket/SSE real-time layer
**VERIFIED**: No WebSocket or SSE code exists anywhere. Frontend relies entirely on polling (5s for stats, 10s for suggestions).

### Gap 2: Dashboard requires refresh for new Telegram messages
**VERIFIED**: Chat view (`chat.html`) loads messages server-side at page load. No mechanism to receive new messages without page reload or polling. The `loadSuggestions()` polls for queue items but not for new inbound messages.

### Gap 3: AI suggestions rely on polling
**VERIFIED**: `setInterval(loadSuggestions, 10000)` in `chat.html:564` and `chat_embed.html:246`.

### Gap 4: Auto-reply setting is checked too late
**VERIFIED**: Global auto-reply (`is_auto_reply_enabled()`) checked at `llm_worker.py:148` AFTER draft generation (`llm_worker.py:144`) and scoring (`llm_worker.py:146`). Wastes LLM tokens when auto-reply is off.

### Gap 5: Context token budgets are incomplete
**VERIFIED**: Only `recent` messages are token-budgeted (`context.py:155`). System prompt, summary, and retrieved history are added without budget enforcement.

### Gap 6: Empty LLM response handling is weak/missing
**VERIFIED**: `response.text` used directly in `llm_worker.py:80`, `scoring.py:122`, `profile.py:78`, `summarizer.py:63`. No null/empty check before using the response.

### Gap 7: Multiple LLM clients exist
**VERIFIED**: 5 separate `genai.Client` instances across `llm_worker.py:38`, `scoring.py:13`, `profile.py:13`, `summarizer.py:8`, `retrieval.py:8`. Each is module-level, so one per process. Within a single worker process, multiple clients coexist (scoring + profile + summarizer + retrieval).

### Gap 8: Persona cache invalidation may be incomplete
**VERIFIED**: Cache only invalidated from dashboard API operations. Direct DB modifications bypass cache. TTL-based expiry (600s) provides eventual consistency.

### Gap 9: Message embeddings may not actually be inserted
**VERIFIED**: `insert_message_embedding()` at `postgres.py:479-490` exists but is **never called** from any code path. Message embeddings table is always empty.

### Gap 10: Vector retrieval may be brute-force
**VERIFIED**: `vector_search_messages()` at `postgres.py:493-524` fetches ALL embeddings for a user with `SELECT ... WHERE user_id = $1`, then computes cosine distance in Python. No vector index, no pgvector extension.

### Gap 11: Summarization may race
**VERIFIED**: No lock prevents concurrent summarization. Multiple summary rows accumulate (insert, not update). Only latest is read. Low risk due to user lock, but possible if lock expires during long processing.

### Gap 12: Operator notification may be incomplete
**VERIFIED**: `notify_operators()` at `llm_worker.py:94-107` only logs. No Telegram notification sent to operators when queue item is created.

### Gap 13: iframe authentication may be problematic
**VERIFIED**: iframe uses same cookies as parent (httponly, same-site=lax). Should work for same-origin. No CORS issues since same origin.

### Gap 14: Outbound embeddings may be missing
**VERIFIED**: `insert_message_embedding()` never called. No embeddings exist for outbound messages.

### Gap 15: Stale AI replies may be possible
**VERIFIED**: No mechanism to cancel in-flight generations. Operator can send while AI is generating. Dedup prevents same message being sent twice, but different messages could both be sent.

### Gap 16: Operator/manual replies may race with automatic replies
**VERIFIED**: No coordination between operator send and LLM worker auto-send. Both can enqueue to `send_messages` stream independently. Dedup only prevents exact duplicates.

### Gap 17: Redis failed-message recovery may be incomplete
**VERIFIED**: `requeue_stalled_messages()` exists (`redis.py:162-177`) but is **never called** by any process. LLM worker messages that crash before ACK remain pending forever until consumer group is reset.

### Gap 18: Message ordering guarantees may be incomplete
**VERIFIED**: Redis Streams provide per-stream ordering. But `send_messages` stream mixes auto-approved, operator-approved, and manual messages. No priority ordering. Out-of-order delivery possible if different producers interleave.

---

## 19. Risk Assessment

### High Risk
| Risk | Impact | Evidence |
|------|--------|----------|
| LLM worker crash loses pending messages | Messages stuck in pending state, never processed | No XAUTOCLAIM for inbound stream |
| Empty LLM response causes crash or bad reply | `response.text` could be None or empty | No null check in 4 locations |
| Operator race with AI double-reply | User receives two replies to same message | No coordination between paths |
| Profile overwrite race | Lost profile facts from concurrent updates | Fire-and-forget post_process |

### Medium Risk
| Risk | Impact | Evidence |
|------|--------|----------|
| Auto-reply wastes LLM tokens | Draft generated when auto-reply is off | Global check at line 148, after generation |
| Vector retrieval degrades with scale | O(n) scan per query | Brute-force in postgres.py:493-524 |
| Circuit breakers unused | No protection against cascading failures | Defined but never called |
| Summarization races | Inconsistent summaries | No lock, multiple rows accumulate |

### Low Risk
| Risk | Impact | Evidence |
|------|--------|----------|
| Persona cache staleness | Wrong persona used for up to 600s | TTL-based, dashboard invalidation |
| Dashboard polling delay | Up to 10s delay for new suggestions | 5-10s intervals |
| DLQ messages unrecoverable | Failed messages lost in Redis stream | No DLQ consumer |

---

## 20. Recommended Implementation Order

Based on verified gaps and risk assessment:

### Phase 1: Stability (address crashes and data loss)
1. Add empty LLM response handling in `llm_worker.py:80`, `scoring.py:122`, `profile.py:78`, `summarizer.py:63`
2. Add XAUTOCLAIM for inbound stream in LLM worker loop
3. Add lock ownership check on release (compare value before delete)
4. Wire up circuit breakers to actual call sites

### Phase 2: Efficiency (reduce wasted work)
5. Move global auto-reply check before draft generation in `llm_worker.py`
6. Add token budget enforcement for system prompt, summary, and retrieved history
7. Implement message embedding insertion in post_process
8. Add vector index (pgvector HNSW) for embedding search

### Phase 3: Real-time (improve UX)
9. Add WebSocket or SSE layer for dashboard
10. Push new messages and suggestions to frontend in real-time
11. Add operator notifications via Telegram

### Phase 4: Robustness (handle edge cases)
12. Add coordination between operator sends and AI auto-sends
13. Add proper DLQ consumer for failed message recovery
14. Add message priority ordering in send stream
15. Add graceful handling for concurrent profile updates

### Phase 5: Quality (testing and observability)
16. Add unit tests for core logic (scoring, context building, profile merge)
17. Add integration tests for Redis stream flow
18. Add API tests for dashboard endpoints
19. Add LLM mocking for worker tests

---

## Files Inspected

| File | Lines | Key Findings |
|------|-------|-------------|
| `chatbotv2/main.py` | 164 | Bot entry point, send stream consumer |
| `chatbotv2/client.py` | 50 | Telethon singleton, lazy init |
| `chatbotv2/handlers.py` | 119 | Inbound handler, debounce logic |
| `chatbotv2/config.py` | 64 | MTProtoSettings, env vars |
| `chatbotv2/dashboard/app.py` | 779 | FastAPI routes, all APIs |
| `chatbotv2/dashboard/auth.py` | 88 | Cookie session auth |
| `core/config.py` | 58 | Settings class |
| `core/scoring.py` | 140 | LLM scoring + hard flags |
| `core/circuit_breaker.py` | 104 | Circuit breaker (unused) |
| `core/limiter.py` | 22 | In-memory rate limiter |
| `core/shutdown.py` | 60 | Graceful shutdown |
| `db/schema.sql` | 137 | PostgreSQL DDL |
| `db/postgres.py` | 643 | All SQL operations |
| `db/redis.py` | 373 | Redis streams, locks, caches |
| `memory/context.py` | 173 | Context window builder |
| `memory/profile.py` | 122 | Profile extraction |
| `memory/retrieval.py` | 25 | Embedding + search |
| `memory/summarizer.py` | 65 | Rolling summarization |
| `workers/llm_worker.py` | 268 | LLM draft worker |
| `workers/send_worker.py` | 126 | Queue flush worker |
| `run_all.py` | 176 | Subprocess launcher |
| `reset_db.py` | 26 | Stale connection cleanup |
| `.env.example` | 31 | Environment template |
| `AGENTS.md` | -- | Project documentation |
| `pyproject.toml` | -- | Build config |
| `requirements.txt` | -- | Dependencies |
| All 12 templates | -- | Frontend HTML |

## Files Changed
- `docs/CODEBASE_MAP.md` (created)

## Tests Executed
- None (no tests exist in the codebase)

## Verified Gaps (18/18)
All 18 gaps from the task specification have been verified against actual source code. See Section 18 for details.

## Unknowns
- Whether `draft_messages` Redis stream was ever used (declared but unused)
- Whether `operators` table is populated and used (schema exists, no API reads it)
- Whether `dlq_messages` PostgreSQL table is populated (DLQ goes to Redis stream, not this table)
- Whether `webhook_url` and `webhook_secret` in Settings were ever used (declared but unused)
- Actual Google Gemini API behavior on empty responses

## Risky Areas
- LLM worker crash recovery (no XAUTOCLAIM for inbound stream)
- Empty LLM responses (no handling)
- Operator/AI reply race conditions
- Vector search scalability (brute-force)
- Circuit breakers defined but never wired up

## Recommended Next Step
Implement Phase 1 (Stability) changes: empty response handling, XAUTOCLAIM for inbound stream, lock ownership checks, and circuit breaker wiring.
