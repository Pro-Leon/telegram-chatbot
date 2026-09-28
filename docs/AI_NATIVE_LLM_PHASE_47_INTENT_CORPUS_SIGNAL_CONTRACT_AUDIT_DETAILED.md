# Phase 47 Detailed Audit — Intent Ontology & Signal Contract
**VERIFIED FROM SOURCE**

## Enumerated 21 Intents (commerce/signals.py:57 frozenset, not 17)
casual_chat,greeting,relationship_building,personal_disclosure,content_curiosity,content_request,price_inquiry,purchase_intent,repeat_purchase_intent,post_purchase,aftercare,tip_interest,complaint,custom_request,negotiation,hesitation,rejection,uncertain,reassurance,appreciation,operator_request,other — 21. Primary reported 17 is outdated; actual 21. Consumers: signals_to_context (purchase_intent->buying_intent_score, price_interest->user_asked_about_price), _derive_conversational_phase (greeting->opening, purchase_intent->commercial_interest), commerce/decision, strategy.

## Ontology per Intent
- greeting: hello/hi, no commerce, boundary casual_chat (general). Needs hi/hello evidence.
- purchase_intent: want to buy, how to pay is NOT explicit per prompt, needs literal.
- price_inquiry: contains $ or price word, vs content_request send video.
- Hard negatives required per confusion matrix.

## Message-Local vs Context
- MESSAGE-LOCAL: greeting, price_inquiry ($), explicit_purchase_request (I want to buy) — lexical.
- CONTEXT-DEPENDENT: declined_recent_offer (needs prior offer), topic_continuity (needs previous message).
- STATE-DEPENDENT: repeat_purchase_intent (needs fangate_transactions), post_purchase.
- GENERATIVE: confidence.

## Flat vs Hierarchical
- Flat 21-way high ambiguity greeting vs casual. Hierarchical commerce (8) vs non-commerce (13) vs negative (3) matches _COMMERCIAL_INTENTS vs _NEGATIVE_INTENTS, reduces ambiguity.

## Per-Intent Mechanism
- greeting: DETERMINISTIC regex hi|hello
- purchase_intent: SEMANTIC (pay vs purchase)
- price: RAPIDFUZZ price/$ + regex
- complaint: SEMANTIC
- uncertain: fallback

## 85 Examples: Need 105 (21*5), balanced 3 short 2 long, typo, slang, question+statement, indirect, hard negatives, negative counterparts.

## Representation
- B multiple per intent (5) > A single. Centroid erases short message variation. D multiple prototypes overkill.

## Cosine
- Need top1 >0.65 and margin >0.1 + lexical, else abstain. Thresholds BENCHMARK REQUIRED via validation set.

## CommerceSignals Minimal Contract
- Required for decision: purchase_intent, content_interest, explicit flags, primary_intent, confidence, fan_asks_question. Others (evidence, model_uncertainty) analytics only. Local may populate purchase_intent, deterministic override for explicit.

## Evidence vs Decision
- Separate UnifiedSignals -> CommerceSignals -> decision.py improves explainability, commerce safety.

## Creator Isolation
- Intent corpus global, product/memories creator-specific, already isolated via fan_knowledge_by_creator.

## Context Dependency
- Current message + previous message for topic_continuity, product state for price, not full 6k.

## Fallback
- UNKNOWN -> low_information (0.0, uncertain, confidence 0) -> no offer, conservative.

## Generative Reasoning
- 20% confidence can be local max(lexical, semantic) or post-generation Qwen, not need LLM#1.

## Safety vs Quality
- Safety deterministic (price, product, offer), quality natural_tone could be post-send telemetry, not critical path.

## Dataset Spec
- {intent, example_text, source, hard_negative_group, creator_scope}

## Test Strategy
- 80/20 split, hard-negative, ambiguous, non-commerce sets.

## Acceptance
- Intent accuracy >0.8, false purchase <5%, no unauthorized offer.

## Phase 48
- MUST: unified_intelligence, corpus 105, RapidFuzz+encode+brute, confidence, map to CommerceSignals
- MUST NOT: price/product authority, one-call yet
- DEFER: HNSW, one-call
- REQUIRES BENCHMARK: encode latency

Evidence: VERIFIED FROM SOURCE for intents, INFERRED for thresholds.
