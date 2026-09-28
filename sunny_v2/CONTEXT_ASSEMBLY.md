# Sunny V2 — Context Assembly (FUTURE)

## Composition order

```text
SYSTEM CONTEXT
+
CREATOR CONTEXT
+
CURRENT FAN IDENTITY
+
CURRENT RELATIONSHIP STATE
+
RELEVANT LONG-TERM MEMORY
+
RECENT EPISODIC MEMORY
+
RECENT CONVERSATION
+
CURRENT CONVERSATION STATE
+
ENGAGEMENT SIGNALS
+
ESCALATION STATE
+
COMMERCE CONTEXT
+
RESPONSE CONSTRAINTS
```

## Rules

- **Token budgets** — each section has a cap; total is bounded. Overflow
  truncates by priority (constraints and commerce context last to drop),
  never silently.
- **Prioritization** — safety constraints > commerce confirmations >
  relationship state > recent conversation > long-term memory > background.
- **Truncation** — deterministic (recency + salience), logged with what was
  dropped.
- **Freshness** — snapshots carry `as_of`; stale snapshots (beyond per-section
  TTL) are refetched or marked stale, never presented as current.
- **Conflict resolution** — commerce confirmations beat memory; current facts
  beat historical facts; fan corrections beat extractions; explicit boundaries
  beat strategy preferences. Every resolution is logged.
- **Grounding** — creator identity from system configuration, fan identity
  from trusted data, commerce facts from commerce confirmations only.
