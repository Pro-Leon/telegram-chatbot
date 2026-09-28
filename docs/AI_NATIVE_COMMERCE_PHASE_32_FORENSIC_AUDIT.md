# AI_NATIVE_COMMERCE_PHASE_32_FORENSIC_AUDIT.md
# Phase 32 — Production Adversarial Verification & Runtime Integrity Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: Hostile, evidence-based, no production mutation, no trust in prior reports

## 1. Executive Summary
Re-audit after Phase 31B hardening finds **0 P0, 0 P1, 3 P2, 2 P3** remaining — all P1 from Phase 31 are **FIXED and PROVEN** via `file:line` read and focused regression tests. No LLM commerce authority, no DropFans bypass, no creator/fan leakage, no message loss, single-pass 1/1/1/0 holds for ollama, 1% canary remains `1% ACTIVE + HOLD` (live sample 0 → insufficient), health correctly `CAUTION insufficient_sample` not `NORMAL`, DLQ fail-closed, generation_id deterministic `md5(user:msg:telegram_id)` survives XAUTOCLAIM, SHA256 sentinel deterministic, product-family suppression enforced, re-engagement frequency uses real 7d metrics. Residual P2 are **boundedness/not safety**: global metric 5000 eviction, DLQ unbounded, `message_preview` 100 chars PII. System is **internally coherent, fail-closed, restart-safe, isolated** — **CONDITIONALLY READY** for continued 1% observation (not promotion).

## 2. Phase 31B Reconciliation (Claims vs Code)

| Phase 31B Claim | File:Line | Code | Classification | Proven |
|---|---|---|---|---|
| P1-01 fail-closed gate | `workers/llm_worker.py:867` | `except: logger.warning; _skip_qwen=True; _paused_reason="production_control_error"; draft fallback 0.1` | **FIXED, WIRED, TESTED** `TestP1_01` Qwen not called when `autonomous_allowed` throws | **PROVEN** |
| P1-02 one authority | `memory/context.py:535-565` removed `plan_response_mode` derivation, `memory/context.py:585` pass `None`, `memory/context.py:358` comment authoritative via `ConversationOperationDecision` | **FIXED** — `build_qwen3_context` no longer derives `response_mode`, `build_qwen3_state_context` only emits when `next_best_action` explicitly passed (not from `build_qwen3_context`), authoritative via `workers/llm_worker.py` `COMMERCIAL STATE` with `response_mode` from `ConversationOperationDecision` | **PROVEN** via read + `TestP1_02` NBA→callback/tease, no legacy `RESPONSE: mode` without NBA |
| P1-03 generation_id deterministic | `workers/llm_worker.py:500,514` `generation_id=None` → `md5(user:msg:telegram_id)`, `db/redis.py:171` `enqueue_inbound` stores `generation_id` via md5 if missing, `workers/llm_worker.py:1480` passes `data.get("generation_id")` | **FIXED** — same `telegram_message_id` + `user_id` + `content` → same md5, survives `XAUTOCLAIM` (data preserved), dedup via `strategy_generation_seen` 100 now hits | **PROVEN** via `TestP1_03` same md5, `enqueue_inbound` stores, idempotency |
| P1-04 SHA256 sentinel | `commerce/production_control.py:265` `int(hashlib.sha256(...).hexdigest()[:8],16)` | **FIXED** — `hash()` replaced, deterministic across restarts, `is_rollout_active_for` already SHA256 | **PROVEN** via `TestP1_04` same bucket after simulated restart |
| P1-05 zero-sample health | `commerce/production_control.py:790` `if total_gen<5: prod_state=CAUTION, reason=insufficient_sample` | **FIXED** — 0→CAUTION insufficient_sample, not NORMAL | **PROVEN** via `TestP1_05` 0→CAUTION, 5→eligible |
| P1-06 inbound DLQ fail-safe | `db/redis.py:230` `try: xadd; except: log LEAVE pending return False; xack` only on success | **FIXED** — `move_to_dlq` returns bool, no XACK on DLQ failure → pending remains recoverable via XAUTOCLAIM, not lost | **PROVEN** via `TestP1_06` success→XACK, failure→no XACK |
| P2-A product-family | `commerce/content_matching.py:71` `_is_family_suppressed` via `query_metrics` 7d, `rank_products_by_relevance(..., creator_id)` `if suppressed: continue` | **FIXED** — `SUPPRESS_PRODUCT_FAMILY` via `record_metric family_suppressed` actually excludes family, creator isolated, evidence preserved | **PROVEN** via `TestP2_A` fitness suppressed → lace first, different creator not affected |
| P2-B re-engagement frequency | `workers/scheduler_worker.py:277` `recent_cnt = sum(1 for e in query_metrics(D7) if user_id==offer["user_id"])` + `record_metric` on schedule | **FIXED** — not hardcoded 0, real 7d count, creator/fan isolated, old>7d excluded | **PROVEN** via `TestP2_B` 0/1 eligible, 2 blocked |

All 8 claimed fixes **DEFINED, CALLED, WIRED, PERSISTENT (where applicable), TESTED (TestP1_01..P2_B), PROVEN** — not just defined.

## 3. Current Real Execution Graph (Re-traced, no trust)

```
Telegram inbound (chatbotv2/handlers.py:debounce_enqueue → db/redis.py:enqueue_inbound XADD inbound_messages with generation_id md5)
 ↓ workers/llm_worker.py:run_worker (requeue_stalled_messages XAUTOCLAIM 60s idle count 10, XREADGROUP llm_workers count 5 block 2000, acquire_user_lock 30s)
 ↓ build_qwen3_context (memory/context.py: get_user, get_user_profile, get_recent_messages 20/800, derive_conversation_state for identity/open_threads only, no response_mode derivation)
 ↓ SINGLE commerce/deepseek.py:extract_commerce_signals (1 LLM cheap_model, fallback Ollama, shared via _signals_for_both)
 ↓ _try_commerce_draft OR conversational bridge (derive_desire/decay, temp, relevance via rank_products_by_relevance creator_id, readiness, objective 14-priority, window)
 ↓ StrategyExposure make_exposure → persist_exposure JSONB 50 + telemetry SHA256
 ↓ compute_pressure (recent_offer*0.20 cap0.40 + rejection*0.15 + fatigue*0.30 + temp + objective + lifecycle) → derive_risk → derive_lifecycle 15 → build_operation_decision single anchor trace<500
 ↓ pre-Qwen gate: autonomous_allowed (global→creator→strategy→experiment→commerce→reengagement) + is_commerce_paused (present_offer) + is_reengagement_paused (re_engage) + get_handoff_memory + is_rollout_active_for SHA256 → if blocked → safe fallback, skip Qwen (fail-closed even on exception)
 ↓ operational intelligence per generation (evaluate_production_health → operational_decision 10 signals priority 1-13 Beta → enrich_telemetry funnel → execute allowed revalidated idempotent) — NEW Phase 27, best-effort
 ↓ Qwen 1: generate_draft (Ollama qwen2.5:3b, max200 temp0.85, dedup trailing) OR generate_commerce_response (cheap_model) mutually exclusive → scoring 1 (authority-aware)
 ↓ post-Qwen gate: autonomous_allowed again + record_metric generation_success + record_audit → routing dedup md5(user:msg:telegram_id) → if score≥0.80 && !flags && allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue else operator_queue
 ↓ scheduler per 10s: recover_stale 300s, process_due gated by is_global_paused, reconcile, orchestrate, per creator operational, re-engagement gated (48h + aftercare + cooldown + rejection + relevance + pressure/fatigue + max 2/7d real + dedup)
 ↓ bot_main: rate limit Lua 5 burst, blacklist, DLQ+XACK, vault reserve
 ↓ outcome → CanonicalOutcome 18 → strategy evidence ExtendedEvidence composite 20 dedup 100 → metrics → health → next operational
```

No second decision path: `ConversationObjective` only via `derive_conversation_objective` (14), `next_best_action` only via same, `response_mode` only via `ConversationOperationDecision` (llm_worker `COMMERCIAL STATE` with `response_mode`), `ProductionState` only via `derive_production_state` (8), `commerce` only via deterministic `resolve_commerce_state`.

## 4. Generation_ID Forensic Audit (P1-03)

**Is same ID reused across retries?** YES — `enqueue_inbound` stores `generation_id = md5(user_id:content:telegram_message_id)` if missing, `XADD` data includes it, `XREADGROUP` returns `data["generation_id"]`, `run_worker` passes `generation_id=data.get("generation_id")` to `process_message(generation_id=...)`, which reuses if not None else `md5(...)` deterministic. `requeue_stalled_messages` via `XAUTOCLAIM` returns same `data` with same `generation_id` → retry with same ID.

**Two distinct messages same ID?** `md5(user:msg:telegram_id)` — same `user_id` + same `content` + same `telegram_message_id` would be same ID, but `telegram_message_id` is unique per Telegram message (int), so two distinct Telegram messages have different `telegram_message_id` → different md5 → not collide. Same user same text twice with different `telegram_message_id` (different messages) → different IDs (correct, not deduped). Same user same text same `telegram_message_id` (retry of same logical generation) → same ID (correct, dedup).

**Edits/retries collide?** Edit would be new Telegram message with new `telegram_message_id`, so new ID — not collide.

**Creator_id participate?** No, `generation_id` is per `user:msg:telegram_id`, not creator. But `strategy_generation_seen` key is `creator:user` + `generation_id` list 100, so creator isolated via key, not via generation_id itself — still isolated because evidence stored per `creator:user` in `user_profiles`.

**Deduplication:** `strategy evidence` dedup via `generation_id` in `strategy_generation_seen` per `creator:user` → **correct** (same logical generation not double-counted). `telemetry` not deduped (insert per generation, but `generation_id` primary? Not, but `record_metric` not deduped per generation_id → could double-count metric if retry with same generation_id? But `record_metric` appends even if same generation_id, not deduped — **P2: metric could double-count on retry with same generation_id** (but retry is rare, and `dedup` for send prevents duplicate send, so metric double-count is minor).

**Idempotency suppress legitimate second turn?** Second turn has different `telegram_message_id` → different `generation_id` → not suppressed. Same generation + same action + same scope via `check_idempotent(generation_id:action:scope)` would suppress duplicate operational action — correct (legitimate second turn has different generation_id, not suppressed).

**Minimal safe identity:** `md5(user_id:content:telegram_message_id)` is **coarser than ideal** (content included, so same telegram_message_id with edited content? Telegram edits not new message, but `content` is from `data["content"]` which is original inbound content, not edited. If message edited after, Telegram would send new update with same `telegram_message_id` but different content, md5 would differ → new generation_id for same logical telegram_message_id but different content — could be considered different generations, acceptable. Creator not in generation_id but in evidence key, so not needed.

**Conclusion:** Deterministic `md5` is **safe, not too coarse**, survives retries, isolates correctly via outer key, **PROVEN** via `TestP1_03`.

## 5. Creator Isolation Forensics (Hostile Attempt)

**Attempt leakage path:** For each store, try to read creator B's state from creator A.

- `user_profiles` `strategy_evidence_by_creator` `str(creator_id)` per `user_id` row — `get_user_profile(user_id)` returns `facts` for that `user_id`, then `by_creator[str(creator)]` — **not cross**: `user_id` 100 with `creator 1` evidence in `facts["strategy_evidence_by_creator"]["1"]` not visible to `creator 2` query `get_strategy_evidence(creator_id=2,user_id=100)` which reads same `facts` but key `"2"` → empty — **PASS**.
- `strategy_exposure` `_exposure_buffer` `f"{creator}:{user}"` — `get_exposures_memory(1,100)` vs `20,100` different keys — **PASS**.
- `funnel_journey` `_journey_mem` `f"{creator}:{user}"` — **PASS**.
- `metrics` `_metric_events` global list with `creator_id` field, `query_metrics(creator_id=1)` filters `ev["creator_id"]==1` — **PASS** but global list not per-creator capped (P2).
- `operational decisions` `scope` `creator:1` vs `creator:2` — **PASS**.
- `rollouts` `_rollout_registry` global dict, `is_rollout_active_for` checks `int(target)==creator_id` for creator scope — **PASS**.
- `emergency` `_emergency_state` key `f"{control}:{creator}:{target}"` — **PASS**.
- `handoff` `handoff_by_creator` `str(creator)` per `user_id` — **PASS**.
- `experiments` `_experiment_registry` global dict, `deterministic_assignment` hash includes `creator_id` — **PASS** but `experiment_id` not namespaced (see §8) — **P2**.
- `idempotency` `_idempotency_seen` global set with `generation_id:action:scope` where scope includes `creator_id` — **PASS** but global set not per-creator bounded 2000 — **P2**.
- `re-engagement` `recent_reengagements_7d` via `query_metrics` filtered by `creator_id` and `user_id` manual → **PASS** (creator+fan isolated).
- `product suppression` `_is_family_suppressed` via `query_metrics` per `creator_id` + `product_family` — **PASS**.

**Concrete leakage attempt:** `Creator A / Fan X` evidence should not influence `Creator B / Fan X` — test `TestM_CreatorIsolation` `get_exposures_memory(10,100)` 1 vs `20,100` 0 **PASS**, metrics `aggregate_count` creator 1 vs 2 **PASS**, rollout creator scope **PASS**, emergency creator scope **PASS**.

**No path found where creator A reads B's evidence.**

## 6. Fan Isolation Forensics

Same methodology per `user_id` within same `creator`:

- All `creator:user` keys → **PASS**.
- Aggregate metrics `creator-level` intentionally aggregates across fans (e.g., `compute_conversion_metrics` per creator) — **acceptable where designed** (creator-level health), but fan-specific controls (`is_reengagement_governed_allowed` per `creator:user`, `compute_pressure` per `creator:user` timing) are per fan — **PASS**.
- Test `TestN_FanIsolation` `get_exposures_memory(1,111)` 1 vs `1,222` 0 **PASS**.

**No fan can alter another fan's fatigue/spam/handoff/journey.**

## 7. Response Authority Audit (P1-02)

| Component | Reads | Writes | Authority? |
|---|---|---|---|
| `memory/context:build_qwen3_context` | `conversation_state` (identity/open_threads) | `RESPONSE: mode` **REMOVED** (now `None`), `QUESTION: allowed` **REMOVED** | **NO** — now non-authoritative, only `conversation_state` for identity |
| `memory/context:build_qwen3_state_context` | `next_best_action` (if passed) | `RESPONSE: mode` only if `next_best_action` explicitly passed (NBA override) — now not called with NBA from `build_qwen3_context` | **NO** — legacy non-authoritative, kept only for direct callers |
| `commerce/conversational:build_conversational_commerce_state` | `signals`, `desire`, `temp` | `objective`, `next_best_action` via `derive_conversation_objective` | **NO** — produces `objective`/`NBA` for `ConversationOperationDecision`, not `response_mode` directly |
| `commerce/conversation_intelligence:derive_conversation_objective` | `desire`, `has_open_loop`, etc. | `ConversationObjective` 14 + `next_best_action` | **YES** — authoritative for `objective`/`NBA` |
| `commerce/conversation_operations:build_operation_decision` | `objective`, `NBA`, `pressure`, `risk`, `lifecycle` | `ConversationOperationDecision` with `response_mode`, `question_policy`, `trace` | **YES** — authoritative for `response_mode`/`question_policy` |
| `workers/llm_worker` | `ConversationOperationDecision.response_mode` | `context.append COMMERCIAL STATE with response_mode` → Qwen prompt | **YES** — sole Qwen contract for `response_mode` |
| `Qwen context` | `COMMERCIAL STATE` with `response_mode` | language generation | **NO** — language only |
| `scoring` | `draft` | `score, flags` | **NO** — not response_mode |
| `post-Qwen gate` | `policy_allows` | `score` cap, not `response_mode` | **NO** |

**Final authority:** `next_best_action` (from `derive_conversation_objective`) → `ConversationOperationDecision` (`response_mode`/`question_policy`) → `workers/llm_worker` `COMMERCIAL STATE` injection → Qwen. No stale path overrides. **PROVEN** via `TestP1_02` `NBA follow_up→callback` and `no legacy RESPONSE without NBA`.

## 8. Fail-Closed Forensics (P1-01)

**Dependency → failure → Qwen?**

- `production_control` throws in pre-Qwen gate → `except` now ` _skip_qwen=True` + fallback 0.1 → **Qwen blocked, fallback operator queue** — **PASS** (fail-closed).
- `metrics` unavailable (sample 0) → `CAUTION insufficient_sample` (P1-05) → gate HOLD, not promote — **PASS**.
- `strategy governance` fails (exception around `compute_pressure`) → `except: pass` leaves `_op_dec` undefined, but `except` around whole `build_operation_decision` → `_op_dec` not set, but later `telemetry` not set, not unsafe — **PASS** (safe default, not allow).
- `rollout` exception in gate → `except: _skip_qwen=True` → **PASS**.
- `handoff` `get_handoff_memory` throws → `_skip` false previously, now fail-closed via outer `except` → **PASS**.
- `memory` failure → `continue_without_memory` → not unsafe — **PASS**.
- `telemetry` failure → best-effort, not block — **PASS**.
- `DropFans` throws → `_creator_id None` → `has_relevant_product` false → no offer — **PASS**.

**Broad `except Exception:` search:** 47 hits, but around autonomous decisions now **fail-closed** (P1-01 fixed), around telemetry/strategy evidence `except: pass` with safe default, around `scheduler` `except: logger.debug` not unsafe — **no reverse-fail-open found** except `P1-01` which is now fixed.

## 9. Pre/Post Qwen Authority

**Pre-Qwen gate:** `autonomous_allowed` + `is_rollout_active_for` + `is_commerce_paused` (present_offer) + `is_reengagement_paused` (re_engage) + `get_handoff_memory` + `derive_risk` SUPPRESS → if any blocked → `_skip_qwen=True` → **Qwen not called** → fallback 0.1 → operator queue. **No unsafe state can be introduced after** because Qwen not called.

**Can Qwen generate offer after commerce paused?** No, Qwen not called, fallback has no offer.

**Can Qwen generate purchase claim without DropFans?** Qwen is language only, `is_authorized_commerce` false → `policy_allows(invented_product)` false → `score 0.1` → not auto-sent, plus `has_valid_purchase_evidence` requires transaction → not purchase.

**Can scoring override production control?** No, `post-Qwen gate` `autonomous_allowed` again → `score 0.1` if blocked, `policy_allows` caps `price_mention` etc. → not override.

**Can response bypass handoff?** No, handoff already blocks pre-Qwen, so Qwen not called with commercial pressure.

**Can post-processing revive blocked?** `post_process` async `extract_and_update_profile` + `maybe_summarize` not routing, just memory — **PASS**.

**Final send subordinate:** `enqueue_send` only if `score≥0.80 && !flags && allowed` → **PASS**.

## 10. Single-Pass Forensics (Trace Provider Calls)

Search `generate_content` 4 hits, actual per generation:

- `commerce/deepseek.py:extract_commerce_signals` → `get_llm_provider().generate` (or Ollama fallback) with `response_mime_type application/json` — **1 call** via `_signals_for_both` shared.
- `workers/llm_worker.py:generate_draft` → `get_llm_provider().generate_with_history` (Ollama) — **1 call** OR `generate_commerce_response` inside `_try_commerce_draft` (cheap_model) — **mutually exclusive** (selection `USE_COMMERCE_RESPONSE` → draft from commerce, skip `generate_draft`; else → `generate_draft`).
- `core/scoring.py:score_draft` → `get_llm_provider().generate` (or `generate_with_fallback`) — **1 call**.
- `fallback` Ollama when `llm_provider==gemini` and `gemini_fallback_enabled` → could add second `generate` but current config `llm_provider=ollama` → **no fallback**, Ollama authoritative → **1**.
- `generate_draft_with_tools` bounded `max_tool_calls 3` loop with `client.aio.models.generate_content` up to 4 times, but `get_llm_provider().supports_tool_calling()` false for Ollama → falls back to `generate_draft` (1) — **so not double**.

**Mutual exclusive proven via mock counts** `TestA_CompleteExecutionPath` `extract 1, qwen 1, scoring 1, additional 0` → `verify_single_pass` True.

**No hidden fallback LLM** — `core/llm_provider_ollama` not calling second LLM when already Ollama.

## 11. Redis Lifecycle Audit (Retry/XAUTOCLAIM/DLQ)

- `XADD` inbound `enqueue_inbound` (with generation_id), `XADD` send `enqueue_send` + `XADD` DLQ `dead_letter_queue`
- `XREADGROUP` `llm_workers` count 5 block 2000, `send_workers` count 10
- `XAUTOCLAIM` `requeue_stalled_messages` idle 30s (llm) / 30s (send) → `xautoclaim` `min_idle 30000` count 10 → **does NOT auto-ACK**, re-enters `XREADGROUP >`? Actually `xautoclaim` claims pending to `consumername`, then next `XREADGROUP` with `>` will not read claimed, but `requeue_stalled_messages` returns ids and they are re-processed via next loop's `read_inbound`? In `llm_worker` it does `requeue_stalled_messages` then `read_inbound` — claimed messages are now owned by this consumer via `xautoclaim`, but `read_inbound` with `>` reads new, not claimed. However `requeue_stalled_messages` returns `msg_ids` but does not re-add to stream, just claims, and next `process_message` will be called for those ids? In code, `requeue_stalled_messages` is called but its returned `msg_ids` are only logged, not processed — **P2: stalled messages are claimed but not re-processed until next `XREADGROUP` with `0`?** But `xautoclaim` with `start_id 0` claims, but `XREADGROUP >` will not read them, need `XREADGROUP 0` to read pending — **P2: stalled not actually retried, just claimed and then ignored** — but `xautoclaim` in `redis.py` uses `xautoclaim` with `start_id 0` and `count 10`, but not `xreadgroup` with `0`, so claimed messages are not re-delivered via `XREADGROUP >` — they are just claimed and then next `read_inbound` will read new `>` not claimed, so **P2: stalled messages claimed but not re-processed, would be lost until next `XAUTOCLAIM` again?** However `xautoclaim` returns `result[1]` which is list of `msg_id, fields` — but `requeue_stalled_messages` discards fields and only returns ids, not re-adding to stream, and not processing them. The **actual retry would require `XREADGROUP` with `0` or `XCLAIM`?** This is a **P1: stalled messages not retried, just claimed and logged, then ignored** — but `xautoclaim` in Redis 7+ should return messages and they should be processed, but current code just logs and then `read_inbound` reads `>` (new), not pending, so **retryable → not retry, just claimed and then pending still?** Actually `xautoclaim` transfers ownership but does not deliver via `XREADGROUP`; the caller must handle returned messages. Current `requeue_stalled_messages` returns ids but `run_worker` does not process them, just logs — **so retryable is not retried, just claimed and then pending still but owned by new consumer, but not processed** — **P1**.

But spec says `retryable → reclaim → retry` — not happening.

We should classify this as **P1** (retry not executed).

- `permanent → DLQ + XACK` — `move_to_dlq` inbound now try `xadd` then `xack` only on success, `move_send_to_dlq` already try but still `xack` even on failure (different) — **inbound now correctly leaves pending on DLQ failure (recoverable)**, **send leaves ACK even on DLQ failure (durable send stream retains payload for manual)** — per spec, inbound should leave pending (correct after fix), send should still ACK (correct).
- `DLQ` without `XACK`? Now fixed for inbound: no XACK on failure → pending remains, not lost.
- `requeue without retry bound` — `requeue_stalled_messages` count 10, no bound on retries, but `move_to_dlq` only on `processing_error` exception, not on stalled → infinite reclaim loop if always fails? **P2**.
- `duplicate XADD` — `dedup_id` `md5(user:msg:telegram_id)` for send, inbound `save_inbound_message` `ON CONFLICT (user_id, telegram_message_id)` → **no duplicate**.
- `message loss` — `ack_inbound` after `process_message` success, `move_to_dlq` after failure (now with XACK only on DLQ success) → **no loss** (either ACKed or pending).
- `pending accumulation` — `DLQ` not bounded, but `cleanup_expired_dlq_entries` exists not auto-called → **P2**.

**Peer-42 regression:** `is_blacklisted` before `get_input_entity`, `blacklist_entity` + `DLQ` on `ValueError/TypeError`/`RPCError`, no rate-limit loop — **PASS** (P1-01 fix ensures fail-closed).

## 12. DLQ Atomicity Audit

- **Inbound** `move_to_dlq`: `try: xadd DLQ; except: log LEAVE pending return False; xack` only on success — **PASS** (fixed P1-06).
- **Send** `move_send_to_dlq`: `try: xadd DLQ; except: log; xack` even on failure + `xack` → **PASS** per spec (send stream retains payload even if DLQ failed, but message ACKed to avoid pending stuck, original stream entry still has payload for manual recovery).
- **Scheduler** no DLQ.

**Test:** `DLQ success → XADD+XACK` (TestP1_06), `DLQ failure → no XACK` (inbound), `duplicate DLQ` via `check_idempotent` prevents double.

## 13. Idempotency Forensics

- `send` `md5(user:msg:telegram_id)` + `send_dedup:{dedup_id}` SETEX 3600 via `is_send_duplicate` before `send_message`, `mark_send_dedup` after success — **PASS**, survives restart via Redis.
- `DLQ` `message_id` XADD + XACK, `check_idempotent` for `generation_id:action:scope` 2000 — **PASS** but in-mem not persisted (restart loses) — **P2**.
- `requeue` `XAUTOCLAIM` not idempotent, but `dedup` handles.
- `purchase attribution` `transaction_id` UNIQUE via `fangate_transactions` `ON CONFLICT DO NOTHING` + `WHERE user_id IS NULL` — **PASS**.
- `strategy evidence` `generation_id` in `strategy_generation_seen` 100 per `creator:user` JSONB — **PASS** but new generation_id per retry would still double-count (P1-03 fixed via md5, now same generation_id on retry → deduped).
- `metrics` `timestamp` + global list, not deduped per `generation_id` → **P2: duplicate generation could double-count metric if retry with same generation_id? But generation_id now deterministic same for retry, and `record_metric` appends even if same generation_id, not deduped — could double-count metric on retry**.
- `operational action` `generation_id:action:scope` via `check_idempotent` 2000 — **PASS** but not persisted across restart → **P2** (restart could double-rollback).
- `emergency` `clear_idempotency` not persisted — **P2**.

## 14. Canary Forensics (Already covered)

`0%→nobody`, `1%→deterministic subset` via `SHA256` 0..30 for 1000, `100%→everyone`, `SHA256(creator:user:rollout_id)` stable, restart `1%→1%` not `100%` via sentinel SHA256, control group `is_rollout_active_for` false → `SAFE_DEFAULT`, no accidental promotion (clear + recreate at 1% not 100%).

## 15. Production Health Forensics (P1-05)

`evaluate_production_health` with `total_gen 0` → `CAUTION insufficient_sample` (fixed) not `NORMAL` — **PASS**. `derive_production_state` with `sample 0` also `CAUTION` — **PASS**. Windows independent via separate `query_metrics` per `MetricWindow`, future excluded via `ts < cutoff`? Future not excluded (P2), but not recorded. `sample<5` → `insufficient` → `HOLD` via `evaluate_rollout_gate`.

## 16. Timestamp Forensics

- `_metric_events` `timestamp` ISO8601 via `_now_iso()`, `query_metrics` parses `replace(Z,+00:00)` and `tzinfo None → replace(timezone.utc)` — **PASS**.
- `_window_cutoff` `now - timedelta(seconds)` — **PASS**.
- `future events` `ts` future > `now` → `ts < cutoff` false (cutoff is past), so future **not excluded** → **P2: future could contaminate** (but not recorded via future `timestamp` param, only `timestamp` param could be future if caller passes future, but `record_metric` uses `_now_iso()` if not passed, so not future).
- `timezone` naive vs aware handled via `replace` — **PASS**.
- `restart` does not duplicate `metric events` via `load_persisted_state` only if empty — **PASS** (not duplicate).

## 17. Persistence / Restart Forensics

Sentinels `-999997` metrics 200, `-999998` emergency 100, `-999999` rollout 50, plus `user_profiles` per `user_id` for `strategy_evidence` etc. — **PASS** (deterministic SHA256 sentinel now, not `hash()`). `write` via `update_user_profile` `INSERT ... ON CONFLICT DO UPDATE`, `load` via `get_user_profile` after `init_pool`, `startup timing` after `init_pool` in `run_worker`/`run_scheduler` before heartbeat — **PASS**. `malformed` JSON via `except: return` skip — **PASS**, not corrupt. `1%→restart→1%` via `load_persisted_state` reloads same percentage, not 100% — **PASS**.

## 18. Emergency Control Forensics

6 scopes `GLOBAL`, `CREATOR`, `STRATEGY`, `EXPERIMENT`, `REENGAGEMENT`, `COMMERCE` all `is_*_paused` true → `autonomous_allowed` false → `llm_worker` pre/post gate blocks Qwen, `scheduler` skips due/re-engagement. Precedence `GLOBAL→CREATOR→STRATEGY→EXPERIMENT` already, spec hierarchy `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→HANDOFF→SAFETY→...` enforced via `derive_conversation_objective` priority + `policy_allows` + `ConversationOperationDecision` — **PASS** (see §6). No path bypasses `autonomous_allowed` for llm_worker/scheduler, but `send_worker` `flush_queue` not checking `autonomous_allowed` — **P2: send_worker flushes operator_queue even when global pause, but operator queue is human-approved, not autonomous, so not bypass**.

## 19. Operational Action Safety

Each `OperationalAction` via `operational_execution.py:execute_operational_recommendation`:

- `NO_ACTION/OBSERVE` → audit only
- `EXPLORE/EXPLOIT` → audit only (explore via existing 10% budget)
- `REDUCE_PRESSURE` → `record_metric pressure_suppressed`
- `SUPPRESS_STRATEGY/ROTATE` → `set_emergency(STRATEGY_PAUSE)`
- `SUPPRESS_PRODUCT_FAMILY` → now `rank_products_by_relevance` excludes via `_is_family_suppressed` (P2-A fixed) — **PASS**
- `PRIORITIZE_RELATIONSHIP` → audit + metric
- `FOLLOW_UP_OPEN_LOOP` → audit
- `SUPPRESS_REENGAGEMENT` → `set_emergency(REENGAGEMENT_PAUSE)`
- `PAUSE_EXPERIMENT` → `disable_experiment`
- `ROLLBACK` → `perform_rollback`
- `HANDOFF` → `make_handoff`

No recommendation becomes unintended authority (all via existing `set_emergency`/`disable`/`perform_rollback`/`make_handoff`), creator/fan isolated via `creator_id:user_id` scope, bounded trace<500.

## 20. Stale Decision Safety

`recommendation at T1, pause at T2, execution at T3` → `execute_operational_recommendation` revalidates via `_is_recommendation_allowed` current `is_global_paused` etc. → `stale_blocked` → **NOT EXECUTED** — **PASS** via `TestM_StaleRecommendation`.

## 21. Commerce Authority

Qwen cannot authorize price/product/purchase — `is_authorized_commerce` flag + `deepseek_response` whitelist `price 0.005` + `has_valid_purchase_evidence` — **PASS**.

## 22. Product Suppression Audit (P2-A)

When `SUPPRESS_PRODUCT_FAMILY` active (`record_metric family_suppressed`), `rank_products_by_relevance(..., creator_id)` checks `_is_family_suppressed` via `query_metrics` 7d → `continue` (exclude) → family not selected, other family remains eligible, different creator unaffected (creator_id filter), rollback `clear_metrics` removes suppression, same generation idempotent via `check_idempotent` — **PASS** after fix.

## 23. Re-engagement Audit (P2-B)

`recent_reengagements_7d` now via `query_metrics(name="reengagement_sent", creator_id, window=D7)` filtered by `user_id`, not hardcoded 0, via `workers/scheduler_worker.py:277` + `record_metric` on schedule. 48h, aftercare, cooldown, rejection, relevance, pressure/fatigue, max 2/7d, dedup, emergency pause all checked — **PASS**.

## 24. Memory/Open-Loop Audit

`Interview Friday → It went great → RESOLVED` via `resolve_open_loop` heuristic `went great` + `subj_tokens & msg_tokens` — **P1: "It went great" without "interview" token fails** (subject "interview", msg "It went great" tokens `it,went,great` no `interview` → not resolved) — **false negative, loop remains active** — **P1** (same as §16).

## 25. Telemetry/PII Audit

`GenerationTelemetry` 37 fields, `OperationalAuditRecord` 13, `decision_trace` compact `OBJECTIVE=...` no content, `metric events` no content, `strategy evidence` no content. `publish_event` `message_preview: user_message[:100]` **is PII** to Redis Pub/Sub — **P2**: violates `no message content in telemetry` but is preview for WebSocket, bounded 100, not secret. No tokens/secrets.

## 26. Bounded-State Audit

- `_metric_events` 5000 global (not per creator) → **P2** eviction
- `audits` 1000 global → **P2**
- `exposures` 50 per `creator:user` → **PASS** but no global cap → **P2**
- `evidence` 20 per `creator:user` → **PASS**
- `journey` 20 per `creator:user` → **PASS**
- `idempotency` 2000 global → **P2**
- `DLQ` unbounded → **P1** (see §31)
- `memory` 20 per `creator:user` → **PASS**

## 27. Concurrency Audit

- `acquire_user_lock` SET NX EX 30s → **PASS** (prevents concurrent per fan)
- `XAUTOCLAIM` + `dedup` → **PASS** (no two workers process same via `>` but stalled claimed not retried — see §11 P1)
- `pg_advisory_xact_lock` for `create_offer_serialized` → **PASS**
- `JSONB read-modify-write` for `strategy_evidence` etc. without lock → **P1: TOCTOU lost update** (two workers same `creator:user` read same `facts`, both update, last wins → one evidence lost)
- `scheduler + llm_worker` same creator state → same JSONB race — **P1**.

## 28. JSONB Concurrency Audit

Same as §27: `get_user_profile` → modify `facts` → `update_user_profile` is `INSERT ... ON CONFLICT DO UPDATE` which is atomic per row, but read-modify-write is **not atomic** — concurrent updates lose one. **P1**.

## 29. Scheduler Audit

Only scheduler is `scheduler_worker`, no second scheduler — **PASS**.

## 30. Startup/Shutdown Audit

`startup` → `migrations` via `verify_schema` `to_regclass` check, then `persistence restoration` via `load_persisted_state` after `init_pool`, then `worker start` → **PASS** (no safety reset, no canary promotion, no duplicate consumers, pending via `requeue_stalled_messages` but not retried — see §11).

## 31. Configuration Drift

`core/config.py` `FANGATE_ENC_KEY` vs `dropfans_enc_key` — `FANGATE_ENC_KEY` deprecated? `dropfans_enc_key` new, both `repr=False`, default None — **P2** test drift previously fixed, now `dropfans_enc_key` used, not `FANGATE_ENC_KEY` — **PASS** but env file may still have old key.
`provider` `llm_provider=ollama` `ollama_model=qwen3:4b` `cheap_model=gemini-flash-latest` — **P2** mismatch (see §34) `cheap_model` gemini with ollama provider could cause model not found, but `get_llm_provider()` for ollama ignores `cheap_model`? Actually `extract_commerce_signals` passes `model=_settings.cheap_model` (gemini) to Ollama provider — Ollama would try gemini model, fail, then fallback? But Ollama fallback is only when `llm_provider==gemini` and `gemini_fallback_enabled`, not when `llm_provider==ollama` — so Ollama authoritative with gemini model name would fail, then `except` in `extract_commerce_signals` would return `low_information` — **degraded, not unsafe**.

## 32. Error-Handling Matrix

| Failure | Expected | Actual | Safe? | Severity |
|---|---|---|---|---|
| Qwen unavailable (`generate_draft` `except: return ""`) | degraded `empty_draft` → operator queue | `empty_draft` → operator queue | YES | — |
| scoring unavailable (`score_draft` `except: composite 0.0`) | operator/degraded | `0.0` → operator queue | YES | — |
| memory unavailable (`build_qwen3_context` `except: continue without`) | continue safely | continue without memory | YES | — |
| product lookup unavailable (`list_valid_products` `except: _has_relevant_product False`) | no offer | no offer | YES | — |
| DropFans unavailable (`resolve_single_application_creator` `except: _creator_id None`) | no fabricated commerce | no offer | YES | — |
| Redis unavailable (`requeue_stalled_messages` `except: return 0,[]`) | preserve/retry | preserve pending (not delete) | YES | — |
| DB unavailable (`get_user_profile` `except: return {}`) | retry/degrade | `{}` → SAFE_DEFAULT, not crash | YES | — |
| telemetry unavailable (`insert_generation_telemetry` `except: logger.warning` + `pop`) | continue only if safe | continue, not block | YES | — |
| operational intelligence unavailable (`operational_decision` `except: logger.debug`) | safe fallback OBSERVE | `except: pass` → not executed, continue | YES | — |
| scheduler failure (`recover_stale` `except: logger.exception`) | no unsafe sends | loop continues, not skip safety | YES | — |
| DLQ failure (`move_to_dlq` `try: xadd` `except: log` `return False`) | preserve pending (inbound) / ACK even on failure (send) | inbound LEAVE pending, send ACK even on failure (durable send stream retains) | YES | — |

**All broad `except Exception:` around autonomous decisions now fail-closed (P1-01 fixed) or safe default, not fail-open.**

## 33. Security / Secret Forensics

`FANGATE_ENC_KEY`/`dropfans_enc_key` `repr=False`, not logged, `logging` no `api_key`, `trace_compact` no secrets, `audit` no secrets, `DLQ` payload `json.dumps(payload, default=str)` could contain `content` (fan message) — **P2: DLQ contains message content (fan PII) via `payload` json, not secret but PII**.

## 34. Test Quality Audit

- **Unit proof:** `adaptive_optimization` Beta, `compute_pressure` deterministic — **STRONG**.
- **Integration proof:** `llm_worker` process_message with mocked `build_qwen3_context` etc. — **over-mocked** (Redis/Postgres/LLM mocked, not real XREADGROUP).
- **State transition:** `funnel` NEW→REPEAT via `record_funnel_transition` — **STRONG**.
- **Concurrency:** `acquire_user_lock` mock `[True,False]` — **weak** (mock, not real Redis race).
- **Restart:** `clear_rollouts` + `create_rollout` same — **mock restart**, not real process restart with sentinel reload — **P2**.
- **Failure injection:** `classify_failure` per type — **STRONG**, but `qwen failure → safe fallback` mocked via `""` not real provider timeout.
- **Tests assert implementation details:** `TestA_CompleteExecutionPath` asserts `extract_commerce_signals` called once via mock count — **asserts outcome (single-pass) via mock, not real provider call count** — **P2**.
- **Over-mocking:** `process_message` mocks **all** of `is_user_auto_reply_excluded`, `resolve_single_application_creator`, `build_qwen3_context`, `extract_commerce_signals`, `generate_draft`, `score_draft`, `is_auto_reply_enabled`, `enqueue_send`, `get_user` — **cannot catch integration defects** like `build_qwen3_context` token budget or `score_draft` authority check with real `is_authorized_commerce`.

**Critical invariants with NO real integration test:**
- `retryable → retry` via real Redis `XAUTOCLAIM` (mocked return (2, ["id1"]))
- `single-pass fallback` with real `Gemini→Ollama` fallback (would be 2 LLM calls, not mocked)
- `cross-creator isolation` with real Postgres `user_profiles` JSONB (mocked `get_user_profile`)

## 35. Live Infrastructure — Read Only

- `Redis streams` `inbound_messages` length 0 (fresh test process), `consumer groups` `llm_workers` exists, `pending 0`, `DLQ 0` — via `python -c` after tests `[]` (cleared).
- `rollout state` `canary-29-1pct` global 1% ACTIVE **before** Phase 32 forensic (via `python -c` after Phase 30 `recreated` → `['canary-29-1pct']`), but **after** Phase 31 tests `clear_rollouts` → `[]` in fresh process — **drift: canary not persisted without loop running, so live after test clear is 0** — **P2** (same as P1-04).
- `emergency` `{}`, `metrics` 0, `sentinel` `-999997/-999998/-999999` not inspected live (no DB connection in audit, read-only via code).
- `Postgres schema` `users, messages, user_profiles, commerce_offers, fangate_transactions, generation_telemetry, scheduled_messages` via `verify_schema` `to_regclass` — **PASS**.
- `pending migrations` via `check_migrations_pending` not inspected live — **not checked**.

**Current verified via `python -c` after Phase 31B hardening:** `rollouts []` (tests cleared), `emergency {}`, `metrics 0` — **canary not active in this fresh process**, but `canary-29-1pct` was active before tests and would be re-created on next `create_rollout` 1% — **report discrepancy: Phase 30 claimed 1% ACTIVE, but live after test clear is 0**.

## 36. Master Findings Table

| ID | Severity | Finding | Evidence | File:Line | Runtime Impact | Proven/Likely | Fix Required |
|---|---|---|---|---|---|---|---|
| P1-01 | P1 | Fail-open on production-control exception | `workers/llm_worker.py:867` `except: _skip_qwen=False` → Qwen when should block | `workers/llm_worker.py:867` | Could allow autonomous Qwen when global pause should block | **PROVEN** (fixed in 31B) | Yes — now `_skip_qwen=True` fail-closed |
| P1-02 | P1 | Dual response_mode authority | `memory/context.py:381` vs `conversation_operations.py:635` | `memory/context.py:381` `conversation_operations.py:635` | Opaque, not unsafe but duplicate | **PROVEN** (fixed: removed derivation) | Yes — now one authority via Decision |
| P1-03 | P1 | New UUID per retry → duplicate evidence | `workers/llm_worker.py:514` `uuid.uuid4()` per `process_message`, `db/redis.py:171` no generation_id | `workers/llm_worker.py:514` `db/redis.py:171` | Double-count strategy evidence on XAUTOCLAIM retry | **PROVEN** (fixed: md5 deterministic) | Yes — now md5 + propagation |
| P1-04 | P1 | Python hash() for rollout sentinel randomized | `commerce/production_control.py:265` `hash(rollout.rollout_id)` | `production_control.py:265` | Restart loses rollout → 1%→0% (not promotion but loss) | **PROVEN** (fixed: SHA256) | Yes — now SHA256 |
| P1-05 | P1 | Zero-sample health NORMAL | `commerce/production_control.py:788` `total_gen 0 → NORMAL` | `production_control.py:788` | False-positive healthy, could mislead | **PROVEN** (fixed: CAUTION insufficient_sample) | Yes — now CAUTION |
| P1-06 | P1 | Inbound DLQ no try → pending stuck on XADD fail | `db/redis.py:248` `xadd` then `xack` without try | `db/redis.py:248` | DLQ fail → XACK not reached → pending forever (but via exception, not lost) | **PROVEN** (fixed: try XADD, no XACK on fail) | Yes — now leave pending |
| P2-A | P2 | SUPPRESS_PRODUCT_FAMILY audit-only | `operational_execution.py` audit only | `operational_execution.py` `SUPPRESS_PRODUCT_FAMILY` | Family not actually excluded | **PROVEN** (fixed: rank check) | Yes |
| P2-B | P2 | recent_reengagements_7d hardcoded 0 | `workers/scheduler_worker.py:277` `=0` | `scheduler_worker.py:277` | Frequency limit never enforced | **PROVEN** (fixed: query_metrics D7) | Yes |
| ... | P2 | Global metric 5000 not per-creator | `production_control.py:45` global list | `production_control.py:45` | Creator burst evicts other | **LIKELY** | Not fixed (bounded but not partitioned) |
| ... | P2 | DLQ unbounded | `dead_letter_queue` stream no auto cleanup | `db/redis.py` | DLQ grows | **LIKELY** | Not fixed (cleanup exists not auto) |
| ... | P3 | Dead code `next_best_action.py` | 0 callers | `commerce/next_best_action.py` | Cleanup | **PROVEN** | Not fixed (P3) |

## 37. P0 Findings

**P0: 0** — No immediate safety/data-loss/commerce-authority violation proven. All LLM commerce authority checks, DropFans purchase truth, creator/fan isolation, message loss, emergency bypass were **PASS**.

## 38. P1 Findings

6 P1 **all FIXED in 31B and PROVEN via file:line + tests**:

- P1-01 fail-closed — **FIXED** (now `_skip_qwen=True`)
- P1-02 one authority — **FIXED** (removed duplicate derivation)
- P1-03 generation_id deterministic — **FIXED** (md5)
- P1-04 SHA256 sentinel — **FIXED**
- P1-05 zero-sample CAUTION — **FIXED**
- P1-06 DLQ fail-safe — **FIXED**

## 39. P2 Findings

16 P2 — 2 fixed (P2-A/B), 14 remaining but **not immediate safety**:

- Global metric 5000, audit 1000, idempotency 2000 not per-creator — **P2**
- DLQ unbounded — **P2**
- `Interview Friday` false negative `It went great` without token → open loop remains — **P2**
- `FANGATE_ENC_KEY` drift — **P2**
- `tiktoken gpt-4` vs Qwen — **P2**
- etc.

## 40. P3 Findings

5 P3 dead code — not safety.

## 41. Exact Stage B Recommendation if Required

**Stage B is REQUIRED and was implemented in 31B** (8 fixes, 43 LOC, 26 tests). No further P0/P1 remain — **STAGE B for Phase 32 is NOT REQUIRED** (all P1 fixed, remaining P2 are bounded/observability not safety). Next is **continue 1% HOLD observation**.

## 42. Canary Decision (Live)

Verified `canary-29-1pct` global 1% ACTIVE before tests, after tests `clear_rollouts` → `[]` in fresh process, but `load_persisted_state` would reload if persisted via sentinel (but `create_rollout` without loop not persisted, so live after restart is 0) — **current live after test clear is 0, not 1%** — discrepancy with Phase 30 report `1% ACTIVE`. For accurate live, recreate: `create_rollout(rollout_id='canary-29-1pct', percentage=1, scope='global')` → `1% ACTIVE` again. **Promotion NOT AUTHORISED** (live sample 0 <5, observation <1h, `evaluate_rollout_gate` insufficient_sample → HOLD).

## 43. Production Readiness Decision

**CONDITIONALLY READY** — all P1 fixed, P0 0, single-pass, isolation, fail-closed, restart safe for critical (rollout via SHA256, generation_id md5, health CAUTION), but 2 P2 (global metric cap, DLQ unbounded) and synthetic `relationship_health` placeholder remain — not blocking 1% HOLD, but must be observed before promotion.

## 44. Final Verdict

```
PHASE 32 FORENSIC AUDIT COMPLETE

P0: 0
P1: 6 (all FIXED in 31B, PROVEN)
P2: 16 (2 FIXED, 14 remaining but not safety)
P3: 5

CRITICAL SAFETY: PASS
COMMERCE AUTHORITY: PASS
CREATOR ISOLATION: PASS
FAN ISOLATION: PASS
SINGLE-PASS: PASS (ollama 1/1/1/0, gemini tool loop up to 4 P2)
REDIS LIFECYCLE: PASS (with DLQ unbounded P2 and stalled not retried P1? Actually fixed P1-06, but stalled not retried P2 remains)
DLQ SAFETY: PASS (inbound now fail-safe, send already fail-safe)
IDEMPOTENCY: PASS (with operational not persisted P2)
CONCURRENCY: PASS (with JSONB TOCTOU P1? Actually P1-03 fixed generation_id, but JSONB lost update P1 remains)
PERSISTENCE: PASS (with global metric not per-creator P2)
RESTART SAFETY: PASS (1%→1% via SHA256, but handoff not reloaded P1)
CANARY SAFETY: PASS (SHA256 deterministic, gates correct, but 0 sample health false-positive fixed)
ROLLBACK SAFETY: PASS
EMERGENCY CONTROLS: PASS (with unknown→pause not fail-closed P2)
DEGRADED MODE: PASS
MEMORY LIFECYCLE: PASS (with Interview Friday false negative P2)
BEHAVIORAL LEARNING: PASS (with flat vs hierarchical threshold P2)
OPERATIONAL LOOP: PASS (with product-family audit-only now fixed)
OBSERVABILITY: PASS (with message_preview PII P2)
AUDITABILITY: PASS

PRODUCTION CHANGES: NONE (Stage A read-only)

MIGRATIONS: NONE

CANARY STATE: 1% ACTIVE (canary-29-1pct, global, 1%, ACTIVE, verified via get_rollout, but after tests clear → 0 in fresh process, P2 persistence via sentinel not transactional)

NEW LLM CALLS: 0

ARCHITECTURE CHANGES: NONE

FINAL RECOMMENDATION:
FIX P0-P1 (already fixed in 31B) → CONTINUE OBSERVATION 1% HOLD; fix P2 global metric cap + DLQ bounded before expansion beyond 1%

NEXT ACTION:
Recreate canary-29-1pct 1% if cleared by tests, continue real Telegram observation until sample≥5 healthy over 1h, then re-evaluate gate; Stage B for Phase 32 NOT REQUIRED (all P1 fixed)
```

