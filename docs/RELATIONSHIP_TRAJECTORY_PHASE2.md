# Relationship Trajectory — Phase 2 (Current-Turn Evidence Integration)

**Status:** Implemented, connected to runtime accumulation (no behavior change
outside the relationship namespace).
**Modules:** `commerce/relationship_evidence.py`, `workers/llm_worker.py`
(`_record_relationship_trajectory`)
**Tests:** `tests/test_relationship_evidence_phase2.py` (76 tests,
`pytest.mark.unit`)

Phase 2 connects current-turn behavioral evidence to the Phase 1 canonical
domain. Core principle: **Phase 2 extracts evidence; Phase 1 decides
relationship state.** The LLM observes (advisory only). The deterministic
relationship domain remains authoritative.

---

## 1. What Phase 2 adds

- `RelationshipTurnEvidence` is **current-turn behavioral evidence** (one
  object per processed turn). Phase 1 remains the deterministic state
  authority (bands, decay, provenance, persistence contract).
- `extract_turn_evidence()` (pure, no Redis/Postgres/LLM/worker at import)
  assigns each evidence field exactly once; see §3.
- `accumulate_relationship_turn_idempotent()` folds evidence into Phase 1
  anchors exactly once per `generation_id` (see §5).

## 2. Canonical accumulation unit

**Relationship trajectory accumulation occurs per processed turn, not per
raw Telegram message.** The worker's existing debounce intentionally
collapses rapid messages; the relationship path runs once per claimed
inbound turn and never reconstructs collapsed messages.

## 3. Normalization rules (one rule per field)

- `user_sent_message` — transport fact for the processed inbound turn; never
  from an LLM signal.
- `user_asked_question` — ONE canonical detector mirroring
  `core/conversation_state` (`?` suffix or 11 interrogative prefixes).
  The contract detector is wider on bare declaratives and the response-mode
  detector lacks `could you`; the conservative conversation_state semantics
  were chosen. `CommerceSignals.fan_asks_question` may corroborate but never
  substitutes — one message, one boolean.
- `user_answered_question` — existing `ConversationState` representation
  (`last_question` present and `last_question_answered`); semantics preserved,
  no new detector.
- `user_shared_information` — OR of the narrow explicit LTM extractor hit
  (`EXPLICIT` 1.0 only; weak inference excluded) and the explicit
  fan-knowledge pattern hit (`USER_EXPLICIT` at explicit confidence only).
  Multiple stores still yield one boolean. Post-hoc profile LLM deltas are
  never consulted; raw text/spans are never persisted.
- `user_continued_topic` — narrow provisional overlap rule over
  `ConversationState` topic/open-thread data. `maintain_topic` is never read
  (vacuously true). Requires the shared token in the current message, the
  state vocabulary, AND prior history, so one message cannot continue a
  topic it created. No semantic similarity; prefers false when uncertain.
  Provisional: `RELATIONSHIP_TOPIC_MIN_OVERLAP = 1`.
- `user_referenced_previous_context` — explicit relationship-specific
  threshold over existing retrieval output: ≥1 shared substantive token
  between the current message and a retrieved subject/value. Deterministic,
  named, provisional, boundary-tested
  (`RELATIONSHIP_RETRIEVAL_MIN_OVERLAP = 1`, distinct from the commercial
  `0.2` prefilter which is not reused). No embeddings, no new retrieval
  subsystem; absent evidence yields false.
- `assistant_asked_question` — actual outbound text via the same canonical
  detector; LLM intent never consulted. Failed generation/send yields false.
- `assistant_shared_information` — provisional planner-derived proxy ONLY:
  `conversation_mode == "share"` when available at the extraction point.
  Documented as proxy, not observation; no content classifier; unavailable
  mode yields false.
- `session_returned` — ONLY `ConversationLifecycle.RETURNING` (≥48h gap).
  No new timeout; LTM COMMITMENT `return` memories never substitute.
- `open_loop_continued` — existing OPEN_LOOP LTM evidence (relevant,
  still-OPEN loop). The dead `commerce.open_loop` module is never used; the
  real `commerce.long_term_memory` implementation is called directly.
- `open_loop_resolved` — return value of
  `long_term_memory.resolve_open_loop(creator_id, user_id, message)`.
- `llm_relationship_engagement` — validated `CommerceSignals` only (never
  raw `OneCallReply`; malformed input degrades to absent). Phase 1's exact
  rule applies (high threshold + substantive signal → informational counter
  only, never band promotion). Intent tags and all content signals
  (`content_interest/curiosity/request`, `explicit_content_request`, raw
  evidence strings) never enter relationship evidence.

## 4. Deduplication

The normalization layer owns deduplication: one inbound message produces one
`RelationshipTurnEvidence`. Deterministic + LLM question agreement is one
vote; dual disclosure hits are one boolean. Topic continuation and
open-loop continuation may both be true (distinct Phase 1 dimensions).

## 5. Idempotency design

The worker is at-least-once; Phase 1 accumulation is not inherently
idempotent. Runtime accumulation is protected by a generation-keyed
processed marker, checked and mutated atomically inside the existing
row-locked `mutate_user_profile_atomically` closure, in the existing
relationship namespace (`relationship_trajectory_by_creator` → this
creator's block). No separate Redis marker, no transitions-as-idempotency,
no check-then-write outside the mutation, no unbounded list.

- First `generation_id`: accumulate, record the id, prune to the bounded
  retention, persist only this creator's block.
- Repeat (redelivery/retry): no accumulation, no counter increments, return
  existing state.
- Bounded retention: the 20 most recent ids
  (`RELATIONSHIP_PROCESSED_IDS_MAX = 20`, chosen from the existing LTM
  per-creator bound of 20 items; provisional).
- Creator-scoped (inside this creator's block) and user-scoped (inside this
  user's row): survives retries via Postgres, never leaks across users or
  creators. Unrelated namespaces and other creators are preserved.
- The marker list lives alongside the Phase 1 anchor keys in the same
  block; Phase 1 readers ignore unknown fields, so the Phase 1 contract is
  byte-for-byte unchanged.

## 6. Runtime placement

`_record_relationship_trajectory()` runs inside `workers/llm_worker.py`
`process_message()` — after state/contract derivation, with history and
(validated, new-path-only) `OneCallResult` signals available — at three
kinds of sites: the main post-send/queue path, the two one-call failure
returns, and the suppressed path. It never runs in `post_process` (grain
would blur) or `handlers.py` (pre-debounce grain violation). The trajectory
state is never passed into commerce decisions, prompts, or objectives.

## 7. Failure / fail-open

Every step is fail-open: extraction failure, missing state, invalid LLM
signals, unavailable memory/LTM, anchor load failure, or persistence
failure never raises into the main path and never blocks response
generation, sending, commerce processing, or operator handoff. Generation
failure still records deterministic user-side evidence with LLM signals
absent. One UTC `now` threads through extraction → accumulation → snapshot
→ persistence metadata. A snapshot may be unavailable after persistence
failure; that is acceptable.

## 8. Explicit non-goals (untouched)

No intimacy/sexual/permission/boundary/content-interest/commercial state; no
purchase/pricing/offer/product/ranking/sealing/execution/desire/temperature/
readiness/window/objective changes; no prompt/strategy/optimizer changes; no
new DB tables, Redis state, LLM schemas, backfill, or semantic topic models.
