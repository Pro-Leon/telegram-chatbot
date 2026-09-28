# Audit PHASE_02 — Classification

- KEEP_COMMERCE: `commerce/` (execution, orchestration, decision, eligibility,
  ownership, catalog, attribution, post-purchase, reconciliation, opportunity
  engine/sealing, dao, single_creator), `integrations/dropfans/`,
  `integrations/fangate/` (legacy provider), commerce tables/queues.
- KEEP_SHARED_INFRASTRUCTURE: `db/*`, `core/config.py`, `core/event_bus.py`,
  `core/generation.py`, `core/telemetry.py`, `core/audit.py`,
  `core/worker_heartbeat.py`, `core/shutdown.py`, `chatbotv2/main.py` send
  loop, dashboard human-override routes, `run_all.py`.
- KEEP_REQUIRED_RUNTIME: launchers, dashboard app shell, scheduler (commerce
  duties), migrations, deployment config.
- V1_LEGACY / V1_DISABLED: `chatbotv2/handlers.py` ingress,
  `workers/llm_worker.py` generation, `workers/send_worker.py` flush,
  `core/one_call*.py`, `core/routing.py`, `core/scoring*.py`,
  `memory/*`, `context_engine/*`, `agent/*`.
- V2_DOCUMENTATION: `sunny_v2/` (existing + new required spec files).
- ARCHIVE: `docs/*.md` forensic reports (marked historical, not current).
- REMOVE: none. No file met the verified-zero-dependency removal bar.
- UNKNOWN: root dev scripts (`qwen3_*`, `verify_*`, `test_qwen*`,
  `benchmark_local.py`, `sweep_phase57.py`, etc.), `recovery_llm_worker/`,
  `simulation*/`, `automation/`, `segments/` consumers — require review.
