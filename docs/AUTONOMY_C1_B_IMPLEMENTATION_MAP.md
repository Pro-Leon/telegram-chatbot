# Phase C.1-B Implementation Map — Conversational Intent & Signal Intelligence

## External Research

### KVIQBOT (FACT)
- Trained on creator's past DMs in ~20 minutes
- Qualifies intent → ladders pricing → sends unlock → takes payment → logs transaction
- Aftercare: post-sale follow-ups, cool-downs after a no, re-opens on softer angles
- Hands off to humans for anything uncertain
- Paces replies to human timing, references chat history
- Reports 17% DM→PPV conversion, $38k sold, 83s median reply

### KVIQA (FACT)
- Omnichannel CRM (Telegram, OnlyFans, WhatsApp pilot)
- KVIQ BOT is AI trained on each creator's tone and vault
- Payments via DropFans and Stripe
- Built-in chatter marketplace with attribution

### DropFans (FACT)
- Sole provider for this system
- Creator-bound API keys
- Vault, drops, posts, earnings, links, moderation
- Canonical tip/buy links via API

### Conversational Commerce Patterns (INFERENCE)
- Multi-intent classification improves context understanding
- Negative signals (rejection, hesitation) should suppress commercial pressure
- Conversation phase detection prevents premature selling
- Confidence calibration prevents over-aggressive action on ambiguous signals
- "Do nothing" must be a first-class outcome

## Current Architecture

### Signal Model (CommerceSignals)
```
purchase_intent: float [0,1]
content_interest: float [0,1]
relationship_engagement: float [0,1]
price_interest: float [0,1]
explicit_purchase_request: bool
explicit_content_request: bool
requested_price: float | None
declined_recent_offer: bool
accepted_recent_offer: bool
asks_for_free_content: bool
negative_sentiment: float [0,1]
conversation_relevance: float [0,1]
confidence: float [0,1]
evidence: list[str]
model_uncertainty: float [0,1]
```

### Decision Chain (17 steps, priority order)
1. Hard eligibility denial → NO_OFFER
1.5. Operator handoff → OPERATOR_HANDOFF
2. Creator disabled → NO_OFFER
3. No product → NO_OFFER
4. Active offer → NO_OFFER
5. Purchase cooldown → NO_OFFER
6. Offer cooldown (outcome-aware) → NO_OFFER
7. Excessive offers → NO_OFFER
8. Explicit buying intent → OFFER_PPV
8.5. Tip eligible → TIP_SUGGESTION
9. Strong buying signal → OFFER_PPV
10. Follow-up due → FOLLOW_UP
11. Moderate buying signal → SOFT_OFFER
12. Relationship ready → SOFT_OFFER
13. Relationship building → RELATIONSHIP_BUILDING
14. No offer → NO_OFFER

### CommerceAction (existing)
NO_OFFER, RELATIONSHIP_BUILDING, SOFT_OFFER, OFFER_PPV, FOLLOW_UP, DONT_OFFER, CHAT, TIP_SUGGESTION, OPERATOR_HANDOFF

### Strengths
- Deterministic decision engine with clear priority chain
- Failure-safe: LLM failure → neutral signals → no offer
- Creator isolation enforced
- AUTONOMY_ENABLED kill switch
- AutomationService as sole write authority

### Weaknesses
- Single-intent model: one purchase_intent float doesn't capture multi-intent messages
- No conversational phase concept in decision engine
- No negative intent detection beyond declined_recent_offer
- No topic continuity/momentum signal
- No reciprocity awareness (over-questioning)
- CommerceSignals extraction prompt doesn't ask for multi-intent or negative signals
- Decision engine doesn't use signal confidence
- No explicit DO_NOT_SELL / CHAT-first path

## Proposed Changes

### 1. Extend CommerceSignals
Add fields:
- `primary_intent`: str — primary intent category
- `intent_tags`: list[str] — all detected intents (multi-intent)
- `negative_intent_tags`: list[str] — negative/hesitant signals detected
- `fan_asks_question`: bool — fan is asking a question (reciprocity)
- `topic_continuity`: str | None — current conversation topic for continuity

### 2. Extend CommerceDecisionContext
Add fields:
- `conversational_phase`: str — derived from relationship + signals
- `negative_intent_count`: int — number of negative signals
- `signal_confidence`: float | None — confidence from signal extraction
- `fan_asks_question`: bool — reciprocity signal
- `offer_fatigue_score`: float — derived from recent offers / attempts

### 3. Intent Taxonomy
Minimal set (not a giant enum):
- CASUAL_CHAT, GREETING, RELATIONSHIP_BUILDING, PERSONAL_DISCLOSURE
- CONTENT_CURIOSITY, CONTENT_REQUEST, PRICE_INQUIRY
- PURCHASE_INTENT, REPEAT_PURCHASE_INTENT, POST_PURCHASE, AFTERCARE
- TIP_INTEREST, COMPLAINT, CUSTOM_REQUEST
- NEGOTIATION, HESITATION, REJECTION
- UNCERTAIN

### 4. Conversational Phase (derived, not persisted)
- OPENING: new fan, first messages
- RAPPORT: relationship building, no commercial signals
- DISCOVERY: fan exploring, casual questions
- ENGAGED_CHAT: active back-and-forth
- CONTENT_CURIOSITY: fan asking about content
- COMMERCIAL_INTEREST: buying signals present
- POST_PURCHASE: recent purchase
- AFTERCARE: post-purchase relationship
- COOLDOWN: after rejection, reduce pressure

### 5. Decision Engine Enhancements
- Add conversational phase to context
- Phase-aware: OPENING/RAPPORT → suppress commercial, prefer CHAT
- Negative signals increase cooldown pressure
- Ambiguity → prefer CHAT over SELL
- Offer fatigue → reduce commercial pressure

### 6. Signal Extraction Prompt
Extend to ask for:
- Primary intent
- All detected intents
- Negative/hesitant signals
- Whether fan is asking questions
- Current topic for continuity

## Test Scenarios
30+ scenario tests covering:
- Greeting → CHAT
- Casual conversation → CHAT
- Relationship disclosure → CHAT
- Content curiosity → CONTENT_CURIOSITY
- Explicit buying → OFFER_PPV
- Price question in context → high relevance
- Ambiguous price question → UNCERTAIN
- Rejection → pressure decrease
- Price objection → no push
- Ignored offer → no second offer
- Returning purchaser → elevated relevance
- Compliment → not sales
- Appreciation → relationship
- Tip interest → TIP_SUGGESTION
- Complaint → OPERATOR_HANDOFF
- Multi-intent → multiple signals
- Low confidence → CHAT
- Cooldown → suppressed
- Offer fatigue → reduced pressure
- Kill switch → no action
- Creator isolation → scoped
