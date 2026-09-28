# PHASE 5.0 — RECONNAISSANCE REPORT

## 1. Existing Fan Data Sources

### Primary Tables (directly fan-relevant)

| Table | Fan Fields | Segment-Relevant Data |
|-------|-----------|----------------------|
| `users` | `id`, `username`, `first_name`, `first_seen`, `last_seen`, `message_count`, `funnel_stage`, `is_blocked`, `persona_id`, `notes`, `do_not_auto_reply` | Funnel stage, activity timestamps, message count, blocked status |
| `messages` | `user_id`, `direction`, `content`, `was_edited`, `was_auto_approved`, `confidence_score`, `operator_id`, `created_at`, `media_type`, `fangate_media_id` | Message volume, direction, timing, media sends |
| `fangate_transactions` | `creator_id`, `user_id`, `transaction_id`, `event_type`, `seller_earning`, `product_id`, `occurred_at` | Purchase history, spend, product purchases |
| `vault_media_deliveries` | `creator_id`, `user_id`, `fangate_media_id`, `product_id`, `sent_at`, `status` | Vault delivery history, product delivery counts |
| `conversation_tags` + `conversation_tag_assignments` | `user_id`, `tag_id`, `tag_name` | Tag membership |
| `conversation_attention` | `user_id`, `status`, `assigned_operator_id`, `reviewed_at` | Attention state, operator assignment |
| `commerce_offers` | `creator_id`, `user_id`, `product_id`, `state`, `price_minor`, `created_at`, `purchased_at` | Offer history, purchase state, revenue |
| `operator_queue` | `user_id`, `status`, `confidence_score`, `created_at` | Queue history, pending items |
| `scheduled_messages` | `user_id`, `status`, `execute_at`, `created_at` | Scheduled message state |
| `user_profiles` | `user_id`, `facts` (JSONB) | Structured profile data |
| `conversation_notes` | `user_id`, `content`, `created_at` | Notes existence/count |
| `user_topics` | `user_id`, `topic_name` | Topic interests |

### Existing Indexes on Fan-Relevant Columns

| Table | Index | Columns | Type |
|-------|-------|---------|------|
| `users` | `idx_users_name_trgm` | `(username, first_name)` | GIN trigram |
| `messages` | `idx_messages_user_id_created` | `(user_id, created_at DESC)` | B-tree |
| `fangate_transactions` | `idx_fangate_transactions_user` | `user_id WHERE user_id IS NOT NULL` | Partial B-tree |
| `fangate_transactions` | `idx_fangate_transactions_creator` | `(creator_id, created_at DESC)` | B-tree |
| `vault_media_deliveries` | `idx_vault_deliveries_creator` | `(creator_id, user_id)` | B-tree |
| `vault_media_deliveries` | `idx_vault_deliveries_user` | `(user_id, fangate_media_id)` | B-tree |
| `commerce_offers` | `idx_commerce_offers_user_state` | `(user_id, state)` | B-tree |
| `commerce_offers` | `idx_commerce_offers_creator_product_state` | `(creator_id, product_id, state)` | B-tree |
| `conversation_attention` | `idx_conv_attention_status` | `status` | B-tree |
| `conversation_tag_assignments` | `idx_conv_tag_assign_user` | `user_id` | B-tree |
| `conversation_tag_assignments` | `idx_conv_tag_assign_tag` | `tag_id` | B-tree |
| `operator_queue` | `idx_operator_queue_status` | `(status, created_at)` | B-tree |
| `scheduled_messages` | `idx_scheduled_messages_user` | `(user_id, created_at DESC)` | B-tree |

## 2. Existing Reusable Queries

### Fan-Level Analytics (db/postgres.py)

| Function | What It Computes | Reuse Potential |
|----------|-----------------|-----------------|
| `get_user_analytics(user_id)` | Single-fan: total/inbound/outbound messages, first/last text, avg confidence, percentile rank | High — pattern for per-fan aggregation CTEs |
| `get_conversations_analytics(...)` | All-fans: paginated with message stats, queue count, response time, attention status | **Direct reuse** — existing filter infrastructure (date, tag, operator, attention) |
| `get_conversation_detail(user_id)` | Single-fan deep: message stats, queue stats, response time, attention reasons | High — pattern for complex per-fan evaluation |
| `get_conversations_export_rows(...)` | Same as `get_conversations_analytics` but unpaginated for export | Could be adapted for segment member enumeration |

### Vault Analytics (db/vault.py)

| Function | What It Computes | Reuse Potential |
|----------|-----------------|-----------------|
| `get_delivery_stats(creator_id)` | Aggregate: total deliveries, unique fans, unique media | High |
| `get_product_delivery_counts(creator_id)` | `{product_id: count}` | High — for "delivered from product X" rules |
| `get_top_fans(creator_id, limit)` | Fans ranked by delivery count | High — for vault-engaged segments |
| `get_fan_delivery_count(creator_id, user_id)` | Per-fan delivery count | High — for delivery count thresholds |
| `has_user_received_media(creator_id, user_id, media_id)` | Boolean: fan received specific media | High — for specific media rules |

### Commerce (commerce/dao.py)

| Function | What It Computes | Reuse Potential |
|----------|-----------------|-----------------|
| `has_purchased_product(creator_id, user_id, product_id)` | Boolean: fan purchased specific product | High |
| `list_offers_for_user(user_id, creator_id, state)` | Offer history for a fan | Medium — for offer-state rules |
| `get_ppv_funnel(creator_id, start, end)` | Per-product funnel aggregates | Medium — for revenue-based segments |

### Attention/Tags (db/postgres.py)

| Function | What It Computes | Reuse Potential |
|----------|-----------------|-----------------|
| `get_conversation_attention(user_id)` | Attention state for a fan | Medium |
| `list_user_conversation_tags(user_id)` | Tags for a fan | High — for tag-based rules |
| `list_conversation_tags()` | All tags | High — for tag field enumeration |

## 3. Existing Analytics/Filter Infrastructure

### Conversation Analytics Filtering (get_conversations_analytics)

The existing `get_conversations_analytics` function already supports:
- **Date range filtering**: `m.created_at >= $X AND m.created_at < $Y`
- **Attention filter**: `needs_attention` (unanswered + slow response + frequent queue), `quiet` (no outbound, has inbound)
- **Tag filter**: `user_id IN (SELECT user_id FROM conversation_tag_assignments WHERE tag_id = $X)`
- **Operator filter**: `att.assigned_operator_id = $X`
- **Sort**: `last_activity`, `message_count`, `response_time`, `queue_count`, `confidence`, `user_id`
- **Pagination**: `LIMIT / OFFSET`

### Key Observation

The conversation analytics infrastructure is the closest thing to a segment evaluator. However, it operates on pre-computed aggregates (CTEs computing per-fan message stats), which means:
- It already computes `inbound_count`, `outbound_count`, `last_inbound_at`, `last_outbound_at`, `avg_confidence`, `queue_count`, `avg_response_seconds` per fan
- But it does NOT compute cross-table aggregates (e.g., total spend, delivery count, purchase count)
- It does NOT support arbitrary boolean composition of rules
- It does NOT persist segment definitions

## 4. Creator Scoping Architecture

### Current State

Creator scoping is **NOT uniform** across the codebase:

| Subsystem | Creator Scoped? | How |
|-----------|----------------|-----|
| Fangate products/transactions/wallet | YES | `WHERE creator_id = $X` in all queries |
| Commerce offers | YES | `WHERE creator_id = $X` in all queries |
| Vault deliveries | YES | `WHERE creator_id = $X` in all queries |
| Scheduled messages | YES | `WHERE creator_id = $X` in queries |
| Users/messages | NO | No creator_id column |
| Conversation attention | NO | No creator_id column |
| Conversation tags | NO | No creator_id column |
| Operator queue | NO | No creator_id column |

### Implication for Segments

- Fan identity (`users.id`) is global, not creator-scoped
- Most fan attributes (messages, attention, tags) are global
- Purchase/delivery data is creator-scoped
- **Segments should be creator-scoped** (matching the fangate/commerce pattern), but fan membership evaluation will cross global tables (users, messages) and creator-scoped tables (transactions, deliveries, offers)

### Single-Creator Resolution

`resolve_single_application_creator()` returns:
- `READY` with `creator_id` if exactly 1 active integration
- `CREATOR_CONTEXT_UNAVAILABLE` if 0 active or DB error
- `AMBIGUOUS_CREATOR_CONTEXT` if >1 active (never picks)

**Recommendation**: Segment routes should use the same `resolve_single_application_creator()` pattern used by fangate routes, with `_require_creator()` guard.

## 5. Dashboard Patterns

### Authentication
- Cookie-based session: `session` cookie → `require_auth` dependency → `{"username": ...}`
- Every route: `auth: dict = Depends(require_auth)`

### API Response Pattern
- `JSONResponse(jsonable_encoder(...))` or `JSONResponse({"ok": True})`
- Pydantic models for request bodies

### Frontend Pattern
- Jinja2 templates extending `dashboard.html`
- Alpine.js components: `x-data="componentName()"` with `init()` method
- Loading/error/empty state per resource
- `.app-table` for data tables
- `.stat-card` for KPI cards
- `.modal-overlay` + `.modal-box` for modals
- API helpers: `apiGet/Post/Patch/Delete` (fangate pattern)

### Route Registration
- Router defined in `chatbotv2/dashboard/routes/segments.py`
- Registered in `chatbotv2/dashboard/app.py` as `segments_router`
- Page route added to `chatbotv2/dashboard/routes/pages.py`
- Nav item added to `chatbotv2/dashboard/templates/dashboard.html`

## 6. Test Conventions

- `pytestmark = [pytest.mark.unit]`
- `@pytest.mark.asyncio` on every async test
- `test_client` fixture from conftest (auto-bypasses auth)
- Patch DB functions on the **route module import path**
- `_make_pool(mock_conn)` for DB test setup
- `_FakePoolConnCM(conn)` async context manager
- Test happy path, validation errors (400/404), edge cases

## 7. Exact Fields Available as Segment Rules

### Direct Field Rules (no aggregation needed)

| Field | Source Table | Type | Operators | Values |
|-------|-------------|------|-----------|--------|
| `funnel_stage` | `users` | TEXT | `=`, `!=`, `in` | `"new"`, `"engaged"`, `"paying"`, etc. |
| `is_blocked` | `users` | BOOLEAN | `=`, `!=` | `true`, `false` |
| `do_not_auto_reply` | `users` | BOOLEAN | `=`, `!=` | `true`, `false` |
| `message_count` | `users` | INTEGER | `>=`, `<=`, `>`, `<`, `=`, `!=` | integer |
| `first_seen_days_ago` | `users` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `last_seen_days_ago` | `users` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `has_username` | `users` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `attention_status` | `conversation_attention` | TEXT | `=`, `!=`, `in` | `"new"`, `"reviewed"` |
| `has_attention` | `conversation_attention` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `assigned_operator_id` | `conversation_attention` | INTEGER | `=`, `!=`, `is_null` | operator id |
| `has_tag` | `conversation_tag_assignments` | BOOLEAN | `=`, `!=` | `true`, `false` |
| `tag_name` | `conversation_tag_assignments` | TEXT | `=`, `in`, `not_in` | tag name(s) |
| `has_notes` | `conversation_notes` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `note_count` | `conversation_notes` (derived) | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `has_scheduled_messages` | `scheduled_messages` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `has_pending_queue` | `operator_queue` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `queue_count` | `operator_queue` (derived) | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |

### Message Activity Rules (require aggregation)

| Field | Source | Type | Operators | Values |
|-------|--------|------|-----------|--------|
| `inbound_count` | `messages` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `outbound_count` | `messages` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `total_messages` | `messages` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `last_inbound_days_ago` | `messages` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `last_outbound_days_ago` | `messages` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `first_message_days_ago` | `messages` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `has_inbound` | `messages` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `has_outbound` | `messages` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `message_count_days` | `messages` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (messages in last N days) |

### Purchase/Revenue Rules (require aggregation)

| Field | Source | Type | Operators | Values |
|-------|--------|------|-----------|--------|
| `total_spend_minor` | `fangate_transactions` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer (minor units) |
| `purchase_count` | `fangate_transactions` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `has_purchased` | `fangate_transactions` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `purchased_product_id` | `fangate_transactions` | INTEGER | `=`, `in` | product id(s) |
| `purchased_any_product` | `fangate_transactions` (derived) | BOOLEAN | `=` | `true` |
| `purchased_product_count` | `fangate_transactions` | INTEGER | `>=`, `<=`, `>`, `<` | integer |

### Vault Delivery Rules (require aggregation)

| Field | Source | Type | Operators | Values |
|-------|--------|------|-----------|--------|
| `delivery_count` | `vault_media_deliveries` | INTEGER | `>=`, `<=`, `>`, `<`, `=` | integer |
| `has_delivery` | `vault_media_deliveries` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `delivered_media_id` | `vault_media_deliveries` | INTEGER | `=`, `in` | media id(s) |
| `delivered_product_id` | `vault_media_deliveries` | INTEGER | `=`, `in` | product id(s) |
| `last_delivery_days_ago` | `vault_media_deliveries` (derived) | INTEGER | `>=`, `<=`, `>`, `<` | integer (days) |
| `delivered_product_count` | `vault_media_deliveries` | INTEGER | `>=`, `<=`, `>`, `<` | integer |

### Commerce/Offer Rules

| Field | Source | Type | Operators | Values |
|-------|--------|------|-----------|--------|
| `has_active_offer` | `commerce_offers` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `has_purchased_offer` | `commerce_offers` (derived) | BOOLEAN | `=`, `!=` | `true`, `false` |
| `offer_state` | `commerce_offers` | TEXT | `=`, `in` | `"pending"`, `"clicked"`, `"purchased"`, etc. |
| `offer_product_id` | `commerce_offers` | INTEGER | `=`, `in` | product id(s) |

## 8. Supported Segment Use Cases — Direct vs Derived

| Use Case | Direct Support | Derived SQL Needed |
|----------|---------------|-------------------|
| High-value fans (spend >= X) | — | `SUM(seller_earning) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $1` |
| High-value fans (purchases >= X) | — | `COUNT(*) FROM fangate_transactions WHERE user_id = u.id AND creator_id = $1 AND event_type = 'purchase'` |
| Active fans (message in last X days) | — | `EXISTS (SELECT 1 FROM messages WHERE user_id = u.id AND created_at >= NOW() - INTERVAL 'X days')` |
| Inactive fans (no message for X days) | — | `NOT EXISTS (SELECT 1 FROM messages WHERE user_id = u.id AND direction = 'inbound' AND created_at >= NOW() - INTERVAL 'X days')` |
| New fans (first_seen in X days) | `first_seen` field | `u.first_seen >= NOW() - INTERVAL 'X days'` |
| Product buyers | — | `EXISTS (SELECT 1 FROM fangate_transactions WHERE user_id = u.id AND creator_id = $1 AND product_id = $X)` |
| Vault-engaged (delivered X media) | — | `COUNT(*) FROM vault_media_deliveries WHERE user_id = u.id AND creator_id = $1` |
| Funnel-based | `funnel_stage` field | Direct comparison |
| Tagged fans | — | `EXISTS (SELECT 1 FROM conversation_tag_assignments a JOIN conversation_tags t ON t.id = a.tag_id WHERE a.user_id = u.id AND t.name = $X)` |
| Attention-based | — | `EXISTS (SELECT 1 FROM conversation_attention WHERE user_id = u.id AND status = $X)` |
| Operator-assigned | — | `conversation_attention.assigned_operator_id = $X` |
| Commerce-state | — | `EXISTS (SELECT 1 FROM commerce_offers WHERE user_id = u.id AND creator_id = $1 AND state = $X)` |

## 9. Performance Considerations

### Scale Target: Thousands to Tens of Thousands of Fans

**Performance risks:**
1. **Cross-table aggregation**: Rules requiring `SUM/COUNT` across `fangate_transactions` or `vault_media_deliveries` per fan could be expensive if not indexed
2. **Nested boolean logic**: `OR` with mixed rule types may prevent index usage
3. **CTE fan-out**: Complex rule evaluation may require multiple CTEs joined to `users`

**Mitigations:**
1. Use `EXISTS` instead of `IN` for subqueries (better optimizer behavior)
2. Use `LATERAL` joins for per-fan aggregation where needed
3. Index on `(user_id, created_at)` covers most time-based rules
4. Index on `(creator_id, user_id)` covers cross-table joins
5. Consider materialized membership only if dynamic evaluation proves too slow
6. Use `NOT EXISTS` instead of `NOT IN` for negative rules

### Query Safety
- All queries use parameterized SQL (asyncpg)
- No unbounded row fetching
- `LIMIT` on member enumeration
- `COUNT(*)` for preview counts (fast)

## 10. Required Migrations

### New Table: `fan_segments`

```sql
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

**Rules JSONB schema:**
```json
{
  "type": "group",
  "operator": "AND",
  "children": [
    {
      "type": "rule",
      "field": "message_count",
      "operator": ">=",
      "value": 10
    },
    {
      "type": "group",
      "operator": "OR",
      "children": [
        {
          "type": "rule",
          "field": "has_tag",
          "operator": "=",
          "value": "vip"
        },
        {
          "type": "rule",
          "field": "total_spend_minor",
          "operator": ">=",
          "value": 5000
        }
      ]
    }
  ]
}
```

## 11. Required Backend Modules

| Module | Purpose |
|--------|---------|
| `db/segments.py` | Segment CRUD (asyncpg) |
| `segments/__init__.py` | Package init |
| `segments/models.py` | Pydantic models for rules and segments |
| `segments/evaluator.py` | Rule → SQL compiler + membership evaluator |
| `segments/fields.py` | Field registry (valid fields, operators, SQL generators) |
| `chatbotv2/dashboard/routes/segments.py` | API routes |

## 12. Required API Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/segments` | List segments |
| `POST` | `/api/segments` | Create segment |
| `GET` | `/api/segments/{id}` | Get segment |
| `PATCH` | `/api/segments/{id}` | Update segment |
| `DELETE` | `/api/segments/{id}` | Delete segment |
| `POST` | `/api/segments/{id}/toggle` | Enable/disable segment |
| `POST` | `/api/segments/{id}/duplicate` | Duplicate segment |
| `POST` | `/api/segments/preview` | Preview segment (evaluate without saving) |
| `GET` | `/api/segments/{id}/members` | List segment members |
| `GET` | `/api/segments/{id}/count` | Get member count |
| `GET` | `/api/segments/{id}/check/{user_id}` | Check if user is member |
| `GET` | `/api/segments/fields` | List available fields and operators |

## 13. Required Frontend Changes

| File | Change |
|------|--------|
| `chatbotv2/dashboard/templates/segments.html` | New template: segment list + rule builder |
| `chatbotv2/dashboard/templates/dashboard.html` | Add nav item for Segments |
| `chatbotv2/dashboard/routes/pages.py` | Add `/dashboard/segments` route |

## 14. Test Plan

### Rule Validation Tests
- Valid field + valid operator + valid value
- Invalid field → rejected
- Invalid operator → rejected
- Invalid value type → rejected
- Empty rule group → rejected
- Nested groups (3+ levels)
- Raw SQL injection attempts

### Boolean Logic Tests
- AND composition
- OR composition
- NOT (where supported)
- Mixed AND/OR
- Nested groups

### Segment CRUD Tests
- Create/read/update/delete
- Enable/disable toggle
- Duplicate
- Authentication required
- Creator isolation

### Membership Tests
- Correct matching users
- Non-matching users
- Zero matches
- Count correctness
- Single-user membership check
- Complex rule membership

### Data Source Tests
- Message-based rules
- Purchase rules
- Vault delivery rules
- Tag rules
- Funnel rules
- Attention rules

### Performance Tests
- No N+1 behavior
- Parameterized SQL
- Bounded query behavior

### Regression
- Full test suite: baseline 2673 passed, 2 pre-existing failures
- New tests added
- No regressions

## 15. Security Considerations

- All segment APIs require `require_auth`
- Segment queries include `creator_id` WHERE clause (fan segments table)
- Membership evaluation passes `creator_id` to cross-table queries (transactions, deliveries, offers)
- No raw SQL execution — all rules compiled to parameterized queries
- Rule field/operator validated against allowlist
- No Fangate credential exposure
- No cross-creator data access

## 16. Explicit Non-Goals

- Mass messaging / campaigns
- Automatic message sending
- LLM-driven segmentation
- Automatic marketing actions
- Scheduled campaign execution
- Materialized membership tables (unless proven necessary)
- Caching layer
- Generic rules engine framework
- Scripts/saved replies

## 17. Architectural Invariants Preserved

- Redis Streams architecture
- Redis Pub/Sub architecture
- asyncpg/raw SQL architecture
- Telethon
- Send worker architecture
- Scheduler architecture
- LLM authority model
- Fangate integration architecture
- Vault delivery reservation/idempotency
- PostgreSQL delivery uniqueness
- DLQ/XAUTOCLAIM behavior
- WebSocket event architecture
- Existing authentication model
- Creator isolation
