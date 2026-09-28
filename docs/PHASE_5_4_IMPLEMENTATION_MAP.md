# Phase 5.4 — Segment Intelligence Implementation Map

**Date:** 2026-08-23
**Status:** READY FOR IMPLEMENTATION

## Design Principles

1. **No second engine** — explanation must derive from the same Rule AST → Field Registry → SQL compilation layer
2. **No materialized membership** — all explanation is computed on-demand
3. **No LLM, no randomness** — all intelligence is deterministic
4. **Creator-scoped** — all segment operations respect creator_id
5. **Fail-safe** — explanation failures must not block segment operations

---

## Work Items

### W1: Bug Fixes (Pre-requisite)

**Fix 1: `db/segments.py::list_segments` — add `enabled_only` parameter**

Current signature: `list_segments(creator_id: int)`
New signature: `list_segments(creator_id: int, enabled_only: bool = False)`

When `enabled_only=True`, add `AND enabled = TRUE` to the WHERE clause.

**File**: `db/segments.py:53`

**Fix 2: `routes/bulk_ops.py::_resolve_user_ids` — fix return order**

Current: `tuple[list[int] | JSONResponse, str | None]` — error return is `(None, JSONResponse)` but callers unpack as `(user_ids, err)`
New: `tuple[list[int], JSONResponse | None]` — success returns `(resolved, None)`, error returns `([], JSONResponse)`

Also fix the error return at line 47 to match: `(JSONResponse, None)` should be `([], JSONResponse)`.

**File**: `chatbotv2/dashboard/routes/bulk_ops.py:37-62`

---

### W2: Segment Explanation Engine

Add `explain_rule()` function to `segments/evaluator.py` that walks a rule tree and produces a human-readable structured explanation.

**Input**: `creator_id: int, rule: SegmentRule, user_id: int`
**Output**: `SegmentExplanation` Pydantic model

#### Data Model (`segments/models.py`)

```python
class FieldEvaluation(BaseModel):
    """Result of evaluating a single FieldRule against a user."""
    field: str
    field_label: str           # From FieldSpec.label
    operator: str
    value: Any
    satisfied: bool            # Did this condition pass?
    actual_value: Any | None   # What the user actually has (for display)

class RuleEvaluation(BaseModel):
    """Result of evaluating a RuleGroup."""
    type: Literal["group"] = "group"
    operator: Literal["AND", "OR"]
    children: list[FieldEvaluation | RuleEvaluation]
    satisfied: bool           # Did the group pass?

class SegmentExplanation(BaseModel):
    """Full explanation of why a user matches (or doesn't match) a segment."""
    user_id: int
    segment_id: int
    segment_name: str
    is_member: bool
    rule_evaluation: RuleEvaluation
    satisfied_count: int      # How many leaf conditions passed
    total_count: int          # Total leaf conditions
```

#### Engine Function (`segments/evaluator.py`)

```python
async def explain_rule(
    creator_id: int,
    rules: SegmentRule,
    user_id: int,
) -> RuleEvaluation:
    """Walk the rule tree, evaluate each FieldRule against the user,
    return a structured evaluation tree."""
```

Implementation approach:
1. Walk the rule tree depth-first
2. For each `FieldRule`: compile to SQL, execute `SELECT EXISTS(...)` with user_id, record satisfied/actual_value
3. For each `RuleGroup`: evaluate children, compute group satisfaction based on AND/OR
4. Return the full evaluation tree

For actual_value retrieval:
- Simple fields (funnel_stage, message_count, etc.): `SELECT field FROM users WHERE id = $1`
- Subquery fields (inbound_count, etc.): `SELECT (subquery) FROM users WHERE id = $1`
- Boolean fields: just show True/False
- Tag fields: show matched tag name

---

### W3: Segment Explanation API

**New endpoint**: `GET /api/user/{user_id}/segments/explain`

Returns all enabled segments with per-segment explanation for a specific user.

**File**: `chatbotv2/dashboard/routes/users.py`

Response shape:
```json
{
  "user_id": 123,
  "segments": [
    {
      "segment_id": 1,
      "segment_name": "High-Value Fans",
      "is_member": true,
      "satisfied_count": 3,
      "total_count": 4,
      "rule_evaluation": { ... }
    }
  ]
}
```

**New endpoint**: `GET /api/segments/{segment_id}/explain/{user_id}`

Returns detailed explanation of why a specific user matches (or doesn't match) a specific segment.

Response shape:
```json
{
  "user_id": 123,
  "segment_id": 1,
  "segment_name": "High-Value Fans",
  "is_member": true,
  "rule_evaluation": {
    "type": "group",
    "operator": "AND",
    "satisfied": true,
    "children": [
      {
        "field": "total_spend",
        "field_label": "Total Spend",
        "operator": ">=",
        "value": 5000,
        "satisfied": true,
        "actual_value": 12500
      },
      {
        "field": "has_tag",
        "field_label": "Has Tag",
        "operator": "=",
        "value": "vip",
        "satisfied": false,
        "actual_value": null
      }
    ]
  },
  "satisfied_count": 1,
  "total_count": 2
}
```

---

### W4: Segment Statistics API

**New endpoint**: `GET /api/segments/{segment_id}/stats`

Returns aggregate statistics for a segment:
- member_count (existing)
- field_breakdown: for each rule, how many members satisfy that specific condition
- last_evaluated_at (existing)

**File**: `chatbotv2/dashboard/routes/segments.py`

Response shape:
```json
{
  "segment_id": 1,
  "member_count": 42,
  "last_evaluated_at": "2026-08-23T...",
  "field_stats": [
    {
      "field": "total_spend",
      "field_label": "Total Spend",
      "operator": ">=",
      "value": 5000,
      "matching_count": 42
    },
    {
      "field": "has_tag",
      "field_label": "Has Tag",
      "operator": "=",
      "value": "vip",
      "matching_count": 28
    }
  ]
}
```

Implementation: For each leaf FieldRule, compile单独 a COUNT query with that single condition, execute, collect results.

---

### W5: UI — Segment Badges in Fan List

**File**: `chatbotv2/dashboard/templates/users.html`

Add a "Segments" column to the fan list table. For each user, fetch their segments via the existing `GET /api/user/{user_id}/segments` endpoint (or batch via a new endpoint).

Approach: Add Alpine.js `x-data` to the users page that:
1. On load, fetches segments for each visible user (or batch)
2. Renders segment name badges next to the user row

**Alternative (simpler)**: Add a `GET /api/users/segments-batch?user_ids=1,2,3` endpoint that returns segment memberships for multiple users in one call.

---

### W6: UI — Segment Badges in Chat Header

**File**: `chatbotv2/dashboard/templates/chat.html`

In the chat header (`.chat-header-right`), add a segment badges section that shows which segments the current dialog user belongs to.

Implementation: On page load, fetch `GET /api/user/{dialog_id}/segments` and render badges.

---

### W7: UI — Segment Badges in Profile Page

**File**: `chatbotv2/dashboard/templates/profile.html`

In the user profile card, add a "Segments" section showing which segments the user belongs to with a brief explanation of why.

Implementation: On page load, fetch `GET /api/user/{user_id}/segments/explain` and render badges with hover explanations.

---

### W8: Enhanced Membership Endpoint

Improve `GET /api/user/{user_id}/segments` to include `is_member` boolean and `member_count` for each segment, so the UI can show both membership and segment size.

Current response: `[{id, name}]`
New response: `[{id, name, is_member: bool, member_count: int}]`

---

## Implementation Order

1. **W1**: Bug fixes (pre-requisite, fast)
2. **W2**: Explanation engine (core capability, no external dependencies)
3. **W3**: Explanation API endpoints (depends on W2)
4. **W8**: Enhanced membership endpoint (small, enables W5-W7)
5. **W4**: Segment statistics API (depends on W2)
6. **W5**: UI — fan list badges (depends on W8)
7. **W6**: UI — chat header badges (depends on W8)
8. **W7**: UI — profile badges (depends on W3, W8)

## Files Modified

| File | Changes |
|------|---------|
| `db/segments.py` | Add `enabled_only` param to `list_segments()` |
| `segments/models.py` | Add `FieldEvaluation`, `RuleEvaluation`, `SegmentExplanation` models |
| `segments/evaluator.py` | Add `explain_rule()`, `explain_user_segments()`, `get_segment_stats()` |
| `chatbotv2/dashboard/routes/users.py` | Fix `list_segments` call, add explain endpoint, add batch segments endpoint |
| `chatbotv2/dashboard/routes/bulk_ops.py` | Fix return order bug |
| `chatbotv2/dashboard/routes/segments.py` | Add stats endpoint |
| `chatbotv2/dashboard/templates/users.html` | Add segment badges column |
| `chatbotv2/dashboard/templates/chat.html` | Add segment badges in header |
| `chatbotv2/dashboard/templates/profile.html` | Add segments section |
| `tests/test_segment_intelligence.py` | NEW — tests for explanation, stats, UI integration |

## Test Plan

### Unit Tests (`tests/test_segment_intelligence.py`)
- `test_explain_rule_single_condition_satisfied`
- `test_explain_rule_single_condition_not_satisfied`
- `test_explain_rule_and_group_partial`
- `test_explain_rule_or_group_partial`
- `test_explain_rule_nested_groups`
- `test_explain_rule_unknown_field`
- `test_get_segment_stats_basic`
- `test_get_segment_stats_empty_segment`
- `test_list_segments_enabled_only`
- `test_list_segments_all`

### Integration Tests
- `test_explain_endpoint_returns_correct_structure`
- `test_explain_endpoint_user_not_found`
- `test_explain_endpoint_segment_not_found`
- `test_batch_segments_endpoint`
- `test_stats_endpoint`
- `test_bulk_ops_return_order`

### Regression
- Run full test suite (3016+ tests)
- Verify no regressions in existing segment tests (52 + 24 = 76 tests)
