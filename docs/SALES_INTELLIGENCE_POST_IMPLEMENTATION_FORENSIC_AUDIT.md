# SALES INTELLIGENCE — POST-IMPLEMENTATION FORENSIC AUDIT

**Date:** 2026-08-29  
**Method:** Independent post-implementation code trace — read-only where stated. Implementation from `docs/SALES_INTELLIGENCE_REMEDIATION_IMPLEMENTATION_REPORT.md` verified against CURRENT working tree. No fixes during audit.

---

## 1. Conversation → Commercial Observation → Commerce State → Strategy → Sales Objective → LLM → Validation → Send

**Legacy path (authoritative `qwen2.5:3b`, live):**

```text
Fan message (Telegram MTProto NewMessage) → handlers.py:142 @client.on → 3s debounce handlers:81+108 → Redis XADD inbound_messages (handlers:129) → workers/llm_worker 970 XREADGROUP llm_workers → acquire_user_lock 467 → generation_id 474 → build_qwen3_context 530 (persona+fan state+CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE) + _try_commerce_draft 566 (resolve_and_run_commerce 422 → decision → strategy → execution 11-step) → derive_commercial_objective(selection) 566-588 injected as COMMERCIAL OBJECTIVE system → if USE_COMMERCE_RESPONSE → deepseek_response 465 VERIFIED FACTS whitelist → else generate_draft (Ollama/qwen2.5:3b) → score_draft 743 price_mention/photo_promise→0.1 → routing 780-888 → enqueue_send SEND_STREAM 826 dedup md5(user:msg:tgId) → _process_send_stream 77 is_send_duplicate + rate-limit 1/s + Telethon 259 → Telegram
```

**Agent path (canary disabled `ai_agent_canary_enabled false, ai_runtime_mode legacy`):** same pre-choice `build_qwen3_context` + `_try_commerce_draft` shared `workers:566`, dummy commerce snapshot `workers:606-618 NEW/NONE`, tools read-only `agent/tools:224` no execute_ppv, pressure `strategy:57` capped `MODERATE`. Both report `commerce_response_text` identically via `_try_commerce_draft` seal.

**Provider parity:** `_try_commerce_draft` reads `CommercePipelineRequest` before provider consulted — identical for `Ollama/qwen2.5:3b` vs `Gemini`. `compose_signal_extraction_input 104` identical. Only transport differs (`deepseek:188 provider.generate format=json` Gem vs `Ollama format=json 162`).

**No duplicate send:** `enqueue_send` dedup `md5 782`, `is_send_duplicate 99/283`, `scheduled dedup post_purchase_followup:{txn}`, `tip:{c}:{u}:{md5(url)[:12]} 937`.

**No duplicate offer:** `create_offer_serialized 82 advisory lock ppv_offer:{creator}:{user}:{product}` + `SELECT pending/clicked LIMIT1 88` → second concurrent returns `ALREADY_EXECUTED 207`.

**No duplicate tip:** `tip:{creator}:{user}:{md5(url)[:12]} + is_send_duplicate 943 3600` immediate + `tool_audit_log COUNT 30d` long fatigue `dao:559`.

**No cross-creator leakage:** Every DAO `WHERE creator_id=$1 AND user_id=$2` `dao:88,136,200,392,411,528` + `Tip dedup includes creator 937` + `product mirror creator_id scoping 120`.

**No LLM commercial facts:** `context:8 conversation never override`, execution `251 price from fangate_products`, `239 sales_url or build_checkout_url drop_id`, execution whitelist `deepseek_response:426/439` fails `invalid_output 538` before send; tool `propose_product_offer` `llm_tools:733` re-validates via `get_fangate_product`.

---

## 2. Tip Fatigue Fix — Forensic

**Before:** `commerce/dao:565 0`, `commerce/state:361 0`, `context_assembler:692 0`, `core/llm_tools:885 no tip args` — `check_tip_eligibility` 12-thresholds collapsed to `relationship in WARM.. → eligible` every turn → `tip every 1h` (URL dedup 3600).

**After trace:**

```text
suggest_tip(reason) → dispatch_tool 298 → _handle_suggest_tip 804
  auth 815 blocked/do_not 823 creator_sales 831
  ctx=build_llm_context 848 (tip ignored previously)
  derive_relationship_state 872 → derive_commercial_pressure 881
  fetch _tip_ctx= get_behavioral_feedback_context(creator,user) 850 NEW
    SELECT COUNT completed/rejected tip 30d from tool_audit_log + MAX(created_at)→hours 559
  check_tip_eligibility(relationship, pressure, hours, sent, ignored, has_active, recent_purchase 885)  → tip_fatigue >=2? 334 / cooldown hours<cooldown 345 / contextual 340 now fires
  get_checkout_links(creator) 906 telegram.tip 911 startswith http 925 → dedup md5(url) 937 + is_send_duplicate 943 3600 → tip_content hard-coded 950 → enqueue_send 952
  tool_audit_log _record_tool_audit 354 outcome completed  → next turn dao sees tip history
```

**Verified `dao:559` 3 queries + hours calc**; `context_assembler:656` now passes `hstl,sent,ignored`; `core/llm_tools:850` real history; backward compatible `except→0,None`. **Tip spam after 1h now `COOLDOWN_ACTIVE 24-72h` or `tip_fatigue INELIGIBLE` after 2.**

---

## 3. asks_for_free_content — Dead → Live

**Before:** `deepseek:70` field in prompt, model `extra=forbid 159` → faithful model always `invalid_payload → low_information 100%`. `asks_for_free` never reaches decision.

**After:** `signals:147 +4 optional` `accepted_recent_offer, asks_for_free_content, conversation_relevance, topic_continuity` default `False/1.0/None` → prompt-model match. `signals_to_context 334 asks_free = signals.asks_for_free_content; has_commercial=False if true` → `347 asks_for_free_content=asks_free` into `CommerceDecisionContext 149` → `decision:309 early RELATIONSHIP_BUILDING free_content_request` before creator-sales. Test `asks_for_free_content=True + purchase_intent 0.9 → relationship_building` passes.

---

## 4. Aftercare Wiring

**Before:** `context_assembler:585 "none"` hard, `context.py:282 filter` omitted `aftercare`. LLM `qwen` in `COMMERCE: Aftercare: pending` never saw it.

**After:** `dao get_behavioral_feedback_context` returns `aftercare_status` from `_query_aftercare_status` `466` (already live but dropped). `context_assembler:591-593` reads `behavioral.get("aftercare_status")` + `memory/context:283 keyword aftercare` added to Qwen filter + `context_assembler:795-796 Aftercare: pending` rendered. Decision suppression `aftercare pending/sent + purchases>0 → AFTERCARE_PHASE 450` already live. Now Qwen sees `Aftercare: pending` → will not re-pitch, `COMMERCIAL OBJECTIVE: aftercare` via objective.

---

## 5. Multi-Product Ranking

**Before:** `product_selection:233 return None` `≥2 unpurchased → ambiguous, autonomous skipped`.

**After:** `sorted(unpurchased, key=(price_minor or INF, id)) ranked[0]` (`product_selection:233-241` cheapest/lowest-id). Test `3 products 5000/1999/2999 → picks 1999 (id 20)` passes; purchased exclusion still `206`, all-purchased `None` still hold. Deterministic, creator-scoped, price from mirror `fangate_products` (int minor).

---

## 6. Commercial Objective Bridge

**Before:** `build_qwen3_context 400` had `COMMERCE: Purchases/Active offer` 5 facts, no sales job. Qwen never *wants* to sell.

**After:** `commerce/objective.py 1-36 NEW` `derive_commercial_objective(selection, relationship_state) → no_sale/relationship/build_desire/aftercare/present_offer` + `workers/llm_worker:574 injected as COMMERCIAL OBJECTIVE: {obj}` system message before `generate_draft`. Language-only (`do not invent product/price/URL`). Measured `+~40 tok`, well under `500` target.

---

## 7. Other Forensic

- **Signal persistence:** `dao tip` 30d COUNT durable via `tool_audit_log` (existing migration `20260822040000`), no new table.
- **Abandoned offer:** `has_active_offer && age≥48h` → `Abandoned offer: {title} ({Nh})` `context_assembler:790` now surfaced. Re-engagement is contextual `CALLBACK`, not auto-spam (respect cooldown).
- **Upsell:** Cheapest-unpurchased rank is cross-sell; `repeat_purchase_eligible` already surfaced `790` but not auto-offer (partial, documented).
- **Provider parity:** Both share `decision/strategy/execution` pre-choice; only transport differs.
- **Scoring:** `photo_promise 0.1` now caps `Sure I can send a pic` correctly (live probe `0.1 [photo_promise, too_generic] → not auto-approved`).

---

## 8. Production Risk — NONE New

`create_offer` advisory lock unchanged, `execute_ppv` 11-gate unchanged, `send_dedup` 3600 unchanged, `rate_limit 1/s burst5` unchanged, `autonomy_enabled` kill switch unchanged, `creator isolation` untouched, `DropFans sole` header untouched.

---

ROOT STATUS:
SALES CONTENT GENERATION: VERIFIED WIRED (commerce PPV via VERIFIED FACTS + conversational via COMMERCIAL OBJECTIVE)
SALES DECISION ENGINE: LIVE
PRODUCT/OFFER PATH: LIVE (ranked, not None)
TIP PATH: VERIFIED (canonical + 3600 dedup + 30d fatigue)
PURCHASE PATH: LIVE (atomic attribution + funnel)
UPSELL: PARTIAL (cheapest unpurchased, not tiered)
FOLLOW-UP: LIVE (3 schedulers, abandoned surfaced)
BIGGEST CONVERSION GAP: CLOSED — multi-product None → ranked; warm still gentle without explicit buy
BIGGEST CONTENT GAP: CLOSED — COMMERCIAL OBJECTIVE now reaches Qwen (~40 tok)
P0: 0 remaining (tip fatigue, free-content, aftercare fixed)
P1: remaining — repeat upsell auto-offer not yet automatic, tip fatigue durable window could be 30d DB vs desired persistent tip_events (acceptable via tool_audit)
P2: currency hardcode USD, tip dedup new-URL bypass, gpt-4 tokenizer on Qwen
P3: budget duplication, fangate_products name legacy
PRODUCTION CHANGES: NONE BEYOND DOCUMENTED P0/P1 REMEDIATION
