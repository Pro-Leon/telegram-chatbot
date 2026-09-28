# AI-Native Commerce — Phase 1 Forensic Implementation Map

**Date:** 2026-08-29  
**Scope:** Complete forensic trace of existing CRM/Telegram/DropFans commerce, conversation, vault, tip, purchase, and AI sales intelligence — read-only mapping for next phases  
**Method:** Independent code trace of the CURRENT working tree. No production code, configuration, database, migrations, provider, canary, prompts, or runtime modified. All findings anchored to `FILE:FUNCTION:LINE`.

---

## 1. Executive Summary

Sunny's commerce is a **deterministic engine with phrasing**, not an AI sales agent. The CRM is **architecturally sound but commercially passive**: eligibility, product mirror, pricing, checkout URL, offer idempotency, creator isolation, `AUTONOMY_ENABLED`, send-queue dedup, and `DropFans` authority are all live and sealed. The AI converses via `ollama/qwen2.5:3b` (live on `https://ollama.brestalogistics.co.ke`, warm `~5.7 tok/s`, `1.9 GB`) with a **conversational state that is now repaired** (`C.1-F` added `ConversationState`, `ResponseMode`, `QuestionBudget`, `CapabilityContract`, `ABOUT SUNNY`). However the AI is **never told to sell** — Qwen's system has zero `sell` lexeme, `SalesPressure` is `PLANNED,BUT UNWIRED` to conversation, `product` never reaches Qwen (multi-product `≥2 → cheapest` was just fixed), desire escalation is **single `TEASE` mode**, vault delivery is **real and idempotent** via `vault_media_deliveries` reservation, but purchase-time **DropFans media contract is synthetically derived** (not per-vault-item download URL), and aftercare is **suppression-only**.

Phase 1 ships **no code**. Phase 2+ can now add vault taxonomy + content matching + desire/qualification ladder + abandoned re-engagement + post-purchase continuation without replacing the deterministic engine.

---

## 2. Existing Architecture

```
Telegram (Telethon Sunnyskyee)
  ↕ handlers.py (debounce 3s) + entity_blacklist
  ↕ Redis Streams: inbound_messages (llm_workers) / send_messages (send_workers) + XAUTOCLAIM 60s
  ↕ workers/llm_worker.py::process_message (user_lock 60s, generation_id, creator resolve, build_qwen3_context, _try_commerce_draft, Qwen, scoring, routing)
  ↕ memory/context.py compact budgets: system 400 + state 200 + conversation 800/20msgs/3assist + summary 200
  ↕ commerce/decision (PURE 23-branch) → strategy (pressure NONE/LOW/MODERATE) → orchestrator → execution 11-gate → deepseek_response (VERIFIED FACTS whitelist)
  ↕ DropFans sole provider (integrations/dropfans/*) over fangate_products mirror (id SHA256(drop_id)%2^62)
  ↕ Postgres (users/messages/conversation_summaries/user_profiles/message_embeddings/operator_queue/commerce_offers/vault_media_deliveries/scheduled_messages/fan_segments/tool_audit_log/generation_telemetry 22-col)
  ↕ Redis: send_dedup TTL 3600, daily_quota gemini:daily_quota:YYYY-MM-DD, tip dedup tip:{c}:{u}:{md5} 3600, auto_reply toggle
  ↕ scoring hard-flag cap 0.1 (price_mention now includes tip), photo_promise 0.1
```

Invariants: `AGENTS.md` phase-1 event contract (`ai.generation_started → ai.generation_completed` after `enqueue_send` + `suggestion.created/operator_queue.updated`), creator isolation `WHERE creator_id`, `AUTONOMY_ENABLED=true` kill switch, DropFans-only (Fangate `410 Gone`).

---

## 3. Current Conversation Flow

`handlers.py:142 @client.on(NewMessage incoming) → 118-128 persona cache 600s → 129 enqueue_inbound {persona} → llm_workers XREADGROUP → process_message 467 acquire_user_lock → 530 build_qwen3_context → 566 _try_commerce_draft (resolve_and_run_commerce 422 → select_commerce_response) → 574 injected COMMERCIAL OBJECTIVE system → 684-707 LEGACY vs Agent (canary false → LEGACY) → generate_draft (Ollama/qwen2.5:3b) → 752 scoring → 825 routing (≥0.80 no flags → enqueue_send else operator queue) → 889 post_process profile+summarizer fire-and-forget.`

Shadow Q1 `qwen_shadow_enabled=false`, Agent `ai_runtime_mode=legacy + ai_agent_canary_enabled=false` never runs (even if toggled, `loop.py:189 generate_with_tools` signature mismatch).

**Identity lifecycle:** `core/conversation_state.py` NEW/ESTABLISHED/RETURNING (message_count + 48h gap), `identity_already_established` via `sunny skye|i'm sunny` regex on outbound history. **Conversational state:** `current_topic/recent/open_threads/last_question/was_answered/consecutive/tone/last_user_fact/questions_in_last_3` derived per turn from last 8 keyword scan (14 keywords) + last 3 assistant.

---

## 4. Current Commerce Flow

Integrated path proved earlier (§3 call graph: 18 stages). Deterministic cascade before any LLM touches commerce: `resolve_commerce_state 169 (read-only SELECTs: get_user, dropfans integration, fangate_product, timing, behavioral) → CommercePipelineRequest 123 → orchestrate_commerce 167 (aftercare classify) → decide_commerce_action 269 (23-branch pure) → build_strategy 176 (allow_cta/price/product, relationship_first) → execution 87-315 (11-gate only OFFER_PPV) → selection 228 (only EXECUTED/ALREADY → USE_COMMERCE_RESPONSE 286) → bypass Qwen → send`. Non-OFFER actions fall through to Qwen with **no price/URL**.

---

## 5. Current LLM Context (What Qwen Receives)

**Order (exact wire `workers/llm_worker.py:99 + memory/context.py:453`):**

1. `IDENTITY` persona `You are Sunny Skye / You are sunny` (trimmed when established `context.py:205`) — **no hardcoded bot templates remain** (grep 0 prod hits for `How's your day`).
2. `Fan: {first_name}` + `facts` (filtered `age/location/occupation/interests` only `258`)
3. `Stage: New fan. Warm welcome.` etc. (4-value map)
4. Rules: `2-4 sentences ... Do not promise photos ... A reply may have no question ... Prefer callbacks` (`220-230`)
5. `STATE: Fan|funnel` always, `PROFILE, RELATIONSHIP, COMMERCE (5 filtered facts, `aftercare` now included), SUMMARY 2 sentences`
6. `IDENTITY: established=... lifecycle= + RULE: Do NOT re-introduce`
7. `CONVERSATION: topic= open=[netflix] last_q= answered= tone=`
8. `ABOUT SUNNY: enjoys cozy movie nights...` (`persona_self.py` 3 facts)
9. `CAPABILITIES: send_text:yes send_photo:no ... NOTE: Do NOT promise...` always
10. `RESPONSE: mode=react... ` + `QUESTION: allowed= / COMMERCIAL OBJECTIVE: relationship|build_desire|present_offer|aftercare|no_sale` (`objective.py` + `llm_worker:580`)
11. `TOOL AUTHORITY` only on Gemini path (Qwen path plain, **no tool declarations** even though `TOOL_AUTHORITY_PROMPT` exists `core/llm_tools.py:1153`)

History then appended: `recent 20 → 800 tok → 3 assistant turns` deduped `workers:98-101`.

**Missing from Qwen:** `Creator: Bella` dropped (no purchase keyword), individual purchase `Title price` lines dropped, `vector relevant_memories` DEAD for Qwen path (`retrieve_relevant_history` only legacy `context:346`), `Creator name` not seen, `Timing exact hours` not seen, `full catalog` never.

---

## 6. Current Sales Intelligence

| Signal | Producer | Consumer | Reach |
|---|---|---|---|
| `purchase_intent` 0-1, `price_interest`, `content_interest`, `relationship_engagement` `signals.py:161` | LLM `deepseek.py:184 provider.generate format=json 0.0` bounded 30×800 | `signals_to_context 320 → decision 517-548 (0.55 soft, 0.80 strong)` | Decision only, **not Qwen** |
| `primary_intent` 20 values, `intent_tags` `negative_intent_tags` `fan_asks_question` | Same | `_derive_conversational_phase → decision:427 opening/rapport → RELATIONSHIP_BUILDING` | Decision only |
| `tip_eligibility` `WARM/BUYING…` | `relationship:259 72/48/24h ×1.5 + dao real tip counts (was hard-zero, now tool_audit 30d 559)` | `decision 500 → TIP_SUGGESTION` vs `suggest_tip` eligibility rebuild via `llm_tools:850 dao` | Decision + tool gate (both now fatigue-aware) |
| `relationship_state` `COLD/NEW/ENGAGED/WARM/BUYING_SIGNAL...` | `relationship:94` from `funnel, purchase_count, last_message 7d, has_active_offer` | `pressure, tip, pipeline relationship` | Qwen sees `RELATIONSHIP: warm` line |
| `commercial_pressure NONE/SOFT/MODERATE` | `relationship:177` `DEVELOPED` (explicit → DIRECT etc) | Decision `SOFT→ OFFER feasibility` | **Not Qwen** (was lossy, now `COMMERCIAL OBJECTIVE` bridges) |
| `conversational_phase` `opening/rapport/commercial_interest/content_curiosity...` | `signals:382` | Decision `427 suppression` | **Unwired to Qwen** (`render_context` omits it) |
| `negative_intent_count, signal_confidence` | Same | Decision `403 NEGATIVE_SUPPRESSED`, `412 LOW_CONFIDENCE_CHAT` | Not Qwen |

---

## 7. Current Vault Integration

### 7.1 DropFans surfaces (live)

- `GET /vault` `client:269 list_vault` → `models.py:19 VaultItem {id CUID str, file_name, file_type image/video, file_path signed media URL ~12h, thumbnail, download_url, file_size, duration, content_tags[], folder_id, moderation_status APPROVED/PENDING, created_at, raw}` + `VaultFolder {id,name,item_count}`.
- Mutations: `upload_vault_item 287 multipart`, `list_vault_folders 349, create 354, delete 360, move 333 PATCH /vault/{id}/folder, update tags 323 PATCH /vault/{id}/tags` via `service:205-340`.
- Video TUS 3-step: `start_video_upload 371 POST /vault/video-upload → video_id, tus_endpoint, library_id, signature`, `complete 386 POST /vault/video-upload/complete`, `get_video_status 407 batch`.
- Drops (= products) — **no `list_products` listing endpoint** on DropFans. Client `create_drop 426 POST /drops {name, price dollars, vaultItemIds 1-10, allowDownload, description}` → `DropResult {product_id CUID str, buy_url, media_count, sales_count}` (`models:199`). `get_drop 450 GET /drops/{id}`, `check_drop_status 478 batch paid` only paid retained.

### 7.2 Local mirror

`db/dropfans.py:81 upsert_dropfans_product(creator_id, dropfans_product_id CUID, name→title, price_cents int(price*100), buy_url, status, allow_download, media_count (dead), sales_count)` → `fangate_products` `INSERT (id SHA256(drop_id)%2^62, creator_id, product_type='dropfans', title, price_minor, sales_url, is_downloadable, raw {dropfans_product_id, vaultItemIds, salesCount}, is_accessible TRUE) ON CONFLICT id DO UPDATE`. `description` sent to API `client:443` but **never stored locally** (`raw` only 3 keys, `media_count` param dead `dropfans:89` unused, `price` dollars lost). Access via `find_dropfans_product` synthetic hash + raw equality, `list_active_dropfans_products 293 WHERE product_type='dropfans'`.

### 7.3 Vault delivery today

`vault/service.py:23 list_media` → **still Fangate-mirror bound** `fdb.list_fangate_products + aggregate_media_from_products` deduplicate by `media.id int` (`aggregate.py:53`), overlay `vdb.get_delivered_media_map`; `reserve_delivery 169 atomic INSERT pending` eliminate TOCTOU `vdb:64`, `finalize 80 UPDATE pending→sent`, `release 104 DELETE`, `release_stale 122 pending created_at<5m`. Product linking `fangate_media_id INTEGER` legacy name (migr `vault_media 5-15 UNIQUE creator,user,fangate_media_id`) `dropfans_media_id/dropfans_vault_item_id TEXT` dead columns never written (`docs P2-3`).

**Post-purchase DropFans delivery** synthesizes media id `sha256("dropfans:{product_id}")%2^31 425` and sends checkout link `479 Your content is ready: {sales_url}` instead of per-item `download_url`.

---

## 8. Current Product / Offer Flow

`fangate_products` mirror (see §7.3) → Valid `is_accessible && sales_url 52` → Deterministic ranking **now** `sorted(price_minor or INF, id)` picks cheapest unpurchased (`product_selection:233 cheapest`) vs old `None` ambiguity. Creator-scoped `list_fangate_products(creator_id)` `120`. LLM may propose via `list_products/get_product_information` tools but `supports_tool_calling false` on Ollama → plain `generate_draft` path skips tool loop (Qwen path is tool-less). Drop creation is `df_service.create_drop` (operator/dashboard), not LLM.

---

## 9. Current Purchase Flow

`offer presented (commerce_offers pending)` → fan purchases on DropFans (`reconcile_dropfans_sales 251 poll / drops/check-status 478 paid`) → `attribute_purchase_from_webhook dao:249` atomic `SELECT pending/clicked` fail 0 or>1, `UPDATE purchased 170`, `UPDATE fangate_transactions.user_id where NULL 183`, `INSERT ppv_analytics_daily 197` → `advance_funnel_to_converted idempotent 46→converted` (only non-new advancing; `new` at 47 msgs prove fan never advanced) → `mark_aftercare_pending none→pending 892` → `schedule_follow_up 24h 282 dedup post_purchase_followup:{txn}` → `enqueue_purchase_confirmation post_purchase:{txn}:{user} 97` → `delivery synthesize` → `scheduled_messages` poll.

Callers verified live; no duplicate factual claim.

---

## 10. Current Aftercare

`commerce_offers.aftercare_status none/pending/sent/completed/skipped` (`migration 20260826010000 5-14` constraint+partial index) + `dao:935 mark_aftercare_pending/completed/get_aftercare_status` (most recent `state=purchased` `491`). Decision suppression `pending/sent + purchases>0 → AFTERCARE_PHASE 450` **live** (scoring not sales). Now **surfaces** `context_assembler:790 Aftercare: pending` and Qwen `COMMERCE: Aftercare: pending` (`memory/context:283 keyword`), plus `COMMERCIAL OBJECTIVE: aftercare` (`objective.py`).

---

## 11. Current Re-engagement

Persistence: `commerce_offers state pending/clicked` (`idx state 35`) forever. No `abandoned` state, no `pending>48h` poller. New wiring adds `Abandoned offer: {title} (Nh ago)` when `has_active_offer && age≥48h` (`context_assembler:790`) → `CALLBACK` mode when fan returns with `netflix/popcorn/saturday`. Decision `FOLLOW_UP 528` requires `previous_offer_status in {declined,revoked,clicked,expired}` + cooldown clear — conversational `deepseek_response` `FOLLOW_UP → gentle mention` but selection treats it `NON_EXECUTING` `selection:80 FALLBACK` not sent as commerce. Single post-purchase ping is the only durable re-engagement (`post_purchase:282`).

---

## 12. Current Tip Flow

`user asks how to tip → LLM proposes suggest_tip(reason) → dispatch_tool 298 validates (timeout 332) → _handle_suggest_tip 804` checkpoints `815 blocked/823 do_not/831 sales`, builds relationship via `build_llm_context 848`, derives pressure/tip with **real `tool_audit_log` 30d counts** (was 0; now `dao:559`), validates `890`, `get_checkout_links(creator) 906` **canonical `telegram.tip` or `web.tip` 911, `startswith http 925`**, `dedup tip:{c}:{u}:{md5(url)[:12]} 937 + is_send_duplicate 943 3600` else hard-coded `tip_content 950` → `enqueue_send 952 SEND_STREAM` → `_process_send_stream:98 is_send_duplicate second check, 283 mark_send_dedup` (`db/redis:73`). Creator-isolated `tip:{creator}:{user}` (audit says `tip:{creator}:{user}:{md5}` — verified, now fatigue-aware).

---

## 13. Desired End-to-End Lifecycle

```
CONVERSATION ENTRY
  ↓ RELATIONSHIP BUILDING (C.1-F Sunny state + question budget + response mode react/share)
DISCOVERY
  ↓ memory/profile last-10 extraction + asks open_threads (work → netflix → popcorn)
INTEREST DETECTION
  ↓ primary_intent content_curiosity / intent_tags purchase_intent (deepseek 80)
DESIRE DEVELOPMENT
  ↓ relationship ladder WARM/BUYING_SIGNAL + commercial_pressure SOFT + TEASE/CALLBACK mode
COMMERCIAL QUALIFICATION
  ↓ decision 23-branch (relationship_score 0.60, buying 0.55/0.80, confidence 0.30) + is_repeat_purchase_eligible
CONTENT MATCHING
  ↓ (Phase 4) metadata title match deterministic
SOFT OFFER
  ↓ LOW pressure (strategy) — "casually mention"
DROP FANS PAYWALL / CHECKOUT
  ↓ OFFER_PPV explicit buy/strong signal → advisory lock → DropFans build_checkout_url (buy_template {productId})
PURCHASE
  ↓ reconcile_dropfans_sales 251 → attribute_purchase
CONTENT DELIVERY
  ↓ synthesize fangate_media_id SHA256 + sales_url (today); per-item download_url missing (§15)
AFTERCARE
  ↓ mark_aftercare_pending; Qwen sees Aftercare: pending; COMMERCIAL OBJECTIVE aftercare; scoring still 0.1 on price spill
RE-ENGAGEMENT
  ↓ Abandoned offer 48h surfaced; FOLLOW_UP via decision 528
NEXT COMMERCE OPPORTUNITY
  ↓ repeat eligible flag already derived but not yet auto-offer; next turn's relationship re-derived
```

Conversation is **continuous** — every turn recomputes `ConversationState` transient and `deterministic strategy` without script; LLM chooses `HOW` to say `REACT/SHARE/TEASE/...` given `mode + QUESTION allowed`.

---

## 14. Desired AI Commerce Role (Conversational Objectives, NOT Authority)

| LLM objective | Deterministic guard | Mode |
|---|---|---|
| `BUILD_RAPPORT` | eligibility passed, phase `opening/rapport` → RELATIONSHIP_BUILDING 427 | REACT |
| `DISCOVER_INTEREST` | curiosity/rapport `content_curiosity` 410 | EXPLORE |
| `DEEPEN_TOPIC` | open threads + topic continuity | CALLBACK |
| `CREATE_DESIRE` | `relationship_state WARM/BUYING + pressure SOFT + TEASE + ABOUT SUNNY` | TEASE |
| `QUALIFY` | `is_repeat_purchase_eligible + interest` soft-gated | SHARE/EXPLORE |
| `SOFT_OFFER` | decision `SOFT_OFFER LOW` only if relationship 0.60 + buying 0.55, price/product conditional `strategy 225` | EXPLORE with casual mention |
| `HANDLE_OBJECTION` | `negative_intent ≥2 → NEGATIVE_SUPPRESSED 403` or `price_objection → mark declined 541` + constraints `never invent discount` `response:122` | REACT |
| `COOL_DOWN` | `consecutive ≥3 → commercial_paused REJECTION_ESCALATION 467` | REACT/CLOSE |
| `AFTERCARE` | `aftercare pending + purchases>0 → AFTERCARE_PHASE 450` | REACT with appreciation |
| `RE_ENGAGE` | `Abandoned offer 48h` surfaced → `FOLLOW_UP` `528` | CALLBACK |

All map to existing `ResponseMode` 8 values; LLM never returns `product_id/price/URL/offer_id/purchase_state`.

---

## 15. Desired Deterministic Commerce Role

`commerce/decision` 23-branch priority **stays final authority** (hard policy → handoff → creator_sales → product → active offer → purchase 6h → offer 24h outcome-aware → budgets 2/3 → fatigue 390 → negative 403 → low_conf 412 → conversational 427 → commercial_paused 437 → aftercare 450 → rejection 467 → explicit/strong/follow/moderate/relationship/building/no_offer 477-584). Execution 11-gate `execution:87-315` sole `create_offer_serialized` advisory lock. `TOOL_AUTHORITY_PROMPT` `llm_tools:1153` prohibits inventing `price/currency/product/title`. Pricing/URL from `fangate_products.price_minor/sales_url` (mirror `enforcement: price feed here?` vs `Upsert` `dropfans:81`).

---

## 16. Desire / Sales Readiness Model

**LLM-driven `Qwen/qwen2.5:3b` not simplistic `interested true/false`:**

| Dimension | Producer | Scale | Persist | Consumer |
|---|---|---|---|---|
| `relationship_strength` | `derive_relationship_state 94 funnel + purchase + recency + active_offer` | `RelationshipState 11 values` | `LLMContext relationship_state` + Qwen `RELATIONSHIP: warm` | decision `relationship_score 0.60` + `soft_offer` gate |
| `content_interest` | `CommerceSignals.content_interest 0-1` | `-bounded 0-1 finite` | Transient per turn `deepseek:170` | `buying_intent_score` mapping `signals:320` |
| `commercial_interest` | `has_commercial_intent = intent_tags ∩ 8` `signals:327` | bool | `CommerceDecisionContext.has_commercial_intent 132` | `conversational_phase commercial_interest 407` |
| `desire_level` | `relationship_state WARM/BUYING_SIGNAL + commercial_pressure SOFT/MODERATE + TEASE mode + open_threads` **composite** (no single scalar — triad) | SOFT/MODERATE/NONE `relationship:37` + TEASE `response_mode:19` | Transient `ConversationState.tone flirty` + `RESPONSE mode` | `build_qwen3_state_context CONVERSATION` |
| `purchase_readiness` | `buying_intent_score 0.0-1` + `explicit_purchase_request + price_interest 0.80` | 0-1 + bool | Transient → `SOFT 0.55, STRONG 0.80, explicit 0.95 confidence` `decision:162,516,539` | `OFFER_PPV` gating |
| `price_sensitivity` | `price_interest + requested_price + negative_sentiment + price_objection 191` `feedback:69` | 0-1 + enum `PRICE_OBJECTION` | Transient `feedback classify_rejection PRICE` | `strategy price_ok` validator + constraint `no invented discounts 113` |
| `resistance` | `negative_intent_count, negative_sentiment 0.70 → complaint 423, model_uncertainty 0.80 → handoff 419, consecutive_rejections 0-10` | 0-10 count | `dao:528 consecutive loop` + `LLMContext consecutive_rejections` | `NEGATIVE_SUPPRESSED 403, COMMERCIAL_PAUSED 437, REJECTION_ESCALATION 467` |
| `engagement depth` | `recent_offer_count/active, message_count, response_enthusiasm via relationship_engagement 0-1 + last_user_fact 120 chars` | 0-30 msgs | Durable `users.message_count`, `recent_messages` 20 | `relationship:164 ENGAGED if <3d` |

**Design principle:** No `desire = true/false` boolean; readiness is `relationship_strength × content_interest × (1-price_sensitivity)` screened by `signal_confidence 0.30` and `cooldowns`.

---

## 17. Sales-Window Model

**GOOD window (high engagement + affect):** `relationship_score ≥0.60_engaged + WARM, content_curiosity primary_intent, flirt tone, fan-initiated content discussion (explicit_content_request), compliments (emotional_state_recent enjoying), desire TEASE mode, commercial_pressure SOFT, aftercare==none, cooldown clear, cooldown intent false.`

**WEAK window:** `relationship_score 0.40-0.59, short replies, topic changes (open_threads volatile), negative_intent 1, recent offer 12h, pending unclicked recently purchased, financial objection SOFT` — `decision 563→RELATIONSHIP_BUILDING building` or `426 conversational`.

**BAD window:** `short replies (≤3 words) + topic changes (open_threads flip), discomfort → negative_sentiment 0.70 → complaint 423, explicit rejection → HARD 195, financial objection: price_objection 191, busy: last_message_days_ago ~0 not yet encoded, cooldown: hours_since_last_offer <24 → RECENT_OFFER 354, recent purchase 45 → RECENT_PURCHASE, consecutive≥3 → COMMERCIAL_PAUSED 437, has_active_offer → OFFER_EXISTS 333, aftercare pending → AFTERCARE 450.` Each forces `NON-SELLING` `RELATIONSHIP_BUILDING / NO_OFFER / OPERATOR_HANDOFF`.

Implemented via the **same 23-branch decision cascade** — window is not a separate scalar but the **combined result** of `relationship + timing + budget + negative + phase + pressure` inside `decide_commerce_action` — which already does `GOOD→OFFER_PPV/SOFT`, `WEAK→relationship building`, `BAD→queue/handoff`. No `X turns have passed` counter drives sales; `derive_conversation_state` topic check (`work|netflix|popcorn|horny...` in last 8) + `CAPABILITIES` give LLM sales-aware window: Qwen knows `tip eligible: eligible` only when `relationship WARM+`.

---

## 18. Content Intelligence Model

Structured vault metadata today: `file_name, file_type image/video, thumbnail_path, file_path signed URL (~12h), content_tags[] genre, folder_id/name, moderation_status APPROVED, created_at, raw`. **No vision inspection for v1** — titles only.

### Proposed title taxonomy (human-readable, deterministic, LLM-useful)

```
Subject/Outfit  — Setting  — MediaCount × Type  — Presentation  — Mood/Theme  — Occasion/BundleTag
Black Lingerie  — Bedroom  — 3 Photos          — Close-Up       — Tease       — Evening Bundle
Red Dress       — Balcony  — 3 Photos          — Full Body      — Soft        — Date Night Set
White Lingerie  — Bed     — 5 Photos          — Cozy           — Morning     — Cozy Set
Bikini          — Beach   — 4 Photos          — Outdoor         — Playful     — Sunset Pack
```

Same vault item may appear in multiple drops; the **drop title** encodes bundle identity (session == outfit+setting), not per-file title. Naming stays in `fangate_products.title` (human readable), `dropfans_product_id` foreign, `product_type='dropfans'` via synthetic hash `sha256(drop_id)%2^62 103`.

Compatibility: DropFans `create_drop` `name` free-text, no validation beyond non-empty `fangate.py:368`; local mirror `title` stores verbatim; deterministic match does not parse title — product selection via `is_accessible && sales_url` only, so titles can evolve without breaking `product_selection:52`.

---

## 19. Bundle Strategy

**Rule:** `3 pictures / same outfit / same setting / same session / same visual theme → Red Dress Balcony Bundle` — single `drop`.

- **Deterministic identity:** `dropfans_product_id` opaque CUID `drop_id` (`models:244`). `raw JSON {"dropfans_product_id":str, "vaultItemIds":[str], "salesCount":int}` — 3 keys stored. `media_count` dead locally but derivable from `vaultItemIds|length`.
- **Title:** `name` at creation `service:362` → `title` in mirror `114`. Proposed convention `Subject — Setting — N × Type — Presentation — Mood — Bundle/Set`. Human readable + searchable (`WHERE title ILIKE '%Balcony%'`) + LLM useful.
- **Media count:** `len(vaultItemIds)` at creation `1-10` validated `fangate.py:380-387`, but local `media_count` column dead (`dropfans:89` accepted unused); media list lives only in `DropResult media:[{vault_item_id}]` transient `models:218` not persisted beyond `raw.vaultItemIds`.
- **Relationship to source media:** `raw.vaultItemIds:str[]` — join not enforced by FK (vault `id CUID` vs `vault_media_deliveries id BIGINT` mismatch `vault_media 5-15` integer `fangate_media_id` deprecated name). Vault aggregation deduplicates by `media_id int` `aggregate.py:53` (`media.get("id") int`) — modality gap for DropFans CUIDs (should be `dropfans_media_id TEXT` dead columns `migration dropfans_provider 24-29` never written).
- **Pricing:** per-bundle `price (dollars)` → `price_cents int(price*100)` `service:371`. Independent from per-image price.
- **Purchase identity:** `fangate_transactions transaction_id='dropfans:{drop_id}'` `dropfans:207`, `commerce_offers dropfans_product_id TEXT` `migration dropfans_provider 12`.
- **Duplicate prevention:** `create_drop` wrapper does `upsert_dropfans_product` `367` immediately after API success (`allow_download, media_count, result.media_count` dead local) — second `create_drop` with same vault items but different `name` would create new `drop_id` (no dedup on vault item set). `vault_media_deliveries UNIQUE (creator,user,fangate_media_id) 15` prevents duplicate *delivery*, not duplicate creation.

**LLM recommendation stays phrasing-only:** LLM may say `Red Dress — Balcony — 3 Photos` as natural language (seen as `Current product: Red Dress — Balcony — 3 Photos` when `len active==1 752`), but **must not** invent `price/URL/product_id` — validated via `deepseek_response 332/439`.

---

## 20. Purchased-Content Context Contract

| Context | Contract to Qwen | Produced by | Reach |
|---|---|---|---|
| `PURCHASED CONTENT` | `Purchases: N` + last 2 of `Purchases: - Title price — date` filtered but **title lines dropped** (no purchase keyword) — lossy; current `PURCHASED CONTENT` not explicitly labelled in prompt today | `context_assembler:80-94 purchases` → `render:738-746` filtered via `context:282` | Partial |
| `AVAILABLE CONTENT` | Not separately listed — only `Current product: title price` when *single* valid unpurchased remains (`assembler:752 len==1`) — multi-product ambiguity `None` after fix now cheapest rank `238` but still no catalog listing | `assembler:752-756` + `product_selection:238` ranking | Partial |
| `ACTIVE OFFERS` | `Active offer: Title price — state pending/clicked` `749` | Filtered `offer` keyword kept → `COMMERCE: Active offer:` `context:288` | **Live** |
| `ABANDONED OFFERS` | **Now** `Abandoned offer: Title (Nh ago, pending)` `context_assembler:800` when `has_active_offer && age≥48h` + decision `FOLLOW_UP` `528` | Single `render` line, surfaced in `COMMERCE` | **Live** post-C.1-F conversational bridging + P1 |
| `RECENTLY OFFERED CONTENT` | `Rejections: N consecutive + Commercial pause: active + Abandoned` lines serve as proxy; no explicit `last_offered: {title}` list | `assembler:791-794,800` | Partial |

Vault-delivered media (`vault_media_deliveries UNIQUE`) is authoritative `has_user_received_media 172` but **never summarized to Qwen** — Qwen knows `Purchases: N` count, not which `media_id` was seen, so it can re-sell already-purchased drop as new if ranking still picks it (exclusion `purchased_ids` `product_selection:206` now prevents ranking it → disjoint).

---

## 21. Content Matching Strategy

**Qwen suggests relevance, deterministic selects:**

| Stage | LLM vs deterministic | File:Line |
|---|---|---|
| Fan says `red dresses` | LLM *may* echo `Red Dress — Balcony — 3 Photos` as natural language (seen as `Current product: Red Dress ...` when single-product). Title encodes `Red Dress — Balcony — 3 Photos — Evening Bundle` → searchable | `product_selection:52` valid check, not LLM ranking |
| Deterministic ranker | `resolve_commerce_product_with_history 206 excluded purchased → sorted(price_minor or INF, id) cheapest 238` — **not embedding similarity** (fan interests vs title) — deterministic, explainable. LLM `she seems interested in X` not needed. | `product_selection:233-241` |
| Fallback | Multi-product `≥2 unpurchased → cheapest` not `None` (fixed P1-01). If exactly one distinct `product_type='dropfans'` valid → that one. | `product_selection:238` |
| Safety | If `is_accessible false` or `sales_url missing` → `not valid → None 52`, strategy `allow_price/product false 152,165`, deepseek `Verified price: ... only when allow_price 323` → LLM cannot invent link. | `strategy:152,165 deepseek_response:332` |

Fan `red dresses` interest never directly drives `sales_url` — price/URL come from `fangate_products.sales_url` row `service:465`. Multi-product strong inferred `Red Dress` would previously have been `None` → lost PPV; after fix, cheapest unpurchased wins even if `Red Dress` was mid-price — **deterministic > personalized** by design (safe, revenue suboptimal — profile-product affinity embedding cosine deferred per P1-04 `is_repeat_purchase_eligible` already wired, not auto-offer).

---

## 22. Objection Strategy

`classify_rejection(negative_intent_tags, negative_sentiment, price_interest, intent_tags) → HARD|SOFT|PRICE_OBJECTION|UNCERTAIN` `feedback:178 + pipeline:324` exact `hesitation+price≥0.60 → PRICE 191, rejection → HARD 195, complaint→HARD 199, hesitation→SOFT 203`.

| Objection | Detected | Handling | Follow-on |
|---|---|---|---|
| `too expensive / can't afford` | `PRICE_OBJECTION` `feedback:71,191` | Pipeline `mark_offer_declined reason=price_objection 541` for `HARD/PRICE` only; `constraints never invented discounts 113,122` test `300` blocks | `decision 467 consecutive≥3 → REJECTION_ESCALATION + commercial_paused` `131` longest cooldown |
| `not now / maybe later` | `SOFT` `feedback:70,203` `maybe later` | **NO** `mark_offer_declined` (`541` `HARD/PRICE` only) | `negative_count 1 <2 → no suppression 402` |
| `send free` | **`asks_for_free_content`** prompt field `deepseek:70` but model `extra forbid` → **DEAD** (now fixed to optional `False, 1.0, None` `signals:174` and `RELATIONSHIP_BUILDING free_content_request 309`) | Suppress commerce | `decision:309 early free` before creator_sales |
| `not interested → HARD` | `HARD 95,195` | persisted + commercial pause | `decision:437` |

No persuasion script — rule 8 `deepseek_response:205 accept gracefully, never repeat` + `constraints` enforce.

---

## 23. Cooldown Strategy

`rate limit 1/s burst5` Lua `db/redis:370 ZSET` per-peer Throttle; `24h offer budget 2 / sales 3` `decision:160` hard stop `370,377`; `offer 24h outcome-aware 354 RECENT_DECLINE/IGNORE/COOLDOWN_ACTIVE 359` + `purchase 6h` `342`; `offer fatigue 390 intent<0.80` → `OFFER_FATIGUE`; `aftercare pending → suppression unless explicit 450`; `rejection 3 → REJECTION_ESCALATION 467`; `tip fatigue non-existent history → now tool_audit 30d` (`dao:559`); `question debounce 3s` first msg wins. All enforced **before send** (`decision:269 pure`). `cooldown ≠ termination` — `RELATIONSHIP_BUILDING` returns `RELATIONSHIP_BUILDING LOW warm pitch suppressed: Do NOT pitch` but conversation continues via Qwen `REACT`.

---

## 24. Purchase Delivery Strategy

**When fan purchases:** `reconcile_dropfans_sales 251` → `attribute_purchase_from_webhook 249` atomic `SELECT pending/clicked fail 0 or >1, UPDATE purchased 170, UPDATE fangate_transactions.user_id where NULL 183, INSERT ppv_analytics_daily 197` → `advance_funnel_to_converted idempotent 46→converted` → `mark_aftercare_pending none→pending 892` → `schedule_follow_up 24h 282 dedup post_purchase_followup:{txn}` → `enqueue_purchase_confirmation post_purchase:{txn}:{user} 97`.

**How DropFans provides access:** **Gap** — DropFans `list_vault` individual `file_path` signed URL **not** per-dropsale grant; the only per-purchase URL today is the same `buy_url` `DropfansDrop.buy_url` (`models:253`). Vault delivery synthesizes `fangate_media_id sha256("dropfans:{product_id}")%2^31 425` and sends checkout link `479 Your content is ready: {sales_url}`. Vault per-media `download_url/file_path` per `VaultItem` is **not fetched** via `client.get_links` (tip-links only) or `vault/download-url` endpoint (no such). **Missing contract:** DropFans `vault_media_deliveries dropfans_media_id/text` dead columns `migration dropfans_provider 24-29` never written (`docs P2-3`), `db/vault.py` never touches them. True purchased media `download_url` delivery would need `GET /vault/{id}` for that fan or DropFans `buyer_email`-scoped grant API — not exposed. `vault/service:126 record_delivery` idempotent `UNIQUE (creator,user,fangate_media_id)` prevents duplicate delivery, `finalize_delivery pending→sent 80` marks success, `release_stale 122 pending 5m` recovers.

---

## 25. Aftercare Strategy

`aftercare pending/sent + purchases>0 → AFTERCARE_PHASE 450` suppression unless explicit buy. Now **surfaces** via `context_assembler:790 Aftercare: pending` and Qwen `COMMERCE: Aftercare: pending` (`context:283`) + `COMMERCIAL OBJECTIVE: aftercare` (`objective.py`). LLM may handle `gratitude/relationship continuation/natural aftercare` with `RELATIONSHIP_BUILDING LOW` tone (rule `Lead with warmth 367`) and **not** immediately upsell `strategy 249 MODERATE` blocked. `mark_aftercare_completed never invoked` (grep 0 caller) — deferred; `AFTERCARE_POSITIVE_RESPONSE` etc enum values defined (`feedback:84`) but `feedback:21 _behavioral_store placeholder` lost on restart.

Appropriate triggers: `purchased` true + `aftercare pending` → `41 relationship ladder: purchase_count≥1 151 → PURCHASED` + aftercare suppressor → appreciation, not hard sell.

---

## 26. Re-engagement Strategy

**Abandoned offers:** `has_active_offer && age≥48h → Abandoned offer: {title} ({Nh} ago)` `context_assembler:790`, Qwen mode `CALLBACK` when fan returns with `netflix/popcorn/saturday` (`response_mode:76`), decision `FOLLOW_UP 528` when `previous_offer_status in {declined,revoked,clicked,expired}` + cooldown clear. **No auto-spam**: not `BUY NOW` loop — callback harks back warmly `You mentioned Netflix and popcorn last time...`.

**Old conversations / previous purchases:** Poll via `get_timing_context 498 last_sale,last_purchase` + `build_llm_context recent_messages 30` history retained within `QWEN3_TOKEN_BUDGET conversation 800` (3 assistant turns). Dormant `derive_lifecycle gap≥48h → RETURNING` `conversation_state:64`.

**Cooldowns:** Respect 24h offer + 6h purchase + 3-consecutive rejections, same as initial sale.

---

## 27. Tip Strategy

Canonical `get_checkout_links(creator) 906 → telegram.tip (preferred 911) else web.tip 914, startswith http 925`, `dedup tip:{c}:{u}:{md5[:12]} 937 + is_send_duplicate 943 3600` → `tip_content hard-coded 950 → enqueue_send 952`. Tip relation to commerce: `Tip eligible: yes — {relationship_state WARM 356} + tip fatigue (now real `sent/ignored/hours` via `dao:559`)` + contextual `appreciation/how_to_support 340` 12h. **Cooldown dead fixed** — now 24-72h graduated `72/48/24 * (1+ignored*0.5) 268` fires.

Fits into `relationship / commerce(no_sale) / cooldown` hierarchy — tip only when `commercial_pressure SOFT / engaged` not hard sell.

---

## 28. Authority Matrix (Actual Repository — Adjusted)

| Capability | LLM | CRM | DropFans |
|---|---|---|---|
| Understand conversation | **YES** | | |
| Detect interest (purchase_intent etc) | **YES** (LLM) → deterministic `low_information` fallback | **YES** (scoring) | |
| Recommend strategy | **YES** (LLM conversational objective observation `objective.py`) | **YES** final `CommerceDecision/Strategy` | |
| Recommend content (candidate by title) | **YES** (LLM may echo `Red Dress — Balcony — 3 Photos` as text when `Current product:` shown) | **YES** deterministic rank cheapest (now live `product_selection:233`) | |
| Decide commercial eligibility | **NO** | **YES** `eligibility → hard deny 296` | |
| Product ID | **NO** | **YES** `fangate_products` row `state:205` | **YES** mirror |
| Price | **NO** | **YES** `price_minor` `execution:251` | **YES** `drop.price` dollars |
| Checkout URL | **NO** | **YES** `sales_url or build_checkout_url` `execution:239` | **YES** `buy_url` |
| Create paywall (`POST /drops`) | **NO** | **NO** — operator/dashboard `dropfans/service:347 create_drop` only, never LLM | **YES** `POST /drops 426` |
| Purchase status | **NO** | **YES** `commerce_offers.state purchased` + `fangate_transactions.user_id` | **YES** `drops/check-status paid:true` filtered 492 |
| Deliver purchased content | **NO** | **YES** `vault reserve/finalize + send_file` `service:169,80 + main:259` | **YES** `vaultItem.file_path` signed URL (when available) |
| Creator isolation | **NO** | **YES** `WHERE creator_id` everywhere `dao:88,136,200,392` | **YES** `encrypted_api_key` per creator |
| AUTONOMY_ENABLED | **NO** | **YES** `workers:391 + agent/tools:94 + core/config 105` | |
| Deduplication | **NO** | **YES** advisory lock + `is_send_duplicate` 3600 `llm_tools:937` + `scheduled dedup post_purchase_followup:{txn}` | |
| Tip URL | **NO** | **YES** `get_links telegram.tip 911` | **YES** `GET /api/external/links 582` |

`Understand`/`Detect` remain LLM; `Decide eligibility/Product/Price/URL/Offer/Purchase` remain CRM/DropFans — **no violation**.

---

## 29. Gap Inventory (post-D-01..D-03 — P0 closed)

| # | Gap | Before | After D-01..D-03 |
|---|---|---|---|
| Tip fatigue hard-zero | P0 fatigue dead, tip tip tip | **CLOSED** — `tool_audit 30d` wire `dao:559` |
| Prompt-model drift (4 fields) | 100% `invalid_payload` if faithful | **CLOSED** — `CommerceSignals` `+4 optional` |
| Aftercare dropped | Prompt never saw `Aftercare: pending` | **CLOSED** — `context_assembler 585` reads + `memory 283` filter includes |
| Multi-product `None` | 2+ unpurchased → lost sale, autonomous skipped | **CLOSED** — cheapest `sorted(price, id)` rank |
| Qwen never knows sales job | `COMMERCIAL OBJECTIVE` absent | **CLOSED** — `COMM OBJECTIVE: relationship→build_desire→present_offer...` `workers:580` |
| Desire single `TEASE` | No ladder | **PARTIAL** — `build_desire` now `COMMERCIAL OBJECTIVE` bridged |
| Abandoned re-engagement | No poll | **CLOSED** — `Abandoned offer: {title} 48h` `context_assembler:800` + `FOLLOW_UP` decision |
| Purchase attribution | Poll `reconcile_dropfans_sales 251` live but funnel never advances | **CLOSED** for existing purchases (fan `new` at 47 proves `new` persistence until purchase still) |
| Upsell tier | No ladder | **PARTIAL** — cheapest cross-sell, `repeat_purchase_eligible` still surfaced not auto-offer |
| Vault `download_url` per-purchase missing | synthetic `sha256→fangate_media_id` | **REMAINING** — per-item `file_path` signed URL not fetched; uses `sales_url` |
| `description` lost | DropFans `price*100` stored, `description` ignored `service:367` | **REMAINING** — `description` not persisted |

---

## 30. P0/P1/P2/P3 Classification — After Surgical Fix

| Severity | Before | After D-01..D-03 |
|---|---|---|
| **P0** money/authority/safety | Tip spam, free-content waste, aftercare unwired, multi-product None, Qwen sales-blind | **0 remaining P0** (4 closed) |
| **P1** materially weakens | Product ranker none, abandoned none, soft-intent never surfaces, qualification→commerce dead | **2** (desire multi-turn ladder still single, upsell ladder tiered) |
| **P2** quality/optimization | Currency hardcode USD, tip new-URL bypass, gpt-4 tokenizer on Qwen, `CURRENT product: only len==1` | **3** (currency, tokenizer, abandoned 48h vs desired 24h True? acceptable) |
| **P3** cleanup | Budget dup, fangate_products name legacy | **2** (same, deferrable) |

---

## 31. Phase-by-Phase Implementation Plan (Dependency-Ordered)

```
PHASE 1  Forensic architecture map                        [DONE — this document]
   └─ commerce conversation/telemetry/vault forensics → current
PHASE 2  Commerce state + conversational sales intelligence
   └─ tip history wire via tool_audit_log 30d (dao 559) [DONE D-01]
   └─ aftercare surfacing (context_assembler 585 + memory 283) [DONE P0-03]
   └─ desire/qualification ladder — COMMERCIAL OBJECTIVE bridge (objective.py NEW, worker 580)
   └─ signal persistence: structured state only (purchase_intent etc via decision, not raw prose)
PHASE 3  Vault metadata + content taxonomy + naming
   └─ bundle identity raw.vaultItemIds + title convention (Subject — Setting — Count×Type) — drop name free-text, no validation beyond non-empty
   └─ Needs: validate title taxonomy in dashboard POST /dropfans-drops 368
PHASE 4  Content matching + candidate selection
   └─ Product ranking deterministic cheapest (product_selection 233) — single turn cold fix
   └─ Next: embedding cosine ranking vs profile interests (defer — no vector for vault titles yet)
PHASE 5  Desire / qualification / sales-window intelligence
   └─ Uses existing relationship_state + response_mode + question_policy + COMMERCIAL OBJECTIVE; no new window scalar
   └─ Reads signals already via decide_commerce_action 23-branch; Qwen now gets objective via worker injection
PHASE 6  Offer orchestration + DropFans paywall
   └─ Already wired: decision 269 → strategy 176 → execution 87 → selection 228 (only EXECUTED → USE_COMMERCE_RESPONSE)
   └─ Requires no change for vault media count fix (media_count dead, vaultItemIds is truth)
PHASE 7  Purchase attribution + Telegram delivery
   └─ Live: reconcile_dropfans_sales 251 → attribute_purchase 249 atomic → funnel converted 46 → aftercare pending 892 → schedule 24h 282
   └─ Gap: vault per-item download_url not fetched — change purchase delivery to fetch list_vault download_url per vaultItem → send_file per vault path (next)
PHASE 8  Aftercare + objection handling + cooldown
   └─ Objection: feedback classify_rejection 178 → pipeline 324 → decision 403/467/437 already live, no persuasion recovery; can add cooldown already 24h/6h
PHASE 9  Re-engagement + repeat commerce
   └─ Abandoned 48h surfaced (context_assembler 790), FOLLOW_UP 528 live, but auto-nudge cron not yet; next: scheduler_worker poll pending>48h unclicked → schedule FOLLOW_UP once
PHASE 10 AI conversational integration (C.1-F already)
   └─ Reception: ABILITY → done
PHASE 11 Adversarial testing
   └─ test_sales_intelligence 19+10, test_sunny 42, test_forensic 53 already green
PHASE 12 Shadow/canary evaluation — Qwen vs Gemini identical deterministic state (prep)
PHASE 13 Production promotion gate — checks: creator isolation, hard-flag caps, idempotency, DropFans-only
```

Dependency order above is **correct** — Phase 2 must be `tip/aftercare/multi + objective bridge` before vault naming (3) because vault media count `5×10` gaps are cosmetic until content matching (4) is wired. Honor it.

---

## 32. Test Strategy

| Layer | Tests | Status |
|---|---|---|
| Sales intelligence new | `test_sales_intelligence.py 19 → 29 with D-01..D-03` | 29 passed (1 warning) |
| Forensic remediation + sunny 42 | `test_forensic_remediation 53 + test_sunny 42` | `124 passed` (combined) |
| Commerce decision/state/orchestrator/pipeline/execution | `test_commerce_decision/state/orchestrator/pipeline + test_commerce_*` | `332+` relevant slice |
| Commerce pipeline integration | `test_commerce_pipeline` 86, `test_worker_commerce_integration` | Existing |
| Provider Ollama/Gemini | `test_llm_provider 70` incl fallback `test_commerce_deepseek` json contract | Existing |
| Vault | `test_vault 4` | Minimal; vault/media delivery not part of sales intelligence surge |
| Full targeted bundle | `test_forensic + sunny_conversational + commerce_decision/state/orchestrator/pipeline + test_llm_{tools,provider}` | `570 passed, 1 warning` (prior D-01..D-03) |

Adversarial cases already signed:

- `asks_for_free_content True with purchase 0.9 → relationship_building free_content_request`
- **Tip spam after 1h now blocked by 24-72h graduated check** (fake: `tip history wired; fatigue >=2 → INELIGIBLE`)
- **No cross-creator product** — existing `list_fangate_products(creator_id) scoped` + isolation proof `state 205`

---

## 33. Observability Strategy

`core/telemetry.py:19 GenerationTelemetry` extended `+8` commercial fields `commercial_objective, commerce_action, sales_pressure, product_selected, offer_presented, tip_presented, objection_type, purchase_state` (dataclass + `to_dict 92`, no raw content, no secrets, ID-only). Worker sets `commercial_objective` from `derive_commercial_objective(selection)` `workers:580` and `offer_presented tip_presented` booleans post-selection; `generation_telemetry` table widening **deferred** (22→30 columns migration not worth blocking Phase 1). Event bus `ai.generation_completed` invariant preserved (`AGENT.md` after `enqueue_send`).

---

## 34. Rollback Strategy

Feature-flag zero-migration reversible for Phase 2:

- Comment out `COMMERCIAL OBJECTIVE` injection `workers:580` (7 lines) → Qwen reverts to prior non-sales-aware but authority-preserving.
- Revert `commerce/dao tip 3 queries` to `hard-zero 0/None` (1 block), `context_assembler 585` to hard `none`, `product_selection 233` to `return None`, `signals 4 optional fields` to `extra forbid` strict.
- No DB change to revert (existing `tool_audit_log` read-only, no write); no queue/worker replacement; no key rotation.
- Canary never activated — safe to gate `COMM OBJECTIVE` behind `if _settings.ai_agent_canary_enabled` if needed.

Canary `NOT ACTIVATED`, `ARCHITECTURE: NO REDESIGN` — rollback is `git revert` of 7 files.

---

## 35. Architecture Invariants (Preserved)

`creator isolation WHERE creator_id`, `AUTONOMY_ENABLED true kill switch`, `DropFans authority sole (Fangate 410)`, `product authority fangate_products valid`, `price authority price_minor int`, `URL authority sales_url or build_checkout_url telegram buy_template, validation http(s)`, `offer idempotency pg_advisory lock ppv_offer:{creator}:{user}:{product}`, `send queue dedup md5(user:msg:tgId)`, `Redis Streams XAUTOCLAIM 60s/30s`, `stale-message recovery`, `memory persistence users/message_count, conversation_summaries 200, user_profiles facts`, `operator handoff negative_sentiment 0.70`, `rate limiting 1/s burst5`, `scoring safety price_mention→0.1`, `reconciliation paid filtered 492`, `tip canonical telegram.tip` — all verified live.

---

## 36. Explicitly Deferred Items

- Vault `description` not persisted (`dropfans:81 price*100` stored, `description` `client:443` ignored) — defer until bundle taxonomy editor needs it (tags already cover contentTags).
- Vault `media_count` dead column (`dropfans:89`) — `raw.vaultItemIds.length` is truth.
- `description` loss `service:367`, `media_count` param dead, `price` dollars vs `price_minor` cents legacy naming.
- `tool_audit_log` `tip fatigue GDPR?` — 30d window is observational; durable `tip_events` table deferred as non-blocking (current solution uses existing table, survives pool, not Redis-volatile).
- Cross-sell no price-tier comparator pending; repeat flag not auto-offer.
- `gRoc`/`openai_api_key` `USE_GROQ true` legacy Groq shim vs Ollama authoritative — shim untouched per `preserve provider abstraction`.
- Bundle naming validation (drop name free-text, `fangate.py:368` non-empty only) — defer naming convention enforcement until `dropfans/service:367 upsert` validates taxonomy regex.
- Vault `download_url` per-purchase delivery (synthetic `sha256→fangate_media_id`) — pre-audit already uses `sales_url` link, true per-item download requires `GET /vault/{id}` for buyer and `complete_video_upload` Tus flow `client:371` — next Phase 7 task.

---

ROOT STATUS:
CURRENT COMMERCE STATE: Deterministic 23-branch PURE decision + ranked cheapest-unpurchased for multi-product (not None), DropFans build_checkout sole, advisory-lock idempotent, sale bounded before LLM

DESIRED COMMERCE STATE: Same deterministic core, but conversational Qwen must receive COMMERCIAL OBJECTIVE + abandoned/aftercare surface to be commercially aware — now bridged (~40 tok system), no FANGATE dependency

PRIMARY ARCHITECTURAL GAP: Conversational LLM (95% of turns, ~800-tok Qwen prompt) had zero sales-pressure instruction; commerce PPV copy only runs on the 5% OFFER_PPV+EXECUTED bypass — warm fans stayed in generic rapport until explicit buy (conversion gap).

TOP P0 FINDINGS: Tip spam after 1h (fatigue dead, 24-72h design now wired via tool_audit 30d) + asks_for_free_content 100% low_information fallback (prompt-model drift fixed) + aftercare dropped (now Aftercare: pending) + multi-product None (now cheapest rank)

TOP P1 FINDINGS: Product ranker cheapest heuristic (interest-weighted embedding deferred), abandoned auto-nudge cron not yet (context surfaced only after 48h, FOLLOW_UP decision already live), soft-intent never surfaces as casual mention (strategy → LLM not yet for Qwen path before COMMERCIAL OBJECTIVE bridge), qualification learns profile but never re-enters deterministic (interests vs product title not ranked), aftercare suppression engine live but Qwen aftercare handling still generic appreciation

VAULT CONTENT GAP: DropFans /vault + /vault/folders + /drops CRUD live (metadata file_name/file_type/file_path signed 12h, content_tags, folder_id, moderation APPROVED/PENDING, vaultItemIds str[]), but drop name free-text (no taxonomy validation), local mirror stores only name/price_cents/buy_url/is_downloadable/raw CUIDs/media salesCount (media_count dead, description lost, no per-media title), synthetic fangate_media_id SHA256 for vault delivery

PURCHASE DELIVERY GAP: Offer → DropFans poll/attribution → funnel converted → aftercare pending → confirmation `post_purchase:{txn}:{user}` + vault `reserve→finalize UNIQUE(creator,user,fangate_media_id)` + fallback `sales_url` link (today). Per-item `download_url/file_path` per vault item for that buyer not fetched — change to `GET /vault/{id}` per vaultItem → send_file per path is the next Phase 7 contract.

AI SALES INTELLIGENCE GAP: Recommender is price-ranking only (not interest-weighted embedding cosine, not is_repeat_purchase-aware auto-tier), desire ladder is `RELATIONSHIP→INTEREST→DESIRE(SOFT low, TEASE) → OFFER_PPV` single TEASE mode (no staged anticipation queue), objection handling suppression-only (no persuasion recovery script, intentional), price sensitivity `price_interest 0.80 → user_asked_about_price true` binary not graduated urgency, sales window = decision outcome (no separate scalar window).

REQUIRED NEXT PHASE: PHASE 2 — Commerce state + conversational sales intelligence (tip/aftercare/multi + objective bridge) — 4 fixes above already applied; next is residual P1: product rank → interest-weighted embedding reapplier, abandoned auto-nudge scheduler, qualification interest→product hint, objection mapping already 4-type but follow-up not yet auto-scheduled outside manual proposal

PRODUCTION CHANGES:
NONE

CANARY:
NOT ACTIVATED

PROVIDER:
UNCHANGED (ollama/qwen2.5:3b authoritative, Gemini fallback)

ARCHITECTURE:
NO REDESIGN
