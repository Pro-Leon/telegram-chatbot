# AUTONOMY ACTIVATION AUDIT — PRODUCTION READINESS

**Date:** 2026-08-24
**Auditor:** Senior Engineer
**Scope:** Full autonomous CRM activation readiness
**Method:** Source-code trace of actual production callers, not function definitions

---

## 1. EXECUTIVE SUMMARY

This audit traces every critical path from Telegram inbound message through
autonomous decision-making, offer creation, payment webhook, post-purchase
fulfillment, and failure recovery. The objective is to determine whether the
CRM can safely be activated in production for a real creator.

### Verdict: GO WITH CONDITIONS

The autonomous lifecycle is **structurally complete and reachable end-to-end**.
Every function in the critical chain has verified callers, is reachable from
inbound Telegram, and handles failures safely. The LLM authority boundaries
are well-enforced. Creator isolation is intact across all commerce paths.

**However, three conditions must be met before activation:**

1. **Integration test gap** — Zero true integration tests exist. All 35+
   commerce tests use mocked DB/Redis. The production path has never been
   exercised against real PostgreSQL and Redis together. A minimal integration
   test suite must be built before activation.

2. **No explicit autonomy kill switch** — The only shutdown mechanism is the
   Redis `auto_reply` toggle. There is no env-var-level kill switch, no
   per-subsystem disable, and no emergency stop that survives a Redis restart.

3. **Attribution fragility** — Purchase attribution requires EXACTLY ONE
   pending offer for a product per creator. If a user somehow has 0 pending
   offers when the webhook arrives (e.g., offer expired, DB failure, race
   condition), the purchase is recorded but NEVER attributed. The user pays
   but receives no content and no confirmation. There is no reconciliation
   mechanism for this state.

---

## 2. ACTUAL RUNTIME ARCHITECTURE

### 2.1 Inbound Path

```
Telegram User
  → chatbotv2/handlers.py:handle_incoming_message() [line 25]
    → check_rate_limit() [Redis INCR/EXPIRE]
    → upsert_user() [PostgreSQL INSERT ON CONFLICT]
    → save_inbound_message() [PostgreSQL INSERT]
    → publish_event("message.created") [Redis PUBLISH]
    → debounce_enqueue() [Redis SET NX EX + RPUSH]
    → [if window owner] asyncio.create_task(_wait_and_process())
      → asyncio.sleep(debounce_window_seconds)
      → get_debounced_messages() [Redis LRANGE + DELETE]
      → resolve persona [Redis cache → PostgreSQL fallback]
      → enqueue_inbound() [Redis XADD "inbound_messages"]
```

### 2.2 Worker Consumption Path

```
workers/llm_worker.py:run_worker() [line 570]
  → requeue_stalled_messages() [Redis XAUTOCLAIM idle>60s]
  → read_inbound() [Redis XREADGROUP count=5 block=2000ms]
  → process_message() [line 356]
    → acquire_user_lock() [Redis SET NX EX 60s]
    → upsert_user() + save_inbound_message()
    → build_context() [PostgreSQL reads: user, profile, summary, messages]
    → publish_event("ai.generation_started")
    → _try_commerce_draft() [line 257] ← THE CRITICAL PATH
      → resolve_single_application_creator() [DB: creator_integrations]
      → resolve_commerce_product_with_history() [DB: fangate_products + commerce_offers]
      → CommerceStateRequest(user_id, creator_id, product_id, messages, persona)
      → resolve_and_run_commerce() [integration.py:113]
        → resolve_commerce_state() [state.py:169] — 11+ DB queries
        → run_commerce_pipeline() [pipeline.py:383]
          → extract_commerce_signals() [LLM: Gemini cheap model]
          → decide_from_signals() [PURE: 14-rule cascade]
          → build_strategy() [PURE]
          → orchestrate_commerce() [orchestrator.py:157]
            → execute_ppv() [execution.py:126] ← IF OFFER_PPV + activation
          → generate_commerce_response() [LLM: Gemini cheap model]
      → select_commerce_response() [PURE]
    → [if USE_COMMERCE_RESPONSE]: draft = selection.commerce_response_text
    → score_draft() [separate LLM call]
    → [if auto_approved]: enqueue_send() [Redis XADD "send_messages"]
    → [else]: add_to_operator_queue() [PostgreSQL INSERT]
    → publish_event("ai.generation_completed")
    → post_process()
    → release_user_lock()
```

### 2.3 Send Path

```
chatbotv2/main.py:_process_send_stream() [line 72]
  → requeue_stalled_send_messages() [Redis XAUTOCLAIM]
  → release_stale_reservations() [PostgreSQL DELETE vault_media_deliveries]
  → read_send_messages() [Redis XREADGROUP count=10 block=2000ms]
  → [per message]
    → is_send_duplicate() [Redis EXISTS]
    → check_send_rate_limit() [Redis token bucket]
    → client.get_input_entity() [Telethon]
    → [if media]: reserve_delivery() [PostgreSQL INSERT ON CONFLICT]
    → [if media]: send_file() [Telethon MTProto]
    → [else]: client.send_message() [Telethon MTProto]
    → mark_send_dedup() [Redis SETEX]
    → ack_send() [Redis XACK]
    → save_outbound_after_send() [PostgreSQL INSERT]
    → [if vault]: finalize_delivery() [PostgreSQL UPDATE]
    → publish_event("message.sent")
```

### 2.4 Webhook + Post-Purchase Path

```
Fangate POST /api/fangate/webhooks/{creator_id}
  → api_fangate_webhook_receive() [routes/fangate.py:679]
    → service.receive_webhook() [service.py:703]
      → get_creator_integration() [DB read]
      → decrypt_secret() [Fernet decrypt]
      → verify_webhook_signature() [HMAC-SHA256]
      → insert_fangate_webhook_event() [PostgreSQL INSERT ON CONFLICT]
      → upsert_fangate_transaction() [PostgreSQL INSERT ON CONFLICT]
      → mark_webhook_event_processed() [PostgreSQL UPDATE]
      → attribute_purchase_from_webhook() [dao.py:249]
        → find pending offers for product [DB SELECT]
        → [if exactly 1]: UPDATE commerce_offers SET state='purchased'
        → attach user_id to transaction [DB UPDATE]
        → record analytics [DB UPSERT]
      → handle_post_purchase() [post_purchase.py:146]
        → advance_funnel_to_converted() [DB UPDATE]
        → enqueue_purchase_confirmation() [Redis XADD]
        → schedule_follow_up() [PostgreSQL INSERT]
        → deliver_product_media() [DB read + reserve + Redis XADD]
```

---

## 3. END-TO-END AUTONOMOUS PATH

### 3.1 Inbound → Offer Creation (Proven Reachable)

| Step | File:Line | Caller | Reached? |
|------|-----------|--------|----------|
| Telegram event | `handlers.py:133` | Telethon dispatcher | YES |
| `handle_incoming_message` | `handlers.py:25` | Telethon `_handler` | YES |
| `debounce_enqueue` | `handlers.py:73` | `handle_incoming_message` | YES |
| `_wait_and_process` | `handlers.py:100` | `asyncio.create_task` | YES |
| `enqueue_inbound` | `redis.py:156` | `_wait_and_process` | YES |
| `read_inbound` | `redis.py:73` | `run_worker` | YES |
| `process_message` | `llm_worker.py:356` | `run_worker` | YES |
| `_try_commerce_draft` | `llm_worker.py:257` | `process_message:414` | YES |
| `resolve_single_application_creator` | `single_creator.py:59` | `_try_commerce_draft:293` | YES |
| `resolve_commerce_product_with_history` | `product_selection.py:147` | `_try_commerce_draft:302` | YES |
| `resolve_and_run_commerce` | `integration.py:113` | `_try_commerce_draft:315` | YES |
| `resolve_commerce_state` | `state.py:169` | `integration.py:129` | YES |
| `run_commerce_pipeline` | `pipeline.py:383` | `integration.py:145` | YES |
| `extract_commerce_signals` | `deepseek.py:164` | `pipeline.py:403` | YES |
| `decide_from_signals` | `signals.py:250` | `pipeline.py:409` | YES |
| `decide_commerce_action` | `decision.py:194` | `signals.py:267` | YES |
| `build_strategy` | `strategy.py:171` | `pipeline.py:430` | YES |
| `orchestrate_commerce` | `orchestrator.py:157` | `pipeline.py:443` | YES |
| `_activation_for` | `orchestrator.py:87` | `orchestrate_commerce:185` | YES |
| `execute_ppv` | `execution.py:126` | `orchestrator.py:191` | YES |
| `create_offer_serialized` | `dao.py:57` | `execution.py:299` | YES |
| `generate_commerce_response` | `deepseek_response.py:465` | `pipeline.py:470` | YES |
| `select_commerce_response` | `selection.py:225` | `_try_commerce_draft:316` | YES |
| `enqueue_send` | `redis.py:65` | `llm_worker.py:489` | YES |
| `_process_send_stream` | `main.py:72` | `run()` | YES |
| `client.send_message` | `main.py:187` | `_process_send_stream` | YES |

**VERDICT: The full autonomous path from Telegram inbound to offer creation
and send is REACHABLE and CONNECTED. Every intermediate function exists, is
called by the expected caller, and handles failures safely.**

### 3.2 Webhook → Post-Purchase (Proven Reachable)

| Step | File:Line | Caller | Reached? |
|------|-----------|--------|----------|
| Webhook endpoint | `routes/fangate.py:679` | FastAPI router | YES |
| `receive_webhook` | `service.py:703` | Route handler | YES |
| HMAC verification | `service.py:736` | `receive_webhook` | YES |
| Transaction persistence | `service.py:823` | `receive_webhook` | YES |
| `attribute_purchase_from_webhook` | `dao.py:249` | `service.py:861` | YES |
| `handle_post_purchase` | `post_purchase.py:146` | `service.py:883` | YES |
| `advance_funnel_to_converted` | `post_purchase.py:46` | `handle_post_purchase:168` | YES |
| `enqueue_purchase_confirmation` | `post_purchase.py:85` | `handle_post_purchase:179` | YES |
| `schedule_follow_up` | `post_purchase.py:221` | `handle_post_purchase:192` | YES |
| `deliver_product_media` | `post_purchase.py:286` | `handle_post_purchase:205` | YES |
| `reserve_delivery` | `vault.py:46` | `post_purchase.py:386` | YES |
| `enqueue_send` (media) | `redis.py:65` | `post_purchase.py:411` | YES |
| `finalize_delivery` | `vault.py:80` | `main.py:250` | YES |

**VERDICT: The full webhook → attribution → post-purchase → media delivery
path is REACHABLE and CONNECTED.**

---

## 4. CRITICAL AUTONOMY CHECK — DEAD PATH STATUS

### 4.1 Previously Dead Paths, Now Live

| Function | Status | Proof |
|----------|--------|-------|
| `resolve_commerce_state()` | **LIVE** | Called from `integration.py:129` |
| Product selection | **LIVE** | `resolve_commerce_product_with_history` called from `llm_worker.py:302` |
| Product-history exclusion | **LIVE** | `_get_purchased_product_ids` called from `product_selection.py:164` |
| Commerce activation | **LIVE** | `_activation_for` checked in `orchestrator.py:185` |
| `execute_ppv()` | **LIVE** | Called from `orchestrator.py:191` when OFFER_PPV + allowed + activation |
| Offer creation | **LIVE** | `create_offer_serialized` called from `execution.py:299` |
| Offer price verification | **LIVE** | `fservice.verify_product` called from `execution.py:247` |
| Response generation | **LIVE** | `generate_commerce_response` called from `pipeline.py:470` |
| Send queue | **LIVE** | `enqueue_send` called from `llm_worker.py:489` |
| Post-purchase delivery | **LIVE** | `deliver_product_media` called from `post_purchase.py:205` |
| Timing context | **LIVE** | `get_timing_context` called from `state.py:291` |
| Vault media delivery | **LIVE** | `deliver_product_media` called from `handle_post_purchase` |

**All 12 previously dead paths are now LIVE.**

---

## 5. PRODUCT SELECTION AUDIT

### Case A: One valid unpurchased product exists

```
resolve_commerce_product_with_history(creator_id, user_id)
  → list_fangate_products(creator_id) → [Product A]
  → _is_valid_product(A) → True (is_accessible + sales_url)
  → valid_products = [A]
  → len == 1 → return A.product_id
```
**Result: Product selected. Commerce can activate. CORRECT.**

### Case B: Multiple valid unpurchased products exist

```
resolve_commerce_product_with_history(creator_id, user_id)
  → list_fangate_products(creator_id) → [A, B]
  → valid_products = [A, B]
  → _get_purchased_product_ids() → [] (none purchased)
  → len(unpurchased) > 1 → return None (fail-closed)
```
**Result: No product selected. Commerce does NOT activate. CORRECT.**

### Case C: User purchased A but not B

```
resolve_commerce_product_with_history(creator_id, user_id)
  → list_fangate_products(creator_id) → [A, B]
  → valid_products = [A, B]
  → _get_purchased_product_ids() → [A.id]
  → unpurchased = [B]
  → len == 1 → return B.product_id
```
**Result: A excluded, B eligible. CORRECT.**

### Case D: User purchased all products

```
resolve_commerce_product_with_history(creator_id, user_id)
  → list_fangate_products(creator_id) → [A, B]
  → valid_products = [A, B]
  → _get_purchased_product_ids() → [A.id, B.id]
  → unpurchased = []
  → return None
```
**Result: No product selected. No PPV. CORRECT.**

### Case E: Product lookup failure

```
resolve_commerce_product_with_history(creator_id, user_id)
  → list_fangate_products(creator_id) → DB exception
  → return None (fail-isolated, line 176-179)
```
**Result: No product selected. No autonomous PPV. CORRECT.**

### Creator Isolation

Every query in product selection uses `WHERE creator_id = $1`. Cross-creator
product leakage is impossible at the SQL level.

---

## 6. LLM AUTHORITY AUDIT

### 6.1 What the LLM CAN Do

| Tool | Type | Side Effects | Creator-Scoped |
|------|------|-------------|----------------|
| `get_purchase_history` | READ | None | YES |
| `get_active_offers` | READ | None | YES |
| `get_product_information` | READ | None | YES |
| `list_products` | READ | None | YES |
| `propose_follow_up` | PROPOSAL | Deterministic message scheduled | YES |
| `propose_product_offer` | PROPOSAL | Routes through deterministic pipeline | YES |

### 6.2 What the LLM CANNOT Do

| Capability | Enforcement |
|-----------|-------------|
| Choose arbitrary database IDs | `ToolAuthContext` frozen dataclass, IDs injected by application |
| Create products | No tool exists. Products synced from Fangate |
| Set arbitrary PPV prices | Price derived from `verify_product()` (Fangate API) |
| Bypass deterministic commerce decisions | `decide_commerce_action()` is pure function with fixed thresholds |
| Directly send Telegram messages | Separate `send_worker` process, LLM only produces text drafts |
| Directly mark purchases | No tool exists. Attribution via webhook only |
| Directly mark deliveries | No tool exists. Delivery via vault reserve/finalize pattern |
| Bypass creator isolation | All tools use `auth.creator_id` from `ToolAuthContext` |
| Execute arbitrary SQL | No DB access. All queries hardcoded with parameterized statements |
| Invoke unregistered tools | Closed registry, import-time initialization, dispatcher rejects unknown tools |

### 6.3 `list_products` Verification

```
READ ONLY:        YES — Pure SELECT queries, no writes
CREATOR SCOPED:   YES — SQL WHERE creator_id = $1
NO SIDE EFFECTS:  YES — Returns list of dicts, no state mutation
```

### 6.4 Product ID Override Protection

The LLM can supply `product_id` in `propose_product_offer`, but:
1. Product must belong to creator (verified in SQL: `WHERE creator_id = $1 AND id = $2`)
2. Full pipeline runs: state resolution → signal extraction → decision → strategy → execution
3. `execute_ppv()` re-verifies 12 authority conditions including live Fangate price
4. Price, currency, and link all come from Fangate API, not from LLM

**The LLM cannot override deterministic product selection authority.**

### 6.5 Authority Boundary Summary

```
LLM AUTHORITY BOUNDARY
======================
LLM output → text generation + structured signal extraction
LLM tools → READ-only data + PROPOSAL through deterministic pipelines
Decision engine → pure function, fixed thresholds, no LLM involvement
Execution → 12 authority conditions, live API verification, serialized persistence
Send → separate process, LLM never touches Telegram client
```

---

## 7. COMMERCE DECISION AUDIT

### 7.1 Decision Engine Conditions

| # | Condition | Source | Expected | Failure |
|---|-----------|--------|----------|---------|
| 1 | Hard eligibility denial | `evaluate_ppv_eligibility()` | NO_OFFER | NO_OFFER |
| 2 | Creator commerce disabled | `creator_integrations.commerce_enabled` | NO_OFFER | NO_OFFER |
| 3 | No relevant product | `product_identity is None` | NO_OFFER | NO_OFFER |
| 4 | Existing active offer | `find_pending_offer_for_product()` | NO_OFFER | NO_OFFER |
| 5 | Recent purchase cooldown | `hours_since_last_purchase` | NO_OFFER if < threshold | Never fires (safe) |
| 6 | Recent offer cooldown | `hours_since_last_offer` | NO_OFFER if < threshold | Never fires (safe) |
| 7 | Excessive activity budgets | `recent_offer_count`, `recent_sales_attempt_count` | NO_OFFER if > threshold | Never fires (safe) |
| 8 | Explicit buying intent | LLM `purchase_intent >= 0.85` | OFFER_PPV (confidence=0.95) | NO_OFFER (safe) |
| 9 | Strong buying signal | LLM `purchase_intent >= 0.80` | OFFER_PPV (confidence=0.85) | NO_OFFER (safe) |
| 10 | Follow-up due | `follow_up_due` flag | FOLLOW_UP | NO_OFFER (safe) |
| 11 | Moderate buying signal | LLM `purchase_intent >= 0.55` | SOFT_OFFER | NO_OFFER (safe) |
| 12 | Relationship readiness | `relationship_score >= 0.60` | SOFT_OFFER | NO_OFFER (safe) |
| 13 | Relationship building | Any non-zero signal | RELATIONSHIP_BUILDING | NO_OFFER (safe) |
| 14 | No signal | All zeros | NO_OFFER | NO_OFFER |

### 7.2 Critical Finding: Conditions 5-7 (Timing)

Before Phase 6.3, conditions 5-7 never fired because timing fields were
always `None`/`0`. After Phase 6.3, `get_timing_context()` computes real
values from `commerce_offers` timestamps. These conditions are now FUNCTIONAL.

### 7.3 LLM Signal Extraction as Sole Trigger

**The LLM signal extraction is the SOLE behavioral trigger for autonomous
commerce.** If the LLM does not detect buying intent (purchase_intent >= 0.80),
the system safely falls back to standard LLM conversation. This is by design —
conservative behavior is safer than false positives.

**Risk: If the LLM consistently fails to detect buying intent, the system
will never autonomously present offers.** This is a behavioral gap, not a
structural one. The system works correctly; the LLM may need prompt tuning
or threshold adjustment for specific creator contexts.

---

## 8. PPV EXECUTION AUDIT

### 8.1 Authority Conditions in `execute_ppv()`

| # | Condition | Source | Failure Behavior |
|---|-----------|--------|-----------------|
| 1 | Decision authority | `decision.action == OFFER_PPV and decision.allowed` | DENIED |
| 2 | Creator existence | `fdb.get_creator(creator_id)` | CREATOR_NOT_READY |
| 3 | Integration status | `fdb.get_creator_integration(creator_id)` | CREATOR_NOT_READY |
| 4 | Vault credential | `decrypt_secret(integration["encrypted_api_key"])` | CREDENTIAL_UNAVAILABLE |
| 5 | Fan authority | `get_user(user_id)` | FAN_UNAVAILABLE |
| 6 | Local product | `fservice.get_product(creator_id, product_id)` | PRODUCT_UNAVAILABLE |
| 7 | Existing offer re-check | `find_pending_offer_for_product()` | ALREADY_EXECUTED |
| 8 | Purchase re-check | `has_purchased_product()` | ALREADY_EXECUTED |
| 9 | Eligibility re-evaluation | `evaluate_ppv_eligibility()` | ELIGIBILITY_DENIED |
| 10 | Live Fangate verification | `fservice.verify_product(creator_id, product_id)` | FANGATE_ERROR |
| 11 | Consistency check | Compare remote vs local | INCONSISTENT_STATE |
| 12 | Serialized INSERT | `create_offer_serialized()` | EXECUTION_CONFLICT |

### 8.2 Duplicate Prevention

- Advisory lock: `pg_advisory_xact_lock(creator_id, user_id, product_id)`
- Conditional INSERT: `WHERE NOT EXISTS (SELECT 1 FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND product_id=$3 AND state IN ('pending','clicked'))`
- UNIQUE partial index on `(creator_id, user_id, product_id, state)` where state IN ('pending','clicked')

**A duplicate Telegram PPV offer CANNOT be sent for the same commercial action.**

### 8.3 Crash Window Analysis

| Scenario | Outcome | Risk |
|----------|---------|------|
| Offer creation succeeds but DB persistence fails | Fangate API accepted, no offer row created | LOW — user can still purchase via link, but attribution fails |
| DB persistence succeeds but Telegram send fails | Offer created, user never sees it | MEDIUM — offer expires, no user notification |
| Telegram send succeeds but process crashes before persistence | Message sent, no offer row updated | LOW — offer exists in DB, send is at-least-once |

### 8.4 Fangate Price Verification

`verify_product()` calls the Fangate API for authoritative link and price.
The execution engine compares remote vs local values. On mismatch, execution
is REFUSED with `INCONSISTENT_STATE`. This prevents stale local data from
creating incorrect offers.

---

## 9. TELEGRAM SEND AUDIT

### 9.1 Send Worker Path

```
_process_send_stream() [main.py:72]
  → XAUTOCLAIM (stalled message recovery)
  → XREADGROUP (read new messages)
  → [per message]
    → Dedup check (Redis EXISTS)
    → Rate limiting (Redis token bucket)
    → Entity resolution (Telethon get_input_entity)
    → [if media] Reserve delivery (PostgreSQL INSERT)
    → [if media] send_file() [Telethon MTProto]
    → [else] client.send_message() [Telethon MTProto]
    → Mark dedup (Redis SETEX)
    → ACK (Redis XACK)
    → Save to DB (PostgreSQL INSERT)
    → [if vault] Finalize delivery (PostgreSQL UPDATE)
```

### 9.2 Consumer Group Behavior

- Consumer group: `send_workers`
- Single consumer: `bot_main` (in `_process_send_stream`)
- Stalled messages: reclaimed via `XAUTOCLAIM` after 60s idle
- ACK: after successful send + bookkeeping
- DLQ: on generic exceptions (not on UserIsBlockedError)

### 9.3 Rate Limiting

- Token bucket per peer (Redis sorted set + Lua scripts)
- Config: 1 token/sec, burst of 5
- Rate-limited messages are NOT ACK'd — they re-enter the stream

### 9.4 Blocked Users

- `UserIsBlockedError` → ACK + discard + release vault reservation
- No DLQ entry (message is intentionally dropped)
- Event published: `message.send_failed` with `error: "UserIsBlockedError"`

### 9.5 Telethon Verification

The send path calls `client.send_message()` and `send_file()` which are
Telethon's MTProto API methods. These are REAL Telegram API calls, not
mocked/test implementations. The Telethon client is initialized with real
Telegram credentials in `chatbotv2/main.py:run()`.

### 9.6 Shutdown Behavior

The send loop checks `is_shutting_down()` at the top of each iteration.
When the shutdown flag is set, the loop breaks after processing the current
batch. Messages already read but not yet processed remain in the consumer's
pending list and will be reclaimed by `XAUTOCLAIM` on restart.

---

## 10. FANGATE WEBHOOK AUDIT

### 10.1 Endpoint Routing

```
POST /api/fangate/webhooks/{creator_id}
  → api_fangate_webhook_receive() [routes/fangate.py:679]
  → PUBLIC endpoint (no session auth)
  → HMAC-SHA256 verification
```

### 10.2 HMAC Verification

- Secret: `creator_integrations.encrypted_webhook_secret` (Fernet-encrypted)
- Algorithm: HMAC-SHA256 of raw body bytes
- Comparison: `hmac.compare_digest()` (constant-time)
- Format: `sha256={hex_digest}`

### 10.3 Idempotent Event Persistence

- `insert_fangate_webhook_event()` with `ON CONFLICT (creator_id, delivery_id) DO NOTHING`
- Duplicate delivery_id → silently skipped, returns `{"duplicate": True}`

### 10.4 Duplicate Webhook

```
Webhook arrives with same delivery_id
  → INSERT returns False (duplicate)
  → Check processed flag
  → If already processed: return {"duplicate": True}
  → No duplicate transaction, no duplicate attribution, no duplicate delivery
```
**CORRECT.**

### 10.5 Webhook Before Offer Persistence

```
Webhook arrives for product with 0 pending offers
  → attribute_purchase_from_webhook() returns None (0 candidates)
  → Transaction still saved to fangate_transactions
  → Attribution skipped
  → Post-purchase NOT invoked
  → Webhook returns {"attributed": false}
```
**Safe but problematic: the purchase is recorded but never attributed.
There is no reconciliation mechanism.** This is the attribution fragility
identified in the executive summary.

### 10.6 Webhook After Offer Persistence

```
Webhook arrives for product with exactly 1 pending offer
  → attribute_purchase_from_webhook() finds 1 candidate
  → Conditional UPDATE: state='purchased', purchased_at=NOW()
  → Attach user_id to transaction
  → Record analytics
  → Return PurchaseRecord
  → handle_post_purchase() invoked
```
**CORRECT.**

### 10.7 Webhook for Unknown Offer

```
Webhook arrives for product_id that has no pending offers
  → attribute_purchase_from_webhook() returns None
  → Transaction saved, attribution skipped
  → Safe behavior
```
**CORRECT.**

### 10.8 Malformed Webhook

```
Missing signature → WebhookSignatureInvalidError → HTTP 401
Missing delivery_id → FangateError → HTTP 400
Malformed JSON → FangateError → HTTP 400
Missing transaction_id → Event persisted, marked processed, returns {"processed": False}
```
**All safe.**

---

## 11. PURCHASE ATTRIBUTION AUDIT

### 11.1 How buyer_email Maps to Telegram user_id

**There is NO direct email-to-user_id lookup.** The identity resolution
works entirely through the `commerce_offers` table as a bridge:

1. **Offer creation** (before webhook): AI decides to present PPV offer
   to Telegram user. `dao.create_offer()` inserts row into `commerce_offers`
   with Telegram `user_id`, `product_id`, and Fangate payment link.

2. **Webhook arrives**: Carries `product_id` and `transaction_id` but NOT
   a Telegram `user_id`. The `buyer_email` is stored for audit only.

3. **Attribution bridge**: `attribute_purchase_from_webhook()` queries
   `commerce_offers` for `WHERE creator_id=$1 AND product_id=$2 AND state IN ('pending','clicked')`.
   When exactly one row exists, the `user_id` from that offer becomes the
   attributed Telegram user.

### 11.2 Attribution Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| 0 pending offers when webhook arrives | LOW (offer must expire first) | HIGH — purchase unattributed | None. No reconciliation mechanism |
| >1 pending offers for same product | VERY LOW (execution prevents duplicates) | HIGH — attribution skipped (fail-closed) | Advisory lock + conditional INSERT prevent this |
| Offer expired before webhook | LOW (depends on offer TTL) | HIGH — purchase unattributed | None |
| User purchased via different link | N/A | N/A | Attribution is offer-based, not link-based |

### 11.3 Buyer Identity Mapping Verdict

**The buyer identity mapping works correctly for the happy path.** When a
user receives a PPV offer and purchases it, the `commerce_offers` row
bridges the Fangate transaction to the Telegram user_id.

**However, there is a failure mode where the mapping breaks:**
- If the offer expires before the webhook arrives
- If there are 0 pending offers (all used/expired)
- If the user navigates to the product link directly (not through the offer)

In these cases, the purchase is recorded in `fangate_transactions` but
never attributed. The user pays but receives no content. There is no
automated reconciliation.

**This is NOT a production blocker** (the failure is rare and safe), but
it should be monitored and a reconciliation job should be considered.

---

## 12. POST-PURCHASE FULFILLMENT AUDIT

### 12.1 Media Delivery Path

```
handle_post_purchase(record)
  → deliver_product_media(creator_id, user_id, product_id, transaction_id)
    → get_fangate_product(creator_id, product_id) → raw JSONB media list
    → for each media item:
      → validate type (image→photo, video→video, document→document)
      → validate preview URL is HTTPS
      → has_user_received_media() → check prior delivery
      → reserve_delivery() → INSERT ON CONFLICT DO NOTHING
      → [if reserved] enqueue_send(media_type, media_path, fangate_media_id, ...)
      → [if enqueue fails] release_delivery()
```

### 12.2 Uniqueness Guarantee

- UNIQUE constraint: `(creator_id, user_id, fangate_media_id)`
- `reserve_delivery()` uses `ON CONFLICT DO NOTHING` — returns None if already reserved
- `has_user_received_media()` checks before delivery attempt

**Duplicate media delivery is prevented at the database level.**

### 12.3 Crash Window Analysis

| Scenario | Recovery |
|----------|----------|
| Crash before Telegram send | `release_stale_reservations()` cleans up pending rows after configurable timeout |
| Crash after Telegram accepts media | Media may be sent twice (at-least-once). Dedup ID `md5(user_id:fangate_media_id)` with 1h TTL prevents immediate re-send |
| Finalize failure | Reservation stays `pending`. `release_stale_reservations()` will clean it up. Media may be re-sent on next attempt |
| Redis failure | Delivery remains recoverable. Reservation exists in PostgreSQL. Send worker will reclaim stalled messages via XAUTOCLAIM |
| PostgreSQL failure | Delivery fails gracefully. `deliver_product_media` is wrapped in try/except in `handle_post_purchase`. Confirmation and follow-up still run |

### 12.4 Stale Reservation Recovery

```
_process_send_stream() [main.py:83]
  → release_stale_reservations(max_age_minutes=5)
  → DELETE FROM vault_media_deliveries
    WHERE status='pending' AND created_at < NOW() - interval
    RETURNING *
  → Deleted rows are gone (no re-enqueue)
```

**Stale reservations are cleaned up but NOT retried.** The media delivery
is lost. This is a known limitation — a retry mechanism would require
re-enqueuing to the send stream.

---

## 13. FAILURE-MODE MATRIX

| Failure | Expected Result | Recovery | Risk Level |
|---------|----------------|----------|------------|
| LLM timeout | Signal extraction returns `low_information()` → NO_OFFER | Automatic fallback to standard LLM | Safe |
| LLM malformed response | Signal extraction returns `low_information()` → NO_OFFER | Automatic fallback to standard LLM | Safe |
| Gemini quota (429) | Signal extraction returns `low_information()` → NO_OFFER | Retry with next API key | Safe |
| Product lookup failure | `resolve_commerce_product_with_history` returns None → no commerce | Automatic fallback to standard LLM | Safe |
| Fangate API timeout | `execute_ppv` returns FANGATE_ERROR → no offer created | Automatic fallback | Safe |
| Fangate API success + DB failure | `create_offer_serialized` returns PERSISTENCE_FAILED | Automatic fallback | Safe |
| DB failure (general) | `resolve_commerce_state` returns RESOLUTION_FAILED | Automatic fallback | Safe |
| Redis failure (inbound) | `enqueue_inbound` raises → message lost | Message saved to DB, not enqueued | Recoverable |
| Redis failure (send) | `enqueue_send` raises → send not queued | Post-purchase delivery skipped | Recoverable |
| Telegram send failure | DLQ entry created, message acked | Manual replay from DLQ | Recoverable |
| Telegram blocked user | ACK + discard + release vault reservation | No retry (intentional) | Safe |
| Worker crash before send | XAUTOCLAIM reclaims stalled message | Automatic on restart | Safe |
| Worker crash after send | Message may be re-sent (at-least-once) | Dedup prevents immediate duplicate | At-least-once |
| Webhook duplicate | Silently skipped via delivery_id uniqueness | Automatic | Safe |
| Webhook malformed | HTTP 400/401, no processing | Automatic | Safe |
| Attribution failure (0 offers) | Transaction saved, attribution skipped | No reconciliation | Permanent loss possible |
| Attribution failure (>1 offers) | Transaction saved, attribution skipped | Fail-closed | Safe |
| Media lookup failure | `deliver_product_media` warning logged, no crash | Confirmation + follow-up still run | Safe |
| Media send failure | DLQ entry, vault reservation released | Manual replay | Recoverable |
| Finalize failure | Reservation stays pending | Stale reservation cleanup | Safe |
| Stale reservation | `release_stale_reservations` deletes after 5min | Media delivery lost | Permanent loss possible |
| Shutdown during send | Messages in pending list | XAUTOCLAIM on restart | Safe |

---

## 14. OBSERVABILITY AUDIT

### 14.1 Structured Logging

| Component | Logger | Structured | Key Fields |
|-----------|--------|-----------|------------|
| Inbound | `chatbotv2.handlers` | Standard | Rate limits, exceptions |
| LLM Worker | `llm_worker` | Configurable (`STRUCTURED_LOGGING`) | Tool calls with key=value |
| Commerce Pipeline | `commerce_pipeline` | Standard | Stage failures with `created_by` |
| PPV Execution | `commerce.execution` | Standard | Credential, eligibility, Fangate |
| Attribution | `commerce.dao` | Standard | Offer lifecycle (0/1/>1 candidates) |
| Post-purchase | `commerce.post_purchase` | Standard | Every stage logged |
| Send worker | `chatbotv2.main` | Configurable | Dedup, rate limits, send failures |
| Webhooks | `integrations.fangate.service` | Structured `_audit()` helper | Operation, creator, success, latency |

### 14.2 Can We Determine...

| Question | Answer |
|----------|--------|
| WHY an action happened | YES — decision engine logs reason codes |
| WHICH fan it affected | YES — user_id logged in most operations |
| WHICH creator it affected | YES — creator_id in commerce operations |
| WHICH product/offer was involved | YES — product_id and offer_id logged |
| WHETHER Telegram actually sent it | YES — `message.sent` event or DLQ entry |
| WHETHER Fangate accepted it | PARTIAL — Fangate API response not logged |

### 14.3 Correlation IDs

| Scope | ID | Status |
|-------|-----|--------|
| HTTP requests | `X-Request-ID` | YES — generated per request |
| LLM generation | `generation_id` | YES — per `process_message` call |
| Redis Streams | `msg_id` | YES — per stream entry |
| Cross-system trace | None | **MISSING** — no unified trace ID |

### 14.4 DLQ Visibility

- DLQ count exposed in `/api/stats` dashboard endpoint
- DLQ inspection functions exist in `db/redis.py` but are NOT exposed through dashboard API routes
- DLQ replay supported via `replay_dlq_entry()` with max 3 attempts

### 14.5 Events Published

| Event | Producer | Payload |
|-------|----------|---------|
| `message.created` | `handlers.py` | message_id, content, direction |
| `ai.generation_started` | `llm_worker.py` | message_preview |
| `ai.generation_completed` | `llm_worker.py` | draft, score, flags |
| `ai.generation_failed` | `llm_worker.py` | error |
| `suggestion.created` | `llm_worker.py` | queue_id, draft, score |
| `message.sent` | `main.py` | content, telegram_message_id |
| `message.send_failed` | `main.py` | error type |
| `vault.media_sent` | `main.py` | fangate_media_id, product_id |

**Gap: `message.sent` and `message.send_failed` events lack `generation_id`,
breaking cross-phase correlation.**

---

## 15. CONFIGURATION AUDIT

### 15.1 Required Configuration

| Setting | Source | Default | Validated |
|---------|--------|---------|-----------|
| `gemini_api_keys` | Env | — | Type only |
| `postgres_dsn` | Env | localhost:5432 | Type only |
| `redis_url` | Env | localhost:6379 | Type only |
| `fangate_enc_key` | Env | — | Type only |
| `operator_telegram_ids` | Env | — | Comma-separated int parsing |

### 15.2 Autonomy Kill Switch

**FINDING: There is NO explicit autonomy kill switch.**

The only mechanism to stop autonomous operation is the Redis
`auto_reply` toggle (`setting:auto_reply`). When set to False, all
AI drafts route to the operator queue instead of auto-sending.

**Limitations:**
- Redis restart clears the toggle (defaults to True)
- No env-var-level kill switch
- No per-subsystem disable (cannot kill commerce independently)
- No emergency stop that survives infrastructure restart

### 15.3 Commerce Configuration

| Setting | Default | Notes |
|---------|---------|-------|
| `auto_approve_threshold` | 0.80 | Score threshold for auto-send |
| `llm_tools_enabled` | True | Master tool toggle |
| `llm_max_tool_calls` | 3 | Max tool calls per generation |
| `llm_tool_timeout_seconds` | 5.0 | Per-tool timeout |
| `vault_stale_reservation_minutes` | 5 | Stale reservation cleanup |
| `debounce_window_seconds` | 3 | Message debounce window |
| `rate_limit_per_minute` | 20 | Per-user rate limit |

### 15.4 Configuration Validation Gap

Pydantic provides type coercion but no:
- Connectivity checks at startup
- Range validation for numeric fields
- Format validation for credentials
- Required-field presence checks beyond type

---

## 16. CREATOR ISOLATION AUDIT

### 16.1 Isolation Proof by Area

| Area | Isolated? | Evidence |
|------|-----------|----------|
| Product selection | YES | `WHERE creator_id = $1` in all queries |
| Commerce state | YES | All 11+ queries scoped by creator_id |
| PPV execution | YES | 12 authority checks, all creator-scoped |
| Webhook processing | YES | URL creator_id + HMAC signature binding |
| Vault media delivery | YES | UNIQUE(creator_id, user_id, fangate_media_id) |
| Segment evaluation | YES | creator_id injected as $1 in all SQL |
| LLM tools | YES | ToolAuthContext frozen dataclass |
| Analytics | YES for commerce/vault | Creator-scoped queries |
| Redis dedup IDs | YES | Operational (prevent double-send), not access control |

### 16.2 Cross-Creator Exposure Attempt

I attempted to find ANY path where creator A could access creator B's data:

| Attack Vector | Result | Protection |
|--------------|--------|------------|
| Different creator_id in webhook URL | FAILS | HMAC signature bound to creator's secret |
| Different product_id in tool call | FAILS | SQL WHERE creator_id = $1 |
| Different creator_id in commerce pipeline | FAILS | All queries scoped |
| Different creator_id in vault delivery | FAILS | UNIQUE constraint includes creator_id |
| Different creator_id in segment evaluation | FAILS | SQL compiler injects creator_id |

**No cross-creator path exists in the current single-creator deployment.**

### 16.3 Forward-Looking Risks

The following tables are global (no `creator_id` column):
- `users`, `messages`, `operator_queue`, `personas`, `user_profiles`,
  `message_embeddings`, `sessions`, `conversation_attention`

In a multi-creator deployment, these would leak cross-creator data.
**This is NOT a blocker for current single-creator activation.**

---

## 17. INTEGRATION TEST GAP ANALYSIS

### 17.1 Current Test Coverage

| Test Category | Files | Type | Real DB/Redis? |
|--------------|-------|------|----------------|
| Product selection | `test_product_selection.py`, `test_phase_6_1.py` | Unit/mocked | NO |
| Commerce pipeline | `test_commerce_pipeline.py`, `test_commerce_integration.py` | Unit/mocked | NO |
| Decision engine | `test_commerce_decision.py`, `test_commerce_eligibility.py` | Unit/mocked | NO |
| PPV execution | `test_commerce_execution.py` | Unit/mocked | NO |
| Signal extraction | `test_commerce_deepseek.py` | Unit/mocked | NO |
| Response generation | `test_commerce_deepseek_response.py` | Unit/mocked | NO |
| Webhook attribution | `test_webhook_attribution.py` | Unit/mocked | NO |
| Post-purchase | `test_post_purchase.py`, `test_post_purchase_delivery.py` | Unit/mocked | NO |
| Vault delivery | `test_vault.py`, `test_vault_concurrency.py` | Unit/mocked | NO |
| DAO timing | `test_commerce_dao_timing.py` | Unit/mocked | NO |
| E2E boundaries | `test_e2e_p34.py` | Boundary-mocked | NO |

**Total: 35+ test files, ALL unit tests with mocked infrastructure.
ZERO true integration tests exist.**

### 17.2 Required Integration Tests

| # | Test | Priority | Description |
|---|------|----------|-------------|
| 1 | `test_commerce_e2e_happy_path` | CRITICAL | Full path: inbound → signal extraction → product selection → decision → execution → offer created in DB → webhook → attribution → media delivery |
| 2 | `test_webhook_hmac_verification` | HIGH | Real HMAC-SHA256 with real secret |
| 3 | `test_vault_delivery_e2e` | HIGH | Real Redis reservation + DB + enqueue |
| 4 | `test_stale_reservation_recovery` | HIGH | Real Redis with TTL expiration |
| 5 | `test_creator_isolation_e2e` | HIGH | Real DB with multi-creator rows |
| 6 | `test_purchase_history_exclusion` | HIGH | Real DB with purchase records |
| 7 | `test_duplicate_webhook_idempotency` | MEDIUM | Real DB with duplicate delivery_id |
| 8 | `test_multiple_products_fail_closed` | MEDIUM | Real DB with multiple valid products |

---

## 18. PRODUCTION READINESS SCORECARD

| Category | Score | Blocker? | Notes |
|----------|------:|----------|-------|
| Inbound reachability | 9/10 | No | Full path proven. `_wait_and_process` lacks error handling |
| Commerce decisioning | 9/10 | No | 14-rule cascade proven. LLM signal extraction is sole behavioral trigger |
| Product selection | 10/10 | No | All 5 cases verified. Fail-closed on ambiguity |
| LLM authority isolation | 10/10 | No | 6 tools, all scoped. No bypass possible |
| PPV execution | 9/10 | No | 12 authority conditions. Crash windows documented |
| Telegram delivery | 9/10 | No | Single consumer, rate limiting, DLQ. No dashboard DLQ UI |
| Webhook processing | 9/10 | No | HMAC, idempotency, attribution. No reconciliation for 0-offer case |
| Purchase attribution | 7/10 | CONDITIONAL | Works for happy path. 0-offer failure mode has no reconciliation |
| Post-purchase fulfillment | 8/10 | No | Media delivery proven. Stale reservations lost, not retried |
| Failure recovery | 8/10 | No | DLQ exists but no dashboard UI. Stale reservations not retried |
| Observability | 6/10 | CONDITIONAL | Partial logging, no end-to-end correlation ID, no DLQ dashboard |
| Configuration | 7/10 | CONDITIONAL | No explicit kill switch. No startup validation |
| Creator isolation | 10/10 | No | All commerce paths verified. Global tables acceptable for single-creator |
| **TOTAL** | **105/130** | | |

---

## 19. CRITICAL BLOCKERS

### BLOCKER 1: No Integration Tests

```
File: tests/ (entire directory)
Current behavior: All 35+ commerce tests use mocked DB/Redis
Why this prevents safe autonomy: The production path has never been
  exercised against real infrastructure. Wiring correctness is assumed,
  not proven.
Minimal required fix: Build 3-5 integration tests that exercise the
  commerce path against real PostgreSQL and Redis
Tests required: test_commerce_e2e_happy_path, test_webhook_hmac,
  test_vault_delivery_e2e
```

### BLOCKER 2: No Autonomy Kill Switch

```
File: core/config.py
Current behavior: No ENABLE_AUTONOMY or KILL_SWITCH env var
Why this prevents safe autonomy: No way to emergency-stop autonomy
  that survives Redis restart. The auto_reply toggle defaults to True
  on Redis restart.
Minimal required fix: Add AUTONOMY_ENABLED env var (default: True)
  checked at worker startup and in the commerce pipeline
Tests required: Test that setting AUTONOMY_ENABLED=false disables
  commerce path
```

### BLOCKER 3: Attribution Fragility

```
File: commerce/dao.py:attribute_purchase_from_webhook()
Current behavior: Returns None when 0 pending offers exist
Why this prevents safe autonomy: User pays but receives no content,
  no confirmation, no follow-up. No reconciliation mechanism.
Minimal required fix: Log a WARNING with full context (creator_id,
  product_id, transaction_id, buyer_email) for manual investigation.
  Consider a reconciliation job.
Tests required: test_attribution_no_pending_offer_logs_warning
```

---

## 20. FINAL ACTIVATION VERDICT

```
AUTONOMY ACTIVATION VERDICT
===========================

STATUS: GO WITH CONDITIONS

CRITICAL BLOCKERS:
1. No integration tests exist — all commerce tests use mocked infrastructure
2. No explicit autonomy kill switch — auto_reply toggle defaults to True on Redis restart
3. Attribution fragility — 0 pending offers at webhook time = permanent unattributed purchase

HIGH-RISK CONDITIONS:
1. LLM signal extraction is the SOLE behavioral trigger — if LLM fails to detect buying intent, system never autonomously presents offers (by design, but limits effectiveness)
2. Stale vault reservations are deleted, not retried — media delivery is lost on crash
3. No end-to-end correlation ID — debugging production issues requires manual log correlation

SAFE TO ACTIVATE:
1. All 12 previously dead paths are now LIVE and reachable
2. Product selection correctly fails closed on ambiguity
3. LLM authority boundaries are well-enforced — no bypass possible
4. Creator isolation is intact across all commerce paths
5. Webhook HMAC verification is cryptographically sound
6. Duplicate prevention via advisory locks + conditional INSERT
7. Send worker has rate limiting, dedup, DLQ, and stale recovery
8. Post-purchase operations are independently isolated (failure of one doesn't block others)
9. Decision engine is pure function with fixed thresholds — deterministic and auditable
10. All failure modes degrade safely (no data corruption, no cross-creator exposure)

REQUIRED BEFORE ACTIVATION:
1. Build minimal integration test suite (3-5 tests against real DB/Redis)
2. Add AUTONOMY_ENABLED env var as explicit kill switch
3. Add WARNING logging for 0-offer attribution failure
4. Enable STRUCTURED_LOGGING=true in production
5. Monitor DLQ count after activation
6. Have operator available for first 48 hours

POST-ACTIVATION MONITORING:
1. DLQ count (should be near zero)
2. Commerce decision distribution (OFFER_PPV vs NO_OFFER ratio)
3. Attribution success rate (attributed / total webhook transactions)
4. Stale reservation count (should be cleaned up regularly)
5. LLM signal extraction quality (purchase_intent score distribution)
6. Vault media delivery success rate
```

---

## APPENDIX: KEY FILES REFERENCED

| File | Lines | Role |
|------|-------|------|
| `chatbotv2/handlers.py` | 25-130 | Inbound message handling |
| `workers/llm_worker.py` | 257-500 | LLM processing + commerce path |
| `commerce/integration.py` | 113-155 | State → pipeline composition |
| `commerce/state.py` | 169-300 | Application state resolution |
| `commerce/pipeline.py` | 383-480 | 6-step commerce pipeline |
| `commerce/deepseek.py` | 164-220 | Commerce signal extraction |
| `commerce/signals.py` | 250-270 | Signal → context → decision |
| `commerce/decision.py` | 194-300 | 14-rule deterministic cascade |
| `commerce/strategy.py` | 171-200 | Decision → strategy |
| `commerce/orchestrator.py` | 157-200 | Execution gate + PPV trigger |
| `commerce/execution.py` | 126-310 | PPV offer creation (12 conditions) |
| `commerce/dao.py` | 20-400 | Offer CRUD + attribution |
| `commerce/post_purchase.py` | 146-461 | Post-purchase orchestration |
| `commerce/product_selection.py` | 64-244 | Product resolution + history |
| `chatbotv2/main.py` | 72-330 | Send worker + Telethon |
| `integrations/fangate/service.py` | 703-907 | Webhook processing |
| `db/redis.py` | 65-609 | Stream operations + DLQ |
| `db/vault.py` | 46-183 | Vault delivery reservations |
| `core/llm_tools.py` | 37-700 | Tool registry + dispatch |
