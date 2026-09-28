# Human Handoff — Research and Proposals

## Part 1: When the architecture hands a conversation to a human today

### Direct handoff decisions (deterministic)

**1. Operator-handoff rules** — `commerce/relationship.py:396-434`
(`check_operator_handoff`), re-checked with live signals in
`commerce/pipeline.py:593-610`:
- Relationship already `OPERATOR_REQUIRED` (safety) — `:396-397`
- Creator configuration issue — `:399-400`
- Complaint (`has_complaint`, incl. `negative_sentiment >= 0.70`
  at `:423-424`) — `:403-404`
- Custom request (`has_custom_request`, from `intent_tags`) —
  `:407-408`, signal derived at `pipeline.py:590`
- Provider uncertain / repeated fulfillment failures ≥ 2 — `:411-416`
- Model uncertainty ≥ 0.80 — `:419-420`
- Ambiguous high intent (buying score ≥ 0.70 with
  curiosity/content-interest/empty category) — `:427-432`

These produce `CommerceDecision.OPERATOR_HANDOFF`, which routes via
`commerce_handoff_required` to operator queue
(`core/routing.py:141-142`, wired at
`workers/llm_worker.py:5194-5212`). Note: the canonical-decision half
of this wire is currently quarantined live (`llm_worker.py:111` always
returns `None`), so the pipeline-evaluated handoff is armed-but-inactive
on the live path — the handoff-memory path below is the live one.

**2. Handoff memory (live)** — set via `OperationalAction.HANDOFF` in
`commerce/operational_execution.py:176-187` (`make_handoff(reason_code)`
→ `set_handoff_memory`), read at `workers/llm_worker.py:2189-2194`:
active memory sets `_allowed_pre = False`, which forces
`_commerce_handoff_required = True` at `:5206-5212` → queue. Persisted
per creator in `user_profiles` (`handoff_by_creator`,
`conversation_operations.py:392-445`) plus an in-memory map (`:450-460`).

**3. Risk model** — `derive_risk` returns `HANDOFF` when `is_handoff`,
blocked, or unresolved high risk
(`commerce/conversation_operations.py:213-223`).

**4. Objective layer** — `HUMAN_HANDOFF` is priority 1 but eligible
**only** when `is_blocked`
(`commerce/conversation_intelligence.py:72-78`). Narrow by design.

### Corroborated model handoff (advisory + deterministic proof)

`core/one_call.py:538-555`: the model's `needs_handoff` flag is merged
with deterministic triggers — safety flags (`:543-545`), boundary
violations (`:549-551`), quality score < 0.30 (`:554-555`). Routing
(`core/routing.py:153-164`): corroborated handoff → queue; a **bare
advisory flag alone never vetoes** — a model saying "handoff" with all
deterministic signals clean still auto-sends. Persona severe failure
also feeds the safety veto (`llm_worker.py:3640-3653` → `safety_block`,
`routing.py:147-148`).

### Operator queue for review (same desk, different reason codes)

`core/routing.py:93-170` precedence: invalid output, boundary violation
/ unknown boundary state (fail-closed), sealed-suppressed, auto-reply
off, blocking flags (price, photo-promise, CTA, persona), score < 0.80,
then corroborated handoff. `STOP_CONVERSATION` forces queue even on a
clean draft (`llm_worker.py:3727-3734`); unknown boundary state forces
queue (`:3717-3724`); missing creator fails closed to queue
(`:1106-1121`). These emit `suggestion.created` +
`ai.generation_completed(was_auto_approved: False)`; `emit_handoff`
telemetry fires on the auto-reply-off and queued paths (`:5303-5312`,
`:4363+`, `:4527+`).

### Not handoff (do not confuse)

- `do_not_auto_reply` exclusion and `DO_NOT_CONTACT` → **SUPPRESS**,
  no outbound at all (`routing.py:132-138`;
  `llm_worker.py:1356-1383,862-949`).
- Send-side `classify_failure → HANDOFF_REQUIRED` on error strings
  (`conversation_operations.py:260-263`) is delivery classification,
  not conversation routing.

### Gap found

`operator_request` intent ("talk to a real person", "live agent")
exists in the taxonomy (`core/one_call.py:1017`) with 15+ validation
examples (`commerce/validation_dataset_440.py:529+`), but **no
deterministic rule maps it to handoff** — no consumer was found. A fan
explicitly asking for a human is currently handled only if the model
raises `needs_handoff` (advisory, non-vetoing) or a parallel signal
(complaint/custom) fires.

---

## Part 2: Proposals (harden only — no architecture change)

### 1. Complaints: de-escalate first, handoff on technical payment issues

The machinery already exists but is blunt: any complaint →
`OPERATOR_HANDOFF` (`commerce/relationship.py:403-404`), which skips
de-escalation entirely.

- **Complaint-triage evidence split (turn-local booleans only, no new
  state):** `payment_claim` vs `technical_payment_signal` vs
  `chat_experience_complaint`. "I was charged twice / link doesn't
  work" → technical. "You never send me anything / this is a scam,
  give me free stuff" → freebie-pattern claim. Both reuse the existing
  regex-evidence style (`boundary_evidence.py` /
  `content_transition_evidence.py` pattern).
- **Route by existing paths:** chat-experience complaints go through
  the existing `RECOVER` move (`commerce/conversation_strategy.py` —
  annoyed affect → `RECOVER`, `react`, `NO_QUESTION`) + persona
  `sincerity_required`. Short, warm, no groveling, no promises.
  Technical payment signals keep the existing complaint handoff —
  because DropFans owns payment truth, the bot must never adjudicate
  charges.
- **Anti-freebie guards already exist — reuse them:**
  `asks_for_free_content` suppresses commerce (`decision.py:462-469`),
  free-photo quota sits behind `authorize_free_photo`, and
  `policy_allows` forbids invented prices/claims
  (`conversation_operations.py:346-375`). The triage must never promise
  refunds, credits, or free content in de-escalation wording — pin that
  with a validator case, same as the CTA flag.
- **Dashboard payload:** handoff queue rows already carry flags +
  `generation_id`; include the triage label (`payment_technical` vs
  `experience`) in the existing reason/flags fields so the human sees
  what kind it is. Metadata only.

### 2. Customs ($100 minimum – $750 maximum): qualify naturally, never price autonomously

Hard constraint: the architecture **forbids invented prices**
(`policy_allows`: `invented_price → False`,
`conversation_operations.py:351-352`) and unauthorized `price_mention`
routes to review (`core/scoring.py`). So the bot must never quote a
custom number on its own — that is the safety property working, not a
limitation to work around.

- **Bot's job (realization only):** acknowledge warmly, ask at most one
  clarifying question (what exactly they want — the existing question
  budget governs this), keep it feeling like appreciation, not a pitch.
  `custom_request` already fires handoff (`relationship.py:407-408`),
  so the flow is: natural acknowledgment → operator gets the handoff
  with the fan's own words attached.
- **Pricing stays human-side:** customs resolve through the existing
  offer-definition path (`offer_definition_resolver`); the human
  sets/selects the priced offer on the dashboard, and only sealed,
  verified offers ever reach the fan (`selection.py` USE gate). If the
  bot ever utters the $100 starting point, that text must come from an
  authorized offer context — otherwise the price flag correctly catches
  it and queues for review.
- **Dashboard:** handoffs already land in the operator queue
  (`add_to_operator_queue` with flags, `generation_id`, `creator_id`;
  UI in `chatbotv2/dashboard/`). The work is enriching the existing
  payload — custom-request summary (what they asked for, in their
  words, truncated), conversation-context link, suggested price-band
  field for the human — not a new handoff system. Every handoff type
  (complaint, custom, confusion, boundary) should carry the same
  envelope: reason + evidence snippet + what the bot already said.

### 3. "Are you a bot?" — deflect once, hand off on repeat

Resolve the tension first: deflecting once and handing off on repeat
are both right, in layers.

- **First occurrence (realization only):** playful in-persona
  deflection, e.g. "what do you mean human? I'm an angel you know, so
  I'll take that as a compliment". The pieces exist: persona identity
  block (`core/persona_self.py`), `out_of_character` validator
  (already flags "as an AI" robot-talk), and the
  `teasing_allowed`/playful path for light deflection. Add as advisory
  deflection guidance keyed off the existing `operator_request` intent
  + bot-accusation phrasing — wording guidance only, zero authority
  change.
- **Repeat, or combined with complaint → existing handoff.** Second
  ask, or "bot + complaint", falls into the current
  `check_operator_handoff` world (custom/complaint/uncertainty). No new
  rule needed beyond counting the repeat in turn-local evidence.
- **Caution:** never have the bot *claim* to be human or make
  verifiable false claims. Playful deflection is fine; false identity
  is already machine-checked (`persona_validation.py` fact checks) —
  keep deflection in the teasing/playful register, not factual claims.

---

## Sequencing (audit → implement → verify, same workflow)

1. **Complaint triage evidence** (new booleans + tests; handoff rule
   untouched except technical-signal passthrough) + dashboard reason
   enrichment.
2. **Custom-request acknowledgment guidance** (prompt-level only) +
   dashboard custom-summary payload. Pricing stays human-side — no
   bot-quoted customs.
3. **Bot-accusation deflection** (advisory guidance, once) +
   repeat-escalates-to-handoff counter. Identity validators untouched.
