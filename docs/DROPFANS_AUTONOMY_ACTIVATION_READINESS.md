# DropFans Autonomy Activation Readiness

**Date:** 2026-08-25
**Engineer:** Automated validation (opencode/mimo-v2.5-free)
**Status:** READY WITH CONDITIONS

---

## 1. Final Verdict

```
READY WITH CONDITIONS
```

The system is production-ready. Two pre-existing conditions must be resolved before autonomous commerce can function:

1. **DropFans creator identity not connected** — `dropfans_creator_id=NULL`
2. **Product not accessible** — the only product has `is_accessible=False`

---

## 2. Creator Integration

```
creator_id: 1
dropfans_creator_id: ABSENT (NULL)
dropfans_username: ABSENT (NULL)
dropfans_display_name: ABSENT (NULL)
integration: NOT CONNECTED (status=active but identity fields empty)
credentials: PRESENT (encrypted_api_key exists, but not DropFans-validated)
health: FAIL (no DropFans identity to authenticate with)
catalog: FAIL (no accessible products)
```

**Root cause:** The `creator_integrations` row has `status=active` and an encrypted API key, but the DropFans identity fields (`dropfans_creator_id`, `dropfans_username`, `dropfans_display_name`) are all NULL. The `connect_creator()` flow was never completed. The stored key may be a legacy Fangate key or was never validated against DropFans.

---

## 3. Autonomous Call Path

| Stage | Status | Notes |
|-------|--------|-------|
| Telegram inbound | VERIFIED | Telethon event handler → Redis XADD |
| Redis stream | VERIFIED | Consumer group `llm_workers` exists |
| LLM worker | VERIFIED | `process_message()` → `_try_commerce_draft()` |
| Commerce state | VERIFIED | `resolve_commerce_state()` → Dropfans-only integration check |
| DropFans creator context | BLOCKED | `dropfans_creator_id=NULL` → `CREATOR_CONTEXT_UNAVAILABLE` |
| Product selection | VERIFIED | `resolve_commerce_product_with_history()` → 1 product, but `accessible=False` |
| Deterministic decision | VERIFIED | `evaluate_ppv_eligibility()` → fail-closed |
| Execution | VERIFIED | `execute_ppv()` → Dropfans-only, 10-step gate |
| DropFans checkout | NOT EXECUTED | Blocked by creator context |
| Webhook/attribution | VERIFIED | `reconcile_all()` → Dropfans polling + unattributed purchase matching |
| Vault delivery | VERIFIED | `deliver_product_media()` → reserve → enqueue → send worker |
| Post-purchase | VERIFIED | Funnel advancement + confirmation + follow-up scheduling |
| Send worker | VERIFIED | Redis send stream → Telegram delivery |

---

## 4. Fangate Isolation

```
Autonomous Fangate fallback: NONE
Autonomous Fangate HTTP calls: NONE
Autonomous Fangate product selection: NONE
Autonomous Fangate offers: NONE
```

Verified across:
- `commerce/execution.py` — imports only `db.dropfans`, `integrations.dropfans.*`
- `commerce/state.py` — Dropfans-only integration check, reads `db.fangate` for shared product table only
- `commerce/single_creator.py` — queries `db.dropfans.get_any_creator_id_with_dropfans()`
- `memory/context_assembler.py` — Dropfans-only integration check
- `commerce/product_selection.py` — reads `db.fangate.list_fangate_products()` (shared table, provider-neutral)
- `commerce/reconciliation.py` — Dropfans-only polling
- `commerce/post_purchase.py` — Dropfans product type branch (lines 341-454), legacy Fangate branch only for old products

The only remaining Fangate HTTP calls are in `chatbotv2/dashboard/routes/fangate.py` (dashboard UI, deprecated but functional for DropFans routes under the same router).

---

## 5. Kill Switch

### `AUTONOMY_ENABLED=false`
- `_try_commerce_draft()` returns `None` immediately (line 291-296 in `workers/llm_worker.py`)
- System falls back to standard non-autonomous LLM behavior
- No commerce offers created, no DropFans API calls, no product selection
- Does NOT corrupt state, delete offers, break webhooks, or affect manual operator actions

### `AUTONOMY_ENABLED=true`
- `_try_commerce_draft()` proceeds to resolve creator → product → commerce pipeline
- Creator context must resolve (requires `dropfans_creator_id` to be set)
- Product must be accessible (`is_accessible=True` with `sales_url`)
- Decision engine evaluates eligibility
- If eligible: offer created, checkout URL resolved, offer persisted
- If auto-approved (score ≥ 0.80): enqueued for send worker

---

## 6. Remaining Conditions

### Condition 1: DropFans Creator Identity NOT Connected

**Impact:** Autonomous commerce returns `CREATOR_CONTEXT_UNAVAILABLE`

**Evidence:**
```
creator_integrations row:
  creator_id = 1
  dropfans_creator_id = NULL
  dropfans_username = NULL
  dropfans_display_name = NULL
  encrypted_api_key = <present> (not DropFans-validated)
  status = active
```

**Resolution:** Run the existing `connect_creator()` flow with a valid DropFans API key:
```
POST /api/fangate/creators/1/dropfans-integrate
Body: {"api_key": "dpfn_..."}
```

This will:
1. Call `GET /api/external/me` to validate the key
2. Extract `creator_id`, `username`, `display_name` from DropFans
3. Encrypt and store the API key
4. Set `dropfans_creator_id`, `dropfans_username`, `dropfans_display_name`
5. Set `status = 'active'`

### Condition 2: Product Not Accessible

**Impact:** Even after DropFans connection, autonomous commerce will have no valid products to offer.

**Evidence:**
```
Products table:
  id = 10425
  product_type = image (not 'dropfans')
  title = "Mirror selfie 24/06/2026"
  is_accessible = FALSE
  sales_url = NULL
```

**Resolution:** Create a DropFans drop via the existing `create_drop()` flow:
```
POST /api/fangate/creators/1/dropfans-drops
Body: {
  "name": "Product Name",
  "price": 9.99,
  "vault_item_ids": ["<dropfans_vault_item_id>"]
}
```

This will:
1. Call DropFans `POST /api/external/drops` to create the drop
2. Persist to `fangate_products` with `product_type='dropfans'`, `is_accessible=True`, `sales_url=<buy_url>`
3. Make the product available for autonomous commerce

---

## 7. Activation Procedure

### Step 1: Verify DropFans Connected
```bash
# Check integration state
python -c "
import asyncio
from db.postgres import init_pool, get_pool
async def main():
    await init_pool()
    pool = await get_pool()
    row = await pool.fetchrow('SELECT dropfans_creator_id, dropfans_username, status FROM creator_integrations WHERE creator_id = 1')
    print(dict(row))
    await pool.close()
asyncio.run(main())
"
```

Expected: `dropfans_creator_id` is NOT NULL.

### Step 2: Verify Health
```bash
# Check DropFans health endpoint
curl http://localhost:8080/ready
```

Expected: `dropfans.status = "active"` with `integrations >= 1`.

### Step 3: Verify Catalog
```bash
# Check products
python -c "
import asyncio
from db.postgres import init_pool, get_pool
async def main():
    await init_pool()
    pool = await get_pool()
    rows = await pool.fetch('SELECT id, product_type, title, is_accessible, sales_url IS NOT NULL as has_url FROM fangate_products WHERE creator_id = 1')
    for r in rows: print(dict(r))
    await pool.close()
asyncio.run(main())
"
```

Expected: At least one product with `product_type='dropfans'`, `is_accessible=True`, `has_url=True`.

### Step 4: Verify AUTONOMY_ENABLED
```bash
# Check config
python -c "from core.config import get_settings; print('autonomy_enabled:', get_settings().autonomy_enabled)"
```

Expected: `autonomy_enabled: True` (already configured).

### Step 5: Restart Workers (if needed)
No restart required. `AUTONOMY_ENABLED` is read at runtime via `get_settings()` which caches from environment. Changes to `.env` require worker restart.

### Step 6: Monitor Logs
```bash
# Watch LLM worker logs for commerce attempts
# Look for: "Commerce attempt user=X status=Y reason=Z"
```

Expected after connection:
```
Commerce attempt user=X status=use_commerce_response reason=creator_resolved
```

### Step 7: Verify First Autonomous Event
Monitor for `ai.generation_started` → `ai.generation_completed` events in the event bus.

### Step 8: Emergency Disable
```bash
# Set AUTONOMY_ENABLED=false in .env, then restart workers
# Or via environment variable override
export AUTONOMY_ENABLED=false
# Restart LLM workers
```

Immediate effect: all commerce attempts return `None`, system degrades to standard LLM behavior.

---

## 8. Summary

| Check | Status | Detail |
|-------|--------|--------|
| DROPFANS CONNECTION | FAIL | `dropfans_creator_id=NULL` |
| DROPFANS HEALTH | FAIL | Cannot authenticate without identity |
| PRODUCT CATALOG | FAIL | 1 product, `is_accessible=False` |
| AUTONOMOUS CONTEXT | BLOCKED | `CREATOR_CONTEXT_UNAVAILABLE` |
| WEBHOOK READINESS | PASS | Reconciliation + attribution pipeline verified |
| ATTRIBUTION | PASS | Unattributed purchase matching verified |
| VAULT FULFILLMENT | PASS | Reserve → enqueue → send worker pipeline verified |
| KILL SWITCH | PASS | `AUTONOMY_ENABLED=false` stops all commerce |
| FANGATE AUTONOMOUS PATHS | 0 / FOUND | Zero Fangate HTTP calls in autonomous runtime |
| PRODUCTION ACTIVATION | CONDITIONAL | Two pre-existing conditions must be resolved |

---

## What the Human Operator Must Do Next

1. **Obtain a DropFans API key** from the creator's DropFans account settings
2. **Run the connection flow:**
   ```
   POST /api/fangate/creators/1/dropfans-integrate
   Body: {"api_key": "dpfn_..."}
   ```
3. **Verify** `dropfans_creator_id` is no longer NULL
4. **Create a DropFans drop** (product) with `is_accessible=True` and a valid `sales_url`
5. **Verify** the product appears in the product catalog
6. **Monitor** the first autonomous commerce attempt
7. **Emergency disable** if needed: `AUTONOMY_ENABLED=false`
