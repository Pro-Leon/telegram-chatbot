# Sunny V2 — Dependency Register (closes gap A4 at doc level)

Owner: `relationship_v2/`. Rule: every reused component documents what V2
depends on, what it does NOT inherit, and why reuse is safe (Build
Instructions §3 greenfield rule). Anything not listed here is NOT a
dependency — flag it if found.

## V2-internal ownership (one owner per store)

| Table | Owner function | Consumers (read-only) |
|---|---|---|
| `v2_relationships` | `persistence.repository:get_or_create_relationship` | turn_context, orchestrator |
| `v2_memory_facts` (+embedding) | `create_memory_fact` / `supersede_memory_fact` | retrieval, observation, backfill |
| `v2_memory_episodes` | `create_memory_episode` | turn_context, summary |
| `v2_conversations` / `v2_conversation_turns` | `create_conversation` / `create_conversation_turn` | queue, turn_context |
| `v2_events` | `create_relationship_event` | processor, orchestrator, replay |
| `v2_commerce_refs` | `create_commerce_ref` | commerce_adapter (confirmed-only cache) |
| `v2_relationship_snapshots` | `relationship_context:persist_snapshot` | shadow, dossier (future) |
| `v2_engagement_signals` | `record_engagement_signal` | pattern_learning, turn_context |
| `v2_processed_events` | `mark_event_processed` | processor, orchestrator, jobs |
| `v2_open_loops` | `create_open_loop` (+reference/resolve/close) | recall, turn_context, sweep (future) |
| `v2_intimate_history` | `record_intimate_signal` | turn_context, dossier |

## Reused shared infrastructure (PRESERVED)

| Component | V2 depends on | V2 does NOT inherit | Why safe |
|---|---|---|---|
| `db.postgres.get_pool` | Connection pooling, `users` row reads (eligibility) | Legacy relationship tables are never read/written by V2 SQL (all statements `v2_*`-scoped; AST-verified per module) | Separate tables, separate keys, fail-closed scope on every function |
| `db.redis` | `v2:lock:{creator}:{user}` namespace only | Legacy `lock:user:` keys never acquired; no durable state in Redis | Distinct keyspace; TTL-bounded; lock loss degrades to deferral, never corruption |
| `core.config` / `core.architecture_router` | `RELATIONSHIP_V2_*` flags (default off) | No request/stream payload can flip flags; commerce never gated | Server-side env only; flag-off short-circuits before I/O |
| `workers/llm_worker.py` (+22-line hook) | Observation point after context-engine telemetry | Generation, routing, sending, state — hook returns metadata or None | Flag-gated, fail-open, verb-absence tested |
| `chatbotv2/main.py` (+2 blocks) | Post-`message.sent` confirmation point | Send/persist/realtime semantics untouched | Flag-gated, fail-open inward only |
| `commerce.*` truth reads | `dao.get_aftercare_status`, `fan_commercial_state`, `offer_history`, `opportunity_engine` (reads) | Prices/products/eligibility/sealing/execution/attribution/delivery decisions; provider clients; legacy relationship modules | Read-only imports allow-listed and AST-enforced in `test_relationship_v2_commerce_ports.py` |

## Explicitly NOT dependencies

Legacy relationship authority (`commerce/relationship.py`,
`relationship_trajectory.py`, `intimacy_trajectory.py`, `memory/profile.py`,
`long_term_memory.py`, `fan_knowledge.py`, `memory/summarizer.py`),
`context_engine/` retrieval, legacy `ConversationState` durability, Phase 1
realtime event semantics (observed, never altered), any LLM provider client.

## Change rule

Adding a dependency requires appending a row above (component, scope of
use, non-inheritance proof, safety argument) plus an import-hygiene test in
the affected area. Silent coupling is a phase-blocking finding.
