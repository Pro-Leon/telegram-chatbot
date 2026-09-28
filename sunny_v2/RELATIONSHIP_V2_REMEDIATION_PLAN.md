# Relationship V2 — Remediation & Completion Plan (Loosely-Implemented + Not-Built)

Status: ACTIVE. Owner: `relationship_v2/`. Authority: `sunny_v2/sunny_upgrade_v2.md` > `Sunny_Relationship_V2_Phased_Plan.md` > `00_DOCUMENT_INDEX.md` > domain contracts (`02`–`06`) > `12_DEFINITION_OF_DONE.md`. Execution: `Sunny_V2_Build_Instructions.md` + `11_OPENCODE_EXECUTION_RULES.md` (READ → AUDIT → PLAN → IMPLEMENT → VERIFY → AUDIT AGAIN → ACCEPT → REPORT → PROCEED). No loose code, no speculative infra, no silent fallback, commerce remains authoritative.

## Baseline (audit 2026-09-27)

Built and sound: `relationship_v2/domain/` (17 frozen Pydantic modules), `persistence/schema.sql` (9x `v2_*` tables), `persistence/repository.py` (9 owners, idempotent, fail-closed), `services/` (11 pure + owner modules: `relationship_context`, `memory_extraction/validation/retrieval`, `conversation_engine`, `context_assembly`, `escalation`, `response_engine`, `commerce_adapter` ports+fakes, `queue` lock helpers, `shadow` zero-I/O, `activation`, `retirement`), 12 phase unit tests. Correctly out-of-scope: no commerce writes, no provider imports, no second transport, no raw fan text in turns.

Loosely-implemented (placeholder rigor): regex memory extraction (`services/memory_extraction.py`), token-overlap retrieval (`services/memory_retrieval.py`), count-threshold lifecycle (`services/relationship_context.py`), fake-port commerce (`services/commerce_adapter.py`), helper-only queue (`services/queue.py`), synthetic-input shadow (`services/shadow.py`), policy-only activation/retirement.

Not-built (no runtime): event envelope + `event_processor`, `contradiction_resolver`, `episode_manager`, `pattern_learning`, `proactive_recall`, `absence_service`, `open_loops/patterns/intimate_history/threads/processed_events` tables + repos, DB wiring (`db/schema.sql` has 0x `v2_*`; `db/migrate.py` missing), real commerce/conversation/operator/event adapters, `context/assembler` DB-backed, `policies/`, `llm_worker.get_context()`, operator-sent-only semantics, per-fan lock integration, XAUTOCLAIM/idempotency, shadow harness, migration validator, replay, 9-stage cutover, legacy shutdown, observability/debug view, security gates.

Not-working: zero production importers of `relationship_v2` (only tests + self-imports); `SUNNY_V2_ENABLED=false`, `is_v2_enabled()` always False; `v2_*` never migrated in prod; no `FanMessageReceived → ResponseSent` path.

## Stage A — Foundation Remediation (Phases 0–4)

Goal: flags, event contract, wired DB, durable root. Protected: Telegram, Redis Streams, LLM/OneCall, commerce, operator queue/send, scheduler.

- A1 (first slice): `RELATIONSHIP_V2_ENABLED/READ/WRITE/SHADOW` in `core/config.py` + `core/architecture_router.py` helpers + `relationship_v2/domain/event.py` envelope (`event_id/event_type/fan_id/relationship_id/episode_id/message_id/generation_id/created_at/source/payload/schema_version`; types `FanMessageReceived, ResponseSent/Failed/Approved/Edited/Rejected, PurchaseCompleted/Refunded, OfferPresented/Accepted/Declined, ProductViewed, ContentDelivered, FanReturned, EpisodeStarted/Updated/Closed`; `ResponseGenerated` internal-only) + `.env.example` + `tests/test_relationship_v2_phase0.py`.
- A2: DB wiring — merge `v2_*` DDL into runtime migrations; add `v2_open_loops, v2_patterns, v2_intimate_history, v2_threads, v2_processed_events(event_id,processor UNIQUE)`; `up/down`, indexes, verification test; never edit legacy tables.
- A3: `services/event_processor.py` (receive → check `processed_events` → process → persist → mark processed, atomically where required) + repos for new tables; evidence-backed `FanRelationship` (no arbitrary LLM scores).

Acceptance: flags default off with documented purpose/activation/rollback; duplicate event = 1 mutation; `pytest tests/test_relationship_v2_phase0.py` + `ruff check` pass; post-audit confirms no legacy/commerce touch.

## Stage B — Memory Correctness (Phases 5–7)

Keep deterministic Track 1; add Track 2 LLM proposals (`memory_candidates/pattern_candidates` JSON) gated by `validation → confidence ≥0.60 → contradiction_resolver → memory_policy`; LLM never writes directly. Build `contradiction_resolver.py` (`ACTIVE/CURRENT` + `SUPERSEDED/HISTORICAL`, never latest-wins-only), `episode_manager.py` (`LIFE_EVENT` with `subject/event/expected_time/follow_up_candidate`), hybrid retrieval (lexical + pgvector, Tier1 always/Tier2 relevant/Tier3 callbacks, `TOTAL_BUDGET 6000`, provenance on every row).

Acceptance: `architect → photographer` preserves history; `hey just got home` recalls soccer game without keyword; full `extract → validate → normalize → persist → retrieve → render → use` demonstrated; memory tests (§39.1–39.2) pass.

## Stage C — Intelligence Layer (Phases 8–15)

In order: `open_loops` (`OPEN/DUE/REFERENCED/RESOLVED/EXPIRED/DISMISSED`), `pattern_learning` + negative learning (`confidence/pos/neg evidence`, no single-event `likes_teasing=true`), semantic `intimate_history` (themes/style/boundaries/turn-offs, abstract only), `relationship_reasoning.py` (Who?/How long?/Comfort?/Unfinished?/Away?), `episode_manager` (`current_topic/prev/threads/objective/emotional+intimate momentum`), `proactive_recall.py` (importance/recency/time-sensitivity/emotional/unresolved/novelty/follow-up/relevance → small coherent set, never full dump), `absence_service.py` (3d continue thread, 30d retain identity + generated re-entry).

Acceptance: continuity (new/known/48h/3d/30d), interaction +/-, intimacy shift/return/boundary tests pass; golden fixtures A–I reused.

## Stage D — Boundaries + Live Context (Phases 16–18)

Real `integration/commerce_adapter.py` (ports → preserved `commerce/`; returns `buyer_status/count/history/owned/available/eligible/active_offer/intent/post_purchase`; cannot invent price/product/eligibility/purchase/ownership/offer). DB-backed `context/assembler.py` typed sections (`WHO HE IS / WHAT YOU KNOW / WHAT HAPPENED / HOW HE RESPONDS / IMPORTANT NOW / OPEN LOOPS / CURRENT STATE / EPISODE / INTIMATE CONTINUITY / COMMERCE / CURRENT CONVERSATION`). `relationship_v2.get_context()` + minimal `workers/llm_worker.py` boundary (no full rewrite); LLM returns `reply/commerce_signals/confidence/needs_handoff`, OneCall validation stays.

Acceptance: commerce isolation tests (cannot invent price/mark purchased/bypass eligibility/seal/deliver) + context quality (relevant/compact/chronological/non-fabricated) pass; fixtures J–L pass.

## Stage E — Correctness Under Failure (Phases 19–20)

Operator semantics: Draft A/B ≠ event, discarded on reject; only `ResponseSent` (incl. operator-edited B) → memory/episode/pattern updates. Concurrency: `v2:lock:{creator}:{user}` integrated with existing per-fan lock for `inbound → approval → send → commit`; optimistic versioning for memory, pessimistic only for `generation → enqueue`. Idempotency: `v2_processed_events` + `(conversation,generation)` + `(creator,user,idempotency_key)` dedup; bounded backoff, `conversation.failed` DLQ; `XAUTOCLAIM`-safe.

Acceptance: operator (Draft A rejected = no mutation; edited B sent = actual event), retry/duplicate/crash, two-workers-same-fan tests = 1 logical transition; fixtures K–M pass.

## Stage F — Shadow → Cutover → Hardening (Phases 21–25)

Shadow harness outside package over live traffic (process/build/assemble/log diffs, no production control); metrics: accuracy/continuity/personalization/false-memories/irrelevant/size. Migration validator (legacy → candidate → high-confidence only); replay `Telegram → FanMessageReceived` only after live stable. 9-stage cutover (DB → ingestion → memory → shadow context → shadow LLM → live context → source-of-truth → disable legacy writes → remove legacy reads); commerce live throughout. Legacy shutdown (`commerce/relationship.py, relationship_trajectory.py, intimacy_trajectory.py, memory/profile.py, long_term_memory.py, fan_knowledge.py, memory/summarizer.py` READ/WRITE disabled; `ConversationState` transient only; `CommercialRelationshipState` split). Hardening: flags `V2_ENABLED/READ/WRITE=true, SHADOW=false`, independent commerce controls, rollback preserves V2 data, per-turn trace (`relationship/fan/event/episode/generation/memory/pattern/processor/timestamp`, `belief → memory → event → message`), debug view, security (isolation, injection/poisoning resistance, fail-closed, sanitized fixtures).

Acceptance: `12_DEFINITION_OF_DONE.md` all boxes or approved exception; final cutover checklist (`Sunny_V2_Build_Instructions.md:64`) + rollback tested; commerce + operator regression gates pass.

## Execution rules (every stage)

Small vertical slices (`domain → repository → service → persistence → test → integration`); scope lock (BLOCKER/REQUIRED vs OUT-OF-SCOPE → OPEN CONSIDERATIONS); golden fixtures A–M reused; property invariants (rejected ≠ sent; duplicate = 1 transition; supersede preserves provenance; memory cannot create purchase; LLM cannot authorize transaction); verification `pytest tests/test_relationship_v2_phase*.py`, `python -m compileall relationship_v2`, `ruff check`; sync-back report per phase; STOP on contradiction, missing DB contract, commerce/operator regression, or unverifiable concurrency — never guess.
