# Sunny V2 — Observability and Forensics

## Goal

Every important conversational decision must be explainable after the fact.

## Generation trace

Each turn must have a stable generation ID.

Trace:

```text
generation_id
→ inbound message
→ relationship version
→ snapshot hash
→ memories selected
→ episodes selected
→ strategy
→ commerce context
→ prompt version
→ model
→ response
→ validation
→ routing
→ send/queue
→ persistence
```

## Structured events

Recommended event categories:

- `v2.turn.received`
- `v2.snapshot.created`
- `v2.memory.extracted`
- `v2.memory.updated`
- `v2.episode.created`
- `v2.strategy.selected`
- `v2.commerce.requested`
- `v2.commerce.result`
- `v2.generation.completed`
- `v2.validation.completed`
- `v2.routing.completed`
- `v2.outbound.claimed`
- `v2.outbound.sent`
- `v2.outbound.queued`
- `v2.relationship.updated`

## Do not log

Avoid raw sensitive conversational content unless existing logging policy explicitly permits it.

Prefer:

- hashes
- IDs
- categories
- bounded summaries
- structured flags

## Memory explainability

For each recalled memory, be able to answer:

- why was it selected?
- when was it observed?
- how confident is it?
- was it current or historical?
- what source supports it?
- why was it relevant?

## Strategy explainability

Record:

- current stage
- previous stage
- selected objective
- active thread
- callback decision
- commerce readiness
- blocking constraints

## Performance metrics

Track:

- context assembly latency
- memory retrieval latency
- strategy latency
- LLM latency
- validation latency
- total turn latency
- queue wait time
- outbound send latency

## Quality metrics

Track:

- memory recall rate
- incorrect recall rate
- ignored-question rate
- topic continuation rate
- thread continuation rate
- callback engagement
- negative engagement adaptation
- offer transition rate
- purchase attribution
- post-purchase continuity

Metrics must describe behavior; do not turn them into subjective "AI quality scores" without a documented definition.
