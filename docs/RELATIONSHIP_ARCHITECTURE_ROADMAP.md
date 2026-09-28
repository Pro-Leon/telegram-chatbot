# Relationship Architecture Roadmap

**Project:** OFM Chatbot
**Repository:** `E:\chatbot`
**Status:** Architecture / Implementation Roadmap
**Primary Goal:** Build a deterministic, relationship-aware conversational architecture that supports realistic rapport, continuity, reciprocity, playfulness, and intimacy while preserving the existing deterministic commerce authority.

---

# 1. Executive Summary

The chatbot already contains substantial infrastructure for:

* conversation state
* message history
* summaries
* profile facts
* long-term memory
* fan knowledge
* topic/thread tracking
* callbacks
* emotional state
* persona behavior
* response modes
* conversational objectives
* commercial readiness
* offer timing
* purchase relationship state
* commerce signals
* deterministic offer eligibility
* deterministic ranking
* deterministic sealing
* deterministic execution
* operator handoff
* cooldown and aftercare

The missing capability is **not another generic memory system** and it is **not another response-mode system**.

The missing capability is a dedicated deterministic **Relationship Layer** that represents the evolving human-to-creator conversational relationship independently from commercial readiness.

The target architecture is:

```text
                    CURRENT USER MESSAGE
                            │
                            ▼
                 EXISTING SIGNAL EXTRACTION
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
   ConversationState   OneCall Signals    Memory/History
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                  RELATIONSHIP DERIVATION
                            │
                            ▼
                 RELATIONSHIP CONTEXT
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
     CONVERSATIONAL OBJECTIVE      BEHAVIOR CONSTRAINTS
              │                           │
              └─────────────┬─────────────┘
                            ▼
                    LLM REALIZATION
                            │
                            ▼
                         RESPONSE


Relationship observations
            │
            ▼
    Existing commerce signals
            │
            ▼
    EXISTING COMMERCE ENGINE
            │
            ▼
 eligibility → ranking → sealing → execution
```

The Relationship Layer is **advisory for conversation**.

The Commerce Engine remains the sole authority for:

* whether a commercial action is eligible
* which offer is eligible
* pricing
* offer selection
* sealing
* execution
* suppression
* operator routing

No relationship score, intimacy level, LLM output, or conversational objective may directly authorize a commercial action.

---

# 2. Core Architectural Principles

These principles are mandatory throughout the implementation.

## 2.1 Relationship and Commerce Are Different Domains

The existing:

```text
RelationshipState
Desire
Temperature
Readiness
SalesWindow
NextBestAction
ConversationObjective
```

contain commercial semantics.

Do not redefine those existing meanings.

For example:

```text
WARM
HOT
DESIRE
PURCHASED
VIP
BUYING_SIGNAL
```

must continue to mean what they currently mean inside commerce.

Do not reinterpret:

```text
HOT = emotionally intimate
DESIRE = sexual desire
WARM = affection
```

Those meanings would silently alter commercial behavior.

---

# 2.2 Relationship State Is Not Permission

The following distinctions are mandatory:

```text
rapport ≠ permission

familiarity ≠ permission

intimacy ≠ permission

sexual tension ≠ permission

previous participation ≠ current permission

historical behavior ≠ current consent

content interest ≠ authorization to escalate
```

A relationship layer may describe the conversational trajectory.

It must not independently authorize behavior that another safety or policy layer prohibits.

---

# 2.3 Current State Beats Historical State for Immediate Behavior

Historical relationship state is useful for continuity.

Current user behavior determines immediate conversational direction.

Example:

```text
Historical:
    rapport = high
    intimacy_history = established

Current:
    user = uncomfortable
```

Expected behavior:

```text
historical rapport remains stored
current conversational state controls immediate behavior
```

The system must not continue intimate behavior merely because the historical relationship was intimate.

---

# 2.4 Deterministic Systems Decide Direction

LLMs may:

* extract observations
* classify signals
* produce natural language
* estimate advisory signals
* summarize conversations

LLMs must not silently become authoritative relationship controllers.

The architecture should prefer:

```text
facts
→ deterministic derivation
→ deterministic objective
→ deterministic constraints
→ LLM wording
```

rather than:

```text
history
→ LLM decides relationship
→ LLM decides strategy
→ LLM decides commercial behavior
```

---

# 2.5 Do Not Create a Single Relationship Score

Do not implement:

```text
relationship_score = 0.83
```

as the primary representation.

A single score loses important distinctions.

Example:

```text
familiarity = high
rapport = high
reciprocity = low
current_affect = cold
intimacy = high
user_initiative = low
```

This state is materially different from:

```text
familiarity = high
rapport = high
reciprocity = high
current_affect = warm
intimacy = high
user_initiative = high
```

A multidimensional representation is required.

---

# 2.6 Trajectory Is More Important Than a Snapshot

The system should eventually represent:

```text
current state
previous state
direction
confidence
evidence
```

For example:

```text
rapport:
    current = HIGH
    previous = MEDIUM
    trend = GROWING
```

rather than only:

```text
rapport = HIGH
```

---

# 2.7 Memory Is Not Policy

Stored relationship information may inform context.

It cannot override:

* current safety constraints
* current boundaries
* creator constraints
* current user behavior
* deterministic commerce gates
* execution authority

---

# 2.8 Existing Systems Must Be Reused

Prefer extending existing seams:

```text
context gathering
pure derivation
objective ranking
prompt rendering
telemetry
tests
```

Avoid creating parallel systems for:

* memory
* message storage
* conversation state
* response modes
* commerce execution

---

# 3. Current Architecture: Existing Components to Preserve

## 3.1 `core/conversation_state.py`

Current purpose:

* lifecycle
* identity
* current topic
* recent topics
* open threads
* last question
* question tracking
* transient tone
* user fact

It is explicitly lightweight and turn-scoped.

### Decision

Do not turn `ConversationState` into the durable relationship database.

Do not add:

```text
rapport_score
familiarity_score
intimacy_level
reciprocity
relationship_history
```

to this object.

A separate relationship context should be derived alongside it.

---

# 3.2 `commerce/relationship.py`

Current purpose:

Commercial relationship state derived from:

* funnel stage
* purchases
* recency
* segments
* cooldown
* VIP/repeat/purchased state

### Decision

Do not overload it.

Reuse:

* deterministic derivation pattern
* enum approach
* explainable reason codes
* prompt rendering conventions
* telemetry conventions

Do not change its semantic meaning.

---

# 3.3 `commerce/conversation_intelligence.py`

Current purpose:

Deterministically select one conversational objective from eligible candidates.

Existing candidates include:

* relationship build
* continue topic
* follow-up open loop
* explore interest
* deepen desire
* qualify
* objection handling
* present offer
* aftercare
* learn preference
* re-engage
* human handoff
* wait

### Decision

Reuse the candidate/priority/eligibility mechanism.

Add relationship-aware candidates only after the relationship state contract exists.

Do not replace the existing selector.

---

# 3.4 Memory

Existing memory systems:

```text
messages
conversation_summaries
user_profiles.facts
long_term_memory
fan_knowledge
```

### Decision

Reuse existing storage.

Do not create:

```text
relationship_memory
rapport_table
intimacy_table
relationship_events_table
```

unless later evidence demonstrates that JSONB/LTM cannot satisfy the required contract.

---

# 3.5 Commerce

Preserve:

```text
opportunity
→ eligibility
→ ranking
→ sealing
→ execution
```

Relationship state may produce observations.

It may never bypass commerce authority.

---

# 4. Target Architecture

The new subsystem should conceptually contain:

```text
relationship/
├── models.py
├── signals.py
├── derivation.py
├── transitions.py
├── memory.py
├── objectives.py
├── policy.py
├── rendering.py
└── telemetry.py
```

Exact module locations should be decided after repository inspection.

Do not create these files blindly.

First identify the project's preferred module organization.

---

# 5. Relationship Domain Model

The initial model should be multidimensional.

Candidate dimensions:

```text
familiarity
rapport
reciprocity
user_initiative
interaction_quality
relationship_trend
personal_disclosure
continuity
playfulness
intimacy
boundary_state
confidence
```

The exact fields must be finalized during Phase 0.

---

# 5.1 Familiarity

Represents how established the interaction feels based on durable history.

Potential evidence:

* number of meaningful sessions
* duration across time
* remembered personal facts
* recurring topics
* prior callbacks
* user recognition
* stable preferences
* repeated return behavior

Do not equate message count alone with familiarity.

---

# 5.2 Rapport

Represents positive conversational connection.

Potential evidence:

* reciprocal engagement
* humor
* positive reactions
* voluntary continuation
* personal disclosure
* callbacks
* appreciation
* playful interaction
* emotional openness

Rapport must not be inferred from a single keyword.

---

# 5.3 Reciprocity

Represents whether conversational investment is mutual.

Potential evidence:

```text
user asks about creator
user responds to creator disclosures
user volunteers information
user continues shared topic
user initiates follow-up
user returns to previous thread
user reciprocates playfulness
```

Do not define reciprocity simply as:

```text
user sent many messages
```

---

# 5.4 User Initiative

Track who initiates meaningful conversational movement.

Potential events:

```text
USER_INTRODUCED_TOPIC
USER_RETURNED_TO_TOPIC
USER_INITIATED_PLAYFULNESS
USER_INITIATED_INTIMACY
USER_VOLUNTEERED_DISCLOSURE
USER_ASKED_PERSONAL_QUESTION
USER_REOPENED_THREAD
USER_INITIATED_CONTACT
```

The system should distinguish these from assistant-generated prompts followed by user answers.

---

# 5.5 Interaction Quality

Represents immediate interaction quality.

Potential states:

```text
POSITIVE
NEUTRAL
LOW
NEGATIVE
UNCERTAIN
```

This is transient and must not overwrite durable rapport.

---

# 5.6 Relationship Trend

Possible states:

```text
GROWING
STABLE
COOLING
RECOVERING
UNCERTAIN
```

Trend must be derived from multiple observations where possible.

Do not infer a durable trend from one emotional keyword.

---

# 5.7 Personal Disclosure

Represent meaningful user self-disclosure.

Examples:

```text
family
work
plans
fears
goals
experiences
preferences
relationships
important events
```

Existing profile/LTM/fan-knowledge systems should be reused.

---

# 5.8 Continuity

Track whether the interaction has meaningful continuity.

Existing primitives:

* current topic
* recent topics
* open loops
* summary
* LTM
* profile
* fan knowledge
* message history

should remain authoritative sources.

---

# 5.9 Playfulness

Playfulness should be separate from sexuality.

Examples:

```text
joking
teasing
banter
shared humor
light challenge
playful callback
```

A playful relationship is not necessarily an intimate relationship.

---

# 5.10 Intimacy

Intimacy is a separate subsystem.

It should eventually represent:

```text
level
direction
user_initiated
mutuality
momentum
boundary_state
```

Do not merge intimacy with:

```text
commerce.desire
commerce.temperature
ConversationState.tone
```

---

# 5.11 Boundary State

Potential states:

```text
NORMAL
CAUTION
DE_ESCALATE
STOP
RECOVERY
```

Exact semantics must be defined with safety requirements before implementation.

---

# 6. Phase 0 — Domain Contract and Architectural Freeze

## Objective

Define the relationship domain before writing implementation code.

No relationship feature implementation should begin until this phase is complete.

---

## 6.1 Sub-phase 0.1 — Terminology Contract

Define exact meanings for:

* relationship
* familiarity
* rapport
* reciprocity
* initiative
* engagement
* intimacy
* sexual tension
* interaction quality
* trend
* boundary
* recovery
* commercial readiness

Document terms that already have conflicting meanings.

Especially:

```text
relationship
desire
temperature
engagement
intimacy
```

---

## 6.2 Sub-phase 0.2 — Authority Matrix

Create an authority table.

Example:

| Concept                | Authority                            |
| ---------------------- | ------------------------------------ |
| Current user text      | Current conversational facts         |
| Safety                 | Safety/policy layer                  |
| Creator identity       | Persona contract                     |
| Relationship state     | Deterministic relationship layer     |
| Commerce eligibility   | Commerce decision engine             |
| Offer selection        | Commerce ranking/sealing             |
| Price                  | Commerce authority                   |
| Execution              | Send/commerce execution path         |
| Natural wording        | LLM                                  |
| Summary                | Memory summarizer                    |
| Explicit user facts    | Deterministic/high-provenance memory |
| Inferred profile facts | Profile extractor with provenance    |

---

## 6.3 Sub-phase 0.3 — Current-vs-Historical Precedence

Define precedence rules.

Required test cases:

```text
long warm history + current cold message
long intimate history + current withdrawal
old preference + current correction
old playful interaction + current serious disclosure
```

Expected architecture:

```text
historical state informs context
current state controls immediate behavior
```

---

## 6.4 Sub-phase 0.4 — Provenance Contract

Every relationship input should eventually be classified as:

```text
EXPLICIT
OBSERVED
INFERRED
TEMPORARY
HISTORICAL
SYSTEM
```

Define confidence semantics.

Do not expose unqualified inferred facts to the prompt as though they were explicit truths.

---

## 6.5 Sub-phase 0.5 — Retention Contract

Before populating relationship memory, define:

* retention period
* expiration
* creator scoping
* fan scoping
* sensitivity
* visibility
* deletion behavior
* stale-state handling
* historical status

---

## 6.6 Sub-phase 0.6 — Objective Precedence Contract

Define which relationship objectives may override commerce objectives.

Do not assume that every relationship objective automatically outranks commerce.

Document cases such as:

```text
current discomfort
boundary
repair
aftercare
active user request
explicit commercial intent
natural content curiosity
```

---

## 6.7 Sub-phase 0.7 — Architecture Freeze

Produce:

```text
RELATIONSHIP_DOMAIN_CONTRACT.md
```

and freeze:

* terminology
* state dimensions
* authority
* persistence strategy
* provenance
* precedence
* safety boundaries

### Exit Criteria

Phase 0 is complete only when:

* terminology is defined
* conflicting existing semantics are documented
* authority matrix exists
* current-vs-historical precedence is defined
* provenance rules exist
* retention rules exist
* objective precedence is defined
* no unresolved design decision is hidden inside implementation

---

# 7. Phase 1 — Canonical Relationship State Model

## Objective

Create the deterministic relationship context object.

---

## 7.1 Sub-phase 1.1 — Data Model

Create a typed immutable structure.

Conceptually:

```python
RelationshipContext(
    familiarity=...,
    rapport=...,
    reciprocity=...,
    user_initiative=...,
    interaction_quality=...,
    relationship_trend=...,
    continuity=...,
    playfulness=...,
    intimacy=...,
    boundary_state=...,
    confidence=...,
    evidence=...,
    reason_codes=...,
)
```

Do not implement until Phase 0 specifies exact fields.

---

## 7.2 Sub-phase 1.2 — Enums

Prefer bounded enums rather than arbitrary strings.

Example:

```text
LOW
MEDIUM
HIGH
UNKNOWN
```

or domain-specific states where ordinal semantics are valid.

Do not create arbitrary numeric scores unless required.

---

## 7.3 Sub-phase 1.3 — Evidence Model

Each derived field should be explainable.

Example:

```text
rapport:
    state = HIGH
    evidence = [
        USER_RETURNED_TO_TOPIC,
        USER_VOLUNTARY_DISCLOSURE,
        POSITIVE_CONTINUATION
    ]
```

Evidence should identify:

* source
* type
* confidence
* recency
* provenance

---

## 7.4 Sub-phase 1.4 — Pure Derivation

Implement:

```text
inputs → RelationshipContext
```

as a pure deterministic function.

No DB writes.

No LLM calls.

No side effects.

---

## 7.5 Sub-phase 1.5 — Fail-Open Behavior

If relationship derivation fails:

```text
commerce continues unchanged
conversation continues using existing behavior
relationship-specific enrichment is omitted
```

The new layer must not break message generation.

---

## 7.6 Sub-phase 1.6 — Tests

Add tests for:

* default state
* missing memory
* missing summary
* empty history
* contradictory inputs
* stale memory
* current negative signal
* current positive signal
* repeated interactions
* creator isolation
* fan isolation
* deterministic output

### Exit Criteria

* typed relationship context exists
* pure derivation exists
* no commerce behavior changed
* all baseline tests pass
* new state is observable but not yet behavior-driving

---

# 8. Phase 2 — Relationship Signal Extraction

## Objective

Convert existing conversation and memory evidence into structured relationship observations.

---

# 8.1 Sub-phase 2.1 — Reuse Existing Signals

Reuse:

* `fan_asks_question`
* topic continuity
* current topic
* open threads
* last user fact
* emotional state
* tone
* LTM
* profile
* fan knowledge
* lifecycle
* timing behavior

Do not duplicate existing extractors.

---

# 8.2 Sub-phase 2.2 — User Initiative Detection

Add role-aware analysis.

Detect:

```text
who introduced the topic
who reopened it
who escalated it
who disclosed information
who asked the question
```

Use actual message roles.

Do not infer initiative from assistant questions.

---

# 8.3 Sub-phase 2.3 — Disclosure Detection

Classify meaningful user disclosures.

Prefer existing:

* fan knowledge
* profile
* LTM

before adding another extractor.

---

# 8.4 Sub-phase 2.4 — Reciprocity Detection

Build observations such as:

```text
USER_RESPONDED_TO_CREATOR_DISCLOSURE
USER_ASKED_ABOUT_CREATOR
USER_VOLUNTEERED_INFORMATION
USER_CONTINUED_TOPIC
USER_REOPENED_THREAD
USER_RECIPROCATED_PLAYFULNESS
```

---

# 8.5 Sub-phase 2.5 — Current Interaction Quality

Use current-turn evidence to derive:

```text
positive
neutral
negative
uncertain
```

Current quality must not directly overwrite durable rapport.

---

# 8.6 Sub-phase 2.6 — Intimacy Observation Extraction

Only after the domain contract is approved.

Separate:

```text
topic sexual
user initiated sexual topic
assistant initiated sexual topic
user reciprocated
user withdrew
user requested content
content curiosity
```

Do not collapse these into one "sexual score."

---

# 8.7 Sub-phase 2.7 — Evidence Provenance

Every extracted signal should contain:

```text
source
provenance
confidence
recency
role
```

### Exit Criteria

* existing signals are reused
* user initiative is role-aware
* reciprocity is observable
* current interaction quality exists
* intimacy observations are separate from commerce desire
* no signal directly authorizes commerce

---

# 9. Phase 3 — Relationship State Transitions

## Objective

Turn observations into trajectory.

---

## 9.1 Sub-phase 3.1 — Previous State

Determine where durable relationship anchors live.

Preferred first candidate:

```text
user_profiles.facts
```

with creator namespace.

---

## 9.2 Sub-phase 3.2 — Event Representation

Use existing LTM relationship-event capability where appropriate.

Potential events:

```text
FIRST_MEANINGFUL_DISCLOSURE
USER_RETURNED
SHARED_TOPIC_ESTABLISHED
PERSONAL_FACT_REMEMBERED
POSITIVE_CALLBACK
USER_INITIATED_PLAYFULNESS
USER_INITIATED_INTIMACY
BOUNDARY_EVENT
REPAIR_EVENT
```

Only chartered events should be persisted.

---

## 9.3 Sub-phase 3.3 — Transition Rules

Represent:

```text
previous state
+
new evidence
=
new state
```

Example:

```text
MEDIUM rapport
+
repeated reciprocal engagement
=
HIGH rapport
```

Avoid arbitrary threshold accumulation without evidence.

---

## 9.4 Sub-phase 3.4 — Trend

Derive:

```text
GROWING
STABLE
COOLING
RECOVERING
UNCERTAIN
```

Trend should be bounded and explainable.

---

## 9.5 Sub-phase 3.5 — Decay

Define which dimensions decay.

Not every relationship fact should decay.

Example distinction:

```text
"fan likes hiking"
    durable preference

"conversation feels warm"
    transient

"rapport has been high for months"
    durable anchor + derived current state

"sexual tension is high"
    highly transient
```

---

## 9.6 Sub-phase 3.6 — Contradiction Handling

Define:

```text
historical relationship state
vs
current interaction evidence
```

Current evidence controls current behavior.

Historical state remains available for continuity.

### Exit Criteria

* trajectory is deterministic
* transitions are explainable
* decay is dimension-specific
* contradiction behavior is defined
* relationship state survives context-window loss where appropriate

---

# 10. Phase 4 — Conversational Strategy Layer

## Objective

Allow relationship state to influence conversational objectives.

---

## 10.1 Sub-phase 4.1 — Candidate Definitions

Potential relationship objectives:

```text
BUILD_RAPPORT
MAINTAIN_CONNECTION
EXPLORE_USER
RECALL_SHARED_CONTEXT
DEEPEN_CONNECTION
RECIPROCATE_PLAYFULNESS
CONTINUE_INTIMATE_THREAD
DE_ESCALATE
REPAIR_CONNECTION
RE_ENGAGE
```

Exact names are subject to Phase 0.

---

## 10.2 Sub-phase 4.2 — Eligibility

Every objective needs deterministic eligibility.

Example:

```text
DEEPEN_CONNECTION
requires:
    rapport >= threshold
    interaction_quality != negative
    no boundary suppression
    no current de-escalation requirement
```

---

## 10.3 Sub-phase 4.3 — Priority

Integrate with existing `conversation_intelligence.py`.

Do not arbitrarily reorder existing priorities.

Document exact interaction with:

```text
PRESENT_OFFER
AFTERCARE
HANDLE_OBJECTION
FOLLOW_UP_OPEN_LOOP
RELATIONSHIP_BUILD
```

---

## 10.4 Sub-phase 4.4 — Objective Reason Codes

Every selected objective must expose why it won.

Example:

```text
objective = DEEPEN_CONNECTION

reason_codes = [
    RAPPORT_GROWING,
    USER_DISCLOSURE_RECENT,
    RECIPROCITY_HIGH,
    NO_BOUNDARY_CONSTRAINT
]
```

---

## 10.5 Sub-phase 4.5 — Commerce Interaction

A relationship objective must not mutate:

```text
eligibility
ranking
sealing
execution
```

It may coexist with a commercial opportunity.

---

# 10.6 Sub-phase 4.6 — Existing Response Mode Integration

Map relationship objectives to existing response modes.

Reuse:

```text
REACT
ANSWER
SHARE
EXPLORE
TEASE
CALLBACK
CLARIFY
CLOSE
```

Do not add a new response mode unless existing modes demonstrably cannot represent the required behavior.

### Exit Criteria

* relationship can influence conversation objective
* commerce authority remains unchanged
* objective selection is deterministic
* existing tests remain valid
* new objective decisions are explainable

---

# 11. Phase 5 — Relationship-Aware Context Assembly

## Objective

Give the LLM a compact representation of relationship state without forcing it to reconstruct the relationship from raw history.

---

# 11.1 Sub-phase 5.1 — New Prompt Block

Add a dedicated labeled block.

Conceptual:

```text
RELATIONSHIP CONTEXT:
- familiarity: established
- rapport: growing
- reciprocity: strong
- user initiative: high
- trend: warming
- current interaction: positive
- objective: deepen_connection
```

Exact rendering must be compact.

---

# 11.2 Sub-phase 5.2 — Provenance Rendering

Only expose provenance where it materially affects trust.

Do not dump internal scoring or implementation details into the user-facing generation prompt.

---

# 11.3 Sub-phase 5.3 — Context Priority

Relationship context should sit below:

```text
Safety
Identity
Truthfulness
Current conversation
```

and above lower-priority commercial wording where appropriate.

The prompt order alone must not be treated as a safety mechanism.

---

# 11.4 Sub-phase 5.4 — Context Compaction

The relationship block must survive history compaction.

Do not place relationship state exclusively inside:

```text
SUMMARY
```

because summaries are lossy and periodically refreshed.

---

# 11.5 Sub-phase 5.5 — Prompt Injection Resistance

Stored memory must be treated as data.

Relationship memory must not be allowed to introduce instructions.

### Exit Criteria

* relationship state is visible to generation
* state survives context compaction
* prompt remains compact
* memory cannot inject instructions
* existing persona priority remains intact

---

# 12. Phase 6 — Intimacy Architecture

## Objective

Build intimacy as a separate trajectory from commerce.

This phase requires explicit safety approval before implementation.

---

# 12.1 Sub-phase 6.1 — Intimacy Vocabulary

Define:

```text
intimacy
sexual topic
sexual interest
sexual tension
sexual initiation
reciprocity
escalation
withdrawal
de-escalation
boundary
```

---

# 12.2 Sub-phase 6.2 — Intimacy State

Potential dimensions:

```text
level
direction
user_initiated
mutuality
momentum
current_interest
boundary_state
```

---

# 12.3 Sub-phase 6.3 — Intimacy Is Not Tone

Existing:

```text
tone = flirty
```

remains a momentary signal.

It must not become the canonical intimacy state.

---

# 12.4 Sub-phase 6.4 — Intimacy Is Not Commerce Desire

Do not reuse:

```text
commerce.desire
```

for intimacy.

A user can have:

```text
high intimacy
low purchase intent
```

or:

```text
high purchase intent
low intimacy
```

These are valid states.

---

# 12.5 Sub-phase 6.5 — Directionality

Represent:

```text
increasing
stable
decreasing
unknown
```

rather than only a level.

---

# 12.6 Sub-phase 6.6 — User-Led vs Assistant-Led

Record who initiated the transition.

This is critical for realistic behavior and boundary handling.

---

# 12.7 Sub-phase 6.7 — Withdrawal

Detect:

```text
topic change
explicit refusal
shortening
discomfort
boundary language
negative affect
```

Do not require a literal "stop" to recognize withdrawal.

---

# 12.8 Sub-phase 6.8 — Safety Gate Integration

Intimacy state may never override safety.

Required precedence:

```text
safety/boundary
>
current user state
>
relationship strategy
>
commerce opportunity
```

### Exit Criteria

* intimacy has independent state
* sexual content interest is separate from purchase intent
* user initiative is tracked
* withdrawal is represented
* safety remains authoritative

---

# 13. Phase 7 — Boundary, Refusal, Recovery State

## Objective

Create deterministic conversational recovery behavior.

---

# 13.1 Sub-phase 7.1 — Boundary Events

Define event taxonomy.

Examples:

```text
BOUNDARY_REQUEST
ESCALATION_REJECTION
TOPIC_WITHDRAWAL
DISCOMFORT_SIGNAL
STOP_REQUEST
SAFETY_BLOCK
```

---

# 13.2 Sub-phase 7.2 — De-escalation

Potential objective:

```text
DE_ESCALATE
```

It should suppress inappropriate escalation.

---

# 13.3 Sub-phase 7.3 — Repair

Potential objective:

```text
REPAIR_CONNECTION
```

Use when appropriate after conversational rupture.

---

# 13.4 Sub-phase 7.4 — Recovery

Define:

```text
boundary event
→ immediate constraint
→ recovery period
→ normal interaction
```

Recovery must not be equivalent to commerce cooldown.

---

# 13.5 Sub-phase 7.5 — Persistence

Only persist boundaries that meet the explicit retention/provenance contract.

Do not persist inferred sensitivity indefinitely.

### Exit Criteria

* boundaries have deterministic semantics
* de-escalation is deterministic
* recovery is distinct from commerce cooldown
* boundary state cannot be bypassed by LLM output

---

# 14. Phase 8 — Natural Content Interest Transition

## Objective

Connect conversational intimacy and content curiosity to commerce without making intimacy itself a sales trigger.

---

# 14.1 Sub-phase 8.1 — Separate Content Interest

Reuse:

```text
content_interest
content_curiosity
content_request
explicit_content_request
asks_for_free_content
```

---

# 14.2 Sub-phase 8.2 — Natural Transition Objective

Introduce a conversation objective such as:

```text
EXPLORE_CONTENT_INTEREST
```

or:

```text
NATURAL_CONTENT_TRANSITION
```

only if the existing objective taxonomy cannot represent it.

---

# 14.3 Sub-phase 8.3 — Relevance

Content suggestions remain deterministic.

Use existing:

```text
available content
semantic titles
relevance ranking
purchased exclusion
```

---

# 14.4 Sub-phase 8.4 — No Forced Pitch

Relationship strategy must be allowed to remain:

```text
CONTINUE_INTIMATE_THREAD
```

even when:

```text
commercial opportunity = true
```

---

# 14.5 Sub-phase 8.5 — Commercial Handoff

Only the commerce engine can transition:

```text
opportunity
→ eligible offer
→ sealed action
→ execution
```

### Exit Criteria

* content interest is acknowledged naturally
* commerce remains deterministic
* intimacy does not automatically cause selling
* commercial opportunities can exist without changing relationship objective

---

# 15. Phase 9 — Relationship ↔ Commerce Integration

## Objective

Expose relationship observations to commerce without allowing commerce to redefine relationship state.

---

# 15.1 Sub-phase 9.1 — Signal Bridge

Potential observations:

```text
relationship_engagement
content_interest
user_initiative
reciprocity
interaction_quality
intimacy_interest
```

Only signals with approved semantics should cross the boundary.

---

# 15.2 Sub-phase 9.2 — Signal Provenance

Commerce should know whether a signal is:

```text
explicit
observed
inferred
low confidence
```

where necessary.

---

# 15.3 Sub-phase 9.3 — Existing Authority

Commerce remains authoritative for:

```text
eligibility
offer
price
timing
sealing
execution
```

---

# 15.4 Sub-phase 9.4 — No Feedback Loop Corruption

Do not create:

```text
commerce readiness
→ relationship score
→ higher intimacy
→ commerce readiness
```

without explicit controls.

This could create a self-reinforcing commercial feedback loop.

---

# 15.5 Sub-phase 9.5 — Opportunity vs Objective

The system must support:

```text
commercial opportunity = TRUE
conversation objective = RELATIONSHIP
```

This is an explicit architectural invariant.

### Exit Criteria

* relationship observations can feed commerce
* commerce cannot redefine relationship state
* no circular scoring loop exists
* opportunity and conversation objective remain separate

---

# 16. Phase 10 — Learning and Optimization

## Objective

Use observed behavior to improve thresholds and strategies without giving the optimizer business authority.

---

# 16.1 Sub-phase 10.1 — Observation Collection

Collect:

```text
relationship state
objective
evidence
response mode
user follow-up
user return
topic continuation
negative response
boundary event
commercial opportunity
commercial action
```

---

# 16.2 Sub-phase 10.2 — Outcome Labels

Potential outcomes:

```text
continued
deepened
neutral
withdrawn
negative
boundary
returned
converted
```

Do not conflate:

```text
conversion = relationship success
```

---

# 16.3 Sub-phase 10.3 — Calibration

Evaluate deterministic thresholds against actual outcomes.

---

# 16.4 Sub-phase 10.4 — Optimizer Restrictions

Optimizer may recommend:

```text
threshold adjustment
priority adjustment
strategy weighting
```

but may not directly:

```text
execute offer
override eligibility
override safety
override boundary
change price
select unauthorized content
```

---

# 16.5 Sub-phase 10.5 — Versioning

Every strategy/threshold change should be versioned.

Telemetry should identify:

```text
relationship_strategy_version
relationship_model_version
commerce_strategy_version
```

### Exit Criteria

* outcomes are measurable
* optimization is advisory
* strategy changes are versioned
* authority remains deterministic

---

# 17. Phase 11 — Observability and State Explainability

## Objective

Make every relationship decision auditable.

---

# 17.1 Sub-phase 11.1 — State Telemetry

Record:

```text
relationship state
relationship trend
rapport
familiarity
reciprocity
initiative
intimacy state
boundary state
confidence
```

---

# 17.2 Sub-phase 11.2 — Evidence Telemetry

Record reason codes rather than raw sensitive text wherever possible.

Example:

```text
RAPPORT_GROWING
USER_VOLUNTARY_DISCLOSURE
USER_REOPENED_THREAD
CURRENT_NEGATIVE_AFFECT
BOUNDARY_SIGNAL
```

---

# 17.3 Sub-phase 11.3 — Objective Telemetry

Record:

```text
selected_objective
candidate_objectives
winning_reason
suppressed_candidates
suppression_reason
```

---

# 17.4 Sub-phase 11.4 — Commerce Comparison

When useful, record:

```text
relationship_objective
commerce_objective
commercial_opportunity
final_objective
```

This allows investigation of cases where:

```text
opportunity = true
but
relationship strategy won
```

---

# 17.5 Sub-phase 11.5 — Debug Explainability

Provide an internal state explanation such as:

```text
RELATIONSHIP
familiarity: established
rapport: high
trend: growing
reciprocity: high
initiative: user-led

OBJECTIVE
deep_connection

WHY
- user disclosed personal information
- user asked reciprocal question
- prior topic reopened
- no boundary suppression

COMMERCE
opportunity: true
offer: suppressed by conversational strategy
```

---

# 18. Phase 12 — Testing and Behavioral Simulation

## Objective

Verify that the system behaves correctly across realistic conversations.

---

# 18.1 Sub-phase 12.1 — Unit Tests

Test:

* state derivation
* transitions
* decay
* evidence weighting
* provenance
* objective eligibility
* objective priority
* boundary state
* intimacy state
* memory persistence

---

# 18.2 Sub-phase 12.2 — Regression Tests

Existing tests must continue to pass.

Especially:

```text
test_phase16_conversation_intelligence.py
test_phase17_conversational_execution.py
test_phase10_lifecycle.py
test_phase14_enterprise.py
test_phase31_hardening.py
test_sunny_conversational_intelligence.py
test_phase75c_context_compact.py
test_phase_c1a_memory
test_m5_profile_freshness
test_phase36_deep_personalization
test_phase78d_retrieval_activation
test_context_engine_gatherers
test_commerce_signals
test_commerce_decision
test_p33_opportunity_ranking
```

---

# 18.3 Sub-phase 12.3 — Relationship Scenarios

Build scenario fixtures for:

### New user

```text
no history
low familiarity
unknown rapport
```

### Returning user

```text
known profile
prior topics
stable preferences
```

### Growing rapport

```text
repeated voluntary disclosure
callbacks
reciprocal questions
positive continuation
```

### One-sided conversation

```text
assistant asks
user answers minimally
low reciprocity
```

### User-led intimacy

```text
user introduces intimate topic
user continues
user remains engaged
```

### Withdrawal

```text
intimate conversation
user changes subject
```

Expected:

```text
intimacy trend decreases
relationship history remains intact
```

### Boundary

```text
user explicitly declines
```

Expected:

```text
boundary state active
escalation suppressed
```

### Commerce overlap

```text
relationship strong
content interest strong
commercial opportunity true
```

Expected:

```text
relationship objective may remain active
commerce opportunity remains independently evaluated
```

---

# 18.4 Sub-phase 12.4 — Adversarial Tests

Test:

```text
contradictory memory
stale summary
incorrect inferred profile
LLM signal failure
OneCall failure
missing memory
database timeout
duplicate message
rapid user messages
concurrent workers
```

---

# 18.5 Sub-phase 12.5 — Longitudinal Simulation

Simulate:

```text
Day 1
Day 2
Day 7
Day 30
Day 60
```

Test whether:

* familiarity survives
* stale states decay
* current affect overrides history
* important facts survive
* intimacy does not become permanently sticky
* commerce behavior remains deterministic

---

# 19. Persistence Architecture

Preferred architecture:

```text
messages
    ↓
raw source of truth

conversation_summaries
    ↓
long-horizon prose continuity

user_profiles.facts
    ↓
durable creator-scoped structured relationship anchors

LTM
    ↓
typed relationship events / facts

RelationshipContext
    ↓
derived per-turn state
```

Do not persist every derived scalar.

Persist meaningful anchors.

---

# 20. Relationship Memory Rules

## Store

Potentially:

```text
meaningful shared experiences
important disclosures
stable preferences
relationship events
important commitments
important boundaries
recurring topics
```

## Do Not Automatically Store

```text
temporary mood
single-turn flirty tone
single-turn sexual intensity
unverified emotional assumptions
speculative attraction
speculative consent
```

---

# 21. Context Assembly Contract

Relationship context should be assembled from:

```text
current message
recent history
ConversationState
relationship memory
profile
fan knowledge
LTM
summary
current emotional state
current timing state
```

Precedence:

```text
Safety
↓
Identity
↓
Truthfulness
↓
Current conversation
↓
Current boundary state
↓
Relationship context
↓
Behavior
↓
Commercial wording
```

This is conceptual precedence, not a substitute for deterministic enforcement.

---

# 22. Failure Isolation

The relationship layer must fail independently.

If it fails:

```text
relationship derivation error
        ↓
log error
        ↓
emit telemetry
        ↓
return safe/default relationship context
        ↓
continue existing conversation path
```

It must not cause:

```text
send failure
commerce execution failure
database corruption
worker crash
```

---

# 23. Concurrency Requirements

Relationship persistence must respect existing:

```text
creator:user
locks
debounce
atomic profile mutation
```

Never perform:

```text
read relationship
compute
write whole profile
```

without preserving existing atomic mutation semantics.

Avoid lost updates between concurrent messages.

---

# 24. Multi-Creator Isolation

All relationship data must be creator-scoped.

A relationship with creator A must never affect:

```text
creator B
creator C
```

Required tests:

```text
same fan + creator A
same fan + creator B
```

must produce independent relationship state.

---

# 25. Security / Privacy Requirements

Relationship information may be more sensitive than ordinary profile data.

Define:

* storage access
* telemetry redaction
* dashboard visibility
* operator access
* retention
* deletion
* export behavior
* audit behavior

Avoid logging raw intimate conversation content when a categorical reason code is sufficient.

---

# 26. Performance Requirements

The relationship layer should preferably use already gathered data.

Do not introduce multiple additional DB round trips per turn.

Preferred:

```text
existing parallel gather
        ↓
relationship derivation
        ↓
objective selection
```

Avoid:

```text
relationship derivation
→ DB query
→ DB query
→ memory query
→ another DB query
```

---

# 27. Determinism Requirements

Given identical:

```text
current input
history
memory
profile
configuration
state
```

the deterministic relationship layer should produce identical output.

LLM output may vary.

Relationship derivation should not.

---

# 28. Configuration Requirements

Relationship thresholds must not be scattered as magic numbers.

Use explicit configuration.

Examples:

```text
rapport thresholds
familiarity thresholds
trend windows
decay periods
initiative thresholds
reciprocity thresholds
intimacy transition thresholds
recovery periods
```

Every threshold should have:

* name
* unit
* default
* explanation
* test coverage
* version

---

# 29. Migration Strategy

No destructive migration should be required initially.

Preferred sequence:

```text
1. Add types
2. Add derivation
3. Add read-only telemetry
4. Compare against current behavior
5. Add prompt context behind feature flag
6. Add objective candidates behind feature flag
7. Validate
8. Enable selectively
9. Add durable relationship events
10. Integrate commerce observations
```

---

# 30. Feature Flags

Relationship behavior should initially be controlled by flags.

Potential:

```text
relationship_context_enabled
relationship_objectives_enabled
relationship_memory_enabled
intimacy_context_enabled
relationship_commerce_bridge_enabled
relationship_learning_enabled
```

Flags should allow:

```text
observe only
context only
strategy
full behavior
```

---

# 31. Rollback Strategy

Each phase should be independently disableable.

If relationship objective selection causes regressions:

```text
relationship_objectives_enabled = false
```

The existing conversation intelligence path must remain functional.

If relationship context causes prompt regressions:

```text
relationship_context_enabled = false
```

Commerce must remain operational.

---

# 32. Explicit Non-Goals

The relationship architecture must NOT:

* replace commerce relationship state
* replace ConversationState
* replace persona behavior
* replace response modes
* replace memory
* replace safety
* directly execute offers
* select prices
* bypass eligibility
* infer permission from intimacy
* infer consent from historical behavior
* create a single universal relationship score
* make the LLM the strategy authority
* turn every intimate conversation into a sales opportunity
* treat conversion as proof of relationship quality

---

# 33. Required Architectural Invariants

These should eventually become executable tests.

## Invariant 1

```text
Changing relationship state must not change commerce eligibility
unless an explicitly approved commerce signal changes.
```

## Invariant 2

```text
Changing commerce opportunity must not automatically change relationship state.
```

## Invariant 3

```text
Historical intimacy cannot override current boundary state.
```

## Invariant 4

```text
Current user withdrawal must override historical conversational momentum.
```

## Invariant 5

```text
Relationship derivation must be deterministic.
```

## Invariant 6

```text
LLM relationship observations cannot directly execute commerce.
```

## Invariant 7

```text
Commerce opportunity may exist while relationship objective remains active.
```

## Invariant 8

```text
Creator A relationship state cannot affect creator B.
```

## Invariant 9

```text
A single flirty/sexual keyword cannot create durable intimacy state.
```

## Invariant 10

```text
A single inferred emotional classification cannot create durable relationship fact.
```

---

# 34. Definition of Done for the Entire Architecture

The relationship architecture is complete only when all of the following are true.

## State

* relationship state is multidimensional
* relationship state is deterministic
* relationship state is explainable
* relationship state is creator-scoped
* relationship state survives context-window loss appropriately

## Continuity

* meaningful relationship facts survive sessions
* stale transient state decays
* current behavior overrides stale assumptions
* provenance is preserved where necessary

## Reciprocity

* user initiative is role-aware
* reciprocity is measurable
* assistant questions are not mistaken for user initiative
* one-sided conversations are distinguishable

## Intimacy

* intimacy is independent of commerce desire
* intimacy is independent of tone
* intimacy has directionality
* user initiation is tracked
* withdrawal is tracked
* boundaries are authoritative

## Strategy

* relationship objectives exist
* objective selection is deterministic
* objective decisions are explainable
* existing commerce objectives remain stable
* response modes are reused

## Commerce

* commerce remains deterministic
* eligibility remains authoritative
* ranking remains authoritative
* sealing remains authoritative
* execution remains authoritative
* relationship state cannot bypass commerce controls

## LLM

* LLM realizes the chosen conversational objective
* LLM does not become hidden strategy authority
* LLM cannot override safety
* LLM cannot execute commerce
* relationship context is compact and explicit

## Testing

* unit tests exist
* regression suite passes
* longitudinal simulations exist
* adversarial tests exist
* commerce invariants are tested
* creator isolation is tested
* concurrency is tested

## Observability

* relationship state is observable
* objective selection is observable
* reason codes exist
* relationship/commerce interaction is observable
* sensitive raw content is appropriately redacted

---

# 35. Recommended Implementation Order

The implementation order is intentionally strict:

```text
PHASE 0
Domain contract
    ↓
PHASE 1
Canonical relationship state
    ↓
PHASE 2
Signal extraction
    ↓
PHASE 3
Trajectory / transitions
    ↓
PHASE 4
Relationship objectives
    ↓
PHASE 5
Prompt/context integration
    ↓
PHASE 6
Intimacy architecture
    ↓
PHASE 7
Boundaries / recovery
    ↓
PHASE 8
Natural content transition
    ↓
PHASE 9
Commerce integration
    ↓
PHASE 10
Learning / optimization
    ↓
PHASE 11
Observability
    ↓
PHASE 12
Longitudinal behavioral validation
```

Do not skip directly from the current architecture to intimacy or sales integration.

The relationship substrate must exist first.

---

# 36. Engineering Workflow for Every Phase

Every implementation phase follows this process:

```text
1. Audit current implementation
2. Identify exact existing seams
3. Confirm no duplicate primitive exists
4. Define data contract
5. Define authority boundary
6. Define failure behavior
7. Define persistence requirements
8. Define tests
9. Implement smallest change
10. Run targeted tests
11. Run relevant regression tests
12. Inspect diff
13. Inspect final implementation report
14. Verify invariants
15. Only then proceed to next phase
```

Never implement multiple architectural phases simultaneously unless explicitly approved.

---

# 37. OpenCode Implementation Protocol

For each phase, OpenCode must first receive a **CODEBASE AUDIT PROMPT**.

The audit must identify:

* exact files
* exact functions
* exact data structures
* exact call sites
* existing tests
* existing persistence
* existing authority
* existing failure handling
* duplicate functionality
* integration seams

OpenCode must not invent architecture based solely on this roadmap.

After the audit:

```text
AUDIT
→ architecture review
→ implementation prompt
→ implementation
→ final report
→ verification
```

No implementation should begin from assumptions about the repository.

---

# 38. Git Safety Rules

Never:

```text
git reset --hard
git clean -fd
delete migrations
drop tables
rewrite history
```

unless explicitly authorized.

Never commit unless explicitly requested.

Every phase should leave the repository in a recoverable state.

---

# 39. Phase Status Tracking

Maintain a status table in this document.

| Phase                     | Status      |
| ------------------------- | ----------- |
| 0 — Domain Contract       | NOT STARTED |
| 1 — Relationship State    | NOT STARTED |
| 2 — Signal Extraction     | NOT STARTED |
| 3 — Transitions           | NOT STARTED |
| 4 — Strategy              | NOT STARTED |
| 5 — Context Assembly      | NOT STARTED |
| 6 — Intimacy              | NOT STARTED |
| 7 — Boundaries / Recovery | NOT STARTED |
| 8 — Content Transition    | NOT STARTED |
| 9 — Commerce Integration  | NOT STARTED |
| 10 — Learning             | NOT STARTED |
| 11 — Observability        | NOT STARTED |
| 12 — Testing / Simulation | NOT STARTED |

Allowed states:

```text
NOT STARTED
AUDITING
DESIGNING
IMPLEMENTING
VERIFYING
BLOCKED
COMPLETE
```

Do not mark a phase complete merely because code exists.

A phase is complete only when its exit criteria and regression requirements pass.

---

# 40. Current Project Position

Based on the existing architecture audit:

```text
Phase 0 — AUDITING / DESIGNING
```

The repository audit has established:

1. Existing commercial relationship state is purchase-centric.
2. Existing conversational state is transient.
3. Existing memory/continuity infrastructure is strong.
4. Existing conversation objective selection is reusable.
5. Existing commerce authority is strong and should remain untouched.
6. Existing engagement signals are turn-scoped and insufficient for durable rapport.
7. Reciprocity and user initiative are genuinely missing.
8. Relationship trajectory is genuinely missing.
9. Intimacy trajectory is genuinely missing.
10. Conversational repair/recovery state is genuinely missing.
11. No new generic memory database is currently justified.
12. The smallest architecture is a parallel deterministic relationship layer.

---

# 41. Immediate Next Step

Do **not** implement Phase 1 yet.

First finalize Phase 0:

```text
RELATIONSHIP_DOMAIN_CONTRACT.md
```

The contract must answer:

1. What exactly constitutes rapport?
2. What exactly constitutes familiarity?
3. What exactly constitutes reciprocity?
4. What exactly constitutes user initiative?
5. What constitutes intimacy?
6. What must never be inferred from a single turn?
7. Which relationship facts may become durable memory?
8. Which relationship facts must remain transient?
9. How should historical relationship state interact with current behavior?
10. What relationship objectives exist?
11. Which objectives can override commercial objectives?
12. What safety/boundary states exist?
13. What information may be surfaced to the LLM?
14. What information may be surfaced to commerce?
15. What information must never cross either boundary?
16. What retention rules apply?
17. What evidence/provenance is required?
18. What deterministic invariants must be enforced?

Only after these questions have explicit answers should implementation begin.

---

# 42. Final Architectural Target

The intended final system is not:

```text
LLM
→ relationship guess
→ sales
```

It is:

```text
                  ┌───────────────────┐
                  │   Raw Interaction │
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Evidence / Memory │
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Relationship State│
                  │                   │
                  │ familiarity       │
                  │ rapport           │
                  │ reciprocity       │
                  │ initiative        │
                  │ trajectory        │
                  │ playfulness       │
                  │ intimacy          │
                  │ boundaries        │
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Conversation      │
                  │ Strategy          │
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Behavior / Persona│
                  └─────────┬─────────┘
                            │
                            ▼
                           LLM
                            │
                            ▼
                         RESPONSE


             RELATIONSHIP OBSERVATIONS
                       │
                       ▼
               COMMERCE SIGNALS
                       │
                       ▼
          ┌─────────────────────────┐
          │ Existing Commerce       │
          │ Authority               │
          │                         │
          │ Opportunity             │
          │ Eligibility             │
          │ Ranking                 │
          │ Sealing                │
          │ Execution              │
          └─────────────────────────┘
```

The core architectural objective is:

> **Build a chatbot that can remember and develop a relationship over time without confusing relationship development with commercial readiness, and without allowing an LLM or relationship state to bypass deterministic safety or commerce authority.**

The relationship system should make the conversation feel increasingly coherent, familiar, reciprocal, and context-aware.

It should not make the conversation increasingly sales-driven merely because the relationship is becoming stronger.

That separation is the central invariant of this roadmap.
