# PHASE 5.1 — IMPLEMENTATION MAP

## Overview

This map defines every file change required for Phase 5 Auto-Segmentation.
All changes are additive. No existing files are modified except for registration hooks.

---

## File Change Summary

### New Files (7)

| File | Purpose |
|------|---------|
| `db/segments.py` | Segment CRUD queries (asyncpg) |
| `segments/__init__.py` | Package init |
| `segments/models.py` | Pydantic models for rules, segments, field registry |
| `segments/fields.py` | Field definitions: valid fields, operators, SQL generators |
| `segments/evaluator.py` | Rule → SQL compiler + membership evaluator |
| `chatbotv2/dashboard/routes/segments.py` | API routes for segment management |
| `chatbotv2/dashboard/templates/segments.html` | Dashboard UI: segment list + rule builder |

### Modified Files (3)

| File | Change |
|------|--------|
| `chatbotv2/dashboard/app.py` | Register `segments_router` |
| `chatbotv2/dashboard/routes/pages.py` | Add `/dashboard/segments` page route |
| `chatbotv2/dashboard/templates/dashboard.html` | Add nav item for Segments |

### New Test Files (1)

| File | Purpose |
|------|---------|
| `tests/test_segments.py` | Comprehensive test suite for segments |

---

## Detailed File Specifications

### 1. `db/segments.py` — Segment CRUD

**Dependencies:** `db.postgres.get_pool()`

**Functions:**

```python
async def create_segment(creator_id: int, name: str, description: str, rules: dict) -> dict
async def get_segment(creator_id: int, segment_id: int) -> dict | None
async def list_segments(creator_id: int) -> list[dict]
async def update_segment(creator_id: int, segment_id: int, **fields) -> dict | None
async def delete_segment(creator_id: int, segment_id: int) -> bool
async def toggle_segment(creator_id: int, segment_id: int, enabled: bool) -> dict | None
async def duplicate_segment(creator_id: int, segment_id: int, new_name: str) -> dict | None
async def update_member_count(creator_id: int, segment_id: int, count: int) -> None
async def update_last_evaluated(creator_id: int, segment_id: int) -> None
```

**SQL patterns:**
- All queries include `WHERE creator_id = $1`
- `ON CONFLICT (creator_id, LOWER(name)) DO UPDATE` for name uniqueness
- `RETURNING *` on mutations
- `DELETE ... WHERE creator_id = $1 AND id = $2`

**Key queries:**

```sql
-- Create
INSERT INTO fan_segments (creator_id, name, description, rules)
VALUES ($1, $2, $3, $4::jsonb)
RETURNING id, creator_id, name, description, rules, enabled, member_count, last_evaluated_at, created_at, updated_at

-- List
SELECT id, name, description, rules, enabled, member_count, last_evaluated_at, created_at, updated_at
FROM fan_segments
WHERE creator_id = $1
ORDER BY created_at DESC

-- Get
SELECT ... FROM fan_segments WHERE creator_id = $1 AND id = $2

-- Update (partial)
UPDATE fan_segments SET name = $3, description = $4, rules = $5::jsonb, updated_at = NOW()
WHERE creator_id = $1 AND id = $2 RETURNING ...

-- Delete
DELETE FROM fan_segments WHERE creator_id = $1 AND id = $2

-- Toggle
UPDATE fan_segments SET enabled = $3, updated_at = NOW()
WHERE creator_id = $1 AND id = $2 RETURNING ...
```

---

### 2. `segments/__init__.py` — Package Init

Minimal. Exports key classes for convenience.

```python
from segments.models import Segment, SegmentRule, RuleGroup
from segments.evaluator import evaluate_segment
```

---

### 3. `segments/models.py` — Pydantic Models

**Rule AST models:**

```python
class FieldRule(BaseModel):
    """A single field condition: field operator value."""
    type: Literal["rule"]
    field: str           # e.g., "message_count", "has_tag"
    operator: str        # e.g., ">=", "=", "in", "not_in"
    value: Any           # str, int, bool, list[int], list[str]

class RuleGroup(BaseModel):
    """Boolean composition of rules."""
    type: Literal["group"]
    operator: Literal["AND", "OR"]
    children: list[FieldRule | RuleGroup]

# Root rule is always a RuleGroup (even single rules are wrapped)
SegmentRule = RuleGroup

class SegmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    rules: RuleGroup

class SegmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    rules: RuleGroup | None = None

class SegmentResponse(BaseModel):
    id: int
    creator_id: int
    name: str
    description: str
    rules: dict
    enabled: bool
    member_count: int
    last_evaluated_at: str | None
    created_at: str
    updated_at: str

class SegmentPreviewRequest(BaseModel):
    rules: RuleGroup

class SegmentMember(BaseModel):
    user_id: int
    username: str | None
    first_name: str | None
    last_seen: str | None
    message_count: int
```

**Validation:**
- `name`: 1-100 chars, stripped
- `description`: 0-500 chars
- `rules`: Must be valid RuleGroup structure
- No raw SQL allowed in any field

---

### 4. `segments/fields.py` — Field Registry

**Purpose:** Central registry of valid segment fields, their operators, value types, and SQL generators.

**Design:**

```python
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class FieldSpec:
    name: str                           # Display name
    sql_type: str                       # PostgreSQL type
    operators: list[str]                # Valid operators
    value_type: str                     # "int", "str", "bool", "list_int", "list_str"
    sql_generator: Callable             # (table_alias, operator, value) -> SQL fragment
    table: str                          # Primary source table
    description: str                    # Human-readable description

# Registry
FIELDS: dict[str, FieldSpec] = {}

def register_field(name: str, **kwargs) -> FieldSpec: ...
def get_field(name: str) -> FieldSpec | None: ...
def list_fields() -> list[dict]: ...
def validate_rule(rule: dict) -> tuple[bool, str | None]: ...
```

**Field definitions (every field in the registry):**

**User Direct Fields (table: `u`):**
| Field | SQL | Type | Operators |
|-------|-----|------|-----------|
| `funnel_stage` | `u.funnel_stage` | TEXT | `=`, `!=`, `in` |
| `is_blocked` | `u.is_blocked` | BOOLEAN | `=`, `!=` |
| `do_not_auto_reply` | `u.do_not_auto_reply` | BOOLEAN | `=`, `!=` |
| `message_count` | `u.message_count` | INTEGER | `>=`, `<=`, `>`, `<`, `=`, `!=` |
| `first_seen_days_ago` | `EXTRACT(DAY FROM NOW() - u.first_seen)::int` | INTEGER | `>=`, `<=`, `>`, `<` |
| `last_seen_days_ago` | `EXTRACT(DAY FROM NOW() - u.last_seen)::int` | INTEGER | `>=`, `<=`, `>`, `<` |
| `has_username` | `(u.username IS NOT NULL AND u.username != '')` | BOOLEAN | `=`, `!=` |

**Message Activity Fields (subquery per fan):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `inbound_count` | `(SELECT COUNT(*) FROM messages WHERE user_id = u.id AND direction = 'inbound')` | INTEGER | `>=`, `<=`, `>`, `<`, `=`, `!=` |
| `outbound_count` | `(SELECT COUNT(*) FROM messages WHERE user_id = u.id AND direction = 'outbound')` | INTEGER | `>=`, `<=`, `>`, `<`, `=`, `!=` |
| `total_messages` | `(SELECT COUNT(*) FROM messages WHERE user_id = u.id)` | INTEGER | `>=`, `<=`, `>`, `<`, `=`, `!=` |
| `last_inbound_days_ago` | `(SELECT EXTRACT(DAY FROM NOW() - MAX(created_at))::int FROM messages WHERE user_id = u.id AND direction = 'inbound')` | INTEGER | `>=`, `<=`, `>`, `<` |
| `last_outbound_days_ago` | `(SELECT EXTRACT(DAY FROM NOW() - MAX(created_at))::int FROM messages WHERE user_id = u.id AND direction = 'outbound')` | INTEGER | `>=`, `<=`, `>`, `<` |
| `has_inbound` | `(SELECT COUNT(*) FROM messages WHERE user_id = u.id AND direction = 'inbound') > 0` | BOOLEAN | `=`, `!=` |
| `has_outbound` | `(SELECT COUNT(*) FROM messages WHERE user_id = u.id AND direction = 'outbound') > 0` | BOOLEAN | `=`, `!=` |
| `message_count_days` | Parameterized: `(SELECT COUNT(*) FROM messages WHERE user_id = u.id AND created_at >= NOW() - INTERVAL '{N} days')` | INTEGER (with param) | `>=`, `<=`, `>`, `<` |

**Tag Fields (subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `has_tag` | `(SELECT COUNT(*) FROM conversation_tag_assignments a JOIN conversation_tags t ON t.id = a.tag_id WHERE a.user_id = u.id AND LOWER(t.name) = LOWER($N)) > 0` | BOOLEAN (param: tag name) | `=`, `!=` |
| `tag_count` | `(SELECT COUNT(*) FROM conversation_tag_assignments WHERE user_id = u.id)` | INTEGER | `>=`, `<=`, `>`, `<` |

**Attention Fields (subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `attention_status` | `(SELECT status FROM conversation_attention WHERE user_id = u.id)` | TEXT | `=`, `!=`, `in` |
| `has_attention` | `(SELECT COUNT(*) FROM conversation_attention WHERE user_id = u.id) > 0` | BOOLEAN | `=`, `!=` |
| `assigned_operator_id` | `(SELECT assigned_operator_id FROM conversation_attention WHERE user_id = u.id)` | INTEGER | `=`, `!=`, `is_null` |

**Queue Fields (subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `has_pending_queue` | `(SELECT COUNT(*) FROM operator_queue WHERE user_id = u.id AND status = 'pending') > 0` | BOOLEAN | `=`, `!=` |
| `queue_count` | `(SELECT COUNT(*) FROM operator_queue WHERE user_id = u.id)` | INTEGER | `>=`, `<=`, `>`, `<` |

**Purchase Fields (creator-scoped subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `total_spend_minor` | `(SELECT COALESCE(SUM(seller_earning), 0) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $C)` | INTEGER | `>=`, `<=`, `>`, `<`, `=` |
| `purchase_count` | `(SELECT COUNT(*) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $C AND event_type = 'purchase')` | INTEGER | `>=`, `<=`, `>`, `<`, `=` |
| `has_purchased` | `(SELECT COUNT(*) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $C AND event_type = 'purchase') > 0` | BOOLEAN | `=`, `!=` |
| `purchased_product_id` | `(SELECT COUNT(*) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $C AND product_id = $N) > 0` | BOOLEAN (param: product id) | `=`, `in` |

**Vault Delivery Fields (creator-scoped subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `delivery_count` | `(SELECT COUNT(*) FROM vault_media_deliveries WHERE user_id = u.id AND creator_id = $C AND status = 'sent')` | INTEGER | `>=`, `<=`, `>`, `<`, `=` |
| `has_delivery` | `(SELECT COUNT(*) FROM vault_media_deliveries WHERE user_id = u.id AND creator_id = $C AND status = 'sent') > 0` | BOOLEAN | `=`, `!=` |
| `delivered_product_id` | `(SELECT COUNT(*) FROM vault_media_deliveries WHERE user_id = u.id AND creator_id = $C AND product_id = $N AND status = 'sent') > 0` | BOOLEAN (param: product id) | `=`, `in` |
| `last_delivery_days_ago` | `(SELECT EXTRACT(DAY FROM NOW() - MAX(sent_at))::int FROM vault_media_deliveries WHERE user_id = u.id AND creator_id = $C AND status = 'sent')` | INTEGER | `>=`, `<=`, `>`, `<` |

**Commerce/Offer Fields (creator-scoped subquery):**
| Field | SQL Pattern | Type | Operators |
|-------|------------|------|-----------|
| `has_active_offer` | `(SELECT COUNT(*) FROM commerce_offers WHERE user_id = u.id AND creator_id = $C AND state IN ('pending','clicked')) > 0` | BOOLEAN | `=`, `!=` |
| `has_purchased_offer` | `(SELECT COUNT(*) FROM commerce_offers WHERE user_id = u.id AND creator_id = $C AND state = 'purchased') > 0` | BOOLEAN | `=`, `!=` |
| `offer_state` | `(SELECT state FROM commerce_offers WHERE user_id = u.id AND creator_id = $C ORDER BY created_at DESC LIMIT 1)` | TEXT | `=`, `in` |

**SQL Generation Pattern:**

Each field's `sql_generator(table_alias, operator, value)` returns a tuple:
```python
(condition_sql: str, params: list[Any])
```

For example:
```python
def gen_message_count(alias: str, op: str, value: int) -> tuple[str, list]:
    return (f"{alias}.message_count {op} $N", [value])

def gen_has_tag(alias: str, op: str, value: str) -> tuple[str, list]:
    subquery = (
        f"(SELECT COUNT(*) FROM conversation_tag_assignments a "
        f"JOIN conversation_tags t ON t.id = a.tag_id "
        f"WHERE a.user_id = {alias}.id AND LOWER(t.name) = LOWER($N)) > 0"
    )
    if op == "=":
        return (subquery, [value])
    elif op == "!=":
        return (f"NOT {subquery}", [value])
```

**Parameter numbering:** Each rule adds parameters sequentially. The evaluator tracks `$N` globally across the entire rule tree.

---

### 5. `segments/evaluator.py` — Rule → SQL Compiler

**Purpose:** Takes a `RuleGroup` and `creator_id`, produces a complete SQL query that evaluates segment membership.

**Design:**

```python
class SQLCompiler:
    """Compiles a SegmentRule tree into a parameterized SQL WHERE clause."""

    def __init__(self, creator_id: int):
        self.creator_id = creator_id
        self.params: list[Any] = []
        self.param_counter = 0

    def _next_param(self) -> str:
        self.param_counter += 1
        return f"${self.param_counter}"

    def compile(self, rule: RuleGroup) -> str:
        """Compile a rule tree into a SQL WHERE clause fragment."""
        if rule.operator == "AND":
            parts = [self.compile(child) for child in rule.children]
            return " AND ".join(f"({p})" for p in parts)
        elif rule.operator == "OR":
            parts = [self.compile(child) for child in rule.children]
            return " OR ".join(f"({p})" for p in parts)

    def compile_field(self, rule: FieldRule) -> str:
        """Compile a single field rule into SQL."""
        field_spec = get_field(rule.field)
        if not field_spec:
            raise ValueError(f"Invalid field: {rule.field}")
        if rule.operator not in field_spec.operators:
            raise ValueError(f"Invalid operator {rule.operator} for field {rule.field}")
        # Get SQL fragment from field spec
        condition, new_params = field_spec.sql_generator("u", rule.operator, rule.value)
        # Replace parameter placeholders with sequential numbers
        for p in new_params:
            condition = condition.replace("$N", self._next_param(), 1)
            self.params.append(p)
        return condition


async def evaluate_segment(creator_id: int, rules: RuleGroup) -> str:
    """Return the SQL WHERE clause + params for a segment."""
    compiler = SQLCompiler(creator_id)
    where_clause = compiler.compile(rules)
    return where_clause, compiler.params


async def get_segment_members(
    creator_id: int,
    rules: RuleGroup,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return matching fans for a segment."""
    where_clause, params = await evaluate_segment(creator_id, rules)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id, u.username, u.first_name, u.last_seen, u.message_count
            FROM users u
            WHERE {where_clause}
            ORDER BY u.last_seen DESC NULLS LAST
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_segment_count(creator_id: int, rules: RuleGroup) -> int:
    """Return count of matching fans."""
    where_clause, params = await evaluate_segment(creator_id, rules)
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            f"SELECT COUNT(*) FROM users u WHERE {where_clause}",
            *params,
        )
    return count or 0


async def check_user_in_segment(
    creator_id: int,
    rules: RuleGroup,
    user_id: int,
) -> bool:
    """Check if a specific user belongs to a segment."""
    where_clause, params = await evaluate_segment(creator_id, rules)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM users u WHERE u.id = ${len(params) + 1} AND {where_clause})",
            *params, user_id,
        )
    return bool(exists)
```

**Key design decisions:**
- SQL is generated server-side, never exposed to clients
- Parameters are numbered sequentially (`$1`, `$2`, ...) across the entire rule tree
- `creator_id` is injected into creator-scoped subqueries as a parameter
- No unbounded queries — `LIMIT` enforced on member enumeration
- `EXISTS` used for single-user membership check (fast with PK index)

---

### 6. `chatbotv2/dashboard/routes/segments.py` — API Routes

**Dependencies:**
- `chatbotv2.dashboard.auth.require_auth`
- `db.segments.*`
- `segments.evaluator.*`
- `segments.models.*`
- `segments.fields.*`
- `commerce.single_creator.resolve_single_application_creator`

**Auth pattern:** `auth: dict = Depends(require_auth)`

**Creator resolution:**
```python
async def _require_creator() -> int:
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        raise HTTPException(status_code=503, detail="Creator integration not available")
    return ctx.creator_id
```

**Routes:**

```python
router = APIRouter(prefix="/api/segments", tags=["segments"])

@router.get("/fields")
async def api_list_fields(auth=Depends(require_auth)):
    """List all available segment fields and operators."""
    return list_fields()

@router.get("")
async def api_list_segments(auth=Depends(require_auth)):
    """List all segments for the current creator."""
    creator_id = await _require_creator()
    segments = await list_segments(creator_id)
    return segments

@router.post("", status_code=201)
async def api_create_segment(body: SegmentCreate, auth=Depends(require_auth)):
    """Create a new segment."""
    creator_id = await _require_creator()
    # Validate rules
    for rule in flatten_rules(body.rules):
        ok, err = validate_rule(rule)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Invalid rule: {err}")
    segment = await create_segment(creator_id, body.name, body.description, body.rules.model_dump())
    # Evaluate member count in background (or eagerly)
    count = await get_segment_count(creator_id, body.rules)
    await update_member_count(creator_id, segment["id"], count)
    await update_last_evaluated(creator_id, segment["id"])
    segment["member_count"] = count
    return segment

@router.get("/{segment_id}")
async def api_get_segment(segment_id: int, auth=Depends(require_auth)):
    """Get a single segment."""
    creator_id = await _require_creator()
    segment = await get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    return segment

@router.patch("/{segment_id}")
async def api_update_segment(segment_id: int, body: SegmentUpdate, auth=Depends(require_auth)):
    """Update a segment."""
    creator_id = await _require_creator()
    updates = body.model_dump(exclude_unset=True)
    if "rules" in updates:
        for rule in flatten_rules(updates["rules"]):
            ok, err = validate_rule(rule)
            if not ok:
                raise HTTPException(status_code=400, detail=f"Invalid rule: {err}")
        updates["rules"] = updates["rules"].model_dump()
    segment = await update_segment(creator_id, segment_id, **updates)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    # Re-evaluate if rules changed
    if "rules" in updates:
        rules = SegmentRule(**segment["rules"])
        count = await get_segment_count(creator_id, rules)
        await update_member_count(creator_id, segment_id, count)
        await update_last_evaluated(creator_id, segment_id)
        segment["member_count"] = count
    return segment

@router.delete("/{segment_id}")
async def api_delete_segment(segment_id: int, auth=Depends(require_auth)):
    """Delete a segment."""
    creator_id = await _require_creator()
    ok = await delete_segment(creator_id, segment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Segment not found")
    return {"ok": True}

@router.post("/{segment_id}/toggle")
async def api_toggle_segment(segment_id: int, auth=Depends(require_auth)):
    """Toggle segment enabled/disabled."""
    creator_id = await _require_creator()
    segment = await get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    new_enabled = not segment["enabled"]
    segment = await toggle_segment(creator_id, segment_id, new_enabled)
    return segment

@router.post("/{segment_id}/duplicate")
async def api_duplicate_segment(segment_id: int, auth=Depends(require_auth)):
    """Duplicate a segment."""
    creator_id = await _require_creator()
    source = await get_segment(creator_id, segment_id)
    if not source:
        raise HTTPException(status_code=404, detail="Segment not found")
    new_name = f"{source['name']} (Copy)"
    segment = await duplicate_segment(creator_id, segment_id, new_name)
    return segment

@router.post("/preview")
async def api_preview_segment(body: SegmentPreviewRequest, auth=Depends(require_auth)):
    """Preview segment members without saving."""
    creator_id = await _require_creator()
    count = await get_segment_count(creator_id, body.rules)
    members = await get_segment_members(creator_id, body.rules, limit=20)
    return {"count": count, "members": members}

@router.get("/{segment_id}/members")
async def api_get_members(segment_id: int, limit: int = 100, offset: int = 0, auth=Depends(require_auth)):
    """Get members of a segment."""
    creator_id = await _require_creator()
    segment = await get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    rules = SegmentRule(**segment["rules"])
    members = await get_segment_members(creator_id, rules, limit=min(limit, 500), offset=offset)
    return {"members": members, "total": segment["member_count"]}

@router.get("/{segment_id}/count")
async def api_get_count(segment_id: int, auth=Depends(require_auth)):
    """Get member count for a segment."""
    creator_id = await _require_creator()
    segment = await get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    rules = SegmentRule(**segment["rules"])
    count = await get_segment_count(creator_id, rules)
    await update_member_count(creator_id, segment_id, count)
    await update_last_evaluated(creator_id, segment_id)
    return {"count": count}

@router.get("/{segment_id}/check/{user_id}")
async def api_check_member(segment_id: int, user_id: int, auth=Depends(require_auth)):
    """Check if a user belongs to a segment."""
    creator_id = await _require_creator()
    segment = await get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    rules = SegmentRule(**segment["rules"])
    is_member = await check_user_in_segment(creator_id, rules, user_id)
    return {"user_id": user_id, "is_member": is_member}
```

---

### 7. `chatbotv2/dashboard/templates/segments.html` — Dashboard UI

**Extends:** `dashboard.html`

**Template blocks:** `nav_segments`, `page_title`, `extra_head`, `content`

**Alpine.js component:** `segmentsApp()`

**Layout:**
- Two-panel layout (list left, builder right) on wide screens
- Single panel (list, then switch to builder) on narrow screens

**Alpine.js state:**
```javascript
segmentsApp() {
    return {
        // List state
        segments: [],
        loading: true,
        error: null,

        // Builder state
        editing: false,          // false = creating, true = editing
        currentSegment: null,
        segmentName: '',
        segmentDescription: '',
        segmentRules: { type: 'group', operator: 'AND', children: [] },
        saving: false,

        // Preview state
        previewMembers: [],
        previewCount: 0,
        previewLoading: false,

        // Available fields
        fields: [],

        // Modal state
        showBuilder: false,

        // Methods
        init() { this.loadFields(); this.loadSegments(); },
        async loadFields() { ... },
        async loadSegments() { ... },
        openCreate() { ... },
        openEdit(segment) { ... },
        async saveSegment() { ... },
        async deleteSegment(id) { ... },
        async toggleSegment(id) { ... },
        async duplicateSegment(id) { ... },
        async previewSegment() { ... },
        async refreshCount(id) { ... },

        // Rule builder helpers
        addRule(parent) { ... },
        addGroup(parent) { ... },
        removeChild(parent, index) { ... },
        updateRule(rule, field, operator, value) { ... },
        getValidOperators(fieldName) { ... },
        getFieldValueHint(fieldName) { ... },
    }
}
```

**UI Sections:**

1. **Header bar**: Title + "Create Segment" button
2. **Segment list**: Table with columns:
   - Name (clickable)
   - Description
   - Rules summary (auto-generated text)
   - Members (count badge)
   - Status (enabled/disabled toggle)
   - Last evaluated
   - Actions (edit, duplicate, delete)
3. **Rule builder panel** (modal or slide-in):
   - Segment name input
   - Description textarea
   - Rule tree visualization:
     - Each group has AND/OR toggle
     - Each rule has: field dropdown → operator dropdown → value input
     - Add Rule / Add Group buttons
     - Remove button per rule/group
   - Preview button → shows matching fans count + first 20 members
   - Save / Cancel buttons
4. **Preview panel**:
   - Match count
   - Member table (user_id, username, first_name, last_seen, message_count)

**CSS classes:** Reuse existing dashboard CSS variables and patterns:
- `.app-card`, `.app-table`, `.btn-primary`, `.btn-secondary`, `.btn-danger`
- `.stat-card` for preview count
- `.modal-overlay` + `.modal-box` for rule builder
- Custom: `.rule-group`, `.rule-row`, `.rule-tree` for the rule builder visualization

---

### 8. `chatbotv2/dashboard/app.py` — Router Registration

Add after existing router registrations:

```python
from chatbotv2.dashboard.routes.segments import router as segments_router
app.include_router(segments_router)
```

---

### 9. `chatbotv2/dashboard/routes/pages.py` — Page Route

Add:

```python
@router.get("/dashboard/segments")
async def dashboard_segments(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "segments.html")
```

---

### 10. `chatbotv2/dashboard/templates/dashboard.html` — Nav Item

Add in sidebar navigation (after Vault, before Follow-Ups):

```html
<a href="/dashboard/segments" class="nav-item {% block nav_segments %}{% endblock %}">
    <i class="fas fa-layer-group"></i>
    <span>Segments</span>
</a>
```

---

### 11. Migration File

**File:** `db/migrations/20260823030000_fan_segments.sql`

```sql
-- Fan segments for deterministic audience building
CREATE TABLE IF NOT EXISTS fan_segments (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    rules JSONB NOT NULL DEFAULT '{}',
    enabled BOOLEAN DEFAULT TRUE,
    member_count INTEGER DEFAULT 0,
    last_evaluated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fan_segments_creator
    ON fan_segments(creator_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fan_segments_creator_name
    ON fan_segments(creator_id, LOWER(name));
```

---

### 12. `tests/test_segments.py` — Test Suite

**Test classes:**

```python
pytestmark = [pytest.mark.unit]

class TestRuleValidation:
    """Rule field/operator validation."""

class TestBooleanLogic:
    """AND/OR/NOT composition."""

class TestSegmentCRUD:
    """Create/read/update/delete segments."""

class TestSegmentMembership:
    """Membership evaluation correctness."""

class TestSegmentAPI:
    """API route tests."""

class TestFieldRegistry:
    """Field registry completeness."""
```

**Test cases (minimum):**

1. **Rule validation:**
   - Valid field + operator + value
   - Invalid field → 400
   - Invalid operator → 400
   - Invalid value type → 400
   - Empty rule group → handled

2. **Boolean logic:**
   - AND with two rules
   - OR with two rules
   - Nested AND inside OR
   - Mixed AND/OR
   - Single rule wrapped in group

3. **CRUD:**
   - Create segment → 201
   - List segments → 200
   - Get segment → 200
   - Update segment → 200
   - Delete segment → 200
   - Toggle enabled → 200
   - Duplicate → 200
   - Get non-existent → 404

4. **Membership:**
   - Simple rule matches correct users
   - Complex rule with AND
   - Complex rule with OR
   - Tag-based rule
   - Purchase-based rule
   - Delivery-based rule
   - Empty result set
   - Count matches members

5. **API:**
   - All routes require auth → 401 without session
   - Preview returns count + members
   - Check returns boolean
   - Fields endpoint returns field list

6. **Security:**
   - SQL injection in rule value → rejected
   - Invalid field names → rejected
   - Creator isolation enforced

---

## Implementation Order

| Step | Files | Description |
|------|-------|-------------|
| 1 | Migration | Create `fan_segments` table |
| 2 | `segments/models.py` | Pydantic models for rules |
| 3 | `segments/fields.py` | Field registry with SQL generators |
| 4 | `segments/evaluator.py` | Rule → SQL compiler + membership |
| 5 | `db/segments.py` | Segment CRUD queries |
| 6 | `chatbotv2/dashboard/routes/segments.py` | API routes |
| 7 | `chatbotv2/dashboard/templates/segments.html` | Dashboard UI |
| 8 | `chatbotv2/dashboard/app.py` | Register router |
| 9 | `chatbotv2/dashboard/routes/pages.py` | Add page route |
| 10 | `chatbotv2/dashboard/templates/dashboard.html` | Add nav item |
| 11 | `tests/test_segments.py` | Comprehensive tests |
| 12 | Regression run | Full test suite |

---

## Dependency Graph

```
Migration
    ↓
models.py (no deps)
    ↓
fields.py (depends on: models for FieldSpec)
    ↓
evaluator.py (depends on: fields, db.postgres)
    ↓
db/segments.py (depends on: db.postgres)
    ↓
routes/segments.py (depends on: db/segments, evaluator, models, fields, auth, single_creator)
    ↓
templates/segments.html (depends on: API routes)
    ↓
app.py, pages.py, dashboard.html (registration hooks)
    ↓
tests/test_segments.py (depends on: all above)
```

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Complex rule trees generate expensive SQL | Use `EXISTS` subqueries, avoid `IN` for large sets; test with realistic data |
| Parameter numbering errors | Careful sequential `$N` tracking in compiler; test parameter ordering |
| Cross-table joins (global + creator-scoped) | Creator ID injected as parameter into scoped subqueries; no unscoped access |
| Rule validation bypass | Strict allowlist for fields and operators; no raw SQL; test injection attempts |
| N+1 queries in membership evaluation | Single query with CTEs/subqueries per evaluation; test query count |
| Frontend rule builder complexity | Start with AND/OR composition; NOT can be deferred if needed |
