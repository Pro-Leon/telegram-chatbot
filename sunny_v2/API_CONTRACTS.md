# Sunny V2 — API Contracts (FUTURE)

Interfaces between subsystems. No APIs were created in cutover.

## Inbound system → V2

- Accepts: fan-scoped inbound event + idempotency key + correlation IDs.
- Returns: accept / duplicate / reject with stable reason codes.
- Guarantees: unknown scope fails closed; malformed payloads never create
  state.

## V2 → Memory subsystem

- `retrieve(scope, query, budget)` → ranked episodes + facts with scores and
  provenance. No exact-topic-only matching.
- `extract(turn)` → candidate facts/episodes with provenance, validated
  before persistence.
- `consolidate(scope)` → supersession chains, contradiction resolutions.

## V2 → Conversation subsystem

- `load_state(scope)` → conversation + relationship + escalation snapshots.
- `apply_turn(state, signals)` → next state or explicit failure; invalid
  transitions rejected with codes.

## V2 → Commerce subsystem

Request/response semantics (see `COMMERCE_CONTRACT.md`):

```text
V2: "What commerce actions are currently available?"  (READ)
Commerce: authoritative list + eligibility + sealed states.

V2: CommerceActionRequest(idempotency_key, scope, action)  (REQUEST)
Commerce: CommerceActionResult(confirmed | rejected + stable code)
```

V2 never manufactures the result; generated text may only quote confirmed
payloads.

## V2 → Outbound delivery

- `enqueue_turn(plan, validation_verdict)` → durable queue entry with dedup
  identity. Delivery reuses the `PRESERVED` send-stream semantics
  (dedup lease, rate limit, ACK/DLQ, audit).

## V2 → Observability

Structured logs with correlation IDs (`generation_id`, `conversation_id`,
fan scope, `commerce_request_id`), state-transition records, latency,
queue depth, and failure counters. Never secrets or raw sensitive payloads.
