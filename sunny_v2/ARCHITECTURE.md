# Sunny V2 — Architecture

## Pipeline (FUTURE)

```text
Inbound Event
    ↓
Conversation/Message Router
    ↓
Relationship Context Loader
    ↓
Memory Retrieval
    ↓
Context Assembly
    ↓
Conversation State
    ↓
Relationship Strategy
    ↓
Escalation Strategy
    ↓
Response Planning
    ↓
LLM Generation
    ↓
Response Validation
    ↓
Outbound Queue
    ↓
Delivery
```

Commerce is a **separate authoritative subsystem** beside this pipeline, not a
stage inside it. The pipeline may consult commerce (READ) and request actions
(REQUEST); commerce confirms or rejects (CONFIRMATION). See
`COMMERCE_CONTRACT.md`.

## Stage responsibilities (FUTURE)

1. **Conversation/Message Router** — classifies the inbound event, enforces
   idempotency keys, routes to V2 / commerce / human override. Never generates
   language.
2. **Relationship Context Loader** — loads persistent fan, relationship, and
   conversation state by `(creator_id, user_id)`. Fail-closed on missing scope.
3. **Memory Retrieval** — hybrid lexical + semantic retrieval over episodic and
   semantic stores with importance, recency, and relevance scoring. Not
   exact-topic-only.
4. **Context Assembly** — builds the bounded generation context with token
   budgets, prioritization, truncation, freshness, and conflict resolution.
   See `CONTEXT_ASSEMBLY.md`.
5. **Conversation State** — turn processing, state transitions, topic
   continuity, re-entry after inactivity, interruption handling, resumption.
6. **Relationship Strategy** — structured relationship state (not a single
   score) informs tone, pacing, and continuity.
7. **Escalation Strategy** — structured progression
   (RELATIONSHIP → EXPLORE → BUILD_DESIRE → QUALIFY → RECOMMEND →
   PRESENT_OFFER → AFTERCARE) as state, not as mandatory script.
8. **Response Planning** — explicit plan (intent, strategy, constraints,
   commerce references) before any token is generated.
9. **LLM Generation** — single generation call against the assembled context.
   The model is advisory for language; it is never authority for commerce,
   memory, or state.
10. **Response Validation** — deterministic rails: invented facts, prices,
    products, purchases, memory contradictions, commerce contradictions,
    repetition, discontinuity, invalid state transitions.
11. **Outbound Queue** — durable enqueue with dedup identity and correlation
    IDs. Retries are idempotent.
12. **Delivery** — transport send with at-least-once semantics, dedup lease,
    ACK/DLQ, and audit records.

## Relation to CURRENT systems

- `CURRENT` ingress (`chatbotv2/handlers.py` Telethon NewMessage + debounce +
  `enqueue_inbound`) is `DISABLED`, not reused. V2 defines its own router.
- `CURRENT` generation (`workers/llm_worker.py process_message`, one-call
  pipeline, scoring, routing) is `DISABLED`, not refactored.
- `PRESERVED` delivery (`chatbotv2/main.py` send-stream consumer), Redis
  Streams, PostgreSQL, event bus (`chatbot:events` Pub/Sub), and commerce
  services are shared infrastructure V2 will build on.
- `PRESERVED` commerce (`commerce/execution.py`, `orchestrator.py`,
  `attribution.py`, `post_purchase.py`, `reconciliation.py`,
  `integrations/dropfans/`) stays authoritative behind the commerce contract.

## Non-goals

V2 does not replace PostgreSQL, Redis Streams, the send-stream delivery loop,
provider clients, or the dashboard. It replaces the conversational decision
chain between inbound classification and outbound enqueue.
