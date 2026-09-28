# Sunny — Relationship V2 Greenfield Implementation Plan

## 0. Purpose

This document defines the implementation plan for a **new Relationship V2 architecture** for Sunny.

Relationship V2 is **not an upgrade, refactor, cleanup, or extension of the existing relationship/memory system**.

It is a new subsystem built from first principles.

The existing relationship, memory, trajectory, profile, summary, and conversational-state systems are treated as **legacy systems**. They may remain operational during construction, but they must not become the architectural foundation of V2.

The objective is to build a persistent relationship intelligence layer that models a fan as a person Sunny has an ongoing relationship with rather than treating every message as an isolated conversation.

---

# 1. Architectural Objective

The new system must provide Sunny with a persistent model of:

- who the fan is
- what the fan is like
- what matters to the fan
- what has happened between Sunny and the fan
- what Sunny has learned from previous interactions
- how the fan tends to communicate
- what the fan likes and dislikes conversationally
- important life events
- promises and commitments
- unresolved/open conversational threads
- intimate history
- romantic/intimate continuity
- commercial history
- current relationship state
- current conversation episode
- periods of absence and return
- meaningful historical facts
- changes in facts over time
- what Sunny should naturally remember or bring up now

The system must support **proactive memory**, not merely keyword-triggered retrieval.

The key architectural principle is:

> Sunny should know the fan as a continuing person and relationship, not as a collection of recent messages.

---

# 2. Greenfield Principle

## 2.1 Do not refactor the existing relationship architecture

Do NOT:

- extend `RelationshipState`
- add more fields to the existing profile
- turn `LongTermMemory` into V2
- turn `FanKnowledge` into V2
- expand `relationship_trajectory`
- expand `intimacy_trajectory`
- make `ConversationState` durable
- bolt more retrieval logic onto the existing memory system
- make the existing summaries the canonical relationship representation
- merge V2 into the old architecture incrementally

Instead:

```text
                 EXISTING SUNNY
                       │
        ┌──────────────┴──────────────┐
        │                             │
 Legacy Relationship             Commerce
 Legacy Memory                    Engine
 Legacy Trajectory                   │
 Legacy Profile                      │
 ConversationState                   │
        │                             │
        │                             │
        └───────────┐       ┌─────────┘
                    │       │
                    ▼       ▼
             Existing Runtime
                    │
                    │
             ───────┼───────
                    │
                    ▼
          NEW RELATIONSHIP V2
                    │
                    ▼
          Relationship Context
                    │
                    ▼
                   LLM
```

During construction, V2 should be able to operate independently.

---

# 3. What Remains Untouched

The following systems are not being replaced by Relationship V2.

## 3.1 Telegram transport

Keep the existing Telegram/Telethon infrastructure.

V2 should consume normalized interaction events rather than owning Telegram transport.

---

## 3.2 Redis Streams

Keep Redis Streams as the existing event/worker transport where appropriate.

V2 should not introduce a second unnecessary message transport.

---

## 3.3 LLM infrastructure

Keep:

- local llama.cpp
- OneCall contract
- response validation
- confidence handling
- routing
- handoff behavior
- existing provider infrastructure

V2 supplies better relationship context.

V2 does not replace the LLM.

---

## 3.4 Commerce

Commerce remains authoritative for:

- products
- prices
- availability
- ownership
- purchase history
- offers
- offer eligibility
- PPV state
- transaction execution
- purchase completion
- purchase/refund truth
- commercial eligibility

The relationship system may understand the **meaning** of commercial events.

It must never become the source of truth for commerce.

---

## 3.5 Operator system

Keep:

- operator queue
- approval flow
- rejection
- regeneration
- editing
- sending
- dashboard

But change the relationship event semantics.

Only an actually sent response becomes a real conversational event.

---

# 4. New High-Level Architecture

```text
                         TELEGRAM
                            │
                            ▼
                    Existing Intake
                            │
                            ▼
                    Normalized Event
                            │
                            ▼
                 ┌────────────────────┐
                 │ Relationship V2    │
                 │ Event Ingestion    │
                 └─────────┬──────────┘
                           │
             ┌─────────────┼──────────────┐
             │             │              │
             ▼             ▼              ▼
          Person       Relationship     Episode
          Memory          State          State
             │             │              │
             └─────────────┼──────────────┘
                           │
                           ▼
                  Memory / Event Store
                           │
                           ▼
                Relationship Reasoning
                           │
                           ▼
                  Proactive Recall
                           │
                           ▼
                Context Assembler
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
       Relationship Context       Commerce Context
              │                         │
              └────────────┬────────────┘
                           ▼
                          LLM
                           │
                           ▼
                    Response Candidate
                           │
                           ▼
                  Existing Validation
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ▼                           ▼
       Auto Send Path              Operator Queue
             │                           │
             │                     approve/edit
             │                           │
             └─────────────┬─────────────┘
                           ▼
                      Actual Send
                           │
                           ▼
                  ResponseSent Event
                           │
                           ▼
                  Relationship V2
                    Event Processor
```

The important distinction is:

> Generated response != relationship event.

Only an actual conversational event should mutate durable relationship state.

---

# 5. Core Domain Model

Relationship V2 should be centered around one root aggregate:

```text
FanRelationship
```

Conceptually:

```text
FanRelationship
│
├── Person
│   ├── identity
│   ├── background
│   ├── interests
│   ├── preferences
│   ├── routines
│   └── important facts
│
├── Relationship
│   ├── familiarity
│   ├── comfort
│   ├── trust
│   ├── reciprocity
│   ├── attraction
│   ├── intimacy
│   ├── continuity
│   └── engagement
│
├── Episodic Memory
│   ├── events
│   ├── conversations
│   ├── promises
│   ├── experiences
│   ├── important moments
│   └── open loops
│
├── Learned Patterns
│   ├── conversational preferences
│   ├── engagement patterns
│   ├── response patterns
│   ├── positive signals
│   └── negative signals
│
├── Intimate History
│   ├── intimate interactions
│   ├── recurring themes
│   ├── preferences
│   ├── successful teasing patterns
│   └── boundaries
│
├── Commerce Relationship
│   ├── purchase relationship
│   ├── purchase history
│   └── commercial opportunities
│
└── Current Episode
    ├── current topic
    ├── open threads
    ├── emotional momentum
    ├── intimate momentum
    ├── current objective
    └── recent context
```

---

# 6. Phase 0 — Freeze and Establish the Boundary

## Goal

Prevent the current architecture from expanding while V2 is being built.

This phase is about creating a clean boundary, not modifying legacy behavior.

## Tasks

### 6.1 Create a new package

Create:

```text
relationship_v2/
```

Suggested structure:

```text
relationship_v2/
    __init__.py

    domain/
        person.py
        relationship.py
        memory.py
        event.py
        episode.py
        pattern.py
        intimacy.py
        open_loop.py

    repositories/
        relationship_repository.py
        memory_repository.py
        event_repository.py
        episode_repository.py
        pattern_repository.py
        open_loop_repository.py

    services/
        event_processor.py
        memory_extractor.py
        memory_validator.py
        contradiction_resolver.py
        relationship_reasoning.py
        episode_manager.py
        pattern_learning.py
        proactive_recall.py
        absence_service.py

    context/
        assembler.py
        models.py

    integration/
        event_adapter.py
        commerce_adapter.py
        conversation_adapter.py
        operator_adapter.py

    policies/
        memory_policy.py
        intimacy_policy.py
        relationship_policy.py

    migrations/
        ...

    tests/
        ...
```

Do not move legacy files into this package.

Do not rename the old systems to make them appear to be V2.

---

## 6.2 Define ownership

Create an explicit architecture contract.

### Relationship V2 owns

- relationship memory
- person understanding
- episodic memory
- learned interaction patterns
- relationship state
- intimate history
- open loops
- proactive recall
- episode continuity

### Commerce owns

- prices
- products
- ownership
- purchase state
- offer eligibility
- transaction state
- commercial execution

### Telegram layer owns

- transport
- inbound/outbound messaging
- Telegram IDs
- delivery state

### Operator system owns

- human approval
- human edits
- rejection
- queue state

### LLM owns

- language generation
- interpretation proposals
- candidate memory observations

The LLM does NOT directly mutate V2 state.

---

# 7. Phase 1 — Define the Domain Model

## Goal

Build the conceptual model before building retrieval or prompts.

No LLM integration yet.

---

## 7.1 Person

Create a semantic person model.

Example:

```python
Person:
    identity
    background
    interests
    preferences
    routines
    important_facts
```

Each fact should support:

```text
value
fact_type
confidence
importance
first_observed_at
last_confirmed_at
source_event_id
status
supersedes
```

Do not use simple scalar fields where historical truth matters.

---

# 8. Phase 2 — Build the Event Model

Relationship V2 should be event-driven.

Events represent things that actually happened.

Suggested event types:

```text
FanMessageReceived
ResponseGenerated
ResponseRejected
ResponseEdited
ResponseApproved
ResponseSent
ResponseFailed

PurchaseCompleted
PurchaseRefunded
OfferPresented
OfferAccepted
OfferDeclined
ProductViewed
ContentDelivered

FanReturned
EpisodeStarted
EpisodeUpdated
EpisodeClosed
```

Important:

```text
ResponseGenerated
```

is NOT a relationship event.

It is an internal generation event.

The relationship system should only treat:

```text
ResponseSent
```

as the conversational response.

---

## 8.1 Event envelope

Every event should contain:

```text
event_id
event_type
fan_id
relationship_id
episode_id
message_id
generation_id
created_at
source
payload
schema_version
```

Use immutable events.

Never modify historical events.

---

# 9. Phase 3 — Build the Database

Create V2 tables independently of legacy memory tables.

Recommended tables:

```text
fan_relationships

relationship_events

relationship_memories

relationship_episodes

relationship_open_loops

relationship_patterns

relationship_intimate_history

relationship_processed_events
```

Optional later:

```text
relationship_memory_links
relationship_memory_embeddings
relationship_event_embeddings
```

Do not start by adding columns to:

```text
user_profiles
conversation_summaries
```

or other legacy relationship tables.

---

# 10. Phase 4 — Build FanRelationship

## Goal

Create the durable relationship root.

Suggested fields:

```text
id
fan_id

created_at
updated_at

relationship_state
relationship_state_reason

familiarity
comfort
trust
reciprocity
attraction
intimacy
continuity
engagement

current_momentum

first_interaction_at
last_interaction_at

current_episode_id

version
```

These values must not become arbitrary LLM-generated scores.

Each meaningful relationship state must have evidence.

Example:

```text
trust = high
```

should be explainable through:

```text
- long interaction history
- repeated personal disclosures
- consistent return behavior
- positive response patterns
- successful intimate interactions
```

The exact implementation may use derived state rather than raw numeric scores.

---

# 11. Phase 5 — Build Semantic Memory

This is one of the most important phases.

## Goal

Replace fragmented memory concepts with durable semantic memories.

Memory types:

```text
PERSON_FACT
PERSON_PREFERENCE
PERSON_DISLIKE
INTEREST
ROUTINE
LIFE_EVENT
RELATIONSHIP_EVENT
PROMISE
COMMITMENT
OPEN_LOOP
CONVERSATIONAL_PREFERENCE
INTIMATE_MEMORY
COMMERCE_RELATIONSHIP_MEMORY
```

Each memory should contain:

```text
id
relationship_id

type
content

importance
confidence

status

created_at
first_observed_at
last_observed_at

source_event_id
source_message_id

supersedes_memory_id
superseded_by_memory_id
```

---

# 12. Phase 6 — Contradiction and Versioning

The new architecture must preserve history.

Example:

```text
2026-01
Fan says:
"I work as an architect."

2026-07
Fan says:
"I quit architecture. I'm a photographer now."
```

V2 should represent:

```text
Historical:
Architect

Current:
Photographer
```

Not:

```text
Photographer
```

with the architect fact destroyed.

---

## 12.1 Memory status

Use states such as:

```text
ACTIVE
SUPERSEDED
REJECTED
UNCERTAIN
ARCHIVED
```

---

## 12.2 Supersession

When a new fact conflicts with an old fact:

```text
Old Memory
    │
    │ superseded by
    ▼
New Memory
```

The old memory remains queryable.

This allows Sunny to remember:

> "You used to work in architecture."

while still understanding:

> "You're a photographer now."

---

# 13. Phase 7 — Build Episodic Memory

V2 needs an explicit concept of events that happened in the fan's life.

Example:

```text
Fan:
"My daughter has a soccer game Saturday."

```

The system should create an episodic memory:

```text
type: LIFE_EVENT

subject:
daughter

event:
soccer game

expected_time:
Saturday

importance:
medium/high

follow_up_candidate:
true
```

Later:

```text
"How did your daughter's soccer game go?"
```

This should be possible even if the fan does not mention soccer in the current message.

---

# 14. Phase 8 — Build Open Loops

Create durable conversational threads.

Examples:

```text
daughter's soccer game
new job
upcoming vacation
doctor appointment
birthday
moving apartments
project deadline
family event
promise to send something
unfinished personal story
```

Open loop model:

```text
id
relationship_id
source_memory_id

description
expected_time

priority
status

last_referenced_at
follow_up_attempts

resolved_at
outcome
```

Statuses:

```text
OPEN
DUE
REFERENCED
RESOLVED
EXPIRED
DISMISSED
```

---

# 15. Phase 9 — Build Learned Interaction Patterns

The system should learn how a specific fan behaves.

Examples:

```text
responds well to teasing
likes short messages
likes longer emotional conversations
dislikes repeated questions
responds better at night
often initiates intimate conversation
responds poorly to abrupt selling
likes being asked about work
does not engage with generic compliments
```

These are probabilistic observations.

Do not encode:

```text
fan.likes_teasing = true
```

after one message.

Instead:

```text
pattern:
responds_well_to_teasing

confidence:
0.78

positive_evidence:
8

negative_evidence:
2

first_observed:
...

last_observed:
...
```

---

# 16. Phase 10 — Learn Negative Signals

Negative evidence is equally important.

Examples:

```text
ignored repeated question
ignored generic compliment
did not respond to specific sales transition
changed subject after certain topic
shortened replies after repetitive questioning
```

Do not interpret one ignored message as a permanent dislike.

Patterns should accumulate evidence.

---

# 17. Phase 11 — Build Intimate History

This must be semantic.

Do not reduce intimacy to:

```text
sexual_conversation = true
```

or:

```text
intimacy_score = 0.7
```

The system should retain meaningful history such as:

```text
previous intimate conversation
recurring themes
successful teasing patterns
preferred style
shared intimate moments
boundaries
turn-offs
positive reactions
relationship intimacy progression
```

The objective is continuity.

If the pair had an intimate conversation two days ago, Sunny should not behave as though the relationship reset simply because today's first message is about work.

---

# 18. Phase 12 — Build Relationship Reasoning

This service interprets the accumulated evidence.

Input:

```text
person memories
relationship memories
events
episodes
patterns
intimate history
absence
commerce relationship
current conversation
```

Output:

```text
relationship context
```

It should answer:

```text
Who is this person?

How long have we known each other?

What is our relationship like?

How comfortable are we?

What has been happening recently?

What does he tend to respond to?

What matters to him?

What is unfinished?

What happened previously?

What should Sunny remember right now?

Is there an active intimate thread?

Has he been away?

Did something important happen while he was away?
```

---

# 19. Phase 13 — Build Episode Management

The current conversation becomes an explicit episode.

Episode:

```text
id
relationship_id

started_at
last_activity_at
ended_at

status

current_topic
previous_topic

open_threads

current_objective

emotional_momentum
intimate_momentum

recent_events
```

An episode should NOT replace the permanent relationship.

Instead:

```text
FanRelationship
       │
       └── Episode
             │
             ├── messages
             ├── current topic
             ├── momentum
             └── open threads
```

---

# 20. Phase 14 — Build Proactive Recall

This is a major architectural feature.

Do not implement:

```text
current_message
    ↓
find matching keywords
    ↓
retrieve memory
```

Instead:

```text
Current Relationship Context
          │
          ▼
What does Sunny know?
          │
          ▼
What would make this interaction
more natural and personal?
          │
          ▼
Candidate memories
          │
          ▼
Rank candidates
          │
          ▼
Select small coherent set
```

Candidate sources:

```text
current episode
important memories
open loops
recent events
relationship history
interaction patterns
intimate history
absence history
meaningful life events
commerce relationship
```

Ranking factors:

```text
importance
recency
time sensitivity
emotional significance
relationship significance
unresolved status
novelty
follow-up value
current conversational relevance
```

Do not dump the entire database into the LLM.

---

# 21. Phase 15 — Build Absence and Return Intelligence

The system should understand absence as part of relationship continuity.

Example:

```text
last_interaction:
2026-08-20

current:
2026-09-20
```

The relationship should remain intact.

Sunny should know:

```text
long absence
known person
previous relationship history
previous intimate history
previous open loops
```

This allows natural responses such as:

```text
"Look who finally came back 😏"
```

when appropriate.

The system must not automatically use the exact same return phrase every time.

---

# 22. Phase 16 — Build Commerce Adapter

Do not rewrite commerce.

Create an adapter:

```text
relationship_v2/integration/commerce_adapter.py
```

The adapter reads commerce facts from the existing authoritative commerce engine.

Example context:

```text
buyer_status
purchase_count
purchase_history
owned_content
available_products
eligible_offers
active_offer
purchase_intent
recent_purchase
post_purchase_state
```

Relationship V2 may interpret:

```text
long-standing buyer
recent purchase
high commercial familiarity
previous successful offer
```

But it cannot invent:

```text
price
product
eligibility
purchase
ownership
offer
```

---

# 23. Phase 17 — Define the Commerce Boundary

The boundary must be explicit.

```text
RELATIONSHIP V2

"He's bought from us several times and tends to engage
well after personal conversation."

                │
                ▼

COMMERCE ENGINE

"Product X costs $Y.
Fan owns A and B.
Fan is eligible for Offer Z."

                │
                ▼

LLM

Generate natural language.
```

The LLM remains advisory.

Commerce remains deterministic.

---

# 24. Phase 18 — Build Context Assembly

Create:

```text
relationship_v2/context/assembler.py
```

The final context should be structured.

Suggested sections:

```text
WHO HE IS

WHAT YOU KNOW ABOUT HIM

WHAT HAS HAPPENED BETWEEN YOU

HOW HE TENDS TO RESPOND

WHAT IS IMPORTANT RIGHT NOW

OPEN LOOPS

CURRENT RELATIONSHIP STATE

CURRENT EPISODE

INTIMATE CONTINUITY

COMMERCE CONTEXT

CURRENT CONVERSATION
```

The assembler should return a typed context object.

Do not concatenate arbitrary database text.

---

# 25. Phase 19 — LLM Integration

Only after the relationship system works independently should it be connected to the LLM.

The LLM receives:

```text
Relationship V2 Context
+
Commerce Context
+
Current Conversation
+
Persona
```

The LLM generates:

```text
reply
commerce_signals
confidence
needs_handoff
```

The existing OneCall validation remains.

---

# 26. Phase 20 — LLM Observation Output

The LLM may optionally propose observations.

Example:

```json
{
  "memory_candidates": [
    {
      "type": "INTEREST",
      "content": "He has become interested in photography.",
      "confidence": 0.86
    }
  ],
  "pattern_candidates": [
    {
      "type": "CONVERSATIONAL_PREFERENCE",
      "content": "He responds well to playful teasing.",
      "confidence": 0.73
    }
  ]
}
```

These are proposals.

They must pass through:

```text
validation
→ confidence checks
→ contradiction resolver
→ persistence policy
```

The LLM must never directly write relationship state.

---

# 27. Phase 21 — Actual Conversation Event Semantics

This is critical.

Current problem:

```text
Draft generated
    ↓
memory extracted
    ↓
relationship mutated
    ↓
operator rejects draft
```

That is incorrect.

V2 must implement:

```text
Draft generated
    ↓
temporary candidate
    ↓
operator rejects
    ↓
DISCARD
```

No conversational relationship mutation.

---

## Approved and sent

```text
Draft generated
    ↓
operator approves/edits
    ↓
message sent
    ↓
ResponseSent
    ↓
relationship event processing
    ↓
memory/episode/pattern updates
```

The edited response is the actual response.

---

# 28. Phase 22 — Operator Edit Semantics

If Sunny generates:

```text
Draft:
"How was your day?"
```

Operator changes it to:

```text
"so how did that meeting with your boss end up going? 👀"
```

The relationship system must treat the second message as the actual conversational event.

It should not learn from the rejected/generated draft as though it was sent.

---

# 29. Phase 23 — Rejection and Regeneration

Correct lifecycle:

```text
Fan message
      │
      ▼
Draft A
      │
      ├── rejected
      │
      ▼
Draft B
      │
      ▼
queue
      │
      ▼
operator
      │
      ▼
approve/edit
      │
      ▼
send
```

Draft A:

```text
NOT a relationship event
```

Draft B:

```text
NOT a relationship event
```

Final sent response:

```text
REAL relationship event
```

---

# 30. Phase 24 — Concurrency Protection

The existing audit identified a potential cross-generation ordering issue:

```text
Old operator-approved response
+
new inbound message
```

may potentially result in both being sent sequentially.

V2 should introduce a send-ordering contract.

For each fan:

```text
fan relationship lock
```

should protect:

```text
inbound processing
operator approval
send
relationship event commit
```

The exact locking implementation should integrate with the existing per-fan lock/generation system rather than creating an unrelated locking mechanism.

---

# 31. Phase 25 — Idempotent Event Processing

Every event processor must be safe to retry.

Use:

```text
relationship_processed_events
```

with:

```text
event_id
processor
processed_at
```

Unique constraint:

```text
(event_id, processor)
```

Processing:

```text
receive event
    ↓
check processed_events
    ↓
already processed?
    ├── yes → return
    └── no
         ↓
process
         ↓
persist changes
         ↓
mark processed
```

Where possible, event application and processed-event insertion should occur atomically.

---

# 32. Phase 26 — Build V2 in Shadow Mode

Do not immediately replace the live relationship system.

Add:

```text
RELATIONSHIP_V2_ENABLED
RELATIONSHIP_V2_READ_ENABLED
RELATIONSHIP_V2_WRITE_ENABLED
RELATIONSHIP_V2_SHADOW_MODE
```

Shadow mode:

```text
Fan message
     │
     ├──────── Existing system
     │
     └──────── V2
```

V2 can:

- process events
- build memories
- build episodes
- build relationship state
- assemble context
- generate candidate context
- log differences

But it must not control production replies yet.

---

# 33. Phase 27 — Backfill Strategy

Do not blindly migrate the old memory database.

The old system has known weaknesses:

- capped memories
- topic-gated retrieval
- scalar overwrite
- summaries
- fragmented relationship models
- weak intimate history
- no proper episodes

Therefore:

```text
Legacy memory
      ↓
candidate historical evidence
      ↓
V2 migration validator
      ↓
high-confidence facts
      ↓
V2
```

Only migrate information that can be interpreted safely.

---

## 33.1 Example

Legacy:

```text
occupation = photographer
```

Import as:

```text
PERSON_FACT
occupation
photographer
confidence: high
historical_source: legacy_profile
```

If older evidence exists:

```text
architect
```

retain it as historical rather than deleting it.

---

# 34. Phase 28 — Replay Historical Events

If reliable historical messages are available, V2 should eventually support event replay.

Conceptually:

```text
Historical Telegram message
        ↓
Normalized FanMessageReceived
        ↓
V2 memory extraction
        ↓
V2 relationship processing
        ↓
V2 episode reconstruction
```

Do this only after the live event model is stable.

Do not make historical replay a prerequisite for the first functional V2.

---

# 35. Phase 29 — Integration with Existing `llm_worker.py`

The audit identified `workers/llm_worker.py` as the central orchestration path.

Do not rewrite the entire worker.

Create a clean integration boundary.

Conceptually:

```python
relationship_context = relationship_v2.get_context(
    fan_id=fan_id,
    conversation_id=conversation_id,
    current_message=current_message,
)
```

Then:

```text
relationship_context
+
commerce_context
+
persona
+
conversation
```

goes into the existing LLM pipeline.

---

# 36. Phase 30 — Disable Legacy Relationship Authority

Only after V2 passes shadow testing.

Legacy systems should become:

```text
READ: disabled as relationship authority
WRITE: disabled
```

Specifically isolate the old systems identified by the audit:

```text
commerce/relationship.py
commerce/relationship_trajectory.py
commerce/intimacy_trajectory.py

memory/profile.py
commerce/long_term_memory.py
commerce/fan_knowledge.py
memory/summarizer.py
```

They may remain temporarily for compatibility if another subsystem still requires them.

But they must not determine V2 relationship context.

---

# 37. Phase 31 — Handle `ConversationState`

Do not delete `ConversationState` merely because V2 exists.

It can remain useful for transient mechanics such as:

```text
current processing turn
temporary generation state
debounce state
current response mechanics
```

But it must not become the durable relationship database.

Correct separation:

```text
ConversationState
=
temporary runtime state

Relationship V2
=
persistent relationship state
```

---

# 38. Phase 32 — Commerce Relationship State

The current commercial `RelationshipState` should not be confused with the new human relationship.

It can remain inside commerce if commerce requires it.

Prefer an explicit boundary such as:

```text
CommercialRelationshipState
```

or a commerce adapter.

Example:

```text
Commerce:
PURCHASED
REPEAT_BUYER
VIP
COOLING_DOWN
DO_NOT_PUSH
```

Relationship V2:

```text
familiarity
comfort
trust
reciprocity
attraction
intimacy
continuity
engagement
```

These are different domains.

---

# 39. Phase 33 — Testing Strategy

Testing must be built alongside each phase.

---

## 39.1 Memory tests

Test:

```text
new fact
same fact repeated
fact contradiction
fact supersession
historical fact preservation
confidence
importance
source provenance
```

Example:

```text
architect
→ photographer
```

Expected:

```text
photographer = current
architect = historical
```

---

## 39.2 Episodic tests

Test:

```text
daughter soccer game
birthday
vacation
new job
appointment
promise
unfinished story
```

Verify later recall.

---

## 39.3 Proactive recall tests

Current message should NOT explicitly mention the memory.

Example:

Previous:

```text
"My daughter has a soccer game Saturday."
```

Later:

```text
"hey, just got home"
```

V2 should be able to identify:

```text
soccer game
```

as a possible natural recall candidate.

---

# 40. Phase 34 — Relationship Continuity Tests

Test:

```text
new fan
known fan
long-term fan
48h absence
3 day absence
30 day absence
return after long absence
```

Verify that relationship history persists.

---

# 41. Phase 35 — Interaction Learning Tests

Test positive:

```text
fan repeatedly engages with teasing
```

Expected:

```text
teasing preference confidence increases
```

Test negative:

```text
fan repeatedly ignores generic compliments
```

Expected:

```text
generic compliment preference decreases
```

Do not overfit from one example.

---

# 42. Phase 36 — Intimacy Tests

Test:

```text
previous intimate conversation
topic shift
return to intimate topic
intimate preference
successful teasing pattern
boundary
turn-off
```

The system should preserve continuity.

---

# 43. Phase 37 — Commerce Isolation Tests

Verify:

```text
relationship memory cannot invent price
relationship memory cannot invent product
relationship memory cannot authorize offer
relationship memory cannot mark purchase complete
LLM cannot authorize transaction
```

Commerce remains authoritative.

---

# 44. Phase 38 — Operator Tests

Test:

```text
Draft A generated
Draft A rejected
Draft B generated
Draft B queued
Draft B edited
Draft B approved
Draft B sent
```

Expected:

```text
Draft A:
no relationship mutation

Draft B:
no relationship mutation until sent

Edited B:
actual relationship event

ResponseSent:
relationship state updated
```

---

# 45. Phase 39 — Retry and Idempotency Tests

Simulate:

```text
same event twice
worker crash
Redis retry
XAUTOCLAIM
duplicate Telegram event
duplicate purchase event
duplicate ResponseSent
```

Expected:

```text
one logical relationship mutation
```

---

# 46. Phase 40 — Concurrency Tests

Simulate:

```text
fan sends message
operator approves old draft
fan sends new message
```

Verify that the system does not create contradictory or incorrectly ordered conversational events.

Also test:

```text
two workers
same fan
same event
```

and:

```text
two simultaneous relationship updates
```

---

# 47. Phase 41 — Context Quality Testing

Build fixtures for fans with:

```text
personal facts
preferences
life events
open loops
relationship history
intimate history
commerce history
long absence
contradictory facts
negative preferences
```

Verify the final context is:

- relevant
- coherent
- compact
- chronological where needed
- not repetitive
- not overloaded
- not fabricated

---

# 48. Phase 42 — Shadow Comparison

During shadow mode compare:

```text
Legacy context
vs
V2 context
```

and:

```text
Legacy draft
vs
V2-informed draft
```

Do not automatically select a winner.

Inspect:

```text
memory accuracy
relationship continuity
personalization
commerce correctness
intimacy continuity
false memories
irrelevant recall
repetition
context size
```

---

# 49. Phase 43 — Gradual Cutover

Recommended progression:

```text
Stage 1
V2 database only

Stage 2
V2 event ingestion

Stage 3
V2 memory processing

Stage 4
V2 shadow context

Stage 5
V2 shadow LLM

Stage 6
V2 context becomes live

Stage 7
V2 becomes relationship source of truth

Stage 8
legacy relationship/memory writes disabled

Stage 9
legacy relationship/memory reads removed
```

Commerce remains live throughout.

---

# 50. Phase 44 — Production Flags

Final production state:

```text
RELATIONSHIP_V2_ENABLED=true

RELATIONSHIP_V2_READ_ENABLED=true

RELATIONSHIP_V2_WRITE_ENABLED=true

RELATIONSHIP_V2_SHADOW_MODE=false

LEGACY_RELATIONSHIP_ENABLED=false

LEGACY_MEMORY_ENABLED=false
```

Commerce controls remain independent.

Do not create a single global kill switch that disables commerce accidentally.

---

# 51. Phase 45 — Rollback

Rollback must be possible.

If V2 fails:

```text
RELATIONSHIP_V2_READ_ENABLED=false
RELATIONSHIP_V2_WRITE_ENABLED=false
LEGACY_RELATIONSHIP_ENABLED=true
LEGACY_MEMORY_ENABLED=true
```

Commerce must remain unaffected.

V2 data should remain intact for diagnosis.

Do not delete V2 state during rollback.

---

# 52. Phase 46 — Observability

Every relationship mutation should be traceable.

Log:

```text
relationship_id
fan_id
event_id
episode_id
generation_id
memory_id
pattern_id
source_message_id
processor
timestamp
```

Useful debug query:

```text
Why does Sunny currently believe this?
```

The system should be able to trace:

```text
Current belief
    ↓
memory
    ↓
source event
    ↓
source message
```

This is essential for debugging hallucinated or incorrect memories.

---

# 53. Phase 47 — Relationship Debug View

Add an operator/debug view eventually showing:

```text
Fan

Relationship
├── familiarity
├── comfort
├── trust
├── reciprocity
├── attraction
├── intimacy
├── continuity
└── engagement

Person
├── facts
├── interests
├── preferences
└── routines

Memories

Open Loops

Episodes

Interaction Patterns

Intimate History

Recent Events

Commerce Relationship

Last Interaction
```

This is not required for the first implementation, but is extremely valuable during development.

---

# 54. Phase 48 — Definition of Done

Relationship V2 is considered functional when all of the following are true.

## Person understanding

Sunny can remember:

- who the fan is
- work
- interests
- routines
- personal facts
- preferences
- important life events

---

## Historical continuity

Sunny preserves:

- old facts
- current facts
- changed facts
- relationship history

---

## Episodic memory

Sunny can remember:

- things that happened
- things expected to happen
- promises
- unfinished conversations
- meaningful events

---

## Proactive recall

Sunny can naturally recall important information even when the current message does not mention it.

---

## Relationship understanding

Sunny understands:

- familiarity
- comfort
- trust
- reciprocity
- intimacy
- continuity
- engagement

without treating these as arbitrary LLM scores.

---

## Interaction learning

Sunny learns:

- what the fan engages with
- what the fan ignores
- what style works
- what style does not work
- conversational preferences

---

## Intimacy

Sunny retains semantic intimate history.

---

## Absence

Sunny understands:

- when someone disappeared
- how long they were gone
- that the relationship still exists
- how to naturally resume interaction

---

## Commerce

Commerce remains deterministic and authoritative.

---

## Operator workflow

Only the final sent response becomes a relationship event.

---

## Reliability

Relationship processing is:

- idempotent
- retry-safe
- concurrency-safe
- observable

---

# 55. Recommended Implementation Order

The actual implementation should follow this order.

```text
PHASE 0
Boundary + feature flags

        ↓

PHASE 1
Domain models

        ↓

PHASE 2
Event model

        ↓

PHASE 3
Database

        ↓

PHASE 4
FanRelationship

        ↓

PHASE 5
Semantic memory

        ↓

PHASE 6
Contradiction/versioning

        ↓

PHASE 7
Episodes

        ↓

PHASE 8
Open loops

        ↓

PHASE 9
Interaction patterns

        ↓

PHASE 10
Negative learning

        ↓

PHASE 11
Intimate history

        ↓

PHASE 12
Relationship reasoning

        ↓

PHASE 13
Episode management

        ↓

PHASE 14
Proactive recall

        ↓

PHASE 15
Absence/return

        ↓

PHASE 16
Commerce adapter

        ↓

PHASE 17
Context assembler

        ↓

PHASE 18
LLM integration

        ↓

PHASE 19
Operator event semantics

        ↓

PHASE 20
Concurrency/idempotency

        ↓

PHASE 21
Shadow mode

        ↓

PHASE 22
Historical migration/replay

        ↓

PHASE 23
Context cutover

        ↓

PHASE 24
Legacy shutdown

        ↓

PHASE 25
Production hardening
```

---

# 56. Critical Implementation Rule

Do not let implementation pressure turn this into:

```text
old memory system
+
more tables
+
more prompts
=
Relationship V2
```

That would recreate the existing architecture with additional complexity.

The intended architecture is:

```text
                FAN
                 │
                 ▼
        FanRelationship
                 │
       ┌─────────┼─────────┐
       │         │         │
       ▼         ▼         ▼
    Person    Episodes   Events
       │         │         │
       └────┬────┴────┬────┘
            │         │
            ▼         ▼
        Memories   Patterns
            │         │
            └────┬────┘
                 ▼
        Relationship Reasoning
                 │
                 ▼
          Proactive Recall
                 │
                 ▼
       Relationship Context
                 │
          ┌──────┴──────┐
          │             │
          ▼             ▼
   Commerce Context   Current Chat
          │             │
          └──────┬──────┘
                 ▼
                LLM
                 │
                 ▼
           Actual Response
                 │
                 ▼
            ResponseSent
                 │
                 ▼
        Relationship Event
```

The relationship is therefore a **persistent domain model**, not a prompt feature.

---

# 57. What NOT To Do

Do not:

- expand the old profile system
- expand `LongTermMemory`
- expand `FanKnowledge`
- make summaries the source of truth
- make raw messages the only memory
- use keyword overlap as the primary memory architecture
- store only relationship scores
- store only trajectory bands
- store only intimacy counters
- treat sexual history as a boolean/counter
- destroy old facts when new facts arrive
- let generated drafts mutate durable relationship state
- let rejected drafts become memories
- let the LLM directly modify relationship state
- let relationship memory authorize commerce
- let commerce become the relationship model
- make every memory permanently relevant
- dump every memory into the prompt
- create a second Telegram transport
- create a second commerce engine
- rewrite the entire `llm_worker.py`
- delete `ConversationState` simply because V2 exists
- migrate every legacy memory blindly
- make migration a prerequisite for first functionality
- make scheduler logic responsible for relationship intelligence

---

# 58. First Functional Milestone

The first usable milestone should NOT attempt to implement everything.

Build this minimum greenfield slice first:

```text
FanMessageReceived
        ↓
FanRelationship
        ↓
Semantic Memory
        ↓
Episode
        ↓
Relationship Context
        ↓
Existing LLM
        ↓
ResponseSent
        ↓
Relationship Event
```

This proves the fundamental architecture.

Then add:

```text
contradictions
open loops
interaction learning
negative learning
intimate history
proactive recall
absence intelligence
commerce adapter
```

incrementally.

---

# 59. Final Architecture Principle

The final Sunny architecture should behave conceptually like this:

```text
A conversation is not the relationship.

A message is not the relationship.

A summary is not the relationship.

A trajectory score is not the relationship.

A memory row is not the relationship.

The relationship is the persistent model that connects
the person, history, events, memories, patterns,
intimacy, episodes, and current interaction.
```

The LLM should receive that model as context and turn it into natural conversation.

The LLM should not be responsible for maintaining the model itself.

Commerce should remain a separate deterministic authority.

And the only conversational reality that permanently changes the relationship is what actually happened between Sunny and the fan.
