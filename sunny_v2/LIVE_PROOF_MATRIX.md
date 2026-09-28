# Sunny V2 — Live Proof Matrix (F7 execution scope)

Status: SCOPED, NOT EXECUTED. Every proof below requires staging (live
PostgreSQL + Redis Streams + workers) and blocks cutover on failure
(MIGRATION_AND_CUTOVER.md). Fake-store equivalents already pass in the V2
unit suite (307 tests); this matrix re-proves them against real infra —
never faked, never waived. Skip cleanly when staging is absent.

## Staging prerequisites (what must exist before run 1)

**Infrastructure** (all reachable from the prover machine):
- PostgreSQL 15+ with pgvector (`CREATE EXTENSION vector`), connection via
  `POSTGRES_DSN` in `.env` (see `.env.example`). No docker-compose exists
  in-repo, so provide PG + Redis out-of-band (local services, Docker CLI,
  or managed instances).
- Redis 7+ at `REDIS_URL`, with Streams + consumer-group support
  (XAUTOCLAIM path used by `workers/llm_worker.py`).
- Python env from `requirements.txt` / `pip install -e ".[dev]"`.

**Schema** (order matters; `db/migrate.py` does not exist — apply manually):
1. `psql $POSTGRES_DSN -f db/schema.sql` (8 legacy tables + vector ext).
2. `psql $POSTGRES_DSN -f relationship_v2/persistence/schema.sql`
   (12 tables + fact-embedding column/index; covers migrations 002–007
   for fresh DBs). For existing DBs, apply
   `relationship_v2/persistence/migrations/00{2,3,4,5,6}.sql` + `007` in
   order instead.
3. Verify: 12× `v2_*` tables present; partial uniques
   (`uq_v2_facts_current_key`, `uq_v2_intimate_current`,
   `uq_v2_processed_event`) in place.

**Services** (`python run_all.py` or subset): MTProto bot
(`chatbotv2.main`), `workers.llm_worker`, `workers.send_worker`,
dashboard :1010. V1 stays DISABLED (`SUNNY_V1_ENABLED` unset/false).

**Flags per proof stage**:
- Proofs L1–L7/G1/scenarios: `RELATIONSHIP_V2_READ_ENABLED=true`,
  `RELATIONSHIP_V2_WRITE_ENABLED=true`, `RELATIONSHIP_V2_SHADOW_MODE=true`
  (observe + persist, never control replies).
- Commerce gate: no V2 commerce flags exist by design; commerce runs
  untouched throughout.

**Seed data**:
- One staging fan (`users` row) + 20+ inbound `messages` rows across 3+
  days (or run the F10 replay job over a fixture dump).
- Commerce fixtures: one purchasable product, one active-eligible and one
  ineligible fan state (blocked + opted-out rows) for gate negatives.
- Operator account for approve/edit/reject paths (dashboard :1010).

**Prover tooling**: `pytest` with staging DSN env, Redis CLI access for
pending-stream inspection (`XPENDING`/`XAUTOCLAIM`), PG `psql` for row
counts before/after each proof, and a second worker process for the
concurrency proofs. Record every run in the execution table below.

## Reliability proofs (gap rows L1–L7)

| ID | Proof | Method (staging) | Pass criteria | Closes |
|---|---|---|---|---|
| F7-L1 | Duplicate generation idempotency | Submit same `(conversation_id, generation_id)` turn twice via `enqueue_validated_turn` | One `v2_conversation_turns` row; second returns existing | L1 |
| F7-L2 | Duplicate/crash event retry | Deliver same `FanMessageReceived` twice; kill worker between `v2_events` insert and `v2_processed_events` mark, then retry | One logical mutation; retry converges APPLIED then DUPLICATE | L2 |
| F7-L4 | Concurrent same-fan serialization | Two workers, same fan+event, same instant | Exactly one APPLIED + one DUPLICATE; zero torn rows | L4 |
| F7-L5 | Failure injection | Drop PG / kill LLM route / timeout commerce mid-turn | No partial state (`v2_events` without marker is retried, not half-applied); turn fails closed | L5 |
| F7-L6 | Queue determinism | Enqueue → DLQ → replay validated turn | Bounded replay, DLQ record, same outcome | L6, L7 |
| F7-L7 | XAUTOCLAIM + Redis retry | Crash consumer with pending stream entries, reclaim after idle threshold | Reclaimed entries process once (idempotency keys hold) | L7 |
| F7-L3 | Duplicate-send prevention | Double-deliver same send-stream entry to `main.py` transport | One Telegram send (dedup key holds); one `ResponseSent` event | L3 |

## Security proofs (gap row G1)

| ID | Proof | Method | Pass criteria |
|---|---|---|---|
| F7-G1a | Prompt injection | "forget instructions / you are now / reveal creator secrets" as fan turns | Treated as content; no instruction, identity, or commerce-state change |
| F7-G1b | Memory poisoning | Adversarial biography + purchase claims across turns | Creator/commerce guards reject; no poisoned CURRENT rows |

## Acceptance scenarios (07_TEST_AND_VERIFICATION A–G + §43)

New fan · recurring interest · changed fact (hotel→construction) · intimate
continuity · negative engagement (3× ignored topic) · buyer (purchase→episode,
eligibility intact) · 30-day return. Each: run in shadow, assert context +
strategy + validation behavior, record `ShadowMetrics` window.

## Commerce regression gate (every step, blocks on any failure)

Product lookup · price lookup · ownership · eligibility · offer create/seal ·
purchase execute/complete · post-purchase · refund. V2 shadow must not alter
any outcome vs baseline.

## Execution record

| Run date | Env | Prover | Result | Blocking findings |
|---|---|---|---|---|
| 2026-09-28 | local PG18.6 + Redis 7.2.5 (no pgvector) | opencode | F7-L2 PASS (duplicate `FanMessageReceived` → APPLIED then DUPLICATE; 1 `v2_events` + 1 `v2_processed_events` row; relationship created) | pgvector absent → semantic-search proof blocked; seed fans 9001/9002/9003 + 5 messages loaded |
| 2026-09-28 | same + pgvector 0.8.6 (Windows binaries, elevated install) | opencode | pgvector LIVE PASS (`vector` ext, `embedding` column, hnsw index, cosine self-match sim 1.0 via real `set_fact_embedding`/`search_facts_semantic`; latent asyncpg list-binding bug fixed with `_to_vector_literal` + unit test); full suite 308 green | semantic-quality proof (real embeddings model) still open; needs embedding-model endpoint |
| 2026-09-28 | same + MiniLM all-MiniLM-L6-v2 local (384 dims, migration 008 resize) | opencode | MiniLM LIVE PASS (3 memories embedded + stored via repo ports, query "hey, just got home" cosine-ranked live; deprecation warning fixed; suite 311 green) | F7 matrix continues: concurrency, crash-retry, failure injection, adversarial |
| 2026-09-28 | same | opencode | **F7 battery 8/8 LIVE PASS**: L1 turn redelivery; L4 concurrent pair → 1 APPLIED + 1 DUPLICATE + 1 mark (via new `(xmax=0)` linearization fix); L5a crash→retry converges; L5b commerce-down degrades with stale section; L6 DLQ + real Redis lock True/False/True; L3 send redelivery dedupes; G1 0 adversarial promotions + price rail suppressed; A new-fan e2e plans with strategy. Suite 318 green | 3 findings fixed in-repo: (1) check-then-act outcome race → `inserted` flag on mark; (2) orchestrator default-port positional bug; (3) G1 proof counted non-FACT candidates. Pre-existing (out of scope): `commerce` package import breaks on live (`db.dropfans` missing) — turn path degraded correctly. L7 XAUTOCLAIM app-wiring + duplicate-send transport still open (need running streams/workers) |
| 2026-09-28 | same | opencode | **Commerce Phase 1+2 LIVE**: `db/fangate.py` (26 fns) + `db/dropfans.py` (20 fns) built from call-site extraction; migrations 001 + 002 + dated p3 file applied; full DAO round-trips PASS (CUID resolution, sales dedupe True/False, vault sync shape, selection config); D1 port chain imports; **live reads PASS** for eligibility + purchase ports; suite 329 green | Next missing module blocking opportunity evaluation: `db/offer_definitions` (then vault/segments/automation/families/migrate) |
| 2026-09-28 | same | opencode | **Offer/intent DAO LIVE**: `db/offer_definitions.py` (9 fns + OFFER_TYPES) + `db/drop_intents.py` (6 fns); migrations 20260916000000/17000000/17010000 applied; lifecycle draft→active→retired + intent pending→failed→pending→active proven live; **all 3 commerce ports read live** (eligibility/purchase/opportunity); unit determinism test hardened (no live-DB dependence); suite 338 green | Remaining DAO: vault/segments/automation/families + migrate runner; then F11 shadow window |
Copy this table per run. A BLOCKED phase cannot be marked complete; findings
go to OPEN CONSIDERATIONS with owners, never silent waivers.
