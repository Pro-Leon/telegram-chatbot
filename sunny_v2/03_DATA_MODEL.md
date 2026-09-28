# Sunny V2 — Data Model Specification

## Design objective

Persist a fan as a longitudinal relationship rather than as a collection of disconnected messages.

## Recommended logical storage

### `v2_relationships`

One row per creator/fan relationship.

Fields should include:

- `id`
- `creator_id`
- `fan_id`
- lifecycle state
- relationship stage
- familiarity counters
- engagement counters
- reciprocity counters
- continuity counters
- intimacy counters
- last interaction
- last meaningful interaction
- last intimate interaction
- last purchase timestamp
- relationship summary
- current thread ID
- version
- created_at
- updated_at

Unique constraint:

`(creator_id, fan_id)`

## `v2_semantic_memories`

One row per current semantic memory.

Conceptual fields:

- `id`
- `creator_id`
- `fan_id`
- `memory_key`
- `value`
- normalized value
- category
- confidence
- importance
- source
- first_observed_at
- last_confirmed_at
- current status
- supersedes_memory_id
- metadata
- created_at
- updated_at

Unique current-memory constraint should prevent multiple current values for the same logical key unless the domain explicitly supports multiple values.

## `v2_memory_history`

Immutable historical changes.

Store:

- memory key
- previous value
- new value
- reason
- evidence
- timestamps
- source generation

Never delete history simply because the current value changed.

## `v2_relationship_episodes`

Stores meaningful events.

Episode categories should be extensible.

Initial categories:

- INTRODUCTION
- PERSONAL_DISCOVERY
- SHARED_INTEREST
- OPEN_LOOP
- PROMISE
- HUMOR
- FLIRTATION
- INTIMATE_THREAD
- CONTENT_INTERACTION
- PURCHASE
- POST_PURCHASE
- OBJECTION
- DECLINED
- REENGAGEMENT
- ABSENCE_RETURN
- NEGATIVE_ENGAGEMENT
- POSITIVE_ENGAGEMENT
- BOUNDARY
- IMPORTANT_EVENT

## `v2_conversation_threads`

Represents ongoing conversational threads.

Fields:

- thread ID
- relationship ID
- topic
- thread type
- status
- last user message
- last assistant response
- importance
- intimacy level
- started_at
- last_active_at
- closed_at

A relationship can have several historical threads but only a bounded number of active threads.

## `v2_engagement_signals`

Stores evidence about what works and does not work.

Examples:

- topic engagement
- response latency
- continuation
- ignored question
- topic abandonment
- teasing response
- CTA response
- content response

Do not treat one event as a permanent preference.

Use confidence/recurrence.

## `v2_relationship_snapshots`

Optional audit table for generated authoritative snapshots.

Useful fields:

- generation ID
- relationship version
- snapshot hash
- strategy
- selected memories
- selected episodes
- commerce state hash
- timestamp

This provides forensic reproducibility.

## Indexing

At minimum consider indexes for:

- `(creator_id, fan_id)`
- `(creator_id, fan_id, updated_at)`
- `(creator_id, fan_id, importance)`
- `(creator_id, fan_id, last_confirmed_at)`
- `(creator_id, fan_id, episode_type, created_at)`
- active thread lookup
- generation ID idempotency

## Retention

Do not use aggressive decay for relationship-defining facts.

Separate:

- current semantic memory
- historical semantic memory
- episodic memory
- derived relationship counters

Decay should influence retrieval priority, not erase important relationship history by default.
