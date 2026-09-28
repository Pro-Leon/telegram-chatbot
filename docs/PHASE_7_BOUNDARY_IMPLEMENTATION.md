# Phase 7 — Boundary, Refusal & Recovery State (Implementation)

Separate deterministic domain for user-established conversational
boundaries. Companion to the Phase 7 audit; this document records what
was built, the exact rules, and the deliberate limitations.

## Non-goals (enforced by absence)

No `permission_score`, `consent_score`, `sexual_readiness`,
`age_verified`, or adult gate. No changes to
`commerce/relationship.py`, `relationship_trajectory.py`,
`intimacy_trajectory.py`, `intimacy_evidence.py`, `desire.py`,
`temperature.py`, `readiness.py`, `offer_readiness.py`,
`conversation_strategy.py`, `persona_behavior.py`, `CommerceSignals`,
flat profile keys, or ordinary LTM memory. No new worker, queue, table,
migration, Redis structure, or dependency. `ResponseMode.TEASE` not
revived; no new strategy moves.

## Modules (new)

- `commerce/boundary_evidence.py` — pure current-turn extractor.
  Seven assertion families (directive + constrained behavior, all
  `\b`-bound) plus explicit-permission relaxation families, an
  ambiguous-only tier (bare "stop", "that's too much"), a
  `don't forget ...` clause guard, a quoted third-party-speech guard,
  ellipsis normalization, and a temporary qualifier
  ("right now / for now / ..."). Ordinary positive conversation never
  relaxes. Same-turn assertion wins over relaxation per type.
- `commerce/boundary_state.py` — durable constraints in
  `user_profiles.facts["boundary_state_by_creator"][creator_id]`
  (schema v1, ≤7 records, processed IDs bounded at 20) via
  `mutate_user_profile_atomically()`. Scopes:
  MODE (flirting/pet-name/questions), TOPIC (sexual/change-topic),
  CONVERSATION (stop), CONTACT (do-not-contact). Persistent by default;
  only temporally-qualified TOPIC constraints and CHANGE_TOPIC expire
  (24h, documented); RECOVERING is read-time derived inside the last 6h
  of a temporary window; CLEARED only via explicit relaxation.
  `BoundarySnapshot` exposes active/recovering/expired + `degraded`.
  `boundary_blocks_commerce()` vetoes offers for
  NO_SEXUAL_TOPIC/CHANGE_TOPIC/STOP_CONVERSATION/DO_NOT_CONTACT (and any
  unknown/degraded state); manner constraints govern realization only.
- `commerce/boundary_validation.py` — pure output-text checks per type
  (pet-name/flirt/sexual lexicons; any `?` under NO_PERSONAL_QUESTION
  or CHANGE_TOPIC; brief/no-question/no-commerce close under
  STOP_CONVERSATION; any outbound under DO_NOT_CONTACT) plus fixed safe
  templates (neutral continue, brief close; None/suppress for contact),
  all pinned to pass the validator.
- `context_engine/boundary_context.py` — read-only selection over
  durable + current evidence (current wins), bounded rendering
  (`BOUNDARY CONTEXT [DERIVED]`, ≤120 tokens, no raw wording, no
  scores, explicit precedence over persona style), and
  `constrain_strategy_for_boundary()` (existing moves only:
  STOP/CONTACT → safe-default ACKNOWLEDGE; barred questions →
  no-question variants).

## Carriers & choke points (modified)

- `context_engine/models.py` — `AuthoritativeState.boundary_context_text`.
- `core/context_compact.py` — canonical order RELATIONSHIP → INTIMACY
  → BOUNDARY → STRATEGY → PERSONA BEHAVIOR (empty renders nothing).
- `core/one_call.py` — `OneCallResult.boundary_violations/boundary_action`;
  `validate_one_call_response(..., boundary_active=...)` corroborates
  handoff on violation (optional; worker choke is authoritative).
- `core/routing.py` — `decide_routing(..., boundary_violation=...)` →
  QUEUE reason `boundary_violation`, precedence 2 (after invalid_output).
- `workers/llm_worker.py` — per turn: extract → idempotent accumulate
  (current evidence enforced from memory on write failure) → CONTACT
  suppression (no generation/queue/send) → sealed-candidate veto →
  boundary context carrier → strategy constraint → PPV/free-photo/
  commerce-draft veto → common post-generation choke (validate text,
  swap safe completion, flag, force QUEUE; STOP forces QUEUE; degraded
  fails closed to QUEUE). Covers OneCall, commerce, agent, and legacy
  paths at one choke.
- `workers/scheduler_worker.py` — scheduled sends suppressed under
  durable CONTACT/STOP boundaries; unreadable boundary state suppresses
  (fail-closed for autonomous outbound).

## Failure model

- Load failure → `degraded` snapshot → QUEUE (operator review), commerce
  vetoed. Never silent auto-send.
- Write failure → current-turn evidence still enforced from memory.
- Validation failure → safe completion + QUEUE (contact → suppress).
- Telemetry: existing `handoff_reason`/flags carry
  `boundary_violation:<type>` / `boundary_do_not_contact`; no new
  telemetry schema (documented §35 decision).

## Limitations (explicit)

1. Debounce latest-only: a boundary in a superseded buffered message of
   the same 3s window is not accumulated until re-asserted (all inbound
   remains in the DB audit log).
2. CHANGE_TOPIC output check is question-based (no old-topic tracking
   per data minimization); enforcement is context + strategy + queue.
3. "don't call me that" maps to NO_PET_NAME without assistant-history
   corroboration (conservative).
4. Bare "I'm not comfortable with this" maps to NO_FLIRTING +
   NO_SEXUAL_TOPIC (neutral continuation passes both; documented
   low-cost over-breadth).
5. Bare "that's too much" is ambiguous-only per spec adversarial H
   (never asserts alone).

## Tests

`tests/test_phase7_boundary.py` — 103 tests: evidence (assertions,
false positives, relaxation), durable state (activation, reaffirmation,
relaxation, conflicts, expiry/recovery, isolation, idempotency,
bounds), precedence, context ordering/leak checks, strategy constraint,
persona non-authority, validation/safe completion, routing, one_call
hook, commerce veto, Phase 1–6 untouched guards, adversarial A–H,
debounce/retry, legacy parity.
