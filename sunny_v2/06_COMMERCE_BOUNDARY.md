# Sunny V2 — Commerce Integration Boundary

## Principle

Commerce remains deterministic and authoritative.

V2 is the relationship and conversation layer around commerce.

## Architecture

```text
Fan Message
   ↓
Sunny V2 Relationship Engine
   ↓
Conversation Strategy
   ↓
Commerce Opportunity Request
   ↓
Existing Deterministic Commerce
   ↓
Commerce Result
   ↓
Sunny V2 Response Generation
   ↓
Validation / Routing
   ↓
Queue or Send
```

## V2 may request

- evaluate whether a commercial opportunity exists
- retrieve current purchase state
- retrieve active offer state
- retrieve available content
- retrieve aftercare state
- request a deterministic offer candidate

## V2 may not decide

- price
- product identity
- ownership
- whether an offer is legally/technically eligible
- purchase completion
- transaction attribution
- delivery
- provider state

## Commerce result contract

The adapter should return a normalized object such as:

```text
CommerceContext
├── purchase_status
├── purchase_count
├── last_purchase
├── active_offer
├── available_opportunities
├── owned_content
├── aftercare_state
├── cooldowns
└── deterministic_constraints
```

## Opportunity handling

V2 can decide:

> "This conversation appears commercially ready."

The commerce layer decides:

> "This exact offer is eligible."

The sealing layer decides:

> "This exact offer is valid now."

The execution layer decides:

> "This outbound action may be sent."

## No duplicated commerce logic

Do not rebuild:

- PPV eligibility
- price validation
- ownership checks
- sealing
- transaction attribution
- provider verification
- delivery

inside V2.

Create adapters.

## Purchase feedback

When a purchase occurs:

1. commerce records it
2. V2 receives an event
3. V2 creates a PURCHASE episode
4. relationship context updates
5. post-purchase strategy becomes available
6. future conversations see buyer context

## Buyer behavior

Purchase history should be available on every relevant turn.

Do not suppress future selling merely because the fan previously purchased.

However, every future offer must still pass deterministic commerce authority.

## Commerce isolation tests

Prove that:

- V2 cannot invent a price
- V2 cannot mark an offer purchased
- V2 cannot bypass eligibility
- V2 cannot seal an invalid offer
- V2 cannot deliver content
- V2 cannot overwrite transaction state
- V2 can consume purchase events
- V2 can personalize post-purchase conversation
