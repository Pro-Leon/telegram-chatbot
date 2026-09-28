# Sunny V2 — Phased Implementation Plan (FUTURE)

Every phase MUST contain:

```text
OBJECTIVE
SCOPE
FILES/MODULES
DEPENDENCIES
DATA CHANGES
EVENTS
APIS
TESTS
OBSERVABILITY
FAILURE MODES
MIGRATION
ROLLBACK
EXIT CRITERIA
AUDIT REQUIREMENTS
```

## Phase sequence (do not implement now)

```text
PHASE 1 — Core domain and persistence (entities, owners, migrations)
PHASE 2 — Relationship context (structured state, snapshots)
PHASE 3 — Memory system (extraction/validation/consolidation/retrieval)
PHASE 4 — Conversation engine (lifecycle, turns, resumption)
PHASE 5 — Context assembly (budgets, prioritization, conflicts)
PHASE 6 — Strategy/escalation (stages, guards, cooldowns)
PHASE 7 — Response engine (plan → generate → validate)
PHASE 8 — Commerce contract integration (READ/REQUEST/CONFIRMATION wiring)
PHASE 9 — Queue/concurrency/idempotency (locks, dedupe, DLQ)
PHASE 10 — Shadow validation (observe-only, no sends/mutations)
PHASE 11 — Controlled activation (flagged, scoped, reversible)
PHASE 12 — V1 retirement (removal only after verified stability)
```

Each phase follows `OPENCODE_BUILD_INSTRUCTIONS.md`: audit → implement →
test → verify → audit again. No phase may introduce V1 dependencies or
commerce authority leakage.
