# Handoff Document — Telegram Human-in-the-Loop Chatbot

> **Version:** 1.0 — 2026-09-12  
> **Repo root:** `E:\chatbot`  
> **Commit:** `19e1884` (main) — 5 commits total, see `git log --oneline -20`  
> **Audience:** Next developer taking ownership. Assumes Python 3.11+, PostgreSQL 15+, Redis 7+, Telethon familiarity.  
> **Research method:** Phased codebase inspection — all claims verified against source at `file_path:line_number`. No speculation.  
> **Status:** Production-hardened (see `docs/FINAL_WHOLE_SYSTEM_TECHNICAL_CLOSURE_REPORT.md:1` — GO verdict, 396 targeted tests pass). Canonical LLM path is `new` (one-call Qwen2.5:3b).

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Purpose & Scope](#2-project-purpose--scope)
3. [Technology Stack](#3-technology-stack)
4. [Repository Layout](#4-repository-layout)
5. [Architecture Overview](#5-architecture-overview)
6. [Core Data Flows](#6-core-data-flows)
7. [Database, Redis & Migrations](#7-database-redis--migrations)
8. [Realtime Phase-1 Contract (IMMUTABLE)](#8-realtime-phase-1-contract-immutable)
9. [LLM & Context Engine](#9-llm--context-engine)
10. [Commerce Subsystem](#10-commerce-subsystem)
11. [Workers, Bot & Dashboard](#11-workers-bot--dashboard)
12. [Configuration / Env Vars](#12-configuration--env-vars)
13. [Local Setup & Running](#13-local-setup--running)
14. [Dashboard API Surface](#14-dashboard-api-surface)
15. [Testing Strategy](#15-testing-strategy)
16. [Known Gaps, Risks & Deferred Work](#16-known-gaps-risks--deferred-work)
17. [Change Boundary Rules](#17-change-boundary-rules)
18. [Onboarding Checklist](#18-onboarding-checklist)
19. [Appendix A — Key File Index](#appendix-a--key-file-index)
20. [Appendix B — Glossary](#appendix-b--glossary)

---

## 1. Executive Summary

This is an **MTProto user-account Telegram chatbot** (Telethon, not Bot API) that provides **AI-drafted replies with human operator approval**. Fans message a creator's Telegram account; the system generates a draft via **Qwen2.5:3b (Ollama, authoritative)** with deterministic fallbacks, scores it (`core/scoring.py:1`, `core/scoring_deterministic.py`), and either **auto-sends (confidence ≥ 0.80, no hard flags)** or **routes to an operator queue**. Operators approve/edit/reject via dashboard or Telegram bot. Post-purchase vault media, free-photo ledger, DropFans commerce, scheduling, and observability are built-in.

**Four concurrent processes** are managed by `run_all.py:186`:
- `chatbotv2.main` — MTProto bot + send-stream consumer
- `workers.llm_worker` — inbound → draft → score → route
- `workers.send_worker` — flushes `operator_queue` → `send_messages` stream
- `workers.scheduler_worker` — scheduled messages + reconciliation + autonomous re-engagement
- `chatbotv2.dashboard.app` — FastAPI dashboard on `:8080`

**Canonical runtime:** `core/config.py:148` `llm_path = "new"` (Context Engine + single Qwen generation). `context_engine_enabled = True` at 100% (`core/config.py:141`), `context_engine_observational` off. `llm_provider = "ollama"` (`core/config.py:84`). Gemini is fallback only when quota/flow demands it.

**Creator isolation is a first-class invariant:** locks, debounces, persona, products, vault, and events are all `creator_id`-scoped. Never broaden without review (see `AGENTS.md:48-66`).

---

## 2. Project Purpose & Scope

| Aspect | Detail |
|--------|--------|
| Primary user | Fan messaging the creator via Telegram |
| Primary operator | Creator / moderator using dashboard (`:8080`) or Telegram operator bot (approve/edit/reject) |
| Core promise | Every fan message is saved, debounced, processed once, drafted, scored, routed deterministically. No LLM-invented price/auth. |
| Commerce promise | PPV/offers are **deterministic, creator-scoped, DB-authoritative** (`commerce/execution.py`, `commerce/product_catalog.py:1`). LLM is advisory only. |
| Delivery promise | Paid media goes through **atomic reserve → Telegram send → finalize** (`db/vault.py:46`, `chatbotv2/main.py:254`). Duplicate sends blocked via `send_dedup:{creator}:{dedup_id}`. |
| Non-goals (historical) | Groq/OpenAI direct usage (replaced by Ollama), paginated AI suggestions without WS fallback, vector HNSW index (brute-force JSONB instead) |

Out of scope for this handoff: Marketing copy, Telegram Bot API migration, pgvector rollout.

---

## 3. Technology Stack

| Layer | Choice | Where |
|-------|--------|-------|
| Language | Python ≥3.11 | `pyproject.toml:5` |
| Telegram | Telethon 1.34+ MTProto user account | `chatbotv2/client.py:1`, `requirements.txt:10` |
| Web | FastAPI 0.100+, Uvicorn, Jinja2, python-multipart | `chatbotv2/dashboard/app.py:1`, `requirements.txt:1` |
| DB | PostgreSQL 15+ (asyncpg), Redis 4.5+ (redis.asyncio), Redis Streams + Pub/Sub | `db/postgres.py:28`, `db/redis.py:1` |
| LLM (authoritative) | Qwen2.5:3b via Ollama native `/api/chat` at `https://ollama.brestalogistics.co.ke` | `core/llm_provider_ollama.py:1` |
| LLM (fallback) | Google Gemini (`google-genai`), Groq/OpenAI legacy keys | `core/config.py:17`, `core/llm_provider_gemini.py` |
| Embeddings | `text-embedding-3-small` via OpenAI-compatible + `sentence-transformers` (MiniLM hybrid) | `pyproject.toml:23` |
| Auth | Cookie sessions in PG `sessions` table | `chatbotv2/dashboard/auth.py` |
| Tooling | `ruff format .` / `ruff check .`, `pytest` + `pytest-asyncio` | `AGENTS.md:34` |
| Packaging | `setuptools`, `pyproject.toml:33` packages = `core, db, memory, workers, chatbotv2, commerce, vault, context_engine, segments, agent, integrations` | `pyproject.toml:38` |

Dependencies are declared twice (intentionally): `pyproject.toml:6` and `requirements.txt:1` — keep in sync.

---

## 4. Repository Layout

```
E:\chatbot\
├── AGENTS.md                        # Architecture + invariants + run commands
├── pyproject.toml                   # deps, ruff, pytest markers, packages
├── requirements.txt                 # flat pip deps (12)
├── .env.example                     # 111-line template (copy to .env)
├── .env                             # local secrets (gitignored)
├── db/schema.sql                    # 217-line baseline DDL (extensions, indexes, seed personas)
├── db/migrations/*.sql              # 25 versioned migrations (see §7)
├── run_all.py                       # single-entry launcher — 5 subprocesses (237 lines)
├── reset_db.py                      # kills stale PG connections
├── chatbotv2.session                # Telethon SQLite session (gitignored, do NOT commit)
├── chatbotv2/
│   ├── main.py                      # 974-line MTProto bot + send-stream consumer
│   ├── client.py                    # Telethon singleton (50 lines)
│   ├── handlers.py                  # 176-line inbound + debounce + typing indicator
│   ├── config.py                    # MTProtoSettings (64 lines)
│   └── dashboard/
│       ├── app.py                   # 196-line FastAPI composition (mounts 26 routers)
│       ├── auth.py                  # cookie sessions
│       ├── ws_manager.py            # WebSocket fan-out
│       ├── event_subscriber.py      # Redis Pub/Sub → WS bridge
│       ├── routes/                  # 26 routers: analytics, queue, vault, fangate, dropfans, live, segments, ws, etc.
│       ├── templates/               # 12 Jinja2 pages
│       └── static/{css,js}/         # CDN-loaded (Tailwind, Alpine)
├── core/                            # 40 modules
│   ├── config.py                    # 220-line Settings (single source of truth, lru_cache)
│   ├── event_bus.py                 # 142-line Redis Pub/Sub publish (best-effort)
│   ├── scoring.py                   # keyword + LLM composite (0.0-1.0, hard-flag cap 0.1)
│   ├── scoring_deterministic.py     # heuristic replacement for LLM #3
│   ├── one_call.py                  # 830-line structured JSON contract + validation + grounding flags
│   ├── one_call_pipeline.py         # 361-line pipeline (context → hints → generate → validate → quality)
│   ├── llm_provider.py              # provider interface + factory
│   ├── llm_provider_ollama.py       # 492-line native /api/chat adapter (think=false default)
│   ├── llm_provider_gemini.py       # fallback adapter
│   ├── conversation_state.py        # derive_conversation_state (once per turn)
│   ├── conversation_contract.py     # participants + contract (Phase 89)
│   ├── context_compact.py           # one-call prompt builder
│   └── ... (capability_contract, health, telemetry, etc.)
├── context_engine/                  # 18 modules — canonical retrieval
│   ├── authoritative_assembly.py    # 337-line ONE TURN = ONE SNAPSHOT (parallel I/O, derive once)
│   ├── models.py                    # AuthoritativeState (frozen dataclass)
│   ├── assembler.py / renderer.py / scorer.py / budget.py
│   └── worker_integration.py        # used by llm_worker new path
├── memory/                          # 9 modules — legacy context + profile
│   ├── context.py                   # legacy build_qwen3_context
│   ├── context_assembler.py         # LLMContext builder
│   ├── creator_persona.py           # structured persona (DB + Redis cache)
│   ├── profile.py / summarizer.py / retrieval.py
│   └── context_limits.py
├── commerce/                        # 65 modules — deterministic commerce authority
│   ├── state.py / signals.py / decision.py / execution.py
│   ├── product_catalog.py           # Phase 102 menu (fangate_products, 143 lines)
│   ├── product_selection.py         # history-aware selection
│   ├── single_creator.py            # resolve_single_application_creator()
│   ├── pipeline.py / orchestrator.py
│   ├── vault_taxonomy.py / free_photo.py / free_photo_routing.py / free_photo_delivery.py
│   ├── warming.py / relationship.py / readiness.py
│   └── post_purchase.py / reconciliation.py / re_engagement.py
├── db/
│   ├── postgres.py                  #  ~1400-line asyncpg pool + all SQL (upsert_user, save_inbound_message, etc.)
│   ├── redis.py                     #  814-line streams, locks, debounce, dedup, DLQ, rate-limit Lua
│   ├── migrate.py                   #  298-line version-tracked migration engine
│   ├── vault.py                     #  563-line pending→sent lifecycle (reserve/finalize/release)
│   ├── fangate.py                   #  588-line Fangate DAL (creator_integrations, products mirror)
│   ├── dropfans.py                  #  DropFans DAL (sole active provider)
│   ├── automation.py / segments.py  #  segments, automation scheduling
│   └── migrations/                  #  25 .sql files
├── workers/
│   ├── llm_worker.py                #  ~1500-line generation + scoring + routing (canonical new path)
│   ├── send_worker.py               #  170-line operator_queue flusher (every 5s)
│   └── scheduler_worker.py          #  358-line scheduler + reconciliation + production control
├── vault/ / segments/ / agent/ / integrations/
│   ├── vault/service.py             # vault delivery service
│   ├── segments/                    # fan segmentation
│   ├── agent/                       # Phase 2 agent runtime (shadow/canary)
│   └── integrations/fangate|dropfans/  # external API clients
├── tests/                           # 170 test files, markers: unit/integration/live
│   └── conftest.py
├── docs/                            # 260 historical forensic reports (see warning below)
│   ├── HANDOFF.md                   # THIS FILE
│   ├── CODEBASE_MAP.md              # 1001-line verified codebase map
│   └── FINAL_WHOLE_SYSTEM_TECHNICAL_CLOSURE_REPORT.md  # GO verdict
└── scripts/ / automation/ / memory/  # helpers & scripts
```

> **WARNING — `docs/` noise:** `docs/` contains ~260 historical reports (`docs\` → 260 entries). These are forensic / audit artifacts from 70+ phases. **Do NOT chase them during daily dev.** Source of truth is code; reports are evidence only. Read `docs/CODEBASE_MAP.md:1` and `AGENTS.md` first.

---

## 5. Architecture Overview

```
                 ┌─────────────────────────────────────────┐
                 │       Telegram (MTProto user account)   │
                 └──────────────┬──────────────────────────┘
                                │ NewMessage(incoming=True)
                                ▼
                 ┌─────────────────────────────────────────┐
                 │ chatbotv2/main.py:937 run()             │  Telethon client (chatbotv2/client.py:14)
                 │  └─ setup_handlers()  chatbotv2/handlers.py:173│
                 └──────────────┬──────────────────────────┘
                                │
                 ┌──────────────▼──────────────────────────┐
                 │ chatbotv2/handlers.py:26 handle_incoming_message │
                 │  • filter Channel/Chat/bot              │
                 │  • check_rate_limit (20/min)            │
                 │  • resolve creator (single_creator)     │
                 │  • save_inbound_message (creator-scoped)│
                 │  • publish message.created (event_bus)  │
                 │  • debounce_enqueue (3s window) ────────┼───► Redis: debounce:{creator}:{user}:lock + :messages
                 │  • if window owner → _wait_and_process  │
                 └──────────────┬──────────────────────────┘
                                │ after 3s: get_debounced_messages → enqueue_inbound
                                ▼
                 ┌─────────────────────────────────────────┐
                 │ Redis Stream: inbound_messages          │  db/redis.py:14 INBOUND_STREAM
                 │ Consumer group: llm_workers             │  db/redis.py:40
                 └──────────────┬──────────────────────────┘
                                │ XREADGROUP (llm_worker)
                                ▼
                 ┌─────────────────────────────────────────┐
                 │ workers/llm_worker.py:464 process_message │
                 │  • acquire_user_lock (creator-scoped)   │  db/redis.py:283 lock:creator:{cid}:user:{uid}
                 │  • upsert_user + is_auto_reply_excluded │
                 │  • get_structured_persona_async         │
                 │  • assemble_authoritative_context       │  context_engine/authoritative_assembly.py:52 (ONE TURN=ONE SNAPSHOT)
                 │  • publish ai.generation_started        │
                 │  • derive_conversation_state (once)     │
                 │  • commerce state (conversational.py)  │
                 │  • one_call_generation (new) OR        │
                 │  │  legacy generate_draft               │  core/one_call_pipeline.py:59
                 │  • validate_one_call_response           │  core/one_call.py:189
                 │  • validate_draft_quality               │  core/scoring_deterministic.py
                 │  • routing decision:                    │
                 │  │   score ≥0.80 & no hard flags →    │
                 │  │      enqueue_send → send_messages   │  MUST succeed before ai.generation_completed (§8)
                 │  │   else → add_to_operator_queue       │
                 │  • publish ai.generation_completed/failed + suggestion.created
                 │  • post_process (profile + summary, fire-and-forget)
                 └──────────────┬──────────────────────────┘
                                │ auto-approved branch
                                ▼
                 ┌─────────────────────────────────────────┐
                 │ Redis Stream: send_messages             │  db/redis.py:17 SEND_STREAM
                 │ Consumer group: send_workers / bot_main │  db/redis.py:41
                 │ Dedup: send_dedup:{creator}:{dedup} TTL 3600 │ db/redis.py:65
                 └──────────────┬──────────────────────────┘
                                │ XREADGROUP (chatbotv2/main.py:537 _process_send_stream)
                                ▼
                 ┌─────────────────────────────────────────┐
                 │ chatbotv2/main.py:89 _process_send_entry_inner │
                 │  • is_send_duplicate?  → skip           │
                 │  • rate-limit Lua (1/sec, burst 5)      │  db/redis.py:498
                 │  • is_blacklisted? → DLQ                │
                 │  • get_input_entity → send or DLQ       │
                 │  • reserve_delivery (vault, atomic)     │  db/vault.py:46
                 │  • _validate_media_path / send_file     │
                 │  • mark_send_dedup + ack_send           │
                 │  • save_outbound_after_send             │
                 │  • finalize_delivery / finalize_dropfans│
                 │  • publish message.sent / vault.media_sent
                 │  • on failure → move_send_to_dlq + message.send_failed
                 │  • reclaim stalled via XAUTOCLAIM        │  db/redis.py:158 (idle > 60s)
                 └─────────────────────────────────────────┘
                                │
                 ┌──────────────▼──────────────────────────┐
                 │ workers/send_worker.py:129 run_send_worker │
                 │  poll every 5s → flush_queue            │
                 │  get_pending_queue_items → enqueue_send │
                 │  resolve_queue_item("approved")         │
                 │  publish operator_queue.updated         │
                 └─────────────────────────────────────────┘
                                │
                 ┌──────────────▼──────────────────────────┐
                 │ workers/scheduler_worker.py:314 run_scheduler │
                 │  every 10s: claim_due_messages → enqueue_send│
                 │  reconciliation (DropFans sales)        │
                 │  autonomous re-engagement (48h offers)  │
                 │  production_control orchestration       │
                 └─────────────────────────────────────────┘
                                │
                 ┌──────────────▼──────────────────────────┐
                 │ chatbotv2/dashboard/app.py:79 FastAPI   │  :8080
                 │  26 routers, Jinja2, cookie auth        │
                 │  event_subscriber → Redis Pub/Sub → WS  │
                 │  fallback: polling (stats 5s, suggestions 10s)
                 └─────────────────────────────────────────┘
```

**Process supervision:** `run_all.py:206` spawns 5 subprocesses, heartbeats via `core/worker_heartbeat.py:10` (interval 10s, TTL 30s), frees port 8080, clears stale locks, auto-applies migrations.

---

## 6. Core Data Flows

### 6.1 Inbound Fan → Draft → Route (hot path)

1. `chatbotv2/handlers.py:26` `handle_incoming_message`  
   - `sender_id is None` or `Channel/Chat/bot` → return  
   - `check_rate_limit(user_id, rate_limit_per_minute=20)` → reply "sending too fast" if exceeded  
   - `upsert_user(user_id, username, first_name)` (`db/postgres.py:96`)  
   - `resolve_single_application_creator()` (`commerce/single_creator.py`) → `creator_id` or `None`  
   - `save_inbound_message(user_id, content, telegram_message_id, creator_id)` (`db/postgres.py:352`) — idempotent ON CONFLICT  
   - Deterministic `generation_id = md5("{user_id}:{content}:{telegram_message_id}")` (`chatbotv2/handlers.py:63`)  
   - `publish_event("message.created", ..., generation_id, creator_id)` (`core/event_bus.py:24`) — best-effort  
   - `debounce_enqueue(user_id, content, message_data, 3s, creator_id)` (`db/redis.py:326`) — first caller gets `is_window_owner=True`, others buffer in `debounce:{creator}:{user}:messages` and get typing indicator  

2. `chatbotv2/handlers.py:128` `_wait_and_process` (after 3s sleep)  
   - `get_debounced_messages(user_id, creator_id)` → list, picks `latest = debounced[-1]` (earlier messages are audit-only)  
   - Resolves persona **creator-scoped**: `get_cached_user_persona` → `get_user_persona` → `get_cached_default_persona` → `get_default_persona` (`db/postgres.py:155`)  
   - `enqueue_inbound({user_id, content=latest.content, telegram_message_id, username, first_name, persona, generation_id})` (`db/redis.py:186`) — if `generation_id` missing it re-derives deterministically  

3. `workers/llm_worker.py:464` `process_message`  
   - If `generation_id is None`: re-derive same MD5 (`workers/llm_worker.py:470`)  
   - Telemetry: `get_telemetry_collector().start_generation(user_id, creator_id, runtime_mode="new"/"legacy", generation_id)` (`core/telemetry.py`)  
   - `resolve_single_application_creator()` again for lock propagation → `_creator_id`, `_creator_sales_enabled`  
   - Sync telemetry `creator_id` into cache key `(creator_id, generation_id)`  
   - `acquire_user_lock(user_id, ttl=60, creator_id)` (`db/redis.py:283` → key `lock:creator:{cid}:user:{uid}`) — if false, skip  
   - `upsert_user` + `is_user_auto_reply_excluded` in parallel  
   - Fail-closed if `_creator_id is None and not autonomy_enabled` (`core/config.py:111`) → `add_to_operator_queue(flags=["creator_context_unavailable"])` + `ai.generation_completed` + `suggestion.created` batch  
   - Fetch structured persona snapshot (`memory/creator_persona.py:get_structured_persona_async`)  
   - **Authoritative assembly** if `llm_path == "new"` (`core/config.py:148`): `assemble_authoritative_context(creator_id, user_id, current_message, generation_id, persona_override, structured_persona_snapshot)` (`context_engine/authoritative_assembly.py:52`) — parallel: `get_user`, `get_user_profile`, `get_recent_messages(limit=20, creator_id)`, `get_latest_summary_with_age`, `get_structured_persona_async`; fan-knowledge isolation; persona text via cache/DB; derive `persona_name`; `trim_to_token_budget(conversation=1500)` + max 3 assistant turns; **derive `conversation_state` exactly once** (counter `get_derive_call_count()`), derive `participants` + `conversation_contract` (`core/conversation_contract.py`), build `commerce_context_text` via `memory/context_assembler.py:build_llm_context` reusing fetched user/recent/summary. On failure: fallback to `build_qwen3_context`.  
   - Else legacy: `build_qwen3_context(user_id, user_message, persona, creator_id)` (`memory/context.py`)  
   - Inject menu context: `commerce/product_catalog.py:130` `get_menu_context(creator_id, max_items=5)` → appends `Authoritative menu for creator {id}:\n- {title} (id:{pid}) {price} [active]`  
   - Publish `ai.generation_started` `{message_length, has_content}` (no raw message, privacy — Phase 103)  
   - Extract LTM: `commerce/long_term_memory.py:extract_explicit_memories` → `add_memory_item`  
   - Fan knowledge: `commerce/fan_knowledge.py:extract_fan_knowledge` → `add_knowledge_item`  
   - Behavioral signal: `commerce/behavioral_intelligence.py:observe_behavioral_signal` with `hour_utc`  
   - Shadow Q1 (if `qwen_shadow_enabled`): `core/qwen3_shadow.py:ShadowRunner.run_shadow` launched as `asyncio.create_task` (never mutates state)  
   - Commerce state: `commerce/conversational.py:build_conversational_commerce_state` (reuses authoritative `conversation_state`, `profile`) → `desire, temperature, readiness, sales_window, objective, next_best_action, response_mode, question_policy, warming, phase101_readiness`  
   - Experiment: `commerce/adaptive_optimization.py:make_exposure` + `persist_exposure`, `compute_fatigue`  
   - Pressure/risk: `commerce/conversation_operations.py:compute_pressure`, `derive_risk`, `build_operation_decision` + `derive_lifecycle`  
   - Objective + production control: `commerce/objective.py:derive_commercial_objective`, `commerce/production_control.py:autonomous_allowed`, `is_rollout_active_for`, `is_commerce_paused`, `record_metric`, `record_audit`  
   - Operational intelligence: `commerce/operational_intelligence.py:operational_decision` → `commerce/operational_execution.py:execute_operational_recommendation`  
   - One-call generation (new path): `core/one_call_pipeline.py:36` `one_call_generation(..., authoritative_state, pipeline_result)` → `context_engine/authoritative_assembly.py` snapshot reused; `build_one_call_from_snapshot` + commerce hints + `validate_one_call_context` + `provider.generate(system_instruction=ONE_CALL_SYSTEM_PROMPT, user_content=json(messages), model=ollama_model, response_mime_type="application/json", max_output_tokens=400)` + `validate_one_call_response(raw, participants, contract)` (`core/one_call.py:189`) + deterministic quality `validate_draft_quality`. Legacy path: `workers/llm_worker.py:84` `generate_draft` → scoring → commerce draft `workers/llm_worker.py:307` `_try_commerce_draft` (sealed 6D/6E boundary).

   Full `process_message` spans `workers/llm_worker.py:464` to beyond line 1500 — the above covers first 1100 lines; continue reading after line 1110 for post-commerce signal handling, context-engine observation (now after `conversation_state`), and final routing.

### 6.2 Auto-approval vs Operator Queue (routing invariant)

- After `one_call_pipeline` or legacy `score_draft` (`core/scoring.py:81` returns `(score: float, flags: list[str])`, hard-flag cap `0.1`):  
  - If `_skip_qwen_due_to_pause` (commerce/reengagement paused) → route to operator queue  
  - If `score ≥ auto_approve_threshold (0.80)` and `no hard flags` → **must call `enqueue_send(..., dedup_id, generation_id, creator_id)` and await success before emitting `ai.generation_completed`** (AGENTS.md invariant).  
  - Else → `add_to_operator_queue(user_id, draft_content, confidence_score, flags, creator_id)` (`db/postgres.py:662`) + `suggestion.created` + `operator_queue.updated` events.  
- `notify_operators()` (`workers/llm_worker.py:424`) today only logs — Telegram operator notifications are via separate operator bot (not in `notify_operators`).

### 6.3 Send Side (Telethon)

`chatbotv2/main.py:77` `_handle_send_entry` → `chatbotv2/main.py:89` `_process_send_entry_inner`:

```
dedup_id + generation_id + creator_id from Redis fields
→ is_send_duplicate(dedup_id, creator_id) ? skip (also publish duplicate_send_suppressed metric)
→ entity resolution: data["entity"] → int(entity) or raw; get_send_rate_limit_wait(peer_key) → sleep if needed; check_send_rate_limit(peer_key) → if not allowed: ack + enqueue_send (re-queue)
→ blacklist: is_blacklisted(entity) ? mark_send_dedup + move_send_to_dlq("entity_blacklisted") ; also on get_input_entity ValueError/TypeError → blacklist_entity(entity) + mark_send_dedup + DLQ("entity_not_found"); FloodWaitError → sleep + re-queue; RPCError → blacklist + DLQ
→ media branch: media_type in {"photo","video","document"} && media_path_raw ?
     • if dropfans_vault_item_id present: SELECT vault_media_deliveries WHERE creator_id/user_id/dropfans_vault_item_id → if missing → DLQ("dropfans_missing_reservation"); if status != pending → ack+skip; else stash (creator, vid) for finalize
     • elif fangate_media_id: reserve_delivery(creator_id, user_id, fangate_media_id) → if None → skip (already reserved); on exception → DLQ("delivery_reservation_failed")
     • RED-1 guard: if sales_url/buyUrl contains "/buy/" or equals media_path and is checkout URL → release_delivery + DLQ("checkout_url_as_media_blocked")
     • _validate_media_path(media_path) → if None → release + DLQ("invalid_media_path"); else send_file(input_entity, validated_path, caption=content, force_document)
→ else: client.send_message(input_entity, content)
→ mark_send_dedup(dedup_id, creator_id) + ack_send(msg_id)
→ if save_to_db=="true": save_outbound_after_send(user_id=entity_int, content, draft_content, was_edited, was_auto_approved, confidence_score, operator_id, telegram_message_id, media_type, media_path, fangate_media_id, creator_id)
     → if delivery_reservation_id: finalize_delivery(reservation_id, telegram_message_id)
     → if dropfans_pending_info: finalize_dropfans_delivery(creator, user_id, vid)
→ publish message.sent {content, telegram_message_id, was_auto_approved, confidence_score, media_type, fangate_media_id} with user_id/dialog_id/generation_id/creator_id scope=user
→ if fangate_media_id && is_media_send: also publish vault.media_sent

on UserIsBlockedError / Exception: release pending + move_send_to_dlq + publish message.send_failed
```

Reclaim loop `chatbotv2/main.py:537` every iteration before XREADGROUP: `requeue_stalled_send_messages("bot_main", idle_ms=redis_pending_idle_ms=60000)` → for each claimed entry call `_handle_send_entry`. Also `release_stale_reservations(max_age_minutes=5)` recovers leaked `pending` vault rows.

> **Dead code warning:** `chatbotv2/main.py:562-902` contains a large unreachable block (after `continue` at line 561). The live path is `_process_send_entry_inner`; the block below it is never executed. Do NOT edit it thinking it is active.

### 6.4 Commerce & Vault

- **Product authority:** `fangate_products` mirror table (synced from DropFans/Fangate API). `commerce/product_catalog.py:54` `get_product(creator_id, product_id)` — checks `is_accessible`, joins `creator_integrations.currency_code`. `list_active_products` orders `title ASC, id ASC`. `get_menu_context` injects into LLM context — no LLM invention.  
- **Selection:** `commerce/product_selection.py:resolve_commerce_product_with_history(creator_id, user_id, current_topic, open_threads)` — excludes already-purchased, relevance-aware ranking.  
- **Free photo:** `commerce/free_photo.py`, `free_photo_routing.py`, `free_photo_delivery.py`; ledger is `vault_media_deliveries` with `dropfans_vault_item_id TEXT` vs `fangate_media_id`. Tests `tests/test_phase97_free_photo_ledger.py` etc.  
- **Vault concurrency:** `db/vault.py:46` reserve is `INSERT ... ON CONFLICT DO NOTHING RETURNING id` — atomic, eliminates TOCTOU. Tests `tests/test_vault_concurrency.py`.

### 6.5 Scheduler Loop

`workers/scheduler_worker.py:172` `_scheduler_loop`:

```
while not is_shutting_down():
  is_global_paused() ? skip due processing : { recover_stale(worker_id) → recover_stale_messages stale_seconds=300 max_attempts=5 ; process_due_messages(worker_id) → claim_due_messages(batch=20) → for each: _fetch_message → cancelled? skip → _check_user_eligible(is_blocked/do_not_auto_reply) → enqueue_send(payload, dedup=schedule:{dedup_key}:{id}) → mark_scheduled_enqueued → aftercare completed if reason=="post_purchase_followup"; on error mark_scheduled_failed }
  reconcile_purchases() → commerce/reconciliation.py:reconcile_all()
  orchestrate_production_controls()
  operational_intelligence per active creator (≤5) → execute_operational_recommendation
  re-engagement: for each active creator → list_offers_for_creator(state=pending, limit=50) → if age≥48h and not paused and governed allowed (pressure/fatigue/7d count via query_metrics) → schedule_reengagement_if_eligible
  sleep 10s
```

---

## 7. Database, Redis & Migrations

### 7.1 PostgreSQL

**DSN:** `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres` (`.env.example:24`)  
**Pool:** `db/postgres.py:28` `asyncpg.create_pool(min_size=1, max_size=5, command_timeout=30, max_inactive_connection_lifetime=60)`  
**Verify at startup:** `db/postgres.py:39` `verify_schema()` checks 13 tables; `run_all.py:91` `_check_migrations()` auto-applies pending migrations on boot (fails hard with guidance if still pending).

**Baseline schema** `db/schema.sql:1` (extensions `uuid-ossp`, `pg_trgm`; **vector removed**, embeddings are JSONB):

| Table | PK/FK | Key columns |
|-------|-------|-------------|
| `users` | `id BIGINT PK` | `username, first_name, funnel_stage, is_blocked, persona_id FK personas(id), notes, do_not_auto_reply, persona_id` |
| `messages` | `id BIGSERIAL PK` | `user_id FK users, creator_id, direction (inbound/outbound), content, draft_content, was_edited, was_auto_approved, confidence_score, operator_id, telegram_message_id, sent_at` |
| `conversation_summaries` | `id BIGSERIAL PK` | `user_id FK, creator_id, summary, message_count_at_summary` |
| `user_profiles` | `user_id PK FK` | `facts JSONB, embedding JSONB` (commercial prefs in `facts->commercial_preferences_by_creator->{creator_id}`) |
| `message_embeddings` | `message_id PK FK` | `user_id, embedding JSONB` |
| `personas` | `id SERIAL PK` | `name, instructions, is_default, creator_id, version, updated_at, metadata JSONB` (seed: sales/friendly/support) |
| `operator_queue` | `id BIGSERIAL PK` | `user_id FK, creator_id, draft_content, confidence_score, flags JSONB, status (pending/approved/rejected/failed), assigned_to, created_at, resolved_at` |
| `operators` | `id BIGSERIAL PK` | `telegram_id UNIQUE, username, is_active` |
| `sessions` | `token TEXT PK` | `username, expires_at` |
| `dlq_messages` | `id BIGSERIAL PK` | `original_stream_id, message_data JSONB, failure_reason, attempts` |
| `conversation_attention` | `user_id PK FK` | `status, assigned_operator_id FK operators(id), reviewed_at/by` |
| `conversation_notes` | `id BIGSERIAL PK` | `user_id FK, content, created_by` |
| `conversation_tags` + `conversation_tag_assignments` | `id / (user_id, tag_id)` | `name UNIQUE LOWER(name), assigned_by` |
| `scheduled_messages` | (via migration) | `user_id, creator_id, content, media_type/path, dedup_key, status, reason, scheduled_at, claimed_at` |
| `vault_media_deliveries` | (via migration) | `creator_id, user_id, fangate_media_id / dropfans_vault_item_id, product_id, status (pending/sent), telegram_message_id, sent_at` |
| `fangate_products` | `id BIGINT PK + creator_id` | `product_type, title, preview_url, price_minor, sales_url, raw JSONB, synced_at ...` |
| `creator_integrations` | `creator_id PK` | `encrypted_api_key, api_key_name, fangate_account_id, currency_code, status (active/error), webhook_id, encrypted_webhook_secret` |
| + `fangate_transactions`, `fangate_wallet_entries`, `fangate_webhook_events`, `tool_audit_log`, `generation_telemetry`, etc. (see migrations) |

**Key functions (paths for goto):**
- `db/postgres.py:96` `upsert_user`, `db/postgres.py:115` `get_user`, `db/postgres.py:148` `is_user_auto_reply_excluded`
- `db/postgres.py:155` `get_user_persona(user_id, creator_id)` — creator-scoped (if `creator_id` given, tries `personas WHERE creator_id=$1 ORDER BY is_default DESC`; falls back to global `creator_id IS NULL`)
- `db/postgres.py:213` `get_all_personas`, `db/postgres.py:230` `create_persona`, `db/postgres.py:257` `update_persona` (version bump), `db/postgres.py:301` `delete_persona`
- `db/postgres.py:352` `save_inbound_message` (idempotent on `(user_id, telegram_message_id)` WHERE inbound), `db/postgres.py:382` `save_outbound_message`, `db/postgres.py:418` `save_outbound_after_send`
- `db/postgres.py:464` `get_recent_messages(user_id, limit=20, creator_id)` — if `creator_id` then `(creator_id=$2 OR creator_id IS NULL)`
- `db/postgres.py:520` `get_commercial_preferences` / `542` `update_commercial_preferences` (creator-scoped inside `facts`)
- `db/postgres.py:662` `add_to_operator_queue`, `db/postgres.py:702` `get_pending_queue_items`, `db/postgres.py:733` `resolve_queue_item`

Indexes include GIN `gin_trgm_ops` for `messages.content`, `users` name search, `conversation_notes` (`db/schema.sql:43`).

### 7.2 Redis

**URL:** `REDIS_URL=redis://127.0.0.1:6379` (`.env.example:25`)  
**Client:** `db/redis.py:21` `redis.asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True, protocol=2)` singleton

| Concept | Key / Stream | Functions |
|---------|--------------|-----------|
| **Streams** | `inbound_messages`, `send_messages`, `dead_letter_queue` ( constants `db/redis.py:14`) | `enqueue_inbound`, `enqueue_send`, `read_inbound`, `read_send_messages`, `ack_inbound`, `ack_send`, `move_to_dlq`, `move_send_to_dlq` |
| **Consumer groups** | `llm_workers` on inbound, `send_workers` on send (`db/redis.py:40`) | `ensure_consumer_group(mkstream=True)` called by all processes |
| **XAUTOCLAIM** | idle threshold `REDIS_PENDING_IDLE_MS=60000` (`core/config.py:45`) | `requeue_stalled_messages`, `requeue_stalled_send_messages` (`db/redis.py:225`, `db/redis.py:158`) |
| **Locks** | `lock:creator:{cid}:user:{uid}` else `lock:user:{uid}` (`db/redis.py:283`) | `acquire_user_lock(ttl=60, creator_id)`, `release_user_lock`, `clear_all_user_locks` |
| **Debounce** | `debounce:creator:{cid}:user:{uid}:lock` (NX EX 3s) + `:messages` list (`db/redis.py:315`) | `debounce_enqueue(window_seconds=3)`, `get_debounced_messages` (legacy fallback) |
| **Rate limit inbound** | `ratelimit:{user_id}` INCR EX 60 (`db/redis.py:356`) | `check_rate_limit(max_per_minute=20)` |
| **Rate limit outbound** | `send_ratelimit:{peer_id}` ZSET Lua token bucket 1/sec burst 5 (`db/redis.py:499`) | `check_send_rate_limit`, `get_send_rate_limit_wait` (Lua `db/redis.py:502`) |
| **Dedup** | `send_dedup:{creator}:{dedup_id}` or `send_dedup:{dedup_id}` TTL 3600 (`db/redis.py:81`) | `mark_send_dedup`, `is_send_duplicate` |
| **Persona cache** | `persona:{creator}:{user_id}`, `persona:creator:{cid}`, `persona:creator:{cid}:default`, `persona:default` TTL 600 | `cache_user_persona`, `get_cached_user_persona`, `cache_creator_persona`, `invalidate_persona_cache` |
| **Context cache** | `context:{user_id}` TTL 300 | `cache_user_context`, `get_cached_context` |
| **Settings** | `setting:auto_reply` ("1"/"0", default true) | `is_auto_reply_enabled`, `set_auto_reply_enabled` |
| **DLQ helpers** | `dead_letter_queue` stream | `count_dlq_entries`, `list_dlq_entries`, `get_dlq_entry`, `delete_dlq_entry`, `cleanup_expired_dlq_entries`, `replay_dlq_entry` |
| **Observability** | gauges | `get_inbound_pending_count`, `get_send_pending_count`, etc. |

**Dedup generation:** `md5("{user_id}:{content}:{telegram_message_id}")` for inbound (`chatbotv2/handlers.py:63`, `db/redis.py:192`), `queue_item:{queue_id}` or `manual:{user_id}:{hash(content)}` for operator sends (`workers/send_worker.py:28`), `scheduled:{dedup_key}:{id}` for scheduler (`workers/scheduler_worker.py:59`).

### 7.3 Migrations

Engine `db/migrate.py:1` — asyncpg, `schema_migrations(version TEXT PK, name, applied_at, execution_ms)`.

```bash
python -m db.migrate status    # shows current_version, applied, pending
python -m db.migrate upgrade    # applies pending in order
python -m db.migrate baseline   # mark existing DB as baseline 00000000000000
```

Files `db/migrations/*.sql` (25 total, discovered lexicographically — version prefix `YYYYMMDDHHMMSS`):

```
00000000000000_baseline.sql
20260819000000_fangate_commerce.sql        # creators, creator_integrations, fangate_products
20260819010000_fangate_ppv_commerce.sql
20260819100000_ppv_intelligence.sql
20260822000000_currency_propagation.sql
20260822010000_media_columns.sql            # media_type/path, fangate_media_id on messages
20260822020000_scheduled_messages.sql       # scheduled_messages
20260822030000_scheduled_messages_creator.sql
20260822040000_tool_audit_log.sql
20260823010000_vault_media.sql              # vault_media_deliveries (pending/sent, dropfans CUID)
20260823020000_vault_pending_index.sql
20260823030000_fan_segments.sql
20260824010000_inbound_message_idempotency.sql
20260825000000_dropfans_provider.sql        # DropFans provider switch
20260826000000_automation_operations.sql
20260826010000_aftercare_persistence.sql
20260828040000_generation_telemetry.sql     # generation_telemetry
20260831000000_generation_id_text.sql
20260831000001_persona_structured.sql       # personas.metadata, version, structured persona
20260831000002_creator_isolation.sql
20260901000000_phase87_observability.sql
20260910000000_phase89_persona.sql
20260911_phase90_roleplay_telemetry.sql
20260912000000_free_photo_ledger.sql
20260913000000_phase103_telemetry.sql
```

`run_all.py:91` auto-applies on startup; if fails, re-check prints `python -m db.migrate upgrade` hint. `reset_db.py` is **not** a migration — it kills idle PG connections (`SELECT pg_terminate_backend`).

---

## 8. Realtime Phase-1 Contract (IMMUTABLE)

> **Source of truth:** `AGENTS.md:11-66`. Do NOT modify without explicit approval. The dashboard accidentally had `asyncio.sleep` import warnings ignored (`pyproject.toml:54` per-file-ignores) but **event semantics are frozen**.

### 8.1 Events (8 total)

| Event | Scope | Producer | Consumer |
|-------|-------|----------|----------|
| `message.created` | `user` | `chatbotv2/handlers.py:82` | dashboard, WS clients |
| `message.sent` | `user` | `chatbotv2/main.py:414` | dashboard, vault |
| `message.send_failed` | `user` | `chatbotv2/main.py:477` | dashboard |
| `ai.generation_started` | `user` | `workers/llm_worker.py:676` | dashboard |
| `ai.generation_completed` | `user` | `workers/llm_worker.py:700` (via `publish_events_batch`) | dashboard |
| `ai.generation_failed` | `user` | `workers/llm_worker.py` (on exception path) | dashboard |
| `suggestion.created` | `user` | `workers/llm_worker.py:709` | dashboard |
| `operator_queue.updated` | `user` | `workers/send_worker.py:101`, `chatbotv2/dashboard/routes/queue.py` | dashboard |

Additional internal: `vault.media_sent` (`chatbotv2/main.py:424`) — not part of Phase-1 contract.

### 8.2 Invariants

- **Lifecycle:** `ai.generation_started` → generation+scoring+routing → if auto-approved: `enqueue_send()` must succeed → `ai.generation_completed`. On any failure (generation/scoring/routing/enqueue): `ai.generation_failed` (`AGENTS.md:22`).  
- **Critical rule:** `ai.generation_completed` **MUST NOT** be emitted before `enqueue_send()` succeeds for auto-approved (`AGENTS.md:24`). Consumer may trust `ai.generation_completed = generation completed AND send-queue handoff succeeded`.  
- **generation_id:** Every lifecycle event for one turn carries **same** `generation_id` (MD5 at intake, propagated through `db/redis.py:192` `enqueue_inbound`, `workers/llm_worker.py:470` re-derive, `db/redis.py:65` `enqueue_send` propagation, and `core/event_bus.py:71` publish). Never generate a new one mid-lifecycle (`AGENTS.md:28`).  
- **event_id:** Every publish gets `uuid4()` (`core/event_bus.py:46` + `core/event_bus.py:112`), frontend dedup relies on it.  
- **Failure isolation:** `publish_event` is **best-effort** (`core/event_bus.py:63` `try/except` logs but never raises). Event failure **must not** break AI generation/scoring/routing/Telegram/Streams/DB, and vice-versa (`AGENTS.md:34`).  
- **Transport:** Workers publish **only via** `core/event_bus.py` (`publish_event`, `publish_events_batch`). Workers **MUST NOT** import `ws_manager` / `event_subscriber` / FastAPI WS (`AGENTS.md:42`).  
- **Frontend:** WS is **acceleration only**, polling remains (`AGENTS.md:46`). When WS connected → immediate updates, polling may pause; when disconnected → polling resumes. Never remove polling.  
- **Scope:** `user`/`dialog`-scoped events only to appropriate clients; `global` may broadcast. Do not broaden without approval (`AGENTS.md:54`).  
- **Backward compat:** Phase-2+ must preserve schema/semantics unless task explicitly requires versioned migration with 6-step proposal (`AGENTS.md:58`).

### 8.3 Event Bus API

- `core/event_bus.py:24` `publish_event(event_type, data, *, user_id, dialog_id, generation_id, scope="global"|"user", creator_id)` — respects `enable_websocket` flag, returns `event_id` or `None`, `CHANNEL="chatbot:events"` (`core/event_bus.py:21`), uses `orjson` if present.  
- `core/event_bus.py:94` `publish_events_batch(events: Sequence[dict])` — single pipeline `publish`, each dict needs `event_type`/`event` + `data` + optional routing keys.

### 8.4 Change Classifications

| Class | Examples |
|-------|----------|
| **SAFE** (no review) | Bug fix preserving event name/schema/semantics, perf, frontend rendering, extra consumers/tests/logging, reconnect/polling improvements |
| **REQUIRES REVIEW** | Changing payload fields, timing, scope, generation_id, ordering, Redis Pub/Sub, WS auth, removing polling, producer/consumer responsibilities |
| **FORBIDDEN without explicit approval** | Renaming/deleting events, changing meaning, making business logic depend on WS, making workers depend on WS, replacing Streams with Pub/Sub for tasks, treating WS as durable, silently changing schema |

---

## 9. LLM & Context Engine

### 9.1 Provider Selection

| Provider | When | Config |
|----------|------|--------|
| **Ollama `qwen2.5:3b`** (authoritative, 100% content success at `think=false`) | `llm_provider` contains `ollama` (default `core/config.py:84` `"ollama"`), or when `llm_provider` empty (`workers/llm_worker.py:109` fallback) | `OLLAMA_BASE_URL=https://ollama.brestalogistics.co.ke` (`core/config.py:89`), `OLLAMA_MODEL=qwen2.5:3b` (`core/config.py:91`), `OLLAMA_TIMEOUT=120` (`core/config.py:93`), `OLLAMA_USERNAME/API_KEY` basic auth (`core/llm_provider_ollama.py:103`) |
| **Gemini** | `llm_provider=gemini` or fallback when `ollama` fails and `gemini_fallback_enabled=true` (`core/config.py:45`) | `GOOGLE_API_KEY` or `GEMINI_API_KEYS` comma-separated (`core/config.py:199`), `GEMINI_RPM_LIMIT=10` + safety margin 0.8 (`core/config.py:40`), `gemini_daily_quota=20` (`core/config.py:44`) |
| **Shadow Qwen (historical)** | `qwen_shadow_enabled=true` — never authoritative, never sent | `QWEN_SHADOW_SAMPLE_RATE`, `QWEN_SHADOW_TIMEOUT=180` (`core/config.py:100`) |

Factory `core/llm_provider.py:get_llm_provider()` returns singleton. Health check `OllamaProvider.health_check()` (`core/llm_provider_ollama.py:406`) tests `/api/tags` + model + test generation.

**Ollama payload** `core/llm_provider_ollama.py:149` ` _build_payload(messages, num_predict, temperature)`:
- `think=false` default: `temperature=0.7, top_p=0.8, top_k=20, min_p=0, presence_penalty=1.5, num_ctx=8192`
- `think=true`: `temperature=0.6, top_p=0.95` (Qwen official)
- `format=json` when `response_mime_type="application/json"`
- Endpoint `POST /api/chat` (`core/llm_provider_ollama.py:217`), retry at `num_predict 500` if `think=true` and empty content (`core/llm_provider_ollama.py:262`), raise `LLMProviderError` on empty (`core/llm_provider_ollama.py:274`).

### 9.2 One-Call Contract

**Schema** `core/one_call.py:39` `OneCallReply(BaseModel)`:
```json
{
  "reply": "string 1..2000 chars",
  "commerce_signals": { "purchase_intent":0..1, "content_interest":0..1, "relationship_engagement":0..1, "price_interest":0..1, "explicit_purchase_request":bool, "explicit_content_request":bool, "requested_price":null|number, "declined_recent_offer":bool, "asks_for_free_content":bool, "negative_sentiment":0..1, "confidence":0..1, "evidence":["≤5 fragments"], "model_uncertainty":0..1, "primary_intent":"casual_chat|...|other (18 categories)", "intent_tags":["≤5"], "negative_intent_tags":["hesitation"...], "fan_asks_question":bool },
  "confidence":0..1,
  "needs_handoff":bool
}
```
All fields **advisory** — none authorize price/payment/access (that is `commerce/execution.py`).

**System prompt** `core/one_call.py:767` `ONE_CALL_SYSTEM_PROMPT` includes explicit rule 9: NEVER prefix reply with speaker label (`Sunny:` etc.).

**Validation** `core/one_call.py:189` `validate_one_call_response(raw_json, is_authorized_commerce, authorized_price_minor, participants, contract)`:
1. JSON parse → fail → `is_valid=False, needs_handoff=True, confidence=0`
2. Pydantic `OneCallReply` → fail → same
3. Speaker-prefix strip `strip_leading_speaker_prefix(reply, character_name, player_name)` (`core/one_call.py:98`, up to 3 iterations, handles `**Sunny:**` etc., idempotent) — also hardens grounding flags
4. Safety flags ` _compute_safety_flags` (`core/one_call.py:341`) — hard flags `price_mention, personal_info_request, distress_signal, legal_mention, photo_promise` (exact list `core/scoring.py:12` + persona violations) — if `is_authorized_commerce` then `price_mention` suppressed when numeric price matches `authorized_price_minor`
5. Quality heuristics ` _compute_quality_heuristics` (`core/one_call.py:393`) — length 10..100 words =10, formality (dear/sincerely→penalty), generic patterns, repetition ratio; composite /40 capped 0.5 if <5 words
6. Conversational grounding ` _compute_conversational_flags(reply, participants, contract)` (`core/one_call.py:480`) — detects `speaker_inversion`, `character_as_player_inversion`, `player_as_character_inversion`, `unauthorized_player_speech`/`player_agency_violation`, `unanswered_question`, `topic_pivot`, `out_of_character` ("as an AI"), `speaker_prefix_leak` — caps quality 0.4-0.6, never auto-regenerates
7. Confidence/handoff fusion: if safety flags → `needs_handoff=True, confidence=min(conf,0.3)`; if quality<0.3 → `needs_handoff=True`.

**Result** `OneCallResult` (`core/one_call.py:72`) carries `reply, signals, confidence, needs_handoff, quality_score, quality_flags, safety_flags, is_valid, provider_name/model_name, input/output/total_tokens, latency_ms, generation_kind, call_index`.

### 9.3 Pipeline

`core/one_call_pipeline.py:36` `one_call_generation(user_id, creator_id, user_message, persona, profile, user, commerce_text, summary, conversation_state, recent_messages, authoritative_state, pipeline_result, generation_id)`:

- **New path** (when snapshot supplied): `build_one_call_from_snapshot(snapshot, authoritative_state, pipeline_result)` → adds commerce hints if missing via `commerce_prompt.build_commerce_signal_hints` → `validate_one_call_context`; else legacy `build_one_call_context` (`core/context_compact.py`)  
- Generate: `provider.generate(system_instruction=ONE_CALL_SYSTEM_PROMPT + COMMERCE_SIGNAL_INSTRUCTIONS, user_content=json.dumps(messages), model=ollama_model, response_mime_type="application/json", max_output_tokens=400)` (`core/one_call_pipeline.py:171`) — timing captured for telemetry  
- Validate: `validate_one_call_response(raw, participants, contract)` where participants/contract come from `authoritative_state.participants / conversation_contract`  
- Attach provider telemetry, merge conversational flags before `validate_draft_quality` (`core/scoring_deterministic.py:validate_draft_quality`), cap quality if roleplay flags present.

**Context compaction** `core/context_compact.py:build_one_call_context` enforces per-section token budgets; `TOTAL_CONTEXT_BUDGET=??` (`context_engine/budget.py`, `context_engine/models.py`) plus `HEADER_RESERVE_TOKENS`.

### 9.4 Authoritative Context Assembly (Phase 2)

`context_engine/authoritative_assembly.py:52` `assemble_authoritative_context(*, creator_id, user_id, current_message, generation_id, persona_override, structured_persona_snapshot)` — **the only place** per-turn DB fetches happen in `new` path (see `context_engine/authoritative_assembly.py:1` header). Guarantees:

- Parallel I/O: `get_user`, `get_user_profile`, `get_recent_messages(limit=20, creator_id)`, `get_latest_summary_with_age`, `get_structured_persona_async` via `asyncio.gather(return_exceptions=True)`  
- Fan-knowledge interest isolation: if `creator_id` then `get_fan_knowledge(profile)` → `profile["interests"]` filtered to `CURRENT`, else strip `interests/preferences`  
- Persona text: creator-scoped cache/DB chain (`get_cached_user_persona` → `get_user_persona` → `cache_user_persona` → fallback to default persona)  
- Persona name: regex `You are ([A-Za-z ]+)` + identity `Sunny Skye` detection + structured `identity.name`  
- Trim `recent` via `memory/context.py:trim_to_token_budget(conversation=1500)` + drop oldest beyond 3 assistant turns  
- **Derive `conversation_state` exactly once** via `core/conversation_state.py:derive_conversation_state(history = recent_trimmed + [{direction:inbound, content:current_message}], user=user)` with counter `get_derive_call_count()` for tests  
- Commerce context via `memory/context_assembler.py:build_llm_context(creator_id, user_id, user_data=user, recent_messages=recent, summary=summary)` → `render_context`  
- Derive `participants` + `conversation_contract` via `core/conversation_contract.py:derive_participants/derive_contract` frozen into `AuthoritativeState.participants / conversation_contract` (fail-open)  

Type `context_engine/models.py:AuthoritativeState` (frozen) holds `creator_id, user_id, generation_id, current_message, timestamp, user, profile, recent_messages, summary, summary_age_days, persona, structured_persona, persona_name, persona_id, persona_version, conversation_state, conversation_state_dict, commerce_context_text, llm_context, fan_knowledge_snapshot, long_term_memories_snapshot, participants, conversation_contract, metadata{acquisition_ms, recent_raw_count, recent_trimmed_count}`.

### 9.5 Scoring

- **Legacy LLM scorer** `core/scoring.py:81` `score_draft(draft, user_message, context, is_authorized_commerce, authorized_price_minor, authorized_url)` — 5-step: keyword hard flags (see `FLAG_KEYWORDS` `core/scoring.py:25`), fallback to `generate_with_fallback` or `get_llm_provider().generate(system_instruction=SCORING_SYSTEM_PROMPT, user_content="User said: ...\nDraft: ...")`, JSON `contextually_aware/natural_tone/appropriate_length/not_repetitive` 0-10, composite/40, hard-flag cap 0.1, on LLM failure `scoring_failed=True → composite 0.0` (P0-1 fix `docs/FINAL_WHOLE_SYSTEM_TECHNICAL_CLOSURE_REPORT.md:39`).  
- **Deterministic scorer** `core/scoring_deterministic.py:validate_draft_quality(reply, user_message)` — heuristic replacement (same 4 dimensions), used by new pipeline when `result.is_valid`.

---

## 10. Commerce Subsystem

### 10.1 Boundaries

- **Commerce is deterministic, advisory LLM signals are never authoritative.** `commerce/signals.py:CommerceSignals` is 18-field advisory; `commerce/decision.py:decide_commerce_action`, `commerce/execution.py:execute_commerce` own PPV/offer creation.  
- **Product/price/currency are DB, not LLM:** `commerce/product_catalog.py:88` `get_product` checks `is_accessible`, reads `creator_integrations.currency_code`.  
- **Autonomy kill switch** `core/config.py:111` `autonomy_enabled` (bool, default true) — when `false`, `workers/llm_worker.py:344` `_try_commerce_draft` short-circuits, commerce attempts skipped. Independent of Redis `setting:auto_reply`.  
- **Provider scope:** DropFans is **sole active provider** (`core/config.py:56` `dropfans_api_base_url=https://www.dropfans.io`; Fangate at `fangate.info/api` is legacy/standby). Migration report `docs/DROPFANS_MIGRATION_FORENSIC_AUDIT.md`.

### 10.2 Single Creator Resolution

`commerce/single_creator.py:resolve_single_application_creator()` returns `SingleCreatorStatus.READY` with `creator_id` when exactly one `creator_integrations WHERE status='active'` exists; else `NOT_READY` (multi-creator not operational). Used everywhere for isolation: `chatbotv2/handlers.py:68`, `workers/llm_worker.py:496`, `workers/llm_worker.py:352` for product selection, `chatbotv2/main.py:95` for dedup.

### 10.3 Commerce Attempt per Message

`workers/llm_worker.py:307` `_try_commerce_draft(user_id, context, persona, signals, conversation_state)` — at most once per inbound, never raises, never enqueues/sends itself, logs `status/reason` (`commerce/selection.py:CommerceSelectionStatus/Reason`). Uses `commerce/state.py:CommerceStateRequest(user_id, creator_id, product_id, messages[-3:], persona)` → `commerce/integration.py:resolve_and_run_commerce` (sealed 6D) → `commerce/selection.py:select_commerce_response` (6E). Product resolved via `commerce/product_selection.py:resolve_commerce_product_with_history` (excludes purchased, relevance-aware).

### 10.4 Vault Delivery

`db/vault.py:17` `record_delivery` (idempotent UNIQUE), `db/vault.py:46` `reserve_delivery` → `INSERT ... ON CONFLICT DO NOTHING RETURNING id` (atomic), `db/vault.py:80` `finalize_delivery(delivery_id, telegram_message_id)` (`status pending→sent`), `db/vault.py:104` `release_delivery`, DropFans variants `finalize_dropfans_delivery`/`release_dropfans_delivery` (by `dropfans_vault_item_id TEXT`), `db/vault.py:171` `release_stale_reservations(5min, batch 50)` — called by `chatbotv2/main.py:551`. Media send validation `chatbotv2/main.py:45` `_validate_media_path` (HTTPS URLs allowed, local files checked existence+100MB cap), allowed types `photo/video/document` (`chatbotv2/main.py:39`).

### 10.5 Free Photo Ledger

`commerce/free_photo.py` + `vault_media_deliveries` ledger (`db/migrations/20260912000000_free_photo_ledger.sql`). Routing `commerce/free_photo_routing.py`, delivery `commerce/free_photo_delivery.py`, concurrency tests `tests/test_phase98_free_photo_concurrency.py`, ledger tests `tests/test_phase97_free_photo_ledger.py`.

### 10.6 Product Menu (Phase 102)

`commerce/product_catalog.py:130` `get_menu_context(creator_id, max_items=5)` — injected in `workers/llm_worker.py:659` (`menu_items` telemetry = count of `id:`). Used by `context_engine` rendering.

### 10.7 Additional Commerce

- **Warming/Readiness** `commerce/warming.py`, `commerce/readiness.py`, `commerce/relationship.py` — deterministic levels (scores, ceilings) emitted to telemetry (`workers/llm_worker.py:878`).  
- **Offer readiness** `commerce/offer_readiness.py`, **sales window** `commerce/sales_window.py`, **next best action** `commerce/next_best_action.py`, **persona_behavior** `commerce/persona_behavior.py`.  
- **Post-purchase** `commerce/post_purchase.py` (+ delivery), **reconciliation** `commerce/reconciliation.py` (webhook↔sales reconciliation via `reconcile_all`, called by scheduler every 10s).  
- **Re-engagement** `commerce/re_engagement.py:schedule_reengagement_if_eligible` — governed by pressure/fatigue, called by scheduler for 48h+ pending offers.  
- **Production control** `commerce/production_control.py` — pause gates (`is_global_paused`, `is_commerce_paused`, `is_reengagement_paused`, `is_rollout_active_for`), `record_metric`, `record_audit`, `orchestrate_production_controls`, persisted state via `load_persisted_state`.  
- **Adaptive optimization** `commerce/adaptive_optimization.py` — fatigue, exposures.  
- **Operational intelligence** `commerce/operational_intelligence.py` + `commerce/operational_execution.py` — health evaluation, recommendations.

---

## 11. Workers, Bot & Dashboard

### 11.1 LLM Worker

**Entry:** `python -m workers.llm_worker --worker-id worker_1` (`workers/llm_worker.py`)  
**Concurrency:** Multiple instances share consumer group `llm_workers` on `inbound_messages`; stalled idle>30s auto-claimed via `XAUTOCLAIM` (note: docs/CODEBASE_MAP §5 said inbound XAUTOCLAIM not called — now present via `db/redis.py:225` and invoked in `workers/llm_worker.py` recovery path).  
**Heartbeat:** `core/worker_heartbeat.py:write_heartbeat(worker_id, worker_type="llm", interval=10, ttl=30)`  
**Logging:** `core/logging_config.py:setup_logging(structured=false→human, true→JSON)`.

See `workers/llm_worker.py:464` `process_message` for full path (§6.1). Post-process `workers/llm_worker.py:440` `post_process(user_id)` does `get_recent_messages(limit=20)` + `get_user` for count + `extract_and_update_profile` + `maybe_summarize` (`memory/summarizer.py:maybe_summarize` every `SUMMARIZE_EVERY_N=20`).

### 11.2 Bot Main (`chatbotv2/main.py:921` `run()`)

- `init_pool()`, `verify_schema()`, `ensure_consumer_group()`, heartbeat `bot_main`, 3 connect retries (`client.get_client()`), `setup_handlers(client)` → `client.run_until_disconnected()`.  
- Send loop `chatbotv2/main.py:537` detailed in §6.3, with stale reclaim + vault recovery before each `read_send_messages("bot_main", count=10, block=2000)`.  
- Cleanup `close_client()`, `close_pool()`, `close_redis()` on shutdown signal (`core/shutdown.py`).

### 11.3 Send Worker

`workers/send_worker.py:129` `run_send_worker` — every 5s `flush_queue(max_items=50)` (`workers/send_worker.py:49`): `get_pending_queue_items` → for each `status==pending` → `is_blacklisted(user_id)`? → `resolve_queue_item(id, "failed")` and skip; else `process_approved_message(user_id, content, confidence_score, operator_id, queue_id)` → `enqueue_send({entity, content, draft_content, was_edited=False, was_auto_approved, confidence_score, operator_id, save_to_db=True}, dedup_id="queue_item:{qid}")` → `resolve_queue_item("approved")` → `publish_event("operator_queue.updated")`. Blacklist prevents cycling every 5s.

### 11.4 Scheduler Worker

`workers/scheduler_worker.py:314` `run_scheduler` — every `SCHEDULER_POLL_INTERVAL=10` (§6.5 diagram). Batch `20`, recovery `300s`/`5 attempts`. Also runs `reconcile_purchases`, `orchestrate_production_controls`, per-creator operational decisions.

### 11.5 Dashboard

**App** `chatbotv2/dashboard/app.py:66` `app = FastAPI(title="MTProto Chatbot Dashboard")` with `RequestIDMiddleware` (`core/middleware.py`) + `StaticFiles(directory="chatbotv2/dashboard/static")`.  
**Startup** `chatbotv2/dashboard/app.py:79` `startup()`: `init_pool`, `verify_schema`, `ensure_consumer_group`, `cleanup_expired_sessions`, `start_event_subscriber()` if `enable_websocket` (`core/config.py:38` default true).  
**Shutdown** cancels subscriber, closes pool/redis.

| Route group | File | Endpoints (excerpt) |
|-------------|------|---------------------|
| Health | `routes/health.py` | `GET /health`, streams/DLQ gauges |
| WS | `routes/ws.py` | `WebSocket /ws` (auth-guarded, scope-filtered) |
| Auth | `routes/auth.py` | `POST /login`, `POST /logout`, session cookie |
| Users | `routes/users.py` | `GET /api/users`, `POST /api/users/{id}/block`, `/do-not-auto-reply`, notes, persona assignment |
| Messages | `routes/messages.py` | `GET /api/dialogs/{id}/messages`, send/edit |
| Dialogs | `routes/dialogs.py` | dialogs list, `POST /api/dialogs/{id}/ai-reply` → `enqueue_inbound` |
| Queue | `routes/queue.py` | `GET /api/queue`, `POST /api/queue/{id}/resolve` (approve/edit/reject) |
| Personas | `routes/personas.py` | CRUD `/api/personas` (creator-scoped) |
| Vault | `routes/vault.py` | media vault, deliveries, analytics |
| Fangate / DropFans | `routes/fangate.py` + dropfans | `GET /api/fangate/*`, health, sync |
| Analytics | `routes/analytics.py` | `GET /api/analytics/*` (KPI, timeline) |
| Search | `routes/search.py` | message/user search (pg_trgm GIN) |
| Segments | `routes/segments.py` | fan segments CRUD |
| Tags/Notes/Attention | `routes/tags.py`, `notes.py`, `attention.py` | tags, notes, attention states |
| Export / Bulk / Followups | `routes/export.py`, `bulk_ops.py`, `followups.py` | CSV, bulk actions |
| Live / AI Intel / Lab | `routes/live.py`, `ai_intel.py`, `lab.py` | live polling fallback, AI intel, lab experiments |
| Pages | `routes/pages.py` | Jinja templates: overview, chats, queue, dashboard |
| Settings | `routes/settings.py` | `GET/POST /api/settings/auto-reply`, global toggles |
| DLQ | `routes/dlq.py` | DLQ list/replay/cleanup |
| Operators | `routes/operators.py` | operator list |

**Auth** `chatbotv2/dashboard/auth.py`: `secrets.token_urlsafe(32)`, 8h expiry in PG `sessions`, `require_auth` dependency on `/api/*` + `/dashboard/*`, any username accepted, password checked against `DASHBOARD_ADMIN_PASSWORD` (`core/config.py:37`), httponly same-site=lax cookie.

**Frontend:** Server-rendered Jinja2 (12 templates) + Tailwind CDN + Alpine.js; polling: `setInterval(loadStats,5000)` overview, `setInterval(loadSuggestions,10000)` chat; **WebSocket acceleration does not remove polling** (AGENTS invariant); iframe popup `chats.html:240` → `chat_embed.html`.

**Event bridge:** `chatbotv2/dashboard/event_subscriber.py:start_event_subscriber` subscribes to `chatbot:events` Pub/Sub, forwards to `ws_manager` for connected clients (scope-filtered). Workers never import WS.

---

## 12. Configuration / Env Vars

Source: `core/config.py:12` `Settings(BaseSettings)` (`env_file=".env"`, `extra="ignore"`, `lru_cache`). Defaults below; env overrides.

| Key | Default | Purpose | File |
|-----|---------|---------|------|
| `API_ID` / `API_HASH` / `PHONE_NUMBER` / `TELETHON_SESSION` | (required) | MTProto user account | `.env.example:2` |
| `OPENAI_API_KEY` | (non-empty, `gsk_...` for Groq) | Required by Settings but Groq-era; not used for generation when provider=ollama (still validated non-empty) | `core/config.py:16` |
| `EMBEDDING_API_KEY` | — | Embedding API key | `.env.example:9` |
| `USE_GROQ` | `true` | Legacy | `.env.example:11` |
| `GOOGLE_API_KEY` | — | Fallback single Gemini key | `.env.example:17` |
| `GEMINI_API_KEYS` | — | Comma-separated 1-4 keys (separate projects for quota) | `.env.example:15` |
| `GEMINI_RPM_LIMIT` / `GEMINI_RPM_SAFETY_MARGIN` | `10` / `0.8` | Per-process rate limit | `.env.example:20` |
| `POSTGRES_DSN` | `postgresql://postgres:postgres@127.0.0.1:5432/postgres` | PG DSN | `.env.example:24` |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis URL | `.env.example:25` |
| `DASHBOARD_ADMIN_PASSWORD` | `admin123` (override to `change_me_in_production`) | Dashboard login | `.env.example:28` |
| `AUTO_APPROVE_THRESHOLD` | `0.80` | Auto-send cutoff | `.env.example:31` |
| `MODEL_NAME` / `CHEAP_MODEL` | `meta-llama/...` (overridden to `gemini-flash-latest` in code default) | Models for generation/scoring | `.env.example:32` / `core/config.py:25` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embeddings | `.env.example:34` |
| `MAX_TOKENS` / `TEMPERATURE` / `PRESENCE_PENALTY` / `FREQUENCY_PENALTY` | `200` / `0.85` / `0.5` / `0.3` | Generation params | `.env.example:35` |
| `SUMMARIZE_EVERY_N` | `20` | Rolling summary interval | `.env.example:39` |
| `DEBOUNCE_WINDOW_SECONDS` | `3` | Debounce window | `.env.example:40` |
| `RATE_LIMIT_PER_MINUTE` | `20` | Inbound per-user | `.env.example:41` |
| `USER_LOCK_TTL` | `60` | Per-user lock TTL | `.env.example:42` |
| `REDIS_PENDING_IDLE_MS` | `60000` | XAUTOCLAIM threshold | `.env.example:47` / `core/config.py:46` |
| `DLQ_MAX_REPLAY_ATTEMPTS` / `DLQ_RETENTION_SECONDS` | `3` / `604800` (7d) | DLQ | `.env.example:50` |
| `STRUCTURED_LOGGING` | `false` | JSON logs when true | `.env.example:56` |
| `WORKER_HEARTBEAT_INTERVAL` / `WORKER_HEARTBEAT_TTL` | `10` / `30` | Heartbeats | `.env.example:60` |
| `FANGATE_API_BASE_URL` | `https://fangate.info/api` | Fangate API | `.env.example:66` |
| `FANGATE_API_TIMEOUT` | `15` | Fangate timeout | `.env.example:68` |
| `FANGATE_ENC_KEY` | — | Fernet master key for Fangate creds | `.env.example:75` |
| `VAULT_STALE_RESERVATION_MINUTES` | `5` | Vault reclaim | `.env.example:81` |
| `LLM_PROVIDER` | `ollama` | `ollama` or `gemini` (comma list) | `core/config.py:84` / `.env.example:86` |
| `OLLAMA_BASE_URL` | `https://ollama.brestalogistics.co.ke` | Ollama endpoint | `.env.example:91` |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Qwen model | `.env.example:92` |
| `OLLAMA_TIMEOUT` | `120.0` | Ollama timeout | `.env.example:93` |
| `OLLAMA_USERNAME` / `OLLAMA_API_KEY` | `ollama` / `` | Ollama basic auth | `core/config.py:92` |
| `AUTONOMY_ENABLED` | `true` | Kill switch for commerce autonomy | `core/config.py:111` / `.env.example:101` |
| `QWEN_SHADOW_ENABLED` / `QWEN_SHADOW_SAMPLE_RATE` / `QWEN_SHADOW_TIMEOUT` | `false` / `0.0` / `180` | Shadow qualification | `core/config.py:100` |
| `ai_runtime_mode` / `agent_max_*` | `legacy` | Phase-2 agent runtime (shadow/canary) | `core/config.py:118` |
| `ai_agent_canary_enabled` / `ai_agent_canary_sample_rate` | `false` / `0.0` | Canary rollout | `core/config.py:129` |
| `context_engine_observational` | `false` | Observational mode | `core/config.py:136` |
| `context_engine_enabled` / `context_engine_sample_rate` | `true` / `1.0` (100% canonical) | Context Engine production | `core/config.py:141` |
| `llm_path` | `new` | `new` (Context Engine+one-call) vs `legacy` (3-LLM) | `core/config.py:148` |
| `context_engine_canary_mode` / `context_engine_canary_sample_rate` | `disabled` / `0.0` | CE A/B canary (observe) | `core/config.py:155` |
| `DROP...` | (see `.env.example:57` ff) | DropFans API base/enc key/timeout, reconciliation 120s | `core/config.py:57` |
| + many more | — | See `core/config.py:12` fully | `core/config.py` |

**Generation:** `FANGATE_ENC_KEY` via `python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(48)).decode())"` (`.env.example:71`).

---

## 13. Local Setup & Running

### 13.1 Prerequisites

- Python 3.11, PostgreSQL 15, Redis 7, Docker (optional).
- Telethon API credentials from `https://my.telegram.org/apps`.
- Groq key (for historical `OPENAI_API_KEY` non-empty check) + Gemini key(s) + Ollama VPS creds.

### 13.2 Install

```bash
pip install -e ".[dev]"          # from pyproject.toml:27 (pytest, ruff)
cp .env.example .env             # edit with real secrets — never commit .env
```

### 13.3 Database

```bash
# Without Docker (PSQL running locally):
psql -U postgres -d postgres -f db/schema.sql          # AGENTS.md:20
# If schema modified, add missing columns manually:
psql -U postgres -d postgres -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS persona_id INTEGER REFERENCES personas(id);"
psql -U postgres -d postgres -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS do_not_auto_reply BOOLEAN DEFAULT FALSE;"

# Migrations (preferred going forward):
python -m db.migrate status
python -m db.migrate upgrade   # auto-applied by run_all.py on boot
```

**Docker:**
```bash
docker-compose up               # starts PG (pgvector image, but vector extension not used), Redis, app
```

### 13.4 Run

```bash
# All-in-one (clears locks, kills port 8080 stale, migrations, starts 5 procs):
python run_all.py               # dashboard at http://localhost:8080, Ctrl+C to stop all

# Individual:
python -m chatbotv2.main
python -m workers.llm_worker --worker-id worker_1   # run >=2 for throughput
python -m workers.llm_worker --worker-id worker_2
python -m workers.send_worker --worker-id sender_1
python -m workers.scheduler_worker --worker-id scheduler_1
python -m uvicorn chatbotv2.dashboard.app:app --host 0.0.0.0 --port 8080
```

`run_all.py:46` kills stale Python procs matching `chatbotv2.main|workers\.llm_worker|workers\.send_worker|workers\.scheduler_worker|uvicorn.*dashboard`, `run_all.py:68` removes `.session-journal/-wal/-shm`, `run_all.py:222` only dies if dashboard exits (workers transient).

### 13.5 Testing & Linting

```bash
pytest                          # all; see markers below
pytest -m unit                  # no infra
pytest -m integration           # needs PG + Redis
pytest -m live                  # needs Ollama VPS (https://ollama.brestalogistics.co.ke)
ruff format .                   # format
ruff check .                    # lint  (pyproject.toml:48 line-length 100)
```

`pyproject.toml:40` markers: `unit`, `integration`, `live`. Example `tests/test_phase75b_one_call.py`, `tests/test_phase8_single_pass.py` (single-pass), `tests/test_worker_commerce_integration.py`.

### 13.6 Quick Smoke

```bash
python verify_10_presmoke.py    # pre-smoke
python verify_ollama.py          # hits Ollama VPS (needs env)
python smoke_phase88.py         # phase 88 observability smoke
curl http://localhost:8080/health
```

---

## 14. Dashboard API Surface

Full router list `chatbotv2/dashboard/app.py:126` (26 routers). Auth is `require_auth` cookie on `/api/*`.

| Area | Method & Path | Purpose |
|------|---------------|---------|
| Auth | `POST /api/auth/login` | username (any) + password (`DASHBOARD_ADMIN_PASSWORD`) → cookie |
| Users | `GET /api/users?query=&funnel=&page=&page_size` | list + filter |
| Users | `GET /api/users/{id}` / `PATCH /api/users/{id}` | detail, block, do-not-auto-reply, notes |
| Messages | `GET /api/dialogs/{id}/messages?limit=&before=` | history |
| Messages | `POST /api/dialogs/{id}/send` `{content}` | manual send → `enqueue_send` |
| Dialogs | `POST /api/dialogs/{id}/ai-reply` | enqueue last inbound → `inbound_messages` |
| Queue | `GET /api/queue?creator_id=&status=pending` | pending suggestions |
| Queue | `POST /api/queue/{id}/resolve` `{action: approve|edit|reject, content?}` | resolve → `enqueue_send` + `operator_queue.updated` |
| Personas | `GET/POST /api/personas`, `PUT/DELETE /api/personas/{id}` | CRUD (creator-scoped), invalidates cache |
| Vault | `GET /api/vault/*` | media list, deliveries, stats, `POST /api/vault/send` |
| Fangate/DropFans | `GET /api/fangate/*`, `/api/dropfans/*` | products, wallet, transactions, webhook |
| Analytics | `GET /api/analytics/*`, `/api/conversations/*` | KPIs, timeline, `get_dashboard_analytics` SQL aggregation (`db/postgres.py:924`) |
| Search | `GET /api/search/messages?q=`, `/api/search/users` | pg_trgm GIN |
| Segments | `GET/POST /api/segments`, `GET /api/segments/{id}/users` | fan segments |
| Tags/Notes/Attention | `POST /api/tags`, `/api/notes`, `/api/attention` | conversation ops |
| Scheduler | `GET /api/scheduled`, `POST /api/scheduled`, `DELETE /api/scheduled/{id}` | scheduled messages (P2.1) |
| DLQ | `GET /api/dlq`, `POST /api/dlq/{entry_id}/replay`, `POST /api/dlq/cleanup` | `db/redis.py:575` |
| WS | `WebSocket /ws` | event fan-out (scope-filtered, auth required) |
| Live | `GET /api/live/*` | polling fallback when WS disconnected |
| Health | `GET /health` | PG, Redis, stream lengths, pending gauges |
| Export | `GET /api/export/*` | CSV (`csv_helpers.py`) |

Pydantic schemas `chatbotv2/dashboard/schemas.py`, validation `dependencies.py`, CSV helpers `csv_helpers.py`.

---

## 15. Testing Strategy

- **Suite size:** 170 files `tests/` (`glob **/*.py` → 170).  
- **Markers** `pyproject.toml:41`: `unit` (no infra), `integration` (real PG+Redis), `live` (Ollama VPS).  
- **Key suites:**
  - **Phase 75 one-call:** `test_phase75b_one_call.py`, `test_phase75c_context_compact.py`, `test_phase75e_scoring_deterministic.py`, `test_phase75f_pipeline.py`, `test_phase8_single_pass.py` (single generation), `test_phase77d_xautoclaim_recovery.py`, `test_phase81_retrieval_hardening.py`
  - **Commerce:** `test_commerce_integration.py`, `test_worker_commerce_integration.py`, `test_commerce_signals.py`, `test_product_selection.py`, `test_phase102_product_catalog.py`
  - **Vault:** `test_vault.py`, `test_vault_concurrency.py`, `test_vault_reservation_recovery.py`, `test_phase97_free_photo_ledger.py`
  - **Infra:** `test_realtime.py` (event contract), `test_redis_recovery.py`, `test_inbound_idempotency.py`, `test_production_reliability.py` (99 tests), `test_forensic_remediation` (28)
  - **Context Engine:** `test_phase1_context_engine_canonical.py`, `test_phase2_authoritative_context.py`, `test_context_engine.py`, `test_phase72_context_engine_integration.py`
  - **Realtime:** `test_realtime.py` verifies lifecycle + generation_id + event_id + failure isolation
- **Run guidance:** `pytest -q` for full; for credibility, run `pytest -m unit -q` before any PR; integration requires `python -m db.migrate upgrade` first.
- **Bench scripts:** `benchmark_local.py`, `tests/benchmarks/`, `phase67_forensic.py`, `evaluate_unified_intelligence.py` — not part of CI but useful for calibration.

---

## 16. Known Gaps, Risks & Deferred Work

Synthesized from `docs/FINAL_WHOLE_SYSTEM_TECHNICAL_CLOSURE_REPORT.md:16` (42 defects: 3 P0 fixed, 9/10 P1 fixed, 4/17 P2 fixed) + `docs/CODEBASE_MAP.md:18` (18 verified gaps). **All P0 runtime-blocking fixed; below are survivors.**

| ID | Severity | Description | Location | Mitigation / Next Step |
|----|----------|-------------|----------|------------------------|
| P1-4 deferred | P1 | `generate_draft_with_tools()` still calls Gemini SDK directly, bypasses `LLMProvider` abstraction | `workers/llm_worker.py:175-202` | Extend `LLMProvider` with `generate_with_tools(function_declarations)`; no runtime impact now |
| Tip fatigue | P2 | Tip suggestions not cooldown-tracked → may over-send | `core/llm_tools.py:_handle_suggest_tip` + `commerce/relationship.py` | New table `tip_deliveries` or leverage `exposures` memory |
| Dashboard offer CRUD stubs | P2 | `routes/fangate.py` silently succeeds on sync failure | `chatbotv2/dashboard/routes/fangate.py` | Surface `FangateError` to UI + toast |
| Duplicate Settings | P2 | `core/config.py` vs `chatbotv2/config.py` duplication | `core/config.py:12` vs `chatbotv2/config.py` | Unify to single `core/config.py`; alias for MTProto |
| Dead `main.py` block | P3 | Unreachable code after `continue` line 561 | `chatbotv2/main.py:562-902` | Delete or mark `# pragma: no cover` |
| DRAFT_STREAM unused | P3 | `DRAFT_STREAM="draft_messages"` declared, never used | `db/redis.py:16` | Remove constant |
| `operators` table | P3 | Schema exists, never populated/read | `db/schema.sql`, `db/postgres.py` | Wire to dashboard `operators` router or drop |
| `dlq_messages` PG table | P3 | DLQ goes to Redis stream `dead_letter_queue`, PG table unused | `db/schema.sql:180` | Consolidate or document |

**Historical high-risk items that were fixed** (keep in mind — do not regress):
- P0-1 scoring fail-closed (`core/scoring.py:176` `scoring_failed → composite 0.0`),
- P0-2 reconciliation fail-closed (`commerce/reconciliation.py: return False` on fulfillment failure),
- P0-3 tip import + deterministic tip `enqueue_send` (`core/llm_tools.py`),
- P1-2 empty draft → operator queue with `[No response generated]`,
- P1-3 delivery `reserve → DLQ` on failure (`chatbotv2/main.py:283`),
- P1-7 latency wired to telemetry (`workers/llm_worker.py` 4 sites).

---

## 17. Change Boundary Rules

> Enforced by `AGENTS.md:68-92`. Classify every realtime-adjacent change before coding.

- **SAFE:** bug fix preserving event name/schema/semantics, perf, rendering, new consumers/tests/logs, reconnect/polling.
- **REQUIRES REVIEW:** payload fields, timing, scope, generation_id behavior, ordering, Redis Pub/Sub, WS auth, removing polling, producer/consumer responsibilities.
- **FORBIDDEN without explicit approval:** renaming/deleting events, changing meaning, business logic depending on WS, workers depending on WS, replacing Streams with Pub/Sub for tasks, treating WS as durable, silently changing schema.  
  **If FORBIDDEN or REQUIRES REVIEW — STOP, explain why contract insufficient, list producers/consumers, propose backward-compatible migration. Do not silently ship.**

---

## 18. Onboarding Checklist

### Day 0 — Read in order (30 min)
- [ ] `AGENTS.md` (run commands + invariants — the contract you must not break)
- [ ] This handoff §1-8 (architecture + invariants)
- [ ] `docs/FINAL_WHOLE_SYSTEM_TECHNICAL_CLOSURE_REPORT.md:1` (GO verdict + what was hardened)
- [ ] `docs/CODEBASE_MAP.md:1` (verified gaps — know stale debt)

### Day 1 — Run locally
- [ ] `pip install -e ".[dev]"` + `cp .env.example .env` + fill `API_ID/API_HASH/PHONE_NUMBER/OLLAMA_*` 
- [ ] Start PG+Redis (Docker or local), then `psql -f db/schema.sql` + `python -m db.migrate upgrade`
- [ ] `python run_all.py` — verify `:8080` loads, `GET /health` returns `healthy`
- [ ] `pytest -m unit -q` passes; `pytest tests/test_phase8_single_pass.py -q` shows one generation not three
- [ ] Send a test inbound via `clients.get_input_entity` or `POST /api/dialogs/{id}/send` and watch WS + polling

### Week 1 — Own the stack
- [ ] Read `workers/llm_worker.py:464` end-to-end (the hot path you will change most)
- [ ] Trace `context_engine/authoritative_assembly.py:52` → `core/one_call.py:189` → `core/one_call_pipeline.py:36`
- [ ] Read `commerce/product_catalog.py:54` + `db/vault.py:46` + `chatbotv2/main.py:89` (commerce authority)
- [ ] Review `workers/scheduler_worker.py:172` + `commerce/reconciliation.py`
- [ ] Pick one P2 from §16 and ship with tests

### Guardrails
- [ ] Never log or commit `FANGATE_ENC_KEY` / `OLLAMA_API_KEY` / `TELETHON_SESSION` (Fernet BasicAuth)
- [ ] Keep `generation_id` deterministic MD5; never rotate mid-lifecycle
- [ ] Respect `AUTONOMY_ENABLED=false` in staging (disables all commerce autonomy)
- [ ] Run `ruff format . && ruff check .` before push

---

## Appendix A — Key File Index

| File | Lines | Role | Entry Points |
|------|-------|------|--------------|
| `run_all.py:1` | 237 | Launcher — 5 procs + stale cleanup + migrations | `python run_all.py` |
| `chatbotv2/main.py:1` | 974 | MTProto bot + send consumer | `python -m chatbotv2.main` |
| `chatbotv2/handlers.py:1` | 176 | Inbound handler + debounce | `setup_handlers`, `handle_incoming_message` |
| `chatbotv2/client.py:1` | 50 | Telethon singleton | `get_client`, `send_file` |
| `chatbotv2/dashboard/app.py:1` | 196 | FastAPI composition (26 routers) | `uvicorn chatbotv2.dashboard.app:app` |
| `core/config.py:1` | 220 | Settings singleton | `get_settings()` |
| `core/event_bus.py:1` | 142 | Redis Pub/Sub publish (best-effort) | `publish_event`, `publish_events_batch` |
| `core/one_call.py:1` | 830 | One-call contract + grounding | `validate_one_call_response`, `OneCallResult` |
| `core/one_call_pipeline.py:1` | 361 | One-call pipeline | `one_call_generation` |
| `core/llm_provider_ollama.py:1` | 492 | Ollama adapter (think=false) | `OllamaProvider.generate*` |
| `core/scoring.py:1` | 192 | LLM + keyword scoring | `score_draft` |
| `context_engine/authoritative_assembly.py:1` | 337 | ONE TURN = ONE SNAPSHOT | `assemble_authoritative_context` |
| `db/postgres.py:1` | ~1400 | PG pool + DAL | `init_pool`, `save_inbound_message`, `get_recent_messages`, `add_to_operator_queue` |
| `db/redis.py:1` | 814 | Streams/locks/debounce/rate-limit/DLQ | `enqueue_inbound`, `enqueue_send`, `acquire_user_lock` |
| `db/vault.py:1` | 563 | Vault reserve/finalize | `reserve_delivery`, `finalize_delivery` |
| `db/migrate.py:1` | 298 | Migration engine | `python -m db.migrate {status,upgrade}` |
| `db/schema.sql:1` | 217 | Baseline DDL | `psql -f db/schema.sql` |
| `commerce/product_catalog.py:1` | 143 | Product menu (DB authoritative) | `get_product`, `get_menu_context` |
| `workers/llm_worker.py:1` | ~1500 | Generation + routing | `python -m workers.llm_worker --worker-id` |
| `workers/send_worker.py:1` | 170 | Operator queue flusher | `python -m workers.send_worker --worker-id` |
| `workers/scheduler_worker.py:1` | 358 | Scheduler + reconciliation | `python -m workers.scheduler_worker --worker-id` |
| `memory/context.py:1` | ~200 | Legacy context | `build_qwen3_context` |
| `memory/context_assembler.py` | — | LLMContext | `build_llm_context`, `render_context` |

> Line counts from `read` tool headers; tilde indicates file was capped at 50KB preview.

## Appendix B — Glossary

| Term | Meaning |
|------|---------|
| **MTProto** | User-account Telegram protocol (Telethon) — not Bot API. Session file `chatbotv2.session`. |
| **Persona** | Creator-scoped `personas(instructions, metadata, version)` used as system prompt. |
| **One-call** | Single Qwen2.5 generation producing `{reply, commerce_signals, confidence, needs_handoff}` (`core/one_call.py:39`). Replaces legacy 3-LLM pipeline. |
| **Authoritative assembly** | `context_engine/authoritative_assembly.py:52` — single coordinated DB fetch per turn, reused downstream. |
| **Commerce Signals** | 18-field advisory struct (`commerce/signals.py`). LLM-sourced, never authoritative. |
| **PIPELINE_MAX_MESSAGES** | `commerce/pipeline.py:PIPELINE_MAX_MESSAGES=3` — boundary for sealed commerce request. |
| **Dedup** | `send_dedup:{creator}:{md5}` Redis SETEX 3600 — prevents duplicate Telegram sends. |
| **DLQ** | Redis stream `dead_letter_queue` + helpers `db/redis.py:575` + optional PG `dlq_messages`. |
| **Warming / Readiness** | Deterministic engagement models (`commerce/warming.py`, `commerce/readiness.py`) — advisory for LLM, enforced by production control. |
| **Autonomy** | `AUTONOMY_ENABLED` (`core/config.py:111`) — when false, all autonomous commerce disabled. |
| **Canary** | Gradual rollout (`ai_agent_canary_sample_rate`, `context_engine_canary_mode`) — shadow comparisons that never mutate state. |
| **Vault ledger** | `vault_media_deliveries(status pending/sent)` — tracks which media went to which fan. |
| **Free photo ledger** | Same vault table with `dropfans_vault_item_id` for free photo sends. |
| **Generation ID** | MD5(`{user_id}:{content}:{telegram_message_id}`) — deterministic, reused across all events of one turn. |
| **Event ID** | UUID4 per publish (`core/event_bus.py:46`) — frontend dedup. |

---

**Gaps in this handoff:** If you find a claim above that does not match code at `file_path:line_number`, please file it as a defect — this doc was built from direct file reads, but code evolves. The single next most valuable file to read end-to-end is `workers/llm_worker.py:464` (1020 lines of production path you will touch daily).
