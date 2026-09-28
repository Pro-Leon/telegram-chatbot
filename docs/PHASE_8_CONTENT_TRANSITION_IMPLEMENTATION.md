# Phase 8 — Natural Content Interest Transition: Implementation

## 1. Objective

Introduce a small deterministic conversational content-interest
transition layer that lets genuine user content curiosity / request /
continuity be acknowledged and bridged naturally, without turning
relationship warmth, intimacy, sexual conversation, desire, or
temperature into automatic sales triggers.

Non-goals (enforced by absence, pinned by tests):

* NOT a commerce redesign (no decision/eligibility/opportunity/
  selection/pricing/execution change).
* NOT a new content-selection authority (no media/price/PPV surface).
* NOT a sexual escalation engine.
* NOT a new conversation-strategy engine (Phase 4 untouched).
* NOT a desire.py fix (the DESIRE@0.0 shadowing defect is untouched).

## 2. Audit verification (pre-implementation, all held)

* Phases 1–7 present and functional (`relationship_trajectory`,
  `relationship_evidence`, `relationship_context`, `intimacy_trajectory`,
  `intimacy_evidence`, `intimacy_context`, `boundary_state`,
  `boundary_evidence`, `boundary_context`, `boundary_validation`,
  `conversation_strategy`).
* Relationship trajectory descriptive/durable; intimacy trajectory
  descriptive/durable (no commerce imports either way).
* Boundary state authoritative (`boundary_blocks_commerce` veto +
  common output validator in the worker).
* Phase 4 strategy canonical (sole move selector).
* Commerce authority deterministic (`decision.py` + `purchase_intent.py`
  verifiers AND-gated in `signals_to_context`).
* Content signals advisory/turn-scoped (`CommerceSignals`,
  `low_information` fallback).
* No canonical content-transition layer existed (grep: zero hits).
* `commerce/open_loop.py` absent (the worker's `from commerce.open_loop
  import ...` at `llm_worker.py:1215` is dead inside try/except).
* `ConversationContract.maintain_topic` unsuitable as continuity
  authority (vacuously true whenever `current_topic` exists,
  `conversation_contract.py:388-396`) — never consumed.
* `unified_intelligence.py` noncanonical/dead (only tests/benchmarks
  import it; zero production imports).
* `content_interest` / `content_curiosity` / `content_request` /
  `explicit_content_request` / `purchase_intent` retain audited
  semantics.
* `desire.py:110` vs `:112` shadowing defect still present and
  intentionally untouched (the selector consumes no desire stage).

## 3. Architecture

```
CURRENT USER CONTENT INTEREST
        |
CONTENT TRANSITION SELECTOR (commerce/content_transition.py)
        |
NATURAL CONVERSATIONAL GUIDANCE (context_engine/content_transition_context.py)
        |
LLM REALIZATION
        |
EXISTING DETERMINISTIC COMMERCE WHEN ELIGIBLE (unchanged)
```

New modules:

* `commerce/content_transition_evidence.py` — pure deterministic
  current-turn evidence extractor. Bounded booleans only:
  `explicit_request`, `curiosity`, `access_question`,
  `thread_continuation`, `purchase_intent`, `current_disinterest`.
  Accepts NO LLM signals parameter (structural LLM neutrality).
  Purchase/price detection delegates to the existing deterministic
  verifiers (`is_explicit_purchase_request`, `is_price_inquiry`).
  Thread linkage uses content-token overlap over `current_topic` /
  `open_threads` / caller-supplied LTM open-loop subjects only.
* `commerce/content_transition.py` — pure deterministic selector.
  Closed vocabulary: `NONE | ACKNOWLEDGE_ONLY | BRIDGE |
  DEFER_TO_COMMERCE`. Accepts only `(evidence, boundary_snapshot)`:
  relationship / intimacy / desire / temperature / history / inventory
  cannot activate a transition by construction (pinned by signature
  and AST-import tests).
* `context_engine/content_transition_context.py` — bounded renderer +
  fail-open assembler. Renders only for non-NONE decisions:

```
CONTENT TRANSITION [DERIVED]
- transition: NONE | ACKNOWLEDGE_ONLY | BRIDGE | DEFER_TO_COMMERCE
- user_interest: NONE | CURIOSITY | REQUEST | CONTINUATION | PURCHASE
- realization: natural | direct_response | commerce_handoff
- no_offer_from_warmth: true
```

Runtime placement (`workers/llm_worker.py`, between the Phase 7
boundary block and the Phase 4 strategy block): evidence is extracted
from the raw turn plus `_conv_state` topics/threads, LTM open-loop
subjects from the already-extracted explicit memories, and
prior-context linkage reused from the existing Phase 5 relationship
selection (no second retrieval). The block is appended to the legacy
`context` list AND carried on
`AuthoritativeState.content_transition_context_text` (new field in
`context_engine/models.py`) so the canonical OneCall path receives
identical guidance via `phase5_snapshot_blocks` in
`core/context_compact.py`. Canonical order is now RELATIONSHIP →
INTIMACY → BOUNDARY → CONTENT TRANSITION → STRATEGY → PERSONA
BEHAVIOR → PLAYER.

## 4. Selector precedence (exact)

1. No usable evidence / no current interest → NONE
2. Current content disinterest → NONE (history cannot override: it is
   not an input)
3. Boundary veto or unknown/degraded boundary → NONE
   (`NO_SEXUAL_TOPIC`, `CHANGE_TOPIC`, `STOP_CONVERSATION`,
   `DO_NOT_CONTACT`; manner-only constraints pass through to Phase 7)
4. Explicit purchase intent or price/access inquiry →
   DEFER_TO_COMMERCE (PURCHASE / commerce_handoff)
5. Explicit content request → DEFER_TO_COMMERCE (REQUEST /
   commerce_handoff)
6. Curiosity + thread continuation → BRIDGE (CONTINUATION / natural)
7. Thread continuation alone → BRIDGE (CONTINUATION / natural)
8. Curiosity alone → ACKNOWLEDGE_ONLY (CURIOSITY / direct_response)
9. Otherwise → NONE

## 5. Anti-funnel

Warmth, intimacy, sexual conversation, desire, temperature,
persona teasing permission, historical interest, and product
inventory are not selector inputs. `"you're driving me crazy
tonight"` → NONE. `"tell me about your day"` on a warm thread →
NONE. Curiosity (`"what kind of pictures do you take?"`) →
ACKNOWLEDGE_ONLY, never an offer. `no_offer_from_warmth: true` on
every decision.

## 6. Boundary integration

Phase 7 authoritative and unmodified. The selector consumes the
already-computed effective snapshot read-only. Unknown/degraded state
fails closed (NONE for guidance; the pipeline stays fail-open and
downstream commerce evaluation still runs independently).

## 7. Available content

Runtime inspection proved the canonical OneCall path injects NO
inventory (no CONTENT-category gatherer exists), while the legacy
`memory/context.py` path injected `AVAILABLE CONTENT` top-2 titles
unconditionally whenever valid products existed. Per the spec's
dormant-reference option (minimal, no selection change):

* The legacy line now carries `reference only — do not mention or
  offer unless the fan explicitly asks about content`, and the legacy
  system-prompt priority note matches.
* Commerce selection/ranking untouched; inventory presence never
  activates a transition (selector takes no inventory input; pinned by
  the products-without-interest NONE regression test).

## 8. Commerce integration (upstream only)

signals → content transition → conversational generation → existing
commerce evaluation/authorization → opportunity eligibility → ranking
→ sealing → execution. Phase 8 performs no commerce: `DEFER` hands
the turn to the existing free-photo/commerce paths, which still own
quota, duplicate prevention, approved-media checks, pricing,
eligibility, sealing, and execution. No protected file modified
(verified in diff discipline).

## 9. No new strategy move, no persistence

No `conversation_strategy.py` change, no new move, no durable Phase 8
state (current-turn evidence + existing snapshots only).

## 10. Tests

`tests/test_phase8_content_transition.py`: 60 deterministic tests,
no LLM/DB/Redis. See the final implementation report for the full
matrix and regression results.

## 11. Desire defect (intentionally excluded)

`desire.py` untouched. The new selector does not consume desire-stage
classification, so the `:110` vs `:112` CURIOSITY-shadowing ordering
defect is out of scope and preserved byte-for-byte.
