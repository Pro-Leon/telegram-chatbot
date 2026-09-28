# Sunny V2 — State Machines (FUTURE)

Each machine defines states, transitions, guards, events, terminal states,
recovery states, and invalid transitions. What follows is the required set;
Phase 1+ details exact diagrams.

## Conversation lifecycle

`started → active → paused → resumed → closed`; `failed` recovery state.
Guards: scope present, idempotency fresh. Terminal: `closed`. Invalid:
`closed → active` without `resumed`.

## Relationship lifecycle

`new → warming → established → deep → dormant → reactivated`; terminal none
(relationships persist). Guards on evidence thresholds. Invalid: skipping
evidence (e.g. `new → deep` without milestones).

## Escalation lifecycle

Stages from `ESCALATION_ENGINE.md` plus `exited` and `cooling_down`.
Guards include commerce eligibility and cooldown timers. Invalid:
`PRESENT_OFFER` while ineligible.

## Commerce interaction lifecycle (mirrors PRESERVED commerce truth)

`none → available → requested → confirmed | rejected → fulfilled → aftercare`.
V2 tracks this; commerce owns transitions. Invalid: `requested → fulfilled`
without `confirmed`.

## Outbound message lifecycle

`planned → validated → enqueued → sent | failed | suppressed`. Terminal:
`sent`, `suppressed`. Recovery: `failed → enqueued` via same idempotency key.
Invalid: `planned → sent` without validation + enqueue.

## Memory lifecycle

`candidate → validated → current → superseded → archived`. Terminal:
`archived`. Invalid: `candidate → superseded`, deletion of `current` without
a superseding row.
