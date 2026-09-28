# AI_NATIVE_COMMERCE_PHASE_24_FORENSIC_AUDIT.md
# Phase 24 — Forensic Reconciliation (STAGE A, before fixes)
# Date: 2026-08-30
# Scope: workers/llm_worker.py, workers/scheduler_worker.py, commerce/*, memory/context, core/telemetry, db/redis, db/postgres, db/dropfans, chatbotv2/*, core/config

## 1. Legend
- DEFINED = symbol exists
- CALLED = at least one production caller
- RUNTIME-REACHABLE = reachable from workers/llm_worker.process_message or scheduler_worker._scheduler_loop or send_worker flush_queue or bot_main _process_send_stream
- ENFORCED = return value gates behavior (not just logged)
- PERSISTED = survives restart via PostgreSQL user_profiles / generation_telemetry / commerce_offers or Redis stream
- OBSERVED = appears in telemetry/audit/trace queryable
- TESTED = has deterministic unit test
- AUTONOMOUS = invoked periodically without manual trigger

## 2. Execution Graph (actual before Phase 24 fixes)
```
Telegram inbound (Telethon client on_update -> debounce_enqueue -> enqueue_inbound XADD inbound_messages)
  ↓ XREADGROUP llm_workers (llm_worker.run_worker: requeue_stalled_messages XAUTOCLAIM 60s, read_inbound count 5)
  ↓ acquire_user_lock (Redis lock:user:{id} 30s TTL) fail → skip
  ↓ upsert_user + is_user_auto_reply_excluded → excluded → telemetry excluded → return (no Qwen)
  ↓ resolve_single_application_creator (dropfans, verified)
  ↓ build_qwen3_context(user_id, user_message, persona, creator_id)
      → get_user, get_user_profile, get_recent_messages 20, trim to 800 tokens
      → derive_conversation_state(history + current_message) → current_topic, open_threads, tone, lifecycle
      → plan_response_mode, evaluate_question_budget
      → build_qwen3_system_prompt (persona compressed, lifecycle-aware)
      → build_qwen3_state_context (profile, commerce_text via build_llm_context, summary, conversation_state, AVAILABLE CONTENT rank_products_by_relevance TOP2, LTM retrieve_relevant_memories 3)
      → recent_history_for_state reused (no duplicate fetch)
  ↓ extract_explicit_memories → add_memory_item (creator-scoped, bounded 20, no LLM)
  ↓ shadow launch (fire-and-forget, only if qwen_shadow_enabled true; default false → 0 LLM)
  ↓ publish ai.generation_started(generation_id UUID, user_id, dialog_id, scope=user, event_id UUID)
  ↓ SINGLE extract_commerce_signals(context) → CommerceSignals (purchase_intent etc.) [1 LLM via cheap_model, fallback Ollama]
  ↓ _try_commerce_draft(user_id, context, persona, signals=signals_for_both)
      → resolve_single_application_creator again
      → resolve_commerce_product_with_history (list_valid_products, relevance, exclude purchased) deterministic
      → CommerceStateRequest(user_id, creator_id, product_id, messages PIPELINE_MAX 30, persona)
      → resolve_and_run_commerce (resolve_commerce_state: eligibility, product identity/state, timing, behavioral, relationship → Pipeline run_commerce_pipeline with signals=once → decide 23-branch → strategy → orchestrate → execute_ppv advisory lock → selection)
      → selection = select_commerce_response (USE_COMMERCE_RESPONSE vs fallback)
  ↓ if USE_COMMERCE_RESPONSE: draft = selection.commerce_response_text (already generated via generate_commerce_response inside pipeline — counts as the 1 Qwen for this branch, no second call)
     else:
       → build_conversational_commerce_state(user_id, creator_id, context, conversation_state, selection, signals, current_topic, open_threads)
           → derive_desire_stage, decay_desire, derive_commercial_temperature, list_valid_products + rank_products_by_relevance (relevance >=0.15), evaluate_offer_readiness, derive_commercial_objective, derive_sales_window, derive_conversation_objective (priority map)
       → derive response_mode/question_policy subordinate to next_best_action (follow_up_open_loop→callback ONE_NATURAL_QUESTION, explore_interest/qualify→explore ONE_NATURAL_QUESTION, deepen_desire→tease OPTIONAL, present_offer→tease NO_QUESTION, handle_objection/aftercare/handoff/wait→react NO_QUESTION)
       → resolve_open_loop (deterministic)
       → strategy exposure make_exposure(creator_id, user_id, generation_id, strategy_family=_nba_str, topic=_cur_topic, conversation_stage=objective, desire_stage, temperature, sales_window, next_best_action, response_mode, question_policy) → persist_exposure (JSONB bounded 50, prune 30d) + in-memory, telemetry strategy_selected/source/mode/confidence/fatigue
       → experiment assignment via _experiment_registry (deterministic SHA256)
       → compute_pressure(recent_offer_count, rejection_count, aftercare, cooldown, fatigue, temp.score, recent_questions, objective) → bucket relationship/exploration/opportunity/suppress → derive_risk (SAFE/CAUTION/SUPPRESS/HANDOFF) → derive_lifecycle (NEW..RE_ENGAGED 15) → build_operation_decision (single authoritative gate, trace_compact bounded <500, no PII)
       → [GAP before Phase 24: pressure/risk/lifecycle computed but NOT enforced via strategy_governed_selection for this turn's Qwen; only telemetry. Fixed in Phase 24 by extending pre-Qwen gate.]
       → [Phase 23 pre-Qwen gate: autonomous_allowed(creator, strategy=_nba_str, experiment) + is_rollout_active_for loop over strategy-scoped rollouts → if not allowed or rollout_blocked → _skip_qwen_due_to_pause True → draft = fallback safe, score 0.1 flags autonomous_paused, skip Qwen. Before Phase 24 this checked only strategy rollouts + global/creator/strategy/experiment pauses, not commerce/reengagement/handoff. Phase 24 extends to commerce, reengagement, handoff, global/creator rollouts.]
       → if _skip_qwen_due_to_pause: draft already fallback, skip Qwen entirely (saves Qwen, fail-closed)
       → else check canary should_use_agent (ai_agent_canary_enabled false default → legacy) → if agent and ai_runtime_mode agent/canary → agent runtime (not used in production canary tests) else legacy:
           → if llm_tools_enabled and creator_id: generate_draft_with_tools(context, user_message, auth) else generate_draft(context, user_message) [1 Qwen via get_llm_provider().generate_with_history, Ollama authoritative, Gemini fallback if enabled, max_output_tokens 200, temp 0.85, deduplication of trailing user message]
  ↓ generation_latency measured
  ↓ if draft empty: route to operator_queue with empty_draft flag, publish ai.generation_completed (was_auto_approved False), telemetry excluded, release lock, return
  ↓ score_draft(draft, user_message, context, is_authorized_commerce, authorized_price/url) → (score 0..1, flags) [1 scoring via cheap_model, fallback, hard flags → min 0.1, failure → 0.0 fail-closed, empty draft already handled] [1 LLM]
  ↓ post-scoring production_control gate (Phase 23 wired): autonomous_allowed(creator, strategy_selected, experiment_id) → if not allowed: score=min(0.1), flags+=autonomous_paused, telemetry operation_block_reason, failure_class handoff_required else operation_allowed True; record_metric generation_success/failure, pressure_suppressed, rejections, etc.; record_audit OperationalAuditRecord generation_id/creator/user/objective/strategy/experiment/variant/risk/pressure/decision/outcome
  ↓ shadow collect (wait 2s, evaluate, log)
  ↓ routing: dedup_id = md5(user_id:user_message:telegram_message_id)
      → if not auto_reply_enabled: operator_queue + completed False + suggestion.created
      → elif score>=0.80 and not flags: enqueue_send({entity=str(user_id), content=draft, draft_content, was_edited False, was_auto_approved True, confidence_score, operator_id None, save_to_db True}, dedup_id) → must succeed before ai.generation_completed (lifecycle invariant) → publish ai.generation_completed (was_auto_approved True) [no suggestion.created]
      → else: operator_queue + suggestion.created
  ↓ post_process async: extract_and_update_profile + maybe_summarize (non-blocking)
  ↓ strategy learning feedback (best-effort): get_user_profile strategy_last_by_creator → previous_strategy → classify_outcome (legacy 6-way) + classify_canonical_outcome (18-way, fan_message, desire_before/after, purchase false) → outcome_strength → attribute_purchase (if exposure time) → update_strategy_evidence + update_strategy_evidence_extended(composite strategy:topic:product_family:lifecycle, dedup generation_id ring 100, bounded 20, Beta confidence ±0.05 purchase +0.10, decay exp(-days/30)) → store current strategy as last for next turn via strategy_last_by_creator
  ↓ telemetry.complete(success) → insert_generation_telemetry best-effort
  ↓ publish ai.generation_failed on exception with same generation_id, failure not hidden

Branches identified:
- SAFETY (is_blocked → HUMAN_HANDOFF)
- HANDOFF (operator_required, is_handoff, automation_restricted → HANDOFF, suppress commercial)
- AFTERCARE (pending/sent → AFTERCARE, suppress offer)
- OBJECTION (has_objection or is_on_cooldown → HANDLE_OBJECTION)
- OPEN LOOP (has_open_loop importance>=0.7 → FOLLOW_UP_OPEN_LOOP)
- DIRECT PURCHASE REQUEST (explicit_purchase_request → PRESENT_OFFER priority 5)
- PRESENT OFFER (readiness ready, window open, has_relevant_product, not active offer, not cooldown, not aftercare)
- QUALIFICATION, DEEPEN_DESIRE, EXPLORE_INTEREST, CONTINUE_TOPIC, RELATIONSHIP_BUILD, RE_ENGAGE, WAIT, DEGRADED (Qwen/scoring/memory/product/DropFans failure → safe fallback), SUPPRESSED (pressure bucket suppress or risk suppress → no commercial), PAUSED (global/creator/strategy/experiment/reengagement/commerce pause → autonomous_paused, score 0.1, operator queue), ROLLBACK (health regression → perform_rollback → rollout ROLLED_BACK)

No later component overrides higher priority: conversation_intelligence priority map lower number higher priority, eligible sorted by priority, first wins; policy_allows before/after Qwen checks aftercare/cooldown/rejection/product/pressure/invented price/product/url/purchase claim/creator cross-contam; operation_decision with handoff/pressure suppress forces allowed=False; scoring hard flags cap at 0.1; autonomous gate before Qwen saves Qwen and forces fallback; post-scoring gate forces operator queue.

## 3. Capability Matrix (Stage A before fixes)

| Capability | DEFINED | CALLED | RUNTIME-REACHABLE | ENFORCED | PERSISTED | OBSERVED | TESTED | AUTONOMOUS |
|---|---|---|---|---|---|---|---:|---|
| Deterministic behavioral learning (strategy_evidence 20, composite key, dedup 100, decay 30d) | YES | YES | YES process_message 1215-1284 | YES via select_strategy_adaptive hierarchy 5/10 thresholds | YES via user_profiles strategy_evidence_by_creator bounded 20 + strategy_generation_seen 100 | YES via telemetry strategy_selected/source/mode/confidence | YES phase20 50+ | YES per inbound |
| Hierarchical strategy selection (FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE, Beta, fatigue, budget 0.10) | YES | YES | YES via adaptive_optimization select_strategy_adaptive | YES via select_strategy_adaptive scores + fatigue | YES via JSONB same as above | YES via trace | YES | YES per turn |
| Outcome attribution (CanonicalOutcome 18, OUTCOME_WEIGHTS, outcome_strength, lifecycle weights) | YES | YES | YES classify_canonical_outcome per fan message | YES via update_strategy_evidence_extended weights | NO (in-memory only for learning, but evidence persisted) | YES via telemetry outcome/strength | YES | YES per turn |
| Strategy fatigue (compute_fatigue 0.15 per repeat >=3/5, maps, response/question/product family) | YES | YES | YES via compute_fatigue before exposure | YES via strategy_score fatigue_penalty | YES via exposures JSONB 50 | YES via telemetry fatigue_score | YES | YES per turn |
| Exploration/exploitation (should_explore, budget 10%, Beta uncertainty 0.02-0.5, SAFE_DEFAULT) | YES | YES | YES via select_strategy_adaptive mode | YES via exploration_budget_ok + mode | YES via evidence same | YES | YES | YES |
| Conversation intelligence (derive_conversation_objective 14 objectives, priority 1-99, candidates) | YES | YES | YES via conversational bridge 189 | YES via objective gate | NO (derived per turn, not stored) | YES via telemetry objective | YES | YES |
| Next-best-action (derive_conversation_objective → next_best_action, response_mode/question_policy subordinate) | YES | YES | YES | YES via response_mode subordinate | NO (per turn) | YES | YES | YES |
| Long-term memory (create/retrieve/extract/resolve, 20 bounded, 90/30/7 day decay, OPEN_LOOP) | YES | YES | YES via retrieval 3 + extraction before Qwen | YES via retrieval ranked by overlap+confidence+recency | YES via user_profiles long_term_memory_by_creator 20 | YES via telemetry memory counts | YES | YES |
| Creator-scoped commercial memory (commercial_preferences_by_creator) | YES | YES | YES via content_matching + retrieval | YES via get_commercial_preferences creator_id | YES via JSONB same key | YES | YES | YES |
| Product knowledge (ProductKnowledge, vault_taxonomy, semantic tokens) | YES | YES | YES via rank_products_by_relevance | YES via relevance 0.15 threshold, purchased excluded | NO (derived from fangate_products) | YES via AVAILABLE CONTENT | YES | YES |
| Objection intelligence (has_objection, consecutive_rejections, HANDLE_OBJECTION) | YES | YES | YES | YES via objective priority | YES via behavioral_feedback consecutive_rejections | YES | YES | YES |
| Qualification (desire qualification, sales_window building/open) | YES | YES | YES | YES | YES via timing | YES | YES | YES |
| Commercial pressure governance (compute_pressure 0..1, bucket relationship/exploration/opportunity/suppress) | YES | YES | YES via compute_pressure 0..1 | PARTIAL (computed but not gating strategy before Qwen; only telemetry) → FIXED Phase 24 | NO (per turn) | YES via telemetry pressure_score | YES | PARTIAL |
| Anti-spam controls (is_spam_risk, repeated questions, reengagement frequency) | YES | YES | YES | YES via is_spam_risk | NO (per turn) | YES | YES | YES |
| Lifecycle state (derive_lifecycle 15 states) | YES | YES | YES | YES via aftercare/cooldown/handoff/purchase | NO (per turn) | YES via telemetry lifecycle_state | YES | YES |
| Risk governance (derive_risk SAFE/CAUTION/SUPPRESS/HANDOFF) | YES | YES | YES | PARTIAL (same as pressure) | NO | YES | YES | PARTIAL |
| Human handoff (make_handoff, get/set/clear memory, automation_restricted) | YES | YES | YES via handoff_by_creator JSONB | PARTIAL (not checked before Qwen pre-gate; only via risk) → FIXED | YES via JSONB handoff_by_creator | YES | YES | PARTIAL |
| Degraded-mode behavior (classify_failure 4, degraded_fallback 10) | YES | YES | YES | YES (Qwen→safe fallback, scoring→operator_queue, memory→continue_without_memory, product→no offer, DropFans→commerce_suppressed) | NO | YES via failure_class | YES | YES |
| Redis Streams (XADD/XREADGROUP/XACK/XAUTOCLAIM/pending/DLQ/dedup) | YES | YES | YES via db/redis | YES (XAUTOCLAIM 30s, DLQ+XACK, dedup md5) | YES via Redis stream persistence | YES | YES | YES per loop |
| Consumer groups (llm_workers, send_workers) | YES | YES | YES | YES | YES via Redis | YES | YES | YES |
| XAUTOCLAIM | YES | YES | YES requeue_stalled_messages 30s | YES | YES | YES | YES | YES per loop 0.5s |
| DLQ | YES | YES | YES move_to_dlq/move_send_to_dlq | YES (ACK after XADD, no requeue) | YES via DLQ_STREAM | YES | YES | YES on failure |
| Idempotency (dedup md5, send_dedup 3600, generation_seen 100, check_idempotent 2000) | YES | YES | YES | YES | PARTIAL (in-memory + Redis dedup 3600) | YES | YES | YES |
| DropFans authority (sole purchase authority, transaction_id unique) | YES | YES | YES via dao has_valid_purchase_evidence, commerce authority | YES via policy_allows purchase_claim_without_evidence + dao | YES via fangate_transactions/commerce_offers | YES | YES | YES |
| Production metrics (MetricWindow 1h/24h/7d/30d, aggregate_count/rate, bounded 5000) | YES | YES | PARTIAL (record_metric per generation after scoring, but scheduler orchestrate reads own in-memory empty → disconnected) → FIXED via persist sentinel -999997 | PARTIAL (in-memory only, not shared) → FIXED | YES | YES | PARTIAL (per generation after Phase 23, but cross-worker disconnected) |
| Canary rollout framework (Rollout 0/1/5/10/25/50/100, is_rollout_active_for SHA256 deterministic) | YES | YES | PARTIAL (only strategy scope checked before Qwen, not global/creator; not enforced in scheduler) → FIXED | PARTIAL | PARTIAL (in-memory, persisted via sentinel -999999 but not loaded) → FIXED load_persisted_state | YES | YES per test |
| Deterministic rollback (should_rollback sample>=5, perform_rollback, rollback_safety_check behavioral-only) | YES | YES | YES via orchestrate + perform_rollback | YES (disable_rollout, disable_experiment, no deletes) | PARTIAL (same as rollout) | YES via audit | YES | YES via scheduler orchestrate |
| Emergency controls (GLOBAL/CREATOR/STRATEGY/EXPERIMENT/REENGAGEMENT/COMMERCE 6, autonomous_allowed) | YES | YES | PARTIAL (before Qwen only global/creator/strategy/experiment, not commerce/reengagement/handoff; after Qwen same) → FIXED | PARTIAL | PARTIAL (in-memory, not persisted) → FIXED via sentinel -999998 | YES | YES | PARTIAL (post-scoring only, not before Qwen) |
| Audit records (OperationalAuditRecord, record_audit 1000, query_audits creator-isolated) | YES | YES | YES per generation + orchestrate | YES | PARTIAL (in-memory) | YES | YES | YES per generation |
| Production-state derivation (derive_production_state NORMAL..RECOVERING 8) | YES | YES | YES | YES | NO | YES | YES | YES via orchestrate |
| Autonomous rollout orchestration (orchestrate_production_controls health→rollback/hold/advance, idempotent, auditable) | YES | YES | YES via scheduler loop each poll interval 10s | YES via evaluate_rollout_gate + _next_canary | PARTIAL (in-memory metrics empty → always healthy) → FIXED via metric persist | YES | YES | YES periodic |

## 4. Single-pass proof (Stage A)
- compose_signal_extraction_input + extract_commerce_signals called ONCE per process_message (line 611) and reused via signals param to _try_commerce_draft and conversational bridge; no second call.
- Generation: either generate_commerce_response inside _try_commerce_draft (if USE_COMMERCE_RESPONSE) OR generate_draft/generate_draft_with_tools (1) else branch, never both; generate_draft_with_tools bounded max_tool_calls 3, tool dispatch via dispatch_tool deterministic, no extra LLM.
- Scoring: score_draft called ONCE (line 1029) with authority-aware price check, fallback 0.0, never second.
- Memory retrieval (retrieve_relevant_memories) pure deterministic text match, no LLM.
- Strategy selection (select_strategy_adaptive) pure math, no LLM.
- Pressure/risk/lifecycle pure, no LLM.
- Experiment deterministic_assignment SHA256, no LLM.
- verify_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0}) → True; additional_llm>0 → False proven via tests.

## 5. Production control enforcement gaps (Stage A)
- Emergency global/creator/strategy/experiment enforced after scoring before auto-approve (score 0.1 + flags → operator queue), NOT before Qwen generation (Qwen still consumed). Commerce and reengagement not checked. Handoff not checked before Qwen. → Phase 24 fixes by moving gate before Qwen and extending to commerce/reengagement/handoff + rollout global/creator.
- Scheduler's process_due_messages and reconcile not gated by is_global_paused; re-engagement scheduling not gated by is_reengagement_paused / is_commerce_paused → fix.
- Metrics disconnected across workers: llm_worker records, scheduler reads empty → health always normal → never rollback → fix via sentinel persistence + load_persisted_state.

## 6. Canary enforcement
- is_rollout_active_for deterministic SHA256(creator:user:rollout_id) bucket 0..100 < percentage, stable per fan. Validated 0,1,5,10,25,50,100 only. Progression _next_canary_percentage 0→1→5→10→25→50→100. However real runtime only checked strategy scope → missing global/creator → fix.
- Restart safety: _rollout_registry in-memory cleared on restart → new rollout starts at 1% not 100% (safe), but rollback state lost (rolled_back cleared) would allow re-creation at higher percentage unsafe → fix via persist via user_profiles sentinel -999999 and load on startup.

## 7. Rollback / Emergency / Redis / Creator / Commerce / Memory / Learning / Experiment / Recovery proofs
- See matrix: all DEFINED and TESTED, most CALLED but partial ENFORCED before Phase 24. Phase 24 makes them ENFORCED on autonomous path.

## 8. Known P2 items (Stage A)
- Same buyer + same amount + same paid_at-second transaction collision: non-blocking, transaction_id unique prevents double attribution; DropFans webhook may deliver same timestamp but different transaction_id distinct → safe.
- Opaque product titles (IMG_4829): relevance 0.0 → rank still returns cheapest but below 0.15 threshold → best_match_or_none returns None → no offer (safe, not crash).
- Tokenizer tiktoken for gpt-4 used for budget trimming: non-blocking, deterministic.
- DropFans buyer downloadUrl grant API missing: non-blocking, controlled by commerce pause (commerce_paused suppresses offer).

## 9. Stage B fixes required (proposed, implemented Phase 24)
- Extend autonomous_allowed to check commerce and reengagement.
- Add autonomous_commerce_allowed helper.
- Add load_persisted_state / persist_emergency_state / _persist_metric_event for restart safety and cross-worker metrics.
- Extend llm_worker pre-Qwen gate to check commerce (for present_offer/complete_purchase), reengagement (for re_engage), handoff (via get_handoff_memory), and global/creator rollouts.
- Extend scheduler loop to check is_global_paused before claim_due_messages and is_reengagement_paused/is_commerce_paused before re-engagement.
- Wire load_persisted_state on worker startup (llm_worker.run_worker and scheduler_worker.run_scheduler).
- Keep changes minimal, no new workers/queues/LLM calls, no architecture redesign.

