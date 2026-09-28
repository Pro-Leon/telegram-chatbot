# AI_NATIVE_COMMERCE_PHASE_24_IMPLEMENTATION_MAP.md
# Phase 24 — Implementation Map (Stage B)
# Date: 2026-08-30

## 1. Actual Runtime Graph (after Phase 24 fixes)
```
Telegram inbound
 → debounce_enqueue (Redis) → XADD inbound_messages
 → XREADGROUP llm_workers (llm_worker 60s idle XAUTOCLAIM)
 → acquire_user_lock 30s
 → upsert_user, is_user_auto_reply_excluded
 → resolve_single_application_creator (DropFans verified)
 → build_qwen3_context (conversation_state, response_mode, question_budget, AVAILABLE CONTENT relevance-ranked, LTM 3, commerce_text, summary)
 → extract_explicit_memories → add_memory_item (creator-scoped, bounded 20)
 → ai.generation_started(generation_id UUID)
 → SINGLE extract_commerce_signals(context) [1 LLM cheap_model]
 → _try_commerce_draft with signals=once → CommerceStateRequest → resolve_and_run_commerce → pipeline (decision → strategy → orchestrate → execute_ppv lock ppv_offer) → selection
 → if USE_COMMERCE_RESPONSE: draft from selection (generate_commerce_response was the 1 Qwen)
   else:
     → build_conversational_commerce_state (desire/decay, temp, relevance, readiness, objective, window, conversation_intelligence priority, NBA)
     → response_mode/question_policy subordinate to NBA
     → resolve_open_loop
     → strategy exposure make_exposure → persist_exposure JSONB 50 + telemetry
     → experiment assignment deterministic SHA256
     → compute_pressure → derive_risk → derive_lifecycle → build_operation_decision (single authoritative gate, trace <500)
     → **Phase 24 pre-Qwen gate (before Qwen, fail-closed, saves Qwen):**
         autonomous_allowed(creator, strategy=_nba_str, experiment) → global→creator→strategy→experiment
         + commerce pause for present_offer/complete_purchase via is_commerce_paused
         + reengagement pause for re_engage via is_reengagement_paused
         + handoff via get_handoff_memory (active → block)
         + is_rollout_active_for loop over _rollout_registry for strategy/global/creator scopes (deterministic SHA256 bucket)
         → if not allowed or rollout_blocked: _skip_qwen True → draft = safe fallback, score 0.1 flags autonomous_paused, skip Qwen entirely
     → else canary should_use_agent check (default false) → legacy:
         generate_draft / generate_draft_with_tools [1 Qwen get_llm_provider().generate_with_history, Ollama authoritative, dedup trailing]
 → scoring (1) score_draft authority-aware (hard flags → 0.1, failure → 0.0)
 → **post-scoring production gate (before send, fail-closed):**
     autonomous_allowed (now includes commerce) + autonomous_commerce_allowed → if not allowed: score 0.1 flags autonomous_paused
     record_metric generation_success/failure, pressure_suppressed, rejections, etc. (now also persist to sentinel -999997 via _persist_metric_event for cross-worker health)
     record_audit OperationalAuditRecord (generation_id, creator, user, objective, strategy, experiment, risk, pressure, decision, outcome) bounded 1000
 → shadow collect (still best-effort, not counted)
 → routing dedup md5(user:msg:telegram_id)
     → if empty draft → operator_queue + completed False
     → elif not auto_reply_enabled → operator_queue
     → elif score>=0.80 and not flags and allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue (lifecycle invariant) else operator_queue + suggestion.created
 → post_process async
 → outcome feedback: classify_canonical_outcome → outcome_strength → attribute_purchase (DropFans authority) → update_strategy_evidence + extended (composite key, dedup generation_id 100, bounded 20, Beta)
 → telemetry.complete → insert_generation_telemetry
 → except → ai.generation_failed same generation_id, not hidden
 → finally release_user_lock

Scheduler loop (_scheduler_loop, every 10s):
 → recover_stale (300s), process_due_messages (claim_due_messages batch 20, check _should_skip_due via is_global_paused), reconcile_purchases
 → orchestrate_production_controls() periodic (health→rollback/hold/advance, idempotent via check_idempotent, audit, metrics)
 → re-engagement loop: for each creator active, list_offers_for_creator pending 50, for each age>=48h → check is_global_paused / is_reengagement_paused / is_commerce_paused before governance → compute_pressure/fatigue → is_reengagement_governed_allowed (pressure_suppress/fatigue/max_frequency) → schedule_reengagement_if_eligible dedup reengage:{c}:{u}:{p} via scheduled_messages

Send path (bot_main _process_send_stream):
 → requeue_stalled_send_messages XAUTOCLAIM 30s
 → read_send_messages
 → is_send_duplicate dedup check → ack skip
 → rate limiting check_send_rate_limit (Lua token bucket 5 burst, 1/sec) → if not allowed: ack + requeue
 → blacklist check is_blacklisted → DLQ + dedup 86400 + XACK
 → get_input_entity → permanent failures (ValueError, RPCError) → blacklist + DLQ + XACK (no requeue, no loop)
 → FloodWait → sleep + ack + requeue (retryable)
 → reserve_delivery atomic before send → if already reserved skip + ack
 → send_file / send_message
 → mark_send_dedup 3600, ack_send
 → save_outbound_after_send (only if save_to_db true and entity int) → publish message.sent + vault.media_sent
 → UserIsBlocked → ack + publish message.send_failed
 → generic exception → move_send_to_dlq + publish send_failed

Worker startup (llm_worker.run_worker, scheduler_worker.run_scheduler):
 → init_pool, ensure_consumer_group
 → **Phase 24: load_persisted_state()** from user_profiles sentinels -999999 (rollouts), -999998 (emergency), -999997 (metrics last 50) → restores _rollout_registry, _emergency_state, _metric_events for restart safety
 → heartbeat, loop

## 2. Identified Gaps (Stage A → B)
| Gap | Before | After | Files Changed |
|---|---|---|---|
| Commerce pause not enforced | autonomous_allowed checked only global/creator/strategy/experiment | Extended autonomous_allowed to check is_commerce_paused + is_reengagement_paused for re_engage; added autonomous_commerce_allowed helper; llm_worker pre-Qwen gate now checks commerce for present_offer/complete_purchase | commerce/production_control.py, workers/llm_worker.py |
| Re-engagement pause not enforced in scheduler | scheduler only checked governed, not emergency pause | Added is_global_paused / is_reengagement_paused / is_commerce_paused gate before re-engagement scheduling and global pause before claim_due_messages | workers/scheduler_worker.py |
| Handoff not enforced before Qwen | Only via risk, not explicit handoff memory | Added get_handoff_memory check in llm_worker pre-Qwen gate | workers/llm_worker.py |
| Rollout only strategy scope | Loop checked only scope==strategy | Extended to also gate global/creator rollouts | workers/llm_worker.py |
| Metrics disconnected across workers | record_metric in-memory only per process, scheduler health empty | Added _persist_metric_event to sentinel -999997 and load_persisted_state merges last 50 on startup; record_metric schedules persist via asyncio.create_task | commerce/production_control.py |
| Rollout/emergency not restart-safe | _rollout_registry and _emergency_state in-memory cleared on restart | Added persist via user_profiles sentinels -999999/-999998 with bounded 50/100, load on startup, persist on set/disable via create_task | commerce/production_control.py, workers/* |
| Pressure/risk not gating strategy | Computed only for telemetry | Now pre-Qwen gate uses pressure-derived _op_dec via handoff + commerce gates; strategy_governed_selection already respects risk suppress but now enforced via autonomous gate (fail-closed) | workers/llm_worker.py (gate) |
| Global pause not saving Qwen | Post-scoring only | Moved check before Qwen to save Qwen (pre-gate) while keeping post-scoring as second fail-closed | workers/llm_worker.py |

## 3. Exact Fixes
### commerce/production_control.py
- Extended `autonomous_allowed` to check `is_commerce_paused` (global→creator) and `is_reengagement_paused` for re_engage strategies, added `autonomous_commerce_allowed`.
- Added `_persist_metric_event` (sentinel -999997, bounded 200, keeps last 50 in-memory on load).
- Added `load_persisted_state` (loads rollouts, emergency, metrics from sentinels), `persist_emergency_state` (bounded 100), integrated with `set_emergency`/`clear_emergency` via `loop.create_task`.
- Modified `record_metric` to schedule `_persist_metric_event` via `loop.create_task` if running.
- Existing `_persist_rollout` already existed (sentinel -creator_id), now complemented by load.

### workers/llm_worker.py
- Extended pre-Qwen gate (lines 809-838) to:
  - Check `is_commerce_paused` when objective is present_offer/complete_purchase
  - Check `is_reengagement_paused` when strategy is re_engage
  - Check `get_handoff_memory` active → block
  - Check global/creator rollouts in addition to strategy
  - Keep fail-closed fallback draft and telemetry
- Added `load_persisted_state` call after `init_pool`/`ensure_consumer_group` in `run_worker`.

### workers/scheduler_worker.py
- Added global pause check before `recover_stale`/`process_due_messages`/`reconcile_purchases` (skip due messages when globally paused, still recover stale).
- Added re-engagement gate: `is_global_paused` or `is_reengagement_paused` or `is_commerce_paused` → continue (skip) before governance.
- Added `load_persisted_state` call after `init_pool`/`ensure_consumer_group` in `run_scheduler`.

### Tests
- Created `tests/test_phase24_production_readiness.py` 86 tests covering A-Z, behavior matrix, P2, single-pass, enforcement, canary, rollback, recovery, concurrency, no unsafe operation.
- All fixes are minimal, deterministic, no new LLM calls, no new workers/queues, no migrations, no architecture redesign, provider unchanged (ollama/qwen2.5:3b).

## 4. Control Enforcement Mapping
- GLOBAL pause → `is_global_paused` → `autonomous_allowed` before Qwen and before send + scheduler skip → blocks all autonomous commerce and re-engagement, forces operator queue, saves Qwen.
- CREATOR pause → `is_creator_paused` → same path, isolated per creator.
- STRATEGY pause → `is_strategy_paused(strategy, creator)` → blocks that strategy cohort deterministically.
- EXPERIMENT pause → `is_experiment_paused` → blocks that experiment variant, falls back to CONTROL.
- COMMERCE pause → `is_commerce_paused` → blocks present_offer/complete_purchase only, not relationship.
- RE-ENGAGEMENT pause → `is_reengagement_paused` → blocks scheduled re-engagement loop and re_engage strategy.
- Rollout percentage → `is_rollout_active_for` SHA256 bucket → stable assignment, gate before Qwen.
- Production state → `derive_production_state` → `evaluate_production_health` → `evaluate_rollout_gate` (sample>=5, window>=1h, error<0.20, etc.) → `orchestrate_production_controls` periodic → hold/advance/rollback.
- Regression → `should_rollback` + `perform_rollback` → `disable_rollout`/`disable_experiment` → `is_rollout_active_for` false → safe fallback, does not delete evidence/memory/purchase/audit.
- Degraded → `classify_failure` + `degraded_fallback` matrix wired before Qwen (Qwen fail → safe fallback, scoring fail → operator queue 0.0, memory→continue_without_memory, product→no offer, DropFans→commerce_suppressed).
- Handoff → `get_handoff_memory` active → risk HANDOFF → `build_operation_decision` allowed False, handoff_required True → pre-Qwen gate blocks.

## 5. Test Mapping
- A complete path → TestA
- B single-pass → TestB + verify_single_pass
- C production-control → TestC (emergency before Qwen, isolated, commerce/reengagement, rollout gating)
- D canary assignment → TestD (deterministic, stable, 0/100, restart safety)
- E canary progression → TestE (0→1→5 health observation, hold on regression)
- F rollback → TestF (regression triggers, preserves history)
- G roll-forward → TestG (enable after rollback)
- H emergency → TestH (6 controls each block/recover, fail-closed)
- I degraded → TestI (5 modes)
- J Redis recovery → TestJ (retryable XAUTOCLAIM)
- K DLQ+ACK → TestK (permanent DLQ ack)
- L idempotency → TestL (md5 stable, generation dedup, send dedup)
- M creator isolation → TestM (exposures, metrics, rollout)
- N fan isolation → TestN
- O DropFans authority → TestO (invented price/product/url/purchase blocked)
- P memory authority → TestP
- Q strategy learning → TestQ (positive/purchase bonus, fatigue, evidence affects selection, one isolated not dominate)
- R experiment safety → TestR (unsafe rejected, deterministic, min sample, disable)
- S handoff → TestS
- T lifecycle → TestT
- U re-engagement → TestU
- V telemetry → TestV (fields, trace bounded <500, no PII)
- W production health → TestW (healthy/caution/degraded/suppressed)
- X recovery → TestX (paused→recovering→caution→normal, rollback not jump to 100)
- Y concurrency → TestY (lock, isolation, dedup)
- Z no unsafe autonomous → TestZ (paused never auto sends, 0% rollout blocked, commerce pause)
- Behavior matrix A-T → TestBehaviorMatrix (8 cases)
- Known P2 → TestKnownP2

## 6. Single-pass Proof
- 1 extract_commerce_signals (shared via signals param to _try_commerce_draft and conversational bridge)
- 1 Qwen (either generate_commerce_response inside pipeline when USE_COMMERCE_RESPONSE, else generate_draft/generate_draft_with_tools; pre-gate may skip Qwen entirely when paused, still counts as 0 not 1 but never 2)
- 1 scoring (score_draft authority-aware)
- 0 additional LLM (memory, strategy, pressure, risk, experiment, telemetry, outcome classification all pure deterministic)
- Verified via `verify_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0})` and via mocked process_message counts.

## 7. No New LLM/Workers/Queues/Migrations
- NEW LLM CALLS: 0 (all new code is pure or best-effort async persistence via existing user_profiles JSONB)
- NEW WORKERS: 0 (reused llm_worker, send_worker, scheduler_worker, bot_main)
- NEW QUEUES: 0 (reused inbound_messages, send_messages, DLQ_STREAM)
- MIGRATIONS: NONE (user_profiles JSONB sentinels -999999/-999998/-999997 use existing table, bounded lists)
- ARCHITECTURE: NO REDESIGN
- PROVIDER: UNCHANGED (ollama/qwen2.5:3b, cheap_model gemini-flash-latest with fallback)

