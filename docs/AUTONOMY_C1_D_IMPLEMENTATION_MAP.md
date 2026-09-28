# Phase C.1-D — Implementation Map

## Current State (What Works)
- `commerce/feedback.py` — RejectionType, should_suggest_tip, is_repeat_purchase_eligible, compute_cooldown_hours
- `commerce/decision.py` — Steps 7.9-7.11 (commercial pause, aftercare, rejection escalation)
- `commerce/relationship.py` — derive_relationship_state, derive_commercial_pressure, check_tip_eligibility

## Gaps (What's Unwired)

### Gap 1: state.py doesn't pass C.1-C feedback values
- `resolve_commerce_state()` calls relationship derivation but doesn't pass:
  - consecutive_rejections
  - aftercare_status
  - tip_suggestions_sent/ignored
  - hours_since_last_tip
  - commercial_paused
  - post_purchase_satisfaction
  - fan_expressed_appreciation / fan_asked_how_to_support
- These all default to zero/False in CommerceConversationContext

### Gap 2: post_purchase.py doesn't record behavioral feedback
- `handle_post_purchase()` does funnel/confirmation/follow-up/delivery
- Does NOT record PURCHASE_COMPLETED event
- Does NOT update aftercare_status to PENDING
- Does NOT set commercial pause after purchase

### Gap 3: should_suggest_tip() is dead code
- Defined in feedback.py but never called from runtime
- check_tip_eligibility() in relationship.py is the actual path
- Need to wire feedback tip logic into the decision context

### Gap 4: Memory has no behavioral feedback
- profile.py doesn't track rejection/purchase/tip history
- summary doesn't include commercial outcomes
- context doesn't render aftercare/tip status

## Wiring Changes

### 1. state.py — Pass C.1-C values into pipeline request
- Query consecutive_rejections from offer history
- Query aftercare_status from post-purchase state
- Query tip history (suggestions sent, ignored, last tip time)
- Derive commercial_paused from relationship state
- Pass all values into pipeline_request

### 2. post_purchase.py — Record behavioral feedback
- After successful attribution, record PURCHASE_COMPLETED event
- Set aftercare_status = PENDING
- Don't duplicate on re-delivery (idempotency key)

### 3. pipeline.py — Wire tip eligibility
- In _apply_signal_flags, compute tip eligibility using feedback module
- Map fan_expressed_appreciation, fan_asked_how_to_support from signals

### 4. memory/context.py — Render behavioral context
- Add aftercare status, tip status, commercial pause to system prompt
- Keep it concise (token budget)

### 5. memory/profile.py — Behavioral facts
- Extract purchase preferences, rejection patterns from conversation
- Use confidence classification

## Tests Required
- Test that C.1-C fields survive state → pipeline → decision projection
- Test post-purchase records feedback event
- Test tip eligibility uses feedback module
- Test memory renders behavioral context
- Test end-to-end: purchase → aftercare → next decision suppressed
- Test end-to-end: rejection → cooldown → next decision reflects it
