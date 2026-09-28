# Sunny V2 — System Boundaries

## V2 owns (FUTURE)

- Relationship context, relationship memory (episodic + semantic + facts).
- Conversation continuity, conversation state, turn processing.
- Response strategy, response planning, response generation (advisory LLM).
- Escalation state and strategy progression.
- Semantic interpretation of fan messages.
- Memory extraction, validation, consolidation, contradiction resolution.
- Context assembly (budgets, prioritization, truncation, freshness).
- Interaction and engagement signals derived from conversation.

## Commerce owns (PRESERVED)

- Products (`fangate_products` mirror), prices (live-verified at execution),
  availability, eligibility, ownership (purchased vault sets).
- Offer state machine (`commerce_offers` pending/clicked/purchased/expired),
  purchase state, provider execution (DropFans sole active provider),
  transaction state (`fangate_transactions`), commerce attribution.
- Post-purchase fulfillment, reconciliation, idempotency, analytics rollups.

## Shared infrastructure (PRESERVED)

PostgreSQL, Redis Streams + Pub/Sub, Telethon transport, send-stream delivery
loop (`chatbotv2/main.py`), dashboard (human override), config/secrets,
logging, telemetry, worker heartbeat, migrations.

## Forbidden cross-boundary mutations

1. V2 must never INSERT/UPDATE `commerce_offers`, `fangate_transactions`,
   `vault_media_deliveries`, `ppv_analytics_daily`, product price/availability,
   or entitlement state directly. All commerce writes go through commerce
   service functions behind `COMMERCE_CONTRACT.md`.
2. V2 must never call provider clients (`integrations/dropfans`,
   `integrations/fangate`) directly.
3. V2 must never assert purchase/availability/price in generated text unless
   quoting a commerce CONFIRMATION payload verbatim.
4. Commerce must never generate free-form relationship language, mutate V2
   memory schema, or drive conversation state transitions. Commerce returns
   structured results; V2 decides how to talk about them.
5. Generated prose is never parsed to authorize commerce. Only explicit
   `CommerceActionRequest` structures cross the boundary.

## Coupling known today (CURRENT)

- The legacy worker called commerce inline (`_try_commerce_draft`,
  opportunity engine evaluation inside `process_message`). That call path is
  `DISABLED` with V1. The coupling is documented here so V2 does not repeat
  it: V2 talks to commerce only through the request/confirmation contract,
  never through shared in-process decision globals.
- The scheduler (`workers/scheduler_worker.py`) mixes commerce duties
  (reconciliation, scheduled commerce follow-ups, re-engagement) with legacy
  orchestration. It is `PRESERVED` running, and V2 must carve commerce duties
  out through the contract during implementation — not by editing commerce
  logic now.
