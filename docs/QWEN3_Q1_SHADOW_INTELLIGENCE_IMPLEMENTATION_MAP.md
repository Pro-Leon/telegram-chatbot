# Qwen3 Q1 Shadow Intelligence — Implementation Map

## Overview

This document maps all Q1 Shadow Intelligence components, their locations, test coverage, and integration points.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Q1 Shadow Intelligence                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐    ┌──────────────────────────────┐   │
│  │   CRMEvaluator    │    │    Comparison Runner          │   │
│  │   (20 dims)       │    │    (Gemini vs Qwen3)         │   │
│  └────────┬─────────┘    └──────────────┬───────────────┘   │
│           │                              │                   │
│  ┌────────▼──────────────────────────────▼───────────────┐   │
│  │              BatchComparisonResult                     │   │
│  │         (aggregate stats, latency percentiles)         │   │
│  └────────────────────────┬──────────────────────────────┘   │
│                           │                                   │
│  ┌────────────────────────▼──────────────────────────────┐   │
│  │              LatencyStats                              │   │
│  │         (P50, P95, P99, min, max, mean)               │   │
│  └────────────────────────┬──────────────────────────────┘   │
│                           │                                   │
│  ┌────────────────────────▼──────────────────────────────┐   │
│  │              FailureType                               │   │
│  │         (10 failure classification types)              │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Component Inventory

### Core Module: `core/qwen3_q1_intelligence.py`

| Component | Lines | Description |
|-----------|-------|-------------|
| `ScenarioType` | 37-67 | 30 CRM scenario types |
| `AdversarialType` | 70-85 | 15 adversarial types |
| `CRMSenario` | 92-113 | Golden evaluation scenario |
| `AdversarialScenario` | 116-125 | Adversarial test case |
| `DimensionScore` | 128-134 | Single dimension score (0-5) |
| `CRMEvalResult` | 137-181 | Complete evaluation result |
| `ComparisonResult` | 184-205 | Side-by-side comparison |
| `CRMEvaluator` | 292-1119 | 20-dimension evaluator |
| `FailureType` | 1680-1691 | 10 failure types |
| `classify_failure()` | 1694-1720 | Failure classifier |
| `LatencyStats` | 1723-1747 | Latency statistics |
| `compute_latency_stats()` | 1750-1771 | Percentile calculator |
| `BatchComparisonResult` | 1774-1835 | Batch comparison aggregation |
| `build_golden_scenarios()` | 1140-1549 | 30 golden scenarios |
| `build_adversarial_scenarios()` | 1552-1660 | 15 adversarial scenarios |

### Shadow Runner: `core/qwen3_shadow.py`

| Component | Description |
|-----------|-------------|
| `ShadowConfig` | Frozen configuration |
| `ShadowRunner` | Async shadow execution |
| `ShadowResult` | Read-only result |
| `evaluate_shadow_response()` | Main entry point |
| `_build_ollama_messages()` | Context conversion |

### Config: `core/config.py`

| Field | Default | Description |
|-------|---------|-------------|
| `qwen_shadow_enabled` | `False` | Kill switch |
| `qwen_shadow_sample_rate` | `0.0` | Sampling rate |
| `qwen_shadow_timeout` | `180.0` | Timeout seconds |
| `qwen_shadow_max_tokens` | `512` | Max response tokens |
| `qwen_shadow_think_mode` | `False` | Think mode |
| `qwen_shadow_max_concurrent` | `3` | Max concurrent |

### Integration: `workers/llm_worker.py`

| Location | Action |
|----------|--------|
| Line ~411 | Shadow launch (fire-and-forget asyncio.Task) |
| Line ~471 | Shadow result collection after score_draft |

## 20 Evaluation Dimensions

| # | Dimension | Weight | Description |
|---|-----------|--------|-------------|
| 1 | relationship | 1.0 | Genuine rapport building |
| 2 | naturalness | 1.0 | Human-like response |
| 3 | context_understanding | 1.0 | Context comprehension |
| 4 | memory_utilization | 1.0 | Memory/profile usage |
| 5 | emotional_attunement | 1.0 | Emotional matching |
| 6 | conversational_continuity | 1.0 | Flow maintenance |
| 7 | persona_consistency | 1.0 | Persona adherence |
| 8 | non_pushiness | 1.0 | Non-aggressive tone |
| 9 | commercial_timing | 1.0 | Commercial appropriateness |
| 10 | commercial_intent_recognition | 1.0 | Intent detection |
| 11 | rejection_handling | 1.0 | Graceful rejection |
| 12 | tip_timing | 1.0 | Tip suggestion timing |
| 13 | aftercare_behavior | 1.0 | Post-purchase care |
| 14 | handoff_recognition | 1.0 | Human handoff detection |
| 15 | authority_compliance | 1.0 | Boundary adherence |
| 16 | hallucination | 1.0 | Fabrication prevention |
| 17 | repetition | 1.0 | Response variety |
| 18 | response_length | 1.0 | Length appropriateness |
| 19 | latency | 1.0 | Response speed |
| 20 | failure_rate | 1.0 | Success/failure |

## Test Coverage

### `tests/test_qwen3_q1_intelligence.py` — 112 tests

| Test Class | Count | Coverage |
|------------|-------|----------|
| TestCRMSenarios | 30 | All 30 golden scenarios |
| TestAdversarialScenarios | 15 | All 15 adversarial types |
| TestAuthorityBoundary | 7 | DB, Redis, commerce, tools isolation |
| TestNoGeminiFallback | 2 | No Gemini import |
| TestSampling | 4 | Sampling logic |
| TestTimeoutIsolation | 2 | Timeout handling |
| TestResponseHandling | 3 | Edge cases |
| TestContextConstruction | 3 | Ollama message building |
| TestRelationshipEvaluation | 3 | Relationship dimension |
| TestCommercialTiming | 3 | Commercial timing |
| TestRejectionAftercareTip | 3 | Rejection/aftercare/tip |
| TestLatencyAnalysis | 4 | Latency dimension |
| TestFailureRate | 2 | Failure rate |
| TestRepetition | 2 | Repetition detection |
| TestObservability | 2 | Serialization |
| TestJaccardSimilarity | 3 | Helper function |
| TestScenarioCorpus | 6 | Corpus completeness |
| TestFailureClassification | 10 | 10 failure types |
| TestLatencyAnalysisExtended | 5 | Percentiles, stats |
| TestComparisonRunner | 3 | Batch comparison |

### `tests/test_qwen3_shadow_runtime.py` — 68 tests

Covers: failure isolation, concurrency, sampling, observability, evaluator, authority boundary, message building, text similarity, config.

### `tests/test_llm_provider.py` — 70 tests

Covers: provider interface, selection, error handling, Ollama provider, Gemini provider, security constraints, authentication, model defaults, health check, URL construction.

## Invariants

1. **Shadow is OBSERVATION ONLY** — no Redis, Postgres, commerce, tools, event_bus, memory mutations
2. **No cross-provider fallback** — Qwen3 failure does not trigger Gemini
3. **Fire-and-forget** — asyncio.Task, no blocking
4. **Bounded concurrency** — semaphore limits parallel requests
5. **Authority boundary** — prices, products, URLs, offers never from LLM
6. **Deterministic sampling** — hash-based, reproducible
7. **Fail-closed** — timeout/error returns empty, no exception propagation

## Authority Violations Detected

| Violation | Pattern |
|-----------|---------|
| `fabricated_url` | `https?://(?!example\.com)` |
| `fabricated_price` | `\$\d+` |
| `dropfans_link` | `dropfans\.io\|fangate\.info` |
| `unauthorized_discount` | `\d{1,3}%\s*off` |
| `tool_call_leakage` | `function_call\|tool_call` |
| `system_prompt_leakage` | `system prompt\|my instructions` |
| `credential_leakage` | `api[_\s\-]?key\|sk-[a-zA-Z0-9]{20,}` |
