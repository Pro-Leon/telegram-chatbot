# Sunny V2 — Observability (FUTURE)

## Required telemetry

- Structured logs with correlation IDs: `generation_id`, `conversation_id`,
  fan/relationship IDs, `event_id`, `commerce_request_id`.
- Per-stage latency (router, context load, retrieval, assembly, strategy,
  generation, validation, enqueue, delivery).
- Failures with stable codes (never free-form exception text toward clients).
- Queue depth, consumer lag, DLQ size and replay counts.
- Generation failures, memory failures, state-transition logs.

## Relation to CURRENT (PRESERVED)

`core/telemetry.py`, `core/audit.py` (durable audit events with server-derived
actors), worker heartbeats, and the `chatbot:events` Pub/Sub channel stay.
V2 adds conversation/memory/strategy dimensions; it does not replace the
audit trail.

## Rules

- Never log secrets, credentials, ciphertext, raw provider bodies, or
  unnecessary raw fan payloads.
- Every state mutation links to its triggering event and correlation IDs.
- Dashboards remain pollable; realtime events are acceleration, not source of
  truth.
