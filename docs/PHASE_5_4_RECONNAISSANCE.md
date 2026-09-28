# Phase 5.4 — Segment Intelligence Reconnaissance Report

**Date:** 2026-08-23
**Status:** COMPLETE

## 1. Current Architecture Summary

The segmentation system (Phase 5.2) provides a deterministic, rule-based audience builder:

- **Rule AST**: `FieldRule` (leaf condition) and `RuleGroup` (AND/OR composition) in `segments/models.py`
- **Field Registry**: 35 fields across 8 categories in `segments/fields.py` — each with SQL generators, operator allowlists, and descriptions
- **SQL Compiler**: `SQLCompiler` in `segments/evaluator.py` compiles rule trees to parameterized PostgreSQL WHERE clauses (no raw SQL accepted from clients)
- **Membership**: Evaluated live via `get_segment_members()`, `get_segment_count()`, `check_user_in_segment()` — no materialized membership table
- **CRUD**: `db/segments.py` provides asyncpg queries against `fan_segments` table (creator-scoped)
- **API**: 12 routes in `routes/segments.py` (fields, CRUD, toggle, duplicate, preview, members, count, check)
- **UI**: Alpine.js rule builder in `segments.html` with live preview

## 2. Field Registry Inventory

### Categories
| Category | Fields | Description |
|----------|--------|-------------|
| Basic Info | funnel_stage, is_blocked, do_not_auto_reply, message_count, days_since_first_seen, days_since_last_seen, has_username | Fan profile fields |
| Name | first_name, last_name, username | Text fields from users table |
| Messages | inbound_count, outbound_count, total_messages, days_since_last_inbound, days_since_last_outbound, days_since_first_message, has_sent_inbound, has_received_outbound | Message activity fields |
| Tags | has_tag, tag_count | Conversation tag fields |
| Attention | attention_status, has_attention_record, assigned_operator_id, has_queue_items, queue_item_count | Conversation routing fields |
| Commerce | total_spend, purchase_count, has_purchased, purchased_product_id, distinct_products_purchased | Fangate transaction fields |
| Vault | delivery_count, has_delivery, delivered_from_product, days_since_last_delivery, distinct_products_delivered | Vault media delivery fields |
| Offers | has_pending_offer, has_purchased_offer, latest_offer_state | Commerce offer fields |

### Key Design Points
- All SQL identifiers come from `fields.py` — never from client input
- `$PH` placeholders replaced sequentially by `SQLCompiler`
- `creator_id` is always `$1` — injected first by `SQLCompiler.__init__`
- Creator-scoped fields use subqueries with `creator_id` filter
- `value_type` controls UI input type: `int`, `str`, `bool`, `list_int`, `list_str`, `none`

## 3. SQL Compilation Flow

```
Client RuleGroup (JSON)
  → Pydantic validation (FieldRule | RuleGroup)
  → SQLCompiler.compile(rule)
    → _compile_group(): AND/OR join of children
    → _compile_field(): field_spec.sql_generator(alias, params, op, value)
      → Returns (sql_with_$PH, param_count)
    → $PH replaced with $N placeholders
  → Parameterized WHERE clause + params list
  → Executed via asyncpg with $N binding
```

## 4. Creator Scoping

- **Users/Messages**: Global (not creator-scoped) — `users.id`, `messages.user_id`
- **fangate_transactions**: Creator-scoped via `creator_id` column
- **vault_media_deliveries**: Creator-scoped via `creator_id` column
- **commerce_offers**: Creator-scoped via `creator_id` column
- **conversation_tags/assignments**: Global but filtered by message ownership
- **conversation_attention**: Global

The `SQLCompiler` always injects `creator_id` as `$1` for use by creator-scoped field generators.

## 5. Membership Evaluation

No materialized membership. Every call to `get_segment_members()`, `get_segment_count()`, or `check_user_in_segment()` compiles the rule tree to SQL and executes it against the database. This is:
- **Pro**: Always up-to-date, no stale data, no sync complexity
- **Con**: Full table scan per evaluation (mitigated by WHERE clause indexing)

## 6. Integration Points (Phase 5.3)

| Location | Integration | Pattern |
|----------|-------------|---------|
| `routes/users.py` | `?segment_id` filter on fan list | Compile rule → WHERE clause |
| `routes/dialogs.py` | `?segment_id` filter on dialog list | Compile rule → WHERE clause |
| `routes/analytics.py` | `?segment_id` filter on conversations | Compile rule → member IDs → user_ids param |
| `routes/bulk_ops.py` | `_resolve_user_ids()` merges explicit IDs + segment members | Compile rule → member IDs → union |
| `routes/users.py` | `GET /api/user/{id}/segments` returns matching segments | Iterate all enabled segments, check membership |
| `schemas.py` | `segment_id` field on bulk request schemas | Optional int parameter |

## 7. Bugs Found During Reconnaissance

### Bug 1: `list_segments` missing `enabled_only` parameter
- **File**: `chatbotv2/dashboard/routes/users.py:137`
- **Call**: `sdb.list_segments(ctx.creator_id, enabled_only=True)`
- **Actual signature**: `list_segments(creator_id: int)` — no `enabled_only` param
- **Impact**: TypeError at runtime when filtering users by segment
- **Fix**: Add `enabled_only` parameter to `db/segments.py::list_segments()`

### Bug 2: `bulk_ops.py` inverted return order
- **File**: `chatbotv2/dashboard/routes/bulk_ops.py:37`
- **Signature**: `async def _resolve_user_ids(...) -> tuple[list[int] | JSONResponse, str | None]`
- **Returns**: `(None, JSONResponse(...))` on error, `(resolved, None)` on success
- **Callers unpack**: `user_ids, err = await _resolve_user_ids(...)`
- **Impact**: On error, `user_ids` gets the JSONResponse and `err` gets None — error never detected
- **Fix**: Swap return order to `(list[int], JSONResponse | None)` and fix the one error return

## 8. Gaps Identified for Phase 5.4

### No Explanation Capability
- No endpoint returns human-readable explanation of why a user matches a segment
- No UI shows "this fan matches these rules" anywhere
- `GET /api/user/{id}/segments` returns `[{id, name}]` — no rule details

### No Per-User Rule Evaluation Visibility
- Operators can't see which specific rules a user satisfies or fails
- No "dry run" explanation for a specific user against a segment

### Limited Segment Statistics
- Only `member_count` and `last_evaluated_at` stored
- No breakdown by field (e.g., "12 fans match rule A, 8 match rule B")
- No trend data (member count over time)

### No Segment Health Indicators
- No warning when a segment has 0 members
- No warning when rules reference fields that may return no results
- No validation feedback beyond "valid/invalid"

## 9. File Inventory

### Segment Core
- `segments/models.py` (70 lines) — FieldRule, RuleGroup, SegmentRule, flatten_rules()
- `segments/fields.py` (876 lines) — 35 fields, SQL generators, validate_rule(), list_fields()
- `segments/evaluator.py` (194 lines) — SQLCompiler, compile_rule(), validate_rules(), membership functions
- `db/segments.py` (191 lines) — CRUD operations

### Integration Points
- `chatbotv2/dashboard/routes/users.py` (155 lines) — Fan list + user segments
- `chatbotv2/dashboard/routes/dialogs.py` (79 lines) — Dialog list
- `chatbotv2/dashboard/routes/analytics.py` (182 lines) — Conversations analytics
- `chatbotv2/dashboard/routes/bulk_ops.py` (199 lines) — Bulk operations
- `chatbotv2/dashboard/schemas.py` (144 lines) — Request/response models

### UI
- `chatbotv2/dashboard/templates/segments.html` (641 lines) — Segment management page
- `chatbotv2/dashboard/templates/users.html` (47 lines) — Fan list (Jinja)
- `chatbotv2/dashboard/templates/chat.html` (1096+ lines) — Conversation view
- `chatbotv2/dashboard/templates/profile.html` (255 lines) — Fan profile
- `chatbotv2/dashboard/templates/dashboard.html` (532 lines) — Shell with sidebar nav

### Auth/Scoping
- `chatbotv2/dashboard/auth.py` (88 lines) — Cookie session auth
- `chatbotv2/dashboard/dependencies.py` — Shared validation helpers
- `commerce/single_creator.py` — SingleCreatorStatus, resolve_single_application_creator()

### Database
- `db/postgres.py` (2638 lines) — All PostgreSQL queries including get_conversations_analytics()

### Tests
- `tests/test_segments.py` — 52 tests (7 classes)
- `tests/test_segment_integration.py` — 24 tests
- `tests/test_conversations_analytics.py` — Conversation analytics tests

## 10. Test Baseline

- **Total**: 3016 passed, 2 failed (pre-existing), 0 regressions
- Pre-existing failures: `test_fangate_integration.py::TestConfig::test_defaults` (env-dependent), `test_fangate_integration.py::TestPhase5BRoutes::test_dashboard_page_no_creator`
