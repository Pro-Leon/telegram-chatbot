# AUTONOMY C.1 — IMPLEMENTATION MAP

**Date:** August 26, 2026
**Status:** Pre-implementation audit complete

---

## EXTERNAL RESEARCH FINDINGS

### KVIQBOT/KVIQA (FACT — from official sources)

| Capability | Detail | Source |
|-----------|--------|--------|
| Persona training | Trained from creator's past DMs in ~20 minutes. Tone, emojis, hard-nos. | kviqbot.com |
| Conversation history | References chat history, same handle, same chat history | kviqbot.com |
| Buying signals | Explicit feature tier — "basic" (Starter) and "advanced" (Growth) | kviqbot.com/pricing |
| Intent qualification | "Qualifies intent, ladders pricing, sends the unlock, takes payment" | kviqbot.com |
| Human handoff | Auto on hard signals (IRL meet, refund, complaint). Manual toggle per chat. | kviqa.com/blog |
| Aftercare | "Post-sale follow-ups, cool-downs after a no, re-opens on softer angles" | kviqbot.com |
| Rejection handling | "Cool-downs after a no" — graceful acceptance, no pushback, softer re-opening | kviqbot.com |
| Ghost recovery | Day-2 cold-touch script. Soft opener ("did I scare u off?"), fresh angle. | kviqbot.com/conversations |
| Upsells | Recognizes spending patterns, anchors high, stacks PPVs, no discounts | kviqbot.com |
| Tips | Listed as play type. Fan-led, not aggressively solicited. | kviqbot.com/conversations |
| Anti-repetition | Trained on creator's actual saved replies. Indistinguishable for 4-6 turns. | kviqa.com/blog |
| Pacing | "Paces replies to human timing" | kviqbot.com |
| Metrics | 17% DM→PPV conversion, $38k sold, 1900 PPVs, 52k messages, 83s median reply | kviqbot.com/results |

### DropFans API (FACT — from official docs at dropfans.io/developers)

| Capability | Detail |
|-----------|--------|
| Base URL | `https://www.dropfans.io/api/external/` |
| Auth | Bearer token, one per creator, full access |
| Rate limits | 60/min personal, 300/min app. Headers on every response. Retry-After on 429. |
| Tips | Canonical URLs via `GET /links`. Web: `?tip=<usd>`. Telegram: `pt_<cents>_<username>`. |
| Drops | `POST /drops` (1-10 vault items, $5-$750). Returns `buyUrl`. |
| Vault | CRUD + video upload + tags + folders. Moderation lifecycle. |
| Posts | CRUD with moderation + scheduled publishing. 5/day cap. |
| Earnings | Stats, charts, transactions (net/gross, by period). |
| Balance | pending/available/processing/paidOut in USD dollars. |
| Webhooks | NOT SHIPPED YET. Planned: `drop.paid`, `tip.received`. Currently polling-only. |
| Sandbox | NONE. All testing against production. |
| Unit inconsistency | Tips: dollars on web, cents in Telegram. Earnings: cents. Balance: dollars. |

### Conversational Commerce Patterns (FACT/INFERENCE from industry research)

| Pattern | Finding | Source |
|---------|---------|--------|
| 3-tier memory | Working (10 msgs) → Session (summaries) → Long-term (facts) | DEV.to 2026 |
| Fact extraction | Atomic facts `{subject, predicate, object, confidence, timestamp}` outperform summaries | Mem0 2026 |
| Intent classification | Multi-label, 10+ sales intents with fallback | FlowX.AI, Nurix AI |
| Human handoff | 3 triggers: explicit request, sentiment drop, low confidence. 10-15% sustainable rate. | BuiltABot, Zylos |
| Idempotency | All write ops need idempotency keys. Agent retry logic is structurally broken without them. | Tian Pan 2026 |
| Post-purchase | Most critical touchpoint. Proactive notifications reduce WISMO 70-95%. | Parcel Perform |
| Ghost recovery | Day-2 is critical window. Soft openers outperform repeat pitches. | UserGems 2026 |
| Guardrails | 4 layers: permissions, output validators, circuit breakers, human-in-loop | gheWARE 2026 |
| Pacing | Moderate delays enhance naturalness. Fast for transactional, natural for conversational. | Springer 2025 |

---

## EXISTING ARCHITECTURE ASSESSMENT

### What Already Works

| Component | Status | Quality |
|-----------|--------|---------|
| Commerce decision engine | Wired, tested, 14-step priority chain | HIGH |
| Deterministic product selection | Wired, history exclusion | HIGH |
| Commerce signals extraction | Wired via DeepSeek adapter | MEDIUM |
| Strategy builder | Wired, 9 strategy kinds | HIGH |
| PPV execution | Wired, idempotent offer creation | HIGH |
| Post-purchase lifecycle | Wired, funnel + confirmation + follow-up | MEDIUM |
| Automation service | Wired, kill switch + idempotency | HIGH |
| DropFans client | Wired, rate-limit handling | HIGH |
| Persona system | Wired, DB-backed, user-specific | MEDIUM |
| Profile extraction | Wired, LLM-based, JSONB storage | MEDIUM |
| Conversation summarization | Wired, rolling append | LOW |
| Semantic retrieval | Wired, trigger-word-only | LOW |
| Segment system | Wired, 30+ fields, SQL compilation | HIGH |
| LLM tools | Wired, 7 tools, bounded execution | HIGH |

### What Is Weak

| Issue | Severity | Location |
|-------|----------|----------|
| Profile extraction runs AFTER generation | HIGH | `llm_worker.py:post_process` |
| Summary chain is append-only, old summaries orphaned | HIGH | `summarizer.py` |
| Retrieval is trigger-word-only (11 phrases) | HIGH | `context.py:should_retrieve` |
| Token budgets not enforced for summary/commerce | MEDIUM | `context.py:build_context` |
| `relationship_state` and `commercial_pressure` fields on CommerceDecisionContext are dead | HIGH | `decision.py:103-104` |
| `to_decision_context()` does not project Phase C fields | HIGH | `context.py:262-282` |
| `signals_to_context()` overwrites caller's buying_intent_score | MEDIUM | `signals.py:220-236` |
| `build_strategy()` called twice (once discarded) | MEDIUM | `pipeline.py:430` |
| Profile list fields rendered as Python repr | LOW | `context.py:format_profile` |
| `preferences` field in schema but not in extraction prompt | LOW | `profile.py` |
| User lock TTL 60s may expire during slow processing | LOW | `llm_worker.py` |

### What Is Missing

| Gap | Severity | Notes |
|-----|----------|-------|
| Relationship module is ORPHANED | CRITICAL | `relationship.py` functions are never called from pipeline |
| Upsell intelligence | HIGH | No cross-sell, no "users who bought X also bought Y" |
| Re-engagement / ghost recovery | HIGH | No outbound re-engagement policy |
| Aftercare sequencing | HIGH | Only 24h follow-up, no graduated aftercare |
| Intent model beyond commerce signals | HIGH | No CASUAL_CHAT, GREETING, COMPLAINT, etc. |
| Conversational phase tracking | MEDIUM | No OPENING, RAPPORT, DISCOVERY, etc. |
| Response quality guardrails | MEDIUM | No anti-repetition checking |
| Decision observability | MEDIUM | No "why did the bot say this?" metadata |
| Effectiveness metrics | MEDIUM | No conversion tracking, no revenue per fan |
| Rejection recovery intelligence | MEDIUM | No graduated cooldown, no softer re-opening |
| Tip contextual triggers | MEDIUM | Tip eligibility defined but not wired |
| Product relevance scoring | LOW | No metadata-based matching |

### What Should NOT Be Changed

| Component | Reason |
|-----------|--------|
| Commerce decision engine core | Tested, stable, 14-step priority chain |
| Deterministic product selection | Provider-neutral, history-exclusion works |
| AutomationService write authority | Kill switch + idempotency enforced |
| DropFans client + rate-limit handling | Structured error classification, Retry-After |
| Creator isolation at DB layer | Every query creator-scoped |
| LLM tools framework | Bounded, audited, timeout-enforced |
| Redis Streams architecture | Consumer groups, pending, DLQ |
| Segment SQL compiler | Parameterized, injection-safe |

### Test Coverage Gaps

| Area | Gap |
|------|-----|
| Relationship derivation | 32 tests exist — GOOD |
| Automation service | 23 tests exist — GOOD |
| Commerce decision | Good coverage |
| Memory/context assembler | Some coverage but no integration tests |
| Profile extraction | No tests for merge logic, stale facts |
| Summarizer | No tests for chain degradation |
| Retrieval | No tests for trigger-word coverage |
| Post-purchase aftercare | Minimal tests |
| Intent classification | No tests |
| Adversarial scenarios | None |
| Ghost recovery | None |
| Upsell logic | None |

### Observability Gaps

| Gap | Impact |
|-----|--------|
| No structured decision metadata | Cannot answer "why did the bot say this?" |
| No "why not sell" metadata | Cannot diagnose missed opportunities |
| No "why this product" metadata | Cannot verify product selection reasoning |
| No conversion funnel tracking | Cannot measure DM→PPV→Purchase→Repeat |
| No rejection/complaint tracking | Cannot measure relationship health |
| No ghost recovery metrics | Cannot measure re-engagement success |

### Failure Modes

| Mode | Current Handling | Gap |
|------|-----------------|-----|
| DropFans timeout | UNKNOWN status, retry | OK |
| DropFans rate limit | Retry-After respected | OK |
| DropFans auth error | Permanent failure, no retry | OK |
| LLM failure | Falls back to no-commerce path | OK |
| Profile extraction failure | Silent,不影响 generation | OK but stale profiles |
| Summarization failure | Silent,不影响 generation | OK but degraded context |
| Duplicate operations | Idempotency keys on offers | OK for offers, not for other ops |
| Kill switch mid-operation | Checked at persist + execute | OK |
| Bot replying to itself | Not protected | GAP |

---

## IMPLEMENTATION PLAN

### Phase C.1-A: Conversation/Memory Hardening

**Goal:** Layered memory with fact extraction, summary chaining, and retrieval improvement.

| # | Change | Files | Type |
|---|--------|-------|------|
| A1 | Fix summary chain: replace append with UPDATE (latest summary covers full history) | `memory/summarizer.py` | BUG FIX |
| A2 | Move profile extraction BEFORE generation (extract from current message, merge, then build context) | `workers/llm_worker.py` | REORDER |
| A3 | Add `preferences` to extraction prompt | `memory/profile.py` | EXTEND |
| A4 | Fix profile rendering: lists as clean text, not Python repr | `memory/context.py` | FIX |
| A5 | Add stale fact pruning: new facts supersede old via `supersedes` field | `memory/profile.py` | EXTEND |
| A6 | Enforce token budgets for summary and commerce sections | `memory/context.py` | HARDEN |
| A7 | Add conversation phase detection (greeting/rapport/discovery/engaged/closing) to context assembler | `memory/context_assembler.py` | NEW |
| A8 | Expand retrieval: semantic search on every message, not just trigger words (with relevance threshold) | `memory/retrieval.py` | EXTEND |

### Phase C.1-B: Intent and Conversational-Phase Hardening

**Goal:** Multi-label intent classification, conversation phase tracking.

| # | Change | Files | Type |
|---|--------|-------|------|
| B1 | Define intent taxonomy: CASUAL_CHAT, GREETING, RELATIONSHIP_BUILDING, CURIOSITY, CONTENT_INTEREST, PRICE_INQUIRY, PURCHASE_INTENT, REPEAT_PURCHASE, TIP_INTEREST, POST_PURCHASE, COMPLAINT, CUSTOM_REQUEST, NEGOTIATION, REJECTION, UNCERTAINTY, OPERATOR_REQUIRED | `commerce/signals.py` | EXTEND |
| B2 | Add multi-label intent extraction to DeepSeek signal adapter | `commerce/deepseek.py` | EXTEND |
| B3 | Wire `relationship_state` and `commercial_pressure` into `to_decision_context()` | `commerce/context.py` | WIRE |
| B4 | Wire `relationship.py` functions into the pipeline (state resolution → decision context) | `commerce/state.py`, `commerce/pipeline.py` | WIRE |
| B5 | Add conversation phase to `LLMContext` and render it | `memory/context_assembler.py` | EXTEND |

### Phase C.1-C: Commercial Pressure and Rejection Intelligence

**Goal:** Graduated cooldown, rejection recovery, softer re-openings.

| # | Change | Files | Type |
|---|--------|-------|------|
| C1 | Track rejection count and recency in relationship derivation | `commerce/relationship.py` | EXTEND |
| C2 | Add graduated cooldown: rejection → 24h pressure=none → 48h rapport → 72h soft re-open | `commerce/relationship.py` | EXTEND |
| C3 | Add rejection-aware commercial pressure: declined offers reduce pressure for 48h | `commerce/relationship.py` | EXTEND |
| C4 | Wire rejection tracking into decision context | `commerce/context.py`, `commerce/state.py` | WIRE |
| C5 | Add "softer re-opening" logic: after cooldown, next commercial action is RELATIONSHIP_BUILDING not OFFER | `commerce/decision.py` | EXTEND |

### Phase C.1-D: Product Relevance / Upsell Intelligence

**Goal:** Context-aware product selection, cross-sell logic.

| # | Change | Files | Type |
|---|--------|-------|------|
| D1 | Add product metadata to fangate_products (content_type, tags, price_tier) | `db/schema.sql` | SCHEMA |
| D2 | Add product relevance scoring: match product metadata against conversation intent + fan preferences | `commerce/product_selection.py` | EXTEND |
| D3 | Add upsell eligibility: purchased products → next-tier recommendation | `commerce/product_selection.py` | NEW |
| D4 | Add price ladder awareness: LOW ($5-15) → MEDIUM ($15-50) → PREMIUM ($50-750) | `commerce/product_selection.py` | NEW |
| D5 | Prevent duplicate selling: already-purchased products excluded from offers | Already works via `has_purchased_product` | VERIFY |

### Phase C.1-E: Tip Intelligence

**Goal:** Contextual tip suggestions, rate-limited, rejection-aware.

| # | Change | Files | Type |
|---|--------|-------|------|
| E1 | Wire `check_tip_eligibility()` into pipeline state resolution | `commerce/state.py` | WIRE |
| E2 | Pass tip eligibility through `to_decision_context()` | `commerce/context.py` | WIRE |
| E3 | Add contextual tip triggers: post-purchase appreciation, explicit support question, long rapport session | `commerce/relationship.py` | EXTEND |
| E4 | Add tip cooldown tracking (72h minimum between suggestions) | `commerce/relationship.py` | ALREADY EXISTS |
| E5 | Add tip rejection tracking: if fan ignores/rejects tip suggestion, increase cooldown | `commerce/relationship.py` | NEW |

### Phase C.1-F: Aftercare / Re-engagement

**Goal:** Graduated aftercare, ghost recovery, non-spammy re-engagement.

| # | Change | Files | Type |
|---|--------|-------|------|
| F1 | Extend post-purchase aftercare: confirmation → 1h tip → 24h check-in → 72h soft re-engage | `commerce/post_purchase.py` | EXTEND |
| F2 | Add ghost recovery policy: 7d → new content notify, 14d → value-add, 30d → check-in, 60d → incentive | `commerce/relationship.py` | NEW |
| F3 | Add re-engagement eligibility check: last inbound, last outbound, last commercial action, creator settings | `commerce/relationship.py` | NEW |
| F4 | Wire ghost recovery into scheduled message system (existing infrastructure) | `workers/llm_worker.py` | WIRE |
| F5 | Add conversation abandonment detection: 5+ messages without response from bot | `memory/context_assembler.py` | NEW |

### Phase C.1-G: Provider Failure/Reconciliation Hardening

**Goal:** Robust DropFans failure handling, deduplication, reconciliation.

| # | Change | Files | Type |
|---|--------|-------|------|
| G1 | Fix `find_dropfans_product` hash inconsistency (use sha256 consistently) | `db/dropfans.py` | BUG FIX |
| G2 | Fix `record_dropfans_sale` dedup: use sale_id not composite key | `db/dropfans.py` | BUG FIX |
| G3 | Fix `post_purchase.py` media ID: use deterministic hash, not `abs(hash())` | `commerce/post_purchase.py` | BUG FIX |
| G4 | Fix unreachable code in `sum_recorded_sales_cents` | `db/dropfans.py` | DEAD CODE |
| G5 | Add rate-limit header tracking and observability | `integrations/dropfans/client.py` | EXTEND |
| G6 | Add reconciliation for UNKNOWN provider outcomes | `commerce/reconciliation.py` | EXTEND |

### Phase C.1-H: Observability and Metrics

**Goal:** Every decision is explainable. Conversion funnel is measurable.

| # | Change | Files | Type |
|---|--------|-------|------|
| H1 | Add decision metadata to every commerce decision (why sell / why not sell / why this product) | `commerce/decision.py` | EXTEND |
| H2 | Add structured audit log for autonomous conversation decisions | `db/postgres.py` | NEW |
| H3 | Add conversion funnel metrics: inbound → conversation → offer → purchase → repeat | `commerce/dao.py` | EXTEND |
| H4 | Add rejection/complaint tracking | `commerce/dao.py` | EXTEND |
| H5 | Add ghost recovery success metrics | `commerce/dao.py` | EXTEND |
| H6 | Add "why did the bot not sell" metadata to every NO_OFFER decision | `commerce/decision.py` | EXTEND |

### Phase C.1-I: Adversarial Tests

**Goal:** Scenario tests covering all acceptance criteria.

| # | Test | Type |
|---|------|------|
| I1 | Scenario A-O from acceptance criteria | SCENARIO |
| I2 | Bot replying to itself protection | ADVERSARIAL |
| I3 | LLM manipulation attempts | ADVERSARIAL |
| I4 | Kill switch bypass attempts | ADVERSARIAL |
| I5 | Creator isolation cross-creator | ADVERSARIAL |
| I6 | Duplicate operation attempts | ADVERSARIAL |
| I7 | Fangate contamination search | FORENSIC |

### Phase C.1-J: Full Forensic Audit

**Goal:** Verify all invariants hold. Produce final report.

| # | Audit | Method |
|---|-------|--------|
| J1 | No direct LLM→provider access | Grep |
| J2 | No autonomous Fangate paths | Grep |
| J3 | Kill switch cannot be bypassed | Code trace |
| J4 | Creator isolation intact | DB query audit |
| J5 | All commercial decisions observable | Metadata audit |
| J6 | No fabricated prices/links | Code trace |
| J7 | No dead code (unless documented) | Import analysis |
| J8 | All state transitions valid | Enum audit |

---

## DECISION: WHAT TO BUILD FIRST

Based on severity and dependency:

1. **BUG FIXES first** (G1-G4, A1-A2) — critical data integrity
2. **WIRE ORPHANED CODE** (B3-B4, E1-E2) — Phase C functions are implemented but unreachable
3. **MEMORY HARDENING** (A3-A8) — conversation quality foundation
4. **INTENT MODEL** (B1-B2) — prerequisite for sales intelligence
5. **REJECTION/COOLDOWN** (C1-C5) — relationship preservation
6. **UPSELL/PRODUCT RELEVANCE** (D1-D5) — revenue intelligence
7. **TIP/AFTERCARE** (E3-E5, F1-F5) — relationship depth
8. **OBSERVABILITY** (H1-H6) — measure everything
9. **ADVERSARIAL TESTS** (I1-I7) — prove correctness
10. **FORENSIC AUDIT** (J1-J8) — final verification
