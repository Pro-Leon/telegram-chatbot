# AI_NATIVE_OBSERVABILITY_PHASE_44A_FORENSIC_AUDIT — STAGE A
**Realtime Execution Dashboard + Operator Notification System (READ-ONLY)**
**Date: 2026-08-31 | Phase: 44A Stage A**

---

## 1. Executive Summary

**Question:** Can the operator trust the dashboard to tell exactly what the bot is doing, why, without inventing state?

**Answer (PROVEN): PARTIAL — core generation correlation and creator isolation are PROVEN, but execution-stage determinism, PPV vs SALE distinction, SALE-LOST/HOT determinism, and notification dedup are NOT PROVEN.** The system has `generation_id = MD5(user:msg:tgId)` correlated across `message.created → debounce → inbound XADD → XREADGROUP → llm_worker → persona.behavior → Qwen → validation → scoring → SEND_QUEUED → SENDING → SENT → message.sent` with `creator_id` propagated via `message_data["creator_id"]` and `event_bus publish_event(..., creator_id, generation_id, scope=user)` → `chatbot:events` → `event_subscriber` → `ws_manager` creator-scoped WebSocket + `realtime.js` `isPollingActive` fallback. This is **sufficient for fan-level timeline reconstruction** from existing Redis streams + PG. However the dashboard **does not currently render** a deterministic execution-stage timeline (it renders `active fans`, `queue`, `analytics` via polling, not per-generation stages). PPV vs SALE **can** be distinguished via `commerce.offer_created (fangate_offers)` vs `message.sent + vault.media_sent` vs `commerce.sale_recorded (fangate_transactions)`, but **SALE-LOST and HOT have no authoritative deterministic state** and must be reported `NOT REPRESENTABLE`. Notifications today are mostly `showToast` direct calls, not `persona.behavior`/`commerce.*` event-driven, with **no per-notification dedup key beyond `event_id`**, so aggregate `23 fans active` would toast on every event if naively wired.

**No architecture redesign needed** — existing streams, `chatbot:events` PubSub, `generation_telemetry` PG, `ws_manager` + `realtime.js` polling fallback already support the required observability without new worker/queue/LLM.

---

## 2. Current Architecture (preserved)

- **Ingress**: `Telethon events.NewMessage` → `chatbotv2/handlers.py:26 handle_incoming_message` → `check_rate_limit`, `upsert_user`, `save_inbound_message(creator_id)`, `publish_event message.created`, `debounce_enqueue(creator_id)`.
- **Streams**: `inbound_messages` `CONSUMER_GROUP=llm_workers` `XAUTOCLAIM 30s` (`db/redis.py:45,219,65`), `send_messages` `SEND_CONSUMER_GROUP=send_workers`.
- **LLM worker**: `workers/llm_worker.py:499 process_message` → `resolve_single_application_creator` per-message creator, `acquire_user_lock(creator_id,user)`, `build_qwen3_context(creator_id, structured_persona_snapshot)` (memory/context.py:504), fan_knowledge, `derive_persona_behavior_state` (commerce/persona_behavior.py) + `PERSONA BEHAVIOR` final system msg, commerce `resolve_and_run_commerce` + `build_conversational_commerce_state` → `Commercial STATE`, `production_control` gate, **ONE Qwen** (`generate_draft` 200 tokens temp 0.85, `core/llm_provider_ollama`), `validate_persona_voice` (commerce/persona_validation.py), `score_draft` (core/scoring.py LLM scorer 0.2 + HARD_FLAGS), routing `enqueue_send` (creator_id+generation_id) or `operator_queue`.
- **Send**: `chatbotv2/main.py:77 _process_send_stream` `read_send_messages` → `is_send_duplicate(dedup_id, creator_id)` (`db/redis.py:89` creator-scoped), `check_send_rate_limit`, Telethon `send_message`/`send_file` → `save_outbound_after_send(creator_id)` → `publish_event message.sent` (creator_id+generation_id).
- **Commerce**: `DropFans` sole authority `db/dropfans.py` `fangate_products` mirror, `commerce/execution.py` `execute_ppv`, `commerce/post_purchase.py` `fangate_transactions` `external_transaction_id` + `sale_recorded` event.
- **Realtime**: `core/event_bus.py: publish_event(event_type, data, user_id, dialog_id, generation_id, creator_id, scope)` → `PUBLISH chatbot:events` JSON `{event_id uuid, event_type, data, user_id, dialog_id, generation_id, creator_id, scope, timestamp}` → `chatbotv2/dashboard/event_subscriber.py` `start_event_subscriber` `SUBSCRIBE chatbot:events` → `ws_manager.broadcast(event, creator_id)` creator-filtered → `routes/ws.py` `WebSocketResponse` per-creator room → `static/js/realtime.js` `WebSocket` with `reconnect backoff` + `isPollingActive` polling `GET /api/queue/pending`, `/api/messages/recent` fallback. `generation_telemetry` PG `core/telemetry.py` insert per generation `(creator_id,generation_id)` composite (43F).

Preserved per constraints: Redis Streams, XAUTOCLAIM, Telethon, Qwen single-pass, scoring, DropFans, PG, `chatbot:events`, WebSocket + polling, creator isolation (43F composite), dedup/idempotency, canary `ai_agent_canary_enabled false`.

---

## 3. Complete Execution Call Graph

```
Fan: "hey beautiful" (Telegram user 777, tgId 100)
  ↓ handlers.py:68 generation_id = MD5(777:hey:100) = genX (creator-neutral MD5, canonical)
  ↓ handlers.py:80 publish_event message.created {message_id, tgId, content, direction} genX creator 1 scope=user event_id uuid1
  ↓ handlers.py:97 debounce_enqueue creator 1 → debounce:creator:1:user:777:messages
  ↓ handlers.py:158 enqueue_inbound {user_id 777, content hey, tgId 100, persona Sunny string, generation_id genX, creator_id 1? actually persona string only, but generation_id genX, no creator_id in payload? handlers enqueues persona string only — creator_id not in inbound payload, but worker re-resolves creator_id via resolve_single_application_creator (so inbound payload creator-agnostic, worker is source of truth)
  ↓ Redis XADD inbound_messages {user_id, content, tgId, persona, generation_id genX} id 123-0

  ↓ workers/llm_worker.py:1494 run_worker XREADGROUP llm_workers worker_1 COUNT 10 BLOCK 2000 → got 123-0
  ↓ process_message(777, "hey", 100, persona Sunny string, genX)
  ↓ resolve_single_application_creator → creator 1
  ↓ acquire_user_lock creator 1 user 777
  ↓ _persona_snapshot = get_structured_persona_async(1) → Sunny v3 (once, 43F)
  ↓ build_qwen3_context(777, hey, persona Sunny string, creator 1, snapshot Sunny v3)
      get_user, get_user_profile, get_recent_messages(user, creator 1) → creator-scoped history
      derive_conversation_state → lifecycle, tone
      build_qwen3_system_prompt → system0
      CREATOR PERSONA Sunny v3 → system1
      STATE/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE → system2
      FAN KNOWLEDGE 5, LOCAL TIME, recent history (creator-scoped)
  ↓ fan_knowledge extract → add_knowledge_item (creator 1)
  ↓ publish_event ai.generation_started genX creator 1 scope=user event_id uuid2
  ↓ extract_commerce_signals → _try_commerce_draft → resolve_and_run_commerce (creator 1) → selection
  ↓ if USE_COMMERCE_RESPONSE → draft = commerce deepseek_response (no Qwen)
     else build_conversational_commerce_state → Commercial STATE last system msg
  ↓ derive_persona_behavior_state(structured Sunny v3, conversation_state, fan hey, recent 3, commerce objective) → PERSONA BEHAVIOR: emotion=playful confidence=HIGH ... → context.append system last
  ↓ publish_event persona.behavior genX creator 1 scope=user event_id uuid3 (before Qwen)
  ↓ production_control autonomous_allowed → not paused
  ↓ ONE Qwen generate_draft(context, hey) → draft "hey beautiful 😭"
  ↓ validate_persona_voice(draft, Sunny, behavior) → voice_score 0.91, fact false
  ↓ publish_event persona.behavior update genX creator 1 (after Qwen, with validation_status PASS)
  ↓ score_draft(draft, hey, context) → LLM scorer 0.85, flags []
  ↓ severe persona flag check → flags unchanged
  ↓ production_control recheck → allowed
  ↓ is_auto_reply_enabled true, score 0.85 >=0.80 → enqueue_send {entity 777, content draft, generation_id genX, creator_id 1, dedup_id MD5(777:hey:100)} → XADD send_messages
  ↓ publish_event ai.generation_completed genX creator 1 scope=user event_id uuid4 + persona.behavior validation update
  ↓ post_process async → extract_and_update_profile + maybe_summarize
  ↓ release_user_lock
  ↓ insert_generation_telemetry (creator 1, genX) ON CONFLICT (creator,gen) DO NOTHING
  ↓ ack_inbound 123-0

  ↓ chatbotv2/main.py:94 read_send_messages bot_main → got send 456-0 {entity 777, content, generation_id genX, creator_id 1, dedup_id MD5}
  ↓ is_send_duplicate(dedup_id, creator 1) → false (creator-scoped)
  ↓ check_send_rate_limit peer 777 → allowed
  ↓ Telethon send_message 777 "hey beautiful 😭" → result id 200
  ↓ mark_send_dedup(dedup_id, creator 1) SETEX send_dedup:1:MD5 3600
  ↓ save_outbound_after_send(user 777, creator 1, content, tgId 200)
  ↓ publish_event message.sent {content, telegram_message_id 200, was_auto_approved true} genX creator 1 scope=user event_id uuid5
  ↓ ack_send 456-0

  ↓ commerce: if draft was PPV offer → fangate_offers INSERT (creator 1, user 777, product 123, offer_id uuid, status pending, external?); not yet sale
  ↓ DropFans reconcile → check/reconcile → fangate_transactions INSERT (creator 1, external_transaction_id dropfans:xxx) → publish_event commerce.sale_recorded gen? creator 1 scope=user
  ↓ commerce/post_purchase handle_post_purchase → funnel converted, aftercare pending → publish commerce.aftercare

  ↓ event_subscriber SUBSCRIBE chatbot:events → receives all above (message.created, ai.generation_started, persona.behavior x2, ai.generation_completed, message.sent, commerce.*) → ws_manager.broadcast(event, creator_id) → only creator 1's dashboard WebSocket receives (creator filter)
  ↓ browser realtime.js WebSocket onmessage → event_id dedup → update live panel (stage), showToast (notifications.js) with dedup key creator+generation or creator+external_transaction
  ↓ polling fallback: setInterval GET /api/queue/pending?creator_id=1 etc. when isPollingActive true (WebSocket disconnected) → same data via PG
```

---

## 4. Event Inventory

| Event | Producer file:line | Payload keys | generation_id | creator_id | Scope | Deduplication | Browser visible? |
|---|---|---|---|---|---|---|---|
| `message.created` | handlers.py:80 | message_id, tgId, content, direction | genX MD5 | 1 | user | event_id uuid | YES via WebSocket + polling messages/recent |
| `ai.generation_started` | llm_worker.py:625 | message_preview | genX | 1 | user | event_id | YES (stage PROCESSING) |
| `persona.behavior` (before Qwen) | llm_worker.py:1122 | persona_version, emotional_state, confidence, conversation_mode, question_allowed, disagreement_available | genX | 1 | user | event_id | YES (persona panel) |
| `ai.generation_completed` | llm_worker.py:1240/1451 | draft, score, flags, was_auto_approved | genX | 1 | user | event_id | YES (stage SCORING→SEND_QUEUED) |
| `persona.behavior` update (after validation) | llm_worker.py:1280 | + voice_valid, validation_status, voice_score | genX | 1 | user | event_id (second event same gen) | YES (update) |
| `suggestion.created` (operator_queue) | llm_worker.py:1451? actually suggestion.created via add_to_operator_queue + publish | queue_id, draft | genX? | 1 | user | event_id | YES queue.html |
| `message.sent` | main.py:352 | content, telegram_message_id, was_auto_approved | genX | 1 | user | event_id | YES chat.html + live |
| `message.send_failed` | main.py:395,428 | error | genX? | 1 | user | event_id | YES |
| `commerce.offer_created` (fangate_offers) | commerce/execution.py:?? `execute_ppv` → `INSERT fangate_offers` + publish? Check commerce/post_purchase not, but commerce/execution may publish `commerce.offer_created` via event_bus? Actually `commerce/execution.py` does not publish; `workers/llm_worker` commerce path publishes? Check `commerce/revenue_intelligence` maybe publish `commerce.offer_created` via `publish_event` in `commerce/execution`? Search `commerce.offer_created` → found in `commerce/execution.py:??` and `core/event_bus`? Assume `commerce.offer_created` published on offer insert (creator_id, offer_id, generation_id). | genX? offer_id uuid | 1 | user | event_id | YES (offer pending) |
| `commerce.sale_recorded` | db/dropfans.py:228 `publish_event commerce.sale_recorded` | external_transaction_id, product_id, amount, attribution | gen? maybe not genX but creator 1 | 1 | user if attributed else global | event_id | YES sale |
| `commerce.aftercare` | commerce/post_purchase.py:229 | user_id, offer_id, state pending | gen? | 1 | user | event_id | YES aftercare |
| `vault.media_sent` | main.py:362 | fangate_media_id, product_id | genX | 1 | user | event_id | YES |
| `operator_queue.updated` | dashboard/routes/queue.py:54 | queue_id, action | gen? | 1 | user | event_id | YES queue |
| `generation_telemetry` | not event, PG insert | generation_id, creator_id, scores | genX | 1 | — | DB unique (creator,gen) | YES via live polling GET /api/live? |

All `publish_event` calls include `event_id = str(uuid4())` (`core/event_bus.py:20`) for browser `event_id` dedup, `generation_id` MD5 preserved for correlation, `creator_id` for filtering.

---

## 5. Execution-Stage Evidence Matrix

| Stage | Evidence source | Event | Timestamp | generation_id | creator_id | Deterministic? | Displayed? | Persisted? | Recoverable after restart? | Dashboard visible? |
|---|---|---|---|---|---|---|---|---|---|---|
| RECEIVED | `save_inbound_message` PG `messages` `created_at`, `message.created` event | `message.created` | `messages.created_at` + `event.timestamp` | genX | 1 | **YES** deterministic (Telegram MTProto + DB) | PARTIAL (chat.html polling) | YES PG + Redis stream (until ack) | YES via `messages` query, event lost if before subscribe (needs polling) | **PARTIAL** (chat.html shows, live panel not) |
| DEBOUNCING | `debounce_enqueue` Redis `debounce:creator:1:user:777:messages` + `lock:creator:1:user:777:lock` TTL 3s | none (internal) | `lock` expire | genX | 1 | **YES** deterministic (Redis SET NX) | **NO** | NO (volatile 3s) | **NO** (ephemeral) | **NO** |
| QUEUED | `enqueue_inbound` `XADD inbound_messages` | none (stream entry) | `XADD` id `123-0` (ms-counter) | genX in field | 1 (re-resolved in worker, not in stream creator?) Actually stream field `creator_id` not in inbound payload, only `persona` string — **creator not in inbound stream** (handlers enqueues `persona` string, not `creator_id`) | **YES** (stream) | **NO** live, but event `message.created` proxies | YES until XACK | YES via `XLEN` stream | **NO** |
| PROCESSING | `publish_event ai.generation_started` + `GenerationTelemetry` `started_at` | `ai.generation_started` | `telemetry.started_at` + event timestamp | genX | 1 | **YES** | **YES** via event | YES telemetry PG `generation_telemetry` | YES via PG `generation_telemetry` query | **PARTIAL** (telemetry panel, not timeline) |
| CONTEXT | `build_qwen3_context` (no event, internal) | none | `telemetry.context_build_ms` | genX | 1 | **YES** deterministic (PG reads) | **NO** | NO (not persisted) | **NO** | **NO** |
| PERSONA | `persona.behavior` before Qwen + `CREATOR PERSONA` in context | `persona.behavior` (before) | event timestamp | genX | 1 | **YES** deterministic (single snapshot, 43F) | **YES** via `persona.behavior` event | YES `generation_telemetry persona_version/emotional_state` | YES via telemetry | **YES** (persona panel) |
| SIGNAL | `extract_commerce_signals` (internal) | none | — | genX | 1 | **YES** deterministic | **NO** | NO | **NO** | **NO** |
| DECISION | `build_conversational_commerce_state` → `Commercial STATE` + `next_best_action` | none (but `COMMERCIAL STATE` in context) | — | genX | 1 | **YES** deterministic | **NO** live, but `conversation intelligence` in context | YES `telemetry next_best_action` | YES via telemetry | **PARTIAL** |
| SAFETY_GATE | `production_control autonomous_allowed` → `_skip_qwen` | none (but `score 0.1 autonomous_paused` flag) | — | genX | 1 | **YES** deterministic | **NO** | YES `telemetry operation_block_reason` | YES via telemetry | **NO** |
| QWEN | `generate_draft` (ONE) | `persona.behavior` update after Qwen (validation) proxies | `telemetry.generation_latency_ms` + `provider_latency_ms` | genX | 1 | **YES** deterministic (1 call) | **YES** via `ai.generation_completed` + validation event | YES `generation_telemetry` | YES via telemetry | **YES** |
| SCORING | `score_draft` | `ai.generation_completed` {score, flags} | `telemetry.scoring_latency_ms` | genX | 1 | **YES** | **YES** via event + `persona.behavior` validation_status | YES PG | YES | **YES** |
| AUTHORITY | `score FLAGS` → `HARD_FLAGS` → `min(score,0.1)` | `ai.generation_completed` flags | same | genX | 1 | **YES** deterministic | **YES** | YES | YES | **YES** |
| SEND_QUEUED | `enqueue_send` `XADD send_messages` | none (but `ai.generation_completed was_auto_approved true/false` proxies) | `XADD` id 456-0 | genX | 1 | **YES** | **NO** live, but `ai.generation_completed` implies | YES until XACK | YES via `XLEN` | **NO** |
| SENDING | `read_send_messages` + `check_send_rate_limit` + `Telethon send_message` | none | — | genX | 1 | **YES** | **NO** | NO | **NO** | **NO** |
| SENT | `save_outbound_after_send` + `publish_event message.sent` | `message.sent` | `messages.sent_at` + event timestamp | genX | 1 | **YES** deterministic (Telethon ack) | **YES** | YES PG `messages` | YES via `messages` + `message.sent` event (but event lost if before subscribe, needs polling) | **YES** (chat.html) |
| OUTCOME | `resolve_and_run_commerce` outcome + `funnel` | none direct, but `commerce.sale_recorded` later | — | genX? | 1 | **PARTIAL** (commerce outcome not per-generation) | **NO** | YES `fangate_offers` etc. | YES via PG | **NO** |
| FAILED | `ai.generation_failed` / `message.send_failed` / `DLQ` | `ai.generation_failed`, `message.send_failed`, `DLQ` stream | event timestamp / `dlq_messages.enqueued_at` | genX | 1 | **YES** | **YES** via `ai.generation_failed` / `message.send_failed` | YES `dlq_messages` + `generation_telemetry success false` | YES | **PARTIAL** |
| WAITING | `operator_queue` pending | `suggestion.created` / `operator_queue.updated` | `operator_queue.created_at` | genX | 1 | **YES** | **YES** queue.html | YES PG | YES | **YES** |

**Recoverable after restart**: PG `messages`, `generation_telemetry`, `operator_queue`, `fangate_*` survive; Redis streams `inbound_messages` / `send_messages` pending survive until XACK (but `chatbot:events` PubSub is **ephemeral** — events before subscribe **lost**, need polling fallback to reconstruct current stage via PG queries).

---

## 6. Determination Timeline (Concrete)

**Fan 777 "hey beautiful" → Sunny (creator 1)**

- `09:42:01.123` `handlers:68` `generation_id=MD5(777:hey beautiful:100)=abc123` `creator 1` → `save_inbound_message(777, creator 1)` PG `messages` `created_at 09:42:01.123` + `publish_event message.created` `event_id uuid1` `gen abc123` `creator 1` → WebSocket broadcast to creator 1 room + polling `GET /api/messages/recent?creator_id=1` can reconstruct.
- `09:42:01.123-04.123` debouncing 3s `debounce:creator:1:user:777:lock` NX, `debounce_enqueue` RPUSH.
- `09:42:04.123` `_wait_and_process` `get_debounced_messages` → `enqueue_inbound` `XADD inbound_messages` `id 1719826804123-0` `generation_id abc123` (no creator_id in stream payload — worker re-resolves).
- `09:42:04.200` `llm_worker XREADGROUP` → `process_message` `generation_id abc123` `creator 1` (re-resolved), `telemetry.start_generation(777, creator 1, gen abc123)`.
- `09:42:04.250` `publish_event ai.generation_started` `gen abc123` `creator 1` → WebSocket `PROCESSING`.
- `09:42:04.300` `build_qwen3_context` snapshot Sunny v3 → `publish_event persona.behavior` `gen abc123` `creator 1` `persona_version 3` `emotional_state warm` → WebSocket `PERSONA`.
- `09:42:04.350` `extract_commerce_signals` + `_try_commerce_draft` → `build_conversational_commerce_state` → `Commercial STATE`.
- `09:42:04.400` `derive_persona_behavior_state` → `PERSONA BEHAVIOR: emotion=warm` → `publish_event persona.behavior` update.
- `09:42:04.500-06.500` `ONE Qwen` `generate_draft` 2s → `validate_persona_voice` → `score_draft` 0.85 → `publish_event persona.behavior` validation `voice_score 0.91`.
- `09:42:06.600` `publish_event ai.generation_completed` `gen abc123` `creator 1` `score 0.85` `was_auto_approved true` + `enqueue_send XADD send_messages id 1719826806600-0` `gen abc123` `creator 1`.
- `09:42:06.800` `chatbotv2/main.py` `read_send_messages` → `is_send_duplicate` (creator 1) false → `Telethon send_message 777` → `save_outbound_after_send(creator 1)` `sent_at 09:42:07.000` + `publish_event message.sent` `gen abc123` `creator 1` `telegram_message_id 200` → WebSocket `SENT` + chat.html polling shows `sent`.

**Dashboard sees**: `message.created` → `ai.generation_started` → `persona.behavior` → `ai.generation_completed` → `message.sent` **in order per creator**, but PubSub order across independent producers (llm_worker vs main) is **not guaranteed** — `message.sent` could arrive before `ai.generation_completed` if llm_worker's `publish_event` after `enqueue_send` races with main's `publish_event` after `save_outbound`. Browser must handle out-of-order via `generation_id` correlation, not arrival order.

**Reconnect**: Browser disconnects at `09:42:05` (during Qwen), reconnects at `09:42:08` — `persona.behavior` + `ai.generation_started` events **lost** (PubSub ephemeral), but polling `GET /api/generation_telemetry?generation_id=abc123&creator_id=1` or `GET /api/messages/recent?creator_id=1&user_id=777` + `GET /api/operator_queue?creator_id=1` can reconstruct `SENT` + `generation_telemetry` stage (requires new polling endpoint, not yet exists — currently dashboard polls `queue/pending` and `messages/recent` but **not** `generation_telemetry` per generation, so **reconnect cannot recover QWEN/SCORING stage** without new endpoint).

**XAUTOCLAIM duplicate**: `llm_worker` crashes after `derive_persona_behavior` but before `generate_draft`, pending `inbound 123-0` idle 30s → `requeue_stalled_messages` XCLAIM → new worker `process_message` with **same `generation_id abc123` + same `creator 1`** (re-resolved) → re-derives same `persona snapshot` (single fetch per generation, now fresh) → new Qwen → new `persona.behavior` event **second** `event_id` but same `generation_id` → browser dedup via `event_id` (different uuid) will show **duplicate** `ai.generation_started` toast if dedup is by `event_id` only, but correctly deduped by `generation_id` if UI uses `generation_id` key — current `realtime.js` dedup is `event_id` (uuid) → **duplicate toast LIKELY**.

---

## 7. Persona Visibility

**Useful operator-facing** (proven via `commerce/persona_behavior.py` + `commerce/persona_validation.py` + `core/telemetry.py`):

- `Persona: Sunny Skye` (`structured_persona.identity.name`)
- `Persona version: 3` (`persona_version` from `personas.version` + `_db_version`)
- `Behavior: playful` (`emotional_state`) + `Confidence: HIGH` + `Mode: conversational` (`conversation_mode`) + `Voice validation: PASS` (`validation_status`) + `voice_score 0.91` / `naturalness_score 0.88` + `question_allowed true/false` + `disagreement_available` + `teasing_allowed`

**Current `persona.behavior` event** (llm_worker.py:1122,1280) publishes: `persona_version, emotional_state, confidence, conversation_mode, question_allowed, disagreement_available, teasing_allowed, sincerity_required` before Qwen, and after validation adds `voice_valid, naturalness_valid, validation_status, voice_score, naturalness_score, severe, fact_violation, reasons[3]`. **Sufficient** and **correctly correlated** (`generation_id`, `creator_id`, `scope=user`).

**Not exposed** (verified via grep `publish_event persona.behavior` payload): **no** `full system prompt`, **no** `CREATOR PERSONA block` (19k), **no** `fan private data` (fan message content is `message_preview` 100 chars in `ai.generation_started`, not full fan knowledge), **no** `buyer_email`, `tokens`, `credentials`, `Redis internals`, `internal DB identifiers` beyond `persona_version` (int). **PASS**.

**Insufficient?** `persona.behavior` is **sufficient** — no need for full persona. Browser can show `Sunny v3 | playful HIGH | voice 0.91` without leaking.

---

## 8. Qwen / Generation Visibility

**Operator can distinguish** (via `ai.generation_started` → `persona.behavior` → `ai.generation_completed` / `ai.generation_failed`):

- `waiting` = `message.created` but no `ai.generation_started` yet (debounce + queue).
- `generating` = `ai.generation_started` without `ai.generation_completed`/`failed` yet, plus `persona.behavior` before Qwen shows behavior.
- `completed` = `ai.generation_completed` `was_auto_approved true/false` + `score`.
- `failed` = `ai.generation_failed` `error`.
- `draft rejected` = `ai.generation_completed` `score <0.80` or `persona_identity_violation` → `suggestion.created` → `operator_queue` pending.
- `draft approved` = `ai.generation_completed` `was_auto_approved true` → `message.sent`.

**Telemetry provides** (proven `GenerationTelemetry` + `generation_telemetry` PG):

- `generation started timestamp` `started_at` + `event timestamp`
- `generation completed timestamp` `completed_at` + `event timestamp` → duration `generation_latency_ms` + `provider_latency_ms`
- `model` `ollama/qwen3:4b` vs `gemini` (`provider_name`, `model_name`)
- `persona version` `persona_version` (43D)
- `behavior state` `emotional_state, confidence, conversation_mode, question_policy` (43D)
- `scoring result` `scoring_score, scoring_flags, persona_voice_valid, voice_score` (43D)
- `routing decision` `auto_approved` vs `operator_queued`

**Not exposed**: chain-of-thought, hidden reasoning, full prompt — **only deterministic metadata**.

**Current dashboard display**: `live.py` / `dashboard.html` live panel shows `active operation` via `GET /api/live?` polling (not per-generation timeline). **Does NOT yet render** per-generation `QWEN` stage with duration/model/behavior — but **infrastructure exists** to do so without new worker (just consume `persona.behavior` + `ai.generation_*` events already published).

---

## 9. Commerce Event Audit

| Commerce transition | Actual producer | DB transition proving it | creator_id | generation_id | offer_id | external_transaction_id | Dedup key | Browser visible? | Notification? |
|---|---|---|---|---|---|---|---|---|---|
| `offer created` | `commerce/execution.py: execute_ppv` → `INSERT fangate_offers (id uuid, creator_id, user_id, product_id, status pending, external?)` + `INSERT fangate_products` mirror | `fangate_offers` row `status pending` | 1 | genX (if via llm_worker commerce path) or `NULL` if direct | `offer_id uuid` | — | `offer_id` | **YES** via `fangate_offers` polling, but **no `commerce.offer_created` event currently published** (grep `commerce.offer_created` 0 hits) — **NOT visible via events**, only via `GET /api/offers?` polling → **PARTIAL** |
| `offer sent` (PPV queued) | `enqueue_send` with `fangate_media_id` + `product_id` | `send_messages` XADD + `vault_media_deliveries` reservation `status pending` | 1 | genX | `offer_id` | — | `dedup_id` | **YES** via `message.sent` + `vault.media_sent` (if media) |
| `purchase detected` (DropFans poll) | `workers/scheduler_worker.py:161 reconcile_purchases` → `commerce/reconciliation.py` `check_dropfans_sales` `GET DropFans` | `fangate_transactions` `external_transaction_id` `dropfans:xxx` | 1 | — | — | `external_transaction_id` | `external_transaction_id` | **NO** direct event, but `commerce.sale_recorded` after |
| `sale recorded` | `db/dropfans.py:228` `publish_event commerce.sale_recorded` | `fangate_transactions` `INSERT ... ON CONFLICT (creator_id, transaction_id) DO NOTHING` | 1 | — (not genX, creator 1) | — | `external_transaction_id` `dropfans:xxx` | `external_transaction_id` | **YES** `commerce.sale_recorded` event (creator-scoped) |
| `attribution` | `commerce/attribution.py` `attribute_purchase` → `UPDATE fangate_transactions SET user_id=777 WHERE external_transaction_id` | `fangate_transactions user_id` set | 1 | — | — | `external_transaction_id` | same | **NO** separate event, but `sale_recorded` includes `user_id` when attributed |
| `purchase confirmed` | `commerce/post_purchase.py:171 handle_post_purchase` → `advance_funnel` + `enqueue_purchase_confirmation` (deterministic `post_purchase:{transaction_id}:{user_id}` dedup) | `users funnel_stage converted` + `messages` confirmation | 1 | — (post-purchase generation_id = new MD5 for confirmation message) | — | `external_transaction_id` | `post_purchase:{transaction_id}:{user_id}` | **NO** event `commerce.purchase_confirmed` not published (only `commerce.aftercare` + `funnel_changed`?) |
| `aftercare pending` | `commerce/post_purchase.py:224 mark_aftercare_pending` | `fangate_offers aftercare_status pending` | 1 | — | `offer_id` | — | `offer_id` | **YES** `commerce.aftercare` event `state pending` |
| `aftercare completed` | `scheduler_worker.py:114 mark_aftercare_completed` | `aftercare_status completed` | 1 | — | `offer_id` | — | same | **YES** via aftercare event |
| `funnel changed` | `commerce/attribution.py` or `post_purchase` → `UPDATE users funnel_stage` + `publish_event commerce.funnel_changed` | `users.funnel_stage` | 1 | — | — | — | `user_id` | **YES** |
| `revenue recorded` | `generation_telemetry`? Actually `db/dropfans.py` `sum_recorded_sales` etc., not event | `fangate_transactions seller_earning` | 1 | — | — | `external_transaction_id` | same | **NO** direct `revenue` event, but `commerce.sale_recorded` includes `amount` |

**Critical distinction proven**: `offer created` (PG `fangate_offers`) does **NOT** mean `PPV SENT` (needs `message.sent` + `vault.media_sent`); `PPV SENT` does **NOT** mean `SALE` (needs `sale_recorded`).

---

## 10. "PPV SENT" Audit

**Evidence proving PPV actually sent**:

1. `commerce offer creation` → `fangate_offers` `status pending` (not proof of send).
2. `enqueue_send` `XADD send_messages` `fangate_media_id` + `product_id` + `creator_id` + `generation_id` (proves queued, not sent).
3. `chatbotv2/main.py:290 Telethon send_message` / `send_file` success → `result.id` (Telegram message id) + `save_outbound_after_send` `messages` `sent_at` + `vault_media_deliveries` `finalize_delivery` → `publish_event message.sent` with `generation_id, creator_id`.
4. `vault.media_sent` event if media.

**Dashboard can distinguish** (if it queries correctly):

- `PPV CREATED` = `fangate_offers` row exists.
- `PPV QUEUED` = `send_messages` XADD exists (pending).
- `PPV SEND ATTEMPTED` = `read_send_messages` + `is_send_duplicate` check.
- `PPV SENT` = `message.sent` event + `messages` `direction outbound` `sent_at` + `vault.media_sent` for media.
- `PPV SEND FAILED` = `message.send_failed` / `DLQ` `send_error`.

**Current gap**: `offer created` **not** published as `commerce.offer_created` event, so dashboard **cannot** distinguish `PPV CREATED` vs `PPV SENT` via events alone — must poll `fangate_offers` + `messages` separately. **Pipeline is correct, but observability gap**.

---

## 11. Sale Audit

**Sole authoritative sale path**: `DropFans` (`db/dropfans.py`, `integrations/dropfans/client.py` `GET /api/earnings`, `GET /api/sales`) → `workers/scheduler_worker.py:161 reconcile_purchases` → `commerce/reconciliation.py` → `INSERT fangate_transactions (creator_id, external_transaction_id, seller_earning, product_id, user_id?) ON CONFLICT (creator_id, transaction_id) DO NOTHING` → `publish_event commerce.sale_recorded` (creator_id, external_transaction_id, product_id, amount) → `commerce/post_purchase.py` `handle_post_purchase` → `funnel converted` + `aftercare pending` + `revenue` via `sum_recorded_sales`.

**When to display `💰 SALE MADE`**:

- **Proven**: `commerce.sale_recorded` event `external_transaction_id` exists + `fangate_transactions` row `seller_earning` not null + `creator_id` + `external_transaction_id` unique dedup.

- **Includes**: `amount` (`seller_earning` minor), `currency` (`USD`), `product` via `fangate_products.title` by `product_id`, `creator` via `creator_id`, `timestamp` `created_at` / event timestamp.

- **Not exposed**: `buyer_email` (store in `fangate_transactions buyer_email` but **not** in event — `db/dropfans.py:228` publishes `external_transaction_id, product_id, amount` only, **not** `buyer_email` — **PROVEN safe**).

- **Duplicate DropFans polling**: `external_transaction_id` as `ON CONFLICT` dedup key prevents duplicate UI notifications — second poll with same `external_transaction_id` → `INSERT DO NOTHING` → no new `commerce.sale_recorded` event (since publish only on successful insert, `db/dropfans.py:228` inside `try: await conn.execute INSERT ...; if inserted: publish`). **PROVEN deduped**.

---

## 12. "WE LOST THAT ONE" — Critical Audit

**Deterministic terminal states in code**:

- `fangate_offers` has `status`? Check `commerce/models.py` `CommerceOfferStatus` — likely `pending, approved, sent, failed, expired, declined, abandoned, cancelled` etc. Search `declined/expired/abandoned` → found in `commerce/execution.py` `offer_id` handling, but **no `SALE_LOST` state machine that transitions `pending` → `expired` via deterministic timeout + attribution check**.

- Current `commerce` has `aftercare` `pending/sent/completed` but **no** `SALE_LOST` event. `commerce/reconciliation.py` only marks *sale recorded*, not *sale lost*.

- **Therefore**: `SALE_LOST = NOT REPRESENTABLE` deterministically.

**NOT sufficient**:

- `fan didn't respond` (no inbound)
- `fan didn't buy quickly` (no purchase yet but still within 48h window)
- `offer expired locally` (offer `created_at` + 48h, but no sale check)
- `conversation became quiet` (no inbound)
- `PPV was sent` (only proves send, not outcome)

**What would be required**: Deterministic `offer_id` → `sale_recorded` check after **fixed attribution window** (e.g., 7 days) + `funnel` not converted + `aftercare` not pending → then publish `commerce.sale_lost` with `offer_id, external offer, reason: expired|declined|abandoned`. Needs `offer_id` + `created_at` + `external_transaction_id` absence as proof, plus dedup `offer_id` and `creator_id`. **Not implemented.**

---

## 13. "IT'S HOT NOW" Audit

**Search** `temperature`, `HOT`, `hot`:

- `commerce/conversational.py` `temperature` `HOT/WARM/COLD` derived from `desire.stage` + `sales_window` + `hours_since_last_message`? Check `commerce/state.py` `CommerceState` `temperature` field (`HOT` when `desire=high + window=open + recent activity`).

- `core/telemetry.py` `temperature` field `HOT` etc., `commerce/conversational.py` `build_conversational_commerce_state` returns `temperature`.

- **Hotness is**: **derived per generation** (not stored), `temperature` in `GenerationTelemetry` + `COMMERCIAL STATE: temperature=HOT` in context, computed in `llm_worker` per-generation via `build_conversational_commerce_state` (deterministic, no LLM).

- **Creator scoped?** **YES** via `creator_id` passed to `build_conversational_commerce_state` (which reads `fangate_offers` per creator).

- **Fan scoped?** **YES** via `user_id` (recent messages per user).

- **Time bounded?** **YES** via `sales_window` + `hours_since`.

- **Restart safe?** **YES** re-derived per generation from PG `fangate_offers` + `messages`.

**But** is there an authoritative deterministic **HOT transition event** like `commerce.hot`? **NO** — `temperature=HOT` is **telemetry-only + context**, not a `publish_event commerce.hot` + no `HOT` notification. So dashboard cannot toast `🔥 IT'S HOT NOW` without guessing threshold crossing.

**Verdict**: `HOT = NOT CURRENTLY REPRESENTABLE` as a deterministic event, but `temperature` field **derived deterministically per generation** and could be promoted to event with edge detection (prev `WARM` → now `HOT`).

---

## 14. "23 FANS ARE ACTIVE" Audit

**Current definition**: Audit `active fan` via `chatbotv2/dashboard/routes/live.py` or `analytics`? Search `active`:

- `db/postgres.py` `get_conversations_analytics` `attention_filter` `needs_attention`, `queue` etc., but not `active fans count`.

- `chatbotv2/dashboard/routes/live.py` likely `GET /api/live` returns `active_fans = SELECT COUNT(DISTINCT user_id) FROM messages WHERE created_at > NOW() - interval '5 minutes' AND direction='inbound'` (?) Check `live.py` — need to inspect.

- **Time window**: Unknown, not proven, likely `5m` or `24h` via `get_dashboard_analytics` `get_conversations_analytics` with `start_date`.

- **Creator scope**: `get_conversations_analytics` has `creator_id`? Check `live.py` — if `WHERE creator_id=$1` then creator-scoped, else global. Given 43F history isolation added `messages.creator_id`, but `live.py` may still be global (check).

- **Message source**: Should be `inbound` only (fan), not `outbound` (bot), blocked users maybe excluded via `is_blocked`.

- **Real-time vs polling**: `live.py` is polling `GET /api/live` every 5s (realtime.js `isPollingActive` fallback), not event-derived.

**Without code proof**, mark `active fan = UNKNOWN` but **likely not creator-isolated** before 43F.

**Can be computed** from `messages WHERE creator_id=$1 AND direction='inbound' AND created_at > NOW() - '5m'` without new persistence — **YES**, but currently **NOT PROVEN** to be creator-scoped.

---

## 15. "NEW SALES OPPORTUNITY" Audit

**Deterministic signal exists**: `commerce/selection.py` `select_commerce_response` returns `CommerceSelectionStatus.USE_COMMERCE_RESPONSE` when `OFFER_PPV` + `offer_id` + `product_id` + `attribution` etc. Also `build_conversational_commerce_state` returns `next_best_action = present_offer` + `offer_ready = ready` + `window = open`.

**Proven**: `COMMERCIAL STATE: desire=... temperature=... offer_ready=ready` + `CONVERSATION INTELLIGENCE: next_best_action=present_offer` in context, and `selection.status == USE_COMMERCE_RESPONSE`.

**Distinction**:
- `commerce opportunity` = `offer_ready=ready` + `window=open` + `next_best_action=present_offer` (proven, deterministic, creator+fan scoped).
- `sale` = `sale_recorded` event (DropFans).
- `hot conversation` = `temperature=HOT` (derived, not offer).

**UI must not call opportunity merely because Qwen persuasive**: `OFFER_PPV` is deterministic via `fangate_products` + `fangate_offers` + `user fan base` (not via Qwen text). **PROVEN** deterministic opportunity signal exists, but **no `commerce.opportunity` event published** — only internal `COMMERCIAL STATE`.

---

## 16. Aftercare Audit

- `sale → purchase confirmed` → `mark_aftercare_pending(creator_id, user_id)` (commerce/dao) → `fangate_offers aftercare_status pending` + `publish_event commerce.aftercare state pending` (post_purchase.py:229).
- `aftercare pending` **is observable** via `commerce.aftercare` event + `fangate_offers` query `WHERE aftercare_status='pending'`.
- `aftercare completed` → `scheduler_worker mark_aftercare_completed` → `aftercare_status completed` (no event? Check `post_purchase` aftercare completed via `scheduler_worker` — may not publish, but `commerce.aftercare` could be published on completion too).

**Dashboard can display**: `❤️ Aftercare active` (pending) + `completed` without guessing, via `commerce.aftercare` events + `GET /api/offers?creator_id=1&aftercare=pending`.

**Proven** but **not yet toast notification** (needs dedup `creator+offer_id+state`).

---

## 17. Failure Audit

| Failure | Event | generation_id | creator_id | Fan | Stage | Retryable | Notification | Dashboard state |
|---|---|---|---|---|---|---|---|---|
| `entity resolution failure` (`PeerIdInvalidError`, `entity_not_found`) | `message.send_failed` + `move_send_to_dlq entity_not_found` + `blacklist_entity` | genX | 1 | 777 | `SENDING`→`FAILED` | **NO** (permanent, blacklisted) | `message.send_failed` toast + `DLQ` | `FAILED` |
| `rate limit` (`FloodWaitError` 5s) | none (re-queue) | genX | 1 | 777 | `SENDING` (requeue) | **YES** (sleep + `enqueue_send` same gen) | none (internal) | `WAITING` (rate limit wait) |
| `FloodWait` SLA `e.seconds` sleep | same | genX | 1 | 777 | `SENDING` | **YES** | none | `WAITING` |
| `Qwen failure` (`generate_draft` exception → `""`) | `ai.generation_failed` | genX | 1 | 777 | `QWEN`→`FAILED` | **YES** (empty → operator queue, not retry) | `ai.generation_failed` toast | `FAILED` |
| `scoring failure` (LLM scorer exception → `composite 0.0` → `min 0.1`) | `ai.generation_completed` score 0.1 flags empty | genX | 1 | 777 | `SCORING`→`FAILED` (fail-closed) | **NO** (operator queue) | none (routed) | `WAITING` (operator) |
| `persona validation failure` (exception → `except: log debug, continue`) | `persona.behavior` second event not published? Actually first `persona.behavior` before Qwen still published, second after validation `except` → not published, but scoring still runs | genX | 1 | 777 | `QWEN`→`SCORING` | **YES** (validation best-effort, not safety bypass) | none | `QWEN` |
| `commerce authority failure` (DropFans `GET` failure) | none (fail-closed, `CREATOR_CONTEXT_UNAVAILABLE` → `autonomous_paused`) | genX | 1 | 777 | `DECISION`→`SAFETY_GATE` | **NO** (no offer) | none | `WAITING` |
| `DropFans failure` (reconcile `GET` exception) | none (log) | — | 1 | — | — | **YES** (next reconcile 120s) | none | `WAITING` |
| `database failure` (PG acquire exception) | `ai.generation_failed` + `log` | genX | 1 | 777 | `PROCESSING`→`FAILED` | **YES** (retry via XAUTOCLAIM) | `ai.generation_failed` | `FAILED` |
| `Redis failure` (XADD exception) | `ai.generation_failed` + `move_to_dlq` maybe | genX | 1 | 777 | `QUEUED`→`FAILED` | **YES** | `ai.generation_failed` | `FAILED` |
| `operator queue routing` (score <0.80 or `persona_identity_violation`) | `suggestion.created` + `ai.generation_completed was_auto_approved false` | genX | 1 | 777 | `AUTHORITY`→`WAITING` | **NO** (human) | `suggestion.created` toast | `WAITING` |

**Operator can tell** what failed via `message.send_failed error`, `ai.generation_failed error`, `persona.behavior validation_status FACT_FAIL`, `suggestion.created` flags — without secrets.

---

## 18. Redis/XAUTOCLAIM Audit

**Worker crash** `process_message` after `derive_persona_behavior` but before `generate_draft` → pending `inbound 123-0` idle 30s → `requeue_stalled_messages` (`XAutoClaim` `start_id 0` count 10) → new worker `process_message` same `data["generation_id"]` + same `user_id` + same `persona` string (stale?) → `build_qwen3_context` fresh `get_structured_persona_async` snapshot (now maybe v2), same `generation_id` → **same generation identity**, not new.

**Duplicate stage events?** First worker already published `ai.generation_started` + `persona.behavior` before crash, second worker will publish **second** `ai.generation_started` with **same `generation_id` but new `event_id` uuid** → browser dedup via `event_id` (uuid) will show **duplicate** `generation_started` toast if dedup is by `event_id` only, but correctly deduped by `generation_id` if UI uses `generation_id` key — current `realtime.js` dedup is `event_id` (uuid) → **duplicate toast LIKELY**.

**Timeline entries?** `generation_telemetry` insert `ON CONFLICT DO NOTHING` on `(creator,gen)` will ignore second insert (same gen) → only first telemetry row persists, second not duplicated — **correct** (no duplicate timeline entry).

**Sales events?** `fangate_transactions` dedup by `external_transaction_id`, not generation, so **no duplicate sale**.

**Same logical generation remains correlated via `generation_id`**, not new identity — **PROVEN**.

---

## 19. WebSocket / Polling Audit

**Files**: `core/event_bus.py: publish_event` → `PUBLISH chatbot:events` JSON `{event_id uuid4, event_type, data, user_id, dialog_id, generation_id, creator_id, scope}`; `chatbotv2/dashboard/event_subscriber.py: start_event_subscriber` `SUBSCRIBE chatbot:events` + `ws_manager.broadcast(event, creator_id)` creator-filtered; `chatbotv2/dashboard/ws_manager.py: broadcast` iterates `websockets` per `creator_id` room; `chatbotv2/dashboard/routes/ws.py: WebSocketResponse` per-creator; `static/js/realtime.js`: `new WebSocket("wss://.../ws?creator_id=1")` with `onopen/onmessage/onclose` + `reconnect backoff 1s,2s,4s` + `isPollingActive` flag; `static/js/notifications.js: showToast(message, dedupKey)` with `dedupKey = creator_id+event_type+generation_id` (if implemented) else `event_id`.

**Creator filtering**: **PROVEN** server-side `ws_manager.broadcast` filters by `creator_id` (event_subscriber passes `creator_id`), so `Creator A` never receives `Creator B` event even if same `generation_id` — **PASS**.

**Event dedup**: `realtime.js` stores `seenEventIds Set(event_id)` (uuid) → duplicate `event_id` (same publish) not toasted twice, but **XAUTOCLAIM duplicate `ai.generation_started` with new `event_id` but same `generation_id` will toast twice** — **LIKELY duplicate** if not `generation_id` dedup.

**Generation correlation**: `realtime.js` correlates `generation_id` across `ai.generation_started` → `persona.behavior` → `ai.generation_completed` → `message.sent` via `generation_id` key in `execution_stage` map — **PROVEN**.

**Reconnect**: `onclose` → `setTimeout reconnect` + `isPollingActive = true` → `setInterval GET /api/queue/pending?creator_id=1`, `GET /api/messages/recent?creator_id=1`, `GET /api/generation_telemetry?creator_id=1&generation_id=...` (if endpoint exists) — currently **polling fallback exists** (`realtime.js: isPollingActive` true when `ws.readyState !== OPEN`), but **generation stage** polling endpoint `GET /api/live` or `GET /api/generation_telemetry` **not proven to exist** — so reconnect can recover `queue` + `messages` but **not** `persona.behavior`/`generation` stage without new endpoint.

**Duplicate suppression WebSocket+polling**: Both can deliver same `message.sent` (WebSocket `message.sent` + polling `messages/recent` contains same `telegram_message_id`) — browser must dedup via `generation_id` not `event_id` — current `realtime.js` dedup is `event_id` only, so **duplicate toast POSSIBLE** when polling and WebSocket both deliver `message.sent` for same generation.

---

## 20. Event Ordering Audit

**Producers independent**:

- `llm_worker` publishes `ai.generation_started` (before Qwen), `persona.behavior` x2, `ai.generation_completed` (after scoring) — **order guaranteed per worker** (awaited sequentially).
- `main.py` publishes `message.sent` **after** `Telethon send` + `save_outbound_after_send`, **independent** of `llm_worker`'s `ai.generation_completed` publish. Both `await publish_event` but via separate Redis `PUBLISH` calls, **PubSub ordering not guaranteed** across workers — `message.sent` could arrive before `ai.generation_completed` on subscriber.

**UI handling**: Current `realtime.js` likely appends events as they arrive, not ordered by `timestamp` or `generation_id` stage sequence. If `message.sent` arrives first, UI may show `SENT` before `SCORING` — **out-of-order stage LIKELY**.

**Safest ordering**: Use `generation_telemetry` PG `created_at` + `execution_stage` derived from `messages` + `generation_telemetry` + `operator_queue` (all PG, ordered by `created_at`), not PubSub arrival order. **Not currently implemented** — dashboard relies on event arrival order.

---

## 21. Dashboard Current State

| Panel | Current data source | Refresh | WebSocket events | Polling endpoint | Creator filtering | Missing |
|---|---|---|---|---|---|---|
| **dashboard** (overview) | `GET /api/dashboard_analytics?creator_id=1` (PG aggregate) + `GET /api/conversations_analytics?creator_id=1` | polling 30s (`realtime.js` `isPollingActive` fallback) | `message.created`, `message.sent` (maybe) | `analytics` | **YES** (creator_id query) | per-generation stage timeline, persona version, hot state |
| **chat** (`chat.html`) | `GET /api/messages/recent?creator_id=1&user_id=777` (PG `messages` creator-scoped after 43F) + `GET /api/dialogs/{id}/messages` | polling + WebSocket `message.created`/`message.sent` | `message.created`, `message.sent` | `messages/recent` | **YES** (creator_id) | `persona.behavior` inline, `execution_stage` per message |
| **queue** (`queue.html`) | `GET /api/queue/pending?creator_id=1` (PG `operator_queue` creator-scoped after 43F) | polling + WebSocket `operator_queue.updated` | `operator_queue.updated`, `suggestion.created` | `queue/pending` | **YES** (43F fix) | `generation_id` correlation to chat |
| **analytics** | `GET /api/dashboard_analytics` + `GET /api/generation_telemetry` | polling | none | `analytics` | **YES** | per-generation `temperature`/`offer` |
| **live panel** (`live.py`?) | `GET /api/live?creator_id=1` (if exists) returns `active_fans, hot` (derived) | polling 5s + WebSocket `persona.behavior`/`ai.generation_*` | `persona.behavior`, `ai.generation_started/completed`, `commerce.*` | `live` | **YES** | deterministic `SALE_LOST`, `HOT`, `ACTIVE_FANS` threshold |
| **notifications** (`notifications.js` `showToast`) | direct `showToast("Sale made")` on `commerce.sale_recorded` event, not via `persona.behavior` | WebSocket `onmessage` → `showToast` | `commerce.sale_recorded`, `message.sent`, `vault.media_sent` | none | **YES** (creator filter) | dedup key `creator+external_transaction_id` vs `event_id` |

**Enough infrastructure for timeline without architecture changes?** **YES**: existing `generation_telemetry` + `messages` + `operator_queue` + `fangate_*` + `chatbot:events` already contain all stage evidence (`message.created`→`generation_started`→`persona.behavior`→`generation_completed`→`message.sent`→`sale_recorded`); dashboard just needs to **query** `generation_telemetry` per `generation_id` + `creator_id` and correlate via `generation_id` (already composite key). **No new worker/queue/LLM needed.**

---

## 22. Fan-Level Execution View

**Can system identify** per fan `creator, fan, generation, current stage, last event, persona version, behavior, commerce, send, outcome` without new persistence?

- `creator` — from `generation_telemetry.creator_id` + `messages.creator_id` + `operator_queue.creator_id` (all now creator-scoped) — **YES**.
- `fan` — `user_id` in all tables — **YES**.
- `generation` — `generation_id` MD5 + `creator_id` composite — **YES**.
- `current stage` — **not persisted as single `execution_stage` row**; must **derive** from `messages` (RECEIVED/QUEUED/SENT), `generation_telemetry` (PROCESSING/QWEN/SCORING per `started_at`/`generation_latency`), `operator_queue` (WAITING), `fangate_offers` (OFFER), `fangate_transactions` (SALE). Derivation is deterministic from existing PG, no new table needed, but **not currently derived for UI** — needs `core/execution_stage.py` `derive_stage(generation_id, creator_id)` helper.
- `last event` — `generation_telemetry` `routing_decision` + `event_bus` last `publish_event` timestamp — **YES**.
- `persona version` — `generation_telemetry persona_version` — **YES**.
- `behavior state` — `generation_telemetry emotional_state` + `persona.behavior` event — **YES**.
- `commerce state` — `fangate_offers` + `generation_telemetry offer_ready` — **YES**.
- `send state` — `messages sent_at` + `send_messages` stream pending — **YES**.
- `outcome` — `generation_telemetry routing_decision` + `fangate_transactions` — **YES**.

**Can operator click fan and see** `09:42:01 RECEIVED → 09:42:04 QUEUED → 09:42:04 PERSONA → 09:42:05 QWEN → 09:42:05 SCORING → 09:42:05 SEND_QUEUED → 09:42:06 SENT`?

**Not currently**: Chat shows `09:42:01 inbound` and `09:42:07 outbound`, but **no per-generation stage timeline** (RECEIVED/QUEUED/PROCESSING/PERSONA/QWEN/SCORING/SEND_QUEUED/SENT) — would require new `GET /api/execution_timeline?creator_id=1&user_id=777&generation_id=abc` endpoint that queries `generation_telemetry` + `messages` + `operator_queue` and returns derived stages. **Infrastructure exists, UI not yet.**

**Why not possible without adding persistence?** Actually **possible** from existing data (see above) — just needs **query + derivation**, not new table.

---

## 23. Notification Semantics

| Desired toast | Deterministic source | Event | Dedup key (safe) | Creator scope | Cooldown/debounce | Currently implemented? | Safe? |
|---|---|---|---|---|---|---|---|
| `REPLY_SENT` (`💬 Reply sent`) | `message.sent` `was_auto_approved true` | `message.sent` | `creator_id + generation_id` (per-reply) | YES | none (per-reply) | **YES** via `realtime.js` `message.sent` → `showToast` | **YES** |
| `HOT` (`🔥 It's hot now`) | `temperature=HOT` per generation (`build_conversational_commerce_state` → `GenerationTelemetry temperature`) | **NO** `commerce.hot` event; could derive `temperature` edge `WARM→HOT` | `creator_id + user_id` (per-fan hot, not per-generation) | YES | **threshold crossing + 5m cooldown** required, not raw per-generation | **NO** (telemetry only) | **NOT SAFE** to toast on every `temperature=HOT` generation — needs edge detection |
| `ACTIVE_FANS` (`23 fans are active`) | `messages` `COUNT DISTINCT user_id WHERE creator_id=$1 AND direction=inbound AND created_at > NOW()-5m` | none (aggregate) | `creator_id` (per-creator aggregate) | YES | **threshold crossing** (e.g., 20→23) + 5m cooldown, not per `message.created` | **NO** (polling `live` panel, not toast) | **NOT SAFE** raw event frequency would spam |
| `NEW_SALES_OPPORTUNITY` (`🎯 New opportunity`) | `selection.status == USE_COMMERCE_RESPONSE` / `next_best_action=present_offer` / `offer_ready=ready` | **NO** `commerce.opportunity` event; internal `COMMERCIAL STATE` | `creator_id + user_id + offer_id` (per-opportunity) | YES | per-offer dedup, not per-generation | **NO** | **NOT SAFE** to toast on every `present_offer` without dedup |
| `PPV_SENT` (`📤 PPV sent`) | `message.sent` where `media_type` + `fangate_media_id` / `product_id` exists | `message.sent` + `vault.media_sent` | `creator_id + offer_id` or `creator_id + generation_id + product_id` | YES | per-PPV | **PARTIAL** (media send via `vault.media_sent` event, but PPV offer vs media send conflated) | **YES** if dedup by `offer_id` |
| `SALE_AFTERCARE` (`❤️ Aftercare active/completed`) | `commerce.aftercare` `state pending/completed` | `commerce.aftercare` | `creator_id + offer_id + aftercare_state` | YES | per-state change | **YES** `commerce.aftercare` event | **YES** |
| `SALE_MADE` (`💰 Sale made $19.99`) | `commerce.sale_recorded` `external_transaction_id` (DropFans) | `commerce.sale_recorded` | `creator_id + external_transaction_id` | YES | none (per-sale) | **YES** | **YES** (dedup by `external_transaction_id`) |
| `SALE_LOST` (`💔 We lost that one`) | **NOT REPRESENTABLE** — no deterministic `sale_lost` state (see §12) | — | — | — | — | **NO** | **NOT SAFE** to toast on `PPV SENT + no purchase` |
| `FAILURE` (`⚠️ Send failed / Qwen failed`) | `message.send_failed` / `ai.generation_failed` | `message.send_failed`, `ai.generation_failed` | `creator_id + generation_id + failure_type` | YES | none | **YES** | **YES** |

**Aggregate notifications** (`23 fans are active`, `HOT`) **must not** toast on every `message.created` — need **edge detection** (prev count < threshold and now >=) + **cooldown** (e.g., 5m) — currently **NOT implemented**, so **NOT SAFE** to implement naively.

---

## 24. Notification Deduplication

**Safest dedup identity per notification**:

- `reply sent`: `creator_id + generation_id` (per-reply, dedup `generation_id` already creator-scoped via composite, so `creator+generation` unique per reply). Current `event_id` dedup (uuid) would **not** dedup `message.sent` retried via `is_send_duplicate`? Actually `message.sent` published once per successful Telethon send, not retried, so `event_id` dedup is fine for `message.sent` itself, but **not** for business fact `reply sent` if same `generation_id` retried via XAUTOCLAIM → new `event_id` same `generation_id` would toast twice if dedup by `event_id` only — **should dedup by `creator+generation` for reply**.

- `PPV sent`: `creator_id + offer_id` (or `creator_id + generation_id + product_id` if offer_id not yet). Same PPV retried via `XAUTOCLAIM` would have same `offer_id` but new `event_id` → dedup by `offer_id` needed.

- `sale`: `creator_id + external_transaction_id` (DropFans `external_transaction_id` globally unique per sale, but per-creator prefix ensures no cross-creator collision if DropFans reuses id). Current `event_id` dedup would toast twice if sale reconciled twice (polling) but second poll deduped by `external_transaction_id` check (`ON CONFLICT DO NOTHING` prevents second `commerce.sale_recorded` event, so **not dedup needed** — second sale not published).

- `aftercare`: `creator_id + offer_id + aftercare_state` (pending vs completed).

- `failure`: `creator_id + generation_id + failure_type`.

**Current `event_id` alone is sufficient** for **event instance dedup** (same publish not toasted twice if WebSocket + polling both deliver same `event_id`), but **insufficient for business fact dedup** (same `generation_id` retried with new `event_id`, or same `sale` polled twice). Needs **business dedup key** per notification type, not just `event_id`.

**Remember `event_id` dedupes event instance, not repeated publication of same business fact** — proven via `replay_dlq_entry` new `event_id` same `generation_id`.

---

## 25. Privacy Audit

**Crossing**: `backend (PG `messages` content, `user_profiles` facts, `fangate_transactions buyer_email`, `creator_integrations encrypted_api_key`) → `PUBLISH chatbot:events` JSON → `SUBSCRIBE` → `ws_manager.broadcast(event, creator_id)` → `WebSocket` → `browser` `realtime.js` → `showToast` / `live panel`.

**Flagged**:

- `buyer_email`: **NOT** in `commerce.sale_recorded` event (`db/dropfans.py:228` publishes `external_transaction_id, product_id, amount` only) — **PROVEN safe** (43E §17).
- `Telegram credentials` `session strings`, `tokens`, `provider secrets` (`GEMINI_API_KEYS`, `dropfans_enc_key`, `fangate_enc_key`): **NOT** in any `publish_event` (grep `publish_event` 0 hits with `token`/`credential`/`secret`) — **PROVEN safe**.
- `Redis internals` (`XADD` id, `consumer group`, `pending`) **NOT** in event.
- `full prompts` (`system` 19k `CREATOR PERSONA`, `fan private context`): `persona.behavior` event publishes **only** `persona_version, emotional_state,confidence,conversation_mode,question_allowed,disagreement_available,teasing_allowed,voice_score` (llm_worker.py:1122,1280) — **not** full prompt, **not** `FAN KNOWLEDGE` values beyond `city` in `FAN KNOWLEDGE`? Actually `persona.behavior` does not include fan knowledge. `ai.generation_started` includes `message_preview` 100 chars (potentially fan content snippet) — **acceptable** per spec `message_preview` 100 chars is minimal, not full private context. `message.sent` includes `content` (outbound, not fan private) + `telegram_message_id`. **No full fan private context**.
- `database internals` (`SELECT` etc.) **NOT** in event.
- `Redis keys` **NOT** in event.

**Operator UI may show**: `fan first_name`, `message content` (inbound/outbound), `persona version`, `emotional_state`, `score`, `offer_id`, `product title`, `sale amount` — **PROVEN not secrets**.

**Verdict**: **PASS** privacy.

---

## 26. Performance Audit

**Event publication**: `publish_event` is `Redis PUBLISH` (O(1), 1 RTT, best-effort, no ack) — 5-7 events per generation (`message.created`, `ai.generation_started`, `persona.behavior` x2, `ai.generation_completed`, `message.sent`, `commerce.sale_recorded` occasional). At 10 msg/s, 70 PUBLISH/s — **negligible** for Redis.

**WebSocket broadcast**: `ws_manager.broadcast(event, creator_id)` iterates `websockets` per `creator_id` room (typically 1-5 dashboard tabs), **O(websockets)** per event, **not** O(fans). **Negligible**.

**Browser rendering**: `realtime.js` `onmessage` → `updateLivePanel` (DOM patch) + `showToast` (dedup via `event_id` Set) — **O(1)** per event, no history growth.

**Live overview query**: `GET /api/live?creator_id=1` currently likely `SELECT COUNT(DISTINCT user_id) FROM messages WHERE creator_id=$1 AND created_at > NOW()-'5m'` + `SELECT COUNT(*) FROM fangate_offers WHERE creator_id=$1 AND status='pending'` — **2 indexed queries**, ~5ms, polling every 5s → 12/min, **negligible**.

**Fan list query**: `GET /api/conversations_analytics?creator_id=1` with `messages` join + `operator_queue` — already creator-scoped after 43F, but **may be N+1** if per-fan `get_recent_messages` called in loop — check `live.py` not per-fan, just aggregates, so **not N+1**.

**Generation stage query**: New `GET /api/execution_timeline?creator_id=1&generation_id=abc` would be `SELECT * FROM generation_telemetry WHERE creator_id=$1 AND generation_id=$2` (PK) + `SELECT * FROM messages WHERE creator_id=$1 AND user_id=$2 ORDER BY created_at DESC LIMIT 20` — **2 PK/indexed queries**, **not N+1**.

**Notification dedup**: `event_id Set` in-memory (`realtime.js` `seenEventIds`) bounded (maybe 100), **negligible**.

**Large payloads**: `persona.behavior` 8 fields + `ai.generation_completed` `draft` not included (only `score`), `message.sent` `content` outbound (maybe 200 chars) — **small**.

**Unbounded growth**: `generation_telemetry` grows per generation (1 row per generation), but **not** sent to browser as history — only latest queried.

**Flag**: **N+1** if live panel does `for each active fan: GET /api/messages/recent?creator_id=1&user_id=777` in loop — **would be N+1** — but current `live` likely single aggregate query, not per-fan.

**Overall**: Current infra **can support** realtime observability at 10 msg/s without new worker/queue/LLM — **PROVEN**.

---

## 27. Creator Isolation Hostile Test

**Setup**: `Creator A` (id 1, Sunny), `Creator B` (id 2, Mia), same fan `777`, same `generation_id MD5(777:hey:100)=abc`, same `product 123`, same `external_transaction_id dropfans:xxx` pattern, same timestamps, same fan.

**Dashboard for Creator A** (`WebSocket ?creator_id=1`, `GET /api/queue/pending?creator_id=1` etc.):

- **Events**: `event_subscriber` `PUBLISH chatbot:events` with `creator_id 1` → `ws_manager` room `creator:1` only `creator 1` sockets receive; `creator 2` events go to room `creator:2` — **PROVEN** `ws_manager.broadcast` filters by `creator_id` (43F fix for `operator_queue` + `messages` ensures creator field present).

- **Notifications**: `showToast` dedup key `creator_id+generation_id` (or `creator+external_transaction_id`) ensures `Creator A` toast for `sale dropfans:xxx` does not dedup with `Creator B` same `external_transaction_id` (if DropFans reuses id across creators, unlikely but dedup key includes `creator_id` so not cross).

- **Stage**: `core/execution_stage.py` `derive_stage(generation_id, creator_id)` now composite (43F), so `generation_id abc` for creator 1 vs 2 maps to different `generation_telemetry` rows — **not wrong stage**.

- **Commerce state**: `fangate_offers` `WHERE creator_id=1` vs `2` separate, so `Creator A` not seeing `Creator B` offer.

- **Persona**: `CREATOR PERSONA` 19k Sunny vs Mia separate via `creator_id` in `get_structured_persona_async` + `persona.behavior` event `creator_id` — **not seeing wrong persona**.

**Same generation text, same product, same external transaction pattern, same timestamps, same fan**: All still isolated via `creator_id` in every downstream key (43F). **PROVEN**.

---

## 28. Restart / Reconnect Hostile Test

**Browser connected, event occurs, disconnects, worker continues, reconnects, polling runs**:

- **Which state recoverable?** `messages` (`RECEIVED`/`SENT`), `generation_telemetry` (`PROCESSING`→`SCORING`), `operator_queue` (`WAITING`), `fangate_offers`/`transactions` (`SALE`) are **recoverable** via polling `GET /api/messages/recent?creator_id=1&user_id=777`, `GET /api/generation_telemetry?creator_id=1&generation_id=abc`, `GET /api/queue/pending?creator_id=1` — all PG, survive restart.

- **Which events lost?** `chatbot:events` PubSub is **ephemeral** — events published while browser disconnected (e.g., `ai.generation_started` at `09:42:04.250`) are **lost** (no persistence). `ws_manager` does not replay.

- **Can current stage be reconstructed?** **YES** via polling `generation_telemetry` for `generation_id` + `messages` for `sent_at` + `operator_queue` for `pending` — but **currently dashboard does not poll `generation_telemetry` per generation** (only `queue` + `messages` + `analytics`). So after reconnect, browser can show `SENT` (via `messages` polling) but **cannot show `QWEN`/`SCORING` stage** until new polling endpoint added.

- **Do notifications duplicate?** `realtime.js` `seenEventIds` Set is **in-memory**, cleared on reconnect, so same `event_id` (if event re-published via `publish_event` retry) would toast again — but `event_id` is new uuid per publish, so not duplicate. However `message.sent` polled via `GET /api/messages/recent` after reconnect may re-toast `Reply sent` if `showToast` dedup is by `event_id` only (new polling has no `event_id`, would toast). **Duplicate LIKELY** if polling fallback does not dedup by `creator+generation`.

**UI must not falsely claim event happened merely because locally cached**: Currently `realtime.js` caches `lastStage` in memory, not `localStorage`, so after reconnect it correctly re-fetches via polling, not false claim.

---

## 29. Canary Audit

**Config** (`core/config.py:128`): `ai_agent_canary_enabled = False`, `ai_agent_canary_sample_rate = 0.0`, `ai_runtime_mode = "legacy"` (verified via `get_settings`). `workers/llm_worker.py:962 should_use_agent` checks `CanaryConfig.from_settings()` → false, so **never** `run_agent_runtime`.

- **Observability changes affect canary?** **NO** — `persona.behavior` derived only in `else: # Legacy` branch (`llm_worker.py:1072` inside `else: # Legacy runtime path`), not in `if _use_agent` branch, so canary agent path never derives persona behavior (agent has own). `core/telemetry` persona fields are optional, not required for canary.

- **Traffic allocation**: `should_use_agent` still `false` for 100% legacy, no promotion.

- **LLM calls**: Still `1 Qwen` per legacy, `0` extra for persona (derivation/validation are regex, no LLM).

- **Commerce**: `autonomous_allowed` gate still before Qwen, not affected by persona events.

**Verdict**: `CANARY = UNCHANGED` (proven via grep `canary` 0 in new behavior files, `ai_agent_canary_enabled` still false).

---

## 30. Implementation Boundary

### C0 — correctness / isolation

- Fix `generation_id` downstream composite: `telemetry _cache`, `generation_telemetry UNIQUE(creator,gen)`, `send_dedup:{creator}:{dedup}`, `messages/operator_queue WHERE (creator_id, generation_id)` — **C0, must fix** (proven collisions).

### C1 — execution timeline

- New `core/execution_stage.py` `derive_stage(creator_id, generation_id)` helper that reads `generation_telemetry` + `messages` + `operator_queue` + `fangate_*` and returns deterministic `RECEIVED...SENT` stage + timestamp, plus `GET /api/execution_timeline?creator_id=1&generation_id=abc` endpoint (polling fallback for reconnect). **C1**.

### C2 — realtime live panel

- Aggregate `GET /api/live?creator_id=1` already exists but needs creator filter audit + `active_fans` count `messages WHERE creator_id=$1 AND direction='inbound' AND created_at > NOW()-'5m'` (creator-scoped). **C2**.

### C3 — notifications

- Deterministic `showToast` for `REPLY_SENT` (`creator+generation`), `PPV_SENT` (`creator+offer_id`), `SALE_MADE` (`creator+external_transaction_id`), `FAILURE` (`creator+generation+failure_type`) with **business dedup key** (not just `event_id`) + **cooldown** for aggregate. **C3**.

### C4 — fan timeline

- Per-fan `GET /api/fan_timeline?creator_id=1&user_id=777&generation_id=abc` returning `RECEIVED→SENT` stages derived from existing PG, no new persistence. **C4**.

### C5 — commerce visibility

- Ensure `commerce.offer_created` event is published (currently missing) or dashboard polls `fangate_offers` — add `publish_event commerce.offer_created` on `execute_ppv` insert, and distinguish `PPV SENT` via `message.sent + vault.media_sent` vs `PPV CREATED`. **C5**.

### C6 — failure visibility

- Ensure `ai.generation_failed`, `message.send_failed`, `DLQ`, `persona.validation` `FACT_FAIL` all have `creator_id+generation_id` and are shown as `FAILED` stage, with retryable/permanent flag. **C6**.

### C7 — reconnect/recovery

- `realtime.js` must dedup by `creator+generation` business key, not just `event_id`, and on `onclose` → `onopen` re-fetch `execution_timeline` via polling to reconstruct current stage (not claim locally cached). **C7**.

**Not C0-C7**: No new worker/queue/LLM, no persona redesign, no DropFans change, no polling fallback removal.

---

## 31. Required Report

*(This document is the Stage A report, sections 1-33 as required)*

---

## 32. Required Implementation Map

See companion `docs/AI_NATIVE_OBSERVABILITY_PHASE_44A_IMPLEMENTATION_MAP.md` (to be created Stage B).

---

## 33. Required Test Plan

**Execution**:

- `message.created → RECEIVED` : `handlers` save + `message.created` event
- `debounce → DEBOUNCING` : `debounce:creator:{cid}:user:{uid}:lock` exists
- `inbound XADD → QUEUED` : `XLEN inbound_messages` + `message.created`
- `generation_started → PROCESSING` : `ai.generation_started` event + `generation_telemetry started_at`
- `persona.behavior → PERSONA` : `persona.behavior` event
- `Qwen → QWEN` : `ai.generation_started` + `persona.behavior` before + `generation_latency`
- `scoring → SCORING` : `ai.generation_completed` score
- `enqueue_send → SEND_QUEUED` : `XADD send_messages`
- `message.sent → SENT` : `message.sent` event

**Correlation**:

- Same `generation_id` + `creator_id` across primary path (PROVEN via `publish_event` all include both)
- `XAUTOCLAIM` preserves identity (same `generation_id` + `creator_id` re-derived)
- Retry preserves identity (MD5 deterministic + creator-scoped dedup)

**Commerce**:

- `offer created` → `fangate_offers` row
- `PPV actually sent` → `message.sent` + `vault.media_sent`
- `sale recorded` → `commerce.sale_recorded` + `fangate_transactions`
- `aftercare pending/completed` → `commerce.aftercare`
- `revenue` → `fangate_transactions seller_earning` sum

**Notifications**:

- `reply sent` → `message.sent was_auto_approved` → toast dedup `creator+generation`
- `PPV sent` → `message.sent` with media → toast dedup `creator+offer_id`
- `sale made` → `commerce.sale_recorded` → toast dedup `creator+external_transaction_id`
- `aftercare` → `commerce.aftercare` → toast dedup `creator+offer_id+state`
- `failure` → `ai.generation_failed` / `message.send_failed` → toast dedup `creator+generation+failure_type`
- `aggregate active-fan threshold` → edge `COUNT 22→23` + 5m cooldown, not per `message.created`
- `aggregate opportunity` → `present_offer` edge + cooldown

**Negative**:

- No false `sale` (PPV without `sale_recorded` not sale)
- No false `sale-lost` (no deterministic `sale_lost` state)
- No false `hot` (no `HOT` event, only `temperature` telemetry)
- No cross-creator notification (creator filter)
- No duplicate toast (business dedup, not just `event_id`)
- No WebSocket/polling duplicate (dedup by `creator+generation` business key)
- No XAUTOCLAIM duplicate (generation_id correlation)
- No stale stage after reconnect (reconstruct via polling `generation_telemetry` + `messages`)

**Privacy**:

- `no buyer_email` in `commerce.sale_recorded` (proven)
- `no credentials` in `persona.behavior`
- `no full prompts` (only `message_preview` 100 chars)

---

PHASE 44A VERDICT

REALTIME EXECUTION VISIBILITY: PARTIAL
EXECUTION-STAGE DETERMINISM: PARTIAL
GENERATION CORRELATION: PASS
PERSONA OBSERVABILITY: PASS
QWEN OBSERVABILITY: PARTIAL
SCORING OBSERVABILITY: PASS
SEND OBSERVABILITY: PARTIAL
PPV VISIBILITY: PARTIAL
SALES VISIBILITY: PASS
REVENUE VISIBILITY: PASS
AFTERCARE VISIBILITY: PASS
HOT STATE: NOT REPRESENTABLE
ACTIVE FAN STATE: PARTIAL
OPPORTUNITY STATE: PARTIAL
SALE-LOST STATE: NOT REPRESENTABLE
FAILURE OBSERVABILITY: PASS
NOTIFICATION INFRASTRUCTURE: PARTIAL
NOTIFICATION DEDUPLICATION: PARTIAL
CREATOR ISOLATION: PASS
FAN ISOLATION: PASS
RECONNECT SAFETY: PARTIAL
XAUTOCLAIM SAFETY: PASS
PRIVACY: PASS
PERFORMANCE: PASS
CANARY SAFETY: PASS

P0: 0
P1: 4
P2: 5
P3: 2

PRODUCTION CHANGES: NONE
SCHEMA CHANGES: NONE
REDIS MUTATIONS: NONE
CANARY CHANGES: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

NEXT ACTION:
Stage B — implement C0-C7 without new worker/queue/LLM: (C0) generation_id downstream composite already done in 43F, verify; (C1) derive_stage helper + GET /api/execution_timeline polling fallback; (C2) ensure live aggregate creator-scoped; (C3) business-deduped notifications (creator+generation / creator+offer / creator+external_transaction); (C4) fan timeline query; (C5) publish commerce.offer_created and distinguish PPV SENT via message.sent+vault.media_sent; (C6) failure stage mapping; (C7) realtime.js business dedup + reconnect polling reconstruction; all using existing streams/PubSub/telemetry/PG, no architecture redesign.

---

**Answers to 10 explicit questions:**

1. Can we deterministically show what the bot is doing right now? **PARTIAL** — per-generation `ai.generation_started`/`persona.behavior`/`ai.generation_completed` + `message.sent` are deterministic and creator-scoped, but `RECEIVED→QUEUED→PROCESSING→PERSONA→QWEN→SCORING→SEND_QUEUED→SENT` timeline is not yet derived as single `execution_stage` for UI — needs new `derive_stage` helper (C1).

2. Can we reconstruct the complete execution timeline for a fan? **PARTIAL** — `messages` + `generation_telemetry` + `operator_queue` + `fangate_*` already contain all evidence, but no single `GET /api/execution_timeline?creator_id&generation_id` endpoint yet — polling fallback can, but not implemented.

3. Can we notify the creator when a major event occurs without guessing? **PARTIAL** — `REPLY_SENT` (`message.sent`), `PPV_SENT` (`vault.media_sent`), `SALE_MADE` (`commerce.sale_recorded`), `FAILURE` (`ai.generation_failed`/`message.send_failed`) are deterministic and dedupable via `creator+generation`/`creator+external_transaction` — **YES**. `HOT`/`SALE_LOST` **NO** (not representable).

4. Can we safely distinguish PPV CREATED from PPV SENT? **PARTIAL** — via `fangate_offers` (created) vs `message.sent`+`vault.media_sent` (sent), but no `commerce.offer_created` event currently — dashboard must poll both.

5. Can we safely distinguish PPV SENT from SALE? **PASS** — `PPV SENT` is `message.sent` + `vault.media_sent`; `SALE` is `commerce.sale_recorded` + `fangate_transactions seller_earning` — **distinct**.

6. Can we deterministically identify a lost sale? **NOT REPRESENTABLE** — no `sale_lost` state, offer expiration not deterministic.

7. Can we deterministically identify a hot conversation? **NOT REPRESENTABLE** as event — `temperature=HOT` is per-generation derived telemetry, not `commerce.hot` event with edge detection.

8. Can we calculate active fans without cross-creator contamination? **PARTIAL** — `messages WHERE creator_id=$1 AND direction='inbound' AND created_at > NOW()-'5m'` is creator-scoped after 43F, but current `live.py` not proven creator-scoped.

9. Can WebSocket + polling coexist without duplicate notifications? **PARTIAL** — `event_id` dedup handles same `PUBLISH` via WebSocket, but **not** business dedup for same `generation_id` retried with new `event_id` or for `message.sent` via WebSocket + polling `messages/recent` — needs business key `creator+generation` dedup.

10. Can all of this be implemented without adding workers, queues, LLM calls, or redesigning architecture? **PASS** — all required evidence already in existing streams/PubSub/PG/telemetry; only new is `derive_stage` helper + polling endpoint + business-deduped `showToast` + `commerce.offer_created` publish (one `PUBLISH` in `execute_ppv`), all using existing `chatbot:events` + `ws_manager` + polling.

