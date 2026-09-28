# Sunny V2 — Escalation Engine (FUTURE)

Strategy/state model, not a mandatory script. Conversations may skip, revisit,
or exit stages.

```text
RELATIONSHIP
    ↓
EXPLORE
    ↓
BUILD_DESIRE
    ↓
QUALIFY
    ↓
RECOMMEND
    ↓
PRESENT_OFFER
    ↓
AFTERCARE
```

## Per-stage definition (required for each)

- **Entry conditions** — relationship, conversation, memory, and commerce
  eligibility facts that allow entering.
- **Exit conditions** — success, stall, rejection, boundary, or ineligibility.
- **Confidence** — structured score with named inputs (never a bare number).
- **Cooldown** — minimum intervals before re-entry after rejection or stall.
- **Reversibility** — which transitions may step back and under what guards.
- **User responsiveness** — momentum signals required to advance.
- **Conversation momentum** — topic engagement evidence.
- **Prior relationship context** — tenure, comfort, milestones.
- **Purchase history** — commerce references only.
- **Commerce eligibility** — authoritative READ snapshot; ineligibility blocks
  PRESENT_OFFER unconditionally.
- **Safety constraints** — boundaries, opt-outs, age/safety gating.

## Rules

1. Strategy is computed by application logic, never hardcoded inside the LLM
   prompt as the decision-maker.
2. PRESENT_OFFER only issues a `CommerceActionRequest`; the offer exists when
   commerce confirms.
3. AFTERCARE reads purchase confirmations; it never invents them.
4. Every stage change emits `strategy.changed` with reasons.
