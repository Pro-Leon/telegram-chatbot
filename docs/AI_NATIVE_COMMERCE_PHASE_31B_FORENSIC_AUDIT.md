# AI_NATIVE_COMMERCE_PHASE_31B_FORENSIC_AUDIT.md
# Phase 31B — Forensic Confirmation (Stage A, READ-ONLY, no production code modified)
# Date: 2026-08-30

## Finding Summary

| ID | Severity | Phase 31 Claim | Current File:Line | Current Behavior | Failure Mode | Proven? |
|---|---|---|---|---|---|---|
| P1-01 | P1 | llm_worker fail-open `except: _skip_qwen=False` | `workers/llm_worker.py:867-868` `except Exception: _skip_qwen_due_to_pause=False` | Production-control exception in pre-Qwen gate (autonomous_allowed, is_commerce_paused, get_handoff_memory, is_rollout_active_for) leaves `_skip_qwen=False`, so Qwen proceeds when should fail-closed | If `is_global_paused` throws (DB down), autonomous Qwen allowed | **PROVEN** via read |
| P1-02 | P1 | dual `response_mode` authority | `memory/context.py:381-383` emits `RESPONSE: mode=react/explore/tease/callback` subordinate to NBA, `commerce/conversation_operations.py:635` `build_operation_decision` also has `response_mode` authoritative, `workers/llm_worker.py` uses `_response_mode` from conversational bridge for `_op_dec` but Qwen prompt already has `RESPONSE: mode` from `build_qwen3_context` before `build_operation_decision` | Qwen receives `RESPONSE: mode` from `build_qwen3_context` (legacy) before `ConversationOperationDecision` is built, could disagree (e.g., legacy says `explore` when Decision says `suppress`) | **PROVEN** — two emissions, but Decision trace is authoritative for control, Qwen prompt is legacy (not control, but opaque) |
| P1-03 | P1 | generation_id changes on retry | `workers/llm_worker.py:514` `generation_id = str(uuid.uuid4())` per `process_message`, `workers/llm_worker.py:1387-1425` `requeue_stalled_messages` XAUTOCLAIM → `process_message(**msg_data)` with **new** generation_id for same `telegram_message_id`, `commerce/strategy_learning.py:249` dedup via `generation_id` in `strategy_generation_seen` 100, `commerce/production_control.py:706` `check_idempotent` per `generation_id:action:scope` | Retry of same inbound (same `user_id:user_message:telegram_message_id`) gets new generation_id → strategy evidence double-counted, idempotency miss | **PROVEN** — `process_message` generates fresh UUID each call, not propagated via Redis payload |
| P1-04 | P1 | Python `hash()` for rollout sentinel | `commerce/production_control.py:265` `sentinel = -abs(hash(rollout.rollout_id) %1000000)-1000` and `hash(rollout.target)` | Python `hash()` is randomized per process (`PYTHONHASHSEED`), so different process/restart gets different sentinel key → `load_persisted_state` misses persisted rollouts (reads wrong sentinel) | **PROVEN** — `hash()` not deterministic, vs `is_rollout_active_for` uses `SHA256` deterministic |
| P1-05 | P1 | zero-sample health reports NORMAL | `commerce/production_control.py:788` `if is_global_paused ... elif handoff>0.10 ... elif ... else: prod_state=NORMAL` with `total_gen 0` → rates 0 → `NORMAL` with `sample_size 0`, `derive_production_state` default `NORMAL` | `sample 0` appears healthy, not `INSUFFICIENT_DATA`; `evaluate_rollout_gate` correctly HOLDs on `sample<5`, but health alone could mislead operator | **PROVEN** — `evaluate_production_health` with `total_gen 0` returns `NORMAL` |
| P1-06 | P1 | inbound DLQ can prevent ACK | `db/redis.py:248` `await r.xadd(DLQ_STREAM, record)` then `await r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)` without try/except around `xadd` | If `XADD DLQ` fails (Redis down, exception), `XACK` never reached → message remains pending forever → repeated recovery | **PROVEN** via read vs `move_send_to_dlq` which has `try/except` around `xadd` but still `xack` |
| P2-A | P2 | `SUPPRESS_PRODUCT_FAMILY` audit-only | `commerce/operational_execution.py: SUPPRESS_PRODUCT_FAMILY` → `record_metric family_suppressed` + audit, not checked in `commerce/content_matching.py:rank_products_by_relevance` | Family degradation recommendation does not actually exclude family from `rank_products_by_relevance` → same family continues | **PROVEN** — execution is audit-only, rank ignores `family_suppressed` metric |
| P2-B | P2 | `recent_reengagements_7d` hardcoded 0 | `workers/scheduler_worker.py:277` `recent_reengagements_7d=0` passed to `is_reengagement_governed_allowed`, not computed via `query_metrics` for last 7d | Frequency limit `max 2/7d` never enforced (always 0 <2) → could over-schedule re-engagement | **PROVEN** — hardcoded 0, not `aggregate_count` for last 7d |

## Detailed Confirmation

### P1-01 — PROVEN

File `workers/llm_worker.py:809-868` pre-Qwen gate:

try:
  _allowed_pre, _reason_pre = _pc_allowed_pre(...)
  ... rollout loop ...
  if not _allowed_pre or _rollout_blocked: _skip_qwen=True
except Exception:
  _skip_qwen_due_to_pause = False  # fail-open

Failure mode: `autonomous_allowed` could throw if `db` or `production_control` has exception (e.g., `is_global_paused` iterates `_emergency_state` but not throwing; however `is_strategy_paused` could throw if `EmergencyControlType` not found? Not, but `get_handoff_memory` could throw if `get_user_profile` DB down). If any of the `try` block throws, `_skip_qwen` remains False → Qwen proceeds when should be blocked. Minimal fix: `except: _skip_qwen=True` fail-closed to operator fallback.

### P1-02 — PROVEN (but not safety bypass)

`memory/context.py:build_qwen3_state_context:360-383` derives `response_mode` from `next_best_action` (follow_up→callback etc.) and emits `RESPONSE: mode`. Then `workers/llm_worker.py:758-785` builds `ConversationOperationDecision` with `response_mode` from same `next_best_action` via `_response_mode` (tease/explore etc.) — **two derivations of same NBA, but could diverge** because `memory/context.py` uses `conversation_state` + `plan_response_mode` (7 rules) while `conversation_operations` uses `next_best_action` directly. However `workers/llm_worker.py` uses `_response_mode` from conversational bridge (which already made `response_mode` subordinate to NBA), so they are same subordinate logic, not diverging in current code. The duplicate is **legacy representation**, not independent decision. Minimal fix: make `memory/context.py` not derive `response_mode` independently, or make it explicitly non-authoritative and ensure `ConversationOperationDecision.response_mode` is the one sent to Qwen contract (or ensure Qwen prompt uses Decision's response_mode, not context's). For hardening, consolidate to one authority: remove `response_mode` derivation from `build_qwen3_state_context` and inject Decision's `response_mode` into Qwen context via `build_conversational_commerce_state` already does `RESPONSE: mode`? Actually `build_conversational_commerce_state` does not inject `RESPONSE: mode`, it injects `COMMERCIAL STATE` etc., not `RESPONSE`. So Qwen currently gets `RESPONSE: mode` only from `memory/context`, not from Decision. **Fix: ensure Qwen receives Decision's `response_mode` as contract, remove legacy `RESPONSE: mode` from `memory/context` or make it derived from Decision.**

### P1-03 — PROVEN

`workers/llm_worker.py:514` `generation_id = str(uuid.uuid4())` per `process_message`. Redis payload `enqueue_inbound` is `{"user_id":..., "content":..., "telegram_message_id":..., "username":..., "first_name":..., "persona":...}` — **no `generation_id`**. `run_worker` `process_message(**msg_data)` where `msg_data` from `data["user_id"]` etc., no generation_id. `requeue_stalled_messages` returns `msg_id` but `process_message` is called with same `data` but new `generation_id` each retry → different. Strategy evidence dedup via `generation_id` in `strategy_generation_seen` will not match retry, so double-count. Minimal fix: propagate `generation_id` via Redis `dedup_id` or payload `generation_id` field, or derive deterministic `generation_id = md5(user_id:content:telegram_message_id)` stable for retry, or store `generation_id` in stream and reuse on XAUTOCLAIM.

### P1-04 — PROVEN

`commerce/production_control.py:265` uses `hash(rollout.rollout_id)`. Python `hash()` randomized per `PYTHONHASHSEED` (default random). Different worker process will have different `hash("canary-29-1pct")` → different sentinel `-abs(hash(...))` → `load_persisted_state` reads wrong sentinel key → not found → `rollout` not restored → canary 1%→0% not promotion but loss. `is_rollout_active_for` uses `SHA256` deterministic, so sentinel should also be `SHA256` deterministic. Fix: `hashlib.sha256(...).hexdigest()` → int.

### P1-05 — PROVEN

`evaluate_production_health` with `total_gen 0` → `success_rate 0/1=0`, `failure 0`, `handoff 0`, etc. → `prod_state NORMAL` with `sample_size 0`. Should be `INSUFFICIENT_DATA` or `UNKNOWN` with `reason_code INSUFFICIENT_SAMPLE`. Minimal fix: if `total_gen <5` → `production_state UNKNOWN/HOLD` and `reason_code INSUFFICIENT_SAMPLE`, or keep `NORMAL` but set `insufficient_data true` and ensure `evaluate_rollout_gate` already HOLDs, but health `reason_code` should reflect insufficient.

### P1-06 — PROVEN

`db/redis.py:248` inbound `move_to_dlq` does `await r.xadd(DLQ_STREAM, record)` then `await r.xack(...)` without try around `xadd`. If `xadd` fails (Redis down, exception), `xack` not executed → pending remains. `move_send_to_dlq` has `try: await r.xadd(...) except: logger.error ...` then `await r.xack` — correct. Inbound should match send's pattern: try XADD, log, then always XACK, or leave pending for retry if DLQ failed? Spec §9 says: if DLQ fails, message must remain recoverable (pending), not lost, but also not ACKed. So inbound current (no try) leaves pending (recoverable) but via exception, not explicit. The desired contract from Phase 31B §9: `DLQ succeeds → XACK, DLQ fails → no XACK, message remains recoverable`. Current inbound `move_to_dlq` with no try will propagate exception → `except` in `run_worker` `move_to_dlq` caller will `logger.exception` and then? In `llm_worker` `except: logger.exception; await move_to_dlq(...)` — if `move_to_dlq` throws, it will be caught by outer `except` → `logger.exception("Worker loop error")` → not ACKed, message remains pending via XAUTOCLAIM → recoverable, but via unhandled exception path, not structured. Minimal fix: wrap `xadd` in try, on success `xack`, on failure log and **do not** `xack` (leave pending), return failure.

### P2-A — PROVEN

`operational_execution.py: SUPPRESS_PRODUCT_FAMILY` only `record_metric family_suppressed` + audit, not checked in `rank_products_by_relevance`. `rank_products_by_relevance` checks `purchased_ids`, `recent_offered_ids/groups`, but not `family_suppressed` metric. Minimal fix: check `is_product_family_suppressed` via `query_metrics` for `family_suppressed` per creator/family/window 7d, or via in-memory suppressed set with `check_idempotent` and `is_strategy_paused`-like, or reuse `recent_offered_groups` already penalizes -0.15, but not suppression. For hardening, add `suppressed_families` check in `rank_products_by_relevance` via `query_metrics` or via `production_control` emergency-like.

### P2-B — PROVEN

`workers/scheduler_worker.py:277` `recent_reengagements_7d=0` hardcoded, not `aggregate_count` for `reengagement_sent` last 7d per `creator:user` or per creator. Minimal fix: `recent = aggregate_count(name="reengagement_sent", creator_id=cid, window=MetricWindow.D7)` or `query_metrics` filtered `user_id`? But `aggregate_count` doesn't filter by `user_id`, only `creator_id`. For per-fan frequency, need `query_metrics` filtered by `user_id` and `creator_id` for last 7d, count `reengagement_sent` or `scheduled_messages` with `reason reengagement`. Use existing `query_metrics` with `creator_id` and filter `user_id` in code, or via `get_recent_offered_groups` not.

## Stage B Fix Plan (Minimal)

- P1-01: 1 LOC `except: _skip_qwen=True` + set fallback draft/score
- P1-02: 5 LOC remove `RESPONSE: mode` derivation from `memory/context.py:build_qwen3_state_context` and ensure `workers/llm_worker.py` injects Decision's `response_mode` into Qwen context (or make legacy non-authoritative comment)
- P1-03: 10 LOC propagate `generation_id` via `enqueue_inbound` payload + `process_message` reuse if present else derive stable `md5`, and `requeue_stalled_messages` preserves generation_id
- P1-04: 1 LOC `hash(...)` → `int(hashlib.sha256(...).hexdigest()[:8],16)`
- P1-05: 3 LOC `if total_gen <5: prod_state=UNKNOWN, reason=INSUFFICIENT_SAMPLE` with `insufficient_data true`
- P1-06: 8 LOC wrap inbound `move_to_dlq` XADD in try, on success XACK, on failure log + no XACK + return
- P2-A: 10 LOC add `is_product_family_suppressed` check in `rank_products_by_relevance` via `query_metrics` for `family_suppressed` per creator/family 7d, or via `production_control` emergency-like
- P2-B: 5 LOC compute `recent = len(query_metrics(name="reengagement_sent", creator_id=cid, window=D7, ...))` filtered by user_id, pass to `is_reengagement_governed_allowed`

Total ~43 LOC, no new worker/queue/LLM/migration, preserve hierarchy, single-pass, DropFans authority.

