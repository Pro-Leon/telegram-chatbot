# Definition-of-Done Gap Closure Plan (Partial + Open Items)

Status: ACTIVE. Authority: `sunny_v2/12_DEFINITION_OF_DONE.md` (normative) >
`Sunny_Relationship_V2_Phased_Plan.md` > `sunny_upgrade_v2.md`.
Execution: `Sunny_V2_Build_Instructions.md` + `11_OPENCODE_EXECUTION_RULES.md`
(READ → AUDIT → PLAN → IMPLEMENT → VERIFY → AUDIT AGAIN → ACCEPT → REPORT →
PROCEED). Verdict scale: PASS (built + tested as far as code reaches without
infra) · PARTIAL (logic/contract exists, live path or proof missing) · OPEN
(not built). Baseline: **262 V2 tests green**, Stages A–F3 complete, all
`RELATIONSHIP_V2_*` flags default false, V1 DISABLED, commerce PRESERVED.

Related: `sunny_v2/RELATIONSHIP_V2_REMEDIATION_PLAN.md` (Stages A–F roadmap).

---

# PART 1 — GAP REGISTER (everything not PASS)

## 1. Architecture (1 PARTIAL)

| # | Checkbox | Verdict | Evidence | Acceptance to close |
|---|---|---|---|---|
| A1 | V2 is a new functional architecture | PARTIAL | Greenfield package, zero legacy imports outside sanctioned `integration/`; no live traffic path | F9 turn orchestrator serving shadow then controlled traffic |
| A2 | Legacy not authoritative | PARTIAL | V1 DISABLED, V2 independent; V2 authoritative nowhere | Cutover Stage 7 (V2 source of truth) per F12 |
| A3 | Explicit adapters at boundaries | PARTIAL | Commerce reads + operator map exist; transport/turn-orchestrator adapter missing | F9 orchestrator + F4 send-path confirmation hook |
| A4 | Dependencies documented | PARTIAL | `owners.py`, module contracts, plans; no single register | F13 dependency register doc |

## 2. Relationship (6 PARTIAL)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| R1 | Persistent creator/fan relationship | PARTIAL | Migrate `v2_*` DDL to prod; first live `get_or_create_relationship` row (F9) |
| R2 | Familiarity / engagement / reciprocity / continuity durable | PARTIAL | Add engagement + reciprocity derivations (F6); prove durability over live windows (F11) |
| R3 | Intimacy trajectory durable | PARTIAL | Trajectory-over-time derivation from `v2_intimate_history` (F6) |
| R4 | Absence tracked | PARTIAL | Live gap measurement in orchestrator (F9) |
| R5 | Buyer integrated | PARTIAL | Live purchase→context flow proven in shadow (F11) |
| R6 | Active threads persist | PARTIAL | First live open-loop rows + recall consumption (F9) |

## 3. Memory (7 PARTIAL, 1 OPEN)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| M1 | Semantic memory exists | PARTIAL | Live extraction→validation→persist flow (F9) |
| M2 | Episodic memory exists | PARTIAL | Live episode rows via `EpisodePlan` (F9) |
| M3 | Relationship summary exists | OPEN | Build distillation/summary service (F5) |
| M4 | Important memories survive long gaps | PARTIAL | Prove with real elapsed data in shadow window (F11) |
| M5 | Proactive recall exists | PARTIAL | Live relevance + callback engagement metrics (F11) |
| M6 | Negative engagement learned | PARTIAL | Accumulation loop wired, strategy adapts in shadow diffs (F9/F11) |
| M7 | Memory retrieval tested | PARTIAL | pgvector semantic tier + tests (F5) |

PASS (no action): current/history semantics, contradictions, provenance.

## 4. Conversation (8 PARTIAL)

C1 new-fan · C2 returning-fan · C3 multi-day continuity · C4 intimate-thread
continuity · C5 natural transitions · C6 question control · C7 repetition +
length control · C8 invented-fact block + identity protection.
All share one closure: the F9 orchestrator executing them live with
acceptance scenarios A–G (`07_TEST_AND_VERIFICATION.md`) green in shadow.

## 5. Strategy (1 PARTIAL)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| S1 | Stages strategic, live-selected | PARTIAL | Live `select_stage` loop in orchestrator; stage-change audit events (F9) |

## 6. Commerce (1 PARTIAL)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| K1 | Buyer context reaches V2 | PARTIAL | Live port reads proven in shadow (F11); commerce regression gate green |

PASS (no action): all eight authority boxes (preserved, never duplicated).

## 7. Reliability (6 PARTIAL, 1 OPEN)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| L1 | Generation IDs idempotent | PARTIAL | Live duplicate-generation test on real PG (F7) |
| L2 | Relationship writes idempotent | PARTIAL | Live duplicate/crash retry on real PG (F7) |
| L3 | Duplicate sends prevented | OPEN | Transport-level dedup proof at send path (F7; send code untouched so far) |
| L4 | Concurrent turns serialized | PARTIAL | Live two-worker same-fan test on real Redis+PG (F7) |
| L5 | Failed turns cannot corrupt | PARTIAL | Live failure-injection (DB/LLM/commerce down) with state audit (F7) |
| L6 | Queue behavior deterministic | PARTIAL | Live enqueue/DLQ/replay observation (F7) |
| L7 | Retry behavior tested | PARTIAL | XAUTOCLAIM + Redis-retry proofs on real streams (F7) |

## 8. Observability (5 PARTIAL)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| O1 | Every turn has a trace | PARTIAL | Assembled live trace (generation→snapshot→memories→strategy→commerce→validation→send→persist) in shadow (F11) |
| O2 | Snapshot reproducible | PARTIAL | Rebuild-from-rows proof (F11) |
| O3 | Memory/strategy/commerce explainable | PARTIAL | Selection-reason surfacing in trace (F11) |
| O4 | Failures diagnosable | PARTIAL | Failure taxonomy + runbooks from F7 injections |
| O5 | Debug view | OPEN | Operator view: relationship/person/memories/loops/episodes/patterns/intimate/commerce (F13) |

## 9. Security (2 PARTIAL)

| # | Checkbox | Verdict | Acceptance to close |
|---|---|---|---|
| G1 | Prompt-injection / memory-poisoning resistance | PARTIAL | Adversarial suites green (F7) |
| G2 | Sensitive logging controls | PARTIAL | Log audit proving no raw/intimate content in prod logs (F13) |

PASS (no action): creator/fan isolation, commerce protection, fail-closed boundaries.

---

# PART 2 — PHASED CLOSURE APPROACH (F4–F13)

General rules: smallest vertical slice per phase; scope lock (BLOCKER/REQUIRED
vs OUT-OF-SCOPE → OPEN CONSIDERATIONS); no legacy behavior edits; no commerce
writes; flags default off until the cutover phase says otherwise; STOP on any
commerce/operator regression or unverifiable concurrency.

## F4 — Send-path confirmation hook (closes A3-part, L3-unblocks)

Goal: confirmed sends construct `ResponseSent` via `integration.operator_events`
and feed `event_processor.process_event` — without editing send semantics.
Scope: one call site in send transport + one in auto-send path, both guarded
by `is_v2_write_enabled()` (default off → zero behavior change), fail-open on
V2 errors (log + continue; V2 must never break sending). Files: send path hook
(~15 lines), `tests/test_relationship_v2_send_hook.py` (flag-off no-op proof,
flag-on event-shape proof with fakes). Verify: V2 suite green + send-path
tests green. Do NOT build the orchestrator here.

## F5 — Retrieval upgrade + summary service (closes M3, M7)

Goal: pgvector semantic tier behind the existing memory API + persisted
relationship summary (cache, regenerable, never source of truth).
Scope: `services/semantic_retrieval.py` (embedding port, caller-supplied like
`GenerationPort`; PG `<=>` queries; fuses with lexical scores; budget-capped),
`services/summary.py` (distill from facts/episodes/reading; provenance links),
tests incl. recall-quality gates (soccer-game callback, irrelevance exclusion).
Infra prerequisite: pgvector extension + embedding model in staging. Do NOT
replace the deterministic tiers; semantic is an additional signal.

## F6 — Missing derivations (closes R2, R3)

Goal: engagement + reciprocity bands, intimacy trajectory, loop due-sweep.
Scope: three small pure functions in existing style (`derive_engagement`,
`derive_reciprocity` on evidence+signals; `derive_intimacy_trajectory` on
intimate rows; `due_loops` scan helper), wired into `reason_about`/`StoreCounts`,
tests mirroring C4–C6. No DB changes.

## F7 — Adversarial + live proof tests (closes L1–L7, G1)

Goal: move reliability/security from fake-store proofs to live proofs.
Scope (staging PG + Redis required): duplicate generation/event idempotency,
crash-retry convergence, two-worker same-fan serialization, XAUTOCLAIM +
Redis-retry replay, failure injection (DB/LLM/commerce down) with state
audit, duplicate-send transport proof, injection/poisoning suites. Each proof
is a test file with infra guards (skip cleanly when staging absent — never
fake a live proof). BLOCKS cutover on any failure.

## F8 — Migration backfill job (consumes F2)

Goal: flatten legacy `user_profiles.facts` + fan-knowledge + LTM through
`validate_legacy_fact` into `v2_memory_facts` as HISTORICAL only.
Scope: one offline job module + dry-run report mode (default) + apply mode
behind explicit flag; high-confidence filter; per-row provenance
`legacy_import:{table}`; counts reconciled before/after. Never auto-CURRENT,
never commerce keys, never verbatim intimate. Requires staging + approval.

## F9 — Turn orchestrator (closes A1, R1/R4–R6, M1/M2/M6, C1–C8, S1)

Goal: the load-bearing live path —
inbound → V2 lock (`run_guarded`) → `get_context()` → LLM → validate →
queue → send → `build_response_sent` → `process_event` → memory/episode/
loop/pattern/intimate updates. Scope: ONE new module
(`services/turn_orchestrator.py` or `workers/v2_turn_worker.py`) composing
only shipped primitives; shadow-mode first (observe + log, no control);
single-creator; per-fan serialization; commerce failures degrade per
FAILURE_MODES. Requires: staging infra, F4 hook merged, F7 proofs passing.
Explicitly NOT: rewriting `llm_worker.py` (call the boundary), second
transport, second commerce engine.

## F10 — Replay job (consumes F3)

Goal: historical Telegram → `normalize_message` → `to_fan_received` →
`process_event` → extraction pipeline → episode reconstruction, after live
stable only. Scope: offline job + progress/idempotency reporting (reruns
dedupe by `replay:msg:{id}`). Requires F9 stable + approval.

## F11 — Shadow validation window (closes M4/M5, K1, O1–O4, R2/R5-live)

Goal: 9-stage cutover stages 1–5 (DB → ingestion → memory → shadow context
→ shadow LLM) on live traffic with `ShadowMetrics` windows; acceptance
scenarios A–G green; commerce regression gate at every step (any regression
BLOCKS). Scope: harness wiring (worker hook already emits V2 metadata;
add legacy-side capture + window summaries), metric thresholds doc,
go/no-go report. Requires F9 shadow-mode + staging-then-prod approval.

## F12 — Cutover + legacy shutdown (closes A2)

Goal: stages 6–9 (live context → source of truth → disable legacy writes →
remove legacy reads) + legacy isolation per `10_LEGACY_ISOLATION.md`
(`commerce/relationship.py`, trajectories, `fan_knowledge`, LTM, profiles,
summarizer READ/WRITE disabled; `ConversationState` transient only;
`CommercialRelationshipState` split). Scope: flag flips
(`V2_ENABLED/READ/WRITE=true, SHADOW=false`), per-system verification,
rollback drill (re-enable legacy, V2 data preserved). Requires: F11 go,
F7 green, explicit approval per system. Commerce stays live throughout.

## F13 — Hardening + observability + register (closes A4, O5, G2)

Goal: dependency register doc, operator debug view, log audit (no raw or
intimate content), latency profiling (context/retrieval/strategy/turn),
final flag/rollback documentation. Requires F12 stable.

---

# PART 3 — POST-BUILD RE-AUDIT PROTOCOL (mandatory after F13)

When every phase above reports PASS, repeat the gap research that produced
Part 1 before claiming done. Do not rely on phase reports alone.

1. **Re-read the normative set**: `12_DEFINITION_OF_DONE.md` (every checkbox),
   `00_DOCUMENT_INDEX.md` precedence, `Sunny_V2_Build_Instructions.md:63–64`
   (final audit + cutover checklist), `MIGRATION_AND_CUTOVER.md`.
2. **Re-inventory the code**: list all `relationship_v2/` modules, all `v2_*`
   tables in the PROD schema (not just `persistence/schema.sql`), all live
   callers (`grep relationship_v2` outside tests), all flag states in prod
   config. Compare against the Part 1 register row by row.
3. **Re-run every proof live**: full V2 suite + F7 live proofs + scenarios
   A–G + commerce regression gate + rollback drill. Any failure reopens its
   phase (status BLOCKED, never waived silently).
4. **Re-check the invariants**: no `commerce.*` imports outside `integration/`;
   no provider imports in V2; no draft-mutates-state path; no V2 commerce
   writes in prod logs; Phase 1 realtime contract byte-identical
   (`ai.generation_completed` semantics, `generation_id` stability,
   `event_id` uniqueness, best-effort isolation, transport layering).
5. **Produce the delta report**: for each Part 1 row, verdict THEN vs NOW
   with file:line evidence. Any row still PARTIAL/OPEN needs either a fix
   phase or a written, approved exception (release gate rule). No exception
   is valid without an owner, an expiry, and a tracking issue.
6. **Only then**: mark the Definition of Done complete and proceed to V1
   removal planning (separate approval, never bundled with cutover).

Standing blockers carried forward: `db/migrate.py` absent (manual `psql -f`
until built); V1 quarantine holds through all phases; commerce reverified
at every step.

---

# PART 4 — RECONCILIATION (post F4–F10, 307 tests green)

Added after Stages F4–F10 completed. Part 1 remains the historical baseline;
this part records what moved and what is left. Nothing below claims live
behavior — no production DB, streams, or traffic has touched V2.

## Closed at code level (live proof still pending F7/F11)

- A1 (orchestrator exists: F9a inbound→plan, F9b sent→learn), A3-part (F4
  send hook wired at both transport sites, flag-off default).
- R6-part, M1/M2/M6-part, C1–C8-part (guards/rails), S1-part (live
  `select_stage` loop in F9a).
- L1/L2/L4–L7-part (fake-store duplicate/crash/concurrent proofs).
- O1-part (trace data model + composer output).
- M3 (summary distiller), M7 (pgvector tier + fusion).
- R2/R3 (engagement/reciprocity/intimacy-trajectory derivations).

## Still remaining (all need staging infra and/or explicit approval)
1. **Live wiring**: orchestrator, send hook, worker hook have zero live
   callers with flags on. Run shadow, then controlled. Needs staging PG +
   Redis + approval.
2. **F7 live proofs**: duplicate/crash/concurrent/XAUTOCLAIM against real
   PG + streams; duplicate-send transport proof; failure injection;
   adversarial injection/poisoning suites.
3. **F8/F10 execution**: backfill + replay jobs built but unexecuted;
   need staging runs, reconciled counts, approval.
4. **F11 shadow window**: thresholds, scenarios A–G, commerce gate per
   step, go/no-go report.
5. **F12 cutover + shutdown**: flag flips, per-system legacy isolation,
   rollback drill — each approval-gated.
6. **F13 hardening**: dependency register, debug view (dossier composer
   landed as code first), prod log audit, latency profiling.7. **`db/migrate.py` absent**: 12 tables + 1 column have no runtime
   migration path; manual `psql -f` only.
8. **Part 3 re-audit**: full THEN-vs-NOW delta per row after 1–7; release
   gate stays NOT MET until then.

## Post-Part-4 code slices (O5-part closed at code level)
- **Dossier composer** (`services/dossier.py`): pure eight-section debug
  view (relationship/person/loops/episodes/patterns/intimate/commerce/
  recall) over `TurnContext` + store rows, bounded lines, deterministic.
  Dashboard route + prod log audit remain F13 open items.

## Doc-level closures (no infra required)

- **A4 closed**: `sunny_v2/DEPENDENCY_REGISTER.md` — every reused
  component with scope-of-use, non-inheritance proof, and safety argument;
  change rule for new dependencies.
- **F7 scoped**: `sunny_v2/LIVE_PROOF_MATRIX.md` — all live proofs
  (L1–L7, G1, A–G, commerce gate) specified with method, criteria, and
  execution record. Status SCOPED/NOT RUN; blocks cutover until green.

## Post-Part-4 F7-rest closure (2026-09-29, live staging)

- **F7 L7/L3/L5/G1 proven live** (`tests/test_f7_*_live.py`, 10 tests
  green on real PG + Redis, residue cleaned): XAUTOCLAIM reclaim →
  APPLIED → DUPLICATE with payload preserved and no auto-ACK;
  crash-between-persist-and-mark converges to one mutation + one mark;
  PG-down intake raises without writing; commerce-down turns assemble
  degraded and deterministic; injection/poisoning turns stored verbatim
  with zero messages/queue/scheduled/offers side effects; vault gate
  reserve→None→finalize; double-delivered send entry → one Telegram
  send with confirmed dedup and flat DLQ.
- **Transport restored to make the proofs possible**: `db/redis.py`
  stream/lease/debounce/DLQ/persona surface rebuilt (~50 fns, all
  test-pinned); `db/postgres.py` persona/sessions/scheduled/queue/
  durable/summary/telemetry fns; `db/dropfans.py` pool-direct +
  creator-scoped upserts (incl. `commerce.sale_recorded` privacy-shaped
  event); `db/fangate.py` blind-overwrite upsert replaced with scoped
  fallback; `chatbotv2.main` + `workers.*` import again.
- **Two real duplicate-send holes closed** (found by the live L3 proof):
  skip-branch ACK failure falling through to a send; unbounded
  `get_send_rate_limit_wait` stalling every repeat send 60s (now
  over-limit only); post-send failure routing aligned to the H4 delivery
  truth (ACK-then-persist, DLQ + repair, never send_failed).
- **Still open (explicit, out of F7 scope)**: V1-frozen worker/handler
  semantics (8 tests expect pre-cutover behavior); legacy
  dashboard-analytics postgres surface (37 names: notes/tags/analytics/
  telemetry-expansion); 3 media tests targeting dead inline code in
  `main.py`; unrecoverable migration bodies (documented in commerce
  spec Phase 3f).
