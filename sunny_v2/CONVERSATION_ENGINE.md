# Sunny V2 — Conversation Engine (FUTURE)

## Lifecycle

`started → active → paused → resumed → closed`, with `failed` as an explicit
recovery state. Every transition has guards, triggering events, and audit.

## Turn processing

1. Classify inbound + idempotency check.
2. Load relationship, memory, conversation, escalation snapshots.
3. Assemble context (see `CONTEXT_ASSEMBLY.md`).
4. Derive conversation state (topic, threads, momentum, lifecycle).
5. Select relationship + escalation strategy.
6. Plan response, generate, validate.
7. Enqueue outbound, persist turn, emit events.

## Required behaviors

- **Context loading** — creator-scoped snapshots; missing scope fails closed.
- **State transitions** — guarded, logged, reversible where specified.
- **Topic continuity** — open threads tracked; pivots explicit.
- **Re-entry after inactivity** — resume with summary of last state +
  elapsed-time context, never a cold open.
- **Interruption handling** — overlapping or out-of-order turns resolved by
  idempotency keys and state version checks.
- **Conversation resumption** — paused conversations restore full state.
- **Response generation lifecycle** — plan → generate → validate → enqueue;
  any failure yields a deterministic outcome (retry, queue for review, or
  suppress), never a silent drop.
- **Failure recovery** — failed turns preserve inbound, emit
  `conversation.failed`, and remain retryable.
