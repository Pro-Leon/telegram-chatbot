# Sunny V2 — Relationship Engine (FUTURE)

Structured persistent relationship state, not one numeric score.

## Represented dimensions

```text
known for a while (tenure)
comfortable interaction (comfort/familiarity)
recurring topics
known preferences
interaction style (pacing, tone, boundaries)
recent activity
long-term history
previous intimacy / closeness markers
previous purchases (references only — truth in commerce)
recent inactivity and re-entry context
current emotional/conversational context
responsiveness and momentum
promises/commitments outstanding
```

## Design rules

1. State is a versioned structure with `as_of` snapshots; transitions are
   event-sourced.
2. Derivations are deterministic functions of memory + conversation state +
   engagement signals. The LLM may describe the relationship; it never sets
   state directly.
3. Purchase history is referenced (counts, recency classes from commerce
   confirmations), never duplicated as truth.
4. Inactivity produces explicit re-entry context (what changed, what to
   resume), not a cold start.
5. Boundaries asserted by the fan persist as constraints on strategy and
   validation.

## Relation to CURRENT code

`CURRENT` relationship/trajectory/intimacy modules under `commerce/`
(`relationship*.py`, `intimacy_*.py`, `relationship_evidence.py`) are
`DEPRECATED` for V2: forensic reference only. V2 must not import them; it
re-derives equivalent coverage from its own memory and state model with
explicit justification per field reused.
