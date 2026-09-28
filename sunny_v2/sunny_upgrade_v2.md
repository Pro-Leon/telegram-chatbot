# Sunny — Relationship V2 Architecture & Implementation Handoff

## 1. Document Purpose

This document is the technical specification for replacing Sunny's existing relationship, memory, intimacy, and conversational-state architecture with a new architecture built around a persistent **Fan Relationship**.

The implementation must be treated as a **new subsystem**, not as an incremental refactor of the existing relationship/memory implementation.

The existing deterministic commerce subsystem must remain operational and authoritative.

The desired end state is:

```text
                    TELEGRAM
                       │
                       ▼
                MESSAGE INGESTION
                       │
                       ▼
              CONVERSATION ORCHESTRATOR
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
      RELATIONSHIP V2        COMMERCE
             │                   │
             │                   │
             └─────────┬─────────┘
                       │
                       ▼
                  LLM CONTEXT
                       │
                       ▼
                   LLM DRAFT
                       │
                       ▼
              VALIDATION / ROUTING
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
            SEND              OPERATOR
                                 │
                                 ▼
                         ACTUAL RESPONSE
                                 │
                                 ▼
                       RELATIONSHIP EVENTS
```

The architecture must preserve the separation between:

* relationship intelligence
* memory
* conversation state
* intimacy history
* commerce
* authoritative system facts
* LLM generation
* operator review
* outbound delivery

---

# 2. Core Architectural Decision

The previous architecture attempted to represent the relationship through multiple partially overlapping systems:

* `RelationshipState`
* relationship trajectory
* intimacy trajectory
* `fan_knowledge`
* long-term memory
* profile extraction
* conversation state
* summaries
* recent-message retrieval
* topic-based memory retrieval

This architecture is being retired as the source of truth.

The new architecture has one conceptual center:

> **Fan Relationship**

The Fan Relationship represents everything Sunny needs to know to behave as though she genuinely knows a person over time.

It is not merely a database profile.

It is a persistent model of:

```text
WHO HE IS
+
WHAT HAS HAPPENED BETWEEN THEM
+
HOW HE TENDS TO RESPOND
+
WHAT MATTERS TO HIM
+
WHAT SUNNY SHOULD REMEMBER
+
HOW THEIR RELATIONSHIP HAS DEVELOPED
+
WHAT IS CURRENTLY HAPPENING
+
WHAT INTIMATE HISTORY EXISTS
```

---

# 3. Non-Negotiable Principles

## 3.1 Commerce remains authoritative

Do not rebuild commerce.

Do not move commerce decisions into Relationship V2.

Do not allow the LLM to determine:

* prices
* products
* purchase status
* ownership
* transaction state
* offer eligibility
* offer validity
* purchase execution
* post-purchase transaction state

The existing deterministic commerce engine remains the authority.

Relationship V2 may consume commerce facts.

Commerce may emit relationship-relevant events.

Neither subsystem becomes the other.

---

## 3.2 Relationship V2 becomes the authority for relationship knowledge

Relationship V2 becomes authoritative for:

* personal memories
* relationship memories
* episodic memories
* open loops
* interaction patterns
* intimacy history
* conversational preferences
* learned engagement/disengagement patterns
* relationship state
* current relationship episode
* longitudinal relationship continuity

---

## 3.3 The LLM is not the database

The LLM may propose:

* memories
* observations
* relationship interpretations
* interaction patterns
* event classifications
* follow-up opportunities

The LLM must not directly mutate persistent relationship state.

The relationship subsystem validates and persists observations.

This prevents hallucinated memories from becoming facts.

---

## 3.4 Current conversation is not the entire relationship

The current conversation is only one episode within a persistent relationship.

Conceptually:

```text
Fan Relationship
│
├── Historical relationship
│
├── Personal memory
│
├── Episodic memory
│
├── Interaction patterns
│
├── Intimate history
│
├── Open loops
│
├── Relationship state
│
└── Current episode
       │
       ├── current topic
       ├── recent messages
       ├── current momentum
       ├── unresolved thread
       └── current objective
```

---

# 4. Legacy Architecture Shutdown

The existing relationship/memory architecture must not remain a competing source of truth.

The following systems are considered legacy:

```text
RelationshipState
relationship_trajectory
intimacy_trajectory
fan_knowledge
long_term_memory
legacy profile extraction as relationship authority
legacy proactive memory retrieval
legacy topic-based relationship retrieval
legacy relationship progression
legacy intimacy progression
relationship semantics inside ConversationState
```

These systems must be disabled from the active decision path.

They should initially remain physically present for rollback/reference purposes.

Do not delete them during the first implementation.

---

# 5. What Remains Operational

The following existing infrastructure should remain operational unless explicitly replaced later:

```text
Telegram / MTProto
message ingestion
debounce / merge
Redis Streams
worker infrastructure
locking
generation IDs
LLM infrastructure
operator queue
send worker
purchase webhooks
commerce engine
commerce database
transaction handling
purchase attribution
outbound delivery
```

The migration is therefore:

```text
OLD APPLICATION
        │
        ├── transport        KEEP
        ├── infrastructure   KEEP
        ├── commerce         KEEP
        ├── sending          KEEP
        │
        └── relationship     REPLACE
```

---

# 6. New Domain Model

The new system should contain the following conceptual entities.

```text
FanRelationship
PersonMemory
RelationshipMemory
Episode
EpisodeEvent
InteractionPattern
OpenLoop
IntimateMemory
RelationshipState
ConversationMomentum
RelationshipObservation
RelationshipContext
```

Commerce remains outside this domain.

---

# 7. Fan Relationship

`FanRelationship` is the root aggregate.

Conceptually:

```python
FanRelationship:
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
    last_interaction_at
    first_interaction_at

    current_episode_id

    version
```

These fields are not intended to become another collection of arbitrary numeric counters.

The state must be derived from evidence and persisted observations.

The system must retain explanatory evidence.

For example:

```text
comfort = high

because:
- long conversation history
- repeated voluntary returns
- personal disclosure
- positive response to teasing
- multiple successful intimate conversations
```

Do not create opaque values with no provenance.

---

# 8. Person Memory

Person memory represents facts about the fan.

Examples:

```text
occupation
location
family
children
siblings
pets
work
hobbies
sports
music
food
travel
routine
friends
relationship status
interests
preferences
goals
important personal facts
```

A memory must contain provenance.

Example:

```json
{
  "memory_type": "person",
  "category": "occupation",
  "value": "photographer",
  "confidence": 0.96,
  "source_event_id": "...",
  "first_observed_at": "...",
  "last_confirmed_at": "...",
  "status": "current"
}
```

---

# 9. Historical vs Current Facts

Contradictions must not destroy history.

Example:

Day 1:

```text
"I'm an architect."
```

Later:

```text
"I've been doing photography full time for a year."
```

The system should represent:

```text
Historical:
    architect

Current:
    photographer
```

The historical fact is not deleted.

The new fact becomes current because it is newer and directly contradicts the old current value.

Memory lifecycle:

```text
observed
   ↓
candidate
   ↓
validated
   ↓
active
   ↓
superseded
```

A superseded memory remains queryable as history.

---

# 10. Relationship Memory

Relationship memory describes things about the relationship itself.

Examples:

```text
They joke about football.
He likes when Sunny teases him.
He responds well to playful challenge.
They have an established flirtatious dynamic.
He tends to open up when conversation is relaxed.
He dislikes overly formal responses.
They have previously talked about his work.
```

This is different from:

```text
He is a photographer.
```

The first is relationship knowledge.

The second is person knowledge.

---

# 11. Episodic Memory

Episodes represent meaningful events.

Examples:

```text
promotion
trip
birthday
daughter's soccer game
job interview
work presentation
holiday
argument
reconciliation
important conversation
sexual interaction
purchase-related interaction
personal disclosure
promise
future plan
```

Example:

```json
{
  "type": "personal_event",
  "title": "daughter's soccer game",
  "description": "Fan said his daughter had a soccer game on Saturday.",
  "importance": "high",
  "status": "open",
  "follow_up_candidate": true
}
```

This enables:

> "How did your daughter's game go?"

without the fan needing to remind Sunny.

---

# 12. Episodic Memory Lifecycle

Every meaningful event follows:

```text
EVENT
  ↓
OBSERVATION
  ↓
MEMORY
  ↓
OPEN LOOP
  ↓
FOLLOW-UP
  ↓
OUTCOME
  ↓
RELATIONSHIP HISTORY
```

Example:

```text
Fan:
"My daughter has a soccer game Saturday."

        ↓

Memory:
daughter has soccer game Saturday

        ↓

Open loop:
ask about game after Saturday

        ↓

Later:
"How did her game go?"

        ↓

Fan:
"They won 3-1."

        ↓

Event outcome:
game completed, daughter won 3-1

        ↓

Relationship history:
Sunny remembered and followed up.
```

This should be a first-class behavior.

---

# 13. Open Loops

An open loop is something Sunny should remember and potentially return to later.

Examples:

```text
job interview tomorrow
presentation next week
daughter's soccer game
trip
doctor appointment
birthday
new project
planned vacation
something fan promised to tell Sunny
unfinished conversation
future plan
```

An open loop contains:

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
open
due
referenced
resolved
expired
dismissed
```

---

# 14. Interaction Patterns

Interaction patterns are learned behavioral observations.

Examples:

```text
responds well to teasing
likes short messages
likes playful challenges
dislikes repeated questions
often disappears when conversation becomes too transactional
responds strongly to personal attention
often initiates sexual conversations late at night
engages more when Sunny shares something rather than asking questions
responds poorly to abrupt topic changes
```

These are probabilistic observations.

They are not absolute rules.

Never store:

```text
"Fan hates questions."
```

when the evidence only suggests:

```text
"Fan appears less responsive when Sunny asks multiple questions consecutively."
```

Patterns should contain:

```text
pattern
confidence
positive_evidence_count
negative_evidence_count
first_observed
last_observed
supporting_events
status
```

---

# 15. Learn Non-Engagement

The system must explicitly learn what does not work.

Examples:

```text
topic repeatedly ignored
teasing repeatedly receives no engagement
long responses lead to shorter replies
certain conversational approaches reduce engagement
specific sales transitions cause disengagement
```

Negative evidence is useful.

However, a single ignored message must not create a permanent negative preference.

Use evidence accumulation.

Conceptually:

```text
1 observation → weak signal
3 observations → meaningful pattern
many consistent observations → strong pattern
contradictory observations → reduce confidence
```

---

# 16. Intimate History

Intimacy must be represented as actual relationship history, not merely as an intimacy counter.

It may contain:

```text
previous intimate conversations
flirting history
sexual conversation themes
successful teasing patterns
intimacy escalation events
shared intimate moments
boundaries
preferences
recurring themes
```

The architecture must preserve continuity.

If Sunny and the fan are in an intimate conversation and the fan suddenly changes topic, the system should not automatically reset the relationship context.

The current momentum should remain available to the LLM.

Example:

```text
Current momentum:
    intimate

Fan:
"So anyway, what did you eat today?"
```

The LLM may naturally preserve the previous dynamic rather than behaving as though the intimate conversation never happened.

---

# 17. Current Relationship State

Relationship state describes the current interpretation of the relationship.

It is not memory.

Example:

```json
{
  "familiarity": "established",
  "comfort": "high",
  "trust": "high",
  "reciprocity": "high",
  "intimacy": "deep",
  "current_momentum": "intimate",
  "continuity": "strong",
  "engagement": "high"
}
```

These are current interpretations derived from historical evidence.

They should not replace the evidence.

---

# 18. Relationship State Categories

The system may use descriptive bands such as:

```text
familiarity:
    stranger
    familiar
    established
    longstanding

comfort:
    low
    moderate
    high

trust:
    low
    moderate
    high

reciprocity:
    low
    moderate
    high

intimacy:
    none
    playful
    romantic
    intimate
    deeply_intimate

engagement:
    low
    moderate
    high

continuity:
    fragile
    stable
    strong
```

Do not recreate the old system's exact trajectory logic.

These states must be derived from the new relationship model.

---

# 19. Current Episode

Each conversation should belong to an episode.

An episode contains:

```text
episode_id
relationship_id
started_at
last_activity_at

current_topic
previous_topic
open_threads

conversation_momentum
emotional_momentum
intimate_momentum

current_objective

recent_messages
recent_events

pending_question
pending_response
```

An episode can survive multiple message exchanges.

It should not necessarily end after every message.

---

# 20. Episode Boundaries

An episode may end because:

```text
conversation naturally ended
long inactivity
explicit goodbye
operator closure
system-defined timeout
```

However, ending an episode does not erase relationship context.

New conversation:

```text
Episode #27
     ↓
same FanRelationship
```

The fan never becomes a new person merely because the conversation ended.

---

# 21. Long Absences

Time since last interaction is relationship context.

For example:

```text
last interaction:
30 days ago

current:
"hey gorgeous"
```

The LLM should receive:

```text
This is a longstanding relationship.
They have not spoken for approximately 30 days.
There is existing history.
```

This allows a natural response such as:

> "look who finally came back 😏"

without pretending they just met.

Do not hardcode exact phrases.

The relationship context informs generation.

---

# 22. Proactive Recall

This is a core feature.

Retrieval must NOT only ask:

> "Which memories match the current message?"

It must ask:

> "What does Sunny know about this person that would make the current response more natural, personal, continuous, or emotionally intelligent?"

Candidate sources:

```text
recent episode
important memories
open loops
recent unresolved topics
relationship history
interaction patterns
intimate history
long absences
recent meaningful events
personal milestones
previous conversations
commercial relationship context
```

The system should rank candidates.

Possible ranking dimensions:

```text
importance
recency
time sensitivity
relationship significance
unresolved status
novelty
follow-up value
current contextual relevance
emotional significance
```

Do not dump every memory into the LLM.

Return a small coherent set.

---

# 23. Memory Retrieval Must Be Semantic

Do not depend only on exact topic overlap.

Example:

Memory:

```text
"Fan was preparing for a presentation at work."
```

Current message:

```text
"finally got some time to breathe"
```

A pure lexical search may miss the connection.

Relationship retrieval should be able to identify:

```text
work presentation
stress
recent work event
open loop
```

as relevant context.

Use semantic retrieval where appropriate, but the architecture must not depend on a specific vector database.

PostgreSQL plus a suitable embedding/retrieval implementation is acceptable.

The important requirement is behavior, not a particular infrastructure product.

---

# 24. Context Assembly

The LLM context must be structured.

Do not send:

```text
random memory records
+
database rows
+
old summaries
+
current messages
```

Instead assemble:

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

The LLM should understand the relationship as a coherent entity.

---

# 25. Example LLM Context

Conceptually:

```text
PERSON
- Photographer
- Has a daughter
- Likes football
- Lives in ...
- Recently changed jobs

RELATIONSHIP
- Longstanding relationship
- Comfortable with Sunny
- Playful dynamic
- Frequently jokes with Sunny
- Has previously responded well to teasing

IMPORTANT HISTORY
- Daughter had a soccer game recently
- Recently started a new project
- Sunny previously promised to ask how it went

OPEN LOOPS
- Ask about the new project if natural
- Follow up on daughter's game if appropriate

INTERACTION PATTERNS
- Responds well to teasing
- Does not engage strongly with repeated questions
- Short playful messages usually receive better engagement

INTIMATE HISTORY
- Previous flirtatious conversations
- Existing intimate continuity
- Current intimate thread: active

CURRENT EPISODE
- Current topic: work
- Previous topic: flirting
- Momentum: playful/intimate
- Last interaction: 3 days ago

COMMERCE
- Repeat buyer
- Owned content: [...]
- Available eligible product: [...]
- Verified price: [...]
- Current opportunity: [...]
```

Then:

```text
RECENT CONVERSATION
...
```

---

# 26. LLM Responsibilities

The LLM is responsible for:

* natural language generation
* tone
* conversational interpretation
* deciding how to express a response
* selecting natural conversational transitions
* using supplied relationship context
* proposing memories
* proposing relationship observations
* proposing interaction patterns
* proposing episode outcomes

---

# 27. LLM Non-Responsibilities

The LLM must not independently determine:

```text
purchase status
price
product existence
ownership
transaction state
offer eligibility
creator identity
available content
memory truth
historical fact truth
relationship database state
```

It can propose:

```text
"Fan appears to have mentioned a new job."
```

The relationship engine decides whether that becomes memory.

---

# 28. Relationship Observation Pipeline

After each meaningful conversation:

```text
Conversation
    ↓
LLM observation extraction
    ↓
candidate observations
    ↓
validation
    ↓
contradiction resolution
    ↓
memory persistence
    ↓
episode update
    ↓
relationship state update
    ↓
pattern learning
    ↓
open-loop updates
```

---

# 29. Observation Validation

Every candidate memory should have:

```text
source message/event
confidence
observation type
subject
value
temporal information
```

The system must reject observations that:

* were not actually present
* contradict authoritative system facts
* are unsupported assumptions
* are inferred solely from generic language
* are invented by the LLM

Example:

Fan:

> "I had a rough day."

Valid:

```text
Fan had a rough day.
```

Invalid:

```text
Fan is depressed.
```

The second is an unsupported interpretation.

---

# 30. Commerce Integration

Commerce should expose a stable service interface.

Conceptually:

```python
commerce_context = commerce.get_context(
    fan_id=fan_id,
    conversation_id=conversation_id
)
```

Potential context:

```text
buyer_status
purchase_history
owned_content
available_products
eligible_offers
active_offer
purchase_intent
post_purchase_state
```

The exact existing commerce implementation should remain authoritative.

Do not duplicate its database logic into Relationship V2.

---

# 31. Commerce Events

Commerce may emit events such as:

```text
PurchaseCompleted
PurchaseRefunded
OfferPresented
OfferAccepted
OfferDeclined
ProductViewed
ContentDelivered
```

Relationship V2 can consume these to update relationship history.

Example:

```text
PurchaseCompleted
        ↓
Relationship Event
        ↓
"Fan purchased content from Sunny."
        ↓
Relationship history updated
```

The relationship system does not decide whether the purchase actually happened.

---

# 32. Selling Behavior

The relationship system must not suppress legitimate commerce opportunities.

The desired behavior is:

```text
No artificial selling
+
No irrelevant offers
+
No manufactured commercial intent
+
Do not suppress legitimate opportunities
+
Allow natural commercial steering
+
Recognize stronger buying relationships
+
Recognize post-purchase propensity
```

The commercial objective chain remains conceptually:

```text
RELATIONSHIP
→ EXPLORE
→ BUILD_DESIRE
→ QUALIFY
→ RECOMMEND
→ PRESENT_OFFER
→ AFTERCARE
```

This is a conversational strategy, not a replacement for deterministic commerce eligibility.

---

# 33. Post-Purchase Relationship

A purchase is also relationship history.

A buyer should not be treated as though every conversation starts from zero.

The relationship context may include:

```text
first purchase
repeat purchase
recent purchase
purchase frequency
previous purchased content
previous successful offers
post-purchase interaction
```

Commerce remains the source of truth for transaction facts.

Relationship V2 stores the relational meaning of those events.

---

# 34. Operator Workflow

The operator queue must become part of the event model.

Flow:

```text
Fan message
    ↓
Draft A
    ↓
operator rejects
    ↓
Draft B generated
    ↓
Draft B placed behind existing queue
    ↓
operator reviews
    ↓
approve / edit / reject
    ↓
actual response sent
```

Rejected drafts must not become relationship events.

---

# 35. Edited Operator Response

If an operator edits the LLM draft, the edited version is the actual conversational response.

Example:

LLM:

```text
"just relaxing tonight"
```

Operator edits:

```text
"just laying here thinking about you actually 😏"
```

Relationship learning must process:

```text
actual_response =
"just laying here thinking about you actually 😏"
```

not the original draft.

This is mandatory.

---

# 36. Message Outcome Events

The relationship system should receive events representing actual interaction.

At minimum:

```text
FanMessageReceived
ResponseGenerated
ResponseRejected
ResponseEdited
ResponseApproved
ResponseSent
ResponseFailed
PurchaseCompleted
FanReturned
EpisodeClosed
```

Only actual sent messages should affect relationship history as things Sunny said.

---

# 37. Idempotency

Every relationship event must have a unique ID.

Example:

```text
event_id
generation_id
fan_id
episode_id
message_id
created_at
```

Consumers must be idempotent.

Processing the same event twice must not:

* duplicate memories
* increase relationship state twice
* create duplicate episodes
* create duplicate open loops
* duplicate interaction evidence
* duplicate purchase history

Use unique constraints and processed-event tracking where appropriate.

---

# 38. Concurrency

A fan should not have simultaneous relationship mutations.

Use a per-fan relationship lock or transactional optimistic concurrency.

Example:

```text
fan:123 relationship lock
```

or:

```text
relationship.version
```

with compare-and-swap semantics.

Two simultaneous messages must not cause state corruption.

---

# 39. Relationship Versioning

Relationship state should have a version.

Example:

```text
relationship_id
version
updated_at
```

Every state mutation increments the version.

This makes debugging possible.

---

# 40. Auditability

Every important relationship mutation should be explainable.

For example:

```text
Why did Sunny believe he is a photographer?

Because:
message #18372
2026-09-25
"Been doing photography full time..."
confidence: 0.96
```

Why did Sunny believe he likes teasing?

```text
Evidence:
event #...
event #...
event #...
```

This is essential for debugging hallucinated memories.

---

# 41. Memory Confidence

Use confidence levels.

Example:

```text
0.95–1.00
explicitly stated and repeatedly confirmed

0.80–0.94
explicitly stated once or strongly supported

0.60–0.79
reasonable behavioral inference

<0.60
do not promote to durable important memory
```

Exact thresholds may be tuned during implementation.

Do not allow weak inference to become hard fact.

---

# 42. Memory Importance

Memories should have importance.

Suggested categories:

```text
critical
high
normal
low
```

Critical examples:

```text
children
major life events
important personal history
relationship-defining events
important boundaries
```

High:

```text
job
hobbies
travel
important plans
important preferences
```

Low:

```text
temporary casual details
minor one-off remarks
```

Importance affects retention and proactive recall.

---

# 43. Memory Decay

Do not blindly delete memories after a time period.

Instead:

```text
importance
+
confirmation
+
recency
+
relationship significance
```

should determine retrieval priority.

A ten-year-old birthday story can still be important.

A two-day-old comment about what he ate for lunch may not be.

---

# 44. Memory Reinforcement

Repeated confirmation should increase confidence.

Example:

```text
"He's a football fan."
```

appears repeatedly.

Evidence:

```text
1 → weak
2 → moderate
5 → strong
```

Contradictory evidence reduces confidence or supersedes the fact.

---

# 45. Conversation Preferences

Conversation preferences should be learned.

Examples:

```text
preferred message length
question tolerance
emoji tolerance
teasing responsiveness
flirting responsiveness
response timing patterns
topic preferences
sales-transition responsiveness
```

These should inform generation.

They should never become rigid templates.

---

# 46. Human-Likeness Requirements

Sunny must avoid:

```text
repetitive greetings
overly formal language
repetitive sentence structures
unnecessary questions
answering every message literally
excessive enthusiasm
excessive emojis
long responses
robotically short responses
constant selling
mentioning things the fan never said
forgetting previous conversations
abrupt topic changes
weak flirting
customer-support language
treating every message as a standalone prompt
```

The relationship architecture should specifically make these failures less likely by providing persistent context.

---

# 47. Response Generation Philosophy

The LLM should not feel obligated to:

```text
ask a question
reference a memory
sell
flirt
summarize
respond to every clause
```

on every turn.

Natural conversation may involve:

```text
short acknowledgement
tease
reaction
callback
topic continuation
emotional response
question
statement
flirt
commercial transition
```

The relationship context gives the LLM options.

It should choose naturally.

---

# 48. Current Momentum

Current momentum should describe what is happening now.

Examples:

```text
casual
playful
flirty
romantic
intimate
sexual
emotional
serious
supportive
commercial
post_purchase
```

Momentum should not erase history.

If the fan changes subject abruptly:

```text
current_momentum = intimate
new_topic = work
```

The system can represent:

```text
topic changed
but intimacy continuity remains
```

This is preferable to resetting the entire relationship.

---

# 49. Relationship Objective

The system should maintain an internal current objective.

Examples:

```text
GET_ACQUAINTED
BUILD_FAMILIARITY
DEEPEN_CONNECTION
MAINTAIN_CONNECTION
FLIRT
BUILD_INTIMACY
CONTINUE_INTIMACY
QUALIFY_INTEREST
NATURAL_COMMERCE_TRANSITION
PRESENT_OFFER
AFTERCARE
REACTIVATE
```

Objectives are guidance, not scripts.

---

# 50. Proactive Commercial Steering

Sunny should not simply wait indefinitely for a perfect buying signal.

The relationship context can identify natural opportunities such as:

```text
high intimacy
strong engagement
fan asking about content
fan expressing desire
repeat buyer
previous successful purchase behavior
conversation naturally touching relevant content
```

Commerce then determines whether an actual opportunity exists.

---

# 51. Reactivation

When a fan returns after a long absence:

```text
Fan returns
    ↓
load relationship
    ↓
calculate time since contact
    ↓
retrieve meaningful history
    ↓
retrieve open loops
    ↓
retrieve relationship state
    ↓
generate response
```

No "new fan" behavior.

---

# 52. Database Architecture

Recommended initial persistence:

```text
PostgreSQL
    │
    ├── fan_relationships
    ├── relationship_memories
    ├── relationship_episodes
    ├── relationship_events
    ├── relationship_patterns
    ├── relationship_open_loops
    ├── relationship_intimacy
    └── relationship_processed_events
```

Redis remains useful for:

```text
locks
queues
debounce
temporary conversation state
job coordination
event streams
```

PostgreSQL remains the durable source of relationship truth.

Do not use Redis as the durable relationship database.

---

# 53. Suggested Schema: fan_relationships

Conceptual fields:

```text
id UUID PRIMARY KEY
fan_id BIGINT UNIQUE NOT NULL

first_interaction_at
last_interaction_at

relationship_state
familiarity
comfort
trust
reciprocity
engagement
continuity
intimacy

current_momentum
current_episode_id

version BIGINT

created_at
updated_at
```

Add appropriate indexes on:

```text
fan_id
last_interaction_at
current_episode_id
```

---

# 54. Suggested Schema: relationship_memories

```text
id UUID PRIMARY KEY
relationship_id UUID NOT NULL

memory_type
category
subject
value

status
importance
confidence

first_observed_at
last_confirmed_at
superseded_at

source_event_id
source_message_id

created_at
updated_at
```

Memory types:

```text
person
relationship
episodic
preference
interaction
intimate
```

---

# 55. Suggested Schema: relationship_episodes

```text
id UUID PRIMARY KEY
relationship_id UUID NOT NULL

status
started_at
last_activity_at
ended_at

current_topic
previous_topic
current_momentum
current_objective

summary
version

created_at
updated_at
```

Do not rely exclusively on a summary.

The episode must be reconstructable from events/messages where required.

---

# 56. Suggested Schema: relationship_events

```text
id UUID PRIMARY KEY

relationship_id
episode_id
fan_id

event_type

message_id
generation_id

payload JSONB

occurred_at
created_at
```

Indexes:

```text
relationship_id
episode_id
event_type
occurred_at
message_id
generation_id
```

---

# 57. Suggested Schema: relationship_patterns

```text
id UUID PRIMARY KEY
relationship_id UUID NOT NULL

pattern_type
description

confidence

positive_evidence_count
negative_evidence_count

first_observed_at
last_observed_at

status

created_at
updated_at
```

---

# 58. Suggested Schema: relationship_open_loops

```text
id UUID PRIMARY KEY
relationship_id UUID NOT NULL

description
source_memory_id

priority
status

expected_at
last_referenced_at
follow_up_attempts

outcome
resolved_at

created_at
updated_at
```

---

# 59. Suggested Schema: processed events

```text
relationship_processed_events

event_id PRIMARY KEY
relationship_id
processor
processed_at
```

This prevents duplicate event processing.

---

# 60. Context Assembly Algorithm

For every inbound message:

```text
1. Identify fan.
2. Load FanRelationship.
3. Acquire relationship lock.
4. Identify/create current Episode.
5. Load recent conversation.
6. Retrieve important person memories.
7. Retrieve relevant relationship memories.
8. Retrieve open loops.
9. Retrieve interaction patterns.
10. Retrieve intimate continuity.
11. Retrieve relevant historical episodes.
12. Calculate current relationship state.
13. Load authoritative Commerce Context.
14. Assemble LLM context.
15. Generate response.
```

After actual response:

```text
16. Persist response event.
17. Extract candidate observations.
18. Validate observations.
19. Update memories.
20. Update open loops.
21. Update interaction patterns.
22. Update relationship state.
23. Update episode.
24. Persist relationship event.
25. Release lock.
```

---

# 61. Context Budget

Never send the entire relationship database to the LLM.

The context assembler should produce a bounded representation.

Example:

```text
Person:
    5–15 important facts

Relationship:
    3–8 meaningful relationship characteristics

Episodes:
    3–6 relevant historical episodes

Open loops:
    0–5

Patterns:
    3–8 strongest relevant patterns

Intimate continuity:
    concise

Commerce:
    authoritative current facts

Current conversation:
    recent messages
```

The exact token budget should be configurable.

---

# 62. Retrieval Priority

Suggested priority:

```text
1. Current episode
2. Active open loops
3. Important unresolved events
4. Current relationship state
5. Highly relevant person facts
6. Relationship-defining memories
7. Relevant interaction patterns
8. Relevant intimate history
9. Older historical context
```

Proactive recall can override pure topical relevance when a memory is sufficiently important.

---

# 63. Relationship Context Must Be Coherent

Bad:

```text
Memory 1: football
Memory 2: architect
Memory 3: daughter
Memory 4: pizza
Memory 5: vacation
Memory 6: football
Memory 7: old job
```

Good:

```text
He's a photographer now; he previously worked in architecture.
He has a daughter and recently mentioned her soccer game.
He's a football fan.
You two have a playful, established dynamic.
He responds well to teasing and generally dislikes being interrogated with lots of questions.
You haven't spoken for three weeks.
```

The context assembler should create a coherent relational representation.

---

# 64. Relationship Summary

A compact relationship summary may be stored for efficiency.

However:

> The summary is a cache, not the source of truth.

It must be regenerable from underlying memories/events.

Do not let summary compression permanently destroy important facts.

---

# 65. Migration Strategy

Do not automatically migrate every old record.

Phase 1:

```text
New architecture
+
new conversations
+
commerce facts
```

Phase 2:

```text
Selective migration of high-confidence historical facts
```

Phase 3:

```text
Historical episode migration if valuable
```

Migration must preserve uncertainty.

Example:

```text
legacy occupation = architect
```

becomes:

```text
historical occupation:
architect

confidence:
legacy_imported

status:
historical
```

It should not automatically become:

```text
current occupation = architect
```

unless supported by newer evidence.

---

# 66. Legacy Read Isolation

Once V2 is active:

```text
legacy memory → READ ONLY
legacy relationship → READ ONLY
```

The new system must not write to old relationship/memory tables.

Commerce may continue writing its own tables.

---

# 67. Feature Flags

Required flags:

```text
RELATIONSHIP_V2_ENABLED
RELATIONSHIP_V2_READ_ENABLED
RELATIONSHIP_V2_WRITE_ENABLED
RELATIONSHIP_V2_SHADOW_MODE
LEGACY_RELATIONSHIP_ENABLED
LEGACY_MEMORY_ENABLED
```

Desired production configuration after cutover:

```text
RELATIONSHIP_V2_ENABLED=true
RELATIONSHIP_V2_READ_ENABLED=true
RELATIONSHIP_V2_WRITE_ENABLED=true

RELATIONSHIP_V2_SHADOW_MODE=false

LEGACY_RELATIONSHIP_ENABLED=false
LEGACY_MEMORY_ENABLED=false
```

Commerce flags remain independent.

---

# 68. Shadow Mode

Before V2 controls responses, it should be possible to run:

```text
incoming message
    ↓
legacy context
    ↓
response

AND

incoming message
    ↓
V2 context
    ↓
V2 proposed response/context
```

V2 should be logged without affecting the live response.

This allows comparison.

Do not allow shadow mode to send messages.

---

# 69. Rollback

Rollback must be:

```text
disable V2 read
enable legacy relationship
disable V2 response context
```

Commerce remains unchanged.

No rollback should require restoring commerce databases.

No rollback should require deleting V2 data.

---

# 70. Testing Requirements

The new architecture must have automated tests for:

### Memory

```text
fact extraction
contradiction handling
historical/current distinction
memory confidence
memory reinforcement
memory suppression
memory retrieval
proactive recall
```

### Episodes

```text
episode creation
episode continuation
episode closure
open loop creation
open loop follow-up
open loop resolution
```

### Relationship

```text
state derivation
continuity
long absence
intimacy continuity
interaction pattern learning
negative evidence
```

### Commerce integration

```text
purchase event
purchase history
offer context
eligibility isolation
price authority
ownership authority
```

### Operator

```text
reject
regenerate
queue
approve
edit
send
relationship update only from actual sent response
```

### Reliability

```text
duplicate event
duplicate message
concurrent message
worker retry
LLM retry
send failure
transaction rollback
```

---

# 71. Critical Acceptance Tests

## Test 1 — New Fan

Fan:

> "Hey, I'm Mike."

Sunny should not pretend to know him.

---

## Test 2 — Personal Memory

Fan:

> "I'm actually a photographer."

Later:

> "How's your week?"

Sunny should be able to naturally remember photography if relevant.

---

## Test 3 — Contradiction

Fan:

> "I'm an architect."

Later:

> "I left architecture. I'm doing photography now."

System must retain:

```text
previous:
architect

current:
photographer
```

---

## Test 4 — Episodic Recall

Fan:

> "My daughter has a soccer game Saturday."

Later:

> "hey gorgeous"

Sunny should be capable of naturally recalling the soccer game when appropriate, even though the current message contains no football/soccer keyword.

---

## Test 5 — Interaction Learning

Fan repeatedly responds positively to teasing.

Relationship system should increase confidence in:

```text
responds_well_to_teasing
```

---

## Test 6 — Negative Learning

Fan repeatedly ignores long question-heavy responses.

System should learn:

```text
lower responsiveness to repeated questions
```

---

## Test 7 — Long Absence

Fan returns after 30 days.

Sunny should receive:

```text
longstanding relationship
+
30-day absence
+
previous history
```

and not behave like a new fan.

---

## Test 8 — Intimate Continuity

Conversation is intimate.

Fan abruptly changes subject.

System should retain:

```text
intimate momentum
```

rather than resetting to neutral.

---

## Test 9 — Purchase

Fan buys content.

Commerce remains authoritative.

Relationship receives:

```text
PurchaseCompleted
```

and can incorporate it into relationship history.

---

## Test 10 — Commerce Price

LLM says:

> "I can send it for $20."

If deterministic Commerce says `$35`, the LLM output must not be allowed to execute an incorrect offer.

---

## Test 11 — Operator Rejection

Draft A rejected.

Draft B generated.

Only Draft B becomes a candidate for actual conversation.

---

## Test 12 — Operator Edit

Draft:

> "I'm relaxing."

Operator changes to:

> "I'm laying here thinking about you."

Relationship learning must process the edited version.

---

# 72. Observability

Log every major relationship decision.

Example:

```text
relationship_context_built
memory_retrieved
memory_created
memory_updated
memory_superseded
pattern_updated
episode_created
episode_updated
open_loop_created
open_loop_resolved
relationship_state_changed
commerce_context_loaded
relationship_event_processed
```

Logs must contain:

```text
fan_id
relationship_id
episode_id
event_id
generation_id
timestamp
```

Never log sensitive content unnecessarily.

---

# 73. Debugging View

Eventually the operator/admin interface should allow:

```text
Fan
│
├── Current relationship
│
├── Person memories
│
├── Relationship memories
│
├── Episodes
│
├── Open loops
│
├── Interaction patterns
│
├── Intimate history
│
├── Commerce history
│
└── Event timeline
```

This is extremely valuable when debugging:

> "Why did Sunny say that?"

The answer should be traceable to the context that was actually supplied to the LLM.

---

# 74. "Why Did Sunny Say That?" Trace

Every generated response should be traceable to:

```text
generation_id
↓
context snapshot
↓
relationship context
↓
commerce context
↓
LLM output
↓
validation
↓
operator action
↓
actual sent message
```

This should be retained sufficiently to debug production behavior.

---

# 75. Security / Authority Boundaries

The LLM must never have unrestricted database mutation capability.

The LLM worker should call controlled services:

```text
RelationshipService
CommerceService
ConversationService
```

not directly mutate arbitrary tables.

Especially:

```text
LLM → Commerce DB
```

must not exist.

Instead:

```text
LLM
 ↓
Commerce context / proposal
 ↓
deterministic commerce validation
 ↓
execution
```

---

# 76. Implementation Order

Implement in this order.

## Phase 1 — Isolation

1. Identify every legacy relationship/memory call site.
2. Introduce feature flags.
3. Stop legacy relationship state from affecting responses.
4. Stop legacy memory retrieval from affecting responses.
5. Keep commerce operational.
6. Add instrumentation around the existing pipeline.

---

## Phase 2 — Domain

Implement:

```text
FanRelationship
Memory
Episode
EpisodeEvent
InteractionPattern
OpenLoop
IntimateHistory
RelationshipState
```

---

## Phase 3 — Persistence

Create PostgreSQL migrations for:

```text
fan_relationships
relationship_memories
relationship_episodes
relationship_events
relationship_patterns
relationship_open_loops
relationship_processed_events
```

Add indexes and uniqueness constraints.

---

## Phase 4 — Event Infrastructure

Implement relationship events.

At minimum:

```text
FanMessageReceived
ResponseSent
ResponseRejected
ResponseEdited
PurchaseCompleted
PurchaseRefunded
FanReturned
EpisodeClosed
```

---

## Phase 5 — Memory Engine

Implement:

```text
observation extraction
validation
memory creation
memory updates
contradiction resolution
reinforcement
confidence
importance
```

---

## Phase 6 — Episode Engine

Implement:

```text
episode creation
episode continuation
episode closure
open loops
episode events
```

---

## Phase 7 — Retrieval

Implement:

```text
current episode retrieval
semantic memory retrieval
important-memory retrieval
open-loop retrieval
pattern retrieval
intimate-history retrieval
proactive recall
```

---

## Phase 8 — Relationship Reasoning

Implement:

```text
familiarity
comfort
trust
reciprocity
engagement
continuity
intimacy
momentum
```

These should be evidence-driven.

---

## Phase 9 — Context Assembly

Create one authoritative:

```text
RelationshipContextAssembler
```

It should produce the exact relationship context supplied to the LLM.

---

## Phase 10 — LLM Integration

Update the LLM contract to consume:

```text
RelationshipContext
CommerceContext
CurrentConversation
```

and return:

```text
response
observations
candidate_memories
candidate_patterns
episode_updates
```

The relationship service validates the latter.

---

## Phase 11 — Operator Integration

Implement:

```text
reject
regenerate
queue
approve
edit
send
```

and ensure only actual sent responses affect relationship history.

---

## Phase 12 — Shadow Testing

Run V2 without controlling responses.

Inspect:

```text
memory quality
recall quality
relationship state
episode continuity
pattern quality
commerce separation
```

---

## Phase 13 — V2 Cutover

Enable:

```text
V2 read
V2 write
```

Disable:

```text
legacy relationship
legacy memory
```

Keep rollback available.

---

# 77. Do Not Do These Things

Do not:

```text
rewrite commerce
replace Commerce with LLM decisions
copy every old memory into V2
use summaries as the primary database
use conversation state as the relationship
use numeric scores as the entire relationship model
make every LLM inference a memory
retrieve memories only by keyword
erase historical facts when they become outdated
reset intimacy when topic changes
treat every conversation as a new relationship
make the LLM directly mutate relationship state
let rejected drafts become relationship events
let unsent drafts become relationship history
make every interaction generate a question
make every interaction generate a sales attempt
```

---

# 78. Definition of Done

The implementation is complete only when:

### Architecture

* Legacy relationship system no longer controls responses.
* Legacy memory system no longer controls context.
* Commerce remains independently operational.
* V2 is the relationship source of truth.

### Memory

* Persistent personal memory exists.
* Persistent relationship memory exists.
* Episodic memory exists.
* Contradictions preserve history.
* Important memories survive long periods.
* Proactive recall works.
* Negative interaction patterns can be learned.

### Relationship

* Fan relationship persists across conversations.
* Current relationship state is derived from evidence.
* Long absences are understood.
* Interaction patterns influence generation.
* Intimate continuity persists.
* Current episodes are distinct from historical relationship.

### Commerce

* Prices remain deterministic.
* Product identity remains deterministic.
* Purchase state remains deterministic.
* Offer eligibility remains deterministic.
* Transaction execution remains deterministic.
* Relationship V2 cannot override commerce authority.

### Operator

* Rejected drafts do not affect relationship state.
* Regenerated drafts are queued correctly.
* Edited responses become the actual conversational event.
* Only actual sent responses are recorded as Sunny's messages.

### Reliability

* Event processing is idempotent.
* Relationship updates are concurrency safe.
* Worker retries cannot duplicate memory/state.
* Rollback is possible.
* Every major relationship decision is traceable.

---

# 79. Final Architecture

The finished system should conceptually look like this:

```text
                         TELEGRAM
                            │
                            ▼
                    MESSAGE INGESTION
                            │
                            ▼
                  CONVERSATION ORCHESTRATOR
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
       RELATIONSHIP V2                 COMMERCE
              │                           │
      ┌───────┼────────┐          ┌───────┼────────┐
      │       │        │          │       │        │
    Person  Memory   Episodes   Products Prices Purchases
      │       │        │          │       │        │
      ├── Patterns    │          Eligibility       │
      ├── Intimacy    │          Offers            │
      ├── Open loops  │          Sealing            │
      └── State       │          Execution          │
              │       │                  │
              └───────┴──────────┬───────┘
                                 │
                                 ▼
                         CONTEXT ASSEMBLER
                                 │
                                 ▼
                              LLM
                                 │
                       ┌─────────┴─────────┐
                       │                   │
                       ▼                   ▼
                    RESPONSE            OBSERVATIONS
                       │                   │
                       ▼                   ▼
               VALIDATION/ROUTING   RELATIONSHIP ENGINE
                       │
               ┌───────┴────────┐
               │                │
               ▼                ▼
              SEND           OPERATOR
                                │
                         approve/edit/reject
                                │
                                ▼
                           ACTUAL EVENT
                                │
                                ▼
                       RELATIONSHIP V2
```

The fundamental architectural rule is:

> **Commerce knows what can be sold and what actually happened commercially. Relationship V2 knows who this person is and what the relationship means. The LLM turns those two sources of truth into natural conversation.**

That separation should remain intact throughout implementation.
