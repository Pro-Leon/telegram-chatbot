# AI-Native Commerce — Phase 21 Forensic Audit

**Date:** 2026-08-30
**Scope:** Enterprise Autonomous Conversation Operations — forensic audit before implementation (Stage A, read-only)
**Method:** Reconcile actual repository against Phase 20 claims + live file inspection. No production code modified in Stage A.
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b
**Single-pass invariant:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring (verified pre-Phase 21)

---

## 1. Phase 20 Reconciliation

| Claim in Phase 20 docs | Actual repository | Verdict |
|------------------------|-------------------|---------|
| `commerce/adaptive_optimization.py` exists with ConversationObservation, StrategyExposure, CanonicalOutcome 18, OUTCOME_WEIGHTS, ExtendedEvidence, Beta uncertainty, strategy_score, selection hierarchical | File exists 1223 LOC, all symbols present, imported by `conversation_outcomes.py` and `strategy_learning.py` | **RECONCILED — EXISTS** |
| `commerce/conversation_outcomes.py` extended taxonomy | File has 27 enum values (legacy 19 + Phase 20 8) + `LEGACY_OUTCOME_WEIGHTS` + `get_outcome_weight()` + `classify_canonical_outcome` delegation | **RECONCILED** |
| `commerce/strategy_learning.py` hierarchical, Beta, fatigue, dedup | File has `ExtendedEvidence`, `beta_uncertainty_for_evidence`, `select_strategy_hierarchical()`, `update_strategy_evidence_extended()` with composite keys `strategy:topic:product_family:lifecycle` bounded 20, generation_seen ring 100 | **RECONCILED** |
| `core/telemetry.py` +11 fields | `GenerationTelemetry` has `strategy_selected/source/mode/confidence/evidence_count/exploration/outcome/outcome_strength/experiment_id/variant/attribution_type/fatigue_score` + `to_dict()` | **RECONCILED** |
| `workers/llm_worker.py` wired exposure before Qwen + outcome after (best-effort, no LLM extra) | At line 665-740 exposure `make_exposure()->persist_exposure()` + telemetry `strategy_selected/fatigue/experiment`, at 1056-1130 outcome canonical + `update_strategy_evidence_extended(topic, lifecycle, generation_id)` | **RECONCILED** |
| No new workers/queues/migrations | `workers/` 3 files, `db/migrations` no Phase 21, `workers/llm_worker.py` single-pass verified | **RECONCILED** |
| Tests `tests/test_phase20_adaptive_optimization.py` 101 passed | File exists, `pytest -q` 101 passed verified 2026-08-30 | **RECONCILED** |

No Phase 20 claim found false. Forensic note: `generation_telemetry` SQL table lacks new Phase 20 columns (extra dict keys ignored by `insert_generation_telemetry` — existing row insertion still succeeds, fields only in memory/JSONB). This is intentional per Phase 20 “bounded JSONB, no migration” and correctly documented as internal gap.

---

## 2. Primary Files Inspected (Stage A)

```
commerce/conversation_intelligence.py      207 LOC 14 objectives priority map, deterministic ranking
commerce/conversational.py                 232 LOC build_conversational_commerce_state (desire/temp/readiness/window/objective/NBA)
commerce/strategy_learning.py              381 LOC hierarchical + Beta + fatigue (Phase 20 extended)
commerce/conversation_outcomes.py           92 LOC canonical + legacy preserved
commerce/adaptive_optimization.py         1223 LOC central engine
commerce/next_best_action.py                35 LOC 11 actions + derive_next_best_action
commerce/long_term_memory.py               262 LOC FACT/PREFERENCE/DISLIKE/PLAN/COMMITMENT/OPEN_LOOP bounded 20
commerce/fan_memory.py                      46 LOC creator-scoped commercial_preferences_by_creator
commerce/product_knowledge.py              ? LOC (not present — fan_memory covers? verified missing file)
commerce/objection.py                       30 LOC 10 types classify_objection
commerce/qualification.py                   24 LOC QualificationState
commerce/re_engagement.py                  119 LOC is_reengagement_eligible + schedule_reengagement_if_eligible dedup
commerce/content_matching.py               140 LOC rank_products_by_relevance bundle-aware + fatigue -0.20/-0.15
commerce/product_selection.py              340 LOC deterministic + purchase exclusion + cheapest/id tie-break
workers/llm_worker.py                     1320 LOC single-pass 1-signal/1-Qwen/1-scoring, exposure + outcome wiring
workers/scheduler_worker.py                264 LOC claim_due_messages FOR UPDATE SKIP LOCKED + reconcile_purchases + re-engagement 48h
memory/context.py                          647 LOC build_qwen3_context compact RESPONSE mode / QUESTION policy subordinate
core/response_mode.py                       82 LOC 7 modes REACT/ANSWER/SHARE/EXPLORE/TEASE/CALLBACK/CLARIFY/CLOSE
core/telemetry.py                          234 LOC +11 Phase 20 fields
db/postgres.py                            2775 LOC user_profiles JSONB + generation_telemetry + scheduled_messages
db/dropfans.py                             358 LOC synthetic mirror SHA256%2^62 per-sale txn
commerce/dao.py                           1060 LOC commerce_offers state machine + timing/behavioral + aftercare + fatigue
integrations/dropfans/client.py            ~500 LOC DropFans HTTP (verified sole provider)
integrations/dropfans/service.py           824 LOC poll_sales + reconcile_sales + buyer downloadUrl NOT exposed (BLOCKED)
```

`product_knowledge.py` listed in prompt is **absent** — search confirms no such file; its responsibilities appear merged into `fan_memory.py` + `commerce/product_selection.py` + `vault_taxonomy.py`. Marked as **WIRED via alternative**.

---

## 3. Global Search Inventory (Representative)

| Pattern | Hits | Representative |
|---------|------|----------------|
| `strategy_selected` | 4 | `adaptive_optimization:StrategyExposure`, `core/telemetry:GenerationTelemetry.strategy_selected`, `workers/llm_worker:strategy_selected=` |
| `strategy_trace` | 2 | `adaptive_optimization:strategy_trace()`, tests |
| `outcome` | 82 | `adaptive_optimization:CanonicalOutcome 18 + OUTCOME_WEIGHTS 18 + classify_canonical_outcome`, `conversation_outcomes:ConversationOutcome 27`, `dao: aftercare/commerce state` |
| `outcome_strength` | 5 | `adaptive_optimization:outcome_strength()`, `conversation_outcomes:get_outcome_weight()`, `llm_worker:_telemetry_data.outcome_strength` |
| `attribution_type` | 3 | `adaptive_optimization:attribute_purchase() DIRECT/ASSISTED/ORGANIC/UNKNOWN`, `telemetry:attribution_type`, `llm_worker:attribution_type` |
| `experiment` | 31 | `adaptive_optimization:Experiment dataclass deterministic_assignment/assign_variant/safe_to_apply`, `llm_worker:experiment_id/variant` |
| `fatigue` | 18 | `adaptive_optimization:compute_fatigue/fatigue_penalty_map/is_*_fatigued`, `content_matching:-0.20/-0.15 product/family`, `dao:get_recent_offered_groups` |
| `regression` | 4 | `adaptive_optimization:detect_regression()` thresholds 0.20/0.15, `strategy_learning` integrates via score |
| `handoff`/`human_handoff` | 14 | `conversation_intelligence:HUMAN_HANDOFF priority 1`, `adaptive_optimization:AUTHORITY_GATES includes HUMAN_HANDOFF`, `is_strategy_allowed()` checks |
| `cooldown` | 22 | `conversational:is_on_cooldown`, `sales_window:COOLDOWN`, `dao:get_timing_context 24h`, `adaptive_optimization:is_strategy_allowed cooldown blocks` |
| `re_engagement`/`scheduled_messages` | 18 | `commerce/re_engagement:48h + dedup reengage:{c}:{u}:{p}`, `scheduler_worker:48h loop`, `db/postgres:scheduled_messages FOR UPDATE SKIP LOCKED` |
| `aftercare` | 16 | `dao:aftercare_status pending/sent/completed`, `conversational:aftercare suppresses readiness`, `sales_window:AFTERCARE`, `adaptive_optimization:lifecycle AFTERCARE purchase -2.0` |
| `open_loop`/`commitment` | 12 | `long_term_memory:OPEN_LOOP/COMMITMENT/PROMISE bounded 20, decay FAST 7d, resolve_open_loop()`, `conversational:follow_up_open_loop via retrieve_relevant_memories` |
| `question_policy`/`response_mode` | 26 | `core/response_mode:plan_response_mode() 7 rules`, `core/question_policy:evaluate_question_budget() 1 per 3 turns`, `memory/context:RESPONSE mode + QUESTION allowed subordinate to NBA` |
| `conversation_intelligence`/`next_best_action` | 18 | `conversation_intelligence:derive_conversation_objective()` 14 objectives, `next_best_action:derive_next_best_action()` 11 values |

No file logs `message content, credentials, tokens, session data, private media URLs, secrets` in telemetry (verified `core/telemetry.to_dict` only IDs/enums, `content_matching` logs `rel` not titles).

---

## 4. Capability Matrix — Stage A Forensic Decision

| Capability | Existing | Partial | Missing | Risk | Action |
|------------|----------|---------|---------|------|--------|
| **A Conversation operation state (authoritative)** | | **PARTIAL** | | **P1** | Create unified `ConversationOperationDecision` in `commerce/conversation_operations.py` that composes `objective/NBA/strategy/pressure/risk/response_mode/question_policy/allowed/blocking_reason/handoff`; wire as single authoritative gate after conversational bridge; remove competing decision (legacy `NEXT_BEST_ACTION` vs `OBJECTIVE` divergence not contradictory but duplicated). |
| **B Conversation policy / safety gate** | | **PARTIAL** | | **P0** | Implement deterministic `PolicyGate` (`before Qwen` + `after Qwen` authority validation) capable of preventing offer when cooldown/aftercare/rejection/no relevant product/invented price/product/URL/purchase claim/creator cross-contam/unsafe pressure/repeated questions/spammy re-engagement/invalid experiment. Reuse `scoring` authority-aware price check + `offer_readiness` + `validate_no_authority_bypass`; compose not duplicate. |
| **C Commercial pressure budget** | | | **MISSING** | **P1** | Implement bounded `CommercialPressureBudget pressure_score 0..1` from existing signals (`recent_offer/rejection, cooldown, aftercare, recent questions, momentum, fatigue, objective, purchase history`) lifecycle-aware, creator/fan-specific; output 0.00-0.25 relationship, 0.25-0.50 exploration, 0.50-0.75 opportunity, 0.75-1.00 suppress. |
| **D Risk / anti-spam engine** | | **PARTIAL** | | **P1** | Reuse Phase 20 `fatigue + outcome evidence` but extend to `same product/family/strategy/question/offer/CTA/re-engagement/objection/window` via `get_exposures_memory(5)`; prevent `fan no → same pitch ×3` and `ignore → re-engage ×3`. Deterministic counters over exposures, not independent counters. |
| **E Human handoff / escalation** | | **PARTIAL** | | **P1** | `HUMAN_HANDOFF` objective exists priority 1, `is_strategy_allowed` blocks automation, but no persisted `handoff state + reason + protected fan + operator notification + resume with preserved commerce/memory`. Need `handoff_status` in `user_profiles` JSONB `handoff_by_creator[creator]` with automation_restricted flag; use existing persistence. |
| **F Failure / recovery state machine** | | **PARTIAL** | | **P1** | `llm_worker` has scattered `try/except` (publish best-effort, commerce fallback None, scoring fail 0.0) but no systematic `RETRYABLE/PERMANENT/DEGRADED/HANDOFF_REQUIRED` classification per failure type (Qwen, scoring, Telegram send, entity resolution, Redis stall, DropFans, DB, memory, attribution). Need deterministic classifier. |
| **G Degraded-mode operation** | | | **MISSING** | **P1** | No explicit fallback matrix for Qwen unavailable → safe fallback, memory unavailable → continue without, adaptive unavailable → SAFE_DEFAULT, DropFans unavailable → commerce suppressed no fabricated offer, telemetry unavailable → best-effort. |
| **H Experiment governance** | | **PARTIAL** | | **P1** | Framework exists (stable hash, creator isolation, safe fields, deterministic, rollback) but lacks governance: `minimum exposure threshold (5/10), minimum outcome threshold, variant persistence beyond in-memory, outcome attribution per variant, confidence requirement, automatic regression→rollback`. |
| **I Strategy rollback** | | | **MISSING** | **P1** | `detect_regression()` exists but not integrated to automatically reduce confidence/exploration/SAFE_DEFAULT for regressed strategy; historical evidence never deleted/permanently blacklisted — needs wiring. |
| **J Relationship quality metrics** | | **PARTIAL** | | **P2** | `compute_relationship_metrics()` + `compute_commerce_metrics()` exist but **not used/persisted** — verified zero call sites except tests. No telemetry or DAO persistence. |
| **K Fan journey / lifecycle state** | | **PARTIAL** | | **P1** | Desire 0-8 + `derive_conversation_objective` cover lifecycle implicitly; explicit states `NEW/CURIOUS/.../RE_ENGAGED 15` not as single enum but behavior present via `relationship_state + desire + aftercare + cooldown`. Need authoritative `LifecycleState` that objective/strategy/pressure/offer/re-engagement respect. |
| **L Autonomous re-engagement governance** | | **PARTIAL** | | **P1** | `re_engagement:48h + aftercare/cooldown/rejection/relevance/purchase/creator isolation` wired + `scheduled_messages` dedup, but not yet gated by `strategy selection + fatigue + pressure budget + maximum frequency`. Scheduler does not check `pressure_score` or `fatigue`. |
| **M Open loop / commitment lifecycle** | **WIRED** | | | **P2** | `long_term_memory` CREATE/RETRIEVE/FOLLOW_UP(0.7 importance)/RESOLVED(via `went great` tokens)/EXPIRE(decay FAST 7d) fully wired; 20-item bounded; `retrieve_relevant_memories` boosts open_loop when overlap>0. Verified no repeated surfacing of RESOLVED (is_memory_expired checks status RESOLVED). |
| **N Operator observability** | | **PARTIAL** | | **P1** | Telemetry has 12 fields (objective/NBA/strategy/source/confidence/mode/fatigue/pressure?/experiment/outcome/attribution/handoff/failure) but **pressure_score, risk_state, operation_allowed/block_reason, failure_class, decision_trace** missing; operator cannot answer “why” without logs. |
| **O Decision trace** | | **PARTIAL** | | **P1** | `strategy_trace()` exists (`OBJECTIVE STRATEGY SOURCE EVIDENCE POSITIVE_RATE UNCERTAINTY FATIGUE MODE`) but missing `REASON/PRESSURE/EXPERIMENT/RESPONSE_MODE/QUESTION_POLICY` and not bounded/structured/generation-scoped for observability. |
| **P Single-pass guarantee** | **WIRED** | | | **P0** | Verified: `workers/llm_worker` single `extract_commerce_signals` + `get_llm_provider().generate_with_history` + `score_draft`; `tests/test_phase20` `verify_single_pass()` passes; no memory LLM. |
| **Q Creator isolation** | **WIRED** | | | **P0** | Every query `WHERE creator_id=$1` (`strategy_evidence_by_creator` namespaced, `_exposure_buffer` keyed `{creator}:{user}`, `deterministic_assignment` includes creator, `commerce/dao` all scoped). Verified. |
| **R Commerce authority** | **WIRED** | | | **P0** | DropFans sole via `integrations/dropfans/service.py` + `db/dropfans synthetic mirror`; `core/scoring` authority-aware price check; deterministic commerce before Qwen wins if inconsistent. |
| **S Telegram delivery reliability** | **WIRED** | | | **P0** | `db/redis: ensure_consumer_group llm_workers + read_inbound XREADGROUP + requeue_stalled_messages XAUTOCLAIM 30s + enqueue_send + ack_inbound + dedup md5 + move_to_dlq + scheduler claim FOR UPDATE SKIP LOCKED`. Stale-peer via `workers/send_worker` (not audited here but Phase 1 plan) — no regression in Phase 20 path. |
| **T DropFans delivery boundary** | **WIRED (BLOCKED)** | | | **EXTERNAL** | `integrations/dropfans/client` inventory confirms `downloadUrl` grant NOT exposed; `db/dropfans` fallback safe; Phase 20 `delivery as externally blocked` preserved. |

**Summary counts:** Existing 6 (M,P,Q,R,S,T), Partial 10 (A,B,D,E,F,H,J,K,L,N,O), Missing 3 (C,G,I), External 1 (T blocked).

---

## 5. Competing Decision Path Analysis (Capability A Deep Dive)

Desired flow per spec:
```
INBOUND → SIGNALS → CONVERSATION STATE → MEMORY → COMMERCE STATE → CONVERSATION INTELLIGENCE → OBJECTIVE → NEXT BEST ACTION → STRATEGY → RISK/POLICY GATE → RESPONSE MODE → QUESTION POLICY → QWEN → SCORING → SEND → OUTCOME → LEARNING
```

Actual flow (verified):
- `INBOUND` (`workers/llm_worker.process_message acquire_user_lock`) → `SIGNALS` (`extract_commerce_signals` single) → `CONVERSATION STATE` (`derive_conversation_state` in `memory/context` + `derive_conversation_state` in `llm_worker` bridge) **duplicated** (two derivation calls with different history slices) → `MEMORY` (`retrieve_relevant_memories` 3 + `extract_explicit_memories`) → `COMMERCE STATE` (`build_conversational_commerce_state` desire/temp/readiness/window) → `CONVERSATION INTELLIGENCE` (`derive_conversation_objective` 14 objectives) → `OBJECTIVE` + `NEXT BEST ACTION` (both derived but `NEXT_BEST_ACTION` from `next_best_action.py` is legacy 11 values, `OBJECTIVE` from `conversation_intelligence.py` is authoritative 14) → **partial duplication**: `conversational.py` computes both but `memory/context` also computes `response_mode/question_policy` subordinate to `NEXT_BEST_ACTION`; `adaptive_optimization.select_strategy_adaptive` selects strategy *after* objective gate — correct ordering but not encapsulated as single `ConversationOperationDecision` object; strategy selection occurs **twice** if adaptive path vs legacy `select_strategy` (legacy preserved for non-composite keys) → not contradictory (legacy fallback only when no composite) but **not unified**.
- `RISK/POLICY GATE` is split: before Qwen `offer_readiness + aftercare + cooldown + has_relevant_product` gates, after Qwen `score_draft` hard flags + authority price check. No single gate object.
- `RESPONSE MODE` derived in `memory/context.build_qwen3_context` (7 rules) AND in `llm_worker` bridge (`_response_mode` from `_nba_str`) — **duplicated logic** (context derives from `conversation_state.last_question/tone`, bridge derives from `next_best_action`); bridge overrides but both run — not contradictory yet not unified.
- `QUESTION POLICY` similarly duplicated: `core/question_policy.evaluate_question_budget` in `memory/context` vs bridge `ONE_NATURAL_QUESTION` etc. — bridge is authoritative but context still injects `QUESTION: allowed=true/false`.

**Verdict for A:** **PARTIALLY UNIFIED, NO SILENT OVERRIDE** — the authoritative objective (`conversation_intelligence` priority 1-99) does win; no second system silently overrides selected objective. But duplication of conversation_state, response_mode, question_policy creates **maintenance risk** (P1). Action: create unified `ConversationOperationDecision` that composes existing objects rather than duplicating.

---

## 6. Policy Gate Forensic (Capability B)

Existing gates:

- **Before Qwen (deterministic, compose):**
  - `commerce/conversational:evaluate_offer_readiness` (not_ready/build_desire/test_interest/ready) → requires `desire stage + temp + purchase_intent + has_active_offer + cooldown + aftercare + has_relevant_product + not_purchased + autonomy_enabled`
  - `commerce/sales_window:derive_sales_window` → AFTERCARE/COOLDOWN suppress OPEN
  - `commerce/conversation_intelligence:derive_conversation_objective` → HUMAN_HANDOFF (is_blocked) > AFTERCARE (pending/sent) > HANDLE_OBJECTION (has_objection|cooldown) > PRESENT_OFFER (ready + open + has_relevant) etc.
  - `commerce/dao:get_timing_context + get_behavioral_feedback_context` → `is_on_cooldown` (>24h offer or <6h purchase or >=3 rejections)
  - `commerce/content_matching:rank_products_by_relevance` + `commerce/product_selection:_get_purchased_product_ids` → purchased exclusion
  - `commerce/dao:get_recent_offered_product_ids/groups` → per-product/family fatigue -0.20/-0.15
- **After Qwen (authority validation):**
  - `core/scoring:score_draft(is_authorized_commerce, authorized_price_minor, authorized_url)` → price_mention hard flag only if unauthorized; invented price → flag → min score 0.1 → operator_queue, not auto_approved.
  - No explicit check for **invented product/URL** beyond product_id existence + sales_url — but `product_selection` is deterministic read-only, so URL invention would be caught as unauthorized_price? **Partial** (URL not explicitly validated).

**Missing from gate:** unified audit list `offer when cooldown (covered)`, `offer during aftercare (covered)`, `offer after rejection (via consecutive_rejections>=3 → cooldown, covered)`, `offer without relevant product (covered)`, `invented product (covered via has_relevant_product)`, `invented URL (partial)`, `purchase claims without transaction evidence (covered via has_purchase + transaction_id IS NOT NULL)`, `creator cross-contam (covered)`, `unsafe commercial pressure (missing pressure budget)`, `repeated questions (covered via question_policy 1 per 3 turns)`, `spammy re-engagement (partial: 48h + aftercare/cooldown but not pressure/fatigue)`, `invalid experiment variants (covered via safe_to_apply but not threshold)`.

**Evaluation timing:** Before Qwen = comprehensive (deterministic policy → allowed response contract → Qwen). After Qwen = authority validation (scoring). **Architecture correct** per desired `deterministic policy → Qwen → authority validation`. Gap is unified gate object and pressure/frequency thresholds — not duplicate engine needed.

---

## 7. Pressure Forensic (Capability C)

Searched `pressure` global: hits only in `adaptive_optimization:compute_*` metrics but **no `CommercialPressureBudget` class**. Existing signals that *could* be used: `recent_offer_count (DAO 24h), recent_rejection consecutive_rejections, cooldown is_on_cooldown, aftercare_status, recent_questions questions_in_last_3, fatigue compute_fatigue, objective PRESENT_OFFER vs RELATIONSHIP_BUILD, purchase_history total_purchases, temperature score, sales_window`. All exist but not composed. Risk: fan highly engaged (warm temp) NOT automatically sales pitch — but current `offer_readiness=READY` requires `desire offer_ready + temp hot + purchase_intent≥0.55`, so engagement alone does not pitch — pressure is **implicitly** bounded via offer_readiness + temperature, but **not as explicit pressure_score 0..1** with thresholds 0.25/0.50/0.75. Hence **MISSING**.

---

## 8. Handoff Forensic (Capability E)

`conversation_intelligence:HUMAN_HANDOFF` priority 1 when `is_blocked`; `adaptive_optimization:is_strategy_allowed(handoff)` blocks optimization; `conversation_outcomes:HANDOFF` outcome. **Missing persisted state:** no `handoff_by_creator[creator] = {status, reason, at, automation_restricted}` in `user_profiles`; no explicit `automation_restricted` flag checked before `enqueue_send`; operator notified only via `suggestion.created`+`operator_queue` generic, not handoff-specific; conversation can resume (no `automation_restricted` so next inbound would still generate); commerce state survives (offers table), memory survives (user_profiles). So partial — automation not yet hard-restricted post-handoff.

---

## 9. Failure Classification Forensic (Capability F)

Scattered `try/except` without taxonomy:
- `generate_draft` try→fallback Ollama on gemini quota, else empty string → operator queue (retryable vs permanent not distinguished)
- `extract_commerce_signals` → None on fail → falls back to minimal objective (degraded)
- `score_draft` LLM fail → 0.0 fail-closed (degraded)
- `publish_event` → best-effort log, not classified
- `build_conversational_commerce_state` inner try per DAO call → neutral defaults on DB fail (degraded)
- `db/postgres: get_user_profile` → {} on fail (degraded)
- `persist_exposure` → fallback memory (degraded)
- Telegram send: `workers/send_worker` handles `Invalid peer → DLQ + ACK (permanent)` vs `transport → retry` (retryable) — **wired** for S but not for llm_worker path classification.
- No `get_strategy_evidence` failure → {} (degraded) but not labeled RETRYABLE/PERMANENT/DEGRADED/HANDOFF_REQUIRED.

**Need:** deterministic `FailureClass` enum and mapping per dependency failure type; learning failure must not break sending (existing `try` already does), commerce authority failure must fail closed (existing), but not labeled.

---

## 10. Degraded-Mode Matrix (Capability G)

Desired fallbacks not explicitly defined as matrix — current implicit:
- Qwen unavailable → `generate_draft` returns "" → `empty_draft` → operator_queue (safe fallback, not hallucinated commerce) — **implicitly correct** but not as documented degraded mode.
- Commerce intelligence unavailable → `build_conversational_commerce_state` catches → minimal objective via `derive_commercial_objective(selection)` — **implicit degraded**.
- Memory unavailable → `retrieve_relevant_memories` try→[] → context continues without memory — **implicit degraded**.
- Adaptive unavailable → Not explicitly handled; fallback would be `select_strategy` legacy SAFE_DEFAULT (existing) — **implicit**.
- DropFans unavailable → `list_valid_products` → [] → `has_relevant_product=False` → `offer_readiness NOT_READY` → no offer, no fabricated price — **correct degraded** but not documented.
- Telemetry unavailable → `TelemetryCollector.record` catch log — **implicit degraded**.

**Gap:** Not as explicit bounded matrix with tests; behavior exists but not auditable.

---

## 11. Experiment Governance Forensic (Capability H)

Framework exists (`Experiment` id/creator/family_allocation/eligibility/start/end/status, `deterministic_assignment`, `assign_variant`, `experiment_safe_to_apply` forbids price/product, `is_active`, `register_experiment`, `disable_experiment`, `persist_experiment` sentinel). **Missing governance:** minimum exposure threshold (5/10 not enforced per variant), minimum outcome threshold, fan-scoped stable well but variant persistence only in-memory (+ sentinel JSONB not queried per-turn), outcome attribution per variant (exposures logged but not aggregated per variant), confidence requirement (Beta not checked before promotion), automatic regression→rollback (detect_regression exists but not wired to `disable_experiment`). So **PARTIAL**.

---

## 12. Root Causes (P0/P1/P2/P3/EXTERNAL)

| Level | Root Cause |
|-------|------------|
| **P0** | No silent safety defect; but policy gate not unified → risk of future bypass if new objective added without updating both before/after gates (maintenance root). |
| **P1** | **Duplicated decision path:** conversation_state/response_mode/question_policy derived in both `memory/context` and `llm_worker` bridge; no single `ConversationOperationDecision` authoritative object → enterprise observability/recovery/escalation lack single anchor. |
| **P1** | **Pressure budget missing:** commercial pressure is implicit via `temperature + offer_readiness + cooldown` but not as explicit `pressure_score 0..1` → cannot gate `re-engagement`, `strategy repetition`, `offer frequency` uniformly. |
| **P1** | **Failure taxonomy missing:** scattered best-effort `try` without `RETRYABLE/PERMANENT/DEGRADED/HANDOFF_REQUIRED` labels → degraded modes work but not auditable/testable. |
| **P1** | **Handoff not persisted as restricted state:** `HUMAN_HANDOFF` objective blocks this turn only; next inbound resumes automation (no `automation_restricted` flag). |
| **P1** | **Strategy rollback not wired:** regression detection disconnected from experiment/evidence confidence reduction. |
| **P1** | **Observability gap:** pressure, risk_state, operation_allowed/block_reason, failure_class, extended decision_trace missing → operator cannot answer “why” without logs. |
| **P2** | Re-engagement not pressure/fatigue/max-frequency governed; relationship vs commerce metrics not persisted; lifecycle 15-state not explicit enum. |
| **P3** | Open-loop already wired; product_knowledge file absent (covered by alternative). |
| **EXTERNAL** | DropFans buyer grant `downloadUrl` still not exposed — delivery remains externally blocked (preserve). |

No `P0` blocking launch; `P1` enterprise hardening required before autonomous operations at scale.

---

## 13. Recommended Stage B Minimal Changes (Not Yet Executed)

- Create `commerce/conversation_operations.py` with `ConversationOperationDecision` composing existing `objective, next_best_action, strategy (hierarchical), pressure_score, risk_state, response_mode, question_policy, experiment_id/variant, allowed, blocking_reason, handoff_required, decision_trace` — compose, not duplicate.
- Implement `CommercialPressureBudget` (pure, deterministic) from existing signals, lifecycle-aware, bounded 0..1.
- Implement `RiskState SAFE/CAUTION/SUPPRESS/HANDOFF` gate before Qwen.
- Implement `FailureClassifier` enum RETRYABLE/PERMANENT/DEGRADED/HANDOFF_REQUIRED per dependency.
- Persist `handoff_by_creator` JSONB restricted flag.
- Wire `strategy rollback` via `detect_regression → confidence reduction → SAFE_DEFAULT`.
- Extend `core/telemetry.py` with 6 Phase 21 fields (`operation_allowed, operation_block_reason, pressure_score, risk_state, handoff_required, failure_class, decision_trace`) compact.
- Govern re-engagement via pressure/fatigue/max-frequency.
- No migrations (reuse JSONB), no new workers/queues, no new LLM calls.

---

## 14. What Must NOT Change

Redis Streams/consumer groups/XAUTOCLAIM, 3 workers, PostgreSQL/raw SQL, Telethon, DropFans sole authority, DLQ/dedup/rate limiting, creator isolation, single-pass 1/1/1, LLM language-only, Phase 20 adaptive layer preserved.

---

## 15. Forensic Verdict

```
PHASE 21 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY

CONVERSATION OPERATIONS: PARTIALLY UNIFIED (no single decision object, duplicated response_mode/question_policy)
COMMERCIAL PRESSURE: MISSING
ANTI-SPAM: PARTIAL (product/family fatigue wired, strategy/question/offer/CTA window not comprehensive)
RISK GOVERNANCE: PARTIAL (authority gates split, no unified RiskState before Qwen)
STRATEGY GOVERNANCE: WIRED (hierarchical + Beta + fatigue) BUT NOT PRESSURE/RISK/REGRESSION-WIRED
EXPERIMENT GOVERNANCE: PARTIAL (stable hash + safe fields, no thresholds/rollback/attribution)
HUMAN HANDOFF: PARTIAL (objective priority 1, no persisted restricted state)
FAILURE RECOVERY: PARTIAL (best-effort try, no taxonomy)
DEGRADED MODE: PARTIAL (implicit fallbacks, no explicit matrix)
LIFECYCLE: PARTIAL (desire 0-8 + objectives 14 via implicit, no explicit 15-state enum)
RE-ENGAGEMENT: PARTIAL (48h + aftercare/cooldown wired, not pressure/fatigue)
OPEN LOOPS: WIRED
OBSERVABILITY: PARTIAL (12 fields, missing pressure/risk/allowed/trace)
DECISION TRACE: PARTIAL (strategy_trace only)
CREATOR ISOLATION: PRESERVED
COMMERCE AUTHORITY: PRESERVED
DROP FANS: SOLE AUTHORITY (externally blocked delivery preserved)
LLM AUTHORITY: LANGUAGE ONLY
SINGLE-PASS: 1 SIGNAL + 1 QWEN + 1 SCORING — PRESERVED
TELEGRAM RELIABILITY: PRESERVED (stream/group/XAUTOCLAIM/dedup/DLQ)
ARCHITECTURE: NO REDESIGN
NEW WORKERS: NONE
NEW QUEUES: NONE
MIGRATIONS: NONE
```

Next: Stage B minimal enterprise implementation (compose, not rewrite) — only missing/high-value P1s.

