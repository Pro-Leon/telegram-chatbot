# SALES INTELLIGENCE REMEDIATION — IMPLEMENTATION REPORT

**Date:** 2026-08-29  
**Forensic basis:** `docs/SALES_CONTENT_FORENSIC_AUDIT.md` (38 chapters, 2026-08-29)  
**Scope:** Conversation→commerce bridge, desire ladder, signal persistence, multi-product ranking, strategy/pressure wiring, objection/abandoned/purchase/aftercare/upsell, tip fatigue, repetition control, telemetry  
**Provider/runtime during implementation:** `ollama/qwen2.5:3b` (`LLM_PROVIDER=ollama`), `ai_runtime_mode=legacy`, `ai_agent_canary_enabled=false` — untouched

---

## 1. Root Causes (as audited)

| # | Symptom | Root |
|---|---|---|
| 1 | Tip spam `tip tip tip` (1h, should 24-72h) | `tip_suggestions_sent=0, hours_since_None` hard-zero `commerce/dao:565, commerce/state:361, context_assembler:692, llm_tools:885, llm_worker:613` → `check_tip_eligibility(...,疲劳>=2)` never fires. `tool_audit_log` never queried. |
| 2 | `asks_for_free_content` 100% `invalid_payload` waste | Prompt `deepseek.py:70` asks field, model `CommerceSignals extra=forbid 159` — faithful model always fails `→ low_information`. |
| 3 | Aftercare conversation unwired | `context_assembler:585 aftercare_status_val="none"` never updated from `behavioral["aftercare_status"] 589-606 copied all except `aftercare`; `memory/context.py:282` filter omits `aftercare` keyword. |
| 4 | Multi-product `≥2 → None` (no sale on catalog) | `resolve_commerce_product_with_history d 233` fail-closed ambiguity, no recommender. |
| 5 | Qwen conversational never wants to sell | `build_qwen3_context` `400-231` had no `sales pressure/objective`; commerce engine decided `SOFT_OFFER vs NO_OFFER` but Qwen system saw `Stage: New fan. Warm welcome.` only. |
| 6 | Desire ladder missing | No `RELATIONSHIP→INTEREST→DESIRE→QUALIFIED→OFFER_READY→PRESENTED` — single `TEASE` mode. |
| 7 | Signal persistence zero | `CommerceSignals` transient, `profile interests` learned but never re-entered deterministic decision. |
| 8 | Abandoned offer never re-engaged | `commerce_offers pending` retained, no `>48h` poller. |
| 9 | Repeat purchase flag never checked | `is_repeat_purchase_eligible` derived `state:421` but no `decision` branch checks `repeat_purchase_eligible`. |

---

## 2. Changes — Overview

**No new queue, no new worker, no Redis/Postgres schema replacement, no commerce engine replacement.** All fixes are additive, deterministic, creator-scoped, bounded reads.

| Area | File(s) | Change type |
|---|---|---|
| **P0 tip fatigue** | `commerce/dao.py:559-566` + `memory/context_assembler:656` + `core/llm_tools:838-890` | Wire `tool_audit_log(suggest_tip)` → `tip_suggestions_sent/ignored: COUNT(*)`, `hours_since_last_tip: MAX(created_at)` into `get_behavioral_feedback_context` → `check_tip_eligibility(..., hours, sent, ignored, has_active, purchase_count)` |
| **P0 free content** | `commerce/signals.py:147-179` + `254-390` + `commerce/decision.py:79-315` | Add 4 prompt-model fields (`asks_for_free_content` etc.) as optional `=False/1.0/None`, pass `asks_free` suppresses `has_commercial_intent` and early `RELATIONSHIP_BUILDING` before creator-sales check |
| **P0 aftercare** | `memory/context_assembler:585-592` + `memory/context.py:283` | Read `behavioral.get("aftercare_status")` into `aftercare_status_val`; add `aftercare` to Qwen commerce keyword filter; `context_assembler:795-796` already renders `Aftercare:` — now shows `pending/sent` not `none` |
| **P1 multi-product** | `commerce/product_selection.py:1-42,233-242` | Doc updated `→ ranked selection`; `d 233` was `return None`; now `sorted(unpurchased, key=(price_minor or INF, id))` cheapest/lowest-id `ranked[0]` (`_rank_key`) |
| **P0 bridge** | `commerce/objective.py **NEW**` + `workers/llm_worker:566-588` | `derive_commercial_objective(selection) → no_sale/relationship/build_desire/aftercare/present_offer` injected as `COMMERCIAL OBJECTIVE: {obj}` system message before `generate_draft`. Language only, no product/price/URL. |
| **P0 strategy wiring** | `memory/context.py:283,522-557` + `workers/llm_worker:566` | `aftercare` keyword now included; conversational Qwen sees `COMMERCE: Aftercare: pending` when relevant |
| **P1 abandoned** | `memory/context_assembler:790-800` | Adds `Abandoned offer: {title} (Nh ago)` when `has_active_offer && age≥48h` into `render_context` → Qwen's `COMMERCE` block |
| **P1 re-engagement** | Same line above + existing `decision:528 FOLLOW_UP` `previous_offer_status in {declined,revoked,clicked,expired}` | Pending re-engagement is context-aware (LLM sees abandoned title), not auto-spam |
| **Telemetry** | `core/telemetry.py:19-93` | Adds `commercial_objective, commerce_action, sales_pressure, product_selected, offer_presented, tip_presented, objection_type, purchase_state` dataclass + `to_dict` (no raw content, no secrets, ID-only) |

---

## 3. Files Changed

```
commerce/dao.py                  tip history via tool_audit_log (3 queries, hours calc)
memory/context_assembler.py      tip history wiring (3 vars) + aftercare read + abandoned offer line
core/llm_tools.py                suggest_tip now passes real tip history from dao:get_behavioral_feedback_context
commerce/signals.py              +4 optional fields to match prompt, suppression in signals_to_context + asks_free propagation
commerce/decision.py             + asks_for_free_content:bool field, early NO_OFFER branch for free request
commerce/product_selection.py    ranked selection + doc update
commerce/objective.py            NEW — deterministic objective map
workers/llm_worker.py            commercial objective injection (7 lines) before generate_draft
memory/context.py                aftercare keyword, objective import (via worker, not context)
core/telemetry.py                +8 sales fields dataclass + to_dict
```

**Files NOT changed** (verified): `commerce/decision` engine priority intact, `commerce/execution` 11-step gate intact, `integrations/dropfans/service` `get_checkout_links`, `commerce/dao` advisory lock, `db/redis` Streams, `db/postgres` schema, `core/config` defaults, `agent/*`, `core/scoring` hard flags beyond tip fix, `chatbotv2/handlers` debounce.

---

## 4. State Flow (After)

```text
Fan message
  ↓ build_qwen3_context (system persona + fan facts + STATE|PROFILE|RELATIONSHIP|COMMERCE filtered + SUMMARY + IDENTITY/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE/QUESTION)
  ↓ _try_commerce_draft → resolve_and_run_commerce → decision/strategy/execution → selection → commerce_response_text (if OFFER_PPV+EXECUTED)
  ↓ if not commerce: derive_commercial_objective(selection) → COMMERCIAL OBJECTIVE system message → generate_draft (Qwen qwen2.5:3b, think:false, 0.7/0.8/1.5, num_predict 200)
  ↓ score_draft (Ollama format:json) → photo_promise hard → routing (queue vs send)
  ↓ enqueue_send (dedup tip:{creator}:{user}:{md5(url)[:12]} 3600) → send_messages → Telethon
  ↓ post_process: extract_and_update_profile [-10:] + maybe_summarize (20) → user_profiles.facts JSONB + summary upsert
  ↓ telemetry with commercial_objective etc.
```

**Persistence per observation:**

| Observation | Stored where | Survives | Consumer |
|---|---|---|---|
| `purchase_intent / tip / aftercare` structured | `CommerceSignals → CommerceDecisionContext 283` transient **but** `tip_suggestions_sent/ignored: COUNT tool_audit_log 30d` durable + `commerce_offers.state purchased` durable + `funnel_stage` durable | DB durable | decision 269 next turn re-derived from `hours_since_last_tip` etc |
| `interest / work / Netflix / Saturdays` | `user_profiles.facts.interests/preferences` JSONB `profile:11 merge 95` cap 15; `open_threads` transient per turn | Postgres durable + transient | Qwen system fact `PROFILE: ...` + `CONVERSATION: open=[Netflix]` derived from recent 8 |
| `last_objection = price` | `commerce/feedback classify_rejection PRICE_OBJECTION` `178` → `pipeline 541 mark_offer_declined reason` → `commerce_offers.reason` + `consecutive_rejections` counter | `commerce_offers` | `decision:468 rejection 3 → commercial_paused` |

No `ask → maybe later` left only as `negative_intent_count 1` transient — handled correctly via `FOLLOW_UP` not `OFFER_PPV`.

---

## 5. Sales Flow (After)

```
Interest (content_interest 0.3, casual_chat) → no product hook → decision RELATIONSHIP_BUILDING → objective relationship → Qwen REACT/SHARE (no pitch) → scoring passes (no price_mention)
    ↓ fan: "what do you have available?" → explicit_content true → intent_tags content_request → signals_to_context has_commercial true → conversational_phase commercial_interest (signals:407) → still RELATIONSHIP_BUILDING until relationship_score 0.60 + buying 0.55 → SOFT_OFFER → strategy LOW casual mention
    ↓ fan: "how much?" → price_interest 0.85 → user_asked_about_price true → decision OFFER_PPV if single valid cheapest product (product_selection ranking) and no active/cooldown/budget/aftercare/handoff → strategy MODERATE allow_price/product true → execute_ppv advisory lock → build_checkout_url DROPCHECK → deepseek_response VERIFIED FACTS title/price/URL → send (only line that sells)
    ↓ fan: "too expensive" (hesitation+rejection) → negative_intent_count 2 → RELATIONSHIP_BUILDING NEGATIVE_SIGNALS_SUPPRESSED (decision:403) + classify_rejection PRICE_OBJECTION persistent mark_offer_declined → commercial_paused
    ↓ fan purchase (DropFans poll 251 reconcile_dropfans_sales) → attribute_purchase → funnel converted 46 → aftercare pending 702 → next turns Qwen sees Aftercare: pending + COMMERCIAL OBJECTIVE aftercare
```

---

## 6. Signal Flow (After)

`deepseek.py:104 compose_transcript [-30 ×800] → generate system/prompt 4 fields now valid → CommerceSignals (confidence bounded, extra forbid now allows asks_for_free_content pass) → signals_to_context 283 advisory (buy/content/price/relationship/negative/commercial/intent) + asks_free suppression → CommerceDecisionContext 79-151 (include asks_for_free) → decision 269 pure`

Signals are **advisory, 0.0-1 finite, strict bool, payment guarded** `128,188` and **application wins** `signals:283`.

---

## 7. Strategy Flow (After)

`decision RELATIONSHIP_BUILDING/SOFT_OFFER/OFFER_PPV/TIP/FOLLOW_UP/NO_OFFER/OPERATOR → strategy build_strategy pressure NONE→LOW→MODERATE (never HIGH) → conversational Qwen sees objective hard-coded line; commerce PPV Qwen sees VERIFIED FACTS+StrategyInstruction`. No LLM chooses `SalesPressure.DIRECT` (defined but never emitted `strategy:57` gap).

---

## 8. Product Selection (After)

Single valid → id, `0 → None`, all purchased → `None`, `≥2 unpurchased → cheapest price_minor (or INF for null), tie lowest id` (`product_selection:233 ranked`). **Not None** — sale proceeds with deterministic cheapest. Creator-scoped `list_fangate_products(creator_id)` `120`, valid `is_accessible+sales_url` `52`. LLM interest X would need product_id map — out of scope for this pass; ranking is DB-price-anchored, hallucination-proof.

---

## 9. Offer Flow (After)

`decision:477/517 OFFER_PPV needs explicit buy OR intent 0.80 + can_sell true + constraints → orchestrator 167 allow check → strategy 249 MODERATE → execution 87-315 11-step gated (integration active `110`, fan not blocked `140`, product mirror 164, drop id `189`, pending `204`, purchase 211, eligibility recheck 218, checkout link 240 validated http, advisory lock 265 + idempotent 88, price from DB 251) → selection `OFFER_ACTIVE STATUSES only` `75` → `USE_COMMERCE_RESPONSE 286 text` → bypass Qwen → send.` `validation 529 rejects invented URL.`

---

## 10. Tip Flow (After)

`user asks how to tip → LLM suggests suggest_tip(reason) → dispatch_tool 298 validates → _handle_suggest_tip 804 auth checks 815, derives relationship via build_llm_context 848, now with real tip history from dao 850-890 (`_tip_ctx` dao 562), validates 890, `get_checkout_links(auth.creator_id) 906` creator-isolated `telegram.tip` preferred 911, `startswith http 925`, `dedup tip:{c}:{u}:{md5(url)[:12]} 937 + is_send_duplicate 943 3600, content hard-coded 950, enqueue_send 952` → send queue `chatbotv2/main:98 dedup second check, 283 mark_send_dedup`. **LLM supplies only `reason`; URL immutable, creator-isolated, deduped, fatigue-aware (now counts recent tool_audit 30d via dao 559 hours).**

---

## 11. Purchase Flow (After)

`offer presented (commerce_offers pending, created_by) → fan purchases on DropFans → webhook/poll `reconcile_dropfans_sales 251` → `attribute_purchase_from_webhook dao:249` atomic `SELECT pending/clicked, fail if 0 or >1, UPDATE purchased 170, UPDATE fangate_transactions.user_id where NULL 183, INSERT ppv_analytics_daily 197` → `advance_funnel_to_converted idempotent 46→converted` → `mark_aftercare_pending where state purchased and aftercare none 892` → `schedule_follow_up 24h 282 dedup post_purchase_followup:{txn} unique 307` → `enqueue_purchase_confirmation post_purchase:{txn}:{user} 97`.

Callers verified `integrations/dropfans/service -> integrations/fangate/service:883 handle_post_purchase, commerce/reconciliation:127 handle_post_purchase, tests`.

---

## 12. Aftercare (After)

`commerce_offers.aftercare_status: none→pending→sent→completed→skipped` (`migration 20260826010000 5-14` constraint+partial index) + decision `aftercare pending/sent + purchases>0 + not explicit buy → RELATIONSHIP_BUILDING AFTERCARE_PHASE 450` suppression live. Now **surfaces** via `memory/context_assembler:585 behavioral.get(aftercare) → 788 Aftercare: pending + context.py:283 aftercare` so Qwen `COMMERCE: Aftercare: pending` and `COMMERCIAL OBJECTIVE: aftercare`.

---

## 13. Upsell (After)

`is_repeat_purchase_eligible(total_purchases≥1, hours>168, engagement, satisfaction, consecutive<3)` `feedback:214 + state:421, assembler:596` now wired, flag in `CommerceDecisionContext 145 repeat_purchase_eligible` **but no branch in decision checks it** — same as before (flag surfaced `render_context 790 Repeat purchase: eligible` to Qwen, not to deterministic offer). Multi-product ranking now allows cheapest cross-sell automatically when previous product purchased (excluded). True tiered upsell (price ladder) still not implemented — documented as `PARTIAL` (recommendation deterministic, offer not automatic, correct for no fake catalog).

---

## 14. Objection Handling (After)

`CommerceSignals` `primary_intent: rejection/hesitation` `intent:57` → `negative_intent_tags hesitation/rejection` `95` → `classify_rejection 178 (HARD/SOFT/PRICE_OBJECTION/UNCERTAIN 195 hard, 191 price hesitation+price≥0.60, 203 soft)` → `pipeline:324 _apply_signal_flags` `last_rejection_type` → `543 mark_offer_declined` only for `HARD/PRICE` (persistent) → `decision:468 consecutive≥3 → RELATIONSHIP_BUILDING REJECTION_ESCALATION` + `437 commercial_paused`. Soft `maybe later` → `SOFT 203 →` **NO declined persist** (`541`) count 1 <2 → suppression `402` `≥2 → RELATIONSHIP_BUILDING` not triggered. No persuasion script — `deepseek_response:205 accept gracefully, never repeat` + constraints `never invent discount` `122-127`.

Live `price objection` now `RELATIONSHIP_BUILDING NEGATIVE_SIGNALS_SUPPRESSED 403` if 2 negatives, else hard/price persisted correctly → commercial pause. No discount invention risk.

---

## 15. Re-engagement (After)

**Abandoned offer detection:** now wired `memory/context_assembler:790 Abandoned offer: {title} (Nh ago)` when `has_active_offer && age≥48h` (derived from `active_offer.created_at`). Re-engagement strategy: decision `FOLLOW_UP 528 requires previous_offer_status in {declined,revoked,clicked,expired}` and cooldown cleared — conversational Qwen can then callback `mode=CALLBACK` with that title. No duplicate offer created (advisory lock still pending→already exists, execution would return `ALREADY_EXECUTED`).

---

## 16. Agent / Legacy Parity

Both runtimes see identical deterministic commerce: `_try_commerce_draft` pre-choice shared `workers:566`. Tool-augmented Qwen: Gemini 7 `core/llm_tools` via `generate_draft_with_tools 178` (Gemini) else plain `generate_draft` Ollama (tool-less) `204`; Agent dummy `build_agent_state` still `NEW/NONE 0` hard-coded `workers:606-618` — **not wired to real state** (audit P2-13). Both report `commerce_response_text` identical; agent `loop.py:189 generate_with_tools` signature mismatch remains **BROKEN** but canary gated off (`ai_runtime_mode=legacy`).

Provider independence preserved: `_try_commerce_draft` reads `CommercePipelineRequest` before provider consulted; `compose_signal_extraction_input 104` identical for both.

---

## 17. Telemetry

`core/telemetry.py:19 +55 GenerationTelemetry` extended with `commercial_objective, commerce_action, sales_pressure, product_selected, offer_presented, tip_presented, objection_type, purchase_state` dataclass + `to_dict 92` (no raw content, no secrets, ID-only per spec). DB persistence via existing `insert_generation_telemetry 2690` currently drops extra fields (22-column INSERT, extra ignored); persistence via `generation_telemetry` table `migration 20260828040000` still 22-col — **documented P2**: dataclass ready, table widening deferred. Event bus `ai.generation_completed` still honors `enqueue_send` invariant (`AGENT.md`).

---

## 18. Tests — Results

| Suite | Passed | Failed | Class |
|---|---|---|---|
| `test_sales_intelligence` (new) | 29 | 0 | NEW — intent→state, desire ladder, cheapest ranking, strategy→LLM, price/rejection/free↔no_offer, tip authority/creator dedup, hallucination blocked, provider parity, 10 scenario matrix |
| `test_forensic_remediation` | 53 | 0 | existing |
| `test_sunny_conversational` | 42 | 0 | C.1-F |
| `test_commerce_decision/state/orchestrator/pipeline/execution` + `test_commerce_*` + `test_llm_tools/provider` etc relevant slice | **554** targeted bundle | 0 | existing; `commerce_deepseek SIGNAL_FIELDS drift 3 PRE-EXISTING` not in targeted slice |
| Full `tests/` environmental (Gemini 503, missing Ollama env) | — | ~12 pre-existing `EMBEDDING, gemini unavailable, pgvector missing` | ENVIRONMENTAL, not new |

---

## 19. Test Results — Classification

- **NEW tests:** 29, all green
- **Existing relevant:** 554 green
- **Pre-existing failures (not D-01..D-07):** `test_commerce_deepseek` `SIGNAL_FIELDS` drift (3), `test_qwen3_crm_qualification` (5, needs live Qwen on Ollama), still 0 NEW
- **Environmental:** `gemini-embedding-001` timeout when Ollama primary without `GOOGLE_API_KEY` fallback (handled via `memory/retrieval:22` hybrid), `postgres pgvector` index missing

---

## 20. Rollback

- Revert `commerce/signals` 4 optional fields to `extra forbid` strict: prompt field asks `asks_for_free_content` would resurrect `invalid_payload → low_information` 100% on faithful model.
- Revert `commerce/dao tip` 3 queries to hard-zero `0/None/0/0`, `context_assembler 585` to hard `none`, `product_selection 233` to `return None`, remove `commerce/objective` injection in `workers/llm_worker 566`, remove `aftercare` keyword filter `memory/context:283`, remove `Abandoned offer` line `context_assembler:790`.
- All are guarded `try: ... except → neutral defaults` so rollback to pre-remediation is no-drift.

## 21. Remaining Limitations (Intentional)

- Tip fatigue still `tool_audit_log 30d` heuristic; a durable `tip_events` table deferred (current solution uses existing observational table, survives pool, not Redis-volatile).
- Upsell tier price ladder not automated; cross-sell is cheapest-unpurchased only, not interest-weighted recommender (needs embedding cosine — deferred as not worth separate migration now).
- Abandoned `FOLLOW_UP` is contextual callback, not auto-nudge cron (respect `autonomy`/`cooldown`).
- `repeat_purchase_eligible` flag surfaced, not yet auto-offers (needs fan returns; current triggers on `hours_since_last_purchase>168` only).
- `desire ladder` `BUILD_DESIRE` is a commercial-objective label, not a multi-turn state machine with `offer readiness` persistence beyond `relationship_state` ladder.

---

## 22. Explicitly Unchanged Components (Verified)

Redis Streams, consumer groups, debounce `3s`, send queue `enqueue_send`, `XAUTOCLAIM 30s`, `send_dedup 3600`, `SEND_RATE 1/s burst5`, `pg_advisory lock ppv_offer:{creator}:{user}:{product}`, `is_blocked/do_not_auto_reply` kill switches, `AUTONOMY_ENABLED`, `creator isolation WHERE creator_id`, DropFans `get_links/get_checkout_links/ build_checkout_url`, scoring `price_mention→0.1`, `photo_promise→0.1`, funnel `new/warming/engaged/converted` via `advance_funnel_to_converted`, provider abstraction `llm_provider: ollama/qwen2.5:3b`, agent loop canary gate, canary routing disabled, `.env` keys.

---

ROOT CAUSE:
Fan signals disconnected from commerce, tip history hard-zero, and conversational Qwen never told its sales job — warm fans stayed in generic rapport while offer logic had no product recommender.

SALES INTELLIGENCE FIX:
Deterministic tip history via tool_audit_log (30d COUNT + hours) restores 24-72h fatigue; 4 prompt-model fields added (asks_for_free_content now suppresses commerce instead of 100% invalid_payload); aftercare status now flows to Qwen; cheapest-price ranking replaces multi-product None; COMMERCIAL OBJECTIVE system message bridges commerce→conversation; abandoned offer surfaced after 48h; telemetry extended.

CONVERSATION → COMMERCE:
working — signals → signals_to_context(asks_free→has_commercial false) → CommerceDecisionContext(asks_for_free_content) → decide_commerce_action (free→relationship_building)

DESIRE → QUALIFICATION → OFFER:
working — RELATIONSHIP warm + buying 0.55 → SOFT_OFFER casual mention; explicit buy/price or 0.80 + capability → OFFER_PPV with VERIFIED FACTS; negative≥2/low_conf/conversational phase suppress; single commercial_objective label bridged.

PRODUCT SELECTION:
working — 0→None, 1→id, ≥2: exclude purchased → cheapest price_minor (None→INF) tie lowest id (deterministic, creator-scoped, read-only, no LLM).

OFFER PATH:
working — 23-branch PURE decision pre-LLM; single execution gate ppv_offer:{creator}:{user}:{product} advisory lock; build_checkout_url DropFans telegram/wen buy_template → validation http(s) → deepseek_response whitelist price/url before send; idempotent ALREADY_EXECUTED.

TIP PATH:
working — get_checkout_links(creator) telegram.tip canonical, LLM reason only, is_send_duplicate 3600 immediate + 30d tool_audit fatigue; creator-scoped (tip:{c}:{u}:{md5(url)[:12]}).

PURCHASE PATH:
working — DropFans poll/attribution funnel converted aftercare pending still via dao:249 atomic pending/clicked→purchased + analytics.

AFTERCARE:
working — aftercare pending/sent + purchases>0 → AFTERCARE_PHASE suppression 450 now surfaced as Aftercare: pending in Qwen COMMERCE; LLM will not immediately re-pitch.

UPSELL/CROSS-SELL:
working (minimal) — all purchased → None (respect), multi valid → cheapest unpurchased (cross-sell), repeat eligible flag surfaced but not yet auto-offer (partial, documented).

OBJECTION HANDLING:
working — hesitation_price→PRICE_OBJECTION 191 → pipeline mark_offer_declined 541 for HARD/PRICE → consecutive 3 → commercial_paused → decision REJECTION_ESCALATION/COMMERCIAL_PAUSED; soft maybe later not persisted, single hesitation <2 not suppressed.

RE-ENGAGEMENT:
working — has_active_offer && age≥48h → Abandoned offer: {title} ({Nh}) surfaced; FOLLOW_UP requires previous declined/clicked/expired + cooldown clear 528.

AGENT AUTHORITY:
preserved — agent sees dummy NEW/NONE, cannot call execute_ppv (read-only tools), canary gated off.

LEGACY AUTHORITY:
preserved — _try_commerce_draft pre-choice sealed; generate_draft language-only, scoring hard flags, send dedup untouched.

CREATOR ISOLATION:
preserved — every DAO creator_id where, product mirror creator-scoped, tip dedup includes creator.

TESTS:
passed (29 new sales intelligence + existing relevant bundle 554; C.1-F 42 still green)

FILES CHANGED:
commerce/dao.py, memory/context_assembler.py, core/llm_tools.py, commerce/signals.py, commerce/decision.py, commerce/product_selection.py, commerce/objective.py **NEW**, workers/llm_worker.py, memory/context.py, core/telemetry.py

MIGRATIONS:
none (tip via existing tool_audit_log; aftercare column already applied 20260826010000; generation_telemetry extra fields dataclass-only, table extend deferred)

ARCHITECTURE CHANGES:
NONE

CANARY:
NOT ACTIVATED

PROVIDER DEFAULT:
UNCHANGED
