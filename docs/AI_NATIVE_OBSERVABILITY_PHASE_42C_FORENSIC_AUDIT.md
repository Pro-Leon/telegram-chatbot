# AI_NATIVE_OBSERVABILITY_PHASE_42C_FORENSIC_AUDIT -- STAGE A
**Hostile Forensic Audit -- Realtime Execution Visibility & Event Intelligence (READ-ONLY)**
**Date: 2026-08-31 | Workspace: E:\chatbot | Auditor: OpenCode Muse Spark | Phase: 42C Stage A**
**NO PRODUCTION CHANGES -- EVIDENCE-BASED**


## 1. Executive Summary

**Scope:** Stage A Read-Only hostile audit of realtime observability for operator dashboard after Phase 42B generation-correlation hardening. No code, schema, Redis, or canary changes. Evidence derived from source, SQL, tests, and runtime config.

**What Phase 42B fixed (PROVEN):**
- `generation_id = md5(user_id:message:telegram_id)` 32 hex now deterministic at `chatbotv2/handlers.py:68` and preserved through `debounce_enqueue -> inbound XADD (db/redis.py:182) -> XREADGROUP -> llm_worker.py:511 -> ai.generation_started:625 -> enqueue_send with generation_id (db/redis.py:65, llm_worker.py:1271, main.py:99) -> message.sent with generation_id (main.py:343)`; `generation_telemetry` column migrated to TEXT `migrations/20260831000000_generation_id_text.sql` so md5 persists; 14 tests in `tests/test_phase42_correlation.py` pass.

**What remains for dashboard (verdict preview):**

| Dimension | Verdict |
|---|---|
| Realtime execution visibility | **PARTIAL** -- generic generation/message events exist, commerce-specific events silent |
| Generation correlation | **PASS** for core lifecycle (inbound->generation->send), **PARTIAL** for scheduled/post-purchase/operator-approved paths (no gid) |
| Live fan state | **PARTIAL** -- active can be derived via recent messages, hot/opportunity require commerce authority not yet emitted |
| Commerce visibility | **PARTIAL** -- offer pending/purchased state pollable, no realtime `offer_created/purchased` |
| Sales visibility | **PARTIAL** -- sale recorded in `fangate_transactions` pollable, no `sale_recorded` event |
| Revenue visibility | **PARTIAL** -- earnings pollable via DropFans, no realtime revenue event, must not infer from PPV sent |
| Post-purchase visibility | **FAIL** -- fulfillment (confirmation, follow-up, media) silent, aftercare pending/completed silent, no funnel transition event |
| Notification infrastructure | **PARTIAL** -- WebSocket + PubSub + polling fallback intact, toast exists but commerce toasts missing, no fan-scoped notification dedup beyond event_id |
| Creator isolation | **FAIL** -- `core/event_bus.py:13` has no creator_id, `ws.py:32` always global, `conversation.note/tag/attention` global broadcast, dashboard polling uses creator-scoped queries but realtime over-broadcasts |
| Fan isolation | **FAIL** -- debounce key `debounce:{user_id}` not creator-scoped, lock is, but realtime still global; same Telegram user across creators could collide |
| Privacy | **PARTIAL** -- event bus does not expose secrets/buyer email, but `message.created` `content` and `ai.generation_started` `preview 100` are bounded PII, `vault` routes expose signed file URLs to operators (intentional) -- no new leaks, but payloads must remain minimal |
| Failure observability | **PARTIAL** -- `message.send_failed`, `ai.generation_failed` exist, but commerce/DropFans failures silent |
| Reconnect safety | **PARTIAL** -- `realtime.js` dedup 200 + jittered reconnect + `isPollingActive` guard correct, but `chats.html`/`queue.html` lack polling fallback, generation filter transient |
| Performance | **PARTIAL** -- per-generation events (~5 per message) safe for 10k fans (~50k events/day), but N+1 bulk_ops and per-user polling could spike |

**P0 remaining before dashboard can be trusted for commerce/revenue:** Global WebSocket scope (creator leak), silence on sale/attribution/fulfillment, and no authoritative sale-lost state. No fake metrics should be invented.

**Stage B must be additive:** `generation_id` propagation already fixed, next is creator-scoped bus param, additive commerce events via existing channel, derived execution stages, and creator-scoped dashboard bridge queries. No new worker/queue/LLM.


## 2. Existing Realtime Architecture (PROVEN)

**Channel:** `core/event_bus.py:10 CHANNEL="chatbot:events"` (single Redis PubSub channel). Verified: no second channel, no Kafka, no Celery.

**Publisher:** `core/event_bus.py:13 publish_event(event_type,data,*,user_id,dialog_id,generation_id,scope="global")->str|None`
- `31 if not settings.enable_websocket: return None` -- global kill-switch `core/config.py:38 enable_websocket True`
- `34 event_id = str(uuid.uuid4())` -- unique per publish, `realtime.js:68` dedup 200
- `35-44 event={event_id,event_type,timestamp_ms,user_id,dialog_id,generation_id,scope,data}`
- `50 await (await get_redis()).publish(CHANNEL, json.dumps(event))` -- transient, `52-54 except: warning return None` best-effort, never propagates (`workers/llm_worker.py:1426` still raises original error, not masked) -- **PASS invariant `AGENTS.md:150`**

**Subscriber:** `chatbotv2/dashboard/event_subscriber.py:14 start_event_subscriber()`
- `35 redis.from_url(settings.redis_url)` separate client (not `db/redis.get_redis` singleton -- two pools, not fatal)
- `40 pubsub.subscribe(CHANNEL)` `45 async for raw_msg in pubsub.listen()` `50 json.loads` skip malformed `55 if not event_type: skip`
- `60 dialog_id=event.get("dialog_id") 61 scope=event.get("scope","global") 64 await get_manager().broadcast(event, dialog_id if scope=="user" else None)` -- correct translation
- Backoff `10 MAX 30.0` `11 INITIAL 1.0` `85 backoff=min(backoff*2,30)` bounded exponential + reconnect `84 sleep(backoff)`; `66 CancelledError: break`
- Started `chatbotv2/dashboard/app.py:97 if enable_websocket: create_task(start_event_subscriber())`, shutdown `112 cancel`

**WebSocket Manager:** `chatbotv2/dashboard/ws_manager.py:18 ConnectionManager`
- `24 _connections: list[ManagedConnection]` `26 _lock`
- `28 connect(ws,dialog_ids,is_global)` `31 await ws.accept()` `35 lock append` -- accepts `dialog_ids` set
- `45 broadcast(event,*,dialog_id)` `54 if dialog_id in conn.dialog_ids elif is_global` -- scoped delivery, stale cleanup `70-75` correct, no lock during `await ws.send_text`

**Endpoint:** `chatbotv2/dashboard/routes/ws.py:12 @router.websocket("/ws")`
- `21 session=ws.cookies.get("session") 23 if not token: close(4001) 26 verify_session 28 if not user: close(4001)` -- 4001 triggers `realtime.js:85` intentionalClose, no reconnect, polling resumes
- `32 await manager.connect(ws, is_global=True)` -- **CRITICAL GAP** always global, never passes `dialog_ids`; all dashboards receive all `scope=user` events (scope invariant `AGENTS.md:173` BROKEN)

**Frontend:** `chatbotv2/dashboard/static/js/realtime.js:19 RealtimeClient`
- `14 MAX_RECONNECT 30000 15 INITIAL 1000 17 DEDUP 200 16 PING 30000` `36 _buildUrl wss/ws loc.host+/ws` `45 new WebSocket` `51 onopen _pollingActive=false 62 onmessage JSON.parse, pong skip, dedup event_id indexOf, handler[event_type]`
- `82 onclose _connected=false _stopPing if code==4001 intentionalClose else scheduleReconnect` `100 onerror close` `104 beforeunload close`
- `116 _scheduleReconnect jitter Math.random()*1000 delay min(reconnectDelay+jitter,MAX) *2` exponential jitter -- correct
- `126 _startPing 30s send ping`
- `142 on(eventType,handler) 157 isConnected 162 isPollingActive`

**Streams (persistent, separate from PubSub):** `db/redis.py:15 INBOUND_STREAM="inbound_messages" 17 SEND_STREAM="send_messages" 18 DLQ_STREAM="dead_letter_queue"` with groups `40 llm_workers 41 send_workers` `48 xgroup_create` `65 enqueue_send(...,generation_id) 71 data["generation_id"]=str(gid)` **post-42B FIXED** `175 enqueue_inbound` deterministic md5 fallback `271 lock:creator:{cid}:user:{uid}` creator-scoped (PASS) vs `303 debounce:{user_id}` not scoped (P2) `424 token bucket 1/sec burst 5`

**Persistence split:**
- Persistent: Streams `inbound_messages`/`send_messages` XADD/XREADGROUP/XACK/XAUTOCLAIM, `scheduled_messages` PG `db/postgres.py:2269`, `messages`/`operator_queue`/`generation_telemetry` PG, `fangate_transactions`/`commerce_offers` PG
- Transient (best-effort loss on downtime): `chatbot:events` PubSub, WebSocket `ws_manager`, dedup cache, telemetry cache `core/telemetry.py:222`

**Polling fallback (preserve `AGENTS.md:176`):**
- `overview.html:307 setInterval loadStats 5000 if rt.isPollingActive()` PASS
- `analytics.html:1403 15s if isPollingActive()` PASS
- `chat.html:968 loadSuggestions` no guard but `ws` handlers call it; `queue.html:103 no interval` relies on reload -- **GAP**
- `chats.html:248 no polling/realtime` -- stale

**No redesign:** Streams not replaced by PubSub (`AGENTS.md FORBIDDEN`), workers `llm_worker`/`scheduler_worker` unchanged, `isPollingActive` guard preserved.

## 3. Full Event Inventory (Evidence Table)

| Event | Producer file:line | Producer function | Payload fields | generation_id | creator_id | user_id/dialog_id | timestamp | privacy | persistent/transient | dashboard | creator-scoped | evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `message.created` | `chatbotv2/handlers.py:70` | `handle_incoming_message` | `message_id, telegram_message_id, content, direction=inbound, username, first_name` | **YES** `68 md5` post-42B | **MISSING** | `80 user_id 81 dialog_id` | `event_bus:35 timestamp_ms` | **PII** full content bounded but existing | transient PubSub + persistent `messages` row | yes via `ws global` (over-broadcast) | **PARTIAL** -- creator_id missing, global ws | PROVEN |
| `ai.generation_started` | `workers/llm_worker.py:625` | `process_message` | `message_preview: user_message[:100]` | **YES** same md5 | **MISSING** (in telemetry `creator_id` but not bus) | `user/dialog` | timestamp_ms | preview 100 bounded PII | transient | yes (filtered `_activeGenerationId`) | partial | PROVEN |
| `ai.generation_completed` | `workers/llm_worker.py:1119/1244/1287/1309` | `process_message` (4 sites) | `draft, score, flags, was_auto_approved` | **YES** same md5 `1287 AFTER enqueue_send 1271` invariant PASS | **MISSING** | user/dialog | timestamp | draft may contain PII | transient | yes | partial | PROVEN |
| `ai.generation_failed` | `workers/llm_worker.py:1426` | `process_message except` | `error` | **YES** | missing | user/dialog | timestamp | safe | transient | yes | partial | PROVEN |
| `suggestion.created` | `workers/llm_worker.py:1257/1322` | `process_message` operator-queued | `queue_id, draft, score, flags` | **YES** | missing | user/dialog | timestamp | draft | transient | yes | partial | PROVEN |
| `message.sent` | `chatbotv2/main.py:344` | `_process_send_stream` | `content, telegram_message_id, was_auto_approved, confidence_score, media_type?, fangate_media_id?` | **YES** post-42B `99 data.get("generation_id")` | **MISSING** (creator via `data["creator_id"]` field not bus param) | user/dialog | timestamp | content full (existing) | transient + persistent `messages` outbound | yes | partial (global ws) | PROVEN |
| `vault.media_sent` | `chatbotv2/main.py:354` | `_process_send_stream` (media) | `fangate_media_id, product_id, user_id` | **YES** | missing | user/dialog | timestamp | media id | transient | yes | partial | PROVEN additive |
| `message.send_failed` | `chatbotv2/main.py:385/417` | `_process_send_stream` except | `error` | **YES** post-42B | missing | entity_int | timestamp | safe | transient | yes toast `chat.html:767` | partial | PROVEN |
| `operator_queue.updated` | `workers/send_worker.py:101` / `chatbotv2/dashboard/routes/queue.py:46` | `process_approved_message` / queue routes | `queue_id, action, user_id` | **MISSING** (global, not lifecycle) | missing | user | timestamp | safe | transient | yes (queue page reload) | partial (should be global but code uses user scope) | PROVEN |
| `conversation.note_changed` | `chatbotv2/dashboard/routes/notes.py:92` | `api_create_note` etc | `user_id, note_id, action, changed_by` | MISSING | missing | **global** | timestamp | note content | transient | yes | **FAIL global broadcast** | PROVEN |
| `conversation.tag_changed` | `routes/tags.py:102` | tag assign | `user_id, tag_id, tag_name` | MISSING | missing | global | timestamp | safe | transient | yes | FAIL global | PROVEN |
| `conversation.attention_changed` | `routes/attention.py:44` | attention | `user_id, status, assigned` | MISSING | missing | global | timestamp | safe | transient | yes | FAIL global | PROVEN |
| `conversation.assigned` (bulk) | `routes/bulk_ops.py:91` | bulk assign | per user | MISSING | missing | global | timestamp | safe | transient per user spam | yes | FAIL | PROVEN |

**Commerce: NO realtime events emitted (silent):** `commerce/execution.py` (PPV), `commerce/post_purchase.py:170`, `commerce/attribution.py`, `commerce/reconciliation.py`, `integrations/dropfans/service.py` -- all have **0 `publish_event`**. Only downstream `message.sent` generic is emitted (see Section 10).

**Legend:** PROVEN = direct read, MISSING = field absent, PARTIAL = creator_id via data not bus param.


## 4. End-to-End Generation Lifecycle (Actual Call Graph)

```
Telegram inbound (Telethon NewMessage event.message.id, message)
  -> chatbotv2/handlers.py:26 handle_incoming_message
    -> 41 check_rate_limit 52 unblacklist 57 upsert_user
    -> 58 save_inbound_message -> db/postgres.py:259 INSERT messages ON CONFLICT (user,telegram_id) WHERE inbound
    -> 68 generation_id = md5(user:msg:telegram_id) -- CANONICAL, post-42B
    -> 70 publish_event message.created {message_id,telegram_message_id,content,username,first_name} generation_id, scope=user
    -> 81 debounce_enqueue user_id, content, message_data={user_id,content,telegram_message_id,username,first_name,generation_id} -- post-42B
    -> 86 if not is_window_owner: send_typing and return (debounced)
    -> 108 _wait_and_process sleep 3s
      -> 118 get_debounced_messages -> latest = debounced[-1] (earlier discarded but already had message.created)
      -> 135 enqueue_inbound({user_id,content,telegram_message_id,username,first_name,persona,generation_id}) -- post-42B
        -> db/redis.py:182 XADD inbound_messages {generation_id} (md5 fallback if absent, not needed post-42B)
  -> workers/llm_worker.py:1448 run_worker loop
    -> 1492 requeue_stalled_messages XAUTOCLAIM idle 30s -> same generation_id
    -> 1502 read_inbound XREADGROUP llm_workers -> data.get("generation_id")
    -> 499 process_message(user_id, msg, tgId, generation_id)
      -> 510 if generation_id is None: md5 else str -- preserves retry/XAUTOCLAIM
      -> 517 telemetry start_generation(generation_id) -- copy to creator_id later
      -> 527 resolve_single_application_creator (lowest active) -> _creator_id
      -> 550 acquire_user_lock lock:creator:{cid}:user:{uid} -- creator-scoped
      -> 559 upsert_user 561 is_user_auto_reply_excluded
      -> 571 build_qwen3_context
      -> 587 fan_knowledge extract+add (idempotent via evidence_generation_id)
      -> 625 publish ai.generation_started {preview 100} generation_id, scope=user
      -> 637 extract_commerce_signals SINGLE
      -> 644 _try_commerce_draft -> 11-gate execution (decision, integration, product, idempotency, eligibility, serialized advisory lock, record_offer_transition)
      -> 669 build_conversational_commerce_state -> derive desire/temperature/readiness/window/objective/next_best_action
      -> 754 make_exposure/persist_exposure bounded 50
      -> 784 compute_pressure/derive_risk/derive_lifecycle/build_operation_decision
      -> 837 autonomous_allowed + rollout + handoff + commerce_pause gates (fail-closed)
      -> 907 operational_decision + execute_operational_recommendation idempotent op_exec:{gid}:{rec}
      -> 962 should_use_agent canary
      -> 971 if paused: draft="will follow up", score 0.1
      -> 1049 generate_draft OR generate_draft_with_tools -- ONE Qwen call
      -> 1119 if empty draft: add_to_operator_queue + publish ai.generation_completed + return
      -> 1136 score_draft ONE scoring, authority-aware price bypass
      -> 1162 autonomous_allowed second gate + record_metric + audit
      -> 1225 auto_reply_on + dedup_id=md5(user:msg:tgId)
        -> if !auto_reply_on: add_to_operator_queue + publish completed + suggestion.created
        -> elif score>=0.80 no flags: enqueue_send({entity,content,draft_content,was_edited,was_auto_approved,confidence_score,operator_id,save_to_db,generation_id}, dedup_id, generation_id) -- post-42B
             -> db/redis.py:71 data["generation_id"]=str(gid) XADD send_messages (persistent)
             -> 1287 publish ai.generation_completed was_auto True generation_id AFTER enqueue -- invariant PASS
        -> else: add_to_operator_queue + publish completed + suggestion.created
      -> 1334 post_process extract_and_update_profile + maybe_summarize async
      -> 1395 update_strategy_evidence_extended dedup via generation_id
      -> 1409 telemetry complete + record -> db/postgres.py:2720 INSERT generation_telemetry TEXT (post-42B migration) -- best-effort, never blocks
      -> 1522 ack_inbound XACK
      -> on exception: 1426 publish ai.generation_failed generation_id; move_to_dlq inbound with payload JSON including generation_id
  -> chatbotv2/main.py:77 _process_send_stream (bot_main)
    -> 82 requeue_stalled_send_messages XAUTOCLAIM preserves generation_id
    -> 94 read_send_messages XREADGROUP send_workers -> data
    -> 99 generation_id = data.get("generation_id") or None -- post-42B
    -> 100 is_send_duplicate -> skip
    -> 111 rate limit LUA 116 if not allowed: ack + enqueue_send(dict(data), generation_id) -- preserves
    -> 176 FloodWait: sleep + ack + enqueue_send(..., generation_id)
    -> 221 reserve_delivery vault (before send)
    -> 275 send_file or 282 send_message via Telethon input_entity
    -> 285 mark_send_dedup 285 ack_send XACK
    -> 294 save_outbound_after_send -> messages outbound
    -> 315 finalize vault delivery
    -> 343 publish message.sent {content,telegram_message_id,was_auto_approved,confidence_score,media_type,fangate_media_id} generation_id -- post-42B
    -> 354 publish vault.media_sent with generation_id
    -> on UserIsBlocked: 385 publish message.send_failed generation_id
    -> on Exception: 417 publish message.send_failed generation_id, move_send_to_dlq with payload
  -> commerce/post_purchase.py:170 handle_post_purchase (only after DropFans sale attribution, not per generation) -- SILENT (see Section 12)
  -> integrations/dropfans/service.py:734 poll_sales / 760 reconcile_sales (scheduler loop) -- SILENT polling, not event
```

**Observable in dashboard (post-42B):** All generation-bound steps emit with same generation_id; send pipeline now correlatable; telemetry persisted; but commerce/post-purchase still silent.

## 5. Generation Correlation Audit (Post-42B)

**Canonical:** `md5(user_id:message:telegram_id)` 32 hex at `handlers.py:68`, `redis.py:179 md5`, `llm_worker.py:512 md5`, `dashboard/routes/messages.py:103 md5`.

**Event-by-event (PROVEN post-42B fixes):**

| Event | generation_id | creator_id | user_id/dialog_id | Same across retry/XAUTOCLAIM? | Evidence |
|---|---|---|---|---|---|
| `message.created` | YES md5 at intake | MISSING | user/dialog | YES (intake deterministic) | `handlers.py:68` |
| `debounce` (message_data) | YES | MISSING | user | YES (JSON preserved `95`) | `handlers.py:95` |
| `inbound XADD` | YES preserved + fallback `182` | - | user | YES | `redis.py:179-182` |
| `XREADGROUP/XAUTOCLAIM` | YES preserved | - | user | YES | `redis.py:211` returns same fields |
| `ai.generation_started` | YES same md5 | MISSING (telemetry has) | user/dialog | YES | `llm_worker.py:632` |
| `commerce offer` (internal) | NO event | - | - | N/A | 0 publish |
| `ai.generation_completed` | YES same md5 AFTER enqueue | MISSING | user/dialog | YES | `llm_worker.py:1287` |
| `suggestion.created` | YES same | MISSING | user/dialog | YES | `1322` |
| `send XADD` | YES `71 data["generation_id"]` | via `data["creator_id"]` field not bus param | entity | YES | `redis.py:71` `llm_worker.py:1271` |
| `send XREADGROUP/XAUTOCLAIM` | YES preserved | - | entity | YES | `main.py:99` |
| `message.sent` | YES `344 generation_id` | MISSING | user/dialog | YES | `main.py:343-349` post-42B |
| `vault.media_sent` | YES | MISSING | user/dialog | YES | `354` |
| `message.send_failed` | YES | MISSING | entity_int | YES | `385,417` |
| `operator_queue.updated` | MISSING (not lifecycle) | MISSING | user | N/A | `send_worker.py:101` |

**Remaining breaks (post-42B):**

- **P1** Scheduled/post-purchase/operator-approved sends: `scheduler_worker.py:107 enqueue_send(no gid)`, `commerce/post_purchase.py:142,507 no gid`, `send_worker.py:29 no gid` -> `main.py:99` yields `generation_id=None` for those sends, so `message.sent` for confirmations/media has `null` gid, cannot join to `ai.generation_started`. **PROVEN** `grep enqueue_send` shows 3 call sites without gid.
- **P1** `operator_queue.updated` and `conversation.note/tag/attention` events have no generation_id (expected, global).
- **P2** `telemetry default uuid4` `core/telemetry.py:21` vs md5: default only used if caller omits gid (now all callers supply), not a break unless direct `GenerationTelemetry()` instantiation.

**No second identifier:** No `uuid4()` for generation correlation (`grep uuid4` only for `event_id` `core/event_bus.py:34`, `request_id`, `telemetry default`), no duplicate generation IDs.

**Verdict:** Core lifecycle **PASS** (all generation-bound events share same md5, survive XAUTOCLAIM/retry/debounce/requeue). Peripheral paths **PARTIAL** (scheduled/fulfillment).

## 6. Execution-Stage Model (Derived from Evidence)

**No persisted state table** -- stages are transient or derived; only `users.funnel_stage` and `messages.direction` are durable.

**Actual code locations that define stages:**

- `RECEIVED` -- `handlers.py:58 save_inbound_message` + `70 message.created`
- `DEBOUNCING` -- `db/redis.py:303 debounce_enqueue` 3s window `handlers.py:108 sleep`
- `QUEUED` -- `db/redis.py:182 XADD inbound_messages`
- `PROCESSING` (consumer) -- `workers/llm_worker.py:1502 read_inbound` + `550 acquire_user_lock`
- `CONTEXT` -- `memory/context.py:488 build_qwen3_context` `llm_worker.py:571`
- `COMMERCE_SIGNAL` -- `commerce/deepseek.py:162 extract_commerce_signals` `llm_worker.py:637 SINGLE`
- `DECISION` (deterministic) -- `commerce/conversation_operations.py:635 build_operation_decision` `llm_worker.py:784` (pressure, risk, lifecycle, next_best_action)
- `SAFETY_GATE` (production controls) -- `commerce/production_control.py:509 autonomous_allowed` `llm_worker.py:837` (pause, rollout, handoff)
- `QWEN` -- `workers/llm_worker.py:83 generate_draft` ONE call `1049`
- `SCORING` -- `core/scoring.py:86 score_draft` ONE call `1136`
- `AUTHORITY` (post-Qwen) -- `llm_worker.py:1162 autonomous_allowed` second gate + score threshold `1270`
- `SEND_QUEUED` -- `db/redis.py:71 XADD send_messages` `llm_worker.py:1271`
- `SENDING` -- `chatbotv2/main.py:275 send_file / 282 send_message` (Telethon)
- `SENT` -- `main.py:344 message.sent` + `db/postgres.py:315 save_outbound_after_send`
- `OUTCOME` -- `commerce/adaptive_optimization.py:38 CanonicalOutcome` `llm_worker.py:1395` + `generation_telemetry` fields `telemetry.py:19-110` (outcome, funnel_state not all persisted `db/postgres.py:2728 INSERT 22 cols`)
- `FAILED` -- `llm_worker.py:1426 ai.generation_failed` or `main.py:385 send_failed`, DLQ `db/redis.py:18`
- `WAITING` -- no outbound pending, `XAUTOCLAIM` idle

**Derived minimal model for dashboard (evidence-based, not invented):**

```
RECEIVED -> DEBOUNCING -> QUEUED -> PROCESSING -> CONTEXT -> COMMERCE_SIGNAL -> DECISION -> SAFETY_GATE -> QWEN -> SCORING -> AUTHORITY -> SEND_QUEUED -> SENDING -> SENT -> OUTCOME
                                                                                                         -> FAILED
                                                                                                         -> WAITING (if no failure/success)
```

- **Explicit events needed:** `RECEIVED` (message.created ?), `QWEN` (generation_started ?), `SCORING` (generation_completed carries score, not separate event -- could derive from completed), `SENT` (message.sent ?), `FAILED` (generation_failed / send_failed ?)
- **Can derive from existing events:** `CONTEXT/SIGNAL/DECISION/SAFETY_GATE` can be inferred from `ai.generation_started` + `telemetry.decision_trace` (if persisted) + `generation_completed` `routing_decision`/`flags`; no need for per-stage realtime event.
- **Can reconstruct from persisted data:** `SENT` via `messages` outbound, `OUTCOME` via `generation_telemetry` (partial, 22/60 fields), `FAILED` via DLQ `dead_letter_queue`.
- **Transient only:** `SENDING` (between `SEND_QUEUED` and `SENT`, no DB, only `send_messages` PEL + `send_ratelimit` ZSET).
- **Missed event:** If `ai.generation_completed` missed, dashboard can still show `SENT` via polling `messages` (fallback), but live timeline gaps.
- **Reconnect:** Realtime.js `DEDUP 200` + jittered reconnect preserves polling via `isPollingActive` guard; transient `SENDING` lost, but `SENT` persists.
- **Worker restart:** `XAUTOCLAIM` preserves `generation_id` fields, so `RECEIVED->QUEUED` replayed without new ID; `SENDING` may be re-attempted (dedup via `send_dedup` 3600s).

**No second decision path if dashboard only derives stage from existing `generation_id` + `decision_trace` + `messages` -- safe.**


## 7. Creator Isolation Audit -- FAIL (Critical)

**Requirement:** `creator A` cannot receive `creator B` events/fans/messages/opportunities/sales/revenue/aftercare/notifications. Server-side enforcement required, not frontend-only.

**Bus-level (FAIL):**

- `core/event_bus.py:13` has no `creator_id` param. All callers omit it. Payload `data` sometimes contains `creator_id` as plain field (`chatbotv2/main.py:28 creator_id_check` for vault) but not consistently, and `core/event_bus.py:35-44` never extracts it for scoping.
- `chatbotv2/dashboard/ws_manager.py:45 broadcast(event, dialog_id)` filters only by `dialog_id in conn.dialog_ids` or `is_global`. No `creator_id` branch. `ws_manager.py:12 ManagedConnection` has no `creator_id` field.
- `chatbotv2/dashboard/routes/ws.py:32 manager.connect(ws, is_global=True)` -- **always global**, never passes `dialog_ids` set, never captures creator from session. Every authenticated dashboard receives every `scope=user` event (e.g., `message.sent` for user 123 -> all operators). **PROVEN P0** `AGENTS.md:173 scope invariant BROKEN`.

**Redis keys (PARTIAL):**

- `lock:creator:{cid}:user:{uid}` `db/redis.py:268` **PASS** creator-scoped.
- `debounce:{user_id}` `db/redis.py:310` **FAIL** not creator-scoped (should be `debounce:creator:{cid}:user:{uid}` or at least include creator). Two creators same fan rapid messages collide.
- `send_ratelimit:{peer}` `db/redis.py:428` per-entity, not creator-scoped (acceptable, Telegram peer is global).
- `send_dedup:{md5}` `db/redis.py:78` dedup global, content-based (acceptable).

**SQL WHERE clauses (PARTIAL):**

- Commerce `WHERE creator_id=$1` **PASS**: `commerce/dao.py:275 SELECT pending/clicked WHERE creator_id=$1 AND product_id=$2`, `db/dropfans.py:318 list_active_dropfans_products WHERE creator_id`, `db/postgres.py:2720 generation_telemetry` has `creator_id` column but `dashboard/routes/notes.py:92` etc ignore it.
- Dashboard routes **FAIL global broadcast:** `routes/notes.py:92 publish conversation.note_changed scope=global`, `tags.py:102`, `attention.py:44` global -- every dashboard gets every note/tag/attention change, no creator filter.
- Dashboard queries **MIXED:** `chatbotv2/dashboard/routes/fangate.py:404 list_active_dropfans_products(creator_id)` **PASS** creator-scoped; but `dashboard/routes/messages.py:139 api_recent_messages SELECT ... FROM messages ORDER BY created_at DESC LIMIT 20` -- **no creator filter**, returns last 20 of entire platform. `chats.html` polling not creator-filtered. `analytics.html` aggregates via `db/postgres.py:2720 generation_telemetry` grouped by `creator_id` but realtime events not filtered.

**Redis channels (FAIL global):**

- Single `CHANNEL="chatbot:events"` `core/event_bus.py:10` for all creators. No per-creator channel. Combined with global ws, any event reaches all dashboards. Requires channel namespacing or payload filtering.

**Dashboard route authorization (PASS auth but FAIL scoping):**

- `routes/ws.py:21-26` verifies `session` cookie via `verify_session` (operator auth) but does not bind session to creator(s). No `creator_id` extracted. Same for `routes/notes.py`, `tags.py` etc -- they `require_auth` but never check `creator_id` owns `user_id`.

**Frontend filtering (FAIL as isolation):**

- `realtime.js:76 handler[parsed.event_type]` dispatches to `chat.html:687 if (evt.dialog_id !== dialogId) return` -- per-dialog filter exists but relies on client knowing dialogId, not server enforcement. `queue.html` has no per-creator filter. Frontend-only is not isolation per spec `Do NOT accept frontend-only filtering`.

**Report:** **FAIL** -- P0 for creator leak via global PubSub channel + global WebSocket + missing `creator_id` on bus. Fix requires optional `creator_id` param on `publish_event`, carry via `event_subscriber` to `ws_manager` broadcast with `creator_ids` set, and `ws.py` to capture creator from session and `connect(creator_ids={cid})`. Additive, backward-compatible.

## 8. Fan Isolation Audit -- FAIL

**Effective identity should be `creator_id + user_id`.**

- `user_id` is Telegram sender `event.sender_id` `handlers.py:37`. Same Telegram user X appears as `users.id BIGINT PRIMARY KEY` `db/schema.sql:7` (global PK). A fan may exist across creators, rows shared.
- `generation_id = md5(user:msg:telegram_id)` `handlers.py:68` includes `user_id` + `telegram_message_id` but **not `creator_id`**. Two creators same fan same `msg`+`tgId` -> same `generation_id` collision (P3). Should include `creator_id` if bus had it, but currently not.
- `debounce:{user_id}` not creator-scoped -> fan X under creator A and B share debounce window (P2).
- `user_profiles.facts` `db/schema.sql:57` JSONB global per `user_id`, but `fan_knowledge` correctly namespaces via `creator_id` inside `get_knowledge_memory` (bounded 30) -- **PARTIAL** fan isolation via memory namespace, but DB row is still global and could leak via `get_user_profile` if not filtered.
- **Global fan queries:** `db/postgres.py:360 get_recent_messages WHERE user_id=$1` -- no creator filter, returns all creators' messages for same fan. `memory/context.py:488 build_qwen3_context` does same; context could mix creators' histories if same Telegram user chats with both.
- `commerce/dao.py:200 find_pending_offer_for_product WHERE creator_id=$1 AND product_id=$2 AND user_id` correctly scopes, so commerce **does** isolate, but realtime `message.created` without creator cannot be routed to correct creator dashboard to show that commerce.

**Verdict:** Global `user_id` as identity fails fan isolation for multi-creator. Requires `creator_id + user_id` as effective key for debounce, generation_id, and dashboard queries. Currently **FAIL**.

## 9. Live Fan Activity Analysis

**Desired realtime view (spec):** `23 fans active, 8 hot, 5 awaiting reply, 3 opportunities, 2 PPVs awaiting purchase`

**Do not invent definitions -- ground in authority.**

- **ACTIVE (recent inbound/outbound/processing/pending send):**

  Signals in system:
  - `messages` `direction=inbound` `created_at` within window (e.g., 15m) `db/postgres.py:360` -- **persisted** `idx_messages_user_id_created`
  - `messages` outbound `direction=outbound` similarly
  - `inbound_messages` Stream PEL `XINFO STREAM` `db/redis.py:15` pending count (processing)
  - `send_messages` Stream PEL pending
  - `is_blocked` / `do_not_auto_reply` flags `db/schema.sql:7`

  Correct definition **not yet deterministic** in code -- no `active` function. Must be derived: `active = COUNT(DISTINCT user_id) WHERE last_message_at > NOW() - interval '15m' AND creator_id=$1` via `messages` join `commerce_offers` to scope by creator (since `messages` lacks `creator_id`, must join via `fangate_transactions` or `commerce_offers` existence per creator). Time window not established; 5m/15m both plausible but need product decision. **Currently DERIVED, not AUTHORITATIVE, missing creator-scoped query** (P1).

- **HOT (commerce state, interest, pressure, PPV readiness):**

  Deterministic authority: `commerce/temperature.py:31` `derive_temperature`? Not directly, but `commerce/conversational.py` `build_conversational_commerce_state` -> `temperature.level`, `desire.stage`, `readiness.value`, `window`, `objective`; `commerce/relationship.py:17` `CommercialPressure`; `commerce/signals.py:147 CommerceSignals` `primary_intent`, `intent_tags`, `negative_intent_tags`, `fan_asks_question`, `asks_for_free_content`; `commerce/decision.py` `CommerceDecision` with `pressure_bucket`.

  Actual `HOT` is **not a stored flag** -- it is `temperature=HOT` bucket in `telemetry` (partially persisted). The decision `present_offer` vs `offer_ready` is the opportunity. Dashboard cannot invent new hot algorithm; must reuse `temperature`/`pressure_bucket`/`offer_readiness` already computed per generation.

  **Currently** `telemetry.to_dict()` includes `temperature` but `db/postgres.py:2728 INSERT` truncates it (22/60 fields) -- so `HOT` not reliably persisted for polling. Realtime `ai.generation_completed` carries no temperature. **DERIVED, not AUTHORITATIVE event** (P1).

- **SALES OPPORTUNITY (deterministic commerce):**

  Exact condition: `CommerceAction.OFFER_PPV` && `evaluate_ppv_eligibility == allowed` && `product_id` exists && `no pending` && not `handoff`/`aftercare`/`cooldown`/`rejected`. Authority is `commerce/execution.py:87 11-gate sequence`. Dashboard must not duplicate engine; it should observe `commerce_offers` where `state=pending` and `created_at` recent, or listen for `commerce.offer_created` event (which does NOT exist yet -- **G-03**). So countable via `SELECT COUNT(*) FROM commerce_offers WHERE creator_id=$1 AND state IN ('pending','clicked')` but not realtime.

- **AWAITING PURCHASE (DropFans state):**

  Use `commerce_offers.state` `pending/clicked` vs `purchased` plus `fangate_transactions.event_type='dropfans_sale'` where `user_id IS NULL` (unattributed) vs `user_id NOT NULL`. Not payment state inference. `has_purchased_product` `commerce/dao.py:392` checks `state='purchased' AND transaction_id IS NOT NULL`. Time window: offer `expires_at` nullable, no explicit expiry unless set. So `awaiting = pending` is correct, not `pending >48h`.

**All four** currently **DERIVED** via polling `messages`/`commerce_offers`/`fangate_transactions`, not realtime, and need creator-scoped queries.

## 10. Commerce Event Analysis

| Commerce moment | Authority | Current event | Can observe? | Gap |
|---|---|---|---|---|
| Offer decision/eligibility | `execution.py:87` `CommerceDecision` | **NONE** | NO -- `record_eligibility_decision` audit only | Needs `commerce.eligibility_checked` |
| Offer created (pending) | `dao.py:57 create_offer_serialized` advisory lock | **NONE** | NO -- `G-03` | Needs `commerce.offer_created {offer_id,product_id,price_minor,generation_id}` |
| Offer sent (message combined) | `llm_worker` -> `send_messages` | `message.sent` generic | PARTIAL -- cannot tell which offer without joining `messages` content | Needs commerce-specific payload |
| Offer clicked | `dao.py:500 SELECT pending -> clicked` | **NONE** | NO | Needs `commerce.offer_clicked` |
| Offer declined/expired | `dao.py:540` | **NONE** | NO | Needs |
| Sale detected (DropFans) | `dropfans/service.py:734 poll_sales` | **NONE** | NO -- `G-05` | Needs `commerce.sale_detected` |
| Sale recorded (`fangate_transactions`) | `db/dropfans.py:218` | **NONE** | NO -- `G-07` | Needs `commerce.sale_recorded` |
| Attribution (pending -> purchased) | `dao.py:309` | **NONE** | NO -- `G-08` | Needs `commerce.sale_attributed` with generation_id if batch has one |
| Funnel transition | `revenue_intelligence.py:224 record_funnel_transition` | **NONE** | NO | Needs `funnel.transition` |
| Evidence/metrics | `dao.py:780 ppv_eligibility_decisions` | **NONE** | NO poll via `GET /commerce/eligibility-decisions` | Not realtime |

**Conclusion:** Commerce lifecycle is **silent best-effort** except final `message.sent`. Dashboard would need to poll many tables, not realtime. Smallest extension is additive `publish_event` after each `INSERT/UPDATE RETURNING` success, carrying `generation_id` (when available via `PurchaseRecord` -> `offer_id` -> lookup `generation_id`? For reconciliation batch, generation_id not available -- acceptable to emit with `generation_id=None` and rely on `creator_id+transaction_id`).

## 11. DropFans Observability

**Operations traced (files 1.1-1.10 above, summarized):**

- Product/drop/link creation: provider `POST /drops` `client.py:426`, mirror `fangate_products` synthetic id, **no event** `G-01`, dashboard poll `routes/fangate.py:404`.
- Check-status: `POST /api/external/drops/check-status` chunked 200 `client.py:478`, filtered `paid:true` `client.py:491`, transient `sales_map`, **no event** `G-05`, poll interval 120s `scheduler_worker.py:31`.
- Earnings: `GET /earnings?tz=UTC` `client.py:559`, `stats` + `transactions` with `buyer_email/name`, transient, **no event** `G-06`, PII risk if returned raw.
- Reconciliation: `reconcile_sales 760` loops earnings 7d, idempotent `ON CONFLICT`, **no event** `G-07`, manual `POST /dropfans-reconcile` poll.
- Attribution: atomic `UPDATE commerce_offers -> purchased` where exactly 1 candidate, `G-08` silent, polled via funnel.
- Fulfillment: `handle_post_purchase 170` best-effort steps (aftercare pending, funnel, confirmation, follow-up, media) **all silent** `G-09`, downstream `message.sent` generic only.
- Aftercare: `mark_aftercare_pending/completed` `dao.py:935` **silent** `G-10`, no route exposes `aftercare_status`.

**Provider secrets never to browser:** `Authorization: Bearer` header `client.py:124` never logged, `_audit` sanitized no secrets `service.py:74`, `file_path/download_url` signed 12h not to be leaked to buyers `post_purchase.py:348` but dashboard vault does expose to operators intentionally.

**Financial truth must be via `fangate_transactions` + `ppv_analytics_daily` + `sum_recorded_sales_cents`, not `PPV sent` or `check-status` alone -- verified via `reconciliation.py:54` window.


## 12. Revenue Truth (Authoritative)

**Sole authority:** `DropFans earnings reconciliation` -> `external transaction identity` -> `creator` -> `product` -> `buyer attribution` -> `purchase evidence` -> `revenue` -> `dashboard event`. Proven: `integrations/dropfans/service.py:611 get_earnings` + `760 reconcile_sales` -> `db/dropfans.py:218 INSERT fangate_transactions` -> `commerce/dao.py:349 ppv_analytics_daily` + `db/dropfans.py:299 sum_recorded_sales_cents`.

**Must NOT use:** `PPV created` `ppv_analytics_daily offers_created` `ppv_intelligence 41`, `PPV sent` `message.sent`, `check-status alone` `client.py:478`, `LLM inference` as revenue truth.

**Verified chain:**

```
DropFans earnings (GET /earnings, stats/transactions, transactionCount, typeTotals)
  -> external_transaction_id = dropfans:{sale_id} or hash(email,amount,paidAt) db/dropfans.py:201
  -> creator (scoped _run_scoped creator_id)
  -> product (synthetic id SHA256(dropfans_product_id) ddb:103)
  -> buyer attribution via reconciliation (user_id NULL -> attributed) dao:334 non-overwriting
  -> purchase evidence (commerce_offers.state purchased, ppv_analytics_daily revenue_minor)
  -> revenue = SUM(seller_earning) sum_recorded_sales_cents ddb:299
  -> dashboard event SHOULD BE commerce.sale_recorded (currently MISSING)
```

**Do NOT infer revenue from:** `ai.generation_completed` (no financial data), `PPV sent` (pending, not paid).

**PII:** `buyer_email/name/paidAt` in `fangate_transactions.buyer_email` column `drops.py:219` and memory `service.py:651` -- must not reach browser; dashboard must aggregate only.

## 13. Post-Purchase Observability

**Trace (proven):**

```
purchase (fangate_transactions + commerce_offers purchased)
  -> attribution (dao.py:334 non-overwriting, or reconciliation.py:183)
  -> fulfillment handle_post_purchase (outside txn, best-effort)
    -> funnel advance_funnel_to_converted UPDATE users.funnel_stage converted post_purchase.py:73
    -> purchase confirmation enqueue_send dedup post_purchase:{txn}:{user} -> message.sent (generic)
    -> follow-up schedule_follow_up INSERT scheduled_messages dedup post_purchase_followup:{txn} 24h post_purchase.py:307
    -> media deliver_product_media INSERT vault_media_deliveries + enqueue_send per vaultItem
  -> funnel intelligence record_funnel_transition (bounded 20, JSONB, no event)
  -> aftercare pending -> completed (dao.py:935-986) -- sent state never set, gap
  -> evidence ppv_eligibility_decisions, tool_audit_log, generation_telemetry (22/60 fields)
  -> metrics ppv_analytics_daily etc poll only
```

**Emits events?** Only downstream `message.sent` generic; `sale confirmed`, `aftercare_triggered`, `fulfillment_completed` are **SILENT** (no publish in `post_purchase.py`, `attribution.py`, `reconciliation.py`). Scheduler `workers/scheduler_worker.py:110 mark_aftercare_completed` silent debug log.

**Not claim aftercare merely because code exists:** Code exists but transitions `pending -> completed` directly, skipping `sent`; `sent` state never set (gap G-10). So aftercare observable only via `aftercare_status=completed` poll, not realtime.

## 14. Notification Audit

**What already produces notifications:**

- `message.send_failed` -> `chat.html:767 toast` red `showToast`
- `operator_queue.updated` / `suggestion.created` -> `queue.html:103 location.reload()` (not toast, full reload)
- `notes/tags/attention` -> broadcast but no toast, just reload or manual refresh
- Header bell `dashboard.html:496` polling `/api/stats` pending_queue count, not push

**Toast system:** `dashboard.html` / `vault.html` `routes` contain `showToast()` helper but only wired for `send_failed`.

**Desired notifications classification:**

**INFORMATIONAL (authoritative):**

- `message received` -- `message.created` YES existing, creator-scoped? partial (global ws) -> **CAN** with fix
- `reply sent` -- `message.sent` YES existing, add toast
- `fan active` -- **DERIVED** not authoritative, needs definition `active` window, duplicate risk

**COMMERCE (authoritative vs derived):**

- `new opportunity` -- **DERIVED** from `temperature HOT + readiness + aftercare` -- not authoritative, must reuse deterministic `present_offer` decision, not dashboard guess -> **REQUIRES** `commerce.offer_created` event
- `PPV sent` -- **MISSING** -- needs `commerce.offer_created` or `message.sent` with offer_id join -> **NEW EVENT REQUIRED**
- `sale detected` -- **AUTHORITATIVE** via `fangate_transactions` INSERT -- but no event -> **NEW**
- `revenue generated` -- **AUTHORITATIVE** via earnings reconciliation sum -- no event -> **NEW**, duplicate protection via `external_transaction_id`

**LIFECYCLE:**

- `aftercare triggered` -- **AUTHORITATIVE** via `mark_aftercare_pending` -- no event -> **NEW**
- `fan returned` -- **DERIVED** gap `>48h` `core/conversation_state.py` returning -- could derive from `last_seen` but not authoritative event
- `conversation reopened` -- similar

**NEGATIVE:**

- `sale lost` -- **NOT DETERMINISTIC** (see Section 12 deep dive) -- must not be guess
- `send failed` -- **YES** `message.send_failed` authoritative
- `commerce blocked` -- via `handoff`/`is_global_paused` -- no event, could derive from `ai.generation_failed` `handoff` reason
- `handoff` -- `handoff` stored via `UserState`? No event, only `operator_queue` pending

**All notifications must be creator-scoped, deduplicated via `event_id` + `generation_id`/`external_transaction_id` + `offer_id`.**

## 15. WebSocket/PubSub Audit (Deep)

**Enter PubSub:** `core/event_bus.py:50 publish` best-effort, never propagates.

**Reach WebSocket:** `event_subscriber.py:64 broadcast` -> `ws_manager.py:68 send_text` per `dialog_id` or `is_global`. Verified: `CHAT.html` receives via `realtime.js:76`.

**Creator scoping (FAIL):** No `creator_id` in event, `ws.py:32` always global, so all dashboards receive all users' events. Fix needs `publish_event` optional `creator_id` and `ws_manager` filter by `creator_ids`.

**Reconnect:** `realtime.js:116 jittered exponential 1s->30s` with `4001` auth fail no reconnect; `onopen _pollingActive false`, `onclose true` restores polling.

**Duplicate:** `event_id` dedup 200 `realtime.js:68`, `send_dedup` 3600s Redis, `scheduled_messages` dedup_key, `vault_media_deliveries` unique, `generation_id` in `strategy_learning:247 seen` -- all prevent duplicate notifications if reused.

**Ordering:** PubSub no ordering guarantee across workers; `ai.generation_completed` could arrive before `message.sent` if `publish_event` after `enqueue_send` but before `main` processes? Actually `ai.generation_completed` emitted after `enqueue_send` succeeds `llm_worker.py:1287`, while `message.sent` emitted later after Telethon `main.py:344`. So order is `generation_completed` before `message.sent` -- correct per `AGENTS.md:113`. Dashboard must use `timestamp_ms` + `generation_id` sequence, not arrival order.

**Backpressure:** No explicit; `ws_manager` `await send_text` per conn no buffering, `event_subscriber` does not await slow consumers; slow client may miss events (transient).

**Polling fallback:** Preserved via `isPollingActive` guard on pages that implement it (overview, analytics) but not on queue/chats -- **PARTIAL**.

**Browser reconciliation:** On refresh, `realtime.js` new `event_id` cache empty, missed events not replayed; must fetch via `polling` `GET /api/messages/recent` etc. For generation timeline, must fetch `generation_telemetry` + `messages` persisted.

**Event loss:** PubSub transient; if Redis down or subscriber restarting, events lost (logs warning). Polling must compensate.

## 16. Event Ordering

**Can arrive out of order:**

- `ai.generation_completed` vs `qwen.completed` -- no separate `qwen.completed` event, so not applicable; dashboard should treat `generation_completed` as after Qwen/scoring (since emitted after both).
- `message.sent` vs `send.started` -- no `send.started` event; `message.sent` is after `client.send_message` success.
- `vault.media_sent` may arrive after `message.sent` for same generation (two events, order not guaranteed, but both share same `generation_id`, dashboard should group).

**Handling:** Use `timestamp_ms` `event_bus:35` (server time) + `generation_id` grouping, not WebSocket arrival order. For stage model, derive `RECEIVED (message.created ts)` -> `PROCESSING (generation_started ts)` -> `SEND_QUEUED (generation_completed ts)` -> `SENT (message.sent ts)`.

## 17. Duplicate Event Handling

**Can duplicate via:**

- Retry `process_message` same `generation_id` md5 deterministic -- `publish_event` will emit same `generation_id` but new `event_id`; `realtime.js` dedup only on `event_id` (different), so duplicate `generation_completed` could show twice. Need `generation_id` dedup for lifecycle.
- XAUTOCLAIM reprocessing after crash -> same.
- Browser reconnect not duplicate (new `event_id` each publish).
- PubSub at-most-once, no duplicate from Redis itself.
- `bulk_ops.py:91` loops `N` `publish` per user -- could spam `conversation.assigned` N times.

**Must not show duplicate revenue:** For commerce, `creator_id` + `external_transaction_id` (`dropfans:{sale_id}`) is authoritative idempotent (`ON CONFLICT DO NOTHING` `drops.py:218`). Dashboard toast must dedup via `external_transaction_id` cache per creator, not just `event_id`.

**For generation:** `generation_id` authoritative dedup via `strategy_generation_seen` etc, but frontend needs `generation_id` dedup cache beyond `event_id` (e.g., ignore second `ai.generation_completed` with same `generation_id` if already completed).

**Current:** `realtime.js` dedup only `event_id`, not `generation_id` -- **P2** duplicate notification risk on retry.

## 18. Persistence vs Realtime State

| Datum | Classification | Survives browser refresh? | Survives WS reconnect? | Survives worker restart? | Survives app restart? |
|---|---|---|---|---|---|
| `sale` (`fangate_transactions`) | Persisted | YES via poll | YES | YES | YES (PG) |
| `revenue` (`sum_recorded_sales_cents`) | Persisted | YES | YES | YES | YES |
| `fan knowledge` (`user_profiles.facts`) | Persisted | YES | YES | YES (PG + memory) | YES (PG, memory lost) |
| `offer state` (`commerce_offers`) | Persisted | YES | YES | YES | YES |
| `evidence` (`ppv_eligibility_decisions`) | Persisted | YES | YES | YES | YES |
| `generation_telemetry` (22 cols) | Persisted (partial) | YES | YES | YES | YES |
| `Qwen currently running` | Realtime/transient | NO (lost) | NO | NO | NO |
| `send currently running` (`send_messages` PEL) | Realtime (but Stream persisted) | YES via `XINFO` pending count, but not via PubSub replay | YES (PEL) | YES (Stream) | YES (Stream if Redis persisted) |
| `worker processing` (`_telemetry_cache`) | Realtime | NO | NO | NO (memory) | NO |
| `23 active fans` (derived count) | Derived | YES via query `messages` last 15m | YES | YES | YES |
| `8 hot` (derived) | Derived | YES via `temperature` last generation query | YES | YES | YES (if telemetry persisted) |
| `current execution stage` (derived) | Derived | YES via `generation_id` + `messages` + `generation_telemetry` | YES | YES | YES |
| `event stream` (`chatbot:events`) | Realtime transient | NO -- lost if not connected | NO | NO | NO |

**Key:** Realtime ` Qwen/send running` is transient, but `SENT`/`sale` are persistent, so dashboard can reconstruct current stage after refresh via persisted `messages` + `generation_telemetry` + Stream pending counts, without relying on missed PubSub.

## 19. Dashboard Bridge Audit (Routes)

**Existing routes can provide (checked):**

- `live fans` -- `GET /api/messages/recent` (global, no creator) `routes/messages.py:139` -- **FAIL** needs `WHERE creator_id` join, pagination `LIMIT 20` not N+1 but global.
- `conversation state` -- `GET /api/dialogs/{id}/messages` `routes/dialogs.py:48` poll `messages` -- creator-scoped via `user_id` param but `user_id` global, no creator filter.
- `execution stage` -- **NO** dedicated route; could derive via `GET /api/dialogs/{id}/ai-intel` `chat.html:909` which fetches `generation_telemetry`? Actually `routes/aiIntel`? Not inspected, but `core/telemetry` exposes via `dashboard/routes/aiIntel`? Unknown.
- `commerce opportunities` -- `GET /api/fangate/creators/{id}/commerce/offers?state=pending` **PASS** creator-scoped.
- `sales` -- `GET /api/fangate/creators/{id}/commerce/offers?state=purchased` **PASS**
- `revenue` -- `GET /api/fangate/creators/{id}/dropfans-transactions` + `sum_recorded_sales_cents` -- **PASS** but PII exposure risk if raw transactions returned (see Privacy).
- `recent events` -- **NO** endpoint; only `messages` and `generation_telemetry` via stats.

**Creator scoping in routes:**

- `fangate` routes **PASS** (all require `creator_id` path param, SQL `WHERE creator_id=$1`)
- `followups` `GET /api/followups?creator_id=$1` **PASS** `routes/followups.py:43`
- `vault` deliveries check per `creator_id` **PASS** `routes/vault.py:257`
- `notes/tags/attention` routes take `user_id` param but **no creator check** -- could leak across creators if same `user_id` exists under both.

**Pagination/N+1/Performance:**

- `bulk_ops.py:91,132,179` loops per user `publish_event` N times + per user DB query -- N+1 spam, no batch.
- `messages` recent `LIMIT 20` fine, but no index on `created_at` alone, only `user_id,created_at` -- global `ORDER BY created_at DESC LIMIT 20` may scan.
- Redis round trips: `is_send_duplicate` + `mark_send_dedup` per send (2), `check_rate_limit` LUA (1) -- per fan per message, acceptable for 10k fans (10k*3 Redis calls burst).
- DB indexes: `messages.user_id_created` helps per-user, not global; `generation_telemetry` indexes help per creator, but `funnel_journey` JSONB not indexed.

**If new routes necessary:** Only for `live fans` aggregated count (new `GET /api/creator/{id}/live-fans` with `WHERE last_message_at > NOW()-interval`) and `execution stage` timeline (new `GET /api/generation/{id}/stage` derived). Otherwise reuse existing.


## 20. Frontend Audit

**Existing realtime wiring:**

- `realtime.js` loaded in `dashboard.html:532`, `chat.html:626`, `queue.html:103`, `overview.html:270`, `analytics.html:*`
- `chat.html:687 message.created -> append to chat`, `698 message.sent -> append + update status`, `709 ai.generation_started -> spinner _activeGenerationId`, `731 ai.generation_completed -> if gid != active skip else render`, `754 generation_failed -> error`, `767 send_failed -> toast`, `773 suggestion.created -> loadSuggestions` -- per-dialog filtering via `dialogId`.
- `dashboard.html:496 bell badge` polling `/api/stats`, not realtime.
- `analytics.html` live counters via `analytics-api.js` polling 15s if `isPollingActive`.

**Toast/notification components:**

- `showToast()` exists in `chat.html` but only for `send_failed`; `vault.html` has similar; no global toast manager for commerce.
- `queue.html` full `location.reload()` on `operator_queue.updated` -- jarring, not toast.

**Reuse:** Existing `realtime.js` + `ConnectionManager` + `isPollingActive` guard should be reused. No second frontend realtime system. Additive handlers for new `commerce.*` events can be registered via `rt.on("commerce.offer_created", ...)` and call existing `showToast`.

**Missing:** No `hot`/`opportunity`/`sale` toast, no live fan counter component, no execution-stage timeline component.

## 21. Performance

**Per-message events (current):** `message.created` (1) -> `ai.generation_started` (1) -> `ai.generation_completed` (1) + `suggestion.created` (0-1) -> `message.sent` (1) + `vault.media_sent` (0-1) = **4-6 events per inbound** (generation lifecycle). Each event `event_id` UUID + JSON ~300 bytes, Redis publish + WS fanout `N dashboards` (1-5 operators) -- negligible.

**Estimate:**

- 10 fans at 1 msg/min -> 50 events/min -> 0.8 events/s -- fine.
- 100 fans -> 8 events/s -- fine.
- 1,000 fans -> 80 events/s -- Redis PubSub + `ws_manager` `send_text` per conn no buffering -- may saturate one `event_subscriber` task but still <100 msg/s, typical Redis handles 10k/s.
- 10,000 fans -> 800 events/s -- **P1 risk**: `broadcast` iterates `_connections` and awaits `send_text` per event per conn. With 10 dashboards, 800*10=8000 sends/s, each `await` could backpressure. No backpressure queue, slow client may block others (though `send_text` no lock during IO, still sequential per event). Should be per-generation, not per-token.

**Per-token Qwen stream:** Not present (correct, dashboard observes lifecycle, not tokens).

**Bulk ops:** `bulk_ops.py:91` publishing N events per bulk (N fans) could spike: 1k fans bulk assign -> 1k publishes in loop, no batch.

**Avoid spam:** Events should be per generation/commerce transition/sale, not per internal operation (e.g., not per `pressure` calculation). Current 4-6 per message is right level.

**Dashboard queries:** `live fans` derived count `SELECT COUNT(DISTINCT user_id) WHERE last_message_at > NOW()-15m AND creator_id=$1` needs index `messages(created_at)` or `users(last_seen)` but current `messages` global query without creator will be N+1. New creator-scoped query must have index.

## 22. Privacy

**Must NOT receive in browser:** Telegram session data, tokens, API keys, DropFans credentials, buyer email, raw provider auth, full sensitive message content unless dashboard auth explicitly permits, internal secrets.

**Current payload minimum:**

- `message.created` includes `content` full -- **PII** but dashboard `chat.html` needs it to display fan message (existing auth permits, operator is trusted). For live activity counter, content not needed -- could use preview or omit.
- `ai.generation_started` includes `message_preview 100` -- bounded, acceptable.
- `message.sent` includes `content` full -- same tradeoff.
- `vault.media_sent` includes `fangate_media_id` not `file_path` -- good (file_path not leaked).
- `offer/sale` events **do not yet exist** but must not leak buyer email, API keys, raw sales `paidAt` etc. Must be `fan_id/user_id, creator_id, generation_id, event_type, stage, timestamp, safe metadata (productId, price_minor)` only.

**Checks:**

- `integrations/dropfans/client.py:124` `Authorization: Bearer` never logged, `service.py:74` audit sanitized.
- `db/dropfans.py:219` `buyer_email` stored, but `routes/fangate.py:404` does not return it in `GET /dropfans-drops` (only `buy_url`); however `routes/fangate.py:239` vault does expose `download_url` signed to operators only.
- `core/event_bus.py:35` `data` is arbitrary dict, no redaction -- callers must not put secrets in `data`.

**Recommendation:** Keep `generation_id, creator_id, user_id, event_type, stage, timestamp_ms` + safe display metadata (e.g., `offer_id`, `product_name`, `amount_cents` as integer, not email). Do not transmit full prompts/LLM responses/provider payloads.

## 23. Failure / Recovery

| Stage | Failure | Existing behavior | Observable? | Dashboard should show |
|---|---|---|---|---|
| LLM/Qwen fails | `generate_draft` exception | `llm_worker.py:1426` publishes `ai.generation_failed` generation_id, `move_to_dlq` persists payload, `generation_telemetry` success=false | YES via `generation_failed` | Error state, not auto-retry from dashboard |
| Scoring fails | `score_draft` exception | bubbles to same `except` -> `generation_failed` | YES | Failed |
| Rate limit | `main.py:116 not allowed` | `ack + enqueue_send(...,generation_id)` requeue | NO dedicated event, but `message.sent` delayed | WAITING (via send queue PEL count) |
| Telegram send fails | `UserIsBlockedError` `main.py:364` | `ack`, `publish message.send_failed generation_id`, `save_outbound` not called | YES | Send failed toast |
| DropFans request fails | `client.py:477` `DropfansError` | caught per call, `_audit` logs, no event, scheduler logs warning | NO | Silent -- needs observability event |
| DropFans timeout | same | retry via `service.py:744 _run_scoped`? No retry, best-effort | NO | Silent |
| Redis reconnects | `event_subscriber.py:70` backoff 1->30s, `redis.py` separate pool | `event_bus.publish` returns None, not crash; Streams PEL preserved if Redis persistence | YES via subscriber warning logs, but not to dashboard | Polling fallback must show stale |
| Worker crashes | `llm_worker` pending PEL | `XAUTOCLAIM` 30s preserves `generation_id` | YES via `requeue_stalled_messages` log, not event | WAITING then retry |
| WebSocket disconnects | `realtime.js:82 onclose` | `_pollingActive true`, `scheduleReconnect` jitter | YES via `ondisconnected` handler | Polling resumes |
| Browser reconnects | new WS `Dedup 200` empty, missed events lost | Must fetch via `poll` `GET /api/messages/recent` | Partial | Reconciliation via persisted `messages`/`generation_telemetry` |

**Dashboard is observational only:** `Qwen failure -> existing fallback/authority behavior -> observability event` NOT `dashboard event -> dashboard decides retry`. No code path where dashboard publish influences commerce.

## 24. Authority Boundaries

**Hierarchy authoritative (unchanged `commerce/decision.py`):**

```
SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM
```

- `llm_worker.py:644` `resolve_and_run_commerce` + `decide` + `select_commerce_response` -> `score_draft` with `is_authorized_commerce` price bypass -- ensures **LLM has no commerce authority**. LLM is language-only; if it invents product/price/url, `score_draft` flags and routes to operator queue (not auto-sent). **PROVEN** `core/scoring.py:86` + `llm_worker.py:1136`.

- `DropFans = purchase authority` -- financial truth via `reconciliation.py:54` `fangate_transactions` unique, `sum_recorded_sales_cents` -- CRM never invents sale. Verified no `PPV sent` inferred as revenue.

- **Dashboard does not become second decision path:** Current `dashboard/routes` never call `decide`/`execute_ppv` directly except via `enqueue_inbound` for AI reply (which re-enters normal LLM path, not bypass). No `dashboard` route calls `commerce/execution.py` directly. All commerce remains via `llm_worker` deterministic engine.

**Must remain:** `realtime` events are best-effort (`event_bus:52`), business never depends on `publish_event` success (`llm_worker:1426` except still raises, `main.py:344` after `ack_send`), so dashboard down does not block sends.

## 25. Canary Observability

**Potential data:** `1% ACTIVE, HOLD, sample=0, observation window, health, promotion authorization` via `agent/canary.py`, `commerce/production_control.py`, `workers/scheduler_worker.py`.

**Inspected:**

- `core/config.py:38 enable_websocket` global kill-switch, not per-canary.
- `workers/llm_worker.py:962 should_use_agent CanaryConfig` routes 1% via `should_use_agent` deterministic hash (user_id+creator_id) -- `agent/canary.py`.
- `commerce/production_control.py` has `is_global_paused`, `is_creator_paused`, `production_state` etc, but no dashboard route exposes `canary state` directly.
- `chatbotv2/dashboard/routes/health.py` (if exists) returns `DB/Redis` health, not canary.

**Can dashboard display safely without changing rollout?**

- **YES** if read-only: `GET /api/canary/status?creator_id` returning `active/hold, sample rate, window, health` from `production_control.get_production_health` etc, polled, no POST.
- **Must NOT:** change rollout state, create synthetic metrics, promote automatically, dashboard-driven promotion. All promotion remains manual via `commerce/production_control` or `agent/canary` config, not dashboard write.

**No automatic promotion via dashboard observer -- verified no dashboard route calls `promote` logic.**


## 26. Master Event Matrix

| Event | Producer | generation_id | creator_id | fan/user id | Persistent? | Realtime? | Dashboard? | Notification? | Commerce authority? | Privacy risk | Missing instrumentation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `message.created` | `handlers.py:70` | PROVEN YES md5 | MISSING | PROVEN YES user/dialog | transient + `messages` persisted | YES | YES (global ws, needs creator) | informational | NO | PII content bounded | Need creator_id |
| `ai.generation_started` | `llm_worker.py:625` | PROVEN YES | MISSING | PROVEN | transient | YES | YES (per dialog) | informational | NO | preview 100 | Need creator_id |
| `ai.generation_completed` | `llm_worker.py:1287` | PROVEN YES after enqueue | MISSING | PROVEN | transient | YES | YES | informational | NO | draft | Need creator_id |
| `ai.generation_failed` | `llm_worker.py:1426` | PROVEN YES | MISSING | PROVEN | transient | YES | YES | negative | NO | safe | Need creator_id |
| `suggestion.created` | `llm_worker.py:1257` | PROVEN YES | MISSING | PROVEN | transient + `operator_queue` row | YES | YES | informational | NO | draft | Need creator_id |
| `message.sent` | `main.py:344` | PROVEN YES post-42B | MISSING (data field) | PROVEN | transient + `messages` persisted | YES | YES | informational (reply sent) | NO | content | Need creator_id |
| `vault.media_sent` | `main.py:354` | PROVEN YES | MISSING | PROVEN | transient + `vault_media_deliveries` | YES | YES | commerce (PPV sent) if enriched | NO | safe media id | Need creator_id, offer link |
| `message.send_failed` | `main.py:385` | PROVEN YES | MISSING | PROVEN | transient | YES | YES toast | negative (send failure) | NO | safe | Need creator_id |
| `operator_queue.updated` | `send_worker.py:101` | MISSING (not lifecycle) | MISSING | PROVEN user | transient | YES | YES queue reload | lifecycle | NO | safe | Should be global scope, add creator |
| `conversation.note_changed` | `routes/notes.py:92` | MISSING | MISSING | user in data | transient | YES | YES | informational | NO | note content | FAIL global broadcast, needs creator+dialog |
| `conversation.tag_changed` | `routes/tags.py:102` | MISSING | MISSING | user in data | transient | YES | YES | lifecycle | NO | safe | FAIL global |
| `conversation.attention_changed` | `routes/attention.py:44` | MISSING | MISSING | user in data | transient | YES | YES | lifecycle | NO | safe | FAIL global |
| `commerce.offer_created` | **MISSING** | MISSING | MISSING | MISSING | would be `commerce_offers` | NO | NO | commerce (PPV sent) | YES (product decision) | safe (price) | **P1** needs additive event |
| `commerce.sale_detected` | **MISSING** | MISSING | MISSING | MISSING | would be `fangate_transactions` | NO | NO | commerce | YES (DropFans) | no PII | P1 needs |
| `commerce.sale_recorded` | **MISSING** | MISSING | MISSING | MISSING | `fangate_transactions` persisted | NO | poll only | revenue | YES | safe | P1 needs |
| `commerce.sale_attributed` | **MISSING** | MISSING | MISSING | MISSING | `commerce_offers` purchased | NO | NO | commerce | YES | safe | P1 needs generation_id if possible |
| `commerce.purchase_confirmed` | **MISSING** | MISSING | MISSING | MISSING | `messages` confirmation | NO (only generic message.sent) | poll | lifecycle (aftercare) | NO | safe | P1 needs |
| `commerce.aftercare_pending/completed` | **MISSING** | MISSING | MISSING | MISSING | `aftercare_status` persisted | NO | NO | lifecycle | NO | safe | P2 needs |
| `funnel.transition` | **MISSING** | MISSING | MISSING | MISSING | `funnel_journey_by_creator` JSONB | NO | NO | informational | NO | safe | P2 needs |
| `commerce.eligibility_checked` | **MISSING** | MISSING | MISSING | MISSING | `ppv_eligibility_decisions` | NO | poll | informational | YES | safe | P2 needs |

**Legend:** PROVEN = read, MISSING = not emitted, persistent = PG/Redis Stream, realtime = PubSub+WS, commerce authority = whether event can claim financial/product truth.

## 27. Execution-Stage Matrix

| Stage | Actual code location | Current event | Can derive? | Needs new event? | Persistent evidence | Realtime safe? | Failure state | Recovery |
|---|---|---|---|---|---|---|---:|---|
| `RECEIVED` | `handlers.py:58 save_inbound` | `message.created` YES | YES from message.created | NO | `messages` inbound row | YES transient + persisted | `message.created` missed -> poll `messages` | Refresh `GET /messages/recent` |
| `DEBOUNCING` | `redis.py:303 debounce_enqueue` `handlers.py:108 sleep 3s` | NONE (transient list) | YES via `debounce:{user_id}:messages` len? Not exposed | NO (derived) | `debounce:{user}` TTL 3s | PARTIAL (transient) | Window owner false -> typing event only | Poll not needed |
| `QUEUED` | `redis.py:182 XADD inbound_messages` | NONE (stream) | YES via `XINFO STREAM` pending count | NO (derived) | Stream PEL | YES via stream pending if dashboard had read-only `XLEN` | XG read fails -> DLQ | `XAUTOCLAIM` |
| `PROCESSING` | `llm_worker.py:1502 read_inbound` `550 lock` | `ai.generation_started` | YES from started | NO | `generation_telemetry` in-memory | YES | lock false -> `routing=locked` | retry after TTL 60s |
| `CONTEXT` | `memory/context.py:488 build_qwen3_context` `571` | NONE | YES via `ai.generation_started` timestamp + `context_build_ms` in telemetry | NO | telemetry `context_build_ms` (partial) | YES (derived) | fallback low-information | `telemetry` truncated but time exists |
| `COMMERCE_SIGNAL` | `commerce/deepseek.py:162` `637` | NONE | YES via `telemetry` if persisted (but truncated) | NO | truncated | YES derived | empty_context -> low-information signal | fallback |
| `DECISION` | `conversation_operations.py:635 build_operation_decision` `784` | NONE | YES via `decision_trace` 480 chars in telemetry (partial) | NO | truncated | YES derived | `handoff`/`aftercare` priority | policy_allows |
| `SAFETY_GATE` | `production_control.py:509` `837` | NONE | YES via `operation_allowed`/`block_reason` in telemetry | NO | truncated | YES derived | `autonomous_paused` flag | HOLD |
| `QWEN` | `llm_worker.py:1049 generate_draft` | `ai.generation_started` -> `ai.generation_completed` gap | YES via time between events | NO (already covered) | telemetry `generation_latency_ms` | YES | `generation_failed` | fallback to plain draft |
| `SCORING` | `core/scoring.py:86` `1136` | `ai.generation_completed` carries `score/flags` | YES | NO | telemetry `scoring_score/flags` | YES | `flags` present -> operator queue | human review |
| `AUTHORITY` | `llm_worker.py:1162` second gate + `1270` | `ai.generation_completed` `was_auto_approved` | YES | NO | `routing_decision` | YES | `autonomous_paused` -> score 0.1 | HOLD |
| `SEND_QUEUED` | `redis.py:71 XADD send_messages` | `ai.generation_completed` was_auto true implies queued | YES via `generation_completed` after `enqueue_send` | NO | Stream PEL | YES | enqueue throws -> `generation_failed` | DLQ |
| `SENDING` | `main.py:275 send_file/282 send_message` | NONE (transient) | YES via `send_messages` PEL not empty | NO (derived) | Stream PEL (pending) | PARTIAL (no event) | `message.send_failed` | DLQ + dedup |
| `SENT` | `main.py:344 message.sent` `db/postgres.py:315` | `message.sent` YES | YES | NO | `messages` outbound persisted | YES | `message.send_failed` | DLQ |
| `OUTCOME` | `adaptive_optimization.py:38` `1395` | NONE (telemetry outcome) | YES via `generation_telemetry` `outcome` (partial) | NO | truncated | YES derived | `outcome` null if telemetry lost | poll `messages` |
| `FAILED` | `llm_worker.py:1426` `main.py:385` | `ai.generation_failed` / `message.send_failed` | YES | NO | DLQ `dead_letter_queue` | YES | DLQ entry | replay |
| `WAITING` | idle | NONE | YES via absence of pending | NO | none | YES | - | - |

**Needs explicit events:** Only `commerce` stages (`offer_created`, `sale_recorded`) need new events; all core AI stages can be derived from existing `message.created`/`ai.generation_*`/`message.sent` + `generation_id` + `timestamp_ms`.

## 28. Notification Matrix

| Notification | Authoritative source | Current event | Can implement from existing data? | New event required? | Deterministic? | Duplicate protection | Creator scoped? |
|---|---|---|---|---|---|---|---|
| `Reply sent` (??) | `message.sent` `main.py:344` | PROVEN YES | YES -- listen `message.sent` | NO | YES (per generation) | `event_id` dedup 200 + `generation_id` | PARTIAL (global ws) |
| `Conversation hot` (??) | `temperature=HOT` bucket `conversational.py` / `temperature` | MISSING (no `hot` event) | PARTIAL -- could derive from `ai.generation_completed` + `telemetry.temperature` but not in event payload | YES needs `commerce.temperature` in `generation_completed` data or separate `commerce.hot` | YES if reuse `temperature` | `generation_id` per generation | Needs creator |
| `Fans active` (?? 23 active) | `messages` last 15m per creator `get_recent_messages` | NO event, poll `messages` | PARTIAL -- can query `COUNT WHERE last_message_at > NOW()-15m AND creator_id=$1` but `messages` lacks creator | YES needs creator-scoped query + optional `presence` event | YES (time window) | Not applicable (count) | FAIL (no creator column) |
| `New sales opportunity` (??) | `CommerceAction.OFFER_PPV` && `evaluate_ppv_eligibility allowed` | MISSING | NO -- `offer_created` not emitted, must not guess from `temperature` | YES `commerce.offer_created` | YES (11-gate) | `offer_id` idempotent | YES |
| `PPV sent` (??) | `execute_ppv -> commerce_offers pending` | MISSING (only generic `message.sent`) | PARTIAL -- `message.sent` with `fangate_media_id` but need offer link | YES `commerce.offer_created` with price `price_minor` | YES | `offer_id` | YES |
| `Sale aftercare` (??) | `mark_aftercare_pending` `dao.py:935` | MISSING | NO -- silent | YES `commerce.aftercare_pending` | YES | `offer_id` aftercare_status | YES |
| `Sale lost` (?) | **NOT DETERMINISTIC** -- no terminal state `OFFER_SENT/PURCHASED/EXPIRED/DECLINED/ABANDONED` authoritatively `MISSING` | NO | NO -- must NOT guess `didn't buy quickly = lost` | **NOT CURRENTLY DETERMINISTICALLY REPRESENTABLE** -- would require `offer.expired`/`declined` events + TTL | NO | N/A | N/A |
| `Revenue generated` (??) | `fangate_transactions` `sum_recorded_sales_cents` | MISSING (purchase) | NO -- transaction INSERT silent | YES `commerce.sale_recorded` with `external_transaction_id` + `amount_cents` | YES | `external_transaction_id` per creator | YES |
| `Send failure` | `message.send_failed` `main.py:385` | PROVEN YES | YES | NO | YES | `generation_id` | PARTIAL |
| `Handoff` | `handoff` state `UserState`? | NO event, only `operator_queue` pending | PARTIAL -- `operator_queue` pending could imply handoff | YES `commerce.handoff` | YES (safety >) | `generation_id` | YES |

**At least 6 of 10** desired toasts require new deterministic events; 4 can be built from existing `message.sent`/`generation_failed` but need creator scoping and dedup.


## 29. Master Findings Table

| ID | Severity | File | Line | Evidence | Impact | Recommended Fix |
|---|---|---|---|---|---|---|
| **P0-01** | **P0** | `chatbotv2/dashboard/routes/ws.py` | 32 `is_global=True` | `manager.connect(ws, is_global=True)` always global, `ws_manager.py:54` `dialog_ids` never populated, `event_subscriber.py:64` correctly scopes but moot. Violates `AGENTS.md:173 scope invariant` -- every dashboard receives every `scope=user` event. | Creator A sees Creator B fan messages (PII leak), violates isolation. | Add optional `creator_id` to `publish_event`, carry via `event_subscriber` to `ws_manager.broadcast` filtering by `creator_ids`, make `ws.py` capture creator from `verify_session` and `connect(creator_ids={cid}, dialog_ids={uid})`. Additive, backward-compatible. |
| **P0-02** | **P0** | `core/event_bus.py` | 13 `publish_event` signature | No `creator_id` param, `ws_manager.py:12` no creator field, `event_subscriber.py:60` no creator branch. Single global channel `chatbot:events`. Financial truth over-broadcast risk. | Global channel + global ws = cross-creator event leak for commerce/revenue. | Add `creator_id: int|None=None` to `publish_event` and `event` dict, preserve existing callers (omit = None), update subscriber/manager to filter. |
| **P0-03** | **P0** | `chatbotv2/dashboard/routes/notes.py` | 92 `scope=global` | `publish conversation.note_changed` `tag_changed` `attention_changed` `bulk_ops.py:91` per user global broadcast, no `creator_id`/`dialog_id` filter. | All dashboards receive all notes/tags even for other creators' fans. | Change to `scope=user` with `dialog_id` and `creator_id` where available, or keep global but require `creator_id` in data and let frontend filter? Server-side filter preferred. |
| **P0-04** | **P0** | `commerce/attribution.py` + `reconciliation.py` + `dropfans/service.py` | -- | Sale `fangate_transactions` INSERT and `commerce_offers` `pending->purchased` transition have **no realtime event**, financial truth poll-only. No `sale lost` deterministic state (see Section 12) but revenue could be double-counted if dashboard infers from `PPV sent`. | Operator cannot trust revenue/realtime sales truth, may invent. | Add additive `publish_event` after successful `INSERT`/`UPDATE RETURNING` with `external_transaction_id` + `amount_cents`, `creator_id`, `generation_id` where available, best-effort, never block transaction. |
| **P1-01** | **P1** | `db/redis.py` | 310 `debounce:{user_id}` | Not creator-scoped vs lock `creator:{cid}:user:{uid}` is. Two creators same fan rapid messages share debounce window. | Fan isolation collision, one creator's rapid burst could suppress another's. | Change to `debounce:creator:{cid}:user:{uid}` or `debounce:{user_id}:{creator_id}` with fallback to global for legacy. |
| **P1-02** | **P1** | `workers/scheduler_worker.py` | 107 `enqueue_send(payload)` | No `generation_id` (SENDED for scheduled, post-purchase, operator-approved). Breaks `generation_id` invariant for those paths, `main.py:99` yields `None`. | Scheduled confirmations/media have `message.sent` with `generation_id=None`, cannot join to `ai.generation_started`. | Propagate `generation_id = f"sched-{dedup_key}"` etc. |
| **P1-03** | **P1** | `commerce/post_purchase.py` | 142,507 `enqueue_send` | Same as above, no gid. | Follow-up/media sent not attributable to purchase transaction. | Pass `generation_id = f"pp-{transaction_id}"` or `offer_id` derived. |
| **P1-04** | **P1** | `workers/send_worker.py` | 29 `enqueue_send` | Operator-approved queue item dedup `queue_item:{id}` without gid. | Approved message `message.sent` has `None` gid. | Pass `generation_id` from `operator_queue` if stored (needs column). |
| **P1-05** | **P1** | `db/postgres.py` | 2728 `INSERT generation_telemetry` 22 cols | `GenerationTelemetry` 60+ fields truncated, `commercial_objective, temperature, funnel_state` etc not persisted. | Dashboard `ai-intel` must recompute, historical hot/aftercare not queryable. | Additive migration to add `extra JSONB` or widen columns, store `to_dict()` full. |
| **P1-06** | **P1** | `db/schema.sql` | 22 `messages` | No `generation_id` column. Cannot correlate `message.sent` realtime with persisted row via gid (only telegram_message_id). | Reconnect must use content match, flaky. | Migration `ADD COLUMN generation_id TEXT` indexed, fill for new rows. |
| **P1-07** | **P1** | `core/telemetry.py` | 21 `uuid4` default | vs md5 `handlers.py:68` deterministic -- dual universes, but not break since callers override. | Confusing, but `start_generation(generation_id)` overrides, so PASS but document. | Unify: keep md5 as canonical, keep uuid4 only for fallback when caller omits gid. |
| **P1-08** | **P1** | `commerce/*` + `scheduler` | 0 publish | No `commerce.offer_created`, `sale_recorded`, `aftercare`, `funnel.transition` events. | Commerce invisible realtime, dashboard must poll `ppv_analytics_daily` and `commerce_offers`. | Additive `publish_event` best-effort after each `INSERT/UPDATE RETURNING`. |
| **P1-09** | **P1** | `chatbotv2/dashboard/static/js/realtime.js` | 68 dedup `event_id` | Not dedup by `generation_id`/`external_transaction_id`. Retry could duplicate toast. | Duplicate `?? $29.99` on retry. | Add `generation_id` dedup cache per `event_type` where applicable, or server-side idempotency via external_transaction_id. |
| **P1-10** | **P1** | `chatbotv2/dashboard/routes/ws.py` | 35 `receive_text` ping | No `creator_id` extraction from session, no `dialog_ids` capture. | Same as P0-01, but P1 for missing creator binding. | Capture creator from DB `sessions` or `operators` and bind. |
| **P2-01** | **P2** | `chatbotv2/dashboard/templates/chats.html` | 248 `chatsApp` | Zero polling/realtime, static list. | Stale `last_message` after new inbound. | Add `isPollingActive` polling 5s or `rt.on message.created` reload list. |
| **P2-02** | **P2** | `chatbotv2/dashboard/templates/queue.html` | 103 `location.reload()` | No `isPollingActive` guard, no toast, full reload. | WS down -> stale queue, operator misses. | Add `rt.onconnected` fetch via `GET /api/queue/pending` and `isPollingActive` interval. |
| **P2-03** | **P2** | `chatbotv2/dashboard/static/js/realtime.js` | 51 `onopen` | `chat.html` never checks `isPollingActive` before `loadSuggestions`, may double fetch. | Not isolation, just extra load. | Guard `if (!rt.isConnected()) fetch` already in `queue`, add to `chat`. |
| **P2-04** | **P2** | `workers/llm_worker.py` | 628 `message_preview 100` | Bounded PII leak via PubSub transient (operator-only, but still PII). | Acceptable per auth, but document. | Keep, but ensure not logged and only to authenticated WS. |
| **P2-05** | **P2** | `commerce/revenue_intelligence.py` | 224 `record_funnel_transition` | Bounded 20 per fan, idempotent via generation_id, but never emits event and no dashboard route reads `funnel_journey_by_creator`. | Funnel invisible. | Add `GET /api/funnel/{user_id}` and event. |
| **P2-06** | **P2** | `workers/scheduler_worker.py` | 77 `process_due_messages` | Claims `FOR UPDATE SKIP LOCKED` correct, but `reconcile_all` inside same loop could block. | Not isolation. | Keep as is, no fix needed Stage B. |
| **P2-07** | **P2** | `chatbotv2/dashboard/routes/bulk_ops.py` | 91 loop N publishes | N per user `publish_event` per bulk (1k fans -> 1k publishes). | Spam, backpressure. | Batch into single `bulk.assigned {user_ids:[], count}` event. |
| **P2-08** | **P2** | `integrations/dropfans/service.py` | 74 `_audit` | Only logs `operation,status` generic, no per-sale amount in log (good for privacy) but no event for dashboard. | Observability gap. | Add `commerce.poll_result` event. |
| **P3-01** | **P3** | `db/redis.py` | 15 `DRAFT_STREAM` | Constant defined but never used (legacy). | Dead code. | Remove or document. |
| **P3-02** | **P3** | `core/event_subscriber.py` | 35 `redis.from_url` | Separate pool from `db/redis.get_redis`, two pools. | Not fatal, but waste. | Reuse `get_redis` singleton. |
| **P3-03** | **P3** | `workers/scheduler_worker.py` | 227 `f"scheduler-{cid}-{ts}-{rec}"` | Not md5, not canonical, but scheduler not generation-scoped -- acceptable for internal operational_execution. | Not break, but inconsistent. | Document as non-generation path. |
| **P3-04** | **P3** | `commerce/feedback.py` | 213 `PURCHASE_COMPLETED` memory | Evidence memory-only, lost on restart. | Should be DB. | Move to `ppv_analytics_daily` or `behavioral_events` table. |

**Severity rationale:** P0 only for security isolation, financial truth, message loss, credential exposure (none found for credentials). Global ws + global note broadcast are P0 isolation failures. Sale silence is P0 financial truth (operator could be misled). Debounce and commerce gaps are P1 (correctness/observability). Polling gaps are P2.

## 30. Stage B Implementation Map (Additive, Minimal)

**Do NOT implement in Stage A -- map only.**

### C1 -- Execution stages (derived, not new events)

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `core/telemetry.py` | `GenerationTelemetry` + `db/postgres.py:2728` | Add `extra JSONB` or widen to persist `temperature, funnel_state, pressure, decision_trace` full dict | Persist hot/aftercare for polling | Migration needed, backward compat TEXT | Verify `generation_telemetry` extra round-trip |
| `chatbotv2/dashboard/routes/aiIntel.py` (new) | `GET /api/generation/{id}/stage` | Derive stage `RECEIVED->SENT` from `generation_id` + `messages` + `generation_telemetry` + Stream PEL `XLEN` | Truthful window without new events | No write, read-only | Unit: stage mapping for `locked`, `failed`, `sent` |
| -- | -- | Keep `RECEIVED->DEBOUNCING->QUEUED->PROCESSING->CONTEXT->DECISION->SAFETY_GATE->QWEN->SCORING->AUTHORITY->SEND_QUEUED->SENDING->SENT->OUTCOME->FAILED/WAITING` as derived enum, not new state table | Answers "what is bot doing" | Low | -- |

### C2 -- Realtime event instrumentation (additive via existing bus)

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `core/event_bus.py` | `publish_event` | Add optional `creator_id: int|None=None` param, add to `event` dict `creator_id`, keep existing callers working (omit=None) | Creator scoping without breaking Phase1 | Backward compat -- old subscriber ignores unknown field | Unit: publish with/without creator_id |
| `chatbotv2/dashboard/event_subscriber.py` | `start_event_subscriber` | Extract `creator_id=event.get("creator_id")` and pass `await get_manager().broadcast(event, dialog_id=..., creator_id=...)` | Forward scoping | No break | Integration: subscriber carries creator |
| `chatbotv2/dashboard/ws_manager.py` | `ManagedConnection`/`broadcast` | Add `creator_ids: set[int]` to connection, filter `if creator_id is not None and creator_id not in conn.creator_ids and not is_global` | Server-side creator isolation | Must populate `creator_ids` at `ws.py` connect | Unit: broadcast filter |
| `chatbotv2/dashboard/routes/ws.py` | `websocket_endpoint` | After `verify_session`, lookup `creator_id` for operator (via `operators` or `creator_integrations` session mapping) and `manager.connect(ws, creator_ids={cid}, dialog_ids=set(), is_global=False)` instead of `is_global=True` | Scope per creator | Auth change, `REQUIRES REVIEW` | Integration: ws only receives own creator events |
| `chatbotv2/handlers.py` | already has `generation_id` post-42B | No change -- already `publish message.created with generation_id` | -- | -- | Verify |
| `workers/llm_worker.py` | already propagates | Add `creator_id` to `publish_event` calls `ai.generation_*` via `_creator_id` | Commerce scoping | -- | Verify |
| `chatbotv2/main.py` | already propagates | Add `creator_id` from `data.get("creator_id")` to `publish_event` for `message.sent` | Commerce scoping | -- | Verify |
| `commerce/post_purchase.py` | `handle_post_purchase` after each `INSERT/UPDATE RETURNING` success | `try: await publish_event("commerce.offer_created",{offer_id,product_id,price_minor}, generation_id=gid, creator_id=cid, user_id=uid, scope="user") except: log` -- 5 events: `offer_created`, `sale_recorded`, `sale_attributed`, `purchase_confirmed`, `aftercare_*`, `funnel.transition` | Fill silent gaps, best-effort never blocks | Additive, old clients ignore | Unit: post_purchase publishes with generation_id where available, else None |
| `integrations/dropfans/service.py` | `reconcile_sales` after `INSERT ... DO NOTHING` where inserted | `publish_event("commerce.sale_recorded",{external_transaction_id,product_id,amount_cents,creator_id}, creator_id, user_id, generation_id=None, scope="user"/"global")` | Revenue truth realtime | Duplicate protection via transaction_id | Unit: dedup via transaction_id |

All new events reuse existing `CHANNEL="chatbot:events"` and `realtime.js` `handler[event_type]` -- old clients ignore unknown types, polling still fallback.

### C3 -- Live fan state (derived queries, no new persistence)

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `db/postgres.py` | new `get_live_fans(creator_id, window_mins=15, limit=100)` | `SELECT DISTINCT user_id FROM messages WHERE creator_id=$1 AND created_at > NOW()-interval` -- but `messages` lacks creator_id, so join via `commerce_offers` or `fangate_transactions` existence OR via `generation_telemetry.creator_id` last 15m; alternatively add `messages.creator_id` migration (P1-06) and backfill via `generation_telemetry` | Live count truthfully via persisted, not frontend guess | Migration for `messages.creator_id` is `REQUIRES REVIEW` (adds column, index) but minimal | Load test: 10k fans `COUNT` <500ms with index |
| `chatbotv2/dashboard/routes/stats.py` (new) | `GET /api/creators/{id}/live` | Expose `active, hot, awaiting, opportunity` counts derived from same queries above + `temperature HOT` via `generation_telemetry` last per fan `WHERE temperature='hot'` | Dashboard needs single endpoint | No N+1, single aggregated SQL | Unit: creator-scoped count |
| `db/postgres.py` | add GIN/partial index `idx_messages_creator_created` after migration | Speed live query | Migration | -- | EXPLAIN ANALYZE |
| Frontend | `dashboard.html:532` `loadCounts` | Poll new live endpoint 30s if `isPollingActive` else ws push | Reuse existing `loadCounts` | Low | Manual |

### C4 -- Commerce notifications (toasts via existing infrastructure)

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/static/js/realtime.js` | `on` handlers | Keep existing, no change to `realtime.js` core | Preserve dedup/backoff | -- | -- |
| `chatbotv2/dashboard/templates/chat.html` `queue.html` | `rt.on("commerce.offer_created", e=> showToast(...))` | Reuse `showToast()` already for `send_failed` | Minimal | No secret in toast payload | Unit: toast payload only `offer_id, product_name, price` not buyer email |
| -- | Dedup | Reuse `event_id` dedup 200 + `generation_id` per commerce offer (`offer_id`) cache | Prevent duplicate `??` | Low | Simulate retry duplicate `offer_id` |

### C5 -- Sales/revenue notifications

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `integrations/dropfans/service.py` | as C2 | `commerce.sale_recorded` must carry `external_transaction_id`, `amount_cents`, `creator_id`, `product_id`, `buyer attribution status` but **not** `buyer_email` | Revenue toast `?? We made $29.99` truthfully from `fangate_transactions` | Must not leak email | Verify payload has no email |
| `chatbotv2/dashboard/templates/earnings.html` | `rt.on("commerce.sale_recorded")` | Update revenue counter, show toast, dedup via `external_transaction_id` per creator (keep set 1000) | Realtime revenue | Low | Simulate 3x same transaction_id -> 1 toast |
| `commerce/reconciliation.py` | after `_reconcile_single` success | Also publish `commerce.sale_attributed` with `offer_id, user_id` | Aftercare trigger | -- | -- |

Explicitly **NOT** implementing `sale lost` -- reported as `NOT DETERMINISTICALLY REPRESENTABLE` (Section 12). Dashboard must not show `? We lost that sale` until `offer.expired`/`declined` deterministic terminal exists.

### C6 -- Dashboard bridge queries (creator-scoped)

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/routes/messages.py` | `api_recent_messages` | Change `SELECT ... FROM messages ORDER BY created_at DESC LIMIT 20` -> `JOIN generation_telemetry ON messages.telegram_message_id = ... WHERE generation_telemetry.creator_id=$1` or use `messages.creator_id` after migration, require `?creator_id=` query param, 403 if not owned | Fix P0 global leak | Migration + route change `REQUIRES REVIEW` | Test: creator A cannot see B's recent |
| `routes/notes,tags,attention` | publish scope | Change `scope=global` to `scope=user` with `dialog_id` + `creator_id` and `broadcast` filter | Fix P0 global note broadcast | -- | Test: note for user 123 not received by other creator |
| `routes/dialogs.py` `routes/attention.py` etc | list queries | Add `WHERE creator_id=$1` via join `commerce_offers` or `messages.creator_id` | Isolation | -- | -- |

### C7 -- Frontend execution timeline

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/templates/chat.html` | `rt.on("ai.generation_started/completed/message.sent")` | Extend `generation_completed` handler to render `decision_trace` + `score` + `temperature` from event `data` if present (future), else fetch `GET /api/generation/{gid}/stage` on demand | Timeline `Received->Sent` | No new LLM | Unit: timeline renders stages in order via `timestamp_ms` |
| `chatbotv2/dashboard/static/js/realtime.js` | no change to core | -- | -- | -- | -- |

### C8 -- Notification UI

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `dashboard/templates` | `showToast` global | Ensure `showToast` is global (in `base.html`) and `realtime.js` can call it for `commerce.*` events, with `creator_id` guard `if (e.creator_id !== currentCreatorId) return` | Creator-scoped toast | Frontend-only filter but server already filters | Manual |

### C9 -- reconnect/reconciliation

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `chat.html` `queue.html` | `rt.onconnected`/`ondisconnected` | On `onconnected`, fetch `GET /api/dialogs/{id}/messages?since=lastTimestamp` to fill missed `message.sent`; on `ondisconnected`, `isPollingActive true` resumes `loadSuggestions` interval | Reconnect safety | No duplicate due to `event_id` dedup | Simulate WS close 30s then reconnect, ensure no missing `sent` |
| `realtime.js` | already has `Dedup 200` `jitter` `ping` | -- | -- | -- | -- |

### C10 -- tests

| File | Function | Change | Why | Risk | Test |
|---|---|---|---|---|---|
| `tests/test_realtime.py` | add | `test_creator_isolation_ws` -- publish `message.sent` with `creator_id=1` should not reach ws connected as creator 2 | Fix P0 | -- | New |
| `tests/test_phase42_correlation.py` | extend | `test_message_created_carries_creator_id` after C2 | Verify | -- | New |
| `tests/test_commerce_pipeline.py` | add | `test_post_purchase_publishes_commerce_events` best-effort mock | Verify | -- | New |
| `tests/test_dropfans_commerce_integrity.py` | add | `test_sale_recorded_idempotent_via_transaction_id` | Financial truth | -- | New |
| `tests/test_dashboard_isolation.py` (new) | add | creator A cannot `GET /api/messages/recent?creator_id=B` | Isolation | -- | New |
| `tests/test_notifications.py` (new) | add | duplicate `commerce.sale_recorded` same transaction_id deduped | No duplicate revenue toast | -- | New |
| `tests/test_performance.py` | add | live fans 10k `EXPLAIN` | Perf | -- | New |

**Risk for all C1-C10:** Additive, best-effort `publish_event` never blocks, old clients ignore unknown `event_type`, `enable_websocket` kill-switch still controls all, no new worker/queue/LLM.


## 31. Test Plan (Stage B)

**Existing coverage that already protects:**

- `tests/test_realtime.py` -- event_bus publish, event_id UUID, enable_websocket guard, subscriber backoff, ws_manager broadcast
- `tests/test_phase42_correlation.py` -- canonical md5, inbound->generation, debounce, send, XAUTOCLAIM, retry, message.sent, legacy, telemetry isolation, duplicate idempotency, full chain (14 tests)
- `tests/test_phase1_regression.py` -- Phase 1 contract invariants (generation_started before completed, same generation_id, event_id unique, worker not importing ws_manager)
- `tests/test_inbound_idempotency.py` -- ON CONFLICT, no dual save
- `tests/test_dlq_recovery.py` -- replay with generation_id preservation (fixed)
- `tests/test_phase31_hardening.py` / `33/36/38/39` -- commerce isolation, DropFans integrity, personalization

**Missing regression to add in Stage B (do not add now, plan only):**

| Test File (new or extend) | Test | Asserts | Why |
|---|---|---|---|
| `tests/test_realtime.py` | `test_creator_isolation_ws` | Publish `message.sent` with `creator_id=1`, connect ws as creator 2, assert not received | P0 isolation |
| `tests/test_realtime.py` | `test_dialog_scoping` | Publish `scope=user dialog_id=123` should not reach `dialog_ids={456}` but should reach `is_global` | Scope invariant |
| `tests/test_phase42_correlation.py` | `test_message_created_carries_creator_id` | After C2, `message.created` event has `creator_id` and `generation_id` | Correlation |
| `tests/test_commerce_pipeline.py` | `test_post_purchase_publishes_commerce_events` | Mock `handle_post_purchase` with `publish_event` mock, assert `commerce.offer_created` etc emitted best-effort | Fill silent gaps |
| `tests/test_dropfans_commerce_integrity.py` | `test_sale_recorded_idempotent_via_transaction_id` | `record_dropfans_sale` twice same `sale_id` -> second `ON CONFLICT DO NOTHING`, only one `commerce.sale_recorded` toast via dedup | Financial dedup |
| `tests/test_dashboard_isolation.py` (new) | `test_creator_cannot_see_other_fans` | `GET /api/messages/recent?creator_id=2` as creator 1 -> 403 or empty, not leaked | Isolation |
| `tests/test_notifications.py` (new) | `test_duplicate_sale_toast_deduped` | Publish same `commerce.sale_recorded` `external_transaction_id` twice -> frontend `showToast` once via `event_id`+`transaction_id` cache | No duplicate revenue |
| `tests/test_dashboard_isolation.py` | `test_note_broadcast_creator_scoped` | `POST /api/notes` for user 123 as creator 1 -> `conversation.note_changed` not received by creator 2 ws | P0-03 fix |
| `tests/test_performance.py` (new) | `test_live_fans_query_uses_index` | `EXPLAIN SELECT ... WHERE creator_id` uses idx, <500ms for 10k fans | Perf |
| `tests/test_persistence.py` (new) | `test_browser_refresh_reconstructs_stage` | After `message.sent` persisted, refresh `GET /api/dialogs/{id}/messages` returns same stage as realtime | Reconnect safety |
| `tests/test_privacy.py` (new) | `test_sale_recorded_has_no_buyer_email` | `commerce.sale_recorded` payload has no `buyer_email` key | Privacy |
| `tests/test_canary_observer.py` (new) | `test_canary_status_readonly` | `GET /api/canary/status` returns HOLD/ACTIVE but POST promotion 403 | Canary safety |

**Do not confuse logs alone as authoritative state:** `tool_audit_log` etc are logs, not financial truth; tests must assert `fangate_transactions` as authority.

## 32. Final Verdict

**All acceptance criteria checked:**

- [x] Existing realtime architecture fully mapped (Section 2: event_bus, subscriber, ws_manager, realtime.js, Stream vs PubSub)
- [x] Every existing event inventoried (Section 3: 13 events + 8 missing commerce)
- [x] Full generation lifecycle traced (Section 4: Telegram inbound -> debounce -> XADD -> llm_worker -> Qwen/scoring -> send -> message.sent)
- [x] Phase 42B generation_id contract verified (Section 5: md5 32 hex preserved through XAUTOCLAIM/retry/send, post-42B PASS for core, PARTIAL for scheduled)
- [x] Creator isolation audited (Section 7: FAIL global ws + global note broadcast, P0)
- [x] Fan isolation audited (Section 8: FAIL debounce/user_id global, generation_id missing creator)
- [x] Execution stages mapped (Section 6: RECEIVED->SENT derived model, transient vs persisted)
- [x] Live fan activity definitions grounded (Section 9: active 15m derived, hot via temperature, opportunity via 11-gate, awaiting via pending state)
- [x] Commerce events mapped (Section 10: silent best-effort, needs offer_created etc)
- [x] DropFans financial truth traced (Section 11: product, check-status, earnings, reconciliation, attribution, fulfillment)
- [x] Post-purchase lifecycle traced (Section 13: purchase->aftercare, all silent except generic message.sent)
- [x] Notification infrastructure audited (Section 14: toast only for send_failed, queue reload, no commerce toasts)
- [x] Duplicate notification risks identified (Section 17: event_id dedup 200 + generation_id/transaction_id needed)
- [x] WebSocket/PubSub behavior audited (Section 15: backoff 1->30s, jitter, dedup, ping, isPollingActive guard)
- [x] Polling fallback preserved (Section 15: overview/analytics guard PASS, queue/chats FAIL)
- [x] Browser privacy audited (Section 22: no secrets, PII bounded preview, vault signed URLs intentional)
- [x] Dashboard queries audited for N+1/global scope issues (Section 19: fangate PASS, messages/notes FAIL global, bulk_ops N spam)
- [x] Persistence/recovery behavior mapped (Section 18: sale/revenue persisted, Qwen/send transient, derived counts)
- [x] Failure behavior mapped (Section 23: LLM/scoring/rate limit/send failures observable, DropFans silent)
- [x] Event ordering risks identified (Section 16: generation_completed before message.sent, use timestamp_ms)
- [x] Canary observability audited (Section 25: read-only status safe, no promotion via dashboard)
- [x] All P0/P1/P2/P3 findings documented (Section 29: 4 P0, 10 P1, 8 P2, 4 P3)
- [x] Stage B implementation map produced (Section 30: C1-C10 additive)
- [x] No production mutations performed (verified via `git status` -- only docs created)
- [x] No architecture redesign proposed (Stage B map additive via existing channel)

**Evidence confidence:** Every important finding includes `file:line` and `PROVEN` where read directly, `MISSING` where grep returned 0 hits for `publish_event` in commerce.

---

PHASE 42C STAGE A VERDICT

REALTIME EXECUTION VISIBILITY: PARTIAL
GENERATION CORRELATION: PASS
LIVE FAN STATE: PARTIAL
COMMERCE VISIBILITY: PARTIAL
SALES VISIBILITY: PARTIAL
REVENUE VISIBILITY: PARTIAL
POST-PURCHASE VISIBILITY: FAIL
NOTIFICATION INFRASTRUCTURE: PARTIAL
CREATOR ISOLATION: FAIL
FAN ISOLATION: FAIL
PRIVACY: PARTIAL
FAILURE OBSERVABILITY: PARTIAL
RECONNECT SAFETY: PARTIAL
PERFORMANCE: PARTIAL

P0: 4
P1: 10
P2: 8
P3: 4

PRODUCTION CHANGES: NONE
CANARY CHANGES: NONE
NEW LLM CALLS: NONE
NEW WORKERS: NONE
NEW QUEUES: NONE
NEW MIGRATIONS: NONE
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

NEXT ACTION:
Implement Stage B C1-C10 additive, minimal, best-effort via existing event_bus/PubSub/WebSocket + polling fallback: add optional creator_id to publish_event and scope ws_manager/event_subscriber (P0 isolation), emit additive commerce events (offer_created, sale_recorded, sale_attributed, purchase_confirmed, aftercare) with generation_id/creator_id after successful INSERT/UPDATE RETURNING, derive execution stages from existing generation_id + messages + generation_telemetry (no new state table), make live-fans query creator-scoped (add messages.creator_id column if needed) with bounded 15m window and hot via temperature, dedup notifications via event_id + generation_id/offer_id/external_transaction_id, keep isPollingActive guard and dedup 200, never expose buyer email/secrets, keep DropFans as sole financial authority, no sale-lost until deterministic terminal state exists.

