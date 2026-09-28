# Optimizer Simulator — Technical Build Plan

## Purpose

Build a deterministic, reproducible simulation environment that generates realistic fan, conversation, offer, exposure, and purchase histories for the offline optimizer.

The simulator must test whether the optimizer can discover useful behavioral signal without being given the simulator's hidden ground truth.

### Core principle

> The simulator knows the truth. The optimizer only sees production-legitimate information.

The simulator is a development/testing subsystem. It must not become part of the production Telegram/Telethon runtime and must never execute real offers, sends, or purchases.

---

# Architecture

```text
                         SIMULATION ENVIRONMENT
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Synthetic World                                             │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Fans                                                    │  │
│  │ Creators                                                │  │
│  │ Content                                                 │  │
│  │ Conversation states                                     │  │
│  │ Purchasing behavior                                     │  │
│  │ Price sensitivity                                       │  │
│  │ Content affinity                                        │  │
│  │ Timing / geography                                      │  │
│  │ Fatigue / relationship                                  │  │
│  │ Noise / drift                                           │  │
│  └───────────────────────┬────────────────────────────────┘  │
│                          ↓                                   │
│                  Simulation Engine                           │
│                          ↓                                   │
│                  Application Adapter                         │
│                          ↓                                   │
│              Opportunity / Evidence                          │
│                          ↓                                   │
│                 OptimizationInput                            │
│                          ↓                                   │
│                     OPTIMIZER                                │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Ground Truth / Evaluation Store                         │  │
│  │ Hidden fan state, true probabilities, affinities,       │  │
│  │ sensitivities, and simulated outcomes                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

The simulator and optimizer are siblings. They communicate through the same application/optimizer contracts wherever practical.

---

# Phase 0 — Forensic Architecture Audit

## Objective

Understand the actual repository before implementing anything.

Do not assume the desired architecture matches the current codebase.

## Inspect

- Opportunity creation
- Exposure/sent state
- Evidence recording
- Outcome recording
- Purchase attribution
- Maturity/readiness
- `OptimizationInput`
- Feature extraction
- Optimizer training
- Optimizer evaluation
- Creator scoping
- Product selection
- Pricing
- Commerce decision flow
- Existing event/stream mechanisms
- Existing tests
- Existing configuration
- Existing database schemas
- Existing Redis contracts
- Existing optimizer implementation

## Deliverable

Create:

```text
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md
```

The report must document:

```text
Exact optimizer input
Exact observation grain
Exposure semantics
Purchase semantics
Maturity semantics
Timestamp semantics
Immutable fields
Mutable fields
Required IDs
Creator boundaries
Reusable functions
Safe integration points
Things that must be mocked
Things that must never be touched
```

## Gate

No simulator implementation begins until the optimizer contract is verified against the real repository.

---

# Phase 1 — Define the Simulation Contract

Create a top-level simulation run abstraction.

Conceptually:

```text
SimulationRun
├── simulation_id
├── scenario_id
├── seed
├── created_at
├── simulated_start
├── simulated_end
├── config_version
├── behavior_model_version
└── schema_version
```

## Requirements

Every simulation must be:

- reproducible
- versioned
- independently identifiable
- isolated from production data
- traceable to a specific scenario
- traceable to a specific random seed

A simulation result must be reproducible from:

```text
simulation_id
seed
scenario_version
behavior_model_version
config_version
code revision
```

---

# Phase 2 — Simulation Clock + Reproducibility

Implement logical simulated time.

The simulator must not wait for real time to model:

- hours
- days
- weeks
- purchase recency
- maturity
- weekends
- pay cycles
- long-term relationships
- temporal drift

Conceptually:

```text
SimulationClock
├── current_time
├── advance()
├── advance_to()
└── local_time()
```

Example:

```text
Day 1
09:00
09:03
09:18
...

Day 2
...

Day 90
```

A 90-day simulation should run in minutes, not 90 days.

---

# Phase 3 — Synthetic Population

Build generators for:

## Creators

```text
creator_id
content_catalog
content_categories
pricing_range
audience_profile
```

## Fans

```text
fan_id
creator_id
timezone
latent purchasing capacity
latent price sensitivity
latent content affinities
latent relationship responsiveness
latent freebie tendency
latent engagement tendency
```

The latent variables are simulator-only.

They must never be exposed directly to the optimizer.

Example:

```text
true_price_sensitivity = 0.73
```

may exist internally, but the optimizer must infer price sensitivity from historical observations.

---

# Phase 4 — Fan State Machine

Model fan behavior as a dynamic state machine rather than a static classification.

Initial conceptual states:

```text
NEW
  ↓
RELATIONSHIP_BUILDING
  ↓
ENGAGED
  ↓
SEXUAL_ENGAGEMENT
  ↓
PURCHASE
  ↓
POST_PURCHASE
  ↓
REPEAT_BUYER
  ↓
HIGH_VALUE
```

Additional transitions:

```text
NEW → FREEBIE_SEEKER
ENGAGED → COLD
SEXUAL_ENGAGEMENT → COLD
PURCHASE → INACTIVE
REPEAT_BUYER → HIGH_VALUE
```

States must be allowed to change over time.

Examples:

```text
warm → cold
cold → warm
warm → purchase
purchase → repeat purchase
repeat purchase → inactive
```

---

# Phase 5 — Conversation Behavior Model

Generate conversation behavior from state and intent rather than random messages.

Possible behavioral intents:

```text
casual conversation
relationship building
flirting
sexual escalation
content curiosity
purchase intent
price objection
negotiation
freebie seeking
post-purchase engagement
cooling off
re-engagement
```

## First implementation

Prefer deterministic/probabilistic behavioral templates.

Advantages:

- reproducibility
- speed
- low cost
- controllable distributions
- easy testing

Do not make an LLM a dependency of the first simulator version.

A language-model layer can be added later if needed.

---

# Phase 6 — Commercial Readiness Model

Separate:

```text
Historical Fan Value
```

from:

```text
Current Opportunity Readiness
```

Example:

```text
Fan A
$2,000 lifetime spend
currently cold

Fan B
$0 lifetime spend
currently highly engaged
strong sexual conversation
```

The simulator must permit both states.

Potential latent components:

```text
relationship_strength
engagement
sexual_engagement
purchase_intent
content_interest
price_sensitivity
freebie_tendency
recent_purchase_effect
fatigue
```

These combine into the hidden purchase probability.

---

# Phase 7 — Content Affinity Model

Each fan receives hidden affinities for content characteristics.

Example:

```text
Fan 482
-------------------------
video              0.82
lingerie           0.91
personalized       0.74
bundle             0.31
new_content        0.88
explicit           0.77
```

Different fans should have different affinity profiles.

The purchase model should depend partly on:

```text
fan affinity × offered content
```

This tests whether the optimizer can learn fan-specific content preferences.

---

# Phase 8 — Price Sensitivity Model

Give each simulated fan a hidden price-response curve.

Example:

```text
Fan A

$10 → 0.60
$20 → 0.48
$30 → 0.25
$50 → 0.08
```

versus:

```text
Fan B

$10 → 0.80
$20 → 0.79
$30 → 0.74
$50 → 0.68
```

The optimizer must not receive the curves directly.

It should infer pricing behavior from historical offers and outcomes.

---

# Phase 9 — Offer Construction

Model offers as structured opportunities.

Conceptual fields:

```text
offer_id
creator_id
fan_id
content_id
content_type
novelty
bundle_size
price
conversation_context
timestamp
```

Potential offer classes:

```text
single
small_bundle
premium_bundle
personalized
new_content
existing_content
```

Production commerce invariants must remain authoritative.

The simulator should not create a second production pricing authority.

---

# Phase 10 — Timing Model

Model:

```text
fan timezone
local hour
day of week
weekend
pay-cycle position
time since last message
time since last purchase
time since last offer
```

Timing effects should be probabilistic, not deterministic.

Example:

```text
Friday evening + stronger opportunity
Monday morning + weaker opportunity
Pay-period + altered purchasing behavior
```

The optimizer must discover these effects from observations.

---

# Phase 11 — Fatigue Model

Model the effect of repeated selling.

Inputs may include:

```text
recent offers
recent accepted offers
recent rejected offers
conversation health
time since last offer
```

Potential effect:

```text
high selling pressure
        ↓
conversation health changes
        ↓
purchase probability changes
```

Allow different fan profiles to respond differently.

Do not assume more offers always improve outcomes.

---

# Phase 12 — Purchase Model

At each eligible opportunity:

```text
fan state
+
conversation state
+
offer
+
price
+
content affinity
+
timing
+
history
+
noise
        ↓
hidden purchase probability
        ↓
random draw
        ↓
PURCHASE / NO PURCHASE
```

Conceptually:

```python
p = hidden_model(
    fan,
    conversation,
    offer,
    timing,
    history,
)

purchased = rng.random() < p
```

The model should support interactions rather than only simple linear effects.

---

# Phase 13 — Noise Model

Introduce meaningful uncertainty.

Do not make:

```text
strong signal = 100% purchase
weak signal = 0% purchase
```

Instead, strong and weak cases should overlap.

For example:

```text
strong signal → 60–75%
weak signal → 10–25%
```

with stochastic outcomes.

The objective is to test probabilistic prediction rather than rule memorization.

---

# Phase 14 — Selection Bias

The simulator must reproduce the distinction between:

```text
eligible
```

and:

```text
actually exposed
```

Example:

```text
100 eligible candidates
        ↓
1 selected
        ↓
1 observed exposure
```

The other 99 candidates are not automatically negative examples.

This is critical for the optimizer dataset.

---

# Phase 15 — Opportunity / Evidence Lifecycle

Simulate the full lifecycle:

```text
candidate opportunity
        ↓
selected
        ↓
exposed / sent
        ↓
opened / interacted
        ↓
purchase OR decline
        ↓
maturity
        ↓
training observation
```

Do not shortcut this by directly inserting training rows.

The simulator should exercise the same data contracts as closely as possible.

---

# Phase 16 — Ground Truth Store

Maintain a simulation-only truth store.

Conceptual fields:

```text
simulation_id
opportunity_id
fan_id
creator_id

latent_purchase_probability
latent_fan_value
latent_price_sensitivity
latent_content_affinity
latent_relationship_state

actual_simulated_outcome
```

This store must never be exposed to the optimizer.

It exists only for evaluation and debugging.

---

# Phase 17 — Scenario Packs

Build multiple environments.

## Scenario 1 — Baseline

Moderate signal and moderate noise.

## Scenario 2 — Strong Content Affinity

Fans have clear content preferences.

## Scenario 3 — Price Sensitivity

Fans differ substantially in willingness to pay.

## Scenario 4 — Whale Population

A small percentage of fans have very high spending capacity.

## Scenario 5 — Freebie-Heavy Population

Many highly engaged users have low purchase intent and repeatedly seek free content.

## Scenario 6 — Temporal Effects

Night, weekend, timezone, and pay-cycle effects.

## Scenario 7 — Fatigue

Repeated selling changes subsequent behavior.

## Scenario 8 — Temporal Drift

Underlying relationships change over simulated months.

## Scenario 9 — No-Signal Control

Purchases are effectively random.

The optimizer should not manufacture predictive performance when no useful signal exists.

## Scenario 10 — Adversarial Correlation

Introduce misleading historical correlations that change later.

This tests whether the optimizer overfits unstable relationships.

---

# Phase 18 — Chronological Dataset Generation

Generate strictly chronological datasets.

Example:

```text
TRAIN
Days 1–60

VALIDATION
Days 61–75

TEST
Days 76–90
```

Do not randomly shuffle observations across temporal boundaries.

This allows evaluation of:

```text
past → future
```

rather than:

```text
random rows → random rows
```

---

# Phase 19 — Evaluation

Evaluate the optimizer against two forms of truth.

## A. Observable Outcome Performance

Measure appropriate metrics such as:

- log loss
- Brier score
- calibration
- ranking metrics
- performance by creator
- performance by fan cohort
- performance by price band
- performance by content type

## B. Latent Truth Recovery

Use the simulator's hidden truth to determine whether the optimizer is learning meaningful underlying relationships or merely exploiting accidental correlations.

---

# Phase 20 — Counterfactual Diagnostics

The simulator can know what would happen under alternative offers.

Example:

```text
same fan
same conversation
same time
different offer
```

Use this primarily as a diagnostic for simulator/optimizer behavior.

Do not use synthetic counterfactual performance as evidence of real-world causal lift.

Keep the production target aligned with the existing optimizer scope.

---

# Phase 21 — Sample-Size Stress Tests

Run the same scenario at different scales:

```text
1,000 observations
5,000
10,000
50,000
100,000
500,000
```

Measure how performance changes.

The objective is to discover how much data the optimizer actually needs rather than assuming readiness thresholds are sufficient for production performance.

---

# Phase 22 — Reproducibility

Every experiment must record:

```text
simulation_id
seed
scenario
scenario_version
behavior_model_version
optimizer_version
code revision
dataset hash
```

A previous simulation must be reproducible.

Example:

```text
seed = 19283
scenario = price_v3
optimizer = abc123
```

must reproduce the same synthetic world and outcomes.

---

# Phase 23 — Experiment Runner

Build one orchestration entry point that can execute:

```text
simulate
    ↓
generate world
    ↓
generate events
    ↓
run lifecycle
    ↓
reach maturity
    ↓
freeze OptimizationInput
    ↓
train optimizer
    ↓
evaluate chronologically
    ↓
compare against baselines
    ↓
generate report
```

Conceptually:

```text
simulator/runner
    ├── world generation
    ├── event generation
    ├── lifecycle execution
    ├── maturity
    ├── dataset freeze
    ├── optimizer invocation
    └── evaluation
```

---

# Phase 24 — Baselines

Every optimizer experiment should include simple reference baselines.

Potential baselines:

```text
global purchase rate
creator purchase rate
fan historical purchase rate
recent purchase rate
existing deterministic policy
```

The purpose is to determine whether the optimizer learns meaningful information beyond simple heuristics.

Do not interpret synthetic baseline comparisons as real-world commercial proof.

---

# Phase 25 — Failure Tests

Explicitly test failure behavior.

## No Signal

Random outcomes.

Expected:

```text
No meaningful predictive advantage.
```

## Leakage

Inject a future variable into the simulator and verify that the input/evaluation pipeline detects or prevents leakage.

## Creator Contamination

Give creators different populations and verify isolation.

## Selection Bias

Make unselected candidates systematically different and verify they are not treated as negatives.

## Temporal Leakage

Create highly predictive future information and verify that frozen optimizer inputs cannot access it.

---

# Phase 26 — Security and Isolation

Synthetic data must be isolated from production.

Every synthetic record should carry a clear origin marker, according to the existing schema conventions.

Conceptually:

```text
data_origin = SIMULATION
```

The simulator must default to:

```text
NO TELEGRAM
NO TELETHON
NO REAL FAN
NO REAL CREATOR
NO REAL PURCHASE
NO PRODUCTION OFFER
NO PRODUCTION SEND
```

Use a dedicated simulation database/schema/environment.

The simulator must never bypass production commerce authority.

---

# Proposed Repository Structure

Reconcile this with the actual repository during Phase 0. Do not impose it blindly.

```text
simulator/
│
├── domain/
│   ├── fan.py
│   ├── creator.py
│   ├── content.py
│   ├── offer.py
│   ├── conversation.py
│   └── world.py
│
├── behavior/
│   ├── engagement.py
│   ├── purchase.py
│   ├── pricing.py
│   ├── content_affinity.py
│   ├── relationship.py
│   ├── fatigue.py
│   └── timing.py
│
├── scenarios/
│   ├── baseline.py
│   ├── whales.py
│   ├── freebies.py
│   ├── price_sensitive.py
│   ├── content_affinity.py
│   ├── fatigue.py
│   ├── temporal_drift.py
│   └── no_signal.py
│
├── adapters/
│   ├── opportunity.py
│   ├── evidence.py
│   ├── outcome.py
│   └── optimizer_input.py
│
├── truth/
│   ├── models.py
│   └── store.py
│
├── evaluation/
│   ├── metrics.py
│   ├── calibration.py
│   ├── cohorts.py
│   ├── baselines.py
│   └── reports.py
│
├── clock.py
├── seed.py
├── config.py
└── runner.py
```

---

# Implementation Order

Build in this order:

```text
P0  Forensic audit
 ↓
P1  Simulation contracts
 ↓
P2  Simulation clock + reproducibility
 ↓
P3  Synthetic population
 ↓
P4  Fan state machine
 ↓
P5  Conversation behavior
 ↓
P6  Offer/content model
 ↓
P7  Purchase/price model
 ↓
P8  Timing/fatigue
 ↓
P9  Application adapter
 ↓
P10 Opportunity/evidence lifecycle
 ↓
P11 Ground-truth store
 ↓
P12 Baseline scenario
 ↓
P13 Optimizer integration
 ↓
P14 Evaluation framework
 ↓
P15 Scenario packs
 ↓
P16 Stress/drift/adversarial testing
 ↓
P17 Experiment runner + reports
```

---

# First Implementation Milestone

Do not begin with the sophisticated behavioral model.

The first milestone is:

> Generate one deterministic synthetic fan, create one legitimate opportunity, produce an outcome, allow it to reach maturity, and produce exactly the same `OptimizationInput` shape that the optimizer expects.

Once this works:

```text
Synthetic fan
      ↓
Synthetic context
      ↓
Synthetic opportunity
      ↓
Exposure
      ↓
Simulated outcome
      ↓
Evidence
      ↓
Maturity
      ↓
OptimizationInput
      ↓
Optimizer
```

then progressively add behavioral complexity.

---

# Core Architectural Principle

There are three different truths:

```text
                 SIMULATION WORLD
                       │
                Hidden truth
                       │
                       ▼
                Behavior Model
                       │
                       ▼
              APPLICATION EVENTS
                       │
                       ▼
              OBSERVABLE HISTORY
                       │
                       ▼
               OPTIMIZATION INPUT
                       │
                       ▼
                  OPTIMIZER
```

The simulator knows both hidden and observable truth.

The optimizer sees only observable truth.

The evaluator compares the optimizer against:

1. observed simulated outcomes
2. known synthetic ground truth

This separation is the foundation of a meaningful simulator.
