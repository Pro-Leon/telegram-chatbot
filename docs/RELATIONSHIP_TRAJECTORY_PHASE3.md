# Relationship Trajectory — Phase 3 (Deterministic State Transitions)

**Status:** Implemented as explicit, test-pinned policy over the existing
transition mechanism (no behavior change to accumulation semantics).
**Modules:** `commerce/relationship_trajectory.py`
(`apply_transition_policy`, sole committer `accumulate_turn`)
**Tests:** `tests/test_relationship_transitions_phase3.py` (90 tests,
`pytest.mark.unit`)

Phase 3 makes the transition policy explicit, deterministic, test-pinned,
and safe. The durable transition mechanism already lived inside
`accumulate_turn()`; Phase 3 extracts it verbatim into the named pure
function `apply_transition_policy()` and pins every rule with boundary
tests. No second transition engine was created.

The implemented contract in `commerce/relationship_trajectory.py`
supersedes stale roadmap sketches (rapport scalars, COOLING/RECOVERING/
UNCERTAIN trend labels, intimacy/boundary events), which are not part of
the architecture and were not introduced here.

---

## 1. Transition policy

`RelationshipTurnEvidence → deterministic counters → deterministic
candidate bands → existing committed band baseline → at most one upward
band transition per accumulation → deterministic transition record →
atomic persistence through the existing Phase 2 mutation.`

- **Independent dimensions.** Familiarity, engagement, reciprocity, and
  continuity transition independently. One dimension reaching a candidate
  threshold never directly modifies another; shared raw evidence
  contributes only through the existing per-dimension counter mapping
  (e.g. one rich turn moves familiarity and engagement on the same
  accumulation without either causing the other).
- **Counter-based candidates.** Candidate bands derive from counters via
  the existing `_derive_*` functions, never from the committed band. The
  committed band is the baseline/clamp state only.
- **One-step upward promotion.** A persisted band advances at most one
  level per accumulation, however many signals the turn carries. Entry
  path per dimension: `unknown → new/low/low/sparse`; then
  `new → familiar → established`, `low → steady → deep`,
  `low → balanced → high` (HIGH additionally gates on ≥5 user messages),
  `sparse → anchored → rich` (RICH needs 3 anchor kinds or ≥3 returns
  with ≥2 kinds, and always takes ≥3 turns from cold).
- **No invented durable decline.** Downward movement is intentionally
  unclamped per the module contract (disengagement evidence applies
  immediately). In practice only reciprocity ratios can regress committed
  bands (e.g. HIGH → BALANCED when assistant moves dilute the share to
  ≤0.75; HIGH → LOW in one accumulation only from a matching candidate,
  characterized by test). Familiarity/engagement/continuity candidates
  derive from cumulative counters and never regress on volume; neutral
  turns reset only the positive streak, never a band. No decline evidence
  model, hysteresis, or cooling vocabulary was added.
- **Trend stays derived.** GROWING requires a 3-turn corroborated streak
  (≥2 behavioral signals per turn); any bare-message turn resets the
  streak to STABLE; dormancy (≥30d) reads DECLINING without touching
  anchors; returns re-warm through ordinary accumulation. The trend label
  is recorded in transition records for explainability but never
  committed as an authoritative band (`bands` holds exactly the four
  persisted dimensions).
- **LLM promotion-inert.** The policy function reads no LLM-derived
  input. `relationship_engagement` continues to update only the
  informational corroboration counter under the exact Phase 1 rule; high
  signal without substantive behavior yields fully identical anchors.

## 2. What Phase 3 did not change

No new thresholds, bands, evidence fields, durable fields, or scores.
Schema stays v1 with the identical serialized field set; the Phase 2
processed-marker behavior is untouched; dormancy decay stays
readout-only; lifecycle semantics (`derive_lifecycle`, 48h) are untouched
— session returns arrive only as Phase 2 booleans and earn no bonus jump
(a RETURNING turn from cold lands on entry levels). Time flows from the
single caller-supplied UTC-aware `now` (naive coerced to UTC; no
wall-clock calls in transition logic). Transition reasons keep the
bounded `accumulated:<fields>` vocabulary; same inputs give the same
record, and replays return the existing record unchanged.

## 3. Boundaries preserved

`(creator_id, user_id)` isolation (interleaved generations, markers,
bands, and transition records verified independent); fail-open
persistence; no raw user/assistant/LLM text in durable state (the
accumulation boundary accepts no text parameter at all); complete
independence from commerce lifecycle state, prompts, objectives,
response strategy, optimizer, intimacy/sexual/permission/boundary
content, new tables, migrations, Redis, and caches.
