# Final Repository Audit (cutover completion)

## Files created

- `core/architecture_router.py` (new: V1/V2/commerce router + gates).
- `sunny_v2/` required spec (24): README, ARCHITECTURE, NON_NEGOTIABLES,
  SYSTEM_BOUNDARIES, DOMAIN_MODEL, DATA_MODEL, EVENT_MODEL, API_CONTRACTS,
  COMMERCE_CONTRACT, MEMORY_ARCHITECTURE, RELATIONSHIP_ENGINE,
  CONVERSATION_ENGINE, ESCALATION_ENGINE, CONTEXT_ASSEMBLY, RESPONSE_ENGINE,
  STATE_MACHINES, IDEMPOTENCY_AND_CONCURRENCY, OBSERVABILITY, FAILURE_MODES,
  SAFETY_AND_BOUNDARIES, TESTING_STRATEGY, MIGRATION_AND_CUTOVER,
  PHASED_IMPLEMENTATION_PLAN, OPENCODE_BUILD_INSTRUCTIONS.
- `sunny_v2/audit/`: BASELINE, PHASE_01–07, FINAL (this file).

## Files moved / deleted

None. No deletions, no moves, no V1 refactors. Pre-existing `sunny_v2/`
design docs kept (new spec takes precedence on conflict).

## Runtime changes (minimal, gating only)

- `core/config.py`: `sunny_v1_enabled=false`, `sunny_v2_enabled=false`.
- `.env.example`: documented `SUNNY_V1_ENABLED` / `SUNNY_V2_ENABLED`.
- `chatbotv2/handlers.py`: ingress fails closed when V1 disabled.
- `workers/llm_worker.py`: `process_message` fails closed (emits
  `ai.generation_failed {error: v1_disabled}`), no draft/score/route/enqueue.
- `workers/send_worker.py`: `flush_queue` returns 0 when V1 disabled.
- Untouched: `chatbotv2/main.py` delivery, scheduler reconciliation,
  dashboard human override, all of `commerce/`, providers, shared infra.

## V1 runtime status

DISABLED: ingress, generation, and operator-flush gates verified by import
test (`v1_enabled=False`) and static reference scan. Cannot process normal
conversations, generate outbound responses, or reactivate via config/payload.

## Commerce runtime status

PRESERVED (code/config untouched) with BLOCKED live verification: the working
tree predates this task without the `db/` package (deleted vs HEAD —
`db/postgres.py`, `db/redis.py`, etc. absent), so worker/commerce imports
cannot execute here. No commerce file was modified by this task; restoration
of `db/` is left to the owner (not done: would overwrite unrelated work).

## V2 documentation status

CREATED: all 24 required files + audit trail. No V2 runtime implemented.

## Unresolved risks

1. `db/` package absent from working tree (pre-existing) — full runtime
   verification of workers/commerce blocked; restore decision required.
2. Pre-existing uncommitted changes throughout the tree — no tag forced.
3. Root dev scripts + misc dirs (UNKNOWN) require review before any removal.
4. Scheduler mixes commerce + legacy orchestration — carve-out is a V2
   implementation duty.
5. `pytest tests/test_routing_tiers.py`: 10/11 pass; 1 failure is the
   pre-existing missing-`db` import, unrelated to this task.

## Commands/checks executed

- `git status`, `git log`, `git diff --stat` (baseline + final).
- `python -m py_compile` on all edited/new runtime files — pass.
- Router import test — `v1_enabled=False, v2_enabled=False`, routing correct.
- `pytest tests/test_routing_tiers.py -q` — 10 passed, 1 pre-existing
  environment failure (missing `db`).
- Grep scans: V1 gate references (3 call sites), queue producers/consumers,
  event bus contract.

## Intentionally NOT changed

V1 domain logic, commerce logic/schema, scheduler behavior, delivery loop,
dashboard routes, migrations, deployment config, docs reports, root scripts.
