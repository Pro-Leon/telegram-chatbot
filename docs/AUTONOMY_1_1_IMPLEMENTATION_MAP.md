# AUTONOMY 1.1 — IMPLEMENTATION MAP

## Overview

This document maps every code change required to close the four autonomous
gaps identified in the reconnaissance report. Each section lists the files to
modify, the specific functions to add or change, the contract, and the tests.

The changes preserve all existing invariants. No authority boundaries move.
No new external dependencies. No LLM authority expansion.

---

## Gap 1: Multi-Product Selection Engine (CRITICAL)

**Problem**: `resolve_commerce_product()` returns `None` when 2+ valid products
exist. Creators with multiple products get no autonomous commerce at all.

**Solution**: Add a deterministic product-matcher that ranks valid products by
purchase history, funnel stage, and recency. When exactly one product is the
best match, return it. When ambiguous (two products tie), return `None`.

### File Changes

#### 1. `commerce/product_selection.py` — Add `resolve_commerce_product_with_history()`

**Current**: `resolve_commerce_product(creator_id)` — works only for exactly 1 valid product.

**New function**: `resolve_commerce_product_with_history(creator_id, user_id) -> int | None`

**Algorithm** (all deterministic, no LLM):

```
1. Load all valid products for creator (existing logic)
2. If 0 valid → None
3. If 1 valid → return it (existing path)
4. If 2+ valid → rank:
   a. Load purchase history for user → exclude already-purchased products
   b. If exactly 1 unpurchased valid product → return it
   c. If 0 unpurchased → None (all purchased)
   d. If 2+ unpurchased → apply ranking:
      - Products with active offers get priority
      - Products in "warming"/"engaged" funnel stage get higher priority
      - Lowest price_minor gets priority (lowest barrier)
      - If still tied → None (fail-closed)
5. DB failure → None (fail-isolated)
```

**Invariants preserved**:
- DETERMINISTIC: same DB state + same args → same result
- CREATOR-SCOPED: all queries filtered by creator_id
- READ-ONLY: only SELECT queries
- NO TEXT INFLUENCE: conversation text never affects selection
- LIVE VERIFICATION: selected product_id still goes through execute_ppv

**The existing `resolve_commerce_product()` remains unchanged** — it's used
by the single-product path and doesn't need modification.

#### 2. `workers/llm_worker.py` — Update `_try_commerce_draft()`

**Current** (line ~299-312):
```python
product_id = None
if creator.status is SingleCreatorStatus.READY and creator.creator_id is not None:
    product_id = await resolve_commerce_product(creator.creator_id)
```

**Change**: Call `resolve_commerce_product_with_history()` instead:
```python
product_id = None
if creator.status is SingleCreatorStatus.READY and creator.creator_id is not None:
    product_id = await resolve_commerce_product_with_history(
        creator.creator_id, user_id
    )
```

**Why**: The user_id is already available in `_try_commerce_draft()` scope.
The import of `resolve_commerce_product_with_history` is added alongside
the existing `resolve_commerce_product` import.

#### 3. `core/llm_tools.py` — Add `list_products` tool

**New handler**: `_handle_list_products(args, auth) -> ToolResult`

**Contract**:
- READ-only tool (TOOL_TYPE_READ)
- Returns list of valid products for the creator: `[{"product_id": int, "title": str, "price_minor": int, "currency": str}]`
- Filters: `is_accessible=True`, `sales_url` not empty
- Bounded: max 20 products returned
- Scoped: only products for `auth.creator_id`
- Sanitized: no internal IDs, no raw Fangate data

**New tool registration**:
```python
register_tool(ToolDef(
    name="list_products",
    description="List available products for the current creator. Use to discover which products exist before proposing an offer.",
    parameters={
        "type": "OBJECT",
        "properties": {},
        "required": [],
    },
    handler=_handle_list_products,
))
```

**Tool type**: READ (safe, no side effects)

**Why this matters**: The LLM currently has a chicken-and-egg problem —
`get_product_information` requires knowing the product_id, but the LLM
has no way to discover it. `list_products` breaks this cycle.

**Prompt update** (TOOL_AUTHORITY_PROMPT): Add `list_products` to the
"Never invent" section — LLM must not hallucinate product IDs from
conversation text.

#### 4. `commerce/product_selection.py` — Export existing function

**Current**: `resolve_commerce_product` is already importable.

**No change needed** — the new function is added alongside it.

---

## Gap 2: Segment Context Integration (MODERATE)

**Problem**: Segments are dashboard-only. Zero runtime integration. Operators
can define segments but they don't influence autonomous behavior.

**Solution**: Load segment membership during context construction and add it
to `LLMContext`. This makes segments available to the LLM, the decision
engine, and strategy builder — all without changing authority boundaries.

### File Changes

#### 1. `memory/context_assembler.py` — Add segment fields to `LLMContext`

**New fields on `LLMContext`** (frozen dataclass):
```python
# Segments
segment_names: list[str] = field(default_factory=list)  # names of segments user belongs to
segment_count: int = 0
```

**Why `segment_names` not `segment_ids`**: The LLM doesn't need IDs.
Segment names are conversational and useful. IDs are internal.

#### 2. `memory/context_assembler.py` — Add `_get_segments_safe()`

**New function**:
```python
async def _get_segments_safe(creator_id: int, user_id: int) -> tuple[int, list[str]]:
    """Return (count, list_of_segment_names) the user belongs to.

    Uses the existing segment evaluation engine to check all enabled
    segments for this creator. Returns (0, []) on failure.
    """
```

**Implementation**:
```python
async def _get_segments_safe(creator_id: int, user_id: int) -> tuple[int, list[str]]:
    try:
        from db import segments as sdb
        from segments.evaluator import check_user_in_segment
        from segments.models import RuleGroup

        segments = await sdb.list_segments(creator_id, enabled_only=True)
        member_names = []
        for seg in segments:
            rules_data = seg.get("rules")
            if not rules_data:
                continue
            try:
                rule = RuleGroup.model_validate(rules_data)
                is_member = await check_user_in_segment(creator_id, rule, user_id)
                if is_member:
                    member_names.append(seg["name"])
            except Exception:
                continue
        return len(member_names), member_names
    except Exception:
        logger.warning(
            "context_assembler: segment query failed (creator=%s user=%s)",
            creator_id, user_id, exc_info=True,
        )
        return 0, []
```

**Performance note**: This evaluates SQL rules per-segment per-user.
For creators with many segments (10+), this could add latency.
Mitigation: segment membership is deterministic and could be cached
with a short TTL. For now, evaluate on each message — the segment
evaluation SQL is lightweight (single-user EXISTS queries).

#### 3. `memory/context_assembler.py` — Wire into `build_llm_context()`

**In `build_llm_context()`** (after the existing query blocks, ~line 504):

```python
try:
    segment_count, segment_names = await _get_segments_safe(creator_id, user_id)
except Exception:
    segment_count, segment_names = 0, []
```

**Add to the `LLMContext` constructor** (~line 506):
```python
return LLMContext(
    ...existing fields...
    segment_names=segment_names,
    segment_count=segment_count,
)
```

#### 4. `memory/context_assembler.py` — Render segments in `render_context()`

**In `render_context()`** (after the creator section, ~line 582):

```python
# Segments
if ctx.segment_count > 0:
    lines.append(f"Segments: {', '.join(ctx.segment_names)}")
```

**Output format**: `Segments: VIP, High Engagement, New Subscriber`

This appears in the `[APPLICATION CONTEXT — DETERMINISTIC FACTS]` block,
making it available to the LLM as authoritative application data.

#### 5. `memory/context.py` — Pass creator_id to segment evaluation

**Current**: `build_context()` already calls `build_llm_context(creator_id, user_id)`.

**No change needed** — the segment loading happens inside `build_llm_context()`,
which already receives `creator_id`.

---

## Gap 3: Segment-Influenced Product Selection (MODERATE)

**Problem**: Product selection is purely infrastructure-based (1-product filter).
Same product offered to all users regardless of segment membership.

**Solution**: Use segment membership as a tiebreaker in multi-product selection.
Additionally, make segment membership available to the decision engine for
strategy differentiation.

### File Changes

#### 1. `commerce/product_selection.py` — Segment-aware ranking in `resolve_commerce_product_with_history()`

**In the ranking step (step 4d from Gap 1)**, add segment-awareness:

```python
# After excluding already-purchased products and finding 2+ unpurchased:

# Load segment membership for this user
segment_names, segment_count = await _get_segments_safe(creator_id, user_id)

# Score each product based on segment affinity
# (products whose title/description matches segment names get priority)
product_scores = []
for product in unpurchased_valid:
    score = 0
    title_lower = (product.get("title") or "").lower()
    for name in segment_names:
        if name.lower() in title_lower:
            score += 10  # strong match
    product_scores.append((product["id"], score))

# Sort by score descending, then price_minor ascending
product_scores.sort(key=lambda x: (-x[1], _get_price(product, x[0])))

# Return the top product only if it has a strictly higher score than the runner-up
if len(product_scores) >= 2 and product_scores[0][1] > product_scores[1][1]:
    return product_scores[0][0]
return None  # still ambiguous
```

**Why this is safe**:
- Segment names are deterministic (same rules → same membership)
- Product title matching is deterministic (no LLM)
- The tiebreaker only activates when purchase history doesn't resolve it
- Fail-closed: if still ambiguous, returns None

#### 2. `commerce/decision.py` — Expose segment context to decision engine

**No changes to `decide_from_signals()`** itself — it already receives all
application state via kwargs. Segment membership flows through the
`CommercePipelineRequest` and `CommerceConversationContext`.

#### 3. `commerce/pipeline.py` — Pass segments through `CommercePipelineRequest`

**Add fields to `CommercePipelineRequest`**:
```python
# Segment context (informational, not decision-authoritative)
user_segment_names: list[str] = Field(default_factory=list)
user_segment_count: StrictNonNegativeInt = 0
```

**Wire through `build_conversation_context()`**:
```python
def build_conversation_context(request: CommercePipelineRequest):
    return CommerceConversationContext(
        ...existing fields...
        user_segment_names=request.user_segment_names,
        user_segment_count=request.user_segment_count,
    )
```

#### 4. `commerce/context.py` — Add segment fields to `CommerceConversationContext`

**Add fields**:
```python
# Segment context
user_segment_names: list[str] = Field(default_factory=list)
user_segment_count: StrictNonNegativeInt = 0
```

**Note**: This is informational context. The decision engine may reference
it but doesn't change authority boundaries.

#### 5. `workers/llm_worker.py` — Pass segments into commerce request

**In `_try_commerce_draft()`**, after building the `CommerceStateRequest`,
the segment data needs to flow through. Currently `CommerceStateRequest`
doesn't carry segments — they're resolved inside `resolve_commerce_state()`.

**Option A (preferred)**: Add `user_segment_names` and `user_segment_count`
to `CommerceStateRequest` and wire through `resolve_commerce_state()`.

**Option B**: Resolve segments inside `_try_commerce_draft()` and pass
directly into the pipeline request.

**Recommendation**: Option A — keeps the resolution layer clean.

#### 6. `commerce/state.py` — Resolve segments in `resolve_commerce_state()`

**In `resolve_commerce_state()`**, after the existing resolution blocks:

```python
# Segment context (informational)
segment_names = []
segment_count = 0
try:
    from segments.evaluator import check_user_in_segment
    from segments.models import RuleGroup
    from db import segments as sdb

    segments = await sdb.list_segments(creator_id, enabled_only=True)
    for seg in segments:
        rules_data = seg.get("rules")
        if not rules_data:
            continue
        try:
            rule = RuleGroup.model_validate(rules_data)
            if await check_user_in_segment(creator_id, rule, request.user_id):
                segment_names.append(seg["name"])
        except Exception:
            continue
    segment_count = len(segment_names)
except Exception:
    pass  # segments are informational, failure is non-critical
```

**Add to `CommercePipelineRequest` constructor**:
```python
pipeline_request = CommercePipelineRequest(
    ...existing fields...
    user_segment_names=segment_names,
    user_segment_count=segment_count,
)
```

---

## Gap 4: LLM Product Discovery (LOW)

**Problem**: LLM cannot discover which products exist. `get_product_information`
requires knowing the ID first.

**Solution**: Add a `list_products` READ tool. (Already covered in Gap 1,
section 3.)

### Additional Change

#### 1. `core/llm_tools.py` — Update TOOL_AUTHORITY_PROMPT

Add to the "Never invent" section:

```
- product IDs, product titles, or product prices — use list_products or
  get_product_information tools to discover actual products
```

This ensures the LLM uses tools to discover products rather than
hallucinating from conversation context.

---

## Summary: All Files Changed

| File | Gap | Change Type |
|---|---|---|
| `commerce/product_selection.py` | 1, 3 | Add `resolve_commerce_product_with_history()`, segment-aware ranking |
| `workers/llm_worker.py` | 1 | Update `_try_commerce_draft()` to call new resolver |
| `core/llm_tools.py` | 4 | Add `list_products` tool, update prompt |
| `memory/context_assembler.py` | 2 | Add segment fields to `LLMContext`, `_get_segments_safe()`, render |
| `commerce/pipeline.py` | 3 | Add segment fields to `CommercePipelineRequest`, wire through |
| `commerce/context.py` | 3 | Add segment fields to `CommerceConversationContext` |
| `commerce/state.py` | 3 | Resolve segments in `resolve_commerce_state()` |

---

## Test Requirements

### Gap 1: Multi-Product Selection

| Test | File | Description |
|---|---|---|
| `test_resolve_multi_product_single_unpurchased` | `tests/test_product_selection.py` | 3 products, 2 purchased → returns the 1 unpurchased |
| `test_resolve_multi_product_all_purchased` | `tests/test_product_selection.py` | 2 products, both purchased → returns None |
| `test_resolve_multi_product_tied` | `tests/test_product_selection.py` | 2 unpurchased, same price → returns None |
| `test_resolve_multi_product_price_tiebreak` | `tests/test_product_selection.py` | 2 unpurchased, different prices → returns cheaper |
| `test_resolve_multi_product_with_active_offer` | `tests/test_product_selection.py` | 2 unpurchased, one has active offer → returns that one |
| `test_list_products_tool` | `tests/test_llm_tools.py` | list_products returns valid products for creator |
| `test_list_products_empty` | `tests/test_llm_tools.py` | list_products returns empty list for creator with no products |

### Gap 2: Segment Context Integration

| Test | File | Description |
|---|---|---|
| `test_segment_names_in_context` | `tests/test_context_assembler.py` | User in 2 segments → segment_names populated |
| `test_segment_names_empty` | `tests/test_context_assembler.py` | User in no segments → segment_names empty |
| `test_segment_names_rendered` | `tests/test_context_assembler.py` | render_context includes segment names |
| `test_segment_evaluation_failure_degrades` | `tests/test_context_assembler.py` | DB failure → segment_count=0, no crash |

### Gap 3: Segment-Influenced Product Selection

| Test | File | Description |
|---|---|---|
| `test_segment_tiebreak_match` | `tests/test_product_selection.py` | 2 products, segment name matches one title → returns that one |
| `test_segment_tiebreak_no_match` | `tests/test_product_selection.py` | 2 products, no segment match → returns None |
| `test_segments_flow_through_pipeline` | `tests/test_commerce_pipeline.py` | segment_names appear in pipeline request |

### Integration Tests

| Test | File | Description |
|---|---|---|
| `test_end_to_end_multi_product` | `tests/test_commerce_e2e.py` | Full pipeline with 2 products, user with purchase history |
| `test_end_to_end_segment_aware` | `tests/test_commerce_e2e.py` | Full pipeline with segments influencing product selection |

---

## Invariant Verification After Changes

| # | Invariant | Status After Change |
|---|---|---|
| 1 | Creator isolation | ✅ PRESERVED — all new queries scoped by creator_id |
| 2 | Deterministic decision boundary | ✅ PRESERVED — product ranking is deterministic |
| 3 | LLM authority isolation | ✅ PRESERVED — list_products is READ-only |
| 4 | Offer price immutability | ✅ PRESERVED — execute_ppv still verifies live Fangate price |
| 5 | Fangate price verification | ✅ PRESERVED — unchanged |
| 6 | Idempotent offer creation | ✅ PRESERVED — unchanged |
| 7 | PostgreSQL delivery uniqueness | ✅ PRESERVED — unchanged |
| 8 | Vault delivery reservation safety | ✅ PRESERVED — unchanged |
| 9 | Redis dedup remains optimization | ✅ PRESERVED — unchanged |
| 10 | Telegram side effects at-least-once | ✅ PRESERVED — unchanged |
| 11 | DLQ/XAUTOCLAIM behavior intact | ✅ PRESERVED — unchanged |
| 12 | Existing operator/manual flows intact | ✅ PRESERVED — unchanged |
| 13 | No cross-creator product exposure | ✅ PRESERVED — list_products scoped by creator_id |
| 14 | No unauthorized autonomous sending | ✅ PRESERVED — unchanged |

---

## Performance Implications

| Change | Latency Impact | Mitigation |
|---|---|---|
| Multi-product selection | +1 DB query (purchase history) | Already cached in connection pool |
| Segment context loading | +N DB queries (N = enabled segments) | Segment evaluation SQL is lightweight (EXISTS query) |
| list_products tool | +1 DB query | Already cached, triggered only when LLM calls tool |
| Segment-aware ranking | +0 DB queries (in-memory) | Uses already-loaded segment data |

**Total added latency per inbound message**: ~5-15ms (1-3 additional DB
queries, each <5ms with connection pooling).

---

## Implementation Order

1. **Gap 1**: Multi-product selection — unblocks autonomous commerce
2. **Gap 4**: list_products tool — enables LLM product discovery (depends on Gap 1)
3. **Gap 2**: Segment context integration — enables segment-aware behavior
4. **Gap 3**: Segment-influenced product selection — advanced personalization (depends on Gap 2)

Gaps 1+4 can be implemented together. Gap 3 depends on Gap 2.
Total estimated effort: 4 files modified, ~200 lines of new code, ~15 new tests.

---

RECONNAISSANCE COMPLETE — AWAITING IMPLEMENTATION APPROVAL
