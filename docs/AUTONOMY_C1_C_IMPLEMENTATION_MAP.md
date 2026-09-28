# Phase C.1-C — Implementation Map

## Core Changes

### 1. Rejection Taxonomy (`commerce/relationship.py`)
- Add `RejectionType` enum: HARD, SOFT, PRICE_OBJECTION, UNCERTAIN
- Add rejection classification function from signals
- Different cooldown consequences per type

### 2. Cooldown Intelligence (`commerce/relationship.py`)
- Add cooldown tracking: `consecutive_rejections`, `last_rejection_type`
- Escalation: 1 rejection → normal, 2+ consecutive → longer, 3+ → commercial pause
- Separate cooldowns: post-offer, post-rejection, post-purchase, post-tip

### 3. Post-Purchase State (`commerce/relationship.py`)
- Add `AftercareStatus` enum: PENDING, SENT, COMPLETED, SKIPPED
- Track purchase satisfaction signals
- Prevent immediate re-selling after purchase

### 4. Tip Intelligence (`commerce/relationship.py`)
- Track tip context (appreciation vs request vs question)
- Tip fatigue: repeated ignored requests → reduce frequency
- Tip and purchase remain distinct signals

### 5. Repeat Purchase Intelligence (`commerce/decision.py`)
- Contextual eligibility: not just purchase_count > 0
- Requires: time since purchase, engagement, satisfaction, product novelty

### 6. Behavioral Feedback (`commerce/feedback.py` — NEW)
- `BehavioralEvent` model for structured outcome recording
- Events: purchase_completed, rejection_received, tip_sent, offer_ignored, complaint_received
- Feedback influences: relationship_state, commercial_pressure, cooldown, memory

### 7. Decision Engine Updates (`commerce/decision.py`)
- Add context fields: `consecutive_rejections`, `rejection_type`, `aftercare_status`, `last_tip_hours_ago`, `repeat_purchase_eligible`
- New decision steps for aftercare, repeat purchase eligibility

### 8. Memory Feedback (`memory/context.py`, `memory/profile.py`)
- Behavioral facts in profile: rejection_history, tip_history, purchase_history
- Summary includes commercial outcomes
- Context renders aftercare and tip status

### 9. Pipeline Integration (`commerce/pipeline.py`)
- Map feedback events into context
- Wire aftercare status into decision

## Files Affected
- `commerce/relationship.py` — RejectionType, cooldown escalation, aftercare, tip tracking
- `commerce/decision.py` — New context fields, new decision steps
- `commerce/feedback.py` — NEW: BehavioralEvent, event recording
- `commerce/context.py` — New context fields, projection
- `commerce/pipeline.py` — Map feedback into context
- `commerce/models.py` — New enums
- `commerce/strategy.py` — AFTERCARE strategy kind
- `memory/context.py` — Aftercare/tip context in system prompt
- `memory/profile.py` — Behavioral facts
- `memory/context_assembler.py` — Render aftercare/tip status
- `tests/test_phase_c1c_feedback.py` — NEW: 40+ tests

## Test Plan
- Rejection types + consequences
- Cooldown escalation
- Post-purchase aftercare flow
- Tip intelligence + fatigue
- Repeat purchase eligibility
- Behavioral feedback recording
- Authority boundaries (kill switch, idempotency)
- Adversarial scenarios (A through L)
