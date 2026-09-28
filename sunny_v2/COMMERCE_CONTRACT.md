# Sunny V2 — Commerce Contract (critical boundary)

Commerce (`PRESERVED`) is the source of truth. V2 (`FUTURE`) is a client.

## Operations

- **READ** — available actions, eligibility, product truth, price truth,
  sealed states. Deterministic, creator-scoped, fail-closed.
- **REQUEST** — structured `CommerceActionRequest` with idempotency key,
  owner, and scope. V2 expresses intent; it does not execute.
- **CONFIRMATION** — `CommerceActionResult`: confirmed (with authoritative
  payload) or rejected (with stable code). Only confirmations authorize
  commerce claims in generated text.

## Authoritative commerce surface (PRESERVED today)

- Decision: `commerce/decision.py`; orchestration:
  `commerce/orchestrator.py`; execution: `commerce/execution.py execute_ppv`.
- Eligibility: `commerce/eligibility.py`; ownership:
  `commerce/ownership.py`; catalog: `commerce/product_catalog.py`.
- Attribution: `commerce/attribution.py`; post-purchase:
  `commerce/post_purchase.py`; reconciliation:
  `commerce/reconciliation.py reconcile_all`.
- Opportunity evaluation: `commerce/opportunity_engine.py` (read-only).
- Provider: `integrations/dropfans/` (sole active); `integrations/fangate/`
  legacy. Tables: `commerce_offers`, `fangate_products`,
  `fangate_transactions`, `vault_media_deliveries`, `scheduled_messages`.

## Guarantees commerce provides

Idempotency (same key → same outcome), authorization (creator scope, fan
eligibility), ownership (purchased vault sets), eligibility (blocked /
opted-out / active-offer / purchased gates), live price verification at
execution, purchase confirmation only after provider reconciliation.

## Explicitly prohibited

```text
V2 directly creating commerce records
V2 directly modifying prices
V2 directly modifying availability
V2 directly marking purchases
V2 bypassing commerce validation
V2 calling provider clients directly
V2 asserting purchases without commerce confirmation
```

## Error states

`denied`, `ineligible`, `unavailable`, `conflict` (already executed),
`provider_error`, `persistence_failed`, `verification_failed`. V2 maps each
to strategy (retry, replan, handoff, suppress) — never to invented success.
