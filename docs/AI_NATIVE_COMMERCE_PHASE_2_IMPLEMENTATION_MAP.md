# AI-Native Commerce — Phase 2 Implementation Map (Forensic, Read-Only)

**Date:** 2026-08-29  
**Forensic basis:** `docs/AI_NATIVE_COMMERCE_PHASE_1_FORENSIC_IMPLEMENTATION_MAP.md` (367 lines, 52 KB) — read fully, verified against CURRENT working tree (no code changed in this phase)  
**Method:** Independent trace of `workers/llm_worker.py, memory/context, core/*, commerce/*, integrations/*, db/*` as listed in §3; callers/callees followed; tests `commerce, sales intelligence, sunny conversational` inventoried. All file:line are **current**.

---

## 1. Existing Commerce & Conversation Path (Verified, Not Trusted)

`Telegram inbound (handlers 142) → debounce 3s (handlers 81, redis 277) → Redis XADD inbound_messages 171 → llm_worker 970 XREADGROUP llm_workers → process_message 467 user_lock 60s → build_qwen3_context 530 (persona+facts+STATE|PROFILE|COMMERCE 5 facts|SUMMARY 2 sentences|IDENTITY/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE/QUESTION) → _try_commerce_draft 566 resolve_and_run_commerce 422 (state→pipeline→decision 23-branch→strategy→execution 11-gate→selection 228) → COMMERCIAL OBJECTIVE system (objective.py NEW, worker 580 injected) → legacy/agent branch 684 vs tool loop (Ollama false) → generate_draft (Ollama/qwen2.5:3b, think:false, 0.7/0.8/1.5, num_predict 200) → score_draft 743 (Ollama format:json, hard flags price_mention/photo_promise) → routing 825 ≥0.80 no flags auto, else queue → enqueue_send SEND_STREAM 826 dedup md5(user:msg:tgId) → _process_send_stream 77 is_send_duplicate(99)+rate 1/s burst5+Telethon 259 → Telegram → post_process 889 profile[-10:]→update_user_profile + maybe_summarize 20`

Phase 1 left **authoritative invariants intact** (see §2).

---

## 2. Current Architecture (Unchanged Since Phase 1)

Postgres `users/messages/conversation_summaries/user_profiles(message_embeddings JSONB cosine, not pgvector)/operator_queue(vault_media_deliveries UNIQUE creator,user,fangate_media_id)/scheduled_messages(dedup_key unique)/fan_segments/tool_audit_log(30d tip history)/generation_telemetry 22-col` + Redis Streams `inbound_messages llm_workers / send_messages send_workers XAUTOCLAIM 60s/30s` + `send_dedup 3600` + Telethon 149.154.x. `DROP_FANS` sole (`Fangate 410`), `AUTONOMY_ENABLED true` pre-choice kill `workers 391`, `commerce/state read-only SELECTs only 15`, `fangate_products` mirror `SHA256(drop_id)%2^62` with `title, price_minor, sales_url, is_accessible, raw{cuid,vaultItemIds,salesCount}` (description & media_count dead).

---

## 3. Current Commerce Lifecycle (Verified)

`first fan msg → upsert_user funnel new → 42 msgs user 8151382101 still new (funnel never advances without purchase, advance_funnel_to_converted only) → LLM generation (relationship state NEW→ENGAGED via derive_relationship_state)→ commerce state (creator resolved, product_id NULL when ≥2 ambiguous now cheapest rank P1-01) → decision (23-branch pure) → strategy (pressure NONE/LOW/MODERATE never HIGH) → offer creation advisory lock ppv_offer:{c}:{u}:{p} → DropFans build_checkout_url telegram buy_template → validation http(s) → deepseek_response whitelist price/URL → send queue → purchase poll reconcile_dropfans_sales 251 + attribute_purchase 249 atomic pending/clicked→purchased → funnel converted 46 → aftercare pending 892 → schedule 24h 282 → vault synthesize fangate_media_id SHA256 → sales_url link delivery (per-item download_url not fetched) → telemetry GenerationTelemetry 8 sales fields (dataclass, in-memory, event bus preserved, table 22-col widening deferred).`

---

## 4. Current LLM Context (What Qwen Receives) — Verified Lossy but Compact

Order `memory/context 453`: `SYSTEM: {persona (trimmed You are sunny when established) + Fan: {first_name} {facts} + Stage: ... + 10 rules (2-4 sentences, never reveal AI, never promise photos, may have no question, prefer callbacks)}` `STATE: Fan|funnel` `PROFILE: 4 fan fields` `COMMERCE: 5 filtered facts` `AFTERCARE now included 283` + `SUMMARY 2 sentences` + `IDENTITY established + RULE Do NOT re-introduce` + `CONVERSATION topic= open=[netflix] last_q was_answered tone=` + `ABOUT SUNNY: cozy movie nights...` + `CAPABILITIES send_text:yes send_photo:no + NOTE` + `RESPONSE mode=` + `QUESTION allowed=` + `COMMERCIAL OBJECTIVE: relationship|build_desire|aftercare|present_offer` (`objective.py` bridge 580). **No** `Creator: Bella` (filtered), `purchase Title price` lines dropped, `vector relevant_memories` DEAD for Qwen path, `Creator name` not seen, full catalog not seen (only `Current product: title price` when single pending).

---

## 5. Current Sales Intelligence (Post-Sales-Remediation)

| Signal | Status | Reaches LLM as objective? |
|---|---|---|
| `purchase_intent 0-1 + price_interest` | decision `SOFT 0.55, STRONG 0.80, explicit 0.95` | **Yes** via `buying_intent → decision → objective build_desire → COMMERCIAL OBJECTIVE` |
| `tip_eligibility` | Now wired `dao 559 tool_audit 30d COUNT + hours` → `relationship:259 72/48/24×1.5` | `Tip eligible: yes — reason` surfaced |
| `negative_intent ≥2 → RELATIONSHIP_BUILDING 403` / `PRICE_OBJECTION 191` | `classify_rejection 178 (HARD/SOFT/PRICE)` persisted only for HARD/PRICE to `mark_offer_declined` | `Rejections: N consecutive` + `Commercial pause: active` lines |
| `aftercare pending` | DB + decision `450 AFTERCARE_PHASE` suppression live, now surfaced | `Aftercare: pending` → `COMMERCIAL OBJECTIVE aftercare` |
| `asks_for_free_content` | **Fixed P0-02**: 4 optional fields added `signals:174`, `has_commercial false` when true, `decision:309 free_content → relationship_building` before creator-sales | Yes, suppresses commerce |
| `multi-product` | Was `None` → `cheapest price, tie lowest id` rank (`product_selection:233`) | Deterministic, but not yet interest-weighted embedding |
| `abandoned offer` | `has_active_offer && age≥48h → Abandoned offer: {title} (Nh)` `context_assembler:790` | Yes, `CALLBACK` can reference |
| `repeat eligible` | flag derived `state:421` surfaced `render_context 790`, no auto-offer | `Repeat purchase: eligible` line only |

---

## 6-12. Desired New Pieces — Verified Gap + Concrete Design (Phase 2 Scope)

**6. Conceptual ladder (then formalized in §8):** `RELATIONSHIP (NEW 1-2 msgs new response, cold) → INTEREST (curiosity CURIOSITY phase 410) → DESIRE (TEASE, content_interest high) → COMMERCIAL_INTEREST (has_commercial + buying 0.55 SOFT) → QUALIFICATION (explicit/product relevance + can_sell) → OFFER_READY (strong/explicit + readiness evaluator) → PURCHASE → AFTERCARE → REPEAT/CROSS`.

Currently **desire is single `TEASE` rule** (`response_mode 80 tone==flirty→TEASE`), not staged `CURIOSITY→INTEREST→DESIRE→QUALIFICATION`.

**7. Commitment:** State is **deterministic derived** `funnel + purchase + recency + has_active_offer` (`relationship 94`), not LLM boolean. Next adds `desire_stage` enum (`RELATIONSHIP=0 ... AFTERCARE=7`) persisted **transiently** (derived per-turn from offer/purchase/cooldown + 30d tip history, not new DB column unless post-implementation forensic shows decay gap).

**8. Decay:** Commercial intent must decay `strong buying signal → fan changes topic → temperature decreases`. Proposal: `buying_intent_score 0.9 → decay ×0.5 if topic discontinuity (open_threads volatile) + ×0.7 per 24h since last offer` as `commercial_temperature` bounded `COLD<0.3<WARM<0.6<HOT` computed from `relationship(0.60) + desire(TEASE/CALLBACK history) + purchase_readiness(0.55/0.80/0.95) - fatigue(2) - cooldown 24h/6h`. Fits existing `SalesPressure NONE/LOW/MODERATE` but adds **scalar temperature** field in telemetry (no 4K prompt cost).

**9. Vault title taxonomy:** `Subject — Setting — Format/Bundle` as proposed `Red Dress — Mirror — 3 Photo Set / Red Lace — Bedroom — 6 Photo Bundle` — DropFans `create_drop.name` free-text, no validation; local `raw + fangate_products.title` store verbatim, searchable `ILIKE '%Balcony%'`, deterministic. Bundle `3 pictures same outfit/setting/session` → one `drop` via operator 1-10 vaultItemIds `fangate 380-387`, price per bundle `price→price_cents int(price*100) 371`.

**10. Bundle strategy:** Deterministic identity `dropfans_product_id CUID`, title convention enforced by **dashboard creation-time validation** (noting current `fname non-empty` only `fangate 368`), media `media_count dead locally` → `raw.vaultItemIds.length` truth, purchase `fangate_transactions transaction_id='dropfans:{drop_id}'` unique, duplicate creation would new CUID (no vault-set dedup) — document as remaining.

**11. Purchase-history exclusion:** Already deterministic via `purchased_ids` set (`product_selection:65 SELECT purchased WHERE transaction_id NOT NULL`) → multi-product ranking excludes, never resells `Red Lace 3 Photo Set` if vault is same `id`. **Cross-sell** (different product same outfit) requires embedding similarity deferred.

**12. Sales-window model:** `GOOD` high engagement `relationship_score≥0.60_engaged + WARM + content_curiosity + flirt escalation + explicit curiosity + fan-initiated content, `BAD` `short replies 3w, topic change (open volatile), negative 1-2, explicit rejection 195, price objection 191, busy fallback generic, cooldown active (hours<24), recent purchase 45, recent offer rejection` → all via existing `decision 23-branch`. Formal `SalesWindow {GOOD,WEAK,BAD}` maps directly `GOOD→OFFER_READY, WEAK→BUILD_DESIRE, BAD→RELATIONSHIP/COOLDOWN`.

---

## 13. Existing Signal Inventory (Reuse, Not Duplicate)

Signals sheet still `deepseek 56-COMMERCE_SIGNAL_EXTRACTION_SYSTEM` 17 fields + 3 intent tags. Extend **in place**, not second system, for desire/temperature.

---

## 14. Implementation Order (Phase 2 Exact)

The Phase 1 forensic order `1 forensic map (DONE) → 2 Commerce state + sales intelligence → 3 Vault metadata → 4 Content matching → 5 Desire/qualification/window → 6 Offer orchestration → 7 Purchase attribution→delivery → 8 Aftercare/cooldown → 9 Re-engagement` **is correct** — tip/aftercare/multi already fixed as 2, vault naming as 3 is cosmetic (title free-text), so `4 content matching` + `5 desire/temperature` + `8-9` are next 3-week critical path. Do not skip to prompt polish.

---

## 15. Documents With Gaps

`PURCHASE DELIVERY GAP: Offer→vault reservation UNIQUE(creator,user,fangate_media_id) → finalize, but DropFans per-item download_url not fetched per buyer — today synthesizes sales_url. Exact blocker: DropFans API has no GET /vault/{id}/download-for-buyer; GET /vault returns file_path signed ~12h for owner, not buyer-grant. Needs DropFans expose buyer-scoped grant or pull vault item after sale webhook via buyer_email`.

Vault contentTags `[]` exist per item (`models 32`) but not indexed for title matching.

---

## 16. Metrics & Observability

Add bounded telemetry `desire_level, commercial_temperature, objection_type, offer_readiness` to `core/telemetry GenerationTelemetry` (ID-only, no raw content) — deferred Phase 2 P3 as in `telemetry 19 sale fields +55` already dataclass-ready, table `generation_telemetry 22-col` widening deferred (in-memory + event bus preserved, Postgres drop extra).

---

## 17. Required Next Phase

`PHASE 2 — Desire ladder + commercial temperature + offer-recognition + content matching + aftercare→renewal` — 5-state desires, preference memory per fan (existing `user_profiles.interests/preferences`), bundle upgrade recognition, cooldown-correct re-engagement.

---
