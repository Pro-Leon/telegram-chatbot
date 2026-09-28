# AI_NATIVE_OBSERVABILITY_PHASE_42_FORENSIC_AUDIT — STAGE A
**Hostile Forensic Audit — Real-Time AI Execution Dashboard + Operator Event System**
**Date: 2026-08-31 | Workspace: E:\chatbot | Git: b34d73a | Auditor: OpenCode Muse Spark**
**STAGE A ONLY — NO PRODUCTION CHANGES**


## 1. Executive Summary

The system **does** have a usable real-time foundation (Redis Pub/Sub `chatbot:events` + WebSocket `ws_manager` + `event_subscriber` + `RealtimeClient` + polling fallback) and a rich generation-scoped telemetry skeleton (`GenerationTelemetry` 110 fields, `production_control` metrics/audit, `commerce_offers` lifecycle). The Phase 1 event contract (`AGENTS.md:81-152`) is **PROVEN preserved** and failure-isolated. However the observability surface that a Stage B real-time dashboard requires is **PARTIAL/MISSING** on the exact dimensions that matter for operator trust.

**What exists (PROVEN):**
- Redis Pub/Sub `chatbot:events` + `core/event_bus.py:13 publish_event` best-effort, `event_id=uuid4`, `generation_id` propagated, never blocks business logic.
- `chatbotv2/dashboard/ws_manager.py:18 ConnectionManager` scoped broadcast + `chatbotv2/dashboard/event_subscriber.py:14 start_event_subscriber` with bounded exponential backoff + `chatbotv2/dashboard/static/js/realtime.js:19 RealtimeClient` with dedup 200, reconnect jitter, `isPollingActive()` guard. Polling fallback is **PROVEN correctly preserved** (`realtime.js:53 onopen _pollingActive=false`, `:93 onclose _pollingActive=true`), invariants `AGENTS.md:190-192` honored.
- Deterministic single-pass pipeline: **ONE** `extract_commerce_signals` (`workers/llm_worker.py:639`), **ONE** Qwen (`83 generate_draft` / `180 generate_draft_with_tools`), **ONE** `score_draft` (`core/scoring.py:86`), ordered `enqueue_send` before `ai.generation_completed` (`workers/llm_worker.py:1271 -> 1285`), tested `tests/test_phase1_regression.py:855`.
- GenerationTelemetry dataclass 110 fields (`core/telemetry.py:19`) + bounded in-memory metrics/audit (`commerce/production_control.py:51 record_metric _METRIC_MAX 5000`, `:624 _audit_log 1000`) + Postgres `generation_telemetry` table (`db/migrations/20260828040000_generation_telemetry.sql`).
- Commerce PPV lifecycle (`commerce_offers` pending/clicked/purchased/declined/expired/revoked `db/migrations/20260819010000_fangate_ppv_commerce.sql:7`) + synthetic DropFans product mirror (`db/dropfans.py:103 sha256(product_id)%2^62`) + earnings reconciliation (`integrations/dropfans/service.py:611 get_earnings`, `734 poll_sales`, `commerce/reconciliation.py:38/251`).
- Creator-scoped commerce writes (`WHERE creator_id=$1` everywhere `commerce/dao.py`, `db/fangate.py:367`, `db/vault.py:18`). Credentials Fernet-encrypted, `repr=False` on enc keys (`core/config.py:54`).

**What is missing/broken (PROVEN):**
- `generation_id` is deterministic `md5(user:msg:telegram_id)` (`workers/llm_worker.py:511`, `db/redis.py:175`) but `generation_telemetry.generation_id UUID UNIQUE` (`migration:6`) **rejects 32-hex md5** -- `insert_generation_telemetry` (`db/postgres.py:2720`) silently fails, telemetry lost for all auto generations. **P0**.
- `generation_id` **dropped at send handoff**: `enqueue_send` (`db/redis.py:65`) and `chatbotv2/main.py:343 message.sent` carry **no** `generation_id`; end-to-end `message -> decision -> LLM -> scoring -> send -> outcome` cannot be joined on one stable id. **P0**.
- `message.created` (`chatbotv2/handlers.py:66`) carries **no** `generation_id`; debounced earlier messages (`handlers.py:115 latest = debounced[-1]`) have DB rows but no generation correlation. **P1**.
- No canonical persisted execution stage automaton. `pipeline_stage`/`execution_stage`/`processing_stage`/`workflow_state` **do not exist**; `LifecycleState` 15 (`commerce/conversation_operations.py:32`), `ConversationLifecycle` 3 (`core/conversation_state.py:26`), `FunnelState` 14 (`commerce/revenue_intelligence.py:111`), `DesireStage` 9 (`commerce/desire.py:20`) are **transient, re-derived per turn, not stored, not transition-guarded**. **P1**.
- Fan live-state is **derived, never stored**: `active`/`waiting`/`replying`/`high-intent` have no column/event/metric. `hot` = `temperature HOT >=0.65` (`commerce/temperature.py:31`) is computed on-demand, never emitted as event, not filterable in segments.
- PPV `sent` not observable (no `commerce_offers.sent_at`, no `commerce.offer.sent` event), `clicked` has no metric/event, sale-lost has **no table/metric/event**, aftercare `sent` not persisted (only `pending->completed` `commerce/dao.py:935/962`), revenue `currency` assumed USD, `buyer_email` stored plaintext and visible in `earnings.html:186` but not redacted in logs (`core/logging_config.py:13 _SENSITIVE_PATTERNS` misses `buyer_email`).
- Fan/creator isolation **FAILS** at core layer: `users(id PK)`/`messages`/`user_profiles`/`conversation_summaries` are **global without `creator_id`** (`db/schema.sql:7`). `get_recent_messages(123)` (`db/postgres.py:360`) returns cross-creator interleaved history. Dashboard `messages.py:143 api_recent_messages` returns last 20 messages of **entire platform** without creator filter. **P0/P1 isolation**.
- Dashboard exists (20 routers `chatbotv2/dashboard/app.py:127-181`, 26 templates) but no stage/funnel visualization; notification is DOM toast partial (`vault.html:94`) with no browser `Notification` push and only `message.send_failed->toast` wired (`chat.html:770`), else `location.reload()`.

**Stage B is CONDITIONALLY READY after P0 fixes.** The architecture constraints (`Redis Streams`, `XAUTOCLAIM`, `existing workers`, `PostgreSQL`, `Telethon`, `DropFans sole purchase authority`, `deterministic hierarchy`, `canary`, `single-pass`, `isolation`, `LLM language-only`) can be preserved with **additive, best-effort** events only.


## 2. Current Architecture

### 2.1 Actual lifecycle (proven from source, not assumed)

```
Telegram inbound (Telethon NewMessage)
  -> handlers.py:58 save_inbound_message ON CONFLICT (user,telegram_message_id) WHERE direction=inbound PROVEN
  -> handlers.py:64 publish_event message.created {message_id,telegram_message_id,content,...} scope=user, NO generation_id PROVEN -- gap
  -> handlers.py:81 debounce_enqueue lock debounce:{user}:lock nx ex window_seconds (3s config.py:34), rpush debounce:{user}:messages PROVEN db/redis.py:299
  -> handlers.py:108 _wait_and_process sleep debounce_window_seconds, get_debounced_messages LRANGE+DEL, latest=debounced[-1] discards earlier PROVEN
  -> handlers.py:129 enqueue_inbound XADD inbound_messages {user_id,content,telegram_message_id,username,first_name,persona,generation_id=md5 if absent} PROVEN db/redis.py:171
  -> workers/llm_worker.py:1502 read_inbound XREADGROUP llm_workers PROVEN db/redis.py:186
  -> workers/llm_worker.py:527 resolve_single_application_creator (lowest creator_id with active integration) PROVEN single_creator.py:57
  -> workers/llm_worker.py:550 acquire_user_lock lock:creator:{creator}:user:{user} (creator-scoped, P1-01) PROVEN db/redis.py:267
  -> workers/llm_worker.py:559 upsert_user PROVEN
  -> workers/llm_worker.py:561 is_user_auto_reply_excluded -> routing_decision=excluded PROVEN
  -> workers/llm_worker.py:571 build_qwen3_context (creator_id scoped) PROVEN memory/context.py:488
  -> workers/llm_worker.py:587 fan_knowledge extract+add (idempotent via evidence_generation_id) PROVEN fan_knowledge.py:294
  -> workers/llm_worker.py:625 publish ai.generation_started {message_preview:100} scope=user generation_id=md5 PROVEN
  -> workers/llm_worker.py:639 extract_commerce_signals SINGLE (P0-01) PROVEN commerce/deepseek.py:170
  -> workers/llm_worker.py:644 _try_commerce_draft sealed 6D/6E at most once, never invents price/url PROVEN
  -> workers/llm_worker.py:669 build_conversational_commerce_state (desire/temperature/readiness/window/objective/next_best_action) PROVEN conversational.py
  -> workers/llm_worker.py:754 make_exposure/persist_exposure bounded 50 PROVEN adaptive_optimization.py:285
  -> workers/llm_worker.py:784 compute_pressure/derive_risk/derive_lifecycle/build_operation_decision PROVEN conversation_operations.py:108/213/49/635
  -> workers/llm_worker.py:837 autonomous_allowed+rollout+handoff+commerce_pause gates (fail-closed, saves Qwen) PROVEN production_control.py:509
  -> workers/llm_worker.py:907 operational_decision+execute_operational_recommendation idempotent via op_exec:{generation_id}:{rec}:{scope}:{creator}:{user} PROVEN operational_execution.py:26
  -> workers/llm_worker.py:962 should_use_agent CanaryConfig routing PROVEN agent/canary.py
  -> workers/llm_worker.py:971 SKIP Qwen if paused (draft="will follow up shortly", score 0.1, flags autonomous_paused) PROVEN
  -> workers/llm_worker.py:1049 generate_draft_with_tools OR generate_draft ONE Qwen call PROVEN
  -> workers/llm_worker.py:1136 score_draft ONE scoring call, authority-aware price bypass 0.005 PROVEN core/scoring.py:86
  -> workers/llm_worker.py:1162 autonomous_allowed second gate + record_metric generation_success/pressure_suppressed + record_audit OperationalAuditRecord PROVEN production_control.py:51/627
  -> workers/llm_worker.py:1225 auto_reply_on gate + dedup_id=md5(user:msg:tg_id) PROVEN
       if auto_reply off -> add_to_operator_queue + publish ai.generation_completed + suggestion.created PROVEN 1232
       elif empty draft -> add_to_operator_queue empty_draft + publish completed PROVEN 1113
       elif score>=0.80 and no flags -> enqueue_send send_messages (NO generation_id) + publish completed AFTER enqueue (invariant) PROVEN 1271->1285
       else -> add_to_operator_queue + notify + publish completed + suggestion.created PROVEN 1299
  -> workers/llm_worker.py:1334 post_process extract_and_update_profile+maybe_summarize async PROVEN 483
  -> workers/llm_worker.py:1395 update_strategy_evidence_extended dedup via strategy_generation_seen_by_creator cap 100 PROVEN strategy_learning.py:250
  -> workers/llm_worker.py:1409 telemetry.complete + record (best-effort, never propagates) PROVEN telemetry.py:229
  -> chatbotv2/main.py:94 read_send_messages XREADGROUP send_workers block 2000 PROVEN db/redis.py:83
  -> chatbotv2/main.py:274 send_message/send_file + save_outbound_after_send PROVEN
  -> chatbotv2/main.py:343 publish message.sent {content,telegram_message_id,was_auto_approved,confidence_score,media_type} scope=user NO generation_id PROVEN -- break
  -> chatbotv2/main.py:352 publish vault.media_sent PROVEN -- NO generation_id
  -> db/postgres messages/operator_queue/generation_telemetry (if not rejected) PROVEN
  -> workers/scheduler_worker:161 reconcile_all -> commerce/reconciliation:38 poll+attribution PROVEN + integrations/dropfans/service:611 get_earnings/734 poll_sales PROVEN
  -> commerce/dao:249 attribution, ppv_analytics_daily revenue_minor, fangate_transactions sole truth PROVEN
```

**Invariants PROVEN honored:** single-pass (1 signal, 1 Qwen, 1 scoring) per generation; `ai.generation_completed MUST NOT before enqueue_send` (`llm_worker.py:1271 await enqueue_send` precedes `1285 publish completed`, failure path emits `ai.generation_failed` only `1424`); `generation_id` shared across `started->completed/failed->suggestion` via same lexical variable `511-514`; workers never import `ws_manager`/`event_subscriber` (grep shows only `core/event_bus` in workers).

**Proven breaks:** send stream + `message.sent` drop `generation_id`; `message.created` has no `generation_id`; debounce collapses N inbounds to 1 generation.

### 2.2 Component map (PROVEN files)

- Ingest: `chatbotv2/handlers.py:25 handle_incoming_message`, `chatbotv2/main.py:77 _process_send_stream`, `chatbotv2/client.py`
- Queue: `db/redis.py` (`INBOUND_STREAM inbound_messages:15`, `SEND_STREAM send_messages:17`, `DLQ_STREAM dead_letter_queue:18`, `CONSUMER_GROUP llm_workers:40`, `SEND_CONSUMER_GROUP send_workers:41`, `enqueue_inbound:171`, `enqueue_send:65`, `read_inbound:186`, `ack_inbound:206`, `requeue_stalled_messages XAUTOCLAIM:211`, `acquire_user_lock:272`, `debounce_enqueue:299`, `check_send_rate_limit Lua ZSET 1/s burst5:420`)
- Determinism: `commerce/desire.py:20 DesireStage`, `commerce/temperature.py:31 derive_commercial_temperature`, `commerce/relationship.py:17 RelationshipState`, `commerce/signals.py:147 CommerceSignals`, `commerce/decision.py:156 CommerceDecision`, `commerce/state.py:139 resolve_commerce_state`, `commerce/pipeline.py:112 CommercePipelineStatus`, `commerce/execution.py:40 ExecutionStatus`, `commerce/conversation_operations.py:32 LifecycleState`, `commerce/conversation_operations.py:569 ConversationOperationDecision`, `commerce/adaptive_optimization.py:38 CanonicalOutcome`, `core/conversation_state.py:26 ConversationLifecycle`
- LLM: `workers/llm_worker.py:83 generate_draft`, `:180 generate_draft_with_tools`, `core/llm_provider.py`, `core/scoring.py`
- Telemetry: `core/telemetry.py:19 GenerationTelemetry`, `:194 TelemetryCollector`, `commerce/production_control.py:51 record_metric`, `:606 OperationalAuditRecord`, `core/event_bus.py:13 publish_event`
- Persistence: `db/postgres.py:28 init_pool`, `:59 verify_schema`, `db/schema.sql:7 users`, `:22 messages`, `:57 user_profiles`, `:94 operator_queue`, `db/migrations/*`, `db/dropfans.py:37`, `db/fangate.py:53`, `db/vault.py:18`
- Realtime: `core/event_bus.py:10 CHANNEL chatbot:events`, `chatbotv2/dashboard/ws_manager.py:18 ConnectionManager`, `chatbotv2/dashboard/event_subscriber.py:14 start_event_subscriber`, `chatbotv2/dashboard/static/js/realtime.js:19 RealtimeClient`, `chatbotv2/dashboard/routes/ws.py:12 @router.websocket /ws`
- Dashboard: `chatbotv2/dashboard/app.py:67 FastAPI` + 20 routers `127-181`, 26 templates `templates/`
- Workers: `workers/llm_worker.py:1446 run_worker`, `workers/send_worker.py`, `workers/scheduler_worker.py:161 reconcile_purchases`


## 3. Existing Event Inventory

### 3.1 Real-time event contract (Phase 1 -- IMMUTABLE, PROVEN preserved)

Defined `AGENTS.md:81-97`, enforced `tests/test_phase1_regression.py:818`.

| # | event_type | File:Line | Scope | generation_id | event_id | Payload (bounded) |
|---|---|---|---|---|---|---|
| 1 | `message.created` | `handlers.py:66` | user | **NONE** PROVEN break | uuid4 | message_id, telegram_message_id, content, direction=inbound, username, first_name |
| 2 | `message.sent` | `main.py:343` | user | **NONE** PROVEN break | uuid4 | content, telegram_message_id, was_auto_approved, confidence_score, media_type, fangate_media_id |
| 3 | `message.send_failed` | `main.py:383/414` | user | **NONE** | uuid4 | error UserIsBlockedError/SendError |
| 4 | `ai.generation_started` | `llm_worker.py:625` | user | **YES** md5 | uuid4 | message_preview 100 |
| 5 | `ai.generation_completed` | `llm_worker.py:1119/1244/1285/1307` | user | **YES** | uuid4 | draft, score, flags, was_auto_approved |
| 6 | `ai.generation_failed` | `llm_worker.py:1424` | user | **YES** | uuid4 | error[:200] |
| 7 | `suggestion.created` | `llm_worker.py:1257/1320` | user | **YES** | uuid4 | queue_id, draft, score, flags |
| 8 | `operator_queue.updated` | `send_worker.py:102` / `queue.py:47` | global | N/A | uuid4 | queue_id, status |
| + | `vault.media_sent` | `main.py:352` | user | **NONE** | uuid4 | fangate_media_id, product_id, user_id |

All go via `core/event_bus.py:50 r.publish(CHANNEL=json.dumps(event))`, `30-32 enable_websocket guard`, `52-54 except: warning return None` -- best-effort, never breaks generation.

### 3.2 Observability mechanisms inventory

| Mechanism | file | function | caller | consumer | storage | retention | scope | creator isolation | generation correlation | idempotency | PII exposure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| publish_event | `core/event_bus.py:13` | publish_event | `llm_worker 5 sites`, `handlers`, `main` | `event_subscriber->ws_manager->realtime.js` | Redis Pub/Sub chatbot:events | ephemeral 0 | global/user | via user/dialog_id, NOT creator | via generation_id param (where supplied) | event_id uuid4 dedup 200 cache | content in message.created/sent, preview 100 in started (safe) |
| ws_manager | `dashboard/ws_manager.py:18` | ConnectionManager.broadcast | `event_subscriber:64` | browser RealtimeClient | mem _connections | conn lifetime | global+dialog_id | is_global vs dialog_id | N/A | stale pruning 72 | none |
| event_subscriber | `dashboard/event_subscriber.py:14` | start_event_subscriber | `dashboard/app.py:98` | ws_manager | Redis pubsub listen | ephemeral+backoff 30s | global/user | broadcast filter | N/A | backoff cap | none |
| generation_id | `llm_worker.py:511` | md5 inline | process_message | event_bus/telemetry/commerce | Redis stream field + telemetry cache + generation_telemetry UUID (broken) | stream inf, DB inf, cache popped | per generation creator/user | via creator_id in payload | md5 deterministic | dedup via generation_id seen lists | preview only |
| GenerationTelemetry | `core/telemetry.py:19` | GenerationTelemetry dataclass 110 fields | `llm_worker:517` | `insert_generation_telemetry` | mem cache + PG generation_telemetry | mem ephemeral, DB indefinite | per generation | creator_id field | generation_id UUID key | generation_id | product_selected, no content/email |
| record_metric | `commerce/production_control.py:51` | record_metric | `llm_worker:1176`, `operational_execution:87` | evaluate_production_health | mem _metric_events 5000 + JSONB sentinel -999997 200 | 5000 prune 1000 + window H1/H24/D7/D30 | creator filter | WHERE creator_id | none | global 5000 unfair | none |
| OperationalAudit | `production_control.py:606` | record_audit | `llm_worker:1187`, `operational_execution:73` | query_audits | mem _audit_log 1000 | 1000 prune 200 | creator+generation | WHERE creator_id | generation_id | none | objective/strategy only |
| Tool audit | `db/postgres.py:2680` | insert_tool_audit_log | `core/llm_tools:220` | dao tip fatigue 30d | PG tool_audit_log | no prune, 30d query | creator,user | creator_id+user_id | none | outcome enum | tool_name only |
| Messages audit | `db/schema.sql:21` | messages table | save_inbound/outbound | get_recent_messages analytics | PG messages | indefinite | user | **NONE global** P0 | none | ON CONFLICT telegram_message_id | full content PII |
| DLQ | `db/redis.py:107` | move_to_dlq/move_send_to_dlq | llm_worker, main | routes/dlq.py replay | Redis DLQ_STREAM + dlq_messages PG | 7d retention 604800 but not auto-cleaned | stream=inbound/send | N/A | payload JSON | replay lock dlq_replay_lock 30s | none |
| Health | `core/health.py:13` | check_redis/postgres/gemini/dropfans | routes/health | dashboard/k8s | ephemeral cache 10s | ephemeral | global | N/A | N/A | cache TTL | none |
| logger | `core/logging_config.py:24` | StructuredFormatter | all workers | stdout | stdout/files | streams | global | per logger | generation_id in msg | N/A | _SENSITIVE_PATTERNS misses buyer_email |

**PROVEN ABSENT:** `record_event`, `emit_event` (0 hits), `span`/OpenTelemetry (0 hits except substring), `Server-Sent Events`, `HTMX`, browser `Notification` API.


## 4. Generation Correlation

### 4.1 Deterministic identity (PROVEN, with bug)

- Creation: `workers/llm_worker.py:511 if generation_id is None: hashlib.md5(f"{user_id}:{user_message}:{telegram_message_id}".encode()).hexdigest()` -- P1-03 survives retries. Parallel `db/redis.py:175 same md5 in enqueue_inbound` if field absent. Else `str(generation_id)` preserved. **PROVEN**.
- Scheduler counterexample: `scheduler_worker.py:227 f"scheduler-{cid}-{ts}-{rec}"` not md5, breaks invariant for scheduler generations **LIKELY**.
- Telemetry fallback: `core/telemetry.py:21 default uuid4`, overridden `220 if generation_id: telemetry.generation_id=generation_id` **PROVEN**.
- **P0 Bug:** `db/migrations/20260828040000_generation_telemetry.sql:6 generation_id UUID NOT NULL UNIQUE` expects 36-char dash UUID, but md5 is 32 hex no dashes. `db/postgres.py:2744 data.get("generation_id","")` inserted as UUID fails -- `except: warning "generation_telemetry insert failed"` `2768` silently drops telemetry for all auto generations. Only `telemetry.py:21 uuid4` fallback would succeed. **PROVEN via type mismatch**.

### 4.2 Hop-by-hop trace

| Hop | file:line | generation_id present | evidence | confidence |
|---|---|---|---|---|
| Telegram save | `handlers.py:58` save_inbound_message | NO column in messages | `schema.sql:21` 0 hits generation_id | PROVEN gap |
| message.created event | `handlers.py:66` publish | NO param | `event_bus.py:41 generation_id=None` | PROVEN gap |
| debounce | `db/redis.py:299 debounce_enqueue` / `handlers.py:81` | NO injection | only `user_id,content,telegram_message_id,username,first_name` | PROVEN |
| enqueue_inbound | `db/redis.py:182 XADD` | YES if supplied else md5 | `data["generation_id"]=gid` | PROVEN |
| read_inbound | `llm_worker.py:1517 data.get("generation_id") or None` | YES passthrough | `msg_data generation_id` | PROVEN |
| ai.generation_started | `llm_worker.py:625` | YES shared | `generation_id=generation_id scope=user` | PROVEN |
| LLM provider call | `llm_worker.py:1049 generate_draft` | NO param (closure only) | no generation_id arg | PROVEN |
| scoring | `llm_worker.py:1136 score_draft` | NO param | `core/scoring.py` 0 hits generation_id | PROVEN |
| enqueue_send | `llm_worker.py:1271` | **NO** | `db/redis.py:65 no generation_id handling` | PROVEN BREAK |
| message.sent | `main.py:343` | **NO** | publish without generation_id | PROVEN BREAK |
| operator_queue insert | `db/postgres.py:505 add_to_operator_queue` | NO column | operator_queue schema:94 no generation_id | PROVEN gap |
| outcome/strategy/fanKnowledge | `commerce/fan_knowledge.py:294`, `behavioral_intelligence.py:18`, `strategy_learning.py:250` | YES dedup | `if e.get("generation_id")==generation_id` + `seen` cap 100 | PROVEN |
| metrics/audit | `llm_worker.py:1187 _PcAudit(generation_id=...)` | YES | `production_control.py:608 OperationalAuditRecord generation_id` | PROVEN |
| telemetry | `core/telemetry.py:121 to_dict generation_id` | YES | `insert_generation_telemetry` UUID bug | PROVEN |

**Result:** lifecycle events `ai.generation_started/completed/failed + suggestion.created` share **same lexical `generation_id`** never regenerated (`llm_worker.py:511,625,1129,1254,1267,1295,1317,1429`). Ordering invariant `enqueue_send before completed` holds (`1271 before 1285`). But `message.created->...->message.sent` cannot be joined.

### 4.3 Retry/XAUTOCLAIM/restart/duplicate (PROVEN stable where present)

- Retry within process_message: preserves passed `generation_id` via `str(generation_id)` else deterministic md5 -- **PROVEN stable**.
- XAUTOCLAIM inbound: `db/redis.py:221 xautoclaim inbound_messages llm_workers` returns `(next_id, [(msg_id, fields)])` preserving original `fields generation_id` -- **PROVEN stable** `llm_worker.py:1492`.
- XAUTOCLAIM send: `db/redis.py:155` same for send, but no generation_id to preserve -- N/A.
- Restart: `ensure_consumer_group id="0" mkstream=True BUSYGROUP ignore` (`db/redis.py:48`) pending PEL survives restart -- **PROVEN** requires Redis persistence.
- Duplicate enqueue: `save_inbound_message ON CONFLICT (user,telegram_message_id)` (`db/postgres.py:259`) dedups DB, but `XADD id="*"` always creates new stream entry even with same `generation_id` -- **LIKELY duplicate entries** -> LLM runs twice with same `generation_id`, downstream dedups via seen lists but emits duplicate `ai.generation_started` with same id (event_id differs).
- Test conflict: `tests/test_phase1_regression.py:997 test_generation_id_unique_per_process_message` expects 3 calls same `(1,hi,100)` yield 3 distinct ids -- **fails under deterministic md5** (proven conflict).

### 4.4 Dashboard reconstruction

Can reconstruct **per-generation** timeline from `ai.generation_started/completed/failed + suggestion.created` joined on `generation_id` (all share id). Cannot reconstruct `message.created -> generation` or `generation -> message.sent` without joining on `user_id+timestamp` heuristic. Historical after 7d relies on `generation_telemetry` which is currently **lost** due to UUID bug, plus `messages`/`operator_queue` (creator-unscoped). So **PARTIAL** -- requires P0 fixes.

### 4.5 Missing instrumentation points

1. `handlers.py:66` add `generation_id` to `message.created` (derive md5 same as enqueue).
2. `db/redis.py:65 enqueue_send` accept `generation_id` field, `main.py:343` forward `generation_id` in `message.sent`/`send_failed`/`vault.media_sent`.
3. `db/postgres.py:505 operator_queue` add `generation_id TEXT` column or JSONB, forward in `suggestion.created`.
4. Fix `generation_telemetry.generation_id` type to `TEXT` or store `uuid5(md5)`.


## 5. Execution Stage Model

### 5.1 No canonical persisted stage (PROVEN)

- `pipeline_stage`: no definition; closest `CommercePipelineStatus` 6 (`commerce/pipeline.py:112 COMPLETED/DECISION_FAILED...`) + `CommercePipelineResult` 234 -- offer-scoped, not conversation.
- `execution_stage`: no enum; only `ExecutionStatus` 10 (`commerce/execution.py:40 EXECUTED/ALREADY_EXECUTED/DENIED...`).
- `conversation_stage`: transient string `adaptive_optimization.py:219 conversation_stage` in `make_exposure`, `revenue_intelligence.py:519` filter, not persisted.
- `processing_stage`/`workflow_state`/`OperationType`: **0 hits** (`rg OperationType` 0 files).
- `ConversationOperationDecision` (`commerce/conversation_operations.py:569`) is **authoritative singleton** dataclass 15 fields (`objective, next_best_action, commercial_pressure, fatigue, risk_state, lifecycle: LifecycleState 15, allowed, blocking_reason, decision_trace 480`); `build_operation_decision:635` enforces `handoff->not allowed`. Not persisted except via `telemetry.decision_trace`.
- `LifecycleState` 15 (`new/curious/engaged/interested/qualified/desiring/offer_ready/purchased/aftercare/repeat/cooldown/rejected/handoff/dormant/re_engaged` `conversation_operations.py:32`) and `ConversationLifecycle` 3 (`new/established/returning` `core/conversation_state.py:26`) and `FunnelState` 14 (`commerce/revenue_intelligence.py:111 NEW/ENGAGED/INTERESTED...`) and `DesireStage` 9 (`commerce/desire.py:20`) are **deterministic, re-derived per turn, no DB column, no transition table**. `is_valid_transition` exists only for automation (`automation/models.py:921`) not for conversation.

### 5.2 Durable state actually stored

- `users.funnel_stage TEXT DEFAULT new` (`schema.sql:14`) -- sole durable stage column, advanced only via `post_purchase.py:46 advance_funnel_to_converted` on purchase + `db/postgres.py:122 update_funnel_stage`. Audit shows `new at 47 msgs` (`docs/SUNNY_CONVERSATIONAL_AI_FORENSIC_AUDIT.md:607`).
- `operator_queue.status` pending/sent/failed, `messages.direction` inbound/outbound, `generation_telemetry` row.

### 5.3 Required dashboard stage vs internal details

The prompt lists desired display stages:

```
RECEIVED, DEBOUNCED, QUEUED, PROCESSING, CONTEXT, PERSONALIZATION, SIGNALS,
DECISION, CONTROL_GATE, QWEN, SCORING, AUTHORIZATION, DRAFT, SEND_QUEUE, SENDING, SENT, OUTCOME, COMMERCE, AFTERCARE, ERROR, WAITING
```

**Authoritative inventory (PROVEN) maps:**
- RECEIVED = `save_inbound_message` + `message.created` (no gen id yet)
- DEBOUNCED = `debounce_enqueue` + `_wait_and_process` (only latest)
- QUEUED = `enqueue_inbound` XADD
- PROCESSING = `read_inbound` + `acquire_user_lock`
- CONTEXT = `build_qwen3_context` (`context_build_ms`)
- PERSONALIZATION = `fan_knowledge` + `behavioral_intelligence` + `long_term_memory` (bounded 30/20/5)
- SIGNALS = `extract_commerce_signals` low_information fallback (`signals.py:226`)
- DECISION = `derive_desire/temperature/evaluate_offer_readiness/derive_sales_window/derive_conversation_objective/compute_pressure/derive_risk/derive_lifecycle/build_operation_decision` (pure)
- CONTROL_GATE = `autonomous_allowed` + `is_rollout_active_for` + `is_commerce_paused` + handoff check (fail-closed)
- QWEN = `generate_draft` single call (provider latency)
- SCORING = `score_draft` single call
- AUTHORIZATION = `score >=0.80 && !flags && auto_reply_on` vs `add_to_operator_queue`
- DRAFT = `selection.commerce_response_text` vs `draft`
- SEND_QUEUE = `enqueue_send` + dedup 3600 (`is_send_duplicate`)
- SENDING = `main.py:274 send_message/send_file`
- SENT/OUTCOME/COMMERCE/AFTERCARE/ERROR/WAITING = persisted `messages`, `operator_queue`, `commerce_offers.state`, `fangate_transactions`, `aftercare_status`.

**Do NOT expose internals:** raw provider prompts, secrets, full message content beyond bounded preview, `strategy_exploration` internals. Minimum safe metadata: `generation_id`, `creator_id`, `user_id`, `timestamp_ms`, `stage`, `objective`, `desire_stage`, `temperature`, `pressure_score`, `risk_state`, `lifecycle_state`, `funnel_state`, `routing_decision`, `score`, `flags`, `was_auto_approved`, `queue_id`, `dedup_id`.

No new state machine should be invented; Stage B should **derive** a display `execution_stage` enum from existing deterministic outputs + timestamps, and surface `decision_trace` (480) already present.

## 6. Fan Live-State Model

### 6.1 Current sources (PROVEN)

Durable truth = `users` + `messages` only.

| Concept | Derived how | File:line | Creator scoped | Observability |
|---|---|---|---|---|
| `ConversationLifecycle NEW/ESTABLISHED/RETURNING` | `derive_lifecycle message_count<=2 -> new; gap>=48h -> returning else established` per turn | `core/conversation_state.py:53` | **NO** global message_count/last_seen | telemetry lifecycle_state 99, not stored |
| `current_topic/open_threads/last_question/tone` | keyword scan `_extract_topics:106`, `_is_question:75`, `_derive_tone:128`, `derive_conversation_state:157` | `core/conversation_state.py` | **NO** global history | only in generation context |
| `has_active_offer` | `SELECT * commerce_offers WHERE creator=$1 AND user=$2 AND state IN (pending,clicked)` | `memory/context_assembler.py:246` | **YES** | context has_active_offer bool |
| `hours_since_last_offer/purchase, recent_offer_count` | `commerce/dao.py:411 get_timing_context SELECT created_at,purchased_at ... ORDER BY created_at DESC LIMIT 1` + `COUNT FILTER` | `dao.py:411` fail-closed neutral | **YES** | temperature, sales_window |
| `replies waiting` | no flag; inbound via `read_inbound` llm_workers XAUTOCLAIM 30s, lock `lock:creator:{creator}:user:{user}` if locked skip `already locked 552` | `llm_worker.py:550` | **YES** for lock, **NO** for debounce/rate-limit global | telemetry routing_decision=locked 555 |
| `high-intent` | not a flag; mapped to `temperature HOT score>=0.65` + `desire OFFER_READY` | `temperature.py:31` | derived | telemetry temperature |

**Gap:** No `active_at`/`waiting_at` timestamp, no presence, no `replying` flag. "Active in last 5m" must be defined from `messages.created_at` or `users.last_seen`.

### 6.2 What "active" can safely mean

From existing semantics, safe derived definitions (Stage B, all creator-scoped via `messages` JOIN `users` filtered by creator's `commerce_offers`/`operator_queue` not global):

- `active` = `MAX(messages.created_at) >= NOW() - interval 5 minutes` for that `creator+fan` in last N messages (or `last_seen` within window but `last_seen` is global, prefer messages).
- `waiting` = `operator_queue.status=pending` for that `creator+fan`.
- `replying` = generation in-flight (`ai.generation_started` without `completed/failed` yet) tracked via `TelemetryCollector._telemetry_cache` (ephemeral) or recent `generation_telemetry` with `success IS NULL`.
- `high-intent` = `temperature=HOT` (score>=0.65) AND `desire_stage=offer_ready` AND `purchase_intent>=0.80` on last generation (telemetry).
- `commerce_opportunity` = `offer_readiness=ready` (derived `offer_readiness.py`) and not `aftercare/cooldown`.
- `PPV sent` = `commerce_offers.state=pending/clicked` ordered by `created_at DESC`.
- `awaiting purchase` = `pending` age `>=0` and `<48h` before re-engagement.
- `purchased` = `commerce_offers.state=purchased`.
- `aftercare` = `aftercare_status=pending/sent/completed` on last purchased offer.
- `handoff` = `get_handoff_memory active` (`conversation_operations.py:392 user_profiles.handoff_by_creator`).
- `inactive` = no inbound `>=72h` AND no `pending` offer (`derive_lifecycle dormant`).

All must be `creator_id, user_id` scoped; current `users.last_seen` global cannot be used alone.

### 6.3 Creator/fan scoping

`has_active_offer`, `get_timing_context`, lock `lock:creator:{creator}:user:{user}` are creator-scoped **PASS**. `debounce:{user_id}:messages` (`db/redis.py:306`), `ratelimit:{user_id}` (`326`), `context:{user_id}` (`335`), `users.id` are **global FAIL** -- same Telegram `user_id` under Creator A and B share state, `get_recent_messages` interleaves.


## 7. Sales Event Model

### 7.1 PPV lifecycle

Migration `20260819010000_fangate_ppv_commerce.sql:7` `commerce_offers(id,creator_id,user_id,product_id,link,price_minor,currency,state pending/clicked/purchased/declined/expired/revoked,created_by,expires_at,clicked_at,purchased_at,transaction_id, UNIQUE transaction_id WHERE NOT NULL:47)`, plus `20260826010000_aftercare_persistence.sql:5 aftercare_status none/pending/sent/completed/skipped`, indexes `idx_commerce_offers_user_state`, `idx_commerce_offers_aftercare`.

| Event | Authoritative source | Creator scope | Observability | File:line |
|---|---|---|---|---|
| Offer authorized / denied | `evaluate_ppv_eligibility` never carries price/url/credentials | via `creator_id` in `state.py:139` | `dao.py:780 ppv_eligibility_decisions {decision,denial_reason,inputs JSONB}` sanitized, never credentials | `eligibility.py:49`, `dao.py:780` |
| Offer created | `INSERT commerce_offers` via `create_offer:20` or `create_offer_serialized:57` with `pg_advisory_xact_lock ppv_offer:{creator}:{user}:{product}:85` race-free | INSERT includes creator_id,user_id,product_id | best-effort `record_offer_transition state=pending -> ppv_analytics_daily offers_created:299` in `execution.py:267` | `dao.py:57`, `execution.py:311` |
| Offer sent (Telegram) | **No DB sent_at**; side-channel `execute_ppv -> ExecutionResult EXECUTED offer_id:311` -> `select_commerce_response` -> `enqueue_send` dedup; correlation via `messages.direction=outbound` + link in draft | `enqueue_send` includes `creator_id,entity=user_id` | No `commerce.offer.sent` event; infer from `messages` + `commerce_offers.created_at` delta | `execution.py:311`, `llm_worker.py:364`, `post_purchase.py:517` |
| Offer clicked | `UPDATE SET state=clicked,clicked_at=NOW() WHERE creator=$1 AND id=$2 AND state=pending RETURNING *` pending-only | creator+id prevents cross-creator flip | No analytics on click except via `record_offer_transition clicked->offers_clicked` if called | `dao.py:629` |
| Purchase (offer->purchased) | `UPDATE SET state=purchased,purchased_at=NOW(),transaction_id=$3 WHERE creator=$1 AND id=$2 AND state IN (pending,clicked) AND (transaction_id IS NULL OR transaction_id=$3) RETURNING *` idempotent | creator+id check; attribution query `WHERE creator=$1 AND product=$2 AND state IN (pending,clicked) ORDER BY created_at ASC:210` per-creator | logger webhook_attribution, `INSERT ppv_analytics_daily offers_purchased+revenue_minor ON CONFLICT:350` | `dao.py:249/647`, `attribution.py:34` |

Vault: DropFans synthetic `id=sha256(dropfans_product_id)%2^62` in `fangate_products.raw {dropfans_product_id,vaultItemIds[],salesCount}:104` (`db/dropfans.py:103`), per-item `vault_media_deliveries (creator,user,dropfans_vault_item_id) ON CONFLICT DO NOTHING:481` (`commerce/post_purchase.py:481`).

Scoring: `core/scoring.py:12 HARD_FLAGS price_mention 12 keywords`, `97 score_draft` bypasses `price_mention` only when `is_authorized_commerce` and `price_within 0.005` `115-119`, else `composite=min(0.1):187` -> operator_queue.

### 7.2 Sale-lost

**Finding: NO explicit sale_lost table/metric/event. PROVEN.**

Proxies: offer terminal `declined/expired/revoked` (`models.py:15`), `mark_offer_declined:694`, `mark_offer_expired:676`; behavioral `consecutive_rejections:539`, `FeedbackEventType HARD_REJECTION/SOFT_REJECTION:23`; `re_engagement` eligibility `pending >=48h` (`scheduler_worker:244`, `commerce/re_engagement:277`) but no `sale_lost` counter; `compute_conversion_metrics:325` tracks `rejection_rate` via `query_metrics name=rejections` but not `sale_lost` (`offer_created - purchased - still_pending`).

Dashboard must NOT invent lost sale without authoritative transition. Required deterministic transition for later: `pending age >= X AND no purchase AND (declined OR expired OR explicit rejection OR provider failure)` -> record `ppv_analytics_daily abandoned/offers_declined` + `record_metric sale_lost`.

### 7.3 Aftercare

| Stage | Change | File:line |
|---|---|---|
| Pending | `UPDATE WHERE state=purchased AND aftercare_status=none ORDER BY purchased_at DESC LIMIT 1 SET pending :943` | `dao.py:935` |
| Sent | **Not persisted as distinct**; `aftercare_status` only `pending->completed` (`962`), intermediate `sent` exists in type but never written except via scheduler | `scheduler_worker:114` |
| Completed | `UPDATE WHERE state=purchased AND aftercare_status IN (pending,sent) SET completed :971` | `dao.py:962` |
| Derived | `derive_desire_stage has_purchased or aftercare pending/sent -> AFTERCARE 0.85` + `funnel_state AFTERCARE` + LLM `COMMERCIAL STATE desire=aftercare` + telemetry | `desire.py:80`, `revenue_intelligence.py:173`, `llm_worker:714` |

### 7.4 Commerce offer visibility for dashboard

`commerce_offers` can power timeline `created->clicked->purchased` + `aftercare_status` without schema changes via `creator_id,user_id` queries (all `WHERE creator_id`). Queries are creator-scoped. **PROVEN**. Missing `sent_at` correlation to `messages`; Stage B should add `commerce_offers.sent_at` + `telegram_message_id` FK or `dedup_key` if precise funnel needed, but can already show `created_at` as sent proxy.

## 8. DropFans Observability

**Contract:** `integrations/dropfans/service.py:3 Dropfans is the sole active external commerce/vault/payment provider. Fangate legacy-only.` `core/config` enc keys.

| Signal | Where | Creator scope | Observability | File:line |
|---|---|---|---|---|
| Check-status poll | `ddb.list_active_dropfans_products(creator) -> client.check_drop_status(product_ids)` batched + `_with_rate_limit_retries:744` -> `DropfansSaleStatus{product_id,paid,sale_amount_cents,buyer_email}:748` | per-creator client `creator_integrations.dropfans_creator_id:47` | logs sanitized via `sanitize_log_value`, no PII | `service.py:734` |
| Earnings reconciliation (authoritative amount) | `client.get_earnings(start_date,end_date,tz) -> {stats total_earnings_cents/gross/transaction_count/avg/type_totals, chart, transactions[{id,product_id,product_name,amount_cents,gross_amount_cents,buyer_email,buyer_name,paid_at,type}]}` | per-creator `_run_scoped` | transactions passed through, not logged | `service.py:611/651` |
| Sale record persistence | `INSERT fangate_transactions (creator_id,transaction_id,event_type=dropfans_sale,buyer_email,seller_earning:=sale_amount_cents/100,currency=USD,product_id=synthetic_pid,user_id nullable,occurred_at NOW()) ON CONFLICT (creator_id,transaction_id,event_type) DO NOTHING:217` id `dropfans:{sale_id}` else hash email+amount+paidAt | creator required; `ON CONFLICT` idempotent | `earnings.html` lists via `list_recorded_sales` | `db/dropfans.py:177` |
| Scheduled reconciliation | `scheduler_worker:161 -> commerce/reconciliation reconcile_all:293 -> reconcile_dropfans_sales 251` loops `list_active_creator_ids ACTIVE only:266` per creator + `reconcile_unattributed_purchases 38` batch 50 last 7d | per active creator isolated | logs `recorded N new:286` | `reconciliation.py:38/251` |

Unattributed->attributed: `SELECT fangate_transactions WHERE user_id IS NULL AND product_id IS NOT NULL AND created_at>=NOW()-7d LIMIT 50:55` -> `_reconcile_single:114` `SELECT commerce_offers WHERE creator=$1 AND product=$2 AND state IN (pending,clicked) ORDER BY created_at ASC:132` 0->retry later `144`, >1->fail-closed ambiguous `147`, =1-> UPDATE offer purchased + attach user_id + ppv_analytics_daily. `handle_post_purchase` best-effort.

**Gaps:** No realtime `commerce.offer.*` pubsub; `check-status` fallback hash without `paid_at` could conflate same buyer same amount different purchases within 7d -- `paid_at` hash mitigates `209-211` but `has_dropfans_sale_been_recorded f"dropfans:{dropfans_product_id}":247` legacy not per-sale.

## 9. Revenue Authority

**SOLE authority: DropFans earnings -> transaction -> sale. PROVEN. Must NOT be inferred from Qwen/commerce decision/PPV price/offer creation.**

- `fangate_transactions` `migrations/20260819000000_fangate_commerce.sql:72` `id BIGSERIAL, creator_id FK, transaction_id TEXT, event_type TEXT, buyer_email TEXT, seller_earning NUMERIC(12,2), currency TEXT, product_id BIGINT, set_price NUMERIC, occurred_at, UNIQUE (creator_id,transaction_id,event_type):85` + `ALTER ADD user_id FK users:52`. DropFans maps `seller_earning = sale_amount_cents/100.0`, `currency=USD hardcoded:222`.
- `ppv_analytics_daily` `migrations/20260819100000_ppv_intelligence.sql:30` `PK(creator_id,product_id,day) offers_created/clicked/purchased/declined/expired/revoked, revenue_minor BIGINT`. Increment via `commerce/dao.py:840 increment_analytics_counter whitelist` atomic `ON CONFLICT DO UPDATE col=col+EXCLUDED.col`. Revenue only on `purchased + revenue_minor>0:886`.
- `fangate_products.price_minor + sales_url` per product; offer snapshots price at creation.
- `fangate_wallet_entries` ledger separate (`migrations:92`) via `sync_wallet paginate <50 break:572-588`.

**Observability:** `db/dropfans.py:254 list_recorded_sales` + `299 sum_recorded_sales_cents SUM(seller_earning*100) WHERE event_type=dropfans_sale` + wallet. Dashboard `list_transactions:590` status `normalize_transaction_status` (`successful/failed/pending`).

**Currency:** DropFans hard USD (`db/dropfans.py:222`), Fangate `currency TEXT` from webhook + product `currency_code` from `creator_integrations.currency_code:102`. Offers store `currency` from product at execution (`execution.py:265`). No multi-currency normalization.

**Idempotency/duplicate:** `ON CONFLICT (creator,transaction,event_type) DO NOTHING` prevents double-claim; per-sale `dropfans:{sale_id}` id strongest; fallback `dropfans:{drop_id}:{emailHash8}:{amount}:{paidAtHash6}` (`db/dropfans.py:204-211`). `ppv_analytics_daily` revenue_minor increments idempotently via unique transaction check.

**Real-time revenue can be displayed safely:** `sum_recorded_sales_cents` per `creator_id` is creator-scoped, idempotent, already used in `vault/service.py:245` fallback. Not inferred. **PROVEN** but `amount_cents` includes `buyer_email` PII -- dashboard must not expose email, only aggregate `revenue_minor`.

**Gaps:** No persisted `total_earnings` rollup beyond daily; no `ltv` unless `purchase_amounts` supplied (`fan_value_model:779` UNKNOWN else sum); revenue not emitted to event bus.


## 10. Dashboard Inventory

### 10.1 Routes (20 routers `app.py:127-181` PROVEN)

`health, dlq, ws (/ws websocket), auth, users (/api/user/{user}, /api/users), messages (/api/recent, /api/search, /api/messages), queue (/api/queue/pending, /api/dialogs/{id}/suggestions), dialogs (/api/dialogs/{id}/messages), personas, settings (/api/stats pending_inbound/dlq, /api/settings/auto-reply), operators, export (/api/export/csv), analytics (/api/analytics/dashboard, /api/analytics/conversations), fangate, followups (/api/followups), bulk_ops, notes, tags, attention, search, ai_intel, pages (HTML pages), vault (12 endpoints), segments (prefix /api/segments fields/presets/toggle/preview/members/stats)`

### 10.2 Templates (26 PROVEN)

`base.html, analytics.html, vault.html, users.html, settings.html, segments.html, queue.html, profile.html, posts.html, personas.html, overview.html, notifications.html, login.html, links.html, followups.html, fangate.html, earnings.html, drops.html, dropfans_*.html, dlq.html, dashboard.html, creators.html, chat.html, chats.html, chat_embed.html`

### 10.3 Components / wiring

- `dashboard.html:12 <script realtime.js>` + `532 window.rt = new RealtimeClient()` + `dashboardApp()` sidebar collapse, `loadCounts /api/stats`, badges `queueCount/unreadTotal`, notification bell (polling, not push).
- `overview.html:246 var rt = new RealtimeClient()` + `loadStats immediate + setInterval 5000 if rt.isPollingActive()`, handlers `message.created->loadStats`, `message.sent->loadStats`, `operator_queue.updated->loadStats`.
- `chat.html:317 flex layout, suggestions bar, ai-native-bar`, `626 var rt`, handlers `message.created->appendMessage`, `message.sent->appendMessage aiMeta`, `ai.generation_started->spinner`, `generation_completed->score/routed/flags+loadSuggestions`, `generation_failed->error`, `message.send_failed->toast 770 showToast`, `suggestion.created->loadSuggestions`, `vault.media_sent->deliveredIds 1396`, polling `loadSuggestions 10000 if rt.isPollingActive()`.
- `chat_embed.html:161 rt`, `queue.html:103 rt.on operator_queue.updated/suggestion.created -> location.reload()`, `analytics.html:194 analyticsApp()` filter row, KPI grid, timeline canvas, handling breakdown, conversation table, polling `15000 if rt.isPollingActive()`.

### 10.4 Metrics views (EXISTS)

- `overview.html:186 KPI` stat-users/messages/queue/blocked + DLQ count via `settings.py:31 xlen`.
- `analytics.html:312 KPI` inbound/outbound/auto_approval_rate/failed(DLQ) + ai_generated/auto_approved/operator_approved/avg_response_time, timeline, handling breakdown, performance table with `attention_reasons, avg_confidence, queue_count`, detail modal.

### 10.5 Reusable vs missing

| Category | Status | Evidence |
|---|---|---|
| CURRENT DASHBOARD | EXISTS functional operator queue/chats/analytics/vault/ segments | 20 routers, 26 templates, Alpine components |
| AVAILABLE DATA | messages, operator_queue, users, analytics counts, vault deliveries, segments, generation_telemetry (if not rejected), metrics/audit mem, fangate_transactions | PG + Redis + mem |
| MISSING DATA | execution stage timeline, hot fan flag, PPV sent/clicked events, sale_lost, aftercare sent, revenue realtime, live fan presence | no column/event/metric |
| REUSABLE COMPONENTS | `RealtimeClient`, `ConnectionManager`, `event_subscriber` startup/shutdown, `isPollingActive` guard, `showToast`, `csv_helpers.sanitize_csv_value`, `segments/evaluator compile_rule` | PROVEN |
| SAFE EXTENSION POINTS | Add `event_type` handlers in `realtime.js:on`, add API `GET /api/generations/{generation_id}`, `GET /api/fans/{user_id}/timeline`, `GET /api/commerce/offers`, reuse `publish_event` best-effort, reuse `generation_id` seen lists | additive consumers only |

Dashboard **PROVEN** has no stage funnel visualization except `segments/fields.py:173 FunnelStage` filter and `memory/context.py:286 STATE {funnel_stage}` injected into Qwen prompt (`STAGE_GUIDANCE new/warming/engaged/converted`). `funnel_journey_by_creator` JSONB bounded 20 (`revenue_intelligence.py:221`) not rendered.

## 11. Real-Time Transport Inventory

### 11.1 What is present (PROVEN)

- **WebSocket:** `core/event_bus.py:10 CHANNEL chatbot:events`, `:13 publish_event` UUID event_id + timestamp_ms, `30 enable_websocket` kill-switch, `50 r.publish`, `52-54 warning never propagates`; `dashboard/ws_manager.py:18 ConnectionManager:28 connect accept, 40 disconnect, 45 broadcast with is_global+dialog_ids scoping, stale cleanup 72`, `:85 get_manager singleton`; `dashboard/event_subscriber.py:14 start_event_subscriber while True redis.from_url pubsub.subscribe CHANNEL, async for listen, json.loads, broadcast, backoff 1->30s:10, CancelledError break`; `dashboard/app.py:98 create_task(start_event_subscriber) if enable_websocket else log disabled`, `:112 shutdown cancel`; `dashboard/routes/ws.py:12 @router.websocket /ws cookie session verify_session close 4001 if missing, manager.connect is_global=True, ping/pong loop`.
- **Frontend:** `dashboard/static/js/realtime.js:19 RealtimeClient` MAX_RECONNECT 30000, INITIAL 1000, PING 30000, DEDUP 200, `_buildUrl wss/ws`, `onopen _connected true _pollingActive false _startPing`, `onmessage dedupCache indexOf, handler[event_type]`, `onclose 4001 auth_failed intentionalClose else scheduleReconnect` jitter 1000 exponential double, `onerror close`, `beforeunload close`, `isPollingActive()`.

### 11.2 What is absent (PROVEN)

- **Server-Sent Events:** 0 hits (`text/event-stream`, `EventSource`) -- missing.
- **HTMX:** 0 hits (`htmx`, `hx-`) -- missing.
- **Polling:** EXISTS correctly as fallback: `overview.html:307 setInterval loadStats 5000 if rt.isPollingActive()`, `chat.html:999 loadSuggestions 10000`, `analytics.html:1403 setTimeout/clearInterval + setInterval 15000 if isPollingActive`, `queue.html:113 location.reload if !isConnected`. Invariants `AGENTS.md:182 may pause when WS connected, 185 must resume when disconnected, 187 never remove polling` -- **PROVEN honored**.

### 11.3 Recommendation

Keep WebSocket+polling. Do NOT add new worker/queue. Smallest compatible push extension is adding commerce event types to existing `chatbot:events` channel (same `publish_event`), reusing `ws_manager`/`event_subscriber`/`RealtimeClient`. If WS disabled, polling remains. SSE not needed; HTMX not needed.

## 12. Notification Inventory

### 12.1 What exists

- **Toast:** `.toast + toast.success/error/info` + `<div id="toast">` + `t.className toast +type, setTimeout hide 3s` in `vault.html:94/592`, `posts.html:127`, `segments.html:126/973`, `notifications.html:72`; `chat.html:1043 showToast fixed bottom right opacity/transform transition 3s` used `770 showToast('Send failed:'+err,'error')` -- **PARTIAL** event->toast bridge only for `send_failed`.
- **Alert fallback:** `queue.html:118 alert Failed to send`, `profile.html:240 alert Message sent`, `analytics.html:951 alert timeline` -- poor UX but exists.
- **Header bell:** `dashboard.html:486 bell x-show showNotifications No new notifications` badge `notificationCount` from polling `/api/stats` pending_queue, NOT browser push.

### 12.2 What is missing (PROVEN)

- **Browser Notification API:** 0 hits `Notification`, `requestPermission`, `new Notification`, service worker push -- **MISSING**.
- **DropFans notifications** (`integrations/dropfans get_notifications`) are Telegram notification polling, unrelated to dashboard toast.
- Event->toast mapping missing for `suggestion.created`, `operator_queue.updated`, `ai.generation_failed` except inline `gen-status` bar; `queue.html:105` does `location.reload()` not toast.

### 12.3 Requirements for operator notifications

Notifications must be creator-scoped, deduplicated, idempotent, safe, non-blocking, never affect bot decision flow -- same constraints as event bus.

** taxonomy classification (see Section 20):**

Conversation AUTHORITATIVE: `message received` (message.created), `reply sent` (message.sent), `handoff` (get_handoff_memory active). DERIVABLE: `fan active/inactive` (derived thresholds). NOT CURRENTLY AVAILABLE: none.

Sales AUTHORITATIVE: `PPV authorized` (ppv_eligibility_decisions), `PPV sent` already inferrable but not evented, `purchase detected` (fangate_transactions INSERT), `sale completed` (commerce_offers purchased). DERIVABLE: `hot opportunity` (temperature HOT), `aftercare` (aftercare_status). NOT CURRENTLY AVAILABLE: `sale lost` (needs deterministic).

Operations AUTHORITATIVE: `send failure` (message.send_failed), `worker error` (generation_failed). DERIVABLE: `production_control pause` (is_commerce_paused, is_global_paused). NOT CURRENTLY AVAILABLE: none.

**Deduplication:** reuse `event_id uuid4` (`realtime.js:68 dedupCache 200`) + `generation_id` for commerce seen lists + `commerce_offer_id` + `external_transaction_id` (`fangate_transactions unique`). `generation_id` idempotency via `strategy_generation_seen_by_creator` cap 100 (`strategy_learning.py:250`), `evidence_generation_id` (`fan_knowledge.py:294`), `op_exec:{generation_id}:{rec}` lock (`operational_execution.py:26`).

Smallest safe extension: add `showToast` bridge in `realtime.js` or `chat.html/queue.html` subscribing to existing + new commerce events, with `event_id` dedup + `isPollingActive` check, creator-scoped via `dialog_id` filter already in `ws_manager`.


## 13. Creator Isolation Audit -- VERDICT: PARTIAL

### PASS -- commerce/vault/segments/integrations are strictly scoped

| Location | Evidence |
|---|---|
| vault | `db/vault.py:18,183 WHERE creator_id=$1 AND user_id=$2` |
| segments | `db/segments.py:46 WHERE creator_id=$1 AND id=$2`, header "All queries are creator-scoped" |
| fangate | `db/fangate.py:367 WHERE creator_id=$1 AND id=$2 comment never leak`, `485 ON CONFLICT (creator_id,transaction_id,event_type)` |
| dropfans | `db/dropfans.py:37 WHERE creator_id=$1`, `223 ON CONFLICT` |
| dao | `commerce/dao.py:88 WHERE creator_id=$1`, `82 pg_advisory_xact_lock hashtextextended ppv_offer:{creator}:{user}:{product}` |
| state | `commerce/state.py:149 _resolve_creator_relationship verifies get_creator` |
| tools | `core/llm_tools.py:420 ToolAuthContext.creator_id`, `433 _handle_get_purchase_history WHERE co.creator_id=$1` |
| automation | `db/automation.py:44 WHERE creator_id=$1 AND idempotency_key=$2` |

### FAIL -- core user/message/profile layer is global, leaks across creators

| Location | Risk |
|---|---|
| `db/schema.sql:7 users(id BIGINT PK)` | single PK Telegram user_id, no creator_id column, shared `funnel_stage,message_count,last_seen,is_blocked,persona_id,notes,do_not_auto_reply` last-write-wins |
| `db/schema.sql:21 messages(user_id FK)` | no creator_id, `idx_messages_user_id_created` global |
| `db/schema.sql:57 user_profiles(user_id PK, facts JSONB)` | global row, namespaced keys `commercial_preferences_by_creator` (`db/postgres.py:402`) mitigates but single row contention `FOR UPDATE` on same row |
| `db/postgres.py:118 get_user WHERE id=$1`, `360 get_recent_messages WHERE user_id=$1`, `377 get_user_profile WHERE user_id=$1` | all user_id only |
| `commerce/single_creator.py:57 resolve_single_application_creator ORDER BY creator_id LIMIT 1` | picks lowest creator_id, multi-creator silently disables commerce rather than isolates |
| Dashboard reads | `dashboard/routes/users.py:61 SELECT ... FROM users ORDER BY last_seen DESC LIMIT`, `42 SELECT ... FROM users ORDER BY`, `dialogs.py:57 SELECT ... FROM messages WHERE user_id=$1`, `messages.py:143 SELECT m.id,m.user_id,content FROM messages ORDER BY created_at DESC LIMIT 20` last 20 of entire platform -- **no creator filter** -- FAIL |
| Failure: `db/postgres.py:751 get_dashboard_analytics FROM messages WHERE TRUE {date_filter}` | no creator_id, platform-wide |

**Mitigations present but insufficient:** `get_commercial_preferences(creator_id,user_id) by_creator[str(creator_id)]` (`db/postgres.py:402`), `add_knowledge_item SELECT ... FOR UPDATE` per fan, `long_term_memory_by_creator` 20, Redis lock `lock:creator:{creator}:user:{user}` (`db/redis.py:267`) -- but `debounce:{user_id}`, `ratelimit:{user_id}`, `context:{user_id}` remain global.

**Cross-creator leakage scenario:** Same Telegram `user_id=123` chats with Creator A and B: `get_recent_messages(123,20)` interleaves both creators messages into Qwen context leak; `users.funnel_stage=converted` greets new creator as repeat buyer. **PROVEN likely**.

Global metrics 5000 not per-creator unfair eviction (`production_control.py:46 _METRIC_MAX 5000 prune 1000`, `docs/PHASE_31_FORENSIC_AUDIT.md:635`).

## 14. Fan Isolation Audit -- VERDICT: FAIL

Same root cause: `users.id PK` single row, `messages.user_id FK`, `user_profiles.facts` single JSONB, `conversation_summaries.user_id`, `operator_queue.user_id`, `conversation_notes.user_id` -- all **no creator**. A note "VIP whaler for Creator A" visible when Creator B views same `user_id`. Vault `vault_media_deliveries` correctly `WHERE creator_id=$1 AND user_id=$2` isolated but join `LEFT JOIN users u ON u.id=vmd.user_id` leaks global username.

Redis `debounce:{user_id}` and `context:{user_id}` global; only lock is creator-scoped. Two creators concurrent writes contend on same `user_profiles` row `FOR UPDATE`.

No fix without `fan_identities(creator_id,user_id)` composite or scoped view. Current mitigations are JSON namespacing within one global row, not row-level tenant isolation. **PROVEN FAIL** for multi-creator future; acceptable only if prod remains single-creator (current `single_creator.py` assumes 1 active).

## 15. Privacy Audit -- VERDICT: PARTIAL

### PASS

- Credentials Fernet AES-128-CBC+HMAC-SHA256 (`integrations/fangate/security.py:31 derive_fernet_key SHA256->base64url`, `get_vault` from `FANGATE_ENC_KEY`/`DROPFANS_ENC_KEY`), ciphertext-only storage `creator_integrations.encrypted_api_key`, never returned via `list_integration_statuses SELECT creator_id,status,last_success_at` (`db/fangate.py:211`), memory-only `Authorization: Bearer {api_key}` (`client.py:92`), `Field(repr=False)` on enc keys (`core/config.py:54`).
- Evidence payment guard `commerce/signals.py:128 _CARD_NUMBER_RE \b\d{13,16}\b`, `140 _contains_payment_data cvv|cvc|pan:`, validator rejects -- prevents LLM `evidence` echoing card.
- CSV injection sanitized `dashboard/csv_helpers.py:12 sanitize_csv_value prefixes =+-@ with '"`.
- LLM context explicitly excludes buyer email/transaction/api keys `memory/context_assembler.py:29 comment NO SECRETS`.

### PARTIAL/FAIL

- **Buyer email:** NOT in `core/logging_config.py:13 _SENSITIVE_PATTERNS ("api_key","token","password","secret","session","authorization","dsn")` -- can flow into structured logs. Stored plaintext `fangate_transactions.buyer_email TEXT`, visible in `earnings.html:186 tx.buyer_email`, returned via `db/dropfans.py:277 list_recorded_sales buyer_email` and `db/fangate.py:508`, not added to redaction. Logged as `txn id` only in reconciliation but not enforced. **P2**.
- **Message content:** intentionally full-exposure to dashboard operators (`dialogs.py:57`, `messages.py:143` raw content) -- acceptable for trusted operator but must be documented; `search_messages` trgm ranking returns raw content. `ai.generation_started preview 100` bounded is safe. No per-message redaction.
- **Log redaction:** `sanitize_log_value` dict-only, misses nested, misses `buyer_email/email/phone`, `_sanitize` in `integrations/**/security.py:99 return record` dead no-op (`docs/DROPFANS_MIGRATION_FORENSIC_AUDIT.md:123`).
- **Tokens:** `core/config.py:14 telegram_token` not `repr=False` could leak if Settings printed (minor).

**Minimum safe real-time payload:** `{event_id, event_type, timestamp_ms, creator_id?, user_id, dialog_id, generation_id, scope, data: {message_id?, queue_id?, score?, flags?, routing_decision?, objective?, desire_stage?, temperature?, lifecycle_state?, amount?, currency?, product_id?, dedup_id?}}` -- never `buyer_email`, `encrypted_api_key`, `session`, `api_key`, full `content` unless `message_preview 100` with explicit opt-in. Reduce `message.created/sent` payload to preview in realtime path, keep full fetch via authenticated `GET /api/dialogs/{id}/messages`.

## 16. Performance Audit -- VERDICT: PARTIAL

| Category | Evidence | Verdict |
|---|---|---|
| Vector search O(N) Python loop | `db/postgres.py:598 vector_search_messages WHERE e.user_id=$1 then scored.sort Python cosine no LIMIT` | FAIL latent |
| N+1 per segment | `dashboard/routes/users.py:138 for seg in segments: await conn.fetchval SELECT EXISTS(... WHERE u.id=$uid_idx AND {where_clause})` -- 1 per segment per user; batch `200-219 for seg: for uid: fetchval` S*N up to 2000 | FAIL |
| Per-product title N+1 | `commerce/dao.py:1029 for pid in product_ids: fetchrow SELECT title FROM fangate_products WHERE id=$1 AND creator_id=$2` | FAIL |
| Unbounded JSONB | `commercial_preferences_by_creator` dict grows per product token no cap (`db/postgres.py:420`), `messages` never pruned | PARTIAL |
| Bounded correctly | `fan_knowledge_by_creator` 30 (`fan_knowledge.py:266`), `long_term_memory_by_creator` 20, `PROFILE_LIST_CAP 15` (`profile.py:61`), `MAX_CONTEXT_MESSAGES 30` (`config.py:72`), `list_fangate_products limit 200` (`product_selection.py:112`), `DLQ list 50/count 500` | PASS |
| Full table scans | `get_dashboard_analytics GROUP BY DATE(created_at) WHERE TRUE {date_filter}` scans entire messages if start_date null (`postgres.py:842`), `get_user_analytics COUNT(*) FROM users JOIN (GROUP BY)` per user view (`687`) | PARTIAL |
| Global metric 5000 not per-creator | `commerce/production_control.py:46 5000 prune 1000 oldest`, cross-creator unfair eviction, not isolated | PARTIAL |
| Context assembly 3 reads/turn | `_get_purchases_safe + _get_active_offers_safe + get_user_profile` per turn -- acceptable, not batched | LIKELY fine |

**Cost for 1/100/1000/10000 active fans:** generation hot path is 3 bounded reads + 1 signal + 1 Qwen + 1 scoring + 2 Redis streams + 1 debounce; scales linearly with fans, not fan count. Risk is analytics/dash queries scanning `messages` globally for 10k fans (timeline GROUP BY). Segment batch S*N is main dashboard perf risk.

## 17. Failure Behavior -- VERDICT: PARTIAL

### Bot must continue if observability fails -- honored for commerce, not for dashboard/worker pending.

| Component unavailable | Behavior | File:line | Verdict |
|---|---|---|---|
| Dashboard down | workers publish best-effort, `publish_event` never propagates, polling remains, no business logic depends on ws_manager invariant `AGENTS.md:190` | `event_bus.py:52`, `llm_worker.py:508 only core/event_bus` | PASS |
| Event publication fails | `core/event_bus.py:53 warning return None`, `llm_worker.py:1422 try publish ai.generation_failed except warning` -- not propagates, business continues | `42-54`, `1424` | PASS |
| Postgres down -- commerce | `commerce/state.py:455 resolve_commerce_state except -> CREATOR_CONTEXT_UNAVAILABLE`, `product_selection.py:113 except return None`, `dao.py:475 get_timing_context return neutral`, `memory/context_assembler:122 _get_*_safe try return default` -- commerce degrades to standard LLM, not crash | `455`, `113`, `122` | PASS |
| Postgres down -- dashboard | `users.py:60 fetch` no try, `dialogs.py:41 fetch` no try, `messages.py:84 fetch` no try -> 500; only `analytics.py:41 try HTTPException 500` has handler `check_migrations_pending:68 except return up_to_date True` hides DB down as healthy | `users.py:60`, `postgres.py:68` | FAIL |
| Postgres down -- worker | `llm_worker.py:550 acquire_user_lock outside try`, `559 upsert_user` no try -> exception bubbles to `move_to_dlq payload dict(data) keep payload` but `ack` not yet -> pending stuck, reclaimed via XAUTOCLAIM 30s tight loop | `550`, `509` | PARTIAL |
| Redis down -- lock/debounce | `redis.py:272 set nx ex` no try in caller, `enqueue_inbound xadd` no try in caller -> exception crashes turn; `requeue_stalled_messages 211 except ResponseError return 0,[]` PASS, `move_to_dlq 261 leave pending for retry` PASS vs `move_send_to_dlq 133 always ACK even if DLQ write fails` divergence | `272`, `211`, `261 vs 133` | PARTIAL |
| Rate limit Redis down | Lua `check_send_rate_limit 420` exception -> no rate limit -> spam risk | `420` | PARTIAL |
| Redis Pub/Sub down | event_bus warning, subscriber `event_subscriber.py:70 warning reconnect backoff 1->30s, unsubscribe/close` | `70` | PASS |

**Coupling violations:** None where business depends on WS delivery. One borderline: `_skip_qwen_due_to_pause` (`llm_worker.py:971`) correctly saves Qwen but audit must verify dashboard `production_control pause` toggle cannot accidentally gate commerce (it is fail-closed, requires explicit `autonomous_allowed`).


## 18. Historical Reconstruction

| Window | What survives | What is lost | After what | File:line |
|---|---|---|---|---|
| 1 hour | All `messages` (indefinite), `operator_queue`, `commerce_offers`, `fangate_transactions`, `generation_telemetry` (if not rejected), `metric _metric_events 5000` window H1 3600, `audit 1000`, `ws dedup 200` | Pub/Sub events transient, telemetry cache popped, recent 100 strategies | restart | mem lost, PG survives |
| 24 hours | Same + `ppv_analytics_daily` per day | metric H24 86400 with 5000 cap evicts oldest across creators | restart/XAUTOCLAIM | XB: `production_control.py:46 prune 1000` |
| 7 days | Same + `DLQ 7d retention 604800` (but `cleanup_expired_dlq_entries 524` not auto-called, DLQ unbounded until cron) | `reconcile_unattributed 7d window LIMIT 50` beyond 7d cannot attribute | XAUTOCLAIM | DLQ caller must invoke |
| 30 days | `messages` indefinite, `tool_audit_log` 30d query window `dao.py:562`, `strategy evidence 30d prune` `adaptive_optimization.py:1091`, `metrics D30 2592000` | global 5000 metrics insufficient for 30d at high throughput; `user_profiles.facts` bounded 20/30 per creator but grows unbounded with creator count | worker crash | `is_fan_manipulation_attempt` etc survive only if PG write succeeded |
| After restart | PG `users/messages/commerce_offers/fangate_transactions/generation_telemetry` survive; Redis streams `inbound_messages/send_messages` PEL survives if Redis persistence (AOF) enabled, consumer group `id=0` recreates; Pub/Sub transient does not | `record_metric 5000` mem lost until `load_persisted_state` (`llm_worker.py:1454`) reloads via sentinel `-999997 metrics_by_creator` 200 last (`production_control.py:537`); `_audit_log` 1000 lost; `_telemetry_cache` lost; `dedup 3600` lost (duplicate sends risk) | `llm_worker.py:1450 ensure_consumer_group BUSYGROUP` + `write_heartbeat` | `production_control: load_persisted_state` best-effort |
| After XAUTOCLAIM | Stalled `inbound_messages` reclaimed via `xautoclaim idle 30s count 10` preserves `generation_id` fields; DLQ `payload=dict(data)` preserves | nothing lost, but duplicate LLM run with same generation_id possible | `db/redis.py:211`, `llm_worker.py:1492` | pending PEL not ACKed |
| Permanently persisted vs transient | **Persistent:** `messages`, `operator_queue`, `users`, `user_profiles`, `commerce_offers`, `fangate_transactions/products`, `ppv_analytics_daily`, `generation_telemetry` (if UUID fixed), `tool_audit_log`. **Transient:** `chatbot:events` Pub/Sub, `generation_telemetry` cache, `_metric_events`, `_audit_log`, ` dedup setex`, `rate limit ZSET`, `debounce`, WebSocket connections | `span` not applicable | `verify_schema` `postgres.py:39` | `schema.sql`, migrations |

**Verdict:** Complete fan timeline can be reconstructed after 7/30 days from PG audit log **except** generation detail lost due to UUID bug and `message.created->generation` not joined. Fix UUID + add generation_id to send stream restores full timeline.

## 19. Master Findings Table

| ID | Severity | Title | File:function:line | Evidence | Confidence |
|---|---|---|---|---|---|
| F-01 | P0 | generation_telemetry.generation_id UUID rejects md5, telemetry silently dropped | `db/migrations/20260828040000_generation_telemetry.sql:6` vs `workers/llm_worker.py:511 md5 32 hex` + `db/postgres.py:2744 insert` + `2720` | warning `generation_telemetry insert failed` | PROVEN |
| F-02 | P0 | generation_id not propagated to send stream/message.sent, end-to-end trace broken | `db/redis.py:65 enqueue_send no generation_id`, `chatbotv2/main.py:343 publish message.sent no generation_id`, `workers/llm_worker.py:1271` | grep generation_id 0 hits in redis enqueue_send, main publish | PROVEN |
| F-03 | P0 | Core users/messages/user_profiles global, cross-creator leakage, dashboard global feeds | `db/schema.sql:7 users PK`, `21 messages`, `57 user_profiles`, `db/postgres.py:360 get_recent_messages`, `dashboard/routes/messages.py:143 api_recent_messages LIMIT 20 global` | no WHERE creator_id | PROVEN |
| F-04 | P1 | message.created missing generation_id, debounce collapses N inbounds to 1 generation lost | `chatbotv2/handlers.py:66 publish message.created no generation_id`, `108 _wait_and_process latest=debounced[-1]`, `129 enqueue_inbound only latest attains generation_id` | latest only PROVEN | PROVEN |
| F-05 | P1 | No canonical persisted execution stage automaton; LifecycleState/FunnelState derived transient | `commerce/conversation_operations.py:32 LifecycleState 15`, `core/conversation_state.py:26 3`, `revenue_intelligence.py:111 14`, `pipeline_stage 0 hits`, `automation/models.py:921 is_valid_transition only for automation` | `derive_lifecycle` pure no transition table | PROVEN |
| F-06 | P1 | PPV sent not observable (no sent_at, no commerce.offer.sent event), clicked not metered | `commerce_offers` schema no sent_at (`20260819010000:7`), `execution.py:311 EXECUTED no publish`, `dao.py:629 mark_offer_clicked no metric/event` | no sent_at column, no publish commerce.* | PROVEN |
| F-07 | P1 | Sale-lost has no authoritative state/metric/event; dashboard would invent | `commerce/models.py:15 OFFER_STATES`, `production_control metrics rejections` but no sale_lost, `reconciliation:38 0->retry >1->ambiguous` | `rg sale_lost 0 hits` | PROVEN |
| F-08 | P1 | Aftercare sent not persisted (pending->completed only) | `commerce/dao.py:935 pending`, `962 completed WHERE IN (pending,sent)`, `scheduler_worker:114 mark_aftercare_completed` no sent write | gap | PROVEN |
| F-09 | P1 | Fan live-state not stored/evented; active/hot must be derived, hot not filterable | `temperature.py:31 HOT >=0.65`, `segments/fields.py:173 FunnelStage only`, no has_active_offer filter in realtime | `funnel_stage new at 47 msgs` audit | PROVEN |
| F-10 | P1 | Dashboard global analytics/users/messages feeds lack creator scope | `dashboard/routes/users.py:61 ORDER BY last_seen`, `postgres.py:751 get_dashboard_analytics FROM messages WHERE TRUE`, `messages.py:143` | no creator_id | PROVEN |
| F-11 | P1 | Single-creator resolver ORDER BY creator_id LIMIT 1 silently disables multi-creator | `commerce/single_creator.py:57 get_any_creator_id_with_dropfans ORDER BY creator_id LIMIT 1`, `single_creator.py:70 READY only if exactly 1 active` | fail-closed but not isolated | PROVEN |
| F-12 | P2 | Metrics global 5000 not per-creator unfair eviction, audit 1000 ephemeral | `production_control.py:46 _METRIC_MAX=5000 prune 1000`, `624 _AUDIT_MAX=1000` | AGENTS.md warning | PROVEN |
| F-13 | P2 | offer.clicked/purchased/revenue/aftercare not emitted to event bus | `core/event_bus` contract lacks commerce.*, `commerce/dao.py:350 ppv_analytics_daily revenue_minor only` | 0 commerce publish hits | PROVEN |
| F-14 | P2 | Notification bridge partial (only send_failed->toast, reload elsewhere), no browser Notification, no duplicate suppression beyond event_id 200 | `chat.html:770 toast`, `queue.html:105 reload`, `realtime.js:68 dedupCache 200` | 0 Notification API | PROVEN |
| F-15 | P2 | Buyer email not redacted in logs, stored plaintext visible in dashboard | `core/logging_config.py:13 _SENSITIVE_PATTERNS` misses buyer_email, `db/fangate.py:508 buyer_email`, `earnings.html:186` | `DROPFANS_MIGRATION:431` | PROVEN |
| F-16 | P2 | N+1 segment batch S*N up to 2000, vector O(N) Python scan | `dashboard/routes/users.py:200 for seg in segments: for uid: fetchval`, `db/postgres.py:598 vector_search_messages` | proven loops | PROVEN |
| F-17 | P2 | DLQ retention 7d not auto-cleaned, unbounded | `core/config.py:48 dlq_retention_seconds 604800`, `db/redis.py:524 cleanup_expired_dlq_entries` not auto-called | manual only | PROVEN |
| F-18 | P2 | DRAFT_STREAM draft_messages dead code | `db/redis.py:16 DRAFT_STREAM` never used | grep 0 writes | PROVEN |
| F-19 | P3 | Stale generation test expects unique ids under deterministic md5 | `tests/test_phase1_regression.py:997 test_generation_id_unique_per_process_message` expects 3 distinct for same args | fails with md5 | PROVEN |
| F-20 | P3 | Tool audit table unbounded, commercial_preferences dict unbounded | `db/postgres.py:402 by_creator[creator_id]=preferences` no cap, `tool_audit_log` no prune | LIKELY | PROVEN |
| F-21 | P3 | Redis locks global vs creator-scoped divergence (debounce/ratelimit/context global, lock creator) | `db/redis.py:267 lock:creator`, `306 debounce:{user_id}:lock` | global keys | PROVEN |
| F-22 | P3 | Scheduler generation_id not md5, breaks invariant | `scheduler_worker.py:227 scheduler-{cid}-{ts}-{rec}` | NOT md5 | PROVEN |


## 20. Stage B Implementation Map

For every change: file, function, current, problem, exact change, why, data source, idempotency, creator isolation, failure, tests.

### C0 -- P0 data-integrity gates (must land before any dashboard promotion)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C0-1 | `db/migrations/20260828040000_generation_telemetry.sql` + `db/postgres.py:2720 insert_generation_telemetry` | generation_telemetry.generation_id UUID | md5 32 hex rejected, PG error swallowed | ALTER generation_telemetry.generation_id TYPE TEXT (or ADD TEXT column md5 + keep UUID for uuid4 path) + insert accepts both; or store uuid5(md5) dash format | Restore telemetry for all auto generations, unlock historical reconstruction | `workers/llm_worker.py:511 md5` | UNIQUE(generation_id TEXT) + fact generation_id already unique per logical inbound | NOT scope-specific, but adds `creator_id` index already | best-effort `except warning` keeps | `tests/test_phase1_regression.py:818` should pass, new `test_generation_telemetry_md5_inserts` |
| C0-2 | `db/redis.py:65 enqueue_send` + `chatbotv2/main.py:343 publish message.sent` + `workers/llm_worker.py:1271` | send stream/data without generation_id | end-to-end trace broken, dashboard cannot join generation->sent | `enqueue_send(message_data, dedup_id, generation_id=None)` adds `data["generation_id"]=generation_id if given`; `llm_worker.py:1271 pass generation_id=generation_id`; `main.py:343 publish_event("message.sent", data, generation_id=data.get("generation_id"), scope="user")` same for `send_failed:382` + `vault.media_sent:352`; include `dedup_id` also | Restore single stable id across `message.created->generation->send`; variant uses same md5 as inbound | inbound `generation_id` already in `data["generation_id"]` via `enqueue_inbound` | `data.get("generation_id")` same md5, event_id uuid4 | `generation_id` already creator+user scoped via user key, forward unchanged | `publish_event` best-effort guard, never raises | `test_enqueue_send_before_generation_completed` + new `test_send_correlation_generation_id` |
| C0-3 | `chatbotv2/handlers.py:66 message.created` + `chatbotv2/handlers.py:81 debounce_enqueue` | message.created/event without generation_id, debounce loses | N inbounds only latest attains generation_id | Compute `gid = md5(user:content:telegram_message_id)` inline same as `llm_worker.py:511` before `publish_event("message.created", ..., generation_id=gid, scope="user")`; include `message_id, telegram_message_id` already; optionally store `generation_id` in `debounce messages` list for discarded audit | Complete timeline from first Telegram hit | `event.message.message/id` | deterministic md5, same as enqueue_inbound fallback -- duplicate publish safe via event_id uuid4 | `user_id` already | `publish_event` best-effort | `test_message_created_carries_generation_id` |

### C1 -- Commerce observability (additive, best-effort, no new queue)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C1-1 | `commerce/execution.py:311` + `commerce/dao.py:57` | offer created silent | PPV sent not observable | After `create_offer_serialized` returns `offer.id`, `try: await publish_event("commerce.offer_created", {offer_id, product_id, price_minor, currency, creator_id}, user_id, dialog_id=user_id, generation_id=current_generation_id, scope="user") except: log warning` -- capture generation_id from `commerce/state.py` bridge via `ContextVar` or pass-through | Operator sees PPV authorized/created realtime | `commerce_offers` row | offer_id PK idempotent, generation_id shared | `creator_id` in payload + scope user | best-effort | `test_commerce_offer_created_event` |
| C1-2 | `commerce/dao.py:629 mark_offer_clicked` + `commerce/dao.py:647 mark_purchased` | clicked/purchased silent | No funnel | Best-effort `publish_event("commerce.offer_clicked"/"commerce.offer_purchased", {offer_id, product_id, transaction_id, amount, generation_id}, ... scope=user)` after `UPDATE RETURNING` success | Funnel `created->sent->clicked->purchased` | `commerce_offers` UPDATE RETURNING | `UPDATE WHERE state=pending/clicked` already idempotent, event_id uuid4 | `WHERE creator_id=$1 AND id=$2` | best-effort | `test_offer_purchased_event` |
| C1-3 | `db/dropfans.py:177 record_dropfans_sale` + `commerce/reconciliation.py:210` | sale recorded silent except log | Revenue not push | After `INSERT ON CONFLICT DO NOTHING` where `xmax=0` (inserted), best-effort `publish_event("commerce.sale_recorded", {transaction_id, product_id, amount_cents, currency=USD, creator_id, user_id?, attributed}, generation_id=offer.generation_id if known, scope="user" or "global")` sanitize amount not email | Revenue generated notification | `fangate_transactions` | `ON CONFLICT` idempotent, transaction_id unique | `WHERE creator_id` | best-effort | `test_sale_recorded_event` |
| C1-4 | `commerce/dao.py:935/962 mark_aftercare_pending/completed` | aftercare silent | Cannot distinguish purchase aftercare | After `UPDATE RETURNING` publish `commerce.aftercare_pending/completed` best-effort with `offer_id, aftercare_status` scope=user | Sale aftercare notification | `commerce_offers.aftercare_status` | UPDATE idempotent | creator,user | best-effort | `test_aftercare_event` |
| C1-5 | `commerce/dao.py:299 record_offer_transition` | ppv_analytics_daily incremented but not push | Operational warnings not push | Keep increment; optionally publish `commerce.analytics_increment` only if operator wants, else derive via transactions -- no push needed | Avoid duplicate truth | `ppv_analytics_daily` | `ON CONFLICT` atomic | PK includes creator_id | best-effort | - |

All C1 events reuse `core/event_bus.py:13` (never import ws_manager), carry `event_id uuid4`, `generation_id md5` when inbound-correlated else `offer_id` correlation, scope user for per-fan privacy, `event_bus.enable_websocket` guard.

### C2 -- Execution stage surface (derived, not invented)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C2-1 | new `core/execution_stage.py:10 derive_execution_stage(generation_id, telemetry_row, messages, offers, transactions)` | no canonical display stage | Dashboard cannot show deterministic stage | Pure function mapping `telemetry.routing_decision, scoring_flags, operation_block_reason, messages.direction, commerce_offers.state, fangate_transactions` -> `RECEIVED/DEBOUNCED/QUEUED/PROCESSING/CONTEXT/PERSONALIZATION/SIGNALS/DECISION/CONTROL_GATE/QWEN/SCORING/AUTHORIZATION/DRAFT/SEND_QUEUE/SENDING/SENT/OUTCOME/COMMERCE/AFTERCARE/ERROR/WAITING` with timestamp from `timestamp_ms` or PG `created_at`; no DB writes | Observability without new decision engine | `generation_telemetry`, `messages`, `commerce_offers`, `fangate_transactions` | deterministic per inputs, no side effects | filter by creator_id where needed | pure, never raises | `test_execution_stage_derivation` pure |
| C2-2 | `chatbotv2/dashboard/routes/analytics.py` + new `dashboard/routes/generations.py` | no per-generation API | Cannot poll fallback detail | Add `GET /api/generations/{generation_id}` returning `telemetry_row + stage + decision_trace + events for generation_id` (joins `generation_telemetry WHERE generation_id=$1 AND creator_id=$2` if supplied), and `GET /api/fans/{user_id}/timeline?creator_id=&limit=` returning `generation_telemetry` + `messages` + `commerce_offers` in `created_at DESC` order, capped 50 | Historical reconstruction + live timeline | `generation_telemetry`, `messages`, `commerce_offers` | read-only, idempotent | `WHERE creator_id=$1` when single-creator resolved, else require explicit `creator_id` query param -- never return cross-creator (filter in SQL) | `try except 500`, never breaks generation | pagination tests |
| C2-3 | `core/telemetry.py:184 to_dict` + `db/postgres.py:2720 insert` | Phase 20/21/25 fields have no SQL columns | Cannot query desire/temperature/lifecycle/funnel | Extend `generation_telemetry` via bounded JSONB `extra JSONB` or add `TEXT` columns `desire_stage, temperature, lifecycle_state, funnel_state` (migration additive, nullable) -- smallest: add `extra JSONB` and store `to_dict` extras there; queries use `extra->>'funnel_state'` | Dashboard can filter hot fans without scan | `GenerationTelemetry.to_dict()` | upsert idempotent | creator_id column already | best-effort insert | migration test |

### C3 -- Fan live-state and hot fan (derived views, not new state)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C3-1 | `chatbotv2/dashboard/routes/users.py` new `GET /api/fans/live?creator_id=` | no live fan API | Cannot show active fans safely | View computes `SELECT user_id, MAX(created_at) last_msg FROM messages WHERE direction=inbound AND creator-scoped via EXISTS (SELECT 1 FROM commerce_offers WHERE commerce_offers.user_id=messages.user_id AND commerce_offers.creator_id=$1) GROUP BY user_id HAVING MAX(created_at) >= NOW()-interval` via creator-scoped join -- but until `messages.creator_id` exists, derive via `commerce_offers` existence per user; also count `pending offers` via `commerce_offers` per-creator, `operator_queue pending`, `temperature HOT` via `generation_telemetry.extra->>'temperature'='hot'` last 1h | Active/hot fans per creator without cross-leak | `messages`+`commerce_offers`+`generation_telemetry` | read-only | `WHERE creator_id=$1` everywhere | `try except` returns `[]` on DB down | `test_live_fans_creator_scoped` with two creators same telegram id |
| C3-2 | `segments/fields.py` | no temperature/desire filter | Hot fan not segmentable | Add `CommercialTemperatureField`, `DesireStageField` evaluating against `generation_telemetry` last row `extra->>'temperature'` or `commerce/temperature` derived via `get_timing_context`+`get_behavioral_feedback_context` -- reuse `derive_commercial_temperature` pure from PG reads (best-effort 3 reads, cached 30s) | Operator can filter hot fans without raw SQL | same 3 reads as llm_worker | deterministic | WHERE creator_id | read-only | segment eval test |
| C3-3 | `chatbotv2/dashboard/static/js/realtime.js` + `chat.html` | hot fan transition not push | No hot fan notification | When `commerce.temperature HOT` derived on server, publish `commerce.hot_fan` (derived, not stored) best-effort from `llm_worker.py:730` after `derive_commercial_temperature` if `HOT and previous not HOT` (check `get_temperature_last` sentinel) -- optionally derived client-side from `funnel_state` event -- dedup via `hot_fan:{creator}:{user}:{hour}` | Operator notification on authoritative transition | `temperature.score` | sentinel `hot_fan:{creator}:{user}:date` Redis setex 3600 dedup | creator+user key | best-effort | `test_hot_fan_dedup` |

### C4 -- Revenue and sale-lost (read-only derivations, no inference)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C4-1 | `chatbotv2/dashboard/routes/stats.py` (new or extend settings) | no revenue endpoint creator-scoped | Cannot show revenue realtime safely | `GET /api/revenue/summary?creator_id=&from=&to=` `SELECT COALESCE(SUM(seller_earning*100),0) revenue_minor FROM fangate_transactions WHERE creator_id=$1 AND event_type='dropfans_sale' AND occurred_at BETWEEN $2 AND $3` + daily breakdown via `ppv_analytics_daily WHERE creator_id=$1` -- never from Qwen | Revenue generated must be funnel truth | `fangate_transactions` + `ppv_analytics_daily` | sum idempotent, unique transaction guard | WHERE creator_id | returns 0 on DB down | `test_revenue_creator_scoped sum idempotent` |
| C4-2 | `commerce/revenue_intelligence.py:306 compute_conversion_metrics` | no sale_lost computed | Cannot notify lost sale | Add `abandoned = SELECT COUNT(*) FROM commerce_offers WHERE creator=$1 AND state IN (declined,expired) AND created_at >= NOW()-interval` + `pending_age = SELECT COUNT(*) WHERE state=pending AND created_at < NOW()-48h` as `sale_lost_potential` -- never publish sale_lost event until explicit `declined/expired` terminal; dashboard shows `pending_stale` not fake lost | Avoid inferring sale merely because PPV sent | `commerce_offers` | count read-only | WHERE creator_id | read-only | `test_sale_lost_counts` |

### C5 -- Redis architecture guard (observer must not interfere)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C5-1 | `docs` + `dashboard/routes/analytics.py` | dashboard could XREADGROUP/ACK | Risk of consuming production messages | Document + lint: dashboard observers use `XRANGE/XREVRANGE/XLEN/XINFO_STREAM` read-only (`db/redis.py:477,493,472`), never `XREADGROUP`, `XACK`, `XAUTOCLAIM`, `XGROUP_CREATE`, `XDEL` for streams `inbound_messages/send_messages`; DLQ replay uses `replay_dlq_entry` with its own lock (`dlq_replay_lock 30s:603`) and re-enqueues via `enqueue_inbound/send` not via consuming. Enforce via `rg XREADGROUP` in CI for `dashboard/` | Protect production consumers | `inbound_messages` vs `dead_letter_queue` | XLEN/XRANGE read-only | N/A | read-only always | `test_dashboard_never_ack` |
| C5-2 | `db/redis.py:524 cleanup_expired_dlq_entries` | retention not auto | DLQ unbounded | Call `cleanup_expired_dlq_entries` from `scheduler_worker:161 reconcile loop` or `dashboard/app.py startup` daily cron | Bounded history | `DLQ_STREAM` | cutoff_id `int(time-ok)*1000-0` idempotent | N/A | best-effort | `test_dlq_cleanup_idempotent` |

### C6 -- Privacy and isolation hardening (scoping)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C6-1 | `core/logging_config.py:13 _SENSITIVE_PATTERNS` | misses buyer_email | PII in logs | Add `buyer_email`, `email`, `phone` to tuple + apply recursively to nested dicts/lists | Redact PII | logs | N/A | N/A | N/A | `test_logs_redact_buyer_email` |
| C6-2 | `chatbotv2/dashboard/routes/messages.py:143` + `users.py:61`, `dialogs.py:42`, `postgres.py:751` | global feeds | Cross-creator leak | Gate behind `resolve_single_application_creator()` when single-creator deployed: `creator_id = (await resolve...).creator_id if READY else None`; if `creator_id` then `AND EXISTS (SELECT 1 FROM commerce_offers co WHERE co.user_id=messages.user_id AND co.creator_id=$creator_id)` or `AND messages.user_id IN (SELECT user_id FROM commerce_offers WHERE creator_id=$1 UNION SELECT user_id FROM operator_queue oq JOIN commerce_offers...)` -- or short-term `require query param creator_id` and filter all 4 routes. Document global users/messages not creator-scoped until `messages.creator_id` migration (future) | Enforce isolation with current schema | `commerce_offers` bridge | read-only | WHERE creator_id via EXISTS | returns `[]` on ambiguous | `test_dashboard_creator_isolation_same_telegram_id` |
| C6-3 | `chatbotv2/dashboard/routes/analytics.py` | returns raw content to any authed | Message content exposure | Keep for trusted operators but add `content_preview 100` for realtime path + require `permission=messages:read` and audit log via `tool_audit_log` insert on bulk fetch | Privacy minimized | `messages.content` | N/A | auth required | 403 on no auth | `test_analytics_requires_auth` |

### C7 -- Observability transport final (no new infra)

| # | file | function | current | problem | exact change | why | data source | idempotency | creator isolation | failure | tests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C7-1 | `chatbotv2/dashboard/static/js/realtime.js` | RealtimeClient exists but no global toast bridge | Notifications missing | Add `rt.on('commerce.sale_recorded', e => showToast('Sale '+e.data.amount, 'success'))` etc. in `dashboard.html` global; use `event_id dedupCache 200` already, plus `generation_id` filter for per-dialog. Keep `isPollingActive` guard for polling fallback | Smallest safe extension | `chatbot:events` | event_id dedup | dialog_id filter | showToast never throws | e2e test |
| C7-2 | `chatbotv2/dashboard/app.py:30 enable_websocket` | no feature flag per creator | Kill-switch global | Keep global `enable_websocket` as kill-switch, no per-creator flag needed Stage B | Safety | `core/config` | N/A | N/A | returns None when disabled | `test_event_bus_disabled_returns_none` |


## 21. Tests Required

**Must pass before Stage B promotion (no implementation in Stage A, but inventory):**

- `test_generation_id_consistent_in_started_and_completed` (`test_phase1_regression.py:818`) -- PROVEN currently passes.
- `test_enqueue_send_before_generation_completed` (`:855`) -- PROVEN ordering.
- `test_generation_failed_emitted_on_exception` (`:943`) -- PROVEN.
- *Fix stale* `test_generation_id_unique_per_process_message` (`:997`) -- currently expects distinct for same `(1,hi,100)`, must change to vary `telegram_message_id` or `content`, or assert deterministic equality.
- New `test_generation_telemetry_md5_inserts` -- md5 generation_id inserts into PG TEXT succeeds and is queryable by `generation_id`.
- New `test_send_correlation_generation_id` -- `enqueue_send` retains `generation_id` and `message.sent` publishes same id.
- New `test_message_created_carries_generation_id` -- `message.created` event carries same md5.
- New `test_commerce_offer_created_event` / `offer_purchased_event` / `sale_recorded_event` / `aftercare_event` -- best-effort publish, not blocking generation.
- New `test_dashboard_creator_isolation_same_telegram_id` -- two creators same telegram `user_id=999`, each sees only own `commerce_offers/fangate_transactions/messages` via bridge query.
- New `test_fan_isolation_interleaved_history` -- `get_recent_messages` for creator A does not return creator B messages (requires bridge query or new column; until column exists, test documents FAIL).
- New `test_dashboard_never_ack` -- `dashboard/` contains 0 `XACK/XREADGROUP/XAUTOCLAIM/DLQ` writes (static grep).
- New `test_dlq_cleanup_idempotent` -- `cleanup_expired_dlq_entries` leaves recent under 7d, deletes older.
- New `test_logs_redact_buyer_email` -- `sanitize_log_value({"buyer_email":"a@b.com"})` => `[REDACTED]`.
- New `test_hot_fan_dedup` -- two `HOT` derivations within hour deduplicated via `hot_fan:{creator}:{user}:hour` key.
- New `test_execution_stage_derivation` -- pure `derive_execution_stage` for `locked/excluded/qwen/scoring/auto_approved/operator_queued/send_failed/purchased` states.
- New `test_revenue_creator_scoped sum idempotent` -- duplicate `record_dropfans_sale` same `transaction_id` does not double `SUM`.
- New `test_sale_lost_counts` -- `declined+expired` counted, `pending_stale` not conflated as lost, no fake revenue.
- Perf: `test_segment_batch_not_N_plus_one` -- `api_users_segments_batch` for 100 users 20 segments uses <=3 queries not 2000 (query count assertion).
- Load: `test_live_fans_100_1000` -- `GET /api/fans/live` for 1k fans completes <200ms with LIMIT 100, no full scan.
- Failure: `test_event_bus_best_effort_never_breaks_generation` -- Redis Pub/Sub mock raises, `process_message` still `enqueue_send` and `record telemetry` succeeds; `test_realtime_fallback_polling` -- WS onclose sets `isPollingActive=true`, setInterval resumes.

## 22. Risks

| Risk | Trigger | Impact | Mitigation (Stage B should enforce) |
|---|---|---|---|
| Cross-creator message leak | Same Telegram user under 2 creators, `get_recent_messages` global, dashboard `messages.py:143` global feed | Creator A sees Creator B sales, violates tenant boundary, GDPR | C6-2 bridge query via `commerce_offers EXISTS`, require `creator_id` param, plan `messages.creator_id` FK later (not in Stage B), audit every `SELECT FROM messages/users` |
| Telemetry blackout after deploy | `generation_id UUID` not altered, inserts continue to fail | Dashboard shows no generations, operator blind | C0-1 migration before any dashboard feature flag on |
| Correlation loss at send | `generation_id` not forwarded to `send_messages` | Timeline gaps, cannot prove `ai.generation_completed` -> `message.sent` causality, dedup via `dedup_id` not linked to generation | C0-2 generation_id in send stream + message.sent event |
| Duplicate sends on dedup loss | `send_dedup:{md5} 3600` mem only, restart loses | Same `dedup_id` re-sent, buyer charged twice via `record_dropfans_sale` idempotent but UX spam | Keep `send_dedup` TTL, persist dedup in PG `operator_queue` or `messages` ON CONFLICT, monitor `dedup_id` reuse |
| Revenue inferred not authoritative | Frontend polls `provider` directly or uses PPV price as revenue | Fake revenue displayed, finance mismatch | C4-1 `fangate_transactions` sole source, never inference, USD only, `UNIQUE (creator,transaction,event_type)` guard |
| Global metrics unfair eviction | 5000 metrics shared across creators, high-volume creator evicts low-volume | Health `PRODUCTION_STATE` miscomputed, pause/rollback wrong | Per-creator `metrics_by_creator` sharding or increase to 20000 + per-creator ring, or persist metrics to PG |
| Hot fan notification storm | Derived `HOT` every turn, no dedup | 100 hot fans spam operators, toast flood | Sentinel `hot_fan:{creator}:{user}:hour` setex 3600, event_id dedup 200, rate limit 1 per user per hour |
| Dashboard observer interferes with Streams | Dashboard uses `XREADGROUP/XACK/XAUTOCLAIM` | Steals production tasks, stalls workers | C5-1 read-only `XRANGE/XLEN` only, CI grep guard, separate Redis client |
| WebSocket auth bypass | `ws.py:26 verify_session` missing/src 4001 not enforced | Unauthed sees events | Keep 4001 close, `is_global` false for unauthed, `dialog_id` filter enforces scope |
| Log PII leak | `buyer_email` not redacted, `_sanitize` dead | GDPR log retention PII | C6-1 extend `_SENSITIVE_PATTERNS` recursively, scrub `buyer_email` at extraction |
| N+1 dashboard DoS | `api_users_segments_batch` S*N 2000 queries | Dashboard 30s latency for 10k fans | Batch via single `SELECT EXISTS` union or `UNNEST` + `compile_rule` once |
| Sale-lost invented | Declining not-purchased treated as lost prematurely | Operator chases phantom | Only terminal `declined/expired/revoked` count as lost, `pending<48h` is `awaiting` |
| Aftercare double-send | `pending->completed` only, no `sent` distinct | Aftercare message sent twice | Persist `sent` when `enqueue_send` for `post_purchase_followup` succeeds (`scheduler_worker:114`) |

## 23. Architecture Impact

No redesign. Preserve every constraint in Section 30 (`AGENTS.md`):

- **Redis Streams + consumer groups + XAUTOCLAIM** retained: dashboard reads via `XRANGE/XLEN` only, never `XREADGROUP/ACK`. No new Streams. `INBOUND_STREAM`/`SEND_STREAM`/`DLQ_STREAM` names unchanged.
- **Existing workers unchanged**: `llm_worker`, `bot_main send`, `scheduler_worker` keep `XREADGROUP` counts/blocks, `ack`/`move_to_dlq`, heartbeat/heartbeat_stop. Observability taps are **additive `publish_event` best-effort** after DB writes, not before.
- **PostgreSQL retained**: no engine swap, no new `pgvector` index Stage B; migration is additive `TEXT` column or `extra JSONB` + `messages` bridge query future.
- **Telethon retained**: no MTProto replacement, `setup_handlers` stays.
- **DropFans sole purchase authority**: earnings+transactions remain sole truth, no frontend polling, no commerce authority in dashboard.
- **Deterministic decision hierarchy** unchanged: `SAFETY>HANDOFF>AFTERCARE>OBJECTION>OPEN_LOOP>DIRECT_REQUEST>COMMERCE>OPTIMIZATION>LLM` (`commerce/conversation_intelligence.py` + `conversation_operations.py:635`) plus `autonomous_allowed`/`is_rollout_active_for` gates before Qwen -- observability does not gate commerce.
- **Production control/canary** preserved: `enable_websocket` kill-switch, no automatic `promote/rollback/enable/disable` from dashboard, no commerce authorization via UI.
- **Fan knowledge/creator persona/single-pass** preserved: single `extract_commerce_signals`, single Qwen, single scoring, idempotent `strategy_generation_seen_by_creator` cap 100, `evidence_generation_id` dedup.
- **Deduplication/idempotency/creator+fan isolation/LLM language-only authority/DropFans purchase authority**: all carried forward; new events reuse same `generation_id`/`event_id`/`offer_id`/`external_transaction_id` dedup.
- **Prohibitions honored**: NO new LLM, NO new worker, NO new production queue, NO replacement of Streams, NO Telethon replacement, NO commerce authority in dashboard, NO second decision path, NO automatic canary promotion/rollback, NO synthetic metrics, NO fake sale events, NO fake revenue, NO frontend DropFans polling, NO credentials in browser, NO message-content logging for UI convenience.

Impact is **additive, backward-compatible**: new `commerce.*` events consumed only by new dashboard handlers; old `realtime.js` ignores unknown `event_type` safely (`handler = _handlers[parsed.event_type]` if handler). `enable_websocket=false` disables all pushes without breaking bot.

## 24. Final Verdict

Production code, DB, Redis, canary unchanged -- verified via `git status` no diff (Stage A).



---

## Appendix -- Evidence Index (file:line authoritative)

- `AGENTS.md:81` Phase 1 event contract + invariants
- `core/event_bus.py:13 publish_event`, `10 CHANNEL chatbot:events`, `30 enable_websocket`, `52 warning best-effort`
- `workers/llm_worker.py:511 md5 generation_id`, `625 ai.generation_started`, `1119/1244/1285/1307 ai.generation_completed`, `1424 ai.generation_failed`, `1257/1320 suggestion.created`
- `db/redis.py:15 INBOUND_STREAM`, `17 SEND_STREAM`, `18 DLQ`, `40 llm_workers`, `65 enqueue_send`, `171 enqueue_inbound md5`, `186 read_inbound`, `211 XAUTOCLAIM`, `272 lock:creator`, `299 debounce`, `420 rate limit`, `524 cleanup DLQ`, `603 replay lock`
- `chatbotv2/handlers.py:66 message.created NO generation_id`, `81 debounce`, `108 _wait_and_process latest only`
- `chatbotv2/main.py:343 message.sent NO generation_id`, `352 vault.media_sent`, `383 send_failed`, `77 _process_send_stream`
- `core/telemetry.py:19 GenerationTelemetry 110 fields`, `229 record best-effort`, `202 _telemetry_cache ephemeral`
- `db/migrations/20260828040000_generation_telemetry.sql:6 UUID`
- `commerce/desire.py:20 DesireStage`, `temperature.py:31 HOT 0.65`, `relationship.py:17`, `signals.py:147`, `decision.py:156`, `state.py:139`, `pipeline.py:112`, `execution.py:40`, `conversation_operations.py:32 LifecycleState 15`, `569 ConversationOperationDecision`
- `db/schema.sql:7 users PK global`, `21 messages`, `57 user_profiles`, `94 operator_queue`
- `db/postgres.py:360 get_recent_messages`, `118 get_user`, `2720 insert_generation_telemetry bounded JSONB`, `751 get_dashboard_analytics`
- `chatbotv2/dashboard/ws_manager.py:18 ConnectionManager`, `dashboard/event_subscriber.py:14 start_event_subscriber`, `dashboard/static/js/realtime.js:19 RealtimeClient`, `dashboard/routes/ws.py:12 /ws`
- `chatbotv2/dashboard/app.py:98 start_event_subscriber if enable_websocket`, `127 20 routers`
- `commerce/dao.py:57 create_offer_serialized 85 advisory lock`, `629 clicked`, `647 purchased`, `935 aftercare pending`, `411 timing context`
- `db/dropfans.py:177 record_dropfans_sale 217 ON CONFLICT`, `103 synthetic product hash`, `integrations/dropfans/service.py:611 get_earnings 734 poll_sales`
- `db/fangate.py:367 get_fangate_product never leak`, `508 buyer_email`
- `commerce/production_control.py:51 record_metric 5000`, `606 OperationalAuditRecord 1000`, `509 autonomous_allowed`
- `commerce/adaptive_optimization.py:38 CanonicalOutcome`, `285 make_exposure`, `core/conversation_state.py:26`
- `tests/test_phase1_regression.py:818/855/943/997`
- `core/logging_config.py:13 _SENSITIVE_PATTERNS misses buyer_email`

*End of report -- STAGE A, no production mutations.*

### PHASE 42A VERDICT

REAL-TIME EXECUTION VISIBILITY: PARTIAL
GENERATION CORRELATION: PARTIAL
FAN LIVE STATE: PARTIAL
SALES VISIBILITY: PARTIAL
REVENUE VISIBILITY: PARTIAL
DROP FANS OBSERVABILITY: PARTIAL
NOTIFICATION INFRASTRUCTURE: PARTIAL
CREATOR ISOLATION: PARTIAL
FAN ISOLATION: FAIL
PRIVACY: PARTIAL
FAILURE ISOLATION: PARTIAL
PERFORMANCE: PARTIAL

P0: 3
P1: 8
P2: 8
P3: 3

PRODUCTION CHANGES: NONE
CANARY CHANGES: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
NEW MIGRATIONS: 0
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

NEXT ACTION:
Fix P0 (C0-1 UUID vs md5, C0-2 generation_id in send stream/message.sent, C0-3 message.created generation_id + debounce), then land C1 additive commerce events + C2 derived execution stage + C6 creator-scoped dashboard bridge queries (all best-effort, backward-compatible, reuse event_bus/ws_manager/isPollingActive); no new workers/queues/LLMs, no metrics/creds in browser, no polling removal.
