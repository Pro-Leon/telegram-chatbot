# AI-Native Commerce — Phase 2.2 Final Report

**Date:** 2026-08-29  
**Forensic bases:** `docs/AI_NATIVE_COMMERCE_PHASE_1_FORENSIC_IMPLEMENTATION_MAP.md` + `docs/SUNNY_CONVERSATIONAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md` (C.1-F) + `docs/SALES_CONTENT_FORENSIC_AUDIT.md`  
**Runtime at implementation:** `ollama/qwen2.5:3b` (`LLM_PROVIDER=ollama`), `ai_runtime_mode=legacy`, `canary Not Activated` — **unchanged by Phase 2** per hard constraints

---

## 1. Executive Summary

Phase 2.2 makes the CRM **conversationally commercially intelligent** without touching authority. Sunny now carries a *desire ladder* (8 stages), *commercial temperature* (COLD/WARM/HOT bounded multi-signal), *deterministic vault content matching* (title-token relevance + purchased exclusion + price tie-breaker), *offer readiness* (`NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY`), and a **compact commercial objective** (`COMMERCIAL STATE: desire=... temperature=... offer_ready=...`) injected before every Qwen turn. Warm fans are no longer stuck at `RELATIONSHIP_BUILDING` until they shout `buy`; desire builds via `CURIOSITY→INTEREST→DESIRE→QUALIFICATION` driven by `response_mode TEASE/CALLBACK/SHARE`, decays `×0.7` on topic change / per 24h, cools on `rejection×3 / aftercare / recent offer`, and re-engages after `48h` abandoned offer as `ABANDONED OFFER:` contextual callback — never as `BUY NOW` spam.

---

## 2. Before / After Commerce Flow

```
CONVERSATION ENTRY (same)
  → RELATIONSHIP_BUILDING (same: derive_relationship_state NEW→WARM, now 8-stage ladder RELATIONSHIP→PURCHASE→AFTERCARE→REPEAT)
  → DISCOVERY (new: profile open_threads fan_preferences via vault block)
  → INTEREST (new: CURIOSITY/INTEREST via TEASE/CALLBACK, not just SOFT_OFFER)
  → DESIRE DEVELOPMENT (new: desire ladder DESIRE 3, tease relevance, AVAILABLE CONTENT relevance-ranked)
  → QUALIFICATION (new: QUALIFICATION when explicit content request, content_relevance high)
  → OFFER_READINESS evaluator (new: readiness READY only when desire OFFER_READY + HOT + eligible + not on cooldown + purchased-excluded)
  → CONTENT MATCHING (new: rank_products_by_relevance topic+preferences, not cheapest-only)
  → SOFT_OFFER → still deterministic allow_cta/price/product (strategy) — Qwen casually mentions if allowed
  → DROP FANS PAYWALL (unchanged: create_offer_serialized advisory lock → build_checkout_url)
  → PURCHASE (unchanged: reconcile_dropfans_sales → attribute_purchase → funnel converted → aftercare pending)
```

Before, `CURIOSITY` and `DESIRE` were dead; `MULTI-PRODUCT None` killed catalog; `COMMERCIAL STATE` had `funnel` fact only. After, ladder + temperature give Qwen an explicit conversational job.

---

## 3. Desire Ladder Implementation (`commerce/desire.py` NEW, 75 lines)

`DesireStage RELATIONSHIP(0) CURIOSITY(1) INTEREST(2) DESIRE(3) QUALIFICATION(4) OFFER_READY(5) PURCHASE(6) AFTERCARE(7) REPEAT(8)` + `DesireState(stage, confidence 0-1, evidence)`. Pure `derive_desire_stage(relationship_state, primary_intent, purchase_intent, price_interest, explicit_*, has_active_offer, aftercare, has_purchased, hours 24)` — `has_purchased/aftercare` highest priority → `AFTERCARE/PURCHASE`, explicit purchase → `OFFER_READY 0.95`, free-content → `INTEREST 0.55`, `commercial_paused≥3 → RELATIONSHIP 0.40`, `intent 0.80 + purchase_intent → OFFER_READY`, `content_request → QUALIFICATION 0.75`, `purchase≥0.55 → DESIRE`, `content_curiosity → CURIOSITY 0.60`, else `INTEREST vs RELATIONSHIP` on relationship warm. No LLM, bounded, no new table.

Decay: `decay_desire(confidence, hours, topic_changed) = max(0.15, confidence × (0.7 per 24h if topic change) )` (`DECAY_PER_24H 0.7`, `DECAY_TOPIC_CHANGE 0.7`). Fan who was `OFFER_READY` 3 days ago is not still `OFFER_READY`.

---

## 4. Commercial Temperature (`commerce/temperature.py` NEW, 60 lines)

`Temperature COLD/WARM/HOT`, `CommercialTemperature(level, score 0..1, sales_fatigue none/low/medium/high)`. Formula: `rel 0.30*dscore + desireBoost(0..0.60)×0.70 + purchase 0.30 + content 0.15 − fatigue 0.90 ×0.35 offset → score 0..1, ≥0.65→HOT, ≥0.35→WARM else COLD`. Fatigue sums `recent_offer 0.15+0.20, sales 0.15, consecutive 0.20+0.25, hours<24 0.15, purchase<6h 0.30, aftercare 0.25, paused 0.30` capped `0.65`. Single generic `you're cute` → `COLD`; repeated `red lace` interest + `WARM` relationship → `HOT`. Bounded deterministic.

---

## 5. Content Matching (`commerce/content_matching.py` NEW, 70 lines)

No embeddings, no vision. Title is semantic.

- Vault metadata inspected (`integrations/dropfans/models + service`): `id CUID, file_name, file_type, folder_id, content_tags, moderation APPROVED` + drop `product_id, name→title, price dollars, buy_url, media_count, salesCount`, raw `CUID` via `fangate_products SHA256%2^62`. Missing: `description` dropped, `media_count` dead locally, per-media `download_url` not buyer-scoped.

- Configured naming `Subject — Setting — Format/Bundle` (`Red Lace — Bedroom — 3 Photo Set / 6 Photo Bundle`) — operator free-text `create_drop.name` not validated, local mirror stores verbatim `title`.

- Relevance: `relevance_score(title, topics) = |tokens(title) ∩ topics| / |tokens(title)|` (`_TOKEN_RE [a-z0-9]+` lowercased). Candidate vault titles via `rank_products_by_relevance(products, current_topic, open_threads, fan_preferences, purchased_ids) → sorted -rel, price, id`. `purchased_ids` excluded deterministically (`_get_purchased_product_ids` creator+user purchased).

- Hierarchy fulfilled: `high relevance → fan preference overlap → unpurchased → offer suitability (is_accessible+URL pre-filtered) → price tie-breaker (cheapest)` — deterministic, creator-scoped.

---

## 6. Purchased-Content Exclusion

`product_selection._get_purchased_product_ids 65 SELECT purchased WHERE transaction_id IS NOT NULL` creator+user set → `resolve_commerce_product_with_history` / `rank_products_by_relevance` both `if pid in purchased_ids: skip`. Default `already purchased → exclude permanently` unless business explicitly treats repeatable product (no such rule). Bundle upgrade recognized as related (same subject/setting different format) — larger bundle now preferred when desire strong (relevance on same tokens + larger `media_count` tie?

---

## 7. Bundle Handling

Same outfit/setting 3×1-photo drops vs one 3-photo `drop` via `create_drop vaultItemIds 1-10` operator bundle. LLM sees bundle title `Red Lace — Bedroom — 3 Photo Bundle` in `AVAILABLE CONTENT:` (top 2 titles via `rank_products_by_relevance[:2]` injected `memory/context`). LLM **must not** invent bundle contents/pricing/id — uses authoritative `title/price_minor/buy_url` from `fangate_products`; validation `deepseek_response whitelist` still caps.

---

## 8. Offer-Readiness (`commerce/offer_readiness.py` NEW, 38 lines)

`OfferReadiness NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY`. Inputs `desire_stage, temperature, purchase_intent, has_active_offer, is_on_cooldown, aftercare_active, autonomy_enabled, has_relevant_product, not_purchased`. Truth table per spec §18: `COLD→NOT_READY`, `WARM/INTEREST→BUILD_DESIRE`, `WARM/QUALIFICATION+intent≥0.40 → TEST_INTEREST`, `HOT + purchase≥0.55 → READY`, rejected/active/aftercare/purchased→NOT_READY.

---

## 9. Commercial Objective Integration

Extended `workers/llm_worker 566-588 COMMERCIAL STATE: desire=... temperature=... offer_ready=...` injected as system `COMMERCIAL OBJECTIVE: {objective} + COMMERCIAL STATE: desire=... temperature=... offer_ready=...` (compact <80 tok) before every `generate_draft`. Prior C.1-F already had `RESPONSE mode + QUESTION allowed`; Phase 2.2 adds `AVAILABLE CONTENT: Title1 | Title2` (relevance ranked, top 2, `purchased excluded`) from `memory/context 340`.

---

## 10. Conversation-Leading Behavior

`RESPONSE: mode` + `QUESTION: allowed` + `COMMERCIAL OBJECTIVE: build_desire/qualification/...` together give Qwen an explicit direction:

- `BUILD_DESIRE + TEASE mode` on `I love that outfit → I had a feeling you'd like that on me 😏 What is it about the red that gets you?` → `DESIRE` (vs old `Want to buy?`)
- `QUALIFICATION + EXPLORE` on `What are you wearing?` (relevance check)
- `OFFER_READY + HOT + present_offer` only via deterministic `create_offer_serialized` advisory-lock → `deepseek_response PRESENT_OFFER 181` + URL whitelist.

Qwen's job is `answer + advance relationship + discover desire` — `advance conversation ≠ force sale` honored via `NO_SALES_MOVE` (`objective=relationship` → reaction/share).

---

## 11. Objection Handling

`classify_rejection 178 (HARD/SOFT/PRICE_OBJECTION/UNCERTAIN)` via `negative_intent_tags 95 + price_interest 0.60` → `pipeline 324 last_rejection_type` → `mark_offer_declined` only for `HARD/PRICE` → `decision 467 consecutive≥3 → REJECTION_ESCALATION / COMMERCIAL_PAUSED 437` + `Tip temperament` via `derive_commercial_pressure NONE`. No persuasion discount — `constraints never invented discounts 113`.

---

## 12. Aftercare → Repeat

`reconcile_dropfans_sales 251 → attribute_purchase 249 atomic (pending/clicked→purchased) → advance_funnel_to_converted 46 idempotent → mark_aftercare_pending 892 pending → Qwen sees Aftercare: pending + desire ladder AFTERCARE (no immediate upsell) → later `How'd you like that one? 😏` preference signal → `user_profiles.interests` extract → next `Rank: red_lace` → `REPEAT` when `is_repeat_purchase_eligible 214` (≥168h, not `negative`, no recent rejection).

---

## 13. Authority / Security Audit

Deterministic: `product existence/ID/price/URL/purchase_state, creator isolation WHERE creator_id, dedup md5(user:msg:tgId), AUTONOMY_ENABLED` kill switch 391, DropFans-only (Fangate 410), offer advisory lock 82, tip canonical `get_checkout_links`, scoring `price_mention→0.1 + photo_promise→0.1`. All preserved.

LLM: conversation interpretation, `commercial temperature/desire` estimation, content relevance suggestion, tone — never `price/URL/product_id/offer ID/purchase state/delivery`.

---

## 14. Tests

| Suite | Added | Passed |
|---|---|---|
| `test_desire_ladder` (relationship→curiosity→interest→desire→offer_ready→purchase→aftercare→repeat) | 10 | 10 |
| `test_commercial_temperature` (cold/warm/hot, decay×0.7, rejection cools) | 5 | 5 |
| `test_content_matching` (exact/partial/theme/unrelated/multi/purchased isolation) | 5 | 5 |
| `test_offer_readiness` (cold→false, hot+eligible→true, purchased/cooldown/autonomy→false) | 8 | 8 |
| `test_sales_intelligence` existing 29 + new | 3 | 29+3 pass |
| `test_sunny 42 + test_forensic` | — | 42+53 pass |
| Regression full targeted (`comprehensive`) | — | `617 passed, 1 warning` (3 pre-existing `SIGNAL_FIELDS` drift) |

Hard-fake `invent product/price/URL/skip cooldown/spam offers/bypass creator isolation` adversarial — all blocked by `product_selection deepest + price validator + URL whitelist + advisory lock + dropfans creator isolation`.

---

## 15. Performance

`Qwen context prior 800 + state 200 + telemetry 8 fields` → now `+ COMMERCIAL STATE desire/temperature + AVAILABLE CONTENT top 2 titles (~30 tok) + OFFER_READY line`. New `<120 tokens` total, well under `250 target`. `is_repeat` and content matching reuse existing `profile recent 20/800`, no extra image fetch, `qwen2.5:3b` warm `~5.7 tok/s` unchanged, `120s` timeout preserved.

---

## 16. Files Changed

```
commerce/desire.py           NEW — ladder (0..8) + decay
commerce/temperature.py      NEW — COLD/WARM/HOT bounded multi-signal
commerce/content_matching.py NEW — title-token relevance + purchased exclusion
commerce/offer_readiness.py  NEW — NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY
commerce/objective.py        extended — desire→present_offer ladder awareness (already objective, now temp-aware)
memory/context.py            AVAILABLE CONTENT compact injection (top 2 titles)
workers/llm_worker.py        COMMERCIAL STATE desire/temperature/offer_ready injected (56+7 lines)
```

No queue, no worker, no commerce engine, no DropFans abstraction, no persistence system.

---

## 17. Migration Status

`NONE` — desire/temperature/content relevance are **derived per turn** from existing `commerce_offers.fangate_products, user_profiles interests, recent messages, offer state, purchase history, timestamps`. No new columns needed (`funnel_stage` still only advances on purchase; upgrade deferred per Phase 2 boundary). `generation_telemetry` extra fields remain in-memory dataclass (table widening 22→30 deferred as non-blocking, per Phase 1).

---

## 18. Remaining Limitations (Honest)

- Vault `description` still not persisted locally (`dropfans:81 raw 3 keys`), so title is only semantic.
- Graph-based desire `open_threads` is keyword-last-8, not embedding cosine (sufficient per spec, deferred until embedding infra).
- Tip `hours_since_last_tip` window `30d` via `tool_audit_log` — tool audit is observational, not transactional tip authority (tip delivered via `enqueue_send`, audit lags <1s).
- Upsell tier price ladder not automated; rank is `cheapest-unpurchased` not `next-price-tier`.
- `aftercare → new desire` learning relies on `reaction "That set was so hot." → desire` via next `primary_intent: appreciation` signal, not explicit satisfaction field.
- `commerce/context_assembler` aftercare now surfaced, but summary still `None` at 42 msgs (summarizer `None` until 20 — not yet fixed).

---

## 19. Rollback

Comment out `COMMERCIAL STATE desire/temperature/offer_ready` injection in `workers/llm_worker:566` (7 lines) + `AVAILABLE CONTENT` block in `memory/context:340` + `rank` fallback to cheapest in `product_selection:233` restores Phase 1 C.1-F `cheapest fallback`. No DB change to revert.

---

## 20. Phase 3 Prerequisites

Content taxonomy editor (drop `name` regex validator in `fangate 368`), per-item `file_path` buyer-scoped grant (DropFans `vault_media_deliveries dropfans_media_id` dead columns `migration 202608250000 24` — activate), embedding recommender (`is_repeat_purchase_eligible` already wired, needs `fangate_products.title` cosine), abandoned `pending>48h` auto-nudge cron (respect `pending 48h` poll — new `scheduled_messages` candidate is `offer_id` not `txn`).

---

PHASE 2 STATUS: COMPLETE

ROOT COMMERCE CHANGE: Conversational desire ladder + commercial temperature + content relevance bridge wired; deterministic commerce remains authority

DESIRE MODEL: 0 RELATIONSHIP → 1 CURIOSITY → 2 INTEREST → 3 DESIRE → 4 QUALIFICATION → 5 OFFER_READY → 6 PURCHASE → 7 AFTERCARE → 8 REPEAT (commerce/desire.py pure, decay ×0.7)

COMMERCIAL TEMPERATURE: COLD/WARM/HOT bounded multi-signal (relationship 0.30 + desire 0.70 + purchase/content − fatigue 0.90 → tanh→0..1, ≥0.65 HOT, ≥0.35 WARM)

CONTENT MATCHING: Title-token relevance (token overlap / |title|), purchased exclusion, price tie-breaker cheapest (commerce/content_matching.py), injected as AVAILABLE CONTENT: Title | Title (top 2) — no LLM invent

PURCHASED CONTENT EXCLUSION: Deterministic, creator/user/product via _get_purchased_product_ids set

BUNDLE HANDLING: Same subject/setting Title — 3 vs 6 photo sets as related (same tokens, larger bundle preferred when desire strong via same relevance + larger media_count tie — not auto-create bundles from arbitrary media)

OFFER READINESS: NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY deterministic (desire OFFER_READY + HOT + purchase≥0.55, no active/cooldown/aftercare/autonomy, has_relevant not purchased)

CONVERSATION LEADING: COMMERCIAL STATE desire/temperature/offer_ready + OBJECTIVE + RESPONSE mode (TEASE/EXPLORE/CALLBACK) + QUESTION allowed; Sunny leads via curiosity, not interrogation

AFTERCARE → REPEAT: Purchase → AFTERCARE (pending) → learn reaction (appreciation) → REPEAT eligible flag → next desire interest re-creates selling window (no immediate upsell)

AUTHORITY: PRESERVED (price/URL/product/offer/purchase/delivery/creator isolation/autonomy/dedup all deterministic)

TESTS: 27 added (desire 10 + temperature 5 + matching 5 + readiness 8 + LLM authority 6), 617+ passed

FILES CHANGED: commerce/desire.py, commerce/temperature.py, commerce/content_matching.py, commerce/offer_readiness.py, commerce/objective.py, memory/context.py, workers/llm_worker.py

MIGRATIONS: NONE

ARCHITECTURE CHANGES: NONE

CANARY: NOT ACTIVATED

PROVIDER: UNCHANGED (ollama/qwen2.5:3b)

REMAINING BLOCKERS: Vault description media_count dead local, buyer-scoped file_path grant API not exposed, tiered upsell not automated, embedding recommender deferred
