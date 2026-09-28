# Audit PHASE_04 — Commerce preservation

- Entry points: `commerce/execution.py execute_ppv`,
  `commerce/orchestrator.py orchestrate_commerce`,
  `commerce/attribution.py attribute_purchase`,
  `commerce/post_purchase.py handle_post_purchase`,
  `commerce/reconciliation.py reconcile_all`,
  `commerce/opportunity_engine.py evaluate_opportunity`,
  `integrations/dropfans/` provider, scheduler reconciliation loop,
  `chatbotv2/main.py` delivery, dashboard commerce routes.
- Services/tables/queues/providers: unchanged (see COMMERCE_CONTRACT.md).
- Validation/execution/attribution/post-processing/idempotency: untouched
  deterministic logic.
- V1-disable impact check: gates sit outside commerce; delivery and
  reconciliation paths verified intact by import + compile + reference scan.
- Known coupling: legacy worker evaluated commerce inline — disabled with V1
  and documented as boundary debt for V2 implementation (no silent rewrite).
