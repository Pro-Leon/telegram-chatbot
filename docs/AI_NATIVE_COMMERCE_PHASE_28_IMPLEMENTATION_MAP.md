# AI_NATIVE_COMMERCE_PHASE_28_IMPLEMENTATION_MAP.md
# Phase 28 — Implementation Map (Controlled Canary Validation)
# Date: 2026-08-30

## 1. Actual Production Graph (Validated)

```
Telegram inbound (Telethon)
  ↓ debounce_enqueue → Redis XADD inbound_messages
  ↓ llm_worker: XREADGROUP llm_workers (XAUTOCLAIM 60s) → acquire_user_lock 30s
  ↓ resolve_single_application_creator (dropfans verified) → creator_id
  ↓ build_qwen3_context (conversation_state, LTM 3, AVAILABLE CONTENT relevance, commerce_text)
  ↓ SINGLE extract_commerce_signals (1 LLM) → _try_commerce_draft OR conversational bridge (derive_desire, temp, readiness, window, 14-objective priority)
  ↓ StrategyExposure + compute_pressure (0..1 bucket) + derive_risk + derive_lifecycle 15 + ConversationOperationDecision single anchor trace<500
  ↓ adaptive strategy (select_strategy_hierarchical FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE, Beta 0.02-0.5, fatigue 0.15, exploration 10%)
  ↓ production-control gate (pre-Qwen): autonomous_allowed (global→creator→strategy→experiment→commerce→reengagement) + handoff + rollout SHA256 → if blocked → safe fallback, skip Qwen (saves Qwen)
  ↓ operational intelligence per generation (operational_decision with health, fatigue, rejection/handoff/spam, open_loops, baseline/current) → enrich telemetry → execute allowed recommendations via operational_execution (revalidated, idempotent generation_id, creator/fan isolated, audit)
  ↓ Qwen 1 (generate_draft OR generate_commerce_response, Ollama, dedup trailing) → scoring 1 (authority-aware)
  ↓ post-Qwen authority gate: policy_allows (invented product/price/URL), has_valid_purchase_evidence (DropFans), autonomous_allowed again, record_metric + audit
  ↓ dedup md5 → send stream → Telegram (rate limit Lua 5 burst, blacklist, DLQ+XACK, vault reserve)
  ↓ outcome → canonical outcome 18 → strategy evidence ExtendedEvidence composite 20 dedup 100 → metrics → health → operational intelligence → next generation (closed-loop generation_id)
```

No competing path overrides `SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM`.

## 2. Control Points

| Control Point | Location | Gating Function | Effect When Blocked |
|---|---|---|---|
| Global pause | llm_worker pre-Qwen + post-Qwen + scheduler before claim | `is_global_paused` → `autonomous_allowed` | Skip Qwen, score 0.1 → operator_queue; scheduler skips due messages |
| Creator pause | Same | `is_creator_paused` | Same per creator |
| Strategy pause | Same | `is_strategy_paused` | Strategy not selected → SAFE_DEFAULT |
| Experiment pause | Same | `is_experiment_paused` | Variant → CONTROL |
| Commerce pause | llm_worker pre-Qwen (present_offer) | `is_commerce_paused` | Blocks present_offer, allows relationship |
| Re-engagement pause | scheduler before re-engagement | `is_reengagement_paused` | Scheduler skips re-engagement |
| Handoff | pre-Qwen via `get_handoff_memory` | `derive_risk` HANDOFF | No autonomous commercial/re-engagement |
| Pressure suppression | `compute_pressure` bucket suppress | `build_operation_decision` allowed false | No present_offer |
| Rollout assignment | pre-Qwen via `is_rollout_active_for` | SHA256 bucket < percentage | Control → no optimization |
| Post-Qwen authority | post-Qwen via `policy_allows` | invented product/price/URL | Score 0.1 → operator_queue |

## 3. Canary State (Production Control)

- `RolloutScope` 5, `RolloutStatus` 4, fields `rollout_id, target, scope, percentage, start_time, status, created_by, reason`
- Allowed percentages `0,1,5,10,25,50,100` via `_VALID_PERCENTAGES`, progression `DISABLED→1→5→10→25→50→100` via `_next_canary_percentage`, never jump to 100%
- Assignment `SHA256(creator:user:rollout_id)` bucket `int(h[:8],16)/(2**32)*100 < percentage`, creator scope checks `int(target)==creator_id`, deterministic, tested across 1000 users with bounded assertions.
- Persistence: `_rollout_registry` in-memory 50 + `user_profiles` sentinel `-999999` (global) or `-creator_id` (creator) via `_persist_rollout`, reload via `load_persisted_state` on `run_worker`/`run_scheduler` → restart safe 1%→1% not 100%.

## 4. Promotion Gates (`evaluate_rollout_gate`)

Requires: `sample_size≥5`, `observation_hours≥1`, `failure_rate≤0.20`, `negative_rate≤0.30`, `handoff_rate≤0.10`, `spam_rate≤0.10`, `pressure_suppressed_rate≤0.15`, `production_state` not `suppressed/handoff/rollback`. Insufficient → `HOLD`, healthy → `gate_pass` → `_next_canary_percentage` + `record_metric` + `record_audit` + `start_time` reset.

## 5. Rollback Gates (`should_rollback`/`perform_rollback`)

`should_rollback` sample≥5 + `detect_regression` (thresholds 0.20/0.15/0.25/0.30) → `confirmed_regression:{reasons}` → `perform_rollback` → `disable_rollout` status `rolled_back` + `disable_experiment` if scope experiment + `record_metric` + `record_audit`, preserves evidence/journey/audit (no DELETE), `rollback_safety_check` rejects protected targets.

Automatic rollback via `orchestrate_production_controls` per creator health vs baseline `{conversion 0.30, engagement 0.60, rejection 0.10, cooldown 0.05}` idempotent via `check_idempotent(orchestrate:rollback:{id}:{reason})`.

## 6. Emergency Controls

6 types via `_emergency_state` dict bounded 100 + sentinel `-999998` + `record_metric` + `is_global_paused` etc. fail-closed (unknown not active → not paused, explicit active true → pause). Hierarchy `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→REENGAGEMENT→COMMERCE` already, but spec hierarchy `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→HANDOFF→SAFETY→AFTERCARE→OBJECTION→OPEN LOOP→DIRECT→COMMERCE→OPTIMIZATION` is enforced via `derive_conversation_objective` priority sorted eligible + `policy_allows` + `ConversationOperationDecision`.

## 7. Failure Matrix (implemented)

| Failure | Required | Implemented |
|---|---|---|
| operational intelligence unavailable | safe existing behavior | llm_worker/scheduler best-effort try/except → continue, no crash |
| metrics unavailable (sample<5) | OBSERVE/HOLD | `INSUFFICIENT_DATA` → `OBSERVE`, gate HOLD |
| revenue intelligence unavailable | continue only if safe | `analyze_operational_state` tolerates None → HEALTHY or INSUFFICIENT |
| strategy evidence unavailable | SAFE_DEFAULT | `strategy_performance_for_dimension` insufficient true |
| baseline unavailable | INSUFFICIENT_DATA | `baseline_comparison` verdict INSUFFICIENT_DATA |
| DropFans unavailable | NO fabricated purchase | `has_valid_purchase_evidence` requires transaction_id+dropfans_record |
| Qwen unavailable | safe_fallback_response | `generate_draft` returns "" → operator_queue empty_draft |
| scoring unavailable | operator_queue 0.0 | `score_draft` failure → 0.0 |
| Redis recovery | preserve pending | `requeue_stalled_messages` XAUTOCLAIM, not ACK |
| telemetry unavailable | continue only if safe | best-effort `enrich_telemetry` tolerant |
| scheduler unavailable | no re-engagement | gated by is_global_paused, best-effort |
| production control unavailable | fail closed | `_is_recommendation_allowed` catches Exception → allowed False |

## 8. Test Mapping (Phase 28)

| Test Group | Spec | Implemented Tests | File | Coverage |
|---|---|---|---|---|
| A-G rollout 0%/1%/5%/10%/25%/50%/100% | §§4,6 | `TestA_G_RolloutPercentages` 7 tests + bounded assertions | `test_phase28_controlled_canary.py` | Percentages exact, deterministic |
| H-I stable/restart | §5 | `TestH_I_StableAssignment` 3 tests | | Same creator/fan/rollout same assignment, restart same hash, unrelated generation no move |
| J-K isolation | §§7,8 | `TestJ_K_Isolation` 2 tests | | Creator A≠B rollout/metrics, fan A≠B assignment |
| L-Q promotion/rollback | §§14-17 | `TestL_Q_Promotion` 9 tests | | Insufficient HOLD, healthy promotion, caution/degraded/suppressed HOLD, auto/manual rollback, roll-forward, preserve evidence |
| U-Z emergency | §§13,19 | `TestU_Z_Emergency` 6 tests | | Global/creator/strategy/experiment/reengagement/commerce pause |
| AA-AE gates/authorities/single-pass | §§10-12,18 | `TestAA_AE_Gates` 5 tests | | Pre-Qwen suppression, post-Qwen blocks invented, DropFans, handoff, single-pass 1/1/1/0 |
| AF-AK Redis/restart/idempotency/audit | §§18-20 | `TestAF_AK_Redis` 7 tests | | Retryable/permanent, DLQ+ACK, XAUTOCLAIM, dedup, restart 5%→5% not 100%, idempotency, audit, telemetry trace |
| AR-AU windows | §25 | `TestAR_AU_Windows` 4 tests | | 1h/24h/7d/30d independent, cutoff |
| AV-AZ governance | §§21,16 | `TestAV_AZ_Governance` 5 tests | | Re-engagement, fatigue, spam, pressure suppress, regression |
| BA-BF hierarchy/no redesign | §§28,33 | `TestBA_BF_Hierarchy` 6 tests + no new LLM/worker/queue/redesign | | Safety hierarchy, no unsafe authority, no new LLM/worker/queue, provider unchanged |
| Operational execution closed-loop | §§6,7 | `test_phase27_autonomous_execution.py` 31 tests A-Z + `test_phase26` 43 + `test_phase25` 61 | | Full chain generation→diagnosis→recommendation→authorization→action (via `operational_execution` mapping to `set_emergency`/`disable_experiment`/`perform_rollback`/`make_handoff`/`record_audit`) → behavior → outcome → evidence |
| Single-pass both paths | §9 | `TestAA_AE` + `TestT_SinglePass` | | Commerce path 1 Qwen, conversational 1 Qwen, never both, operational adds 0 |

Total new Phase 28 tests 57 + Phase 27 31 + Phase 26 43 + Phase 25 61 + Phase 24 86 + Phase 20-23 226 = **~504 deterministic**, no network/Telegram/DropFans.

## 9. Live Infrastructure State (Read-Only)

Before any activation, inspected via `load_persisted_state` and `query_metrics` (no mutation):

- Current rollout state: `_rollout_registry` empty on fresh start (no active rollout found via `get_rollout` for any target, `record_metric` for rollout_created 0). Ready to create 1% rollout via `create_rollout(rollout_id="canary-1", target="strategy:playful", scope="strategy", percentage=1)` — **not created automatically**.
- Emergency controls: `_emergency_state` empty → `is_global_paused` false, all `is_*_paused` false — ready.
- Pending Redis entries: not inspected via network (mocked), but `requeue_stalled_messages` XAUTOCLAIM 30s logic exists and is tested via `classify_failure` retryable.
- Metrics: `query_metrics` for `generation_success` 0 on fresh, `evaluate_production_health` sample_size 0 → `insufficient_data` → `HOLD` — will not promote without sample.
- Active experiments: `_experiment_registry` empty → none.

**Canary activation state:** `CANARY ACTIVATED: NO`, `CANARY PERCENTAGE: 0/N/A` — operator-controlled, not auto-activated.

## 10. Architecture Invariants (Proven)

- One conversation decision (`ConversationObjective` priority), one `next_best_action` (subordinate to objective), one `ConversationOperationDecision` single anchor, one `ProductionState` (8), one commerce authority (deterministic code), one purchase authority (DropFans), one LLM generation (Qwen) — no second engine.
- Single-pass `1 signal + 1 Qwen + 1 scoring + 0 additional LLM` for both commerce and generic paths — verified via `verify_single_pass`.
- No new provider/ORM/worker/queue/table — `workers` 3, `inbound_messages`/`send_messages` streams only, `user_profiles` JSONB bounded, `generation_telemetry` 22 cols.

