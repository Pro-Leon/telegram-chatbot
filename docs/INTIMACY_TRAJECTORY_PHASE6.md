# Intimacy Trajectory - Phase 6: Descriptive Intimacy Architecture

> Phase 6 adds a bounded deterministic intimacy-trajectory layer beside
> the Phase 1 relationship trajectory. It creates no permission system,
> no consent authorization, no sexual-response policy, no adult gate,
> no refusal/boundary state, and no commerce behavior. Phases 1-5
> contracts are unchanged.

```text
Intimacy trajectory is descriptive state.
It is not permission, consent, safety policy, or commerce authority.
```

## 1. Purpose

Allow the system to retain useful knowledge of conversational
intimacy even after the relevant interaction has fallen outside the
recent-message window - without dumping history, without a second
retrieval engine, without giving the LLM authority over state, and
without ever treating observed intimacy as permission.

A parallel goal: keep the Phase 4 production wiring intact while
giving strategy access to genuine current-turn intimate-context
references through the existing evidence shape (the same narrow
bridge pattern Phase 5 established).

After Phase 6:

* durable intimacy trajectory is accumulated once per processed turn,
  creator-scoped, bounded, and fail-open;
* intimacy context reaches the production OneCall path between
  relationship context and strategy;
* the same selected intimacy context informs Phase 4
  previous-context evidence only when real current-turn reference
  exists - without a new strategy move, hint, or priority;
* legacy and OneCall paths use the same underlying selection.

## 2. Architecture

```text
CURRENT TURN (user message, once, dominant)
        ↓
Conversation State / Contract (once-derived, authoritative transient)
        ↓
Existing retrieval (LTM + fan knowledge, existing signatures)
+ descriptive relationship bands (Phase 1, read-only)
+ descriptive intimacy bands (Phase 6, read-only)
        ↓
ONE bounded deterministic intimacy selection
(context_engine/intimacy_context.py)
        ↓
ONE bounded data-only representation (INTIMACY CONTEXT [DERIVED])
        ↓
+-- legacy ``context`` list (append; parity) --------------+
+-- authoritative snapshot field --> OneCall compact path -+
        ↓
Phase 4 strategy (intimate reference ORs into the existing
referenced-previous-context boolean; no new input, no new move)
        ↓
OneCall / legacy LLM (realization only)
```

Persistence flow (once per processed generation):

```text
one processed generation
        ↓
extract_intimacy_evidence (pure, current-turn only)
        ↓
accumulate_intimacy_turn_idempotent (atomic, bounded marker list)
        ↓
facts["intimacy_trajectory_by_creator"][str(creator_id)]
```

## 3. State dimensions

Exactly five independent descriptive dimensions
(`commerce/intimacy_trajectory.py::INTIMACY_DIMENSIONS`):

* `romantic` - missing/thinking-of-you/romantic conversational signals
* `playful` - teasing/flirting laughter signals (conversational play,
  never sexual permission)
* `emotional` - trust/safety/vulnerability disclosure signals
* `sexual_conversation` - sexual-conversational propositions and
  corroborated desire-adjective pairs (observed conversation only)
* `intimate_continuity` - current intimate signal with prior intimate
  airing in history

Each dimension uses four bands: `unknown / low / steady / deep`.
There is no aggregate intimacy score and no HIGH/VERY_HIGH vocabulary.
Bands describe accumulated conversational evidence. They do NOT mean
permission, consent, authorization, readiness, adult status, or
commercial interest.

## 4. Evidence

`commerce/intimacy_evidence.py::extract_intimacy_evidence` is pure and
conservative. Exactly one rule assigns each `IntimacyTurnEvidence`
field:

* `romantic_signal` - narrow romantic patterns (word-boundaried).
* `playful_signal` - teasing/flirting/laughter patterns. The existing
  transient `tone` heuristic may corroborate playfulness but is never
  its sole authority (and never any authority for sexual signals).
* `emotional_signal` - trust/safety/vulnerability patterns.
* `sexual_conversation_signal` - one unnegated strong-pattern hit OR
  at least two *distinct* unnegated soft tokens
  (`sexy/horny/naughty/aroused/arousal`) in the same message. One
  ambiguous word alone never sets it, and a negated proposition
  ("don't kiss me", "never touch me") never sets it: a bounded
  per-match negation guard examines the governing clause segment
  (punctuation and contrast conjunctions bound governance; a short
  emphatic insert such as "never, ever" looks through one
  punctuation boundary only). `hot / beautiful / gorgeous / babe /
  photo / pic / picture` are explicitly excluded (word boundaries
  make `photo -> hot` impossible by construction).
* `intimate_continuity_signal` - current intimate signal AND prior
  intimate vocabulary in history (co-occurrence input to the durable
  counter only).
* `prior_intimate_context_reference` - current intimate signal AND
  prior intimate vocabulary AND substantive token overlap (at least
  one shared non-stopword token, Phase 5 overlap philosophy,
  intimate-scoped) between the current message and the prior intimate
  texts. Unrelated co-occurrence ("I miss you" history + "lol funny"
  now) is NOT a reference; topic-mediated linkage (continuity on a
  currently intimate topic/thread) additionally qualifies at
  selection time.
* `user_initiated_intimacy` / `assistant_intimacy_continuation` /
  `current_intimate_topic` - initiation/continuation/topic flags from
  already-available turn data.
* `llm_content_interest` / `llm_explicit_content` - validated
  advisory-only corroboration inputs (see section 12).

Historical trajectory alone never generates current-turn evidence: a
DEEP band with an ordinary current message yields all-False signals.
Per-dimension booleans deduplicate: repeated hits in one turn count
once.

Provenance per source (mirrors Phase 1 vocabulary):

* strong sexual/romantic lexical hit -> `explicit` (1.0)
* >= 2 dimension signals in one turn -> `strong_inference` (0.8)
* user turn bookkeeping -> `system_event` (0.9)
* otherwise -> `weak_inference` (0.5)

Weak inference alone never moves a band: entry requires repeated
corroborated observations (LOW >= 2).

## 5. Persistence

Namespace: `facts["intimacy_trajectory_by_creator"][str(creator_id)]`
in `user_profiles` - separate from
`relationship_trajectory_by_creator`. Same `*_by_creator` key
conventions (str keys for JSON, int keys tolerated on read).

Stored per creator block (minimum contract):

```text
schema_version (= 1)
observation_count
romantic/playful/emotional/sexual_conversation/intimate_continuity
  counts + last_observed timestamps
corroborated_observations (informational only)
last_provenance / last_source
bands ({dimension: value})
transitions ({dimension: {from, to, at, reason}}, bounded)
processed_generation_ids (bounded marker list, <= 20)
```

Mechanism: `mutate_user_profile_atomically` row-locked mutation;
marker check + accumulation + prune inside the closure (no Redis
marker, no check-then-write outside). No new table, no Redis state,
no queue, no lock. Fail-open everywhere: persistence failure returns
`(None, False)` and conversation continues.

Never persisted: raw user messages, raw LLM outputs, sexual text,
prompt fragments, permission decisions, commerce state, adult status.

## 6. Transition policy

`apply_intimacy_transition_policy` (sole committer, called once per
accumulation by `accumulate_intimacy_turn`):

* independent dimensions; counter-based candidates from updated
  counters; committed bands are the clamp baseline only;
* at most one band step upward per processed turn
  (`UNKNOWN -> LOW -> STEADY -> DEEP`; never `UNKNOWN -> DEEP`);
* entry thresholds (absolute corroborated counts):
  LOW >= 2, STEADY >= 6, DEEP >= 12;
* no invented durable decline (counters never regress here);
* LLM inputs promotion-inert (corroboration counter only);
* neutral turns count as observations, move nothing, erase nothing,
  grant nothing, trigger nothing.

## 7. Decay

Readout-only decay mirrors Phase 1: when `days_since_last_seen >=
30.0` (`DECAY_DORMANT_DAYS`, same conservative horizon), the derived
snapshot downgrades each band one step for readout. Anchors are never
mutated by decay; renewed intimate conversation re-warms readout
through new accumulation. Single `now` authority supplied by the
caller (worker); no second clock.

## 8. Snapshot

`derive_intimacy_snapshot(anchors, turn_evidence, now)` returns
`IntimacySnapshot`: five bands + `days_since_last_seen` +
`decay_applied` + `active_signals_this_turn` + `computed_at`.
Raw counters, timestamps, provenance, generation IDs, commerce
state, permission state, adult status, refusal state, and raw sexual
text are never exposed.

## 9. Context rendering

`context_engine/intimacy_context.py`:

* `select_intimacy_context(snapshot/bands, evidence)` - pure;
  reference flag requires genuine current-turn linkage
  (overlap-proven `prior_intimate_context_reference`, or continuity
  on a currently intimate topic/thread); bands alone, current
  intimacy alone, and unrelated co-occurrence never set it.
* `render_intimacy_context` - emits e.g.:

```text
INTIMACY CONTEXT [DERIVED]:
romantic: steady
playful: low
```

Unknown bands omitted; empty selection renders `""`
(byte-identical abstention). Deny-list discipline (permission,
commerce, safety-policy, escalation, directive vocabulary) pinned by
tests; unknown dimension names dropped. Soft token ceiling
`MAX_INTIMACY_TOKENS = 120`, carved from the existing
advisory/context budget - the relationship 200-token budget and the
global budget are unchanged.

## 10. Phase 4 bridge

Phase 4 (`commerce/conversation_strategy.py`) is untouched: same
hierarchy, vocabulary, priorities, hints (no `tease`, no new moves).
The worker ORs the intimacy reference into the existing turn
evidence:

```text
referenced = retrieval_backed_evidence
           OR relationship_selection.has_prior_context
           OR intimacy_selection.has_intimate_reference
```

`has_intimate_reference` requires current-turn support, so bands
alone never influence strategy. `ResponseMode.TEASE` is not revived;
`teasing_allowed` is not reinterpreted.

## 11. Commerce isolation

Phase 6 imports no commerce funnel state: `desire`, `temperature`,
`readiness`, `offer_readiness`, and `commerce/relationship.py` are
never imported for intimacy authority (pinned by tests). There is no
path `intimacy -> desire / temperature / readiness / offer / PPV /
price / ranking / sealing / execution`. The existing
`content_interest -> temperature -> readiness -> commerce` pipeline is
untouched. Validated `CommerceSignals` fields may be *observed* as
corroboration-only advisories (never authority); in particular
`explicit_content_request` alone never creates intimacy evidence.

## 12. Adult-gate limitation

```text
No adult gate implemented.
No sexual-response policy implemented.
```

No `age_verified` / `adult` / `is_adult` field exists in the Phase 6
namespace, and none may be added there. Adult status is never inferred
from language, profile age, sexual behavior, purchase history,
persona assumptions, or LLM classification. Phase 6 therefore remains
descriptive: it records that intimate conversation happened; it never
authorizes any response to it.

## 13. Phase 7 boundary/refusal handoff

Phase 6 implements no refusal state, sexual boundary state, consent
state, de-escalation memory, or sexual safety state. Compatibility
guarantees for the future layer:

* intimacy bands are readable descriptive inputs a refusal layer may
  consult, but nothing in Phase 6 preempts, delays, or weakens a
  refusal decision;
* fail-open intimacy paths never convert safety/routing outcomes;
* the deny-listed renderer never emits permission-like language that
  could be mistaken for a grant;
* neutral/ordinary turns are always representable and never blocked.

## 14. Failure model

Every Phase 6 operation fails open: bad evidence input -> neutral
evidence; bad anchors -> neutral; selection/render failure -> empty
context (`""`); snapshot attach failure -> logged, generation
continues; persistence failure -> `(None, False)`, redelivery retries
under the same `generation_id`. Intimacy failures never become
commerce failures, safety failures, or send blockers. Existing
safety/routing/capability systems remain authoritative.

## 15. Creator isolation

All durable intimacy values are `(creator_id, user_id)`-scoped via
`intimacy_trajectory_by_creator`. Flat profile scalars are never
used. Cross-creator tests pin that creator A's intimacy never appears
in creator B's selection, accumulation markers are per-creator, and
assembly never mutates the input profile.

## 16. Testing

* `tests/test_intimacy_trajectory_phase6.py` - dimensions, bands,
  transitions, clamp, neutral turns, decay, snapshot hygiene,
  persistence contract, creator isolation, forbidden semantics.
* `tests/test_intimacy_evidence_phase6.py` - category evidence,
  false positives (`hot/beautiful/gorgeous/babe`, `photo -> hot`,
  word boundaries), continuity rules, LLM isolation
  (LLM-only moves nothing; content-interest/explicit-content
  isolation), dedup, provenance, fail-open, idempotent accumulation.
* `tests/test_intimacy_context_phase6.py` - selection, rendering
  hygiene (deny-list, no counters/permission/commerce), assembly,
  creator isolation, OneCall ordering
  (`RELATIONSHIP -> INTIMACY -> STRATEGY -> PERSONA`), Phase 4
  compatibility, commerce/adult isolation, Phase 5 preservation.

## 17. Explicit non-goals

Phase 6 does not implement sexual/intimacy permission, consent
authorization, sexual-response policy, explicit escalation rules,
adult verification, refusal/boundary/consent state, de-escalation
memory, commerce integration, purchase/product/pricing/offer logic,
readiness, desire, temperature, ranking, sealing, execution,
optimizer learning, new relationship dimensions, new tables, new
Redis structures, new vector stores, new LLM decision engines, new
strategy engines, LLM-controlled state/policy, or historical
backfill.
