# Sunny V2 — Legacy Architecture Isolation

## Objective

V2 must be capable of operating without the legacy relationship/conversation architecture.

The existing commerce layer is retained.

## Allowed dependency

```text
Sunny V2
   ↓
Commerce Adapter
   ↓
Existing Deterministic Commerce
```

## Disallowed dependencies

V2 must not depend on legacy:

- conversation state
- relationship trajectory
- intimacy trajectory
- legacy memory retrieval
- legacy response-mode planner
- legacy desire engine
- legacy sales-window logic
- legacy conversation planner

unless an explicit compatibility adapter is approved and documented.

## Feature flag

V2 should have an explicit runtime mode.

Conceptually:

```text
SUNNY_CONVERSATION_ENGINE=v1|v2|disabled
```

The exact configuration mechanism must follow the existing project's conventions.

## Cutover model

Recommended stages:

### Stage 1 — dark audit

V2 runs without affecting outbound behavior.

### Stage 2 — shadow generation

V2 generates and records candidate responses without sending.

### Stage 3 — operator review

V2 outputs are visible for review.

### Stage 4 — controlled V2 send

V2 handles selected traffic.

### Stage 5 — V2 primary

V2 becomes the conversation engine.

### Stage 6 — legacy disabled

Legacy relationship/conversation execution paths are disabled.

Commerce remains active.

## Isolation tests

Tests must prove:

- V2 does not import legacy relationship modules
- V2 does not mutate legacy relationship state
- V2 does not require legacy conversation state
- V2 can construct a relationship snapshot from V2 persistence
- commerce remains reachable through the adapter
- disabling V2 does not corrupt commerce

## No accidental dual writes

During transition, explicitly identify every state write.

There must never be an accidental situation where:

```text
V1 relationship state
+
V2 relationship state
```

both become authoritative.

V2 has one source of truth for V2 relationship behavior.
