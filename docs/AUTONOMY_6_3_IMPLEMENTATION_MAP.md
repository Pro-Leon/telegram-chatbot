# AUTONOMY 6.3 — IMPLEMENTATION MAP

## Overview

This document maps every code change required to close the two MEDIUM gaps
identified in the 6.2 end-to-end reconnaissance:

1. **Timing fields** — cooldown checks in the decision engine never fire
   because `hours_since_last_offer`, `hours_since_last_purchase`, and
   `recent_*_count` fields stay at neutral defaults. The DAO has the
   timestamps; the resolver never computes them.

2. **Vault media delivery** — post-purchase sends a text confirmation but
   never delivers purchased media files. The vault reserve/finalize pattern
   exists in the send worker but `handle_post_purchase()` never triggers it.

Both changes preserve all existing invariants. No authority boundaries move.
No new external dependencies. No LLM authority expansion.

---

## Gap 1: Timing Field Resolution (MEDIUM)

### Problem

The `CommercePipelineRequest` defines five timing fields (pipeline.py:152-156)
that flow through `_request_engine_kwargs()` (pipeline.py:349-353) into the
decision engine's `CommerceDecisionContext` (decision.py:81-86). The decision
engine consumes them in steps 5-7 (lines 255-297) for cooldown enforcement.

These fields are never populated. `resolve_commerce_state()` (state.py:282-297)
builds the `CommercePipelineRequest` without setting them. They stay at
`None`/`0`, so:

- Step 5 (recent purchase cooldown): never fires — `hours_since_last_purchase`
  is always `None`
- Step 6 (recent offer cooldown): never fires — `hours_since_last_offer`
  is always `None`
- Step 7 (excessive activity budgets): never fires — `recent_offer_count` and
  `recent_sales_attempt_count` are always `0`

### Evidence

The DAO returns rows with timestamp columns:
- `commerce_offers.created_at` — when the offer was created
- `commerce_offers.purchased_at` — when the purchase was made

Existing indexes support these queries:
- `idx_commerce_offers_creator_user_created` on `(creator_id, user_id, created_at DESC)`

### File Changes

#### 1. `commerce/dao.py` — Add `get_timing_context()`

**New function** (after `has_purchased_product`, ~line 407).

**Contract**: `async def get_timing_context(creator_id: int, user_id: int) -> dict[str, Any]`

Returns a dict with:
- `hours_since_last_offer: float | None`
- `hours_since_last_purchase: float | None`
- `recent_offer_count: int` (active offers in last 24h)
- `recent_purchase_count: int` (purchases in last 24h)
- `recent_sales_attempt_count: int` (all offers in last 24h)

**SQL** (2 queries):

```sql
-- Query 1: most recent offer for this user+creator
SELECT created_at, purchased_at
FROM commerce_offers
WHERE creator_id = $1 AND user_id = $2
ORDER BY created_at DESC
LIMIT 1

-- Query 2: 24h counts
SELECT
    COUNT(*) AS total_offers,
    COUNT(*) FILTER (WHERE state = 'purchased') AS purchases,
    COUNT(*) FILTER (WHERE state IN ('pending', 'clicked')) AS active_offers
FROM commerce_offers
WHERE creator_id = $1 AND user_id = $2
  AND created_at >= NOW() - INTERVAL '24 hours'
```

**Compute**:
- `hours_since_last_offer` = `(now - last_offer.created_at).total_seconds() / 3600`
- `hours_since_last_purchase` = `(now - last_offer.purchased_at).total_seconds() / 3600` if purchased_at is not None
- `recent_offer_count` = `counts["active_offers"]`
- `recent_purchase_count` = `counts["purchases"]`
- `recent_sales_attempt_count` = `counts["total_offers"]`

**Failure**: Return neutral defaults (`None`/`0`) on any exception.

**Invariants preserved**:
- READ-ONLY: only SELECT queries
- CREATOR-SCOPED: all queries filter by `creator_id`
- TIME-BOUNDED: 24h window for count queries
- FAIL-ISOLATED: returns neutral defaults on any exception
- NO LLM: purely deterministic computation

**SQL queries**: 2. Both use existing indexes. No N+1.

**Import needed**: `from datetime import UTC, datetime` (at top of file)

#### 2. `commerce/state.py` — Wire timing into `resolve_commerce_state()`

**Add import** at top of file:
```python
from commerce.dao import get_timing_context
```

**In `resolve_commerce_state()`**, before building `CommercePipelineRequest`
(after segment evaluation, around line 281, before the `pipeline_request = ...` block):

```python
# Timing context for cooldown enforcement
timing = await get_timing_context(creator_id, request.user_id)
```

**In the `CommercePipelineRequest` constructor** (line 282-297), add these
five keyword arguments:

```python
hours_since_last_offer=timing["hours_since_last_offer"],
hours_since_last_purchase=timing["hours_since_last_purchase"],
recent_offer_count=timing["recent_offer_count"],
recent_purchase_count=timing["recent_purchase_count"],
recent_sales_attempt_count=timing["recent_sales_attempt_count"],
```

**No changes needed to**: `decision.py`, `signals.py`, `pipeline.py`,
`context.py` — the existing `_request_engine_kwargs()` already maps these
fields from the pipeline request into the decision context.

---

## Gap 2: Vault Post-Purchase Delivery (MEDIUM)

### Problem

After a purchase is attributed, `handle_post_purchase()` (post_purchase.py:147)
runs three operations:
1. `advance_funnel_to_converted()` — funnel stage update
2. `enqueue_purchase_confirmation()` — text confirmation
3. `schedule_follow_up()` — 24h follow-up

None trigger vault media delivery. The user receives "your content is now
available" but must access it through the Fangate link. There is no
in-Telegram media delivery.

### Architecture Context

The vault reserve/finalize pattern already works in `chatbotv2/main.py`
(lines 132-234). The send worker:
1. Reads `fangate_media_id` from the Redis stream message
2. Calls `reserve_delivery(creator_id, user_id, fangate_media_id, product_id)`
3. Sends media via Telethon (using `media_path` as HTTPS URL)
4. Calls `finalize_delivery(delivery_id, telegram_message_id)` on success

The dashboard vault send route (`dashboard/routes/vault.py:360-412`) enqueues
media sends with:
- `entity` = user_id
- `media_type` = "photo"/"video"/"document"
- `media_path` = HTTPS URL (Fangate preview/download URL)
- `fangate_media_id` = int
- `product_id` = int
- `creator_id` = int

The product's media list is in the `raw` JSONB column of `fangate_products`:
```json
{"media": [{"id": 123, "type": "photo", "preview": "https://..."}]}
```

The `FangateMedia` model (integrations/fangate/models.py:155) has:
- `id: int` — fangate media ID
- `type: str | None` — media type
- `preview: str | None` — HTTPS URL for the media file

### File Changes

#### 1. `commerce/post_purchase.py` — Add `deliver_product_media()`

**New function** (after `schedule_follow_up`, ~line 259).

**Contract**:
```python
async def deliver_product_media(
    creator_id: int,
    user_id: int,
    product_id: int,
    transaction_id: str,
) -> None:
```

**Algorithm**:
1. Query `db_fangate.get_fangate_product(creator_id, product_id)`
2. Parse `product["raw"]` JSONB — extract `media` list
3. For each media item:
   a. Extract `id` (fangate_media_id), `type` (media_type), `preview` (media_path)
   b. Skip if `preview` is None or not HTTPS
   c. Call `db.vault.has_user_received_media(creator_id, user_id, media_id)` — skip if already delivered
   d. Call `db.vault.reserve_delivery(creator_id, user_id, media_id, product_id)` — skip if None
   e. Enqueue via `enqueue_send()` with the same message shape as the dashboard vault route

**Enqueue message shape** (matching dashboard/routes/vault.py:394-411):
```python
{
    "entity": str(user_id),
    "content": "",  # no caption for auto-delivery
    "draft_content": "",
    "was_edited": False,
    "was_auto_approved": False,
    "confidence_score": "1.0",
    "operator_id": "",
    "save_to_db": "true",
    "media_type": media_type,
    "media_path": preview_url,
    "fangate_media_id": str(media_id),
    "product_id": str(product_id),
    "creator_id": str(creator_id),
}
```

**Dedup**: `dedup_id = f"post_purchase_media:{transaction_id}:{media_id}"`

**Failure handling**: Wrap entire function in try/except. Log warning, never
raise. This is best-effort — failures must not break the webhook pipeline.

**Invariants preserved**:
- BEST-EFFORT: failure logged, never breaks webhook pipeline
- IDEMPOTENT: `reserve_delivery` uses ON CONFLICT; dedup_id prevents duplicate enqueue
- CREATOR-SCOPED: all queries filtered by `creator_id`
- EXISTING PATTERN: reuses the send worker's reserve/finalize lifecycle
- NO NEW TABLES: uses existing `vault_media_deliveries` table
- NO NEW WORKERS: uses existing send stream consumer

**Imports needed** (can be lazy inside the function body):
```python
from db import fangate as db_fangate
from db.vault import reserve_delivery, has_user_received_media
from db.redis import enqueue_send
```

#### 2. `commerce/post_purchase.py` — Wire into `handle_post_purchase()`

**In `handle_post_purchase()`** (after the follow-up scheduling block, ~line 197):

```python
# Media delivery (best-effort, after confirmation)
if record.product_id is not None:
    try:
        await deliver_product_media(
            creator_id=record.creator_id,
            user_id=record.user_id,
            product_id=record.product_id,
            transaction_id=record.transaction_id,
        )
    except Exception:
        logger.warning(
            "deliver_product_media step failed (txn=%s)",
            record.transaction_id,
            exc_info=True,
        )
```

**Placement**: Runs AFTER confirmation and follow-up. Failures are caught
independently — they don't affect funnel advancement, confirmation, or
follow-up.

---

## Summary: All Files Changed

| File | Gap | Change Type |
|---|---|---|
| `commerce/dao.py` | 1 | Add `get_timing_context()` — ~40 lines |
| `commerce/state.py` | 1 | Wire timing into `resolve_commerce_state()` — ~8 lines |
| `commerce/post_purchase.py` | 2 | Add `deliver_product_media()` — ~60 lines |
| `commerce/post_purchase.py` | 2 | Wire into `handle_post_purchase()` — ~10 lines |

**Total**: 3 files modified, ~118 lines of new code.

---

## Test Requirements

### Gap 1: Timing Fields

| Test | File | Description |
|---|---|---|
| `test_get_timing_context_no_offers` | `tests/test_commerce_dao_timing.py` | No offers → all defaults (None/0) |
| `test_get_timing_context_with_offer` | `tests/test_commerce_dao_timing.py` | One offer, no purchase → hours_since_last_offer computed, hours_since_last_purchase=None |
| `test_get_timing_context_with_purchase` | `tests/test_commerce_dao_timing.py` | One purchased offer → both hours computed |
| `test_get_timing_context_24h_counts` | `tests/test_commerce_dao_timing.py` | 3 offers in 24h (1 pending, 1 clicked, 1 purchased) → counts correct |
| `test_get_timing_context_creator_isolation` | `tests/test_commerce_dao_timing.py` | Offers from other creators excluded |
| `test_get_timing_context_db_failure` | `tests/test_commerce_dao_timing.py` | DB exception → returns neutral defaults |
| `test_timing_flows_to_decision_engine` | `tests/test_commerce_timing_e2e.py` | State resolution with timing → pipeline request has correct fields |
| `test_cooldown_enforced_after_timing` | `tests/test_commerce_timing_e2e.py` | Offer created 2h ago → decision engine returns NO_OFFER (cooldown active) |
| `test_budget_enforced_after_timing` | `tests/test_commerce_timing_e2e.py` | 3 offers in 24h → decision engine returns NO_OFFER (too many offers) |

### Gap 2: Vault Delivery

| Test | File | Description |
|---|---|---|
| `test_deliver_product_media_success` | `tests/test_post_purchase_delivery.py` | Product with 1 media → reserve + enqueue called |
| `test_deliver_product_media_already_delivered` | `tests/test_post_purchase_delivery.py` | Already delivered → skip, no enqueue |
| `test_deliver_product_media_no_media` | `tests/test_post_purchase_delivery.py` | Product with no media in raw → no-op |
| `test_deliver_product_media_no_preview` | `tests/test_post_purchase_delivery.py` | Media with null preview → skip that media |
| `test_deliver_product_media_db_failure` | `tests/test_post_purchase_delivery.py` | DB exception → warning logged, no crash |
| `test_deliver_product_media_product_not_found` | `tests/test_post_purchase_delivery.py` | Product doesn't exist → warning logged, no crash |
| `test_handle_post_purchase_calls_deliver` | `tests/test_post_purchase_delivery.py` | handle_post_purchase calls deliver_product_media |
| `test_handle_post_purchase_deliver_failure_isolated` | `tests/test_post_purchase_delivery.py` | deliver_product_media raises → confirmation + follow-up still run |

---

## Invariant Verification After Changes

| # | Invariant | Status After Change |
|---|---|---|
| 1 | Creator isolation | PRESERVED — all new queries scoped by creator_id |
| 2 | Deterministic decision boundary | PRESERVED — timing is deterministic from DB timestamps |
| 3 | LLM authority isolation | PRESERVED — no LLM involvement in timing or vault delivery |
| 4 | Offer price immutability | PRESERVED — unchanged |
| 5 | Fangate price verification | PRESERVED — unchanged |
| 6 | Idempotent offer creation | PRESERVED — unchanged |
| 7 | Vault delivery uniqueness | PRESERVED — reserve_delivery uses ON CONFLICT |
| 8 | Vault delivery reservation safety | PRESERVED — reserve before send, finalize after |
| 9 | Redis dedup remains optimization | PRESERVED — unchanged |
| 10 | Telegram side effects at-least-once | PRESERVED — unchanged |
| 11 | DLQ/XAUTOCLAIM behavior intact | PRESERVED — unchanged |
| 12 | Existing operator/manual flows intact | PRESERVED — no changes to dashboard or operator paths |
| 13 | Post-purchase failure isolation | PRESERVED — deliver_product_media is independent try/except |

---

## Performance Implications

| Change | Latency Impact | Mitigation |
|---|---|---|
| get_timing_context() | +2 DB queries (<5ms each) | Uses existing indexes, creator+user scoped |
| deliver_product_media() | +3-5 DB calls per product | Only runs post-purchase (not per-message), best-effort |

**Per-message latency increase**: ~10ms (timing context queries).
**Post-purchase latency increase**: ~20-30ms (vault delivery enqueue).
Neither is user-facing latency — the Telegram response is already queued.

---

## Implementation Order

1. **Gap 1**: Timing fields — straightforward, no external dependencies
2. **Gap 2**: Vault delivery — depends on understanding the send worker pattern

Gaps are independent and can be implemented in parallel.

---

## Post-Implementation Status

After these changes, the autonomy matrix from 6.2 becomes:

| Capability | Before | After |
|-----------|--------|-------|
| Cooldown enforcement | NEVER FIRES | **FUNCTIONAL** |
| Activity budgets | NEVER FIRES | **FUNCTIONAL** |
| Vault media delivery | MISSING | **FUNCTIONAL** |
| Autonomous PPV lifecycle | COMPLETE | COMPLETE |

The only remaining MEDIUM gap (LLM signal extraction as sole trigger) is
by design — conservative behavior is safer.

---

AWAITING IMPLEMENTATION APPROVAL
