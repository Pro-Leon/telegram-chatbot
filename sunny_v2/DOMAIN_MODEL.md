# Sunny V2 — Domain Model (FUTURE)

No entity below is implemented. This is the design vocabulary for Phase 1+.

## Entities

- **Fan** — identity `(creator_id, user_id)`. Creator scope is mandatory;
  unknown scope fails closed. Owns relationship, memory, conversations.
- **Relationship** — persistent structured state between creator and fan
  (tenure, comfort, style, milestones, responsiveness). Not a single score.
- **RelationshipMemory** — aggregate root for episodic + semantic memory.
- **MemoryFact** — current-or-historical fact with `effective_from`,
  `previous_value`, provenance, confidence, contradiction links.
- **MemoryEpisode** — one processed turn or event with summary, signals,
  salience, and links to facts/conversations.
- **Conversation** — a session or continuous thread; has lifecycle
  (started/active/paused/closed/failed) and summary.
- **ConversationTurn** — one inbound + one outbound pair with correlation
  (`generation_id`), routing decision, validation verdict.
- **ConversationState** — derived deterministic snapshot (lifecycle, topic,
  threads, momentum) used by strategy.
- **RelationshipState** — derived snapshot of relationship dimensions.
- **InteractionSignal / EngagementSignal** — deterministic observations
  (responsiveness, topic affinity, momentum), never LLM verdicts.
- **EscalationState** — current strategy stage + confidence + cooldown +
  reversibility + commerce eligibility snapshot.
- **ResponsePlan** — explicit plan (intent, strategy, constraints, commerce
  references) produced before generation.
- **CommerceContext** — read-only authoritative snapshot returned by commerce
  (available actions, eligibility, sealed states). Never authored by V2.
- **CommerceActionRequest** — V2→commerce structured request with idempotency
  key, owner, and scope.
- **CommerceActionResult** — commerce→V2 confirmation or rejection with stable
  codes. The only basis for commerce claims in generated text.

## Identity and lifecycle

- Identity is always `(creator_id, user_id)` plus entity UUID where stored.
- Facts are append-only with temporal validity; corrections supersede, never
  overwrite.
- Conversations resume by loading persisted state, never by re-deriving from
  the last N messages alone.
- Every persistent entity records `created_at`, `updated_at`, `provenance`,
  and owner module.

## Invariants

1. No commerce truth stored in V2 tables except cached confirmations with
   `commerce_request_id`, `confirmed_at`, and expiry.
2. No state transition without an explicit event and guard evaluation.
3. No memory write without provenance.
4. No outbound turn without a persisted plan + validation verdict.
