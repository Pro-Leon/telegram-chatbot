# Sunny V2 — Data Model (FUTURE requirements)

Persistent storage requirements. No tables were created in the cutover task.

## Required stores

- **Fan identity** — `(creator_id, user_id)` primary scope, profile snapshot
  reference, funnel/entitlement references owned by commerce (never copied).
- **Relationship context** — structured dimensions (tenure, comfort, style,
  milestones), derived snapshots with `as_of`, full history preserved.
- **Episodic memory** — per-turn episodes: summary, signals, salience,
  generation link, provenance. Append-only.
- **Semantic memory** — distilled facts and preferences with confidence,
  contradiction links, and supersession chains.
- **Current facts vs historical facts** — every fact carries `effective_from`,
  optional `effective_to`, `previous_value`, `provenance`, `confidence`.
  Queries support both "true now" and "true at T".

```text
Fact: occupation = "teacher"              (effective_from = T1)
Later: occupation = "software developer"  (effective_from = T2, previous_value = "teacher")
```

- **Topic affinity** — engage/avoid signals per topic with evidence counts.
- **Interaction preferences** — style, pacing, boundaries asserted by the fan.
- **Conversation history** — turns with correlation IDs, routing, validation.
- **Conversation summaries** — rolling + milestone summaries with source
  ranges, never destructive rewrites of history.
- **State** — conversation, relationship, escalation snapshots with versioning.
- **Events** — durable domain events with `event_id`, producer, consumer,
  idempotency key.
- **Commerce references** — `commerce_request_id`, confirmed payload hash,
  `confirmed_at`; authoritative rows stay in commerce tables
  (`PRESERVED`: `commerce_offers`, `fangate_transactions`,
  `vault_media_deliveries`, `fangate_products`).

## Temporal evolution rules

1. Never overwrite history destructively; supersede with new rows.
2. Every write carries provenance (turn, source, extractor version).
3. Contradictions create explicit supersession or conflict records, never
   silent replacement.
4. Retention and decay are explicit policies, not accidental deletion.

## Ownership (FUTURE)

Each future table gets one owner module, named in the Phase 1 design. Shared
or ownerless columns are rejected at review.
