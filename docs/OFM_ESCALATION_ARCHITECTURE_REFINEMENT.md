# OFM Conversation Escalation, Intimacy, and Sales Readiness — Architecture Refinement Notes

## Context

The codebase audit gives enough concrete evidence to refine the architecture discussion. The key finding is that we should not add three new scalar states; several of the concepts already exist, but some are overloaded or have competing representations.

## What to change first

The biggest architectural issue is this:

```text
warm relationship
      ↓
INTEREST
      ↓
DEEPEN_DESIRE
      ↓
TEASE
```

INTEREST currently conflates "the fan likes talking to us" with actual commercial interest. The audit identifies `desire.py:116` as the main warmth → commerce bridge.

At the same time, TEASE is doing double duty:

```text
social/playful tease
        +
sales/desire tease
        ↓
     TEASE
```

There is no actual flirt-depth state; `teasing_allowed` is basically a boolean and `conversation_mode=tease` is the other representation.

Aim for:

```text
                 FAN STATE
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
    RAPPORT       INTIMACY      COMMERCE
       │             │             │
       │             │             ├─ curiosity
       │             │             ├─ buying intent
       │             │             ├─ qualification
       │             │             └─ offer readiness
       │             │
       │             └─ descriptive
       │
       └─ relationship/warmth
                     │
                     ▼
               ACTION POLICY
                     │
             ┌───────┴────────┐
             ▼                ▼
        conversation       commerce
             │
        social tease
```

## Important correction

Do not create:

```text
flirt_level
commercial_curiosity
transition_readiness
```

as three new persistent variables.

The audit specifically found that each would compete with existing sources of truth:

- `flirt_level` vs `teasing_allowed` / `conversation_mode` / `ResponseMode.TEASE`
- `commercial_curiosity` vs existing content-curiosity / current-content-interest machinery
- `transition_readiness` vs `offer_readiness` / Phase 101 readiness / sales window

That would make the architecture more complicated rather than cleaner.

## What to investigate next

Make the next coding-agent task very focused:

1. Split INTEREST evidence into warmth-derived interest vs genuine commercial/content interest.
2. Eliminate unfenced legacy commerce callers, so every commercial decision goes through the Phase 9 current-turn evidence fence.
3. Separate social teasing from commercial teasing without introducing a new flirt-level state.
4. Add a deterministic detector for soft-sell language, because price mentions are caught but generic promotional language currently isn't reliably caught.
5. Add the missing multi-turn behavioral tests.

The last point is especially important. The existing tests prove many individual components, but the audit found no end-to-end test for:

> "Fan becomes increasingly intimate over many turns, never expresses commercial intent, and therefore never gets sold to."

That is exactly the behavior we need to establish empirically.

## One particularly interesting finding

There is currently a strange asymmetry:

```text
"hot"
"beautiful"
"gorgeous"
"babe"
```

are excluded from sexual-intimacy evidence, but some of those same words participate in the flirty tone detector and can therefore produce TEASE.

So the system effectively says:

> "This isn't evidence that the fan is sexually escalating."

while simultaneously saying:

> "This can be evidence that we should respond playfully/flirtatiously."

That may be intentional, but it is exactly the sort of boundary we should test rather than assume is correct.

## The next audit to run

Rather than another broad architecture audit, run a behavioral trace audit: construct ~15–20 synthetic conversation trajectories and trace every state/action after each turn.

For example:

- A. warm only
- B. playful only
- C. fan-led flirting
- D. fan-led sexual conversation
- E. sexual + no commercial intent
- F. commercial curiosity
- G. price inquiry
- H. explicit purchase request
- I. warm + repeated purchase history
- J. intimate for 20 turns + zero commerce
- K. rejected offer + renewed intimacy
- L. rejected offer + explicit price inquiry
- M. sexual conversation + "stop"
- N. flirting + "don't sell me anything"
- O. commercial curiosity + no purchase intent

For each turn, capture:

```text
raw fan message
→ extracted evidence
→ relationship state
→ intimacy state
→ desire
→ temperature
→ offer readiness
→ commerce context
→ conversation objective
→ persona mode
→ final action
```

That gives actual behavioral trajectories, rather than another static code review, and shows precisely where the architecture produces unwanted behavior. The existing audit already identifies the machinery needed; the next step is to test its composition over multiple turns.

## How to improve without changing the architecture

Goal: make the existing system better without changing its fundamental behavior or introducing a new architecture. Treat this as a hardening/refinement project, not a redesign.

The principle — don't change:

```text
Rapport
   ↓
existing desire/temperature/readiness
   ↓
existing commerce decision
   ↓
existing action
```

Instead, make each existing component more precise about what it already means.

### 1. Fix INTEREST rather than creating a new state

Currently the biggest semantic problem:

```text
warm relationship
       ↓
INTEREST
```

INTEREST can originate from warmth without commercial evidence. Don't create `commercial_interest`. Keep INTEREST, but make its evidence provenance explicit. For example, internally:

```text
INTEREST
  evidence:
    warmth
    content_interest
    purchase_intent
```

Evidence tokens already exist, so this is a refinement rather than a new architecture. Downstream code can then distinguish `INTEREST + warmth` from `INTEREST + content_interest` without changing the overall ladder. Benefit: the existing state machine is preserved while "fan is warm" stops looking identical to "fan wants something."

### 2. Make commercial actions stricter, not the conversation more restrictive

Don't change `warm → interest → conversation` into `warm → nothing`. Preserve conversational behavior but tighten the boundary at the commercial action. Conceptually, `warm → INTEREST → conversation continues` is fine, but `warm → INTEREST → SOFT_OFFER` should require commercial evidence. Phase 9 was designed for this; the problem is that legacy/unfenced callers can bypass it. So the improvement is to make Phase 9 the universal commerce gate — not to redesign commerce, but to make sure every path uses the existing architecture.

### 3. Don't add flirt_level

Avoid `flirt_level = 0..10`. The current system doesn't actually have a numeric flirt escalation model. It has `teasing_allowed`, `conversation_mode`, `ResponseMode.TEASE`, `tone=flirty`, and `NO_FLIRTING`. Adding another state would create five competing ways to describe essentially the same thing. Instead, improve the existing TEASE semantics. The useful distinction is `TEASE ├── social └── commercial`. It doesn't necessarily need persistence; it can be derived at the decision/prompt boundary:

```text
if NBA == DEEPEN_DESIRE:
    tease_context = commercial
elif playful/current-turn interaction:
    tease_context = social
```

Same architecture, much clearer behavior.

### 4. Separate "commercial tease" from "flirting" conceptually

Right now, `DEEPEN_DESIRE → TEASE` and `fan is playful → TEASE` both produce the same realization mode. Keep TEASE, but give the prompt/realizer enough context to understand why teasing was selected. `TEASE, reason = social` means being playful because the conversation is playful; `TEASE, reason = desire_building` means maintaining the existing commercial/conversational objective. This doesn't mean pushing harder — it stops the LLM from having to infer why TEASE was selected.

### 5. Improve the evidence ledger

At the commerce decision point, the system should be able to answer why it is acting right now, not just what the fan's overall state is. Make the decision context expose something like:

```text
CURRENT_TURN:
  purchase_request: false
  price_request: false
  content_request: false
  commercial_continuation: false

HISTORICAL:
  relationship: warm
  intimacy: deep
  previous_purchase: true

DERIVED:
  desire: interest
  temperature: warm
  readiness: test_interest

AUTHORIZATION:
  commercial_action: false
```

This isn't a new state machine — it's an explainability/provenance layer around the existing one. It also makes debugging dramatically easier.

### 6. Strengthen output validation rather than relying entirely on prompts

Price/promotional language has relatively strong protection (`price mention → hard flag → queue`), but generic soft selling can potentially get through because phrases like "exclusive content" don't necessarily trigger the same deterministic protections. Extend the existing validation model: detect unauthorized commercial CTA patterns (e.g. "want to see...", "send you something...", "unlock...", "check out my...", "exclusive...", "special for you...") and ask whether a commercial action was actually authorized; if not, downgrade/queue. That preserves the existing direction: planner says what to do, LLM realizes it, validator checks whether realization stayed within authority.

### 7. Fix the hot/beautiful/gorgeous/babe inconsistency

Document/test the intended distinction:

```text
"you're gorgeous"
    → flirtatious tone
    → possible playful response
    → NOT sexual evidence
    → NOT commerce

"I'm horny"
    → sexual evidence
    → intimacy trajectory
    → NOT commerce

"how much?"
    → commercial evidence
    → commerce evaluation
```

Three clean meanings without changing the underlying architecture.

### 8. Don't let intimacy become a hidden escalation mechanism

Keep the deterministic isolation of intimacy from commerce. The concern is the LLM-facing advisory path: `intimacy trajectory → prompt context → LLM → possibly flirtier wording`. Don't remove intimacy context; make its semantics explicit (descriptive only; authorizes neither sexual escalation nor commerce; implies neither consent nor purchase intent; requires no matching intensity), then test whether that changes model behavior. That is a prompt-contract improvement, not an architectural change.

### 9. Add behavioral tests around the existing architecture

Build golden conversation tests such as:

```text
Turn 1: "You seem really sweet."
Turn 2: "I love talking to you."
Turn 3: "You're gorgeous."
Turn 4: "I really like your personality."
Turn 5: "You make me smile."
Assert: NO purchase intent, NO commercial basis, NO offer, NO PPV.

Turn 6: "Do you have anything exclusive?"
Assert: commercial curiosity, not automatically PPV.

Turn 7: "How much?"
Assert: explicit commercial request → existing PPV/offer path.
```

And separately:

```text
Turn 1: "You're gorgeous."
Turn 2: "I'm horny thinking about you."
Turn 3: "You're turning me on."
Turn 4: "I want you."
...
Assert: intimacy trajectory increases; commerce does NOT increase unless commercial evidence appears.
```

This validates the exact architectural contract being preserved.

### 10. Measure the system before changing thresholds

Do not immediately tune `0.55`, `0.80`, `0.35`, `0.65`, or `2 / 6 / 12` — those thresholds are part of the existing behavioral contract. First instrument `decision`, `current-turn evidence`, `historical evidence`, `derived state`, `authorization`, and `final action`, then collect real/synthetic traces. For example:

```text
1000 conversations
warm-only: 184, commercial offers: 7  ← investigate
sexual-only: 96, commercial offers: 0  ← desired
content curiosity: 72, PPV: 0, qualification: 51
price inquiries: 43, PPV: 41, blocked: 2
```

Adjust thresholds based on observed behavior rather than intuition.

## Sequencing

- Phase 1 — No behavior change: observability. Add/verify logging of `current_turn_evidence`, `historical_state`, `derived_state`, `decision_reason`, `commercial_authorization`, `persona_mode`, `final_action`.
- Phase 2 — Preserve architecture, remove leaks: make Phase 9 universal; remove/disable unfenced legacy commerce callers; ensure warmth-derived INTEREST cannot independently become a commercial action; preserve intimacy → commerce isolation.
- Phase 3 — Clarify existing semantics: INTEREST evidence provenance; TEASE reason/context; intimacy prompt contract; normalize the inconsistent tone/evidence vocabulary.
- Phase 4 — Strengthen realization safety: validation for unauthorized soft-sell language.
- Phase 5 — Behavioral verification: multi-turn scenario tests run against the real pipeline.

## End state

Not a new architecture. Rather:

```text
                  EXISTING ARCHITECTURE
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
    Rapport          Intimacy          Commerce
       │                 │                 │
       │                 │          existing ladder
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │
                 existing action policy
                         │
                    existing LLM
                         │
                 stronger validation
```

The key improvement is semantic precision and enforcement, not more state. Keep untouched: purchase-intent verifiers, boundary system, intimacy isolation, decision priority skeleton / Phase 9 fences, routing veto order, and sealed execution gate.
