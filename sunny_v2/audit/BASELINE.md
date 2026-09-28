# Sunny V2 — Cutover Audit Baseline (CURRENT, pre-change record)

## Repository

- Language/runtime: Python >= 3.11, package `telegram-chatbot` 1.0.0.
- Package manager: pip (`requirements.txt`, `pyproject.toml` setuptools).
- Branch: `main` (working tree ahead of origin with uncommitted changes;
  no tag forced — see `audit/PHASE_01.md`).

## V1 runtime entry points (DISABLED after cutover)

- `chatbotv2/main.py` — MTProto bot + `bot_main` send-stream consumer.
- `chatbotv2/handlers.py` — `handle_incoming_message`, `_wait_and_process`
  (debounce owner → `enqueue_inbound`).
- `workers/llm_worker.py` — `process_message` + `llm_workers` consumer loop.
- `workers/send_worker.py` — `flush_queue`, `process_approved_message`.
- `workers/scheduler_worker.py` — scheduled messages + reconciliation +
  re-engagement + orchestration loop (commerce duties preserved).
- `chatbotv2/dashboard/app.py` (+ `routes/messages.py`, `queue.py`,
  `vault.py`) — review UI + human-authorized sends (preserved override).
- Launchers: `run_all.py`, `__main__.py`.

## V1 modules (DEPRECATED, frozen)

`core/one_call*.py`, `core/routing.py`, `core/scoring*.py`,
`core/conversation_state.py`, `core/conversation_contract.py`,
`memory/*`, `context_engine/*`, `agent/*` (canary/shadow), `commerce/`
conversational/relationship/intimacy helpers used by V1 only.

## Commerce modules (PRESERVED)

`commerce/execution.py`, `orchestrator.py`, `decision.py`, `eligibility.py`,
`ownership.py`, `product_catalog.py`, `attribution.py`, `post_purchase.py`,
`reconciliation.py`, `opportunity_engine.py`, `opportunity_sealing.py`,
`dao.py`, `single_creator.py`, `production_control.py`, `vault_sets.py`;
providers `integrations/dropfans/` (sole active), `integrations/fangate/`
(legacy); delivery `chatbotv2/main.py`; queues inbound/send/DLQ; tables
`commerce_offers`, `fangate_products`, `fangate_transactions`,
`vault_media_deliveries`, `scheduled_messages`, `creator_integrations`.

## Shared infrastructure (PRESERVED)

`db/postgres.py`, `db/redis.py`, `db/dropfans.py`, `db/fangate.py`,
`db/vault.py`, `db/migrate.py`; `core/config.py`, `core/event_bus.py`
(`chatbot:events`), `core/generation.py`, `core/telemetry.py`,
`core/audit.py`, `core/worker_heartbeat.py`, `core/shutdown.py`.

## Known coupling

Legacy `process_message` evaluated commerce inline (opportunity engine +
`_try_commerce_draft`). Gated off with V1; documented in
`SYSTEM_BOUNDARIES.md` for removal during V2 implementation.

## Documentation inventory

- `sunny_v2/` pre-existing design docs (kept): `00_DOCUMENT_INDEX.md` … `12_…`,
  `Sunny_*`, `sunny_upgrade_v2.md` — historical design, subordinate to the
  new required spec files on conflict.
- `docs/*.md` (~200 forensic/phase reports): ARCHIVED historical reference,
  not current authority.

## Unresolved risks

1. Working tree had pre-existing uncommitted modifications (not authored by
   this task) — no tag forced; checkpoint recorded, not created.
2. Root-level dev scripts (`qwen3_*`, `verify_*`, `test_qwen*`, etc.) marked
   UNKNOWN — not deleted, require review.
3. Scheduler mixes commerce + legacy orchestration — kept running; carve-out
   is a V2 implementation duty.
