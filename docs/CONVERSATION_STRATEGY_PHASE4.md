# Conversation Strategy — Phase 4

> Phase 4 introduces conversational strategy selection only. Sexual/intimacy state, boundaries, content transitions, and commerce integration are later phases.

## 1. Purpose

Given the current relationship trajectory, the current conversational
context, and the applicable hard constraints, decide what kind of
conversational move should happen next — deterministically, without an
LLM call, without new persistence, and without touching commerce.

## 2. Architecture

```text
Relationship trajectory (descriptive state)
        +
current conversational context
        +
hard conversational constraints
        ↓
CONVERSATIONAL STRATEGY (commerce/conversation_strategy.py)
        ↓
realization hint
        ↓
LLM realization
```

The trajectory remains descriptive. The strategy layer is its first
consumer and changes nothing about it: bands are read, never written,
promoted, or reinterpreted. The LLM realizes the selected move in
wording; it never chooses durable strategy and never gains authority
over relationship or commercial state.

Runtime placement (`workers/llm_worker.py`, immediately after persona
behavior derivation, before draft generation):

```text
authoritative ConversationState + contract (assembled)
        ↓
commerce objective / next_best_action (unchanged, commerce-only)
        ↓
persona behavior constraints (unchanged, read-only for strategy)
        ↓
★ strategy: snapshot ← already-fetched profile (pure read)
★ strategy: turn summary ← pre-generation evidence extraction
★ strategy: select → optional CONVERSATION STRATEGY block
        ↓
one-call / legacy generation (unchanged)
```

Effective prompt hierarchy (unchanged; strategy slots in as documented):

```text
safety / capability / grounding
        ↓
conversation requirements (contract answer_required, sincerity)
        ↓
strategy (CONVERSATION STRATEGY block)
        ↓
persona realization constraints (PERSONA BEHAVIOR block)
        ↓
LLM wording
```

## 3. Strategy vocabulary

Moves (`StrategyMove`, new Phase 4 contract):

| Move | Meaning |
|---|---|
| `ACKNOWLEDGE` | Respond to what the fan just said; answer an open question or meet a sincerity requirement. |
| `CONTINUE` | Stay with the current topic / natural continuation. |
| `EXPLORE` | Invite or deepen understanding of the user/current subject (asks only when allowed). |
| `CALLBACK` | Reference an earlier thread/fact backed by real prior-context evidence. |
| `SHARE` | Reciprocal participation without asking (used when exploration evidence exists but asking is disallowed). |
| `RECOVER` | Conservative repair move on concrete annoyance evidence. |
| `CLOSE` | Explicit farewell; natural close only. |

Realization hints reuse the existing response-mode token set as a
lower-level phrasing hint: `react`, `answer`, `share`, `explore`,
`callback`, `close`. The flirty/playful escalation token stays owned by
persona behavior; the clarification token stays owned by the capability
contract. Relationship bands can never select them.

Question vocabulary: `NO_QUESTION`, `ONE_NATURAL_QUESTION`.

Confidence is categorical only (`HIGH` / `MEDIUM` / `LOW`): confidence
in the decision from available deterministic evidence. It is not a
score and is never optimized.

This implementation is authoritative over stale roadmap vocabulary:
roadmap-only names were never runtime contracts and are not imported.
`BUILD_RAPPORT`, `DEEPEN_INTIMACY`, `RECIPROCATE_FLIRT`,
`CONTINUE_INTIMATE_THREAD`, `EXPLORE_CONTENT_INTEREST`,
`NATURAL_CONTENT_TRANSITION`, `DE_ESCALATE`, `RESPECT_BOUNDARY`, and
similar concepts do not exist in this layer.

## 4. Priority rules

Exactly one move or abstain (`None`). Earlier wins:

1. `CLOSE` — explicit farewell act in the current turn.
2. `RECOVER` — concrete repair evidence (annoyed affect).
3. `ACKNOWLEDGE` — direct question to answer, or sincerity required.
4. `CALLBACK` — familiar/established familiarity + anchored/rich
   continuity + real prior-context evidence + open threads.
5. `CONTINUE` — current topic present and continued this turn.
6. `EXPLORE` — answered question + steady/deep engagement +
   balanced/high reciprocity + question allowed.
7. `SHARE` — same reciprocity evidence as `EXPLORE`, but asking is
   currently disallowed.
8. `CONTINUE` — natural (topic present or steady/deep engagement).
9. `ACKNOWLEDGE` — safe default (low confidence, no question).

## 5. Relationship-state usage

Bands influence conversational depth and continuity only:

* New/unknown familiarity → `ACKNOWLEDGE` / `CONTINUE`; no callbacks
  requiring historical familiarity.
* Familiar/established → `CALLBACK` / `EXPLORE` / `SHARE` /
  `CONTINUE` when the current turn supplies matching evidence.
* Low engagement → never interrogative.
* Steady/deep engagement → may support richer moves with evidence.
* Low reciprocity → never compensated with questions.
* Sparse continuity → callbacks never invented.
* Anchored/rich continuity → callbacks permitted with real evidence.
* Growing trend → momentum context only. Declining trend → context
  for `RECOVER` at most; it never triggers a move alone.

Cold start (every band unknown) abstains: no block is rendered and the
prompt is byte-identical to pre-Phase-4 behavior.

## 6. Current-context precedence

An explicit current-turn requirement always outranks historical bands,
however strong. `ESTABLISHED + DEEP + HIGH + RICH + GROWING` with a
direct question still yields `ACKNOWLEDGE`/`answer`; with a farewell it
yields `CLOSE`. Band-only evidence never triggers `CALLBACK`,
`EXPLORE`, `SHARE`, or `RECOVER`. One lively message cannot create
durable state (Phase 1 remains the authority there).

`ConversationContract.maintain_topic` is never read (it is vacuously
true whenever a topic exists). Topic continuation uses turn evidence
plus explicit topic/thread fields only.

## 7. Question policy

A question is emitted only when the existing
`core.question_policy.evaluate_question_budget` allows an exploratory
move AND persona constraints allow it. Unanswered-question protection,
consecutive-question limits, per-three-turn limits, and
serious/annoyed vetoes all survive: when asking is disallowed, the
selector downgrades (`EXPLORE` → `SHARE`/`CONTINUE`) or abstains. It
never modifies `core/question_policy.py`.

## 8. Persona constraints

`question_allowed=False`, sincerity requirements, emotional-state
vetoes, and capability/contract restrictions always win. Persona
behavior is consumed read-only as constraints; the file is unmodified.

## 9. Fail-open behavior

`None` means abstain: no `CONVERSATION STRATEGY` block is appended and
the rest of the prompt is untouched. Abstention covers missing
snapshots, cold start, incomplete inputs, selector errors, and render
errors. Strategy failure can never block generation, commerce, routing,
sending, the operator queue, or relationship accumulation.

## 10. Commerce separation

The selector accepts no funnel, catalog, price, ranking, sealing,
execution, or tuning inputs — its signature is exactly `(snapshot,
conversation_state, contract, persona, turn)` — so strategy output is
invariant under commerce-signal changes by construction. The commerce
draft bypass and opportunity evaluation are unchanged.

## 11. Explicit non-goals

No sexual/intimacy state, no permission/boundary/refusal logic, no
content-interest state or transitions, no product/offer/pricing
decisions, no eligibility/ranking/sealing/execution changes, no
optimizer or learning changes, no telemetry changes, no scalar
relationship score, no second relationship engine, no duplicate
objective engine, no new persistence (DB/Redis/schema), no LLM call
for strategy.

## 12. Examples

```text
Q: fan asks a question at ESTABLISHED/DEEP/HIGH/RICH/GROWING
→ move=ACKNOWLEDGE realization=answer question=NO_QUESTION
  reasons=CURRENT_QUESTION

Q: fan answered our question, STEADY/BALANCED, budget clear
→ move=EXPLORE realization=explore question=ONE_NATURAL_QUESTION
  reasons=ANSWERED_QUESTION,ENGAGEMENT_STEADY,RECIPROCITY_BALANCED,QUESTION_BUDGET

Q: same, but question budget exhausted
→ move=SHARE realization=share question=NO_QUESTION
  reasons=ANSWERED_QUESTION,ENGAGEMENT_STEADY,RECIPROCITY_BALANCED,QUESTION_BUDGET

Q: RICH continuity but no prior-context evidence
→ not CALLBACK (bands alone never suffice)

Q: "bye, talk later!"
→ move=CLOSE realization=close question=NO_QUESTION reasons=FAREWELL
```

Rendered block (omitted entirely when abstaining):

```text
CONVERSATION STRATEGY:
move=CALLBACK
realization=callback
question=ONE_NATURAL_QUESTION
reasons=PRIOR_CONTEXT,OPEN_THREADS,CONTINUITY_RICH,FAMILIARITY_ESTABLISHED,QUESTION_BUDGET
```

## 13. Why strategy is not permission

A maximal snapshot (`ESTABLISHED`, `DEEP`, `HIGH`, `RICH`, `GROWING`)
on a plain turn yields `ACKNOWLEDGE`/`CONTINUE` with `react` and
`NO_QUESTION`. The module contains no escalation vocabulary and no
rule mapping bands to teasing, intimacy, content, or commerce. Pinned
by `tests/test_conversation_strategy_phase4.py::TestLMaxStateSafety`.

## 14. Why no persistence exists

Strategy is a pure read/decision/render layer over already-fetched
state. The snapshot derives from the already-available profile mapping
(no new DB read); nothing is written to profiles, memory, Redis, or
any table. Pinned by `TestPNoPersistence` (write APIs patched to raise;
inputs snapshotted before/after) and a source scan for persistence and
commerce hooks.
