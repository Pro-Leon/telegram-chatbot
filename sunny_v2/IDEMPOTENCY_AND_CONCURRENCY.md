# Sunny V2 — Idempotency and Concurrency (FUTURE)

## Duplicate protection

- **Duplicate inbound events** — idempotency key per inbound (source +
  message identity); redelivery yields `duplicate`, never a second turn.
- **Duplicate response generation** — one generation per
  `(conversation_id, turn_id)`; retries reuse the stored plan/result.
- **Duplicate outbound sends** — dedup identity per logical send enforced at
  enqueue and at transport (lease + confirm), mirroring the `PRESERVED`
  send-stream semantics.
- **Duplicate commerce requests** — same idempotency key → same
  `CommerceActionResult`; commerce owns enforcement.
- **Duplicate purchase attribution** — conditional offer transitions +
  unique transaction identity (`PRESERVED` DAO semantics).

## Concurrency

- **Concurrent fan conversations** — per-scope locks with bounded TTL;
  contention defers (no ACK-loss), never double-processes.
- **Stale relationship state** — snapshot versioning; writes check versions
  and retry or replan on conflict.
- **Stale memory writes** — source-order gating at the mutation boundary;
  older sources cannot overwrite newer applied state.
- **Race conditions** — re-read authoritative rows before send/confirm;
  stale snapshots suppress with audit.
- **Retry storms** — bounded retries, backoff, DLQ with replay limits,
  circuit awareness on provider/commerce failures.

## Strategies

Optimistic concurrency for memory/state (version check + retry); pessimistic
scoped locks only around the generation→enqueue critical section. All locks
bounded with TTL and heartbeat.
