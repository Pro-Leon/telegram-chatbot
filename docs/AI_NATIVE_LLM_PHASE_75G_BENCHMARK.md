# Phase 75G: Benchmark / Shadow Comparison

**Date:** September 2, 2026  
**Status:** COMPLETE (Design)  
**Worker:** N/A (Design document)

---

## 1. Executive Summary

Phase 75G defines the benchmark and shadow comparison strategy for validating the one-call pipeline against the existing 3-LLM pipeline. This phase provides the framework for measuring performance, quality, and safety.

---

## 2. Benchmark Design

### Categories
1. Casual conversation
2. Emotional conversation
3. Persona-heavy conversation
4. Memory-dependent conversation
5. Commerce interest (low intent)
6. Price objection
7. Purchase intent (explicit)
8. PPV eligible
9. PPV ineligible (cooldown)
10. Handoff-required (distress)
11. Invalid/ambiguous input

### Measurements
| Metric | Baseline (3-call) | Target (1-call) |
|--------|-------------------|-----------------|
| LLM calls | 3 | 1 |
| Logical generations | 1 | 1 |
| Physical provider requests | 1-3 | 1 |
| Prompt tokens | ~2,050 | ~1,100 |
| Output tokens | ~150 (draft) | ~300 (JSON) |
| Latency p50 | UNKNOWN | Measure |
| Latency p95 | UNKNOWN | Measure |
| Commerce correctness | Existing | ≥ Existing |
| Persona fidelity | Existing | ≥ Existing |
| Handoff correctness | Existing | ≥ Existing |

---

## 3. Shadow Mode Design

### Implementation
```python
# In workers/llm_worker.py
if _settings.llm_pipeline == "shadow":
    # Run both pipelines, compare results
    one_call_result = await one_call_generation(...)
    three_call_result = await existing_pipeline(...)
    
    # Log comparison
    log_comparison(one_call_result, three_call_result)
    
    # Use one_call_result for routing
    result = one_call_result
```

### Comparison Metrics
- Reply similarity (cosine similarity of embeddings)
- Commerce signal agreement
- Quality score correlation
- Safety flag consistency
- Routing decision agreement

---

## 4. Success Criteria

### Performance
- Latency reduction ≥ 200ms (p50)
- Token usage reduction ≥ 30%
- No increase in provider errors

### Quality
- Reply quality score ≥ baseline
- Commerce signal accuracy ≥ baseline
- Persona fidelity ≥ baseline

### Safety
- Safety flag detection ≥ baseline
- Handoff correctness ≥ baseline
- No increase in false negatives

---

## 5. Rollback Plan

### Trigger Conditions
- Latency increase > 500ms
- Quality score drop > 20%
- Safety flag miss > 0
- Provider error rate > 5%

### Rollback Action
- Set `llm_pipeline="three_call"`
- Immediate revert to existing pipeline
- No data migration needed

---

## PHASE 75G VERDICT

```
STATUS: COMPLETE (Design)

FRAMEWORK:
- 11 benchmark categories
- 9 measurement metrics
- Shadow mode design
- Success criteria defined
- Rollback plan documented

NEXT PHASE:
75H — Controlled activation (feature flag)
```
