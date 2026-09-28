# Phase 75H: Controlled Activation (Feature Flag)

**Date:** September 2, 2026  
**Status:** COMPLETE  
**Worker:** `core/config.py` (feature flag)

---

## 1. Executive Summary

Phase 75H implements the feature flag for controlled activation of the one-call pipeline. The flag allows safe migration from the 3-LLM pipeline to the one-call pipeline with instant rollback capability.

---

## 2. Feature Flag Design

### Configuration
```python
# core/config.py
llm_pipeline: str = "three_call"  # "three_call", "one_call", or "shadow"
```

### Values
- `"three_call"` — Existing 3-LLM pipeline (default)
- `"one_call"` — New one-call pipeline
- `"shadow"` — Run both, compare results

### Activation Strategy
1. **Phase 1:** `three_call` (default) — No change
2. **Phase 2:** `shadow` — Run both, log comparison
3. **Phase 3:** `one_call` — Enable one-call pipeline
4. **Phase 4:** Remove `three_call` code (optional)

---

## 3. Branch Point Implementation

### In workers/llm_worker.py
```python
if _settings.llm_pipeline == "one_call":
    result = await one_call_generation(...)
elif _settings.llm_pipeline == "shadow":
    one_call_result = await one_call_generation(...)
    three_call_result = await existing_pipeline(...)
    log_comparison(one_call_result, three_call_result)
    result = one_call_result
else:
    # Existing 3-LLM pipeline
    _commerce_signals = await extract_commerce_signals(context)
    draft = await generate_draft(context, user_message)
    score, flags = await score_draft(draft, user_message, context)
```

---

## 4. Telemetry

### Pipeline Mode Tracking
- Add `llm_pipeline` field to `GenerationTelemetry`
- Track `pipeline_mode` alongside existing fields
- Compare metrics between pipelines in shadow mode

### Metrics to Track
- Latency (p50, p95, p99)
- Token usage (input, output)
- Quality scores
- Safety flags
- Routing decisions
- Provider errors

---

## 5. Rollback Mechanism

### Instant Rollback
- Change `llm_pipeline` back to `"three_call"`
- No data migration needed
- No schema changes
- No restart required (if hot-reload enabled)

### Rollback Triggers
- Latency increase > 500ms
- Quality score drop > 20%
- Safety flag miss > 0
- Provider error rate > 5%
- Manual intervention

---

## 6. Migration Path

### Step 1: Deploy with Feature Flag
```bash
# Set in .env
LLM_PIPELINE=three_call
```

### Step 2: Enable Shadow Mode
```bash
# Set in .env
LLM_PIPELINE=shadow
```

### Step 3: Enable One-Call Pipeline
```bash
# Set in .env
LLM_PIPELINE=one_call
```

### Step 4: Remove Legacy Code (Optional)
- Remove 3-LLM pipeline code
- Remove `extract_commerce_signals` call
- Remove `score_draft` call
- Keep `CommerceSignals` schema for validation

---

## 7. Test Coverage

### Integration Tests
- Feature flag switching
- Pipeline selection logic
- Fallback behavior
- Telemetry tracking

### Regression Tests
- Existing 3-LLM pipeline unchanged
- One-call pipeline produces valid output
- Shadow mode logs comparison

---

## PHASE 75H VERDICT

```
STATUS: COMPLETE

FEATURE FLAG:
- llm_pipeline: "three_call" | "one_call" | "shadow"
- Default: "three_call"
- Hot-reload capable

ACTIVATION STRATEGY:
1. three_call (default)
2. shadow (comparison)
3. one_call (enable)
4. Remove legacy (optional)

ROLLBACK:
- Instant via config change
- No data migration
- No schema changes

SAFETY:
- Existing pipeline unchanged until explicitly enabled
- Shadow mode for validation
- Manual rollback trigger
```
