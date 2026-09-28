# Sunny V2 — Event Model (FUTURE)

Event-driven communication design. No handlers were created in cutover.

## Categories

```text
conversation.received
conversation.started
conversation.updated
message.received
message.generated
message.sent
relationship.updated
memory.created
memory.updated
memory.invalidated
strategy.changed
commerce.context.updated
commerce.action.requested
commerce.action.confirmed
purchase.confirmed
conversation.failed
```

## Relation to CURRENT events (PRESERVED contract)

Phase 1 realtime events (`message.created`, `message.sent`,
`message.send_failed`, `ai.generation_started`, `ai.generation_completed`,
`ai.generation_failed`, `suggestion.created`, `operator_queue.updated`) remain
the stable interface between workers, Redis Pub/Sub (`chatbot:events`), the
dashboard WebSocket layer, and browser clients. Phase 2+ must preserve their
schema and semantics, including:

- `ai.generation_completed` only after `enqueue_send()` succeeds for
  auto-approved generations.
- Stable `generation_id` across lifecycle events; unique `event_id` per event.
- Best-effort publication that never breaks business logic.
- Workers communicate only through the event bus (no `ws_manager` imports).

New V2 events above extend this model; they do not rename or reinterpret
existing events without a versioned migration.

## Idempotency requirements

- Every event carries `event_id` (dedupe), correlation IDs
  (`generation_id`, `conversation_id`, fan scope), and producer identity.
- Consumers are idempotent: redelivery yields the same state.
- `commerce.action.requested` carries the idempotency key commerce enforces;
  retries reuse it.
- No business state may depend on delivery order or exactly-once transport.
