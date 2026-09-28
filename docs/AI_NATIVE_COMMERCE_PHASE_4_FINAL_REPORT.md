# AI-Native Commerce — Phase 4 Final Report (Conversational Sales Execution & Fan Lifecycle)

**Date:** 2026-08-29  
**Impl:** `docs/AI_NATIVE_COMMERCE_PHASE_4_IMPLEMENTATION_MAP.md` + prior `C.1-F + Sales Intelligence` phases 1-3  
**Runtime:** `ollama/qwen2.5:3b` on `https://ollama.brestalogistics.co.ke`, `ai_runtime_mode=legacy, canary Not Activated` — **unchanged**

---

## 1. Executive Summary

Phase 4 moves Sunny from *"knowledgable"* to *"leading"* — the bot now carries a **sales window** (`NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE`) derived from `desire 0-8 + temperature COLD/WARM/HOT + offer readiness + aftercare + fatigue`, on top of the Phase 2 ladder/temperature already shipped. Conversations flow `RELATIONSHIP 0 → CURIOSITY 1 → INTEREST 2 → DESIRE 3 → QUALIFICATION 4 → OFFER_READY 5 → PURCHASE 6 → AFTERCARE 7 → REPEAT 8` with **evidence-based escalation** (explicit `how much → OFFER_READY` can jump; `you're cute → CURIOSITY` cannot) and **decay** (`lunch topic change ×0.7, 24h ×0.7, rejection ×3 → RELATIONSHIP + commercial_paused`).

## 2. Current Architecture

Same `Telegram → debounce 3s → Redis inbound → llm_worker 530 build_qwen3_context → _try_commerce_draft 566 (state 169 read-only) → decision 23-branch PURE → strategy → execution advisory lock ppv_offer:{c}:{u}:{p} → selection 228 → COMMERCIAL OBJECTIVE + COMMERCIAL STATE desire/temperature/window system → Qwen/qwen2.5:3b → scoring 0.1 caps photo/price → routing → SEND_STREAM dedup → Telethon → post_process profile[-10:]` + poll `reconcile_dropfans_sales 251` → `attribute_purchase → funnel converted → aftercare pending`.

## 3. Phase 3 Baseline

DropFans `POST /drops price/vaultItemIds 1-10 → DropResult product_id CUID, buyUrl` mirrored as `fangate_products SHA256%2^62` with `title/price_minor/sales_url` (description dead), vault `GET /vault filePath signed 12h owner-scoped` (no buyer grant), `reconcile product_id NULL` was dead → **fixed** `db/dropfans 187 synthetic`.

## 4. Sales Lifecycle (Phase 4)

`RELATIONSHIP → CURIOSITY → INTEREST → DESIRE → QUALIFICATION → OFFER_READY → PURCHASE/DECLINE/HESITATION → AFTERCARE → PREFERENCE_LEARNING → REPEAT_OPPORTUNITY` — backward moves allowed (topic change → `RELATIONSHIP`, `COOLDOWN → 24h`).

## 5. Desire Model

`commerce/desire.py: RELATIONSHIP 0, CURIOSITY 1, INTEREST 2, DESIRE 3, QUALIFICATION 4, OFFER_READY 5, PURCHASE 6, AFTERCARE 7, REPEAT 8` + `DesireState(stage, confidence 0-1, evidence)`. Pure `derive_desire_stage(relationship_state, primary_intent, purchase_intent 0.55/0.80, price_interest 0.55, explicit_*, aftercare, has_purchased, hours_since, has_active_offer, commercial_paused)`.

## 6. Commercial Temperature

`commerce/temperature.py: COLD/WARM/HOT` `rel0.30 + desire 0-0.60 + purchase 0.30 + content 0.15 − fatigue 0.90 → 0..1, ≥0.65 HOT, ≥0.35 WARM` fatigue `recent 0.15+0.20, sales 0.15, consecutive 0.20+0.25, <24h 0.15, purchase<6h 0.30, aftercare 0.25, paused 0.30` capped `0.65`. Bounded, sales-fatigue aware.

## 7. Sales-Window Logic

`commerce/sales_window.py NEW: derive_sales_window(desire, temperature, readiness, aftercare, cooldown) → NO_WINDOW (relationship) / BUILDING (desire/interest/qualification) / OPEN (offer_ready+warm/hot) / COOLDOWN (is_on_cooldown) / AFTERCARE (aftercare_active)`. Wired `workers:580` as 5th `COMMERCIAL STATE` token.

## 8. Conversational Objectives

Extended `commerce/objective.py`: `relationship | explore | build_desire | qualify | present_offer | objection_handling | aftercare | reengage | no_sale` + Phase 4 `BUILD_DESIRE/QUALIFY` refined via `relationship WARM/BUYING_SIGNAL → build_desire`, `PURCHASED/VIP → aftercare`.

## 9. LLM Context Contract

`COMMERCIAL STATE: desire=... temperature=... offer_ready=... window=...` + `COMMERCIAL OBJECTIVE: ...` + `AVAILABLE CONTENT: Title | Title` (top 2 relevance, `purchased excluded`, creator-scoped, no private URLs/IDs) + `CAPABILITIES send_photo:no` + `RESPONSE mode + QUESTION allowed`. Compact `<80 tok` before Qwen, no giant prompt.

## 10. Content Matching

`commerce/content_matching.py` title-token `|tokens(title)∩topics|/|t|` + bundle-aware (`rel≥0.30 → larger bundle first else cheapest`) + fan `interests/preferences` overlap + `purchased exclusion`. `commerce/vault_taxonomy.py` parses `Subject — Setting — Format → media_count/bundle_size/group` for related `Red Lace 3 vs 6 Bundle` recognition (same `subject|setting` group). No embeddings, no vision.

## 11. Bundle Behavior

Same `subject|setting` larger bundle preferred when `rel ≥0.30` and `desire strong`; otherwise cheaper small set. No LLM invent, no auto-`POST /drops` creation.

## 12. Offer Transition

`conversational 95% never *wants* to sell` — Qwen conversational system has zero `sell` lexeme; conversational curiosity → `REACT/SHARE/TEASE/CALLBACK`. Soft transition only via `COMMERCIAL OBJECTIVE build_desire → tease + curiosity`, qualification `→ CLARIFY`. Deterministic `offer_readiness READY + HOT + eligible + no active/cooldown/aftercare/autonomy + not_purchased` + `POST /drops` authoritative. Fan `I love red on you → That one is one of my favorites 😏` → `DESIRE`, fan `Do you have more? → QUALIFICATION`, fan `How much? → OFFER_READY → present authoritative offer naturally`.

## 13. Objection Handling

`classify_rejection 178 (HARD/SOFT/PRICE/UNCERTAIN)` → marked `mark_offer_declined` only HARD/PRICE → `decision 467 consecutive≥3 → REJECTION_ESCALATION + commercial_paused 437`. Qwen `REACT/SHARE/CALLBACK` cools, no discount invented (`constraints never invented discounts 113`).

## 14. Cooldown

`OFFER 24h outcome-aware 354, PURCHASE 6h 342, recent_offer 2, sales 3, negative≥2 → RELATIONSHIP_BUILDING 403, low_conf 412, conversational phase 427, rejection 3, aftercare 450` all suppress. `SalesWindow COOLDOWN` via `is_on_cooldown` (high fatigue) → `QUESTION allowed=false`.

## 15. Abandoned Offer Recovery

Persistence `commerce_offers pending/clicked` forever; detection `has_active_offer && age≥48h → Abandoned offer: {title} (Nh)` `context_assembler:790` → `CALLBACK/RE_ENGAGE`, not `BUY NOW!!!` spam.

## 16. Purchase Flow

`offer pending → DropFans poll/attribution → funnel converted 46 → aftercare pending 935 → schedule 24h 282 dedup post_purchase_followup:{txn} → delivery synthesize sha256→sales_url`. Idempotent `UNIQUE(creator,transaction,event)` + advisory lock `ppv_offer`.

## 17. Aftercare

`AFTERCARE pending/sent + purchases>0 → AFTERCARE_PHASE 450 RELATIONSHIP_BUILDING` → Qwen `Aftercare: pending` + `COMMERCIAL OBJECTIVE aftercare` → `acknowledge → ask reaction → learn preference → not upsell immediately`.

## 18. Preference Learning

`extract_and_update_profile [-10:] 140` cap15 lists → `user_profiles.facts interests/preferences` → `AVAILABLE CONTENT` relevance fan_preferences param `31` — creator-isolated, bounded, explicit/inferred confidence, no raw sensitive storage.

## 19. Repeat Purchase

After `168h eligible` + new `content matching` interest, `REPEAT` flag surfaced, next relevant unpurchased ranked.

## 20. Anti-Spam Controls

`cooldown 24h/6h, tip fatigue 30d, sales fatigue medium/high, question budget MAX_CONSECUTIVE 1 + questions_in_last_3 ≥1 block, debate loop 3s, send dedup 3600, advisory lock`.

## 21. Authority Model

LLM: conversation, interpretation, preference extraction. App: eligibility, product ID/price/URL/offer creation/purchase state/delivery. Never LLM: `price as $20`, `here is checkout URL` unless supplied as `VERIFIED FACTS` or `AVAILABLE CONTENT`.

## 22. Telemetry

+8 fields `desire_stage, commercial_temperature, sales_window, commercial_objective, offer_readiness, content_match, sales_fatigue, aftercare` (ID-only, no raw body).

## 23. Tests

`71 passed` (29 sales intelligence + 42 sunny conversational), `570+ commerce/provider` slice green + 3 pre-existing `SIGNAL_FIELDS` drift.

## 24. Database / Migration

`tip via existing tool_audit_log` no migration; `fangate_products` mirror `SHA256` deterministic, per-item buyer grant deferred as external blocker documented.

## 25. DropFans Limitations

Per-item buyer `downloadUrl` grant not exposed (`client 269-627` inventory, no `buyer_email` param). Safe fallback `sales_url` retained, owner `filePath` never leaked.

## 26. Remaining Gaps

Vault `description` dead local, per-item buyer grant blocked, tiered upsell not automated.

---

PHASE 4 STATUS: CONDITIONALLY COMPLETE — desire ladder + temperature + content relevance + offer readiness + sales window wired; landing is natural not hard sell
ROOT COMMERCE BEHAVIOR: relationship → desire → qualification → offer → aftercare cycle now has deterministic state (8/cool + COLD/WARM/HOT + window)
SALES LIFECYCLE: RELATIONSHIP → CURIOSITY → INTEREST → DESIRE → QUALIFICATION → OFFER_READY → PURCHASE → AFTERCARE → REPEAT (backward-allowed, decay ×0.7)
SALES WINDOW: NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE deterministic via desire+temperature+readiness+cooldown+aftercare
CONVERSATIONAL LEAD: COMMERCIAL STATE + OBJECTIVE + RESPONSE mode TEASE/EXPLORE/CALLBACK + QUESTION allowed; Sunny leads via curiosity, not interrogation
CONTENT MATCHING: title-token + bundle-aware + purchased exclusion + price tie-breaker
OFFER TRANSITION: conversational curiosity → desire → qualification → OFFER_READY (HOT+intent≥0.55+eligible) → deterministic offer only when eligible
OBJECTION / COOLDOWN: PRICE/TIMING trust → reduce temperature, then relationship, not re-pitch
PURCHASE / DELIVERY: poll product_id synthetic fix → attributed → funnel → aftercare, reservation UNIQUE(creator,user,media), sales_url fallback (buyer media blocked)
AFTERCARE: pending → RELATIONSHIP_BUILDING, preference learning via Qwen aftercare prompt, no immediate upsell
REPEAT PURCHASE: 168h eligible + relevant unpurchased ranking (tier not automated)
AUTHORITY: PRESERVED  FILES CHANGED: desire/temperature/content_matching/offer_readiness/objective/context/vault_taxonomy/workers/telemetry  FILES CREATED: sales_window
MIGRATIONS: NONE  ARCHITECTURE CHANGES: NONE  CANARY: NOT ACTIVATED  PROVIDER: UNCHANGED  PRODUCTION DEFAULTS: UNCHANGED  ROLLBACK: comment out COMMERCIAL STATE desire/window + AVAILABLE CONTENT (7 lines) + ranking fallback  FINAL VERDICT: CONDITIONALLY READY — sales window + desire loop proven, vault per-item grant remains external blocker
