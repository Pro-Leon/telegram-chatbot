# Sunny V2 — Response Engine (FUTURE)

```text
intent/meaning interpretation
→ response strategy
→ response plan
→ generation
→ validation
→ delivery
```

## Stages

1. **Interpretation** — semantic understanding of the fan message against
   memory and conversation state. Produces structured intent, not prose.
2. **Strategy** — relationship + escalation + commerce-eligibility inputs
   select tone, objective, and constraints.
3. **Plan** — explicit `ResponsePlan` (intent, key points, commerce
   references, forbidden claims). Persisted before generation.
4. **Generation** — LLM renders the plan within constraints. Advisory only.
5. **Validation** — deterministic rails reject or route for review:
   invented facts, invented purchases, invented products, incorrect prices,
   unsupported claims, memory contradictions, commerce contradictions,
   excessive repetition, context discontinuity, inappropriate state
   transitions.
6. **Delivery** — validated turns enqueue to the durable outbound queue.

## Hard rules

- The LLM must not independently determine authoritative commerce facts.
- Generated text cannot mutate state; only the application layer applies
  validated outcomes.
- Invalid output never auto-sends; it queues for review or suppresses with a
  stable reason.
