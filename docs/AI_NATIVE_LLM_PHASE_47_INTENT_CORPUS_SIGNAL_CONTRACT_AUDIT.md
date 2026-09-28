# AI_NATIVE_LLM_PHASE_47 — INTENT CORPUS & SIGNAL CONTRACT AUDIT
**Date: 2026-08-31 | READ-ONLY**

## 1. Phase Deltas
- 44C: persona 19k->3.3k, context 22k->6k, num_ctx 8192, parallel DB, snapshot single. VERIFIED.
- 45: 3 LLM (signal 1, Qwen 1, scoring 1), memory lexical PG JSONB, no embeddings. VERIFIED.
- 46: CommerceSignals 20 fields, ~50% lexical, 30% semantic, 20% generative, 0 examples. VERIFIED.

## 2. Intent Inventory — VERIFIED FROM SOURCE commerce/signals.py:57
17 labels (not 20): casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other — actually 21? Count frozenset 21. Primary reported 17 but source has 21. All defined in INTENT_CATEGORIES frozenset, consumed in commerce/decision.py signals_to_context and _derive_conversational_phase.

Each influences CommerceDecisionContext has_commercial_intent, conversational_phase, negative_intent_count, not routing directly.

## 3. Ontology
- greeting: fan hello, positive vs casual_chat general. Boundary: greeting+casual overlap.
- purchase_intent: explicit buy vs content_curiosity: interest without ask.
- Hard negatives: purchase vs general interest, price vs product, hesitation vs rejection.

## 4. Message-Local vs Context-Dependent
- MESSAGE-LOCAL: greeting, price_inquiry (contains $), explicit_purchase_request (I want to buy) — lexical.
- CONTEXT-DEPENDENT: declined_recent_offer (needs prior offer), accepted_recent_offer, topic_continuity.
- STATE-DEPENDENT: repeat_purchase_intent (needs purchase history), post_purchase.
- GENERATIVE: confidence, model_uncertainty.

## 5. Flat vs Hierarchical
- Flat 21-way ambiguous (greeting vs casual_chat). Hierarchical commerce vs non-commerce: commerce {purchase, price, content, tip, negotiation} vs non-commerce {greeting, casual, relationship, uncertain} reduces ambiguity. VERIFIED decision uses _COMMERCIAL_INTENTS frozenset 8 vs _NEGATIVE 3.

## 6. Per-Intent Mechanism
- greeting/casual: DETERMINISTIC (regex hi/hello) or RAPIDFUZZ 90, not semantic.
- purchase_intent: SEMANTIC (pay vs buy 0.8) + lexical buy.
- price_inquiry: RAPIDFUZZ ($, price) + regex.
- complaint/hesitation: SEMANTIC vs lexical.
- uncertain: fallback.

## 7. 85 Examples Specification
- 21 intents *5=105 not 85, 17*5=85 but actual 21*5=105. Need 5 per intent balanced, 3 short (<10 chars), 2 long, include typo (puchase), slang (u), question+statement, indirect (how does this work?).
- Require hard negatives per confusion pair.

## 8. Hard Negatives
- purchase_intent vs content_curiosity: evidence "send me video" vs "interesting content"
- price_inquiry vs content_request: "$20?" vs "send video"
- hesitation vs rejection: "maybe later" vs "no"
- Need distinguishing evidence quoted fragment.

## 9. Representation: B multiple per intent (5 embeddings) > A single. Centroid erases variation for short messages. Hierarchical prototypes overkill for 21.

## 10. Cosine: need top-1 absolute >0.65 and margin top1-top2 >0.1 + lexical confirm, else abstain to uncertain. Thresholds BENCHMARK REQUIRED.

## 11. CommerceSignals Contract: minimal should be purchase_intent, content_interest, price_interest, explicit flags, primary_intent, confidence, fan_asks_question — others (model_uncertainty, evidence) analytics only, not decision. Local may populate purchase_intent via semantic, deterministic must override for explicit flags.

## 12. Evidence vs Decision: separate UnifiedSignals -> CommerceSignals -> decision.py, improves testing, confidence handling, commerce safety.

## 13. Creator Isolation: intent corpus global (21 labels generic), product embeddings creator-specific (fangate_products per creator), memories per-user per-creator (30 bounded, already). Do not hardcode Sunny.

## 14. Context Dependency: current message + small deterministic context (previous message for topic_continuity, product state for price, not full generation context). Not full 6k.

## 15. Fallback: UNKNOWN -> low_information (purchase_intent 0.0, primary_intent uncertain, confidence 0.0, model_uncertainty 1.0) — conservative, no offer, matches signals_to_context None -> no commercial intent.

## 16. Generative Reasoning: 20% (confidence) can be local max(lexical, semantic) or post-generation Qwen implicit handling, not need LLM#1 for evidence.

## 17. Safety vs Quality: Safety deterministic (price, product, offer, persona violations via persona_validation) — no second LLM. Quality natural_tone could be post-send telemetry, not critical path.

## 18. Dataset Specification: {intent, example_text, source, hard_negative_group, creator_scope: global, notes} JSONL, 105 lines.

## 19. Test Strategy: stratified 80/20 reference/validation per intent, hard-negative set per confusion, ambiguous set, non-commerce set.

## 20. Acceptance: intent accuracy >0.8, false purchase_intent <5%, abstention 10-20%, no unauthorized offer, latency lexical 1ms + encode 50ms + search 0.2ms.

## 21. Phase 48 Scope: MUST create commerce/unified_intelligence.py deterministic, corpus 105, RapidFuzz+encode+brute, confidence/abstention, map to CommerceSignals, unit tests. MUST NOT touch price/product/offer authority, creator isolation, one-call yet.

## 22. Risks: typo vs semantic, low data 5 per intent, model 80MB per worker, CPU 50ms contending with Qwen 500ms.

## 23. Unknowns: actual intent_examples 0, model latency on VPS not benchmarked, orjson bytes vs str not runtime tested.

## 24. Final Recommendation: Phase 48 implement unified_intelligence replacing signal LLM (1 LLM saved), keep Qwen + scoring (2 LLM), defer HNSW, defer one-call until deterministic scoring proven.

Evidence: VERIFIED FROM SOURCE for all intents, INFERRED for thresholds.
