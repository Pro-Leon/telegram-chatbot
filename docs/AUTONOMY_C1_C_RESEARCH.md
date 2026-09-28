# Phase C.1-C — Research Findings

## Date: 2026-08-26

---

## 1. KVIQBOT / ChatterIQ Patterns

**FACT:**
- ChatterIQ is an AI training platform for OnlyFans chat operators with realistic fan simulations
- OpenFlow trains AI on creator's past conversations, captions, vault media for voice matching
- Bambi Agency: DM-based revenue (PPV, tips, customs) = 60-70% of total income
- Fans who receive DM response within 5 minutes are 40% more likely to purchase PPV
- Hybrid approach (AI triage + human chat) cuts first-response from 23min to 90sec

**RESEARCH FINDING:**
- Welcome message within 5 min of subscribing = higher tip probability within 48h
- Progressive escalation: free conversation → small tip ($5-10) → PPV → premium custom
- Tip volume correlates with chatting quality
- 3 tip contexts: post tip, DM tip, live tip — each needs different approach
- Tiered pricing with anchoring effect: audio $10, photo $30, video $80 → most choose photo

**INFERENCE:**
- Post-purchase conversation quality determines repeat purchase probability
- Rejection handling is a key differentiator between top 10% and rest
- Cooldowns after rejection are standard practice (cool-downs after "no", ghost recovery)
- Tips are unlimited in frequency but must be contextual, not beggary

**DESIGN DECISION:**
- Our system must distinguish rejection types (hard/soft/price) for different cooldowns
- Aftercare is NOT upsell — it's relationship maintenance
- Tips emerge from appreciation, not from scripted requests

---

## 2. Conversational Sales / Retention Patterns

**FACT:**
- 89% of customers more likely to return after positive post-purchase experience
- 93% of shoppers consider post-purchase experience important
- Post-purchase day 3: delivery satisfaction check → day 14: cross-sell
- Lapse detection at 2x purchase cycle → re-engagement
- Shoppers exposed to AI chat post-purchase spend 25% more

**RESEARCH FINDING:**
- Post-purchase automation fails because reality is rarely binary (not just "shipped/delivered")
- Data fragmentation is root cause of failed automation
- Chatbot retention playbook: satisfaction check → review request → cross-sell → re-engagement
- Complaints must immediately stop commercial automation

**INFERENCE:**
- Our bot should NOT immediately sell after purchase
- Delivery confirmation + natural aftercare → eventually repeat opportunity
- Complaints are high-priority safety boundaries
- Repeated negative outcomes must increase restraint

**DESIGN DECISION:**
- Post-purchase: verify → attribute → deliver → aftercare → eventual relevance
- Complaints: suppress all commercial → handoff available
- Cooldown escalation: 1 rejection → normal, 2 consecutive → longer, 3+ → pause

---

## 3. Tip / Donation Patterns

**FACT:**
- Tips can account for up to 30% of creator revenue
- DM tipping is main playground for chatters
- $500/day/fan limit
- Same fan can send multiple tips per day if engaged
- Tip-to-PPV conversion: fans who tip first are more likely to buy PPV

**RESEARCH FINDING:**
- Tip menu: 5-7 options, $5-$100 range, anchored pricing
- Fan who sends $5 today may send $50 in 2 weeks if nurtured
- Generic mass DMs kill tip potential
- Not having visible tip menu → fan doesn't know what to buy → buys nothing
- Overloading menu (>10 options) creates confusion

**INFERENCE:**
- Tips must arise naturally from appreciation, not be demanded
- Successful tip should NOT immediately trigger another request
- Repeated tip requests must increase restraint
- Tips and purchases are distinct behavioral signals

**DESIGN DECISION:**
- Track tip context (appreciation vs request vs question)
- Tip cooldown separate from offer cooldown
- Tip fatigue: repeated ignored requests → reduce suggestion frequency
- Tip and purchase remain distinct in decision engine

---

## 4. Objection Handling / Cooldown Patterns

**FACT:**
- Top creators use cool-downs after "no"
- Ghost recovery: re-engagement after periods of silence
- Progressive escalation is standard: small commitment → larger commitment
- Over-selling is the #1 reason fans unsubscribe

**RESEARCH FINDING:**
- Hard rejection ("no", "stop asking") → immediate commercial pause
- Soft rejection ("maybe later") → moderate cooldown, normal conversation OK
- Price objection → don't push same price, don't auto-discount
- Uncertainty → don't interpret as buying intent
- Escalating penalties for repeated rejections are common

**INFERENCE:**
- Single rejection ≠ relationship damage
- Repeated rejection = commercial pause, not relationship termination
- Price objections should not trigger fabricated discounts
- Fan comfort must be preserved after rejection

**DESIGN DECISION:**
- Rejection taxonomy: HARD, SOFT, PRICE_OBJECTION, UNCERTAIN
- Each type produces different consequences
- Escalation ceiling: 3+ rejections → commercial pause (not permanent)
- Cooldowns are policy-driven, not hardcoded

---

## 5. Post-Purchase Experience Patterns

**RESEARCH FINDING:**
- Post-purchase phase is emotional, not transactional
- Expectation shifts from discovery to fulfillment
- Any friction during fulfillment is highly damaging
- Post-purchase complaints are highest-priority safety boundaries
- Delivery confirmation reduces anxiety
- Satisfaction check at day 3, cross-sell at day 14

**INFERENCE:**
- Our system must verify purchase → attribute → deliver → confirm
- Aftercare is "make fan feel looked after", not "sell again"
- Complaint about purchased content → all commercial automation stops
- Repeat purchase eligibility requires multiple factors, not just purchase_count > 0

**DESIGN DECISION:**
- Post-purchase flow: verify → attribute → deliver → aftercare → eventual relevance
- Aftercare: acknowledge → confirm delivery → natural appreciation → continue conversation
- Complaint: suppress commercial → operator handoff available
- Repeat purchase: contextual eligibility, not automatic

---

## 6. Behavioral Feedback Loops

**RESEARCH FINDING:**
- Best systems use structured feedback: what happened → what changed → what to do next
- Every commercial event should have a consequence
- Memory should contain useful facts, not raw transaction data
- Transaction truth stays in provider DB; memory has conversational interpretation
- Confidence and recency matter for behavioral memory

**INFERENCE:**
- Purchase → positive feedback → aftercare state → eventual repeat relevance
- Rejection → commercial pressure decreases → longer cooldown
- Tip → positive appreciation → don't immediately ask again
- Complaint → stop commercial → handoff available
- Ignored offer → reduce pressure → increase spacing

**DESIGN DECISION:**
- BehavioralFeedback model for structured event recording
- Feedback influences: relationship_state, commercial_pressure, cooldown, memory
- No duplicate transaction data in memory
- Transaction truth stays in DropFans/application DB

---

## 7. Provider Hardening Patterns

**RESEARCH FINDING:**
- Idempotency is critical for post-purchase flows
- Timeout-after-success is a real scenario
- Rate limiting must reduce activity, not cause retry storms
- Only retry safe (idempotent) operations
- Kill switch must win over every commercial signal

**INFERENCE:**
- Every autonomous provider write must check AUTONOMY_ENABLED
- Failure classification enables appropriate retry behavior
- Non-idempotent writes need idempotency keys
- Duplicate fulfillment must be impossible through normal retries

**DESIGN DECISION:**
- Existing classify_provider_error + is_retryable_error handles most cases
- Add idempotency key verification for post-purchase actions
- Kill switch check before every autonomous write
- DropFans remains sole provider (no Fangate fallback)
