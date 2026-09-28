# Relationship Trajectory — Phase 1 (Canonical Deterministic Domain)

**Status:** Implemented, unconnected to runtime consumers (no behavior change).
**Module:** `commerce/relationship_trajectory.py`
**Tests:** `tests/test_relationship_trajectory_phase1.py` (41 tests, `pytest.mark.unit`)

Phase 1 implements exactly four concerns: typed contract, deterministic
derivation, durable creator-scoped anchor persistence, and tests. No prompt,
objective, commerce, intimacy, or permission behavior was changed.

---

## 1. Why `commerce.RelationshipState` is not reused

`commerce/relationship.py:17-34 RelationshipState` is a **commercial
lifecycle classifier**: `funnel_stage + purchase history + recency +
segments` → `cold/new/engaged/warm/buying_signal/purchased/repeat_buyer/
vip/cooling_down/do_not_push/operator_required`. Six of eleven values name
purchase outcomes; `warm/engaged` are defined by recency/funnel thresholds;
the values gate pricing-adjacent decisions, tip eligibility, pressure, and
cooldowns, with tests pinning that behavior.

Redefining any of those strings to mean human rapport would silently alter
when the system sells. The new domain therefore uses a distinct namespace
(`RelationshipTrajectory` / `RelationshipSnapshot`) and vocabulary, coexists
explicitly, and shares no imports with decision/ranking/sealing/execution.
`commerce/relationship.py` is untouched.

## 2. What the new trajectory means

Per `(creator_id, user_id)` grain (never global, never cross-creator):

- **Familiarity** (`unknown/new/familiar/established`) — accumulated
  interaction history and continuity. NOT affection, trust, or permission.
- **Engagement** (`unknown/low/steady/deep`) — behavioral participation
  depth over multiple interactions. NOT psychological interpretation and
  NOT the LLM `relationship_engagement` float (never persisted).
- **Reciprocity** (`unknown/low/balanced/high`) — balance of fan vs
  creator-side participation. NOT emotional commitment.
- **Continuity** (`unknown/sparse/anchored/rich`) — presence of durable
  conversational anchors (returns, open loops, prior-context references).
- **Trend** (`unknown/declining/stable/growing`) — recent movement
  direction. GROWING requires a corroborated multi-turn streak; a single
  turn can never create it.

## 3. What it does not mean

No affection/attraction/trust scores, no intimacy/sexual-tension state, no
consent/permission/boundary state, no escalation state, no purchase desire,
no commercial temperature/readiness, no content selection, no pricing, no
eligibility. A deepening trajectory authorizes nothing.

## 4. Cold-start semantics

Absent state = `unknown` bands = **"insufficient relationship history has
been accumulated by this subsystem"** — not rejection, low interest, poor
relationship, permission denied, or commercial coldness. No backfill.

## 5. Persistence location

`user_profiles.facts["relationship_trajectory_by_creator"]["<creator_id>"]`,
following the established `*_by_creator` pattern (`long_term_memory`,
`fan_knowledge`, `commercial_preferences`). Writes go through the existing
row-locked `mutate_user_profile_atomically` helper and touch only the
caller's namespace. Schema version stamped (`schema_version: 1`);
fixed-key deterministic serialization; bounded size (counters + ≤5
transition records, no message text). No new table, no Redis state.
Failures are fail-open (`store_relationship_anchors` returns `False`);
relationship state never blocks sending.

## 6. Provenance model

Numeric vocabulary mirrors `commerce/long_term_memory.py` exactly:
`EXPLICIT 1.0 / SYSTEM_EVENT 0.9 / STRONG_INFERENCE 0.8 /
WEAK_INFERENCE 0.5` (local constants, no commerce import). Every durable
update records `{reason, provenance, source}` per changed band via bounded
reason tokens built from evidence field names — "why did this change?" is
answerable without raw conversation text. LLM engagement contributes only
an informational corroboration counter when a high value coincides with
substantive behavioral evidence in the same turn; it never promotes bands.

## 7. Decay model

Dormancy (`DECAY_DORMANT_DAYS = 30`, provisional) downgrades the
familiarity/engagement *readout* one step with floors (`new`/`low`);
anchors are never destroyed, and returns re-warm the readout through new
accumulation. Trend reads DECLINING under dormancy. Desire-decay semantics
are not reused. Thresholds/half-lives are explicit constants documented as
provisional pending calibration review.

## 8. Explicit exclusions (later phases, separate design)

Intimacy, sexual tension, permission/consent/boundaries, escalation,
prompt integration, objective candidates, response-mode changes, commerce
authority changes, feature-flag wiring, and historical backfill are all
out of Phase 1. The module has no runtime consumers yet by design; a
`relationship_state_enabled`-style flag is deferred to the integration
phase that first connects it, to avoid config churn without consumers.
