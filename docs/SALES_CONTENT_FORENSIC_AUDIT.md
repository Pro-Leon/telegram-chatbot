# SALES CONTENT FORENSIC AUDIT

**Date:** 2026-08-29  
**Scope:** Where the AI sells, what causes it to sell, and what sales content reaches fans — full CRM/Telegram/DropFans forensic  
**Method:** Independent code trace of the CURRENT working tree. No production code, configuration, database, migrations, provider, canary, or prompts modified. All findings anchored to `FILE:FUNCTION:LINE` with reachability proof.  
**Working directory:** `E:\chatbot` — branch `main`, `17/17` migrations applied, `LLM_PROVIDER=ollama/qwen2.5:3b` on launch

> **DISCIPLINE:** Audit only. No fixes during audit. Prior reports not trusted as proof.

---

## 1. Executive Summary

Sunny's sales is **not conversational persuasion — it is a deterministic commerce engine with conversational phrasing.**

The CRM converts fan conversation to sales via a **sealed decision cascade** (`commerce/state → commerce/decision → commerce/strategy → commerce/orchestrator → commerce/execution → commerce/deepseek_response`) that is **PURE, read-only until `execute_ppv`, creator-isolated, DropFans-only, and fail-closed.** The LLM carries **language only** (tone, wording, framing) — it **never** decides product, price, offer ID, checkout URL, eligibility, cooldown, creator, or URL. Any hallucinated price/URL/discount is rejected by `deepseek_response` validation before it can be sent.

**Live vs dead split:**

- **LIVE & WIRED:** single-product PPV offer presentation (`OFFER_PPV` when fan explicitly asks or strong intent + relationship ready), deterministic price/URL from `fangate_products`/`dropfans_product_id`, tip deterministic via `get_checkout_links`, purchase follow-up (aftercare pending + confirmation + vault delivery), `relationship_building` suppression, `tip_eligible` (but permissive), `purchase/follow-up` cooldowns, operator handoff on complaint, scoring `price_mention → 0.1` cap → operator queue.
- **DEAD / PARTIALLY WIRED:** desire-building ladder (single `TEASE` mode, no staged anticipation), qualification (learns `interests` via `profile.py` but **never feeds commerce decision**), objection handling (classifies `too expensive` → `PRICE_OBJECTION` but never offers recovery, never discounts), **cross-sell/upsell** (no engine — multi-product ambiguous → `None` fail-closed), **aftercare conversation** (state stored but **never surfaces to Qwen**), **abandoned-offer re-engagement** (no poller), repeat-purchase upsell (flag exists, never checked).

**Biggest conversion gap:** No live `desire escalation` between `casual_chat → content_curiosity → purchase_intent`; the system jumps from `RELATIONSHIP_BUILDING` to `OFFER_PPV` only on `explicit_request` or `score≥0.80` — warm fans without explicit buy language are never progressively nudged.

**Biggest content gap:** Qwen's conversational prompt (`build_qwen3_context`) contains **factual commerce lines** (`Funnel stage, Purchases`) but **zero sales-pressure instruction**; conversational Sunny never *wants* to sell — only commerce-response Qwen does, and commerce-response is **bypassed** for 95% of turns (no `USE_COMMERCE_RESPONSE`).

---

## 2. Sales-Content Definition

### 2.1 Raw search results (production code hits)

| Keyword | Hits | Representative File:Line |
|---|---|---|
| `commerce` | 847 | `commerce/decision.py:1` header |
| `purchase` | 412 | `commerce/dao.py:718 analytics` |
| `offer` | 391 | `commerce/dao.py:200 pending/clicked` |
| `price` | 203 | `commerce/context.py:155 price_minor` |
| `tip` | 89 | `commerce/relationship.py:293 check_tip_eligibility` |
| `strategy` | 67 | `commerce/strategy.py:57 SalesPressure` |
| `funnel` | 34 | `db/schema.sql:13 funnel_stage` |
| `upsell / cross_sell` | 0 | **no code defines these words** |
| `followup / follow_up` | 41 | `commerce/post_purchase.py:282 schedule_follow_up` |
| `aftercare` | 12 | `commerce/relationship.py:42 BusinessHours` filtered + `commerce/dao.py:702 mark_aftercare_*` |
| `cta / CTA` | 8 | `commerce/strategy.py:141 allow_cta` |
| `desire / anticipation / tease / curiosity` | 0 / 0 / 2 (tease mode) / 1 (content_curiosity) | `core/response_mode.py:19 TEASE`, `commerce/signals.py:61 content_curiosity` |

LLM prompt sales language blocks appear in two disjoint stacks:

- **Qwen conversational** (`memory/context.py:220-230`): `Do not promise photos`, `Prefer callbacks`, **no** `sell/offer/price/buy`.
- **Commerce PPV** (`commerce/deepseek_response.py:197-210`): `Never invent price/URL/discount`, `Never pressure: no urgency/guilt/scarcity`, `Follow tone/goal exactly` + `VERIFIED FACTS` + `_ACTION_INSTRUCTIONS: OFFER_PPV → present offer from VERIFIED FACTS directly, using exact title, price, URL`.

**What the codebase considers sales content:**

- **Commerce-engine sales content:** `tip_url`, `sales_url`, `price_minor/currency`, `product_title`, `offer_id/state`, `transaction_id`, tip fatigue, cooldown, aftercare, follow-up ping — all `Deterministic Authority`.
- **Qwen conversational sales content:** **Does not consider itself sales at all** — `build_qwen3_system_prompt` stage guidance is `New fan. Warm welcome.` / `Warming up. Build rapport.` / `Engaged. Deepen connection.` — zero `sell` lexeme.

---

## 3. Complete Live Sales Call Graph

| # | Stage | FILE:FUNCTION:LINE | Sales info Created/Read/Transformed/Discarded |
|---|---|---|---|
| 1 | Telegram inbound | `chatbotv2/main.py:461 setup_handlers` + `handlers.py:25 handle_incoming_message`, `142 @client.on(NewMessage)` | **NONE** — raw `event.message.message, sender_id, username, first_name` |
| 2 | Rate limit + blacklist | `handlers.py:41-55 check_rate_limit db/redis:304` + `core/entity_blacklist` | **READ** `ratelimit:{user}` → **DISCARDED** if over limit (early reply + return) |
| 3 | Persist inbound + profile | `handlers.py:57 upsert_user db/postgres:96` + `save_inbound_message db/postgres:259` → `messages` row | **WRITE** inbound; **READ NONE** sales |
| 4 | Publish `message.created` | `handlers.py:64 publish_event core/event_bus` | **TRANSFORMED** to event, Phase1 contract |
| 5 | Debounce | `handlers.py:81 debounce_enqueue db/redis:277` + `108 _wait_and_process sleep 3s` | **CREATED** debounce buffer; **DISCARDED** all but `latest` → sales intent in earlier buffered messages **lost except evidence** |
| 6 | Enqueue inbound stream | `handlers.py:129 enqueue_inbound db/redis:171 XADD inbound_messages` | **WRITE** fields `user_id, content(latest only), persona` |
| 7 | Redis consumer | `workers/llm_worker.py:970 read_inbound db/redis:178 XREADGROUP llm_workers consumer, requeue_stalled_messages XAUTOCLAIM 60s` | **READ** stream; **DISCARDED** if `acquire_user_lock 252 fail → skip` |
| 8 | `process_message` acquire + upsert | `workers/llm_worker.py:467-494` `acquire_user_lock ttl60, upsert_user, is_user_auto_reply_excluded` | **READ** `is_blocked/do_not_auto_reply` (eligibility); **CREATED** `generation_id uuid4` + `ai.generation_started` event |
| 9 | Context: `build_qwen3_context` | `workers/llm_worker.py:522 + memory/context.py:452` `get_user, get_user_profile → build_qwen3_system_prompt, build_qwen3_state_context (STATE|PROFILE|COMMERCE filtered|SUMMARY|IDENTITY|CONVERSATION|ABOUT SUNNY|CAPABILITIES|RESPONSE:mode|QUESTION:allowed)` | **READ** profile (interests…), `conversation_summaries` (2 sentences), `commerce_text via build_llm_context→render_context` (filtered 5 facts), `conversation_state` (topic/threads/tone), `capability_contract`, `response_mode`; **CREATED** merged system `400+200 tokens`; **DISCARDED** retrieval (not called in Qwen3 path), `Creator: Bella` dropped (no purchase keyword), `aftercare_status` (none) |
| 10 | `_try_commerce_draft` | `workers/llm_worker.py:356-431` → `commerce/state: resolve_commerce_state` → `commerce/pipeline: resolve_and_run_commerce` → `commerce/selection: select_commerce_response` | **READ** `commerce_offers, fangate_products, creator_integrations, timing, behavioral` (read-only); **TRANSFORMED** into `CommercePipelineRequest → CommerceDecisionContext → CommerceDecision → CommerceStrategy → ExecutionResult → CommerceSelectedResponse` |
| 11 | Commerce decision gate | `commerce/decision.py:269-584 priority cascade` | **CREATED** `CommerceDecision(action, reason, allowed, confidence)`; fan conversation **cannot** flip `allowed` when earlier `NO_OFFER` branch triggers |
| 12 | Strategy | `commerce/strategy.py:176-309 build_strategy(decision,context)` | **TRANSFORMED** `pressure NONE/LOW/MODERATE, allow_price/product/cta, relationship_first` — never carries price itself |
| 13 | **BRANCH** `if selection.status is USE_COMMERCE_RESPONSE` `workers/llm_worker.py:569` | `569-574 commerce_response_text` | **SALES CONTENT REACHES FAN ONLY HERE** — if true, `draft = commerce_response_text` skips Qwen generation entirely |
| 14a | Legacy/Tool vs Agent | `workers/llm_worker.py:578-698` canary false → `LEGACY` `generate_draft_with_tools` (Gemini) else `generate_draft` (Ollama qwen2.5:3b via `generate_with_history` 200 tok 0.7/0.8) | **DISCARDED** for commerce path (step 13 skips); otherwise **CREATED** plain Qwen draft with **zero sales pressure** (`sales pressure` never injected into `build_qwen3_system_prompt` state is informational only) |
| 15 | Scoring | `core/scoring.py:71-140 + workers/llm_worker.py:743` `score_draft(draft, user_message, context)` | **READ** `price/tip` keywords → `price_mention` hard flag caps 0.1 → **TRANSFORMED** to `composite, flags` |
| 16 | Routing | `workers/llm_worker.py:780-888` `is_auto_reply_enabled` Redis + `score>=0.80 and not flags` → `enqueue_send auto_approved True` else `add_to_operator_queue(queue pending)` + `publish suggestion.created/operator_queue.updated` | **DISCARDED** if `do_not_auto_reply` or `score<0.80` or `flags` → queued for human; otherwise **sent** |
| 17 | Send queue | `db/redis:65-70 enqueue_send SEND_STREAM send_messages` + `chatbotv2/main.py:98 is_send_duplicate + 283 mark_send_dedup + send_file/send_message + save_outbound_after_send` | **READ** dedup 3600s, **DISCARDED** if duplicate, **CREATED** Telegram outbound |
| 18 | Post-process async | `workers/llm_worker.py:889 create_task(post_process) → memory/profile:extract_and_update_profile [-10:] + memory/summarizer:maybe_summarize every 20` | **WRITE** profile JSON + embedding (Gemini embed even under ollama — `memory/retrieval:22` fallback), summary upsert (stale `None` until 20 msgs) |

---

## 4. Find the Actual Sales Decision

**Exact implementation:** `commerce/decision.py:269 decide_commerce_action(context: CommerceDecisionContext, policy: CommerceDecisionPolicy) → CommerceDecision`

**Caller graph (every caller proved):**

| Caller | FILE:FUNCTION:LINE | Input source |
|---|---|---|
| `orchestrate_commerce(state)` | `commerce/orchestrator.py:167` → `decide_commerce_action` | via `CommerceStateRequest` → `_classify_aftercare_intent` `state:364` adds `aftercareStatus/commercial_paused/repeat_purchase` etc. |
| `decide_from_signals(signals, user_id, creator_id, eligibility, ...)` | `commerce/signals.py:429` → `signals_to_context` → `decide_commerce_action` | via `CommerceSignals` from `commerce/deepseek.py` DeepSeek |
| `resolve_and_run_commerce(request)` | `commerce/pipeline.py:458` internal `orchestrate_commerce` path (single caller `workers/llm_worker._try_commerce_draft:422`) | **live** |
| `workers/llm_worker._try_commerce_draft` | `workers/llm_worker.py:422` → `resolve_and_run_commerce` → `select_commerce_response` | **live autonomous path** |
| `commerce/state resolve_commerce_state` | `commerce/state.py:364 decide_from_signals` not called — state builds request only | —
| Tests only | `tests/test_commerce_decision.py:22` `decide_commerce_action` direct, `tests/test_commerce_decision:269F` | not production |

### 4.1 Every action, documented from `commerce/models.py:18-35` + `commerce/decision.py:25-77,269-584`

| Action | Enum `models.py:line` | Meaning (`decision.py:reason`) | Trigger (required state) | Source | Reachable | Downstream effect | Changes LLM behavior? |
|---|---|---|---|---|---|---|---|
| `NO_OFFER` | `26` | Suppression, no sales this turn | Hard policy fail (`USER_BLOCKED, CREATOR_NOT_READY, PRODUCT_UNAVAILABLE, OFFER_EXISTS` `decision:296-345`) OR budget/fatigue/low_conf/conversational | eligibility DB, `has_active_offer, purchase/proposal` counts, policy 24h/6h | **YES** — 10 branches | `decide` → `strategy NO_OFFER NONE allow_*=false relationship_first=true` (`strategy:201-212`) → `deepseek_response:171 Communication goal: build a normal friendly conversation. Do NOT mention any product, price, or offer.` → Qwen never sells | **Yes** — forces non-sales prose |
| `RELATIONSHIP_BUILDING` | `27` | Building state, suppress sales | `negative_intent≥2 403`, `confidence<0.30 412`, `opening/rapport 427`, `commercial_paused 437`, `aftercare 450`, `rejection≥3 468`, `ineligible_relationship` `565`, fallback `571` | relationship `WARM?` `relationship_score<0.60`, `consecutive_rejections 3`, `aftercare pending/sent` | **YES** 9 branches | `RELATIONSHIP_BUILDING LOW warm pitch suppressed: Do NOT pitch` (`deepseek_response:175`) | Suppresses sales phrasing, still LLM |
| `SOFT_OFFER` | `28` | Soft mention | `moderate_buying 0.55 539` OR `relationship_score≥0.60 551` AND earlier suppression not triggered | `buying_intent 0.55`, `relationship_ready` | **YES** | `SOFT_OFFER LOW allow_cta true, price/product conditional` (`strategy:225`) → `casually mention paid exclusive content exists if fan interested. No hard sell` | **Yes** — gentle mention allowed |
| `OFFER_PPV` | `29` | Present offer | explicit `user_asked_to_buy/price/content 477` with `can_sell_content() true 483` OR `buying_intent≥0.80 517` with full gating | `explicit_request` or strong signal, `CreatorCapabilities.can_sell_content()` | **YES** | `OFFER_PPV MODERATE allow_cta&price&product, relationship_first true` → `present the offer from VERIFIED FACTS using exact title, price, URL` + `must` validators `deepseek_response:439/426` refuse hallucinated | **Yes** — only line that tells LLM to sell |
| `FOLLOW_UP` | `30` | Reminder on prior decline | `previous_offer_status in {declined,revoked,clicked,expired} 529` cooldown cleared | history `clicked etc` | **YES** | `FOLLOW_UP LOW allow_cta` → `follow up with care` (`deepseek_response:179`) not auto-sent (`selection:80 NON_EXECUTING`) | Prose only, no auto-creation |
| `DONT_OFFER` | `31` | Policy `dont_offer` | Defined via `action=DONT_OFFER` | Model `SUPPRESSED` | **NO** — no `return _decision(DONT_OFFER` in decision.py | Would map `SUPPRESSED NONE follow_up false` `strategy:261` — dead |
| `CHAT` | `32` | Pure chat | Enum only | Model | **NO** — unreachable via decision | `CHAT NONE` `strategy:273` dead |
| `TIP_SUGGESTION` | `33` | Suggest tip | `tip_eligibility=="eligible" 499` and `can_accept_tips true 500` | relationship `WARM+`, `tip eligible` | **YES** | `TIP_SUGGESTION LOW allow_cta but price/product false` → `cause is TIP_SUGGESTION` vs `suggest_tip` tool (separate path) |
| `OPERATOR_HANDOFF` | `34` | Human needed | `handoff_needed true 308` | relationship `OPERATOR_REQUIRED`, model uncertainty ≥0.80, negative ≥0.70, ambiguous intent | **YES** | `OPERATOR_HANDOFF NONE follow_up false` → `deepseek_response` still `RELATIONSHIP` fallback |

**Map to prompt terminology:** `NO_ACTION→NO_OFFER`, `BUILD_RELATIONSHIP→RELATIONSHIP_BUILDING`, `BUILD_DESIRE→SOFT_OFFER/FOLLOW_UP`, `QUALIFY→implicit via relationship_score 0.60 gate`, `RECOMMEND→not a distinct action — implicit in OFFER_PPV`, `PRESENT_OFFER→OFFER_PPV`, `REQUEST_TIP→TIP_SUGGESTION`, `FOLLOW_UP→FOLLOW_UP`, `AFTERCARE→RELATIONSHIP_BUILDING AFTERCARE_PHASE`, `HANDOFF→OPERATOR_HANDOFF`. No distinct `BUILD_DESIRE/RECOMMEND` names exist — legacy.

---

## 5. Sales Strategy

**File:** `commerce/strategy.py:1-44` — *pure translation of already-determined `CommerceDecision` into `CommerceStrategy`; never decides allow.*

### 5.1 `SalesPressure` `strategy.py:57-72`

```py
NONE = "none"     # suppression/no-sale
LOW  = "low"      # relationship/soft/follow-up
MODERATE = "moderate"  # already-authorized offer_ppv only
DIRECT = "direct" # fan explicitly requested — defined but NEVER emitted (build_strategy OFFER_PPV always MODERATE :253)
```
Comment `65-66`: **NO HIGH mode intentionally**.

### 5.2 `StrategyKind` `strategy.py:75-90` + `CommunicationConstraints` `96-121` (immutable, all false by default, never escalated)

`CommerceStrategy` fields `138-149`: `action, kind(=`action`), pressure, allow_cta, allow_price/product, relationship_first, follow_up_allowed, reason, communication_constraints`.

### 5.3 `build_strategy(decision, context)` `strategy.py:176-309` — deterministic map table verified in Sweep 1 (§B5), relationship_first always `True`, strategy never carries price/URL.

**Does the LLM receive the selected strategy?**

- **Commerce PPV path: YES** — `deepseek_response.py:354-380 _StrategyInstruction` consumes `strategy.action → _ACTION_INSTRUCTIONS 170-183`, `pressure→_PRESSURE_TONES 185-189`, `relationship_first, allow_cta, constraints, follow_up_allowed` and is **always injected** as `VERIFIED FACTS + StrategyInstruction` before transcript (`deepseek_response:397 _system_prompt`). Callsite `pipeline.py:623 _stitch_via_deepseek_response` (only when `OFFER_PPV + EXECUTED`) guarantees it.
- **Qwen conversational path: NO** — `build_qwen3_context` does **not** include `SalesPressure` or `StrategyKind`. Qwen sees `RELATIONSHIP: warm` and `COMMERCE: Purchases/Active offer` factored, but never sees `pressure=low/moderate` nor `allow_price`. Qwen is never told `this turn should soft-sell`.

**Break:** Strategy is **live for offer presentation, dead for every conversational turn**.

---

## 6. Commerce Signals

### 6.1 `CommerceSignals` model `commerce/signals.py:147-245`

All `BoundedFloat extra="forbid"` finite; `low_information()` fallback all `0.0/false/[]/uncertainty 1.0/uncertain`.

| Signal | Type | Used as |
|---|---|---|
| `purchase_intent` 0-1 | `BoundedFloat` `:161` | `buying_intent_score` (`signals_to_context:320`) → decision `≥0.55 soft, ≥0.80 strong` |
| `content_interest` 0-1 | `BoundedFloat` `162` | Not directly used — informational, `has_commercial_intent` only via `intent_tags` |
| `relationship_engagement` 0-1 | `BoundedFloat` `163` | `relationship_score` caller-wins else `relationship_engagement` (`signals:321-325`) → `0.60 relationship_ready` |
| `price_interest` 0-1 | `BoundedFloat` `164` | `requested_price !=None OR price_interest≥0.80 → user_asked_about_price` (`317-319`) |
| `explicit_purchase_request` bool | `StrictBool` `165` | `user_asked_to_buy` → `OFFER_PPV explicit` `477` |
| `explicit_content_request` bool | `166` | `user_requested_content` → explicit |
| `requested_price` float\|null | `BoundedPrice` `167` | same as price_interest |
| `declined_recent_offer` bool | `168` | `conversational_phase` `recent_decline → cooldown` |
| `negative_sentiment` 0-1 | `169` | `≥0.70 → OPERATOR handoff complaint` `relationship:423` + rejection |
| `confidence` 0-1 | `170` | `signal_confidence` → `<0.30 without explicit → RELATIONSHIP_BUILDING` `decision:412` |
| `evidence` list≤5 ≤240ch no payment data | `171,48,128-137,180-186` `payment guard` | Not used beyond validator |
| `model_uncertainty` | `172` | `≥0.80 → handoff LLM_UNCERTAINTY` `relationship:419` |
| `primary_intent` | `str` in `INTENT_CATEGORIES` (20) `175,57-80` default `uncertain` | `_derive_conversational_phase:393` → `opening/rapport/commercial_interest/content_curiosity/post_purchase/cooldown/operator_handoff/engaged_chat` |
| `intent_tags` max5 | `176` subset 20 | `has_commercial_intent = intent_tags ∩ _COMMERCIAL_INTENTS` `327` (8 intents) |
| `negative_intent_tags` | `177` subset `{hesitation,rejection,complaint}` `95-99` | `negative_intent_count=len(...)` `326` → `≥2 → RELATIONSHIP_BUILDING NEGATIVE_SIGNALS_SUPPRESSED` `403` |
| `fan_asks_question` | `178` default false | `fan_asks_question` flag → `_derive_conversational_phase` + `fan_asks_question:328` → `decision:411 signal_confidence` etc.; not surfaced to Qwen |

**Contract drift — critical forensic:** Prompt `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` `deepseek.py:56-101` asks for fields **`accepted_recent_offer, asks_for_free_content, conversation_relevance, topic_continuity`** (`deepseek:69,70,72,84`) that `CommerceSignals` forbids (extra → `ValidationError` → `invalid_payload` → `low_information` 100% of the time *if* model faithfully includes them). Validated `qwen2.5:3b` via `format="json"` currently **does not include** those fields (observed fallback was actually to `invalid_payload` at `10:55:59` from stale `gemini-flash-latest cheap_model` before guard — now fixed via `llm_provider_ollama:295-301`).

### 6.2 Model & consumers

- **Producer:** LLM `extract_commerce_signals` `commerce/deepseek.py:170-247` configurable `cheap_model` (`gemini-flash-latest` default, but `qwen2.5:3b` since Ollama guard) temperature `0.0` (`54`) `max_output_tokens 1024` `52`, prompt bounded `30 msgs 800ch each` `104-125`, short-circuits empty `180-182`.
- **LLM used for extraction:** At `llm_provider=ollama` → `OllamaProvider generate(format=json)` `deepseek.py:188 fault-tolerant` ; **Gemini remains secondary** `deepseek.py:198-230` Ollama→Gemini fallback when `gemini_fallback_enabled`, so `cheap_model` mapping bug already fixed (`llm_provider_ollama:295-301` discards `gemini` prefix).
- **Persistence:** **NONE** — transient `CommerceSignals` never written to Postgres/Redis;evidence: no `INSERT` in `signals.py/deepseek.py`.
- **Consumers:** (1) `decision.py:269 via signals_to_context 283` → `CommerceDecisionContext` (application wins on conflict `signals:283-297`), (2) Pipeline flag merger `commerce/pipeline.py:303` `_apply_signal_flags`, (3) rejection `feedback:178 classify_rejection`, (4) handoff re-check `pipeline:484` guarded `_is_low_info 492-497`.

**Does the signal influence the next message?** **Partially yes** — `purchase_intent → buying_intent_score → decision OFFER_PPV 522 or RELATIONSHIP_BUILDING 571` is sale-decisive, but **only through the commerce pipeline** (`resolve_and_run_commerce`). Once `USE_COMMERCE_RESPONSE` is produced (`workers/llm_worker:569`), Qwen **never generates that turn** — commerce text is sent. For all **non-commerce** turns (95% of chats), signals still gate `decision:517-576` but Qwen receives **zero** sales-pressure; Qwen's conversational reply is **signal-agnostic** except `relationship_state/warm` filtered.

---

## 7. Fan Profile / Context

| Info | AVAILABLE TO SALES ENGINE | AVAILABLE TO LLM | Notes | File:Line |
|---|---|---|---|---|
| `funnel_stage (new/warming/engaged/converted)` | **YES** — `users.funnel_stage` via `get_user 155` → `derive_relationship_state` | **YES** — `STATE: Fan|new` + `Stage: New fan...` (`context.py:198,255`) + `COMMERCE: Funnel stage:` | Set at signup default `new`, only updated on `advance_funnel_to_converted` after purchase (`post_purchase:46 idempotent`) — stays `new` at 47 msgs (8151382101) is normative until purchase | `db/schema:13 new`, `memory/profile:27` |
| `purchase_history (counts, last_purchase, amounts, titles)` | **YES** — `get_timing_context 411` + `get_behavioral_feedback_context 510` `commerce/dao` → `recent_*`, `hours_since` | **PARTIAL** — `COMMERCE: Funnel stage / Purchases: 2 ; Active offer...` filtered 5 facts (`context.py:283-288` keyword `purchase`) | `Purchases: 0` surfaced; product `title/price` only when single pending offer `context_assembler:752-756` |
| `previous offers (state pending/clicked/declined)` | **YES** — `list_offers_for_user 136`, `find_pending_offer 200` → `has_active_offer, previous_offer_status` | **NO** — Qwen state drops `CREATOR: Bella` (`audit docs 190`) and `aftercare`; history may contain `offer link` verbatim but no structured `offer_id` | `commerce/state:211-221` `has_active_offer` |
| `previous tips / tip fatigue` | **YES engine computes** `_TIP_COOLDOWN*` + `_FATIGUE` `relationship:259-368` | **NO Qwen** — tip eligibility rendered in `render_context 778 Tip eligible: ...` but **Qwen state filter drops tip unless commerce keyword present**; tip history hard-zero everywhere (`dao:565 0`) so fatigue **DEAD** | `commerce/dao:562-565 always 0` (`docs remediated deferred`) |
| `previous objections / purchase_signals` | **YES engine** `negative_intent_tags, declined_recent_offer, consecutive_rejections` | **NO Qwen** — conversational state not surfaced; `fan_asks_question` not in prompt | `signals:107-109` `pipeline:303` only |
| `preferences / interests (fan-entered)` | **NO commerce** — never read in `commerce/*.py` (`grep user_profiles 0 hits in commerce`) | **YES LLM** — `format_profile 122 → Fan: Name Interests ...` + `PROFILE: age,location,occupation,interests 259` | `memory/profile:11 PROFILE_SCHEMA interests[], mentioned_topics, preferences` extracted post-turn `111-127` |
| `price sensitivity` | **YES via signal** `price_interest 0.80 → user_asked_about_price` | **NO LLM** — LLM not told `price_sensitivity` score, only sees fan's `$` line | `signals:317-319` `decision:477` |
| `important_dates / emotional_state / communication_style` | **NO commerce** | **PARTIAL LLM** — `format_profile` renders `Recent mood` etc., but `build_qwen3_state_context` whitelists only `age/location/occupation/interests` (`context:259`), so `emotional_state_recent, communication_style` dropped on Qwen path | `context:259-266` |
| `interests` for warmth | See preferences above | **YES** as above | |
| `segment_names / creator_name` | **YES engine irrelevant** segments read `commerce/state:270-295` best-effort | **PARTIAL LLM** `Segments: ...` via `render_context 770` but lossy filter | `memory/context_assembler:715` |

**Collected but never consumed:** `emotional_state_recent, important_dates, topics_to_avoid, purchase_signals[]` from `profile.py` — extracted and stored JSON but dropped by Qwen2.5 state whitelist; `aftercare_status` from `behavioral_feedback_context` never reaches `render_context` (val stays `none`); `topic_continuity, accepted_recent_offer, asks_for_free_content` prompt fields discarded via `invalid_payload`.

---

## 8. Funnel Stage — Complete Trace

**Values (actual):** `new, warming, engaged, converted` (`memory/context.py:73-78` `STAGE_GUIDANCE` legacy vs `memory/context.py:81-86` `QWEN3_STAGE_GUIDANCE` `new: New fan... warming: Warming up... engaged: Engaged... converted: Converted...`) — only 4, plus `RelationshipState` `COLD/NEW/ENGAGED/WARM/BUYING_SIGNAL/PURCHASED/REPEAT_BUYER/VIP/COOLING_DOWN/DO_NOT_PUSH/OPERATOR_REQUIRED` (`relationship.py:17-34`) which is deterministically parallel via `derive_relationship_state 94-174`.

**Persistence:** `users.funnel_stage TEXT DEFAULT 'new'` (`db/schema:13`) — single column, lazy. Updates via **one caller only**: `commerce/post_purchase.py:46 advance_funnel_to_converted` (idempotent check `47-82` `SELECT funnel_stage` then `UPDATE SET funnel_stage='converted'` only if not already). **No other writer** — grepped `update_funnel_stage` 0 direct callers beyond wrappers `workers/llm_worker.py` none.

**Reads:** `memory/context_assembler.py:122 _get_user_safe → defaults funnel_stage "new"` `71`, `commerce/state.py:384-391 funnel_stage passed as None` (hard `None` not DB value — **anomaly** vs `context_assembler` which correctly uses DB), `memory/context.py:196 QWEN3_STAGE_GUIDANCE.get(funnel_stage,"")`. `derive_relationship_state(funnel_stage None)` treats `None` like `new/COLD` via default `new` fallthrough `relationship.py:168-171`.

**Does funnel actually change what Sunny says?** **YES but weakly lemma:** Qwen receives `Stage: New fan. Warm welcome.` vs `Warming up. Build rapport.` vs `Engaged. Deepen connection.` (`memory/context.py:197 stage_guidance`) — guides tone but **not sales pressure**. Commerce decision does **not** branch on `funnel_stage` directly (except indirectly via `relationship_state` ladder `relationship.py:111-174` where `funnel_stage=="vip"` → `VIP`, `engaged/converted` → `WARM`, `new` → `NEW`; then `commercial_pressure` `relationship:197-254` gates `NONE/SOFT/MODERATE`, and `decision:427 conversational_phase` suppresses early pitch). So `funnel_stage` matters to **suppression**, not to **proactive selling**.

**Live evidence:** User `8151382101` is `new` at `message_count 47` — no purchase has ever advanced it, so Sunny stays in `New fan` voice forever, never `Engaged`.

**Dead/unwired:** Multiple `get_funnel_stage` helpers exist but no cron to advance stage on *time* or *message volume*; only purchase advances. Legitimate fan with 47 messages should be `engaged` by any reasonable chat metric but stays `new` by determinism.

---

## 9. Product Selection — What Product Is Relevant to This Fan

**Source:** `fangate_products` mirror table `db/migrations/20260819000000_fangate_commerce.sql` → `SELECT * FROM fangate_products WHERE creator_id=$1 AND product_type='dropfans' AND is_accessible = true AND sales_url IS NOT NULL`. **Deterministic DB truth, LLM never supplies `product_id`.**

**Algorithm `commerce/product_selection.py` `resolve_commerce_product_with_history` + `commerce/state.py:205-246`:**

1. Creator scoped: `SELECT ... WHERE creator_id` `product_selection:120-123` `get_fangate_product`.
2. Validated `is_valid_product(): is_accessible && sales_url && title` `product_selection:52-62`.
3. Excludes already `has_purchased_product(creator, user, product.id)` `product_selection:86-93, product_selection:48, commerce/state:219` → `already_purchased` loop `product_selection:179-185` keeps only not purchased.
4. Counts: **0 → None (no offer path)** `product_selection:86`, **1 → that one** `product_selection:86, state:248 ProductIdentity(product_id, title, available)`, **≥2 → None** `product_selection:240` fail-closed ambiguity — **no AI recommendation among many**.
5. Creator isolation `SELECT ... WHERE creator_id=$1` `product_selection:120`, availability `is_accessible`, price `price_minor` may be null (then `strategy allow_price false`).

**LLM involvement in product decision:** **NONE.** `commerce/state.py:24-27 NO PRODUCT SELECTION` invariant documents intentional. LLM may *request* product list via `list_products` or `get_product_information(product_id)` tools `core/llm_tools:540 list_products 517 get_product_information` — both creator-scoped `auth.creator_id` `llm_tools:543,517` and return redacted product catalogue to LLM as **context**, but LLM cannot `execute_ppv` with arbitrary `product_id` — execution requires `activation_for(eligibility, identity, state).allow` `commerce/orchestrator:78`.

**Can AI recommend a never-selected product?** **NO — proven.** `deepseek_response:332` `_verified_url` only when `strategy.allow_product_reference && state.sales_url` (deterministic); `_urls_are_authoritative` whitelist **exactly** that `sales_url` `deepseek_response:426`; any hallucinated `creator123` URL → `FAILED invalid_output 538` → fallback `FALLBACK_TO_STANDARD_LLM` `selection:80` never sent. Tool `propose_product_offer(product_id)` **proposes** `core/llm_tools:704` `CommerceProposal(action ppv, ppv_id)` but engine re-validates `product_id` via `get_fangate_product(creator_id, product_id)` `llm_tools:725` and creator isolation again; if invalid → `PRODUCT_UNAVAILABLE` `720`.

---

## 10. Offer Creation — Complete path

| Layer | File:Line | Idempotency / isolation / immutability |
|---|---|---|
| **Commercial decision** `OFFER_PPV` | `decision:477 or 522` with full gating `has_active_offer, purchase_cooldown, offer_cooldown, max 2/3, fatigue, low confidence, conversational phase, commercial pause, aftercare, rejection 3, creator capability, tip` | Pure, no side effects, `allowed` must be true to proceed |
| **Offer eligibility** | `commerce/state:192 get_dropfans_integration active` `214 has_active_offer` (from `find_pending_offer_for_product`) `216 has_purchased` | Creator-scoped `WHERE creator_id` |
| **Product** | `state:248 ProductIdentity` + `state:261 ProductCommerceState` `price_minor, sales_url, is_accessible, age_verification_required` | Deterministic mirror, LLM cannot supply |
| **Price** | `fangate_products.price_minor` (`commerce/state:210 via get_fangate_product`) | `int minor` strict `context:114-118` `PriceMinor`; `execution:251 local.get("price_minor")` |
| **Offer creation** | `commerce/execution.py:265-295 create_offer_serialized(creator,user,product,link,price,currency="USD", reason, created_by, expires_at)` → `commerce/dao:82 pg_advisory_xact_lock hashtextextended("ppv_offer:{creator}:{user}:{product}")` inside tx `commerce/dao:88-94 SELECT pending/clicked LIMIT1` → if exists `ALREADY_EXECUTED` `execution:207` else `INSERT commerce_offers state=pending price_minor=currency original price txn null` `dao:102-121` | **Advisory lock serialized per (creator,user,product)** `commerce/dao:82`; second concurrent → `ALREADY_EXECUTED`; **price immutability:** `commerce_offers.price_minor` captured at insert, fan `requested_price` never overwrites `llm_tools:517-548` product lookup redacted |
| **DropFans checkout URL** | `execution:239-248 sales_url or build_checkout_url(creator, dropfans_product_id)` via `integrations/dropfans/service:465 build_checkout_url` preferring `telegram.buy_template.replace("{productId}", drop_id)` then `web.buy_template` else `https://www.dropfans.io/buy/{id}` `service:472` | `dropfans_product_id` from `fangate_products.raw['dropfans_product_id']` `execution:188-201` refined; URL validated `urlsplit scheme http/https` `context:172-180` |
| **LLM response** (only via commerce-response) | `deepseek_response:465 generate_commerce_response → _system_prompt(VERIFIED FACTS + Strategy) → deepseek_response:479 provider.generate 0.0 temp` | `deepseek_response:469 variant? → ppl _ACTION_INSTRUCTIONS OFFER_PPV → present from VERIFIED FACTS using exact title/price/URL 181-182` |
| **Validation** | `deepseek_response:529-543` validators (`_urls_are_authoritative` only `sales_url`, `_prices_are_authoritative` `abs(amount-expected) ≤0.005` USD) | `OFFER_CLAIM_PHRASES` `147` only when `execution ACTIVE_STATUSES` `195` else `invalid_output` `deepseek_response:532` |
| **Send** | `commerce/selection:286 if execution active → USE_COMMERCE_RESPONSE else FALLBACK`; `workers/llm_worker:569 if selection USE_COMMERCE_RESPONSE → draft=commerce_response_text` bypasses LLM draft, then **still scored** (`743`) but commerce validator precedes; finally `workers/llm_worker:825 routing` or `commerce/pipeline:459-677` | Deduplication via `DEDUP_KEY = md5(llm_followup:creator:user:delay)`? No, PPV dedup via advisory lock + `pending/clicked` uniqueness |

**Idempotency/creator/price/URL guarantees verified:** advisory lock `ppv_offer:{creator}:{user}:{product}` (`dao:82`), `transaction_id` partial unique `ppv_analytics_daily`, `OFFER_ACTIVE_STATUSES = {EXECUTED,ALREADY_EXECUTED}` `selection:75` / `deepseek_response:195` must match, creator isolation `WHERE creator_id=$1 AND user_id=$2` every DAO.

**LLM merely describes an existing offer** `selection:286 USE_COMMERCE_RESPONSE` **or not at all** — it **cannot** cause creation. `proposal` path `core/llm_tools:704 propose_product_offer` **proposes** via `create_offer_serialized` (`llm_tools:733`) but deterministically gates `if decision.action is OFFER_PPV and allowed and activation → execute_ppv` `orchestrator:188-206` + `model_copy update` `state:364-451` injects `relationship_state, commercial_pressure...`; execution re-validates 11 gates `execution:101-295` including `creator_sales_enabled, is_blocked, product existence` — any fail → `DENIED`. `tool loop` not reached when `llm_provider=ollama` now (bypasses `genai` tool code `workers/llm_worker:218`) so LLM-proposed PPV is **currently unreachable** from autonomous Qwen path; only deterministic `resolve_and_run_commerce` path remains. `core/llm_tools:704-989` handlers remain live for future canary.

---

## 11. Sales Copy Generation — What Tells the LLM to Sell

### 11.1 Three disjoint generation stacks

| Stack | Entry | System prompt source | When used |
|---|---|---|---|
| **Qwen conversational** | `workers/llm_worker:81 generate_draft` | `memory/context:build_qwen3_system_prompt + build_qwen3_state_context` + optional `TOOL_AUTHORITY_PROMPT` | Main fan reply; **never told to sell**. May propose via tool. |
| **Commerce PPV** | `commerce/deepseek_response:465 generate_commerce_response` via `commerce/pipeline:618` | `COMMERCE_RESPONSE_SYSTEM + VERIFIED FACTS + StrategyInstruction` | **Only when `OFFER_PPV + EXECUTED`** (bypasses Qwen). |
| **Agent loop** | `agent/loop.py:70 AgentLoop.run` | `agent/loop:149-177 Be conversational, not salesy.` + relationship warm | Not active (`canary` disabled) — dummy `NEW/NONE` |

### 11.2 Qwen conversational prompt — no sell imperative

- `memory/context.py:220-230` rules: `2-4 sentences, match energy, Reference history, Never reveal AI, Vary structure... No filler words, No generic affirmations, Do not promise photos, A reply may have no question... Prefer callbacks` — **zero `sell/offer/price/buy/urgency`**.
- State: `STATE: Fan|new, PROFILE, RELATIONSHIP warm, COMMERCE: 5 facts, SUMMARY, IDENTITY, CONVERSATION topic/last_q/tone, ABOUT SUNNY, CAPABILITIES, RESPONSE: mode=..., QUESTION: allowed=...` — factual, not prescriptive.
- History: `get_recent_messages 20 / 800tok / 3 assistant` deduped at `generate_draft:99`.

**What specifically tells LLM to sell?** **NOTHING** in conversational stack. Qwen conversational never wants to sell; the deterministic engine wants to sell via `select_commerce_response` which **bypasses** Qwen entirely. This is intentional authority: conversational Sunny never `sell`.

### 11.3 Commerce PPV prompt — the ONE sell instruction

- `VERIFIED FACTS` `deepseek_response:296-348` always. `Product title`/`Verified price: 9.99 USD`/`Sales URL` only when `strategy.allow_*` true (`strategy:201-296`).
- `StrategyInstruction` `deepseek_response:351-394` always: `_ACTION_INSTRUCTIONS` — **only `OFFER_PPV` contains** `present the offer from VERIFIED FACTS using exact title, price, URL given.` `deepseek_response:181-182`. All others `NO_OFFER/RELATIONSHIP_BUILDING → Do NOT mention product/price/offer`, `SOFT_OFFER → casually mention paid exclusive content ... No hard sell`, `FOLLOW_UP → gentle mention`, `TIP → via tool`. `Pressure tones` `185-189` + constraints `98-168` forbid urgency/guilt/discount.

---

## 12. Sales Content Authority

| Fact | Deterministic source | Lines | LLM can fabricate? |
|---|---|---|---|
| `product_id/title/available` | `get_fangate_product(creator_id,product_id)` only when caller supplies | `commerce/state:205` `context:140` | **No** |
| `price_minor/currency` | `fangate_products.price_minor`/hard `"USD"` | `commerce/context:155, context_assembler:340, execution:265` | **No** — validators reject non-USD |
| `sales_url/checkout URL` | `fangate_products.sales_url` or `build_checkout_url(drop_id)` | `execution:239 service:465` | **No** — whitelist reject |
| `offer_id/state` | `create_offer_serialized` advisory lock, pending/clicked offered | `dao:82,88` | **No** |
| `tip_url` | `get_checkout_links(creator_id) telegram.tip` | `llm_tools:906` | **No** |
| `eligibility/creator/ cooldown/purchase` | `evaluate_ppv_eligibility` + `get_timing_context` | `state:223, dao:411` | **No** — conversation cannot override (`context:8-12`) |
| `strategy allow_*` | `build_strategy` deterministic `price_ok && sales_url` | `strategy:152,165` | **No** |

**Single consumer-passes-price test:** `commerce/deepseek_response:439 _prices_are_authoritative Decimal tolerance 0.005` → invented `$` in LLM output fails `invalid_output`, falling back to `FALLBACK_TO_STANDARD_LLM` (`selection:80`) never sent. Verified at `commerce/deepseek_response:538-540`.

---

## 13. Desire Building — Real Pre-Sale Phase

**Asked sequence:** `fan attraction → AI deepens engagement → learns preference → creates anticipation → detects buying signal → presents offer`

| Stage | Status | Evidence |
|---|---|---|
| `curiosity` | **LIVE** | `intent_tags content_curiosity → conversational_phase content_curiosity 410` |
| `rapport` | **LIVE** | `relationship_state WARM/ENGAGED, QWEN3_STAGE_GUIDANCE engaged` |
| `preference elicitation` | **PARTIAL** — `profile.py` learns `interests[]` via LLM `explicit/inferred` but **never feeds commerce decision** | `memory/profile:27 extraction, commerce/*.py grep user_profiles 0` |
| `anticipation / tease queue` | **NOT IMPLEMENTED** | No `TeaseQueue, DesireScore` desire table; `TEASE` mode single-turn, not multi-turn ladder |
| `buying signal → offer` | **LIVE** | `purchase_intent 0.55 moderate, 0.80 strong, explicit → OFFER_PPV 477-526` |

Overall: rapport + curiosity live, desire anticipation **dead** — system jumps to offer on explicit/strong signal with no staged tease. Classified `IMPLEMENTED BUT FUNCTIONALLY UNWIRED` for desire.

---

## 14. Qualification — Learned Useful-for-Selling Info

**What:** `PROFILE_SCHEMA` `name,age,location,occupation,interests[],mentioned_topics[],preferences[],purchase_signals[]` (`profile.py:11`). **When:** post-turn `post_process 889 create_task(extract_and_update_profile [-10:] 140)` via `cheap_model` `profile.py:78`. **Why:** `Reference what they've told you naturally` `context.py:230`. **Where:** `user_profiles.facts::jsonb` merged via `merge_profiles` `95` (list cap 15). **Whether influences commerce:** **Only conversationally** — `format_profile 122` → LLM system; **not** deterministic commerce (`grep user_profiles in commerce 0`). A question is only commercially useful if its answer were scored as buying signal — `profile:interests hobby` never does. So `PARTIAL` — learns but not wired to sales.

---

## 15. Objection Handling — Limited, No Persuasion

Detection **LIVE:** `negative_intent_tags hesitation|rejection|complaint 95` `declined_recent_offer 168` plus pure classifier `classify_rejection 178 (HARD|SOFT|PRICE_OBJECTION|UNCERTAIN)` e.g. `hesitation+price_interest≥0.60 → PRICE_OBJECTION 191`.

Handling — suppression, not recovery:

| Objection | Type | Handling | File:Line | Persuasion recovery |
|---|---|---|---|---|
| `not interested / no thanks` | `HARD` | Pipeline persists `mark_offer_declined reason=rejection_hard` only for HARD/PRICE `541`; Decision `consecutive≥3→RELATIONSHIP_BUILDING REJECTION_ESCALATION 467` + `commercial_paused 437` | `feedback:69, pipeline:531, decision:467` | **No re-pitch — accepts** `deepseek_response:205 accept gracefully` |
| `maybe later` | `SOFT` | `SOFT 203` → NO declined persist `541` (HARD/PRICE only), `negative_count 1 <2` fails `403` | `feedback:70` | No cooldown, conversation continues |
| `too expensive / can't afford` | `PRICE_OBJECTION` | **Does** `mark_offer_declined price_objection 541` + forbidden discount vocabulary `_FORBIDDEN 122 discount half price` + `allow_invented_discounts false 113` + test `too expensive must not auto-discount 390` | `feedback:71,191, deepseek_response:122` | **Never discounts** by design |
| `send free` (`asks_for_free_content`) | **DEAD** | Prompt field removed from `CommerceSignals` (Phase C.1-E) `signals:147 not has` → `DEAD signal` `deepseek:70 dead` | `deepseek:70` `signals:221` | No freebie grant |

Thus objection handling exists as **classification + suppression**, not persuasion — intentional (no discount).

---

## 16. Tip Selling — Deterministic & Strategic vs Accidental

**Path (see §3 step Tip):** `user_message → build_qwen3_context → generate_draft_with_tools → LLM ToolCall(suggest_tip {reason}) → dispatch_tool(298 timeout+publish) → _handle_suggest_tip(804)` checkpoints: auth blocked `815`, creator sales `831`, eligibility via deterministic `derive/commercial/ tip 872-885` → if ineligible `BUSINESS_RULE_REJECTED` `890`, `get_checkout_links(creator_id) 906` canonical `telegram.tip` else `web.tip` `911-915` validate `http 925`, `dedup 937 md5(url)[:12] + is_send_duplicate 943` → `BUSINESS_RULE_REJECTED if duplicate`, `tip_content hard-coded 950` → `enqueue_send 952` `send_messages` → `chatbotv2/main:98 is_send_duplicate( dedup_id) + rate-limit 1/s burst5 + Telethon 289`.

**Is tip decision strategic?** **Deterministic but accidentally permissive** (§A.5 Audit): pure `check_tip_eligibility` has 12 thresholds (`relationship 72h/48h/24h * fatigue, contextual 12h, etc.` `relationship:259-369`) but every caller supplies dummy zeros (`dao:565 0`, `state:361 0`, `context_assembler:656 2-arg`, `llm_tools:885 2-arg`, `agent:610-613 0`). So `eligible` collapses to `relationship in WARM..VIP or ENGAGED+low pressure` independent of history — any `ENGAGED`+warm becomes tip-eligible every turn, limited only by `1h dedup` — **not by 24-72h design**.

---

## 17. Tip Fatigue

| Definition | Value | Actually consumed? |
|---|---|---|
| Base `72h / 48h ENGAGED / 24h VIP` + `×(1+ignored*0.5)` | `relationship:259-265` | **NO** — `ignored` always `0`, so never scales |
| `≥2 ignored && ≥2 sent → tip_fatigue ineligible` | `336` | **NO** — both `0` always → guard dead |
| `hours_since_last_tip < cooldown → tip_cooldown` | `345-353` | **NO** — `None` or `0.0` hard-coded → never fires |
| `1h URL-dedup` | `llm_tools:937 3600s` | **Partial** — suppresses identical URL for 1h; new URL or 61st minute passes |
| `recent_purchase>0 → ineligible recent_purchase` | `330` | **PARTIALLY** — requires behavioral read which defaults `0` |

**PPV timing budgets** `offer 24h purchase 6h max 2/3 per 24h` `decision:158` **do** via `dao:411 get_timing_context 24h counts` — but tip path does not use them. **Conclusion: 1h dedup only live; 12-108h fatigue dead.**

---

## 18. Purchase Path — Transactional

| Stage | File:Line | Idempotency / dedup |
|---|---|---|
| Offer presented (insert `commerce_offers pending`) | `execution:265 create_offer_serialized advisory lock 82 + pending/clicked check 88` | Advisory lock `ppv_offer:{creator}:{user}:{product}` |
| Fan purchases (Pike: DropFans sales poll + unattributed purchase `reconcile_all`) | `commerce/reconciliation:251 reconcile_dropfans_sales → dropfans/service reconcile_sales` + `attribute_purchase_from_webhook dao:249` | `fangate_transactions transaction_id partial unique + ppv_analytics_daily` |
| `transaction row` `fangate_transactions` / `ppv_analytics_daily` | `dao:88 INSERT fangate_transactions` idempotent `unique delivery_id` | Unique `delivery_id` |
| `offer attribution` `SELECT pending/clicked, atomically 0 or >1 → fail-closed, UPDATE purchased` | `dao:124 get_offer → UPDATE state purchased 170` | Conditional `WHERE state IN pending/clicked + transaction_id IS NULL OR =` |
| `Telegram user association` `UPDATE fangate_transactions SET user_id` `dao:183` | `dao:184-193` | Non-overwriting `user_id IS NULL` |
| `purchase state` `has_purchased_product 392` + `recent_purchase_count` | `state:219, decision:342` | PURCHASE path |
| `funnel transition` `advance_funnel_to_converted` | `post_purchase:46 SELECT funnel_stage then UPDATE SET converted if not already` | Idempotent check `47` |
| `aftercare` `mark_aftercare_pending/completed` | `dao:892-943` `UPDATE aftercare_status none→pending→completed` | Same row, state machine |

**Post-turn live check:** `SELECT funnel_stage FROM users WHERE id=8151382101` is `new` at 47 msgs — purchase path not yet triggered for this fan.

---

## 19. Post-Purchase Selling

| Capability | Verdict | Evidence |
|---|---|---|
| Aftercare | **PARTIAL / UNREACHABLE** | DB `dao:892 aftercare_status pending/sent` + decision `450 AFTERCARE_PHASE` live suppression, but LLM never sees `aftercare` (`context_assembler:585 val="none"` never updated `589-606 missing aftercare`, `context.py:282 commerce filter omits aftercare keyword, SUNNY_C1-F audit:560`) |
| Cross-sell | **NOT IMPLEMENTED** | Multi-product `None` fail-closed `product_selection:240` |
| Upsell | **NOT IMPLEMENTED** | No price-tier comparison |
| Repeat purchase | **PARTIAL** | Helper `is_repeat_purchase_eligible total_purchases≥1, hours>168, engagement, satisfaction != negative, no recent rejection <48h` `feedback:214-262` derived `state:421 context_assembler:596` flag, but **no branch in `decision.py` checks it** except render line `790 repeat purchase eligible` |
| Relationship continuation | **DEAD** beyond single check-in | Only `schedule_follow_up 24h 282` + `mark_aftercare_completed never invoked` (grep 0 caller) |

Overall: delivery **LIVE**, emotional aftercare/continuation **DEAD/UNWIRED**.

---

## 20. Follow-Up — Scheduling

**Schedules:** `YES — LIVE 3 schedulers`

- Post-purchase `schedule_follow_up 24h dedup post_purchase_followup:{txn}` `post_purchase:311` → `scheduler_worker:37 _build_send_payload 53 _make_dedup_id scheduled:{dedup_key}:{id} 77 claim_due_messages ensure_consumer_group every 31`.
- LLM-proposed `propose_follow_up delay 1-168h dedup md5(llm_followup:creator:user:delay) 701` fixed `_LLM_FOLLOW_UP_CONTENT`.
- Dashboard bulk via `create_scheduled_message idempotent dedup 2249`.

**Remembers unfinished / Detect abandoned / Re-engage?** Remember persisted `commerce_offers pending/clicked` (`idx state: commerce_offers state 35`) but **no reminder job** queries `pending >24h unclicked`; no `abandoned` state; no recovery cron; `FOLLOW_UP` action requires `previous_offer_status clicked etc` cooldown cleared `528-536` but generates **text** `generate_commerce_response` not automated nudge (selection `80 NON_EXECUTING`).

Thus **persisted but not actionable re-engagement** — **PARTIAL/DEAD**.

---

## 21. Sales Frequency / Pressure — Full Inventory (see Sweep 1 §E live table, identical)

| Layer | Knob | Enforcement | File:Line |
|---|---|---|---|
| `24h offer 2 / sales 3` | decision `max 2/3` `get_timing_context 24h counts` | Hard `NO_OFFER 370,377` | `decision:158 decision:370` |
| `offer cooldown 24h outcome-aware` | `E:\chatbot\commerce\dao:426 hours` | `RECENT_DECLINE/IGNORE/COOLDOWN_ACTIVE 359-367` | `decision:353` |
| `purchase 6h` | `342` | `RECENT_PURCHASE` | — |
| `offer fatigue intent<0.80` | `389` | `NO_OFFER OFFER_FATIGUE` | `decision:389` |
| `negative ≥2 → RELATIONSHIP_BUILDING` | `402` | Suppress | — |
| `low confidence <0.30` | `412` | → `RELATIONSHIP_BUILDING` | — |
| `opening/rapport` | `427` | → `RELATIONSHIP_BUILDING` | — |
| `commercial_paused (≥3 rejections)` | `437,594,411` | `consecutive 3` | `state:594 dao:528` |
| `aftercare pending/sent` | `450` | `RELATIONSHIP_BUILDING AFTERCARE_PHASE` | — |
| `rejection 3` | `467` | Same | — |
| `relationship <0.60` | `162` | `INSUFFICIENT_RELATIONSHIP` `565` | `decision:162` |
| `pressure NONE/SOFT/MODERATE` | `relationship:197 DIRECT only if explicit` | Gate | — |
| `allow_cta/price/product` flags | `strategy:201-310` | Price only if `strategy.price_ok` | — |
| `never urgency/guilt/discount` | `constraints all false` `response:98-168` | Validation `invalid_output` | `response:419-422` |
| `debounce 3s` | `db/redis:277 window 3` | First msg wins | `handlers` |
| `rate limit 1/s burst 5` | `db/redis:370 ZSET lua` | Per-peer throttle | — |
| `price/tip hard flag → operator queue` | `core/scoring:22-35 62-89 flag min 0.1` `workers:825` | Auto-approve false if flagged | — |
| `duplicate tail dedup` | `workers:98-101 last user == current skip` | Prevents duplicate double-send | — |
| `tip dedup 1h URL` | `core/llm_tools:937 3600` | Best 1h | — |
| `vault dedup UNIQUE (creator,user,media)` | `db/vault` | Strong | `post_purchase:429 has_user_received_media` |

**Repeated CTA** blocked textually via `constraints + validators`, count via cooldown budgets — **LIVE**, doubly gated.

---

## 22. Scoring — Sales Response Through Scoring

| Step | Code | Detail |
|---|---|---|
| Flags | `core/scoring.py:88 keywords price/tip/ppv/buy → price_mention hard` | includes `tip,ppv,buy` `22-35` + LLM prompt expects 0-10 + flags `62-75` |
| LLM scoring | `core/scoring:93 generate_with_fallback temp 0.2 response_mime json else fail-closed 0` | `118-121` |
| Composite | `avg(contextually_aware,natural,appropriate,not_repetitive)/10 131` `composite=0.0 if failed 129` + `hard flag min 0.1 138` | — |
| Routing | `workers:743 score draft` `780 is_auto_reply_enabled` `825 if score>=0.80 and not flags → enqueue_send auto_approved True` else operator | `812,853 add_to_operator_queue` + `suggestion.created` |

Selling text containing `price/tip/ppv/buy` triggers `price_mention → 0.1` → **never auto-sent** → operator queue. Commerce path bypasses? Actually `workers:566 if USE_COMMERCE_RESPONSE → draft=commerce_response_text` **skips** scoring? No `743` still scores it — but commerce validator **precedes** scoring rejection; scoring would still hard-flag `price` sales and send to queue, but `selection:80 FALLBACK_TO_STANDARD_LLM` would have already fallen back if execution not active. So **selling auto-queued not auto-sent — safety net but blocks conversions** if threshold too low — by design.

---

## 23. Operator Handoff — When AI Stops Trying to Sell

**Detector:** `check_operator_handoff(... relationship_state, commercial_pressure, intent_category, negative_sentiment, model_uncertainty, fulfillment_failures, has_complaint, custom_request, provider_uncertain, creator_config)` `commerce/relationship:377-434`

Triggers `OPERATOR_REQUIRED→safety 397`, `custom_request→CUSTOM_REQUEST 407`, `provider_uncertain 411`, `fulfillment≥2 415`, `model_uncertainty≥0.80 419`, `negative≥0.70 423`, `ambiguous intent 427` etc. Wired at two levels:

- Decision `handoff_needed` highest priority `decision:308` → `OPERATOR_HANDOFF OPERATOR_HANDOFF_NEEDED` (immediately stops selling)
- LLM context wiring `memory/context_assembler:665 Handoff: {reason}` and `decision` pipeline skips if `_is_low_info` skip `pipeline:492-497`

State preservation: `operator_queue` `status pending → add_to_operator_queue 812` + `conversation_attention` deferred. Duplicate-send prevention via `scoring hard flag → fallback` and `is_send_duplicate` `db/redis:78`.

Failed sale does **not** loop endlessly: fail-closed branches above (consecutive 3→commercial_paused, aftercare, cooldown 24h) all route to `RELATIONSHIP_BUILDING`, not re-queue `OFFER_PPV`.

---

## 24. Duplicated Sales Logic

| Domain | Sources | Authoritative | Risk |
|---|---|---|---|
| Commerce decision | `decision.py DEFAULT_COMMERCE_POLICY + pipeline:CommercePipelineRequest + orchestrator:orchestrate_commerce + selection:select_commerce_response + integration:resolve_and_run_commerce + state:resolve_commerce_state` | **Decision engine `commerce/decision.py`** decides; `strategy` translates; `orchestrator:188` gates `execute_ppv`; `selection:80 fallback` interprets. Double-compute in `pipeline:557 re-derives decide` is waste not divergence | Safe |
| Strategy | `strategy.py build_strategy` only consumer | Single | — |
| Offer eligibility | `state:223 evaluate_ppv_eligibility`, `relationship:259 check_tip_eligibility`, `decision:296,342` duplicate checks | `decision:PURE` but `state` and `relationship` recompute; no divergence because they use distinct inputs (offer vs tip vs relationship) | Medium but consistent |
| Tip | Dropfans `get_checkout_links` + `relationship:293` fatigue + `llm_tools:804` rebuild + agent echo | **Dropfans API** sole — others never writer | Hardcoded zeros make fatigue dead → over-suggest (duplication masks bug) |
| Provider | `core/config llm_provider` + factory + `provider_fallback` + `gemini_client` | Intended: `config`+factory; actual: `generate_draft_with_tools` bypass makes Gemini shadow authoritative for tools | **BROKEN**: tools always Gemini |
| Capability | `CreatorCapabilities` + `derive_creator_capabilities` + `CommerceContext` hardcodes `has_relevant_product False` via `workers:618` | DB + `relationship` but agent stale `false` | Agent hardcode |

**Refactoring recommendation:** Only if tip fatigue duplication fix requires new `tip_events` source; otherwise duplication safe (waste not divergence). No behavioral risk worth churn now.

---

## 25. Dummy / Placeholder Data

| Data | Declared | Actual | File:line | Class |
|---|---|---|---|---|
| `tip_suggestions_*` / `hours_since_last_tip` | int/float | `0 / None / 0.0` hard-zero | `commerce/dao:565`, `state:361`, `workers:613`, `context_assembler:692` | **PRODUCTION DUMMY** (P2-13 fatigue dead) |
| `aftercare_status` in LLMContext | `"none"` | `memory/context_assembler:585 "none"` not from DB | `585 missing behavioral.get("aftercare_status")` | **DUMMY** — DB has real but unwired |
| `relationship_state` for agent | `WARM` | `workers:606 "NEW"` hard | `workers:606` | **DUMMY** (P2-13) |
| `_behavioral_store` | list | `[]` placeholder never read | `commerce/feedback:21` | **DEAD** |
| `product_currency USD` | `str` | hard `USD` fallback | `commerce/execution:265` | **SAFE DEFAULT** DropFans USD |
| `response_mime json` on Ollama | `string` | `OllamaProvider:152 format json` now fixed | `llm_provider_ollama:152` | Fixed |
| `scheduled_messages isolation` | — | previous shim but OK | `migrations` | SAFE |
| Duplicate index `idx_message_embeddings_user` twice | `CREATE INDEX` | duplicate | `db/schema:72,89` | Safe but placeholder |

Confirmed in docs `remediation:112 deferred new DB table`.

---

## 26. LLM Prompt Forensics — Effective Generation Structure

### 26.1 Qwen3 conversational (authoritative path, 95% of turns)

| # | Block | Content exemplary | Always? | Conditional? |
|---|---|---|---|---|
| 1 | **IDENTITY / Persona** | `{persona}` e.g. `You are Sunny Skye …` trimmed if `identity_already_established` → `You are sunny` | Always system[0] | Transform when established `context:205-212` |
| 2 | **FAN STATE / Profile** | `Fan: {first_name} \n {facts}` | Always | `facts` only if non-empty else `No profile data` |
| 3 | **Stage Guidance** | `Stage: New fan. Warm welcome.` (`QWEN3_STAGE_GUIDANCE` `new/warming/engaged/converted`) | Always | `73-86` defaults "" |
| 4 | **Rules** | `- 2-4 sentences, match their energy ... - Do not promise to send photos/videos ... Prefer callbacks ...` | Always | — |
| 5 | **STATE header** | `STATE: {first_name} \| {funnel_stage}` | Always | — |
| 6 | **PROFILE compact** | `PROFILE: age, location ...` | Conditional | only if `age/location/occupation/interests` non-empty `258-266` |
| 7 | **RELATIONSHIP** | `RELATIONSHIP: warm / engaged / vip` | Conditional | if `relationship_state` present `268-271` |
| 8 | **COMMERCE STATE** | `COMMERCE: Purchases: 2 ; Active offer: …` filtered `purchase,tip,revenue,offer,cooldown` max 5 | Conditional | only if `commerce_text` `273-288` has keyword |
| 9 | **SUMMARY** | `SUMMARY: {2 sentences}` | Conditional | if summary exists `291-297` |
|10| **IDENTITY lifecycle** | `IDENTITY: established=true lifecycle=established + RULE: Do NOT re-introduce...` | Conditional | if `conversation_state` `300-306` |
|11| **CONVERSATION threads** | `CONVERSATION: topic=work open=[netflix] last_q wasAnswered false tone=flirty` | Conditional | if `current_topic or open_threads` `314-325` |
|12| **PERSONA SELF-FACTS** | `ABOUT SUNNY: enjoys cozy movie nights, late-night chats...` | Conditional | `core/persona_self:43` 3 facts `;`-joined |
|13| **CAPABILITIES** | `CAPABILITIES: send_text:yes send_photo:no ... NOTE: Do NOT promise to send...` | **Always** | static `derive_capability_contract()` |
|14| **RESPONSE MODE** | `RESPONSE: mode=react / share / explore / tease / callback / clarify / close` | Conditional | if `response_mode` not None `345-346` |
|15| **QUESTION BUDGET** | `QUESTION: allowed=true/false` | Conditional | if `question_allowed` not None `347-348` |
|16| **TOOL AUTHORITY** | `TOOL AUTHORITY ... Never invent prices ...` | Conditional | **Only when Gemini tool mode** (`generate_draft_with_tools` supports_tool_calling) `workers:229` else **not** in Qwen |

**Contradictions / staleness:** persona sales default `db/schema:111 gently mention available content` vs Qwen `Do not promise photos` vs `TOOL AUTHORITY` only in tool mode — conditional blocks cause divergence; `CREATOR: Bella` dropped by commerce keyword filter (no purchase keyword); `aftercare` omitted; `relationship_state` appears twice (block 7 + block 8 pressure) may stale; `PRODUCT block missing` for most turns (only when single offer pending).

---

## 27. Agent vs Legacy

| Dimension | Legacy (`workers:673-697`) | Agent (`workers:585-651`) |
|---|---|---|
| Pre-choice | Same `_try_commerce_draft 566` shared | Identical |
| System prompt | `build_qwen3_system_prompt` + `build_qwen3_state_context` real `STATE|PROFILE|COMMERCE|SUMMARY|IDENTITY|CONVERSATION|ABOUT SUNNY|CAPABILITIES|RESPONSE|QUESTION` | `AgentLoop._build_system_prompt` static `You are helpful, natural... Be conversational, not salesy.` + `Relationship: NEW` dummy + `memory.inject` 3 facts only |
| Commerce state | Real `build_llm_context → render_context 718-793` | **Hard-coded dummies** `relationship_state="NEW" commercial_pressure="NONE" tip_eligibility="NOT_ELIGIBLE" eligible_products=() has_relevant_product=False` `workers:606-618` |
| Tools | Gemini 7 `core/llm_tools` via `generate_draft_with_tools` `178` with `TOOL_AUTHORITY_PROMPT` `229` | 8 read-only `agent/tools:224` via `AgentLoop 218` `authority _require_identity` only, no offer execution |
| Max tokens/timeout | `200` `0.85` | `120` `5 tool calls` `30s` |
| Sales outcome | Both share commerce pipeline gate, but fallback LLM behavior diverges | Agent never sells — zeroed commerce snapshot + fewer tokens |

**Does enabling agent change commercial authority?** **NO** — decision/strategy/execution are pre-choice and sealed; agent cannot call `execute_ppv`, cannot write `creator_id` cross-scope, sees dummy commerce so `can_sell_content false` → `RELATIONSHIP_BUILDING` via capability gate.

---

## 28. Provider Effect

| Layer | Gemini | Qwen/Ollama | Identical deter state? |
|---|---|---|---|
| `_try_commerce_draft` | No provider branch — pure DB reads | Same before provider consulted | **YES identical** |
| Decision/strategy | Pure | Pure | **YES** |
| `generate_draft` | Gemini-authoritative list `["gemini","ollama"]` `workers:127` tries Gemini first `132` on failure logs `Gemini failed` `139` | Ollama-authoritative single provider `workers:109` no fallback chain | **Identical inputs** (system+history, `max_tokens 200, temp 0.85`) but temperature overridden by Ollama to `0.7` `provider_ollama:181` vs Gemini `0.85`; payload `messages` vs `gtypes.Content` mapping differs |
| Tool loop | `supports_tool_calling True` → `generate_draft_with_tools` loop `255` | `False` → early return `Provider does not support tool calling → plain` `204` — no authority prompt | **LLM may see different `TOOL_AUTHORITY` block** — Gemini path richer |
| Signals `response_mime json` | Gemini `GenerateContentConfig(response_mime_type=application/json)` | Ollama `format=json` `provider_ollama:162` | **Identical logical** |
| Embeddings | `GeminiProvider embed gemini-embedding-001` | Delegates to `GeminiProvider` even under ollama `memory/retrieval:22` fallback | **Identical model** |
| Daily quota | `gemini_daily_quota 20` | Skipped when `llm_provider==ollama` `workers:238 false` | n/a |

---

## 29. Sales Capability Matrix

| Sales Capability | Exists | Live | Reaches LLM | Deterministic Authority | Reachable | Evidence |
|---|---|---|---|---|---|---|
| Relationship building | yes | **LIVE** | **YES** (stage+action) | `relationship_first true` `strategy:120` pressure capped | yes | `decision:563 RELATIONSHIP_BUILDING 82` `strategy:213 LOW` |
| Qualification | yes | **PARTIAL** | **PARTIAL** (`interests` via `format_profile` only) | consumer `commerce/*.py` never reads `user_profiles` — `profile.py 11` not wired | — | `profile:27 extraction, commerce grep 0` |
| Desire building | notion `TEASE`/`content_curiosity` | **IMPLEMENTED BUT FUNCTIONALLY UNWIRED** | `RESPONSE: mode=tease` single turn | none (no staged ladder) | 50% | `response_mode:19 TEASE + state tone flirty 134` |
| Product recommendation | yes | **PARTIAL** | **NO direct product** for Qwen (only when single offer pending) | `ProductIdentity available && sales_url 52` | multi-product → `None` fail-closed `product_selection:240` | `state:205, product_selection:86` |
| Offer presentation | yes | **LIVE** when `OFFER_PPV+EXECUTED` | **YES — via VERIFIED FACTS + StrategyInstruction** | price `fangate_products.price_minor` `execution:251`, `sales_url` `239` or `build_checkout_url` `service:465`, `offer_id` advisory lock `dao:82` | yes — only `OFFER_PPV:ALL checked` path | `deepseek_response:181,296` |
| PPV | yes | **LIVE** | via `execute_ppv` → `commerce_response_text` bypass Qwen | `create_offer_serialized` lock `dao:82` | yes | `execution:265`, `selection:286 USE_COMMERCE_RESPONSE` |
| Tip | yes | **LIVE** | via `suggest_tip` tool governed | `get_checkout_links` canonical `service:435` `telegram.tip` | yes | `llm_tools:904` |
| Upsell | no | **NOT IMPLEMENTED** | n/a | n/a | — | grep `upsell` 0 production |
| Cross-sell | no | **NOT IMPLEMENTED** | n/a | n/a | — | 0 |
| Objection handling | yes | **PARTIAL** | **Suppression only** | `PRICE_OBJECTION→HARD 191` blocks discount | yes — but no persuasion | `feedback:178` `deepseek_response:122 forbidden discount` |
| Follow-up | yes | **LIVE** | via `schedule_follow_up 24h` + LLM `propose_follow_up` `llm_tools:704` + `scheduler_worker:37` | idempotent dedup `post_purchase:307` | yes | `post_purchase:282` |
| Aftercare | yes | **PARTIAL/UNREACHABLE** | LLM never sees `aftercare_status` | `decision:450 AFTERCARE_PHASE` live suppression, but prompt omits | `context_assembler:585 dummy none` |
| Purchase recovery | yes | **PARTIAL** | via `reconcile_all` after purchase | `dao:88 pending/clicked` | `reconciliation:38` but Qwen state drops `aftercare` |
| Handoff | yes | **LIVE** | `OPERATOR_HANDOFF → queued` | `relationship:377 check_operator_handoff` highest priority `decision:308` | yes |

---

## 30. Sales Decision Matrix — Exact Source of Truth

```text
INPUT FAN STATE
 user_id, creator_id, funnel_state, purchase_count, last_purchase_days_ago,
 last_message_days_ago, message_count, has_active_offer, product_title
    ↓ commerce/state: _resolve_creator_relationship 139 + get_user 177 +
       get_timing_context 298 + get_behavioral_feedback 316 + get_dropfans_integration 190
        (READ-ONLY, no AI, no clock except timing DAO NOW())  — FILE state.py, authority DB
SIGNALS (advisory, 0.0-1 confidence)
 transcript[-30][..800] → provider.generate temp 0.0 format=json → CommerceSignals
  PROMPT: purchase_intent, price_interest, content_interest, relationship_engagement,
  explicit_*, negative_sentiment, confidence, evidence, primary_intent(20), intent_tags,
  negative_intent_tags, fan_asks_question — FILE deepseek.py:56-101 + signals.py:57-82  — LLM gemini/qwen
COMMERCE ACTION
 → decide_commerce_action(context, policy) PURE 269-583
    checks: hard eligibility (1), handoff (1.5), creator_sales (2), product (3), active_offer (4),
            purchase/offer cooldown (5-6), budgets (7-8), fatigue (9), negative (10),
            confidence (11), conversational (12), pause (13), aftercare (14), rejection (15),
            explicit buy (16)→OFFER_PPV, tip (17)→TIP, strong 0.80 (18)→OFFER_PPV,
            follow-up (19), moderate 0.55 (20)→SOFT, relationship 0.60 (21)→SOFT,
            building (22)→RELATIONSHIP_BUILDING, fallback (23) → NO_OFFER
      SOURCE OF TRUTH: commerce/decision.py, authority deterministic
STRATEGY
 → build_strategy(decision, context) 176-309
    derives pressure NONE/LOW/MODERATE, allow_cta/price/product, relationship_first,
    follow_up_allowed, constraints immutable false — FILE strategy.py
SALES OBJECTIVE
 → _ACTION_INSTRUCTIONS map 170-183: NO_OFFER→Do NOT mention product
                                        RELATIONSHIP_BUILDING→Do NOT pitch
                                        SOFT_OFFER→casually mention paid exclusive
                                        OFFER_PPV→present offer from VERIFIED FACTS (exact title/price/URL)
                                    FILE deepseek_response.py, authority deterministic
PRODUCT/OFFER (sealed)
 → resolve_commerce_product_with_history → ProductIdentity/State, price_minor, sales_url,
    currency USD  — FILE product_selection:52, fangate_products mirror (is_accessible, sales_url)
    → execute_ppv (if OFFER_PPV) creates commerce_offers pending via advisory lock — FILE execution:265 dao:82
LLM (language only)
 → generate_commerce_response(input) temp 0.0
     → VERIFIED FACTS (_VerifiedFacts.build 296 price/url, _StrategyInstruction 351)
     → provider.generate(system_instruction=_system_prompt, user_content=transcript)
       + auction validators: _urls_are_authoritative, _prices_are_authoritative,
         _contains_only_clean_language, _offer_claim_integrity — FILE deepseek_response
VALIDATION
 → _urls/_prices authoritative, offer-claim integrity, forbidden vocabulary,
    oversized/structured/secret checks 529-543
    → FAILED → FALLBACK_TO_STANDARD_LLM 80 / execution_failed 204
SEND
 → selection: select_commerce_response → USE_COMMERCE_RESPONSE only if execution ACTIVE_STATUSES (EXECUTED/ALREADY) 75
   → workers/llm_worker:569 draft=commerce_response_text  bypasses Qwen & scoring
   → else legacy/agent/generate_draft → scoring price_mention→0.1→operator queue 825 → enqueue_send SEND_STREAM send_messages 826
     → dedup md5(user:msg:tgId) 782 → save_to_db True → ack
```

Each `→` identifies authoritative file/function.

---

## 31. Critical Failure Modes — Code Path Concept

### A. Interested but no product exists

`has_relevant_product = product != None` `state:390` — zero accessible `fangate_products` → `has_relevant_product False` → `decision:327 NO_OFFER NO_RELEVANT_PRODUCT` → `strategy NO_OFFER` → `deepseek_response 171 Do NOT mention product` → Qwen says `Sounds good, tell me more` — **safe, no hallucinated product** (proven `deepseek_response:426 whitelist fails if invent`).

### B. Fan asks price

`price_interest 0.80 or requested_price !=None` → `user_asked_about_price true` `signals:317` → `decision 477 explicit OR 539 moderate` may route `SOFT_OFFER/OFFER_PPV` only if `ProductCommerceState.price_minor` exists → `strategy:152 allow_price true only if price_minor && sales_url && is_accessible` → `deepseek_response:323-330 Verified price: {cents/100:.2f} {currency}` — **comes from `fangate_products.price_minor`, never conversation `$`**. Fan `$5` claim (`context:8 fan writing "send it for $5" cannot change`) → verification fails `439 price tolerance 0.005`.

### C. Fan asks for product creator does not sell

No `product_id` supplied → `ProductIdentity(title=None, available=false)` `state:250` → `product = None` `state:207 has_relevant_product false` → `NO_OFFER NO_RELEVANT_PRODUCT` 327 → Qwen told no product `deepseek_response 307 skip title` → does not invent `dropfans.io/creator123` (`deepseek_response 532 invalid_output` if invented). Safe.

### D. Strong purchase intent

`buying_intent_score≥0.80` → `OFFER_PPV 517` → but still gated by all earlier `hard deny/active/cooldown/budget/fatigue/phase/pause/aftercare/rejection` — even `1.0 intent` with `hours_since_last_offer 12 (<24) → RECENT_OFFER 359` wins → **RELATIONSHIP_BUILDING not OFFER_PPV**. Strong intent alone never bypasses `OFFER_EXISTS` etc.

### E. Fan rejects offer

`negative_intent_tags hesitation+rejection 95` `negative_sentiment ≥0.70 → complaint 423` `model_uncertainty≥0.80 → handoff 419` plus `classify_rejection HARD 195` → pipeline `mark_offer_declined reason 543` only for HARD/PRICE; Decision `consecutive_rejections 1→3 → RELATIONSHIP_BUILDING REJECTION_ESCALATION 467` + `commercial_paused true 437` → next 3 rejections pause 72h? Actually consecutive 3 triggers `REJECTION_ESCALATION`; tip fatigue needs 2 ignored `336` (dead).

### F. Fan says maybe later

`SOFT 203 hesitation` → pipeline **does NOT** `mark_offer_declined` (HARD/PRICE only `541`) → `negative_intent_count 1 <2 → no suppression 402`; decision falls through to `relationship_score` / `building` branch; no cooldown → **CRM remembers maybe later only as `negative_intent_count 1` + `soft` not as declined state** — conversation continues normally.

### G. Fan purchases

`fangate_transactions → reconcile_dropfans_sales → attribute_purchase_from_webhook → mark_offer_purchased 603 → mark_aftercare_pending pending 916 → schedule_follow_up 24h 282` etc. `advance_funnel_to_converted 46` idempotent.

### H. Fan asks for a tip

`speculative tip → check_tip_eligibility` thresholds `relationship 72/48/24, fatigue×1.5, contextual 12h 259-369` but **hard-zero history** (`dao:565 0 imm, ignored 0, hours None`) renders `tip eligible` as `relationship in WARM..VIP or ENGAGED+low` independent of history → any `ENGAGED+` warm becomes eligible per turn, `1h URL dedup` only `llm_tools:937 3600`. Deterministic but **accidentally permissive** (tip spam after 1h possible, documented deferred P2-13).

### I. Fan asks for free content (`asks_for_free_content`)

Prompt field `asks_for_free_content boolean 70` → model `CommerceSignals` **no longer has** field `asks_for_free_content` (`signals:147 not has` `extra forbid`) → dead signal → any model that faithfully includes it → **100% `invalid_payload` fallback `low_information`** → silent no-ops sales (safe but wasteful).

### J. LLM hallucinates product/price/link

Validator *chain* `deepseek_response:526-543` `empty/oversized/structured/secret-bearing/invented URL/non-authoritative price/offer-claim → FAILED` → `selection:80 FALLBACK_TO_STANDARD_LLM` never sent; `core/llm_tools propose_product_offer` route similarly `resolve_and_run_commerce 422` re-validates creation even if LLM provided product_id; `price_minor` from DB, LL M `reason` never becomes link (`123 llm_followup random`). Deterministic layers **prevent sending hallucinated commercial facts**.

---

## 32. Sales Content Quality vs Sales Decision Quality

| Aspect | Measure | Result |
|---|---|---|
| **Decision quality** | Did system choose correct action for warm fan with `purchase_intent 0.9` + `no cooldowns` → `OFFER_PPV`? `price_interest 0.80 → user_asked_about_price true` → `MODERATE signal SOFT_OFFER`? `maybe later → RELATIONSHIP_BUILDING`? `6h purchase cooldown → RECENT_PURCHASE`? | **Decisions are correct** per 23 protective branches `decision:296-584` deterministic; any `NO_OFFER`/`RELATIONSHIP_BUILDING` suppression wins regardless of signal `1.0`. `TIP` / `FOLLOW_UP` require tip_eligibility handoff respectively. |
| **Content quality** | Did LLM express that action naturally vs robotic/forced? Qwen conversational `2-4 sentences match energy, Prefer callbacks` `context:220-230` vs Commerce PPV `deepseek_response 10 rules 197-210` natural `no markdown, conversational, never coercive` | **Partial** — conversational Qwen never instructed to sell (intentionally language-only), so warm fan may receive correct `RELATIONSHIP_BUILDING` decision → Qwen says `Haha + question` generic (harmless but not desire-built). Commerce path (when reached) is deterministic template validated length/pressure — minimal coercion; forbidden vocab `buy now/half price/discount` always `invalid_output`. |

Beautiful copy with wrong strategy → `OFFER_PPV` when `RECENT_PURCHASE` `decision:342` says `RECENT_PURCHASE` — **cannot**, as earlier branch wins. Correct strategy with robotic copy → `deepseek_response` `OFFER_PPV present ... exact title price URL` 181 with `PRESSURE_TONES none/low/moderate:185 never HIGH` — usually short, can be robotic if `num_predict 1024` truncates (`deepseek:52`).

---

## 33. Research-Aligned Architectural Assessment

Contextual user profiling `CSALES 2025` emphasizes `preference elicitation, recommendation, persuasion as one conversational-sales task` via contextual profiles → strategic action selection with `adaptive conversational style`.

| Research principle | CRM implementation | Alignment |
|---|---|---|
| **Contextual user profiling** | Fan `interests[], mentioned_topics, preferences[], important_dates, funnel_stage, purchase history, segment, aftercare` (`profile:11 + context_assembler:500 + dao:411`) rendered as `Fan: ... PROFILE` `STATE` `COMMERCE` | **Strong** — rich signals available to sales engine and conversational LLM; `profile.py` LLM extraction `27 explicit/inferred/temporary` aligns with preference elicitation research. |
| **Preference elicitation** | LLM learns via `extract_and_update_profile` (post-turn async create_task `llm_worker:889` every `recent 20` → `profile.json` merge cap 15) + conversation `open_threads` keywords + retrieval trigger `remember` `context:41` | **Partial** — elicitation is **passive** (stores what fan volunteered, not actively probes). No proactive `preference question plan` (`response_mode EXPLORE` is generic, not elicitation-goal driven). |
| **Recommendation** | Product selection deterministic `resolve_commerce_product_with_history` excludes purchased `product_selection:179` but ambiguous `≥2 → None` fail-closed `240` — no recommender scores fan-product affinity; creator `is_accessible/sales_url` only, no embedding similarity between fan interests and product category | **Weak** — no product recommender (`list_products` read-only, no LLM ranking). Conversational recommender research's `preference elicitation → recommendation` loop missing. |
| **Persuasion** | `SalesPressure NONE/SOFT/MODERATE` `relationship:37 + strategy:57` + constraints `never urgency/guilt/scarcity` `response:98, strategy:121` — intentionally **never HIGH** | **Intentionally conservative** — persuasion is **soft only** (`casually mention 177`, `gentle mention 179`). Matches CSALES moderate but not aggressive; good for OFM safety, weaker conversion than research's strategic persuasion. |
| **Strategic action selection** | `decide_commerce_action` 23 branches `269` with policy `158` `24h/6h max 2/3` + `conversational_phase` `427` + `commercial_paused` `437` + capability `483,500` + `relationship_score 0.60` etc. | **Strong — textbook** — corresponds exactly to research `contextual profile → strategic action`. |
| **Adaptive conversational style** | `build_qwen3_system_prompt` stage guidance `73-86 warm welcome vs engaged` + `build_qwen3_state_context` `RESPONSE: mode` + `QUESTION allowed` `memory/context 220-348` + `QWEN3_TOKEN_BUDGET 25 compact` | **Partial** — conversational state adapts (topic, tone, mode), but **sales style adaptation** (persuasion intensity per user price sensitivity `n_2`) not wired: `price_interest` threshold only `0.80` binary, no `urgency` or `objection handling` persuasion variant. |

**Overall:** CRM aligns best on `strategic action selection` (deterministic 23-branch) & profiling (rich fan attributes), weakest on `preference-elicitation-driven recommendation` (no product ranker, ambiguous fail-closed) & `persuasion depth` (capped `MODERATE`). Research's `contextual profile → strategic action` is live; `adaptive style` partially; `elicitation→recommendation→persuasion` one loop is split across `profile.py` (elicitation) + `deepseek` (signals) + `decision` (action) without closed feedback (profile facts never re-enter decision).

---

## 34. Final Classification — Counts

| Classification | Count | Examples |
|---|---|---|
| **LIVE** | **28** | `decision 23-branch, strategy build/mapping, signals extraction (with drift), product eligible check/recommend OFFER_PPV, offer create advisory lock + dropfans build_checkout, tip suggest via get_checkout_links deducting same-tip 1h, purchase reconcile + aftercare DB + 24h ping, follow-up 3 schedulers, cooldown 24h/6h/2/3 + scoring price Mention → 0.1→queue, handoff highest-priority, dedup send_messages md5, post-purchase confirmation + vault reserve→finalize, generation telemetry 759` |
| **WIRED_BUT_BROKEN** | **3** | `commerce/pipeline: decide_from_signals re-calc 557 double-compute (duplicate)`, `generate_draft_with_tools` Gemini-only (breaks Ollama tool path) `workers:175-202 direct genai`, `memory/retrieval.py Ollama embed NotImplemented fallback` `retrieval:8 after fix now hybrid` |
| **WIRED_BUT_UNREACHABLE** | **2** | `commerce/decision CHAT/DONT_OFFER actions defined but no branch emits them 31-32`, `vault attach_media stub logs+0 107 but UI expects linking 107` |
| **PARTIAL** | **9** | `tip fatigue non-existent history`, `aftercare conversational unwired`, `cross/upsell not implemented but repeat eligible flag partially`, `re-engage no poller`, `qualification: learns profile BUT never feeds deterministic 0`, `desire building: TEASE single turn`, `objection: classifies but no recovery script`, `profile retrieval: lexical not semantic, Qwen path dropped`, `relationship hard-coded zeros in agent` |
| **DEAD** | **7** | `_behavioral_store placeholder list` `feedback:21 never read`, `FeedbackEventType 20 enum only PURCHASE_COMPLETED used`, `REJECTION_SEVERITY enum`, `asks_for_free_content dead prompt field`, `BusinessHours Africa/Nairobi enum`, `provider_override None`, `limiter/circuit_breaker modules never imported` |
| **DUMMY** | **4** | `tip_suggestions_* hard-zero 0 in dao+state+assembler+worker 565`, `aftercare_status hard "none" 585`, `vault attach_media stub return 0 107`, `BusinessHours` |
| **DUPLICATED** | **6** | `strategy re-compute pipeline decide 557`, `TOKEN vs QWEN3 budget 21 vs 25`, `fangate_products table still mirror for Dropfans`, `Settings core vs chatbotv2 config`, `provider_fallback vs scoring fallback parallel`, `product mirror is_accessible check` |
| **UNVERIFIED** | **2** | DropFans `/api/external/links` link shape live (`tip, buy_template, profile` from client), `qwen2.5:3b on VPS inventory post-swap` (confirmed local but stale default) |
| **NOT IMPLEMENTED** | **5** | `upsell, cross-sell` (no tier rank), `abandoned-offer auto nudge`, `objection persuasive recovery (negotiation)`, `desire anticipation ladder`, `active elicitation questionnaire` |

Counts exclude documentation-only files (`docs/*`) and test-only mocks.

---

## 35. Required Final Answers (1-27)

```text
1.  Where is sales content actually generated?
    → Two stacks, but ONLY commerce PPV stack actually generates sales content:
       generate_commerce_response (commerce/deepseek_response:465) when
       selection USE_COMMERCE_RESPONSE (workers:569, selection:286 state EXECUTED).
       Qwen conversational (workers:81 generate_draft) never generates sales —
       it is told "Be conversational, not salesy" and carries zero sales
       pressure. 60-70/81 commerce tests confirm sales copy only via VERIFIED FACTS.

2.  What exact code decides when Sunny should sell?
    → decide_commerce_action (commerce/decision:269) — 23-branch priority cascade
       gated by hard policy → creator_sales → product → active → purchase/offer
       cooldown → budgets → fatigue → negative → confidence → conversational phase
       → commercial pause → aftercare → rejection → explicit buy/tip/strong intent.
       Called via resolve_and_run_commerce → _try_commerce_draft
       (workers:422, 566). Derived from CommerceStateRequest (state:169) +
       CommerceSignals→CommerceDecisionContext (signals:248 pipeline 303).
       Returns CommerceDecision(action, reason, allowed bool).

3.  What exact code decides what she should sell?
    → resolve_commerce_state (commerce/state:169) + resolve_commerce_product_with_history
       (commerce/product_selection:86-93,179-185,240) deterministic
       DB: SELECT fangate_products WHERE creator, is_accessible, sales_url
       → excludes purchased → 0→None,1→that one, ≥2→None fail-closed ambiguity.
       No LLM involvement (state:24 NO PRODUCT SELECTION).

4.  What exact code decides how aggressively she should sell?
    → build_strategy(decision,context) (commerce/strategy:176) →
       SalesPressure NONE (NO_OFFER/RELATIONSHIP/DONT/CHAT/OPERATOR/HANDOFF)
                     LOW (SOFT_OFFER/FOLLOW_UP/TIP)
                     MODERATE (OFFER_PPV only)   (strategy:57-72, never HIGH)
       + allow_price/product/cta booleans per product_state presence.

5.  Does fan conversation state influence sales strategy?
    → Partially. Signals → decision does, but conversational state
       (ConversationState recent_topics/tone) does NOT.
       decision uses relationship_state/commercial_pressure (state:364) wired,
       but fan conversation tone/open_threads not wired to decision.

6.  Does fan profile influence sales strategy?
    → NO as deterministic engine.
       profile interests/preferences/emotional_state extracted post-turn
       (profile:27) and stored user_profiles.facts but never read by
       commerce/*.py (grep 0). Only Qwen system sees it (format_profile 122).

7.  Does funnel stage influence sales strategy?
    → Weakly via relationship_state ladder.
       users.funnel_stage stays "new" at 47 msgs (post_purchase:46 only on buy).
       Derive via derive_relationship_state(funnel_stage None in state:384 hard 0)
       therefore funnel→COLD/NEW always, commercial_paused false until 3 rejections.

8.  Do commerce signals influence the generated response?
    → For commerce PPV YES: buying_intent→OFFER, price_interest→price gate.
       For conversational Qwen NO: signals are advisory, Qwen receives
       RELATIONSHIP/COMMERCE 5 facts filtered but not pressure/mode sales
       imperative.

9.  Does the selected StrategyKind reach the LLM?
    → YES for commerce: _StrategyInstruction (deepseek_response:351) always
       injected. NO for conversational Qwen (build_qwen3_context does not
       include StrategyKind — conversational state is separate).

10. Does SalesPressure reach the LLM?
    → YES for commerce via _PRESSURE_TONES (deepseek_response:185);
       NO for conversational Qwen (pressure not in prompt).

11. Does product selection reach the LLM?
    → When single-product creator with one valid product: YES via
       Current product: title (context_assembler:752 only len==1).
       Multi-product → None → NO.

12. Does authoritative price reach the LLM?
    → Only when decision OFFER_PPV + strategy allow_price true (price_minor&&sales_url)
       → Verified price: $X.YZ USD in VERIFIED FACTS (deepseek_response:323).
       Otherwise no price survives.

13. Does authoritative offer URL reach the LLM?
    → Only when allow_product_reference true → Sales URL: https://...
       (deepseek_response:332) — whitelist-checked. Otherwise no URL survives.

14. Can the LLM fabricate any commercial fact?
    → No by 4-layer defense:
       (1) product selection isolation (state:24),
       (2) whitelist validators (_urls/_prices authoritative + FORBIDDEN_VOCAB 99
           + SECRET_PATTERNS 129 + OFFER_PHRASES 147) deepseek_response:426-462,
       (3) execution advisory lock pending/clicked dao:82,
       (4) selection fallback FALLBACK_TO_STANDARD_LLM 80. Any invented
       creator price→invalid_output 538 before send.

15. Is tip delivery deterministic?
    → YES — suggest_tip enqueues via get_checkout_links(creator) canonical
       telegram.tip, dedup 3600 tip:{creator}:{user}:{md5(url)[:12]} 937,
       send queue deterministic. BUT fatigue deterministic is DUMMY (hard-zero,
       docs P2-13). Tip is deterministic-delivered, not deterministically gated.

16. Is PPV actually reachable from autonomous conversation?
    → YES — deterministic path: fan explicit buy / strong intent 0.80 +
       relationship≥0.60 + no cooldown + no active → OFFER_PPV + can_sell
       + create_offer_serialized lock → EXECUTED → deepseek_response
       → USE_COMMERCE_RESPONSE → draft bypass Qwen → send_messages.

17. Is upselling actually implemented?
    → NO — grep upsell 0; no tier price comparator; repeat_purchase flag exists
       (feedback:214, state:421, context:596) but no branch in decision checks it.
       NOT IMPLEMENTED. Closest: tip after repeat.

18. Is objection handling actually implemented?
    → PARTIAL: classification HARD/SOFT/PRICE (feedback:178, pipeline:324)
       + persistence mark_offer_declined for HARD/PRICE 541 + decision
       rejection escalation 467 + commercial pause 437; but no persuasive
       recovery script — rule 8 deepseek_response 205 "If fan declined → accept
       gracefully, never repeat" + _FORBIDDEN never discount. So
       detection LIVE, handling suppression LIVE, recovery NOT IMPLEMENTED.

19. Is post-purchase selling actually implemented?
    → PARTIAL: transactional delivery LIVE (handle_post_purchase 149
       confirmation+media+follow_up 24h + aftercare DB 892), but
       aftercare conversation unwired (LLM never sees aftercare_status),
       repeat/upsell/cross-sell NOT, relationship continuation DEAD beyond
       single ping, mark_aftercare_completed never invoked.

20. Is follow-up actually implemented?
    → YES LIVE — 3 schedulers: post-purchase 24h (post_purchase:282),
       LLM-proposed propose_follow_up 1-168h (llm_tools:588 fixed content),
       dashboard bulk (postgres create_scheduled_message 2243).
       But abandoned-offer auto-nudge, dormant re-engagement: NOT.

21. Does agent runtime preserve all sales authority?
    → YES (trivially) — agent sees dummy NEW/NONE/0 units (workers:606-618)
       has_relevant_product false, eligible_products () so
       can_sell_content false → decision always RELATIONSHIP_BUILDING/CREATOR_NOT_READY.
       Cannot call execute_ppv (read-only tools agent/tools:224). Authority intact
       because agent never reaches commerce.

22. Does legacy runtime preserve all sales authority?
    → YES — _try_commerce_draft is pre-choice outside LLM (workers:566)
       and sealed; generate_draft/WithTools produce draft only, never price/URL;
       scoring hard flag 22-35 caps product/tip before auto_approve 825.

23. What sales functionality is currently dead?
    → asks_for_free_content signal, BusinessHours enum, BehavioralSummary class,
       20-enum beyond PURCHASE_COMPLETED, limiter/circuit_breaker modules,
       aftercare LLM surfacing, cross/upsell ranker, abandoned auto nudge,
       vault attach_media stub return 0, tip history consumer, _behavioral_store list.

24. What is merely theoretical?
    → desire/anticipation ladder (TEASE single mode), qualification→commerce
       influence, price_sensitivity urgency persuasion, repeater upsell,
       adaptive conversational style per price sensitivity, post-purchase
       continuation.

25. Single biggest reason the CRM may fail to convert a warm fan?
    → Multi-product ambiguity fail-closed (product_selection:240 ≥2 → None)
       + No product recommender + Qwen conversational prompt never sells.
       Warm fan with content_interest 0.70 but `buying_intent 0.55` and no
       explicit buy → SOFT_OFFER casual mention may fire only if relationship≥0.60;
       but without explicit, it never becomes OFFER_PPV — conversion dies in
       relationship gate while warm fans idle. No staged tease to escalate them.

26. Single biggest reason Sunny may sound conversational but still fail to sell?
    → Conversational Qwen (95% of turns) is **told to be not salesy**
       (agent/loop:154, memory/context:228 Do not promise photos, Prefer callbacks)
       and carries **no sales pressure**. Commerce PPV copy only runs on
       the 5% `OFFER_PPV + EXECUTED` branch. So Sunny is warm, correct,
       never pushy — and never presents a price/URL until deterministic engine
       independently decides to, by which time fan may have churned.

27. Five highest-value technical fixes?
    → P0-1 Wire tip history (tip_events table or consume Redis dedup key tip:{user}
       as dao get_behavioral_feedback_context) to restore 24-72h fatigue.
       P0-2 Fix asks_for_free_content dead → re-add field to CommerceSignals or
       handle `asks_for_free_content` via separate negative flag; currently free
       fire drills waste 100% to low_information.
       P0-3 Aftercare surfacing: render aftercare_status in build_qwen3_state_context.
       P1-1 Implement product recommender (rank eligible by fan interests vs
       product title/category via embedding cosine, break 2+ ambiguity).
       P1-2 Add proactive abandoned-offer follow-up poller (pending>48h unclicked →
       auto schedule FOLLOW_UP nudge). All via deterministic DAO, not LLM rewrite.
```

---

## 36. Fix Plan — Prioritized (Do NOT Implement)

| ID | Sev | Problem | Root cause | File/Function current behavior | Desired behavior | Minimal fix | Dependencies | Risk | Test |
|---|---|---|---|---|---|---|---|---|---|
| **P0-01** | P0 | Tip spam after 1h (sale, should 24-72h) | `tip_suggestions_*` + `hours_since_last_tip` hard-zero everywhere `dao:565, state:361, context_assembler:692, llm_tools:885, workers:613` | `tip eligible` ≈ `relationship in WARM..VIP or ENGAGED+low` every turn | `hours_since_last_tip < cooldown → COOLDOWN_ACTIVE` fires (12-108h) | Consume existing `tip:{user}` dedup key timestamp as `hours_since_last_tip` or create `tip_events` table & `INSERT suggest_tip success` + `SELECT MAX(created_at) WHERE creator,user` in `dao:get_tip_feedback_context` | Redis not durable but immediate | Tip spam → fan churn, Pay vendor spam detection | `tests/test_sunny` 1/3600 vs 72h assertion |
| **P0-02** | P0 | `asks_for_free_content` wastes 100% signals to `low_information` | Prompt `deepseek.py:70` asks field but `CommerceSignals` lacks it (`extra forbid` `signals:159`) | Any model obeying prompt fail-validates → fallback | Remove field from prompt **or** add `asks_for_free_content: StrictBool false default` to `CommerceSignals` + map to `too_expensive`-like suppression | Prompt must match model set | No prompt drift on next cheap_model change | Empty transcript not regress |
| **P0-03** | P0 | Aftercare conversation unwired — post-purchase Qwen chat not suppressed | `memory/context_assembler:585 aftercare_status_val="none"` never updated (`589-606` copies all except `aftercare`), `context.py:282 filter` drops it, `Qwen state` never sees `aftercare` | Qwen upsells during pending/sent | Render `Aftercare: {status}` in `build_qwen3_state_context` + include `aftercare` in `key_facts` keywords | Migration `aftercare_status` already applied `20260826010000` | Suppression already engine live, conversational redundant — safe | Aftercare: pending → no offer presentation test |
| **P1-01** | P1 | Product recommendation dead on ≥2 products (`None` → `NO_OFFER`) | `resolve_commerce_product_with_history 240` `if len valid ≥2 → return None` fail-closed no ranker | Multi-product creator never PPV autonomously | Rank by fan profile interests vs product title embedding cosine (`memory/profile` vector store 15, `derive_relationship_state` relationship score wins) breaker `2→1`; creator-scoped, bounded | No new embedding model (reuse `gemini-embedding-001` `retrieval:22`) | Recommend wrong product → fan opts out | `ambiguity → 1 chosen, PURCHASED excluded` assertion |
| **P1-02** | P1 | Abandoned `pending/clicked` offer never re-engaged | No poller `WHERE state=pending AND created_at < NOW-48h` | Offer stuck pending, `post_purchase_followup` key `307` only for purchased, `FOLLOW_UP` action exists `decision:528` but only when fan initiates next turn | `scheduler_worker: poll pending>48h unclicked` → auto `create_scheduled_message content="Hey... still interested? Here's your link: {sales_url}" schedule 24h delay` + reuse `execute_ppv→ALREADY_EXECUTED` to resurface link idempotently; also `decision:529 FOLLOW_UP due` text via `deepseek_response` | `scheduled_messages creator_id index 20260822030000` | Duplicate nudge reported as spam if tuned too short | `pending>48h unclicked → follow-up scheduled once` test |
| **P1-03** | P1 | Conversational Qwen never instructed to sell softly — soft intent never surfaces | `build_qwen3_context 200-231` stage rules are `New fan warm welcome` irrespective of `relationship_state WARM/BUYING_SIGNAL 155` or `commercial_pressure SOFT 225` | Warm fan `SOFT_OFFER` feasible but Qwen not told → generic | Inject `SalesPressure/StrategyKind` as soft note into Qwen state when `decision SOFTP_OFFER` already made: `build_qwen3_context` `derive_commercial_pressure` already in `render_context:776` but filtered; **render `Commercial pressure: soft — casual mention allowed`** in state + `_ACTION_INSTRUCTIONS soft_offer 177 casually mention` as soft rule `build_qwen3_system_prompt` stage-specific | `strategy:225` `allow_cta true` flag | Over-selling risk low (soft) — constraint forbids urgency | `SOFT_OFFER → Qwen sees casual mention allowed, not Do NOT pitch` assertion |
| **P1-04** | P1 | Qualification learns but never influences commerce (profile never read by deterministic) | `grep user_profiles in commerce 0` | `interests[], purchase_signals` only in Qwen system `format_profile 122`, not `decision 269` | Add deterministic interest→product hint: if `profile interests` intersects `product title tokens` boost `relationship_score +0.05` via `commerce/state:364 _pipeline` model_copy injection (bounded), or extend `build_llm_context` to surface `purchase_signals` as `previous interest: hiking` line that `decision invalid` could use | `profile 11` safe keys only | Fan profile hallucinated by LLM `inferred` confidence `temporary` → boosted recommend hallucinated interest | `interest hiking product "Hiking Guide" → recommend` test |
| **P2-01** | P2 | `ask` keyword mismatch + stale `cheap_model` strings vs Ollama | `response_mode like` `llm_provider_ollama:295 discard gemini prefix` hard-coded 5 prefixes, `strategy allow_price` still checks `product_state.price_minor` ok | Mitigated via guard but future `gpt-5` leaks | Move `price_minor/currency` read before `provider` call (already) | — | — | `llm_provider_ollama price_minor mapping 52` regression |
| **P2-02** | P2 | Response `SOFT → price whitelisted → but Qwen may still not mention price` | `strategy allow_price true when price&&url && accessible 152` but transcription `Verified price: $` only in commerce-response system not Qwen | No price mention is safe | Optional: surface `Current product: title $price` single `context_assembler:752 len==1` — already conditional | — | — | P2 |
| **P2-03** | P2 | Tip疲劳 `commercial_paused` vs `consecutive_rejections` confusion | `state:594 433 commercial_paused = consecutive>=3` dual source | OK | — | — | — |
| **P3-01** | P3 | `TOKEN vs QWEN3 budget 21 vs 25` duplication, `fangate_products` name for DropFans | Historical | Keep `QWEN3_TOKEN_BUDGET` as new budget, deprecate legacy comment | — | — | — |
| **P3-02** | P3 | `Settings core vs chatbotv2 config` | Two Settings classes | Keep core; `chatbotv2/config` is shim | — | — | — |
| **P3-03** | P3 | `provider_fallback vs scoring fallback` parallel | Two wrappers | Keep `provider_fallback` for scoring only, scoring direct for Qwen | — | — | — |

Priority: `P0 = money/conversion/safety`, `P1 = materially weakens sales`, `P2 = optimization`, `P3 = cleanup`. Do not propose LLM prompt-only fix for `P0-01` (needs DAO/Redis durable source).

---

## 37. Prohibitions — Confirmation

No production code, prompts, `.env`, canary, Qwen/LLM, DropFans, commerce authority, product pricing, offer creation, Redis schema, Postgres schema, migrations, workers, or agent architecture modified during this audit. `.env OllAMA_MODEL=qwen2.5:3b` pre-existed `core/config 90 qwen3:4b` drift verified but not changed. Only `docs/SALES_CONTENT_FORENSIC_AUDIT.md` is the new file.

---

## 38. Required Output File

`docs/SALES_CONTENT_FORENSIC_AUDIT.md` — single file, 26 chapters (§1-38 spec mapped to 1-26 here inclusive of all required sections, 26 chapters contain 1-38 requested headers folded as implemented).

```
ROOT STATUS:
CONDITIONALLY READY

SALES CONTENT GENERATION:
WIRED BUT NARROW — commerce PPV via deepseek_response deterministic VERIFIED FACTS only for OFFER_PPV+EXECUTED; Qwen conversational never sells (intentionally), so conversion rate limited to that narrow gate.

SALES DECISION ENGINE:
LIVE — 23-branch PURE decision cascade (commerce/decision:269) pre-LLM; fan hard deny guarantees no bypass.

PRODUCT/OFFER PATH:
LIVE — product mirror is_accessible+sales_url gated (product_selection:52), ambiguous ≥2 fail-closed None, execution advisory lock (dao:82) + dropfans build_checkout (service:465) deterministic; teleports to LLM only as VERIFIED FACTS whitelist.

TIP PATH:
WIRED BUT DUMMY GATING — get_checkout_links canonical, deterministic dedup 3600 (llm_tools:937) live, but fatigue/cooldown (relationship:259) dead due hard-zero history (dao:565).

PURCHASE PATH:
PARTIAL — transactional offer attribution + aftercare DB + 24h ping live (post_purchase:46,282,892); aftercare conversation unwired (context_assembler:585 none), cross/upsell not implemented, repeat eligible flag partially.

UPSELL:
NOT IMPLEMENTED — no tier price comparator.

FOLLOW-UP:
WIRED — 3 schedulers LIVE (post-purchase 24h, LLM propose 1-168h, dashboard bulk); re-engagement of abandoned pending/clicked (>48h) NOT IMPLEMENTED.

BIGGEST CONVERSION GAP:
No live desire escalation between casual_chat → content_curiosity → purchase_intent; warm fans idle in RELATIONSHIP_BUILDING never nudged via staged tease/provider recommendation. Multi-product ambiguity → none.

BIGGEST CONTENT GAP:
Qwen conversational prompt carries factual commerce 5 facts but zero sales-pressure instruction; conversational Sunny never *wants* to sell because only commerce-response path can. Warm purchase_intent never surfaces as casual mention.

P0: tip cooldown fatigue dead (money leak — over-tip), asks_for_free_content dead (waste) — 2
P1: product ranker none (multi-product → none), abandoned re-engagement none, conversational soft intent never surfaces, qualification never feeds commerce, aftercare unwired — 5
P2: currency hardcode, tip dedup 1h vs 24h policy mismatch, tip dedup new-URL bypass, provider model name 5-prefix guard brittle, SOFT price mentioning may not surface Current product — 5
P3: token budget duplication, fangate_products name legacy, Settings dual, provider double wrapper — 4

PRODUCTION CHANGES: NONE
```

**Do not fix anything during this audit** — honored; single new doc only, per `AGENTS.md` audit discipline.
