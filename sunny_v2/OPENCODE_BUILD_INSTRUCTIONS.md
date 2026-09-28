# Sunny V2 — OpenCode Build Instructions

Master rules for every future implementation session.

## Cycle (mandatory)

```text
1. AUDIT
2. IMPLEMENT
3. TEST
4. VERIFY
5. AUDIT AGAIN
6. ONLY THEN PROCEED
```

## Before coding — identify

Current code, callers, dependencies, data model, events, queues,
configuration, tests, external interfaces. Name the owner of every field,
the producer/consumer of every event and queue, and the contract of every
API touched.

## During coding

Implement only the phase scope. No speculative infrastructure, placeholder
services, dead functions, unused events/queues/config, fake APIs, or mock
production paths.

## After coding — verify

Imports, tests, type checks, lint, migrations, runtime references, event
wiring, queue wiring, persistence, error handling, concurrency, idempotency.
Confirm commerce boundary: no direct commerce writes, no provider calls from
V2, no invented commerce truth in prompts or outputs.

## Final audit — confirm

```text
No orphan code
No dead code
No unused imports
No unused configuration
No unused events
No undocumented database changes
No undocumented API changes
No hidden V1 dependency
No commerce authority leakage
No broken runtime path
```

Stop on any failure. Report the conflict instead of working around it.
