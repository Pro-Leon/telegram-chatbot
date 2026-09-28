# AI_NATIVE_COMMERCE_PHASE_31B_IMPLEMENTATION_MAP.md
# Phase 31B — Implementation Map (Stage B, Surgical Hardening)
# Date: 2026-08-30

## 1. Findings Reconciled (from 31B forensic)

| ID | Finding | Proven | Fix Applied |
|---|---|---|---|
| P1-01 | llm_worker fail-open on production-control exception (`except: _skip_qwen=False`) | PROVEN via `workers/llm_worker.py:867` | **FIXED** → `except: _skip_qwen=True` + fallback draft 0.1 + log fail-closed |
| P1-02 | dual `response_mode` authority (`memory/context` vs `ConversationOperationDecision`) | PROVEN via `memory/context.py:381` vs `conversation_operations.py:635` | **FIXED** → removed derivation in `memory/context.py:build_qwen3_context` and emission, authoritative via `ConversationOperationDecision` only (NBA→response_mode in `llm_worker`) |
| P1-03 | generation_id new UUID per retry → duplicate evidence | PROVEN via `workers/llm_worker.py:514` `uuid.uuid4()` per `process_message`, Redis payload no `generation_id` | **FIXED** → deterministic `md5(user:msg:telegram_id)` in `enqueue_inbound` + `process_message(generation_id=None)` reuse, `run_worker` passes `data.get("generation_id")` |
| P1-04 | `hash(rollout_id)` Python hash randomized | PROVEN via `commerce/production_control.py:265` `hash(rollout.rollout_id)` | **FIXED** → `hashlib.sha256(...).hexdigest()[:8]` deterministic |
| P1-05 | zero-sample health reports NORMAL (false-positive) | PROVEN via `evaluate_production_health` total_gen 0 → NORMAL | **FIXED** → `if total_gen<5: prod_state=CAUTION, reason=insufficient_sample` |
| P1-06 | inbound DLQ `XADD` failure can prevent `XACK` leaving pending forever | PROVEN via `db/redis.py:248` no try around `xadd` | **FIXED** → try `xadd`, on success `xack` return True, on failure log `LEAVE pending` return False |
| P2-A | `SUPPRESS_PRODUCT_FAMILY` audit-only, not enforced | PROVEN via `operational_execution.py` audit only | **FIXED** → `rank_products_by_relevance` now checks `_is_family_suppressed(creator_id, bundle_group)` via `query_metrics` 7d and `continue` (exclude) while preserving evidence; added `creator_id` param to `rank_products_by_relevance` and callers |
| P2-B | `recent_reengagements_7d` hardcoded 0 | PROVEN via `workers/scheduler_worker.py:277` `recent_reengagements_7d=0` | **FIXED** → compute real `recent_cnt = sum(1 for e in query_metrics(name="reengagement_sent", creator_id=cid, window=D7) if e.get("user_id")==offer["user_id"])` and `record_metric` on schedule |

## 2. P1 Fixes Detail

### P1-01 — Fail-Closed Qwen Gate
- **File:** `workers/llm_worker.py:867-868`
- **Before:** `except Exception: _skip_qwen_due_to_pause = False`
- **After:** `except Exception: logger.warning(...); _skip_qwen_due_to_pause=True; _paused_reason="production_control_error"; draft="Thanks..."; score 0.1, flags autonomous_paused:production_control_error, telemetry handoff_required`
- **LOC:** 8
- **Test:** `TestP1_01_FailClosed` — production-control exception → Qwen not called (checked via file content and direct gate logic) — **PASS**

### P1-02 — One Authority
- **File:** `memory/context.py:535-565, 585-591, 358-383`
- **Before:** `build_qwen3_context` derived `response_mode` via `plan_response_mode` + `evaluate_question_budget` and emitted `RESPONSE: mode`/`QUESTION: allowed` in `build_qwen3_state_context`; duplicate with `ConversationOperationDecision` in `workers/llm_worker.py`
- **After:** Removed derivation in `build_qwen3_context` (keep only `derive_conversation_state` for identity/open_threads), pass `response_mode=None, question_allowed=None` to `build_qwen3_state_context`, added comment `authoritative via ConversationOperationDecision`, kept NBA override block only for explicit callers
- **LOC:** -20 +3
- **Test:** `TestP1_02_OneAuthority` — NBA follow_up→callback, present_offer→tease, no legacy `RESPONSE: mode` without NBA — **PASS**

### P1-03 — Generation ID Survives Retries
- **Files:** `workers/llm_worker.py:500,514`, `db/redis.py:171`
- **Before:** `generation_id = str(uuid.uuid4())` per `process_message`, `enqueue_inbound` no `generation_id`, `run_worker` not passing
- **After:** `process_message(..., generation_id=None)` → `if generation_id is None: hashlib.md5(f"{user_id}:{user_message}:{telegram_message_id}".encode()).hexdigest()` else use passed; `enqueue_inbound` generates deterministic `generation_id` if missing; `run_worker` passes `data.get("generation_id")`
- **LOC:** 12
- **Test:** `TestP1_03_GenerationId` — same `user:msg:telegram_id` → same md5, XAUTOCLAIM preserves id, idempotency `check_idempotent` prevents duplicate evidence — **PASS**

### P1-04 — Deterministic Hash
- **File:** `commerce/production_control.py:265`
- **Before:** `sentinel = -abs(hash(rollout.rollout_id) %1000000)-1000`
- **After:** `_h = int(hashlib.sha256(rollout.rollout_id.encode()).hexdigest()[:8],16); sentinel = -abs(_h %1000000)-1000`
- **LOC:** 2
- **Test:** `TestP1_04_Sentinel` — same creator/user/rollout same bucket after simulated restart, `hashlib.sha256` deterministic — **PASS**

### P1-05 — Zero-Sample Health
- **File:** `commerce/production_control.py:790`
- **Before:** `if total_gen<5` not checked, `NORMAL` with 0 sample
- **After:** `if total_gen<5: prod_state=CAUTION, reason="insufficient_sample"` before other checks
- **LOC:** 3
- **Test:** `TestP1_05_ZeroSample` — 0→CAUTION insufficient_sample, 1-4→CAUTION, 5 eligible — **PASS**

### P1-06 — DLQ Failure-Safe
- **File:** `db/redis.py:230-249`
- **Before:** `await r.xadd(DLQ_STREAM, record); await r.xack(...)` without try
- **After:** `try: await r.xadd(...); except: logger.error LEAVE pending; return False; await r.xack` only on success, return True
- **LOC:** 8
- **Test:** `TestP1_06_DLQ` — success → XADD+XACK True, failure → no XACK False, message remains recoverable — **PASS**

## 3. P2 Fixes Detail

### P2-A — Product Family Enforcement
- **Files:** `commerce/content_matching.py:71` added `_is_family_suppressed` helper via `query_metrics` 7d per `creator:family`, `rank_products_by_relevance` now `creator_id` param and `if suppressed: continue` (exclude but preserve evidence), `best_match_or_none` updated, callers `memory/context.py:595` and `commerce/conversational.py:174` now pass `creator_id`
- **LOC:** 20
- **Test:** `TestP2_A_FamilySuppression` — without suppression fitness first, after `family_suppressed` fitness|gym metric → lace first, different creator unaffected — **PASS**

### P2-B — Real Frequency
- **File:** `workers/scheduler_worker.py:277`
- **Before:** `recent_reengagements_7d=0` hardcoded
- **After:** compute `recent_cnt = sum(1 for e in query_metrics(name="reengagement_sent", creator_id=cid, window=D7) if e.get("user_id")==offer["user_id"])` and pass `recent_reengagements_7d=_recent_cnt_gov`; after `await schedule_reengagement_if_eligible` true → `record_metric(name="reengagement_sent", creator_id=cid, user_id=offer["user_id"])`
- **LOC:** 12
- **Test:** `TestP2_B_ReengagementFrequency` — 0→eligible, 1→eligible, 2→blocked, old >7d not counted, creator/fan isolated — **PASS**

## 4. Exact Files Changed

- `workers/llm_worker.py` — P1-01 fail-closed, P1-03 generation_id deterministic, P1-02 via context not here but wiring
- `memory/context.py` — P1-02 remove duplicate response_mode authority
- `db/redis.py` — P1-03 enqueue generation_id, P1-06 inbound DLQ fail-safe
- `commerce/production_control.py` — P1-04 SHA256 sentinel, P1-05 zero-sample health
- `commerce/content_matching.py` — P2-A suppression check + creator_id param
- `commerce/conversational.py` — P2-A pass creator_id
- `workers/scheduler_worker.py` — P2-B real frequency + metric
- `tests/test_phase31_hardening.py` — NEW 26 tests for P1/P2 + invariants

## 5. Authority Flow (Preserved)

```
SAFETY (is_blocked → HUMAN_HANDOFF)
> CREATOR ISOLATION (WHERE creator_id)
> HANDOFF (get_handoff_memory → HANDOFF)
> AFTERCARE (aftercare_status pending)
> OBJECTION (has_objection)
> OPEN_LOOP (has_open_loop)
> DIRECT_REQUEST (explicit_purchase)
> COMMERCE (has_relevant_product, is_authorized_commerce)
> PRODUCTION CONTROL (autonomous_allowed, derive_production_state, rollout gate)
> OPTIMIZATION (select_strategy_hierarchical, operational_decision)
> LLM LANGUAGE (Qwen single)
```

No change to hierarchy, only hardening.

## 6. Retry/Generation-ID Flow (P1-03)

```
Telegram inbound (user_id, content, telegram_message_id)
 ↓ enqueue_inbound: deterministic generation_id = md5(user_id:content:telegram_message_id) stored in stream data["generation_id"]
 ↓ XREADGROUP → data["generation_id"] preserved
 ↓ process_message(generation_id=data.get("generation_id")) → if None: md5 deterministic else reuse → same logical generation
 ↓ XAUTOCLAIM stalled → same data["generation_id"] preserved → process_message with same generation_id → strategy_generation_seen dedup prevents double-count, check_idempotent prevents duplicate operational action
```

## 7. Rollout Determinism (P1-04)

`hash(rollout_id)` Python randomized → `hashlib.sha256(rollout_id.encode()).hexdigest()[:8]` deterministic int → sentinel `-abs(_h %1000000)-1000` same across restarts, `load_persisted_state` finds same key, assignment `SHA256(creator:user:rollout_id)` already deterministic, now sentinel too.

## 8. Health Semantics (P1-05)

`evaluate_production_health` with `total_gen 0` → `CAUTION insufficient_sample` (not NORMAL), `sample 1-4` → CAUTION, `sample 5` → eligible for gate, future timestamps excluded via `ts < cutoff` skip, windows independent via separate `query_metrics` per window.

## 9. DLQ Semantics (P1-06)

Inbound `move_to_dlq`: try `XADD DLQ`, on success `XACK` original → success; on `XADD` exception log + return False (no XACK) → message remains pending → `XAUTOCLAIM` retry → recoverable, not lost. Send `move_send_to_dlq` already had try but still `XACK` even on failure (durable send stream retains payload for manual recovery) — preserved.

## 10. Product-Family Enforcement (P2-A)

`SUPPRESS_PRODUCT_FAMILY` → `record_metric(name="family_suppressed", creator_id, product_family, value=1.0)` 7d window → `_is_family_suppressed` via `query_metrics(name="family_suppressed", creator_id, window=D7)` check `product_family` → `rank_products_by_relevance(..., creator_id)` → if suppressed `continue` (exclude) but metric preserved, no evidence deletion, creator isolated, fan isolated via creator only (global per creator, not per fan, but fan isolation via `creator_id` in rank, not `user_id` — per spec fan isolated where applicable, family is per creator, acceptable), bounded via `record_metric` 5000, restart safe via sentinel, idempotent via generation_id, auditable via metric.

## 11. Re-engagement Frequency Enforcement (P2-B)

`actual re-engagement count = sum(1 for e in query_metrics(name="reengagement_sent", creator_id=cid, window=D7) if e.get("user_id")==offer["user_id"])` → passed as `recent_reengagements_7d` to `is_reengagement_governed_allowed` (max 2/7d) → 0/1 eligible, 2 blocked, old >7d excluded via window cutoff, creator/fan isolated via `creator_id`+`user_id` filter, restart same via `load_persisted_state` metrics 50, auditable via metric.

## 12. Test Mapping

| Finding | Test | File | Result |
|---|---|---|---|
| P1-01 fail-closed | production-control exception → Qwen not called | `test_phase31_hardening.py:TestP1_01` | PASS |
| P1-02 one authority | NBA→response_mode, legacy not override | `TestP1_02` | PASS |
| P1-03 generation_id | same md5, XAUTOCLAIM preserves, idempotency | `TestP1_03` | PASS |
| P1-04 SHA256 | same bucket after restart, deterministic sentinel | `TestP1_04` | PASS |
| P1-05 zero sample | 0→CAUTION insufficient, 5 eligible | `TestP1_05` | PASS |
| P1-06 DLQ | success→XACK, failure→no XACK | `TestP1_06` | PASS |
| P2-A family | suppressed excluded, other family eligible, creator isolated | `TestP2_A` | PASS |
| P2-B frequency | 0/1 eligible, 2 blocked, old excluded, isolated | `TestP2_B` | PASS |
| Invariants | single-pass, creator/fan isolation, DropFans, emergency, rollback, canary HOLD | `TestInvariants` | PASS |

Total new tests 26, plus existing 20-30 regression 518 — all green.

