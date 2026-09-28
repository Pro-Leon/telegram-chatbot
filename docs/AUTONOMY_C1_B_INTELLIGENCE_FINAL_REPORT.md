# Phase C.1-B — Conversational Intent & Signal Intelligence: Final Report

## Date: 2026-08-26

## 1. Research Summary

### KVIQBOT (Trained OnlyFans Creator Chatbot)
- Trained from creator DMs (~20 min)
- Qualification → pricing ladder → unlock → payment → aftercare
- Cool-downs after no; ghost recovery
- 17% DM→PPV conversion, $38k sold, 83s median reply
- Key insight: conversation-first approach, only offer when signals are strong

### DropFans API
- 20+ endpoints, bearer auth, no sandbox, polling-only
- Tips via canonical URLs, `GET /links` for tip URLs
- Rate limits 60/min
- DropFans is SOLE active provider

### Industry Patterns
- **Timing signals**: Recent content posting, tip activity, price mentions, explicit requests
- **Relationship stages**: Stranger → acquaintance → interested → buyer → repeat buyer → VIP
- **Conversational phase**: Opening → rapport → engaged → commercial → close → post-purchase
- **Negative signals**: Rejection, hesitation, complaints, price objections
- **Fatigue**: Too many offers in a window suppresses conversion

## 2. Architecture

### Intent Taxonomy (18 categories)
`greeting`, `casual_chat`, `personal_disclosure`, `relationship_building`, `content_curiosity`, `content_request`, `price_inquiry`, `purchase_intent`, `repeat_purchase_intent`, `post_purchase`, `aftercare`, `tip_interest`, `complaint`, `custom_request`, `negotiation`, `hesitation`, `rejection`, `uncertain`, `reassurance`, `appreciation`, `operator_request`, `other`

### Negative Intents (3)
`hesitation`, `rejection`, `complaint`

### Commercial Intents (6)
`content_curiosity`, `content_request`, `price_inquiry`, `purchase_intent`, `repeat_purchase_intent`, `tip_interest`

### Conversational Phases (7)
- `opening` — greeting, first contact
- `rapport` — building relationship, personal disclosure
- `engaged_chat` — casual conversation, no commercial intent
- `content_curiosity` — fan asks about content
- `commercial_interest` — explicit purchase or price interest
- `post_purchase` — after a purchase, aftercare
- `cooldown` — after rejection, cooling down
- `operator_handoff` — needs human intervention

## 3. Changes Made

### `commerce/signals.py` — Intent taxonomy & multi-intent support
- Added `INTENT_CATEGORIES` (22 categories, including reassurance, appreciation, operator_request, other)
- Added `_COMMERCIAL_INTENTS` (6), `_NEGATIVE_INTENTS` (3)
- Added fields to `CommerceSignals`: `primary_intent`, `intent_tags`, `negative_intent_tags`, `fan_asks_question`, `topic_continuity`
- Validators for all new fields
- Updated `low_information()` with defaults
- Added `_derive_conversational_phase()` pure function
- Updated `signals_to_context()` to project new fields into `CommerceDecisionContext`

### `commerce/decision.py` — Phase-aware decision engine
- Added 5 new reason codes: `CONVERSATIONAL_CHAT`, `NEGATIVE_SIGNALS_SUPPRESSED`, `OFFER_FATIGUE`, `LOW_CONFIDENCE_CHAT`, `UNCERTAIN_AMBIGUOUS`
- Added fields to `CommerceDecisionContext`: `conversational_phase`, `negative_intent_count`, `signal_confidence`, `fan_asks_question`, `has_commercial_intent`
- Added `min_signal_confidence_for_commerce` to `CommerceDecisionPolicy`
- Added decision steps 7.5-7.8:
  - 7.5: Offer fatigue (recent_offer_count >= 1, recent_sales_attempt_count >= 2, buying_intent < strong threshold)
  - 7.6: Negative signal suppression (negative_intent_count >= 2)
  - 7.7: Low confidence → conversation (confidence < min_signal_confidence, no explicit intent)
  - 7.8: Opening/rapport phase suppression (phase in opening/rapport, no explicit intent)
- Used `policy.strong_buying_intent_score` for fatigue gate (was hardcoded 0.80)
- Used `policy.min_signal_confidence_for_commerce` for low-confidence gate (was hardcoded 0.30)

### `commerce/deepseek.py` — Updated extraction prompt
- Added 5 new fields to LLM prompt: `primary_intent`, `intent_tags`, `negative_intent_tags`, `fan_asks_question`, `topic_continuity`
- Added validation rules for each new field

### `commerce/context.py` — Orchestrator integration
- Added 5 new fields to `CommerceConversationContext`
- Updated `to_decision_context()` to project all C.1-B fields

### `commerce/pipeline.py` — Pipeline signal mapping
- Added imports for `_COMMERCIAL_INTENTS`, `_derive_conversational_phase`
- Updated `_apply_signal_flags()` to map new fields from signals to context

### `tests/test_phase_c1b_intelligence.py` — 67 tests
- Intent taxonomy validation (8 tests)
- Multi-intent support (6 tests)
- Negative signal detection (4 tests)
- Conversational phase derivation (11 tests)
- Phase-aware decision (4 tests)
- Offer fatigue (3 tests)
- Low confidence (4 tests)
- Scenario tests (14 tests)
- Adversarial tests (13 tests)

### `tests/test_commerce_deepseek.py` — Updated
- Added 5 new fields to `SIGNAL_FIELDS` set
- Added 5 new fields to `valid_payload()` helper

### `tests/test_commerce_pipeline.py` — Updated
- Updated `_signals()` helper with reasonable defaults (confidence=0.5, primary_intent="other") to avoid C.1-B guards overriding existing test intent

## 4. Forensic Audit Results

### CRITICAL (fixed)
1. **Dual decision path** — `to_decision_context()` was missing C.1-B fields, making them dead code in the orchestrator path. Fixed by adding fields to `CommerceConversationContext` and `to_decision_context()`.

2. **Prompt/intent taxonomy mismatch** — 4 intents in LLM prompt (reassurance, appreciation, operator_request, other) were not in `INTENT_CATEGORIES`, causing silent validation failure. Fixed by adding them to the taxonomy.

### MODERATE (fixed)
3. **Hardcoded 0.80 fatigue threshold** — Now uses `policy.strong_buying_intent_score`.

4. **Hardcoded 0.30 low-confidence threshold** — Now uses `policy.min_signal_confidence_for_commerce`.

### CLEAN
- No duplicated intent engines outside commerce/
- All `CommerceDecisionContext` creations pass `creator_id`
- No bypassed cooldowns via `AutomationService`
- No fangate references in new C.1-B files
- All new `CommerceSignals` fields have validators
- `_derive_conversational_phase` properly accessible

## 5. Test Results

```
3556 passed, 14 failed (all pre-existing), 1 skipped
```

All 14 failures are pre-existing:
- 8 DropFans model renames (title→file_name, price_cents, net_cents, pending_cents, web_buy_url)
- 1 fangate DB
- 5 integration_real_infra DB

New tests: 67 (C.1-B) + 3 (C.1-B related) = 70 new tests

## 6. Decision Engine Priority Order (updated)

1. Hard eligibility denial
1.5. Operator handoff needed
2. Creator commerce disabled
3. No relevant product
4. Existing active offer
5. Recent purchase cooldown
6. Recent offer cooldown (outcome-aware reasons)
7. Excessive offers / sales attempts per 24h
7.5. Offer fatigue (recent_offer_count >= 1, sales_attempts >= 2, intent < strong)
7.6. Negative signal suppression (negative_intent_count >= 2)
7.7. Low confidence → conversation (confidence < min, no explicit intent)
7.8. Opening/rapport phase suppression (no explicit intent)
8. Explicit user buying intent
8.5. Tip suggestion eligible
9. Strong buying signal (implicit)
10. Follow-up due
11. Moderate buying signal
12. Relationship readiness
13. Relationship building
14. No offer

## 7. Production Readiness

### Ready
- Intent taxonomy aligned between LLM prompt and validation
- Multi-intent support with negative signal detection
- Conversational phase derivation
- Phase-aware decision engine with fatigue, negative, low-confidence guards
- All thresholds in `CommerceDecisionPolicy` (no hardcoded values in decision path)
- Orchestrator correctly projects C.1-B fields

### Remaining work (Phase C.1-C onward)
- Rejection/cooldown intelligence (detailed cooldown strategies)
- Upsell paths (post-purchase → repeat purchase)
- Tip integration (tip_interest → tip suggestion)
- Aftercare flows (post_purchase → follow-up)
- Provider hardening (rate limits, retry logic)
- Observability (metrics, logging, alerting)
