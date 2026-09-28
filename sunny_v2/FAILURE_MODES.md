# Sunny V2 — Failure Modes (FUTURE)

For each failure: detection → recovery → retry policy → idempotency →
user-facing behavior → observability.

- **Database failure** — detect via health checks; serve nothing requiring
  writes, fail closed on scope/state; retry bounded; user sees safe fallback
  or silence, never fabricated state.
- **Memory retrieval failure** — degrade to recent-conversation-only context,
  mark response low-confidence, queue for review below threshold.
- **LLM failure** — no silent fallback text; deterministic safe completion or
  operator queue; generation_failed event with stable code.
- **Commerce timeout** — treat as unknown, never as confirmed; retry with
  same idempotency key; never claim purchase/offer.
- **Commerce rejection** — replan strategy (cooldown, alternative stage);
  never re-request identically without new inputs.
- **Duplicate events** — dedupe by idempotency key; acknowledge without
  reprocessing.
- **Queue failure** — hold (do not lose turns); replay from durable state.
- **Stale context** — refetch or mark stale; block PRESENT_OFFER on stale
  commerce snapshots.
- **Partial state** — explicit `failed` states with recovery paths; never
  half-applied transitions.
- **Invalid generated response** — validation rejects; review queue or safe
  completion.
- **Provider failure** — commerce owns retries/circuit; V2 sees stable error.
- **Deployment failure** — health gates block traffic; V1 stays disabled;
  rollback to last verified state.
